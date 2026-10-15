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
  heat-up only raises the fan. A served room that stays below its target and
  barely rises gets the unit set higher, half a degree at a time (`_adapt_lift`).
  Only once that is used up the command says the unit does not get the room there
  (`delivers`), and the boiler brings what it still lacks.
* A quick heat-up and the drying of a room turn the fan up, the drying to the
  strongest stage the unit has, never down from a stronger one. Once neither
  applies any more the fan gets back the stage it was turned up from, as long as
  it still shows the one it was turned up to; a stage somebody chose meanwhile is
  left alone. Apart from that only a start sets the fan: a stage chosen by hand stays.
  Stages are sent as the unit spells them ("Auto" for "auto").
* Economics come from the advisor (measured COP, else learned/prior estimate).
  Rarely visited outdoor-temperature bins get short learning runs when the
  estimate is close to break-even, so the COP map can learn cold conditions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..const import (
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
from .heat_pump import USER_STATES
from .learning import MIN_BIN_COUNT
from ..models import HeatPumpCommand, HeatPumpState, Parameters, RoomResult

START_BELOW_TARGET_K = 0.3
STOP_ABOVE_TARGET_K = 0.2
# Split heat pumps typically refuse heating setpoints below 17 °C and report the
# clamped value back, which would look like a user change and hand the unit over for the
# time a change by hand is respected (Parameters.manual_hold_s).
SETPOINT_MIN = 17.0
SETPOINT_MAX = 30.0
SETPOINT_STEP = 0.5  # what is sent unless the unit names a coarser step of its own
ADJUST_MIN_INTERVAL_S = 600.0
ADJUST_MIN_STEP_K = 0.5
OFFSET_MIN = 0.5
OFFSET_MAX = 5.0
OFFSET_STEP = 0.5
OFFSET_REVIEW_S = 1800.0
RATE_TOO_SLOW_K_H = 0.3
# A unit that follows the rooms regulates on the air where it stands. With that room warmer than
# the served ones it throttles before they are warm (seen 2026-10-13: set to the bathroom's 21 °C,
# 22 °C at its own intake, the bathroom at 19.8 °C, and the boiler was called for it). The unit
# is then set higher in small steps, and the boiler helps only once that did not do it.
LIFT_MAX_K = 2.0  # what is added to the configured offset at most
LIFT_REVIEW_S = 1200.0  # a room lags this long before the next step: the last one needs time to show
LIFT_LOWER_S = 7200.0  # ... and this long passes after the last step before one back, with the rooms at their targets
LIFT_WARM_K = 0.5  # every served room this far above its target: the steps back come at the pace of the steps up
THROTTLED_WITHIN_K = 1.0  # intake air this close to the setpoint: the unit holds back, a higher setpoint helps
RATE_TOO_FAST_K_H = 1.5
LEARNING_MIN_RATIO = 0.85  # estimate must be at least 85 % of break-even
DRYING_MAX_OVERSHOOT_K = 1.5  # drying keeps asking for heat, but not without a ceiling
LEARNING_REPEAT_S = 24 * 3600.0
LEARNING_RUN_S = 3600.0
MANUAL_DETECT_GRACE_S = 120.0  # a unit may take this long to show what it was sent
# After a restart the room sensors report a minute or two late, and a running unit is not stopped
# for rooms that are merely not known yet. Ten minutes is ample for that. A sensor that is still
# silent then has failed, and its room must not hold the unit on for good.
ROOM_DATA_GRACE_S = 600.0
NOT_CARRIED_OUT = "not_carried_out"  # the unit differs from our command, and nobody touched it
SETTLING = "unit_settling"  # it differs, and it is too early to say why
RETRY_MAX_S = 3600.0  # a command the unit keeps ignoring is repeated ever more rarely, up to this
FAN_ECO = "auto"
FAN_BOOST = "high"
# Drying a room wants the most air a unit can move: it carries the damp out and dries the walls,
# whether the unit heats at the moment or not. Left on "auto", a unit that has reached its
# setpoint turns its fan down to the least. Makers name the top stage differently; the first of
# these a unit offers is taken.
FAN_STRONGEST = ("full", "turbo", "max", "strong", "high")
# targets that mean "nobody wants this room warm": a unit that follows the rooms is not run for them.
# The night setback is not among them: the unit then holds the setback temperature plus the offset.
RESTING_REASONS = ("mode_away", "away_humidity_guard", "mode_off_frost_protection", "heat_pump_cooling")

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "offset_k", "manual_until_ts", "running_since_ts", "stopped_since_ts",
    "last_command_ts", "last_target", "learning_until_ts", "last_offset_review_ts", "fan_until_ts",
    "lagging_since_ts",
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
    # the fan stage a drying or a quick heat-up turned the fan up from, to put back afterwards; None: not turned up
    fan_turned_up_from: str | None = None
    # ... and the stage it was turned up to: put back only while the unit still shows that one
    fan_turned_up_to: str | None = None
    # A unit that follows the rooms: since when a served room lags, and whether the unit, set as high
    # as it may be, still does not get it there, so that the boiler helps (see `_adapt_lift`).
    lagging_since_ts: float | None = None
    boiler_helps: bool = False

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
            "fan_turned_up_from": self.fan_turned_up_from,
            "fan_turned_up_to": self.fan_turned_up_to,
            "lagging_since_ts": self.lagging_since_ts,
            "boiler_helps": self.boiler_helps,
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
        fan, fan_to = data.get("fan_turned_up_from"), data.get("fan_turned_up_to")
        return cls(
            learning_bins=tuple(bins),
            fan_turned_up_from=fan if isinstance(fan, str) else None,
            fan_turned_up_to=fan_to if isinstance(fan_to, str) else None,
            last_hvac_mode=mode if isinstance(mode, str) else None,
            manual_by=by if by in hand.SHOWN else None,
            boiler_helps=data.get("boiler_helps") is True,
            **{key: as_float(data.get(key)) for key in _STORED_FLOATS},
        )

    def fresh_run_state(self) -> ControlMemory:
        """Keep what was learned, forget what a previous (possibly simulated) run left behind.

        A fan run-on stays on record. Forgotten in the middle of it (the control switched to
        planning and back), the unit's fan only was taken for somebody ventilating and left on
        for good. Whether the unit really is in it shows in the next cycle: one that is not is
        left as it is found.
        """
        run_on = self.fan_until_ts is not None
        return ControlMemory(
            offset_k=self.offset_k, learning_bins=self.learning_bins,
            fan_until_ts=self.fan_until_ts, stopped_since_ts=self.stopped_since_ts if run_on else None,
        )


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
    started_ts: float | None = None  # when the control itself began (Home Assistant started, the entry was loaded)



def to_step(value: float, step: float | None) -> float:
    """A setpoint the unit takes as it is: in half degrees, or in the unit's own step where that is coarser.

    A unit that takes whole degrees shows 22 °C for the 21.5 °C it was sent, which reads as a command
    it did not carry out, again and again. Halves go up, as such a unit rounds them itself.
    """
    if step is None or step <= SETPOINT_STEP:
        return round_half(value)
    return math.floor(value / step + 0.5) * step


def _in_range(setpoint: float) -> float:
    return min(max(setpoint, SETPOINT_MIN), SETPOINT_MAX)


def eco_setpoint(heat_pump: HeatPumpState, offset_k: float) -> float | None:
    # the intake air sensor when it works, else the unit's own reading
    base = _intake(heat_pump)
    if base is None:
        return None
    return _in_range(to_step(base + offset_k, heat_pump.target_temp_step))


def _intake(heat_pump: HeatPumpState) -> float | None:
    return heat_pump.intake_temp.value_or_none if heat_pump.intake_temp.valid else heat_pump.indoor_temp


def wanted_rooms(rooms: tuple[RoomResult, ...]) -> list[RoomResult]:
    """Served rooms somebody wants warm and the unit may heat (window shut)."""
    return [r for r in rooms if r.heat_pump_allowed and r.target_reason not in RESTING_REASONS]


def strongest_fan(offered: tuple[str, ...]) -> str | None:
    """The top fan stage of a unit, as the unit spells it. None when it offers none that is known."""
    by_name = {stage.lower(): stage for stage in offered}
    return next((by_name[name] for name in FAN_STRONGEST if name in by_name), None)


def spelled(offered: tuple[str, ...], stage: str) -> str:
    """A fan stage as the unit spells it ("Auto" for "auto"), as it is where the unit offers none like it.

    A unit that names its stages "Auto" ... "Turbo" was never given "auto" back after a drying:
    a stage the unit does not offer is not sent (review 2026-10-14).
    """
    return next((name for name in offered if name.lower() == stage), stage)


def _air(stage: str | None) -> int:
    """How much air a fan stage moves, as far as its name tells: 2 a top stage, 1 "high", 0 any other."""
    name = (stage or "").lower()
    if name == FAN_BOOST:
        return 1
    return 2 if name in FAN_STRONGEST else 0


def follow_offset(p: Parameters, mem: ControlMemory) -> float:
    """The offset of a unit that follows the rooms: the configured one, plus what the rooms asked for on top."""
    base = p.heat_pump_setpoint_offset_k
    learned = mem.offset_k if mem.offset_k is not None else base
    return min(max(learned, base), base + LIFT_MAX_K)


def room_setpoint(
    p: Parameters, rooms: tuple[RoomResult, ...], step: float | None = None, offset: float | None = None
) -> float | None:
    """The setpoint of a unit that follows the rooms: their highest target plus the offset. None without such a room."""
    wanted = wanted_rooms(rooms)
    if not wanted:
        return None
    offset = p.heat_pump_setpoint_offset_k if offset is None else offset
    return _in_range(to_step(max(r.target for r in wanted) + offset, step))


def fan_plan(
    unit: HeatPumpState, up: str | None, before: str | None, to: str | None
) -> tuple[str | None, bool, str | None, str | None]:
    """The fan stage the unit is to have, whether to ask for it, and the stages to put back later and turned up to.

    `up` is the stage a quick heat-up or a drying turns the fan up to (None: neither applies),
    `before` the stage it was turned up from and `to` the one it was turned up to (None: it is not
    turned up). A unit that shows no fan stage is asked once, for the turn up and for the turn back.
    Neither turned up nor to be put back, the fan is not ours to set (None): seen 2026-10-13, "full"
    chosen by hand stayed for 45 minutes, until the next setpoint of ours took "auto" along and put
    the fan back. A fan is never "turned up" to less air than it moves already, and it is put back
    only while it still shows the stage it was turned up to (review 2026-10-14: "full" chosen by
    hand went down to "high" with a quick heat-up, and a stage chosen during a quick heat-up or a
    drying was taken for ours at its end and "auto" sent).
    """
    shown = unit.fan_mode
    if up is not None:
        if before is not None and shown in (None, before, to, up):
            # turned up already, and not touched since: ours, not shown, or not carried out (asked again)
            if shown == to != up and _air(to) > _air(up):
                return to, False, before, to  # a drying's top stage stays through a quick heat-up
            return up, shown is not None and shown != up, before, up
        # not turned up yet, or somebody chose another stage meanwhile
        if _air(shown) > _air(up):
            return None, False, None, None  # it moves more air already: left as it is, nothing to put back
        return up, shown != up, shown or spelled(unit.fan_modes, FAN_ECO), up  # whoever had set that stage gets it back
    if before is None:
        return None, False, None, None
    if shown is None:
        return before, True, before, to
    if shown == before:
        return before, False, None, None  # back already: nothing left to put back
    if shown == to:
        return before, True, before, to
    return None, False, None, None  # somebody chose another stage meanwhile: it stays


def differs(inp: ControlInputs, mem: ControlMemory) -> bool:
    """The unit shows another mode or setpoint than our last command asked for."""
    if mem.last_hvac_mode is None:
        return False
    unit = inp.heat_pump
    if (unit.hvac_mode or "off") != mem.last_hvac_mode:
        return True
    if mem.last_hvac_mode != "heat" or mem.last_target is None or unit.target_temp is None:
        return False
    # half a step of the unit is its rounding of what was sent (21.5 °C sent before its step was known, 22 °C shown)
    off = abs(unit.target_temp - mem.last_target)
    return off >= SETPOINT_STEP and off > (unit.target_temp_step or 0.0) / 2.0


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
    if watched is not None and watched <= mem.last_command_ts + MANUAL_DETECT_GRACE_S:
        # Every change since our command is known, and there was none. A restart right after the
        # command counts the same: the unit had not had its time to follow, and nobody that of a hand.
        return NOT_CARRIED_OUT
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
    if inp.blocked_reason and inp.blocked_reason not in out:  # the advisor names the safety fallback as well
        out.append(inp.blocked_reason)
    if inp.heat_pump.error_code not in (None, 0):
        out.append(f"error_code_{inp.heat_pump.error_code}")
    return out


def _adapt_offset(inp: ControlInputs, mem: ControlMemory, offset: float, heating_rooms: list[RoomResult]) -> tuple[float, float | None]:
    """Every 30 min of an eco run: raise the offset if rooms barely warm up, lower it if they race.

    `heating_rooms` are the rooms that are to warm up. A room inside its hold band is none of them:
    it is held, its trend is near zero by design, and read as "too slow" it took the offset from
    1.5 to 5 K in eight reviews.
    """
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


def _adapt_lift(inp: ControlInputs, mem: ControlMemory, need: list[RoomResult], setpoint: float | None) -> ControlMemory:
    """Set a unit that follows the rooms higher for a served room it does not get warm, in small steps.

    A room lags that is below its target and rises more slowly than `RATE_TOO_SLOW_K_H`; an
    unknown trend is no evidence. After `LIFT_REVIEW_S` of that the offset goes up by half a degree,
    `LIFT_MAX_K` at most, while the unit holds back (its intake air near its setpoint). Once the
    offset is used up, or the unit works flat out already and a higher setpoint would change
    nothing, the boiler helps until the rooms are at their targets. With the rooms at their targets
    `LIFT_LOWER_S` after the last step, the offset takes a step back; every step makes the unit
    beep, so there are few. Rooms clearly too warm (`LIFT_WARM_K`) take one every `LIFT_REVIEW_S`:
    seen 2026-10-13, the unit ran for 1.7 h at the raised setpoint with every served room 1-2 K above
    its target, because the two hours had begun anew with a restart.
    """
    p, now = inp.params, inp.now_ts
    base, offset = p.heat_pump_setpoint_offset_k, follow_offset(p, mem)
    last = mem.last_offset_review_ts if mem.last_offset_review_ts is not None else mem.running_since_ts
    # A room that is being dried says nothing: its window may be open, and it is above its usual target.
    judged = [r for r in need if not r.drying.active]
    if any(r.trend_k_per_h is not None and r.trend_k_per_h < RATE_TOO_SLOW_K_H for r in judged):
        since = mem.lagging_since_ts if mem.lagging_since_ts is not None else now
        if since != mem.lagging_since_ts:
            mem = replace(mem, lagging_since_ts=since)
        if mem.boiler_helps or now - max(since, last or since) < LIFT_REVIEW_S:
            return mem
        intake = _intake(inp.heat_pump)
        holds_back = setpoint is not None and intake is not None and intake >= setpoint - THROTTLED_WITHIN_K
        if holds_back and offset < base + LIFT_MAX_K:
            step = max(OFFSET_STEP, inp.heat_pump.target_temp_step or 0.0)
            return replace(mem, offset_k=min(offset + step, base + LIFT_MAX_K), last_offset_review_ts=now)
        return replace(mem, boiler_helps=True, last_offset_review_ts=now)
    if mem.lagging_since_ts is not None:
        mem = replace(mem, lagging_since_ts=None)
    known = [r for r in wanted_rooms(inp.rooms) if r.temperature is not None]
    if not known or any(r.temperature < r.target for r in known):
        return mem  # on their way: nothing to change
    if mem.boiler_helps:
        mem = replace(mem, boiler_helps=False)
    warm = all(r.temperature >= r.target + LIFT_WARM_K for r in known)
    if offset > base and last is not None and now - last >= (LIFT_REVIEW_S if warm else LIFT_LOWER_S):
        step = max(OFFSET_STEP, inp.heat_pump.target_temp_step or 0.0)
        mem = replace(mem, offset_k=max(offset - step, base), last_offset_review_ts=now)
    return mem


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
    unit = inp.heat_pump
    active = inp.control_mode == CTRL_ACTIVE

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
            delivers=not gives_up,
        )

    # A unit that follows the rooms' target regulates by itself. Whether it gets the rooms there is
    # judged by the rooms (`_adapt_lift`), not by its intake air: at its setpoint there, it still ran
    # and the bathroom rose by 2 K an hour (2026-10-13), and the boiler was called all the same.
    follows = p.heat_pump_follow_rooms
    own = room_setpoint(p, inp.rooms, unit.target_temp_step, follow_offset(p, mem)) if follows else None
    gives_up = follows and mem.boiler_helps

    if inp.control_mode == CTRL_OFF:
        return cmd(HEAT_PUMP_PLAN_DISABLED, "none", "control_off"), replace(mem, running_since_ts=None)

    # ---- hands off -------------------------------------------------------------
    if inp.run_state == HEAT_PUMP_UNAVAILABLE:
        # Right after a start the unit's entity may not be loaded yet, and its run is not forgotten for
        # that. Seen 2026-10-13 after three restarts: the next cycle took the running unit over as a run
        # of its own, planned what it had already, and the two hours of `_adapt_lift` began anew.
        starting = inp.started_ts is not None and now - inp.started_ts < ROOM_DATA_GRACE_S
        kept = mem if starting else replace(mem, running_since_ts=None)
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "midea_unavailable"), kept
    # ---- the fan runs on after a stop of ours, then the unit is switched off --------------
    if mem.fan_until_ts is not None:
        shown = unit.hvac_mode or "off"
        ours = not active or shown == "fan_only"
        settling = mem.last_command_ts is not None and now - mem.last_command_ts <= MANUAL_DETECT_GRACE_S
        asked = hand.deliberate_since(unit.setting_change, mem.last_command_ts) is not None
        if not ours and shown == "off" and (asked or not settling):
            # switched off at the unit or in Home Assistant: where the run-on was heading anyway.
            # Not while the unit may still be on its way into fan only and shows "off" in passing.
            # A run-on carried over from before the control was switched (no command on record)
            # leaves nothing behind: a unit started by hand later is then found and taken over.
            mem = replace(mem, fan_until_ts=None, last_hvac_mode="off" if mem.last_command_ts is not None else None)
        elif not ours and (asked or not settling):
            # another mode than ours: the run-on is over, and whose change it was is judged below
            mem = replace(mem, fan_until_ts=None)
        elif now >= mem.fan_until_ts and (active or shown != "fan_only"):
            new = replace(mem, fan_until_ts=None, last_command_ts=now, last_hvac_mode="off")
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", "fan_run_on"), new
        else:
            # Also past its end while the control only plans and the unit really is in fan only:
            # the "off" cannot go out now, and it is still owed once the control is active again.
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "fan_run_on", waiting="fan_run_on"), mem

    def stop(reason: str, blockers=()) -> tuple[HeatPumpCommand, ControlMemory]:
        new = replace(
            mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, learning_until_ts=None,
            last_hvac_mode="off", fan_until_ts=None,
            fan_turned_up_from=None, fan_turned_up_to=None,  # the next start sets the fan anyway
            lagging_since_ts=None, boiler_helps=False,  # a run that ended judges nothing any more
        )
        just_sent = mem.last_command_ts is not None and now - mem.last_command_ts <= MANUAL_DETECT_GRACE_S
        if active and (unit.hvac_mode or "off") == "off" and not just_sent:
            # Somebody switched it off meanwhile, or a start was not carried out: there is nothing to
            # stop. A "fan only" sent now starts the fan of a unit that stands still and runs it for a
            # quarter of an hour. (Right after a command of ours the unit may only not show it yet.)
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, blockers=blockers), new
        # the fan runs on where the unit has a mode for it; one without would refuse the stop every cycle
        if p.heat_pump_fan_run_on_s > 0 and (not unit.hvac_modes or "fan_only" in unit.hvac_modes):
            new = replace(new, last_hvac_mode="fan_only", fan_until_ts=now + p.heat_pump_fan_run_on_s)
        return cmd(HEAT_PUMP_PLAN_OFF, "stop", reason, blockers=blockers, stop_to=new.last_hvac_mode), new

    if active and mem.manual_until_ts is not None:
        # A further change during the time in hand renews it, as on a room thermostat; one after
        # its end is a new matter. Its end leaves nothing behind, whatever mode the unit is in.
        change = unit.setting_change
        during = change is not None and change.origin != hand.OURS and change.ts <= mem.manual_until_ts
        if during and change.ts + p.manual_hold_s > mem.manual_until_ts + 1.0:
            by = change.origin if change.origin in hand.DELIBERATE else (mem.manual_by or hand.DEVICE)
            mem = replace(mem, manual_until_ts=change.ts + p.manual_hold_s, manual_by=by)
        if mem.manual_until_ts is not None and mem.manual_until_ts <= now:
            mem = replace(mem, manual_until_ts=None, manual_by=None)
    if inp.run_state in USER_STATES:
        # The "off" after our own run-on that the unit did not take leaves it in fan only: that is
        # not somebody ventilating, and without a second "off" it would stay there for good.
        ours_left_over = active and inp.run_state == HEAT_PUMP_FAN_ONLY and mem.last_hvac_mode == "off"
        if ours_left_over and changed_by(inp, mem) == NOT_CARRIED_OUT and retry_due(mem, now):
            again = replace(mem, last_command_ts=now, not_carried_out=mem.not_carried_out + 1)
            return cmd(HEAT_PUMP_PLAN_OFF, "stop", NOT_CARRIED_OUT), again
        return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", f"manual_mode_{unit.hvac_mode}"), replace(mem, running_since_ts=None)

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
                if mem.last_hvac_mode != "heat" and unit.hvac_mode == "heat":
                    return stop(NOT_CARRIED_OUT)  # it was to stop and still heats: stop it again
                # it was to heat: forget the command, and the plan below sends what is due
                heating = unit.hvac_mode == "heat"
                mem = replace(
                    mem, last_hvac_mode=None, last_target=None, last_command_ts=None,
                    running_since_ts=mem.running_since_ts if heating else None,
                )
        elif by is not None:
            off_now = unit.hvac_mode != "heat"
            new = replace(
                mem, manual_until_ts=now + p.manual_hold_s, manual_by=by, running_since_ts=None, last_hvac_mode=None,
                fan_until_ts=None, not_carried_out=0, lagging_since_ts=None, boiler_helps=False,
                # switched off by hand: the minimum pause counts from now once the unit is ours again
                stopped_since_ts=now if off_now else mem.stopped_since_ts,
            )
            return cmd(HEAT_PUMP_PLAN_HANDS_OFF, "none", "manual_takeover"), new
        elif mem.not_carried_out:
            # the unit shows what was sent again, or nothing of ours is on record any more
            mem = replace(mem, not_carried_out=0)
        # A unit found heating without a run of ours on record is taken over as a run that starts
        # now: the control just switched to active, the hands-off time after a change by hand is
        # over, or the unit was out of reach or in another mode for a while and is back as we left
        # it (a different setpoint was judged above: that is a change by hand). Without this nothing
        # would ever stop it: no target reached, no mode "off", no icing lockout. The setpoint it shows
        # counts as the last one sent, and ours goes out in this cycle only where it differs (seen
        # 2026-10-13: an "adjust" to what it had already, on record as sent). A stop for want of
        # demand waits for the full minimum run.
        if mem.last_hvac_mode in (None, "heat") and mem.running_since_ts is None and unit.hvac_mode == "heat" and not settling:
            mem = replace(
                mem, running_since_ts=now, stopped_since_ts=None, last_command_ts=None,
                last_hvac_mode="heat", last_target=unit.target_temp, last_offset_review_ts=None,
                lagging_since_ts=None, boiler_helps=False,
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
    # Only for the first minutes of watching, though: after that a room without a temperature
    # simply does not count, or one dead sensor would keep the unit on for good.
    # The watch on the unit is carried across a restart, so its age alone says nothing about one:
    # seen 2026-10-13, the first cycle after a restart found the bathroom without a temperature,
    # took the night setback of the other room for the target and set the unit from 21 to 18.5 °C.
    watching_since = unit.settings_watched_since_ts if unit.settings_watched_since_ts is not None else mem.last_command_ts
    begun = max((ts for ts in (watching_since, inp.started_ts) if ts is not None), default=None)
    just_begun = begun is not None and now - begun < ROOM_DATA_GRACE_S
    if running and just_begun and any(r.heat_pump_allowed and r.temperature is None for r in inp.rooms):
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

    # Worth running: its heat is the cheaper one, or it is the source recommended, or the only one
    # allowed. The recommendation alone was not enough: it names a source only while a room calls
    # the boiler (from 0.3 K below its target to 0.1 K below). A unit switched on and off by the
    # rooms was stopped as "gas cheaper" the moment the call ended, never reaching the 0.2 K above
    # target it is to heat to, and never started for a room whose radiator thermostat is switched off.
    recommended = inp.advice_source in (SOURCE_HEAT_PUMP, SOURCE_BOTH)
    economic = inp.cheaper or recommended or inp.op_mode == MODE_HEAT_PUMP_ONLY
    # rooms somebody wants warm: a unit that follows the rooms stays on for them
    awake = wanted_rooms(inp.rooms) if follows else []  # with or without a temperature: the unit regulates by itself
    learning_active = mem.learning_until_ts is not None and mem.learning_until_ts > now
    learning_new = False
    if (
        not economic
        and not learning_active
        and p.heat_pump_learning_runs
        and inp.outdoor_bin is not None
        and inp.bin_count < MIN_BIN_COUNT
        and inp.expected_cop is not None
        and inp.break_even_cop is not None
        and inp.expected_cop >= LEARNING_MIN_RATIO * inp.break_even_cop
        and (need or drying)
    ):
        last = dict(mem.learning_bins).get(inp.outdoor_bin)
        learning_new = last is None or now - last >= LEARNING_REPEAT_S
    # A learning run begins with a start, which puts its bin and its end on record. A unit that
    # runs already begins none: once the weather moved into the next bin, "new" every cycle and
    # never on record, the run went on without an end.
    learning = learning_active or (learning_new and not running)

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

    # A room that is being dried gets the strongest fan, also above its ceiling for the heat and
    # while the unit idles at its setpoint: moved air dries without heat too.
    dries = any(r.heat_pump_allowed and r.drying.active for r in inp.rooms)
    drying_fan = strongest_fan(unit.fan_modes) if dries else None
    eco_fan, boost_fan = spelled(unit.fan_modes, FAN_ECO), spelled(unit.fan_modes, FAN_BOOST)

    def fan_up(load: str | None) -> str | None:
        """The stage the fan is turned up to: a quick heat-up has its own, also during a drying. None: not turned up."""
        return boost_fan if load == "boost" else drying_fan

    def setpoint(load: str | None) -> float | None:
        if follows:
            return own if own is not None else mem.last_target
        return SETPOINT_MAX if load == "boost" else eco_setpoint(unit, offset)

    def keep_heating(mem: ControlMemory, reason: str, load: str | None, target: float | None, waiting: str | None = None):
        """A unit that goes on heating is sent what it does not have yet: the setpoint, the fan stage."""
        up = fan_up(load)
        first = up is not None and mem.fan_turned_up_from is None
        fan, turn, before, to = fan_plan(unit, up, mem.fan_turned_up_from, mem.fan_turned_up_to)
        if (before, to) != (mem.fan_turned_up_from, mem.fan_turned_up_to):
            mem = replace(mem, fan_turned_up_from=before, fan_turned_up_to=to)
        retarget = target is not None and (mem.last_target is None or abs(target - mem.last_target) >= ADJUST_MIN_STEP_K)
        recently = mem.last_command_ts is not None and now - mem.last_command_ts < ADJUST_MIN_INTERVAL_S
        # At once: the first turn of the fan (with a setpoint that is due as well, however recent the
        # last command), the full power of a quick heat-up and the way down from it. Everything else
        # keeps the pace of an adjustment, a repeat for a unit that did not follow included.
        from_full_power = not follows and mem.last_target == SETPOINT_MAX
        at_once = (first and turn) or (retarget and (load == "boost" or from_full_power))
        if not (retarget or turn) or (recently and not at_once):
            waiting = waiting or ("rate_limit" if retarget else None)
            if mem.last_command_ts is None and not (retarget or turn):
                # taken over with what it is to have: as if sent now, so that a change after it is judged
                mem = replace(mem, last_command_ts=now)
            return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=fan, load=load, waiting=waiting), mem
        new = replace(mem, last_command_ts=now, last_target=target if retarget else mem.last_target, last_hvac_mode="heat")
        if up is None and unit.fan_mode is None:
            new = replace(new, fan_turned_up_from=None, fan_turned_up_to=None)  # asked once, see fan_plan
        return cmd(HEAT_PUMP_PLAN_HEAT, "adjust", reason, target=new.last_target, fan=fan, load=load, waiting=waiting), new

    if not running:
        if not wants_start:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason if (need or drying or boost or awake) else "no_demand"), mem
        if mem.stopped_since_ts is not None and now - mem.stopped_since_ts < p.heat_pump_min_off_s:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, waiting="min_off_time"), mem
        target, up = setpoint(load), fan_up(load)
        if target is None:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "intake_temperature_unknown"), mem
        bins = mem.learning_bins
        learning_until = mem.learning_until_ts
        if learning_new and inp.outdoor_bin is not None:
            learning_until = now + LEARNING_RUN_S
            # a learning run only counts once it really ran; a planned one must not use up the bin
            if active:
                bins = tuple((b, t) for b, t in bins if b != inp.outdoor_bin) + ((inp.outdoor_bin, now),)
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
            fan_turned_up_from=eco_fan if up else None,  # a start sets the fan anyway: that is what it gets back
            fan_turned_up_to=up,
            lagging_since_ts=None,
            boiler_helps=False,
        )
        return cmd(HEAT_PUMP_PLAN_HEAT, "start", reason, target=target, fan=up or eco_fan, load=load), new

    # running
    if not keep_running:
        if now - (mem.running_since_ts or now) < p.heat_pump_min_run_s:
            # target reached, or economics turned: finish the minimum run (in eco when the
            # gas boiler takes over afterwards)
            if rests:
                reason, load = "comfort_time_over", "eco"
            else:
                reason, load = ("target_reached", load) if permitted else (reason, "eco")
            # At the setpoint it has, unless that is the full power of a quick heat-up that reached
            # its target: left at 30 °C, the unit would drive the room on for the rest of the minimum run.
            full_power = not follows and mem.last_target == SETPOINT_MAX
            return keep_heating(mem, reason, load, setpoint(load) if full_power else mem.last_target, waiting="min_run_time")
        return stop("comfort_time_over" if rests else ("target_reached" if permitted else reason))

    # only a real run tells how fast the rooms warm; a planned run heats nothing
    if not follows and load in ("eco", "learning") and active:
        offset, review_ts = _adapt_offset(inp, mem, offset, need)
        if review_ts != mem.last_offset_review_ts:
            mem = replace(mem, offset_k=offset, last_offset_review_ts=review_ts)
    if follows and load == "eco" and active:
        mem = _adapt_lift(inp, mem, need, own)
        own = room_setpoint(p, inp.rooms, unit.target_temp_step, follow_offset(p, mem))
        gives_up = mem.boiler_helps
    return keep_heating(mem, reason, load, setpoint(load))
