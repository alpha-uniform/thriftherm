"""Midea controller (Phase 7): decides heat on/off, setpoint and fan for the Midea.

Pure logic, no Home Assistant import. The same decision runs in shadow mode
(published and logged only) and in active mode (sent to the climate entity),
so a shadow period shows exactly what active control would have done.

Principles
* Hands off whenever the user runs the Midea manually (cool, dry, fan only,
  auto) or changed our settings: cooling never collides with heating.
* Blockers (safety fallback, mode, outdoor limit, icing lockout, error code)
  stop a run immediately. Comfort-driven stops respect the minimum run time,
  restarts the minimum off time.
* The Midea regulates on its own intake air (the room it stands in), not on
  the served rooms. Its setpoint is therefore used as a power lever:
  intake + small offset = low compressor load = high COP ("eco"), 30 °C = full
  power ("boost", only for "quick heat-up"). The offset adapts so the served
  rooms warm up slowly but steadily.
* Economics come from the advisor (measured COP, else learned/prior estimate).
  Rarely visited outdoor-temperature bins get short learning runs when the
  estimate is close to break-even, so the COP map can learn cold conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..const import (
    HEAT_PUMP_AUTO,
    HEAT_PUMP_COOLING_USER,
    CTRL_ACTIVE,
    CTRL_OFF,
    HEAT_PUMP_FAN_ONLY,
    HEAT_PUMP_PLAN_DISABLED,
    HEAT_PUMP_PLAN_HANDS_OFF,
    HEAT_PUMP_PLAN_HEAT,
    HEAT_PUMP_PLAN_OFF,
    HEAT_PUMP_UNAVAILABLE,
    MODE_BOILER_ONLY,
    MODE_HEAT_PUMP_ONLY,
    MODE_OFF,
    SAFETY_FALLBACK,
    SOURCE_BOTH,
    SOURCE_HEAT_PUMP,
)
from .common import as_dict, as_float, round_half
from ..models import HeatPumpCommand, HeatPumpState, Parameters, RoomResult

START_BELOW_TARGET_K = 0.3
STOP_ABOVE_TARGET_K = 0.2
# Split heat pumps typically refuse heating setpoints below 17 °C and report the
# clamped value back, which would look like a user change and trigger a 3 h hands-off.
SETPOINT_MIN = 17.0
SETPOINT_MAX = 30.0
ADJUST_MIN_INTERVAL_S = 600.0
ADJUST_MIN_STEP_K = 0.5
OFFSET_MIN = 0.5
OFFSET_MAX = 5.0
OFFSET_STEP = 0.5
OFFSET_REVIEW_S = 1800.0
RATE_TOO_SLOW_K_H = 0.3
RATE_TOO_FAST_K_H = 1.5
LEARNING_MIN_BIN_COUNT = 5
LEARNING_MIN_RATIO = 0.85  # estimate must be at least 85 % of break-even
DRYING_MAX_OVERSHOOT_K = 1.5  # drying keeps asking for heat, but not without a ceiling
LEARNING_REPEAT_S = 24 * 3600.0
LEARNING_RUN_S = 3600.0
MANUAL_HANDS_OFF_S = 3 * 3600.0
MANUAL_DETECT_GRACE_S = 120.0
FAN_ECO = "auto"
FAN_BOOST = "high"

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "offset_k", "manual_until_ts", "running_since_ts", "stopped_since_ts",
    "last_command_ts", "last_target", "learning_until_ts", "last_offset_review_ts",
)


@dataclass(frozen=True)
class ControlMemory:
    """Controller state carried between cycles (persisted by the coordinator)."""

    running_since_ts: float | None = None
    stopped_since_ts: float | None = None
    last_command_ts: float | None = None
    last_hvac_mode: str | None = None
    last_target: float | None = None
    offset_k: float | None = None  # learned eco offset; None = parameter default
    last_offset_review_ts: float | None = None
    learning_until_ts: float | None = None
    learning_bins: tuple[tuple[int, float], ...] = ()  # (outdoor bin, last learning run ts)
    manual_until_ts: float | None = None

    def to_storage(self) -> dict:
        # The run state is persisted too: after a restart mid-run the controller must
        # still know it started the unit, or no blocker and no "target reached" stops it.
        return {
            "offset_k": self.offset_k,
            "learning_bins": [list(x) for x in self.learning_bins],
            "manual_until_ts": self.manual_until_ts,
            "running_since_ts": self.running_since_ts,
            "stopped_since_ts": self.stopped_since_ts,
            "last_command_ts": self.last_command_ts,
            "last_hvac_mode": self.last_hvac_mode,
            "last_target": self.last_target,
            "learning_until_ts": self.learning_until_ts,
            "last_offset_review_ts": self.last_offset_review_ts,
        }

    @classmethod
    def from_storage(cls, data: dict | None) -> ControlMemory:
        data = as_dict(data)
        stored_bins = data.get("learning_bins")
        bins: list[tuple[int, float]] = []
        for item in stored_bins if isinstance(stored_bins, (list, tuple)) else ():
            try:
                bins.append((int(item[0]), float(item[1])))
            except (TypeError, ValueError, IndexError):
                continue
        mode = data.get("last_hvac_mode")
        return cls(
            learning_bins=tuple(bins),
            last_hvac_mode=mode if isinstance(mode, str) else None,
            **{key: as_float(data.get(key)) for key in _STORED_FLOATS},
        )

    def fresh_run_state(self) -> ControlMemory:
        """Keep what was learned, forget what a previous (possibly simulated) run left behind."""
        return ControlMemory(offset_k=self.offset_k, learning_bins=self.learning_bins)


@dataclass(frozen=True)
class ControlInputs:
    now_ts: float
    control_mode: str
    op_mode: str
    heat_pump: HeatPumpState
    run_state: str
    rooms: tuple[RoomResult, ...]  # rooms served by the Midea
    advice_source: str
    blocked_reason: str | None  # advisor/defrost block (not the user-mode ones)
    safety_state: str
    outdoor_c: float | None
    expected_cop: float | None
    break_even_cop: float
    outdoor_bin: int | None
    bin_count: int
    params: Parameters



def eco_setpoint(heat_pump: HeatPumpState, offset_k: float) -> float | None:
    # the intake air sensor when it works, else the unit's own reading
    base = heat_pump.intake_temp.value_or_none if heat_pump.intake_temp.valid else heat_pump.indoor_temp
    if base is None:
        return None
    return min(max(round_half(base + offset_k), SETPOINT_MIN), SETPOINT_MAX)


def _blockers(inp: ControlInputs) -> list[str]:
    p = inp.params
    out: list[str] = []
    if inp.safety_state == SAFETY_FALLBACK:
        out.append("safety_fallback")
    if inp.op_mode in (MODE_OFF, MODE_BOILER_ONLY):
        out.append(f"mode_{inp.op_mode}")
    outdoor = inp.outdoor_c if inp.outdoor_c is not None else inp.heat_pump.outdoor_temp
    if outdoor is None:
        out.append("outdoor_temperature_unknown")
    elif outdoor < p.heat_pump_min_outdoor_temp:
        out.append(f"outdoor_below_{p.heat_pump_min_outdoor_temp:g}C")
    if inp.blocked_reason:
        out.append(inp.blocked_reason)
    if inp.heat_pump.error_code not in (None, 0):
        out.append(f"error_code_{inp.heat_pump.error_code}")
    return out


def _adapt_offset(inp: ControlInputs, mem: ControlMemory, offset: float, heating_rooms: list[RoomResult]) -> tuple[float, float | None]:
    """Every 30 min of an eco run: raise the offset if rooms barely warm up, lower it if they race."""
    if mem.running_since_ts is None or inp.now_ts - mem.running_since_ts < OFFSET_REVIEW_S:
        return offset, mem.last_offset_review_ts
    if mem.last_offset_review_ts is not None and inp.now_ts - mem.last_offset_review_ts < OFFSET_REVIEW_S:
        return offset, mem.last_offset_review_ts
    trends = [r.trend_k_per_h for r in heating_rooms if r.trend_k_per_h is not None]
    if not trends:
        return offset, mem.last_offset_review_ts
    slowest = min(trends)
    if slowest < RATE_TOO_SLOW_K_H:
        offset = min(offset + OFFSET_STEP, OFFSET_MAX)
    elif slowest > RATE_TOO_FAST_K_H:
        offset = max(offset - OFFSET_STEP, OFFSET_MIN)
    return offset, inp.now_ts


def decide(inp: ControlInputs, mem: ControlMemory) -> tuple[HeatPumpCommand, ControlMemory]:
    p = inp.params
    now = inp.now_ts

    def cmd(plan: str, action: str, reason: str, *, target=None, fan=None, load=None, blockers=(), waiting=None) -> HeatPumpCommand:
        return HeatPumpCommand(
            plan=plan,
            action=action,
            hvac_mode="heat" if plan == HEAT_PUMP_PLAN_HEAT else ("off" if action == "stop" else None),
            target_temp=target,
            fan_mode=fan,
            load=load,
            reason=reason,
            blockers=tuple(blockers),
            waiting=waiting,
        )

    if inp.control_mode == CTRL_OFF:
        return cmd(HEAT_PUMP_PLAN_DISABLED, "none", "control_off"), replace(mem, running_since_ts=None)

    # ---- hands off -------------------------------------------------------------
    if inp.run_state == HEAT_PUMP_UNAVAILABLE:
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "midea_unavailable"), replace(mem, running_since_ts=None)
    if inp.run_state in (HEAT_PUMP_COOLING_USER, HEAT_PUMP_FAN_ONLY, HEAT_PUMP_AUTO):
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", f"manual_mode_{inp.heat_pump.hvac_mode}"), replace(mem, running_since_ts=None)
    if inp.control_mode == CTRL_ACTIVE:
        if mem.manual_until_ts is not None and mem.manual_until_ts > now:
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), mem
        changed_by_user = (
            mem.last_hvac_mode is not None
            and mem.last_command_ts is not None
            and now - mem.last_command_ts > MANUAL_DETECT_GRACE_S
            and (
                (inp.heat_pump.hvac_mode or "off") != mem.last_hvac_mode
                or (
                    mem.last_hvac_mode == "heat"
                    and mem.last_target is not None
                    and inp.heat_pump.target_temp is not None
                    and abs(inp.heat_pump.target_temp - mem.last_target) >= 0.5
                )
            )
        )
        if changed_by_user:
            new = replace(mem, manual_until_ts=now + MANUAL_HANDS_OFF_S, running_since_ts=None, last_hvac_mode=None)
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), new

    running = mem.running_since_ts is not None
    offset = mem.offset_k if mem.offset_k is not None else p.heat_pump_setpoint_offset_k

    # ---- blockers stop immediately ---------------------------------------------------
    blockers = _blockers(inp)
    if blockers:
        if running:
            new = replace(mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, last_hvac_mode="off", learning_until_ts=None)
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", "blocked", blockers=blockers), new
        return cmd(HEAT_PUMP_PLAN_OFF, "none", "blocked", blockers=blockers), mem

    # ---- demand of the served rooms -----------------------------------------------------
    allowed = [r for r in inp.rooms if r.heat_pump_allowed and r.temperature is not None]
    need = [r for r in allowed if r.temperature <= r.target - START_BELOW_TARGET_K or (r.boost_active and r.temperature < r.target)]
    # Drying deliberately keeps demanding heat even once the target is met —
    # warm air carries the moisture out. It still needs a ceiling, or the
    # bathroom would be driven up for the full hour the mode may last.
    drying = [r for r in allowed if r.drying.active and r.temperature < r.target + DRYING_MAX_OVERSHOOT_K]
    boost = [r for r in allowed if r.boost_active and r.temperature < r.target]
    below = [r for r in allowed if r.temperature < r.target + STOP_ABOVE_TARGET_K]
    satisfied = not below and not drying

    economic = inp.advice_source in (SOURCE_HEAT_PUMP, SOURCE_BOTH) or inp.op_mode == MODE_HEAT_PUMP_ONLY
    learning_active = mem.learning_until_ts is not None and mem.learning_until_ts > now
    learning_new = False
    if (
        not economic
        and not learning_active
        and p.heat_pump_learning_runs
        and inp.outdoor_bin is not None
        and inp.bin_count < LEARNING_MIN_BIN_COUNT
        and inp.expected_cop is not None
        and inp.expected_cop >= LEARNING_MIN_RATIO * inp.break_even_cop
        and (need or drying)
    ):
        last = dict(mem.learning_bins).get(inp.outdoor_bin)
        learning_new = last is None or now - last >= LEARNING_REPEAT_S
    learning = learning_active or learning_new

    if boost:
        reason, load = "quick_heat_up", "boost"
    elif drying:
        reason, load = "bathroom_drying", "eco"
    elif inp.op_mode == MODE_HEAT_PUMP_ONLY:
        reason, load = "mode_midea_only", "eco"
    elif economic:
        reason, load = "cheaper_than_gas", "eco"
    elif learning:
        reason, load = "learning_run", "learning"
    else:
        reason, load = "gas_cheaper", None

    permitted = bool(boost or drying or economic or learning)
    wants_start = permitted and bool(need or drying or boost)
    keep_running = running and permitted and not satisfied

    def setpoint() -> tuple[float | None, str]:
        if load == "boost":
            return SETPOINT_MAX, FAN_BOOST
        return eco_setpoint(inp.heat_pump, offset), FAN_ECO

    if not running:
        if not wants_start:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason if (need or drying or boost) else "no_demand"), mem
        if mem.stopped_since_ts is not None and now - mem.stopped_since_ts < p.heat_pump_min_off_s:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, waiting="min_off_time"), mem
        target, fan = setpoint()
        if target is None:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "intake_temperature_unknown"), mem
        bins = mem.learning_bins
        learning_until = mem.learning_until_ts
        # a learning run only counts once it really ran; a planned one must not use up the bin
        if learning_new and inp.outdoor_bin is not None and inp.control_mode == CTRL_ACTIVE:
            bins = tuple((b, t) for b, t in bins if b != inp.outdoor_bin) + ((inp.outdoor_bin, now),)
            learning_until = now + LEARNING_RUN_S
        new = replace(
            mem,
            running_since_ts=now,
            stopped_since_ts=None,
            last_command_ts=now,
            last_hvac_mode="heat",
            last_target=target,
            learning_bins=bins,
            learning_until_ts=learning_until,
            last_offset_review_ts=None,
        )
        return cmd(HEAT_PUMP_PLAN_HEAT, "start", reason, target=target, fan=fan, load=load), new

    # running
    if not keep_running:
        if now - (mem.running_since_ts or now) < p.heat_pump_min_run_s:
            # target reached, or economics turned: finish the minimum run (in eco when the
            # gas boiler takes over afterwards)
            reason, load = ("target_reached", load) if permitted else (reason, "eco")
            return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=FAN_ECO, load=load, waiting="min_run_time"), mem
        new = replace(mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, last_hvac_mode="off", learning_until_ts=None)
        return cmd(HEAT_PUMP_PLAN_OFF, "stop", "target_reached" if permitted else reason), new

    # only a real run tells how fast the rooms warm; a planned run heats nothing
    if load in ("eco", "learning") and inp.control_mode == CTRL_ACTIVE:
        offset, review_ts = _adapt_offset(inp, mem, offset, need or below)
        if review_ts != mem.last_offset_review_ts:
            mem = replace(mem, offset_k=offset, last_offset_review_ts=review_ts)
    target, fan = setpoint()
    if target is None:
        return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=fan, load=load), mem
    if mem.last_target is None or abs(target - mem.last_target) >= ADJUST_MIN_STEP_K:
        if mem.last_command_ts is not None and now - mem.last_command_ts < ADJUST_MIN_INTERVAL_S and load != "boost":
            return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=fan, load=load, waiting="rate_limit"), mem
        new = replace(mem, last_command_ts=now, last_target=target, last_hvac_mode="heat")
        return cmd(HEAT_PUMP_PLAN_HEAT, "adjust", reason, target=target, fan=fan, load=load), new
    return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=fan, load=load), mem
