"""Thriftherm – demand-driven heating for Home Assistant.

Plans (and, once released, controls) an own gas boiler (over ebusd, or through
two entities), an optional
air-to-air heat pump and the room thermostats. Every controller starts in plan
only mode and writes nothing until active control is released and selected.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEFAULT_HEAT_RATE_HEAT_PUMP,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_SYSTEM_TYPE,
    CONFIG_VERSION,
    CTRL_ACTIVE,
    DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP,
    DEFAULT_SYSTEM_TYPE,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOST,
    SERVICE_CLEAR_AWAY,
    SERVICE_CLEAR_OVERRIDE,
    SERVICE_RESET_LEARNING,
    SERVICE_SET_AWAY,
    SERVICE_SET_OVERRIDE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from . import card, issues, learning_reset
from .config_schema import known_keys
from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator

_LOGGER = logging.getLogger(__name__)

# hass.data key: entry id -> (heat pump climate, let-go) of an entry unloaded neither disabled nor yet deleted. A delete
# carries it out, and so does the next run of a reload that does not go on with that unit actively or fails to start.
PENDING_LET_GO = f"{DOMAIN}_pending_let_go"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
        vol.Optional("duration_min"): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
    }
)
CLEAR_OVERRIDE_SCHEMA = vol.Schema({vol.Optional("room"): cv.string})
RESET_LEARNING_SCHEMA = vol.Schema(
    {
        vol.Optional("scope"): vol.All(cv.ensure_list, [vol.In(learning_reset.SCOPES)]),
        vol.Optional("rooms"): vol.All(cv.ensure_list, [cv.string]),
    }
)
SET_AWAY_SCHEMA = vol.Schema({vol.Optional("return_time"): cv.datetime, vol.Optional("clear_return_time"): cv.boolean})
# no fields: a return time meant for set_away is refused, not taken as an end of the absence (review 2026-11-08)
CLEAR_AWAY_SCHEMA = vol.Schema({})
BOOST_SCHEMA = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Optional("duration_min"): vol.All(vol.Coerce(int), vol.Range(min=5, max=240)),
    }
)


def _coordinators(hass: HomeAssistant) -> list[ThrifthermCoordinator]:
    """Loaded coordinators; a service call without any is an error, not a silent no-op."""
    found = [entry.runtime_data for entry in hass.config_entries.async_entries(DOMAIN) if hasattr(entry, "runtime_data")]
    if not found:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return found


def _require_room(coord: ThrifthermCoordinator, room: str) -> None:
    if room not in {r.key for r in coord.builder.rooms}:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_room", translation_placeholders={"room": room})


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_OVERRIDE):
        return

    async def set_override(call: ServiceCall) -> None:
        for coord in _coordinators(hass):
            _require_room(coord, call.data["room"])
            await coord.async_set_override(call.data["room"], call.data["temperature"], call.data.get("duration_min"))

    async def clear_override(call: ServiceCall) -> None:
        for coord in _coordinators(hass):
            if "room" in call.data:
                # a wrong key would clear nothing and still report success, leaving the override in force
                _require_room(coord, call.data["room"])
            await coord.async_clear_override(call.data.get("room"))

    async def set_away(call: ServiceCall) -> None:
        rt = call.data.get("return_time")
        ts = None
        if rt is not None:
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=dt_util.get_default_time_zone())
            ts = rt.timestamp()  # the coordinator refuses a time in the past
        for coord in _coordinators(hass):
            await coord.async_set_away(ts, bool(call.data.get("clear_return_time")))

    async def clear_away(call: ServiceCall) -> None:
        for coord in _coordinators(hass):
            await coord.async_clear_away()

    async def boost(call: ServiceCall) -> None:
        for coord in _coordinators(hass):
            _require_room(coord, call.data["room"])
            await coord.async_boost(call.data["room"], call.data.get("duration_min"))

    async def reset_learning(call: ServiceCall) -> None:
        for coord in _coordinators(hass):
            await coord.async_reset_learning(call.data.get("scope"), call.data.get("rooms"))

    hass.services.async_register(DOMAIN, SERVICE_SET_OVERRIDE, set_override, schema=SET_OVERRIDE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_OVERRIDE, clear_override, schema=CLEAR_OVERRIDE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_AWAY, set_away, schema=SET_AWAY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_AWAY, clear_away, schema=CLEAR_AWAY_SCHEMA)
    # Months of learning gone with no undo: an administrator's call, not that of any household
    # account or a wall tablet's token (review 2026-11-08).
    async_register_admin_service(hass, DOMAIN, SERVICE_RESET_LEARNING, reset_learning, schema=RESET_LEARNING_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, schema=BOOST_SCHEMA)


# the default heat-up rate of heat pump rooms up to config version 3, see async_migrate_entry
OLD_DEFAULT_HEAT_RATE_HEAT_PUMP = 1.2


async def async_migrate_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> bool:
    """Bring an older config entry up to the current schema.

    Version 1 only ever described one kind of installation: an own gas boiler
    reachable over ebusd. Recording that explicitly lets later versions offer
    district heating and heat-pump-only setups without guessing.

    Version 3 keeps one copy. The options flow wrote everything into the
    options, so the data kept the values of the first setup and the keys of
    fields removed since. Both are merged the way they were read (the options
    win), leftovers dropped, and the data emptied.

    Version 4 holds the heat pump at its minimum. "Setpoint follows the rooms" and the setpoint
    offset are gone with the keys they were stored under; a configured offset is not carried over,
    since a setpoint above the rooms is what overheated them (seen 2026-10-29).
    The old default heat-up rate of heat pump rooms, 1.2 °C/h, becomes the new one, 0.5: that is
    what the unit gives at its minimum (0.46-0.66 measured 2026-10-29/30). Any other value was set
    on purpose and stays.
    """
    if entry.version > CONFIG_VERSION:
        # written by a newer version: running on data we do not understand is worse than not starting
        return False
    if entry.version == CONFIG_VERSION:
        return True
    data = {**entry.data}
    if entry.version < 2:
        data.setdefault(CONF_SYSTEM_TYPE, DEFAULT_SYSTEM_TYPE)
    merged = {**data, **entry.options}
    options = known_keys(merged)
    if entry.version < 4 and options.get(CONF_DEFAULT_HEAT_RATE_HEAT_PUMP) == OLD_DEFAULT_HEAT_RATE_HEAT_PUMP:
        options[CONF_DEFAULT_HEAT_RATE_HEAT_PUMP] = DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP
    hass.config_entries.async_update_entry(entry, data={}, options=options, version=CONFIG_VERSION)
    _LOGGER.info("Thriftherm config entry migrated to version %s, dropped %s", CONFIG_VERSION, sorted(set(merged) - set(options)))
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once, so automations referencing them validate at startup; serve the dashboard card."""
    _register_services(hass)
    await card.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else None
    # first: the dashboards keep the card while a failing first refresh makes the entry retry
    await card.async_load(hass, version)
    # a reload: the last run left the unit to this one, if this one goes on with it
    parked = hass.data.get(PENDING_LET_GO, {}).pop(entry.entry_id, None)
    coordinator = ThrifthermCoordinator(hass, entry)
    try:
        await coordinator.async_start()
        if parked is not None and not (
            coordinator.control_mode == CTRL_ACTIVE
            and coordinator.has_heat_pump
            and coordinator.config.get(CONF_HEAT_PUMP_CLIMATE) == parked[0]
        ):
            # Active control taken away, the heat pump removed or another unit chosen: nobody goes on with what the
            # last run left, and a quick heat-up stayed at 30 °C for good (verifier of 0.7.3b, #R1, #R8).
            let_go, parked = parked[1], None
            await let_go()
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        if parked is not None:
            await parked[1]()  # this run never starts: the unit is left to nobody, as on a disable
        raise
    entry.runtime_data = coordinator
    # Room devices point at the hub via via_device_id. Platforms load concurrently, so the
    # hub must exist before any of them, or a room device registered first loses the link.
    # Its firmware field shows the installed version: which package runs was not to be seen anywhere.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Thriftherm",
        manufacturer="Thriftherm",
        model="Heating control",
        sw_version=version,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_registry_entries(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # The coordinator schedules its next cycle only while someone listens. With every entity
    # disabled the control would stop; this listener keeps it running.
    entry.async_on_unload(coordinator.async_add_listener(lambda: None))

    async def _save_on_stop(_event: Event) -> None:
        # Home Assistant does not unload entries when it stops: without this, up to ten minutes
        # of learning and controller timers would be lost on every restart.
        await coordinator.async_save_store(force=True)
        # a boiler addressed through entities keeps a block for good once nothing commands it
        await coordinator.async_release_boiler(final=True)

    # not listen_once: its remover would log an error on an unload after the stop
    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _save_on_stop))
    _LOGGER.info("Thriftherm started (heat pump control: %s)", coordinator.control_mode)
    return True


