"""A room with its radiator valves instead of a room thermostat (docs/PHASE13_VALVES.md §2, §7): the setup."""

from __future__ import annotations

import pytest

from custom_components.thriftherm import config_schema
from custom_components.thriftherm.adapters.config import room_configs_from_config

ROOM = {"name": "Bad", "temperature_sensor": "sensor.bad", "schedule_weekday": "", "schedule_weekend": ""}
BAD_VALVE = "climate.heizkorper_badezimmer"


def _room(**extra):
    return {**ROOM, **extra}


# ------------------------------------------------------------------ the form
def test_a_room_has_valves_or_a_room_thermostat_not_both():
    errors = config_schema.validate_room(_room(climate_entity="climate.badezimmer", valve_entities=[BAD_VALVE]))
    assert errors == {"valve_entities": "valves_and_thermostat"}


def test_a_valve_belongs_to_one_room():
    others = [{"key": "kuche", "name": "Küche", "valve_entities": [BAD_VALVE]}]
    assert config_schema.validate_room(_room(valve_entities=[BAD_VALVE]), others) == {"valve_entities": "valve_used_twice"}
    others = [{"key": "kuche", "name": "Küche", "climate_entity": BAD_VALVE}]
    assert config_schema.validate_room(_room(valve_entities=[BAD_VALVE]), others) == {"valve_entities": "valve_used_twice"}


def test_a_room_thermostat_cannot_be_the_valve_of_another_room():
    others = [{"key": "kuche", "name": "Küche", "valve_entities": [BAD_VALVE]}]
    assert config_schema.validate_room(_room(climate_entity=BAD_VALVE), others) == {"climate_entity": "climate_used_twice"}


def test_the_heat_pumps_unit_is_no_valve():
    errors = config_schema.validate_room(_room(valve_entities=["climate.midea"]), (), "climate.midea")
    assert errors == {"valve_entities": "climate_is_heat_pump"}


def test_a_valve_driven_by_an_active_better_thermostat_is_refused():
    """§4: two controllers at one valve got two setpoints a minute. A disabled entry drives nothing (the driver None)."""
    driver = {BAD_VALVE: "climate.badezimmer"}.get
    assert config_schema.validate_room(_room(valve_entities=[BAD_VALVE]), driver=driver) == {
        "valve_entities": "valve_driven_elsewhere"
    }
    assert config_schema.validate_room(_room(valve_entities=["climate.heizkorper_kuche"]), driver=driver) == {}


# ------------------------------------------------------------------ what the engines get
def test_the_first_valve_is_the_rooms_thermostat_and_all_are_its_valves():
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(valve_entities=[BAD_VALVE, "climate.bad_2"])}])
    assert cfg.direct and cfg.valve_entities == (BAD_VALVE, "climate.bad_2")
    assert cfg.climate_entity == BAD_VALVE  # hand, reappearance, silence and lead as for a room thermostat


def test_a_room_with_both_stored_keeps_its_room_thermostat():
    """Stored by hand or an older form: what it did before, no valve written to."""
    (cfg,) = room_configs_from_config(
        [{"key": "bad", **_room(climate_entity="climate.badezimmer", valve_entities=[BAD_VALVE])}]
    )
    assert not cfg.direct and cfg.climate_entity == "climate.badezimmer" and cfg.valve_entities == ()


def test_a_room_of_before_has_no_valves():
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(climate_entity="climate.badezimmer")}])
    assert not cfg.direct and cfg.valve_entities == ()


