"""The lead at the room thermostat (docs/PHASE13_VALVES.md §6): while the boiler runs for a room, its thermostat is
asked for a little more than the room's target, so that it does not throttle its valve in the band where the boiler
heats (CALL_ON_K to CALL_OFF_K below the target). It never moves where the valve shuts: the lead ends with the call."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.thriftherm.const import CTRL_ACTIVE
from custom_components.thriftherm.engines import heat_call, room_control
from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

from .conftest import climate_loaded, room_result

NOW = 2_000_000.0


def rr(temp=20.6, target=21.0, reason="schedule_comfort", allowed=True):
    return room_result(
        "wohnzimmer", target, temp, target_reason=reason, deviation_k=round(temp - target, 2),
        heating_allowed=allowed, window_open=not allowed,
    )


def lead(  # noqa: PLR0913 - every condition of the rule
    state=heat_call.CALL_CALLING, sent=None, own=None, option=0.5, boiler_heats=True, left_to_unit=False, leading=False,
    held=None,
):
    own = own or rr()
    return heat_call.lead_k(state, sent or own, own, option, boiler_heats, left_to_unit, leading=leading, held_k=held)


def test_a_calling_room_gets_the_lead_while_the_boiler_heats():
    assert lead() == 0.5
    assert lead(option=1.0) == 1.0


@pytest.mark.parametrize(
    "state",
    [
        heat_call.CALL_IDLE,
        heat_call.CALL_STALLED,
        heat_call.CALL_COMFORT_ENDING,
        heat_call.CALL_NOT_ALLOWED,
        # the thermostat reports its valve shut (issue #3): a higher setpoint opened it, and the room called by itself
        heat_call.CALL_THERMOSTAT_IDLE,
    ],
)
def test_no_lead_unless_the_room_calls(state):
    assert lead(state=state) == 0.0


def test_no_lead_without_a_burning_boiler_for_the_room():
    assert lead(boiler_heats=False) == 0.0  # blocked, district heat, no boiler control
    assert lead(left_to_unit=True) == 0.0  # the heat pump serves the room
    assert lead(option=0.0) == 0.0  # switched off
    assert lead(own=rr(allowed=False)) == 0.0  # window open


def test_a_room_heating_along_gets_the_larger_of_the_two_never_both():
    own = rr(temp=20.8)
    joined = replace(own, target=21.5, target_reason=heat_call.JOINED_COMFORT)
    # comfort + JOIN_K already is the room's target + 0.5: nothing on top of it
    assert lead(state=heat_call.CALL_IDLE, sent=joined, own=own) == 0.0
    # a lead of 1 K reaches above it: the thermostat gets the room's target + 1 K, 22 °C
    assert lead(state=heat_call.CALL_IDLE, sent=joined, own=own, option=1.0) == 0.5
    preheat = replace(own, target=21.0, target_reason=heat_call.JOINED_PREHEAT)
    setback = replace(own, target=17.0, target_reason="schedule_setback")
    assert lead(state=heat_call.CALL_IDLE, sent=preheat, own=setback) == 0.0  # comfort is above setback + lead


@pytest.mark.parametrize("state", [heat_call.CALL_STALLED, heat_call.CALL_COMFORT_ENDING])
def test_a_stalled_room_heating_along_gets_no_more_than_join_k(state):
    """Review 2026-11-13: a stalled room heats along (join skips only calling rooms); the stall says the room is as
    warm as it gets, and the lead of 1 K must not pull its valve open further than JOIN_K."""
    own = rr(temp=20.8)
    joined = replace(own, target=21.5, target_reason=heat_call.JOINED_COMFORT)
    assert lead(state=state, sent=joined, own=own, option=1.0) == 0.0


def _decide(result, mem=None, thermostat=21.0, lead_k=0.0, now=NOW, **kw):  # noqa: PLR0913
    return room_control.decide(
        "wohnzimmer", CTRL_ACTIVE, result, True, "heat", thermostat, now, mem or RoomCtrlMemory(), lead_k=lead_k, **kw
    )


def test_the_thermostat_gets_the_target_plus_the_lead_and_the_room_keeps_its_target():
    cmd, mem = _decide(rr(), lead_k=0.5)
    assert cmd.plan == "set" and cmd.send and cmd.target == 21.5
    assert mem.last_sent_target == 21.5 and mem.sent_extra_k == 0.5
    # confirmed: in sync, nothing sent
    cmd, mem = _decide(rr(), mem, thermostat=21.5, lead_k=0.5, now=NOW + 30)
    assert cmd.plan == "hold" and cmd.reason == "in_sync" and not cmd.send
    # the call ends: the target goes back once the minute is over
    cmd, mem = _decide(rr(temp=20.9), mem, thermostat=21.5, lead_k=0.0, now=NOW + 61)
    assert cmd.send and cmd.target == 21.0 and mem.sent_extra_k == 0.0


def test_the_lead_stays_within_the_thermostats_limits():
    cmd, mem = _decide(rr(), thermostat=20.0, lead_k=0.5, max_temp=21.0)
    assert cmd.target == 21.0 and mem.sent_extra_k == 0.0


def test_a_thermostat_already_showing_the_lead_is_in_sync_and_remembered_so():
    before = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 600, confirmed=True)
    cmd, mem = _decide(rr(), before, thermostat=21.5, lead_k=0.5)
    assert cmd.reason == "in_sync" and mem.last_sent_target == 21.5 and mem.sent_extra_k == 0.5


def test_a_thermostat_with_a_rule_of_its_own_gets_no_lead():
    cmd, _ = _decide(rr(), thermostat=12.0, lead_k=0.5, own_target=True)
    assert cmd.reason == "thermostat_own_rule" and cmd.target == 21.0


def test_the_extra_is_stored_and_read_back():
    mem = RoomCtrlMemory(last_sent_target=21.5, sent_extra_k=0.5)
    assert RoomCtrlMemory.from_storage(mem.to_storage()) == mem
    assert RoomCtrlMemory.from_storage({"last_sent_target": 21.0}).sent_extra_k == 0.0


@pytest.mark.parametrize(("step", "option", "sent"), [(1.0, 0.5, 21.0), (1.0, 1.0, 22.0), (0.5, 0.5, 21.5), (None, 0.5, 21.5)])
def test_a_thermostat_in_whole_degrees_gets_the_lead_in_its_own_steps(step, option, sent):
    """Review 2026-11-13: half a degree of lead on a thermostat in whole degrees is a whole degree or none; it gets
    the lead only where its step holds it, and what is subtracted from a turn is what it really had."""
    cmd, mem = _decide(rr(), thermostat=20.0, lead_k=option, step=step)
    assert cmd.target == sent and mem.sent_extra_k == sent - 21.0


@pytest.mark.parametrize(("value", "extra", "wish"), [(22.0, 0.5, 21.5), (22.0, 0.0, 22.0), (21.0, 0.5, 20.5), (22.0, 1.0, 21.0)])
def test_a_turn_by_hand_is_a_wish_for_the_room_without_the_lead(value, extra, wish):
    assert room_control.without_extra(value, RoomCtrlMemory(last_sent_target=21.5, confirmed=True, sent_extra_k=extra)) == wish


# ------------------------------------------------------------------ through Home Assistant
LIVING = "climate.living_trv"


async def _living_with_thermostat(hass, freezer, lead=None):
    from datetime import datetime

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

    from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN

    from .test_integration import _entry_data, _set_states

    freezer.move_to(datetime(2026, 11, 14, 10, 0, tzinfo=dt_util.UTC))
    await climate_loaded(hass)
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "mqtt", "publish")  # the boiler's commands
    _set_states(hass)
    hass.states.async_set(LIVING, "heat", {"temperature": 18.5, "current_temperature": 17.0, "hvac_action": "heating"})
    data = _entry_data()
    data[CONF_ROOMS][1]["climate_entity"] = LIVING
    if lead is not None:
        data[CONF_ROOMS][1]["thermostat_lead_k"] = lead
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm",
        options={"room_allow_active_control": True, "boiler_allow_active_control": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for control in ("boiler_control", "room_control"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": f"select.thriftherm_{control}", "option": "active"}, blocking=True
        )
    await hass.async_block_till_done()
    return entry.runtime_data, set_temp


def _sent(set_temp):
    return [c.data["temperature"] for c in set_temp if c.data["entity_id"] == LIVING]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_the_thermostat_of_a_calling_room_gets_the_lead_until_the_call_ends(hass, freezer) -> None:
    from datetime import timedelta

    coordinator, set_temp = await _living_with_thermostat(hass, freezer)
    assert hass.states.get("sensor.thriftherm_boiler_command").state == "heat"
    assert hass.states.get("sensor.thriftherm_wohnzimmer_heat_demand").attributes["boiler_call"] == "calling"
    assert _sent(set_temp)[-1] == 19.0  # comfort 18.5 + 0.5
    target = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")
    assert float(target.state) == 18.5  # the room's target is its own
    plan = hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan")
    assert plan.attributes["setpoint"] == 19.0 and plan.attributes["lead_k"] == 0.5

    # at its target: the call ends, and it does not heat along either (the bathroom keeps the boiler running)
    hass.states.async_set(LIVING, "heat", {"temperature": 19.0, "current_temperature": 18.5, "hvac_action": "heating"})
    hass.states.async_set("sensor.living_temp", "18.5")
    freezer.tick(timedelta(seconds=61))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_wohnzimmer_heat_demand").attributes["boiler_call"] == "idle"
    assert _sent(set_temp)[-1] == 18.5
    assert float(hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature").state) == 18.5


async def test_a_room_without_lead_gets_its_target_as_before(hass, freezer) -> None:
    _, set_temp = await _living_with_thermostat(hass, freezer, lead=0)
    assert _sent(set_temp) == []  # it shows 18.5 already: in sync
    assert hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan").attributes["lead_k"] == 0.0


async def test_a_turn_by_hand_during_the_lead_is_a_wish_for_the_room(hass, freezer) -> None:
    from datetime import timedelta

    from homeassistant.core import Context

    coordinator, set_temp = await _living_with_thermostat(hass, freezer)
    hass.states.async_set(LIVING, "heat", {"temperature": 19.0, "current_temperature": 17.0, "hvac_action": "heating"})
    freezer.tick(timedelta(seconds=30))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    person = Context(user_id="someone")
    data = {"entity_id": LIVING, "temperature": 20.0}
    hass.bus.async_fire("call_service", {"domain": "climate", "service": "set_temperature", "service_data": data}, context=person)
    turned = {"temperature": 20.0, "current_temperature": 17.0, "hvac_action": "heating"}
    hass.states.async_set(LIVING, "heat", turned, context=person)
    freezer.tick(timedelta(seconds=30))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    target = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")
    # Review 2026-11-13: a value a person or an automation asks for is absolute. Whoever sets 20 wants 20; only a turn of
    # the knob at the device is relative to what it showed.
    assert float(target.state) == 20.0 and target.attributes["reason"] == "override"
    # still calling: the thermostat keeps what the person set, 19.5 + 0.5
    freezer.tick(timedelta(seconds=61))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _sent(set_temp)[-1] == 20.5  # the room's 20 plus the lead, while it calls
    plan = hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan")
    assert plan.attributes["setpoint"] == 20.5


async def test_a_turn_of_the_knob_during_the_lead_counts_for_the_room(hass, freezer) -> None:
    """A turn at the device, no context: one click up from the 19 it showed is 19 for the room, 18.5 + 0.5."""
    from datetime import timedelta

    coordinator, set_temp = await _living_with_thermostat(hass, freezer)
    hass.states.async_set(LIVING, "heat", {"temperature": 19.0, "current_temperature": 17.0, "hvac_action": "heating"})
    freezer.tick(timedelta(seconds=120))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    hass.states.async_set(LIVING, "heat", {"temperature": 19.5, "current_temperature": 17.0, "hvac_action": "heating"})
    freezer.tick(timedelta(seconds=30))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")
    assert float(target.state) == 19.0 and target.attributes["reason"] == "override"
    assert target.attributes["override_by"] == "device"
    plan = hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan")
    assert plan.attributes["reason"] == "in_sync" and plan.attributes["setpoint"] == 19.5


async def test_no_lead_while_the_boiler_runs_on_its_own_knob(hass, freezer) -> None:
    """Review 2026-11-13: in "observe only" the boiler's plan is worked out, but the boiler goes by its own knob."""
    from datetime import timedelta

    coordinator, set_temp = await _living_with_thermostat(hass, freezer)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "shadow"}, blocking=True
    )
    freezer.tick(timedelta(seconds=120))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    plan = hass.states.get("sensor.thriftherm_wohnzimmer_thermostat_plan")
    assert plan.attributes["setpoint"] == 18.5 and plan.attributes["lead_k"] == 0.0


