"""Boiler controller: flow temperature and heating release via ebusd SetMode.

Pure logic, no Home Assistant import. Like the Midea controller it runs in
shadow mode (plan published and logged) or active mode (SetMode sent).

Principles
* SetMode is the only operational lever of the atmoTEC without a controller.
  It has to be repeated; without it the boiler returns to its front knob after
  9-16 minutes (measured). We resend every 2 minutes, so a lost telegram is
  harmless and a dead Home Assistant falls back to the knob.
* Hands off (nothing sent) when the boiler data is not trustworthy or the
  safety state is "fallback": the boiler then runs on its knob.
* Heating is released only when a room needs the boiler (advisor: boiler or
  both, or frost protection). Otherwise heating is blocked with disablehc, so
  the boiler stops keeping the circuit hot. Hot water fields are always sent
  with fixed, configured values and are never disabled.
* Flow temperature = heating curve (two points, -10 °C and +15 °C outdoor)
  + learned offset + demand boost, limited to the configured range and ramped
  up by at most 5 °C per 10 minutes.

Learning the offset (slow on purpose)
* Two signals: the flow/return spread while the boiler heats, and rooms that
  barely warm up although they ask for heat.
* Guards so the loop stays calm and never fights Better Thermostat, which
  calibrates its valves on a scale of minutes: samples only after the first
  10 minutes of a burner run, at least 10 samples, a dead band between 5 and
  15 °C spread, one adjustment per hour at most, at most four per day, and a
  pause whenever a setpoint was just changed, a boost or the drying mode runs,
  or the safety state is not ok.
* After about six adjustments or a quiet day the controller switches from
  "learning" (1 °C steps) to "refining" (0.5 °C steps) and keeps following
  the season.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median

from ..const import (
    BOILER_PLAN_BLOCK,
    BOILER_PLAN_DISABLED,
    BOILER_PLAN_HANDS_OFF,
    BOILER_PLAN_HEAT,
    BOILER_REPORTS_HEATING,
    BOILER_REPORTS_HEATING_AFTER,
    BOILER_REPORTS_HOT_WATER,
    CTRL_ACTIVE,
    CTRL_OFF,
    MODE_OFF,
    SAFETY_FALLBACK,
    SOURCE_BOILER,
    SOURCE_BOTH,
)
from .common import as_dict, as_float, as_floats, round_half
from .heat_call import CALL_CALLING, CALL_STALLED
from ..models import BoilerCommand, Parameters, RoomResult

RESEND_INTERVAL_S = 120.0  # boiler falls back to its knob after 9-16 min without SetMode (measured)
MIN_STATE_S = 300.0  # no heat/block toggling faster than this (frost excepted)
RAMP_UP_K = 5.0
RAMP_WINDOW_S = 600.0
DEMAND_BOOST_K = 8.0  # added at 100 % room demand
OFFSET_LIMIT = 10.0

# learning guards
SETTLE_S = 600.0  # ignore the first minutes of a burner run
# A boiler that fires for a minute and then waits a quarter of an hour delivers far
# more power than the rooms take: its flow setpoint is too high. Measured here on
# 2026-09-17 with one radiator open (60 s of burner every 15 min).
CYCLE_WINDOW_S = 3600.0
CYCLE_MIN_STARTS = 3  # within the window
CYCLE_SHORT_BURN_S = 300.0  # a run this short never reaches a steady spread
CYCLE_KEEP = 10
# Hot water on a combi boiler heats the primary exchanger, and the circuit sensors
# see it: return above flow, flow far above anything the heating curve asks for.
# ebusd does not reliably report the hot-water state (measured 2026-09-12: pump
# state stayed "off" through a shower), so it is recognised from the temperatures
# themselves and the circuit is given time to cool back before spread is trusted.
HOT_WATER_REVERSE_SPREAD_K = 1.0
HOT_WATER_ABOVE_FLOW_MAX_K = 2.0
HOT_WATER_HOLDOFF_S = 3600.0
# ebusd polls flow and return only every few minutes, so the temperature rules above
# notice a shower late. Two signals are faster:
HOT_WATER_GAS_W = 1000.0  # gas burning although space heating was blocked: only hot water is left
# A block counts only once it has been sent without a gap for this long. After an eBUS
# outage the boiler is back on its front knob and may fire for space heating while the
# first block is still on its way (measured 2026-09-19 00:59: adapter gone for 12 min,
# burner ran 20 s in S.4 and was taken for hot water).
BLOCK_IN_FORCE_S = 120.0
HOT_WATER_ABOVE_SETPOINT_K = 15.0  # flow far above the heating flow we sent (burner overshoot stays below)
HOT_WATER_SHOWN_S = 600.0  # how long "hot water active" stays on after the last sign, given the polling lag
# A guess (gas under a block, temperatures) is taken back when the boiler's status code then says
# "after heating" (S.5-S.8): after hot water it shows S.15-S.17. Measured 2026-09-28 00:44: the block
# had been sent for two minutes, but ebusd, restarted after an address change, could not write yet;
# the boiler fired once more by its knob and went to S.7.
HOT_WATER_RETRACT_S = 300.0
MIN_SAMPLES = 10
REVIEW_S = 1200.0
ADJUST_COOLDOWN_S = 3600.0
ADJUST_MAX_PER_DAY = 4
DAY_S = 24 * 3600.0
STEP_LEARNING_K = 1.0
STEP_REFINING_K = 0.5
REFINE_AFTER_ADJUSTMENTS = 6
SPREAD_LOW_K = 5.0
SPREAD_HIGH_K = 15.0
SPREAD_ALPHA = 0.2
SLOW_RATE_K_H = 0.2
DEMAND_ACTIVE = 0.3
# A cycling boiler never gives a settled spread: its burner runs for a minute and then waits out
# its lockout. What it delivers shows in the rooms alone. Measured 2026-10-10 with the flow minimum
# lowered from 45 to 35 °C: burner runs fell from 62 s to 41 s, the pause stayed at the lockout
# (15 min), and the bathroom hung 0.2-0.3 K below target for 14 hours. So while cycling, a room that
# is called for and does not get there asks for a HIGHER curve, and only rooms warming up briskly
# allow a lower one.
LAG_DEFICIT_K = 0.2  # still this far below target
LAG_MIN_HEAT_S = 1800.0  # after the heating has been released this long
BRISK_RATE_K_H = 0.5  # every called room rises at least this fast: the curve may come down
BURNER_SHARE_WINDOW_S = 3600.0

PHASE_LEARNING = "learning"
PHASE_REFINING = "refining"
PHASE_PAUSED = "paused"

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = ("state_since_ts", "last_flow", "last_flow_ts", "ramp_from", "ramp_since_ts", "hot_water_seen_ts")


@dataclass(frozen=True)
class BoilerMemory:
    last_send_ts: float | None = None
    last_payload: str | None = None
    last_flow: float | None = None
    last_flow_ts: float | None = None
    state: str | None = None  # BOILER_PLAN_HEAT | BOILER_PLAN_BLOCK: the plan in force
    state_since_ts: float | None = None
    offset_k: float = 0.0
    heating_since_ts: float | None = None
    spread_ema: float | None = None
    samples: int = 0
    last_review_ts: float | None = None
    adjustments: tuple[float, ...] = ()  # timestamps of offset changes
    hot_water_seen_ts: float | None = None  # last sample that looked like hot water
    burner_on: bool = False
    burn_started_ts: float | None = None
    burn_starts: tuple[float, ...] = ()  # when the burner came on (short cycling)
    burn_durations: tuple[float, ...] = ()
    ramp_from: float | None = None  # flow at which the current rise began
    ramp_since_ts: float | None = None
    last_adjust_reason: str | None = None
    block_since_ts: float | None = None  # first block of an unbroken series of sends; not stored
    hot_water_guessed: bool = False  # the last hot water sign was a guess, not the boiler's status code
    hot_water_before_guess_ts: float | None = None  # hot_water_seen_ts before that guess; not stored
    flame_since_ts: float | None = None  # the burner is on for the heating since then; not stored
    flame_runs: tuple[tuple[float, float], ...] = ()  # (start, end) of the heating burns of the last hour; not stored

    def phase(self, now_ts: float) -> str:
        if len(self.adjustments) >= REFINE_AFTER_ADJUSTMENTS:
            return PHASE_REFINING
        if self.adjustments and now_ts - self.adjustments[-1] > DAY_S:
            return PHASE_REFINING
        return PHASE_LEARNING

    def adjustments_last_day(self, now_ts: float) -> int:
        return len([t for t in self.adjustments if now_ts - t < DAY_S])

    def to_storage(self) -> dict:
        # The timers go with it: after a restart the controller must still know that it
        # switched to "heat" two minutes ago, or it may toggle the boiler right away.
        # `last_send_ts` is deliberately left out so a SetMode goes out immediately.
        return {
            "offset_k": self.offset_k,
            "adjustments": list(self.adjustments[-10:]),
            "last_adjust_reason": self.last_adjust_reason,
            "state": self.state,
            **{key: getattr(self, key) for key in _STORED_FLOATS},
            "burn_starts": list(self.burn_starts[-CYCLE_KEEP:]),
            "burn_durations": list(self.burn_durations[-CYCLE_KEEP:]),
        }

    @classmethod
    def from_storage(cls, data: dict | None) -> BoilerMemory:
        data = as_dict(data)
        offset = as_float(data.get("offset_k"))
        reason = data.get("last_adjust_reason")
        state = data.get("state")
        return cls(
            offset_k=0.0 if offset is None else offset,
            adjustments=tuple(as_floats(data.get("adjustments"))),
            last_adjust_reason=reason if isinstance(reason, str) else None,
            state=state if state in (BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK) else None,
            burn_starts=tuple(as_floats(data.get("burn_starts"))[-CYCLE_KEEP:]),
            burn_durations=tuple(as_floats(data.get("burn_durations"))[-CYCLE_KEEP:]),
            **{key: as_float(data.get(key)) for key in _STORED_FLOATS},
        )


@dataclass(frozen=True)
class BoilerInputs:
    now_ts: float
    control_mode: str
    op_mode: str
    safety_state: str
    boiler_available: bool
    rooms: tuple[RoomResult, ...]
    frost_rooms: tuple[str, ...]
    advice_source: str
    outdoor_c: float | None
    params: Parameters
    flow_c: float | None = None
    return_c: float | None = None
    burner_heating: bool = False  # pump running for space heating (not hot water)
    gas_power_w: float | None = None
    reported_mode: str | None = None  # BOILER_REPORTS_* from the boiler's status code, None if unknown
    status_known: bool = False  # the boiler's status code is configured and readable
    learning_allowed: bool = True  # False while setpoints move, boost/drying run or safety is not ok
    # Right after a restart the room sensors report within seconds to minutes. A block decided
    # before they did locks the heating (and the pump) off for the minimum state time.
    room_data_pending: bool = False



def curve_flow(outdoor_c: float | None, p: Parameters) -> float:
    """Linear heating curve through (-10 °C, cold) and (+15 °C, warm).

    Without an outdoor temperature the curve uses its middle: the cold point would
    heat the flow to the winter maximum on a mild day just because a sensor died.
    """
    if outdoor_c is None:
        value = (p.boiler_curve_flow_cold + p.boiler_curve_flow_warm) / 2.0
    else:
        slope = (p.boiler_curve_flow_cold - p.boiler_curve_flow_warm) / 25.0
        value = p.boiler_curve_flow_warm + slope * (15.0 - outdoor_c)
    return min(max(value, p.boiler_flow_min), p.boiler_flow_max)


def payload(flow: float, disable_hc: bool, disable_hwc_load: bool = False) -> str:
    # hcmode;flowtempdesired;hwctempdesired;hwcflowtempdesired;disablehc;disablehwctapping;
    # disablehwcload;remoteControlHcPump;releaseBackup;releaseCooling
    # disablehwcload stops what the boiler heats without a tap open: the keep-warm of a combi
    # boiler (reported 2026-10-10: one burner start an hour all night) or the loading of a cylinder.
    # Hot water gets "-" (no value): tested 13.09.2026, the boiler takes the heating
    # setpoint and hot water keeps following the knob, so control never changes it.
    return f"auto;{flow:.1f};-;-;{1 if disable_hc else 0};0;{1 if disable_hwc_load else 0};0;0;0"


def _direction(spread: float | None, heating: list[RoomResult]) -> tuple[int, str | None]:
    """Which way the heating curve should move, from spread and room progress."""
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


def track_burner(mem: BoilerMemory, burning: bool, now_ts: float) -> BoilerMemory:
    """Remember when the burner ran, so short cycling can be recognised."""
    if burning and not mem.burner_on:
        return replace(mem, burner_on=True, burn_started_ts=now_ts, burn_starts=(*mem.burn_starts[-CYCLE_KEEP:], now_ts))
    if not burning and mem.burner_on:
        duration = 0.0 if mem.burn_started_ts is None else now_ts - mem.burn_started_ts
        return replace(mem, burner_on=False, burn_started_ts=None, burn_durations=(*mem.burn_durations[-CYCLE_KEEP:], duration))
    return mem


def _flame(inp: BoilerInputs) -> bool:
    """The burner itself, as exactly as the installation can tell: gas meter, status code, pump state."""
    if inp.reported_mode == BOILER_REPORTS_HOT_WATER:
        return False
    if inp.gas_power_w is not None:
        return inp.gas_power_w > HOT_WATER_GAS_W
    if inp.status_known:
        return inp.reported_mode == BOILER_REPORTS_HEATING
    return inp.burner_heating


def track_flame(mem: BoilerMemory, flame: bool, now_ts: float) -> BoilerMemory:
    """Keep the heating burns of the last hour: what a cycling boiler delivers is its burner share."""
    runs = tuple(run for run in mem.flame_runs if now_ts - run[1] <= BURNER_SHARE_WINDOW_S)
    if flame and mem.flame_since_ts is None:
        return replace(mem, flame_since_ts=now_ts, flame_runs=runs)
    if not flame and mem.flame_since_ts is not None:
        return replace(mem, flame_since_ts=None, flame_runs=(*runs, (mem.flame_since_ts, now_ts)))
    return mem if runs == mem.flame_runs else replace(mem, flame_runs=runs)


def burner_share(mem: BoilerMemory, now_ts: float) -> tuple[float, int]:
    """Share of the last hour the burner ran for the heating (0..1), and how often it started."""
    since = now_ts - BURNER_SHARE_WINDOW_S
    runs = list(mem.flame_runs) + ([(mem.flame_since_ts, now_ts)] if mem.flame_since_ts is not None else [])
    burning = sum(max(min(end, now_ts) - max(start, since), 0.0) for start, end in runs)
    return burning / BURNER_SHARE_WINDOW_S, len([1 for start, _ in runs if start >= since])


def short_cycling(mem: BoilerMemory, now_ts: float) -> bool:
    """Several short burner runs within the last hour."""
    starts = [t for t in mem.burn_starts if now_ts - t <= CYCLE_WINDOW_S]
    if len(starts) < CYCLE_MIN_STARTS:
        return False
    # a run still burning has a start but no duration yet
    finished = len(starts) - (1 if mem.burner_on and mem.burn_started_ts in starts else 0)
    runs = list(mem.burn_durations[-finished:]) if finished > 0 else []
    return bool(runs) and median(runs) <= CYCLE_SHORT_BURN_S


def cycling_direction(inp: BoilerInputs, mem: BoilerMemory) -> tuple[int, str | None]:
    """Which way a cycling boiler's curve should move, judged by the rooms it is heating."""
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


