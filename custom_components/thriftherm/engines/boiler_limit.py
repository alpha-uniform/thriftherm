"""The boiler's own limit (19.11.2026): pure logic, no Home Assistant import.

A boiler whose least power is far more than its radiators take reaches the flow it is sent within a minute or two, runs
its pump on and waits out its lockout: on average far less arrives than was sent (measured 18./19.11., the bathroom
alone: 43 °C sent, 28-34 °C on average, burns of 94 s, a lockout of 7.5 minutes, the bathroom falling 0.2 K an hour).
Then the curve is not too low, and lifting it teaches it the boiler's cycling. What helps is more sent: the burns grow
longer, and the boiler's lockout shorter (measured 16./17.11.: off 13.8 min at 40 °C, 11.8 at 43, 9.3 at 49).

`limited` tells that case: two short burns in the hour at least (LIMIT_MIN_BURNS), half an hour of heating on average
LIMIT_GAP_K or more below the flow sent. `compensate` then adds COMP_UP_K above the curve once the last step has stood
COMP_UP_S (long enough to show, and to be judged) while a room is short of the pace it is planned with (`verdict`),
and takes a step back once all rise with room to spare, but no lower than a room fell short at after a step down
(decision 19.11. 02:10); never above `most_c`, lower for a condensing boiler, whose efficiency falls with a hot return.
The ramp of the flow sent (boiler_control) applies on top.

What the rooms rose reliably with is learned for good, per room's line (as the curve's, by load class) and outdoor
band (`CompLine`): a heating begins with it instead of climbing from 0 every day, or soon after the last one a step
below what that ended with (COMP_CARRY_S). A phase is a heating of one line:
its value is the least the rooms rose reliably with, more than the most they fell short at (`phase_value`), each judged
settled. One phase moves nothing; two that agree beyond COMP_HYST_K move it by what both say, COMP_SHIFT_MAX_K at most.

The curve's learning judges its own way (boiler_load.lagging_rooms): a target set by hand still being heated up to and
a heat-up on plan say nothing about the curve, which is for holding. For the compensation they count, against their
plan: tonight (19.11.) the bathroom set to 21.0 at 19.7 was not lagging for the curve, and nothing was added.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from statistics import median

from ..const import BOILER_PLAN_HEAT
from ..models import Parameters, RoomResult
from . import boiler_load as load
from .boiler_learning import BURNER_HOUR_S, CYCLE_SHORT_BURN_S
from .boiler_memory import BAND_MARK, COMP_MOST_K, COMP_PHASES_KEPT, BoilerMemory, CompLine
from .heat_call import CALL_CALLING, CALL_STALLED
from .room import PLAN_RATE_MIN_K_H, TREND_WINDOW_S

FLOW_MEAN_TAU_S = 1800.0  # the flow measured, damped over half an hour
LIMIT_SETTLED_S = 1800.0  # heating this long before its mean says anything
LIMIT_GAP_K = 8.0  # on average this far below the flow sent: the boiler's limit, not the curve's
# Two short burns in the hour: with d.02 = 30 min and 8 kW the boiler started 2.7 times an hour in the model, too few for
# the cycling the curve's learning counts (three), still at its limit
LIMIT_MIN_BURNS = 2
COMP_STEP_K = 1.0  # a step down, and back up after one
COMP_SETTLE_S = 2700.0  # this long after a step a judgement is evidence of the phase
# Up 2 K once the last step has stood as long as its judgement needs (19.11.2026: 1 K every quarter of an hour, judged by
# a trend over 90 minutes, rode from 1 to 12 K while the bathroom rose from about 8 on, and learned the overshoot)
COMP_UP_K = 2.0
COMP_UP_S = COMP_SETTLE_S
COMP_MAX_CONDENSING_C = 55.0  # the most sent to a condensing boiler at its limit
# A step back once all rooms rise this much faster than planned, this long on end, and this long after the last step:
# the trend looks back 90 minutes, a step needs its time to show
COMP_MARGIN_K_H = 0.1
COMP_AMPLE_S = 1800.0
COMP_DOWN_S = 2700.0
# A heating this soon after the last, in the same outdoor band, begins a step below what that one ended with (19.11.2026
# 16:12: 12 K to 0 for a block of half an hour, and hours of steps back up)
COMP_CARRY_S = 7200.0
COMP_HYST_K = 1.0  # phases within this of what was learned move nothing
COMP_SHIFT_MAX_K = 2.0  # the most two phases move it by
BAND_K = 5  # outdoor bands of 5 K, from -10 to +15 °C
BAND_MIN, BAND_MAX = -10, 15


def follow_mean(
    mean: float | None, mean_ts: float | None, flow_c: float | None, now_ts: float
) -> tuple[float | None, float | None]:
    """The flow measured, damped over FLOW_MEAN_TAU_S (time weighted); unchanged without a reading."""
    if flow_c is None:
        return mean, mean_ts
    if mean is None or mean_ts is None:
        return flow_c, now_ts
    weight = 1.0 - math.exp(-max(now_ts - mean_ts, 0.0) / FLOW_MEAN_TAU_S)
    return round(mean + weight * (flow_c - mean), 2), now_ts


def limited(mem: BoilerMemory, now_ts: float) -> bool:
    """The boiler at its own limit: heating for LIMIT_SETTLED_S, cycling short, on average LIMIT_GAP_K below the flow sent."""
    if mem.state != BOILER_PLAN_HEAT or mem.state_since_ts is None or now_ts - mem.state_since_ts < LIMIT_SETTLED_S:
        return False
    if mem.flow_mean_c is None or mem.last_flow is None:
        return False
    burns = [end - start for start, end in mem.burns if start >= now_ts - BURNER_HOUR_S]
    if len(burns) < LIMIT_MIN_BURNS or median(burns) > CYCLE_SHORT_BURN_S:
        return False
    return mem.last_flow - mem.flow_mean_c >= LIMIT_GAP_K - 1e-9


def judged_rooms(rooms: tuple[RoomResult, ...], lag_k: float) -> list[tuple[RoomResult, float, float]]:
    """The calling rooms below target whose rise says something about the flow, each with its pace and the pace it
    needs: a heat-up (a preheat, a raised target, a target set by hand) the pace it is planned with, PLAN_RATE_MIN_K_H at
    least; holding, LAG_RATE_K_H. A preheat by its pace since it began, else the trend.

    Not judged: a quick heat-up (its own extra), a room whose thermostat shuts its valve or whose valve cannot open or was open in part by
    the flow within the last half hour (load.valve_limited), one
    calling for less than LAG_MIN_CALL_S, a target moved within the trend's window, a silent sensor."""
    judged = []
    for r in rooms:
        if not r.heating_allowed or r.boost_active or r.thermostat_heating is False or r.temperature is None:
            continue
        if r.valve_unable is not None or load.valve_limited(r) or r.boiler_call not in (None, CALL_CALLING, CALL_STALLED):
            continue
        if round(r.target - r.temperature, 2) < lag_k or (r.calling_s is not None and r.calling_s < load.LAG_MIN_CALL_S):
            continue
        if r.temperature_age_s is not None and r.temperature_age_s > TREND_WINDOW_S:
            continue
        preheat = r.preheat_for_ts is not None and r.preheat_pace_k_per_h is not None
        if not preheat and r.target_steady_s is not None and r.target_steady_s < TREND_WINDOW_S:
            continue
        pace = r.preheat_pace_k_per_h if preheat else r.trend_k_per_h
        if pace is None:
            continue
        if load.heats_up(r):
            need = max(r.heat_rate_k_h if r.heat_rate_k_h is not None else PLAN_RATE_MIN_K_H, PLAN_RATE_MIN_K_H)
        else:
            need = load.LAG_RATE_K_H
        judged.append((r, pace, need))
    return judged


