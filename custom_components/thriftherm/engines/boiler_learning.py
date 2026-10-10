"""What the boiler controller learns about the heating curve, and from what.

Pure logic. The control (`boiler_control`) calls three things each cycle: `track_burns`
with the burner sign, `learn` while the heating is released, and `status` for the display.

What is learned
* Two numbers that correct the configured curve: its **level** (added at every outdoor
  temperature) and its **slope** (added in full at -10 °C, not at all at +15 °C). A step
  taken on a mild day mostly moves the level, one taken in the cold mostly the slope, as a
  heating engineer sets a curve by hand: too cold only in winter means steeper, too cold in
  between means higher.

From what
* **The burner**, from the best sign the installation has: a gas meter, else the boiler's
  status code, else its pump state. Burns for hot water never count.
* **A boiler that runs through** gives a settled flow/return spread: small means the flow
  is hotter than the radiators take, large with demand means too cold.
* **A boiler that cycles** never gives a settled spread; what it delivers shows in the
  rooms alone. A room that is called for and does not get there asks for more, and only
  rooms warming up briskly allow less.

Slow on purpose: evidence only after the first 10 minutes of a burner run, a 20 minute
review window, one step per hour and four per day, each correction within ±10 K, and never
beyond what the flow limits would swallow. After six steps or a quiet day the steps shrink
from 1 K ("learning") to 0.5 K ("refining").
"""

from __future__ import annotations

from dataclasses import replace
from statistics import median

from ..const import BOILER_PLAN_HEAT, BOILER_REPORTS_HEATING, BOILER_REPORTS_HOT_WATER, CTRL_OFF
from ..models import RoomResult
from .boiler_memory import CORRECTION_LIMIT_K, BoilerInputs, BoilerMemory, cold_weight, correction, curve_flow
from .heat_call import CALL_CALLING, CALL_STALLED

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
SLOW_RATE_K_H = 0.2
DEMAND_ACTIVE = 0.3

# --- a boiler that cycles
# Measured 2026-10-10 with the flow minimum lowered from 45 to 35 °C: burner runs fell from 62 s
# to 41 s, the pause stayed at the boiler's lockout (15 min, and the lockout grows as the flow
# setpoint falls), and the bathroom hung 0.2-0.3 K below target for 14 hours. Less flow is less
# heat there, not fewer starts.
LAG_DEFICIT_K = 0.2  # a called room still this far below target
LAG_MIN_HEAT_S = 1800.0  # after the heating has been released this long
BRISK_RATE_K_H = 0.5  # every called room rises at least this fast: the curve may come down

# --- the steps
ADJUST_COOLDOWN_S = 3600.0
ADJUST_MAX_PER_DAY = 4
DAY_S = 24 * 3600.0
STEP_LEARNING_K = 1.0
STEP_REFINING_K = 0.5
REFINE_AFTER_ADJUSTMENTS = 6

PHASE_LEARNING = "learning"
PHASE_REFINING = "refining"
PHASE_PAUSED = "paused"


