"""Boiler engine: derived states and rough thermal power of the gas boiler.

The Vaillant atmoTEC plus VCW 194/4-5 publishes no flame or modulation value
through the default ebusd MQTT integration, so heating activity is derived
from the boiler state and flow/return spread. The thermal power estimate uses the
nominal circulation flow of the internal pump (860 l/h) and is therefore an
indication (±30 %), not a measurement.

The last field of ebusd "Status01" is named pump state but follows the heating
demand (off/on/overrun/hwc); it is kept for hot water and heating detection.
Whether water actually circulates comes from the pump itself (ebusd "WP") when
that is configured.
"""

from __future__ import annotations

from ..const import PUMP_STATE_HWC, PUMP_STATE_ON, PUMP_STATE_OVERRUN
from ..models import BoilerResult, BoilerState, Prices
from .economics import gas_energy_kwh, gas_power_w

CP_WATER_J_KG_K = 4186.0
MIN_DELTA_T_FOR_HEATING_K = 2.0
# The eBUS adapter drops its signal for about a second every few minutes (measured
# 2026-09-13..16: 2169 changes in three days). Treating each of those as a data
# outage stopped the SetMode for a minute and reset the controller's timers.
EBUS_SIGNAL_GRACE_S = 120.0


def signal_with_grace(signal_ok: bool | None, lost_since: float | None, now_ts: float) -> tuple[bool | None, float | None]:
    """Report the signal as present until it has really been gone for the grace period."""
    if signal_ok is not False:
        return signal_ok, None
    started = now_ts if lost_since is None else lost_since
    return (True if now_ts - started < EBUS_SIGNAL_GRACE_S else False), started


def evaluate(boiler: BoilerState, prices: Prices) -> BoilerResult:
    issues: list[str] = []
    available = boiler.signal_ok is not False and boiler.flow_temp.valid
    if boiler.signal_ok is False:
        issues.append("ebusd_signal_lost")

    flow = boiler.flow_temp.value_or_none
    ret = boiler.return_temp.value_or_none
    delta = None if flow is None or ret is None else round(flow - ret, 2)

    hwc_active: bool | None
    if boiler.pump_state is None and boiler.hwc_mode is None:
        hwc_active = None
    else:
        hwc_active = boiler.pump_state == PUMP_STATE_HWC or boiler.hwc_mode == "on"

    hc_active: bool | None
    if boiler.pump_state is None:
        hc_active = None
    else:
        hc_active = (
            boiler.pump_state == PUMP_STATE_ON
            and not hwc_active
            and delta is not None
            and delta >= MIN_DELTA_T_FOR_HEATING_K
        )

    if boiler.pump_running is not None:
        circulating = boiler.pump_running
    else:
        circulating = boiler.pump_state in (PUMP_STATE_ON, PUMP_STATE_OVERRUN, PUMP_STATE_HWC)
    thermal = None
    if delta is not None and delta > 0 and circulating:
        mass_flow = boiler.circulation_l_h / 3600.0  # kg/s (1 l ≈ 1 kg)
        thermal = round(mass_flow * CP_WATER_J_KG_K * delta, 0)

    gas_p = None
    if boiler.gas_flow_m3h.valid:
        gas_p = round(gas_power_w(boiler.gas_flow_m3h.value, prices.gas_z_factor, prices.gas_calorific_kwh_m3), 0)
    gas_e = None
    if boiler.gas_volume_m3.valid:
        gas_e = round(gas_energy_kwh(boiler.gas_volume_m3.value, prices.gas_z_factor, prices.gas_calorific_kwh_m3), 3)

    return BoilerResult(
        available=available,
        hc_active=hc_active,
        hwc_active=hwc_active,
        delta_t_k=delta,
        thermal_power_estimate_w=thermal,
        gas_power_w=gas_p,
        gas_energy_kwh=gas_e,
        issues=tuple(issues),
        pump_running=boiler.pump_running,
        state_number=boiler.state_number,
    )
