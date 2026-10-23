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
from .test_boiler_control import NOW, corr, feed, heating_memory, rr
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
    assert load.fair_share_from(5, 0.5) == 3 and load.fair_share_from(4, 0.5) == 2 and load.fair_share_from(1, 0.5) == 1
    assert load.fair_share_from(5, 0.0) == 1 and load.fair_share_from(0, 0.5) == 1  # never fewer than one room
    assert load.spread_rule(2, 5, 0.5) == "blocked_few_rooms" and load.spread_rule(3, 5, 0.5) == "active"
    assert load.spread_rule(2, 4, 0.5) == "active" and load.spread_rule(1, 2, 0.5) == "active"  # half is a fair share
    assert [load.load_class(n, 5, 0.5) for n in range(6)] == [None, "single", "few", "majority", "majority", "majority"]


def test_the_majority_is_more_than_half_of_the_rooms():
    """Seen 2026-10-13: half of four rooms was the majority already, so two rooms and four ran in one class."""
    assert [load.load_class(n, 4, 0.5) for n in range(5)] == [None, "single", "few", "majority", "majority"]
    assert [load.load_class(n, 3, 0.5) for n in range(4)] == [None, "single", "majority", "majority"]
    assert [load.load_class(n, 2, 0.5) for n in range(3)] == [None, "single", "majority"]
    assert load.load_class(1, 1, 0.5) == "majority"  # the only room
    assert load.majority_from(4, 1.0) == 4 and load.majority_from(0, 0.5) == 1  # all of them at most, one at least
    assert load.majority_from(5, 0.2) == 2 and load.majority_from(5, 0.0) == 1

    # four rooms: two of them run on the line of a few, three on the majority's, which was stored before
    lines = load.lines_from_storage({"few": {"level_k": 1.0, "hours": 5.0}, "majority": {"level_k": 3.0, "hours": 5.0}})
    cmd, _ = decide(inp(flat(2)[:4], mode="active"), replace(heating_memory(), lines=lines))
    assert (cmd.calling_rooms, cmd.rooms_total, cmd.load_class, cmd.offset_k) == (2, 4, "few", 1.0)
    assert cmd.spread_rule == "active"  # the spread still judges with half of the rooms calling
    cmd, _ = decide(inp(flat(3)[:4], mode="active"), replace(heating_memory(), lines=lines))
    assert (cmd.load_class, cmd.offset_k) == ("majority", 3.0)


def test_only_the_bathroom_heating_with_a_small_spread_does_not_lower_the_curve():
    """One open radiator returns the water almost as warm as it went out; that used to read as "flow too hot"."""
    cmd, mem, _ = feed(heating_memory(), flat(1), flow=45.0, ret=42.0)
    assert correction(mem, 5.0, "single") == 0.0 and mem.adjustments == ()
    assert cmd.calling_rooms == 1 and cmd.rooms_total == 5 and cmd.spread_rule == "blocked_few_rooms"
    assert cmd.load_class == "single"


def test_the_majority_heating_with_a_small_spread_lowers_the_curve_as_before():
    cmd, mem, _ = feed(heating_memory(), flat(3), flow=45.0, ret=42.0)
    # the line that set the flow went down, and no room that called keeps a higher one
    cells = [load.room_cell("majority", key) for key in KEYS[:3]]
    assert [correction(mem, 5.0, c) for c in cells] == pytest.approx([-1.0] * 3) and mem.last_adjust_reason == "spread_small"
    assert cmd.spread_rule == "active" and cmd.load_class == "majority"


def test_the_majority_heating_with_a_large_spread_and_demand_raises_the_curve():
    _, mem, _ = feed(heating_memory(), flat(3, demand=0.5), flow=50.0, ret=32.0)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "spread_large"
    _, few, _ = feed(heating_memory(), flat(2, demand=0.5), flow=50.0, ret=32.0)
    assert few.adjustments == ()  # with two of five the spread says nothing either way


