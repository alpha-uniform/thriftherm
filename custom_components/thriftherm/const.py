"""Constants for Thriftherm integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "thriftherm"
PLATFORMS: Final = ["sensor", "binary_sensor", "select", "button", "number", "datetime"]
STORAGE_KEY: Final = "thriftherm.learning"
STORAGE_VERSION: Final = 1

UPDATE_INTERVAL_S: Final = 60

# ---------------------------------------------------------------------------
# Operating modes
# ---------------------------------------------------------------------------
MODE_AUTO: Final = "auto"
MODE_BOILER_ONLY: Final = "boiler_only"
MODE_HEAT_PUMP_ONLY: Final = "midea_only"
MODE_OFF: Final = "off"
MODE_AWAY: Final = "away"
MODES: Final = [MODE_AUTO, MODE_BOILER_ONLY, MODE_HEAT_PUMP_ONLY, MODE_OFF, MODE_AWAY]

SOURCE_BOILER: Final = "boiler"
SOURCE_HEAT_PUMP: Final = "midea"
SOURCE_BOTH: Final = "both"
SOURCE_NONE: Final = "none"
SOURCE_UNKNOWN: Final = "unknown"

SAFETY_OK: Final = "ok"
SAFETY_DEGRADED: Final = "degraded"
SAFETY_FALLBACK: Final = "fallback"

AUTOMATION_OBSERVING: Final = "observing"
AUTOMATION_PLANNING: Final = "planning"
AUTOMATION_ACTIVE: Final = "active"

# Control modes shared by heat pump, boiler and room control. "shadow" computes and logs commands without sending them.
CTRL_OFF: Final = "off"
CTRL_SHADOW: Final = "shadow"
CTRL_ACTIVE: Final = "active"

# Planned Midea state published by the command sensor
HEAT_PUMP_PLAN_HEAT: Final = "heat"
HEAT_PUMP_PLAN_OFF: Final = "off"
HEAT_PUMP_PLAN_HANDS_OFF: Final = "hands_off"
HEAT_PUMP_PLAN_DISABLED: Final = "disabled"
HEAT_PUMP_PLANS: Final = [HEAT_PUMP_PLAN_HEAT, HEAT_PUMP_PLAN_OFF, HEAT_PUMP_PLAN_HANDS_OFF, HEAT_PUMP_PLAN_DISABLED]

# Boiler control (Phase 8): same three control modes as the Midea
BOILER_PLAN_HEAT: Final = "heat"
BOILER_PLAN_BLOCK: Final = "block"
BOILER_PLAN_HANDS_OFF: Final = "hands_off"
BOILER_PLAN_DISABLED: Final = "disabled"
BOILER_PLANS: Final = [BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK, BOILER_PLAN_HANDS_OFF, BOILER_PLAN_DISABLED]

# Room control via Better Thermostat
ROOM_PLAN_SET: Final = "set"
ROOM_PLAN_HOLD: Final = "hold"
ROOM_PLAN_HANDS_OFF: Final = "hands_off"
ROOM_PLAN_DISABLED: Final = "disabled"
ROOM_PLANS: Final = [ROOM_PLAN_SET, ROOM_PLAN_HOLD, ROOM_PLAN_HANDS_OFF, ROOM_PLAN_DISABLED]

# ---------------------------------------------------------------------------
# Config keys – general / prices
# ---------------------------------------------------------------------------
CONF_SYSTEM_TYPE: Final = "system_type"
SYSTEM_GAS: Final = "gas"  # own gas or oil boiler, consumption billed by meter
SYSTEM_DISTRICT: Final = "district"  # central heating / district heat, billed per kWh with an allocation key
SYSTEM_NONE: Final = "none"  # no central heat source: heat pump and room control only
SYSTEM_TYPES: Final = (SYSTEM_GAS, SYSTEM_DISTRICT, SYSTEM_NONE)
DEFAULT_SYSTEM_TYPE: Final = SYSTEM_GAS

CONFIG_VERSION: Final = 2

CONF_ELECTRICITY_PRICE: Final = "electricity_price"
CONF_GAS_PRICE: Final = "gas_price"
CONF_BOILER_EFFICIENCY: Final = "boiler_efficiency"
CONF_GAS_CALORIFIC_VALUE: Final = "gas_calorific_value_kwh_m3"
CONF_GAS_Z_FACTOR: Final = "gas_z_factor"

DEFAULT_ELECTRICITY_PRICE: Final = 0.306
DEFAULT_GAS_PRICE: Final = 0.0889
DEFAULT_BOILER_EFFICIENCY: Final = 0.84
DEFAULT_GAS_CALORIFIC_VALUE: Final = 11.45
DEFAULT_GAS_Z_FACTOR: Final = 0.9381

# District heating: the German Heizkostenverordnung splits the building's heat
# bill into a consumption share and a floor-area share. Only the consumption
# share reacts to what this flat actually uses; of the area share this flat
# carries its ownership fraction (Miteigentumsanteil). Saving one kWh therefore
# saves less than the headline price, which raises the break-even COP.
CONF_HEAT_PRICE: Final = "heat_price"
CONF_HEAT_CONSUMPTION_SHARE: Final = "heat_consumption_share_pct"
CONF_HEAT_OWNERSHIP_SHARE: Final = "heat_ownership_share_pct"

DEFAULT_HEAT_PRICE: Final = 0.12
DEFAULT_HEAT_CONSUMPTION_SHARE: Final = 70.0
DEFAULT_HEAT_OWNERSHIP_SHARE: Final = 0.0

# ---------------------------------------------------------------------------
# Config keys – boiler (ebusd) and gas meter
# ---------------------------------------------------------------------------
CONF_BOILER_FLOW_TEMP: Final = "boiler_flow_temp"
CONF_BOILER_RETURN_TEMP: Final = "boiler_return_temp"
CONF_BOILER_PUMP_STATE: Final = "boiler_pump_state"
# ebusd "Status01" calls its last field the pump, but it follows the heating demand: measured
# 2026-09-18 it read "off" while the pump ran audibly. The real pump is ebusd "WP" (d.10).
CONF_BOILER_PUMP_RUNNING: Final = "boiler_pump_running"
CONF_BOILER_STATE_NUMBER: Final = "boiler_state_number"  # ebusd "Statenumber": the S.xx on the display
CONF_BOILER_HWC_MODE: Final = "boiler_hwc_mode"
CONF_BOILER_SIGNAL: Final = "boiler_signal"
CONF_BOILER_CIRCULATION_L_H: Final = "boiler_circulation_l_h"
CONF_GAS_VOLUME: Final = "gas_volume"
CONF_GAS_FLOW: Final = "gas_flow"

DEFAULT_BOILER_CIRCULATION_L_H: Final = 860.0

# Boiler control via ebusd SetMode
CONF_BOILER_EBUS_CIRCUIT: Final = "boiler_ebus_circuit"
CONF_BOILER_PROFILE: Final = "boiler_profile"  # key into boiler_profiles.PROFILES; absent means Vaillant bai
CONF_BOILER_CURVE_COLD: Final = "boiler_curve_flow_at_minus10"
CONF_BOILER_CURVE_WARM: Final = "boiler_curve_flow_at_plus15"
CONF_BOILER_FLOW_MIN: Final = "boiler_flow_min"
CONF_BOILER_FLOW_MAX: Final = "boiler_flow_max"
CONF_BOILER_ALLOW_ACTIVE: Final = "boiler_allow_active_control"

DEFAULT_BOILER_EBUS_CIRCUIT: Final = "bai"
DEFAULT_BOILER_CURVE_COLD: Final = 55.0
DEFAULT_BOILER_CURVE_WARM: Final = 30.0
DEFAULT_BOILER_FLOW_MIN: Final = 30.0  # minimum of the atmoTEC (d.05)
DEFAULT_BOILER_FLOW_MAX: Final = 60.0  # below d.71 (75 °C)
DEFAULT_BOILER_ALLOW_ACTIVE: Final = False

# ---------------------------------------------------------------------------
# Config keys – Midea
# ---------------------------------------------------------------------------
CONF_HEAT_PUMP_CLIMATE: Final = "midea_climate"
CONF_HEAT_PUMP_POWER: Final = "midea_power"
CONF_HEAT_PUMP_INTAKE_TEMP: Final = "midea_intake_temp"
CONF_HEAT_PUMP_INTAKE_RH: Final = "midea_intake_rh"
CONF_HEAT_PUMP_OUTLET_TEMP: Final = "midea_outlet_temp"
CONF_HEAT_PUMP_AIRFLOW_CURVE: Final = "midea_airflow_curve"

# ---------------------------------------------------------------------------
# Config keys – outdoor
# ---------------------------------------------------------------------------
CONF_OUTDOOR_TEMP_SENSORS: Final = "outdoor_temp_sensors"
CONF_OUTDOOR_RH_SENSOR: Final = "outdoor_rh_sensor"
CONF_WEATHER_ENTITY: Final = "weather_entity"

# ---------------------------------------------------------------------------
# Config keys – rooms
# ---------------------------------------------------------------------------
CONF_ROOMS: Final = "rooms"
CONF_ROOM_KEY: Final = "key"
CONF_ROOM_NAME: Final = "name"
CONF_ROOM_PRIORITY: Final = "priority"
CONF_ROOM_TEMP: Final = "temperature_sensor"
CONF_ROOM_HUMIDITY: Final = "humidity_sensor"
CONF_ROOM_WINDOWS: Final = "window_sensors"
CONF_ROOM_CLIMATE: Final = "climate_entity"
CONF_ROOM_HEAT_PUMP: Final = "served_by_midea"
CONF_ROOM_GAIN_SENSORS: Final = "internal_gain_sensors"
CONF_ROOM_COMFORT_TEMP: Final = "comfort_temp"
CONF_ROOM_SETBACK_TEMP: Final = "setback_temp"
CONF_ROOM_SCHEDULE_WEEKDAY: Final = "schedule_weekday"
CONF_ROOM_SCHEDULE_WEEKEND: Final = "schedule_weekend"
CONF_ROOM_DRYING: Final = "bathroom_drying_mode"
CONF_ROOM_SCHEDULE_ENTITY: Final = "schedule_entity"

DEFAULT_ROOM_COMFORT_TEMP: Final = 20.0
DEFAULT_ROOM_SETBACK_TEMP: Final = 17.0
DEFAULT_SCHEDULE_WEEKDAY: Final = "06:00-22:00"
DEFAULT_SCHEDULE_WEEKEND: Final = "08:00-23:00"

# ---------------------------------------------------------------------------
# Config keys – control parameters (all editable in options flow)
# ---------------------------------------------------------------------------
CONF_SENSOR_MAX_AGE_MIN: Final = "sensor_max_age_min"
CONF_WINDOW_GRACE_S: Final = "window_grace_s"
CONF_FROST_TEMP: Final = "frost_protection_temp"
CONF_AWAY_TEMP: Final = "away_temp"
CONF_DEMAND_FULL_DELTA: Final = "demand_full_delta_k"
CONF_TREND_WEIGHT_H: Final = "trend_weight_h"
CONF_GAIN_THRESHOLD_W: Final = "internal_gain_threshold_w"
CONF_GAIN_FACTOR: Final = "internal_gain_factor_per_w"
CONF_BREAK_EVEN_MARGIN_ON: Final = "break_even_margin_on"
CONF_BREAK_EVEN_MARGIN_OFF: Final = "break_even_margin_off"
CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP: Final = "midea_min_outdoor_temp"
CONF_HEAT_PUMP_SETPOINT_OFFSET: Final = "midea_setpoint_offset_k"
CONF_COP_WARMUP_S: Final = "cop_warmup_s"
CONF_COP_MIN_POWER_W: Final = "cop_min_power_w"
CONF_COP_MIN_DELTA_T: Final = "cop_min_delta_t_k"
CONF_COP_MAX_SENSOR_AGE_S: Final = "cop_max_sensor_age_s"
CONF_COP_MIN_SAMPLES: Final = "cop_min_samples"
CONF_COP_EMA_TAU_S: Final = "cop_ema_tau_s"
CONF_COP_MIN_RAW: Final = "cop_min_raw"
CONF_COP_MAX_RAW: Final = "cop_max_raw"
CONF_HEAT_PUMP_DUCT_FACTOR: Final = "midea_duct_factor"
CONF_HEAT_PUMP_ROOM: Final = "midea_room"  # room the unit itself stands in
CONF_COP_SETTLE_MAX_K_PER_MIN: Final = "cop_settle_max_k_per_min"
CONF_ICING_BLOCK_MIN: Final = "icing_block_min"
CONF_DEFROST_COIL_RISE_K: Final = "defrost_coil_rise_k"
CONF_DEFROST_MAX_CYCLES_90MIN: Final = "defrost_max_cycles_90min"
CONF_ICING_COIL_DEPRESSION_K: Final = "icing_coil_depression_k"
CONF_DRYING_TARGET_TEMP: Final = "drying_target_temp"
CONF_DRYING_ABS_HUMIDITY_RISE: Final = "drying_abs_humidity_rise_g_m3"
CONF_DRYING_MAX_MIN: Final = "drying_max_min"
CONF_AWAY_DEWPOINT_MARGIN: Final = "away_dewpoint_margin_k"
CONF_PREHEAT_MARGIN_MIN: Final = "preheat_margin_min"
CONF_DEFAULT_HEAT_RATE_RADIATOR: Final = "default_heat_rate_radiator_k_h"
CONF_DEFAULT_HEAT_RATE_HEAT_PUMP: Final = "default_heat_rate_midea_k_h"
CONF_OVERRIDE_DEFAULT_MIN: Final = "override_default_min"
CONF_HEAT_PUMP_MIN_RUN_MIN: Final = "midea_min_run_min"
CONF_HEAT_PUMP_MIN_OFF_MIN: Final = "midea_min_off_min"
CONF_BOOST_DURATION_MIN: Final = "boost_duration_min"
CONF_HEAT_PUMP_LEARNING_RUNS: Final = "midea_learning_runs"
CONF_HEAT_PUMP_ALLOW_ACTIVE: Final = "midea_allow_active_control"
CONF_ROOM_ALLOW_ACTIVE: Final = "room_allow_active_control"

# Zigbee room sensors report only on change; in a room at steady temperature
# three silent hours are normal (measured 2026-09-13, living room). A shorter
# limit discards a working sensor and falls back to the thermostat's own
# reading next to the radiator. A dead device shows up as unavailable anyway.
DEFAULT_SENSOR_MAX_AGE_MIN: Final = 720
DEFAULT_WINDOW_GRACE_S: Final = 90
DEFAULT_FROST_TEMP: Final = 7.0
DEFAULT_AWAY_TEMP: Final = 15.0
DEFAULT_DEMAND_FULL_DELTA: Final = 2.0
DEFAULT_TREND_WEIGHT_H: Final = 0.5
DEFAULT_GAIN_THRESHOLD_W: Final = 150.0
DEFAULT_GAIN_FACTOR: Final = 0.001
DEFAULT_BREAK_EVEN_MARGIN_ON: Final = 0.10
DEFAULT_BREAK_EVEN_MARGIN_OFF: Final = 0.05
DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP: Final = -10.0  # manufacturer heating limit (midea.com)
DEFAULT_HEAT_PUMP_SETPOINT_OFFSET: Final = 1.0
DEFAULT_COP_WARMUP_S: Final = 600
DEFAULT_COP_MIN_POWER_W: Final = 150.0
DEFAULT_COP_MIN_DELTA_T: Final = 3.0
DEFAULT_COP_MAX_SENSOR_AGE_S: Final = 300
DEFAULT_COP_MIN_SAMPLES: Final = 12
DEFAULT_COP_EMA_TAU_S: Final = 300
DEFAULT_COP_MIN_RAW: Final = 0.5
DEFAULT_COP_MAX_RAW: Final = 7.0
DEFAULT_HEAT_PUMP_DUCT_FACTOR: Final = 1.0
DEFAULT_COP_SETTLE_MAX_K_PER_MIN: Final = 0.5
DEFAULT_ICING_BLOCK_MIN: Final = 120
DEFAULT_DEFROST_COIL_RISE_K: Final = 5.0
DEFAULT_DEFROST_MAX_CYCLES_90MIN: Final = 3
DEFAULT_ICING_COIL_DEPRESSION_K: Final = 8.0
DEFAULT_DRYING_TARGET_TEMP: Final = 22.0
DEFAULT_DRYING_ABS_HUMIDITY_RISE: Final = 2.5
DEFAULT_DRYING_MAX_MIN: Final = 60
DEFAULT_AWAY_DEWPOINT_MARGIN: Final = 3.0
DEFAULT_PREHEAT_MARGIN_MIN: Final = 20
DEFAULT_DEFAULT_HEAT_RATE_RADIATOR: Final = 0.8
DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP: Final = 1.2
DEFAULT_OVERRIDE_DEFAULT_MIN: Final = 120
DEFAULT_HEAT_PUMP_MIN_RUN_MIN: Final = 20
DEFAULT_HEAT_PUMP_MIN_OFF_MIN: Final = 10
DEFAULT_BOOST_DURATION_MIN: Final = 45
DEFAULT_HEAT_PUMP_LEARNING_RUNS: Final = True
DEFAULT_HEAT_PUMP_ALLOW_ACTIVE: Final = False
DEFAULT_ROOM_ALLOW_ACTIVE: Final = False

SERVICE_SET_OVERRIDE: Final = "set_override"
SERVICE_CLEAR_OVERRIDE: Final = "clear_override"
SERVICE_SET_AWAY: Final = "set_away"
SERVICE_CLEAR_AWAY: Final = "clear_away"
SERVICE_RESET_LEARNING: Final = "reset_learning"
SERVICE_BOOST: Final = "boost"

# Physical plausibility ranges for incoming readings
RANGE_ROOM_TEMP: Final = (-10.0, 45.0)
RANGE_HUMIDITY: Final = (0.0, 100.0)
RANGE_OUTDOOR_TEMP: Final = (-40.0, 50.0)
RANGE_FLOW_TEMP: Final = (0.0, 100.0)
RANGE_POWER_W: Final = (-50.0, 30000.0)
RANGE_GAS_FLOW_M3H: Final = (0.0, 10.0)

# Boiler pump states as published by ebusd (Vaillant bai Status01 pumpstate)
PUMP_STATE_HWC: Final = "hwc"
PUMP_STATE_ON: Final = "on"
PUMP_STATE_OVERRUN: Final = "overrun"

# Midea derived run states
HEAT_PUMP_UNAVAILABLE: Final = "unavailable"
HEAT_PUMP_OFF: Final = "off"
HEAT_PUMP_COOLING_USER: Final = "cooling_user"
HEAT_PUMP_FAN_ONLY: Final = "fan_only"
HEAT_PUMP_AUTO: Final = "auto"
HEAT_PUMP_HEATING_IDLE: Final = "heating_idle"
HEAT_PUMP_HEATING_WARMING_UP: Final = "heating_warming_up"
HEAT_PUMP_HEATING_STABLE: Final = "heating_stable"
HEAT_PUMP_RUN_STATES: Final = [
    HEAT_PUMP_UNAVAILABLE,
    HEAT_PUMP_OFF,
    HEAT_PUMP_COOLING_USER,
    HEAT_PUMP_FAN_ONLY,
    HEAT_PUMP_AUTO,
    HEAT_PUMP_HEATING_IDLE,
    HEAT_PUMP_HEATING_WARMING_UP,
    HEAT_PUMP_HEATING_STABLE,
]
