"""Defrost and anti-icing detection for the Midea heat pump.

Midea AC LAN exposes no defrost flag, so defrost is inferred from patterns
(architecture §11). Two outcomes:

* ``cycle``      – a normal reverse-cycle defrost: COP samples are discarded,
                   no error is raised.
* ``persistent`` – frequent cycles or a sustained iced/inefficient state: the
                   Midea is locked out for heating for ``icing_block_min``.

Indicators
  D1 reversal   : outdoor coil rises > coil_rise_k within 2 min while the
                  compressor runs and the indoor coil falls
  D2 cold outlet: heating_stable, outlet − intake < 1 K for > 3 min
  D3 iced coil  : outdoor coil < outdoor air − depression_k for > 10 min — only
                  counts together with D2 or D4: at full load in frost an
                  evaporator 8–12 K below the air is normal operation
  D4 power drop : electrical power falls > 40 % within 1 min, compressor on
  D5 environment: outdoor < 5 °C and RH > 80 % (amplifier only)
"""

from __future__ import annotations

from ..const import HEAT_PUMP_HEATING_STABLE
from ..models import DefrostResult, HeatPumpSample, HeatPumpState, Parameters

CYCLE_WINDOW_S = 90 * 60.0
REVERSAL_WINDOW_S = 120.0
COLD_OUTLET_S = 180.0
ICED_COIL_S = 600.0
POWER_DROP_FRACTION = 0.4
POWER_DROP_WINDOW_S = 60.0
INEFFICIENT_COP_S = 30 * 60.0


def _samples_within(history: tuple[HeatPumpSample, ...], now_ts: float, window_s: float) -> list[HeatPumpSample]:
    return [s for s in history if now_ts - s.ts <= window_s]


def _reversal(history: tuple[HeatPumpSample, ...], now_ts: float, rise_k: float) -> bool:
    recent = _samples_within(history, now_ts, REVERSAL_WINDOW_S)
    if len(recent) < 2:
        return False
    first, last = recent[0], recent[-1]
    if None in (first.outdoor_coil_temp, last.outdoor_coil_temp, last.compressor_hz):
        return False
    if last.compressor_hz <= 0:
        return False
    coil_rise = last.outdoor_coil_temp - first.outdoor_coil_temp
    indoor_falls = (
        first.indoor_coil_temp is not None
        and last.indoor_coil_temp is not None
        and last.indoor_coil_temp < first.indoor_coil_temp - 1.0
    )
    return coil_rise > rise_k and indoor_falls


def _cold_outlet(history: tuple[HeatPumpSample, ...], now_ts: float, run_state: str) -> bool:
    if run_state != HEAT_PUMP_HEATING_STABLE:
        return False
    recent = _samples_within(history, now_ts, COLD_OUTLET_S)
    if len(recent) < 3:
        return False
    deltas = [s.outlet_temp - s.intake_temp for s in recent if s.outlet_temp is not None and s.intake_temp is not None]
    if len(deltas) < 3:
        return False
    return max(deltas) < 1.0 and (recent[-1].ts - recent[0].ts) >= COLD_OUTLET_S * 0.8


def _iced_coil(history: tuple[HeatPumpSample, ...], now_ts: float, outdoor_air: float | None, depression_k: float) -> bool:
    if outdoor_air is None:
        return False
    recent = _samples_within(history, now_ts, ICED_COIL_S)
    if len(recent) < 3 or (recent[-1].ts - recent[0].ts) < ICED_COIL_S * 0.8:
        return False
    coils = [s.outdoor_coil_temp for s in recent if s.outdoor_coil_temp is not None and (s.compressor_hz or 0) > 0]
    if len(coils) < 3:
        return False
    return max(coils) < outdoor_air - depression_k


def _power_drop(history: tuple[HeatPumpSample, ...], now_ts: float) -> bool:
    recent = _samples_within(history, now_ts, POWER_DROP_WINDOW_S * 2)
    powers = [(s.ts, s.power_w) for s in recent if s.power_w is not None and (s.compressor_hz or 0) > 0]
    if len(powers) < 2:
        return False
    peak = max(p for _, p in powers)
    last = powers[-1][1]
    return peak > 300.0 and last < peak * (1.0 - POWER_DROP_FRACTION)


def evaluate(
    heat_pump: HeatPumpState,
    run_state: str,
    outdoor_air_c: float | None,
    outdoor_rh_pct: float | None,
    now_ts: float,
    params: Parameters,
    cycle_starts: tuple[float, ...],
    inefficient_since_ts: float | None,
    existing_block_until_ts: float | None,
) -> DefrostResult:
    """Classify the current state. `cycle_starts` are timestamps of previously detected cycles."""
    indicators: list[str] = []
    hist = heat_pump.history

    if _reversal(hist, now_ts, params.defrost_coil_rise_k):
        indicators.append("D1_coil_reversal")
    if _cold_outlet(hist, now_ts, run_state):
        indicators.append("D2_cold_outlet")
    if _iced_coil(hist, now_ts, outdoor_air_c, params.icing_coil_depression_k):
        indicators.append("D3_iced_coil")
    if _power_drop(hist, now_ts):
        indicators.append("D4_power_drop")
    if outdoor_air_c is not None and outdoor_rh_pct is not None and outdoor_air_c < 5.0 and outdoor_rh_pct > 80.0:
        indicators.append("D5_icing_conditions")

    cycle_now = "D1_coil_reversal" in indicators or ("D2_cold_outlet" in indicators and "D4_power_drop" in indicators)
    recent_cycles = [t for t in cycle_starts if now_ts - t <= CYCLE_WINDOW_S]
    cycles = len(recent_cycles) + (1 if cycle_now and (not recent_cycles or now_ts - recent_cycles[-1] > 300.0) else 0)

    block_reason: str | None = None
    block_until = existing_block_until_ts if existing_block_until_ts and existing_block_until_ts > now_ts else None

    persistent = False
    if cycles > params.defrost_max_cycles_90min:
        persistent = True
        block_reason = "frequent_defrost_cycles"
    if "D3_iced_coil" in indicators and ("D2_cold_outlet" in indicators or "D4_power_drop" in indicators):
        persistent = True
        block_reason = "outdoor_coil_iced"
    if inefficient_since_ts is not None and now_ts - inefficient_since_ts >= INEFFICIENT_COP_S:
        persistent = True
        block_reason = "sustained_inefficiency"

    if persistent:
        block_until = now_ts + params.icing_block_min * 60.0
    elif block_until is not None:
        block_reason = "icing_lockout_active"

    kind = "persistent" if persistent else ("cycle" if cycle_now else "none")
    return DefrostResult(
        suspected=cycle_now or persistent,
        kind=kind,
        indicators=tuple(indicators),
        cycles_last_90min=cycles,
        block_reason=block_reason,
        block_until_ts=block_until,
    )