def test_with_few_rooms_only_their_progress_counts():
    _, slow, _ = feed(heating_memory(), flat(1, demand=0.5, trend=0.1), flow=45.0, ret=42.0)
    assert correction(slow, 5.0, "single:badezimmer") == pytest.approx(1.0) and slow.last_adjust_reason == "rooms_slow"
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
    assert correction(mem, 5.0, "majority:badezimmer") == pytest.approx(-1.0)
    assert (mem.offset_k, mem.slope_k) == (0.0, 0.0)  # the common correction is left alone
    bath = flat(1, demand=0.5, trend=0.1)  # then the bathroom alone, not getting there
    for minute in range(11, 60):
        cmd, mem = decide(inp(bath, mode="active", now=now + minute * 60, flow=45.0, ret=42.0, burner=True), mem)
    assert cmd.load_class == "single" and mem.last_adjust_reason == "rooms_slow"
    # it started from its own -1 K among the flat (the same radiator) and went up by a first step
    assert correction(mem, 5.0, "single:badezimmer") == pytest.approx(0.0)
    assert mem.lines["single:badezimmer"].last_step_k == 1.0  # a whole one: the flat's step down is not its step before
    assert correction(mem, 5.0, "majority:badezimmer") == pytest.approx(-1.0)  # which the flat does not feel
    assert set(cmd.curve_by_load) == {"single:badezimmer", *(load.room_cell("majority", key) for key in KEYS[:3])}
    assert cmd.curve_by_load["majority:badezimmer"]["correction_k"] == -1.0 and cmd.flow_set_by == "badezimmer"
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
    assert mem.lines == {}
    _, mem = decide(replace(on, now_ts=NOW + 3660.0), mem)
    bath = mem.lines["single:badezimmer"]  # the room's own line
    assert bath.hours == pytest.approx(1 / 60) and bath.calm_hours == pytest.approx(1 / 60)
    assert set(mem.lines) == {"single:badezimmer"}
    _, mem = decide(replace(on, now_ts=NOW + 3720.0, learning_allowed=False), mem)  # a setpoint just moved
    _, mem = decide(replace(on, now_ts=NOW + 3780.0), mem)
    assert mem.lines["single:badezimmer"].hours == pytest.approx(1 / 60)

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
    assert mem.lines["single:badezimmer"].hours > 3.0 and not mem.lines["single:badezimmer"].found


def test_a_found_line_is_not_moved_by_a_short_lag_but_by_a_lasting_one():
    lines = {"single:badezimmer": LoadLine(level_k=3.0, hours=10.0, calm_hours=5.0, found=True)}
    bath = flat(1, demand=0.5, trend=0.1)

    def run(minutes, mem, rooms=bath):
        for minute in minutes:
            _, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60, flow=45.0, ret=42.0, burner=True), mem)
        return mem

    mem = run(range(0, 60), replace(heating_memory(start=NOW - 3600), lines=lines))
    assert mem.adjustments == () and mem.lines["single:badezimmer"].asked == 1  # an hour of reviews: not yet long enough
    mem = run(range(60, 100), mem)
    after = mem.lines["single:badezimmer"]
    assert len(mem.adjustments) == 1 and after.level_k > 3.0 and not after.found

    mem = run(range(0, 40), replace(heating_memory(start=NOW - 3600), lines=lines))  # a short lag ...
    assert mem.lines["single:badezimmer"].asked == 1
    mem = run(range(40, 200), mem, flat(1, demand=0.5, trend=0.4))  # ... and the room gets going again
    assert mem.adjustments == () and mem.lines["single:badezimmer"].level_k == 3.0


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


def test_switched_off_a_flow_the_demand_boost_holds_at_its_maximum_is_not_raised_further():
    """The curve's 55 °C and the boost of 8 K ask for 63 °C: the flow stands at 60, a step would move nothing."""
    room = [replace(rr(demand=1.0, temp=18.0, target=20.0, trend=0.5), boiler_call="calling")]
    cmd, mem, _ = feed_with(COMMON, room, flow=60.0, ret=40.0, outdoor=-10.0)
    assert cmd.flow_setpoint == COMMON.boiler_flow_max
    assert mem.adjustments == () and (mem.offset_k, mem.slope_k) == (0.0, 0.0)


def test_switched_off_a_slow_room_on_a_flow_its_boost_lifts_above_the_minimum_raises_the_curve():
    """The curve's 33 °C lies below the minimum of 35, but with the boost of 4.8 K the flow sent is 38 °C."""
    params = replace(COMMON, boiler_flow_min=35.0)
    room = [replace(rr(demand=0.6, temp=19.4, target=20.0, trend=0.05), boiler_call="calling")]
    before, _ = decide(inp(room, mode="active", params=params, outdoor=12.0), heating_memory())
    assert before.flow_setpoint == 38.0
    _, mem, _ = feed_with(params, room, flow=38.0, ret=30.0, outdoor=12.0)
    assert mem.last_adjust_reason == "rooms_slow" and len(mem.adjustments) == 1


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

    # "cells" were stored for nine hours on 2026-10-11, between two commits and by no release: they are ignored
    cells = {"single/mild": {"hours": 2.6, "lag_hours": 1.85, "level_k": 2.83, "slope_k": 0.77}}
    unreleased = BoilerMemory.from_storage({"offset_k": 3.8, "slope_k": 0.97, "cells": cells})
    assert (unreleased.offset_k, unreleased.slope_k, unreleased.lines) == (3.8, 0.97, {})

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


