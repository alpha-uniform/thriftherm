"""Cooling with the heat pump (docs/PHASE12_COOLING.md §4-6): run, rest, more power, run-on, hand, safety.

Pure logic, no Home Assistant import. Called instead of heat_pump_control.decide while the season is cooling, on
the same ControlMemory: one unit, one time in hand. The heating control is not touched by it; what both need (the
hand detection, the fan run-on, the fan stage of normal operation) comes from there.

Principles
* The room comes first (decided 2026-11-12). The warmest served room, against its own cooling target, decides run or
  rest: at its target the unit runs, half a degree below every target it rests. A room that stays too warm turns the
  unit up, half a degree of setpoint every ten minutes down to three below its own reading, at once where a room is two
  degrees too warm; "quiet" (an option) keeps it at its least speed instead.
* At the target it holds its least speed, as in heating, the other way round: a PortaSplit regulates on its own reading
  at the indoor unit, so the setpoint is a lever on the compressor. The constants are mirrored from heating and are
  assumptions until they are measured in summer 2027 (§11).
* It rests at the highest setpoint in cool mode, from where it restarts softly; its fan runs on there. With no room to
  cool it is stopped, with a run-on of its fan that dries the coil, also after a long rest.
* No served room below the floor, whatever another room wants: the unit rests until that room is half a degree above it.
* Frost, an error code, the safety fallback and an open window where the unit stands stop it at once. It never heats.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..const import (
    COOL_SHORTFALL_LIFT,
    COOL_SHORTFALL_QUIET,
    CTRL_ACTIVE,
    CTRL_OFF,
    HEAT_PUMP_PLAN_COOL,
    HEAT_PUMP_PLAN_DISABLED,
    HEAT_PUMP_PLAN_HANDS_OFF,
    HEAT_PUMP_PLAN_OFF,
    SAFETY_FALLBACK,
)
from ..models import HeatPumpCommand, HeatPumpState, Parameters, RoomResult
from . import hand
from .heat_pump_control import (
    ADJUST_MIN_INTERVAL_S,
    ADJUST_MIN_STEP_K,
    CAP_HOLD_S,
    LAG_S,
    MANUAL_DETECT_GRACE_S,
    NOT_CARRIED_OUT,
    READING_HIGH_S,
    REST_FAILSAFE_S,
    ROOM_DATA_GRACE_S,
    SETPOINT_STEP,
    SETTLING,
    STALE_OFF_S,
    STAND_S,
    ControlMemory,
    changed_by,
    fan_run_on,
    normal_fan,
    retry_due,
    runs,
    season_pause_s,
    stood_s,
    stopped_ts,
    to_step,
)
from .safety import HEAT_PUMP_DROPOUT_S

COOL_SETPOINT_MIN = 16.0  # a Midea in cool mode takes 16-30 °C (its min_temp and max_temp)
COOL_SETPOINT_MAX = 30.0  # ... and rests at the highest
# Assumptions, mirrored from heating (heat_pump_control.AIM_K, READING_JUMP_K, CAP_K, UNIT_HYSTERESIS_K), to be measured:
COOL_AIM_K = 1.0  # its least speed with the setpoint a degree above its own reading
COOL_READING_JUMP_K = 1.5  # its reading falls this much once the compressor runs
COOL_CAP_K = 0.5  # its reading this far above the setpoint while it runs: its own controller would climb
COOL_UNIT_HYSTERESIS_K = 2.0  # it stops itself about this far below its setpoint
REST_BELOW_K = 0.5  # every room this far below its target: it rests
RESUME_AT_K = 0.0  # ... and at its target it runs again
FLOOR_HYSTERESIS_K = 0.5  # a room below the floor rests the unit until it is this far above it
# A room this far above its target that falls slower than this lags (as LAG_BELOW_K, RATE_TOO_SLOW_K_H).
WARM_ABOVE_K = 0.3
FALL_TOO_SLOW_K_H = 0.3
LIFT_STEP_K = 0.5
LIFT_MAX_K = 3.0  # turned up at most this far below its own reading
LIFT_AT_ONCE_K = 2.0  # a room this far above its target turns it up fully at once

HEATED_BY_HAND = "heating_by_hand_in_cooling"  # the reason of a room somebody heats at its thermostat (the coordinator)

SHORTFALL_LIFT = COOL_SHORTFALL_LIFT
SHORTFALL_QUIET = COOL_SHORTFALL_QUIET


@dataclass(frozen=True)
class CoolInputs:
    now_ts: float
    control_mode: str
    heat_pump: HeatPumpState
    rooms: tuple[RoomResult, ...]  # the rooms the unit serves, with their cooling targets
    params: Parameters
    floor: float = 20.0  # no served room is cooled below this
    shortfall: str = SHORTFALL_LIFT  # a room that stays too warm: turned up, or "quiet"
    fan_stage: str = "auto"  # the fan stage of cooling
    unit_window_open: bool = False  # a window open in the room where the unit stands
    safety_state: str = "ok"
    safety_pending: bool = False  # the fallback comes only from room readings not arrived since the start
    frost: bool = False  # a room on the frost list
    started_ts: float | None = None
    heat_pump_gone_s: float | None = None
    season_since_ts: float | None = None  # when the season was switched to cooling


def _held(value: float | None, unit: HeatPumpState) -> float | None:
    """A setpoint in the unit's step, above its lowest and below the rest's."""
    if value is None:
        return None
    step = max(SETPOINT_STEP, unit.target_temp_step or 0.0)
    return to_step(min(max(value, COOL_SETPOINT_MIN), COOL_SETPOINT_MAX - step), unit.target_temp_step)


def ceiling(unit: HeatPumpState) -> float:
    """The highest setpoint, where the unit rests."""
    return to_step(COOL_SETPOINT_MAX, unit.target_temp_step)


def _intake(unit: HeatPumpState) -> float | None:
    return unit.intake_temp.value_or_none if unit.intake_temp.valid else None


def ref_reading(unit: HeatPumpState, mem: ControlMemory, now: float) -> float | None:
    """The reading a setpoint is measured from: its own, or the intake sensor where its own stands still (off long)."""
    off_long = unit.hvac_mode not in ("cool", "fan_only") and (
        mem.stopped_since_ts is None or now - mem.stopped_since_ts >= STALE_OFF_S
    )
    if (off_long and _intake(unit) is not None) or unit.indoor_temp is None:
        return _intake(unit)
    return unit.indoor_temp


def may_run(unit: HeatPumpState) -> bool:
    """Its compressor runs, or may: a unit in cool mode that shows no frequency."""
    return runs(unit) or (unit.compressor_hz is None and unit.hvac_mode == "cool")


def latch_running(own: float | None, unit: HeatPumpState) -> float | None:
    """The setpoint of a unit whose compressor runs: a degree above its own reading."""
    return _held(None if own is None else own + COOL_AIM_K, unit)


def latch_start(reading: float | None, unit: HeatPumpState) -> float | None:
    """The setpoint of a unit whose compressor stands: half a degree below its reading (it falls once it runs)."""
    return _held(None if reading is None else reading + COOL_AIM_K - COOL_READING_JUMP_K, unit)


def ceiling_rests(unit: HeatPumpState, now: float) -> bool:
    """The highest setpoint stops its compressor: its reading as it would run at least its hysteresis below it."""
    if unit.indoor_temp is None:
        return False
    standing = (stood_s(unit, now) or 0.0) >= STAND_S
    return unit.indoor_temp - (COOL_READING_JUMP_K if standing else 0.0) <= COOL_SETPOINT_MAX - COOL_UNIT_HYSTERESIS_K


def lags(room: RoomResult) -> bool:
    """Too warm and barely falling. An unknown trend is no evidence."""
    if room.temperature is None or room.cool_target is None or room.trend_k_per_h is None:
        return False
    return round(room.temperature - room.cool_target, 3) >= WARM_ABOVE_K and room.trend_k_per_h > -FALL_TOO_SLOW_K_H


def counted(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """The served rooms that count: a cooling target, a reading, the window shut."""
    return [r for r in rooms if r.cooling_allowed and r.cool_target is not None and r.temperature is not None]


def reading_missing(rooms: tuple[RoomResult, ...]) -> bool:
    """A served room would count but for its reading: a cooling target, its window shut, not heated by hand."""
    return any(
        r.cool_target is not None and r.temperature is None and not r.window_open
        and r.cool_target_reason != HEATED_BY_HAND
        for r in rooms
    )


def _blockers(inp: CoolInputs) -> list[str]:
    out: list[str] = []
    pending = inp.safety_pending and inp.started_ts is not None and inp.now_ts - inp.started_ts < ROOM_DATA_GRACE_S
    if inp.safety_state == SAFETY_FALLBACK and not pending:
        out.append("safety_fallback")
    if inp.frost:
        out.append("frost")
    if inp.heat_pump.error_code not in (None, 0):
        out.append(f"error_code_{inp.heat_pump.error_code}")
    return out


def decide(inp: CoolInputs, mem: ControlMemory) -> tuple[HeatPumpCommand, ControlMemory]:
    """The command for this cycle and the memory after it."""
    command, new = _decide(inp, mem)
    if new.running_since_ts is None and (new.cool_lifted or new.cool_warm_since_ts is not None):
        new = replace(new, cool_lifted=False, cool_warm_since_ts=None)
    if new.running_since_ts is None and new.cool_blind_since_ts is not None:
        new = replace(new, cool_blind_since_ts=None)
    held = new.manual_until_ts is not None and new.manual_until_ts > inp.now_ts
    return replace(
        command,
        manual_until_ts=new.manual_until_ts if held else None,
        manual_by=new.manual_by if held else None,
        not_carried_out=new.not_carried_out,
    ), new


def _decide(inp: CoolInputs, mem: ControlMemory) -> tuple[HeatPumpCommand, ControlMemory]:
    p = inp.params
    now = inp.now_ts
    unit = inp.heat_pump
    active = inp.control_mode == CTRL_ACTIVE

    def cmd(plan: str, action: str, reason: str, *, target=None, fan=None, blockers=(), waiting=None, stop_to="off"):
        return HeatPumpCommand(
            plan=plan,
            action=action,
            hvac_mode="cool" if plan == HEAT_PUMP_PLAN_COOL else (stop_to if action == "stop" else None),
            target_temp=target,
            fan_mode=fan,
            load="eco" if plan == HEAT_PUMP_PLAN_COOL else None,
            reason=reason,
            blockers=tuple(blockers),
            waiting=waiting,
        )

    if inp.control_mode == CTRL_OFF:
        return cmd(HEAT_PUMP_PLAN_DISABLED, "none", "control_off"), replace(mem, running_since_ts=None)

    # ---- hands off ----------------------------------------------------------------------------------
    if not unit.available:
        starting = inp.started_ts is not None and now - inp.started_ts < ROOM_DATA_GRACE_S
        dropout = inp.heat_pump_gone_s is not None and inp.heat_pump_gone_s <= HEAT_PUMP_DROPOUT_S
        kept = mem if starting or dropout else replace(mem, running_since_ts=None)
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "midea_unavailable"), kept

    step, mem = fan_run_on(unit, mem, now, active)
    if step == "stop":
        return cmd(HEAT_PUMP_PLAN_OFF, "stop", "fan_run_on"), mem
    if step == "wait":
        return cmd(HEAT_PUMP_PLAN_OFF, "none", "fan_run_on", waiting="fan_run_on"), mem

    def stop(reason: str, blockers=()) -> tuple[HeatPumpCommand, ControlMemory]:
        new = replace(
            mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, last_hvac_mode="off", fan_until_ts=None,
            resting=False, phase_since_ts=None, above_cap_since_ts=None, reading_high_since_ts=None, cool_lifted=False,
            cool_warm_since_ts=None, cool_blind_since_ts=None, learning_until_ts=None,
            # what a heating run left behind, where a change of season ends one
            boiler_helps=False, lifted=False, lagging_since_ts=None, drying_held=False, fan_turned_up_from=None,
            fan_turned_up_to=None, econ_rest_since_ts=None,
        )
        just_sent = mem.last_command_ts is not None and now - mem.last_command_ts <= MANUAL_DETECT_GRACE_S
        if active and (unit.hvac_mode or "off") == "off" and not just_sent:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, blockers=blockers), new
        # The fan runs on and dries the coil, also after a long rest: the coil is wet as long as the air is damp.
        if p.heat_pump_fan_run_on_s > 0 and (not unit.hvac_modes or "fan_only" in unit.hvac_modes):
            new = replace(new, last_hvac_mode="fan_only", fan_until_ts=now + p.heat_pump_fan_run_on_s)
        return cmd(HEAT_PUMP_PLAN_OFF, "stop", reason, blockers=blockers, stop_to=new.last_hvac_mode), new

    if mem.last_hvac_mode == "heat":
        # A heating run of ours from the heating season, also across a restart: ended, with its fan run-on, never taken
        # for a hand (the mirror of heat_pump_control: docs/PHASE12_COOLING.md §2.4).
        if (unit.hvac_mode or "off") == "heat":
            return stop("season_ended")
        mem = replace(mem, last_hvac_mode=None, running_since_ts=None, resting=False, phase_since_ts=None)
    if active and mem.manual_until_ts is not None:
        change = unit.setting_change
        during = change is not None and change.origin != hand.OURS and change.ts <= mem.manual_until_ts
        if during and change.ts + p.manual_hold_s > mem.manual_until_ts + 1.0:
            by = change.origin if change.origin in hand.DELIBERATE else (mem.manual_by or hand.DEVICE)
            mem = replace(mem, manual_until_ts=change.ts + p.manual_hold_s, manual_by=by)
        if mem.manual_until_ts is not None and mem.manual_until_ts <= now:
            mem = replace(mem, manual_until_ts=None, manual_by=None, manual_over=())

    shown = unit.hvac_mode or "off"
    if shown not in ("cool", "off"):
        # The "off" after our own run-on that the unit did not take leaves it in fan only: not somebody ventilating.
        ours_left_over = active and shown == "fan_only" and mem.last_hvac_mode == "off"
        if ours_left_over and changed_by(inp, mem) == NOT_CARRIED_OUT and retry_due(mem, now):
            again = replace(mem, last_command_ts=now, not_carried_out=mem.not_carried_out + 1)
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", NOT_CARRIED_OUT), again
        # heat, dry, fan only, auto: somebody's (the control never heats in the cooling season)
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", f"manual_mode_{shown}"), replace(mem, running_since_ts=None)

    blockers = _blockers(inp)
    settling = False
    if active:
        if mem.manual_until_ts is not None:
            if shown == "cool" and blockers:
                mem = replace(mem, manual_until_ts=None, manual_by=None, manual_over=())
                return stop("blocked", blockers)
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover", blockers=blockers), mem
        by = changed_by(inp, mem)
        if by == SETTLING:
            settling = True
        elif by == NOT_CARRIED_OUT:
            if retry_due(mem, now):
                mem = replace(mem, not_carried_out=mem.not_carried_out + 1)
                if mem.last_hvac_mode != "cool" and shown == "cool":
                    return stop(NOT_CARRIED_OUT)
                cooling = shown == "cool"
                mem = replace(
                    mem, last_hvac_mode=None, last_target=None, last_command_ts=None,
                    running_since_ts=mem.running_since_ts if cooling else None,
                )
        elif by is not None:
            new = replace(
                mem, manual_until_ts=now + p.manual_hold_s, manual_by=by, running_since_ts=None, last_hvac_mode=None,
                fan_until_ts=None, not_carried_out=0, resting=False, phase_since_ts=None, above_cap_since_ts=None,
                reading_high_since_ts=None, cool_lifted=False, cool_warm_since_ts=None,
                stopped_since_ts=now if shown != "cool" else mem.stopped_since_ts,
            )
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), new
        elif mem.not_carried_out:
            mem = replace(mem, not_carried_out=0)
        # a unit found cooling without a run of ours on record is taken over as a run that starts now
        if mem.last_hvac_mode in (None, "cool") and mem.running_since_ts is None and shown == "cool" and not settling:
            mem = replace(
                mem, running_since_ts=now, stopped_since_ts=None, last_command_ts=None, last_hvac_mode="cool",
                last_target=unit.target_temp, resting=False, phase_since_ts=None, above_cap_since_ts=None,
                reading_high_since_ts=None, cool_lifted=False, cool_warm_since_ts=None,
            )

    running = mem.running_since_ts is not None

    # ---- what stops at once ----------------------------------------------------------------------------
    if blockers:
        return stop("blocked", blockers) if running else (cmd(HEAT_PUMP_PLAN_OFF, "none", "blocked", blockers=blockers), mem)
    if inp.unit_window_open:
        reason = "cool_window_open_unit_room"
        return stop(reason) if running else (cmd(HEAT_PUMP_PLAN_OFF, "none", reason), mem)
    if settling:
        plan = HEAT_PUMP_PLAN_COOL if running else HEAT_PUMP_PLAN_OFF
        return cmd(plan, "none", SETTLING, target=mem.last_target if running else None, waiting=SETTLING), mem

    watching_since = unit.settings_watched_since_ts if unit.settings_watched_since_ts is not None else mem.last_command_ts
    begun = max((ts for ts in (watching_since, inp.started_ts) if ts is not None), default=None)
    just_begun = begun is not None and now - begun < ROOM_DATA_GRACE_S
    if running and just_begun and any(r.cool_target is not None and r.temperature is None for r in inp.rooms):
        return cmd(HEAT_PUMP_PLAN_COOL, "none", "waiting_for_room_data", target=mem.last_target, waiting="room_data"), mem

    normal = normal_fan(unit.fan_modes, inp.fan_stage)

    def keep(mem: ControlMemory, reason: str, target: float | None, waiting: str | None = None, at_once: bool = False):
        """A unit that goes on cooling is sent what it does not have yet: the setpoint, a newly chosen fan stage once."""
        fan, turn = None, False
        given = mem.fan_normal
        offered = not unit.fan_modes or normal in unit.fan_modes
        if given is not None and unit.fan_mode == given != normal and offered:
            fan, turn = normal, True  # still on the stage it was given, another one chosen since
        retarget = target is not None and (mem.last_target is None or abs(target - mem.last_target) >= ADJUST_MIN_STEP_K)
        recently = mem.last_command_ts is not None and now - mem.last_command_ts < ADJUST_MIN_INTERVAL_S
        if not (retarget or turn) or (recently and not (retarget and at_once)):
            if mem.last_command_ts is None and not (retarget or turn):
                mem = replace(mem, last_command_ts=now)
            waiting = waiting or ("rate_limit" if retarget else None)
            return cmd(HEAT_PUMP_PLAN_COOL, "none", reason, target=mem.last_target, waiting=waiting), mem
        new = replace(mem, last_command_ts=now, last_target=target if retarget else mem.last_target, last_hvac_mode="cool")
        if turn:
            new = replace(new, fan_normal=normal)
        return cmd(HEAT_PUMP_PLAN_COOL, "adjust", reason, target=new.last_target, fan=fan, waiting=waiting), new

    # ---- the rooms ---------------------------------------------------------------------------------------
    rooms = counted(inp.rooms)
    if not rooms:
        # No room counts for a missing reading alone (the room sensors away for a minute, Zigbee restarting): not
        # cooled blind, but not stopped at once either, which cost a quarter of an hour of fan and a hard start. The
        # unit rests at the highest setpoint for ten minutes and is stopped then (decided 2026-11-13).
        blind_since = mem.cool_blind_since_ts if mem.cool_blind_since_ts is not None else now
        if running and reading_missing(inp.rooms) and now - blind_since < ROOM_DATA_GRACE_S:
            mem = replace(mem, cool_blind_since_ts=blind_since)
            if mem.resting:
                return keep(mem, "waiting_for_room_data", ceiling(unit), waiting="room_data")
            if (shown == "cool" or not active) and ceiling_rests(unit, now):
                rest = replace(
                    mem, resting=True, phase_since_ts=now, above_cap_since_ts=None, reading_high_since_ts=None,
                    cool_lifted=False, cool_warm_since_ts=None,
                )
                return keep(rest, "waiting_for_room_data", ceiling(unit), waiting="room_data", at_once=True)
        return stop("cool_no_room") if running else (cmd(HEAT_PUMP_PLAN_OFF, "none", "cool_no_room"), mem)
    if mem.cool_blind_since_ts is not None:
        mem = replace(mem, cool_blind_since_ts=None)
    excess = max(round(r.temperature - r.cool_target, 3) for r in rooms)
    floor_limit = inp.floor + (FLOOR_HYSTERESIS_K if mem.cool_floor_hold else 0.0)
    below_floor = any(r.temperature is not None and r.temperature < floor_limit for r in inp.rooms if r.cooling_allowed)
    if below_floor != mem.cool_floor_hold:
        mem = replace(mem, cool_floor_hold=below_floor)
    need = excess >= RESUME_AT_K and not below_floor
    cool_enough = excess <= -REST_BELOW_K or below_floor
    rest_reason = "cool_floor_reached" if below_floor else "rooms_cool_compressor_rests"
    if not running:
        if not need:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", rest_reason if below_floor else "no_demand"), mem
        if mem.stopped_since_ts is not None and now - mem.stopped_since_ts < p.heat_pump_min_off_s:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "cooling", waiting="min_off_time"), mem
        # Right after the change of season the heat of the other one leaves the coil and the radiators first (§2.5)
        if inp.season_since_ts is not None and now - inp.season_since_ts < season_pause_s(p):
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "season_switch_pause", waiting="season_switch_pause"), mem
        target = latch_start(ref_reading(unit, mem, now), unit)
        if target is None:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "intake_temperature_unknown"), mem
        new = replace(
            mem, running_since_ts=now, stopped_since_ts=None, last_command_ts=now, last_hvac_mode="cool", last_target=target,
            resting=False, phase_since_ts=now, above_cap_since_ts=None, reading_high_since_ts=None, fan_normal=normal,
            cool_lifted=False, cool_warm_since_ts=None, fan_turned_up_from=None, fan_turned_up_to=None,
        )
        return cmd(HEAT_PUMP_PLAN_COOL, "start", "cooling", target=target, fan=normal), new

    phase_start = mem.phase_since_ts if mem.phase_since_ts is not None else mem.running_since_ts
    own = unit.indoor_temp

    # ---- resting at the highest setpoint, in cool mode ------------------------------------------------------
    if mem.resting:
        if need:
            if now - (phase_start or now) < p.heat_pump_min_off_s:
                return keep(mem, rest_reason, ceiling(unit), waiting="min_off_time")
            target = latch_running(own, unit) if runs(unit) else latch_start(ref_reading(unit, mem, now), unit)
            resumed = replace(mem, resting=False, phase_since_ts=now, above_cap_since_ts=None, reading_high_since_ts=None)
            return keep(resumed, "resumed_at_minimum", target, at_once=True)
        ran_through = unit.compressor_running_s is None or unit.compressor_running_s >= REST_FAILSAFE_S
        if runs(unit) and now - (phase_start or now) >= REST_FAILSAFE_S and ran_through:
            return stop("rest_not_reached")
        return keep(mem, rest_reason, ceiling(unit))

    # ---- cool enough: rest, or stop where the highest setpoint would not rest it -------------------------------
    if cool_enough:
        if now - (phase_start or now) < p.heat_pump_min_run_s and not below_floor:
            return keep(mem, "cooling", mem.last_target, waiting="min_run_time")
        if (shown == "cool" or not active) and ceiling_rests(unit, now):
            rest = replace(
                mem, resting=True, phase_since_ts=now, above_cap_since_ts=None, reading_high_since_ts=None,
                cool_lifted=False, cool_warm_since_ts=None,
            )
            return keep(rest, rest_reason, ceiling(unit), at_once=True)
        return stop(rest_reason)

    # ---- set once: a unit taken over -------------------------------------------------------------------------
    if mem.last_target is None or mem.phase_since_ts is None:
        target = latch_running(own, unit) if may_run(unit) else latch_start(ref_reading(unit, mem, now), unit)
        latched = replace(mem, phase_since_ts=phase_start, above_cap_since_ts=None, reading_high_since_ts=None)
        return keep(latched, "holding_at_minimum", target, at_once=True)

    # ---- a room that stays too warm -----------------------------------------------------------------------------
    lagging = active and any(lags(r) for r in rooms)
    warm_since = (mem.cool_warm_since_ts if mem.cool_warm_since_ts is not None else now) if lagging else None
    at_targets = excess <= RESUME_AT_K
    if warm_since != mem.cool_warm_since_ts:
        mem = replace(mem, cool_warm_since_ts=warm_since)
    lift = inp.shortfall == SHORTFALL_LIFT
    if mem.cool_lifted and (at_targets or not lift):
        target = latch_running(own, unit)
        down = replace(mem, cool_lifted=False, cool_warm_since_ts=None, above_cap_since_ts=None, reading_high_since_ts=None)
        return keep(down, "holding_at_minimum", target, at_once=True)
    # A step that cannot go out yet is not made: on record as made, it showed "turned up" at the old setpoint and
    # came ten minutes late (review 2026-11-13).
    recently = mem.last_command_ts is not None and now - mem.last_command_ts < ADJUST_MIN_INTERVAL_S
    if lift and own is not None and not at_targets:
        lowest = _held(own - LIFT_MAX_K, unit)
        full = excess >= LIFT_AT_ONCE_K and lowest is not None and mem.last_target - lowest >= ADJUST_MIN_STEP_K
        # At once only the first time: its reading falls fast at full power, and following it down at once was a
        # setpoint and a beep for every half degree (review 2026-11-13, as once in heating). After that as any step.
        if full and (not mem.cool_lifted or not recently):
            up = replace(mem, cool_lifted=True, cool_warm_since_ts=now)
            return keep(up, "cool_lift", lowest, at_once=True)
        if not full and not recently and warm_since is not None and now - warm_since >= LAG_S and lowest is not None:
            step = max(SETPOINT_STEP, unit.target_temp_step or 0.0)
            target = _held(max(mem.last_target - step, own - LIFT_MAX_K), unit)
            if target is not None and mem.last_target - target >= ADJUST_MIN_STEP_K:
                up = replace(mem, cool_lifted=True, cool_warm_since_ts=now)
                return keep(up, "cool_lift", target)
    hold_reason = "cool_too_warm_quiet" if lagging and not lift else ("cool_lift" if mem.cool_lifted else "cooling")
    if mem.cool_lifted:
        return keep(mem, hold_reason, mem.last_target)

    # ---- holding its least speed ------------------------------------------------------------------------------------
    # It stopped itself although a room is warm: set anew, once per stop.
    stood = stood_s(unit, now)
    if need and stood is not None and stood >= STAND_S:
        stopped = stopped_ts(unit)
        answered = max((ts for ts in (mem.last_command_ts, mem.stop_noted_ts) if ts is not None), default=None)
        if answered is None or (stopped is not None and stopped > answered):
            target = latch_start(own, unit)
            again = replace(mem, stop_noted_ts=now, above_cap_since_ts=None, reading_high_since_ts=None)
            if target is not None and abs(target - mem.last_target) >= ADJUST_MIN_STEP_K:
                if mem.last_command_ts is None or now - mem.last_command_ts >= ADJUST_MIN_INTERVAL_S:
                    return keep(again, "relatched_unit_stopped", target)
            else:
                mem = again
    # Its reading rose well above the setpoint while it runs: its own controller would climb past its least speed.
    if may_run(unit) and own is not None and own - mem.last_target > COOL_CAP_K:
        since = mem.above_cap_since_ts if mem.above_cap_since_ts is not None else now
        if now - since >= CAP_HOLD_S:
            target = latch_running(own, unit)
            if target is not None and abs(target - mem.last_target) >= ADJUST_MIN_STEP_K:
                return keep(replace(mem, above_cap_since_ts=None), "relatched_reading_rose", target)
        if since != mem.above_cap_since_ts:
            mem = replace(mem, above_cap_since_ts=since)
    elif mem.above_cap_since_ts is not None:
        mem = replace(mem, above_cap_since_ts=None)
    # Its reading fell towards where it stops itself: a degree above it, where it holds its least speed.
    low = None if own is None else mem.last_target - own
    if runs(unit) and low is not None and low >= COOL_READING_JUMP_K:
        since = mem.reading_high_since_ts if mem.reading_high_since_ts is not None else now
        if low >= COOL_UNIT_HYSTERESIS_K or now - since >= READING_HIGH_S:
            target = latch_running(own, unit)
            if target is not None and abs(target - mem.last_target) >= ADJUST_MIN_STEP_K:
                return keep(replace(mem, reading_high_since_ts=None), "relatched_reading_fell", target)
        if since != mem.reading_high_since_ts:
            mem = replace(mem, reading_high_since_ts=since)
    elif mem.reading_high_since_ts is not None:
        mem = replace(mem, reading_high_since_ts=None)
    return keep(mem, hold_reason, mem.last_target)
