"""Midea controller (Phase 7): decides heat on/off, setpoint and fan for the Midea.

Pure logic, no Home Assistant import. The same decision runs in shadow mode
(published and logged only) and in active mode (sent to the climate entity),
so a shadow period shows exactly what active control would have done.

Principles
* Hands off whenever the user runs the Midea manually (cool, dry, fan only,
  auto) or changed our settings: cooling never collides with heating. Who
  changed a setting comes from Home Assistant (engines/hand.py): a person or an
  automation counts at once, a change at the unit itself once the unit has had
  time to settle. A unit that merely differs from what was sent, with nobody
  having touched it, did not carry the command out and gets it again. A change
  by hand is respected for as long as one on a room thermostat.
* Blockers (safety fallback, mode, outdoor limit, icing lockout, error code)
  stop a run immediately. Comfort-driven stops respect the minimum run time,
  restarts the minimum off time.
* A unit is never switched from heating straight to off: it first runs its fan
  alone for a quarter of an hour (parameter), which blows the heat left in the
  coil into the rooms, and is switched off after that.
* The Midea regulates on its own intake air (the room it stands in), not on
  the served rooms. Its setpoint is therefore used as a power lever:
  intake + small offset = low compressor load = high COP ("eco"), 30 °C = full
  power ("boost", only for "quick heat-up"). The offset adapts so the served
  rooms warm up slowly but steadily.
* With "setpoint follows the rooms" (option) the unit gets the highest target of
  the rooms it serves plus the setpoint offset (comfort 21 °C by day gives 22 °C,
  setback 17 °C at night gives 18 °C) and regulates by itself. It stays on, day
  and night, for as long as the heat pump is the cheaper source, whether or not
  a room is below its target right now, and is switched off only while nobody
  wants the served rooms warm (away, off, a window open in each of them). Quick
  heat-up only raises the fan. While the intake air is at the setpoint the unit
  idles; the command says so (`delivers`), and the boiler brings what a served
  room still lacks.
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
from . import hand
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
MANUAL_DETECT_GRACE_S = 120.0  # a unit may take this long to show what it was sent
NOT_CARRIED_OUT = "not_carried_out"  # the unit differs from our command, and nobody touched it
SETTLING = "unit_settling"  # it differs, and it is too early to say why
RETRY_MAX_S = 3600.0  # a command the unit keeps ignoring is repeated ever more rarely, up to this
FAN_ECO = "auto"
FAN_BOOST = "high"
# targets that mean "nobody wants this room warm": a unit that follows the rooms is not run for them.
# The night setback is not among them: the unit then holds the setback temperature plus the offset.
RESTING_REASONS = ("mode_away", "away_humidity_guard", "mode_off_frost_protection", "heat_pump_cooling")

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "offset_k", "manual_until_ts", "running_since_ts", "stopped_since_ts",
    "last_command_ts", "last_target", "learning_until_ts", "last_offset_review_ts", "fan_until_ts",
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
    manual_by: str | None = None  # who made the change by hand that is respected until then (engines/hand.py)
    not_carried_out: int = 0  # commands in a row the unit did not carry out though nobody touched it; not stored
    fan_until_ts: float | None = None  # the unit runs its fan on until then, then it is switched off

    def to_storage(self) -> dict:
        # The run state is persisted too: after a restart mid-run the controller must
        # still know it started the unit, or no blocker and no "target reached" stops it.
        return {
            "offset_k": self.offset_k,
            "learning_bins": [list(x) for x in self.learning_bins],
            "manual_until_ts": self.manual_until_ts,
            "manual_by": self.manual_by,
            "running_since_ts": self.running_since_ts,
            "stopped_since_ts": self.stopped_since_ts,
            "last_command_ts": self.last_command_ts,
            "last_hvac_mode": self.last_hvac_mode,
            "last_target": self.last_target,
            "learning_until_ts": self.learning_until_ts,
            "last_offset_review_ts": self.last_offset_review_ts,
            "fan_until_ts": self.fan_until_ts,
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
        by = data.get("manual_by")
        return cls(
            learning_bins=tuple(bins),
            last_hvac_mode=mode if isinstance(mode, str) else None,
            manual_by=by if by in hand.SHOWN else None,
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
    break_even_cop: float | None  # None when the prices cannot be used
    outdoor_bin: int | None
    bin_count: int
    params: Parameters
    cheaper: bool = False  # the heat pump is the cheaper source right now, whether or not a room calls



def eco_setpoint(heat_pump: HeatPumpState, offset_k: float) -> float | None:
    # the intake air sensor when it works, else the unit's own reading
    base = _intake(heat_pump)
    if base is None:
        return None
    return min(max(round_half(base + offset_k), SETPOINT_MIN), SETPOINT_MAX)


def _intake(heat_pump: HeatPumpState) -> float | None:
    return heat_pump.intake_temp.value_or_none if heat_pump.intake_temp.valid else heat_pump.indoor_temp


def wanted_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Served rooms somebody wants warm and the unit may heat (window shut)."""
    return [r for r in rooms if r.heat_pump_allowed and r.target_reason not in RESTING_REASONS]


