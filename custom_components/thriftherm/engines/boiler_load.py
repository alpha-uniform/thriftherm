"""How many rooms take heat: the second thing the heating curve depends on, next to the outdoor temperature.

Pure logic. One open radiator returns the water almost as warm as it went out; five take
several times the heat. A flow/return spread, and a correction learned from it, therefore
mean little without the number of rooms that called for heat.

Two things follow
* **The spread rule.** The spread only judges the curve while a fair share of the rooms that
  can take heat call (`spread_rule`). Below that, only the progress of the calling rooms counts.
* **A curve per load class and room.** The operation is sorted into three classes: one room
  calls, a few, or the majority, which is more than the share of the spread rule
  (`majority_from`; with half: three of four or five rooms, two of two or three). Within a
  class each room has its own correction of the heating curve (a line: a level and a slope),
  and the room that needs the most sets the flow. A learning step only moves the line it was
  taken for: what the bathroom alone taught does not shift the flow for the whole flat
  (measured 2026-10-11: one room calling drove the common correction to +4 K, and three rooms
  then started from it).

Kept simple on purpose
* Lines, not a grid of cells by outdoor band: a line has no edge at which the flow jumps, and
  it learns from mild and cold weather alike.
* A line without a value of its own borrows one until its first step (`line_of`): the same
  room's where more rooms call, else the common correction.
* A value may be **found**: once the rooms of a class have reached their targets for some
  hours without a step, it stays put, and only evidence that lasts moves it again. Without
  this the learning answers every small deviation and the flow keeps going up and down.
* A newly seen class only counts after it has held for a while (`settled_class`), so the flow
  does not flap at the boundary between two classes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from math import ceil, floor, isfinite

from ..models import RoomResult
from .common import as_dict, as_float
from .heat_call import CALL_CALLING, CALL_STALLED
from .room import TREND_WINDOW_S

CLASS_SINGLE = "single"
CLASS_FEW = "few"
CLASS_MAJORITY = "majority"
CLASSES = (CLASS_SINGLE, CLASS_FEW, CLASS_MAJORITY)  # in order of load: neighbours are next to each other
ROOM_MARK = ":"  # "few:kuche": the line of one room within a load class

SPREAD_RULE_ACTIVE = "active"
SPREAD_RULE_BLOCKED = "blocked_few_rooms"

# The rooms' verdict, the same evidence the cycling rule of the learning uses (measured 2026-10-10).
LAG_DEFICIT_K = 0.2  # a called room still this far below target ...
LAG_RATE_K_H = 0.2  # ... and rising more slowly than this: it does not get there
# ... once its own call has run this long. A call begins three tenths below target with a trend of 0 over the 90
# minutes before: with another room keeping the boiler released, every call began as "lagging" (review 2026-11-13).
LAG_MIN_CALL_S = 1800.0
HAND_HOLD_BAND_K = 1.0  # a target set by hand counts once the room is this close to it: holding, not heating up
MAX_GAP_S = 300.0  # a longer gap between two cycles (restart, outage) is not counted as operation

# A value that was found. First values, to be reviewed against the recorded cycles.
FOUND_AFTER_H = 3.0  # this much judged operation in the class without a lagging room and without a step
HOLD_UP_S = 2700.0  # a found value is raised only after a room has asked for it this long ...
HOLD_DOWN_S = 7200.0  # ... and lowered only after the rooms have allowed it this long: comfort before saving
# The asking need not be unbroken: a cycling boiler's calls end, a room 0.2 K below target reads 0.1 K
# the next half hour (measured 2026-10-10 in the bathroom). It only starts over after this long without it.
ASK_GRACE_S = 2700.0
# At a boundary (a third room calling on and off) the class would change by the minute, and with
# it the flow (review 2026-10-11: 4 K every minute, each one sent to the boiler).
DWELL_S = 600.0


@dataclass(frozen=True)
class LoadLine:
    """The correction of the heating curve for one room within a load class, and how well it has served."""

    level_k: float = 0.0  # added at every outdoor temperature
    slope_k: float = 0.0  # added in full at -10 °C, not at all at +15 °C
    hours: float = 0.0  # operation with the heating released
    lag_hours: float = 0.0  # of which a called room did not get there
    calm_hours: float = 0.0  # operation since the last lagging room or step
    found: bool = False  # it has served for `FOUND_AFTER_H` since its last step: only lasting evidence moves it
    last_step_k: float = 0.0  # the last learning step taken in this class (signed), and when: the next
    last_step_ts: float | None = None  # one doubles or halves by it, whatever the other classes did since
    # A step up for a room a cycling boiler did not get there is a trial, and so is every step down of such a
    # boiler (boiler_learning.review_trials): how fast the room rose then (K/h; None: it did not lag), and the
    # line's level and slope before it. None: no step is under review.
    trial: tuple[float | None, float, float] | None = None
    no_help_ts: float | None = None  # the last step up that left the room rising no faster was taken back then
    no_less_ts: float | None = None  # the last step down that left the room worse off was taken back then
    fast_calls: int = 0  # holding calls in a row that got the room to its target briskly (boiler_learning.count_calls)
    fast_call_ts: float | None = None  # ... and when the last of them ended: a day on, the count starts over
    borrowed: bool = False  # its value is not its own yet: `line_of` takes it from where the line starts
    asked: int = 0  # the direction the learning asks a found value to move in (not stored)
    asked_since_ts: float | None = None
    asked_last_ts: float | None = None

    @property
    def lag_share(self) -> float:
        return self.lag_hours / self.hours if self.hours > 0 else 0.0

    def to_storage(self) -> dict:
        return {
            "level_k": self.level_k,
            "slope_k": self.slope_k,
            "hours": round(self.hours, 4),
            "lag_hours": round(self.lag_hours, 4),
            "calm_hours": round(self.calm_hours, 4),
            "found": self.found,
            "last_step_k": self.last_step_k,
            "last_step_ts": self.last_step_ts,
            "trial": None if self.trial is None else list(self.trial),
            "no_help_ts": self.no_help_ts,
            "no_less_ts": self.no_less_ts,
            "fast_calls": self.fast_calls,
            "fast_call_ts": self.fast_call_ts,
            "borrowed": self.borrowed,
        }

    @classmethod
    def from_storage(cls, data: object) -> LoadLine:
        data = as_dict(data)

        def number(key: str, signed: bool = False) -> float:
            value = as_float(data.get(key))
            return value if value is not None and isfinite(value) and (signed or value >= 0) else 0.0

        def stamp_of(key: str) -> float | None:
            value = as_float(data.get(key))
            return value if value is not None and isfinite(value) else None

        raw = data.get("trial")
        trial = [None if v is None else as_float(v) for v in raw] if isinstance(raw, list) and len(raw) == 3 else []
        # the room's rise may be None (a step down for brisk calls), the line before the step may not
        whole = len(trial) == 3 and (raw[0] is None or trial[0] is not None) and None not in trial[1:]
        whole = whole and all(isfinite(v) for v in trial if v is not None)
        calls = as_float(data.get("fast_calls"))
        return cls(
            level_k=number("level_k", signed=True),
            slope_k=number("slope_k", signed=True),
            hours=number("hours"),
            lag_hours=number("lag_hours"),
            calm_hours=number("calm_hours"),
            found=data.get("found") is True,
            last_step_k=number("last_step_k", signed=True),
            last_step_ts=stamp_of("last_step_ts"),
            trial=(trial[0], trial[1], trial[2]) if whole else None,
            no_help_ts=stamp_of("no_help_ts"),
            no_less_ts=stamp_of("no_less_ts"),
            fast_calls=int(calls) if calls is not None and isfinite(calls) and calls > 0 else 0,
            fast_call_ts=stamp_of("fast_call_ts"),
            borrowed=data.get("borrowed") is True,
        )


Lines = Mapping[str, LoadLine]


def lines_from_storage(data: object) -> dict[str, LoadLine]:
    """Stored lines; anything that is not a room within one of the three classes is dropped.

    A store from before lines knew whether their value was their own kept every line with the value it had
    started from. One that never took a step and holds exactly what the same room's line has where fewer rooms
    call took it from there, the way `_start` has closed: it gives the value up (seen 2026-11-12:
    "majority:badezimmer" with the +10.8 K of "few:badezimmer"). Every other line keeps what it was stored with.
    """
    stored = {key: as_dict(value) for key, value in as_dict(data).items() if ROOM_MARK in key and class_of(key) in CLASSES}
    lines = {key: LoadLine.from_storage(value) for key, value in stored.items()}
    for key, line in lines.items():
        lighter = (lines.get(room_cell(other, room_of(key))) for other in CLASSES[: CLASSES.index(class_of(key))])
        taken = any(o and o.last_step_ts is not None and (o.level_k, o.slope_k) == (line.level_k, line.slope_k) for o in lighter)
        if "borrowed" not in stored[key] and line.last_step_ts is None and taken:
            lines[key] = replace(line, borrowed=True, found=False, calm_hours=0.0)
    return lines


def lines_to_storage(lines: Lines) -> dict:
    return {key: line.to_storage() for key, line in lines.items()}


def room_cell(load: str, room: str) -> str:
    """The line of one room within a load class."""
    return f"{load}{ROOM_MARK}{room}"


def class_of(cell: str) -> str:
    return cell.split(ROOM_MARK, 1)[0]


def room_of(cell: str | None) -> str | None:
    """The room of a room's line; None for the line of a whole class."""
    return cell.split(ROOM_MARK, 1)[1] if cell is not None and ROOM_MARK in cell else None


