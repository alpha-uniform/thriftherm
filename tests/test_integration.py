"""Integration tests against a real Home Assistant test instance."""

from __future__ import annotations

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

    dew = hass.states.get("sensor.thriftherm_badezimmer_dew_point")
    assert dew is not None
    assert 10.0 < float(dew.state) < 11.5

    heating = hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active")
    assert heating is not None and heating.state == "on"

    gas_power = hass.states.get("sensor.thriftherm_gas_power")
    assert gas_power is not None
    assert float(gas_power.state) == pytest.approx(12889, rel=0.01)

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
    assert [r[CONF_ROOM_KEY] for r in result["data"][CONF_ROOMS]] == ["badezimmer", "kuche"]
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
        {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0},
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

    assert entry.version == 2
    assert entry.data["system_type"] == "gas"
    assert entry.runtime_data.has_boiler is True
    assert hass.states.get("sensor.thriftherm_boiler_command") is not None


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
    assert hass.states.get("binary_sensor.thriftherm_badezimmer_drying_mode") is not None
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


async def test_entry_from_a_newer_version_is_not_set_up(hass: HomeAssistant) -> None:
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={}, unique_id=DOMAIN, title="Thriftherm", version=3)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_setback_above_comfort_is_rejected(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    entry = await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_set_room_temperature("badezimmer", "setback", 25.0)  # comfort is 21


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


async def test_the_real_pump_and_the_display_status_reach_the_boiler_sensor(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.wp", "on")
    hass.states.async_set("sensor.statenumber", "8")
    await _setup(hass, options={CONF_BOILER_PUMP_RUNNING: "sensor.wp", CONF_BOILER_STATE_NUMBER: "sensor.statenumber"})

    attrs = hass.states.get("sensor.thriftherm_boiler_command").attributes
    assert attrs["pump_running"] is True
    assert attrs["boiler_status"] == "S.8"


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
