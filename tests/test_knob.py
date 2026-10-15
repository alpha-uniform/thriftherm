"""A knob turned at a valve behind a room thermostat that does not pass the turn on."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm.const import DOMAIN
from custom_components.thriftherm.engines.room_control import knob_turn

from .test_echo_filter import _register, _setup_room_control, start  # noqa: F401 - `start` is a fixture
from .test_integration import _set_states

NOW = 1_000_000.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ------------------------------------------------------------------ the rule
def test_a_turn_is_the_difference_to_what_the_valve_had():
    assert knob_turn(16.0, 14.5, ((NOW - 600, 16.0),), NOW) == -1.5  # seen 2026-10-12: three clicks down
    assert knob_turn(16.0, 12.0, ((NOW - 600, 16.0),), NOW) == -4.0
    assert knob_turn(16.0, 16.5, (), NOW) == 0.5
    assert knob_turn(16.0, 16.2, (), NOW) is None  # no click is that small
    assert knob_turn(5.0, 30.0, (), NOW) is None  # and none that large


def test_the_valves_answer_to_a_command_and_a_late_echo_are_no_turn():
    assert knob_turn(16.0, 16.5, (), NOW, asked=(16.4,)) is None  # asked for 16.4 a moment ago: its answer, late
    assert knob_turn(16.0, 16.5, (), NOW, asked=(18.0,)) == 0.5
    seen = ((NOW - 30, 16.5), (NOW - 20, 16.0))
    assert knob_turn(16.0, 16.5, seen, NOW) is None  # it showed 16.5 half a minute ago: an echo
    assert knob_turn(16.0, 16.5, ((NOW - 300, 16.5), (NOW - 200, 16.0)), NOW) == 0.5  # five minutes ago is no echo


# ------------------------------------------------------------------ through Home Assistant
TRV, BT = "climate.bath_trv", "climate.bath_bt"


def _valve(hass: HomeAssistant, freezer, seconds: float, setpoint: float, context: Context | None = None) -> None:
    freezer.tick(timedelta(seconds=seconds))
    hass.states.async_set(TRV, "heat", {"temperature": setpoint, "current_temperature": 19.4}, context=context)


def _thermostat_writes(hass: HomeAssistant, freezer, seconds: float, setpoint: float) -> None:
    """The room thermostat sets its valve: a service call, and the valve's state carries its context."""
    freezer.tick(timedelta(seconds=seconds))
    context = Context()
    data = {"entity_id": TRV, "temperature": setpoint}
    call = {"domain": "climate", "service": "set_temperature", "service_data": data}
    hass.bus.async_fire("call_service", call, context=context)
    hass.states.async_set(TRV, "heat", {"temperature": setpoint, "current_temperature": 19.4}, context=context)


async def _running(hass: HomeAssistant, freezer):
    """Room control active; the thermostat has our 21 °C, its valve its corrected 18.5 °C."""
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    _register(hass)
    _set_states(hass)
    hass.states.async_set(TRV, "heat", {"temperature": 18.5, "current_temperature": 19.4})
    hass.states.async_set(BT, "heat", {"temperature": 17.0, "current_temperature": 18.5})
    coordinator = await _setup_room_control(hass)
    assert set_temp[-1].data == {"entity_id": BT, "temperature": 21.0}
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(BT, "heat", {"temperature": 21.0, "current_temperature": 18.5})
    await coordinator.async_refresh()
    return coordinator, set_temp


def _target(hass: HomeAssistant):
    state = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    return float(state.state), state.attributes["reason"], state.attributes["override_by"]


async def test_a_knob_turn_the_thermostat_leaves_standing_becomes_an_override(hass: HomeAssistant, start) -> None:  # noqa: F811
    coordinator, set_temp = await _running(hass, start)
    _valve(hass, start, 600, 17.0)  # three clicks down at the valve, and the thermostat does nothing about it
    await coordinator.async_refresh()
    assert _target(hass) == (21.0, "schedule_comfort", None)  # too early: it might still judge the turn

    start.tick(timedelta(seconds=6))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _target(hass) == (19.5, "override", "device")  # 1.5 K less than the room had
    assert set_temp[-1].data == {"entity_id": BT, "temperature": 19.5}  # and the thermostat is told
    assert coordinator.overrides["badezimmer"].until_ts - coordinator.overrides["badezimmer"].set_at_ts == 7200


async def test_a_turn_the_thermostat_writes_back_over_is_left_alone(hass: HomeAssistant, start) -> None:  # noqa: F811
    """Better Thermostat with its child lock on (measured 2026-10-12: 16 -> 14.5, back at 16 after 0.9 s)."""
    coordinator, set_temp = await _running(hass, start)
    sent = len(set_temp)
    _valve(hass, start, 600, 17.0)  # three clicks down at the valve
    assert "badezimmer" in coordinator._knob_turns
    _thermostat_writes(hass, start, 0.9, 18.5)  # and the thermostat puts its own value back, keeping its target
    assert coordinator._knob_turns == {}

    start.tick(timedelta(seconds=10))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _target(hass) == (21.0, "schedule_comfort", None)  # its lock holds against us too
    assert coordinator.overrides == {} and len(set_temp) == sent


