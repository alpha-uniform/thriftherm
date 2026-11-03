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

from dataclasses import dataclass, replace

from ..const import PUMP_STATE_HWC, PUMP_STATE_ON, PUMP_STATE_OVERRUN
from ..models import BoilerResult, BoilerState, Prices
from .economics import gas_energy_kwh, gas_power_w

CP_WATER_J_KG_K = 4186.0
MIN_DELTA_T_FOR_HEATING_K = 2.0
# The eBUS adapter drops its signal for about a second every few minutes (measured
# 2026-09-13..16: 2169 changes in three days). Treating each of those as a data
# outage stopped the SetMode for a minute and reset the controller's timers.
EBUS_SIGNAL_GRACE_S = 120.0
# Issue #5: a flow of 98 to 113 °C at a target of 45 °C, also in S.8 with the burner off and under a block sent for over an
# hour, so nothing sent stops it. What it gets: no new release for a while after it, and a warning. From 90 °C: above the
# highest flow sent (75 °C) plus its overshoot of 14 degrees. The warning only from 100 °C, as a knob may stand at 85 °C.
FAULT_FLOW_C = 90.0
OVERHEAT_C = 100.0
FAULT_HOLD_S = 1800.0
FAULT_HOLD_MAX_S = 4 * 3600.0  # a reading that hangs must not hold back the heating for good (engines/safety.py)
OVERHEAT_SHOWN_S = 24 * 3600.0


@dataclass(frozen=True)
class BoilerFault:
    """Since when and until when signs of a fault were seen, the highest flow past `OVERHEAT_C` and the last one."""

    since_ts: float | None = None
    seen_ts: float | None = None
    overheat_ts: float | None = None
    overheat_c: float | None = None
    overheat_last_ts: float | None = None

    def to_storage(self) -> dict:
        return {key: getattr(self, key) for key in _FAULT_KEYS}

    @classmethod
    def from_storage(cls, raw: object) -> BoilerFault:
        if not isinstance(raw, dict):
            return cls()
        values = [raw.get(key) for key in _FAULT_KEYS]
        if not all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in values):
            return cls()
        fault = cls(*(None if v is None else float(v) for v in values))
        if None in (fault.overheat_ts, fault.overheat_c, fault.overheat_last_ts):  # all three or none
            return cls(fault.since_ts, fault.seen_ts)
        return fault


_FAULT_KEYS = ("since_ts", "seen_ts", "overheat_ts", "overheat_c", "overheat_last_ts")


def watch_fault(fault: BoilerFault, flow_c: float | None, waits_for_water: bool, now_ts: float) -> BoilerFault:
    """Note the signs seen now; of the flows past `OVERHEAT_C` within a day the highest with its time, and the last."""
    if flow_c is not None and flow_c >= OVERHEAT_C:
        if not overheated(fault, now_ts) or flow_c > fault.overheat_c:
            fault = replace(fault, overheat_ts=now_ts, overheat_c=flow_c)
        fault = replace(fault, overheat_last_ts=now_ts)
    if not waits_for_water and (flow_c is None or flow_c < FAULT_FLOW_C):
        return fault
    over = fault.seen_ts is None or now_ts - fault.seen_ts >= FAULT_HOLD_S
    return replace(fault, since_ts=now_ts if over or fault.since_ts is None else fault.since_ts, seen_ts=now_ts)


def no_new_release(fault: BoilerFault, now_ts: float) -> bool:
    """Half an hour after the last sign, and no longer than `FAULT_HOLD_MAX_S` in all."""
    if fault.seen_ts is None or fault.since_ts is None:
        return False
    return now_ts - fault.seen_ts < FAULT_HOLD_S and now_ts - fault.since_ts < FAULT_HOLD_MAX_S


def overheated(fault: BoilerFault | None, now_ts: float) -> bool:
    return fault is not None and fault.overheat_last_ts is not None and now_ts - fault.overheat_last_ts < OVERHEAT_SHOWN_S


def signal_with_grace(signal_ok: bool | None, lost_since: float | None, now_ts: float) -> tuple[bool | None, float | None]:
    """Report the signal as present until it has really been gone for the grace period."""
    if signal_ok is not False:
        return signal_ok, None
    started = now_ts if lost_since is None else lost_since
    return (True if now_ts - started < EBUS_SIGNAL_GRACE_S else False), started


def _hot_water(boiler: BoilerState) -> bool | None:
    """Whether the boiler makes hot water: Status01 says hwc, or the hot water sign is on; None without either."""
    if boiler.pump_state is None and boiler.hwc_mode is None:
        return None
    return boiler.pump_state == PUMP_STATE_HWC or boiler.hwc_mode == "on"


def circulating(boiler: BoilerState) -> bool:
    """Whether the pump moves water: the pump itself where it is configured, else Status01 (on, overrun, hot water)."""
    if boiler.pump_running is not None:
        return boiler.pump_running
    return boiler.pump_state in (PUMP_STATE_ON, PUMP_STATE_OVERRUN, PUMP_STATE_HWC)


def pump_heating(boiler: BoilerState) -> bool:
    """Whether the pump runs for the heating, its overrun included: the time a heat pump that heats can save.

    Its hot water time is not: the boiler makes the hot water whoever heats, and the pump share is taken per kWh
    of heating gas only (engines/pump_share.py).
    """
    return circulating(boiler) and not _hot_water(boiler)


def evaluate(boiler: BoilerState, prices: Prices) -> BoilerResult:
    issues: list[str] = []
    available = boiler.signal_ok is not False and boiler.flow_temp.valid
    if boiler.signal_ok is False:
        issues.append("ebusd_signal_lost")

    flow = boiler.flow_temp.value_or_none
    ret = boiler.return_temp.value_or_none
    delta = None if flow is None or ret is None else round(flow - ret, 2)

    hwc_active = _hot_water(boiler)

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

    thermal = None
    if delta is not None and delta > 0 and circulating(boiler):
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
