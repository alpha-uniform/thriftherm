"""How far a room's valve opens on another room's flow is learned for each room (engines/flow_throttle.py, 0.8.2).

The share of its radiator's output a room needs (valve_plan.flow_share) is worked out from the flow the room would ask
for itself. A room that hardly ever sets the flow never has that line learned, and an opening is no share of the output:
so what the valve gets is corrected by what came of it.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.thriftherm.engines import flow_throttle as ft
from custom_components.thriftherm.engines.flow_throttle import Throttle

NOW = 2_000_000.0
MIN = 60.0


def step(t: Throttle, minute: float, *, calling=True, limited=False, behind=False, target=21.0, room_c=20.7,
         disturbed=False) -> Throttle:
    return ft.follow(t, calling=calling, limited=limited, behind=behind, target=target, room_c=room_c,
                     now_ts=NOW + minute * MIN, disturbed=disturbed)


def test_a_room_that_falls_behind_its_limited_valve_gets_more_the_next_time():
    t = step(Throttle(), 0, limited=True)
    t = step(t, 10, limited=True)
    assert t.adjust_pct == 0.0
    t = step(t, 16, behind=True)  # opened fully again: it rose too slowly
    assert t.adjust_pct == 10.0
    t = step(t, 17, behind=True)
    t = step(t, 30, limited=True)
    t = step(t, 40, behind=True)
    assert t.adjust_pct == 10.0  # once a call
    t = step(t, 50, calling=False, room_c=21.0)
    t = step(t, 90, limited=True)
    t = step(t, 100, behind=True)
    assert t.adjust_pct == 20.0  # the next call


def test_falling_behind_long_after_the_limit_or_after_a_new_target_is_not_the_limits_doing():
    t = step(Throttle(), 0, limited=True)
    assert step(t, 45, behind=True).adjust_pct == 0.0  # fully open for more than half an hour since
    assert step(t, 5, behind=True, target=22.0).adjust_pct == 0.0  # the target went up: of course it is behind
    assert step(Throttle(), 5, behind=True).adjust_pct == 0.0  # never limited


def test_a_room_overshooting_after_a_call_that_ended_limited_gets_less_the_next_time():
    t = step(Throttle(), 0, limited=True)
    t = step(t, 20, limited=True, room_c=20.9)
    t = step(t, 21, calling=False, room_c=21.0)
    assert t.adjust_pct == 0.0
    t = step(t, 30, calling=False, room_c=21.2)
    assert t.adjust_pct == 0.0  # 0.2 above: the radiator's rest
    t = step(t, 35, calling=False, room_c=21.3)
    assert t.adjust_pct == -10.0
    t = step(t, 40, calling=False, room_c=21.5)
    assert t.adjust_pct == -10.0  # once


def test_no_overshoot_is_counted_late_while_heating_along_or_after_a_call_that_ended_fully_open():
    ended = step(step(Throttle(), 0, limited=True), 1, calling=False, room_c=21.0)
    assert step(ended, 50, calling=False, room_c=21.4).adjust_pct == 0.0  # the sun, three quarters of an hour on
    joined = step(ended, 5, calling=False, room_c=21.1, disturbed=True)  # heats along to half a degree more
    assert step(joined, 10, calling=False, room_c=21.4).adjust_pct == 0.0
    open_end = step(step(step(Throttle(), 0, limited=True), 10), 20, calling=False, room_c=21.0)
    assert step(open_end, 25, calling=False, room_c=21.4).adjust_pct == 0.0
    fresh = step(ended, 5, room_c=20.7)  # it calls again: the watch is over
    assert step(fresh, 6, calling=False, room_c=21.4).adjust_pct == 0.0


def test_what_was_learned_stays_within_bounds_is_stored_and_added_to_the_share():
    t = Throttle(adjust_pct=ft.ADJUST_MAX_PCT)
    t = step(step(t, 0, limited=True), 5, behind=True)
    assert t.adjust_pct == ft.ADJUST_MAX_PCT
    low = Throttle(adjust_pct=ft.ADJUST_MIN_PCT)
    low = step(step(step(low, 0, limited=True), 1, calling=False, room_c=21.0), 5, calling=False, room_c=21.4)
    assert low.adjust_pct == ft.ADJUST_MIN_PCT
    assert Throttle.from_storage(replace(t, limited_ts=NOW).to_storage()) == Throttle(adjust_pct=ft.ADJUST_MAX_PCT)
    assert Throttle.from_storage("x") == Throttle() and Throttle.from_storage({"adjust_pct": 500}) == Throttle()
    assert ft.adjusted(0.64, Throttle(adjust_pct=20.0)) == 0.84
    assert ft.adjusted(0.2, Throttle(adjust_pct=-30.0)) == 0.0 and ft.adjusted(0.64, None) == 0.64