# ------------------------------------------------------------------ review 2026-10-13
def test_a_room_exactly_two_tenths_below_target_lags():
    """Sensors report tenths, and 21.0 - 20.8 is 0.1999999999999993."""
    for target in (15.0, 18.5, 21.0, 25.0):
        room = replace(rr(temp=round(target - 0.2, 1), target=target, trend=0.0), boiler_call="calling")
        assert load.lagging((room,)), target
    assert not load.lagging((replace(rr(temp=20.9, target=21.0, trend=0.0), boiler_call="calling"),))


def test_a_found_line_goes_on_asking_while_another_guard_stops_its_step():
    """Granted by its hold but stopped by the cooldown of another class's step, the line forgot that it was asked;
    the asking lapsed and started over, and the step came at minute 111 instead of 85."""
    mem = BoilerMemory(lines={"single": LoadLine(level_k=2.0, found=True)})
    on = inp(flat(1), mode="active")
    minute = 0
    while mem.lines["single"].found and minute < 240:
        if minute == 40:  # another class takes a step: 45 minutes without a step up for any class
            mem = replace(mem, adjustments=(NOW + 40 * 60,), adjustment_steps=(1.0,))
        if not 21 <= minute < 50:  # ... and is in use from minute 21 to 50, when nothing asks this line
            mem = learning.apply(replace(on, now_ts=NOW + minute * 60), mem, 1, "rooms_lagging", "single")
        minute += 1
    assert minute - 1 == 85 and mem.lines["single"].last_step_ts == NOW + 85 * 60

    cooling = BoilerMemory(adjustments=(NOW - 60,), adjustment_steps=(1.0,))
    assert learning.apply(on, cooling, 1, "rooms_lagging", "single") is cooling  # a class without a line gets none this way



# ---- what counts as a room that does not get there (2026-10-14)
def test_a_room_lags_only_while_its_thermostat_heats_and_a_wish_by_hand_only_once_it_holds():
    """A household that heats by hand only must still teach the curve; a heat-up to a wish must not."""
    behind = replace(rr(temp=20.6, target=21.0, trend=0.0), boiler_call="calling")
    assert load.lagging((behind,))
    assert load.lagging((replace(behind, thermostat_heating=True),))
    # its valve is shut although the room is below target: a hotter flow would only shut it sooner
    assert not load.lagging((replace(behind, thermostat_heating=False),))

    by_hand = replace(behind, target_reason="override")
    assert load.lagging((by_hand,))  # 0.4 K below a wish by hand and not rising: holding
    assert load.lagging((replace(by_hand, temperature=20.0),))  # 1.0 K: still holding
    assert not load.lagging((replace(by_hand, temperature=19.9),))  # 1.1 K: heating up to it
    assert not load.lagging((replace(behind, target_reason="schedule_comfort+boost", boost_active=True),))
    assert load.lagging((replace(behind, temperature=18.0),))  # a planned target counts at any distance
    assert load.lagging_rooms((by_hand, replace(behind, key="kuche", thermostat_heating=False))) == [by_hand]


# ---- a line per room (2026-10-14)
def calling(*keys: str, **room) -> list:
    """The five rooms, those named calling; `room` changes the kitchen."""
    rooms = [
        replace(rr(key=key, demand=0.0, temp=19.8, trend=0.4), boiler_call="calling" if key in keys else "idle") for key in KEYS
    ]
    return [replace(r, **room) if r.key == "kuche" else r for r in rooms]