def room_setpoint(p: Parameters, rooms: tuple[RoomResult, ...]) -> float | None:
    """The setpoint of a unit that follows the rooms: their highest target plus the offset. None without such a room."""
    wanted = wanted_rooms(rooms)
    if not wanted:
        return None
    return min(max(round_half(max(r.target for r in wanted) + p.heat_pump_setpoint_offset_k), SETPOINT_MIN), SETPOINT_MAX)


def differs(inp: ControlInputs, mem: ControlMemory) -> bool:
    """The unit shows another mode or setpoint than our last command asked for."""
    if mem.last_hvac_mode is None:
        return False
    unit = inp.heat_pump
    return (unit.hvac_mode or "off") != mem.last_hvac_mode or (
        mem.last_hvac_mode == "heat"
        and mem.last_target is not None
        and unit.target_temp is not None
        and abs(unit.target_temp - mem.last_target) >= 0.5
    )


def changed_by(inp: ControlInputs, mem: ControlMemory) -> str | None:
    """Who set the unit differently from our last command.

    An origin of engines/hand.py for a change by hand, NOT_CARRIED_OUT when the unit differs
    and nobody touched it, SETTLING while it is too early to say, None when the unit is as we
    left it.
    """
    if mem.last_command_ts is None or not differs(inp, mem):
        return None
    unit = inp.heat_pump
    asked = hand.deliberate_since(unit.setting_change, mem.last_command_ts)
    if asked is not None:
        return asked  # somebody asked for it in Home Assistant: no waiting
    if abs(inp.now_ts - mem.last_command_ts) <= MANUAL_DETECT_GRACE_S:
        return SETTLING
    change = unit.setting_change
    if change is not None and change.ts > mem.last_command_ts:
        # the unit answering our own command with something else has not carried it out
        return NOT_CARRIED_OUT if change.origin == hand.OURS else hand.DEVICE
    watched = unit.settings_watched_since_ts
    if watched is not None and watched <= mem.last_command_ts:
        return NOT_CARRIED_OUT  # every change since our command is known, and there was none
    return hand.DEVICE  # not seen (a restart in between): as before, somebody at the unit


def retry_due(mem: ControlMemory, now_ts: float) -> bool:
    """A command the unit did not carry out is repeated after 2, 4, 8 ... minutes, at most hourly."""
    wait = min(MANUAL_DETECT_GRACE_S * 2 ** min(mem.not_carried_out, 8), RETRY_MAX_S)
    return mem.last_command_ts is None or now_ts - mem.last_command_ts >= wait


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
    """The command for this cycle and the memory after it; the command also shows what the memory holds."""
    command, new = _decide(inp, mem)
    held = new.manual_until_ts is not None and new.manual_until_ts > inp.now_ts
    return replace(
        command,
        manual_until_ts=new.manual_until_ts if held else None,
        manual_by=new.manual_by if held else None,
        not_carried_out=new.not_carried_out,
    ), new


