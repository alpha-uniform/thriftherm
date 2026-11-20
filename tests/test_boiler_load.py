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


def inp(*args, **kwargs):
    return _inp(*args, **kwargs)


def flat(calling: int, *, demand: float = 0.2, trend: float | None = None, others: str = "idle") -> list:
    """Five rooms, the first `calling` of them asking the boiler for heat."""
    return [
        replace(rr(key=key, demand=demand if i < calling else 0.0, trend=trend), boiler_call="calling" if i < calling else others)
        for i, key in enumerate(KEYS)
    ]


def seeded(load_class: str, level_k: float, **line) -> dict[str, LoadLine]:
    """A line with this value for every room in the class."""
    return {load.room_cell(load_class, key): LoadLine(level_k=level_k, **line) for key in KEYS}


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

    # four rooms: two of them run on the line of a few, three on the majority's
    lines = {**seeded("few", 1.0, hours=5.0), **seeded("majority", 3.0, hours=5.0)}
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
    for minute in range(11, 110):  # a step up waits 90 minutes after any step
        cmd, mem = decide(inp(bath, mode="active", now=now + minute * 60, flow=45.0, ret=42.0, burner=True), mem)
    assert cmd.load_class == "single" and mem.last_adjust_reason == "rooms_slow"
    # it started from its own -1 K among the flat (the same radiator) and went up by a first step
    assert correction(mem, 5.0, "single:badezimmer") == pytest.approx(0.0)
    assert mem.lines["single:badezimmer"].last_step_k == 1.0  # a whole one: the flat's step down is not its step before
    assert correction(mem, 5.0, "majority:badezimmer") == pytest.approx(-1.0)  # which the flat does not feel
    assert set(cmd.curve_by_load) == {"single:badezimmer", *(load.room_cell("majority", key) for key in KEYS[:3])}
    assert cmd.curve_by_load["majority:badezimmer"]["correction_k"] == -1.0 and cmd.flow_set_by == "badezimmer"
    assert cmd.offset_k == 0.0  # what is shown is the correction in use


def test_a_line_nothing_is_known_for_starts_from_the_common_correction():
    assert load.line_of({}, "few:kuche", 2.0, 1.0) == LoadLine(level_k=2.0, slope_k=1.0, borrowed=True)
    lines = {"majority:kuche": LoadLine(level_k=1.0, slope_k=0.5, hours=20.0)}
    assert load.line_of(lines, "majority:kuche", 9.0, 9.0) is lines["majority:kuche"]
    mem = BoilerMemory(offset_k=9.0, lines=lines)
    cells = (None, "single:kuche", "majority:kuche", "single:badezimmer")
    assert [correction(mem, 15.0, c) for c in cells] == [9.0, 1.0, 1.0, 9.0]
    assert correction(mem, -10.0, "majority:kuche") == 1.5  # each line has its own slope
    # a line of a class without a room (stored for four days in October 2026, by no release) is none
    stored = {"majority": {"level_k": 4.0}, "majority:kuche": {"level_k": 1.0}}
    assert load.lines_from_storage(stored).keys() == {"majority:kuche"}
    assert load.deciding_cell(lines, "majority", (), 9.0, 9.0, 0.0) is None  # no room has called: the common correction


def test_operation_is_booked_to_the_class_and_gaps_are_not_operation():
    on = inp(flat(1), mode="active", flow=45.0, ret=42.0, burner=True)
    _, mem = decide(on, heating_memory())
    _, mem = decide(replace(on, now_ts=NOW + 3600.0), mem)  # an hour without a cycle: a restart, not an hour of heating
    assert mem.lines == {}
    _, mem = decide(replace(on, now_ts=NOW + 3660.0), mem)
    bath = mem.lines["single:badezimmer"]  # the room's own line
    assert bath.hours == pytest.approx(1 / 60)
    assert set(mem.lines) == {"single:badezimmer"}
    _, mem = decide(replace(on, now_ts=NOW + 3720.0, learning_allowed=False), mem)  # a setpoint just moved
    _, mem = decide(replace(on, now_ts=NOW + 3780.0), mem)
    assert mem.lines["single:badezimmer"].hours == pytest.approx(1 / 60)

    lines = load.observe({}, "few", 2.0, False, 1.0, 0.0)
    assert (lines["few"].level_k, lines["few"].hours) == (1.0, 2.0)
    lines = load.observe(lines, "few", 0.5, True, 1.0, 0.0)  # a called room did not get there
    assert (lines["few"].hours, lines["few"].lag_hours, lines["few"].lag_share) == (2.5, 0.5, 0.2)
    assert load.observe(lines, None, 1.0, False, 1.0, 0.0) == lines  # no room calls: no class


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

    lines = {**seeded("few", 0.0, hours=5.0), **seeded("majority", 4.0, hours=5.0)}
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


