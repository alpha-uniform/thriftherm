"""How many rooms call for heat: the spread rule, and a heating curve per load class."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import boiler_learning as learning
from custom_components.thriftherm.engines import boiler_load as load
from custom_components.thriftherm.engines.boiler_control import decide
from custom_components.thriftherm.engines.boiler_load import LoadLine
from custom_components.thriftherm.engines.boiler_memory import BoilerMemory, correction

from .conftest import PARAMS
from .test_boiler_control import NOW, feed, heating_memory, rr
from .test_boiler_control import inp as _inp

KEYS = ("badezimmer", "schlafzimmer", "wohnzimmer", "kuche", "arbeitszimmer")
COMMON = replace(PARAMS, boiler_load_model=False)  # one common correction for all load classes, as before


def inp(*args, **kwargs):
    return _inp(*args, **kwargs)


def flat(calling: int, *, demand: float = 0.2, trend: float | None = None, others: str = "idle") -> list:
    """Five rooms, the first `calling` of them asking the boiler for heat."""
    return [
        replace(rr(key=key, demand=demand if i < calling else 0.0, trend=trend), boiler_call="calling" if i < calling else others)
        for i, key in enumerate(KEYS)
    ]


def feed_with(params, rooms, mem=None, *, flow=45.0, ret=42.0, samples=22, start=NOW, outdoor=5.0):
    mem = mem or heating_memory(start=start)
    cmd, now = None, start
    for i in range(samples):
        now = start + learning.SETTLE_S + i * 60.0
        on = inp(rooms, mode="active", now=now, params=params, flow=flow, ret=ret, burner=True, outdoor=outdoor)
        cmd, mem = decide(on, mem)
    return cmd, mem, now


# ------------------------------------------------------------------ stage 1: the spread rule
def test_counting_the_rooms():
    assert load.majority_from(5, 0.5) == 3 and load.majority_from(4, 0.5) == 2 and load.majority_from(1, 0.5) == 1
    assert load.majority_from(5, 0.0) == 1 and load.majority_from(0, 0.5) == 1  # never fewer than one room
    assert load.spread_rule(2, 5, 0.5) == "blocked_few_rooms" and load.spread_rule(3, 5, 0.5) == "active"
    assert [load.load_class(n, 5, 0.5) for n in range(6)] == [None, "single", "few", "majority", "majority", "majority"]
    assert load.load_class(1, 2, 0.5) == "majority"  # one of two rooms is a fair share


def test_only_the_bathroom_heating_with_a_small_spread_does_not_lower_the_curve():
    """One open radiator returns the water almost as warm as it went out; that used to read as "flow too hot"."""
    cmd, mem, _ = feed(heating_memory(), flat(1), flow=45.0, ret=42.0)
    assert correction(mem, 5.0, "single") == 0.0 and mem.adjustments == ()
    assert cmd.calling_rooms == 1 and cmd.rooms_total == 5 and cmd.spread_rule == "blocked_few_rooms"
    assert cmd.load_class == "single"


def test_the_majority_heating_with_a_small_spread_lowers_the_curve_as_before():
    cmd, mem, _ = feed(heating_memory(), flat(3), flow=45.0, ret=42.0)
    assert correction(mem, 5.0, "majority") == pytest.approx(-1.0) and mem.last_adjust_reason == "spread_small"
    assert cmd.spread_rule == "active" and cmd.load_class == "majority"


def test_the_majority_heating_with_a_large_spread_and_demand_raises_the_curve():
    _, mem, _ = feed(heating_memory(), flat(3, demand=0.5), flow=50.0, ret=32.0)
    assert correction(mem, 5.0, "majority") == pytest.approx(1.0) and mem.last_adjust_reason == "spread_large"
    _, few, _ = feed(heating_memory(), flat(2, demand=0.5), flow=50.0, ret=32.0)
    assert few.adjustments == ()  # with two of five the spread says nothing either way


def test_with_few_rooms_only_their_progress_counts():
    _, slow, _ = feed(heating_memory(), flat(1, demand=0.5, trend=0.1), flow=45.0, ret=42.0)
    assert correction(slow, 5.0, "single") == pytest.approx(1.0) and slow.last_adjust_reason == "rooms_slow"
    _, fine, _ = feed(heating_memory(), flat(1, demand=0.5, trend=0.6), flow=45.0, ret=42.0)
    assert fine.adjustments == ()


def test_rooms_heating_along_are_not_counted_as_calling():
    """They never call by themselves; counting them would let one real call pass for a majority."""
    rooms = flat(1)
    along = [rooms[0]] + [replace(r, target_reason="joined_comfort", target=r.target + 0.5) for r in rooms[1:]]
    assert len(load.calling_rooms(tuple(along))) == 1
    cmd, mem, _ = feed(heating_memory(), along, flow=45.0, ret=42.0)
    assert cmd.spread_rule == "blocked_few_rooms" and mem.adjustments == ()


def test_heating_along_raises_a_target_after_the_call_was_judged():
    """The controller sees the call state from before the raise: a room heating along stays "idle"."""
    from custom_components.thriftherm.engines import heat_call

    calling = replace(rr(key="badezimmer", temp=20.5, target=21.0), boiler_call="calling")
    below = replace(rr(key="kuche", temp=18.3, target=18.5), boiler_call="idle")  # 0.2 below: no call of its own
    rooms = {r.key: r for r in (calling, below)}
    joined = heat_call.join(rooms, {"badezimmer": "calling", "kuche": "idle"}, {"badezimmer": 21.0, "kuche": 18.5}, NOW)
    assert joined["kuche"].target == 19.0 and joined["kuche"].boiler_call == "idle"
    assert [r.key for r in load.calling_rooms(tuple(joined.values()))] == ["badezimmer"]


def test_rooms_that_cannot_take_heat_are_not_in_the_total():
    """Review 2026-10-11: with two windows open, two of the three usable radiators calling read as "too few"."""
    rooms = [replace(r, heating_allowed=False) for r in flat(0)[:2]] + [
        replace(rr(key=key), boiler_call="calling" if i < 2 else "idle") for i, key in enumerate(KEYS[2:])
    ]
    cmd, _ = decide(inp(rooms, mode="active"), heating_memory())
    assert (cmd.calling_rooms, cmd.rooms_total, cmd.spread_rule, cmd.load_class) == (2, 3, "active", "majority")
    nothing = [replace(r, heating_allowed=False) for r in flat(0)]
    assert len(load.able_rooms(tuple(nothing))) == 0 and load.spread_rule(0, 0, 0.5) == "blocked_few_rooms"


def test_the_share_is_a_setting():
    strict = replace(PARAMS, boiler_spread_min_room_share=1.0)
    _, mem, _ = feed_with(strict, flat(3))
    assert mem.adjustments == ()  # three of five is not all of them
    loose = replace(PARAMS, boiler_spread_min_room_share=0.2)
    _, mem, _ = feed_with(loose, flat(1))
    assert mem.last_adjust_reason == "spread_small"


def test_the_spread_collected_under_few_rooms_is_not_judged_as_the_majoritys():
    """Review 2026-10-11: 19 minutes of the bathroom's 1 K spread and three cycles of the flat lowered the curve."""
    _, mem, now = feed_with(PARAMS, flat(1), flow=45.0, ret=44.0, samples=20)
    assert mem.spread_ema == pytest.approx(1.0) and mem.spread_judging_since_ts is None
    for i in range(1, 4):
        on = inp(flat(3), mode="active", now=now + i * 60, flow=45.0, ret=39.0, burner=True)
        _, mem = decide(on, mem)
    assert mem.adjustments == () and mem.spread_ema == pytest.approx(6.0) and mem.spread_judging_since_ts == now + 60

    # the rhythm of the reviews goes on: a third room that calls on and off does not stop the learning
    # (review 2026-10-11: wiping the samples as well gave 0 steps in four hours)
    mem = heating_memory()
    for minute in range(240):
        rooms = flat(2 if minute % 15 == 0 else 3, demand=0.5, trend=0.1)  # two of five every quarter of an hour
        on = inp(rooms, mode="active", now=NOW + 600 + minute * 60, flow=45.0, ret=42.0, burner=True)
        _, mem = decide(on, mem)
    assert mem.last_adjust_reason == "rooms_slow" and len(mem.adjustments) >= 3  # the rooms' progress is still judged
    _, steady, _ = feed(heating_memory(), flat(3), flow=45.0, ret=42.0)
    assert steady.last_adjust_reason == "spread_small"  # and the spread, once enough rooms have called for a whole window