# ------------------------------------------------------------------ which valves another entry drives
async def test_the_driver_of_a_valve_is_an_enabled_better_or_versatile_thermostat(hass) -> None:
    from homeassistant.config_entries import ConfigEntryDisabler
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.adapters.inputs import valve_driver

    registry = er.async_get(hass)
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": [{"trv": BAD_VALVE}]})
    bt.add_to_hass(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_bad", suggested_object_id="badezimmer", config_entry=bt)
    vt = MockConfigEntry(domain="versatile_thermostat", data={"underlying_entity_ids": ["climate.heizkorper_kuche"]})
    vt.add_to_hass(hass)
    registry.async_get_or_create("climate", "versatile_thermostat", "vt_kuche", suggested_object_id="kuche", config_entry=vt)
    off = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.heizkorper_wohnzimmer"]},
                          disabled_by=ConfigEntryDisabler.USER)
    off.add_to_hass(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_wz", suggested_object_id="wohnzimmer", config_entry=off)

    assert valve_driver(hass, BAD_VALVE) == "climate.badezimmer"
    assert valve_driver(hass, "climate.heizkorper_kuche") == "climate.kuche"
    assert valve_driver(hass, "climate.heizkorper_wohnzimmer") is None  # its entry is disabled
    assert valve_driver(hass, "climate.heizkorper_arbeitszimmer") is None


# ------------------------------------------------------------------ what the adapter reads of a valve
def _trvzbt_valve(hass, select="remote_temperature", calibration="0", available=True):
    """A bathroom valve as Zigbee2MQTT shows it (read 2026-11-13): a part of its 44 entities."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="mqtt")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mqtt", "zigbee2mqtt_0xa4c138fcc3bce63e")},
        manufacturer="SONOFF", model="Zigbee thermostatic radiator valve",
    )
    registry = er.async_get(hass)
    entities = {
        "climate": ("climate", "", None),
        "number_ext": ("number", "_external_temperature_input", "°C"),
        "select": ("select", "_temperature_sensor_select", None),
        "number_cal": ("number", "_local_temperature_calibration", "°C"),
        "lock": ("switch", "_child_lock", None),
    }
    for domain, suffix, _unit in entities.values():
        registry.async_get_or_create(
            domain, "mqtt", f"0x<appliance-id>{suffix or '_climate'}_zigbee2mqtt",
            suggested_object_id=f"heizkorper_badezimmer{suffix}", device_id=device.id, config_entry=entry,
        )
    bad = "unavailable" if not available else None
    hass.states.async_set(BAD_VALVE, bad or "heat", {"current_temperature": 20.2, "temperature": 21.0})
    hass.states.async_set("number.heizkorper_badezimmer_external_temperature_input", bad or "20.2",
                          {"unit_of_measurement": "°C", "min": -9.9, "max": 99.9, "step": 0.1})
    hass.states.async_set("select.heizkorper_badezimmer_temperature_sensor_select", bad or select,
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    hass.states.async_set("number.heizkorper_badezimmer_local_temperature_calibration", bad or calibration,
                          {"unit_of_measurement": "°C", "min": -10.0, "max": 10.0, "step": 0.2})
    hass.states.async_set("switch.heizkorper_badezimmer_child_lock", bad or "on")


async def test_the_adapter_tells_what_trvzbt_valve_can_do_and_shows(hass) -> None:
    from homeassistant.core import Context

    from custom_components.thriftherm.adapters.inputs import valve_view

    _trvzbt_valve(hass, calibration="-0.6")
    view = valve_view(hass, BAD_VALVE)
    assert view.available and view.caps.ways() == ("external", "offset", "setpoint")
    assert view.caps.external_temperature == "number.heizkorper_badezimmer_external_temperature_input"
    assert view.caps.child_lock == "switch.heizkorper_badezimmer_child_lock"
    assert view.quirks.external_max_age_s == 7200.0  # Phase B
    assert (view.selection, view.external_shown, view.offset_shown) == ("remote_temperature", 20.2, -0.6)
    assert view.offset_limits == (-10.0, 10.0, 0.2)
    assert (view.shown_c, view.target_shown) == (20.2, 21.0)
    assert not view.selection_by_person

    hass.states.async_set("select.heizkorper_badezimmer_temperature_sensor_select", "local_temperature",
                          {"options": ["local_temperature", "remote_temperature"]}, context=Context(user_id="a-person"))
    assert valve_view(hass, BAD_VALVE).selection_by_person


async def test_a_valve_whose_entities_are_unavailable_is_not_available(hass) -> None:
    from custom_components.thriftherm.adapters.inputs import valve_view

    _trvzbt_valve(hass, available=False)
    assert not valve_view(hass, BAD_VALVE).available


# ------------------------------------------------------------------ the whole way, in Home Assistant
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def _direct(
    hass, freezer, select="local_temperature", trvzb=True, valve_c=18.0, accuracy=None, modes=None, **options
):
    """The bathroom driven directly: its valve `climate.bath` (a real climate entity) with a TRVZB's entities."""
    from datetime import datetime

    from homeassistant.components.climate import HVACMode
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        MockPlatform,
        mock_platform,
    )

    from .test_hand import UNIT_READINGS, Climate
    from .test_integration import _set_states

    freezer.move_to(datetime(2026, 10, 12, 14, 30, tzinfo=dt_util.UTC))
    bath = Climate("bath", HVACMode.HEAT, 17.0, modes or [HVACMode.HEAT, HVACMode.OFF], {"current_temperature": valve_c})
    midea = Climate("midea", HVACMode.OFF, 22.0, [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY],
                    UNIT_READINGS)

    async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
        async_add_entities([bath, midea])

    mock_platform(hass, "test.climate", MockPlatform(async_setup_platform=async_setup_platform))
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()
    _set_states(hass)
    midea.async_write_ha_state()

    owner = MockConfigEntry(domain="mqtt") if trvzb else None
    if owner is None:
        return await _thriftherm_on(hass, freezer, bath, **options)
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("mqtt", "trv_bath")}, manufacturer="SONOFF", model="TRVZB"
    )
    registry = er.async_get(hass)
    registry.async_update_entity("climate.bath", device_id=device.id)
    for domain, key in (("number", "external_temperature_input"), ("select", "temperature_sensor_select"),
                        ("switch", "child_lock"), ("switch", "open_window")):
        registry.async_get_or_create(domain, "mqtt", f"trv_bath_{key}", suggested_object_id=f"bath_{key}",
                                     device_id=device.id, config_entry=owner)
    hass.states.async_set("number.bath_external_temperature_input", "0", {"unit_of_measurement": "°C"})
    hass.states.async_set("select.bath_temperature_sensor_select", select,
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    hass.states.async_set("switch.bath_child_lock", "off")  # off on purpose in the bathroom
    hass.states.async_set("switch.bath_open_window", "on")
    if accuracy is not None:
        registry.async_get_or_create("number", "mqtt", "trv_bath_temperature_accuracy",
                                     suggested_object_id="bath_temperature_accuracy", device_id=device.id,
                                     config_entry=owner)
        hass.states.async_set("number.bath_temperature_accuracy", str(accuracy), {"unit_of_measurement": "°C"})

    return await _thriftherm_on(hass, freezer, bath, **options)


def _quiet_house(hass) -> None:
    """Every room of the test home at its target: none calls, the boiler rests."""
    hass.states.async_set("sensor.bath_temp", "21.5")
    hass.states.async_set("sensor.living_temp", "19.0")


async def _thriftherm_on(hass, freezer, bath, as_thermostat=False, boiler_active=True, **options):
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

    from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN

    from .test_integration import _entry_data

    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity" if as_thermostat else "valve_entities"] = (
        "climate.bath" if as_thermostat else ["climate.bath"]
    )
    data[CONF_ROOMS][0].update(options.pop("room", {}))
    data.update(options.pop("data", {}))
    released = {"room_allow_active_control": True, "midea_allow_active_control": True,
                "boiler_allow_active_control": True, **options}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm", options=released)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # after the entry: its own number, select and switch platforms set up the real services
    calls = {
        "number": async_mock_service(hass, "number", "set_value"),
        "select": async_mock_service(hass, "select", "select_option"),
        "switch_on": async_mock_service(hass, "switch", "turn_on"),
        "switch_off": async_mock_service(hass, "switch", "turn_off"),
        "lock": async_mock_service(hass, "lock", "lock"),
    }
    coordinator.set_room_control_mode("active")
    if boiler_active and coordinator.has_boiler:
        coordinator.set_boiler_control_mode("active")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    return coordinator, bath, calls


async def test_a_valve_driven_directly_gets_the_room_temperature_its_selection_and_its_setpoint(hass, freezer) -> None:
    from .test_hand import _cycle

    coordinator, bath, calls = await _direct(hass, freezer)
    # the room at 18.5 calls: the valve safely above where it shuts, in its half degrees (review 2026-11-15 §3.4)
    assert bath.target_temperature == 21.5
    assert [c.data for c in calls["number"]] == [
        {"entity_id": "number.bath_external_temperature_input", "value": 18.5}  # the room sensor
    ]
    assert [c.data for c in calls["select"]] == [
        {"entity_id": "select.bath_temperature_sensor_select", "option": "remote_temperature"}
    ]
    hass.states.async_set("number.bath_external_temperature_input", "18.5", {"unit_of_measurement": "°C"})
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature"]})
    await _cycle(hass, coordinator, freezer, 60)
    assert len(calls["number"]) == 1 and len(calls["select"]) == 1  # nothing new to say
    hass.states.async_set("sensor.bath_temp", "18.8")
    await _cycle(hass, coordinator, freezer, 70)
    assert calls["number"][-1].data["value"] == 18.8
    # decided 2026-11-13: what is set at the valve is the truth; its child lock and window detection stay untouched
    assert not calls["switch_on"] and not calls["switch_off"] and not calls["lock"]
    plan = coordinator.data["valve_plans"]["climate.bath"]
    assert (plan.way, plan.reason) == ("external", "room_temperature_to_valve")


