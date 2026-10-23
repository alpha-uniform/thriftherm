"""The heat-up speed: an extra on the boiler's flow per room while it heats up (2026-10-14)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import heat_call, heat_up
from custom_components.thriftherm.engines.boiler_control import DEMAND_BOOST_K, decide
from custom_components.thriftherm.engines.boiler_load import LoadLine
from custom_components.thriftherm.engines.boiler_memory import BoilerMemory
from custom_components.thriftherm.engines.heat_up import HeatUp

from .conftest import PARAMS
from .test_boiler_control import NOW, heating_memory, inp, rr

BRISK = replace(PARAMS, boiler_heat_up_rate_k_h=1.0)


def living(temp: float, target: float = 23.5, **kw):
    """David's living room, raised from 20.5 to 23.5 °C."""
    room = rr(key="wohnzimmer", temp=temp, target=target, demand=min((target - temp) / 2.0, 1.0))
    return replace(room, boiler_call="calling", **kw)


def run(states, temps, *, rate=1.0, start=NOW, step_s=60.0, heating=True, judging=True):
    """One reading a minute; returns the states after the last one."""
    for i, temp in enumerate(temps):
        states = heat_up.follow(states, (living(temp),), rate, start + i * step_s, heating, judging)
    return states


def test_a_room_that_rises_too_slowly_gets_more_flow_and_one_that_races_less():
    flat = [20.5] * 21  # nothing happens for twenty minutes
    states = run({}, flat)
    assert states["wohnzimmer"].extra_k == heat_up.BIG_STEP_K  # less than half of 1 K/h: the big step
    states = run(states, [20.5 + 0.25 * i / 20 for i in range(1, 21)], start=NOW + 21 * 60)  # 0.75 K/h: a little slow
    assert states["wohnzimmer"].extra_k == heat_up.BIG_STEP_K + heat_up.STEP_K
    states = run(states, [20.75 + 0.33 * i / 20 for i in range(1, 21)], start=NOW + 41 * 60)  # 1 K/h: as asked
    assert states["wohnzimmer"].extra_k == heat_up.BIG_STEP_K + heat_up.STEP_K
    states = run(states, [21.08 + 0.7 * i / 20 for i in range(1, 21)], start=NOW + 61 * 60)  # 2 K/h: less
    assert states["wohnzimmer"].extra_k == heat_up.BIG_STEP_K


def test_the_first_verdict_waits_for_the_flow_and_the_radiator():
    states = run({}, [20.5] * 20)
    assert states["wohnzimmer"].extra_k == 0.0 and states["wohnzimmer"].since_ts == NOW


def test_what_a_heat_up_needed_is_where_the_next_one_starts():
    states = run({}, [20.5] * 21)
    states = heat_up.follow(states, (living(23.4),), 1.0, NOW + 3 * 3600, True, True)  # 0.1 K short: done
    assert states["wohnzimmer"] == HeatUp(extra_k=heat_up.BIG_STEP_K)
    room = living(20.5)
    assert heat_up.extra_k(states, room) == 0.0  # between heat-ups it adds nothing
    states = heat_up.follow(states, (room,), 1.0, NOW + 20 * 3600, True, True)
    assert heat_up.extra_k(states, room) == heat_up.BIG_STEP_K  # the next one starts with it at once
    assert heat_up.extra_k(states, replace(room, demand=0.25)) == pytest.approx(1.0)  # shrinking with the demand


def test_a_small_raise_is_no_heat_up_and_a_heat_up_ends_close_to_target():
    assert heat_up.follow({}, (living(22.6),), 1.0, NOW, True, True) == {}  # 0.9 K: the demand boost's
    states = heat_up.follow({}, (living(22.5),), 1.0, NOW, True, True)
    assert states["wohnzimmer"].since_ts == NOW
    states = heat_up.follow(states, (living(23.2),), 1.0, NOW + 60, True, True)
    assert states["wohnzimmer"].since_ts == NOW  # 0.3 K short: still under way
    window = replace(living(22.0), heating_allowed=False)
    assert heat_up.follow(states, (window,), 1.0, NOW + 120, True, True)["wohnzimmer"].since_ts is None


def test_a_rise_says_nothing_when_the_flow_was_not_ours_or_the_valve_shut():
    flat = [20.5] * 21
    assert run({}, flat, judging=False)["wohnzimmer"].extra_k == 0.0  # plan only, or the boiler blocked
    assert run({}, flat, heating=False)["wohnzimmer"].extra_k == 0.0  # the boiler did not heat at all
    states = {}
    for i in range(21):  # the thermostat says its valve is shut
        states = heat_up.follow(states, (living(20.5, thermostat_heating=False),), 1.0, NOW + i * 60, True, True)
    assert states["wohnzimmer"].extra_k == 0.0
    # close to target the valve throttles: no verdict
    near = {}
    for i in range(21):
        near = heat_up.follow(near, (living(23.1, target=23.5),), 1.0, NOW + i * 60, True, True)
    assert near == {} or near["wohnzimmer"].extra_k == 0.0