# ------------------------------------------------------------------ a curve per load class
def test_a_step_only_moves_the_load_class_it_was_taken_in():
    """Measured 2026-10-11: the bathroom alone drove the common correction to +4 K, and three rooms started from it."""
    _, mem, now = feed(heating_memory(), flat(3), flow=45.0, ret=42.0)  # the flat as a whole: a small spread, 1 K less
    assert correction(mem, 5.0, "majority") == pytest.approx(-1.0)
    assert (mem.offset_k, mem.slope_k) == (0.0, 0.0)  # the common correction is left alone
    bath = flat(1, demand=0.5, trend=0.1)  # then the bathroom alone, not getting there
    for minute in range(11, 60):
        cmd, mem = decide(inp(bath, mode="active", now=now + minute * 60, flow=45.0, ret=42.0, burner=True), mem)
    assert cmd.load_class == "single" and mem.last_adjust_reason == "rooms_slow"
    assert correction(mem, 5.0, "single") == pytest.approx(0.0)  # it started from the flat's -1 K and went up by a first step
    assert mem.lines["single"].last_step_k == 1.0  # a whole one: the flat's step down is not its step before
    assert correction(mem, 5.0, "majority") == pytest.approx(-1.0)  # which the flat does not feel
    assert set(cmd.curve_by_load) == {"single", "majority"} and cmd.curve_by_load["majority"]["correction_k"] == -1.0
    assert cmd.offset_k == 0.0  # what is shown is the correction in use


