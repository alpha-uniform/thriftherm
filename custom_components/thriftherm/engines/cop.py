"""COP engine: air-side thermal power, validity gates, robust aggregation.

    COP = Q_thermal / P_electrical

Q_thermal is computed from the enthalpy difference between intake and outlet
air and an airflow estimated from the indoor fan speed (rpm → m³/h curve
calibrated during setup). When any gate fails the COP is reported as
unavailable instead of an invented number.
"""

from __future__ import annotations

from statistics import median

from ..const import HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP
from ..models import CopResult, HeatPumpState, Parameters
from . import psychrometrics as psy

CARNOT_FRACTION_LIMIT = 0.6
MEDIAN_WINDOW = 5
COP_STALE_S = 180.0  # three update cycles without a valid sample
RH_MAX_AGE_S = 1800.0  # humidity sensors report on change only; RH drifts slowly


def parse_airflow_curve(text: str | None) -> tuple[tuple[float, float], ...]:
    """Parse 'rpm:m3h,rpm:m3h' into sorted calibration points."""
    if not text or not text.strip():
        return ()
    points: list[tuple[float, float]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        rpm_s, flow_s = part.split(":")
        points.append((float(rpm_s), float(flow_s)))
    points.sort()
    return tuple(points)


def airflow_from_rpm(curve: tuple[tuple[float, float], ...], rpm: float | None) -> float | None:
    if rpm is None or len(curve) < 2:
        return None
    # Just outside the calibrated range, volume flow still follows fan speed
    # (fan law: flow ~ rpm); holding the end value flat misstated COP by up to 25 %.
    if rpm <= curve[0][0]:
        return curve[0][1] * rpm / curve[0][0] if rpm >= curve[0][0] * 0.8 else None
    if rpm >= curve[-1][0]:
        return curve[-1][1] * rpm / curve[-1][0] if rpm <= curve[-1][0] * 1.2 else None
    for (r0, f0), (r1, f1) in zip(curve, curve[1:]):
        if r0 <= rpm <= r1:
            if r1 == r0:
                return f0
            return f0 + (f1 - f0) * (rpm - r0) / (r1 - r0)
    return None


def outlet_slope_k_per_min(history: tuple[tuple[float, float], ...], window_s: float = 120.0) -> float:
    """Absolute temperature slope of the outlet sensor over the last window (K/min).

    The SNZB-02B needs several minutes to settle after a compressor start; a
    sample taken while it is still climbing under-reports the thermal power.
    Returns 0 when the history is too short to judge.
    """
    if len(history) < 2:
        return 0.0
    last_ts = history[-1][0]
    pts = [(ts, t) for ts, t in history if last_ts - ts <= window_s]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < window_s * 0.5:
        return 0.0
    return abs(pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 60.0)


def carnot_limit(indoor_coil_c: float | None, outdoor_coil_c: float | None) -> float | None:
    if indoor_coil_c is None or outdoor_coil_c is None:
        return None
    lift = indoor_coil_c - outdoor_coil_c
    if lift < 3.0:
        return None
    return (indoor_coil_c + 273.15) / lift


def instant(heat_pump: HeatPumpState, run_state: str, electrical_power_w: float | None, params: Parameters) -> CopResult:
    """Compute one raw COP sample with all gates applied."""
    gates: list[str] = []
    airflow = airflow_from_rpm(params.airflow_curve, heat_pump.fan_rpm)
    if airflow is not None:
        airflow = round(airflow * params.duct_factor, 1)

    if run_state == HEAT_PUMP_HEATING_WARMING_UP:
        gates.append("warming_up")
    elif run_state != HEAT_PUMP_HEATING_STABLE:
        gates.append(f"not_heating:{run_state}")
    if electrical_power_w is None:
        gates.append("electrical_power_missing")
    elif electrical_power_w < params.cop_min_power_w:
        gates.append("electrical_power_below_minimum")
    if not params.airflow_curve:
        gates.append("airflow_curve_not_calibrated")
    elif airflow is None:
        gates.append("fan_rpm_outside_curve")
    for name, reading, max_age in (
        ("intake_temp", heat_pump.intake_temp, RH_MAX_AGE_S),  # intake air is room air: changes slowly, reports on change
        ("intake_rh", heat_pump.intake_rh, RH_MAX_AGE_S),
        ("outlet_temp", heat_pump.outlet_temp, params.cop_max_sensor_age_s),
    ):
        if not reading.valid:
            gates.append(f"{name}_invalid:{reading.reason}")
        elif reading.age_s is not None and reading.age_s > max_age:
            gates.append(f"{name}_stale")

    if outlet_slope_k_per_min(heat_pump.outlet_temp_history) > params.cop_settle_max_k_per_min:
        gates.append("outlet_temp_not_settled")

    thermal = None
    raw = None
    if not gates:
        assert airflow is not None and electrical_power_w is not None
        delta_t = heat_pump.outlet_temp.value - heat_pump.intake_temp.value
        if delta_t < params.cop_min_delta_t_k:
            gates.append("delta_t_below_minimum")
        else:
            # Heating adds no water to the air, so the outlet humidity ratio equals the
            # intake one. A measured outlet RH only adds error: at 35 °C each RH point is
            # ~0.4 g/kg, and a stale reading once turned COP 3.4 into 8.1.
            outlet_rh = None
            thermal = psy.air_heating_power_w(
                heat_pump.intake_temp.value,
                heat_pump.intake_rh.value,
                heat_pump.outlet_temp.value,
                outlet_rh,
                airflow,
            )
            raw = thermal / electrical_power_w
            if raw < params.cop_min_raw or raw > params.cop_max_raw:
                gates.append("raw_cop_out_of_range")
            limit = carnot_limit(heat_pump.indoor_coil_temp, heat_pump.outdoor_coil_temp)
            if limit is not None and raw > CARNOT_FRACTION_LIMIT * limit:
                gates.append("exceeds_carnot_limit")

    return CopResult(
        value=None,
        raw=None if gates else raw,
        thermal_power_w=None if thermal is None else round(thermal, 0),
        electrical_power_w=electrical_power_w,
        airflow_m3h=airflow,
        samples=0,
        gate_reasons=tuple(gates),
    )


def aggregate(samples: list[tuple[float, float]], now_ts: float, params: Parameters) -> tuple[float | None, int]:
    """Median-filter then time-weighted EMA over valid raw samples.

    `samples` are (timestamp_s, raw_cop) within the retention window.
    """
    # A value from a run that has ended is not a measurement any more.
    if samples and now_ts - max(ts for ts, _ in samples) > COP_STALE_S:
        return None, len(samples)
    if len(samples) < params.cop_min_samples:
        return None, len(samples)
    ordered = sorted(samples)
    filtered: list[tuple[float, float]] = []
    for i in range(len(ordered)):
        lo = max(0, i - MEDIAN_WINDOW // 2)
        hi = min(len(ordered), i + MEDIAN_WINDOW // 2 + 1)
        filtered.append((ordered[i][0], median(v for _, v in ordered[lo:hi])))
    ema = filtered[0][1]
    prev_ts = filtered[0][0]
    for ts, v in filtered[1:]:
        dt = max(ts - prev_ts, 0.0)
        alpha = 1.0 - pow(2.718281828, -dt / params.cop_ema_tau_s) if params.cop_ema_tau_s > 0 else 1.0
        ema += alpha * (v - ema)
        prev_ts = ts
    return round(ema, 3), len(samples)
