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
  rooms alone. A room that is called for and does not get there asks for more; a step up
  that leaves it rising no faster is taken back (`review_trials`). Only rooms close to their
  targets that still rise briskly allow less.
* **Rooms that hold a temperature**, not rooms heating up to one: a room's progress counts
  once its target has stood for the 90 minutes its trend looks back (`boiler_load.counts`).

Careful, not slow: evidence only after the first 10 minutes of a burner run and a 20 minute
review window; 90 minutes before a step up and an hour before a step down; a step up doubles
after a step up (to 2 K at most), any step halves after one against it (down to 0.5 K), and
steps down never grow; per day at most 10 K up and 4 K down, each correction within ±10 K (a step
the limit swallows or cuts short is shown as held back), and never further than the flow limits
let the flow follow. After six steps or a quiet day a run of steps starts at 0.5 K ("refining")
instead of 1 K ("learning").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from statistics import median

from ..const import BOILER_PLAN_HEAT, BOILER_REPORTS_HEATING, BOILER_REPORTS_HOT_WATER, CTRL_OFF
from ..models import EndedCall, RoomResult
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
DEMAND_ACTIVE = 0.3

# --- a boiler that cycles
# Measured 2026-10-10 with the flow minimum lowered from 45 to 35 °C: burner runs fell from 62 s
# to 41 s, the pause stayed at the boiler's lockout (15 min, and the lockout grows as the flow
# setpoint falls), and the bathroom hung 0.2-0.3 K below target for 14 hours. Less flow is less
# heat there, not fewer starts.
# A step up for a room that lags is a trial (`review_trials`):
HELPED_K_H = 0.1  # it helped a room that rises at least this much faster than when the step was taken
TRIAL_MAX_S = 3 * 3600.0  # judged after the wait that follows a step up, and no later than this
NO_HELP_S = 24 * 3600.0  # after one that was taken back none is tried for the room for this long: then things may have changed
LAG_MIN_HEAT_S = 1800.0  # the rooms' progress (`boiler_load.lagging`) counts once the heating is released this long
# What lowers a cycling boiler's curve is the pace of the rooms' holding calls (`count_calls`). The test bathroom, its
# line 10 K too high and lagging in 63 % of its hours, made 0.3 to 0.4 K/h in them (2026-10-24/25): brisk has to
# lie well above that. 0.2 K in 20 minutes is one or two burner cycles (a cycle every 9 to 16 minutes, measured).
FAST_CALL_K_H = 0.6
FAST_CALLS = 3  # so many brisk calls in a row, and the line that set the flow for them comes down a step
CALLS_KEPT = 10  # a room's last calls, kept for the diagnostics
RELEASE_SLACK_S = 300.0  # the release may follow a call's start by the control's minimum state time

# --- the steps
# Steps that grow with certainty: a step UP doubles after one before it, and any step against the
# one before halves, coarse first and fine once the answer has been overshot. Fixed 1 K steps, four
# a day, left the bathroom 0.6 K below target after eight hours (measured 2026-10-11). Steps DOWN
# never grow, and each direction has its own allowance for the day: comfort comes before saving,
# and a curve lowered by 10 K in an afternoon (review 2026-10-11) must not leave the day without a
# step back up.
STEP_LEARNING_K = 1.0  # the first step of a run, while "learning"
STEP_REFINING_K = 0.5  # ... while "refining", and the smallest step
STEP_MAX_K = 2.0  # the largest step up
STREAK_S = 3 * 3600.0  # a step this long after the last one starts over at the first step
# What a step up did shows in the rooms' trend, which looks back 90 minutes. After 45 minutes it still held more of
# the time before the step than after it (seen 2026-10-22: +1, +2 and +4 K within two and a half hours).
ADJUST_COOLDOWN_UP_S = load.TREND_WINDOW_S
ADJUST_COOLDOWN_DOWN_S = 3600.0
ADJUST_MAX_UP_K_PER_DAY = 10.0  # what the curve may be raised by within a day ...
ADJUST_MAX_DOWN_K_PER_DAY = 4.0  # ... and lowered
DAY_S = 24 * 3600.0
REFINE_AFTER_ADJUSTMENTS = 6
# A step the ±10 K limit swallowed or cut short is shown as held back this long: longer than the wait for the
# next try, which comes a review window or two after the wait that follows a step up. So the reason stands for
# as long as the room keeps asking, and goes once nothing has met the limit for that long (review 2026-10-23).
LIMIT_SHOWN_S = ADJUST_COOLDOWN_UP_S + 2 * REVIEW_S

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