def test_switched_off_no_heat_up_runs_and_what_was_learned_stays():
    states = run({}, [20.5] * 21)
    off = heat_up.follow(states, (living(20.5),), 0.0, NOW + 22 * 60, True, True)
    assert off == {"wohnzimmer": HeatUp(extra_k=heat_up.BIG_STEP_K)} and heat_up.extra_k(off, living(20.5)) == 0.0


def test_the_extras_survive_a_restart_and_nothing_else_does():
    states = run({}, [20.5] * 21)
    back = BoilerMemory.from_storage(BoilerMemory(heat_up=states).to_storage())
    assert back.heat_up == {"wohnzimmer": HeatUp(extra_k=heat_up.BIG_STEP_K)}
    stored = {"heat_up": {"kuche": "x", "bad": -1.0, "flur": 99.0, "wohnzimmer": 6.0, "arbeitszimmer": 0.0}}
    assert BoilerMemory.from_storage(stored).heat_up == {"wohnzimmer": HeatUp(extra_k=6.0), "arbeitszimmer": HeatUp()}


# ---- in the boiler control
def test_davids_living_room_gets_a_hotter_flow_and_the_curve_does_not_learn_from_it():
    mem = heating_memory(start=NOW - 3600)
    before, _ = decide(inp([living(20.5)], mode="active", now=NOW, params=PARAMS, burner=True, flow=45.0, ret=28.0), mem)
    for minute in range(60):
        on = inp([living(20.5)], mode="active", now=NOW + minute * 60, params=BRISK, burner=True, flow=45.0, ret=28.0)
        cmd, mem = decide(on, mem)
        if minute == 21:
            first = cmd
    assert first.heating_up == ("wohnzimmer",) and first.heat_up_k == {"wohnzimmer": heat_up.BIG_STEP_K}
    assert first.flow_setpoint == before.flow_setpoint + heat_up.BIG_STEP_K and first.flow_set_by == "wohnzimmer"
    # the curve is for holding: a wide spread on the hotter flow is no lesson for it
    assert cmd.learning_phase == "paused" and mem.adjustments == ()
    assert cmd.heat_up_k == {"wohnzimmer": 2 * heat_up.BIG_STEP_K}  # the room did not move: two verdicts in the hour

    # with the setting at 0 the curve's learning takes the same hour as a lesson
    plain = heating_memory(start=NOW - 3600)
    for minute in range(60):
        on = inp([living(20.5)], mode="active", now=NOW + minute * 60, burner=True, flow=45.0, ret=28.0)
        cmd, plain = decide(on, plain)
    assert cmd.heating_up == () and cmd.heat_up_k is None and plain.last_adjust_reason == "spread_large"


def test_the_room_that_needs_the_most_sets_the_flow_with_its_boost():
    """The kitchen's line holds it; the living room heating up needs more: its own line and boost.

    Not the kitchen's line and the living room's boost, as one line for all rooms gave it.
    """
    lines = {"few:kuche": LoadLine(level_k=3.0, hours=5.0), "few:wohnzimmer": LoadLine(level_k=0.0, hours=5.0)}
    kitchen = replace(rr(key="kuche", temp=18.4, target=18.5, demand=0.0), boiler_call="calling")
    others = [
        replace(rr(key=key, temp=20.0, target=20.0, demand=0.0), boiler_call="idle")
        for key in ("badezimmer", "schlafzimmer", "arbeitszimmer")
    ]
    rooms = [kitchen, living(20.5), *others]
    cmd, _ = decide(inp(rooms, mode="active"), replace(heating_memory(), lines=lines))
    # the living room's line is the one the learning moves: a step on the kitchen's would not move the flow
    assert cmd.flow_set_by == "wohnzimmer" and cmd.offset_k == 0.0
    assert cmd.flow_setpoint == pytest.approx(cmd.curve_flow + DEMAND_BOOST_K, abs=0.5)  # not curve + 3 + 8
    cmd, _ = decide(inp([kitchen, living(23.5), *others], mode="active"), replace(heating_memory(), lines=lines))
    assert cmd.flow_set_by == "kuche" and cmd.flow_setpoint == pytest.approx(cmd.curve_flow + 3.0, abs=0.5)


# ---- review 2026-10-14
def test_a_stretch_counts_only_if_the_flow_was_ours_and_the_valve_open_all_through_it():
    """A verdict only looked at the moment it was due: 18 minutes on the boiler's own knob gave +4 K."""
    states = {}
    for minute in range(21):
        ours = minute < 2 or minute == 20  # the boiler on its knob, plan only or blocked in between
        states = heat_up.follow(states, (living(20.5),), 1.0, NOW + minute * 60, True, ours)
    assert states["wohnzimmer"].extra_k == 0.0 and states["wohnzimmer"].mark_ts == NOW + 20 * 60  # measured afresh
    shut = {}
    for minute in range(21):
        valve = None if minute in (0, 1, 20) else False  # Better Thermostat: idle for 18 of the 20 minutes
        shut = heat_up.follow(shut, (living(20.5, thermostat_heating=valve),), 1.0, NOW + minute * 60, True, True)
    assert shut["wohnzimmer"].extra_k == 0.0


