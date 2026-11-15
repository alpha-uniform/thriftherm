"""The cooling target of a room served by the heat pump (docs/PHASE12_COOLING.md §3).

Pure logic, no Home Assistant import. Kept apart from engines/room.py on purpose: the heating side reads its
schedules as "comfort above setback", and cooling wants the lower value in its blocks. Nothing here touches the
heating target, and a cooling target never reaches a room thermostat (a setpoint there is a heating command).

Order, the first that applies:
    mode off or boiler only (no cooling) → override → away (pre-cooling for the return) → cooling schedule
    (pre-cooling for the next block); without a schedule the comfort holds all day (decided 2026-11-12).
    Cheap power then lowers every target but an override (§20.1), never one that means "do not cool".
The floor applies to every result: no room is cooled below it. A result of 32 °C is no target: the room is not cooled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..const import MODE_AWAY, MODE_BOILER_ONLY, MODE_OFF
from ..models import Override, Week
from .schedule_week import block_running, level_at, next_block_start

# Without a learned rate (stage 2, §20.3: from three episodes): half a degree an hour, as a heat pump room heats at
# its least speed.
COOL_RATE_K_H = 0.5
PRECOOL_LEAD_MAX_S = 4 * 3600.0  # as PREHEAT_LEAD_MAX_HEAT_PUMP_S: at most four hours ahead
PRECOOL_HORIZON_S = 12 * 3600.0  # a block further ahead than this is not looked at
# A target this high means "do not cool": the top of "outside the schedule" and of "away", and "off" on the room
# climate. Taken as a target, a room at 33 °C under a roof started the unit though it was switched off (review 2026-11-13).
NO_COOLING_C = 32.0

REASON_OFF = "cool_off"
REASON_OVERRIDE = "cool_override"
REASON_AWAY = "cool_away"
REASON_PRECOOL = "cool_precool"
REASON_COMFORT = "cool_schedule_comfort"
REASON_OUTSIDE = "cool_schedule_outside"
REASON_CHEAP_POWER = "cool_cheap_power"


@dataclass(frozen=True)
class CoolSchedule:
    """A room's cooling schedule helper: its week where it tells its blocks, else its state and next start."""

    week: Week | None = None
    active: bool = False
    temperature: float | None = None  # the running block's own temperature
    next_start: datetime | None = None  # where it does not tell its blocks, the coming one's temperature is not known


@dataclass(frozen=True)
class CoolTargetInputs:
    now: datetime
    mode: str
    temperature: float | None
    comfort: float
    outside: float  # outside the schedule's blocks
    away: float
    floor: float
    schedule: CoolSchedule | None = None
    override: Override | None = None
    away_return_ts: float | None = None
    away_provisional: bool = False
    latch_ts: float | None = None  # the time a begun pre-cool is for (a block start or the return)
    margin_s: float = 1200.0
    cheap_k: float = 0.0  # cheap power: the targets this much lower (docs/PHASE12_COOLING.md §20.1), 0 without
    rate_k_h: float | None = None  # how fast the unit cools the room, as learned (§20.3); None: COOL_RATE_K_H


@dataclass(frozen=True)
class CoolTarget:
    target: float | None  # None: the room is not to be cooled
    reason: str
    precool_for_ts: float | None = None  # the block start or return a pre-cool is for, while it runs
    precool_start_ts: float | None = None  # when a pre-cool for what comes next begins


def precool_start(
    for_ts: float, aim: float, temperature: float | None, margin_s: float, rate_k_h: float | None = None
) -> float:
    """When cooling must begin to reach `aim` at `for_ts`, at most `PRECOOL_LEAD_MAX_S` ahead."""
    lead = margin_s
    if temperature is not None:
        lead += max(temperature - aim, 0.0) / (rate_k_h or COOL_RATE_K_H) * 3600.0
    return for_ts - min(lead, PRECOOL_LEAD_MAX_S)