def track_burns(
    mem: BoilerMemory, burning: bool, now_ts: float, day_start_ts: float | None = None, *, readable: bool = True
) -> BoilerMemory:
    """Keep the heating burns of the last hour, and count the starts of the day (from `day_start_ts`, local midnight).

    A start is counted when its burn ends as a heating burn: until then it may still turn out to be hot
    water (`drop_burn_in_progress`). Counted as the burn began, a start had to be taken back, and the day's
    count went down (seen 2026-10-22 to 25: 72, ten seconds later 71). Home Assistant warns about that for a
    total_increasing sensor, and its statistics take a drop of more than a tenth for a new meter. So a burn
    running across midnight is a start of the day it ends on.
    """
    if day_start_ts is not None and mem.starts_day_ts != day_start_ts:
        mem = replace(mem, starts_day_ts=day_start_ts, starts_today=0)
    if mem.burn_interrupted and readable:
        # The first cycle after Home Assistant stopped during a burn in which the boiler can be read: the first
        # cycles often come seconds before its entities (review 2026-10-26). A burner on now is taken for that
        # burn still going, and counted as it ends; otherwise it ended while nobody watched, and is counted now.
        mem = replace(mem, burn_interrupted=False, starts_today=mem.starts_today + (0 if burning else 1))
    burns = tuple(burn for burn in mem.burns if now_ts - burn[1] <= BURNS_KEPT_S)
    if burning and mem.burn_since_ts is None:
        return replace(mem, burn_since_ts=now_ts, burns=burns)
    if not burning and mem.burn_since_ts is not None:
        return replace(mem, burn_since_ts=None, burns=(*burns, (mem.burn_since_ts, now_ts)), starts_today=mem.starts_today + 1)
    return mem if burns == mem.burns else replace(mem, burns=burns)


def drop_burn_in_progress(mem: BoilerMemory) -> BoilerMemory:
    """Forget the burn that is running: it turned out not to be for the heating, so it is no start of the heating either.

    Its start was not counted yet (`track_burns`), so the count stays as it is. A burn that Home Assistant's
    stop interrupted is judged the same way at the first cycle after the restart.
    """
    if mem.burn_since_ts is None and not mem.burn_interrupted:
        return mem
    return replace(mem, burn_since_ts=None, burn_interrupted=False)


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
def _slow_rooms(heating: list[RoomResult]) -> list[RoomResult]:
    """Of the rooms that call, those with a real demand that barely rise."""
    return [
        r for r in heating
        if (r.demand or 0.0) > DEMAND_ACTIVE and r.trend_k_per_h is not None and r.trend_k_per_h < load.LAG_RATE_K_H
    ]


def given_time(mem: BoilerMemory, now_ts: float) -> bool:
    """The heating has been released for long enough that the rooms' progress says something about the flow."""
    return mem.state_since_ts is not None and now_ts - mem.state_since_ts >= LAG_MIN_HEAT_S


def steady_direction(spread: float | None, heating: list[RoomResult], lagging: bool = False) -> tuple[int, str | None]:
    """A boiler that runs through: from its settled spread and the progress of the rooms.

    `lagging` says a called room stays below target and barely rises (`boiler_load.lagging`).
    The rooms are looked at before anything lowers the curve. Review 2026-10-13: three of three
    rooms 1.2 K below target and not rising, with a spread of 3.5 K, lowered it by 4 K a day
    while the same cycles were booked as lagging; and a room 0.2 K below target that did not
    move counted as satisfied (its demand is 0.1) and lowered it by 1 K an hour.
    """
    demand = max(((r.demand or 0.0) for r in heating), default=0.0)
    slow = _slow_rooms(heating)
    if spread is not None and spread < SPREAD_LOW_K:
        # any slow room holds the curve back from going down
        return (0, None) if lagging or slow else (-1, "spread_small")
    if spread is not None and spread > SPREAD_HIGH_K and demand > DEMAND_ACTIVE:
        return 1, "spread_large"
    if demand > DEMAND_ACTIVE:
        # but only a room whose progress says something about the flow asks for more (`boiler_load.counts`)
        return (1, "rooms_slow") if any(load.counts(r) for r in slow) else (0, None)
    if demand <= 0.1 and not lagging:
        return -1, "rooms_satisfied"
    return 0, None


