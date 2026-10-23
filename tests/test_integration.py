"""Integration tests against a real Home Assistant test instance."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.thriftherm.const import (
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_STATE_NUMBER,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_GAS_FLOW,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_POWER,
    CONF_OUTDOOR_TEMP_SENSORS,
    CONF_ROOM_HUMIDITY,
    CONF_ROOM_KEY,
    CONF_ROOM_HEAT_PUMP,
    CONF_ROOM_NAME,
    CONF_ROOM_PRIORITY,
    CONF_ROOM_TEMP,
    CONF_ROOM_WINDOWS,
    CONF_ROOMS,
    DOMAIN,
)
from custom_components.thriftherm.config_schema import ROOM_ACTION_ADD


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _entry_data() -> dict:
    return {
        CONF_BOILER_FLOW_TEMP: "sensor.flow",
        CONF_BOILER_RETURN_TEMP: "sensor.return",
        CONF_BOILER_PUMP_STATE: "sensor.pump",
        CONF_BOILER_SIGNAL: "binary_sensor.ebus",
        CONF_GAS_FLOW: "sensor.gas_flow",
        CONF_HEAT_PUMP_CLIMATE: "climate.midea",
        CONF_HEAT_PUMP_POWER: "sensor.midea_power",
        CONF_OUTDOOR_TEMP_SENSORS: ["sensor.outdoor"],
        CONF_ROOMS: [
            {
                CONF_ROOM_KEY: "badezimmer",
                CONF_ROOM_NAME: "Badezimmer",
                CONF_ROOM_PRIORITY: 1,
                CONF_ROOM_TEMP: "sensor.bath_temp",
                CONF_ROOM_HUMIDITY: "sensor.bath_rh",
                CONF_ROOM_WINDOWS: ["binary_sensor.bath_window"],
                CONF_ROOM_HEAT_PUMP: True,
                "comfort_temp": 21.0,
                "setback_temp": 17.0,
                "schedule_weekday": "00:00-23:59",
                "schedule_weekend": "00:00-23:59",
            },
            {
                CONF_ROOM_KEY: "wohnzimmer",
                CONF_ROOM_NAME: "Wohnzimmer",
                CONF_ROOM_PRIORITY: 3,
                CONF_ROOM_TEMP: "sensor.living_temp",
                CONF_ROOM_HEAT_PUMP: False,
                "comfort_temp": 18.5,
                "setback_temp": 17.0,
                "schedule_weekday": "00:00-23:59",
                "schedule_weekend": "00:00-23:59",
            },
        ],
    }


def _set_states(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.flow", "45.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.return", "38.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.pump", "on")
    hass.states.async_set("binary_sensor.ebus", "on")
    hass.states.async_set("sensor.gas_flow", "1.2")
    hass.states.async_set("sensor.midea_power", "12.5")
    hass.states.async_set("sensor.outdoor", "4.0")
    hass.states.async_set("sensor.bath_temp", "18.5")
    hass.states.async_set("sensor.bath_rh", "60")
    hass.states.async_set("binary_sensor.bath_window", "off")
    hass.states.async_set("sensor.living_temp", "17.0")
    hass.states.async_set(
        "climate.midea",
        "fan_only",
        {
            "hvac_modes": ["off", "cool", "heat", "fan_only"],
            "temperature": 22,
            "indoor_temperature": 21,
            "outdoor_temperature": 21.5,
            "compressor_frequency": 0,
            "indoor_fan_speed": 496,
            "realtime_power": 13.3,
            "error_code": 0,
        },
    )


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options=options or {}, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_and_observes(hass: HomeAssistant) -> None:
    await _setup(hass)

    advice = hass.states.get("sensor.thriftherm_recommended_heat_source")
    assert advice is not None
    assert advice.state == "boiler"  # midea in fan_only (user mode) -> blocked
    assert advice.attributes["observation_only"] is True

    reason = hass.states.get("sensor.thriftherm_decision_reason")
    assert reason is not None
    assert "heat_pump_unavailable" in reason.attributes["reason_codes"]

    be = hass.states.get("sensor.thriftherm_break_even_cop")
    assert be is not None
    assert float(be.state) == pytest.approx(2.89, abs=0.01)

    cop = hass.states.get("sensor.thriftherm_heat_pump_cop")
    assert cop is not None
    assert cop.state == "unknown"  # no calibration, no sensors -> unavailable value
    assert "airflow_curve_not_calibrated" in cop.attributes["gate_reasons"]

    safety = hass.states.get("sensor.thriftherm_safety_state")
    assert safety is not None
    assert safety.state == "ok"

    bath_demand = hass.states.get("sensor.thriftherm_badezimmer_heat_demand")
    assert bath_demand is not None
    assert float(bath_demand.state) == 100.0

    # a detail sensor: computed, but disabled until enabled, so it writes nothing to the recorder
    from homeassistant.helpers import entity_registry as er

    dew = er.async_get(hass).async_get("sensor.thriftherm_badezimmer_dew_point")
    assert dew is not None and dew.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.thriftherm_badezimmer_dew_point") is None
    rooms = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.data["rooms"]
    assert 10.0 < rooms["badezimmer"].dew_point_c < 11.5

    heating = hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active")
    assert heating is not None and heating.state == "on"

    gas_power = hass.states.get("sensor.thriftherm_gas_power")
    assert gas_power is not None
    assert float(gas_power.state) == pytest.approx(12889, rel=0.01)
    assert hass.states.get("sensor.thriftherm_gas_energy") is None  # only the flow is configured

    mode = hass.states.get("select.thriftherm_operating_mode")
    assert mode is not None and mode.state == "auto"


async def test_window_open_and_mode_change(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    hass.states.async_set("binary_sensor.bath_window", "on")
    await hass.async_block_till_done()
    # grace period not yet elapsed -> still closed from the engine's point of view
    win = hass.states.get("binary_sensor.thriftherm_badezimmer_window_open")
    assert win is not None and win.state == "off"

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    win = hass.states.get("binary_sensor.thriftherm_badezimmer_window_open")
    assert win is not None and win.state == "on"
    allowed = hass.states.get("binary_sensor.thriftherm_badezimmer_heating_allowed")
    assert allowed is not None and allowed.state == "off"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.thriftherm_operating_mode", "option": "off"},
        blocking=True,
    )
    await hass.async_block_till_done()
    advice = hass.states.get("sensor.thriftherm_recommended_heat_source")
    assert advice is not None and advice.state == "none"


async def test_sensor_failure_degrades_but_keeps_running(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    hass.states.async_set("sensor.bath_temp", "unavailable")
    freezer.tick(timedelta(seconds=70))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    safety = hass.states.get("sensor.thriftherm_safety_state")
    assert safety is not None and safety.state == "degraded"
    assert "room_temperature_missing:badezimmer" in safety.attributes["issues"]
    advice = hass.states.get("sensor.thriftherm_recommended_heat_source")
    assert advice is not None and advice.state == "boiler"  # living room still requests heat


async def test_config_flow_full_path(hass: HomeAssistant) -> None:
    _set_states(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form" and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"system_type": "gas"})
    assert result["step_id"] == "prices"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"electricity_price": 0.306, "gas_price": 0.0889, "boiler_efficiency": 0.84, "gas_calorific_value_kwh_m3": 11.45, "gas_z_factor": 0.9381},
    )
    assert result["step_id"] == "boiler"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"boiler_flow_temp": "sensor.flow", "boiler_circulation_l_h": 860})
    assert result["step_id"] == "midea"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"midea_climate": "climate.midea", "midea_airflow_curve": "bad"})
    assert result["errors"] == {"midea_airflow_curve": "invalid_airflow_curve"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"midea_climate": "climate.midea"})
    assert result["step_id"] == "outdoor"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"outdoor_temp_sensors": ["sensor.outdoor"]})
    assert result["step_id"] == "room"
    room = {
        "name": "Badezimmer",
        "priority": 1,
        "temperature_sensor": "sensor.bath_temp",
        "served_by_midea": True,
        "comfort_temp": 21.0,
        "setback_temp": 17.0,
        "schedule_weekday": "05:15-07:30,17:30-22:00",
        "schedule_weekend": "09:00-22:00",
        "add_another": True,
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], room)
    assert result["step_id"] == "room"
    bad = {**room, "name": "Küche", "schedule_weekday": "nonsense", "add_another": False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"schedule_weekday": "invalid_schedule"}
    good = {**bad, "schedule_weekday": "06:00-22:00", "served_by_midea": False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], good)
    assert result["type"] == "create_entry"
    assert result["data"] == {}  # everything in the options (config version 3)
    assert [r[CONF_ROOM_KEY] for r in result["options"][CONF_ROOMS]] == ["badezimmer", "kuche"]
    await hass.async_block_till_done()

    # second instance is refused
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"


async def test_options_flow_edit_prices_and_delete_room(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "prices"})
    assert result["step_id"] == "prices"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"electricity_price": 0.40, "gas_price": 0.10, "boiler_efficiency": 0.84, "gas_calorific_value_kwh_m3": 11.45, "gas_z_factor": 0.9381},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    be = hass.states.get("sensor.thriftherm_break_even_cop")
    assert float(be.state) == pytest.approx(0.40 * 0.84 / 0.10, abs=0.01)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    assert result["step_id"] == "rooms"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room": "wohnzimmer", "action": "delete"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert [r[CONF_ROOM_KEY] for r in entry.options[CONF_ROOMS]] == ["badezimmer"]


async def test_options_flow_adds_a_room(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rooms"})
    assert result["step_id"] == "rooms"
    # the "add" choice is a translated select value: hassfest wants [a-z0-9-_], no leading "_"
    assert ROOM_ACTION_ADD == "add-room"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room": ROOM_ACTION_ADD, "action": "edit"})
    assert result["step_id"] == "room"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Küche", "priority": 4, "temperature_sensor": "sensor.kitchen_temp", "comfort_temp": 21.0, "setback_temp": 17.0}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert [r[CONF_ROOM_KEY] for r in entry.options[CONF_ROOMS]] == ["badezimmer", "wohnzimmer", "kuche"]


async def test_services_override_and_away(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    # override: bathroom to 23 °C for 30 min
    await hass.services.async_call(
        "thriftherm", "set_override", {"room": "badezimmer", "temperature": 23, "duration_min": 30}, blocking=True
    )
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert target is not None and float(target.state) == 23.0
    assert target.attributes["reason"] == "override"
    ov = hass.states.get("binary_sensor.thriftherm_override_active")
    assert ov is not None and ov.state == "on"

    # expires after 30 min
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 21.0
    assert hass.states.get("binary_sensor.thriftherm_override_active").state == "off"

    # away without return time -> away target
    await hass.services.async_call("thriftherm", "set_away", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("select.thriftherm_operating_mode").state == "away"
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 15.0

    await hass.services.async_call("thriftherm", "clear_away", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("select.thriftherm_operating_mode").state == "auto"

    # unknown room is rejected
    with pytest.raises(Exception):
        await hass.services.async_call(
            "thriftherm", "set_override", {"room": "keller", "temperature": 20}, blocking=True
        )


async def test_heat_pump_control_plans_only_by_default(hass: HomeAssistant) -> None:
    await _setup(hass)
    select = hass.states.get("select.thriftherm_heat_pump_control")
    assert select is not None and select.state == "shadow"
    assert select.attributes["options"] == ["off", "shadow"]  # "active" needs the release in the options
    assert hass.states.get("sensor.thriftherm_automation_state").state == "planning"
    command = hass.states.get("sensor.thriftherm_heat_pump_command")
    assert command is not None and command.state == "hands_off"  # the Midea runs fan-only by hand
    assert command.attributes["commands_sent"] is False
    with pytest.raises(Exception):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": "select.thriftherm_heat_pump_control", "option": "active"}, blocking=True
        )


async def test_quick_heat_up_button(hass: HomeAssistant) -> None:
    await _setup(hass)
    button = hass.states.get("button.thriftherm_badezimmer_quick_heat_up")
    assert button is not None
    assert hass.states.get("button.thriftherm_wohnzimmer_quick_heat_up") is None  # radiator-only room
    await hass.services.async_call("button", "press", {"entity_id": "button.thriftherm_badezimmer_quick_heat_up"}, blocking=True)
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert target.attributes["quick_heat_up"] is True
    await hass.services.async_call("thriftherm", "clear_override", {"room": "badezimmer"}, blocking=True)
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert target.attributes["quick_heat_up"] is False


async def test_active_control_sends_commands(hass: HomeAssistant) -> None:
    hvac_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    temp_calls = async_mock_service(hass, "climate", "set_temperature")
    fan_calls = async_mock_service(hass, "climate", "set_fan_mode")
    entry = await _setup(hass, options={"midea_allow_active_control": True})
    hass.states.async_set(
        "climate.midea",
        "off",
        {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0,
         "fan_modes": ["auto", "high", "full"], "fan_mode": "high"},  # a unit with fan stages shows them
    )
    await hass.async_block_till_done()
    select = hass.states.get("select.thriftherm_heat_pump_control")
    assert "active" in select.attributes["options"]
    # shadow first: a plan, nothing sent
    await entry.runtime_data.async_refresh()
    command = hass.states.get("sensor.thriftherm_heat_pump_command")
    assert command.state == "heat" and not hvac_calls
    # a plan once started is remembered: reset memory so that "active" starts again
    from custom_components.thriftherm.engines.heat_pump_control import ControlMemory

    entry.runtime_data.control_memory = ControlMemory()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_heat_pump_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    assert [c.data["hvac_mode"] for c in hvac_calls] == ["heat"]
    assert temp_calls and temp_calls[-1].data["temperature"] == 22.0  # intake 21 °C + 1 K
    assert fan_calls and fan_calls[-1].data["fan_mode"] == "auto"
    assert hass.states.get("sensor.thriftherm_automation_state").state == "active"


async def test_boiler_only_without_heat_pump(hass: HomeAssistant) -> None:
    """Smart boiler control is the core; the Midea is an optional add-on."""
    _set_states(hass)
    data = {k: v for k, v in _entry_data().items() if k not in (CONF_HEAT_PUMP_CLIMATE, CONF_HEAT_PUMP_POWER)}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for entity_id in (
        "sensor.thriftherm_heat_pump_cop",
        "sensor.thriftherm_heat_pump_command",
        "sensor.thriftherm_break_even_cop",
        "select.thriftherm_heat_pump_control",
        "binary_sensor.thriftherm_heat_pump_blocked_for_heating",
        "button.thriftherm_badezimmer_quick_heat_up",
        "binary_sensor.thriftherm_badezimmer_drying_mode",
    ):
        assert hass.states.get(entity_id) is None, entity_id

    assert hass.states.get("sensor.thriftherm_safety_state").state == "ok"
    advice = hass.states.get("sensor.thriftherm_recommended_heat_source")
    assert advice.state == "boiler"  # the bathroom is a radiator room now
    reasons = hass.states.get("sensor.thriftherm_decision_reason").attributes["reasons"]
    assert not any("midea" in r for r in reasons)
    assert hass.states.get("sensor.thriftherm_heat_cost_per_kwh_boiler") is not None
    assert hass.states.get("select.thriftherm_operating_mode") is not None


async def test_boiler_control_plans_by_default_and_sends_when_active(hass: HomeAssistant) -> None:
    publish = async_mock_service(hass, "mqtt", "publish")
    await _setup(hass, options={"boiler_allow_active_control": True})
    select = hass.states.get("select.thriftherm_boiler_control")
    assert select is not None and select.state == "shadow"
    command = hass.states.get("sensor.thriftherm_boiler_command")
    assert command is not None and command.state == "heat"  # living room is cold, boiler advised
    assert command.attributes["setmode_payload"].startswith("auto;")
    assert command.attributes["setmode_payload"].endswith(";-;-;0;0;0;0;0;0")
    assert command.attributes["commands_sent"] is False
    setmode = [c for c in publish if c.data["topic"].endswith("/SetMode/set")]
    assert not setmode  # plan only; read requests to ebusd are allowed
    flow = hass.states.get("sensor.thriftherm_boiler_flow_setpoint_planned")
    assert flow is not None and 30.0 <= float(flow.state) <= 60.0

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    setmode = [c for c in publish if c.data["topic"].endswith("/SetMode/set")]
    assert setmode, "SetMode must be published in active mode"
    call = setmode[-1]
    assert call.data["topic"] == "ebusd/bai/SetMode/set"
    assert call.data["retain"] is False
    assert call.data["payload"] == command.attributes["setmode_payload"]


async def test_boiler_active_needs_release(hass: HomeAssistant) -> None:
    await _setup(hass)
    select = hass.states.get("select.thriftherm_boiler_control")
    assert select.attributes["options"] == ["off", "shadow"]


async def test_room_control_plans_and_writes_thermostat(hass: HomeAssistant, freezer) -> None:
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 17.0, "current_temperature": 18.5})
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, options={"room_allow_active_control": True}, unique_id=DOMAIN, title="Thriftherm"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    select = hass.states.get("select.thriftherm_room_control")
    assert select is not None and select.state == "shadow" and "active" in select.attributes["options"]
    plan = hass.states.get("sensor.thriftherm_badezimmer_thermostat_plan")
    assert plan is not None and plan.state == "set" and plan.attributes["setpoint"] == 21.0
    assert not set_temp  # plan only
    assert hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan") is None  # no thermostat configured

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_room_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    assert set_temp and set_temp[-1].data == {"entity_id": "climate.bath_bt", "temperature": 21.0}

    # thermostat confirms, then someone turns it to 23 °C -> override instead of a fight
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5})
    freezer.tick(timedelta(seconds=70))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 23.0, "current_temperature": 18.5})
    freezer.tick(timedelta(seconds=70))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 23.0 and target.attributes["reason"] == "override"

    # a thermostat switched off (summer) is left alone
    calls = len(set_temp)
    hass.states.async_set("climate.bath_bt", "off", {"temperature": 5.0})
    freezer.tick(timedelta(seconds=70))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(set_temp) == calls
    assert hass.states.get("sensor.thriftherm_badezimmer_thermostat_plan").state == "hands_off"


async def test_room_temperature_numbers_and_schedule_helper(hass: HomeAssistant, freezer) -> None:
    """Comfort/setback are adjustable at runtime, and a schedule helper drives the target."""
    _set_states(hass)
    far_ahead = (dt_util.now() + timedelta(days=2)).isoformat()
    hass.states.async_set("schedule.bath", "off", {"next_event": far_ahead})
    data = _entry_data()
    data[CONF_ROOMS][0]["schedule_entity"] = "schedule.bath"
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    comfort = hass.states.get("number.thriftherm_badezimmer_comfort_temperature")
    setback = hass.states.get("number.thriftherm_badezimmer_setback_temperature")
    assert comfort is not None and float(comfort.state) == 21.0
    assert setback is not None and float(setback.state) == 17.0

    # schedule is off -> setback, not the configured comfort window
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 17.0 and target.attributes["reason"] in ("schedule_setback", "schedule_preheat")

    # the slider changes the target without touching the configuration
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.thriftherm_badezimmer_setback_temperature", "value": 18.5}, blocking=True
    )
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 18.5

    # an active block wins
    hass.states.async_set("schedule.bath", "on", {"temperature": 22.0})
    freezer.tick(timedelta(seconds=70))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(target.state) == 22.0 and target.attributes["reason"] == "schedule_comfort"


async def test_district_heating_creates_no_boiler_control(hass: HomeAssistant) -> None:
    """District heating has no boiler of ours to address, so none of it may appear."""
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_entry_data(), "system_type": "district", "heat_price": 0.12,
              "heat_consumption_share_pct": 70.0, "heat_ownership_share_pct": 5.0},
        options={},
        unique_id=DOMAIN,
        title="Thriftherm",
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.has_boiler is False
    assert coordinator.has_heat_source is True
    # the select exists in a gas setup; here it must not
    assert hass.states.get("select.thriftherm_boiler_control") is None
    assert coordinator.boiler_control_modes == ["off"]
    assert coordinator.boiler_control_mode == "off"
    for gone in ("sensor.thriftherm_boiler_command", "sensor.thriftherm_gas_power",
                 "sensor.thriftherm_gas_energy", "binary_sensor.thriftherm_boiler_space_heating_active"):
        assert hass.states.get(gone) is None, gone
    # the cost comparison still works and uses the allocation key
    cost = hass.states.get("sensor.thriftherm_heat_cost_per_kwh_boiler")
    assert cost is not None and float(cost.state) == pytest.approx(0.12 * 0.715)


async def test_heat_pump_only_setup_drops_the_comparison(hass: HomeAssistant) -> None:
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={**_entry_data(), "system_type": "none"}, options={},
        unique_id=DOMAIN, title="Thriftherm", version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.has_boiler is False and coordinator.has_heat_source is False
    assert hass.states.get("sensor.thriftherm_heat_cost_per_kwh_boiler") is None
    assert hass.states.get("sensor.thriftherm_break_even_cop") is None


async def test_entry_from_version_1_is_migrated_to_an_own_boiler(hass: HomeAssistant) -> None:
    """A v1 entry only ever described an ebusd gas boiler; migration must say so."""
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={}, unique_id=DOMAIN,
                            title="Thriftherm", version=1)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 3
    assert entry.data == {} and entry.options == {**_entry_data(), "system_type": "gas"}
    assert entry.runtime_data.has_boiler is True
    assert hass.states.get("sensor.thriftherm_boiler_command") is not None


# keys of fields removed before 0.4.0, still in the live entry on 2026-09-28
LEFTOVERS = {
    "boiler_dhw_setpoint": "sensor.dhw_setpoint", "boiler_flow_temp_desired": "sensor.flow_desired",
    "boiler_storage_temp": "sensor.storage", "fallback_room_temp": 19.0, "midea_energy": "sensor.midea_energy",
    "midea_outlet_rh": "sensor.midea_outlet_rh",
}


async def test_entry_from_version_2_keeps_one_copy_of_the_configuration(hass: HomeAssistant, hass_storage) -> None:
    """Like the live entry: the data froze at setup, every options save wrote a full copy into the options."""
    import json

    from custom_components.thriftherm import learning_reset
    from custom_components.thriftherm.config_schema import CONFIG_KEYS
    from custom_components.thriftherm.const import STORAGE_KEY, STORAGE_VERSION

    data = {**_entry_data(), "system_type": "gas", "boiler_flow_min": 30.0, "boiler_signal": "binary_sensor.adapter", **LEFTOVERS}
    rooms = [{**room, "schedule_entity": f"schedule.{room[CONF_ROOM_KEY]}"} for room in data[CONF_ROOMS]]
    options = {
        **data, "boiler_flow_min": 45.0, "boiler_signal": "binary_sensor.ebus", "boiler_allow_active_control": True,
        "midea_room": "wohnzimmer", "room_echo_filter": True, "sensor_max_age_min": 180.0, CONF_ROOMS: rooms,
    }
    read_before = {**data, **options}
    # learned on exactly this configuration; 0.4.0 still fingerprinted the flow limits
    basis = learning_reset.basis(read_before)
    basis["boiler"] = json.dumps({"curve_cold": 55.0, "curve_warm": 30.0, "flow_min": 45.0, "flow_max": 60.0}, sort_keys=True)
    hass_storage[f"{STORAGE_KEY}.live"] = {
        "version": STORAGE_VERSION, "minor_version": 1, "key": f"{STORAGE_KEY}.live",
        "data": {"learning_basis": basis, "boiler_memory": {"offset_k": 2.0}, "heat_rates": {"rates": {"badezimmer": [1.5]}},
                 "control_memory": {"offset_k": 1.5}},
    }
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, options=options, entry_id="live", unique_id=DOMAIN, title="Thriftherm", version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 3 and entry.data == {}
    assert set(entry.options) == set(read_before) - set(LEFTOVERS)  # presence matters: a key holding None is no default
    assert {k: entry.options.get(k) for k in CONFIG_KEYS} == {k: read_before.get(k) for k in CONFIG_KEYS}
    assert entry.options["boiler_flow_min"] == 45.0 and entry.options[CONF_ROOMS] == rooms  # the options won
    coordinator = entry.runtime_data
    assert coordinator.last_learning_reset is None  # the migration forgets nothing learned
    assert coordinator.boiler_memory.offset_k == 2.0 and coordinator.control_memory.offset_k == 1.5
    assert coordinator.heat_rates.rates == {"badezimmer": [1.5]}


async def test_quick_heat_up_exists_for_every_controllable_room(hass: HomeAssistant) -> None:
    """The boost works through Better Thermostat too, so it is not a heat-pump privilege."""
    data = _entry_data()
    # give the living room a thermostat but no heat pump
    data[CONF_ROOMS][1]["climate_entity"] = "climate.living"
    hass.states.async_set("climate.living", "heat", {"temperature": 18.0, "current_temperature": 17.0})
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm", version=2)
    _set_states(hass)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("button.thriftherm_badezimmer_quick_heat_up") is not None
    assert hass.states.get("button.thriftherm_wohnzimmer_quick_heat_up") is not None


async def test_diagnostics_describe_the_installation_and_the_plans(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["installation"]["system_type"] == "gas"
    assert diag["installation"]["has_boiler"] is True
    assert diag["installation"]["rooms"] == ["badezimmer", "wohnzimmer"]
    assert diag["control"]["setmode_topic"].endswith("/SetMode/set")
    assert diag["plans"]["boiler"] is not None
    assert set(diag["rooms"]) == {"badezimmer", "wohnzimmer"}
    assert "target_reason" in diag["rooms"]["badezimmer"]
    assert "cop_map" in diag["learning"]


async def test_repair_issues_appear_and_clear(hass: HomeAssistant) -> None:
    """A room without any heating window is a silent failure, so it gets a repair entry."""
    from homeassistant.helpers import issue_registry as ir

    data = _entry_data()
    # the living room points at a schedule helper that no longer exists
    data[CONF_ROOMS][1]["schedule_entity"] = "schedule.deleted_by_accident"
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm", version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    # right after start the schedule integration may simply not be loaded yet: no repair
    assert registry.async_get_issue(DOMAIN, "thriftherm_schedule_missing_wohnzimmer") is None
    import time as _time

    entry.runtime_data.issue_since["schedule_missing_wohnzimmer"] = _time.time() - 600
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    issue = registry.async_get_issue(DOMAIN, "thriftherm_schedule_missing_wohnzimmer")
    assert issue is not None
    assert issue.translation_placeholders == {"room": "Wohnzimmer", "entity_id": "schedule.deleted_by_accident"}
    # the bathroom has working time windows, so it must not be flagged
    assert registry.async_get_issue(DOMAIN, "thriftherm_schedule_missing_badezimmer") is None
    assert registry.async_get_issue(DOMAIN, "thriftherm_no_schedule_badezimmer") is None

    # once the helper is back the issue has to disappear again
    hass.states.async_set("schedule.deleted_by_accident", "off", {"next_event": None})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "thriftherm_schedule_missing_wohnzimmer") is None


async def test_repairs_and_the_store_go_with_the_entry(hass: HomeAssistant, hass_storage) -> None:
    """Code audit 2026-09-28 (H6): repairs stayed after disabling or deleting the entry, and its store stayed behind."""
    from homeassistant.config_entries import ConfigEntryDisabler
    from homeassistant.helpers import issue_registry as ir

    entry = await _setup(hass)
    registry = ir.async_get(hass)
    issue = "thriftherm_airflow_not_calibrated"  # the heat pump has no airflow curve
    assert registry.async_get_issue(DOMAIN, issue) is not None

    # a reload keeps it, and an ignored repair stays ignored
    ir.async_ignore_issue(hass, DOMAIN, issue, True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue).dismissed_version is not None

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue) is None
    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue) is not None  # enabled again, the first cycle finds it

    store = f"thriftherm.learning.{entry.entry_id}"
    assert store in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue) is None
    assert store not in hass_storage


async def test_warns_when_the_heat_pump_cannot_reach_the_rooms_it_serves(hass: HomeAssistant) -> None:
    """Ducts missing: the warm air stays where the unit stands, not in the bathroom."""
    from homeassistant.helpers import issue_registry as ir

    # the bathroom is served by the heat pump; the unit stands in the study, no ducts
    data = {**_entry_data(), "midea_room": "wohnzimmer", "midea_duct_factor": 1.0}
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm", version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, "thriftherm_heat_stays_in_the_room")
    assert issue is not None
    assert issue.translation_placeholders == {"rooms": "Badezimmer"}

    command = hass.states.get("sensor.thriftherm_heat_pump_command")
    assert command is not None
    assert command.attributes["served_rooms"] == ["badezimmer"]
    assert command.attributes["installed_in_room"] == "wohnzimmer"


async def test_no_duct_warning_once_ducts_are_configured(hass: HomeAssistant) -> None:
    data = {**_entry_data(), "midea_room": "wohnzimmer", "midea_duct_factor": 0.75}
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm", version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import issue_registry as ir

    assert ir.async_get(hass).async_get_issue(DOMAIN, "thriftherm_heat_stays_in_the_room") is None


async def test_entity_ids_are_english_on_a_german_installation(hass: HomeAssistant) -> None:
    """Display names follow the system language; entity ids must not."""
    await hass.config.async_update(language="de")
    await _setup(hass)

    boost = hass.states.get("button.thriftherm_badezimmer_quick_heat_up")
    assert boost is not None
    assert boost.attributes["friendly_name"].endswith("Schnell aufheizen")  # still German to read
    assert hass.states.get("select.thriftherm_heat_pump_control") is not None
    assert hass.states.get("binary_sensor.thriftherm_badezimmer_window_open") is not None
    assert hass.states.get("number.thriftherm_wohnzimmer_comfort_temperature") is not None
    ours = [s.entity_id for s in hass.states.async_all() if s.entity_id.split(".", 1)[1].startswith("thriftherm_")]
    german = ("steuerung", "trocknungsmodus", "aufheizen", "solltemperatur", "komfort", "absenk", "fenster", "heizen")
    assert ours and not [e for e in ours if any(word in e for word in german)]



async def test_boiler_learning_waits_for_active_boiler_control(hass: HomeAssistant) -> None:
    """While only planning, the front knob sets the flow: learning an offset would be fiction."""
    await _setup(hass)
    state = hass.states.get("sensor.thriftherm_learning_state")
    assert state is not None
    assert "boiler_control_not_active" in state.attributes["paused_because"]


async def test_learning_without_a_burner_run_says_why_it_waits(hass: HomeAssistant) -> None:
    """Nothing blocks the learning, but it only learns from a running burner (shown since 2026-10-03)."""
    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    coordinator._last_target_change_ts = 0.0
    hass.states.async_set("sensor.pump", "off")
    hass.states.async_set("sensor.gas_flow", "0")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.thriftherm_learning_state")
    assert state.state == "paused"
    assert coordinator.data["learning_blocked_by"] == []
    assert state.attributes["paused_because"] == ["no_heating_run"]


async def test_switching_heat_pump_control_starts_from_a_clean_run_state(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.const import CONF_HEAT_PUMP_ALLOW_ACTIVE
    from custom_components.thriftherm.engines.heat_pump_control import ControlMemory

    entry = await _setup(hass, options={CONF_HEAT_PUMP_ALLOW_ACTIVE: True})
    coordinator = entry.runtime_data
    coordinator.control_memory = ControlMemory(running_since_ts=1.0, last_command_ts=1.0, last_hvac_mode="heat", offset_k=2.5)
    coordinator.set_control_mode("active")
    assert coordinator.control_memory.running_since_ts is None
    assert coordinator.control_memory.last_hvac_mode is None
    assert coordinator.control_memory.offset_k == 2.5



async def test_devices_and_entities_of_removed_rooms_are_cleaned_up(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = await _setup(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    gone = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_room_gone")}, name="Thriftherm Gone")
    stale = ent_reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_gone_target", config_entry=entry, device_id=gone.id)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert ent_reg.async_get(stale.entity_id) is None
    device = dev_reg.async_get(gone.id)
    assert device is None or entry.entry_id not in device.config_entries
    assert hass.states.get("sensor.thriftherm_badezimmer_target_temperature") is not None  # live rooms untouched


async def test_a_room_gets_no_entity_for_what_it_does_not_have(hass: HomeAssistant) -> None:
    """They stayed unknown for good: a window sensor without contacts, a dew point without humidity, a drying nobody chose."""
    from homeassistant.helpers import entity_registry as er

    entry = await _setup(hass)  # the bathroom has a window contact and a humidity sensor, the living room neither
    ent_reg = er.async_get(hass)
    assert hass.states.get("binary_sensor.thriftherm_badezimmer_window_open") is not None
    assert ent_reg.async_get("sensor.thriftherm_badezimmer_dew_point") is not None  # off by default, but there
    assert hass.states.get("binary_sensor.thriftherm_wohnzimmer_window_open") is None
    assert ent_reg.async_get("sensor.thriftherm_wohnzimmer_dew_point") is None
    assert ent_reg.async_get("sensor.thriftherm_wohnzimmer_absolute_humidity") is None
    assert hass.states.get("binary_sensor.thriftherm_badezimmer_drying_mode") is None  # the drying mode is not switched on
    assert hass.states.get("binary_sensor.thriftherm_wohnzimmer_heating_allowed") is not None  # what every room has stays

    # entities an older version had created for such rooms go with the next start
    old = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{entry.entry_id}_wohnzimmer_window_open", config_entry=entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert ent_reg.async_get(old.entity_id) is None


async def test_options_heat_pump_step_keeps_the_room_after_a_validation_error(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "midea"})
    assert "midea_room" in [str(k) for k in result["data_schema"].schema]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"midea_climate": "climate.midea", "midea_airflow_curve": "bad", "midea_duct_factor": 1.0}
    )
    assert result["errors"] == {"midea_airflow_curve": "invalid_airflow_curve"}
    assert "midea_room" in [str(k) for k in result["data_schema"].schema]


async def test_services_without_a_loaded_entry_raise(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.thriftherm.const import SERVICE_CLEAR_AWAY

    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_CLEAR_AWAY, {}, blocking=True)


async def test_back_to_automatic_for_a_room_that_does_not_exist_is_refused(hass: HomeAssistant) -> None:
    """A wrong room must not pass as done: the override meant to end would stay in force unnoticed."""
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass)
    await hass.services.async_call(DOMAIN, "set_override", {"room": "badezimmer", "temperature": 23}, blocking=True)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "clear_override", {"room": "Badezimmer"}, blocking=True)  # the name, not the key
    await hass.async_block_till_done()
    assert "badezimmer" in hass.states.get("binary_sensor.thriftherm_override_active").attributes["overrides"]


async def test_entry_from_a_newer_version_is_not_set_up(hass: HomeAssistant) -> None:
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={}, unique_id=DOMAIN, title="Thriftherm", version=4)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_setback_above_comfort_is_rejected(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    entry = await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_set_room_temperature("badezimmer", "setback", 25.0)  # comfort is 21


async def test_comfort_below_setback_takes_the_setback_along(hass: HomeAssistant) -> None:
    """Turning comfort down like a room thermostat must work; the setback follows."""
    await _setup(hass)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.thriftherm_badezimmer_comfort_temperature", "value": 16.0}, blocking=True
    )
    await hass.async_block_till_done()
    assert float(hass.states.get("number.thriftherm_badezimmer_comfort_temperature").state) == 16.0
    assert float(hass.states.get("number.thriftherm_badezimmer_setback_temperature").state) == 16.0  # was 17
    assert float(hass.states.get("number.thriftherm_wohnzimmer_setback_temperature").state) == 17.0


async def test_the_learning_state_says_which_change_paused_it(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data._last_target_change_ts = 0.0  # the start itself is long past
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.thriftherm_learning_state").attributes
    assert "setpoint_recently_changed" not in attrs["paused_because"]
    assert attrs["paused_until"] is None and attrs["setpoint_changed_in"] == []

    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.thriftherm_wohnzimmer_comfort_temperature", "value": 20.0}, blocking=True
    )
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.thriftherm_learning_state").attributes
    assert "setpoint_recently_changed" in attrs["paused_because"]
    assert attrs["setpoint_changed_in"] == ["Wohnzimmer"]
    until = dt_util.parse_datetime(attrs["paused_until"])
    assert 19 * 60 < (until - dt_util.utcnow()).total_seconds() <= 20 * 60


_HA_ATTRIBUTES = {"friendly_name", "icon", "device_class", "unit_of_measurement", "state_class", "options", "supported_features", "restored", "min", "max", "step", "mode"}


async def test_every_attribute_has_a_translated_name(hass: HomeAssistant) -> None:
    import json
    from pathlib import Path

    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    en = json.loads((Path(__file__).resolve().parent.parent / "custom_components" / "thriftherm" / "translations" / "en.json").read_text())
    registry = er.async_get(hass)
    missing = []
    for entry in er.async_entries_for_config_entry(registry, hass.config_entries.async_entries(DOMAIN)[0].entry_id):
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        known = en["entity"].get(entry.domain, {}).get(entry.translation_key, {}).get("state_attributes", {})
        missing += [f"{entry.entity_id}.{attr}" for attr in state.attributes if attr not in _HA_ATTRIBUTES and attr not in known]
    assert missing == []


async def test_decision_reason_follows_the_home_assistant_language(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    await _setup(hass)
    state = hass.states.get("sensor.thriftherm_decision_reason")
    assert state is not None
    assert "Badezimmer" in state.state or "Wohnzimmer" in state.state
    assert "target" not in state.state and "Soll" in state.state
    assert state.attributes["reason_codes"]


async def test_flow_and_return_are_kept_fresh_on_the_bus(hass: HomeAssistant) -> None:
    publish = async_mock_service(hass, "mqtt", "publish")
    entry = await _setup(hass)  # gas flow 1.2 m³/h: the burner is on
    calls = [(c.data["topic"], c.data["payload"]) for c in publish]
    assert ("ebusd/bai/FlowTemp/get", "?1") in calls and ("ebusd/bai/ReturnTemp/get", "?1") in calls
    assert ("ebusd/bai/FlowTemp/get", "") in calls  # direct read while burning
    assert not [c for c in publish if "SetMode" in c.data["topic"]]  # reading only, nothing written

    coordinator = entry.runtime_data
    now = coordinator._ebus_read_ts
    publish.clear()
    await coordinator._async_keep_boiler_readings_fresh(20000.0, now + 30)
    assert not publish  # at most once a minute, priority already set
    await coordinator._async_keep_boiler_readings_fresh(20000.0, now + 61)
    assert [c.data["payload"] for c in publish] == ["", "", "", "", ""]  # flow, return, status, pump, S.xx
    publish.clear()
    await coordinator._async_keep_boiler_readings_fresh(0.0, now + 200)
    assert not publish  # burner off: ebusd's own polling is enough
    await coordinator._async_keep_boiler_readings_fresh(0.0, now + 700)
    assert [c.data["payload"] for c in publish] == ["?1"] * 5  # priority renewed: survives an ebusd restart


async def test_without_a_gas_meter_the_boiler_values_are_read_all_the_time(hass: HomeAssistant) -> None:
    """Nothing else tells that the burner started, and ebusd's polling pauses for minutes."""
    publish = async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass)).runtime_data
    now = coordinator._ebus_read_ts
    publish.clear()
    await coordinator._async_keep_boiler_readings_fresh(None, now + 61)
    assert [(c.data["topic"], c.data["payload"]) for c in publish] == [
        ("ebusd/bai/FlowTemp/get", ""),
        ("ebusd/bai/ReturnTemp/get", ""),
        ("ebusd/bai/Status01/get", ""),
        ("ebusd/bai/WP/get", ""),
        ("ebusd/bai/Statenumber/get", ""),
    ]
    publish.clear()
    await coordinator._async_keep_boiler_readings_fresh(None, now + 90)
    assert not publish  # at most once a minute here too