# ------------------------------------------------------------------ review of 2026-11-13 (code)
def test_a_lead_already_at_the_thermostat_stays_while_it_reports_idle_within_the_call():
    """Review 2026-11-13: the valve's own control shuts for a moment and reports idle. Dropping the lead then and giving
    it back when it reports heating again sent a setpoint every minute. The call runs: the rule of issue #3 (a higher
    setpoint opens a shut valve and starts a call of its own) does not apply."""
    assert lead(state=heat_call.CALL_THERMOSTAT_IDLE, leading=True) == 0.5
    assert lead(state=heat_call.CALL_THERMOSTAT_IDLE, leading=False) == 0.0
    assert lead(state=heat_call.CALL_IDLE, leading=True) == 0.0  # the call ended: the lead ends with it


def test_a_lead_is_held_while_the_boiler_waits_for_its_data():
    """Review 2026-11-13: right after a restart, or while the boiler's data are missing, the boiler's plan is "hands off".
    A room that still calls (or has no reading yet) keeps the lead its thermostat has, instead of a 21 and a 21.5 again
    once the sensors are back; a room that is warm loses it."""
    no_reading = rr()
    no_reading = replace(no_reading, temperature=None)
    assert heat_call.lead_k(heat_call.CALL_IDLE, no_reading, no_reading, 0.5, False, False, held_k=0.5) == 0.5
    assert lead(state=heat_call.CALL_CALLING, boiler_heats=False, held=0.5) == 0.5
    assert lead(state=heat_call.CALL_IDLE, boiler_heats=False, held=0.5) == 0.0
    assert lead(state=heat_call.CALL_CALLING, boiler_heats=False, held=0.5, option=0.0) == 0.0