def _level(inp: CoolTargetInputs, at: datetime) -> tuple[float, str]:
    """What the schedule asks at `at`: the comfort (or the block's temperature) in a block, else the outside target."""
    sched = inp.schedule
    if sched is None:
        return inp.comfort, REASON_COMFORT
    if sched.week is not None:
        if block_running(sched.week, at) is None:
            return inp.outside, REASON_OUTSIDE
        return level_at(sched.week, at, inp.comfort, inp.outside)[0], REASON_COMFORT
    if sched.active:
        return (inp.comfort if sched.temperature is None else sched.temperature), REASON_COMFORT
    return inp.outside, REASON_OUTSIDE


def _next_block(inp: CoolTargetInputs) -> tuple[datetime, float] | None:
    sched = inp.schedule
    if sched is None:
        return None
    if sched.week is not None:
        return next_block_start(sched.week, inp.now, inp.comfort)
    if sched.active or sched.next_start is None:
        return None
    return sched.next_start, inp.comfort


def _latched(inp: CoolTargetInputs, for_ts: float) -> bool:
    return inp.latch_ts is not None and abs(inp.latch_ts - for_ts) < 1.0


def _precool(inp: CoolTargetInputs, for_ts: float, aim: float) -> tuple[bool, float | None]:
    """Whether the pre-cool for `for_ts` runs now, and when it begins. Without a reading none begins, a begun one goes on."""
    if inp.temperature is None:
        return _latched(inp, for_ts), None
    start = precool_start(for_ts, aim, inp.temperature, inp.margin_s, inp.rate_k_h)
    return inp.now.timestamp() >= start or _latched(inp, for_ts), start


def _bounded(target: float, inp: CoolTargetInputs) -> float:
    return max(target, inp.floor)


def cooling_target(inp: CoolTargetInputs) -> CoolTarget:
    aim = _target(inp)
    if aim.target is not None and aim.target >= NO_COOLING_C:
        return CoolTarget(None, REASON_OFF, precool_start_ts=aim.precool_start_ts)
    if aim.target is None or inp.cheap_k <= 0.0 or aim.reason == REASON_OVERRIDE:
        return aim  # by hand is by hand: who asks for 26 °C gets 26 °C
    lowered = _bounded(aim.target - inp.cheap_k, inp)
    if lowered >= aim.target:
        return aim  # at the floor already
    return CoolTarget(lowered, REASON_CHEAP_POWER, aim.precool_for_ts, aim.precool_start_ts)


def _target(inp: CoolTargetInputs) -> CoolTarget:
    now_ts = inp.now.timestamp()
    if inp.mode in (MODE_OFF, MODE_BOILER_ONLY):
        return CoolTarget(None, REASON_OFF)
    if inp.override is not None and inp.override.until_ts > now_ts:
        return CoolTarget(_bounded(inp.override.target, inp), REASON_OVERRIDE)

    if inp.mode == MODE_AWAY:
        ret = inp.away_return_ts
        if ret is None or ret <= now_ts or inp.away_provisional:
            return CoolTarget(_bounded(inp.away, inp), REASON_AWAY)
        back = datetime.fromtimestamp(ret, inp.now.tzinfo)
        aim = min(_level(inp, back)[0], inp.away)
        running, start = _precool(inp, ret, aim)
        if running:
            return CoolTarget(_bounded(aim, inp), REASON_PRECOOL, precool_for_ts=ret, precool_start_ts=start)
        return CoolTarget(_bounded(inp.away, inp), REASON_AWAY, precool_start_ts=start)

    level, reason = _level(inp, inp.now)
    coming = _next_block(inp)
    if reason == REASON_OUTSIDE and coming is not None:
        begins, aim = coming
        for_ts = begins.timestamp()
        if aim < level and for_ts - now_ts <= PRECOOL_HORIZON_S:
            running, start = _precool(inp, for_ts, aim)
            if running:
                return CoolTarget(_bounded(aim, inp), REASON_PRECOOL, precool_for_ts=for_ts, precool_start_ts=start)
            return CoolTarget(_bounded(level, inp), reason, precool_start_ts=start)
    return CoolTarget(_bounded(level, inp), reason)
