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


_LAST_MINUTE = time(23, 59)


@dataclass(frozen=True)
class TimeWindow:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.end == _LAST_MINUTE:
            # "to 23:59" is how the form says "to the end of the day": the last minute belongs to it
            return t >= self.start
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
    next_end: datetime | None = None  # end of the running comfort block


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
    drying_automatic: bool = True  # False: a drying starts with the button only, not by the humidity after a shower
    schedule_entity: str | None = None  # weekly schedule helper, replaces the text windows


@dataclass(frozen=True)
class Override:
    target: float
    until_ts: float
    set_at_ts: float
    by: str | None = None  # who asked for it (engines/hand.py), None for one stored before that was kept


@dataclass(frozen=True)
class SettingChange:
    """The last time a commanded setting of a device changed (its mode or its setpoint), and who did it."""

    ts: float
    origin: str  # engines/hand.py: ours | person | automation | device
    setpoint: bool = True  # the setpoint changed
    mode: bool = False  # the mode changed


@dataclass(frozen=True)
class DryingState:
    active: bool = False
    started_ts: float | None = None
    baseline_abs_humidity: float | None = None
    ended_ts: float | None = None  # when the time limit or a window open without effect ended the last drying
    manual: bool = False  # started with the button: runs to the time limit, whatever the humidity
    # The humidity load of a shower was noticed then, and the drying waits for the shower to end:
    # for the humidity to stop climbing (its highest value so far, and when that was read).
    wet_since_ts: float | None = None
    peak_abs_humidity: float | None = None
    peak_ts: float | None = None


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
    trv_hvac_mode: str | None = None  # state of the room thermostat, None if unavailable
    # the room thermostat says it heats now (its valve open), or that it does not; None if it does not say
    thermostat_heating: bool | None = None
    # the thermostat applies a target of its own right now (Versatile Thermostat: window, load shedding, safety)
    thermostat_own_target: bool = False
    thermostat_change: SettingChange | None = None  # the last change of the thermostat's mode or target
    thermostat_silent_s: float | None = None  # how long the valve device behind it has not reported
    schedule: ScheduleState | None = None  # None: no schedule helper configured or it is unavailable
    preheat_latch_ts: float | None = None  # comfort start (or away return) preheating already began for
    # the setpoints the room thermostat accepts (its min_temp and max_temp), None if it does not say
    thermostat_min_temp: float | None = None
    thermostat_max_temp: float | None = None
    schedule_next_temp: float | None = None  # the temperature the schedule helper's coming block names, None if none


