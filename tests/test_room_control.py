"""Phase 10: room setpoints for Better Thermostat."""

from dataclasses import replace

from custom_components.thriftherm.engines.room_control import RoomCtrlMemory, decide, manual_change, thermostat_setpoint
from custom_components.thriftherm.models import RoomResult

NOW = 3_000_000.0


def rr(target=21.0, reason="schedule_comfort") -> RoomResult:
    return RoomResult(
        key="badezimmer", target=target, target_reason=reason, temperature=20.0, deviation_k=-1.0, demand=0.5,
        trend_k_per_h=None, dew_point_c=None, abs_humidity_g_m3=None, window_open=False, window_unknown=False,
        heating_allowed=True, internal_gain_w=None,
    )


def run(mode="active", target=21.0, hvac="heat", thermostat=20.0, now=NOW, mem=RoomCtrlMemory(), has=True):
    return decide("badezimmer", mode, rr(target), has, hvac, thermostat, now, mem)


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