def deciding_cell(
    lines: Lines, load: str, rooms: Iterable[str], level_k: float, slope_k: float, cold_weight: float
) -> str | None:
    """Of the rooms that call, the line that asks for the highest flow; None without rooms: the common correction.

    There is one flow for all radiators. The room whose radiator is the smallest for its losses
    needs the most, and it sets the flow; the others take less through their valves. Which room
    that is depends on the radiator, the walls and the windows, not on the room's size: on the test
    day of 2026-10-13 the small kitchen stopped rising while the living room went on.
    """
    best, best_k = None, None
    for room in sorted(rooms):
        cell = room_cell(load, room)
        line = line_of(lines, cell, level_k, slope_k)
        value = line.level_k + line.slope_k * cold_weight
        if best_k is None or value > best_k + 1e-9:
            best, best_k = cell, value
    return best


# ---------------------------------------------------------------------------
# Counting the rooms
# ---------------------------------------------------------------------------
def wanting_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Rooms that want heat: those that call, and those the stall rule has given up on.

    A stalled room does not call the boiler any more, but its valve is open and it is below its
    target: while the boiler runs for another room, the flow is to reach it as well.
    """
    return [
        r
        for r in rooms
        if r.heating_allowed and r.temperature is not None and r.boiler_call in (None, CALL_CALLING, CALL_STALLED)
    ]


def calling_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Rooms that really ask the boiler for heat.

    A room that only heats along while the boiler runs anyway is not among them: its call
    state is judged before its target is raised, and it never calls by itself. Its open
    radiator still takes heat and widens the spread a little. Not counted, it cannot lift
    one real call over the threshold; once enough rooms really call, the spread that is
    judged includes it, which errs towards "the radiators take the heat".
    """
    return [r for r in rooms if r.heating_allowed and r.temperature is not None and r.boiler_call in (None, CALL_CALLING)]