def verdict(rooms: tuple[RoomResult, ...], lag_k: float) -> tuple[bool, bool]:
    """(short, ample): a judged room rises slower than it needs; all rise COMP_MARGIN_K_H faster at least."""
    judged = judged_rooms(rooms, lag_k)
    if not judged:
        return False, False
    short = any(pace < need - 1e-9 for _, pace, need in judged)
    return short, not short and all(pace >= need + COMP_MARGIN_K_H - 1e-9 for _, pace, need in judged)


def compensate(
    mem: BoilerMemory, at_limit: bool, short: bool, ample: bool, now_ts: float, room_k: float
) -> BoilerMemory:
    """Up COMP_UP_K while the boiler is at its limit and a room is short, once the last step has stood COMP_UP_S (back up
    after a step down, COMP_STEP_K: to where it was short no more); down COMP_STEP_K once all
    rooms have risen with room to spare for COMP_AMPLE_S and the last step is COMP_DOWN_S ago, never below the floor:
    what a room fell short at after a step down, plus that step. Otherwise it holds. `room_k` is how much more the most
    allowed leaves."""
    ample_ts = (mem.comp_ample_ts if mem.comp_ample_ts is not None else now_ts) if ample else None
    if ample_ts != mem.comp_ample_ts:
        mem = replace(mem, comp_ample_ts=ample_ts)
    since = None if mem.comp_ts is None else now_ts - mem.comp_ts
    if at_limit and short:
        if since is not None and since < COMP_UP_S - 1e-6:
            return mem
        up = min(COMP_STEP_K if mem.comp_last_up is False else COMP_UP_K, max(room_k, 0.0))
        if up <= 0.0:
            return mem
        value = round(mem.comp_k + up, 1)
        floor = value if mem.comp_last_up is False else mem.comp_floor_k  # back after a step down: no lower again
        return replace(mem, comp_k=value, comp_ts=now_ts, comp_last_up=True, comp_floor_k=floor)
    floor = mem.comp_floor_k or 0.0
    if (
        ample_ts is not None and now_ts - ample_ts >= COMP_AMPLE_S - 1e-6
        and (since is None or since >= COMP_DOWN_S - 1e-6) and mem.comp_k > floor + 1e-9
    ):
        value = round(max(mem.comp_k - COMP_STEP_K, floor), 1)
        return replace(mem, comp_k=value, comp_ts=now_ts, comp_last_up=False, comp_ample_ts=None)
    return mem