def _remove_stale_registry_entries(hass: HomeAssistant, entry: ThrifthermConfigEntry, coordinator: ThrifthermCoordinator) -> None:
    """Drop entities and room devices the current configuration no longer provides.

    A deleted room, or a switch from own boiler to district heating, would
    otherwise leave devices and entities behind that the UI cannot remove.
    """
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.unique_id not in coordinator.expected_unique_ids:
            ent_reg.async_remove(reg_entry.entity_id)
    rooms = {r.key for r in coordinator.builder.rooms}
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not _device_still_provided(entry, device, rooms):
            dev_reg.async_remove_device(device.id)  # our room devices belong to this entry alone


def _device_still_provided(entry: ThrifthermConfigEntry, device: dr.DeviceEntry, rooms: set[str]) -> bool:
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == entry.entry_id:
            return True
        prefix = f"{entry.entry_id}_room_"
        if identifier.startswith(prefix) and identifier[len(prefix):] in rooms:
            return True
    return False


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ThrifthermConfigEntry, device: dr.DeviceEntry) -> bool:
    """Allow deleting devices of rooms that no longer exist; never the hub or a live room."""
    rooms = {r.key for r in entry.runtime_data.builder.rooms} if hasattr(entry, "runtime_data") else set()
    return not _device_still_provided(entry, device, rooms)