def test_a_common_correction_learned_without_a_curve_per_room_is_where_every_room_starts():
    """Until 2026-11-13 the curve per room could be switched off, and one common correction was learned for all.
    That correction stays: a room without a value of its own borrows it, so the flow is as it was, and what the
    room learns from then on is its own."""
    mem = replace(heating_memory(), offset_k=2.0)
    cmd, _ = decide(inp(flat(1), mode="active"), mem)
    assert cmd.offset_k == 2.0 and cmd.curve_by_load == {}
    cmd, mem2, _ = feed_with(PARAMS, flat(3), mem)  # a small spread with the majority calling: a step down
    assert (mem2.offset_k, mem2.slope_k) == (2.0, 0.0)  # of the room's own line, not of the common correction
    assert correction(mem2, 5.0, "majority:badezimmer") == pytest.approx(1.0)
    assert not mem2.lines["majority:badezimmer"].borrowed and cmd.load_class == "majority"


def test_a_flow_the_demand_boost_holds_at_its_maximum_is_not_raised_further():
    """The curve's 55 °C and the boost of 8 K ask for 63 °C: the flow stands at 60, a step would move nothing."""
    room = [replace(rr(demand=1.0, temp=18.0, target=20.0, trend=0.5), boiler_call="calling")]
    cmd, mem, _ = feed_with(PARAMS, room, flow=60.0, ret=40.0, outdoor=-10.0)
    assert cmd.flow_setpoint == PARAMS.boiler_flow_max
    assert mem.adjustments == () and (mem.offset_k, mem.slope_k) == (0.0, 0.0)


def test_a_slow_room_on_a_flow_its_boost_lifts_above_the_minimum_raises_the_curve():
    """The curve's 33 °C lies below the minimum of 35, but with the boost of 4.8 K the flow sent is 38 °C."""
    params = replace(PARAMS, boiler_flow_min=35.0)
    room = [replace(rr(demand=0.6, temp=19.4, target=20.0, trend=0.05), boiler_call="calling")]
    before, _ = decide(inp(room, mode="active", params=params, outdoor=12.0), heating_memory())
    assert before.flow_setpoint == 38.0
    _, mem, _ = feed_with(params, room, flow=38.0, ret=30.0, outdoor=12.0)
    assert mem.last_adjust_reason == "rooms_slow" and len(mem.adjustments) == 1


def test_the_safe_flow_limits_hold_for_every_line():
    lines = seeded("majority", 10.0, slope_k=10.0, hours=30.0)
    mem = replace(heating_memory(), lines=lines, last_flow=60.0)
    cmd, _ = decide(inp(flat(3, demand=1.0), mode="active", outdoor=-10.0), mem)
    assert cmd.offset_k == 20.0 and cmd.flow_setpoint == PARAMS.boiler_flow_max
    lines = seeded("majority", -10.0, slope_k=-10.0, hours=30.0)
    cmd, _ = decide(inp(flat(3, demand=0.0), mode="active", outdoor=15.0), replace(heating_memory(), lines=lines))
    assert cmd.flow_setpoint == PARAMS.boiler_flow_min
    # and a step never puts a line beyond what the flow can follow
    on = inp(flat(3), mode="active", outdoor=15.0)
    at_max = {"majority:kuche": LoadLine(level_k=10.0)}
    assert learning.apply(on, BoilerMemory(lines=at_max), 1, "rooms_lagging", "majority:kuche").lines == at_max