async def test_the_valve_of_a_room_with_its_window_open_goes_down_to_the_frost_limit(hass, freezer) -> None:
    """The option "frost limit" in place of "off" for an open window."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, window_valve_action="frost")
    hass.states.async_set("binary_sensor.bath_window", "on")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 15 * 60)  # past the window's grace
    assert bath.target_temperature == coordinator.builder.params.frost_temp
    hass.states.async_set("binary_sensor.bath_window", "off")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 70)
    assert bath.target_temperature == 21.5  # calling again
    assert "badezimmer" not in coordinator.overrides  # its own frost setpoint is no hand


async def test_the_valve_memory_survives_a_restart(hass, freezer) -> None:
    from custom_components.thriftherm import persistence

    coordinator, _, _ = await _direct(hass, freezer)
    stored = persistence.to_store(coordinator)["valve_ctrl_memory"]
    assert stored["climate.bath"]["external_c"] == 18.5
    assert stored["climate.bath"]["selection_before"] == "local_temperature"


def test_the_valve_memory_is_read_back_for_the_valves_still_configured():
    from types import SimpleNamespace

    from custom_components.thriftherm import persistence
    from custom_components.thriftherm.engines.valve_plan import ValveMemory
    from custom_components.thriftherm.models import RoomConfig

    rooms = (RoomConfig("bad", "Bad", 1, "sensor.bad", valve_entities=(BAD_VALVE,)),)
    coord = SimpleNamespace(builder=SimpleNamespace(rooms=rooms), valve_ctrl_memory={})
    data = {"valve_ctrl_memory": {BAD_VALVE: {"external_c": 20.4}, "climate.gone": {"external_c": 19.0}}}
    persistence._restore_valves(coord, data)
    assert coord.valve_ctrl_memory == {BAD_VALVE: ValveMemory(external_c=20.4)}


# ------------------------------------------------------------------ review 2026-11-15
def test_a_room_thermostat_driving_another_rooms_valve_is_refused():
    others = [{"key": "bad", "name": "Bad", "valve_entities": [BAD_VALVE]}]
    drives = {"climate.kuche_bt": (BAD_VALVE,)}.get
    room = {**ROOM, "name": "Küche", "climate_entity": "climate.kuche_bt"}
    assert config_schema.validate_room(room, others, None, lambda e: drives(e, ())) == {"climate_entity": "climate_used_twice"}


async def test_a_better_thermostat_entity_is_no_valve(hass) -> None:
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.adapters.inputs import valve_driver

    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": [{"trv": BAD_VALVE}]})
    bt.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("climate", "better_thermostat", "bt_bad", suggested_object_id="badezimmer",
                                          config_entry=bt)
    assert valve_driver(hass, "climate.badezimmer") == "climate.badezimmer"


async def test_a_value_that_did_not_arrive_keeps_the_selection_and_the_valve_waits(hass, freezer) -> None:
    """A selection on the room after a value that failed put the valve on the 0 °C it showed after its new start."""
    from homeassistant.exceptions import HomeAssistantError

    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer)
    calls["select"].clear()

    async def fail(call):
        raise HomeAssistantError("timeout")

    hass.services.async_register("number", "set_value", fail)
    hass.states.async_set("number.bath_external_temperature_input", "0", {"unit_of_measurement": "°C"})
    hass.states.async_set("select.bath_temperature_sensor_select", "local_temperature",
                          {"options": ["local_temperature", "remote_temperature"]})
    await _cycle(hass, coordinator, freezer, 70)
    assert not calls["select"]
    assert coordinator.valve_ctrl_memory["climate.bath"].external_c == 18.5  # what was sent before stays
    retry = coordinator._valve_retry["climate.bath"]
    await _cycle(hass, coordinator, freezer, 60)
    assert coordinator._valve_retry["climate.bath"] == retry  # waited, not tried again at once


async def test_a_valve_better_thermostat_drives_again_is_left_alone(hass, freezer) -> None:
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer)
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath"]})
    bt.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt",
                                          config_entry=bt)
    count = len(calls["number"])
    hass.states.async_set("sensor.bath_temp", "19.4")
    await _cycle(hass, coordinator, freezer, 70)
    assert len(calls["number"]) == count
    assert coordinator.data["valve_plans"]["climate.bath"].way == "driven_elsewhere"


async def test_a_window_opened_shorter_than_its_delay_leaves_the_valve_as_it_is(hass, freezer) -> None:
    """Decided 2026-11-15: the delay "window open" (window_grace_s, default 90 s) holds for the valves as well."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, window_valve_action="frost")
    assert coordinator.builder.params.window_grace_s == 90.0
    hass.states.async_set("binary_sensor.bath_window", "on")
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.target_temperature == 21.5  # open for a minute: within the delay
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.target_temperature == coordinator.builder.params.frost_temp  # two minutes: past it