def _decide(inp: ControlInputs, mem: ControlMemory) -> tuple[HeatPumpCommand, ControlMemory]:
    p = inp.params
    now = inp.now_ts

    def cmd(
        plan: str, action: str, reason: str, *, target=None, fan=None, load=None, blockers=(), waiting=None, stop_to="off"
    ) -> HeatPumpCommand:
        return HeatPumpCommand(
            plan=plan,
            action=action,
            hvac_mode="heat" if plan == HEAT_PUMP_PLAN_HEAT else (stop_to if action == "stop" else None),
            target_temp=target,
            fan_mode=fan,
            load=load,
            reason=reason,
            blockers=tuple(blockers),
            waiting=waiting,
            delivers=not idles,
        )

    # A unit that follows the rooms' target stops heating by itself once its intake air is there.
    follows = p.heat_pump_follow_rooms
    own = room_setpoint(p, inp.rooms) if follows else None
    intake = _intake(inp.heat_pump)
    idles = own is not None and intake is not None and intake >= own

    if inp.control_mode == CTRL_OFF:
        return cmd(HEAT_PUMP_PLAN_DISABLED, "none", "control_off"), replace(mem, running_since_ts=None)

    # ---- hands off -------------------------------------------------------------
    if inp.run_state == HEAT_PUMP_UNAVAILABLE:
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "midea_unavailable"), replace(mem, running_since_ts=None)
    # ---- the fan runs on after a stop of ours, then the unit is switched off --------------
    if mem.fan_until_ts is not None:
        shown = inp.heat_pump.hvac_mode or "off"
        ours = inp.control_mode != CTRL_ACTIVE or shown == "fan_only"
        settling = mem.last_command_ts is not None and now - mem.last_command_ts <= MANUAL_DETECT_GRACE_S
        asked = hand.deliberate_since(inp.heat_pump.setting_change, mem.last_command_ts) is not None
        if not ours and shown == "off" and (asked or not settling):
            # switched off at the unit or in Home Assistant: where the run-on was heading anyway.
            # Not while the unit may still be on its way into fan only and shows "off" in passing.
            mem = replace(mem, fan_until_ts=None, last_hvac_mode="off")
        elif not ours and (asked or not settling):
            # another mode than ours: the run-on is over, and whose change it was is judged below
            mem = replace(mem, fan_until_ts=None)
        elif now >= mem.fan_until_ts:
            new = replace(mem, fan_until_ts=None, last_command_ts=now, last_hvac_mode="off")
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", "fan_run_on"), new
        else:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "fan_run_on", waiting="fan_run_on"), mem

    def stop(reason: str, blockers=()) -> tuple[HeatPumpCommand, ControlMemory]:
        run_on = p.heat_pump_fan_run_on_s > 0
        new = replace(
            mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, learning_until_ts=None,
            last_hvac_mode="fan_only" if run_on else "off", fan_until_ts=now + p.heat_pump_fan_run_on_s if run_on else None,
        )
        return cmd(HEAT_PUMP_PLAN_OFF, "stop", reason, blockers=blockers, stop_to="fan_only" if run_on else "off"), new

    active = inp.control_mode == CTRL_ACTIVE
    if active and mem.manual_until_ts is not None:
        # A further change during the time in hand renews it, as on a room thermostat; one after
        # its end is a new matter. Its end leaves nothing behind, whatever mode the unit is in.
        change = inp.heat_pump.setting_change
        during = change is not None and change.origin != hand.OURS and change.ts <= mem.manual_until_ts
        if during and change.ts + p.manual_hold_s > mem.manual_until_ts + 1.0:
            by = change.origin if change.origin in hand.DELIBERATE else (mem.manual_by or hand.DEVICE)
            mem = replace(mem, manual_until_ts=change.ts + p.manual_hold_s, manual_by=by)
        if mem.manual_until_ts is not None and mem.manual_until_ts <= now:
            mem = replace(mem, manual_until_ts=None, manual_by=None)
    if inp.run_state in (HEAT_PUMP_COOLING_USER, HEAT_PUMP_FAN_ONLY, HEAT_PUMP_AUTO):
        # The "off" after our own run-on that the unit did not take leaves it in fan only: that is
        # not somebody ventilating, and without a second "off" it would stay there for good.
        ours_left_over = active and inp.run_state == HEAT_PUMP_FAN_ONLY and mem.last_hvac_mode == "off"
        if ours_left_over and changed_by(inp, mem) == NOT_CARRIED_OUT and retry_due(mem, now):
            again = replace(mem, last_command_ts=now, not_carried_out=mem.not_carried_out + 1)
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", NOT_CARRIED_OUT), again
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", f"manual_mode_{inp.heat_pump.hvac_mode}"), replace(mem, running_since_ts=None)

    settling = False
    if active:
        if mem.manual_until_ts is not None:
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), mem
        by = changed_by(inp, mem)
        if by == SETTLING:
            settling = True
        elif by == NOT_CARRIED_OUT:
            # Nobody touched the unit, it just does not show what was sent (seen 2026-10-11: 18 °C
            # sent, the unit stayed at 23 °C). That is no hand. The command is repeated, ever more
            # rarely while the unit keeps ignoring it; in between the plan goes on as if it had followed.
            if retry_due(mem, now):
                mem = replace(mem, not_carried_out=mem.not_carried_out + 1)
                if mem.last_hvac_mode != "heat" and inp.heat_pump.hvac_mode == "heat":
                    return stop(NOT_CARRIED_OUT)  # it was to stop and still heats: stop it again
                # it was to heat: forget the command, and the plan below sends what is due
                heating = inp.heat_pump.hvac_mode == "heat"
                mem = replace(
                    mem, last_hvac_mode=None, last_target=None, last_command_ts=None,
                    running_since_ts=mem.running_since_ts if heating else None,
                )
        elif by is not None:
            off_now = inp.heat_pump.hvac_mode != "heat"
            new = replace(
                mem, manual_until_ts=now + p.manual_hold_s, manual_by=by, running_since_ts=None, last_hvac_mode=None,
                fan_until_ts=None, not_carried_out=0,
                # switched off by hand: the minimum pause counts from now once the unit is ours again
                stopped_since_ts=now if off_now else mem.stopped_since_ts,
            )
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), new
        elif mem.not_carried_out:
            # the unit shows what was sent again, or nothing of ours is on record any more
            mem = replace(mem, not_carried_out=0)
        # A unit found heating without a command of ours on record (control just switched to
        # active, or the hands-off time after a change by hand is over) is taken over as a run
        # that starts now. Without this nothing would ever stop it. No setpoint of ours is on
        # record, so the next cycle sends one (or the stop) and the unit is ours from there.
        if mem.last_hvac_mode is None and mem.running_since_ts is None and inp.heat_pump.hvac_mode == "heat":
            mem = replace(
                mem, running_since_ts=now, stopped_since_ts=None, last_command_ts=None,
                last_hvac_mode="heat", last_target=None, last_offset_review_ts=None,
            )

    running = mem.running_since_ts is not None
    offset = mem.offset_k if mem.offset_k is not None else p.heat_pump_setpoint_offset_k

    # ---- blockers stop immediately ---------------------------------------------------
    blockers = _blockers(inp)
    if blockers:
        if running:
            return stop("blocked", blockers)
        return cmd(HEAT_PUMP_PLAN_OFF, "none", "blocked", blockers=blockers), mem

    # ---- no command while the unit differs and it is too early to say why -------------------
    # A command sent now would move "our last command" past the change that is still to be judged:
    # a unit switched off at its remote and then sent a new setpoint looked as if it had not started.
    if settling:
        plan = HEAT_PUMP_PLAN_HEAT if running else HEAT_PUMP_PLAN_OFF
        return cmd(plan, "none", SETTLING, target=mem.last_target if running else None, waiting=SETTLING), mem

    # ---- no decision on missing data ---------------------------------------------------
    # Right after a restart the room sensors have not reported yet. "No room is cold" would be
    # read from that and a running unit stopped, although nothing has changed in the home.
    # The unit keeps what it has until the rooms are known again; the blockers above still stop it.
    if running and any(r.heat_pump_allowed and r.temperature is None for r in inp.rooms):
        return cmd(HEAT_PUMP_PLAN_HEAT, "none", "waiting_for_room_data", target=mem.last_target, waiting="room_data"), mem

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
    # rooms somebody wants warm: a unit that follows the rooms stays on for them
    awake = wanted_rooms(inp.rooms) if follows else []  # with or without a temperature: the unit regulates by itself
    if awake and inp.cheaper:
        economic = True
    learning_active = mem.learning_until_ts is not None and mem.learning_until_ts > now
    learning_new = False
    if (
        not economic
        and not learning_active
        and p.heat_pump_learning_runs
        and inp.outdoor_bin is not None
        and inp.bin_count < LEARNING_MIN_BIN_COUNT
        and inp.expected_cop is not None
        and inp.break_even_cop is not None
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

    rests = False  # a unit that follows the rooms while nobody wants a served room warm
    if follows:
        rests = not (awake or drying or boost)
        wants_start = permitted and not rests
        keep_running = running and permitted and not rests

    def setpoint() -> tuple[float | None, str]:
        if follows:
            return (own if own is not None else mem.last_target), FAN_BOOST if load == "boost" else FAN_ECO
        if load == "boost":
            return SETPOINT_MAX, FAN_BOOST
        return eco_setpoint(inp.heat_pump, offset), FAN_ECO

    if not running:
        if not wants_start:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason if (need or drying or boost or awake) else "no_demand"), mem
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
            if rests:
                reason, load = "comfort_time_over", "eco"
            else:
                reason, load = ("target_reached", load) if permitted else (reason, "eco")
            return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=FAN_ECO, load=load, waiting="min_run_time"), mem
        return stop("comfort_time_over" if rests else ("target_reached" if permitted else reason))

    # only a real run tells how fast the rooms warm; a planned run heats nothing
    if not follows and load in ("eco", "learning") and inp.control_mode == CTRL_ACTIVE:
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