# ------------------------------------------------------------------ storage
def test_lines_survive_a_restart_and_what_was_stored_before_is_kept():
    line = LoadLine(level_k=3.8, slope_k=0.97, hours=2.5, lag_hours=1.7, last_step_k=2.0, last_step_ts=NOW - 60)
    back = BoilerMemory.from_storage(BoilerMemory(offset_k=1.0, lines={"single:kuche": line}).to_storage())
    assert back.lines == {"single:kuche": line}
    assert back.offset_k == 1.0

    before = {"offset_k": 2.0, "adjustments": [1.0, 2.0], "burn_starts": [1.0], "burn_durations": [60.0]}
    old = BoilerMemory.from_storage(before)  # stored before there were classes at all
    assert old.offset_k == 2.0 and old.lines == {} and correction(old, 15.0, "single:kuche") == 2.0

    broken = BoilerMemory.from_storage({"lines": {
        "single:kuche": {"level_k": "x", "slope_k": float("nan"), "hours": -3, "calm_hours": float("inf")},
        "nonsense": {"hours": 5}, "few:kuche": 7,
    }})
    assert broken.lines == {"single:kuche": LoadLine(), "few:kuche": LoadLine()}
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
    behind = learning.step_room(on, BoilerMemory(lines=lines), load.calling_rooms(on.rooms), "rooms_lagging", "few:wohnzimmer")
    assert behind.key == "kuche"
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
    # a line that started from another's value and is capped keeps the cap: the value is its own from then on
    started = {key: line for key, line in lines.items() if key != "majority:badezimmer"}  # the bathroom at the common 2.5 K
    _, mem, _ = feed_with(PARAMS, flat(3), replace(heating_memory(), lines=started, offset_k=2.5))
    assert correction(mem, 5.0, "majority:badezimmer") == pytest.approx(2.0) and not mem.lines["majority:badezimmer"].borrowed
    assert correction(BoilerMemory.from_storage(mem.to_storage()), 5.0, "majority:badezimmer") == pytest.approx(2.0)


def test_a_room_without_a_line_starts_from_what_is_nearest_to_it():
    mine = {"few:kuche": LoadLine(level_k=2.0, hours=5.0), "majority:kuche": LoadLine(level_k=-1.0)}
    assert load.line_of(mine, "single:kuche", 0.0, 0.0) == LoadLine(level_k=2.0, borrowed=True)  # its value, none of its hours
    assert load.line_of(mine, "single:kuche", 0.0, 0.0).level_k == 2.0  # its own radiator in the nearest class
    assert load.line_of(mine, "single:badezimmer", 0.5, 0.0).level_k == 0.5  # nothing known: the common correction


def test_a_room_whose_sensor_has_not_reported_within_the_trends_window_says_nothing_about_the_flow():
    """Issue #7 (2026-11-12): room sensors that report on a change of about a degree only (Shelly H&T). The room's
    curve stood flat for hours and then jumped (bathroom 04:00 to 08:00, the nursery two hours into its comfort), so
    its trend read 0.0 K/h: "does not rise", and the curve went up, while nobody knew what the room did."""
    behind = replace(rr(temp=20.5, target=21.0, trend=0.0), boiler_call="calling")
    assert load.lagging((behind,)) and load.lagging((replace(behind, temperature_age_s=5400.0),))
    assert not load.lagging((replace(behind, temperature_age_s=5401.0),))


def test_a_room_lags_only_once_its_own_call_has_run_half_an_hour():
    """Review 2026-11-13: with another room keeping the boiler released, every call began as "lagging": three tenths
    below target and a trend of 0 over the 90 minutes before. The curve went up for it, and its brisk calls counted
    for nothing."""
    behind = replace(rr(temp=20.7, target=21.0, trend=0.0), boiler_call="calling")
    assert load.lagging((behind,))  # nothing known about its call: as before
    assert not load.lagging((replace(behind, calling_s=600.0),)) and load.lagging((replace(behind, calling_s=1800.0),))
    assert load.lagging((replace(behind, boiler_call="stalled", calling_s=None),))  # given up on: it is the evidence
    # and a room that lags starts the count of its brisk calls over
    lines = {"single:kuche": LoadLine(fast_calls=2, fast_call_ts=NOW)}
    assert load.observe(lines, "single:kuche", 0.1, True, 0.0, 0.0)["single:kuche"].fast_calls == 0
    assert load.observe(lines, "single:kuche", 0.1, False, 0.0, 0.0)["single:kuche"].fast_calls == 2