async def test_a_valve_without_input_or_offset_gets_its_setpoint_corrected(hass, freezer) -> None:
    """Way c, the fallback for every climate entity with a current temperature: this one reads 1 K warm."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, trvzb=False, valve_c=19.5)
    # 21 for the room, 1 K more for a valve reading 1 K warm, 0.5 while the room calls
    assert bath.target_temperature == 22.5
    plan = coordinator.data["room_commands"]["badezimmer"]
    assert plan.target == 22.5
    assert coordinator.room_ctrl_memory["badezimmer"].sent_correction_k == 1.0
    await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides  # its own corrected setpoint is no hand


# ------------------------------------------------------------------ way d, the room's own controller (§3.4, a preview)
def test_a_room_can_be_set_to_its_own_controller():
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(valve_entities=[BAD_VALVE], valve_control="opening")}])
    assert cfg.valve_opening
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(valve_entities=[BAD_VALVE], valve_control="nonsense")}])
    assert not cfg.valve_opening
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(climate_entity="climate.bt", valve_control="opening")}])
    assert not cfg.valve_opening  # a room thermostat regulates itself


def test_a_room_can_force_its_valves_onto_a_way_for_a_test():
    """Review 2026-11-15 item 6: the offset (b) or the corrected setpoint (c), for a day each in the test rooms."""
    for force in ("offset", "setpoint"):
        (cfg,) = room_configs_from_config([{"key": "bad", **_room(valve_entities=[BAD_VALVE], valve_control=force)}])
        assert cfg.valve_force == force and not cfg.valve_opening
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(valve_entities=[BAD_VALVE], valve_control="auto")}])
    assert cfg.valve_force is None
    (cfg,) = room_configs_from_config([{"key": "bad", **_room(climate_entity="climate.bt", valve_control="offset")}])
    assert cfg.valve_force is None


async def test_a_trvzb_forced_to_the_corrected_setpoint_is_put_on_its_own_sensor(hass, freezer) -> None:
    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer, select="remote_temperature", room={"valve_control": "setpoint"})
    await _cycle(hass, coordinator, freezer, 70)
    assert "local_temperature" in [c.data["option"] for c in calls["select"]]
    assert coordinator.data["valve_plans"]["climate.bath"].way == "setpoint"


async def test_a_room_on_its_own_controller_writes_the_opening_and_keeps_the_target_at_the_valve(hass, freezer) -> None:
    from homeassistant.helpers import entity_registry as er

    from custom_components.thriftherm.const import CONF_ROOMS

    from .test_hand import _cycle

    coordinator, bath, calls = await _direct(hass, freezer)
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    entry = coordinator.config_entry
    rooms = [dict(r) for r in (entry.options.get(CONF_ROOMS) or entry.data[CONF_ROOMS])]
    rooms[0]["valve_control"] = "opening"
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await _cycle(hass, coordinator, freezer, 70)
    openings = [c.data["value"] for c in calls["number"] if c.data["entity_id"] == "number.bath_heating_valve_position"]
    assert openings and 0.0 < openings[-1] <= 100.0
    assert bath.target_temperature == 21.0  # no lead: the valve's own control closes it at the target (the net)
    assert coordinator.data["valve_plans"]["climate.bath"].way == "opening"


async def test_a_valve_not_opened_for_a_week_is_moved_and_then_given_its_setpoint_again(hass, freezer) -> None:
    from dataclasses import replace

    from .test_hand import _cycle

    # a TRVZB says no truth with its hvac_action: at its setpoint it counts as shut
    coordinator, bath, _ = await _direct(hass, freezer, valve_c=21.5)
    mem = coordinator.valve_ctrl_memory["climate.bath"]
    coordinator.valve_ctrl_memory["climate.bath"] = replace(mem, opened_ts=mem.opened_ts - 8 * 86400)
    _quiet_house(hass)  # no room calls, the boiler does not heat: an exercise may begin (concept §5.6)
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    hour = dt_util.as_local(dt_util.utcnow()).hour  # an exercise begins between 10 and 18 o'clock local time
    freezer.tick(timedelta(hours=(12 - hour) % 24))
    seen = []
    for _ in range(6):
        await _cycle(hass, coordinator, freezer, 120)  # a step lasts 90 s
        seen.append(bath.target_temperature)
    assert seen == [30.0, 5.0, 30.0, 5.0, 21.0, 21.0]  # the room at its target: no call, no margin
    assert "badezimmer" not in coordinator.overrides  # its own moves are no hand
    assert coordinator.data["room_commands"]["badezimmer"].reason != "valve_exercise"



async def test_the_valves_of_a_room_with_its_window_open_are_off_and_on_again_once_it_is_shut(hass, freezer) -> None:
    """Decided 2026-11-15: off is the default, as Better and Versatile Thermostat do it."""
    from homeassistant.components.climate import HVACMode

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    assert coordinator.builder.params.window_valve_off
    hass.states.async_set("binary_sensor.bath_window", "on")
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.HEAT  # within the delay
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.OFF and bath.target_temperature == 21.5
    assert coordinator.data["room_commands"]["badezimmer"].reason == "window_open_off"
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.OFF  # not taken for a person's off
    hass.states.async_set("binary_sensor.bath_window", "off")
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.HEAT and bath.target_temperature == 21.5
    await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides
    assert coordinator.data["room_commands"]["badezimmer"].reason == "in_sync"


async def test_a_valve_a_person_switched_off_stays_off_whatever_the_window(hass, freezer, hass_admin_user) -> None:
    from homeassistant.components.climate import HVACMode
    from homeassistant.core import Context

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": "climate.bath", "hvac_mode": "off"},
                                   blocking=True, context=Context(user_id=hass_admin_user.id))
    hass.states.async_set("binary_sensor.bath_window", "on")
    await _cycle(hass, coordinator, freezer, 150)
    hass.states.async_set("binary_sensor.bath_window", "off")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.OFF


# ------------------------------------------------------------------ review 2026-11-15, second round
async def _on_own_controller(hass, freezer, coordinator):
    from homeassistant.helpers import entity_registry as er

    from custom_components.thriftherm.const import CONF_ROOMS

    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    entry = coordinator.config_entry
    rooms = [dict(r) for r in (entry.options.get(CONF_ROOMS) or entry.data[CONF_ROOMS])]
    rooms[0]["valve_control"] = "opening"
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_on_its_own_controller_a_valve_without_a_room_reading_is_handed_back_fully_open(hass, freezer) -> None:
    """Review 2 (2): left at 3 %, a room whose sensor died was never heated again."""
    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer)
    coordinator = await _on_own_controller(hass, freezer, coordinator)
    hass.states.async_set("sensor.bath_temp", "22.5")  # above the target: shut
    for _ in range(2):
        await _cycle(hass, coordinator, freezer, 330)
    openings = [c.data["value"] for c in calls["number"] if c.data["entity_id"] == "number.bath_heating_valve_position"]
    assert openings[-1] == 3.0  # a TRVZB is never shut below 3 %
    hass.states.async_set("sensor.bath_temp", "unavailable")
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 330)
    openings = [c.data["value"] for c in calls["number"] if c.data["entity_id"] == "number.bath_heating_valve_position"]
    assert openings[-1] == 100.0


async def test_a_turn_keeps_the_correction_apart_from_the_lead(hass, freezer) -> None:
    """Review 2 (7): the correction of way c in a turn's extra read as a lead afterwards."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, trvzb=False, valve_c=19.5)
    assert coordinator.room_ctrl_memory["badezimmer"].sent_correction_k == 1.0
    await _cycle(hass, coordinator, freezer, 70)
    bath.at_the_device(target=23.0)
    await _cycle(hass, coordinator, freezer, 70)
    mem = coordinator.room_ctrl_memory["badezimmer"]
    # 23 shown, 1 of it the correction and 0.5 the margin of the calling room
    assert coordinator.overrides["badezimmer"].target == 21.5
    assert mem.sent_correction_k == 1.0