async def test_what_the_thermostat_writes_to_its_valve_is_no_turn(hass: HomeAssistant, start) -> None:  # noqa: F811
    coordinator, _ = await _running(hass, start)
    _thermostat_writes(hass, start, 600, 18.0)  # its calibration, by service call
    _thermostat_writes(hass, start, 30, 18.5)
    _valve(hass, start, 8, 18.0)  # and a late echo of the 18.0, with nobody's context
    start.tick(timedelta(seconds=10))
    await coordinator.async_refresh()
    assert coordinator._knob_turns == {} and _target(hass) == (21.0, "schedule_comfort", None)


async def test_a_turn_the_thermostat_passes_on_counts_once(hass: HomeAssistant, start) -> None:  # noqa: F811
    coordinator, _ = await _running(hass, start)
    _valve(hass, start, 600, 20.0)  # the knob: 1.5 K up at the valve
    start.tick(timedelta(seconds=0.5))
    # Better Thermostat with its child lock off takes the number the valve shows as the room's target
    # (events/trv.py of 1.9.3), and sets the valve to what it works out for that
    hass.states.async_set(BT, "heat", {"temperature": 20.0, "current_temperature": 18.5})
    _thermostat_writes(hass, start, 0.4, 17.5)
    start.tick(timedelta(seconds=10))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _target(hass) == (20.0, "override", "device")  # the thermostat's own target, not that plus the turn
    assert coordinator._knob_turns == {}


async def test_a_knob_is_only_judged_while_the_room_control_is_active(hass: HomeAssistant, start) -> None:  # noqa: F811
    coordinator, set_temp = await _running(hass, start)
    coordinator.set_room_control_mode("shadow")
    sent = len(set_temp)
    _valve(hass, start, 600, 17.0)
    start.tick(timedelta(seconds=10))
    await coordinator.async_refresh()
    assert coordinator.overrides == {} and coordinator._knob_turns == {} and len(set_temp) == sent


async def test_a_valve_set_in_our_own_context_and_answering_late_is_no_turn(hass: HomeAssistant, start) -> None:  # noqa: F811
    """Better Thermostat passes the context of the call it got on to its valve; ours, after our command."""
    coordinator, set_temp = await _running(hass, start)
    start.tick(timedelta(minutes=10))
    await hass.services.async_call(DOMAIN, "set_override", {"room": "badezimmer", "temperature": 23.0}, blocking=True)
    await hass.async_block_till_done()
    assert set_temp[-1].data == {"entity_id": BT, "temperature": 23.0}
    ours = set_temp[-1].context
    hass.states.async_set(BT, "heat", {"temperature": 23.0, "current_temperature": 18.5}, context=ours)
    data = {"entity_id": TRV, "temperature": 20.5}  # what the thermostat works out for its valve
    hass.bus.async_fire("call_service", {"domain": "climate", "service": "set_temperature", "service_data": data}, context=ours)
    start.tick(timedelta(seconds=7))  # the valve answers late, in a context of its own
    hass.states.async_set(TRV, "heat", {"temperature": 20.5, "current_temperature": 19.4})
    assert coordinator._knob_turns == {}
    start.tick(timedelta(seconds=10))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _target(hass)[0] == 23.0  # the override asked for, not that plus two degrees


async def test_a_replaced_valve_is_the_one_whose_knob_counts(hass: HomeAssistant, start) -> None:  # noqa: F811
    """The valves were looked up once at the start: after a swap the knob and the echo filter listened to the old one."""
    from homeassistant.helpers import entity_registry as er

    coordinator, set_temp = await _running(hass, start)
    assert coordinator._valves["badezimmer"] == (BT, (TRV,))
    new = "climate.bath_trv_new"
    er.async_get(hass).async_get_or_create("climate", "mqtt", "trv_new", suggested_object_id="bath_trv_new")
    thermostat = hass.config_entries.async_entries("better_thermostat")[0]
    hass.config_entries.async_update_entry(thermostat, options={"thermostat": [new]})  # pointed at the new valve
    hass.states.async_set(new, "heat", {"temperature": 18.5, "current_temperature": 19.4})
    start.tick(timedelta(minutes=10))
    await coordinator.async_refresh()
    assert coordinator._valves["badezimmer"] == (BT, (new,)) and coordinator._valve_room == {new: "badezimmer"}

    _valve(hass, start, 60, 17.0)  # the old valve, still on the shelf: nobody's knob
    assert coordinator._knob_turns == {}
    start.tick(timedelta(seconds=60))
    hass.states.async_set(new, "heat", {"temperature": 17.0, "current_temperature": 19.4})  # three clicks down at the new one
    assert "badezimmer" in coordinator._knob_turns
    start.tick(timedelta(seconds=6))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _target(hass) == (19.5, "override", "device")