def _learn(inp: BoilerInputs, mem: BoilerMemory, heating: list[RoomResult]) -> BoilerMemory:
    """Collect spread samples and move the offset at most once per hour."""
    now = inp.now_ts
    if inp.learning_allowed and short_cycling(mem, now):
        direction, reason = cycling_direction(inp, mem)
        if direction:
            moved = _apply(inp, mem, direction, reason, now)
            if moved is not mem:
                return replace(moved, burn_starts=(), burn_durations=())  # start counting again
    if not inp.learning_allowed or not inp.burner_heating or inp.flow_c is None or inp.return_c is None:
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

    direction, reason = _direction(ema, heating)
    if direction == 0:
        return replace(mem, last_review_ts=now)
    moved = _apply(inp, mem, direction, reason, now)
    return moved if moved is not mem else replace(mem, last_review_ts=now)


def _apply(inp: BoilerInputs, mem: BoilerMemory, direction: int, reason: str, now: float) -> BoilerMemory:
    """Move the heating curve offset, unless one of the guards says no."""
    # anti-windup: an offset that the flow limits already swallow must not keep growing
    planned = curve_flow(inp.outdoor_c, inp.params) + mem.offset_k
    if (direction > 0 and planned >= inp.params.boiler_flow_max) or (direction < 0 and planned <= inp.params.boiler_flow_min):
        return mem
    if mem.adjustments and now - mem.adjustments[-1] < ADJUST_COOLDOWN_S:
        return mem
    if mem.adjustments_last_day(now) >= ADJUST_MAX_PER_DAY:
        return mem

    step = STEP_REFINING_K if mem.phase(now) == PHASE_REFINING else STEP_LEARNING_K
    offset = min(max(mem.offset_k + direction * step, -OFFSET_LIMIT), OFFSET_LIMIT)
    if offset == mem.offset_k:
        return mem
    return replace(
        mem,
        offset_k=offset,
        adjustments=(*mem.adjustments[-9:], now),
        last_adjust_reason=reason,
        last_review_ts=now,
        samples=0,
    )


