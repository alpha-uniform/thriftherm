"""What the boiler controller learns about the heating curve, and from what.

Pure logic. The control (`boiler_control`) calls `track_burns` with the burner sign each cycle
and `learn` while the heating is released; for the display there are `shown_phase`, `held_back`,
`burner_share`, `adjustments_last_day` and `moved_last_day`.

What is learned
* Two numbers that correct the configured curve: its **level** (added at every outdoor
  temperature) and its **slope** (added in full at -10 °C, not at all at +15 °C). A step
  taken on a mild day mostly moves the level, one taken in the cold mostly the slope, as a
  heating engineer sets a curve by hand: too cold only in winter means steeper, too cold in
  between means higher.

From what
* **The burner**, from the best sign the installation has: a gas meter, else the boiler's
  status code, else the coarse sign the coordinator hands over (the boiler's own flame sign
  where it has one, else its pump state). Burns for hot water never count.
* **A boiler that runs through** gives a settled flow/return spread: small means the flow
  is hotter than the radiators take, large with demand means too cold.
* **A boiler that cycles** never gives a settled spread; what it delivers shows in the
  rooms alone. A room that is called for and does not get there asks for more, and only
  rooms warming up briskly allow less.

Careful, not slow: evidence only after the first 10 minutes of a burner run and a 20 minute
review window; 45 minutes before a step up and an hour before a step down; a step up doubles
after a step up (to 4 K at most), any step halves after one against it (down to 0.5 K), and
steps down never grow; per day at most 10 K up and 4 K down, each correction within ±10 K,
and never further than the flow limits let the flow follow. After six steps or a quiet day a
run of steps starts at 0.5 K ("refining") instead of 1 K ("learning").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from statistics import median

from ..const import BOILER_PLAN_HEAT, BOILER_REPORTS_HEATING, BOILER_REPORTS_HOT_WATER, CTRL_OFF
from ..models import RoomResult
from . import boiler_load as load
from .boiler_memory import (
    ADJUSTMENTS_STORED,
    CORRECTION_LIMIT_K,
    BoilerInputs,
    BoilerMemory,
    cold_weight,
    correction,
    curve_flow,
)

BURNING_GAS_W = 1000.0  # a gas meter reading above this is a burning boiler

# --- the burner
BURNS_KEPT_S = 3600.0  # cycling and the burner share look an hour back
# A boiler that fires for a minute and then waits out its lockout is cycling. Measured
# 2026-09-17 with one radiator open: 60 s of burner every 15 minutes.
CYCLE_MIN_STARTS = 3  # within the hour
CYCLE_SHORT_BURN_S = 300.0  # a run this short never reaches a steady spread

# --- a boiler that runs through
SETTLE_S = 600.0  # ignore the first minutes of a burner run
MIN_SAMPLES = 10
REVIEW_S = 1200.0
SPREAD_LOW_K = 5.0
SPREAD_HIGH_K = 15.0
SPREAD_ALPHA = 0.2
SLOW_RATE_K_H = load.LAG_RATE_K_H
DEMAND_ACTIVE = 0.3

# --- a boiler that cycles
# Measured 2026-10-10 with the flow minimum lowered from 45 to 35 °C: burner runs fell from 62 s
# to 41 s, the pause stayed at the boiler's lockout (15 min, and the lockout grows as the flow
# setpoint falls), and the bathroom hung 0.2-0.3 K below target for 14 hours. Less flow is less
# heat there, not fewer starts.
LAG_MIN_HEAT_S = 1800.0  # a lagging room (`boiler_load.lagging`) counts once the heating has been released this long
BRISK_RATE_K_H = load.BRISK_RATE_K_H  # every called room rises at least this fast: the curve may come down

# --- the steps
# Steps that grow with certainty. Fixed 1 K steps, four a day, were measured on 2026-10-11: with
# the flow minimum 10 K too low for the bathroom alone, eight hours brought the correction to
# +4 K and the room stayed 0.6 K below target. Now a step UP doubles after one before it, and
# any step against the one before halves: coarse first, fine once the answer has been overshot.
# Steps DOWN never grow, and each direction has its own allowance for the day: comfort comes
# before saving, and a curve lowered by 10 K in an afternoon (review 2026-10-11) must not leave
# the day without a step back up.
STEP_LEARNING_K = 1.0  # the first step of a run, while "learning"
STEP_REFINING_K = 0.5  # ... while "refining", and the smallest step
STEP_MAX_K = 4.0  # the largest step up
STREAK_S = 3 * 3600.0  # a step this long after the last one starts over at the first step
ADJUST_COOLDOWN_UP_S = 2700.0  # a room needs about this long to answer a higher flow
ADJUST_COOLDOWN_DOWN_S = 3600.0
ADJUST_MAX_UP_K_PER_DAY = 10.0  # what the curve may be raised by within a day ...
ADJUST_MAX_DOWN_K_PER_DAY = 4.0  # ... and lowered (the four 1 K steps a day of before)
DAY_S = 24 * 3600.0
REFINE_AFTER_ADJUSTMENTS = 6

PHASE_LEARNING = "learning"
PHASE_REFINING = "refining"
PHASE_PAUSED = "paused"


# ---------------------------------------------------------------------------
# The burner
# ---------------------------------------------------------------------------
def flame(inp: BoilerInputs) -> bool:
    """Is the burner on for the heating, by the best sign this installation has.

    A gas meter sees the flame itself, the status code is the boiler's own word, and
    `burner_heating` is the coarsest sign (a flame sign of the boiler's own, else the pump state,
    which runs on for minutes after the flame). None of them is required. What the boiler
    reports as hot water is never a heating burn.
    """
    if inp.reported_mode == BOILER_REPORTS_HOT_WATER or inp.hot_water_active:
        return False
    if inp.gas_power_w is not None:
        return inp.gas_power_w > BURNING_GAS_W
    if inp.status_known:
        return inp.reported_mode == BOILER_REPORTS_HEATING
    return inp.burner_heating


def track_burns(mem: BoilerMemory, burning: bool, now_ts: float) -> BoilerMemory:
    """Keep the heating burns of the last hour."""
    burns = tuple(burn for burn in mem.burns if now_ts - burn[1] <= BURNS_KEPT_S)
    if burning and mem.burn_since_ts is None:
        return replace(mem, burn_since_ts=now_ts, burns=burns)
    if not burning and mem.burn_since_ts is not None:
        return replace(mem, burn_since_ts=None, burns=(*burns, (mem.burn_since_ts, now_ts)))
    return mem if burns == mem.burns else replace(mem, burns=burns)


def drop_burn_in_progress(mem: BoilerMemory) -> BoilerMemory:
    """Forget the burn that is running: it turned out not to be for the heating."""
    return mem if mem.burn_since_ts is None else replace(mem, burn_since_ts=None)


def burner_share(mem: BoilerMemory, now_ts: float) -> tuple[float, int]:
    """Share of the last hour the burner ran for the heating (0..1), and how often it started."""
    since = now_ts - BURNS_KEPT_S
    runs = list(mem.burns) + ([(mem.burn_since_ts, now_ts)] if mem.burn_since_ts is not None else [])
    burning = sum(max(min(end, now_ts) - max(start, since), 0.0) for start, end in runs)
    return burning / BURNS_KEPT_S, len([1 for start, _ in runs if start >= since])


def short_cycling(mem: BoilerMemory, now_ts: float, since_ts: float | None = None) -> bool:
    """Several short burner runs within the last hour (a run still going is not judged yet).

    `since_ts`: only runs from then on count, e.g. those since the learning may act again.
    """
    since = now_ts - BURNS_KEPT_S if since_ts is None else max(now_ts - BURNS_KEPT_S, since_ts)
    finished = [end - start for start, end in mem.burns if start >= since]
    starts = len(finished) + (1 if mem.burn_since_ts is not None and mem.burn_since_ts >= since else 0)
    return starts >= CYCLE_MIN_STARTS and bool(finished) and median(finished) <= CYCLE_SHORT_BURN_S


# ---------------------------------------------------------------------------
# Which way the curve should move
# ---------------------------------------------------------------------------
def steady_direction(spread: float | None, heating: list[RoomResult], lagging: bool = False) -> tuple[int, str | None]:
    """A boiler that runs through: from its settled spread and the progress of the rooms.

    `lagging` says a called room stays below target and barely rises (`boiler_load.lagging`).
    The rooms are looked at before anything lowers the curve. Review 2026-10-13: three of three
    rooms 1.2 K below target and not rising, with a spread of 3.5 K, lowered it by 4 K a day
    while the same cycles were booked as lagging; and a room 0.2 K below target that did not
    move counted as satisfied (its demand is 0.1) and lowered it by 1 K an hour.
    """
    demand = max(((r.demand or 0.0) for r in heating), default=0.0)
    demanding = [r for r in heating if (r.demand or 0.0) > DEMAND_ACTIVE]
    slow_rooms = [r for r in demanding if r.trend_k_per_h is not None and r.trend_k_per_h < SLOW_RATE_K_H]
    slow = bool(slow_rooms)  # any slow room holds the curve back from going down
    if spread is not None and spread < SPREAD_LOW_K:
        return (0, None) if lagging or slow else (-1, "spread_small")
    if spread is not None and spread > SPREAD_HIGH_K and demand > DEMAND_ACTIVE:
        return 1, "spread_large"
    if demanding:
        # but only a room whose progress says something about the flow asks for more (`boiler_load.counts`)
        return (1, "rooms_slow") if any(load.counts(r) for r in slow_rooms) else (0, None)
    if demand <= 0.1 and not lagging:
        return -1, "rooms_satisfied"
    return 0, None


def cycling_direction(inp: BoilerInputs, mem: BoilerMemory) -> tuple[int, str | None]:
    """A boiler that cycles: judged by the rooms it is heating.

    A room the stall rule has given up on still counts as lagging: it is the evidence.
    """
    released_for = 0.0 if mem.state_since_ts is None else inp.now_ts - mem.state_since_ts
    if load.lagging(inp.rooms) and released_for >= LAG_MIN_HEAT_S:
        return 1, "rooms_lagging"
    calling = load.calling_rooms(inp.rooms)
    if calling and all(r.trend_k_per_h is not None and r.trend_k_per_h >= BRISK_RATE_K_H for r in calling):
        return -1, "short_cycling"
    return 0, None


# ---------------------------------------------------------------------------
# Taking a step
# ---------------------------------------------------------------------------
def phase(mem: BoilerMemory, now_ts: float) -> str:
    if len(mem.adjustments) >= REFINE_AFTER_ADJUSTMENTS:
        return PHASE_REFINING
    if mem.adjustments and now_ts - mem.adjustments[-1] > DAY_S:
        return PHASE_REFINING
    return PHASE_LEARNING


def adjustments_last_day(mem: BoilerMemory, now_ts: float) -> int:
    return len([t for t in mem.adjustments if now_ts - t < DAY_S])


def _sizes(mem: BoilerMemory) -> list[float | None]:
    """The signed size of each remembered step, None for one stored before sizes were kept."""
    count = len(mem.adjustments)
    known: list[float | None] = list(mem.adjustment_steps)[-count:] if count else []
    return [None] * (count - len(known)) + known


def moved_last_day(mem: BoilerMemory, now_ts: float, direction: int = 0) -> float:
    """By how much the curve was moved within the last day: up (1), down (-1) or both added up (0).

    A step stored before sizes were kept was a step of 1 K, its direction unknown: it counts
    for either direction.
    """
    total = 0.0
    for t, size in zip(mem.adjustments, _sizes(mem), strict=False):
        if now_ts - t >= DAY_S:
            continue
        if size is None:
            total += STEP_LEARNING_K
        elif direction == 0 or (size > 0) == (direction > 0):
            total += abs(size)
    return total


def next_step(mem: BoilerMemory, direction: int, now_ts: float, line: load.LoadLine | None = None) -> float:
    """The size of a step in this direction now.

    Up after a step up: doubled. Against the step before: halved. Down after a step down, a
    first step, or one after a step whose size is not known: the first step. With a load
    class's `line` the step before is the last one taken in that class: a step the bathroom
    alone took says nothing about the size of the next one for the whole flat.
    """
    first = STEP_REFINING_K if phase(mem, now_ts) == PHASE_REFINING else STEP_LEARNING_K
    if line is not None:
        last, last_ts = (line.last_step_k, line.last_step_ts) if line.last_step_ts is not None else (None, None)
    else:
        sizes = _sizes(mem)
        last, last_ts = (sizes[-1], mem.adjustments[-1]) if sizes and mem.adjustments else (None, None)
    if last is None or last_ts is None or last == 0 or now_ts - last_ts > STREAK_S:
        return first
    if (last > 0) != (direction > 0):
        return max(abs(last) / 2.0, STEP_REFINING_K)
    return min(max(abs(last), STEP_REFINING_K) * 2.0, STEP_MAX_K) if direction > 0 else first


def _limited(value: float) -> float:
    """The ±10 K bound of a correction."""
    return min(max(value, -CORRECTION_LIMIT_K), CORRECTION_LIMIT_K)


def step_cell(inp: BoilerInputs, heating: list[RoomResult], direction: int, reason: str | None, cell: str | None) -> str | None:
    """The line a step moves: a step up for a room that does not get there moves that room's own line.

    Every other step moves the line that sets the flow (`cell`): a spread or a cycling boiler tells
    something about the flow that was sent, and that was this line's. A room's line is only ever
    raised by its own lack: the kitchen not getting there says nothing about the living room.
    """
    if cell is None or load.room_of(cell) is None or direction <= 0:
        return cell
    if reason == "rooms_lagging":
        behind = max(load.lagging_rooms(inp.rooms), key=lambda r: round(r.target - r.temperature, 2), default=None)
    elif reason == "rooms_slow":
        slow = [
            r for r in heating
            if load.counts(r)
            and (r.demand or 0.0) > DEMAND_ACTIVE
            and r.trend_k_per_h is not None
            and r.trend_k_per_h < SLOW_RATE_K_H
        ]
        behind = min(slow, key=lambda r: r.trend_k_per_h, default=None)
    else:
        return cell
    return cell if behind is None else load.room_cell(load.class_of(cell), behind.key)


def _take_step(
    inp: BoilerInputs,
    mem: BoilerMemory,
    heating: list[RoomResult],
    direction: int,
    reason: str | None,
    cell: str | None,
    boosts: Mapping[str, float] | None = None,
) -> BoilerMemory:
    """A step of the line it belongs to (`step_cell`), from the flow `cell` set."""
    after = apply(inp, mem, direction, reason, step_cell(inp, heating, direction, reason, cell), cell, boosts)
    return _capped(inp, after, mem, cell, boosts or {})


def _capped(
    inp: BoilerInputs, after: BoilerMemory, before: BoilerMemory, cell: str | None, boosts: Mapping[str, float]
) -> BoilerMemory:
    """After a step down of the line that set the flow, no room that wants heat keeps a higher one.

    The step down says that every such room got more than it needed. Kept higher, the next of
    them would set the flow at once, and the flow would never come down. What the rooms ask for
    on top of their lines counts: no room's line and boosts stay above the new flow.
    """
    group = load.class_of(cell) if cell is not None and load.room_of(cell) is not None else None
    if group is None or after.adjustments == before.adjustments or (after.adjustment_steps or (0.0,))[-1] >= 0:
        return after
    cold = cold_weight(inp.outdoor_c)
    top = correction(after, inp.outdoor_c, cell) + boosts.get(load.room_of(cell), 0.0)
    lines = dict(after.lines)
    for r in load.wanting_rooms(inp.rooms):
        own = load.room_cell(group, r.key)
        line = load.line_of(lines, own, after.offset_k, after.slope_k)
        excess = line.level_k + line.slope_k * cold + boosts.get(r.key, 0.0) - top
        if own != cell and excess > 1e-9:
            lines[own] = replace(line, level_k=round(line.level_k - excess, 3))
    return replace(after, lines=lines)


def apply(
    inp: BoilerInputs,
    mem: BoilerMemory,
    direction: int,
    reason: str | None,
    cell: str | None = None,
    deciding: str | None = None,
    boosts: Mapping[str, float] | None = None,
) -> BoilerMemory:
    """Move the curve by one step at the current outdoor temperature, unless a guard says no.

    The step is shared between level and slope by where the outdoor temperature lies between
    the curve's points, scaled so that the flow at this temperature moves by the full step.

    With `cell`, the load class or the room within it the step is taken under, only that line
    moves; a line that was found first has to be asked for long enough (`boiler_load.ask`).
    Without one the common correction moves, as it always did. `deciding` is the line that set
    the flow, when it is another than `cell`: a room that got more than its own line asks for
    more than what it got. `boosts` are what the calling rooms ask for on top of their lines.
    """
    now = inp.now_ts
    p = inp.params
    # anti-windup: a correction the flow limits already swallow must not keep growing
    raw = curve_flow(inp.outdoor_c, p, limited=False)
    extra = boosts or {}
    line_flow = raw + correction(mem, inp.outdoor_c, cell)
    # what the room asked for, and the flow that was sent: what the room that set it asked for, never
    # below the minimum (each a line and, while the room calls, its boosts)
    planned = line_flow + extra.get(load.room_of(cell), 0.0)
    setter = deciding or cell
    asked = raw + correction(mem, inp.outdoor_c, setter) + extra.get(load.room_of(setter), 0.0)
    if direction > 0 and asked < p.boiler_flow_min:
        # The flow stands at its minimum, not on any line: a room that does not get there needs more
        # than the minimum on a day this mild, which says nothing about the curve. Taken into a line,
        # the gap to the minimum became a correction at every outdoor temperature (test of 2026-10-15:
        # at 16 °C outside one step moved a line by 4.4 K, three steps took it to +9.3 K). It is shown
        # as held back instead (`held_back`); the minimum is the user's to raise.
        return mem
    sent = max(asked, p.boiler_flow_min)
    room = p.boiler_flow_max - max(planned, sent) if direction > 0 else planned - p.boiler_flow_min
    if room <= 0:
        return mem
    line = load.line_of(mem.lines, cell, mem.offset_k, mem.slope_k) if cell else None
    if line is not None:
        allowed, line = load.ask(line, direction, now)
        if line.found:
            # A found line remembers that it was asked, whatever stops the step below. Review 2026-10-13:
            # granted by its hold but stopped by the cooldown of another class's step, it forgot the
            # asking, which lapsed and started over (a step at +111 minutes instead of +85).
            mem = replace(mem, lines={**mem.lines, cell: line})
        if not allowed:
            return mem  # a value that was found stays put until the evidence has lasted
    cooldown = ADJUST_COOLDOWN_UP_S if direction > 0 else ADJUST_COOLDOWN_DOWN_S
    if mem.adjustments and now - mem.adjustments[-1] < cooldown:
        return mem
    allowance = ADJUST_MAX_UP_K_PER_DAY if direction > 0 else ADJUST_MAX_DOWN_K_PER_DAY
    left = allowance - moved_last_day(mem, now, direction)
    # no further than the day allows and the flow limit lets the flow follow
    size = min(next_step(mem, direction, now, line), left, room)
    if size < STEP_REFINING_K:
        return mem

    # A step up starts at the flow that was sent, or it would move nothing for hours: a room below the
    # line of the room that set the flow got more than its own line and still did not get there. What
    # is felt is the step.
    below = max(sent - planned, 0.0) if direction > 0 else 0.0
    step = direction * size + below
    cold = cold_weight(inp.outdoor_c)
    share = step / (1.0 - cold + cold * cold)
    from_level, from_slope = (mem.offset_k, mem.slope_k) if line is None else (line.level_k, line.slope_k)
    level = _limited(from_level + share * (1.0 - cold))
    slope = _limited(from_slope + share * cold)
    # Rounded as it is stored. Shared between level and slope and added up again, half a kelvin comes
    # out as 0.49999999999999994 at many outdoor temperatures, and four in ten of the smallest steps
    # were thrown away as "swallowed" (review 2026-10-13).
    moved = round((level - from_level) + (slope - from_slope) * cold - below, 3)
    if abs(moved) < STEP_REFINING_K:
        # the ±10 K limit swallows it: no step is spent on what the flow at this temperature would not feel
        return mem
    known = [size for size in _sizes(mem) if size is not None]
    return replace(
        mem,
        offset_k=level if line is None else mem.offset_k,
        slope_k=slope if line is None else mem.slope_k,
        lines=mem.lines if line is None else {**mem.lines, cell: load.stepped(line, level, slope, moved, now)},
        adjustments=(*mem.adjustments[-(ADJUSTMENTS_STORED - 1):], now),
        adjustment_steps=(*known[-(ADJUSTMENTS_STORED - 1):], moved),
        last_adjust_reason=reason,
        last_review_ts=now,
        samples=0,
    )


def learn(
    inp: BoilerInputs,
    mem: BoilerMemory,
    heating: list[RoomResult],
    burning: bool,
    cell: str | None = None,
    boosts: Mapping[str, float] | None = None,
) -> BoilerMemory:
    """One cycle of learning while the heating is released; `burning` is the heating flame now.

    `heating` are the rooms that call. How many of them there are decides whether the spread
    may judge the curve; `cell` is the line that sets the flow (a load class, or the room within it
    that needs the most), and `step_cell` says which line a step moves. `boosts` are what each calling
    room asks for on top of its line (`boiler_control._boosts`).
    """
    now = inp.now_ts
    share = inp.params.boiler_spread_min_room_share
    able = len(load.able_rooms(inp.rooms))
    enough_rooms = load.spread_rule(len(heating), able, share) == load.SPREAD_RULE_ACTIVE
    if enough_rooms and mem.spread_judging_since_ts is None:
        # The spread collected while too few rooms called was that of one or two radiators (review
        # 2026-10-11: carried over, three cycles of the whole flat were judged by the bathroom's 1 K).
        # Only the spread starts over; the review rhythm and the rooms' progress go on.
        mem = replace(mem, spread_ema=None, spread_judging_since_ts=now)
    elif not enough_rooms and mem.spread_judging_since_ts is not None:
        mem = replace(mem, spread_judging_since_ts=None)
    # the spread judges only what it has seen under enough rooms for a whole review window
    judged_by_spread = mem.spread_judging_since_ts is not None and now - mem.spread_judging_since_ts >= REVIEW_S
    if not inp.learning_allowed:
        return replace(mem, learning_since_ts=None) if mem.learning_since_ts is not None else mem
    if mem.learning_since_ts is None:
        # After a pause the evidence starts over: what came before was another setpoint, a drying, or
        # the flow of the boiler's own knob. Seen 2026-10-13: the boiler control came back from "plan
        # only", and in the same cycle the curve went up by 4 K on burner runs of the knob's 55 °C.
        mem = replace(mem, learning_since_ts=now, last_review_ts=None, samples=0, spread_ema=None)
    fresh = now - mem.learning_since_ts < REVIEW_S
    if not fresh and short_cycling(mem, now, mem.learning_since_ts):
        direction, reason = cycling_direction(inp, mem)
        if direction:
            after = _take_step(inp, mem, heating, direction, reason, cell, boosts)
            if after.adjustments != mem.adjustments:  # not by their number: that stops changing once the list is full
                return after
            mem = after  # not taken; a found line remembers that it was asked
    if not burning or inp.flow_c is None or inp.return_c is None:
        return mem
    if mem.heating_since_ts is None or now - mem.heating_since_ts < SETTLE_S:
        return mem

    spread = inp.flow_c - inp.return_c
    ema = spread if mem.spread_ema is None else mem.spread_ema + SPREAD_ALPHA * (spread - mem.spread_ema)
    mem = replace(mem, spread_ema=ema, samples=mem.samples + 1)
    if mem.last_review_ts is None:
        return replace(mem, last_review_ts=now)  # the 20 min window starts with the first settled sample
    if mem.samples < MIN_SAMPLES or now - mem.last_review_ts < REVIEW_S:
        return mem

    # With only a few rooms calling the spread says little about the curve: one open radiator
    # returns the water almost as warm as it went out. Then only the rooms' progress counts.
    direction, reason = steady_direction(ema if judged_by_spread else None, heating, load.lagging(inp.rooms))
    if direction == 0:
        return replace(mem, last_review_ts=now)
    after = _take_step(inp, mem, heating, direction, reason, cell, boosts)
    # reviewed either way: a step that was not taken (a found line still being asked) waits for the next window
    return after if after.adjustments != mem.adjustments else replace(after, last_review_ts=now)


def follow_heating_run(mem: BoilerMemory, burning: bool, now_ts: float) -> BoilerMemory:
    """The spread only means something while the boiler really heats the circuit: start and end of a run."""
    if burning and mem.heating_since_ts is None:
        return replace(mem, heating_since_ts=now_ts, samples=0, spread_ema=None)
    if not burning and mem.heating_since_ts is not None:
        return replace(mem, heating_since_ts=None, samples=0)
    return mem


def forget_run(mem: BoilerMemory) -> BoilerMemory:
    """Drop what the run in progress has collected (hot water heated the exchanger)."""
    return replace(mem, heating_since_ts=None, samples=0, spread_ema=None)


def reset(mem: BoilerMemory) -> BoilerMemory:
    """Forget what was learned; the control's own state stays."""
    return replace(
        mem, offset_k=0.0, slope_k=0.0, lines={}, adjustments=(), adjustment_steps=(), last_adjust_reason=None,
        spread_ema=None, samples=0, last_review_ts=None, heat_up={},
    )


