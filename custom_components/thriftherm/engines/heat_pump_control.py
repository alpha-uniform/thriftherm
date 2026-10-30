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
  coil into the rooms, and is switched off after that. Out of a long rest, its
  fan running all along, it is switched off at once.
* Hold at minimum, the rooms decide the rest (2026-10-30). The Midea regulates on
  its own reading where it stands, not on the served rooms, so its setpoint does
  one job only: it holds the compressor at its least speed. Set a degree below its
  own reading (`AIM_K`), a PortaSplit ran the night of 2026-10-29/30 at 24 Hz,
  about 340 W and a COP of 3.4-3.6, and the rooms drifted slowly instead of rising;
  a degree above it, its own controller climbed to 600-690 W and the rooms rose a
  degree an hour. The setpoint is set once per phase (a start, a resume, a
  takeover, the end of a quick heat-up) and then held: a room target that changes
  sends nothing, and a reading that rises never raises it (that chase cost 20
  beeps that night). It is set anew only where the unit stopped itself while a
  room is short, or its reading fell well below the setpoint (`CAP_K`).
* The coldest served room decides run or rest. Half a degree above its target
  the unit rests at the lowest setpoint, in heat mode: from there it restarts
  softly, where every start from off or fan only boosted to 60-79 Hz. At its
  target the unit heats again. A unit in a room too cold to rest by setpoint is
  stopped instead, and one the lowest setpoint does not stop as well.
* More power comes only from a quick heat-up (30 °C and the fan high). A served
  room that lags lets the boiler add the peak (`delivers`), and the unit stays at
  its least speed: above it the extra heat costs more than the boiler's. Only
  without a boiler, or in the mode "heat pump only", is the unit set higher, half
  a degree at a time.
* A quick heat-up and the drying of a room turn the fan up, the drying to the
  strongest stage the unit has, never down from a stronger one. Once neither
  applies any more the fan gets back the stage it was turned up from, as long as
  it still shows the one it was turned up to; a stage somebody chose meanwhile is
  left alone. Apart from that only a start and a newly chosen stage of normal operation
  set the fan: a stage chosen by hand stays.
  Stages are sent as the unit spells them ("Auto" for "auto"). A drying counts as
  a room that is short: it never rests, and the compressor stays at its least speed.
* In normal operation the fan has the stage chosen in the options ("auto" unless
  chosen otherwise): a start sets it, a turn up gives it back, and a unit still on
  the stage it was given before gets the one chosen now, once. One chosen by hand stays.
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

# Split heat pumps typically refuse heating setpoints below 17 °C and report the
# clamped value back, which would look like a user change and hand the unit over for the
# time a change by hand is respected (Parameters.manual_hold_s).
SETPOINT_MIN = 17.0
SETPOINT_MAX = 30.0
SETPOINT_STEP = 0.5  # what is sent unless the unit names a coarser step of its own
ADJUST_MIN_INTERVAL_S = 600.0
ADJUST_MIN_STEP_K = 0.5
RATE_TOO_SLOW_K_H = 0.3
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
# The fan stage of normal operation is an option (Parameters.heat_pump_fan_stage, 2026-10-30). Measured 2026-10-13,
# "full" gave about 7 % more heat than "auto" at the same power, and "auto" turns slower than "silent" at low load;
# more air also carries more of the heat through the hoses into the served rooms. It is louder, so "auto" stays
# the default. "full" is the strongest stage the unit has (FAN_STRONGEST), and a stage it does not offer is "auto".
FAN_BOOST = "high"
# Drying a room wants the most air a unit can move: it carries the damp out and dries the walls,
# whether the unit heats at the moment or not. Left on "auto", a unit that has reached its
# setpoint turns its fan down to the least. Makers name the top stage differently; the first of
# these a unit offers is taken.
FAN_STRONGEST = ("full", "turbo", "max", "strong", "high")
# targets that mean "nobody wants this room warm": the unit is not run for them. The night setback
# is not among them: the rooms then decide at the setback temperature.
RESTING_REASONS = ("mode_away", "away_humidity_guard", "mode_off_frost_protection", "heat_pump_cooling")

