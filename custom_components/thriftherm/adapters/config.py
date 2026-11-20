"""Translate raw config-entry dictionaries into typed model objects."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..const import (
    CONF_AWAY_DEWPOINT_MARGIN,
    CONF_AWAY_TEMP,
    CONF_BOILER_CURVE_COLD,
    CONF_BOILER_CURVE_WARM,
    CONF_BOILER_EFFICIENCY,
    CONF_BOILER_CONDENSING,
    CONF_BOILER_FLOW_MAX,
    CONF_BOILER_FLOW_MIN,
    CONF_BOILER_HEAT_UP_RATE,
    CONF_BOILER_HOT_WATER_STANDBY,
    CONF_BOILER_SPREAD_MIN_ROOM_SHARE,
    CONF_BREAK_EVEN_MARGIN_OFF,
    CONF_BREAK_EVEN_MARGIN_ON,
    CONF_COP_INTAKE_MAX_AGE_MIN,
    CONF_COP_MIN_DELTA_T,
    CONF_COP_MIN_POWER_W,
    CONF_COP_MIN_SAMPLES,
    CONF_COP_OUTLET_MAX_AGE_MIN,
    CONF_COP_SETTLE_MAX_K_PER_MIN,
    CONF_COP_WARMUP_S,
    CONF_DEFAULT_HEAT_RATE_HEAT_PUMP,
    CONF_DEFAULT_HEAT_RATE_RADIATOR,
    CONF_DEFROST_COIL_RISE_K,
    CONF_DEFROST_MAX_CYCLES_90MIN,
    CONF_DEMAND_FULL_DELTA,
    CONF_DRYING_ABS_HUMIDITY_RISE,
    CONF_DRYING_MAX_MIN,
    CONF_DRYING_TARGET_TEMP,
    CONF_ELECTRICITY_PRICE,
    CONF_FROST_TEMP,
    CONF_GAIN_FACTOR,
    CONF_GAIN_THRESHOLD_W,
    CONF_GAS_CALORIFIC_VALUE,
    CONF_GAS_PRICE,
    CONF_GAS_Z_FACTOR,
    CONF_HEAT_CONSUMPTION_SHARE,
    CONF_HEAT_OWNERSHIP_SHARE,
    CONF_HEAT_PRICE,
    CONF_HEAT_PUMP_AIRFLOW_CURVE,
    CONF_HEAT_PUMP_COP_PROFILE,
    CONF_HEAT_PUMP_DUCT_FACTOR,
    CONF_HEAT_PUMP_FAN_RUN_ON_MIN,
    CONF_HEAT_PUMP_FAN_STAGE,
    CONF_HEAT_PUMP_LEARNING_RUNS,
    CONF_HEAT_PUMP_MIN_OFF_MIN,
    CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    CONF_HEAT_PUMP_MIN_RUN_MIN,
    CONF_ICING_BLOCK_MIN,
    CONF_ICING_COIL_DEPRESSION_K,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_PREHEAT_MARGIN_MIN,
    CONF_ROOM_CLIMATE,
    CONF_ROOM_COMFORT_TEMP,
    CONF_ROOM_COOLING_SCHEDULE_ENTITY,
    CONF_ROOM_DRYING,
    CONF_ROOM_DRYING_AUTO,
    CONF_ROOM_GAIN_SENSORS,
    CONF_ROOM_HEAT_PUMP,
    CONF_ROOM_HUMIDITY,
    CONF_ROOM_KEY,
    CONF_ROOM_LEAD_K,
    CONF_ROOM_NAME,
    CONF_ROOM_PREHEAT_MAX_MIN,
    CONF_ROOM_PRIORITY,
    CONF_ROOM_SCHEDULE_ENTITY,
    CONF_ROOM_SCHEDULE_WEEKDAY,
    CONF_ROOM_SCHEDULE_WEEKEND,
    CONF_ROOM_SENSORS_ON_CHANGE,
    CONF_ROOM_SETBACK_TEMP,
    CONF_ROOM_TEMP,
    CONF_ROOM_VALVE_CONTROL,
    CONF_ROOM_VALVES,
    CONF_ROOM_WINDOWS,
    CONF_SENSOR_MAX_AGE_MIN,
    CONF_SYSTEM_TYPE,
    CONF_TREND_WEIGHT_H,
    CONF_WINDOW_GRACE_S,
    CONF_TOLERANCE_ABOVE_K,
    CONF_TOLERANCE_BELOW_K,
    CONF_WINDOW_VALVE_ACTION,
    DEFAULT_AWAY_DEWPOINT_MARGIN,
    DEFAULT_AWAY_TEMP,
    DEFAULT_BOILER_CURVE_COLD,
    DEFAULT_BOILER_CURVE_WARM,
    DEFAULT_BOILER_EFFICIENCY,
    DEFAULT_BOILER_CONDENSING,
    DEFAULT_BOILER_FLOW_MAX,
    DEFAULT_BOILER_FLOW_MIN,
    DEFAULT_BOILER_HEAT_UP_RATE,
    DEFAULT_BOILER_HOT_WATER_STANDBY,
    DEFAULT_BOILER_SPREAD_MIN_ROOM_SHARE,
    DEFAULT_BREAK_EVEN_MARGIN_OFF,
    DEFAULT_BREAK_EVEN_MARGIN_ON,
    DEFAULT_COP_INTAKE_MAX_AGE_MIN,
    DEFAULT_COP_MIN_DELTA_T,
    DEFAULT_COP_MIN_POWER_W,
    DEFAULT_COP_MIN_SAMPLES,
    DEFAULT_COP_OUTLET_MAX_AGE_MIN,
    DEFAULT_COP_SETTLE_MAX_K_PER_MIN,
    DEFAULT_COP_WARMUP_S,
    DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP,
    DEFAULT_DEFAULT_HEAT_RATE_RADIATOR,
    DEFAULT_DEFROST_COIL_RISE_K,
    DEFAULT_DEFROST_MAX_CYCLES_90MIN,
    DEFAULT_DEMAND_FULL_DELTA,
    DEFAULT_DRYING_ABS_HUMIDITY_RISE,
    DEFAULT_DRYING_MAX_MIN,
    DEFAULT_DRYING_TARGET_TEMP,
    DEFAULT_ELECTRICITY_PRICE,
    DEFAULT_FROST_TEMP,
    DEFAULT_GAIN_FACTOR,
    DEFAULT_GAIN_THRESHOLD_W,
    DEFAULT_GAS_CALORIFIC_VALUE,
    DEFAULT_GAS_PRICE,
    DEFAULT_GAS_Z_FACTOR,
    DEFAULT_HEAT_CONSUMPTION_SHARE,
    DEFAULT_HEAT_OWNERSHIP_SHARE,
    DEFAULT_HEAT_PRICE,
    DEFAULT_HEAT_PUMP_COP_PROFILE,
    DEFAULT_HEAT_PUMP_DUCT_FACTOR,
    DEFAULT_HEAT_PUMP_FAN_RUN_ON_MIN,
    DEFAULT_HEAT_PUMP_FAN_STAGE,
    DEFAULT_HEAT_PUMP_LEARNING_RUNS,
    DEFAULT_HEAT_PUMP_MIN_OFF_MIN,
    DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    DEFAULT_HEAT_PUMP_MIN_RUN_MIN,
    DEFAULT_ICING_BLOCK_MIN,
    DEFAULT_ICING_COIL_DEPRESSION_K,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_PREHEAT_MARGIN_MIN,
    DEFAULT_ROOM_COMFORT_TEMP,
    DEFAULT_ROOM_LEAD_K,
    DEFAULT_ROOM_SENSORS_ON_CHANGE,
    DEFAULT_ROOM_SETBACK_TEMP,
    DEFAULT_SCHEDULE_WEEKDAY,
    DEFAULT_SCHEDULE_WEEKEND,
    DEFAULT_SENSOR_MAX_AGE_MIN,
    DEFAULT_SYSTEM_TYPE,
    DEFAULT_TREND_WEIGHT_H,
    DEFAULT_WINDOW_GRACE_S,
    ROOM_LEAD_MAX_K,
    SYSTEM_TYPES,
    VALVE_CONTROL_OFFSET,
    VALVE_CONTROL_OPENING,
    VALVE_CONTROL_SETPOINT,
    WINDOW_VALVE_FROST,
)
from ..engines.cop import parse_airflow_curve
from ..engines.room import parse_schedule
from ..models import Parameters, Prices, RoomConfig


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value if v)


def _minutes_as_s(value: Any) -> float | None:
    """A number of minutes in seconds; None where none is set. 0 is a value: no preheat at all."""
    try:
        return None if value in (None, "") else float(value) * 60.0
    except (TypeError, ValueError):
        return None


def _lead_k(value: Any) -> float:
    """A room's lead at its thermostat: in half degrees from 0 to ROOM_LEAD_MAX_K, the default where none is stored."""
    try:
        lead = float(value)
    except (TypeError, ValueError):
        return DEFAULT_ROOM_LEAD_K
    if not math.isfinite(lead):
        return DEFAULT_ROOM_LEAD_K
    return min(max(round(lead * 2.0) / 2.0, 0.0), ROOM_LEAD_MAX_K)