async def async_unload_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> bool:
    if hasattr(entry, "runtime_data"):
        # first the cycle under way: what it sent belongs in the save, and nothing goes out after it
        await entry.runtime_data.async_stop_cycles()
        if entry.runtime_data.control_mode == CTRL_ACTIVE:
            # A quick heat-up or a drying would keep its setpoint for good, as a block would on the boiler: let go
            # when nothing runs after this one. Not on a reload, whose next run goes on with them: set down to
            # 17 °C there, the drying held the 17 after it, the unit stopped and started hard (second review
            # 2026-11-08). A delete unloads first as well, so the let-go waits for async_remove_entry.
            if entry.disabled_by is not None:
                await entry.runtime_data.async_let_go_of_heat_pump()
            else:
                hass.data.setdefault(PENDING_LET_GO, {})[entry.entry_id] = (
                    entry.runtime_data.config.get(CONF_HEAT_PUMP_CLIMATE),
                    _let_go_later(entry.runtime_data),
                )
        # learning and user state changed since the last periodic save must survive a restart
        await entry.runtime_data.async_save_store(force=True)
        await entry.runtime_data.async_release_boiler(final=True)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.disabled_by is not None:
        # disabled: no cycle checks the repairs any more (a reload keeps them, its first cycle syncs)
        issues.async_clear(hass)
    return unloaded


def _let_go_later(coordinator: ThrifthermCoordinator) -> Callable[[], Awaitable[None]]:
    """The let-go of an unloaded run, carried out once it is clear that nothing goes on with the unit.

    The store it saved at the unload is saved again with what the let-go left: a run that starts from it after a
    failed setup finds the unit as it was left, not at 30 °C.
    """

    async def _let_go() -> None:
        await coordinator.async_let_go_of_heat_pump()
        await coordinator.async_save_store(force=True)

    return _let_go


async def async_remove_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> None:
    """The entry is gone: so are its repairs, its stored learning and controller state, and the card's resource."""
    parked = hass.data.get(PENDING_LET_GO, {}).pop(entry.entry_id, None)
    if parked is not None:
        await parked[1]()
    issues.async_clear(hass)
    await Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}").async_remove()
    await card.async_remove_resource(hass)


async def _async_update_listener(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
