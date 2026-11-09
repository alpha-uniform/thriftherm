"""Phase 10: room setpoints for Better Thermostat."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines.room_control import (
    RoomCtrlMemory,
    decide,
    manual_change,
    pending_since,
    thermostat_setpoint,
    valve_heard,
)
from custom_components.thriftherm.models import RoomResult

from .conftest import room_result

NOW = 3_000_000.0


def rr(target=21.0, reason="schedule_comfort") -> RoomResult:
    return room_result(target=target, temperature=20.0, target_reason=reason, deviation_k=-1.0)


def run(mode="active", target=21.0, hvac="heat", thermostat=20.0, now=NOW, mem=RoomCtrlMemory(), has=True, step=None, wrapped=False):
    return decide("badezimmer", mode, rr(target), has, hvac, thermostat, now, mem, step=step, wrapped=wrapped)


def test_setpoint_rounding_and_limits():
    assert thermostat_setpoint(20.74) == 20.5
    assert thermostat_setpoint(20.76) == 21.0
    assert thermostat_setpoint(2.0) == 5.0
    assert thermostat_setpoint(40.0) == 30.0


def test_off_and_hands_off_cases():
    assert run(mode="off")[0].plan == "disabled"
    assert run(has=False)[0].reason == "no_thermostat"
    assert run(hvac=None)[0].reason == "thermostat_unavailable"
    cmd, _ = run(hvac="off")
    assert cmd.plan == "hands_off" and cmd.reason == "thermostat_off"  # summer: left alone


def test_sends_when_different_and_holds_when_in_sync():
    cmd, mem = run(thermostat=17.0)
    assert (cmd.plan, cmd.target, cmd.send) == ("set", 21.0, True)
    assert mem.last_sent_target == 21.0 and not mem.confirmed
    cmd, mem = run(thermostat=21.0, now=NOW + 30, mem=mem)
    assert cmd.plan == "hold" and cmd.reason == "in_sync" and mem.confirmed


def test_shadow_plans_without_memory():
    cmd, mem = run(mode="shadow", thermostat=17.0)
    assert cmd.plan == "set" and cmd.send
    assert mem == RoomCtrlMemory()


def test_waits_for_confirmation_then_retries():
    _, mem = run(thermostat=17.0)
    cmd, mem = run(thermostat=17.0, now=NOW + 120, mem=mem)
    assert cmd.plan == "hold" and cmd.reason == "waiting_for_thermostat"
    cmd, _ = run(thermostat=17.0, now=NOW + 301, mem=mem)
    assert cmd.plan == "set" and cmd.send  # retry, not a manual change


def test_manual_change_detection():
    confirmed = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 600, confirmed=True)
    assert manual_change("active", 23.0, confirmed) == 23.0
    assert manual_change("active", 21.0, confirmed) is None  # unchanged
    assert manual_change("shadow", 23.0, confirmed) is None  # nothing sent in shadow mode
    pending = replace(confirmed, confirmed=False)
    assert manual_change("active", 23.0, pending) is None  # our value not echoed yet: not manual
    # with the override applied the room target equals the thermostat: in sync, no fight
    cmd, _ = run(target=23.0, thermostat=23.0, mem=RoomCtrlMemory(last_sent_target=23.0, last_sent_ts=NOW, confirmed=True))
    assert cmd.plan == "hold" and cmd.reason == "in_sync"


def test_new_target_is_sent_after_rate_limit():
    mem = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 30, confirmed=True)
    cmd, _ = run(target=17.0, thermostat=21.0, mem=mem)
    assert cmd.plan == "hold" and cmd.reason == "rate_limit"
    cmd, _ = run(target=17.0, thermostat=21.0, mem=replace(mem, last_sent_ts=NOW - 61))
    assert cmd.plan == "set" and cmd.target == 17.0


def test_a_setpoint_stays_within_what_the_thermostat_accepts():
    # found in the code 2026-10-13: the frost target of 7 °C for a thermostat that starts at 8 was
    # rejected by Home Assistant and sent again every five minutes for good
    assert thermostat_setpoint(7.0, 8.0, 28.0) == 8.0 and thermostat_setpoint(29.5, 8.0, 28.0) == 28.0
    assert thermostat_setpoint(4.2, 4.0, 35.0) == 4.0 and thermostat_setpoint(33.0, 4.0, 35.0) == 33.0  # its limits, not 5 and 30
    assert thermostat_setpoint(40.0, 28.0, 8.0) == 30.0  # limits that make no sense are none

    frost = rr(7.0, "mode_off_frost_protection")
    cmd, mem = decide("badezimmer", "active", frost, True, "heat", 21.0, NOW, RoomCtrlMemory(), min_temp=8.0, max_temp=28.0)
    assert (cmd.plan, cmd.target, cmd.send) == ("set", 8.0, True) and mem.last_sent_target == 8.0
    cmd, mem = decide("badezimmer", "active", frost, True, "heat", 8.0, NOW + 30, mem, min_temp=8.0, max_temp=28.0)
    assert cmd.reason == "in_sync" and not cmd.send and mem.confirmed
    assert manual_change("active", 8.0, mem) is None  # the thermostat at its limit is nobody's hand


def test_a_rounded_setpoint_the_thermostat_already_shows_is_not_sent_for_good():
    # review 2026-11-08: a thermostat in whole degrees at 22 is sent 21.5. It rounds to 22, which it
    # already shows, so no state change and no answer comes. It was sent 21.5 every five minutes for good.
    mem = RoomCtrlMemory(last_sent_target=22.0, last_sent_ts=NOW - 3600, confirmed=True)
    sends = 0
    for minute in range(0, 60):
        cmd, mem = run(target=21.5, thermostat=22.0, now=NOW + 60 * minute, mem=mem)
        sends += cmd.send
    assert sends == 2  # the setpoint and one retry, in case the first got lost on the way
    assert cmd.plan == "hold" and cmd.reason == "in_sync"
    assert manual_change("active", 22.0, mem) is None  # its rendering is nobody's hand


def test_an_unanswered_setpoint_is_pending_since_its_first_send():
    # review 2026-11-08: the retries must not restart the time a thermostat has had to answer
    _, mem = run(thermostat=17.0)
    assert pending_since(mem) == NOW
    for minute in range(1, 31):
        _, mem = run(thermostat=17.0, now=NOW + 60 * minute, mem=mem)
    assert mem.last_sent_ts > NOW and pending_since(mem) == NOW
    _, mem = run(target=22.0, thermostat=17.0, now=NOW + 1900, mem=mem)  # another target: still no answer since
    assert pending_since(mem) == NOW
    assert pending_since(RoomCtrlMemory.from_storage(mem.to_storage())) == NOW
    _, mem = run(target=22.0, thermostat=22.0, now=NOW + 1960, mem=mem)
    assert mem.confirmed and pending_since(mem) is None
    _, mem = run(target=21.0, thermostat=22.0, now=NOW + 2100, mem=mem)  # pending anew from this send
    assert pending_since(mem) == NOW + 2100
    rendered = RoomCtrlMemory(last_sent_target=21.5, last_sent_ts=NOW, thermostat_before_send=22.0, answered_with=22.0, pending_since_ts=NOW)
    assert pending_since(rendered) is None  # the thermostat answered in its own step
    assert pending_since(RoomCtrlMemory()) is None


def test_a_setpoint_further_off_is_still_retried():
    # a thermostat that stays 4 degrees off has not rendered what was sent: retried as before
    _, mem = run(thermostat=17.0)
    sends = 0
    for minute in range(1, 31):
        cmd, mem = run(thermostat=17.0, now=NOW + 60 * minute, mem=mem)
        sends += cmd.send
    assert sends == 6


def test_behind_a_room_thermostat_of_its_own_a_setpoint_waits_for_the_valve():
    # review 2026-11-08 (#R0/#R13/#R20): Better Thermostat takes a setpoint at once, whether its valve is
    # there or not, so its answer said nothing and a valve that dropped out was found only after 6 hours
    _, mem = run(thermostat=17.0)
    _, mem = run(thermostat=21.0, now=NOW + 30, mem=mem)  # Better Thermostat answers
    assert mem.confirmed and pending_since(mem) is None
    assert pending_since(mem, wrapped=True) == NOW  # its valve has not been heard since
    assert valve_heard(mem, silent_s=100.0, now_ts=NOW + 90) is mem  # heard last before the send
    heard = valve_heard(mem, silent_s=20.0, now_ts=NOW + 90)  # heard 70 s after the send: it answered
    assert pending_since(heard, wrapped=True) is None
    assert pending_since(RoomCtrlMemory.from_storage(mem.to_storage()), wrapped=True) == NOW
    # a new target before the valve was heard: still unanswered since the first one
    _, later = run(target=22.0, thermostat=21.0, now=NOW + 600, mem=mem, wrapped=True)
    assert pending_since(later, wrapped=True) == NOW
    _, later = run(target=22.0, thermostat=21.0, now=NOW + 600, mem=heard, wrapped=True)
    assert pending_since(later, wrapped=True) == NOW + 600
    assert valve_heard(RoomCtrlMemory(), silent_s=0.0, now_ts=NOW) == RoomCtrlMemory()
    assert valve_heard(mem, silent_s=None, now_ts=NOW + 90) is mem


@pytest.mark.parametrize("step", [0.5, None])
def test_a_thermostat_in_half_degrees_that_lost_a_setpoint_is_retried(step):
    # review 2026-11-08 (#R14): at 21 and sent 22 twice without an answer, it was taken for a rendering of 22
    # and held a degree low with no more retries; a thermostat in half degrees can show 22
    mem = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 3600, confirmed=True)
    sends = 0
    for minute in range(0, 31):
        cmd, mem = run(target=22.0, thermostat=21.0, now=NOW + 60 * minute, mem=mem, step=step)
        sends += cmd.send
    assert sends == 7 and cmd.reason != "in_sync"
    assert mem.answered_with is None and pending_since(mem) == NOW
    mem = RoomCtrlMemory(last_sent_target=22.0, last_sent_ts=NOW - 3600, confirmed=True)
    for minute in range(0, 31):  # 21.5 it holds in half degrees: 22 shown is no rendering of it
        cmd, mem = run(target=21.5, thermostat=22.0, now=NOW + 60 * minute, mem=mem, step=0.5)
    assert mem.answered_with is None and pending_since(mem) == NOW


def test_a_thermostat_in_whole_degrees_that_says_so_renders_a_half_degree():
    mem = RoomCtrlMemory(last_sent_target=22.0, last_sent_ts=NOW - 3600, confirmed=True)
    sends = 0
    for minute in range(0, 60):
        cmd, mem = run(target=21.5, thermostat=22.0, now=NOW + 60 * minute, mem=mem, step=1.0)
        sends += cmd.send
    assert sends == 2 and cmd.reason == "in_sync"
    mem = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 3600, confirmed=True)
    for minute in range(0, 31):  # but 22 it can show: 21 is no rendering of it
        cmd, mem = run(target=22.0, thermostat=21.0, now=NOW + 60 * minute, mem=mem, step=1.0)
    assert mem.answered_with is None and cmd.reason != "in_sync"