def room_configs_from_config(rooms: list[Mapping[str, Any]]) -> tuple[RoomConfig, ...]:
    out: list[RoomConfig] = []
    for raw in rooms:
        # Valves and a room thermostat both stored (by hand, or a form of before): what it did before, no valve written
        thermostat = raw.get(CONF_ROOM_CLIMATE) or None
        valves = () if thermostat else tuple(dict.fromkeys(_as_tuple(raw.get(CONF_ROOM_VALVES))))
        out.append(
            RoomConfig(
                key=str(raw[CONF_ROOM_KEY]),
                name=str(raw.get(CONF_ROOM_NAME, raw[CONF_ROOM_KEY])),
                priority=int(raw.get(CONF_ROOM_PRIORITY, 5)),
                temperature_sensor=raw.get(CONF_ROOM_TEMP) or None,
                humidity_sensor=raw.get(CONF_ROOM_HUMIDITY) or None,
                window_sensors=_as_tuple(raw.get(CONF_ROOM_WINDOWS)),
                climate_entity=thermostat or (valves[0] if valves else None),
                served_by_heat_pump=bool(raw.get(CONF_ROOM_HEAT_PUMP, False)),
                internal_gain_sensors=_as_tuple(raw.get(CONF_ROOM_GAIN_SENSORS)),
                comfort_temp=float(raw.get(CONF_ROOM_COMFORT_TEMP, DEFAULT_ROOM_COMFORT_TEMP)),
                setback_temp=float(raw.get(CONF_ROOM_SETBACK_TEMP, DEFAULT_ROOM_SETBACK_TEMP)),
                schedule_weekday=parse_schedule(raw.get(CONF_ROOM_SCHEDULE_WEEKDAY, DEFAULT_SCHEDULE_WEEKDAY)),
                schedule_weekend=parse_schedule(raw.get(CONF_ROOM_SCHEDULE_WEEKEND, DEFAULT_SCHEDULE_WEEKEND)),
                bathroom_drying=bool(raw.get(CONF_ROOM_DRYING, False)),
                drying_automatic=bool(raw.get(CONF_ROOM_DRYING_AUTO, True)),
                schedule_entity=raw.get(CONF_ROOM_SCHEDULE_ENTITY) or None,
                cooling_schedule_entity=raw.get(CONF_ROOM_COOLING_SCHEDULE_ENTITY) or None,
                preheat_max_s=_minutes_as_s(raw.get(CONF_ROOM_PREHEAT_MAX_MIN)),
                lead_k=_lead_k(raw.get(CONF_ROOM_LEAD_K)),
                valve_entities=valves,
                valve_opening=bool(valves) and raw.get(CONF_ROOM_VALVE_CONTROL) == VALVE_CONTROL_OPENING,
                valve_force=raw.get(CONF_ROOM_VALVE_CONTROL) if valves and raw.get(CONF_ROOM_VALVE_CONTROL) in (
                    VALVE_CONTROL_OFFSET, VALVE_CONTROL_SETPOINT
                ) else None,
            )
        )
    out.sort(key=lambda r: r.priority)
    return tuple(out)