def test_what_a_room_learned_alone_is_not_taken_over_where_more_rooms_call():
    """Seen 2026-11-12: the bathroom's +10.8 K, learned while it called alone and the boiler cycled (its flow then
    averages far below what is set), stood in "majority:badezimmer" too, and would have set the flow for the whole flat.
    The other way round holds: what a radiator needed with the boiler running through, it needs alone at least."""
    alone = {"single:badezimmer": LoadLine(level_k=9.66, slope_k=3.27, last_step_k=0.5, last_step_ts=NOW)}
    assert load.line_of(alone, "few:badezimmer", 0.5, 0.0).level_k == 0.5
    assert load.line_of(alone, "majority:badezimmer", 0.5, 0.0).level_k == 0.5
    among = {"majority:badezimmer": LoadLine(level_k=2.0), "few:badezimmer": LoadLine(level_k=3.0)}
    assert load.line_of(among, "single:badezimmer", 0.0, 0.0).level_k == 3.0  # the nearest class with more rooms
    assert load.line_of({"majority:badezimmer": LoadLine(level_k=2.0)}, "single:badezimmer", 0.0, 0.0).level_k == 2.0


def test_a_line_without_a_value_of_its_own_follows_where_it_starts_from_until_it_takes_a_step():
    """Seen 2026-11-12: booked once, a line kept the value it had started from for good ("majority:badezimmer" at
    9.64/3.22 after 0.1 h and no step); neither a step nor a reset of the line it came from reached it any more."""
    lines = {"majority:kuche": LoadLine(level_k=2.0, last_step_k=1.0, last_step_ts=NOW)}
    lines = load.observe(lines, "single:kuche", 1.0, False, 0.0, 0.0)
    assert lines["single:kuche"].hours == 1.0 and lines["single:kuche"].borrowed
    assert load.line_of(lines, "single:kuche", 0.0, 0.0).level_k == 2.0
    moved = {**lines, "majority:kuche": load.stepped(lines["majority:kuche"], 3.0, 0.5, 1.0, NOW)}
    follows = load.line_of(moved, "single:kuche", 0.0, 0.0)
    assert (follows.level_k, follows.slope_k, follows.hours, follows.borrowed) == (3.0, 0.5, 1.0, True)  # its own hours
    gone = {key: line for key, line in lines.items() if key != "majority:kuche"}
    assert load.line_of(gone, "single:kuche", 0.5, 0.0).level_k == 0.5
    assert load.summary(moved, 0.0, 0.0, 0.0)["single:kuche"]["level_k"] == 3.0  # what is shown is what is used
    # a line that only booked hours is no value for others to start from
    assert load.line_of(lines, "single:wohnzimmer", 0.5, 0.0).level_k == 0.5
    assert load.line_of({"few": lines["single:kuche"]}, "single", 0.5, 0.0).level_k == 0.5

    own = load.stepped(follows, 3.5, 0.5, 0.5, NOW)  # its first step: from then on the value is its own
    assert not own.borrowed
    kept = {**moved, "single:kuche": own, "majority:kuche": LoadLine(level_k=-4.0)}
    assert load.line_of(kept, "single:kuche", 0.0, 0.0).level_k == 3.5
    back = BoilerMemory.from_storage(BoilerMemory(lines=moved | {"few:kuche": own}).to_storage()).lines
    assert back["single:kuche"].borrowed and not back["few:kuche"].borrowed and not back["majority:kuche"].borrowed


def test_a_stored_line_that_never_took_a_step_gives_up_the_value_it_was_stored_with():
    """The test installation's store of 2026-11-12, cut down: the lines written before a line knew whether its value was its own."""
    def stored(level, slope, hours, stamp, step=0.5):
        return {"level_k": level, "slope_k": slope, "hours": hours, "lag_hours": 0.0, "calm_hours": 0.0, "found": False,
                "last_step_k": step if stamp else 0, "last_step_ts": stamp}

    mem = BoilerMemory.from_storage({"lines": {
        "single:badezimmer": stored(9.657, 3.273, 93.26, 1793587683.6),
        "few:badezimmer": stored(9.644, 3.217, 4.33, 1793025794.2),
        "majority:badezimmer": stored(9.644, 3.217, 0.1, None),  # the value of "few", and never a step
        "majority:kuche": stored(0.0, 0.0, 0.1, None),
    }})
    assert correction(mem, 6.0, "majority:badezimmer") == 0.0 and mem.lines["majority:badezimmer"].hours == 0.1
    assert not mem.lines["majority:kuche"].borrowed
    # Any other line without a step keeps what it was stored with: a step down of the line that set the flow
    # lowered the other rooms' lines to it without a step of their own, and those values were learned.
    capped = BoilerMemory.from_storage({"offset_k": 4.0, "lines": {
        "majority:kuche": stored(-1.0, 0.0, 30.0, 1793587683.6, step=-1.0),
        "majority:wohnzimmer": stored(-1.0, 0.0, 30.0, None),
        "few:wohnzimmer": stored(-1.0, 0.0, 2.0, None),
    }})
    assert [correction(capped, 6.0, f"{c}:wohnzimmer") for c in ("majority", "few", "single")] == [-1.0, -1.0, -1.0]
    assert correction(mem, 6.0, "single:badezimmer") == pytest.approx(9.657 + 3.273 * 0.36)
    assert correction(mem, 6.0, "few:badezimmer") == pytest.approx(9.644 + 3.217 * 0.36)