def hot_water_suspected(
    flow_c: float | None,
    return_c: float | None,
    flow_max_c: float,
    *,
    gas_w: float | None = None,
    heating_blocked: bool = False,
    heating_flow_c: float | None = None,
    reported_mode: str | None = None,
    status_known: bool = False,
) -> bool:
    """True when the boiler can only be making hot water.

    `heating_blocked` and `heating_flow_c` describe what was really sent to the
    boiler (active control); in plan-only mode the knob rules and neither applies.
    The boiler's own status code, where it is known, outranks every guess.
    """
    if reported_mode == BOILER_REPORTS_HOT_WATER:
        return True
    if reported_mode == BOILER_REPORTS_HEATING:
        return False
    burning = gas_w is not None and gas_w > HOT_WATER_GAS_W
    if reported_mode == BOILER_REPORTS_HEATING_AFTER and not burning:
        # overrun after a heating run; with gas burning the code lags behind a new start
        return False
    if heating_blocked and burning:
        return True
    if flow_c is None:
        return False
    if flow_c > flow_max_c + HOT_WATER_ABOVE_FLOW_MAX_K:
        return True
    if heating_flow_c is not None and flow_c > heating_flow_c + HOT_WATER_ABOVE_SETPOINT_K:
        return True
    if status_known:
        # Return above flow is also what the circuit shows after every heating run while the
        # pump keeps running (S.7): the exchanger cools first. With a status code at hand the
        # boiler says hot water itself, so this rule only stands in for boilers without one.
        return False
    return return_c is not None and return_c - flow_c > HOT_WATER_REVERSE_SPREAD_K