def test_a_class_without_a_value_starts_from_its_nearest_neighbour():
    assert load.line_of({}, "few", 2.0, 1.0) == LoadLine(level_k=2.0, slope_k=1.0)  # nothing known: the common correction
    lines = {"single": LoadLine(level_k=5.0, hours=9.0), "majority": LoadLine(level_k=1.0, slope_k=0.5, hours=20.0)}
    assert load.line_of(lines, "single", 9.0, 9.0) is lines["single"]
    few = load.line_of(lines, "few", 9.0, 9.0)
    # both are one step away: the one with more rooms (review 2026-10-11: starting "few" from the bathroom's value
    # was the measured problem again), and none of its hours
    assert (few.level_k, few.slope_k, few.hours) == (1.0, 0.5, 0.0)
    only = {"majority": lines["majority"]}
    assert load.line_of(only, "single", 9.0, 9.0).level_k == 1.0 and load.line_of(only, "few", 9.0, 9.0).slope_k == 0.5
    mem = BoilerMemory(offset_k=9.0, lines=lines)
    assert [correction(mem, 15.0, c) for c in (None, "single", "few", "majority")] == [9.0, 5.0, 1.0, 1.0]
    assert correction(mem, -10.0, "majority") == 1.5  # each line has its own slope


def test_operation_is_booked_to_the_class_and_gaps_are_not_operation():
    on = inp(flat(1), mode="active", flow=45.0, ret=42.0, burner=True)
    _, mem = decide(on, heating_memory())
    _, mem = decide(replace(on, now_ts=NOW + 3600.0), mem)  # an hour without a cycle: a restart, not an hour of heating
    assert "single" not in mem.lines
    _, mem = decide(replace(on, now_ts=NOW + 3660.0), mem)
    assert mem.lines["single"].hours == pytest.approx(1 / 60) and mem.lines["single"].calm_hours == pytest.approx(1 / 60)
    _, mem = decide(replace(on, now_ts=NOW + 3720.0, learning_allowed=False), mem)  # a setpoint just moved
    _, mem = decide(replace(on, now_ts=NOW + 3780.0), mem)
    assert mem.lines["single"].hours == pytest.approx(1 / 60)

    lines = load.observe({}, "few", 2.0, False, 1.0, 0.0)
    assert (lines["few"].level_k, lines["few"].hours, lines["few"].calm_hours) == (1.0, 2.0, 2.0)
    lines = load.observe(lines, "few", 0.5, True, 1.0, 0.0)  # a called room did not get there
    assert (lines["few"].hours, lines["few"].lag_hours, lines["few"].calm_hours, lines["few"].lag_share) == (2.5, 0.5, 0.0, 0.2)
    assert load.observe(lines, None, 1.0, False, 1.0, 0.0) == lines  # no room calls: no class