def cycling_direction(inp: BoilerInputs, mem: BoilerMemory) -> tuple[int, str | None]:
    """A boiler that cycles: judged by the rooms it is heating.

    A room that does not get there asks for more, one the stall rule has given up on included: it is the
    evidence. What allows less is not seen here but in the calls that end (`count_calls`).
    """
    return (1, "rooms_lagging") if given_time(mem, inp.now_ts) and load.lagging(inp.rooms) else (0, None)


def _call_not_the_flows(inp: BoilerInputs, mem: BoilerMemory, call: EndedCall, key: str | None) -> str | None:
    """Why a room's holding call still says nothing about the line `key`; None: it does."""
    burns = [end - start for start, end in mem.burns if end > call.start_ts]
    if mem.burn_since_ts is not None:
        burns.append(inp.now_ts - mem.burn_since_ts)
    if not inp.learning_allowed or mem.learning_since_ts is None or mem.learning_since_ts > call.start_ts:
        return "learning_paused"
    if mem.state != BOILER_PLAN_HEAT or mem.state_since_ts is None or mem.state_since_ts > call.start_ts + RELEASE_SLACK_S:
        return "not_released"
    if not burns or max(burns) > CYCLE_SHORT_BURN_S:
        return "not_cycling"  # no burn at all, or a boiler that ran through: there the spread judges
    if key is not None and load.room_of(key) in mem.outvoted:
        return "not_setting_flow"  # another room's line or boosts set the flow in it: it got more than its own line
    return None


def count_calls(inp: BoilerInputs, mem: BoilerMemory) -> BoilerMemory:
    """Take in the rooms' calls that ended since the last cycle: keep them, and let the brisk ones lower the curve.

    A room that holds its target swings by a tenth or two, and its trend over 90 minutes shows nothing; a rule
    on that trend only ever saw heat-ups (2026-10-23: a step down a kelvin below a target just raised, a step up
    54 minutes later). What a cycling boiler's flow does for a room shows in how fast a holding call gets it
    from three tenths below its target to it (`heat_call`, `EndedCall`). `FAST_CALLS` such calls in a row at
    `FAST_CALL_K_H` or more and within a day of each other, with the boiler released and cycling all through
    them and no other room setting the flow, take the room's line down a step. The step is a trial
    (`review_trials`): a room that lags after it gets the step back and no further one for a day. Any slower
    call, and any lagging, starts the count over; a call that says nothing about the flow leaves it as it is.

    Called every cycle with the memory of the cycle before, in which the call still ran: its load class, its
    rooms, the plan in force.
    """
    for r in inp.rooms:
        call = r.last_call
        if call is None or any(row[0] == r.key and row[1] == call.end_ts for row in mem.calls):
            continue
        if inp.now_ts - call.end_ts > load.MAX_GAP_S:
            continue  # taken in long ago and forgotten since (a reset): the room shows it until its next call ends
        key = None if mem.class_in_use is None else load.room_cell(mem.class_in_use, r.key)
        pace = call.pace_k_per_h
        verdict = call.spoiled or _call_not_the_flows(inp, mem, call, key)
        counted = verdict is None
        if counted:
            # rounded as it is kept: 20.9 - 20.7 is 0.1999999999999993, and 0.2 K in 20 minutes is brisk
            verdict = "fast" if pace is not None and round(pace, 2) >= FAST_CALL_K_H else "slow"
        mem = replace(mem, outvoted=tuple(k for k in mem.outvoted if k != r.key))  # its next call is asked anew
        starts = len([1 for start, _ in mem.burns if start >= call.start_ts])
        starts += 1 if mem.burn_since_ts is not None and mem.burn_since_ts >= call.start_ts else 0
        row = (
            r.key, call.end_ts, round((call.end_ts - call.start_ts) / 60.0, 1), round(call.end_c - call.start_c, 2),
            None if pace is None else round(pace, 2), mem.last_flow, starts, verdict,
        )
        mine = [c for c in mem.calls if c[0] == r.key]
        dropped = mine[: len(mine) - (CALLS_KEPT - 1)] if len(mine) >= CALLS_KEPT else []
        mem = replace(mem, calls=(*(c for c in mem.calls if c not in dropped), row))
        if not counted or key is None:
            continue
        line = load.line_of(mem.lines, key, mem.offset_k, mem.slope_k)
        before = line.fast_calls if _within_day(line.fast_call_ts, inp.now_ts) else 0
        fast = before + 1 if verdict == "fast" else 0
        # so many brisk calls are evidence that lasts: a found line yields to them without being asked for hours
        line = replace(line, fast_calls=fast, fast_call_ts=inp.now_ts if fast else None, found=line.found and fast < FAST_CALLS)
        mem = replace(mem, lines={**mem.lines, key: line})
        if fast < FAST_CALLS or _within_day(line.no_less_ts, inp.now_ts):
            continue
        after = apply(inp, mem, -1, "short_cycling", key, key)
        if after.adjustments != mem.adjustments:
            lowered = replace(after.lines[key], trial=(None, line.level_k, line.slope_k))
            mem = replace(after, lines={**after.lines, key: lowered})
    return mem


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