def test_the_lines_of_the_rooms_are_stored_and_the_rooms_of_a_call_are_not():
    lines = {"few:kuche": LoadLine(level_k=2.0, hours=3.0), "majority:wohnzimmer": LoadLine(level_k=-1.0)}
    stored = BoilerMemory(lines=lines, cell_rooms=("kuche",)).to_storage()
    assert "cell_rooms" not in stored
    back = BoilerMemory.from_storage({**stored, "lines": {**stored["lines"], "nonsense:kuche": {"hours": 1.0}}})
    assert back.lines == lines and back.cell_rooms == ()
    assert load.summary(lines, 0.0, 0.0, 0.0).keys() == {"few:kuche", "majority:wohnzimmer"}


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
def test_only_the_value_is_taken_from_another_line():
    lines = {"majority:kuche": LoadLine(level_k=4.0, hours=30.0)}
    start = load.line_of(lines, "few:kuche", 0.0, 0.0)
    assert (start.level_k, start.hours) == (4.0, 0.0)


def test_lines_stored_with_the_found_value_of_before_load():
    """The "found" value, its calm hours and the asking went on 2026-11-15 (they never acted in weeks of use)."""
    stored = {"lines": {"single:kuche": {"level_k": 2.0, "hours": 3.0, "calm_hours": 4.0, "found": True}}}
    assert BoilerMemory.from_storage(stored).lines == {"single:kuche": LoadLine(level_k=2.0, hours=3.0)}


def test_a_room_catching_up_with_another_line_stays_within_the_bound():
    """At a deciding line of +10 K the kitchen went to +11 K, and its next step was booked as one down."""
    lines = {"few:wohnzimmer": LoadLine(level_k=10.0), "few:kuche": LoadLine(level_k=0.0)}
    on = inp(calling("kuche", "wohnzimmer", temperature=19.0, trend_k_per_h=0.0, demand=0.5), mode="active", outdoor=12.0)
    after = learning.apply(on, BoilerMemory(lines=lines), 1, "rooms_lagging", "few:kuche", "few:wohnzimmer")
    assert correction(after, 12.0, "few:kuche") <= 10.0 + 1e-9
    assert all(step >= 0 for step in after.adjustment_steps)


def test_while_a_room_with_a_limited_preheat_wants_heat_the_curve_learns_nothing(monkeypatch):
    """Cold at the start of its block by choice, and brisk in it: either way no sign of the flow (issue #6). Left out of
    the rules upward only, it pushed its line down morning after morning (review 2026-11-03)."""
    seen = []
    learn = learning.learn
    monkeypatch.setattr(learning, "learn", lambda inp, *a, **k: seen.append(inp.learning_allowed) or learn(inp, *a, **k))
    room = rr(temp=19.0, target=21.0)
    decide(_inp([room], mode="active", flow=45.0, ret=40.0, burner=True), heating_memory())
    assert seen == [True]
    decide(_inp([replace(room, short_preheat=True)], mode="active", flow=45.0, ret=40.0, burner=True), heating_memory())
    assert seen == [True, False]


def test_a_direct_room_whose_valve_cannot_be_open_does_not_lag():
    """Review 2026-11-15 item 4: a valve in its exercise or on a flat battery is no evidence that the flow is too low
    (an off or silent valve already takes the room's heating away)."""
    behind = replace(rr(temp=20.6, target=21.0, trend=0.0), boiler_call="calling")
    assert load.lagging((behind,))
    for why in ("exercise", "battery"):
        assert not load.lagging((replace(behind, valve_unable=why),))