def test_of_the_rooms_that_want_heat_the_one_that_needs_most_sets_the_flow():
    """Seen 2026-10-13: the small kitchen stopped rising at a flow the living room went on with."""
    lines = {"few:kuche": LoadLine(level_k=3.0, hours=5.0), "few:wohnzimmer": LoadLine(level_k=1.0, hours=5.0)}
    cmd, mem = decide(inp(calling("kuche", "wohnzimmer"), mode="active"), replace(heating_memory(), lines=lines))
    assert (cmd.load_class, cmd.offset_k, cmd.flow_set_by) == ("few", 3.0, "kuche")
    assert mem.cell_rooms == ("kuche", "wohnzimmer")
    cmd, _ = decide(inp(calling("badezimmer", "wohnzimmer"), mode="active"), replace(heating_memory(), lines=lines))
    assert (cmd.offset_k, cmd.flow_set_by) == (1.0, "wohnzimmer")  # the bathroom without a line starts from the common 0
    # a room the stall rule gave up on does not call, but its valve is open: the flow is to reach it as well
    stalled = calling("wohnzimmer", boiler_call="stalled")
    cmd, mem = decide(inp(stalled, mode="active"), replace(heating_memory(), lines=lines))
    assert (cmd.load_class, cmd.calling_rooms, cmd.offset_k, cmd.flow_set_by) == ("single", 1, 3.0, "kuche")
    assert mem.cell_rooms == ("kuche", "wohnzimmer")  # the single room's lines start from the same rooms' among a few


def test_a_room_that_does_not_get_there_raises_its_own_line_from_the_flow_that_was_sent():
    lines = {"few:wohnzimmer": LoadLine(level_k=3.0, hours=5.0), "few:kuche": LoadLine(level_k=0.0, hours=5.0)}
    rooms = calling("kuche", "wohnzimmer", temperature=19.0, trend_k_per_h=0.0, demand=0.5)
    on = inp(rooms, mode="active")
    assert learning.step_cell(on, load.calling_rooms(on.rooms), 1, "rooms_lagging", "few:wohnzimmer") == "few:kuche"
    assert learning.step_cell(on, load.calling_rooms(on.rooms), -1, "spread_small", "few:wohnzimmer") == "few:wohnzimmer"
    after = learning.apply(on, BoilerMemory(lines=lines), 1, "rooms_lagging", "few:kuche", "few:wohnzimmer")
    # the kitchen lagged at the living room's +3 K: one step from there, not from its own 0
    assert correction(after, 5.0, "few:kuche") == pytest.approx(4.0) and correction(after, 5.0, "few:wohnzimmer") == 3.0
    # what each asked for on top of its line counts: the kitchen's 2 K boost and its line made 2, the
    # flow was the living room's 3; one step more is 4 with the boost, a line of 2
    boosted = learning.apply(on, BoilerMemory(lines=lines), 1, "rooms_lagging", "few:kuche", "few:wohnzimmer", {"kuche": 2.0})
    assert correction(boosted, 5.0, "few:kuche") == pytest.approx(2.0)

    # in operation the kitchen's demand boost (4 K) sets the flow already: its own line goes up one
    # step, and so does the flow
    before, _ = decide(inp(rooms, mode="active"), replace(heating_memory(), lines=lines))
    assert before.flow_set_by == "kuche" and before.flow_setpoint == pytest.approx(before.curve_flow + 4.0, abs=0.5)
    _, mem, _ = feed_with(PARAMS, rooms, replace(heating_memory(), lines=lines))
    cmd, _ = decide(inp(rooms, mode="active", now=NOW + 3600), mem)
    assert mem.last_adjust_reason == "rooms_slow" and cmd.flow_set_by == "kuche" and cmd.offset_k == pytest.approx(1.0)
    assert cmd.flow_setpoint == pytest.approx(before.flow_setpoint + 1.0, abs=0.5)
    assert mem.lines["few:wohnzimmer"].level_k == 3.0  # the living room's own line is left alone


def test_a_step_down_leaves_no_room_that_wants_heat_above_it():
    """Else the next of them sets the flow at once, and the flow never comes down."""
    lines = {
        "majority:badezimmer": LoadLine(level_k=2.5, hours=5.0),
        "majority:schlafzimmer": LoadLine(level_k=1.0, hours=5.0),
        "majority:wohnzimmer": LoadLine(level_k=3.0, hours=5.0),
        "majority:kuche": LoadLine(level_k=6.0, hours=5.0),  # not calling: not asked
    }
    _, mem, _ = feed_with(PARAMS, flat(3), replace(heating_memory(), lines=lines))
    assert mem.last_adjust_reason == "spread_small"
    assert [correction(mem, 5.0, f"majority:{key}") for key in KEYS[:4]] == pytest.approx([2.0, 1.0, 2.0, 6.0])
    assert mem.lines["majority:badezimmer"].hours > 5.0  # capped, not stepped: its bookkeeping goes on