def system_type_from_config(config: Mapping[str, Any]) -> str:
    value = config.get(CONF_SYSTEM_TYPE)
    return value if value in SYSTEM_TYPES else DEFAULT_SYSTEM_TYPE


def prices_from_config(config: Mapping[str, Any]) -> Prices:
    def num(key: str, default: float) -> float:
        """Read a number, treating a cleared field like a missing one."""
        value = config.get(key)
        return default if value is None else float(value)

    return Prices(
        electricity_eur_kwh=num(CONF_ELECTRICITY_PRICE, DEFAULT_ELECTRICITY_PRICE),
        gas_eur_kwh=num(CONF_GAS_PRICE, DEFAULT_GAS_PRICE),
        boiler_efficiency=num(CONF_BOILER_EFFICIENCY, DEFAULT_BOILER_EFFICIENCY),
        gas_calorific_kwh_m3=num(CONF_GAS_CALORIFIC_VALUE, DEFAULT_GAS_CALORIFIC_VALUE),
        gas_z_factor=num(CONF_GAS_Z_FACTOR, DEFAULT_GAS_Z_FACTOR),
        system_type=system_type_from_config(config),
        heat_eur_kwh=num(CONF_HEAT_PRICE, DEFAULT_HEAT_PRICE),
        heat_consumption_share_pct=num(CONF_HEAT_CONSUMPTION_SHARE, DEFAULT_HEAT_CONSUMPTION_SHARE),
        heat_ownership_share_pct=num(CONF_HEAT_OWNERSHIP_SHARE, DEFAULT_HEAT_OWNERSHIP_SHARE),
    )