async def test_the_correction_is_not_learned_while_the_window_is_open(hass, freezer) -> None:
    """Review 2 (9): cold air at the room sensor skewed it for half an hour after."""
    from .test_hand import _cycle

    coordinator, _, _ = await _direct(hass, freezer, trvzb=False, valve_c=19.5, window_valve_action="frost")
    learned = coordinator.valve_ctrl_memory["climate.bath"].correction
    hass.states.async_set("binary_sensor.bath_window", "on")
    await _cycle(hass, coordinator, freezer, 150)
    hass.states.async_set("sensor.bath_temp", "12.0")
    await _cycle(hass, coordinator, freezer, 60)
    assert coordinator.valve_ctrl_memory["climate.bath"].correction.ema_k == learned.ema_k


async def test_an_exercise_goes_on_while_the_valves_other_writes_fail(hass, freezer) -> None:
    """Review 2 (3): not committed with the failed writes, it began anew every few minutes."""
    from dataclasses import replace
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util import dt as dt_util

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, valve_c=21.5)
    mem = coordinator.valve_ctrl_memory["climate.bath"]
    coordinator.valve_ctrl_memory["climate.bath"] = replace(mem, opened_ts=mem.opened_ts - 8 * 86400)
    _quiet_house(hass)  # no room calls, the boiler does not heat: an exercise may begin (concept §5.6)

    async def fail(call):
        raise HomeAssistantError("timeout")

    hass.services.async_register("number", "set_value", fail)
    hour = dt_util.as_local(dt_util.utcnow()).hour
    freezer.tick(timedelta(hours=(12 - hour) % 24))
    seen = []
    for _ in range(6):
        hass.states.async_set("sensor.bath_temp", str(18.5 + 0.2 * len(seen)))  # a new value due each cycle
        await _cycle(hass, coordinator, freezer, 120)
        seen.append(bath.target_temperature)
    assert seen == [30.0, 5.0, 30.0, 5.0, 21.5, 21.5]


# ------------------------------------------------------------------ the valve's own hysteresis (2026-11-15, 03:59)
async def test_a_valve_driven_directly_that_says_idle_does_not_keep_its_room_from_calling(hass, freezer) -> None:
    """Seen 2026-11-15: the bathroom at 19.6 for 20 did not call, its TRVZB said idle (it opens 1 K below only)."""
    coordinator, bath, _ = await _direct(hass, freezer)
    bath.extra["hvac_action"] = "idle"
    bath.async_write_ha_state()
    from .test_hand import _cycle

    await _cycle(hass, coordinator, freezer, 70)
    assert hass.states.get("sensor.thriftherm_badezimmer_heat_demand").attributes["boiler_call"] == "calling"


async def test_a_calling_room_gives_its_valve_its_hysteresis_on_top(hass, freezer) -> None:
    """The valve opens only `accuracy` below its setpoint: while the room calls, the setpoint is that much higher."""
    from homeassistant.helpers import entity_registry as er

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_temperature_accuracy",
                                 suggested_object_id="bath_temperature_accuracy", device_id=device)
    hass.states.async_set("number.bath_temperature_accuracy", "-1.0", {"unit_of_measurement": "°C"})
    await _cycle(hass, coordinator, freezer, 70)
    calling = coordinator.data["room_commands"]["badezimmer"]
    assert bath.target_temperature == 22.0 and calling.target == 22.0  # 21 for the room at 18.5, 1 K for the valve



async def test_a_valve_entered_as_room_thermostat_gets_its_hysteresis_and_its_idle_does_not_block(hass, freezer) -> None:
    """A TRV entered as the room's thermostat (0.7.5 and before): the same hysteresis, the same idle (2026-11-15)."""
    coordinator, bath, _ = await _direct(hass, freezer, accuracy=-1.0, as_thermostat=True)
    bath.extra["hvac_action"] = "idle"
    bath.async_write_ha_state()
    from .test_hand import _cycle

    await _cycle(hass, coordinator, freezer, 70)
    assert hass.states.get("sensor.thriftherm_badezimmer_heat_demand").attributes["boiler_call"] == "calling"
    assert bath.target_temperature == 22.0


async def test_a_valve_without_a_known_hysteresis_entered_as_room_thermostat_keeps_its_idle(hass, freezer) -> None:
    """Without the entity nothing changes for a room thermostat: an idle valve keeps the room from calling."""
    coordinator, bath, _ = await _direct(hass, freezer, as_thermostat=True)
    bath.extra["hvac_action"] = "idle"
    bath.async_write_ha_state()
    from .test_hand import _cycle

    await _cycle(hass, coordinator, freezer, 70)
    assert hass.states.get("sensor.thriftherm_badezimmer_heat_demand").attributes["boiler_call"] == "thermostat_idle"


async def test_the_valves_behind_better_thermostat_with_a_coarse_threshold_are_named(hass, freezer) -> None:
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.models import RoomConfig

    coordinator, _, _ = await _direct(hass, freezer, accuracy=-1.0)
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath"]})
    bt.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt",
                                          config_entry=bt)
    room = RoomConfig("badezimmer", "Badezimmer", 1, "sensor.bath_temp", climate_entity="climate.bath_bt")
    coordinator.builder._valve_entities.clear()
    assert coordinator.coarse_valves(room) == (("climate.bath", 1.0),)
    hass.states.async_set("number.bath_temperature_accuracy", "-0.4", {"unit_of_measurement": "°C"})
    assert coordinator.coarse_valves(room) == ()
    direct = RoomConfig("badezimmer", "Badezimmer", 1, "sensor.bath_temp", climate_entity="climate.bath",
                        valve_entities=("climate.bath",))
    assert coordinator.coarse_valves(direct) == ()  # Thriftherm allows for it itself