def observe(
    mem: BoilerMemory, at_limit: bool, short: bool, ample: bool, now_ts: float, judged: bool = True
) -> BoilerMemory:
    """The evidence of the phase, once the compensation has stood COMP_SETTLE_S: the least the rooms rose as planned
    with; the most one fell short at while the boiler was at its limit (not at it, that is the curve's)."""
    if not judged or (mem.comp_ts is not None and now_ts - mem.comp_ts < COMP_SETTLE_S - 1e-6):
        return mem
    if short:
        if not at_limit:
            return mem
        most = mem.comp_k if mem.comp_short_k is None else max(mem.comp_short_k, mem.comp_k)
        return replace(mem, comp_short_k=most) if most != mem.comp_short_k else mem
    least = mem.comp_k if mem.comp_ok_k is None else min(mem.comp_ok_k, mem.comp_k)
    return replace(mem, comp_ok_k=least) if least != mem.comp_ok_k else mem


def phase_value(ok_k: float | None, short_k: float | None) -> float | None:
    """What a phase says it needs: the least the rooms rose reliably with, more than the most they fell short at."""
    if short_k is None:
        return ok_k
    more = round(short_k + COMP_STEP_K, 1)
    return more if ok_k is None else max(ok_k, more)


def record(line: CompLine, value: float) -> CompLine:
    """A phase's value: one moves nothing; the last two beyond COMP_HYST_K on the same side move what was learned by
    what both say, COMP_SHIFT_MAX_K at most, and the phases start afresh."""
    value = min(max(value, 0.0), COMP_MOST_K)
    phases = (*line.phases, value)[-COMP_PHASES_KEPT:]
    if len(phases) >= 2:
        a, b = (v - line.value_k for v in phases[-2:])
        if (a > COMP_HYST_K and b > COMP_HYST_K) or (a < -COMP_HYST_K and b < -COMP_HYST_K):
            shift = min(abs(a), abs(b), COMP_SHIFT_MAX_K)
            moved = line.value_k + (shift if a > 0 else -shift)
            return CompLine(value_k=round(min(max(moved, 0.0), COMP_MOST_K), 1), phases=())
    return replace(line, phases=phases)


def band(outdoor_c: float | None) -> int | None:
    """The outdoor band, BAND_K wide, from BAND_MIN to BAND_MAX; None without an outdoor temperature."""
    if outdoor_c is None or not math.isfinite(outdoor_c):
        return None
    return int(min(max(math.floor(outdoor_c / BAND_K) * BAND_K, BAND_MIN), BAND_MAX))


def key(cell: str | None, outdoor_c: float | None) -> str | None:
    """What is learned for a room's line in an outdoor band; None while no room has called (the common correction)."""
    if cell is None or load.room_of(cell) is None:
        return None
    b = band(outdoor_c)
    return cell if b is None else f"{cell}{BAND_MARK}{b}"