def moved_last_day(mem: BoilerMemory, now_ts: float, direction: int = 0) -> float:
    """By how much the curve was moved within the last day: up (1), down (-1) or both added up (0)."""
    steps = zip(reversed(mem.adjustments), reversed(mem.adjustment_steps), strict=False)  # paired from the newest
    return sum(
        abs(size) for t, size in steps if now_ts - t < DAY_S and (direction == 0 or (size > 0) == (direction > 0))
    )


def next_step(mem: BoilerMemory, direction: int, now_ts: float, line: load.LoadLine | None = None) -> float:
    """The size of a step in this direction now.

    Up after a step up: doubled. Against the step before: halved. Down after a step down, or
    a first step: the first step. With a load
    class's `line` the step before is the last one taken in that class: a step the bathroom
    alone took says nothing about the size of the next one for the whole flat.
    """
    first = STEP_REFINING_K if phase(mem, now_ts) == PHASE_REFINING else STEP_LEARNING_K
    if line is not None:
        last, last_ts = (line.last_step_k, line.last_step_ts) if line.last_step_ts is not None else (None, None)
    else:
        steps = mem.adjustments and mem.adjustment_steps
        last, last_ts = (mem.adjustment_steps[-1], mem.adjustments[-1]) if steps else (None, None)
    if last is None or last_ts is None or last == 0 or now_ts - last_ts > STREAK_S:
        return first
    if (last > 0) != (direction > 0):
        return max(abs(last) / 2.0, STEP_REFINING_K)
    return min(max(abs(last), STEP_REFINING_K) * 2.0, STEP_MAX_K) if direction > 0 else first


def _limited(value: float) -> float:
    """The ±10 K bound of a correction."""
    return min(max(value, -CORRECTION_LIMIT_K), CORRECTION_LIMIT_K)


def _within_day(taken_back_ts: float | None, now_ts: float) -> bool:
    """A step of a line was taken back within the last `NO_HELP_S` (`LoadLine.no_help_ts`, `no_less_ts`)."""
    return taken_back_ts is not None and now_ts - taken_back_ts < NO_HELP_S