# ---- hold at minimum (measured on a PortaSplit 2026-10-20 to 2026-10-30, 148 compressor events) ----
# Setpoint minus its own reading while the compressor runs. From -1.5 to -0.5 it held 24 Hz and 342 W;
# -1.0 ran 620 minutes without a stop, -1.5 stopped after about 4 minutes (it stops at about -1.75).
# A constant, not learned (2026-10-30): two short stops of its own in a row raised it by half a degree,
# meant for units with less hysteresis. On the owner's PortaSplit those stops come from the office warming
# (a PC of up to 460 W), so it would have learned -0.5 for good, and every start would boost to 60-79 Hz.
AIM_K = -1.0
# Its own reading rises this much once the compressor runs (median 1.42 of 48 restarts), and falls as
# much within 4-5 minutes of a stop: the reading sits in the still air at the coil.
READING_JUMP_K = 1.5
# A start or a resume is set the aim plus this jump above its standing reading: half a degree with AIM_K.
# A degree and more above it, a start boosted to 60-79 Hz within 1-3 minutes.
# Setpoint minus its own reading while it runs, at most (0 and +0.5 gave 28 Hz; +1.0 gave 48 Hz and 624 W),
# ... and while a room lags with no boiler to help.
CAP_K = 0.5
CAP_LIFT_K = 1.5
CAP_HOLD_S = 600.0  # above the cap this long with the compressor running: set anew from its reading
REST_ABOVE_K = 0.5  # the coldest served room this far above its target: the unit rests
RESUME_AT_K = 0.0  # ... and at its target it heats again
# A rest by setpoint needs its own reading this far above the lowest setpoint: it stops at about
# 1.75-2 degrees above its setpoint. In a colder room it is stopped by mode instead.
UNIT_HYSTERESIS_K = 2.0
REST_FAILSAFE_S = 900.0  # its compressor still runs this long after the lowest setpoint went out: stopped by mode
STAND_S = 300.0  # its compressor at 0 Hz this long: the unit stopped itself (its shortest restart came after 5.4 min)
# A defrost stops the compressor and its own reading falls to 15.5-17.5 °C: nothing is set anew
# during it and for this long after it.
DEFROST_HOLD_S = 900.0
# Off for this long its own reading stands still (1.9 degrees above the intake sensor, seen
# 2026-10-28): a start is measured from the intake sensor then.
STALE_OFF_S = 1800.0
PREHEAT_JUDGE_S = 1800.0  # a preheat is judged for "behind" only after this long: a trend needs time
BEHIND_MARGIN_K = 0.1  # projected to its comfort start this far below its target: behind
BOOST_C = 30.0  # quick heat-up
LATCH_ABOVE_TARGET_MAX_K = 3.0  # a setpoint held never more than this above the warmest wanted target (a stuck reading)
# A served room this far below its target that barely rises (RATE_TOO_SLOW_K_H) for this long lags.
# Tuned in the simulation of 2026-10-20 to 2026-10-30: at 0.3 K and 20 minutes the rooms missed their
# comfort target by 0.3 K and more for 249 minutes in ten days, at 0.2 K and 10 minutes for none.
LAG_BELOW_K = 0.2
LAG_S = 600.0
# "Warm up slower" with a boiler: it helps a served room only this far below its target (comfort, setback or away),
# or a preheat projected this far short of its comfort start, and then until the room is there. Simulated
# 2026-10-30 on 10 recorded days of the owner's flat (comfort 21, setback 20): plain "slower" left the rooms
# 0.3-0.6 degrees below target on average and up to 1.7 in frost; with this floor they sat 0.13-0.18 below,
# at 2.07 instead of 2.14 EUR a day for "boiler helps", with 2-4 boiler runs of about 40 minutes a day instead of
# 5-9 of about 15. Ending the help at the floor less a band instead gave many 10-minute boiler runs, a floor of
# 1.0 or more never helped in mild weather, and setback rooms mattered only in frost (about 0.02 EUR a day).
# A preheating room counts by its projection only: a degree below as it begins, the floor would help every preheat.
SLOWER_FLOOR_K = 0.5
# A start from off boosts to 60-79 Hz within 1-3 minutes (seen 2026-10-20 to 30): its heat-up rates count
# from this long after the compressor started, at the least speed a preheat plans with.
SETTLED_RUN_S = 600.0

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "manual_until_ts", "running_since_ts", "stopped_since_ts", "last_command_ts", "last_target",
    "learning_until_ts", "fan_until_ts", "lagging_since_ts", "phase_since_ts",
)


