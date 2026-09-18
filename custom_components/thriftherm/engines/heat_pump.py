"""Midea engine: run state classification and electrical power selection."""

from __future__ import annotations

from ..const import (
    HEAT_PUMP_AUTO,
    HEAT_PUMP_COOLING_USER,
    HEAT_PUMP_FAN_ONLY,
    HEAT_PUMP_HEATING_IDLE,
    HEAT_PUMP_HEATING_STABLE,
    HEAT_PUMP_HEATING_WARMING_UP,
    HEAT_PUMP_OFF,
    HEAT_PUMP_UNAVAILABLE,
)
from ..models import HeatPumpResult, HeatPumpState, Parameters

PLAUSIBILITY_TOLERANCE = 0.15


def electrical_power(heat_pump: HeatPumpState) -> tuple[float | None, str | None]:
    """Prefer the measured plug power; fall back to the Midea internal value."""
    if heat_pump.plug_power.valid:
        return heat_pump.plug_power.value, "plug"
    if heat_pump.realtime_power_w is not None and heat_pump.available:
        return heat_pump.realtime_power_w, "midea_internal"
    return None, None


def run_state(heat_pump: HeatPumpState, params: Parameters) -> str:
    if not heat_pump.available:
        return HEAT_PUMP_UNAVAILABLE
    mode = (heat_pump.hvac_mode or "").lower()
    if mode in ("", "off"):
        return HEAT_PUMP_OFF
    if mode in ("cool", "dry"):
        return HEAT_PUMP_COOLING_USER
    if mode == "fan_only":
        return HEAT_PUMP_FAN_ONLY
    if mode == "auto":
        return HEAT_PUMP_AUTO
    # heat
    if heat_pump.compressor_hz is not None and heat_pump.compressor_hz > 0:
        if heat_pump.compressor_running_s is not None and heat_pump.compressor_running_s < params.cop_warmup_s:
            return HEAT_PUMP_HEATING_WARMING_UP
        return HEAT_PUMP_HEATING_STABLE
    return HEAT_PUMP_HEATING_IDLE


def evaluate(heat_pump: HeatPumpState, params: Parameters) -> HeatPumpResult:
    issues: list[str] = []
    power, source = electrical_power(heat_pump)
    state = run_state(heat_pump, params)

    if heat_pump.plug_power.valid and heat_pump.realtime_power_w is not None and heat_pump.plug_power.value > 50:
        rel = abs(heat_pump.plug_power.value - heat_pump.realtime_power_w) / heat_pump.plug_power.value
        if rel > PLAUSIBILITY_TOLERANCE:
            issues.append(f"power_mismatch_plug_vs_internal:{rel:.0%}")

    if heat_pump.error_code not in (None, 0):
        issues.append(f"error_code:{heat_pump.error_code}")

    heating_available = (
        heat_pump.available
        and heat_pump.error_code in (None, 0)
        and state not in (HEAT_PUMP_COOLING_USER, HEAT_PUMP_FAN_ONLY, HEAT_PUMP_UNAVAILABLE)
    )
    if state in (HEAT_PUMP_COOLING_USER, HEAT_PUMP_FAN_ONLY):
        issues.append("user_mode_active_not_available_for_heating")

    return HeatPumpResult(
        run_state=state,
        electrical_power_w=power,
        electrical_power_source=source,
        heating_available=heating_available,
        issues=tuple(issues),
    )