async def test_the_switched_off_selection_of_a_valve_driven_directly_is_named(hass, freezer) -> None:
    from homeassistant.helpers import entity_registry as er

    coordinator, _, calls = await _direct(hass, freezer)
    er.async_get(hass).async_update_entity(
        "select.bath_temperature_sensor_select", disabled_by=er.RegistryEntryDisabler.USER
    )
    room = coordinator.builder.rooms[0]
    assert coordinator.disabled_valve_entities(room) == ("select.bath_temperature_sensor_select",)
    from .test_hand import _cycle

    count = len(calls["number"])
    hass.states.async_set("sensor.bath_temp", "19.3")
    await _cycle(hass, coordinator, freezer, 70)
    assert coordinator.data["valve_plans"]["climate.bath"].way != "external"  # its input went unheeded
    assert len(calls["number"]) == count


# ------------------------------------------------------------------ a room without its own sensor (2026-11-15)
def test_a_room_needs_no_sensor_of_its_own():
    """Asked 2026-11-15: as in Better Thermostat, the room's sensor is a choice; without it the valve measures."""
    room = {"name": "Flur", "climate_entity": "climate.flur", "schedule_weekday": "", "schedule_weekend": ""}
    assert config_schema.validate_room(room) == {}


def test_drying_still_needs_a_sensor_of_the_room():
    """The humidity belongs to the room's air; with the valve's warm reading a drying started for nothing."""
    room = {"name": "Bad", "climate_entity": "climate.bad", "humidity_sensor": "sensor.bad_rh", "served_by_midea": True,
            "bathroom_drying_mode": True, "schedule_weekday": "", "schedule_weekend": ""}
    assert config_schema.validate_room(room) == {"temperature_sensor": "drying_needs_temperature"}


def test_without_a_room_sensor_the_room_is_no_fault():
    from dataclasses import replace
    from datetime import datetime

    from custom_components.thriftherm.engines import room as room_engine
    from custom_components.thriftherm.engines import safety
    from custom_components.thriftherm.models import SensorReading

    from .conftest import PARAMS, room_config, room_state, snapshot

    cfg = replace(room_config("flur"), temperature_sensor=None)
    state = replace(room_state(cfg, temp=None), temperature=SensorReading.missing(None, "not_configured"),
                    trv_local_temp=20.5, trv_local_entity="climate.flur")
    snap = snapshot({"flur": state})
    now = datetime(2026, 1, 12, 18, 0)
    rooms = {k: room_engine.evaluate_room(s, now, "auto", PARAMS) for k, s in snap.rooms.items()}
    assert rooms["flur"].temperature == 20.5
    res = safety.evaluate(snap, rooms)
    assert res.state == "ok" and not res.issues


async def test_a_valve_driven_directly_in_a_room_without_its_own_sensor_goes_by_its_own(hass, freezer) -> None:
    from custom_components.thriftherm.const import CONF_ROOMS

    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer, select="remote_temperature")
    entry = coordinator.config_entry
    rooms = [dict(r) for r in (entry.options.get(CONF_ROOMS) or entry.data[CONF_ROOMS])]
    rooms[0].pop("temperature_sensor", None)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    calls["number"].clear()
    await _cycle(hass, coordinator, freezer, 70)
    plan = coordinator.data["valve_plans"]["climate.bath"]
    assert (plan.way, plan.reason) == ("own_sensor", "no_room_sensor")
    assert not calls["number"]  # no room temperature, no offset
    assert calls["select"][-1].data["option"] == "local_temperature"


def test_a_room_without_sensor_thermostat_or_valve_has_nothing_to_measure_by():
    room = {"name": "Flur", "schedule_weekday": "", "schedule_weekend": ""}
    assert config_schema.validate_room(room) == {"temperature_sensor": "room_needs_a_temperature"}


def test_a_room_set_up_without_a_sensor_learns_with_the_valves_reading_a_failed_sensor_does_not():
    """Decided 2026-11-15: a room without a sensor of its own is controlled by the valve's reading, so it learns by it.
    A room whose sensor failed does not: rates of the radiator's corner are not the room's. A sensor added later starts
    the learning anew (the room's fingerprint holds its sensor)."""
    from custom_components.thriftherm.coordinator import _measures_the_room

    assert _measures_the_room(("using_trv_local_temperature:not_configured",))
    assert not _measures_the_room(("using_trv_local_temperature:stale",))
    assert _measures_the_room(())


async def test_a_room_whose_valve_is_being_exercised_or_on_a_flat_battery_is_marked(hass, freezer) -> None:
    """Review 2026-11-15 item 4: the learning leaves out the hours a direct room's valve cannot be open."""
    from dataclasses import replace
    from datetime import timedelta

    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, valve_c=21.5)
    assert coordinator.data["rooms"]["badezimmer"].valve_unable is None
    mem = coordinator.valve_ctrl_memory["climate.bath"]
    coordinator.valve_ctrl_memory["climate.bath"] = replace(mem, opened_ts=mem.opened_ts - 8 * 86400)
    _quiet_house(hass)  # no room calls, the boiler does not heat: an exercise may begin (concept §5.6)
    hour = dt_util.as_local(dt_util.utcnow()).hour
    freezer.tick(timedelta(hours=(12 - hour) % 24))
    await _cycle(hass, coordinator, freezer, 120)
    await _cycle(hass, coordinator, freezer, 120)
    assert coordinator.valve_ctrl_memory["climate.bath"].exercise_step
    assert coordinator.data["rooms"]["badezimmer"].valve_unable == "exercise"
    for _ in range(5):
        await _cycle(hass, coordinator, freezer, 120)
    assert coordinator.data["rooms"]["badezimmer"].valve_unable is None

    device = er.async_get(hass).async_get("climate.bath").device_id
    owner = er.async_get(hass).async_get("switch.bath_child_lock").config_entry_id
    er.async_get(hass).async_get_or_create("sensor", "mqtt", "trv_bath_battery", suggested_object_id="bath_battery",
                                           device_id=device, config_entry=hass.config_entries.async_get_entry(owner))
    hass.states.async_set("sensor.bath_battery", "4", {"unit_of_measurement": "%", "device_class": "battery"})
    await _cycle(hass, coordinator, freezer, 70)
    assert coordinator.data["rooms"]["badezimmer"].valve_unable == "battery"
    hass.states.async_set("sensor.bath_battery", "60", {"unit_of_measurement": "%", "device_class": "battery"})
    await _cycle(hass, coordinator, freezer, 70)
    assert coordinator.data["rooms"]["badezimmer"].valve_unable is None


