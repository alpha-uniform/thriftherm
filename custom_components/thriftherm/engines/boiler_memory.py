"""What the boiler controller remembers and is given each cycle, and the heating curve.

Pure data and arithmetic, shared by the control (`boiler_control`) and the learning
(`boiler_learning`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite

from ..const import BOILER_PLAN_BLOCK, BOILER_PLAN_HEAT
from ..models import Parameters, RoomResult
from . import heat_up
from .boiler_load import CLASSES, ROOM_MARK, LoadLine, class_of, line_of, lines_from_storage, lines_to_storage
from .common import as_dict, as_float, as_floats

CORRECTION_LIMIT_K = 10.0  # the level and the slope correction each stay within this
CURVE_WARM_C = 15.0  # outdoor temperatures of the curve's two points
CURVE_COLD_C = -10.0
ADJUSTMENTS_STORED = 48  # the steps of a day: the daily allowances of the learning are counted from them

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "state_since_ts", "last_flow", "ramp_from", "ramp_since_ts", "hot_water_seen_ts", "starts_day_ts", "comp_ts",
    "comp_ok_k", "comp_short_k", "comp_floor_k", "comp_ample_ts", "comp_left_ts",
)
COMP_MOST_K = 40.0  # what is sent above the curve at the boiler's limit, and learned for it, stays within this
COMP_PHASES_KEPT = 3
BAND_MARK = "@"  # "single:badezimmer@0": what a room's line learned at the boiler's limit, outdoor 0 to 5 °C


@dataclass(frozen=True)
class CompLine:
    """What the rooms rose reliably with above the curve at the boiler's limit, for a room's line and an outdoor band
    (engines/boiler_limit.py): the value a heating begins with, and the phases not yet moved on."""

    value_k: float = 0.0
    phases: tuple[float, ...] = ()

    def to_storage(self) -> dict:
        return {"k": self.value_k, "p": list(self.phases)}


def _comp_value(value: object) -> float | None:
    number = None if isinstance(value, bool) else as_float(value)
    return number if number is not None and isfinite(number) and 0.0 <= number <= COMP_MOST_K else None


def comp_key_ok(key: object) -> bool:
    """A room's line within one of the three classes, with or without an outdoor band."""
    if not isinstance(key, str):
        return False
    cell, _, band = key.partition(BAND_MARK)
    if ROOM_MARK not in cell or class_of(cell) not in CLASSES or not cell.split(ROOM_MARK, 1)[1]:
        return False
    return not band or band.lstrip("-").isdigit()


def comp_lines_from_storage(data: object) -> dict[str, CompLine]:
    lines = {}
    for key, raw in as_dict(data).items():
        row = as_dict(raw)
        value = _comp_value(row.get("k"))
        if not comp_key_ok(key) or value is None:
            continue
        phases = row.get("p") if isinstance(row.get("p"), list) else []
        kept = tuple(v for v in (_comp_value(p) for p in phases) if v is not None)[-COMP_PHASES_KEPT:]
        lines[key] = CompLine(value_k=value, phases=kept)
    return lines