def _heating_after_all(inp: BoilerInputs, mem: BoilerMemory, now: float) -> bool:
    """A recent hot water guess that the boiler's status code now contradicts."""
    seen = mem.hot_water_seen_ts
    if not mem.hot_water_guessed or seen is None or now - seen > HOT_WATER_RETRACT_S:
        return False
    burning = inp.gas_power_w is not None and inp.gas_power_w > HOT_WATER_GAS_W
    return inp.reported_mode == BOILER_REPORTS_HEATING or (inp.reported_mode == BOILER_REPORTS_HEATING_AFTER and not burning)


def decide(inp: BoilerInputs, mem: BoilerMemory) -> tuple[BoilerCommand, BoilerMemory]:
    """Plan the boiler command, keeping hot-water episodes out of the learning."""
    now = inp.now_ts
    sent = inp.control_mode == CTRL_ACTIVE and mem.last_send_ts is not None
    block_in_force = (
        sent
        and mem.state == BOILER_PLAN_BLOCK
        and inp.boiler_available
        and mem.block_since_ts is not None
        and now - mem.block_since_ts >= BLOCK_IN_FORCE_S
    )
    if hot_water_suspected(
        inp.flow_c,
        inp.return_c,
        inp.params.boiler_flow_max,
        gas_w=inp.gas_power_w,
        heating_blocked=block_in_force,
        heating_flow_c=mem.last_flow if sent and mem.state == BOILER_PLAN_HEAT else None,
        reported_mode=inp.reported_mode,
        status_known=inp.status_known,
    ):
        # discard whatever this run had collected: those samples already carry the shower
        guessed = inp.reported_mode != BOILER_REPORTS_HOT_WATER
        before = (mem.hot_water_before_guess_ts if mem.hot_water_guessed else mem.hot_water_seen_ts) if guessed else None
        mem = replace(
            mem,
            hot_water_seen_ts=now,
            heating_since_ts=None,
            samples=0,
            spread_ema=None,
            hot_water_guessed=guessed,
            hot_water_before_guess_ts=before,
        )
    elif _heating_after_all(inp, mem, now):
        # the boiler says it heated: the guess was wrong, the hot water hold-off is taken back
        mem = replace(
            mem, hot_water_seen_ts=mem.hot_water_before_guess_ts, hot_water_guessed=False, hot_water_before_guess_ts=None
        )
    if inp.burner_heating and mem.hot_water_seen_ts is not None and now - mem.hot_water_seen_ts < HOT_WATER_HOLDOFF_S:
        # still cooling back from hot water: plan normally, but do not treat it as a heating run
        inp = replace(inp, burner_heating=False)
    mem = track_burner(mem, inp.burner_heating, now)
    mem = track_flame(mem, _flame(inp) and mem.state == BOILER_PLAN_HEAT, now)
    cmd, mem = _decide(inp, mem)
    share, starts = burner_share(mem, now)
    cmd = replace(cmd, burner_share=share, burner_starts_last_hour=starts)
    recent = mem.hot_water_seen_ts is not None and now - mem.hot_water_seen_ts < HOT_WATER_SHOWN_S
    return replace(cmd, hot_water=recent, hot_water_seen_ts=mem.hot_water_seen_ts), mem