# ------------------------------------------------------------------ concept §7: when Thriftherm goes
async def _given_back(hass, freezer, how):
    from homeassistant.config_entries import ConfigEntryDisabler

    from custom_components.thriftherm.const import CONF_ROOMS

    coordinator, _, calls = await _direct(hass, freezer)
    # the mock services record only: the valve as the first cycle left it
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    entry = coordinator.config_entry
    calls["select"].clear()
    if how == "disable":
        await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    elif how == "remove":
        await hass.config_entries.async_remove(entry.entry_id)
    elif how == "stop":
        # stage 1 of a stop: before EVENT_HOMEASSISTANT_STOP, on which MQTT and ZHA go down (review #2)
        for job in list(hass._shutdown_jobs):
            if (task := hass.async_run_hass_job(job.job, *job.args)) is not None:
                await task
        await hass.async_block_till_done()  # handed back here already; the stop event only saves
    else:  # an options change reloads it: the next run goes on with the valves
        rooms = [dict(r) for r in (entry.options.get(CONF_ROOMS) or entry.data[CONF_ROOMS])]
        rooms[0]["name"] = "Bad"
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    return [c.data["option"] for c in calls["select"] if c.data["entity_id"] == "select.bath_temperature_sensor_select"], calls


@pytest.mark.parametrize("how", ["disable", "remove", "stop"])
async def test_the_valves_are_handed_back_when_thriftherm_goes(hass, freezer, how) -> None:
    options, calls = await _given_back(hass, freezer, how)
    assert options == ["local_temperature"]
    assert not calls["switch_on"] and not calls["switch_off"]  # the child lock is never touched


async def test_a_reload_does_not_hand_the_valves_back(hass, freezer) -> None:
    options, _ = await _given_back(hass, freezer, "reload")
    assert "local_temperature" not in options


async def test_a_room_no_longer_driven_directly_hands_its_valves_back(hass, freezer) -> None:
    """§7, direct → room thermostat: the reload's new run drives the valve no more, the run before gives it back."""
    from custom_components.thriftherm.const import CONF_ROOMS

    coordinator, _, calls = await _direct(hass, freezer)
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    entry = coordinator.config_entry
    calls["select"].clear()
    rooms = [dict(r) for r in (entry.options.get(CONF_ROOMS) or entry.data[CONF_ROOMS])]
    rooms[0].pop("valve_entities")
    rooms[0]["climate_entity"] = "climate.bath"
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    assert [c.data["option"] for c in calls["select"]] == ["local_temperature"]



# ------------------------------------------------------------------ decided 15.11.: no own boiler, no call to serve
async def _calling_with_accuracy(hass, freezer, **kw):
    from homeassistant.helpers import entity_registry as er

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, **kw)
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_temperature_accuracy",
                                 suggested_object_id="bath_temperature_accuracy", device_id=device)
    hass.states.async_set("number.bath_temperature_accuracy", "-0.4", {"unit_of_measurement": "°C"})
    await _cycle(hass, coordinator, freezer, 70)
    return coordinator, bath


async def test_with_its_boiler_run_by_thriftherm_a_calling_valve_stands_above_where_it_shuts(hass, freezer) -> None:
    _, bath = await _calling_with_accuracy(hass, freezer)
    assert bath.target_temperature == 21.5  # 21 for the room at 18.5 + 0.4 rounded up to the valve's 0.5


@pytest.mark.parametrize("how", [{"data": {"system_type": "district"}}, {"data": {"system_type": "none"}},
                                 {"boiler_active": False}])
async def test_without_a_boiler_to_call_the_valve_gets_the_target_and_controls_by_itself(hass, freezer, how) -> None:
    """District heating, no central heat source, or a boiler Thriftherm only observes: the valve is the only thing that
    controls the room, and no call starts a burner. No margin, no band: the target, as Better Thermostat gives it."""
    _, bath = await _calling_with_accuracy(hass, freezer, **how)
    assert bath.target_temperature == 21.0


async def test_a_valve_entered_as_room_thermostat_gets_no_margin_with_district_heating(hass, freezer) -> None:
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, accuracy=-1.0, as_thermostat=True,
                                         data={"system_type": "district"})
    await _cycle(hass, coordinator, freezer, 70)
    assert bath.target_temperature == 21.0