# ---------------------------------------------------------------------------
# The burner
# ---------------------------------------------------------------------------
def flame(inp: BoilerInputs) -> bool:
    """Is the burner on for the heating, by the best sign this installation has.

    A gas meter sees the flame itself, the status code is the boiler's own word, the pump
    state runs on for minutes after the flame. None of them is required. What the boiler
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


def short_cycling(mem: BoilerMemory, now_ts: float) -> bool:
    """Several short burner runs within the last hour (a run still going is not judged yet)."""
    since = now_ts - BURNS_KEPT_S
    finished = [end - start for start, end in mem.burns if start >= since]
    starts = len(finished) + (1 if mem.burn_since_ts is not None and mem.burn_since_ts >= since else 0)
    return starts >= CYCLE_MIN_STARTS and bool(finished) and median(finished) <= CYCLE_SHORT_BURN_S


# ---------------------------------------------------------------------------
# Which way the curve should move
# ---------------------------------------------------------------------------
def steady_direction(spread: float | None, heating: list[RoomResult]) -> tuple[int, str | None]:
    """A boiler that runs through: from its settled spread and the progress of the rooms."""
    demand = max(((r.demand or 0.0) for r in heating), default=0.0)
    if spread is not None and spread < SPREAD_LOW_K:
        return -1, "spread_small"
    if spread is not None and spread > SPREAD_HIGH_K and demand > DEMAND_ACTIVE:
        return 1, "spread_large"
    demanding = [r for r in heating if (r.demand or 0.0) > DEMAND_ACTIVE]
    if demanding:
        known = [r.trend_k_per_h for r in demanding if r.trend_k_per_h is not None]
        if known and min(known) < SLOW_RATE_K_H:
            return 1, "rooms_slow"
        return 0, None
    if demand <= 0.1:
        return -1, "rooms_satisfied"
    return 0, None


def cycling_direction(inp: BoilerInputs, mem: BoilerMemory) -> tuple[int, str | None]:
    """A boiler that cycles: judged by the rooms it is heating.

    A room the stall rule has given up on still counts as lagging: it is the evidence.
    """
    called = [
        r
        for r in inp.rooms
        if r.heating_allowed and r.temperature is not None and r.boiler_call in (None, CALL_CALLING, CALL_STALLED)
    ]
    released_for = 0.0 if mem.state_since_ts is None else inp.now_ts - mem.state_since_ts
    lagging = [
        r
        for r in called
        if r.target - r.temperature >= LAG_DEFICIT_K and r.trend_k_per_h is not None and r.trend_k_per_h < SLOW_RATE_K_H
    ]
    if lagging and released_for >= LAG_MIN_HEAT_S:
        return 1, "rooms_lagging"
    calling = [r for r in called if r.boiler_call != CALL_STALLED]
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


def _limited(value: float) -> float:
    return min(max(value, -CORRECTION_LIMIT_K), CORRECTION_LIMIT_K)


def apply(inp: BoilerInputs, mem: BoilerMemory, direction: int, reason: str | None) -> BoilerMemory:
    """Move the curve by one step at the current outdoor temperature, unless a guard says no.

    The step is shared between level and slope by where the outdoor temperature lies between
    the curve's points, scaled so that the flow at this temperature moves by the full step.
    """
    now = inp.now_ts
    p = inp.params
    # anti-windup: a correction the flow limits already swallow must not keep growing
    planned = curve_flow(inp.outdoor_c, p) + correction(mem, inp.outdoor_c)
    if (direction > 0 and planned >= p.boiler_flow_max) or (direction < 0 and planned <= p.boiler_flow_min):
        return mem
    if mem.adjustments and now - mem.adjustments[-1] < ADJUST_COOLDOWN_S:
        return mem
    if adjustments_last_day(mem, now) >= ADJUST_MAX_PER_DAY:
        return mem

    step = direction * (STEP_REFINING_K if phase(mem, now) == PHASE_REFINING else STEP_LEARNING_K)
    cold = cold_weight(inp.outdoor_c)
    share = step / (1.0 - cold + cold * cold)
    level = _limited(mem.offset_k + share * (1.0 - cold))
    slope = _limited(mem.slope_k + share * cold)
    moved = (level - mem.offset_k) + (slope - mem.slope_k) * cold
    if abs(moved) < abs(step) / 2.0:
        # a limit swallows it: no step is spent on what the flow at this temperature would not feel
        return mem
    return replace(
        mem,
        offset_k=level,
        slope_k=slope,
        adjustments=(*mem.adjustments[-9:], now),
        last_adjust_reason=reason,
        last_review_ts=now,
        samples=0,
    )


def learn(inp: BoilerInputs, mem: BoilerMemory, heating: list[RoomResult], burning: bool) -> BoilerMemory:
    """One cycle of learning while the heating is released; `burning` is the heating flame now."""
    now = inp.now_ts
    if not inp.learning_allowed:
        return mem
    if short_cycling(mem, now):
        direction, reason = cycling_direction(inp, mem)
        if direction:
            moved = apply(inp, mem, direction, reason)
            if moved is not mem:
                return moved
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

    direction, reason = steady_direction(ema, heating)
    if direction == 0:
        return replace(mem, last_review_ts=now)
    moved = apply(inp, mem, direction, reason)
    return moved if moved is not mem else replace(mem, last_review_ts=now)


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
        mem, offset_k=0.0, slope_k=0.0, adjustments=(), last_adjust_reason=None, spread_ema=None, samples=0, last_review_ts=None
    )


# ---------------------------------------------------------------------------
# For the display
# ---------------------------------------------------------------------------
def shown_phase(inp: BoilerInputs, mem: BoilerMemory, burning: bool) -> str:
    """Learning goes on while the burner heats, or while the boiler cycles through a released heating."""
    released = mem.state == BOILER_PLAN_HEAT and inp.control_mode != CTRL_OFF
    at_work = burning or (released and short_cycling(mem, inp.now_ts))
    return phase(mem, inp.now_ts) if (inp.learning_allowed and at_work) else PHASE_PAUSED


def held_back(inp: BoilerInputs, mem: BoilerMemory) -> str | None:
    """A step that is due but cannot be taken: cycling asks for less, and the flow is at its minimum."""
    if not short_cycling(mem, inp.now_ts) or cycling_direction(inp, mem)[0] >= 0:
        return None
    at_minimum = curve_flow(inp.outdoor_c, inp.params) + correction(mem, inp.outdoor_c) <= inp.params.boiler_flow_min
    return "short_cycling_at_flow_min" if at_minimum else None
