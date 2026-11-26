"""How far a room's valve opens on another room's flow, learned for each room (0.8.2).

Pure logic, no Home Assistant import. A room that calls while another room sets a higher flow has its valve open in part,
by the share of its radiator's output it needs (engines/valve_plan.flow_share). That share rests on two guesses: the flow
the room would ask for itself, from a line that is hardly learned for a room that seldom sets the flow, and that an
opening passes that share of the heat. So the opening is corrected by what came of it, a step a call:

* the room fell behind its limited valve (engines/boiler_control._throttle_rooms: half a degree below its target,
  rising too slowly) and the valve had to be opened fully again: the next time it gets more;
* its call ended with the valve limited and the room went on past its target: the next time it gets less.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

STEP_PCT = 10.0  # what one call moves it by, the step of the opening (valve_plan.OPENING_STEP_PCT)
ADJUST_MIN_PCT = -30.0
ADJUST_MAX_PCT = 50.0
BEHIND_WITHIN_S = 1800.0  # falling behind this long after the valve was last limited is not the limit's doing
OVERSHOOT_K = 0.3  # above its target after a call that ended limited: the valve was open too far
WATCH_S = 2700.0  # ... within this time after the call; later it is the sun or a stove
LIMITED_TO_THE_END_S = 150.0  # limited this shortly before the call ended: it ended limited


@dataclass(frozen=True)
class Throttle:
    adjust_pct: float = 0.0  # added to the opening the share gives
    calling: bool = False
    limited_ts: float | None = None  # when its valve was last limited in the call that runs
    target: float | None = None  # the target it was limited at
    judged: bool = False  # this call moved `adjust_pct` already
    ended_ts: float | None = None  # its call ended with the valve limited: watched for going on past its target

    def to_storage(self) -> dict:
        return {"adjust_pct": self.adjust_pct}  # what was learned; a call under way begins anew after a restart

    @classmethod
    def from_storage(cls, data: object) -> Throttle:
        value = data.get("adjust_pct") if isinstance(data, dict) else None
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        return cls(adjust_pct=float(value)) if ok and ADJUST_MIN_PCT <= value <= ADJUST_MAX_PCT else cls()


def adjusted(share: float, throttle: Throttle | None) -> float:
    """The share the valve's opening is taken from: what the room needs of its radiator's output, and what was learned."""
    return share if throttle is None else round(max(share + throttle.adjust_pct / 100.0, 0.0), 4)


def follow(
    t: Throttle, *, calling: bool, limited: bool, behind: bool, target: float, room_c: float | None, now_ts: float,
    disturbed: bool = False,
) -> Throttle:
    """One cycle of a room: `limited`, its valve is open in part by the flow now; `behind`, it calls, does not set the
    flow and fell behind (its valve fully open for that). `disturbed`: something else moves the room (it heats along, its
    window is open): no watch for an overshoot."""
    if t.target is not None and abs(target - t.target) > 0.05:
        t = replace(t, limited_ts=None, target=None, ended_ts=None)  # another target: what was seen was for the old one
    if calling:
        if not t.calling:
            t = replace(t, judged=False, limited_ts=None)
        t = replace(t, calling=True, ended_ts=None)
        if limited:
            return replace(t, limited_ts=now_ts, target=target)
        recent = t.limited_ts is not None and now_ts - t.limited_ts <= BEHIND_WITHIN_S
        if behind and recent and not t.judged:
            return replace(t, adjust_pct=min(t.adjust_pct + STEP_PCT, ADJUST_MAX_PCT), judged=True)
        return t
    if t.calling:
        to_the_end = t.limited_ts is not None and now_ts - t.limited_ts <= LIMITED_TO_THE_END_S and not t.judged
        t = replace(t, calling=False, limited_ts=None, ended_ts=now_ts if to_the_end else None)
    if t.ended_ts is None:
        return t
    if disturbed or now_ts - t.ended_ts > WATCH_S:
        return replace(t, ended_ts=None)
    if room_c is not None and round(room_c - target, 2) >= OVERSHOOT_K:
        return replace(t, adjust_pct=max(t.adjust_pct - STEP_PCT, ADJUST_MIN_PCT), judged=True, ended_ts=None)
    return t