def step_room(
    inp: BoilerInputs, mem: BoilerMemory, heating: list[RoomResult], reason: str | None, cell: str | None
) -> RoomResult | None:
    """The room a step up is taken for: of those that do not get there, the worst off that more flow may help.

    For a cycling boiler the room furthest below its target, for one that runs through the one rising the
    slowest. With a line per room, a room whose last such step was taken back (`review_trials`) is passed
    over for a day: the next room is asked, and without one no step is taken.
    """
    if reason == "rooms_lagging":
        rooms = sorted(load.lagging_rooms(inp.rooms), key=lambda r: -round(r.target - r.temperature, 2))
    else:
        rooms = sorted((r for r in _slow_rooms(heating) if load.counts(r)), key=lambda r: r.trend_k_per_h)
    if cell is None:
        return rooms[0] if rooms else None
    lines = ((r, load.line_of(mem.lines, load.room_cell(load.class_of(cell), r.key), mem.offset_k, mem.slope_k)) for r in rooms)
    return next((r for r, line in lines if not _within_day(line.no_help_ts, inp.now_ts)), None)


def review_trials(inp: BoilerInputs, mem: BoilerMemory, cell: str | None) -> BoilerMemory:
    """Judge the steps a cycling boiler's rooms were given on trial, once the trend can show what they did.

    Seen 2026-10-22: the bathroom stayed 0.9 K below target at a flow set to 40 as at 47 °C, and its line went
    on to +10.8 K. A boiler that burns a minute at a time delivers what its burner and its lockout allow,
    whatever flow is asked. So a step for a room is a trial, judged between the trend's 90 minutes and
    `TRIAL_MAX_S` after it; later nobody can tell what it did, and it stays.

    * A step **up** for a room that lagged: if the room still lags and rises no faster than it did (by
      `HELPED_K_H`), the step is taken back and none is tried for this room for `NO_HELP_S` (`held_back` says
      why). Then the flow does not matter to this room at this point of the curve, so less of it is tried at
      once: a step down, on trial as well, though never below the common correction. A line that is high only
      because more did not help comes down a step a day that way, until less begins to hurt.
    * A step **down**: if the room lags after it (after a step for brisk calls), or rises slower than it did by
      `HELPED_K_H` (after one tried for a lagging room), it is taken back, as a step up that counts for the
      waits, and this line is not lowered again for `NO_HELP_S`.

    Only the lines of the class in use are judged: with another number of rooms calling the boiler runs otherwise.
    """
    now = inp.now_ts
    lagging = {r.key: r for r in load.lagging_rooms(inp.rooms)}
    for key, line in tuple(mem.lines.items()):
        age = None if line.trial is None or line.last_step_ts is None else now - line.last_step_ts
        if cell is None or age is None or age < ADJUST_COOLDOWN_UP_S or load.class_of(key) != load.class_of(cell):
            continue
        trend, level, slope = line.trial
        room = lagging.get(load.room_of(key))
        if age >= TRIAL_MAX_S:
            mem = replace(mem, lines={**mem.lines, key: replace(line, trial=None)})
        elif not given_time(mem, now):
            continue  # released minutes ago: how the room does shows later
        elif line.last_step_k > 0:
            if room is None or trend is None or room.trend_k_per_h >= trend + HELPED_K_H:
                mem = replace(mem, lines={**mem.lines, key: replace(line, trial=None)})
                continue
            back = replace(line, level_k=level, slope_k=slope, trial=None, no_help_ts=now)
            mem = replace(mem, lines={**mem.lines, key: back}, last_adjust_reason="flow_no_help")
            above_common = correction(mem, inp.outdoor_c, key) - next_step(mem, -1, now, back) - correction(mem, inp.outdoor_c)
            if above_common < -1e-9 or _within_day(back.no_less_ts, now):
                continue
            after = apply(inp, mem, -1, "flow_less_trial", key, key)
            if after.adjustments != mem.adjustments:
                probe = replace(after.lines[key], trial=(room.trend_k_per_h, level, slope), no_help_ts=now)
                mem = replace(after, lines={**after.lines, key: probe})
        elif room is not None and (trend is None or room.trend_k_per_h < trend - HELPED_K_H):
            size = abs(line.last_step_k)
            # its own last step is none of either direction: the next one, up or down, is a first step
            back = replace(
                line, level_k=level, slope_k=slope, trial=None, no_less_ts=now, last_step_k=0.0, last_step_ts=now, fast_calls=0
            )
            mem = replace(
                mem,
                lines={**mem.lines, key: back},
                adjustments=(*mem.adjustments[-(ADJUSTMENTS_STORED - 1):], now),
                adjustment_steps=(*mem.adjustment_steps[-(ADJUSTMENTS_STORED - 1):], size),
                last_adjust_reason="flow_less_hurt",
            )
        elif trend is not None:
            # tried for a room that lagged: it does no worse, or lags no more
            mem = replace(mem, lines={**mem.lines, key: replace(line, trial=None)})
    return mem


