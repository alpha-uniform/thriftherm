"""What the boiler controller remembers and is given each cycle, and the heating curve.

Pure data and arithmetic, shared by the control (`boiler_control`) and the learning
(`boiler_learning`).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..const import BOILER_PLAN_BLOCK, BOILER_PLAN_HEAT
from ..models import Parameters, RoomResult
from .common import as_dict, as_float, as_floats

CORRECTION_LIMIT_K = 10.0  # the level and the slope correction each stay within this
CURVE_WARM_C = 15.0  # outdoor temperatures of the curve's two points
CURVE_COLD_C = -10.0

# restored as they are, anything that is not a number becomes None
_STORED_FLOATS = (
    "state_since_ts", "last_flow", "last_flow_ts", "ramp_from", "ramp_since_ts", "hot_water_seen_ts", "burn_since_ts",
)


@dataclass(frozen=True)
class BoilerMemory:
    # --- control
    last_send_ts: float | None = None
    last_payload: str | None = None
    last_flow: float | None = None
    last_flow_ts: float | None = None
    state: str | None = None  # BOILER_PLAN_HEAT | BOILER_PLAN_BLOCK: the plan in force
    state_since_ts: float | None = None
    ramp_from: float | None = None  # flow at which the current rise began
    ramp_since_ts: float | None = None
    block_since_ts: float | None = None  # first block of an unbroken series of sends; not stored
    # --- hot water
    hot_water_seen_ts: float | None = None  # last sample that looked like hot water
    hot_water_guessed: bool = False  # the last hot water sign was a guess, not the boiler's status code
    hot_water_before_guess_ts: float | None = None  # hot_water_seen_ts before that guess; not stored
    # --- what was learned: the curve's level, and what it needs on top towards the cold point
    offset_k: float = 0.0  # level: added at every outdoor temperature
    slope_k: float = 0.0  # slope: added in full at -10 °C, not at all at +15 °C
    adjustments: tuple[float, ...] = ()  # timestamps of the corrections
    last_adjust_reason: str | None = None
    # --- evidence of the heating run in progress
    heating_since_ts: float | None = None
    spread_ema: float | None = None
    samples: int = 0
    last_review_ts: float | None = None
    burn_since_ts: float | None = None  # the burner is on for the heating since then
    burns: tuple[tuple[float, float], ...] = ()  # (start, end) of the heating burns of the last hour

    def to_storage(self) -> dict:
        # The timers go with it: after a restart the controller must still know that it
        # switched to "heat" two minutes ago, or it may toggle the boiler right away.
        # `last_send_ts` is deliberately left out so a SetMode goes out immediately.
        return {
            "offset_k": self.offset_k,
            "slope_k": self.slope_k,
            "adjustments": list(self.adjustments[-10:]),
            "last_adjust_reason": self.last_adjust_reason,
            "state": self.state,
            **{key: getattr(self, key) for key in _STORED_FLOATS},
            "burns": [list(burn) for burn in self.burns],
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
        return cls(
            offset_k=0.0 if offset is None else offset,
            slope_k=0.0 if slope is None else slope,
            adjustments=tuple(as_floats(data.get("adjustments"))),
            last_adjust_reason=reason if isinstance(reason, str) else None,
            state=state if state in (BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK) else None,
            burns=tuple(burns),
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
    burner_heating: bool = False  # the boiler's pump state says "heating" (not hot water): the coarsest burner sign
    hot_water_active: bool = False  # the boiler's pump state or hot water mode says: hot water
    gas_power_w: float | None = None  # from a gas meter, None without one
    reported_mode: str | None = None  # BOILER_REPORTS_* from the boiler's status code, None if unknown
    status_known: bool = False  # the boiler's status code is configured and readable
    learning_allowed: bool = True  # False while setpoints move, boost/drying run or safety is not ok
    # Right after a restart the room sensors report within seconds to minutes. A block decided
    # before they did locks the heating (and the pump) off for the minimum state time.
    room_data_pending: bool = False


def cold_weight(outdoor_c: float | None) -> float:
    """Where the outdoor temperature lies between the curve's points: 0 at +15 °C, 1 at -10 °C.

    Without an outdoor temperature the middle is taken, as the curve itself does.
    """
    if outdoor_c is None:
        return 0.5
    return min(max((CURVE_WARM_C - outdoor_c) / (CURVE_WARM_C - CURVE_COLD_C), 0.0), 1.0)


def curve_flow(outdoor_c: float | None, p: Parameters) -> float:
    """Linear heating curve through (-10 °C, cold) and (+15 °C, warm).

    Without an outdoor temperature the curve uses its middle: the cold point would
    heat the flow to the winter maximum on a mild day just because a sensor died.
    """
    if outdoor_c is None:
        value = (p.boiler_curve_flow_cold + p.boiler_curve_flow_warm) / 2.0
    else:
        slope = (p.boiler_curve_flow_cold - p.boiler_curve_flow_warm) / (CURVE_WARM_C - CURVE_COLD_C)
        value = p.boiler_curve_flow_warm + slope * (CURVE_WARM_C - outdoor_c)
    return min(max(value, p.boiler_flow_min), p.boiler_flow_max)


def correction(mem: BoilerMemory, outdoor_c: float | None) -> float:
    """What the learning adds to the curve at this outdoor temperature: level plus its share of the slope."""
    return mem.offset_k + mem.slope_k * cold_weight(outdoor_c)
