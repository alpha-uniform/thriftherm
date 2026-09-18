"""Pure data model for Thriftherm.

Nothing in this module imports Home Assistant. All engines operate on these
immutable structures so that the control logic can be unit-tested without a
running Home Assistant instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Literal

Source = Literal["boiler", "midea", "both", "none", "unknown"]
SafetyState = Literal["ok", "degraded", "fallback"]


@dataclass(frozen=True)
class SensorReading:
    """A validated numeric reading from a Home Assistant entity."""

    entity_id: str | None
    value: float | None
    age_s: float | None = None
    valid: bool = False
    reason: str | None = None

    @staticmethod
    def missing(entity_id: str | None = None, reason: str = "not_configured") -> SensorReading:
        return SensorReading(entity_id=entity_id, value=None, age_s=None, valid=False, reason=reason)

    @property
    def value_or_none(self) -> float | None:
        return self.value if self.valid else None


@dataclass(frozen=True)
class TimeWindow:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        # window across midnight
        return t >= self.start or t < self.end


@dataclass(frozen=True)
class ScheduleState:
    """State of a Home Assistant schedule helper assigned to a room."""

    active: bool
    temperature: float | None = None  # optional per-block temperature
    next_start: datetime | None = None  # start of the next comfort block


@dataclass(frozen=True)
class RoomConfig:
    key: str
    name: str
    priority: int
    temperature_sensor: str | None
    humidity_sensor: str | None = None
    window_sensors: tuple[str, ...] = ()
    climate_entity: str | None = None
    served_by_heat_pump: bool = False
    internal_gain_sensors: tuple[str, ...] = ()
    comfort_temp: float = 20.0
    setback_temp: float = 17.0
    schedule_weekday: tuple[TimeWindow, ...] = ()
    schedule_weekend: tuple[TimeWindow, ...] = ()
    bathroom_drying: bool = False
    schedule_entity: str | None = None  # weekly schedule helper, replaces the text windows


@dataclass(frozen=True)
class Override:
    target: float
    until_ts: float
    set_at_ts: float


@dataclass(frozen=True)
class DryingState:
    active: bool = False
    started_ts: float | None = None
    baseline_abs_humidity: float | None = None


@dataclass(frozen=True)
class RoomState:
    config: RoomConfig
    temperature: SensorReading
    humidity: SensorReading
    window_readings: tuple[tuple[str, str | None, float | None], ...]  # (entity_id, state, open_since_s)
    trv_local_temp: float | None
    trv_target_temp: float | None
    internal_gain_w: float | None
    temperature_history: tuple[tuple[float, float], ...]  # (timestamp_s, temp)
    abs_humidity_history: tuple[tuple[float, float], ...] = ()  # (timestamp_s, g/m³), up to 3 h
    override: Override | None = None
    drying: DryingState = DryingState()
    heat_rate_k_h: float | None = None  # learned heat-up rate for preheat planning
    boost_until_ts: float | None = None  # "quick heat-up" requested until
    trv_hvac_mode: str | None = None  # state of the room thermostat (Better Thermostat), None if unavailable
    schedule: ScheduleState | None = None  # None: no schedule helper configured or it is unavailable
    preheat_latch_ts: float | None = None  # comfort start (or away return) preheating already began for


@dataclass(frozen=True)
class HeatPumpState:
    available: bool
    hvac_mode: str | None
    hvac_action: str | None
    target_temp: float | None
    indoor_temp: float | None
    outdoor_temp: float | None
    indoor_coil_temp: float | None
    outdoor_coil_temp: float | None
    compressor_hz: float | None
    fan_rpm: float | None
    realtime_power_w: float | None
    error_code: int | None
    plug_power: SensorReading
    intake_temp: SensorReading
    intake_rh: SensorReading
    outlet_temp: SensorReading
    compressor_running_s: float | None  # seconds since compressor started, None if not running
    outlet_temp_history: tuple[tuple[float, float], ...] = ()  # (timestamp_s, °C), recent
    history: tuple["HeatPumpSample", ...] = ()  # recent samples for defrost detection
    configured: bool = True  # False: the heat pump add-on is not used


@dataclass(frozen=True)
class HeatPumpSample:
    """Compact per-cycle record of the heat pump, kept for ~30 min."""

    ts: float
    compressor_hz: float | None
    power_w: float | None
    outdoor_coil_temp: float | None
    indoor_coil_temp: float | None
    intake_temp: float | None
    outlet_temp: float | None


@dataclass(frozen=True)
class BoilerState:
    signal_ok: bool | None
    flow_temp: SensorReading
    return_temp: SensorReading
    pump_state: str | None
    hwc_mode: str | None
    gas_volume_m3: SensorReading
    gas_flow_m3h: SensorReading
    circulation_l_h: float
    pump_running: bool | None = None  # the heating pump itself (ebusd WP), None if not configured
    state_number: int | None = None  # boiler status S.xx (ebusd Statenumber)


@dataclass(frozen=True)
class Prices:
    electricity_eur_kwh: float
    gas_eur_kwh: float
    boiler_efficiency: float
    gas_calorific_kwh_m3: float
    gas_z_factor: float
    system_type: str = "gas"
    heat_eur_kwh: float = 0.12
    heat_consumption_share_pct: float = 70.0
    heat_ownership_share_pct: float = 0.0


@dataclass(frozen=True)
class Parameters:
    sensor_max_age_s: float = 43200.0
    window_grace_s: float = 90.0
    frost_temp: float = 7.0
    away_temp: float = 15.0
    demand_full_delta_k: float = 2.0
    trend_weight_h: float = 0.5
    gain_threshold_w: float = 150.0
    gain_factor_per_w: float = 0.001
    break_even_margin_on: float = 0.10
    break_even_margin_off: float = 0.05
    heat_pump_min_outdoor_temp: float = -10.0
    heat_pump_setpoint_offset_k: float = 1.0
    cop_warmup_s: float = 600.0
    cop_min_power_w: float = 150.0
    cop_min_delta_t_k: float = 3.0
    cop_max_sensor_age_s: float = 300.0
    cop_min_samples: int = 12
    cop_ema_tau_s: float = 300.0
    cop_min_raw: float = 0.5
    cop_max_raw: float = 7.0
    airflow_curve: tuple[tuple[float, float], ...] = ()  # (rpm, m3/h)
    duct_factor: float = 1.0  # real airflow / free-blowing airflow (hoses attached)
    cop_settle_max_k_per_min: float = 0.5  # outlet sensor must have settled
    icing_block_min: float = 120.0
    defrost_coil_rise_k: float = 5.0
    defrost_max_cycles_90min: int = 3
    icing_coil_depression_k: float = 8.0
    drying_target_temp: float = 22.0
    drying_abs_humidity_rise_g_m3: float = 2.5
    drying_max_s: float = 3600.0
    away_dewpoint_margin_k: float = 3.0
    preheat_margin_s: float = 1200.0
    default_heat_rate_radiator_k_h: float = 0.8
    default_heat_rate_heat_pump_k_h: float = 1.2
    heat_pump_min_run_s: float = 1200.0
    heat_pump_min_off_s: float = 600.0
    heat_pump_learning_runs: bool = True
    boiler_curve_flow_cold: float = 55.0  # flow temperature at -10 °C outdoor
    boiler_curve_flow_warm: float = 30.0  # flow temperature at +15 °C outdoor
    boiler_flow_min: float = 30.0
    boiler_flow_max: float = 60.0


@dataclass(frozen=True)
class HeatingSnapshot:
    now: datetime
    mode: str
    rooms: dict[str, RoomState]
    heat_pump: HeatPumpState
    boiler: BoilerState
    outdoor_temp: SensorReading
    outdoor_temp_source: str | None
    outdoor_rh: SensorReading
    prices: Prices
    params: Parameters
    away_return_ts: float | None = None


# ---------------------------------------------------------------------------
# Engine results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomResult:
    key: str
    target: float
    target_reason: str
    temperature: float | None
    deviation_k: float | None
    demand: float | None  # 0..1
    trend_k_per_h: float | None
    dew_point_c: float | None
    abs_humidity_g_m3: float | None
    window_open: bool | None
    window_unknown: bool
    heating_allowed: bool
    internal_gain_w: float | None
    issues: tuple[str, ...] = ()
    override_until_ts: float | None = None
    drying: DryingState = DryingState()
    drying_reason: str | None = None
    preheat_start_ts: float | None = None
    heat_pump_allowed: bool = True  # Midea may heat (window closed, or drying mode via Midea)
    boost_active: bool = False
    preheat_for_ts: float | None = None  # comfort start / away return this room is preheating for


@dataclass(frozen=True)
class BoilerResult:
    available: bool
    hc_active: bool | None
    hwc_active: bool | None
    delta_t_k: float | None
    thermal_power_estimate_w: float | None
    gas_power_w: float | None
    gas_energy_kwh: float | None
    issues: tuple[str, ...] = ()
    pump_running: bool | None = None
    state_number: int | None = None


@dataclass(frozen=True)
class HeatPumpResult:
    run_state: str
    electrical_power_w: float | None
    electrical_power_source: str | None
    heating_available: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class CopResult:
    value: float | None
    raw: float | None
    thermal_power_w: float | None
    electrical_power_w: float | None
    airflow_m3h: float | None
    samples: int
    gate_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DefrostResult:
    suspected: bool
    kind: str  # none | cycle | persistent
    indicators: tuple[str, ...]
    cycles_last_90min: int
    block_reason: str | None  # set when the Midea should be locked out for heating
    block_until_ts: float | None


@dataclass(frozen=True)
class EconomicsResult:
    gas_cost_per_kwh_thermal: float
    heat_pump_cost_per_kwh_thermal: float | None
    break_even_cop: float
    cheaper_source: str
    saving_pct: float | None


@dataclass(frozen=True)
class SafetyResult:
    state: SafetyState
    issues: tuple[str, ...]
    frost_rooms: tuple[str, ...]


@dataclass(frozen=True)
class HeatPumpCommand:
    """What the Midea controller wants (Phase 7). In shadow mode it is only published."""

    plan: str  # heat | off | hands_off | disabled
    action: str  # none | start | stop | adjust
    hvac_mode: str | None
    target_temp: float | None
    fan_mode: str | None
    load: str | None  # eco | boost | learning
    reason: str
    blockers: tuple[str, ...] = ()
    waiting: str | None = None  # min_run_time / min_off_time / rate_limit


@dataclass(frozen=True)
class BoilerCommand:
    """SetMode plan for the boiler. Sent only in active mode."""

    plan: str  # heat | block | hands_off | disabled
    send: bool  # a SetMode telegram is due this cycle
    payload: str | None
    flow_setpoint: float | None
    disable_hc: bool
    reason: str
    curve_flow: float | None = None
    offset_k: float = 0.0
    blockers: tuple[str, ...] = ()
    waiting: str | None = None
    spread_k: float | None = None  # smoothed flow/return spread while the boiler heats
    learning_phase: str = "paused"  # learning | refining | paused
    adjustments_last_day: int = 0
    last_adjust_reason: str | None = None
    hot_water: bool = False  # hot water recognised within the last minutes
    hot_water_seen_ts: float | None = None


@dataclass(frozen=True)
class RoomCommand:
    """Setpoint plan for a room thermostat (Better Thermostat)."""

    room: str
    plan: str  # set | hold | hands_off | disabled
    target: float | None
    send: bool
    reason: str
    thermostat_target: float | None = None  # what the thermostat currently has
    manual_target: float | None = None  # set when a manual change on the thermostat was detected


@dataclass(frozen=True)
class Reason:
    """One reason of a decision: a code plus the values its text needs.

    The text itself is rendered per language in `texts.py`, so the engines stay
    free of wording and Home Assistant can show German or English.
    """

    code: str
    params: tuple[tuple[str, Any], ...] = ()

    def param(self, key: str, default: Any = None) -> Any:
        return dict(self.params).get(key, default)


def reason(code: str, **params: Any) -> Reason:
    return Reason(code, tuple(params.items()))


@dataclass(frozen=True)
class Advice:
    source: Source
    reasons: tuple[Reason, ...]
    blocked: dict[str, str] = field(default_factory=dict)
