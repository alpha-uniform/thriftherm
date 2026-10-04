"""Thriftherm – demand-driven heating for Home Assistant.

Plans (and, once released, controls) an own gas boiler over ebusd, an optional
air-to-air heat pump and the room thermostats. Every controller starts in plan
only mode and writes nothing until active control is released and selected.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SYSTEM_TYPE,
    CONFIG_VERSION,
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
from . import issues, learning_reset
from .config_schema import known_keys
from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator

_LOGGER = logging.getLogger(__name__)

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
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_AWAY, clear_away)
    hass.services.async_register(DOMAIN, SERVICE_RESET_LEARNING, reset_learning, schema=RESET_LEARNING_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, schema=BOOST_SCHEMA)


async def async_migrate_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> bool:
    """Bring an older config entry up to the current schema.

    Version 1 only ever described one kind of installation: an own gas boiler
    reachable over ebusd. Recording that explicitly lets later versions offer
    district heating and heat-pump-only setups without guessing.

    Version 3 keeps one copy. The options flow wrote everything into the
    options, so the data kept the values of the first setup and the keys of
    fields removed since. Both are merged the way they were read (the options
    win), leftovers dropped, and the data emptied.
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
    hass.config_entries.async_update_entry(entry, data={}, options=options, version=CONFIG_VERSION)
    _LOGGER.info("Thriftherm config entry migrated to version %s, dropped %s", CONFIG_VERSION, sorted(set(merged) - set(options)))
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once, so automations referencing them validate at startup."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> bool:
    coordinator = ThrifthermCoordinator(hass, entry)
    await coordinator.async_start()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Room devices point at the hub via via_device_id. Platforms load concurrently, so the
    # hub must exist before any of them, or a room device registered first loses the link.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Thriftherm",
        manufacturer="Thriftherm",
        model="Heating control",
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
        # learning and user state changed since the last periodic save must survive a restart
        await entry.runtime_data.async_save_store(force=True)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.disabled_by is not None:
        # disabled: no cycle checks the repairs any more (a reload keeps them, its first cycle syncs)
        issues.async_clear(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> None:
    """The entry is gone: so are its repairs and its stored learning and controller state."""
    issues.async_clear(hass)
    await Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