def able_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Rooms that could take the boiler's heat right now: the total the calling rooms are measured against.

    A room with an open window, a thermostat switched off or silent, or without a temperature
    has its radiator shut or unknown; counting it would let two of three usable radiators pass
    for "too few".
    """
    return [r for r in rooms if r.heating_allowed and r.temperature is not None]


def fair_share_from(total: int, share: float) -> int:
    """How many calling rooms make "a fair share" of the installation (at least one)."""
    return max(ceil(total * min(max(share, 0.0), 1.0) - 1e-9), 1)


def majority_from(total: int, share: float) -> int:
    """How many calling rooms are the majority: more than the share (at least one, at most all).

    Not the fair share of the spread rule: half of four rooms made two the majority, and a flat of
    four knew one room and the majority only (seen 2026-10-13: two and four rooms ran in one class).
    """
    return min(max(floor(total * min(max(share, 0.0), 1.0) + 1e-9) + 1, 1), max(total, 1))


def spread_rule(calling: int, total: int, share: float) -> str:
    """May the flow/return spread judge the curve with this many rooms calling?"""
    return SPREAD_RULE_ACTIVE if calling >= fair_share_from(total, share) else SPREAD_RULE_BLOCKED


def load_class(calling: int, total: int, share: float) -> str | None:
    """One room, a few, or the majority; None while no room calls."""
    if calling <= 0:
        return None
    if calling >= majority_from(total, share):
        return CLASS_MAJORITY
    return CLASS_SINGLE if calling == 1 else CLASS_FEW


def settled_class(
    in_use: str | None, candidate: str | None, candidate_since_ts: float | None, seen: str | None, now_ts: float
) -> tuple[str | None, str | None, float | None]:
    """The class that counts: it changes once the class in use has not been seen for `DWELL_S`.

    Returns the class in use, the last other class seen and since when the class in use has
    been missing. Without a class in use the first one counts at once. A class in use that
    comes back in between stays; if two other classes take turns, the one seen when the time
    is up counts (review 2026-10-11: restarting the clock for each of them pinned the old
    class for hours). While no room calls the class in use stays as well: the heating is
    still released, and the flow jumped at the end of every call. The controller drops it
    when it blocks.
    """
    if seen == in_use or seen is None:
        return in_use, None, None
    if in_use is None:
        return seen, None, None
    since = candidate_since_ts if candidate is not None and candidate_since_ts is not None else now_ts
    if now_ts - since >= DWELL_S:
        return seen, None, None
    return in_use, seen, since


# ---------------------------------------------------------------------------
# The rooms' verdict
# ---------------------------------------------------------------------------
def counts(r: RoomResult) -> bool:
    """Whether a room's progress says anything about the flow (see `lagging_rooms`)."""
    if not r.heating_allowed or r.boost_active or r.thermostat_heating is False or r.temperature is None:
        return False
    if r.target_steady_s is not None and r.target_steady_s < TREND_WINDOW_S:
        return False
    if r.temperature_age_s is not None and r.temperature_age_s > TREND_WINDOW_S:
        return False
    return r.target_reason != "override" or round(r.target - r.temperature, 2) <= HAND_HOLD_BAND_K