def learned(lines: Mapping[str, CompLine], at: str | None) -> float | None:
    """What a heating of this line in this band begins with: its own, else the next band's (the colder of two)."""
    if at is None:
        return None
    if at in lines:
        return lines[at].value_k
    cell, _, b = at.partition(BAND_MARK)
    if not b:
        return None
    for other in (int(b) - BAND_K, int(b) + BAND_K):
        if (near := lines.get(f"{cell}{BAND_MARK}{other}")) is not None:
            return near.value_k
    return None


_PHASE_OVER = {"comp_ok_k": None, "comp_short_k": None, "comp_floor_k": None, "comp_ample_ts": None, "comp_last_up": None}


def _close(mem: BoilerMemory) -> BoilerMemory:
    """The phase in progress ends: its value goes to its line."""
    value = phase_value(mem.comp_ok_k, mem.comp_short_k)
    lines = mem.comp_lines
    if mem.comp_key is not None and value is not None:
        lines = {**lines, mem.comp_key: record(lines.get(mem.comp_key, CompLine()), value)}
    return replace(mem, comp_lines=lines, comp_key=None, **_PHASE_OVER)


def begin(mem: BoilerMemory, at: str | None, now_ts: float, start_learned: bool = True) -> BoilerMemory:
    """A heating of another line or band (or the first): the phase before ends, and the compensation begins with what
    was learned for this one. Without, a heating after a block begins at 0, or within COMP_CARRY_S of the last in the
    same band a step below what that one ended with: the boiler's limit did not change in the block. A change within
    one keeps what it sends and when it stepped, more only if the new line learned more: the boiler's limit is the same
    whichever room sets the flow (19.11.2026 06:42: the kitchen calling along took the bathroom's 2 K to the 0 of its
    other line). Frost alone does not begin with it: its floor is enough, no gas for the comfort learned."""
    if at == mem.comp_key:
        return mem
    fresh = mem.comp_key is None
    mem = replace(_close(mem), comp_key=at)
    value = learned(mem.comp_lines, at) if start_learned else None
    if fresh:
        carry = _carried(mem, at, now_ts) if start_learned else None
        mem = replace(mem, comp_left_k=0.0, comp_left_ts=None, comp_left_key=None)
        if carry is not None and (value is None or carry > value):
            value = carry
    if value is None:
        return replace(mem, comp_k=0.0, comp_ts=None) if fresh and mem.comp_k else mem
    if not fresh and value <= mem.comp_k:
        return mem
    return replace(mem, comp_k=value, comp_ts=now_ts) if value != mem.comp_k else mem


def _band_of(key: str | None) -> str | None:
    return None if key is None or BAND_MARK not in key else key.split(BAND_MARK)[1]


def _carried(mem: BoilerMemory, at: str | None, now_ts: float) -> float | None:
    """A step below what the last heating ended with, if it ended within COMP_CARRY_S in the same outdoor band."""
    if mem.comp_left_k <= 0.0 or mem.comp_left_ts is None or not 0.0 <= now_ts - mem.comp_left_ts <= COMP_CARRY_S:
        return None
    if at is None or _band_of(at) != _band_of(mem.comp_left_key):
        return None
    carry = round(mem.comp_left_k - COMP_STEP_K, 1)
    return carry if carry > 0.0 else None


def end(mem: BoilerMemory, now_ts: float) -> BoilerMemory:
    """The heating ends (a block): its phase goes to its line, nothing is sent above the curve; what was sent is
    remembered for a heating soon after."""
    if mem.comp_k > 0.0:
        mem = replace(mem, comp_left_k=mem.comp_k, comp_left_ts=now_ts, comp_left_key=mem.comp_key)
    mem = _close(mem)
    return replace(mem, comp_k=0.0, comp_ts=None) if mem.comp_k or mem.comp_ts is not None else mem


def summary(lines: Mapping[str, CompLine]) -> dict[str, float] | None:
    """What was learned, for the display: the lines that add something."""
    return {k: line.value_k for k, line in sorted(lines.items()) if line.value_k > 0.0} or None


def most_c(params: Parameters) -> float:
    """The most the flow sent may reach with the compensation: lower for a condensing boiler."""
    return min(COMP_MAX_CONDENSING_C, params.boiler_flow_max) if params.boiler_condensing else params.boiler_flow_max