def test_a_value_that_was_found_only_yields_to_evidence_that_lasts():
    """Without this the learning answers every small deviation and the flow keeps going up and down."""
    fresh = load.observe({"single": LoadLine()}, "single", 2.9, False, 0.0, 0.0)["single"]
    found = load.observe({"single": fresh}, "single", 0.1, False, 0.0, 0.0)["single"]
    assert not fresh.found and found.found  # three hours without a lagging room and without a step
    assert load.observe({"single": found}, "single", 0.5, True, 0.0, 0.0)["single"].found  # a lagging room does not unfind it
    assert load.ask(fresh, 1, NOW) == (True, fresh)  # still being found: every step is taken

    allowed, line = load.ask(found, 1, NOW)  # a room starts lagging
    assert not allowed and (line.asked, line.asked_since_ts) == (1, NOW)
    assert load.ask(line, 1, NOW + 2699)[0] is False and load.ask(line, 1, NOW + 2700)[0] is True  # 45 minutes of it
    allowed, turned = load.ask(line, -1, NOW + 2000)  # the verdict turns: the wait starts over
    assert not allowed and (turned.asked, turned.asked_since_ts) == (-1, NOW + 2000)
    for minutes in (20, 40, 60, 80, 100):  # asked again at every review
        allowed, turned = load.ask(turned, -1, NOW + 2000 + minutes * 60)
        assert not allowed
    assert load.ask(turned, -1, NOW + 2000 + 7200)[0] is True  # two hours of it

    after = load.stepped(line, 1.0, 0.5, 1.0, NOW + 2700)
    assert (after.level_k, after.slope_k, after.calm_hours, after.asked, after.found) == (1.0, 0.5, 0.0, 0, False)
    assert (after.last_step_k, after.last_step_ts) == (1.0, NOW + 2700)  # only a step unfinds it


def test_the_asking_may_pause_but_not_for_long():
    """Review 2026-10-11: one good cycle in 41 minutes, or a sensor reading 0.2 and 0.1 K below target in turn
    every half hour (the bathroom on 2026-10-10), kept a found line from ever being raised."""
    found = LoadLine(found=True)
    _, line = load.ask(found, 1, NOW)
    _, line = load.ask(line, 1, NOW + 1200)  # asked again at the next review
    allowed, line = load.ask(line, 1, NOW + 1200 + 2700)  # then 45 minutes of nothing: still the same asking
    assert allowed and line.asked_since_ts == NOW
    allowed, again = load.ask(line, 1, NOW + 1200 + 2700 + 2701)  # a longer pause: it starts over
    assert not allowed and again.asked_since_ts == NOW + 1200 + 2700 + 2701


def test_only_time_in_which_the_rooms_could_have_complained_counts_towards_found():
    """Review 2026-10-11: a bathroom calling 25 minutes at a time, stuck a kelvin below target, was found after eight calls."""
    line = load.observe({}, "single", 4.0, False, 0.0, 0.0, judged=False)["single"]
    assert (line.hours, line.calm_hours, line.found) == (4.0, 0.0, False)  # released, but no verdict was possible

    stuck = [replace(r, temperature=r.target - 1.0, trend_k_per_h=0.0) if i == 0 else r for i, r in enumerate(flat(1))]
    mem = heating_memory()
    for call in range(10):  # ten calls of 25 minutes with a block in between
        start = NOW + call * 3600
        for minute in range(25):
            _, mem = decide(inp(stuck, mode="active", now=start + minute * 60, learning=True), replace(mem, adjustments=()))
        _, mem = decide(inp(flat(0), mode="active", advice="none", now=start + 1800), mem)
    assert mem.lines["single"].hours > 3.0 and not mem.lines["single"].found


def test_a_found_line_is_not_moved_by_a_short_lag_but_by_a_lasting_one():
    lines = {"single": LoadLine(level_k=3.0, hours=10.0, calm_hours=5.0, found=True)}
    bath = flat(1, demand=0.5, trend=0.1)

    def run(minutes, mem, rooms=bath):
        for minute in minutes:
            _, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60, flow=45.0, ret=42.0, burner=True), mem)
        return mem

    mem = run(range(0, 60), replace(heating_memory(start=NOW - 3600), lines=lines))
    assert mem.adjustments == () and mem.lines["single"].asked == 1  # an hour of reviews: asked for, not yet long enough
    mem = run(range(60, 100), mem)
    assert len(mem.adjustments) == 1 and mem.lines["single"].level_k > 3.0 and not mem.lines["single"].found

    mem = run(range(0, 40), replace(heating_memory(start=NOW - 3600), lines=lines))  # a short lag ...
    assert mem.lines["single"].asked == 1
    mem = run(range(40, 200), mem, flat(1, demand=0.5, trend=0.4))  # ... and the room gets going again
    assert mem.adjustments == () and mem.lines["single"].level_k == 3.0