def lagging_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Rooms that are called for, stay below target and barely rise (a stalled one included).

    What is no evidence that the flow is too low:
    * a room whose target moved within the trend's 90 minutes. The trend still holds the time before, flat or
      falling, so the room reads as not rising while it has only begun to (seen 2026-10-22: target 21 °C from
      03:07, a step up at 04:11). The curve is for holding a temperature; heating up to one is the demand
      boost's and the heat-up extra's;
    * a room whose sensor has not reported within those 90 minutes. One that reports on a change of about a
      degree only (issue #7, Shelly H&T) stood flat for four hours and then jumped: a trend of 0.0 K/h that
      is silence, and nobody knows what the room did;
    * a quick heat-up: it is a wish for a while;
    * a target set by hand that the room is still heating up to. Seen 2026-10-13: four rooms set 3 K
      above their temperature for a test drove the correction for most rooms from +1 K to +8.5 K in
      three hours. Once the room is within `HAND_HOLD_BAND_K` and stays there, it is holding, and
      that counts: a household that heats by hand only would never teach the curve otherwise;
    * a room whose thermostat says it does not heat. Its valve is shut although the room is below
      target (the valve's own sensor at the hot radiator, a window rule of the thermostat), and a
      hotter flow would only shut it sooner.
    """
    return [
        r
        for r in rooms
        if counts(r)
        and r.boiler_call in (None, CALL_CALLING, CALL_STALLED)
        # rounded as the call rule rounds it: sensors report tenths, and 21.0 - 20.8 is 0.1999999999999993
        and round(r.target - r.temperature, 2) >= LAG_DEFICIT_K
        and r.trend_k_per_h is not None
        and r.trend_k_per_h < LAG_RATE_K_H
        and (r.calling_s is None or r.calling_s >= LAG_MIN_CALL_S)
    ]


def lagging(rooms: tuple[RoomResult, ...]) -> bool:
    """Whether any room lags (`lagging_rooms`)."""
    return bool(lagging_rooms(rooms))


# ---------------------------------------------------------------------------
# The lines
# ---------------------------------------------------------------------------
def line_of(lines: Lines, load: str, level_k: float, slope_k: float) -> LoadLine:
    """The line in use for a load class or a room within it.

    One without a value of its own (`borrowed`, or not kept yet) takes it from where it starts (`_start`), each
    time it is asked: its hours and whether it was found are its own, its value follows the line it comes from
    until its first step. Kept with the value instead, it stood for good (seen 2026-11-12: "majority:badezimmer"
    at +10.8 K after 0.1 h and no step), and neither a step nor a reset of the other line reached it.
    """
    kept = lines.get(load)
    if kept is not None and not kept.borrowed:
        return kept
    start = _start(lines, load)
    level, slope = (level_k, slope_k) if start is None else (start.level_k, start.slope_k)
    return replace(kept or LoadLine(), level_k=level, slope_k=slope, borrowed=True, found=False)


def _start(lines: Lines, load: str) -> LoadLine | None:
    """The line a room's line without a value of its own takes it from; None: the common correction.

    The same room's line where more rooms call, the nearest first: it is the same radiator. Only that way
    round: alone the boiler cycles and its flow averages far below what is set, so what a room learned alone
    is too much among others.
    """
    room = room_of(load)
    heavier = () if room is None else CLASSES[CLASSES.index(class_of(load)) + 1:]
    return next((line for other in heavier if (line := lines.get(room_cell(other, room))) and not line.borrowed), None)


def observe(
    lines: Lines, load: str | None, hours: float, lag: bool, level_k: float, slope_k: float, judged: bool = True
) -> dict[str, LoadLine]:
    """Book one cycle of released heating to a line: its hours, and whether its room got there.

    `judged` says a verdict was possible in this cycle: a room called and had been given time.
    Only such time counts towards "found" (review 2026-10-11: a bathroom calling 25 minutes at
    a time and stuck a kelvin below target was "found" after eight calls).
    """
    out = dict(lines)
    if load is None or hours <= 0:
        return out
    line = line_of(lines, load, level_k, slope_k)
    calm = 0.0 if lag else line.calm_hours + (hours if judged else 0.0)
    out[load] = replace(
        line,
        hours=line.hours + hours,
        lag_hours=line.lag_hours + (hours if lag else 0.0),
        fast_calls=0 if lag else line.fast_calls,
        calm_hours=calm,
        # a lagging room does not unfind it: that takes a step; and a borrowed value is not this line's to find
        found=not line.borrowed and (line.found or calm >= FOUND_AFTER_H),
    )
    return out


def ask(line: LoadLine, direction: int, now_ts: float) -> tuple[bool, LoadLine]:
    """May a step in this direction be taken now? A found value only yields to evidence that lasts.

    Returns the answer and the line with what is being asked for remembered. The asking may
    pause for up to `ASK_GRACE_S`; after a longer pause, or once the other direction is
    asked for, it starts over.
    """
    if not line.found:
        return True, line
    fresh = (
        line.asked == direction
        and line.asked_since_ts is not None
        and line.asked_last_ts is not None
        and now_ts - line.asked_last_ts <= ASK_GRACE_S
    )
    if not fresh:
        return False, replace(line, asked=direction, asked_since_ts=now_ts, asked_last_ts=now_ts)
    hold = HOLD_UP_S if direction > 0 else HOLD_DOWN_S
    return now_ts - line.asked_since_ts >= hold, replace(line, asked_last_ts=now_ts)


def stepped(line: LoadLine, level_k: float, slope_k: float, moved_k: float, now_ts: float) -> LoadLine:
    """The line after a learning step: its new value, and it has to prove itself again."""
    return replace(
        line, level_k=level_k, slope_k=slope_k, calm_hours=0.0, found=False, last_step_k=moved_k, last_step_ts=now_ts,
        trial=None, no_help_ts=None, fast_calls=0, borrowed=False, asked=0, asked_since_ts=None, asked_last_ts=None,
    )


def summary(lines: Lines, cold_weight: float, level_k: float, slope_k: float) -> dict[str, dict]:
    """The rooms' lines, each with the value in use, for the display."""
    out = {}
    for load in sorted(lines):
        line = line_of(lines, load, level_k, slope_k)
        out[load] = {
            "correction_k": round(line.level_k + line.slope_k * cold_weight, 2),
            "level_k": round(line.level_k, 2),
            "slope_k": round(line.slope_k, 2),
            "found": line.found,
            "hours": round(line.hours, 1),
            "lag_share": round(line.lag_share, 2),
        }
    return out
