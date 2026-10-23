"""How fast a room heats up: an extra on the boiler's flow while a room is far below its target.

Seen at a user's on 2026-10-14: the living room raised from 20.5 to 23.5 °C, the flow stayed at
45 °C (the curve and the demand boost of `boiler_control`), the boiler cycled with the return at
44 °C, and two hours later the room was 1.7 K up. Every radiator needs another flow to heat its
room at a given speed, and only the household can say how fast that has to be: some rather wait
three hours and burn less gas, others want the room warm within the hour. That is the setting
`boiler_heat_up_rate_k_h`; 0 leaves the flow as it was.

While a room heats up, its rise is measured from where it stood at the start or at the last
verdict: once the boiler has had SETTLE_S to act, then every REVIEW_S. Slower than the setting
asks for, the room's extra goes up, much faster, down. A stretch counts only if the flow was ours
and the room's valve open all through it, and the boiler burnt in it; one in which the flow stood
at its maximum or was held by the ramp may not raise the extra, which could not act. A heat-up that
ends much faster than asked takes a step off as well, or a short one would never bring it down.

The room's own rise is the measure, not the flow/return spread: a radiator that gives off little
heat and one whose room loses much of it look alike at the boiler. The extra shrinks with the
room's demand as it nears its target, as the demand boost does, and a room's extra at the end of
a heat-up is where its next one starts.

It is no part of the heating curve, which is for holding a temperature: while an extra is in force
the curve's learning pauses (a boiler cycling on a high flow would teach it to go down).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..models import RoomResult
from .common import as_dict, as_float
from .heat_call import CALL_CALLING

START_K = 1.0  # a heat-up begins this far below target; less is the demand boost's
END_K = 0.2  # and ends this close to it
JUDGE_K = 0.5  # its rise is judged while the room is this far below target: closer, the valve throttles
SETTLE_S = 1200.0  # the first verdict: the flow rises by 5 K per 10 minutes, the radiator has to warm through
REVIEW_S = 1200.0  # 1 K/h is a third of a kelvin in that time, more than a sensor's tenth
STEP_K = 2.0
BIG_STEP_K = 4.0  # less than half of the speed asked for
EXTRA_MAX_K = 16.0  # the flow's maximum limits it as well
SLOW_SHARE = 0.8  # slower than this share of the speed asked for: more
FAST_SHARE = 1.5  # faster than this: less


@dataclass(frozen=True)
class HeatUp:
    """A room's extra: in force during a heat-up, between two the value the next one starts from."""

    extra_k: float = 0.0  # stored
    since_ts: float | None = None  # the heat-up under way began then; None between heat-ups
    mark_ts: float | None = None  # its rise is measured from here: the start or the last verdict
    mark_c: float | None = None
    burned: bool = False  # the boiler heated since the mark
    clean: bool = True  # since the mark the flow was ours and the valve open, all through
    held: bool = False  # since the mark the flow stood at its maximum or was held by the ramp
    start_c: float | None = None  # the room's temperature when the heat-up began


HeatUps = Mapping[str, HeatUp]


def from_storage(data: object) -> dict[str, HeatUp]:
    out = {}
    for room, value in as_dict(data).items():
        extra = as_float(value)
        if isinstance(room, str) and extra is not None and 0.0 <= extra <= EXTRA_MAX_K:
            out[room] = HeatUp(extra_k=extra)
    return out


def to_storage(states: HeatUps) -> dict[str, float]:
    return {room: s.extra_k for room, s in states.items() if s.extra_k > 0.0}


def _gap(r: RoomResult) -> float | None:
    return None if r.temperature is None else round(r.target - r.temperature, 2)


def _verdict(extra: float, rise_k_h: float, rate_k_h: float) -> float:
    if rise_k_h < rate_k_h * SLOW_SHARE:
        return min(extra + (BIG_STEP_K if rise_k_h < rate_k_h / 2 else STEP_K), EXTRA_MAX_K)
    if rise_k_h > rate_k_h * FAST_SHARE:
        return max(extra - STEP_K, 0.0)
    return extra


def _ended(s: HeatUp, r: RoomResult, rate_k_h: float, now: float, reached: bool) -> HeatUp:
    """Between heat-ups only the extra is kept; one that got there much faster than asked takes a step off."""
    extra = s.extra_k
    if reached and s.start_c is not None and s.since_ts is not None and now - s.since_ts >= REVIEW_S / 2:
        rise = (r.temperature - s.start_c) / (now - s.since_ts) * 3600.0
        if rise > rate_k_h * FAST_SHARE:
            extra = max(extra - STEP_K, 0.0)
    return HeatUp(extra_k=extra)


def follow(
    states: HeatUps,
    rooms: tuple[RoomResult, ...],
    rate_k_h: float,
    now: float,
    heating: bool,
    judging: bool,
    held: bool = False,
) -> dict[str, HeatUp]:
    """One cycle: heat-ups begin and end, and are judged.

    `heating`: the boiler burns for the heating right now. `judging`: the flow is ours to set
    (active control, heating released), so a room's rise says something about it. `held`: the flow
    sent stood at its maximum or was held back by the ramp.
    """
    out = dict(states)
    if rate_k_h <= 0.0:
        # switched off: no heat-up is under way, what was learned is kept for when it comes back
        return {room: HeatUp(extra_k=s.extra_k) for room, s in out.items()} if any(s.since_ts for s in out.values()) else out
    for r in rooms:
        s = out.get(r.key, HeatUp())
        gap = _gap(r)
        wants = r.heating_allowed and gap is not None and r.boiler_call in (None, CALL_CALLING)
        if s.since_ts is None:
            if not wants or gap < START_K:
                continue
            s = HeatUp(extra_k=s.extra_k, since_ts=now, mark_ts=now, mark_c=r.temperature, start_c=r.temperature)
        elif not wants or gap < END_K:
            # got there whether or not it still calls: with sensors in tenths the call stops 0.1 K short
            # of target (heat_call.CALL_OFF_K) in the very cycle the room comes this close
            reached = r.heating_allowed and gap is not None and gap < END_K
            out[r.key] = _ended(s, r, rate_k_h, now, reached=reached)
            continue
        s = replace(
            s,
            burned=s.burned or heating,
            clean=s.clean and judging and r.thermostat_heating is not False,
            held=s.held or held,
        )
        due = now - s.mark_ts >= (SETTLE_S if s.mark_ts == s.since_ts else REVIEW_S)
        if due:
            extra = s.extra_k
            if s.clean and s.burned and gap >= JUDGE_K:
                rise = (r.temperature - s.mark_c) / (now - s.mark_ts) * 3600.0
                verdict = _verdict(extra, rise, rate_k_h)
                # more would not have reached the radiator: the flow could not rise
                extra = extra if s.held and verdict > extra else verdict
            # either way the rise is measured afresh from here
            s = replace(s, extra_k=extra, mark_ts=now, mark_c=r.temperature, burned=False, clean=True, held=False)
        out[r.key] = s
    return out


def extra_k(states: HeatUps, r: RoomResult) -> float:
    """What a room's heat-up adds to the flow now: its extra, shrinking with its demand."""
    s = states.get(r.key)
    if s is None or s.since_ts is None:
        return 0.0
    return s.extra_k * min(max(r.demand or 0.0, 0.0), 1.0)


def in_force(states: HeatUps, rooms: tuple[RoomResult, ...]) -> list[str]:
    """The rooms whose heat-up adds to the flow now."""
    return [r.key for r in rooms if extra_k(states, r) > 0.0]