def test_a_room_without_a_line_starts_from_what_is_nearest_to_it():
    lines = {"majority": LoadLine(level_k=4.0, hours=5.0), "single": LoadLine(level_k=6.0), "few:kuche": LoadLine(level_k=2.0)}
    # a class's line learned before there were rooms: the update keeps the flow
    assert load.line_of(lines, "majority:kuche", 0.0, 0.0) == LoadLine(level_k=4.0)  # its value, none of its hours
    assert load.line_of(lines, "single:kuche", 0.0, 0.0).level_k == 6.0
    assert load.line_of(lines, "few:wohnzimmer", 0.0, 0.0).level_k == 4.0  # no line for "few": the nearest class's
    mine = {"few:kuche": LoadLine(level_k=2.0), "majority:kuche": LoadLine(level_k=-1.0)}
    assert load.line_of(mine, "single:kuche", 0.0, 0.0).level_k == 2.0  # its own radiator in the nearest class
    assert load.line_of(mine, "single:badezimmer", 0.5, 0.0).level_k == 0.5  # nothing known: the common correction


def test_the_lines_of_the_rooms_are_stored_and_the_rooms_of_a_call_are_not():
    lines = {"few:kuche": LoadLine(level_k=2.0, hours=3.0), "majority:wohnzimmer": LoadLine(level_k=-1.0)}
    stored = BoilerMemory(lines=lines, cell_rooms=("kuche",)).to_storage()
    assert "cell_rooms" not in stored
    back = BoilerMemory.from_storage({**stored, "lines": {**stored["lines"], "nonsense:kuche": {"hours": 1.0}}})
    assert back.lines == lines and back.cell_rooms == ()
    assert load.summary(lines, 0.0).keys() == {"few:kuche", "majority:wohnzimmer"}


def test_each_room_books_its_own_operation_with_whether_it_got_there():
    rooms = calling("kuche", "wohnzimmer", temperature=19.0, trend_k_per_h=0.0, demand=0.5)
    mem = heating_memory(start=NOW - 3600)
    for minute in range(3):
        _, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60), mem)
    kitchen, living = mem.lines["few:kuche"], mem.lines["few:wohnzimmer"]
    assert kitchen.hours == living.hours == pytest.approx(2 / 60)
    assert (kitchen.lag_hours, living.lag_hours) == (pytest.approx(2 / 60), 0.0)
    assert set(mem.lines) == {"few:kuche", "few:wohnzimmer"}  # the rooms that did not call book nothing


# ---- review 2026-10-14
def test_a_room_whose_progress_says_nothing_is_not_found_by_calm_time():
    """A wish by hand 2 K off and a valve Better Thermostat keeps shut booked calm hours and were found."""
    by_hand = [replace(r, target_reason="override", temperature=r.target - 2.0, trend_k_per_h=0.0) if r.key == "badezimmer" else r
               for r in flat(1)]
    shut = [replace(r, temperature=r.target - 0.5, trend_k_per_h=0.0, thermostat_heating=False) if r.key == "badezimmer" else r
            for r in flat(1)]
    for rooms in (by_hand, shut):
        mem = heating_memory(start=NOW - 3600)
        for minute in range(200):
            _, mem = decide(inp(rooms, mode="active", now=NOW + minute * 60, learning=True), replace(mem, adjustments=()))
        bath = mem.lines["single:badezimmer"]
        assert bath.hours > 3.0 and bath.calm_hours == 0.0 and not bath.found


def test_a_class_line_found_before_there_were_rooms_stays_found_for_its_rooms():
    lines = {"majority": LoadLine(level_k=4.0, hours=30.0, calm_hours=5.0, found=True)}
    seeded = load.line_of(lines, "majority:kuche", 0.0, 0.0)
    assert (seeded.level_k, seeded.found, seeded.calm_hours, seeded.hours) == (4.0, True, 5.0, 0.0)
    assert not load.line_of({"few:kuche": LoadLine(level_k=1.0, found=True)}, "single:kuche", 0.0, 0.0).found


def test_a_room_catching_up_with_another_line_stays_within_the_bound():
    """At a deciding line of +10 K the kitchen went to +11 K, and its next step was booked as one down."""
    lines = {"few:wohnzimmer": LoadLine(level_k=10.0), "few:kuche": LoadLine(level_k=0.0)}
    on = inp(calling("kuche", "wohnzimmer", temperature=19.0, trend_k_per_h=0.0, demand=0.5), mode="active", outdoor=12.0)
    after = learning.apply(on, BoilerMemory(lines=lines), 1, "rooms_lagging", "few:kuche", "few:wohnzimmer")
    assert correction(after, 12.0, "few:kuche") <= 10.0 + 1e-9
    assert all(step >= 0 for step in after.adjustment_steps)