@dataclass(frozen=True)
class ControlMemory:
    """Controller state carried between cycles (persisted by the coordinator)."""

    running_since_ts: float | None = None
    stopped_since_ts: float | None = None
    last_command_ts: float | None = None
    last_hvac_mode: str | None = None
    last_target: float | None = None  # the setpoint sent last; while it runs the one held (latched)
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
    # since when a served room lags, and whether the boiler adds the peak for it (`delivers` is False then)
    lagging_since_ts: float | None = None
    boiler_helps: bool = False
    resting: bool = False  # the unit rests at the lowest setpoint, in heat mode
    # without a boiler to help: the setpoint was set higher for a lagging room, and stays so until every room is there
    lifted: bool = False
    phase_since_ts: float | None = None  # start of the current run or rest; None: not set since it was taken over
    # the fan stage of normal operation the unit was given last; None: "auto", what every start set before the option
    fan_normal: str | None = None
    # not stored: when its last stop of its own was answered (a setpoint sent or not), since when its reading
    # is below the setpoint by more than the cap, and until when a defrost holds the setpoint
    stop_noted_ts: float | None = None
    above_cap_since_ts: float | None = None
    defrost_until_ts: float | None = None

    def to_storage(self) -> dict:
        # The run state is persisted too: after a restart mid-run the controller must
        # still know it started the unit, or no blocker and no "target reached" stops it.
        return {
            "learning_bins": [list(x) for x in self.learning_bins],
            "manual_until_ts": self.manual_until_ts,
            "manual_by": self.manual_by,
            "running_since_ts": self.running_since_ts,
            "stopped_since_ts": self.stopped_since_ts,
            "last_command_ts": self.last_command_ts,
            "last_hvac_mode": self.last_hvac_mode,
            "last_target": self.last_target,
            "learning_until_ts": self.learning_until_ts,
            "fan_until_ts": self.fan_until_ts,
            "fan_turned_up_from": self.fan_turned_up_from,
            "fan_turned_up_to": self.fan_turned_up_to,
            "lagging_since_ts": self.lagging_since_ts,
            "boiler_helps": self.boiler_helps,
            "resting": self.resting,
            "lifted": self.lifted,
            "phase_since_ts": self.phase_since_ts,
            "fan_normal": self.fan_normal,
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
        fan, fan_to, normal = data.get("fan_turned_up_from"), data.get("fan_turned_up_to"), data.get("fan_normal")
        return cls(
            learning_bins=tuple(bins),
            fan_turned_up_from=fan if isinstance(fan, str) else None,
            fan_turned_up_to=fan_to if isinstance(fan_to, str) else None,
            fan_normal=normal if isinstance(normal, str) else None,
            last_hvac_mode=mode if isinstance(mode, str) else None,
            manual_by=by if by in hand.SHOWN else None,
            boiler_helps=data.get("boiler_helps") is True,
            resting=data.get("resting") is True,
            lifted=data.get("lifted") is True,
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
            learning_bins=self.learning_bins,
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
    defrosting: bool = False  # a defrost is suspected right now (engines/defrost.py)
    # False without a boiler, in the mode "heat pump only", and where the option for a shortfall is "heat pump runs higher"
    boiler_can_help: bool = True
    may_lift: bool = True  # without the boiler's help the unit is set higher for a lagging room; False: "warm up slower"
    # "warm up slower" with a boiler: it helps only from SLOWER_FLOOR_K below a target, not for a room that merely lags
    help_at_floor: bool = False


def to_step(value: float, step: float | None) -> float:
    """A setpoint the unit takes as it is: in half degrees, or in the unit's own step where that is coarser.

    A unit that takes whole degrees shows 22 °C for the 21.5 °C it was sent, which reads as a command
    it did not carry out, again and again. Halves go up, as such a unit rounds them itself.
    """
    if step is None or step <= SETPOINT_STEP:
        return round_half(value)
    return math.floor(value / step + 0.5) * step


def _intake(heat_pump: HeatPumpState) -> float | None:
    return heat_pump.intake_temp.value_or_none if heat_pump.intake_temp.valid else heat_pump.indoor_temp


def ref_reading(unit: HeatPumpState, mem: ControlMemory, now: float) -> float | None:
    """The reading a setpoint is measured from: its own, or the intake sensor where its own stands still.

    Off for half an hour and more its own reading no longer moves (seen 2026-10-28: 1.9 degrees above the
    intake sensor beside it). Without the intake sensor its own reading counts after all, and the other way round.
    """
    off_long = unit.hvac_mode not in ("heat", "fan_only") and (
        mem.stopped_since_ts is None or now - mem.stopped_since_ts >= STALE_OFF_S
    )
    if off_long or unit.indoor_temp is None:
        return _intake(unit)
    return unit.indoor_temp


def runs(unit: HeatPumpState) -> bool:
    """Its compressor runs. Not known (no frequency shown) counts as not running."""
    return unit.compressor_hz is not None and unit.compressor_hz > 0


def may_run(unit: HeatPumpState) -> bool:
    """Its compressor runs, or may: a unit in heat mode that shows no frequency (any climate entity without Midea's).

    Such a unit is held as a running one (review 2026-10-30): taken for standing, it was left at the setpoint of a
    start, half a degree above its running reading, and after a quick heat-up far above it, its fall never set anew.
    Whether it stopped itself it cannot tell: its own thermostat restarts it.
    """
    return runs(unit) or (unit.compressor_hz is None and unit.hvac_mode == "heat")


def compressor_heats(unit: HeatPumpState, mem: ControlMemory, active: bool) -> bool:
    """Its compressor heats its rooms, as far as can be told: what its rooms' heat-up and cool-down learning asks.

    A unit that shows no frequency counts as heating in heat mode, except in a rest of ours at the lowest
    setpoint (review 2026-10-30: held at its least speed it is in heat mode nearly all season, and its rooms
    never learned how fast they cool). A rest only planned sets nothing, so its own thermostat decides then.
    """
    if unit.hvac_mode != "heat":
        return False
    if unit.compressor_hz is not None:
        return unit.compressor_hz > 0
    return not (active and mem.resting)


def stood_s(unit: HeatPumpState, now: float) -> float | None:
    """How long its compressor has stood: 0 while it runs, None where the unit shows no frequency.

    From the samples of the last half hour; a compressor that stood all along stood at least that long.
    """
    if unit.compressor_hz is None:
        return None
    if unit.compressor_hz > 0:
        return 0.0
    last_ran = max((s.ts for s in unit.history if s.compressor_hz is not None and s.compressor_hz > 0), default=None)
    stood = [s.ts for s in unit.history if last_ran is None or s.ts > last_ran]
    return max(now - min(stood, default=now), 0.0)


def _held(value: float | None, unit: HeatPumpState, rooms: tuple[RoomResult, ...]) -> float | None:
    """A setpoint to hold, in the unit's step: above the lowest one (that is the rest), below a quick heat-up's,
    and never far above the warmest wanted target (a reading stuck high)."""
    if value is None:
        return None
    step = max(SETPOINT_STEP, unit.target_temp_step or 0.0)
    top = BOOST_C - step
    targets = [r.target for r in wanted_rooms(rooms)]
    if targets:
        top = min(top, max(targets) + LATCH_ABOVE_TARGET_MAX_K)
    return to_step(min(max(value, SETPOINT_MIN + step), top), unit.target_temp_step)


def latch_running(own: float | None, unit: HeatPumpState, rooms: tuple[RoomResult, ...]) -> float | None:
    """The setpoint of a unit whose compressor runs: its own reading plus the aim, a degree below it."""
    return _held(None if own is None else own + AIM_K, unit, rooms)


def latch_start(reading: float | None, unit: HeatPumpState, rooms: tuple[RoomResult, ...]) -> float | None:
    """The setpoint of a unit whose compressor stands: half a degree above its reading.

    Once it runs its reading jumps by about 1.5 degrees, and the setpoint then sits about a degree below
    it, where `latch_running` would set it. Set further above, a start boosts to 60-79 Hz.
    """
    return _held(None if reading is None else reading + AIM_K + READING_JUMP_K, unit, rooms)


def latched_setpoint(mem: ControlMemory) -> float | None:
    """The setpoint the unit is held at while it runs; None while it rests, heats up quickly or is not ours."""
    if mem.running_since_ts is None or mem.resting or mem.last_target is None or mem.last_target >= BOOST_C:
        return None
    return mem.last_target


def floor_setpoint(unit: HeatPumpState) -> float:
    """The lowest setpoint, where the unit rests."""
    return to_step(SETPOINT_MIN, unit.target_temp_step)


def preheat_short_k(room: RoomResult, now: float) -> float | None:
    """How far below its target a preheating room will be at its comfort start at the pace it rises now; None: not known.

    Judged half an hour after the preheat began, not after its planned start: that is recomputed every
    cycle and moves towards now while the room climbs (review 2026-10-30: at 0.4 K/h judged after 127 minutes).
    Its pace is the one since it began: the room's 90-minute trend read 0.12 K/h then for a preheat climbing
    the planned 0.5 K/h. An unknown pace, as for half an hour after a restart, is no evidence.
    """
    began = room.preheat_began_ts if room.preheat_began_ts is not None else room.preheat_start_ts
    if room.preheat_for_ts is None or began is None or room.temperature is None or room.preheat_pace_k_per_h is None:
        return None
    if now - began < PREHEAT_JUDGE_S:
        return None
    pace = max(room.preheat_pace_k_per_h, 0.0)
    projected = room.temperature + pace * max(room.preheat_for_ts - now, 0.0) / 3600.0
    return room.target - projected


def behind(room: RoomResult, now: float) -> bool:
    """A room preheating for its comfort start that will not be there by then at the pace it rises now."""
    short = preheat_short_k(room, now)
    return short is not None and short > BEHIND_MARGIN_K


def below_floor(room: RoomResult, now: float) -> bool:
    """"Warm up slower": a room at least SLOWER_FLOOR_K below its target, or a preheat projected that far short of
    its comfort start, lets the boiler help. A room being dried says nothing, as for `lags`."""
    if room.temperature is None or room.drying.active:
        return False
    if room.preheat_for_ts is not None:
        short = preheat_short_k(room, now)
        return short is not None and round(short, 3) >= SLOWER_FLOOR_K
    return round(room.target - room.temperature, 3) >= SLOWER_FLOOR_K


def lags(room: RoomResult, now: float) -> bool:
    """Below its target and barely rising. An unknown trend is no evidence, and a room being dried says
    nothing: its window may be open, and its target is raised.

    A preheating room is judged as for `behind`: by its pace since it began, and half an hour in at the earliest
    (review 2026-10-30: its 90-minute trend still held the flat setback time, every preheat lagged after ten
    minutes, and the boiler helped one that rose fast enough).
    """
    rising = room.trend_k_per_h
    if room.preheat_for_ts is not None:
        began = room.preheat_began_ts if room.preheat_began_ts is not None else room.preheat_start_ts
        if began is None or now - began < PREHEAT_JUDGE_S:
            return False
        rising = room.preheat_pace_k_per_h
    if room.temperature is None or rising is None or room.drying.active:
        return False
    return round(room.target - room.temperature, 3) >= LAG_BELOW_K and rising < RATE_TOO_SLOW_K_H


def heats_at_minimum(
    command: HeatPumpCommand, mem: ControlMemory, unit: HeatPumpState, rooms: tuple[RoomResult, ...], now_ts: float | None = None
) -> bool:
    """The unit heats its rooms at the least speed it is held at: what its heat-up rates are learned from.

    Not during a quick heat-up (30 °C, 650-930 W) or a drying (the strongest fan), not in a rest, and not
    in the first minutes of a run, while a start may still boost. A unit that shows no frequency counts
    from the start of its run phase (review 2026-10-30: it never learned a rate of its own).
    """
    if unit.compressor_hz is None:
        settled = may_run(unit) and now_ts is not None and mem.phase_since_ts is not None
        settled = settled and now_ts - mem.phase_since_ts >= SETTLED_RUN_S
    else:
        settled = runs(unit) and unit.compressor_running_s is not None and unit.compressor_running_s >= SETTLED_RUN_S
    # Nor in a lift, or with the setpoint further above its own reading than the cap (review 2026-10-30): lifted runs
    # at 600-1000 W were learned as its least speed, the preheat planned too late, and the lift came back.
    setpoint = command.target_temp if command.target_temp is not None else mem.last_target
    above = setpoint is not None and unit.indoor_temp is not None and setpoint - unit.indoor_temp > CAP_K
    return (
        command.plan == HEAT_PUMP_PLAN_HEAT
        and command.load != "boost"
        and not mem.resting
        and not mem.lifted
        and not above
        and settled
        and not any(r.drying.active for r in rooms)
    )


def why_above_minimum(unit: HeatPumpState, rooms: tuple[RoomResult, ...], mem: ControlMemory | None = None) -> str | None:
    """What runs the unit above the least speed it is held at: a quick heat-up (30 °C, 650-930 W, also while
    it is held at 30 to its minimum run), a drying (the strongest fan) or a lift (600-1000 W). None: nothing does.

    Asked before the control decides, so from the rooms, the unit's own setpoint and the memory of the last
    decision, not from the command.
    """
    held_at_boost = unit.target_temp is not None and unit.target_temp >= BOOST_C
    if held_at_boost or any(r.heat_pump_allowed and r.boost_active for r in rooms):
        return "quick_heat_up"
    if any(r.heat_pump_allowed and r.drying.active for r in rooms):
        return "bathroom_drying"
    # Release check 2026-10-30: kept out of the heat-up rates (heats_at_minimum), a lift still entered the COP map.
    # Not judged by the gap to its own reading: that falls 1.5 for minutes after a stop, and a run at its least speed
    # that re-latched then sat a degree above it; dropped, the whole cycle's COP was lost.
    return "lift" if mem is not None and mem.lifted else None


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


def normal_fan(offered: tuple[str, ...], stage: str) -> str:
    """The fan stage of normal operation as the unit spells it: "full" its strongest, one it does not offer "auto"."""
    if not offered:
        return stage  # not known what it offers: as chosen, and the executor sends only what it may
    by_name = {name.lower(): name for name in offered}
    found = strongest_fan(offered) if stage == "full" else by_name.get(stage)
    return found or spelled(offered, FAN_ECO)


def _air(stage: str | None) -> int:
    """How much air a fan stage moves, as far as its name tells: 2 a top stage, 1 "high", 0 any other."""
    name = (stage or "").lower()
    if name == FAN_BOOST:
        return 1
    return 2 if name in FAN_STRONGEST else 0


def fan_plan(
    unit: HeatPumpState, up: str | None, before: str | None, to: str | None, normal: str = FAN_ECO
) -> tuple[str | None, bool, str | None, str | None]:
    """The fan stage the unit is to have, whether to ask for it, and the stages to put back later and turned up to.

    `up` is the stage a quick heat-up or a drying turns the fan up to (None: neither applies),
    `before` the stage it was turned up from and `to` the one it was turned up to (None: it is not
    turned up), `normal` the stage of normal operation, what a unit that shows none gets back. A unit
    that shows no fan stage is asked once, for the turn up and for the turn back.
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
        return up, shown != up, shown or normal, up  # whoever had set that stage gets it back
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


def decide(inp: ControlInputs, mem: ControlMemory) -> tuple[HeatPumpCommand, ControlMemory]:
    """The command for this cycle and the memory after it; the command also shows what the memory holds."""
    command, new = _decide(inp, mem)
    if (new.running_since_ts is None or new.resting) and (new.boiler_helps or new.lagging_since_ts is not None or new.lifted):
        # A run that is not ours any more judges nothing (review 2026-10-30): out of reach, in a mode chosen by hand or
        # found off, a help kept on record let every heat pump room call the boiler from two tenths below.
        new = replace(new, boiler_helps=False, lagging_since_ts=None, lifted=False)
    held = new.manual_until_ts is not None and new.manual_until_ts > inp.now_ts
    return replace(
        command,
        manual_until_ts=new.manual_until_ts if held else None,
        manual_by=new.manual_by if held else None,
        not_carried_out=new.not_carried_out,
        # in every mode: a served room lags and the boiler adds the peak, the unit staying at its least speed
        delivers=not new.boiler_helps,
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
        )

    if inp.control_mode == CTRL_OFF:
        return cmd(HEAT_PUMP_PLAN_DISABLED, "none", "control_off"), replace(mem, running_since_ts=None)

    # ---- hands off -------------------------------------------------------------
    if inp.run_state == HEAT_PUMP_UNAVAILABLE:
        # Right after a start the unit's entity may not be loaded yet, and its run is not forgotten for
        # that. Seen 2026-10-13 after three restarts: the next cycle took the running unit over as a run
        # of its own, planned what it had already, and set it anew.
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

    def stop(reason: str, blockers=(), run_on: bool = True) -> tuple[HeatPumpCommand, ControlMemory]:
        new = replace(
            mem, running_since_ts=None, stopped_since_ts=now, last_command_ts=now, learning_until_ts=None,
            last_hvac_mode="off", fan_until_ts=None,
            fan_turned_up_from=None, fan_turned_up_to=None,  # the next start sets the fan anyway
            lagging_since_ts=None, boiler_helps=False,  # a run that ended judges nothing any more
            resting=False, lifted=False, phase_since_ts=None, above_cap_since_ts=None,
        )
        just_sent = mem.last_command_ts is not None and now - mem.last_command_ts <= MANUAL_DETECT_GRACE_S
        if active and (unit.hvac_mode or "off") == "off" and not just_sent:
            # Somebody switched it off meanwhile, or a start was not carried out: there is nothing to
            # stop. A "fan only" sent now starts the fan of a unit that stands still and runs it for a
            # quarter of an hour. (Right after a command of ours the unit may only not show it yet.)
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, blockers=blockers), new
        # the fan runs on where the unit has a mode for it; one without would refuse the stop every cycle
        if run_on and p.heat_pump_fan_run_on_s > 0 and (not unit.hvac_modes or "fan_only" in unit.hvac_modes):
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
                resting=False, lifted=False, phase_since_ts=None, above_cap_since_ts=None,
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
        # demand waits for the full minimum run. Its setpoint is set once, where it differs from the hold.
        if mem.last_hvac_mode in (None, "heat") and mem.running_since_ts is None and unit.hvac_mode == "heat" and not settling:
            mem = replace(
                mem, running_since_ts=now, stopped_since_ts=None, last_command_ts=None,
                last_hvac_mode="heat", last_target=unit.target_temp, lagging_since_ts=None, boiler_helps=False,
                resting=False, lifted=False, phase_since_ts=None, above_cap_since_ts=None,
            )

    running = mem.running_since_ts is not None

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
    wanted = wanted_rooms(inp.rooms)  # with or without a temperature: somebody wants them warm
    known = [r for r in wanted if r.temperature is not None]
    # The coldest served room, measured against its own target, decides run or rest alone. Rounded:
    # 20.8 °C read for 21 °C is 0.2 degrees short, not 0.19999.
    gap = min((round(r.temperature - r.target, 3) for r in known), default=None)
    # Drying deliberately keeps demanding heat even once the target is met —
    # warm air carries the moisture out. It still needs a ceiling, or the
    # bathroom would be driven up for the full hour the mode may last.
    drying = [r for r in allowed if r.drying.active and r.temperature < r.target + DRYING_MAX_OVERSHOOT_K]
    boost = [r for r in allowed if r.boost_active and r.temperature < r.target]
    need = bool(boost or drying) or (gap is not None and gap <= RESUME_AT_K)
    warm = not boost and not drying and gap is not None and gap >= REST_ABOVE_K
    awake = bool(wanted or drying or boost)

    # Worth running: its heat is the cheaper one, or it is the source recommended, or the only one
    # allowed. The recommendation alone was not enough: it names a source only while a room calls
    # the boiler (from 0.3 K below its target to 0.1 K below). A unit switched on and off by the
    # rooms was stopped as "gas cheaper" the moment the call ended, and never started for a room
    # whose radiator thermostat is switched off.
    recommended = inp.advice_source in (SOURCE_HEAT_PUMP, SOURCE_BOTH)
    economic = inp.cheaper or recommended or inp.op_mode == MODE_HEAT_PUMP_ONLY
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
        and need
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
    if inp.defrosting:
        mem = replace(mem, defrost_until_ts=now + DEFROST_HOLD_S)
    frozen = inp.defrosting or (mem.defrost_until_ts is not None and mem.defrost_until_ts > now)

    # A room that is being dried gets the strongest fan, also above its ceiling for the heat and
    # while the unit idles at its setpoint: moved air dries without heat too.
    dries = any(r.heat_pump_allowed and r.drying.active for r in inp.rooms)
    drying_fan = strongest_fan(unit.fan_modes) if dries else None
    normal, boost_fan = normal_fan(unit.fan_modes, p.heat_pump_fan_stage), spelled(unit.fan_modes, FAN_BOOST)

    def fan_up(load: str | None) -> str | None:
        """The stage the fan is turned up to: a quick heat-up has its own, also during a drying. None: not turned up."""
        return boost_fan if load == "boost" else drying_fan

    def goes_out(mem: ControlMemory, target: float | None) -> bool:
        """A setpoint differs from the one sent last, and it is not too soon after that for it."""
        if target is None or (mem.last_target is not None and abs(target - mem.last_target) < ADJUST_MIN_STEP_K):
            return False
        return mem.last_command_ts is None or now - mem.last_command_ts >= ADJUST_MIN_INTERVAL_S

    def keep_heating(
        mem: ControlMemory, reason: str, load: str | None, target: float | None, waiting: str | None = None, at_once: bool = False
    ):
        """A unit that goes on heating is sent what it does not have yet: the setpoint, the fan stage.

        At once: the first turn of the fan (with a setpoint that is due as well, however recent the last
        command), and a setpoint marked so (a quick heat-up, the way down from it, a rest, a resume, a
        hold set once). Everything else keeps the pace of an adjustment, a repeat for a unit that did not
        follow included.
        """
        up = fan_up(load)
        first = up is not None and mem.fan_turned_up_from is None
        given = mem.fan_normal or spelled(unit.fan_modes, FAN_ECO)
        before = mem.fan_turned_up_from
        if up is None and before is not None and before == given != normal and unit.fan_mode != before:
            before = normal  # turned up from the normal stage of before: it gets back the one chosen now
        fan, turn, before, to = fan_plan(unit, up, before, mem.fan_turned_up_to, normal)
        offered = not unit.fan_modes or normal in unit.fan_modes
        if up is None and before is None and not turn and unit.fan_mode == given != normal and offered:
            # Still on the normal stage it was given, another one chosen since: it gets that, once. A stage chosen
            # by hand differs from the one given and stays; a unit that does not follow is not asked again.
            fan, turn = normal, True
        if (before, to) != (mem.fan_turned_up_from, mem.fan_turned_up_to):
            mem = replace(mem, fan_turned_up_from=before, fan_turned_up_to=to)
        # Given is what normal operation set or found (review 2026-10-30): a turn up to the stage chosen since was taken
        # for it, and the old stage put back after the drying stayed for good; a fan found on the chosen stage after
        # a drying, nothing sent, kept the old one on record, and the next choice was taken for one by hand.
        if up is None and before is None and unit.fan_mode == normal != mem.fan_normal:
            mem = replace(mem, fan_normal=normal)
        retarget = target is not None and (mem.last_target is None or abs(target - mem.last_target) >= ADJUST_MIN_STEP_K)
        recently = mem.last_command_ts is not None and now - mem.last_command_ts < ADJUST_MIN_INTERVAL_S
        if not (retarget or turn) or (recently and not ((first and turn) or (retarget and at_once))):
            waiting = waiting or ("rate_limit" if retarget else None)
            if mem.last_command_ts is None and not (retarget or turn):
                # taken over with what it is to have: as if sent now, so that a change after it is judged
                mem = replace(mem, last_command_ts=now)
            return cmd(HEAT_PUMP_PLAN_HEAT, "none", reason, target=mem.last_target, fan=fan, load=load, waiting=waiting), mem
        new = replace(mem, last_command_ts=now, last_target=target if retarget else mem.last_target, last_hvac_mode="heat")
        if up is None and turn and fan == normal:
            new = replace(new, fan_normal=normal)
        if up is None and unit.fan_mode is None:
            new = replace(new, fan_turned_up_from=None, fan_turned_up_to=None)  # asked once, see fan_plan
        return cmd(HEAT_PUMP_PLAN_HEAT, "adjust", reason, target=new.last_target, fan=fan, load=load, waiting=waiting), new

    if not running:
        if not (permitted and need):
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason if need else "no_demand"), mem
        if mem.stopped_since_ts is not None and now - mem.stopped_since_ts < p.heat_pump_min_off_s:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", reason, waiting="min_off_time"), mem
        up = fan_up(load)
        target = BOOST_C if load == "boost" else latch_start(ref_reading(unit, mem, now), unit, inp.rooms)
        if target is None:
            return cmd(HEAT_PUMP_PLAN_OFF, "none", "intake_temperature_unknown"), mem
        bins = mem.learning_bins
        learning_until = mem.learning_until_ts
        if learning_new and inp.outdoor_bin is not None:
            learning_until = now + LEARNING_RUN_S
            # a learning run only counts once it really ran; a planned one must not use up the bin
            if active:
                bins = tuple((b, t) for b, t in bins if b != inp.outdoor_bin) + ((inp.outdoor_bin, now),)
        # A start sets the fan anyway, to the stage of normal operation: that is what it gets back. A unit
        # that offers no such stage is never sent it and keeps the one it shows, so that one is put back:
        # asked for "auto" instead, it stayed on its top stage for the rest of the run.
        back = normal if not unit.fan_modes or normal in unit.fan_modes else unit.fan_mode or normal
        new = replace(
            mem,
            running_since_ts=now,
            stopped_since_ts=None,
            last_command_ts=now,
            last_hvac_mode="heat",
            last_target=target,
            learning_bins=bins,
            learning_until_ts=learning_until,
            fan_turned_up_from=back if up else None,
            fan_turned_up_to=up,
            lagging_since_ts=None,
            boiler_helps=False,
            resting=False,
            lifted=False,
            phase_since_ts=now,
            above_cap_since_ts=None,
            fan_normal=normal,
        )
        return cmd(HEAT_PUMP_PLAN_HEAT, "start", reason, target=target, fan=up or normal, load=load), new

    # ---- running: not worth it any more, or nobody wants the served rooms warm --------------
    if not (permitted and awake):
        if not awake:
            reason, load = "comfort_time_over", "eco"
        elif not permitted:
            load = "eco"
        # Out of a long rest its fan has run all along: a run-on blows nothing warm into the rooms. A run
        # resumed out of a rest has its minimum run from the resume (review 2026-10-30: stopped two minutes in).
        rested = mem.resting and (stood_s(unit, now) or 0.0) >= p.heat_pump_fan_run_on_s
        ran_since = mem.running_since_ts if mem.resting else (mem.phase_since_ts or mem.running_since_ts)
        if not rested and now - (ran_since or now) < p.heat_pump_min_run_s:
            # Finish the minimum run at the setpoint it has, unless that is the full power of a quick
            # heat-up: left at 30 °C, the unit would drive the rooms on for the rest of the minimum run.
            boosted = mem.last_target is not None and mem.last_target >= BOOST_C
            own_latch = latch_running(unit.indoor_temp, unit, inp.rooms) if boosted else mem.last_target
            return keep_heating(mem, reason, load, own_latch, waiting="min_run_time", at_once=boosted)
        return stop(reason, run_on=not rested)

    # ---- a quick heat-up: full power, at once, also out of a rest ---------------------------
    if boost:
        if mem.resting:
            mem = replace(mem, resting=False, phase_since_ts=now)
        return keep_heating(mem, reason, load, BOOST_C, at_once=True)

    # ---- resting at the lowest setpoint, in heat mode -----------------------------------------
    phase_start = mem.phase_since_ts if mem.phase_since_ts is not None else mem.running_since_ts
    if mem.resting:
        floor = floor_setpoint(unit)
        if need:
            if now - (phase_start or now) < p.heat_pump_min_off_s:
                return keep_heating(mem, reason, load, floor, waiting="min_off_time")
            # half a degree above its reading: it restarts softly, and runs a degree below it once its reading jumped;
            # a compressor the floor has not stopped yet is held at once
            if runs(unit):
                target = latch_running(unit.indoor_temp, unit, inp.rooms)
            else:
                target = latch_start(ref_reading(unit, mem, now), unit, inp.rooms)
            resumed = replace(mem, resting=False, phase_since_ts=now, above_cap_since_ts=None)
            return keep_heating(resumed, "resumed_at_minimum", load, target, at_once=True)
        # A unit the lowest setpoint does not stop runs on at whatever speed it likes: stopped by mode.
        # A defrost is no evidence, its compressor starts again afterwards by design.
        ran_through = (unit.compressor_running_s is None or unit.compressor_running_s >= REST_FAILSAFE_S)
        if runs(unit) and not frozen and now - (phase_start or now) >= REST_FAILSAFE_S and ran_through:
            return stop("rest_not_reached")
        return keep_heating(mem, "rooms_warm_compressor_rests", load, floor)

    # ---- running at its least speed ------------------------------------------------------------
    own = unit.indoor_temp
    # The boiler helps only rooms whose radiator may heat (2026-10-30): one whose thermostat is switched off by hand
    # or silent takes no boiler heat, and kept the help on, the unit saying the boiler takes the peak, until every
    # room was at its target. The unit itself, set higher where no boiler helps, heats every room it serves.
    judged = [r for r in known if r.heating_allowed] if inp.boiler_can_help else known
    at_targets = bool(known) and all(r.temperature >= r.target for r in judged)
    # Only a real run tells whether the rooms lag; a planned one heats nothing.
    lagging = active and any(lags(r, now) for r in judged)
    late = active and any(behind(r, now) for r in judged)
    lagging_since = (mem.lagging_since_ts if mem.lagging_since_ts is not None else now) if lagging else None
    lag_due = late or (lagging_since is not None and now - lagging_since >= LAG_S)
    # "Warm up slower" lets the boiler help only at its comfort floor, and then, as "boiler helps", until every room is there.
    due = active and any(below_floor(r, now) for r in judged) if inp.help_at_floor else lag_due
    helps = (mem.boiler_helps or due) and inp.boiler_can_help
    was_lifted, lifted = mem.lifted, mem.lifted and not inp.boiler_can_help and inp.may_lift
    if at_targets:
        # the boiler helps until every room is there, not only while one lags; a lift alike
        helps, lagging_since, lifted = False, None, False
    if (lagging_since, helps, lifted) != (mem.lagging_since_ts, mem.boiler_helps, mem.lifted):
        mem = replace(mem, lagging_since_ts=lagging_since, boiler_helps=helps, lifted=lifted)
    # Without a boiler to help, a lagging room may have the unit set higher, up to `CAP_LIFT_K`, and a lift stays
    # until every room is there. A room merely short, as after each resume, leaves the cap at its least speed
    # (review 2026-10-30: up to 1.5 degrees above its reading, 48-67 Hz, with no room lagging). A preheat that is
    # behind lifts as well, however fast it climbs: it lets the boiler help, too. "Warm up slower" lifts never.
    lifting = not inp.boiler_can_help and inp.may_lift and (lagging_since is not None or late or lifted)
    cap = CAP_LIFT_K if lifting else CAP_K

    if warm and now - (phase_start or now) >= p.heat_pump_min_run_s and not learning_active:
        # A unit found off (switched off at the unit, a start it did not carry out) is not switched on to rest.
        heating = not active or unit.hvac_mode == "heat"
        # A compressor that stood a while reads 1.5 degrees lower than it would run: judged as it would run.
        standing = (stood_s(unit, now) or 0.0) >= STAND_S
        running_own = None if own is None else own + (READING_JUMP_K if standing else 0.0)
        if heating and running_own is not None and running_own >= SETPOINT_MIN + UNIT_HYSTERESIS_K:
            rest = replace(mem, resting=True, phase_since_ts=now, above_cap_since_ts=None)
            return keep_heating(rest, "rooms_warm_compressor_rests", load, floor_setpoint(unit), at_once=True)
        # In a cold room the lowest setpoint is too close to its reading to stop it: stopped by mode.
        return stop("target_reached")
    waiting = "min_run_time" if warm and not learning_active else None
    hold_reason = "target_reached" if warm else ("boiler_adds_peak" if mem.boiler_helps else reason)
    boosted = mem.last_target is not None and mem.last_target >= BOOST_C

    # During a defrost and a quarter of an hour after it the readings say nothing: the setpoint is held.
    if frozen and not boosted:
        if mem.above_cap_since_ts is not None:
            mem = replace(mem, above_cap_since_ts=None)
        return keep_heating(mem, hold_reason, load, mem.last_target, waiting=waiting)

    # Set once: a unit taken over, the first cycle after an update, the end of a quick heat-up.
    if mem.last_target is None or mem.phase_since_ts is None or boosted:
        floor = floor_setpoint(unit)
        if not need and not boosted and mem.last_target is not None and mem.last_target <= floor:
            # Found at the floor with no room short: a rest that a moment out of reach or the update from 0.6.8
            # left off the record (review 2026-10-30: it was resumed for no room, two beeps and a run).
            rest = replace(mem, resting=True, phase_since_ts=now, above_cap_since_ts=None)
            return keep_heating(rest, "rooms_warm_compressor_rests", load, floor)
        target = (latch_running if may_run(unit) else latch_start)(
            own if may_run(unit) else ref_reading(unit, mem, now), unit, inp.rooms
        )
        latched = replace(mem, phase_since_ts=phase_start, above_cap_since_ts=None)
        return keep_heating(latched, "holding_at_minimum", load, target, waiting=waiting, at_once=True)

    # A lift that ended sets the unit back to its least speed at once (review 2026-10-30): left up to half a degree
    # above its reading, within the cap, it ran on at 52 Hz and about 730 W for two hours with every room warm.
    if was_lifted and not lifted and may_run(unit):
        target = latch_running(own, unit, inp.rooms)
        if target is not None and mem.last_target - target >= ADJUST_MIN_STEP_K:
            down = replace(mem, above_cap_since_ts=None)
            return keep_heating(down, "holding_at_minimum", load, target, waiting=waiting, at_once=True)

    # It stopped itself although a room is short: the office warmed (a PC of up to 460 W), or the unit
    # stops closer to its setpoint than a PortaSplit. Set anew it keeps working for the other rooms. A stop
    # not answered yet is answered once: with a setpoint, or, where that would be the one it has, by its own
    # thermostat; its reading falling on while it stands is no new stop to be set lower for.
    stood = stood_s(unit, now)
    if need and stood is not None and stood >= STAND_S:
        stopped_ts = now - stood
        answered = max((ts for ts in (mem.last_command_ts, mem.stop_noted_ts) if ts is not None), default=None)
        if answered is None or stopped_ts > answered:
            target = latch_start(own, unit, inp.rooms)
            again = replace(mem, stop_noted_ts=now, above_cap_since_ts=None)
            if goes_out(again, target):
                return keep_heating(again, "relatched_unit_stopped", load, target, waiting=waiting)
            if target is None or (mem.last_target is not None and abs(target - mem.last_target) < ADJUST_MIN_STEP_K):
                mem = again  # nothing to send: noted, and its own thermostat restarts it

    # Its reading fell well below the setpoint while it runs (the office cooled, a door open): its own
    # controller would climb past the least speed. Set anew from its reading after a while.
    if may_run(unit) and own is not None and mem.last_target is not None and mem.last_target - own > cap:
        since = mem.above_cap_since_ts if mem.above_cap_since_ts is not None else now
        if now - since >= CAP_HOLD_S:
            target = latch_running(own, unit, inp.rooms) if not lifting else _held(own + cap, unit, inp.rooms)
            if goes_out(mem, target):
                down = replace(mem, above_cap_since_ts=None)
                return keep_heating(down, "relatched_reading_fell", load, target, waiting=waiting)
        if since != mem.above_cap_since_ts:
            mem = replace(mem, above_cap_since_ts=since)
    elif mem.above_cap_since_ts is not None:
        mem = replace(mem, above_cap_since_ts=None)

    # Without a boiler a lagging room has the unit set half a degree higher every `LAG_S`, up to the lift cap.
    if lifting and lag_due and own is not None and mem.last_target is not None and mem.last_target - own < CAP_LIFT_K:
        step = max(SETPOINT_STEP, unit.target_temp_step or 0.0)
        target = _held(min(mem.last_target + step, own + CAP_LIFT_K), unit, inp.rooms)
        if goes_out(mem, target):
            lift = replace(mem, lagging_since_ts=now, lifted=True)
            return keep_heating(lift, "heat_pump_only_lift", load, target, waiting=waiting)

    # the normal case: the setpoint held, nothing sent
    return keep_heating(mem, hold_reason, load, mem.last_target, waiting=waiting)