# ---------------------------------------------------------------------------
# For the display
# ---------------------------------------------------------------------------
def shown_phase(inp: BoilerInputs, mem: BoilerMemory, burning: bool) -> str:
    """Learning goes on while the burner heats, or while the boiler cycles through a released heating."""
    released = mem.state == BOILER_PLAN_HEAT and inp.control_mode != CTRL_OFF
    at_work = burning or (released and short_cycling(mem, inp.now_ts))
    return phase(mem, inp.now_ts) if (inp.learning_allowed and at_work) else PHASE_PAUSED


def held_back(inp: BoilerInputs, mem: BoilerMemory, cell: str | None = None, asked: float | None = None) -> str | None:
    """A step that is due but cannot be taken, because the flow stands at its minimum.

    Cycling asks for less; or a room does not get there while the flow the rooms ask for (`asked`)
    lies below the minimum, where the learning takes no step up (`apply`).
    """
    if asked is not None and asked < inp.params.boiler_flow_min and load.lagging(inp.rooms):
        return "rooms_lagging_at_flow_min"
    if not short_cycling(mem, inp.now_ts) or cycling_direction(inp, mem)[0] >= 0:
        return None
    planned = curve_flow(inp.outdoor_c, inp.params, limited=False) + correction(mem, inp.outdoor_c, cell)
    at_minimum = planned <= inp.params.boiler_flow_min
    return "short_cycling_at_flow_min" if at_minimum else None