@dataclass(frozen=True)
class BoilerMemory:
    # --- control
    last_send_ts: float | None = None
    last_payload: str | None = None
    last_flow: float | None = None
    state: str | None = None  # BOILER_PLAN_HEAT | BOILER_PLAN_BLOCK: the plan in force
    state_since_ts: float | None = None
    ramp_from: float | None = None  # flow at which the current rise began
    ramp_since_ts: float | None = None
    lockout_since_ts: float | None = None  # the boiler has reported its overrun or lockout since then; not stored
    block_since_ts: float | None = None  # first block of an unbroken series of sends; not stored
    # --- hot water
    hot_water_seen_ts: float | None = None  # last sample that looked like hot water
    hot_water_guessed: bool = False  # the last hot water sign was a guess, not the boiler's status code
    hot_water_before_guess_ts: float | None = None  # hot_water_seen_ts before that guess; not stored
    warm_start_ts: float | None = None  # last burn of the boiler's warm start (engines/boiler_control.py); not stored
    # --- what was learned: the curve's level, and what it needs on top towards the cold point. This is
    # the common correction, where a room without a value of its own starts; each room has a line of its own
    # within each load class (`lines`).
    offset_k: float = 0.0  # level: added at every outdoor temperature
    slope_k: float = 0.0  # slope: added in full at -10 °C, not at all at +15 °C
    adjustments: tuple[float, ...] = ()  # timestamps of the learning steps
    adjustment_steps: tuple[float, ...] = ()  # and by how much each moved the flow (signed K)
    last_adjust_reason: str | None = None
    limit_cut_ts: float | None = None  # the last step the ±10 K limit swallowed or cut short; shown, not stored
    # The rooms' last calls that ended (boiler_learning.count_calls), for the diagnostics: the room, when it ended,
    # its minutes, the room's rise (K) and pace (K/h), the flow sent, the burner's starts in it, and the verdict
    # ("fast", "slow", or why it says nothing about the flow).
    calls: tuple[tuple, ...] = ()
    # rooms that called in this release while another room's line or boosts set the flow (not stored): what their
    # calls say is about that flow, not about their own line
    outvoted: tuple[str, ...] = ()
    # --- evidence of the heating run in progress
    heating_since_ts: float | None = None
    spread_ema: float | None = None
    samples: int = 0
    last_review_ts: float | None = None
    burn_since_ts: float | None = None  # the burner is on for the heating since then; not stored
    burn_interrupted: bool = False  # it was on when Home Assistant stopped; its start is not counted yet (`track_burns`)
    burns: tuple[tuple[float, float], ...] = ()  # (start, end) of the heating burns of the last three hours
    # the heating burner's starts since local midnight, and that midnight: a sensor shows them, the statistics a day each
    starts_today: int = 0
    starts_day_ts: float | None = None
    # --- by how many rooms call (boiler_load): a line per load class, and the cycle the operation was last booked at
    lines: Mapping[str, LoadLine] = field(default_factory=dict)
    lines_seen_ts: float | None = None  # not stored
    class_in_use: str | None = None  # the class that counts right now; a new one has to hold first (not stored)
    class_candidate: str | None = None
    class_candidate_since_ts: float | None = None
    # the rooms of the call in progress or the last one: their lines set the flow, also between calls (not stored)
    cell_rooms: tuple[str, ...] = ()
    spread_judging_since_ts: float | None = None  # since when enough rooms have called for the spread to judge (not stored)
    learning_since_ts: float | None = None  # the learning may act again since then, after a pause (not stored)
    heat_up: Mapping[str, heat_up.HeatUp] = field(default_factory=dict)  # each room's extra while it heats up
    flow_held: bool = False  # the flow sent stood at its maximum or was held by the ramp (not stored)
    # the boiler's own limit (engines/boiler_limit.py): the flow measured, damped over half an hour while it heats (not
    # stored), and what is sent above the curve as it cycles on its least power far below the flow sent
    flow_mean_c: float | None = None
    flow_mean_ts: float | None = None
    comp_k: float = 0.0
    comp_ts: float | None = None
    # ... learned for good per room's line and outdoor band (`CompLine`), and the phase in progress: its key, the least
    # the rooms rose reliably with and the most they fell short at, settled; the floor a step down stops at, since when
    # they rise with room to spare, and whether the last step went up
    comp_lines: Mapping[str, CompLine] = field(default_factory=dict)
    comp_key: str | None = None
    comp_ok_k: float | None = None
    comp_short_k: float | None = None
    comp_floor_k: float | None = None
    comp_ample_ts: float | None = None
    comp_last_up: bool | None = None
    # what the last heating sent above the curve as it ended, when, and on which line: a heating soon after begins a step
    # below it (`boiler_limit.begin`)
    comp_left_k: float = 0.0
    comp_left_ts: float | None = None
    comp_left_key: str | None = None

    def to_storage(self) -> dict:
        # The timers go with it: after a restart the controller must still know that it
        # switched to "heat" two minutes ago, or it may toggle the boiler right away.
        # `last_send_ts` is deliberately left out so a SetMode goes out immediately.
        # A burn in progress is left out as well: nobody saw the burner while Home Assistant was away.
        # Restored, a burn was closed at the first cycle after the stop: six hours away gave a burn of
        # six hours and a burner share of 1.0, and a short restart stretched a burn of one minute
        # (review 2026-10-13).
        # Only that a burn was on is kept, as `burn_interrupted`: a start is counted as its burn ends, so
        # without it a burn that ended while Home Assistant was away would never be counted.
        return {
            "offset_k": self.offset_k,
            "slope_k": self.slope_k,
            "adjustments": list(self.adjustments[-ADJUSTMENTS_STORED:]),
            "adjustment_steps": list(self.adjustment_steps[-ADJUSTMENTS_STORED:]),
            "last_adjust_reason": self.last_adjust_reason,
            "calls": [list(call) for call in self.calls],
            "state": self.state,
            **{key: getattr(self, key) for key in _STORED_FLOATS},
            "burns": [list(burn) for burn in self.burns],
            "starts_today": self.starts_today,
            "burn_interrupted": self.burn_since_ts is not None or self.burn_interrupted,
            "lines": lines_to_storage(self.lines),
            "heat_up": heat_up.to_storage(self.heat_up),
            "comp_k": self.comp_k,
            "comp_lines": {key: line.to_storage() for key, line in self.comp_lines.items()},
            "comp_key": self.comp_key,
            "comp_last_up": self.comp_last_up,
            "comp_left_k": self.comp_left_k,
            "comp_left_key": self.comp_left_key,
        }

    @classmethod
    def from_storage(cls, data: dict | None) -> BoilerMemory:
        data = as_dict(data)
        offset, slope = as_float(data.get("offset_k")), as_float(data.get("slope_k"))
        reason = data.get("last_adjust_reason")
        state = data.get("state")
        raw_burns = data.get("burns")
        burns: list[tuple[float, float]] = []
        for burn in raw_burns if isinstance(raw_burns, list) else []:
            pair = as_floats(burn) if isinstance(burn, (list, tuple)) else []
            if len(pair) == 2 and pair[1] >= pair[0]:
                burns.append((pair[0], pair[1]))
        starts = as_float(data.get("starts_today"))
        calls = []
        for row in data.get("calls") if isinstance(data.get("calls"), list) else []:
            if not isinstance(row, list) or len(row) != 8 or not isinstance(row[0], str) or not isinstance(row[7], str):
                continue
            numbers = [None if v is None else as_float(v) for v in row[1:7]]
            if all(v is None or isfinite(v) for v in numbers) and None not in (numbers[0], numbers[1], numbers[2], numbers[5]):
                calls.append((row[0], *numbers[:5], int(numbers[5]), row[7]))
        # a step is its time and its size, paired from the newest: one that lacks either is none
        times, sizes = (v if isinstance(v, list) else [] for v in (data.get("adjustments"), data.get("adjustment_steps")))
        steps = [(as_float(t), as_float(s)) for t, s in zip(reversed(times), reversed(sizes), strict=False)]
        steps = [(t, s) for t, s in reversed(steps) if t is not None and s is not None and isfinite(t) and isfinite(s)]
        steps = steps[-ADJUSTMENTS_STORED:]
        level = offset if offset is not None and isfinite(offset) else 0.0
        slope = slope if slope is not None and isfinite(slope) else 0.0
        return cls(
            offset_k=level,
            slope_k=slope,
            adjustments=tuple(t for t, _ in steps),
            adjustment_steps=tuple(s for _, s in steps),
            last_adjust_reason=reason if isinstance(reason, str) else None,
            calls=tuple(calls),
            state=state if state in (BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK) else None,
            burns=tuple(burns),
            starts_today=int(starts) if starts is not None and isfinite(starts) and starts > 0 else 0,
            burn_interrupted=data.get("burn_interrupted") is True,
            lines=lines_from_storage(data.get("lines")),
            heat_up=heat_up.from_storage(data.get("heat_up")),
            comp_k=comp if (comp := _comp_value(data.get("comp_k"))) is not None else 0.0,
            comp_lines=comp_lines_from_storage(data.get("comp_lines")),
            comp_key=key if comp_key_ok(key := data.get("comp_key")) else None,
            comp_last_up=last_up if isinstance(last_up := data.get("comp_last_up"), bool) else None,
            comp_left_k=left if (left := _comp_value(data.get("comp_left_k"))) is not None else 0.0,
            comp_left_key=left_key if comp_key_ok(left_key := data.get("comp_left_key")) else None,
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
    # the coarsest burner sign: the boiler's own flame sign where it has one, else its pump state says
    # "heating" (never while it makes hot water)
    burner_heating: bool = False
    hot_water_active: bool = False  # the boiler's pump state or hot water mode says: hot water
    gas_power_w: float | None = None  # from a gas meter, None without one
    reported_mode: str | None = None  # BOILER_REPORTS_* from the boiler's status code, None if unknown
    status_known: bool = False  # the boiler's status code is configured and readable
    heating_pump_on: bool | None = None  # the heating circuit's pump runs (ebusd WP or pump state); None unknown
    learning_allowed: bool = True  # False while setpoints move, boost/drying run or safety is not ok
    # Right after a restart the room sensors report within seconds to minutes. A block decided
    # before they did locks the heating (and the pump) off for the minimum state time.
    room_data_pending: bool = False
    day_start_ts: float | None = None  # local midnight of today: the day's starts are counted from it
    no_new_release: bool = False  # signs of a fault of the boiler: a block stays (engines/boiler.py)
    # the rooms heating along last cycle (heat_call.join), each with its valve's opening in %: a trial of the curve is
    # judged by the burner only while these stay as they were (boiler_learning.review_trials)
    heating_along: tuple[tuple[str, float], ...] = ()


def cold_weight(outdoor_c: float | None) -> float:
    """Where the outdoor temperature lies between the curve's points: 0 at +15 °C, 1 at -10 °C.

    Without an outdoor temperature the middle is taken, as the curve itself does.
    """
    if outdoor_c is None:
        return 0.5
    return min(max((CURVE_WARM_C - outdoor_c) / (CURVE_WARM_C - CURVE_COLD_C), 0.0), 1.0)


def curve_flow(outdoor_c: float | None, p: Parameters, limited: bool = True) -> float:
    """Linear heating curve through (-10 °C, cold) and (+15 °C, warm).

    Without an outdoor temperature the curve uses its middle: the cold point would
    heat the flow to the winter maximum on a mild day just because a sensor died.

    `limited=False`: the curve as it is, for a sum that is limited as a whole. Limited first, the
    minimum became the base the correction and the demand were added to: measured 2026-10-13 at
    15 °C outside, a curve of 30 °C taken as 40, and one room was heated with 53 instead of 43.5 °C.
    """
    if outdoor_c is None:
        value = (p.boiler_curve_flow_cold + p.boiler_curve_flow_warm) / 2.0
    else:
        slope = (p.boiler_curve_flow_cold - p.boiler_curve_flow_warm) / (CURVE_WARM_C - CURVE_COLD_C)
        value = p.boiler_curve_flow_warm + slope * (CURVE_WARM_C - outdoor_c)
    return min(max(value, p.boiler_flow_min), p.boiler_flow_max) if limited else value


def correction(mem: BoilerMemory, outdoor_c: float | None, load: str | None = None) -> float:
    """What the learning adds to the curve at this outdoor temperature: level plus its share of the slope.

    With `load`, a room within a load class, it is that line's (`boiler_load.line_of`); without, the
    common correction.
    """
    cold = cold_weight(outdoor_c)
    if load is None:
        return mem.offset_k + mem.slope_k * cold
    line = line_of(mem.lines, load, mem.offset_k, mem.slope_k)
    return line.level_k + line.slope_k * cold