def test_a_new_class_has_to_hold_before_it_counts():
    """Review 2026-10-11: a class changing by the minute moved the flow by 4 K every minute, each one sent."""
    assert load.settled_class(None, None, None, "single", NOW) == ("single", None, None)  # the first one counts at once
    assert load.settled_class("single", None, None, "single", NOW) == ("single", None, None)
    assert load.settled_class("single", None, None, "majority", NOW) == ("single", "majority", NOW)
    assert load.settled_class("single", "majority", NOW, "majority", NOW + 599) == ("single", "majority", NOW)
    assert load.settled_class("single", "majority", NOW, "majority", NOW + 600) == ("majority", None, None)
    assert load.settled_class("single", "majority", NOW, "single", NOW + 300) == ("single", None, None)  # it went back
    # another one does not start the clock again: what counts is how long "single" has been gone
    assert load.settled_class("single", "majority", NOW, "few", NOW + 599) == ("single", "few", NOW)
    assert load.settled_class("single", "few", NOW, "majority", NOW + 600) == ("majority", None, None)
    assert load.settled_class("single", "majority", NOW, None, NOW + 60) == ("single", None, None)  # no room calls: it stays

    lines = {"few": LoadLine(level_k=0.0, hours=5.0), "majority": LoadLine(level_k=4.0, hours=5.0)}
    mem = replace(heating_memory(), lines=lines, last_flow=40.0)
    seen = set()
    for minute in range(30):  # half an hour with a third room calling on and off
        rooms = flat(3 if minute % 2 else 2, demand=0.0)
        cmd, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60), mem)
        seen.add((cmd.load_class, cmd.offset_k))
    assert seen == {("few", 0.0)}
    for minute in range(30, 42):  # the third room stays: after ten minutes the majority's line counts
        cmd, mem = decide(inp(flat(3, demand=0.0), mode="active", now=NOW + minute * 60), mem)
    assert (cmd.load_class, cmd.offset_k) == ("majority", 4.0)
    cmd, mem = decide(inp(flat(0), mode="active", now=NOW + 43 * 60), mem)  # nobody calls, the heating still released
    assert cmd.plan == "heat" and cmd.load_class == "majority"
    cmd, mem = decide(inp(flat(0), mode="active", advice="none", now=NOW + 50 * 60), mem)
    assert cmd.plan == "block" and mem.class_in_use is None  # blocked: no class is in use


def test_switched_off_there_is_one_common_correction_as_before():
    lines = {"single": LoadLine(level_k=5.0, hours=9.0)}
    mem = replace(heating_memory(), offset_k=2.0, lines=lines)
    cmd, mem2, _ = feed_with(COMMON, flat(3), mem)
    assert cmd.curve_by_load is None and cmd.load_class == "majority"  # the class is still shown, its line is not used
    assert mem2.offset_k + mem2.slope_k * 0.4 == pytest.approx(1.0) and mem2.lines == lines  # the step went to the common one
    cmd, _ = decide(inp(flat(1), mode="active", params=COMMON), mem)
    assert cmd.offset_k == 2.0
    cmd, _ = decide(inp(flat(1), mode="active"), mem)  # and switched on again every line is where it was
    assert cmd.offset_k == 5.0


def test_the_safe_flow_limits_hold_for_every_line():
    lines = {"majority": LoadLine(level_k=10.0, slope_k=10.0, hours=30.0)}
    mem = replace(heating_memory(), lines=lines, last_flow=60.0)
    cmd, _ = decide(inp(flat(3, demand=1.0), mode="active", outdoor=-10.0), mem)
    assert cmd.offset_k == 20.0 and cmd.flow_setpoint == PARAMS.boiler_flow_max
    lines = {"majority": LoadLine(level_k=-10.0, slope_k=-10.0, hours=30.0)}
    cmd, _ = decide(inp(flat(3, demand=0.0), mode="active", outdoor=15.0), replace(heating_memory(), lines=lines))
    assert cmd.flow_setpoint == PARAMS.boiler_flow_min
    # and a step never puts a line beyond what the flow can follow
    on = inp(flat(3), mode="active", outdoor=15.0)
    at_max = {"majority": LoadLine(level_k=10.0)}
    assert learning.apply(on, BoilerMemory(lines=at_max), 1, "rooms_lagging", "majority").lines == at_max