def no_help(inp: BoilerInputs, mem: BoilerMemory, cell: str | None) -> bool:
    """A room lags behind a cycling boiler, and for every such room a step up was tried and taken back lately."""
    return cell is not None and load.lagging(inp.rooms) and step_room(inp, mem, [], "rooms_lagging", cell) is None


def _take_step(
    inp: BoilerInputs,
    mem: BoilerMemory,
    heating: list[RoomResult],
    direction: int,
    reason: str | None,
    cell: str | None,
    boosts: Mapping[str, float] | None = None,
) -> BoilerMemory:
    """A step of the line it belongs to, from the flow `cell` set.

    A step up for a room that does not get there moves that room's own line (`step_room`): the kitchen not
    getting there says nothing about the living room. Every other step moves the line that sets the flow
    (`cell`): a spread or a cycling boiler tells something about the flow that was sent, and that was this
    line's.
    """
    line_key, room = cell, None
    if cell is not None and direction > 0 and reason in ("rooms_lagging", "rooms_slow"):
        room = step_room(inp, mem, heating, reason, cell)
        if room is None:
            return mem  # more flow did not help any of them
        line_key = load.room_cell(load.class_of(cell), room.key)
    before = None if line_key is None else load.line_of(mem.lines, line_key, mem.offset_k, mem.slope_k)
    after = apply(inp, mem, direction, reason, line_key, cell, boosts)
    if reason == "rooms_lagging" and room is not None and after.adjustments != mem.adjustments:
        trial = (room.trend_k_per_h, before.level_k, before.slope_k)
        after = replace(after, lines={**after.lines, line_key: replace(after.lines[line_key], trial=trial)})
    return _capped(inp, after, mem, cell, boosts or {})


def _capped(
    inp: BoilerInputs, after: BoilerMemory, before: BoilerMemory, cell: str | None, boosts: Mapping[str, float]
) -> BoilerMemory:
    """After a step down of the line that set the flow, no room that wants heat keeps a higher one.

    The step down says that every such room got more than it needed. Kept higher, the next of
    them would set the flow at once, and the flow would never come down. What the rooms ask for
    on top of their lines counts: no room's line and boosts stay above the new flow.
    """
    if cell is None or after.adjustments == before.adjustments or (after.adjustment_steps or (0.0,))[-1] >= 0:
        return after
    group = load.class_of(cell)
    cold = cold_weight(inp.outdoor_c)
    top = correction(after, inp.outdoor_c, cell) + boosts.get(load.room_of(cell), 0.0)
    lines = dict(after.lines)
    for r in load.wanting_rooms(inp.rooms):
        own = load.room_cell(group, r.key)
        line = load.line_of(lines, own, after.offset_k, after.slope_k)
        excess = line.level_k + line.slope_k * cold + boosts.get(r.key, 0.0) - top
        if own != cell and excess > 1e-9:
            lines[own] = replace(line, level_k=round(line.level_k - excess, 3), borrowed=False)  # its own from here on
    return replace(after, lines=lines)