def test_the_extra_counts_from_the_rooms_own_target_also_when_it_heats_along():
    """Review 2026-11-13: heating along, the thermostat stands JOIN_K above the room's target; a turn of the knob then is
    relative to that too, and the plan says so."""
    own = rr(temp=20.8)
    joined = replace(own, target=21.5, target_reason=heat_call.JOINED_COMFORT)
    cmd, mem = _decide(joined, thermostat=21.0, own_c=21.0)
    assert cmd.target == 21.5 and cmd.lead_k == 0.5 and mem.sent_extra_k == 0.5


@pytest.mark.parametrize(("target", "sent"), [(20.25, 20.5), (20.75, 21.5), (21.0, 21.5)])
def test_a_target_off_the_half_degrees_still_gets_half_a_degree(target, sent):
    """Review 2026-11-13: a value set by a person (absolute, in a thermostat's own step of 0.1) is no half degree;
    rounding target + lead gave 1 K or none."""
    cmd, mem = _decide(rr(target=target, temp=target - 0.5), thermostat=19.0, lead_k=0.5)
    assert cmd.target == sent and mem.sent_extra_k == 0.5


def test_a_thermostat_in_whole_degrees_gets_the_lead_on_its_own_grid():
    """Review 2026-11-13: 21.5 shown as 22 by a thermostat in whole degrees; a lead of 1 K is 23, not 22.5 shown as 22."""
    cmd, mem = _decide(rr(target=21.5), thermostat=20.0, lead_k=1.0, step=1.0)
    assert cmd.target == 23.0 and mem.sent_extra_k == 1.0