# ------------------------------------------------------------------ storage
def test_lines_survive_a_restart_and_what_was_stored_before_is_kept():
    lines = {"single": LoadLine(level_k=3.8, slope_k=0.97, hours=2.5, lag_hours=1.7, calm_hours=0.4, found=True,
                                last_step_k=2.0, last_step_ts=NOW - 60, asked=1, asked_since_ts=NOW)}
    back = BoilerMemory.from_storage(BoilerMemory(offset_k=1.0, lines=lines).to_storage())
    assert back.lines["single"] == replace(lines["single"], asked=0, asked_since_ts=None)  # a request starts over
    assert back.offset_k == 1.0

    before = {"offset_k": 2.0, "adjustments": [1.0, 2.0], "burn_starts": [1.0], "burn_durations": [60.0]}
    old = BoilerMemory.from_storage(before)  # stored before there were classes at all
    assert old.offset_k == 2.0 and old.lines == {} and correction(old, 15.0, "single") == 2.0

    # stored by the version that booked its steps to cells, here the test installation on 2026-10-11:
    # 3.8 K of level in common, 2.83 K of it taken by the bathroom alone, the rest before there were cells
    cells = {
        "single/mild": {"hours": 2.6, "lag_hours": 1.85, "level_k": 2.83, "slope_k": 0.77, "active": False},
        "majority/mild": {"hours": 0.6, "lag_hours": 0.43, "level_k": 0.0, "slope_k": 0.0},
        "majority/cool": {"hours": 1.0, "lag_hours": 0.0, "level_k": 0.0, "slope_k": 0.0},
    }
    moved = BoilerMemory.from_storage({"offset_k": 3.8, "slope_k": 0.97, "cells": cells})
    assert (moved.offset_k, moved.slope_k) == (3.8, 0.97)  # the common correction is kept for the switch
    assert moved.lines["single"].level_k == 3.8 and moved.lines["single"].hours == 2.6  # nobody else stepped: all of it
    flat_line = moved.lines["majority"]
    assert (flat_line.level_k, flat_line.slope_k) == (pytest.approx(0.97), pytest.approx(0.2))  # without the bathroom's steps
    assert flat_line.hours == pytest.approx(1.6) and flat_line.lag_hours == pytest.approx(0.43)

    broken = BoilerMemory.from_storage({"lines": {
        "single": {"level_k": "x", "slope_k": float("nan"), "hours": -3, "calm_hours": float("inf")},
        "nonsense": {"hours": 5}, "few": 7,
    }})
    assert broken.lines == {"single": LoadLine(), "few": LoadLine()}
    assert BoilerMemory.from_storage({"lines": "x"}).lines == {}


def test_a_learning_reset_forgets_the_lines():
    mem = learning.reset(BoilerMemory(offset_k=1.0, slope_k=0.5, lines={"single": LoadLine(level_k=3.0)}, state="heat"))
    assert (mem.offset_k, mem.slope_k, mem.lines, mem.state) == (0.0, 0.0, {}, "heat")


def test_two_other_classes_taking_turns_do_not_pin_the_old_one():
    """Review 2026-10-11: "single" stayed in use for four hours while two and three rooms called in turn."""
    mem = replace(heating_memory(), class_in_use="single")
    for minute in range(12):
        rooms = flat(3 if (minute // 5) % 2 else 2, demand=0.0)  # two rooms, then three, five minutes each
        cmd, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60), mem)
    assert cmd.load_class == "few"  # ten minutes without "single": what is seen at that moment counts


def test_lines_from_cells_stay_within_the_limits_and_skip_what_is_no_cell():
    cells = {"single/mild": {"level_k": 6.0}, "majority/mild": {"level_k": 0.0, "hours": 1.0}, "few/cold": "junk"}
    moved = BoilerMemory.from_storage({"offset_k": -9.0, "cells": cells})
    assert set(moved.lines) == {"single", "majority"} and moved.lines["majority"].level_k == -10.0  # -15 K, limited
    assert moved.lines["single"].level_k == -9.0