async def test_a_valve_off_for_the_window_is_switched_on_and_set_to_the_target_when_thriftherm_goes(hass, freezer) -> None:
    """Review 2026-11-15 (2) #1: disabled while the window had the valve off, the room never heated again."""
    from homeassistant.config_entries import ConfigEntryDisabler

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    hass.states.async_set("binary_sensor.bath_window", "on")
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 70)
    assert bath.hvac_mode == "off"
    await hass.config_entries.async_set_disabled_by(coordinator.config_entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    assert bath.hvac_mode == "heat" and bath.target_temperature == 21.0


async def test_leaving_active_room_control_hands_the_valves_back(hass, freezer) -> None:
    """Review #6: an offset or our setpoint stayed for good, and nothing later handed them back."""
    coordinator, _, calls = await _direct(hass, freezer)
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    calls["select"].clear()
    coordinator.set_room_control_mode("shadow")
    await hass.async_block_till_done()
    assert [c.data["option"] for c in calls["select"]] == ["local_temperature"]


def test_the_margin_of_a_room_counts_its_coarsest_valve_in_half_degrees(hass, monkeypatch):
    """Review (2) #4: only the first valve's threshold counted; #5: a step under 0.5 lost the rounding up in `decide`;
    a room the boiler helps calls until the target (end 0.0)."""
    from types import SimpleNamespace

    from custom_components.thriftherm import coordinator as module

    room = SimpleNamespace(valve_entities=("climate.a", "climate.b"), climate_entity="climate.a")
    thresholds = {"climate.a": 0.4, "climate.b": 1.0}
    monkeypatch.setattr(module, "valve_view", lambda hass, valve: SimpleNamespace(hysteresis_k=thresholds[valve]))
    assert module.direct_margin(hass, room, True, end_above_k=-0.1, step=0.5) == 1.0
    thresholds["climate.b"] = 0.2
    # a step of 0.1 is rounded in half degrees all the same: 0.5 above with the end 0.5 above the target is 1.0
    assert module.direct_margin(hass, room, True, end_above_k=0.5, step=0.1) == 1.0
    assert module.direct_margin(hass, room, False, end_above_k=0.5, step=0.1) == 0.0


# ------------------------------------------------------------------ Apple Home: a room thermostat of Thriftherm's own
async def test_a_room_driven_directly_has_a_room_climate_for_apple_home(hass, freezer) -> None:
    """Driven directly a room has no Better Thermostat any more, and with it no tile in Apple Home and no Siri (decided
    steers by Siri). Thriftherm's room climate stands in, without cooling too: heat and off, its own target, the room's
    temperature; a setpoint set on it is an override."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    await _cycle(hass, coordinator, freezer, 70)
    state = hass.states.get("climate.thriftherm_badezimmer_room_climate")
    assert state is not None and state.state == "heat"
    assert state.attributes["hvac_modes"] == ["heat", "off"]
    assert state.attributes["temperature"] == 21.0 and state.attributes["current_temperature"] == 18.5
    # a room with its own thermostat has one too since 2026-11-17 (Apple Home: one of the two is left out of the bridge)
    assert hass.states.get("climate.thriftherm_wohnzimmer_room_climate").attributes["hvac_modes"] == ["heat", "off"]
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.thriftherm_badezimmer_room_climate", "temperature": 22.0},
        blocking=True,
    )
    assert coordinator.overrides["badezimmer"].target == 22.0
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.thriftherm_badezimmer_room_climate", "hvac_mode": "off"},
        blocking=True,
    )
    assert coordinator.overrides["badezimmer"].target == coordinator.builder.params.frost_temp
    assert hass.states.get("climate.thriftherm_badezimmer_room_climate").state == "off"



async def test_a_valve_without_an_off_gets_the_frost_limit_while_the_window_is_open(hass, freezer) -> None:
    """Final review 2026-11-15 #1: a valve that offers "heat" only refused the off, and, held without a setpoint, went on
    heating with the window open."""
    from homeassistant.components.climate import HVACMode

    from .test_hand import _cycle

    coordinator, bath, calls = await _direct(hass, freezer, modes=[HVACMode.HEAT])
    hass.states.async_set("binary_sensor.bath_window", "on")
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 60)
    assert bath.hvac_mode == HVACMode.HEAT
    assert bath.target_temperature == coordinator.builder.params.frost_temp


async def test_a_valve_that_does_not_take_a_value_is_not_written_every_cycle(hass, freezer) -> None:
    """Final review 2026-11-15 #2: a call over MQTT never fails, so the backoff never held; a valve that did not show
    the value (fallen back on its own sensor, out of reach) got it every cycle. Once more at once, then further apart."""
    from .test_hand import _cycle

    coordinator, _, calls = await _direct(hass, freezer)  # the mock services take nothing: the selection stays local
    calls["select"].clear()
    for _ in range(15):
        await _cycle(hass, coordinator, freezer, 60)
    sent = [c for c in calls["select"] if c.data["entity_id"] == "select.bath_temperature_sensor_select"]
    assert 1 <= len(sent) <= 4  # 15 minutes: at once, again, then after 5 and 10 minutes
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    await _cycle(hass, coordinator, freezer, 60)
    # taken: the next asking starts afresh (a local selection now would be a person's, and stays)
    assert not [k for k in coordinator._valve_unanswered if k[0] == "select.bath_temperature_sensor_select"]


@pytest.mark.parametrize("busy", ["window", "boiler"])
async def test_no_valve_exercise_begins_while_the_boiler_heats_or_the_window_is_open(hass, freezer, busy) -> None:
    """Final review 2026-11-15 #6 (concept §5.6): its "shut" closed the valve for minutes while the boiler heated, and
    begun with the window open it stood at its first step."""
    from dataclasses import replace

    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer, valve_c=21.5)  # 15:30 local: within the hours of an exercise
    if busy == "window":
        _quiet_house(hass)
        hass.states.async_set("binary_sensor.bath_window", "on")
    for _ in range(2):  # the window past its delay; or the bathroom calling, the boiler heating, as the test home is
        await _cycle(hass, coordinator, freezer, 60)
    mem = coordinator.valve_ctrl_memory["climate.bath"]
    coordinator.valve_ctrl_memory["climate.bath"] = replace(mem, opened_ts=mem.opened_ts - 8 * 86400)
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 120)
    assert coordinator.valve_ctrl_memory["climate.bath"].exercise_step == 0
    assert bath.target_temperature not in (30.0, 5.0)

async def test_leaving_active_room_control_sets_our_own_setpoint_back_to_the_target(hass, freezer) -> None:
    """Final review 2026-11-15 #4: the room memory was cleared before the hand-back looked at it, and the first valve
    kept its margin."""
    from .test_hand import _cycle

    coordinator, bath, _ = await _direct(hass, freezer)
    await _cycle(hass, coordinator, freezer, 70)
    assert bath.target_temperature == 21.5  # calling: the margin on top
    coordinator.set_room_control_mode("shadow")
    await hass.async_block_till_done()
    assert bath.target_temperature == 21.0


async def test_leaving_active_room_control_hands_back_with_what_was_sent_to_every_room(hass, freezer, monkeypatch) -> None:
    """#4 with several rooms: the memory is cleared as the mode changes, so what was sent is taken along at once."""
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    coordinator, _, _ = await _direct(hass, freezer)
    coordinator.room_ctrl_memory = {"badezimmer": RoomCtrlMemory(last_sent_target=21.5),
                                    "wohnzimmer": RoomCtrlMemory(last_sent_target=19.0)}
    seen = {}

    async def record(only=None, leaving=False, sent=None):
        seen.update(sent or {})

    monkeypatch.setattr(coordinator, "async_hand_back_valves", record)
    coordinator.set_room_control_mode("shadow")
    await hass.async_block_till_done()
    assert seen == {"badezimmer": 21.5, "wohnzimmer": 19.0}


def test_the_room_climate_speaks_the_native_names_and_the_old_ones_only_where_home_assistant_has_no_others():
    """Home Assistant 2026.11 warned three times (the old names stop working in 2027.11): unit, current and target
    temperature are native_* now. The old names stand only on a Home Assistant that does not know the new ones yet."""
    from homeassistant.components.climate import ClimateEntity

    from custom_components.thriftherm.climate import RoomClimate

    for name in ("native_current_temperature", "native_target_temperature", "_attr_native_temperature_unit"):
        assert name in RoomClimate.__dict__
    old = {"current_temperature", "target_temperature", "_attr_temperature_unit"} & set(RoomClimate.__dict__)
    assert old == (set() if hasattr(ClimateEntity, "native_current_temperature") else
                   {"current_temperature", "target_temperature", "_attr_temperature_unit"})