def test_a_turn_before_the_thermostat_confirmed_us_takes_off_what_it_still_showed():
    """Review 2026-11-13: the call ended and 21 went out, the slow valve still shows the 21.5 of the lead. A turn to 22
    is one click up from 21.5: 21.5 for the room, not 22."""
    sent = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW, thermostat_before_send=21.5, sent_extra_k=0.0,
                          extra_before_send=0.5)
    assert room_control.without_extra(22.0, sent) == 21.5
    confirmed = replace(sent, confirmed=True)
    assert room_control.without_extra(22.0, confirmed) == 22.0
    cmd, mem = _decide(rr(temp=21.0), RoomCtrlMemory(last_sent_target=21.5, last_sent_ts=NOW - 600, confirmed=True,
                                                    sent_extra_k=0.5), thermostat=21.5)
    assert cmd.send and mem.extra_before_send == 0.5 and mem.sent_extra_k == 0.0


def test_a_new_extra_alone_is_remembered_in_sync():
    """Review 2026-11-13: same setpoint, other extra (an override of 21.5 replaces a 21 + 0.5): the extra changes."""
    before = RoomCtrlMemory(last_sent_target=21.5, last_sent_ts=NOW - 600, confirmed=True, sent_extra_k=0.5)
    cmd, mem = _decide(rr(target=21.5), before, thermostat=21.5)
    assert cmd.reason == "in_sync" and mem.sent_extra_k == 0.0


@pytest.mark.parametrize(("raw", "kept"), [(None, 0.5), ("x", 0.5), (float("nan"), 0.5), (0.3, 0.5), (0.2, 0.0), (2, 1.0),
                                           (-1, 0.0), (1, 1.0), (0, 0.0)])
def test_a_stored_lead_option_is_read_safely(raw, kept):
    from custom_components.thriftherm.adapters.config import room_configs_from_config

    room = {"key": "wohnzimmer", "temperature_sensor": "sensor.t"}
    if raw is not None:
        room["thermostat_lead_k"] = raw
    assert room_configs_from_config([room])[0].lead_k == kept


def test_the_room_form_offers_the_lead_with_its_default():
    from custom_components.thriftherm.config_schema import schema_room

    fields = {str(key): key for key in schema_room({}, False).schema}
    assert fields["thermostat_lead_k"].default() == 0.5
    stored = {str(key): key for key in schema_room({"thermostat_lead_k": 1.0}, False).schema}
    assert stored["thermostat_lead_k"].default() == 1.0