def _decide(inp: BoilerInputs, mem: BoilerMemory) -> tuple[BoilerCommand, BoilerMemory]:
    p = inp.params
    now = inp.now_ts

    def command(plan: str, send: bool, text: str | None, flow: float | None, disable: bool, reason: str, *, curve=None, blockers=(), waiting=None) -> BoilerCommand:
        phase = mem.phase(now) if (inp.burner_heating and inp.learning_allowed) else PHASE_PAUSED
        # short cycling with rooms warming briskly asks for a lower curve; at the flow minimum that
        # step is swallowed silently
        held_back = (
            "short_cycling_at_flow_min"
            if short_cycling(mem, now)
            and cycling_direction(inp, mem)[0] < 0
            and curve_flow(inp.outdoor_c, p) + mem.offset_k <= p.boiler_flow_min
            else None
        )
        return BoilerCommand(
            plan=plan,
            send=send,
            payload=text,
            flow_setpoint=flow,
            disable_hc=disable,
            reason=reason,
            curve_flow=curve,
            offset_k=mem.offset_k,
            blockers=tuple(blockers),
            waiting=waiting,
            spread_k=None if mem.spread_ema is None else round(mem.spread_ema, 1),
            learning_phase=phase,
            adjustments_last_day=mem.adjustments_last_day(now),
            last_adjust_reason=mem.last_adjust_reason,
            held_back=held_back,
        )

    if inp.control_mode == CTRL_OFF:
        return command(BOILER_PLAN_DISABLED, False, None, None, False, "control_off"), mem

    # the spread is only meaningful while the boiler really heats the circuit
    if inp.burner_heating and mem.heating_since_ts is None:
        mem = replace(mem, heating_since_ts=now, samples=0, spread_ema=None)
    elif not inp.burner_heating and mem.heating_since_ts is not None:
        mem = replace(mem, heating_since_ts=None, samples=0)

    blockers: list[str] = []
    if not inp.boiler_available:
        blockers.append("boiler_data_unavailable")
    if inp.safety_state == SAFETY_FALLBACK:
        blockers.append("safety_fallback")
    if inp.room_data_pending:
        blockers.append("waiting_for_room_data")
    if blockers:
        # send nothing: the boiler returns to its front knob after ~10 min
        return (
            command(BOILER_PLAN_HANDS_OFF, False, None, None, False, "hands_off", blockers=blockers),
            replace(mem, state=None, state_since_ts=None, block_since_ts=None),
        )

    frost = bool(inp.frost_rooms)
    need = frost or (inp.op_mode != MODE_OFF and inp.advice_source in (SOURCE_BOILER, SOURCE_BOTH))
    reason = "frost_protection" if frost and inp.advice_source not in (SOURCE_BOILER, SOURCE_BOTH) else ("room_demand" if need else "no_room_needs_boiler")
    if inp.op_mode == MODE_OFF and not frost:
        reason = "summer_mode"

    waiting = None
    # the state held against toggling is the plan itself: heat or block
    wanted_state = BOILER_PLAN_HEAT if need else BOILER_PLAN_BLOCK
    if mem.state is not None and wanted_state != mem.state and not frost:
        if mem.state_since_ts is not None and now - mem.state_since_ts < MIN_STATE_S:
            wanted_state, waiting = mem.state, "min_state_time"
    if wanted_state != mem.state:
        mem = replace(mem, state=wanted_state, state_since_ts=now, last_review_ts=None, samples=0)

    # only rooms that call raise the flow: one hanging below target (stalled) or at the end of its
    # comfort period does not ask the boiler for anything
    heating_rooms = [
        r for r in inp.rooms if r.heating_allowed and r.temperature is not None and r.boiler_call in (None, CALL_CALLING)
    ]
    curve = round(curve_flow(inp.outdoor_c, p), 1)
    if wanted_state == BOILER_PLAN_HEAT:
        mem = _learn(inp, mem, heating_rooms)
        demand = max(((r.demand or 0.0) for r in heating_rooms), default=0.0)
        flow = curve + mem.offset_k + DEMAND_BOOST_K * demand
        if frost:
            flow = max(flow, 40.0)
        flow = round_half(min(max(flow, p.boiler_flow_min), p.boiler_flow_max))
        if frost:
            # frost protection goes straight to its floor; a ramp would only delay it
            mem = replace(mem, ramp_from=None, ramp_since_ts=None)
        elif mem.last_flow is not None and flow > mem.last_flow:
            # at most RAMP_UP_K per window, counted from where the rise began: the window
            # must not restart with every cycle, or 5 K per 10 min becomes 5 K per minute
            base = mem.ramp_from if mem.ramp_from is not None else mem.last_flow
            since = mem.ramp_since_ts if mem.ramp_since_ts is not None else now
            limit = base + RAMP_UP_K * (1 + int((now - since) // RAMP_WINDOW_S))
            if flow > limit:
                flow = limit
                waiting = waiting or "ramp_limit"
            mem = replace(mem, ramp_from=base, ramp_since_ts=since)
        elif (
            mem.ramp_from is None
            or flow <= mem.ramp_from
            or (mem.ramp_since_ts is not None and now - mem.ramp_since_ts >= RAMP_WINDOW_S)
        ):
            # A dip within the first window keeps the rise where it began. Measured 2026-10-02:
            # the bathroom target was clicked up and down for 25 s, every dip restarted the ramp,
            # and the flow went from 45 to 53 °C in 21 seconds.
            mem = replace(mem, ramp_from=None, ramp_since_ts=None)
        disable = False
    else:
        flow = p.boiler_flow_min
        disable = True
        # the next heating run ramps from its own start, not from a rise that ended with this block
        mem = replace(mem, ramp_from=None, ramp_since_ts=None)

    if mem.last_flow is None or flow != mem.last_flow:
        mem = replace(mem, last_flow=flow, last_flow_ts=now)
    text = payload(flow, disable, not p.boiler_hot_water_standby)
    due = text != mem.last_payload or mem.last_send_ts is None or now - mem.last_send_ts >= RESEND_INTERVAL_S
    if due:
        mem = replace(mem, last_send_ts=now, last_payload=text)
    if not disable or inp.control_mode != CTRL_ACTIVE:
        mem = replace(mem, block_since_ts=None)
    elif mem.block_since_ts is None:
        mem = replace(mem, block_since_ts=now)
    return command(wanted_state, due, text, None if disable else flow, disable, reason, curve=curve, waiting=waiting), mem