async def test_flow_and_return_are_read_in_the_next_cycle_also_when_it_comes_a_millisecond_early(hass: HomeAssistant) -> None:
    """Home Assistant starts each cycle 60 s after the last, give or take a millisecond: once a minute must hold."""
    publish = async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass)).runtime_data  # gas flow 1.2 m³/h: the burner is on
    now = coordinator._ebus_read_ts
    publish.clear()
    await coordinator._async_keep_boiler_readings_fresh(20000.0, now + 59.999)
    assert [c.data["payload"] for c in publish] == [""] * 5  # flow, return, status, pump, S.xx


async def test_a_flapping_ebus_signal_does_not_stop_the_boiler_control(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    assert hass.states.get("sensor.thriftherm_boiler_command").state != "hands_off"

    hass.states.async_set("binary_sensor.ebus", "off")  # one-second dropout of the adapter
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_boiler_command").state != "hands_off"

    freezer.tick(timedelta(seconds=180))  # really gone: now the boiler is left to its knob
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    command = hass.states.get("sensor.thriftherm_boiler_command")
    assert command.state == "hands_off"
    assert "boiler_data_unavailable" in command.attributes["blockers"]

    hass.states.async_set("binary_sensor.ebus", "on")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_boiler_command").state != "hands_off"


async def test_a_short_burn_is_not_lost_between_cycles(hass: HomeAssistant) -> None:
    """The gas meter must wake the coordinator: a burn shorter than one cycle still counts."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    watched = coordinator.builder.watched_entities()
    assert "sensor.gas_flow" in watched
    assert "sensor.pump" in watched

    hass.states.async_set("sensor.gas_flow", "0.0")
    await hass.async_block_till_done()
    assert coordinator.data["boiler"].gas_power_w == 0

    # the gas meter changing must start a cycle on its own, well inside one update interval
    hass.states.async_set("sensor.gas_flow", "1.4")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert coordinator.data["boiler"].gas_power_w > 1000


async def test_the_boiler_control_works_without_a_gas_meter(hass: HomeAssistant) -> None:
    """The gas meter is optional: pump state, status code and temperatures carry the control."""
    publish = async_mock_service(hass, "mqtt", "publish")
    _set_states(hass)
    hass.states.async_set("sensor.statenumber", "31")
    data = {k: v for k, v in _entry_data().items() if k != CONF_GAS_FLOW}
    options = {"boiler_allow_active_control": True, CONF_BOILER_STATE_NUMBER: "sensor.statenumber"}
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=options, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert "sensor.gas_flow" not in coordinator.builder.watched_entities()

    assert hass.states.get("sensor.thriftherm_safety_state").state == "ok"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    command = hass.states.get("sensor.thriftherm_boiler_command")
    assert command.state == "heat"  # living room 17.0 °C, comfort 18.5 °C
    assert command.attributes["blockers"] == []
    setmode = [c for c in publish if c.data["topic"] == "ebusd/bai/SetMode/set"]
    assert setmode and setmode[-1].data["payload"] == command.attributes["setmode_payload"]
    assert ("ebusd/bai/FlowTemp/get", "?1") in [(c.data["topic"], c.data["payload"]) for c in publish]

    # without a meter the boiler's status code tells the learning that the burner heats (S.4)
    hass.states.async_set("sensor.statenumber", "4")
    coordinator._last_target_change_ts = 0.0
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_learning_state").state != "paused"
    # sensors that could only ever say "unknown" are left out
    assert hass.states.get("sensor.thriftherm_gas_power") is None
    assert hass.states.get("sensor.thriftherm_gas_energy") is None

    # hot water comes from the boiler's own status code
    hass.states.async_set("sensor.statenumber", "14")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.thriftherm_boiler_hot_water_active").state == "on"
    await coordinator.async_refresh()  # the pause reasons are gathered before this cycle's hot water check
    await hass.async_block_till_done()
    assert "hot_water_recent" in hass.states.get("sensor.thriftherm_learning_state").attributes["paused_because"]


async def test_the_real_pump_and_the_display_status_reach_the_boiler_sensor(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.wp", "on")
    hass.states.async_set("sensor.statenumber", "8")
    await _setup(hass, options={CONF_BOILER_PUMP_RUNNING: "sensor.wp", CONF_BOILER_STATE_NUMBER: "sensor.statenumber"})

    attrs = hass.states.get("sensor.thriftherm_boiler_command").attributes
    assert attrs["pump_running"] is True
    assert attrs["boiler_status"] == "S.8"


async def test_the_display_shows_what_the_control_uses(hass: HomeAssistant) -> None:
    """Code audit 2026-09-28 (K8): heating and hot water showed at once, "observation only" looked
    at the heat pump alone, and the outdoor sensor left out the last known value the curve uses."""
    hass.states.async_set("sensor.statenumber", "14")  # the boiler itself says: hot water
    entry = await _setup(hass, options={CONF_BOILER_STATE_NUMBER: "sensor.statenumber", "room_allow_active_control": True})
    assert hass.states.get("binary_sensor.thriftherm_boiler_hot_water_active").state == "on"
    assert hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active").state == "off"

    advice = "sensor.thriftherm_recommended_heat_source"
    assert hass.states.get(advice).attributes["observation_only"] is True
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_room_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(advice).attributes["observation_only"] is False  # the room control acts

    outdoor = "sensor.thriftherm_outdoor_temperature_selected_source"
    assert float(hass.states.get(outdoor).state) == 4.0 and hass.states.get(outdoor).attributes["source"] == "sensor.outdoor"
    hass.states.async_set("sensor.outdoor", "unavailable")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(outdoor).state) == 4.0 and hass.states.get(outdoor).attributes["source"] == "last_known"


async def test_after_a_restart_the_boiler_waits_for_the_room_sensors(hass: HomeAssistant) -> None:
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "unknown")
    hass.states.async_set("sensor.living_temp", "unknown")
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.get("sensor.thriftherm_boiler_command").attributes
    assert hass.states.get("sensor.thriftherm_boiler_command").state == "hands_off"
    assert "waiting_for_room_data" in attrs["blockers"]


async def test_rooms_heat_along_while_another_room_calls_the_boiler(hass: HomeAssistant) -> None:
    """The living room calls; the bathroom, 0.1 K below comfort, opens its radiator too."""
    entry = await _setup(hass)
    hass.states.async_set("sensor.bath_temp", "20.9")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.thriftherm_boiler_command").state == "heat"
    living = hass.states.get("sensor.thriftherm_wohnzimmer_heat_demand")
    assert living.attributes["boiler_call"] == "calling"
    bath_demand = hass.states.get("sensor.thriftherm_badezimmer_heat_demand")
    assert bath_demand.attributes["boiler_call"] == "idle"  # too close to comfort to call by itself
    bath = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(bath.state) == 21.5 and bath.attributes["reason"] == "joined_boiler_run"
    assert hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature").attributes["reason"] == "schedule_comfort"


async def test_a_stalled_room_heating_along_names_the_temperature_its_own_target_calls_again_at(hass: HomeAssistant) -> None:
    """The bathroom, stalled 0.2 K below its 21.0 °C, heats along to 21.5 °C while the living room calls:
    it calls the boiler again below 20.5 °C, not below 21.0 °C."""
    from custom_components.thriftherm.engines.heat_call import CallMemory

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.call_memory["badezimmer"] = CallMemory(stalled_target=21.0)
    hass.states.async_set("sensor.bath_temp", "20.8")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    bath = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(bath.state) == 21.5 and bath.attributes["reason"] == "joined_boiler_run"
    demand = hass.states.get("sensor.thriftherm_badezimmer_heat_demand").attributes
    assert demand["boiler_call"] == "stalled"
    assert demand["calls_again_below"] == 20.5


async def test_rooms_of_a_cooling_heat_pump_never_call_the_boiler(hass: HomeAssistant) -> None:
    """Cooling never collides with heating (code audit 2026-09-28, F8): the bathroom, served by the
    heat pump and 2.5 K below comfort, called the boiler while the heat pump cooled it."""
    _set_states(hass)
    midea = hass.states.get("climate.midea")
    hass.states.async_set("climate.midea", "cool", midea.attributes)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5})
    hass.states.async_set("sensor.living_temp", "19.0")  # warm enough: only the bathroom could call
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    bath = coordinator.data["rooms"]["badezimmer"]
    assert bath.target == 17.0 and bath.target_reason == "heat_pump_cooling" and bath.boiler_call == "not_allowed"
    assert coordinator.data["advice"].source == "none"
    assert coordinator.data["boiler_command"].plan == "block"
    assert hass.states.get("sensor.thriftherm_badezimmer_thermostat_plan").attributes["setpoint"] == 17.0

    # frost protection always applies
    hass.states.async_set("sensor.bath_temp", "5.0")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["advice"].source == "boiler"
    assert coordinator.data["boiler_command"].plan == "heat"


async def test_a_sent_setpoint_is_stored_at_once(hass: HomeAssistant, hass_storage) -> None:
    """2026-09-26: the store still held 21.0 while the thermostat had 21.5 from us; a restart
    took that for a change by hand. A sent setpoint is now written without waiting."""
    async_mock_service(hass, "climate", "set_temperature")
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 17.0, "current_temperature": 18.5})
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, options={"room_allow_active_control": True}, unique_id=DOMAIN, title="Thriftherm"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_room_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()

    # the thermostat forgot our value; the periodic save has just run, nothing else is pending
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    coordinator.room_ctrl_memory["badezimmer"] = RoomCtrlMemory()
    coordinator._store_dirty = False
    coordinator._last_store_save = time.time()
    stored = next(v for k, v in hass_storage.items() if k.startswith("thriftherm"))["data"]
    stored["room_ctrl_memory"] = {}

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    stored = next(v for k, v in hass_storage.items() if k.startswith("thriftherm"))["data"]
    assert stored["room_ctrl_memory"]["badezimmer"]["last_sent_target"] == 21.0


async def test_state_is_saved_when_home_assistant_stops(hass: HomeAssistant) -> None:
    """Home Assistant does not unload entries on stop (code review 2026-09-28)."""
    from unittest.mock import AsyncMock

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.async_save_store = AsyncMock()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    coordinator.async_save_store.assert_awaited_with(force=True)


async def test_the_control_keeps_running_with_every_entity_disabled(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.const import PLATFORMS

    entry = await _setup(hass)
    assert await hass.config_entries.async_unload_platforms(entry, PLATFORMS)  # no entity listens any more
    assert entry.runtime_data._listeners  # our own listener still schedules the next cycle
    await entry.runtime_data.async_shutdown()  # the cycle timer would outlive the test


async def test_a_deleted_room_takes_its_device_along(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr

    from custom_components.thriftherm import _remove_stale_registry_entries

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    dev_reg = dr.async_get(hass)
    room_device = dev_reg.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_room_wohnzimmer"), entry.entry_id)
    assert room_device is not None
    coordinator.builder.rooms = tuple(r for r in coordinator.builder.rooms if r.key != "wohnzimmer")
    _remove_stale_registry_entries(hass, entry, coordinator)
    assert dev_reg.async_get(room_device.id) is None
    assert dev_reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id) is not None  # the hub stays


async def test_the_curve_per_load_class_is_shown_recorded_and_kept(hass: HomeAssistant, hass_storage) -> None:
    """Which flow suits how many rooms: visible in the learning state, one row per cycle, and it survives a restart."""
    from dataclasses import replace

    from custom_components.thriftherm.engines.boiler_load import LoadLine

    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    attrs = hass.states.get("sensor.thriftherm_learning_state").attributes
    assert attrs["calling_rooms"] == 2 and attrs["rooms_total"] == 2 and attrs["spread_rule"] == "active"
    assert attrs["load_class"] == "majority" and attrs["curve_by_load"] == {}  # nothing learned yet
    assert coordinator.load_log and len(coordinator.load_log[-1]) == 10  # one compact row per cycle
    assert coordinator.load_log[-1][5:7] == [2, 2] and coordinator.load_log[-1][9] == [2.5, 1.5]  # target minus actual

    lines = {
        "majority": LoadLine(level_k=2.0, hours=30.0, calm_hours=4.0, found=True),
        "single": LoadLine(level_k=5.0, hours=12.0),
    }
    coordinator.boiler_memory = replace(coordinator.boiler_memory, offset_k=9.0, lines=lines)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.thriftherm_learning_state").attributes
    assert attrs["heating_curve_offset_k"] == 2.0  # the majority's own line, not the common 9 K nor the bathroom's 5 K
    assert attrs["curve_by_load"]["majority"]["found"] is True and attrs["curve_by_load"]["single"]["level_k"] == 5.0

    await coordinator.async_save_store(force=True)
    stored = hass_storage[f"thriftherm.learning.{entry.entry_id}"]["data"]
    assert stored["boiler_memory"]["lines"]["single"]["level_k"] == 5.0 and stored["boiler_load_log"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.boiler_memory.lines["majority"].calm_hours >= 4.0
    assert len(entry.runtime_data.load_log) >= len(stored["boiler_load_log"])


async def test_hot_water_within_the_hour_does_not_switch_the_whole_learning_off(hass: HomeAssistant, monkeypatch) -> None:
    """The hour is for the spread to settle; the boiler control holds the spread back itself.

    With the learning switched off for it, a combi boiler with its hourly keep-warm never learned.
    """
    import time

    from custom_components.thriftherm import coordinator as coordinator_module

    async_mock_service(hass, "mqtt", "publish")
    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("active")
    allowed: list[bool] = []
    decide = coordinator_module.boiler_control.decide

    def watched(inp, mem):
        allowed.append(inp.learning_allowed)
        return decide(inp, mem)

    monkeypatch.setattr(coordinator_module.boiler_control, "decide", watched)
    coordinator._last_target_change_ts = 0.0  # no setpoint moved lately
    coordinator.boiler_memory = replace(coordinator.boiler_memory, hot_water_seen_ts=time.time() - 600)
    await coordinator.async_refresh()
    assert allowed[-1] is True, coordinator.data["learning_blocked_by"]


# ---- a boiler addressed through entities instead of ebusd (e.g. OpenTherm on an ESP) -----

ENTITY_BOILER = {
    "boiler_allow_active_control": True,
    "boiler_flow_setpoint_entity": "input_number.boiler_flow",
    "boiler_heating_enable_entity": "switch.boiler_heating",
}


def _entity_boiler(hass: HomeAssistant, value: str = "35", heating: str = "on", step: float = 0.5) -> list[tuple[str, dict]]:
    """A number and a switch that take what they are sent; returns the calls made."""
    calls: list[tuple[str, dict]] = []
    limits = {"min": 20.0, "max": 80.0, "step": step}

    async def set_value(call) -> None:
        calls.append(("set_value", dict(call.data)))
        hass.states.async_set(call.data["entity_id"], str(call.data["value"]), hass.states.get(call.data["entity_id"]).attributes)

    async def turn(call) -> None:
        calls.append((call.service, dict(call.data)))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("input_number", "set_value", set_value)  # "number" is taken by the integration itself
    hass.services.async_register("homeassistant", "turn_on", turn)
    hass.services.async_register("homeassistant", "turn_off", turn)
    hass.states.async_set("input_number.boiler_flow", value, limits)
    hass.states.async_set("switch.boiler_heating", heating)
    return calls


async def test_a_boiler_through_entities_gets_number_and_switch_and_nothing_goes_to_ebusd(hass: HomeAssistant) -> None:
    published = async_mock_service(hass, "mqtt", "publish")
    calls = _entity_boiler(hass)
    await _setup(hass, options=ENTITY_BOILER)
    assert calls == []  # planning only: nothing is written
    command = hass.states.get("sensor.thriftherm_boiler_command")
    assert command.state == "heat"
    assert command.attributes["setmode_topic"] is None and command.attributes["setmode_payload"] is None

    active = {"entity_id": "select.thriftherm_boiler_control", "option": "active"}
    await hass.services.async_call("select", "select_option", active, blocking=True)
    await hass.async_block_till_done()
    planned = float(hass.states.get("sensor.thriftherm_boiler_flow_setpoint_planned").state)
    assert calls == [("set_value", {"entity_id": "input_number.boiler_flow", "value": planned})]  # the heating was on already
    assert published == []  # no SetMode, no read requests


async def test_a_block_switches_the_heating_off_first_and_a_release_sets_the_flow_first(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _setup(hass, options=ENTITY_BOILER)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("active")
    hass.states.async_set("sensor.bath_temp", "22.0")
    hass.states.async_set("sensor.living_temp", "20.0")
    coordinator.boiler_memory = replace(coordinator.boiler_memory, state="block", state_since_ts=0.0)
    await coordinator.async_refresh()
    assert coordinator.data["boiler_command"].plan == "block"
    assert [c[0] for c in calls] == ["turn_off", "set_value"]
    assert hass.states.get("switch.boiler_heating").state == "off"

    calls.clear()
    coordinator.set_boiler_control_mode("shadow")  # the control lets go: the boiler must not stay blocked
    await coordinator.async_refresh()
    assert [c[0] for c in calls] == ["set_value", "turn_on"]
    assert hass.states.get("switch.boiler_heating").state == "on"
    calls.clear()
    await coordinator.async_refresh()
    assert calls == []  # handed back once


async def test_a_failed_entity_command_is_tried_again_next_cycle(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _setup(hass, options=ENTITY_BOILER)
    coordinator = entry.runtime_data
    hass.states.async_set("input_number.boiler_flow", "unavailable")
    coordinator.set_boiler_control_mode("active")
    await coordinator.async_refresh()
    assert calls == [] and coordinator.boiler_memory.last_send_ts is None
    hass.states.async_set("input_number.boiler_flow", "35", {"min": 20.0, "max": 80.0, "step": 0.5})
    await coordinator.async_refresh()
    assert [c[0] for c in calls] == ["set_value"]


async def test_the_flow_is_fitted_to_the_number_entity(hass: HomeAssistant) -> None:
    from custom_components.thriftherm import executors

    calls = _entity_boiler(hass, step=1.0)
    hass.states.async_set("input_number.boiler_flow", "35", {"min": 30.0, "max": 45.0, "step": 1.0})
    assert await executors.async_send_boiler_entities(hass, "input_number.boiler_flow", "switch.boiler_heating", 41.5, True)
    assert await executors.async_send_boiler_entities(hass, "input_number.boiler_flow", "switch.boiler_heating", 60.0, True)
    assert [c[1]["value"] for c in calls] == [42.0, 45.0]  # on the step, within the limits


async def _blocked_entity_boiler(hass: HomeAssistant, calls: list) -> MockConfigEntry:
    """An active control that has blocked the heating through the entities."""
    entry = await _setup(hass, options=ENTITY_BOILER)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("active")
    hass.states.async_set("sensor.bath_temp", "22.0")
    hass.states.async_set("sensor.living_temp", "20.0")
    coordinator.boiler_memory = replace(coordinator.boiler_memory, state="block", state_since_ts=0.0)
    await coordinator.async_refresh()
    assert coordinator.data["boiler_command"].plan == "block" and hass.states.get("switch.boiler_heating").state == "off"
    calls.clear()
    return entry


async def test_the_boiler_is_handed_back_for_good_when_home_assistant_stops(hass: HomeAssistant) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert hass.states.get("switch.boiler_heating").state == "on"
    assert float(hass.states.get("input_number.boiler_flow").state) == 60.0  # the maximum, as a knob would stand
    calls.clear()
    await entry.runtime_data.async_refresh()  # a cycle that still runs after the stop must not block again
    assert calls == [] and hass.states.get("switch.boiler_heating").state == "on"


async def test_the_boiler_is_handed_back_when_the_entry_is_unloaded(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert [c[0] for c in calls] == ["set_value", "turn_on"]


async def test_the_final_hand_back_comes_after_a_block_that_is_under_way(hass: HomeAssistant) -> None:
    """Review finding C10: a cycle that was writing a block when Home Assistant stopped left it behind for good."""
    import asyncio

    _entity_boiler(hass)
    coordinator = (await _setup(hass, options=ENTITY_BOILER)).runtime_data
    coordinator.set_boiler_control_mode("active")
    hass.states.async_set("sensor.bath_temp", "22.0")
    hass.states.async_set("sensor.living_temp", "20.0")
    coordinator.boiler_memory = replace(coordinator.boiler_memory, state="block", state_since_ts=0.0)
    switching, switched = asyncio.Event(), asyncio.Event()

    async def slow_turn_off(call) -> None:  # the gateway takes its time
        switching.set()
        await switched.wait()
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("homeassistant", "turn_off", slow_turn_off)
    cycle = hass.async_create_task(coordinator.async_refresh())
    await switching.wait()  # the cycle is switching the heating off
    release = hass.async_create_task(coordinator.async_release_boiler(final=True))
    await asyncio.wait([release], timeout=0.2)  # without waiting for the cycle, the hand-back is done by now
    switched.set()
    await cycle
    await release
    assert hass.states.get("switch.boiler_heating").state == "on"
    assert float(hass.states.get("input_number.boiler_flow").state) == 60.0


async def test_a_failing_evaluation_does_not_leave_the_heating_blocked(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    working = coordinator._evaluate

    def broken(snapshot):
        raise RuntimeError("boom")

    coordinator._evaluate = broken
    await coordinator.async_refresh()
    assert hass.states.get("switch.boiler_heating").state == "on"
    coordinator._evaluate = working
    await coordinator.async_refresh()
    assert hass.states.get("switch.boiler_heating").state == "off"  # and the control takes it back


async def test_a_hand_back_that_failed_is_made_up_after_a_reload(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    hass.states.async_set("switch.boiler_heating", "unavailable")  # the gateway is away while the control lets go
    hass.config_entries.async_update_entry(entry, options={**entry.options, "boiler_allow_active_control": False})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.thriftherm_boiler_control").state != "active"
    hass.states.async_set("switch.boiler_heating", "off")  # back, as the block left it
    await entry.runtime_data.async_refresh()
    assert hass.states.get("switch.boiler_heating").state == "on"


async def test_entities_the_control_never_commanded_are_left_alone(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass, heating="off")
    entry = await _setup(hass, options=ENTITY_BOILER)  # planning only
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert calls == [] and hass.states.get("switch.boiler_heating").state == "off"


async def test_a_heating_switched_off_after_the_hand_back_stays_off(hass: HomeAssistant) -> None:
    """Following the curve wrote the number and switched the heating on again with every move."""
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("shadow")
    await coordinator.async_refresh()
    assert hass.states.get("switch.boiler_heating").state == "on"  # handed back
    hass.states.async_set("switch.boiler_heating", "off")  # switched off by hand, for the summer
    calls.clear()
    hass.states.async_set("sensor.outdoor", "-10.0")
    await coordinator.async_refresh()
    assert [c[0] for c in calls] == ["set_value"]  # the flow follows, the switch is left alone
    assert hass.states.get("switch.boiler_heating").state == "off"


async def test_a_boiler_that_follows_the_curve_gets_the_maximum_when_home_assistant_stops(hass: HomeAssistant) -> None:
    """Review 2026-10-14: handed back while planning only, it kept the curve of a mild day once nothing followed it any more."""
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("shadow")
    await coordinator.async_refresh()
    assert float(hass.states.get("input_number.boiler_flow").state) < 60.0  # handed back, on the curve
    hass.states.async_set("switch.boiler_heating", "off")  # switched off by hand meanwhile
    calls.clear()
    await coordinator.async_release_boiler(final=True)
    assert calls == [("set_value", {"entity_id": "input_number.boiler_flow", "value": 60.0})]  # the switch is left alone
    calls.clear()
    await coordinator.async_release_boiler(final=True)
    await coordinator.async_refresh()
    assert calls == []  # once, and nothing after it


async def test_a_control_switched_off_leaves_the_maximum_and_writes_nothing_more(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("off")
    await coordinator.async_refresh()
    assert hass.states.get("switch.boiler_heating").state == "on"
    assert float(hass.states.get("input_number.boiler_flow").state) == 60.0  # as a knob would stand
    calls.clear()
    hass.states.async_set("sensor.outdoor", "-10.0")
    await coordinator.async_refresh()
    assert calls == []


async def test_a_handed_back_boiler_gets_the_maximum_once_the_outdoor_temperature_is_long_gone(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("shadow")
    await coordinator.async_refresh()
    assert float(hass.states.get("input_number.boiler_flow").state) < 60.0  # the curve on a mild day
    hass.states.async_set("sensor.outdoor", "unavailable")
    value, seen = coordinator._last_outdoor
    coordinator._last_outdoor = (value, seen - 7 * 3600)  # seven hours without a reading
    await coordinator.async_refresh()
    assert float(hass.states.get("input_number.boiler_flow").state) == 60.0


async def test_a_boiler_that_cannot_be_reached_is_warned_about_once(hass: HomeAssistant, caplog) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    hass.states.async_set("switch.boiler_heating", "unavailable")  # the gateway is away
    coordinator.set_boiler_control_mode("shadow")  # the hand-back is due every cycle until it goes through
    caplog.clear()
    for _ in range(4):
        await coordinator.async_refresh()

    def warned() -> int:
        return len([r for r in caplog.records if r.levelname == "WARNING" and "boiler command not sent" in r.getMessage()])

    assert warned() == 1
    hass.states.async_set("switch.boiler_heating", "off")
    await coordinator.async_refresh()  # back, and handed back
    assert hass.states.get("switch.boiler_heating").state == "on"
    coordinator.set_boiler_control_mode("active")
    hass.states.async_set("switch.boiler_heating", "unavailable")  # the next outage is warned about again
    caplog.clear()
    await coordinator.async_refresh()
    assert warned() == 1


async def test_a_handed_back_boiler_follows_the_curve_while_home_assistant_runs(hass: HomeAssistant) -> None:
    calls = _entity_boiler(hass)
    entry = await _blocked_entity_boiler(hass, calls)
    coordinator = entry.runtime_data
    coordinator.set_boiler_control_mode("shadow")
    await coordinator.async_refresh()
    mild = float(hass.states.get("input_number.boiler_flow").state)
    hass.states.async_set("sensor.outdoor", "-10.0")
    await coordinator.async_refresh()
    assert float(hass.states.get("input_number.boiler_flow").state) > mild + 5.0


async def test_the_thriftherm_device_shows_the_installed_version(hass: HomeAssistant) -> None:
    """Which package runs was not to be seen anywhere in Home Assistant (asked for after an install on 2026-10-14)."""
    import json
    from pathlib import Path

    from homeassistant.helpers import device_registry as dr

    entry = await _setup(hass)
    manifest = json.loads((Path(__file__).parent.parent / "custom_components" / "thriftherm" / "manifest.json").read_text())
    hub = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert hub is not None and hub.sw_version == manifest["version"]


async def test_the_boiler_heating_starts_of_the_day_are_a_sensor(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    state = hass.states.get("sensor.thriftherm_boiler_heating_starts_today")
    assert state is not None
    assert int(state.state) == entry.runtime_data.data["boiler_command"].starts_today == 1  # the test home burns at start
    assert state.attributes["state_class"] == "total_increasing"  # a daily counter: the statistics give starts per day