def _call_band(config: Mapping[str, Any]) -> dict[str, float]:
    """The band of a room's call; the one of before where none is stored or it makes no band (an end before the start)."""
    from ..engines.heat_call import CALL_OFF_K, CALL_ON_K

    try:
        on = float(config.get(CONF_TOLERANCE_BELOW_K, CALL_ON_K))
        off = -float(config.get(CONF_TOLERANCE_ABOVE_K, -CALL_OFF_K))
    except (TypeError, ValueError):
        return {"call_on_k": CALL_ON_K, "call_off_k": CALL_OFF_K}
    # narrow on purpose (review 2026-11-15 §3.8): below 0.2 to 0.5, above −0.1 (the end of before) to 0.5
    if not (math.isfinite(on) and math.isfinite(off)) or off >= on or not 0.2 <= on <= 0.5 or not -0.5 <= off <= 0.1:
        return {"call_on_k": CALL_ON_K, "call_off_k": CALL_OFF_K}
    return {"call_on_k": on, "call_off_k": off}


def parameters_from_config(config: Mapping[str, Any]) -> Parameters:
    def f(key: str, default: float) -> float:
        return float(config.get(key, default))

    return Parameters(
        sensor_max_age_s=f(CONF_SENSOR_MAX_AGE_MIN, DEFAULT_SENSOR_MAX_AGE_MIN) * 60.0,
        room_sensors_on_change=bool(config.get(CONF_ROOM_SENSORS_ON_CHANGE, DEFAULT_ROOM_SENSORS_ON_CHANGE)),
        window_grace_s=f(CONF_WINDOW_GRACE_S, DEFAULT_WINDOW_GRACE_S),
        window_valve_off=config.get(CONF_WINDOW_VALVE_ACTION) != WINDOW_VALVE_FROST,
        **_call_band(config),
        frost_temp=f(CONF_FROST_TEMP, DEFAULT_FROST_TEMP),
        away_temp=f(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP),
        demand_full_delta_k=f(CONF_DEMAND_FULL_DELTA, DEFAULT_DEMAND_FULL_DELTA),
        trend_weight_h=f(CONF_TREND_WEIGHT_H, DEFAULT_TREND_WEIGHT_H),
        gain_threshold_w=f(CONF_GAIN_THRESHOLD_W, DEFAULT_GAIN_THRESHOLD_W),
        gain_factor_per_w=f(CONF_GAIN_FACTOR, DEFAULT_GAIN_FACTOR),
        break_even_margin_on=f(CONF_BREAK_EVEN_MARGIN_ON, DEFAULT_BREAK_EVEN_MARGIN_ON),
        break_even_margin_off=f(CONF_BREAK_EVEN_MARGIN_OFF, DEFAULT_BREAK_EVEN_MARGIN_OFF),
        heat_pump_min_outdoor_temp=f(CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP, DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP),
        cop_warmup_s=f(CONF_COP_WARMUP_S, DEFAULT_COP_WARMUP_S),
        cop_min_power_w=f(CONF_COP_MIN_POWER_W, DEFAULT_COP_MIN_POWER_W),
        cop_min_delta_t_k=f(CONF_COP_MIN_DELTA_T, DEFAULT_COP_MIN_DELTA_T),
        cop_outlet_max_age_s=f(CONF_COP_OUTLET_MAX_AGE_MIN, DEFAULT_COP_OUTLET_MAX_AGE_MIN) * 60.0,
        cop_intake_max_age_s=f(CONF_COP_INTAKE_MAX_AGE_MIN, DEFAULT_COP_INTAKE_MAX_AGE_MIN) * 60.0,
        cop_min_samples=int(f(CONF_COP_MIN_SAMPLES, DEFAULT_COP_MIN_SAMPLES)),
        airflow_curve=parse_airflow_curve(config.get(CONF_HEAT_PUMP_AIRFLOW_CURVE)),
        duct_factor=f(CONF_HEAT_PUMP_DUCT_FACTOR, DEFAULT_HEAT_PUMP_DUCT_FACTOR),
        heat_pump_cop_profile=str(config.get(CONF_HEAT_PUMP_COP_PROFILE) or DEFAULT_HEAT_PUMP_COP_PROFILE),
        cop_settle_max_k_per_min=f(CONF_COP_SETTLE_MAX_K_PER_MIN, DEFAULT_COP_SETTLE_MAX_K_PER_MIN),
        icing_block_min=f(CONF_ICING_BLOCK_MIN, DEFAULT_ICING_BLOCK_MIN),
        defrost_coil_rise_k=f(CONF_DEFROST_COIL_RISE_K, DEFAULT_DEFROST_COIL_RISE_K),
        defrost_max_cycles_90min=int(f(CONF_DEFROST_MAX_CYCLES_90MIN, DEFAULT_DEFROST_MAX_CYCLES_90MIN)),
        icing_coil_depression_k=f(CONF_ICING_COIL_DEPRESSION_K, DEFAULT_ICING_COIL_DEPRESSION_K),
        drying_target_temp=f(CONF_DRYING_TARGET_TEMP, DEFAULT_DRYING_TARGET_TEMP),
        drying_abs_humidity_rise_g_m3=f(CONF_DRYING_ABS_HUMIDITY_RISE, DEFAULT_DRYING_ABS_HUMIDITY_RISE),
        drying_max_s=f(CONF_DRYING_MAX_MIN, DEFAULT_DRYING_MAX_MIN) * 60.0,
        away_dewpoint_margin_k=f(CONF_AWAY_DEWPOINT_MARGIN, DEFAULT_AWAY_DEWPOINT_MARGIN),
        preheat_margin_s=f(CONF_PREHEAT_MARGIN_MIN, DEFAULT_PREHEAT_MARGIN_MIN) * 60.0,
        default_heat_rate_radiator_k_h=f(CONF_DEFAULT_HEAT_RATE_RADIATOR, DEFAULT_DEFAULT_HEAT_RATE_RADIATOR),
        default_heat_rate_heat_pump_k_h=f(CONF_DEFAULT_HEAT_RATE_HEAT_PUMP, DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP),
        heat_pump_min_run_s=f(CONF_HEAT_PUMP_MIN_RUN_MIN, DEFAULT_HEAT_PUMP_MIN_RUN_MIN) * 60.0,
        heat_pump_min_off_s=f(CONF_HEAT_PUMP_MIN_OFF_MIN, DEFAULT_HEAT_PUMP_MIN_OFF_MIN) * 60.0,
        manual_hold_s=f(CONF_OVERRIDE_DEFAULT_MIN, DEFAULT_OVERRIDE_DEFAULT_MIN) * 60.0,
        heat_pump_fan_run_on_s=f(CONF_HEAT_PUMP_FAN_RUN_ON_MIN, DEFAULT_HEAT_PUMP_FAN_RUN_ON_MIN) * 60.0,
        heat_pump_learning_runs=bool(config.get(CONF_HEAT_PUMP_LEARNING_RUNS, DEFAULT_HEAT_PUMP_LEARNING_RUNS)),
        heat_pump_fan_stage=str(config.get(CONF_HEAT_PUMP_FAN_STAGE) or DEFAULT_HEAT_PUMP_FAN_STAGE),
        boiler_curve_flow_cold=f(CONF_BOILER_CURVE_COLD, DEFAULT_BOILER_CURVE_COLD),
        boiler_curve_flow_warm=f(CONF_BOILER_CURVE_WARM, DEFAULT_BOILER_CURVE_WARM),
        boiler_flow_min=f(CONF_BOILER_FLOW_MIN, DEFAULT_BOILER_FLOW_MIN),
        boiler_flow_max=f(CONF_BOILER_FLOW_MAX, DEFAULT_BOILER_FLOW_MAX),
        boiler_hot_water_standby=bool(config.get(CONF_BOILER_HOT_WATER_STANDBY, DEFAULT_BOILER_HOT_WATER_STANDBY)),
        boiler_condensing=bool(config.get(CONF_BOILER_CONDENSING, DEFAULT_BOILER_CONDENSING)),
        boiler_heat_up_rate_k_h=max(f(CONF_BOILER_HEAT_UP_RATE, DEFAULT_BOILER_HEAT_UP_RATE), 0.0),
        boiler_spread_min_room_share=f(CONF_BOILER_SPREAD_MIN_ROOM_SHARE, DEFAULT_BOILER_SPREAD_MIN_ROOM_SHARE),
    )