def _boost(boosts: Mapping[str, float], cell: str | None) -> float:
    """What the rooms ask for on top of the line `cell` while they call.

    A room's own line carries that room's boosts. The common correction is shared by all of
    them, and the room asking the most on top sets the flow
    (`boiler_control._need`): without it the flow sent and the flow the limits see would differ.
    """
    room = load.room_of(cell)
    return boosts.get(room, 0.0) if room is not None else max((0.0, *boosts.values()))


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
    Without one the common correction moves. `deciding` is the line that set
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
    planned = line_flow + _boost(extra, cell)
    setter = deciding or cell
    asked = raw + correction(mem, inp.outdoor_c, setter) + _boost(extra, setter)
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
    wanted = (from_level + share * (1.0 - cold), from_slope + share * cold)
    level, slope = _limited(wanted[0]), _limited(wanted[1])
    # Seen 2026-10-23: at the limit, the bathroom's line swallowed step after step while the learning state
    # said nothing but that the room lagged. A step the limit touches is remembered, to be shown (`held_back`).
    cut = (level, slope) != wanted
    # Rounded as it is stored. Shared between level and slope and added up again, half a kelvin comes
    # out as 0.49999999999999994 at many outdoor temperatures, and four in ten of the smallest steps
    # were thrown away as "swallowed" (review 2026-10-13).
    moved = round((level - from_level) + (slope - from_slope) * cold - below, 3)
    if abs(moved) < STEP_REFINING_K:
        # the ±10 K limit swallows it: no step is spent on what the flow at this temperature would not feel
        return replace(mem, limit_cut_ts=now) if cut else mem
    return replace(
        mem,
        offset_k=level if line is None else mem.offset_k,
        slope_k=slope if line is None else mem.slope_k,
        lines=mem.lines if line is None else {**mem.lines, cell: load.stepped(line, level, slope, moved, now)},
        adjustments=(*mem.adjustments[-(ADJUSTMENTS_STORED - 1):], now),
        adjustment_steps=(*mem.adjustment_steps[-(ADJUSTMENTS_STORED - 1):], moved),
        last_adjust_reason=reason,
        last_review_ts=now,
        samples=0,
        # a step cut short still moves up to the limit and says so; one that fits takes the reason back
        limit_cut_ts=now if cut else None,
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
    if not fresh:
        mem = review_trials(inp, mem, cell)
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


def reset(mem: BoilerMemory, rooms: Iterable[str] | None = None) -> BoilerMemory:
    """Forget what was learned; the control's own state stays. With `rooms`, only what these rooms learned:
    their lines in every load class, their heat-up extra and their calls."""
    if rooms is not None:
        gone = set(rooms)
        return replace(
            mem,
            lines={key: line for key, line in mem.lines.items() if load.room_of(key) not in gone},
            heat_up={room: state for room, state in mem.heat_up.items() if room not in gone},
            calls=tuple(call for call in mem.calls if call[0] not in gone),
        )
    return replace(
        mem, offset_k=0.0, slope_k=0.0, lines={}, adjustments=(), adjustment_steps=(), last_adjust_reason=None,
        limit_cut_ts=None, spread_ema=None, samples=0, last_review_ts=None, heat_up={}, calls=(),
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
    """A step that is due but is not taken in full: the flow at its minimum, more of it no help, or the correction at its limit.

    The rooms' brisk calls ask for less (`count_calls`); or a room does not get there while the flow the rooms ask for (`asked`)
    lies below the minimum, where the learning takes no step up (`apply`), or after a step up that
    left it rising no faster and was taken back (`no_help`). These are what holds back now, so they come first.
    Otherwise a step within the last `LIMIT_SHOWN_S` met the ±10 K limit of a correction, which
    swallowed it or cut it short (`apply`). That is remembered once for all lines: it says the last
    step tried met the limit, whichever line that was (`curve_by_load` shows which).
    """
    if asked is not None and asked < inp.params.boiler_flow_min and load.lagging(inp.rooms):
        return "rooms_lagging_at_flow_min"
    line = None if cell is None else load.line_of(mem.lines, cell, mem.offset_k, mem.slope_k)
    if line is not None and line.fast_calls >= FAST_CALLS and _within_day(line.fast_call_ts, inp.now_ts):
        planned = curve_flow(inp.outdoor_c, inp.params, limited=False) + correction(mem, inp.outdoor_c, cell)
        if planned <= inp.params.boiler_flow_min:
            return "short_cycling_at_flow_min"
    direction = cycling_direction(inp, mem)[0] if short_cycling(mem, inp.now_ts) else 0
    if direction > 0 and no_help(inp, mem, cell):
        return "flow_no_help"
    if mem.limit_cut_ts is not None and inp.now_ts - mem.limit_cut_ts < LIMIT_SHOWN_S:
        return "correction_limit"
    return None