def test_a_flow_that_could_not_rise_does_not_raise_the_extra():
    """At the flow maximum or under the ramp the extra climbed to 16 K and stayed for days."""
    states = {}
    for minute in range(21):
        states = heat_up.follow(states, (living(20.5),), 1.0, NOW + minute * 60, True, True, held=minute == 7)
    assert states["wohnzimmer"].extra_k == 0.0
    racing = {"wohnzimmer": HeatUp(extra_k=8.0)}
    for minute in range(21):  # held, but far too fast: less is always allowed
        racing = heat_up.follow(racing, (living(20.5 + minute * 0.05),), 1.0, NOW + minute * 60, True, True, held=True)
    assert racing["wohnzimmer"].extra_k == 6.0


def test_a_short_heat_up_that_raced_takes_a_step_off_at_its_end():
    """A 1.2 K heat-up racing at 3 K/h never got a verdict and kept its 16 K for five days."""
    states = {"wohnzimmer": HeatUp(extra_k=16.0)}
    for minute in range(23):
        states = heat_up.follow(states, (living(21.3 + minute * 0.05, target=22.5),), 1.0, NOW + minute * 60, True, True)
    assert states["wohnzimmer"] == HeatUp(extra_k=14.0)  # ended 0.1 K short after 1.1 K in 22 minutes
    window = {"wohnzimmer": HeatUp(extra_k=16.0)}
    for minute in range(20):  # ended by an open window: no verdict
        room = living(21.3 + minute * 0.05, target=22.5)
        window = heat_up.follow(window, (replace(room, heating_allowed=minute < 19),), 1.0, NOW + minute * 60, True, True)
    assert window["wohnzimmer"].extra_k == 16.0


def test_a_short_heat_up_that_raced_takes_a_step_off_when_its_call_ends_a_tenth_short_of_target():
    """Sensors report tenths: the room's call stops 0.1 K below target in the very cycle the heat-up ends."""
    states = {"wohnzimmer": HeatUp(extra_k=16.0)}
    call = heat_call.CallMemory()
    for minute in range(23):  # 1.1 K in 22 minutes, a tenth every two minutes
        room = living(round(21.3 + 0.1 * (minute // 2), 1), target=22.5)
        state, call = heat_call.update(room, call, NOW + minute * 60)
        states = heat_up.follow(states, (replace(room, boiler_call=state),), 1.0, NOW + minute * 60, True, True)
    assert state == heat_call.CALL_IDLE
    assert states["wohnzimmer"] == HeatUp(extra_k=14.0)


def test_the_extra_is_judged_in_the_hour_after_a_shower():
    """The hour after hot water holds back the spread; the rooms' rise does not wait for it."""
    mem = replace(heating_memory(start=NOW - 3600), hot_water_seen_ts=NOW - 300)
    for minute in range(21):
        on = inp([living(20.5)], mode="active", now=NOW + minute * 60, params=BRISK, burner=True, flow=45.0, ret=38.0)
        cmd, mem = decide(replace(on, status_known=True, reported_mode="heating"), mem)
    assert mem.heat_up["wohnzimmer"].extra_k == heat_up.BIG_STEP_K


def test_the_extras_are_shown_only_where_they_act():
    stored = replace(heating_memory(), heat_up={"wohnzimmer": HeatUp(extra_k=6.0)})
    cmd, _ = decide(inp([living(20.5)], mode="active", params=BRISK), stored)
    assert cmd.heat_up_k == {"wohnzimmer": 6.0} and cmd.heating_up == ("wohnzimmer",) and cmd.flow_set_by == "wohnzimmer"
    off, _ = decide(inp([living(20.5)], mode="active"), stored)  # the speed set to 0
    assert off.heat_up_k is None and off.heating_up == ()
    blocked, _ = decide(inp([living(20.5)], mode="active", params=BRISK, advice="none"), replace(stored, state="block"))
    assert blocked.plan == "block" and blocked.heating_up == () and blocked.flow_set_by is None


def test_an_extra_swallowed_by_the_flow_minimum_does_not_grow():
    """Test of 2026-10-15: at 22 °C outside the curve gave 23 °C, the minimum 40; the extra rose to 14 K and moved nothing."""
    warm = replace(BRISK, boiler_flow_min=40.0)
    mem = heating_memory(start=NOW - 3600)
    for minute in range(42):
        on = inp([living(20.5, target=21.5)], mode="active", now=NOW + minute * 60, params=warm, burner=True, outdoor=22.0)
        cmd, mem = decide(on, mem)
    assert cmd.flow_setpoint == 40.0 and mem.heat_up["wohnzimmer"].extra_k == 0.0