@dataclass(frozen=True)
class HeatPumpState:
    available: bool
    hvac_mode: str | None
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
    setting_change: SettingChange | None = None  # the last change of the unit's mode or setpoint
    fan_mode: str | None = None  # the fan stage the unit shows
    fan_modes: tuple[str, ...] = ()  # the fan stages it offers
    settings_watched_since_ts: float | None = None  # every such change since then is known
    target_temp_step: float | None = None  # the step of its setpoint, where the unit names one
    hvac_modes: tuple[str, ...] = ()  # the modes it offers; empty: not known


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
    flame: bool | None = None  # the boiler's own "burner on" (e.g. OpenTherm), None if not configured
    # the boiler's own sums of burner output (Vaillant PrEnergySum*), name -> reading; engines/gas_split.py
    energy_sums_heating: tuple[tuple[str, float], ...] = ()
    energy_sums_hot_water: tuple[tuple[str, float], ...] = ()


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
    heat_pump_follow_rooms: bool = False  # the unit gets the served rooms' target plus the offset; False: intake air plus offset
    cop_warmup_s: float = 600.0
    cop_min_power_w: float = 150.0
    cop_min_delta_t_k: float = 3.0
    cop_max_sensor_age_s: float = 300.0  # this and the three limits below are fixed: no form asks for them
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
    manual_hold_s: float = 7200.0  # how long a change by hand is respected, on a thermostat and on the heat pump alike
    heat_pump_fan_run_on_s: float = 900.0  # fan only for this long before the unit is switched off; 0: off at once
    heat_pump_learning_runs: bool = True
    boiler_curve_flow_cold: float = 55.0  # flow temperature at -10 °C outdoor
    boiler_curve_flow_warm: float = 30.0  # flow temperature at +15 °C outdoor
    boiler_flow_min: float = 30.0
    boiler_flow_max: float = 60.0
    boiler_hot_water_standby: bool = True  # False: SetMode disables the keep-warm / cylinder load
    boiler_heat_up_rate_k_h: float = 0.0  # how fast a room is to heat up (engines/heat_up.py); 0: off
    boiler_spread_min_room_share: float = 0.5  # the spread judges the curve only while this share of the rooms calls
    boiler_load_model: bool = True  # a curve per load class (how many rooms call); False: one common correction for all


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
    override_by: str | None = None  # who asked for the override in force (engines/hand.py)
    drying: DryingState = DryingState()
    drying_reason: str | None = None
    preheat_start_ts: float | None = None
    heat_pump_allowed: bool = True  # Midea may heat (window closed, or drying mode via Midea)
    boost_active: bool = False
    preheat_for_ts: float | None = None  # comfort start / away return this room is preheating for
    comfort_end_ts: float | None = None  # end of the running comfort window (schedule only)
    boiler_call: str | None = None  # engines/heat_call.py: calling, idle, stalled, comfort_ending, not_allowed
    thermostat_heating: bool | None = None  # see RoomState


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
    cycle_cop: float | None = None  # heat over electricity of the last whole on/off cycle, while it is fresh


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
    gas_cost_per_kwh_thermal: float | None  # None, like the break-even, when the prices cannot be used
    heat_pump_cost_per_kwh_thermal: float | None
    break_even_cop: float | None
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
    delivers: bool = True  # False once a unit that follows the rooms, set as high as it may be, does not get a room warm
    manual_until_ts: float | None = None  # a change by hand is respected until then
    manual_by: str | None = None  # and who made it (engines/hand.py)
    not_carried_out: int = 0  # commands in a row the unit did not carry out, though nobody touched it


@dataclass(frozen=True)
class BoilerCommand:
    """Plan for the boiler: a SetMode telegram over ebusd, or flow and heating for its entities. Sent only in active mode."""

    plan: str  # heat | block | hands_off | disabled
    send: bool  # a SetMode telegram is due this cycle
    payload: str | None
    flow_setpoint: float | None
    disable_hc: bool
    reason: str
    curve_flow: float | None = None
    offset_k: float = 0.0  # what the learning adds at the current outdoor temperature
    offset_level_k: float = 0.0  # its level part, added at every outdoor temperature
    offset_slope_k: float = 0.0  # its slope part, added in full at -10 °C
    blockers: tuple[str, ...] = ()
    waiting: str | None = None
    spread_k: float | None = None  # smoothed flow/return spread while the boiler heats
    learning_phase: str = "paused"  # learning | refining | paused
    adjustments_last_day: int = 0
    moved_last_day_k: float = 0.0  # by how much those steps moved the curve, both directions added up
    last_adjust_reason: str | None = None
    hot_water: bool = False  # hot water recognised within the last minutes
    hot_water_seen_ts: float | None = None
    held_back: str | None = None  # a learning step that is due but cannot be taken, e.g. at the flow minimum
    burner_share: float = 0.0  # share of the last hour the burner ran for the heating (0..1)
    burner_starts_last_hour: int = 0
    calling_rooms: int = 0  # rooms that really ask the boiler for heat (not the ones heating along)
    rooms_total: int = 0  # rooms whose radiator could take heat right now
    spread_rule: str | None = None  # active | blocked_few_rooms, None while the heating is not released
    load_class: str | None = None  # how many rooms call: single | few | majority, None while no room calls
    flow_set_by: str | None = None  # the room whose line sets the flow
    heat_up_k: dict | None = None  # each room's heat-up extra: in force now, or where its next heat-up starts
    heating_up: tuple[str, ...] = ()  # the rooms whose heat-up adds to the flow now
    curve_by_load: dict | None = None  # the line of each load class: correction now, level, slope, found, hours


@dataclass(frozen=True)
class RoomCommand:
    """Setpoint plan for a room thermostat."""

    room: str
    plan: str  # set | hold | hands_off | disabled
    target: float | None
    send: bool
    reason: str
    thermostat_target: float | None = None  # what the thermostat currently has


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
