"""Form schemas, field groups, validation and merging for the config and options flow."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.util import slugify

from . import const
from .adapters.config import system_type_from_config
from .const import (
    CHEAP_POWER_DOMAINS,
    CONF_AWAY_DEWPOINT_MARGIN,
    CONF_AWAY_RETURN_PERSONS,
    CONF_AWAY_TEMP,
    CONF_BOILER_ALLOW_ACTIVE,
    CONF_BOILER_CIRCULATION_L_H,
    CONF_BOILER_CURVE_COLD,
    CONF_BOILER_CURVE_WARM,
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_EFFICIENCY,
    CONF_BOILER_FILL_DECLINED,
    CONF_BOILER_FLAME,
    CONF_BOILER_FLOW_MAX,
    CONF_BOILER_FLOW_MIN,
    CONF_BOILER_FLOW_SETPOINT_ENTITY,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HEAT_UP_RATE,
    CONF_BOILER_HEATING_ENABLE_ENTITY,
    CONF_BOILER_HOT_WATER_STANDBY,
    CONF_BOILER_CONDENSING,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_NOMINAL_INPUT_KW,
    CONF_BOILER_PROFILE,
    CONF_BOILER_PUMP_POWER_W,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_BOILER_SPREAD_MIN_ROOM_SHARE,
    CONF_BOILER_STATE_NUMBER,
    CONF_BOOST_DURATION_MIN,
    CONF_BREAK_EVEN_MARGIN_OFF,
    CONF_BREAK_EVEN_MARGIN_ON,
    CONF_COOL_AWAY_TEMP,
    CONF_COOL_FLOOR_TEMP,
    CONF_COOLING_CHEAP_POWER_ENTITY,
    CONF_COOLING_CHEAP_POWER_K,
    CONF_COOLING_SEASON_FROM_C,
    CONF_COOLING_SEASON_VETO_C,
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
    CONF_GAS_FLOW,
    CONF_GAS_PRICE,
    CONF_GAS_VOLUME,
    CONF_GAS_Z_FACTOR,
    CONF_HEAT_CONSUMPTION_SHARE,
    CONF_HEAT_OWNERSHIP_SHARE,
    CONF_HEAT_PRICE,
    CONF_HEAT_PUMP_AIRFLOW_CURVE,
    CONF_HEAT_PUMP_ALLOW_ACTIVE,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_COOL_FAN_STAGE,
    CONF_HEAT_PUMP_COOL_SHORTFALL,
    CONF_HEAT_PUMP_COOLING_ALLOWED,
    CONF_HEAT_PUMP_COP_PROFILE,
    CONF_HEAT_PUMP_DUCT_FACTOR,
    CONF_HEAT_PUMP_FAN_RUN_ON_MIN,
    CONF_HEAT_PUMP_FAN_STAGE,
    CONF_HEAT_PUMP_INTAKE_RH,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_LEARNING_RUNS,
    CONF_HEAT_PUMP_MIN_OFF_MIN,
    CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    CONF_HEAT_PUMP_MIN_RUN_MIN,
    CONF_HEAT_PUMP_OUTLET_TEMP,
    CONF_HEAT_PUMP_POWER,
    CONF_HEAT_PUMP_ROOM,
    CONF_HEAT_PUMP_SHORTFALL,
    CONF_HEATING_SEASON_FROM_C,
    CONF_ICING_BLOCK_MIN,
    CONF_ICING_COIL_DEPRESSION_K,
    CONF_OUTDOOR_RH_SENSOR,
    CONF_OUTDOOR_TEMP_SENSORS,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_PREHEAT_MARGIN_MIN,
    CONF_ROOM_ALLOW_ACTIVE,
    CONF_ROOM_CLIMATE,
    CONF_ROOM_COMFORT_TEMP,
    CONF_ROOM_COOLING_SCHEDULE_ENTITY,
    CONF_ROOM_DRYING,
    CONF_ROOM_DRYING_AUTO,
    CONF_ROOM_ECHO_FILTER,
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
    CONF_ROOMS,
    CONF_SENSOR_MAX_AGE_MIN,
    CONF_SYSTEM_TYPE,
    CONF_TREND_WEIGHT_H,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_GRACE_S,
    CONF_TOLERANCE_ABOVE_K,
    CONF_TOLERANCE_BELOW_K,
    CONF_WINDOW_VALVE_ACTION,
    COOL_FAN_STAGE_CHOICES,
    COOL_SHORTFALL_CHOICES,
    COOL_SHORTFALL_LIFT,
    DEFAULT_AWAY_DEWPOINT_MARGIN,
    DEFAULT_AWAY_TEMP,
    DEFAULT_BOILER_ALLOW_ACTIVE,
    DEFAULT_BOILER_CIRCULATION_L_H,
    DEFAULT_BOILER_CURVE_COLD,
    DEFAULT_BOILER_CURVE_WARM,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DEFAULT_BOILER_EFFICIENCY,
    DEFAULT_BOILER_FLOW_MAX,
    DEFAULT_BOILER_FLOW_MIN,
    DEFAULT_BOILER_HEAT_UP_RATE,
    DEFAULT_BOILER_HOT_WATER_STANDBY,
    DEFAULT_BOILER_CONDENSING,
    DEFAULT_BOILER_SPREAD_MIN_ROOM_SHARE,
    DEFAULT_BOOST_DURATION_MIN,
    DEFAULT_BREAK_EVEN_MARGIN_OFF,
    DEFAULT_BREAK_EVEN_MARGIN_ON,
    DEFAULT_COOL_AWAY_TEMP,
    DEFAULT_COOL_FLOOR_TEMP,
    DEFAULT_COOLING_CHEAP_POWER_K,
    DEFAULT_COOLING_SEASON_FROM_C,
    DEFAULT_COOLING_SEASON_VETO_C,
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
    DEFAULT_HEAT_PUMP_ALLOW_ACTIVE,
    DEFAULT_HEAT_PUMP_COP_PROFILE,
    DEFAULT_HEAT_PUMP_DUCT_FACTOR,
    DEFAULT_HEAT_PUMP_FAN_RUN_ON_MIN,
    DEFAULT_HEAT_PUMP_FAN_STAGE,
    DEFAULT_HEAT_PUMP_LEARNING_RUNS,
    DEFAULT_HEAT_PUMP_MIN_OFF_MIN,
    DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    DEFAULT_HEAT_PUMP_MIN_RUN_MIN,
    DEFAULT_HEAT_PUMP_SHORTFALL,
    DEFAULT_HEATING_SEASON_FROM_C,
    DEFAULT_ICING_BLOCK_MIN,
    DEFAULT_ICING_COIL_DEPRESSION_K,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_PREHEAT_MARGIN_MIN,
    DEFAULT_ROOM_ALLOW_ACTIVE,
    DEFAULT_ROOM_COMFORT_TEMP,
    DEFAULT_ROOM_ECHO_FILTER,
    DEFAULT_ROOM_LEAD_K,
    DEFAULT_ROOM_SENSORS_ON_CHANGE,
    DEFAULT_ROOM_SETBACK_TEMP,
    DEFAULT_SCHEDULE_WEEKDAY,
    DEFAULT_SCHEDULE_WEEKEND,
    DEFAULT_SENSOR_MAX_AGE_MIN,
    DEFAULT_SYSTEM_TYPE,
    DEFAULT_TREND_WEIGHT_H,
    DEFAULT_WINDOW_GRACE_S,
    FAN_STAGE_CHOICES,
    ROOM_LEAD_MAX_K,
    ROOM_TEMP_RANGES,
    SHORTFALL_CHOICES,
    SYSTEM_DISTRICT,
    SYSTEM_GAS,
    SYSTEM_TYPES,
    WINDOW_VALVE_FROST,
    WINDOW_VALVE_OFF,
    VALVE_CONTROL_AUTO,
    VALVE_CONTROL_OFFSET,
    VALVE_CONTROL_SETPOINT,
)
from .engines import cop_profiles
from .engines.cop import parse_airflow_curve
from .engines.room import parse_schedule

ROOM_ACTION_ADD = "add-room"  # slugified room keys never contain a hyphen


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------
def _entity(domain: str | list[str], multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain, multiple=multiple))


def _number(min_: float, max_: float, step: float | str, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=min_, max=max_, step=step, mode=selector.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _text() -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT))


def _opt(key: str, current: Mapping[str, Any], default: Any = None) -> vol.Optional:
    value = current.get(key, default)
    if value is None:
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _req(key: str, current: Mapping[str, Any], default: Any = None) -> vol.Required:
    value = current.get(key, default)
    if value is None:
        return vol.Required(key)
    return vol.Required(key, default=value)


def _section(fields: dict[Any, Any], collapsed: bool = False) -> section:
    """A group of fields under a heading of its own; one that folds shows only its heading at first."""
    return section(vol.Schema(fields), {"collapsed": collapsed})


def _markers(schema: vol.Schema) -> Iterable[Any]:
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            yield from _markers(value.schema)
        else:
            yield marker


def keys_of(schema_fn: Callable[[Mapping[str, Any]], vol.Schema]) -> tuple[str, ...]:
    """The keys a step stores, read off its form, the fields inside its sections included.

    Only for steps whose fields never depend on the current configuration.
    """
    return tuple(marker.schema for marker in _markers(schema_fn({})))


def flatten_sections(schema: vol.Schema, user_input: Mapping[str, Any]) -> dict[str, Any]:
    """The answer of a form with sections as the flat keys that are stored: a section is only a heading."""
    sections = {marker.schema for marker, value in schema.schema.items() if isinstance(value, section)}
    flat = {key: value for key, value in user_input.items() if key not in sections}
    for name in sections:
        flat.update(user_input.get(name) or {})
    return flat


# ---------------------------------------------------------------------------
# Step schemas (shared by config and options flow)
# ---------------------------------------------------------------------------
def schema_system(cur: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _req(CONF_SYSTEM_TYPE, cur, DEFAULT_SYSTEM_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SYSTEM_TYPES),
                    translation_key="system_type",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )


SYSTEM_KEYS = keys_of(schema_system)


def schema_prices(cur: Mapping[str, Any]) -> vol.Schema:
    """Price fields for the configured system type.

    Gas and oil are billed by meter volume, so they need calorific value and
    efficiency. District heat is billed per kWh with an allocation key, so it
    needs that key instead. Without a central heat source only electricity
    matters.
    """
    fields: dict[Any, Any] = {
        _req(CONF_ELECTRICITY_PRICE, cur, DEFAULT_ELECTRICITY_PRICE): _number(0, 2, "any", "€/kWh"),
    }
    system = system_type_from_config(cur)
    if system == SYSTEM_GAS:
        # a price of 0 leaves nothing to compare against
        fields[_req(CONF_GAS_PRICE, cur, DEFAULT_GAS_PRICE)] = _number(0.001, 1, "any", "€/kWh")
        fields[_req(CONF_BOILER_EFFICIENCY, cur, DEFAULT_BOILER_EFFICIENCY)] = _number(0.5, 1.1, 0.01)
        fields[_req(CONF_GAS_CALORIFIC_VALUE, cur, DEFAULT_GAS_CALORIFIC_VALUE)] = _number(8, 14, 0.001, "kWh/m³")
        fields[_req(CONF_GAS_Z_FACTOR, cur, DEFAULT_GAS_Z_FACTOR)] = _number(0.8, 1.1, "any")
    elif system == SYSTEM_DISTRICT:
        fields[_req(CONF_HEAT_PRICE, cur, DEFAULT_HEAT_PRICE)] = _number(0.001, 1, "any", "€/kWh")
        # at least 1 %: with no consumption share and no ownership share a saved kWh would save nothing
        fields[_req(CONF_HEAT_CONSUMPTION_SHARE, cur, DEFAULT_HEAT_CONSUMPTION_SHARE)] = _number(1, 100, 1, "%")
        fields[_req(CONF_HEAT_OWNERSHIP_SHARE, cur, DEFAULT_HEAT_OWNERSHIP_SHARE)] = _number(0, 100, 0.01, "%")
    return vol.Schema(fields)


# every price field of every system type: the form shows only some of them
PRICE_KEYS = (
    CONF_ELECTRICITY_PRICE,
    CONF_GAS_PRICE,
    CONF_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    CONF_GAS_Z_FACTOR,
    CONF_HEAT_PRICE,
    CONF_HEAT_CONSUMPTION_SHARE,
    CONF_HEAT_OWNERSHIP_SHARE,
)


def _hot_water_running(cur: Mapping[str, Any]) -> Mapping[str, Any]:
    """The form without a stored ebusd Status02 hwcmode: the field takes it no more, and shown it could not be sent."""
    if str(cur.get(CONF_BOILER_HWC_MODE) or "").startswith("binary_sensor."):
        return cur
    return {k: v for k, v in cur.items() if k != CONF_BOILER_HWC_MODE}


def schema_boiler(cur: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_BOILER_FLOW_TEMP, cur): _entity("sensor"),
            _opt(CONF_BOILER_RETURN_TEMP, cur): _entity("sensor"),
            _opt(CONF_BOILER_PUMP_STATE, cur): _entity("sensor"),
            _opt(CONF_BOILER_PUMP_RUNNING, cur): _entity(["sensor", "binary_sensor"]),
            _opt(CONF_BOILER_STATE_NUMBER, cur): _entity("sensor"),
            # a binary sensor only: ebusd Status02 hwcmode, offered here before 0.8.1, is a mode, not a run
            _opt(CONF_BOILER_HWC_MODE, _hot_water_running(cur)): _entity("binary_sensor"),
            _opt(CONF_BOILER_FLAME, cur): _entity(["binary_sensor", "sensor"]),
            _opt(CONF_BOILER_SIGNAL, cur): _entity("binary_sensor"),
            _req(CONF_BOILER_CIRCULATION_L_H, cur, DEFAULT_BOILER_CIRCULATION_L_H): _number(100, 5000, 1, "l/h"),
            _opt(CONF_GAS_VOLUME, cur): _entity("sensor"),
            _opt(CONF_GAS_FLOW, cur): _entity("sensor"),
            _opt(CONF_BOILER_NOMINAL_INPUT_KW, cur): _number(3, 100, 0.1, "kW"),
            _opt(CONF_BOILER_PUMP_POWER_W, cur): _number(1, 300, 1, "W"),
            _req(CONF_BOILER_EBUS_CIRCUIT, cur, DEFAULT_BOILER_EBUS_CIRCUIT): _text(),
            _opt(CONF_BOILER_FLOW_SETPOINT_ENTITY, cur): _entity(["number", "input_number"]),
            _opt(CONF_BOILER_HEATING_ENABLE_ENTITY, cur): _entity(["switch", "input_boolean"]),
            _req(CONF_BOILER_CURVE_COLD, cur, DEFAULT_BOILER_CURVE_COLD): _number(30, 75, 0.5, "°C"),
            _req(CONF_BOILER_CURVE_WARM, cur, DEFAULT_BOILER_CURVE_WARM): _number(30, 60, 0.5, "°C"),
            _req(CONF_BOILER_FLOW_MIN, cur, DEFAULT_BOILER_FLOW_MIN): _number(30, 50, 0.5, "°C"),
            _req(CONF_BOILER_FLOW_MAX, cur, DEFAULT_BOILER_FLOW_MAX): _number(35, 75, 0.5, "°C"),
            _req(CONF_BOILER_HEAT_UP_RATE, cur, DEFAULT_BOILER_HEAT_UP_RATE): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3, step=0.25, unit_of_measurement="°C/h", mode=selector.NumberSelectorMode.SLIDER
                )
            ),
            _req(CONF_BOILER_HOT_WATER_STANDBY, cur, DEFAULT_BOILER_HOT_WATER_STANDBY): selector.BooleanSelector(),
            _req(CONF_BOILER_CONDENSING, cur, DEFAULT_BOILER_CONDENSING): selector.BooleanSelector(),
            _req(CONF_BOILER_SPREAD_MIN_ROOM_SHARE, cur, DEFAULT_BOILER_SPREAD_MIN_ROOM_SHARE): _number(0, 1, 0.05),
            _req(CONF_BOILER_ALLOW_ACTIVE, cur, DEFAULT_BOILER_ALLOW_ACTIVE): selector.BooleanSelector(),
        }
    )


BOILER_KEYS = keys_of(schema_boiler)


def schema_heat_pump(cur: Mapping[str, Any]) -> vol.Schema:
    """Heat pump entities, airflow calibration and where the unit stands.

    The installation room matters: without ducts the warm air stays there, no
    matter which rooms the unit is meant to serve.
    """
    rooms = room_options(r for r in (cur.get(CONF_ROOMS) or []) if r.get(CONF_ROOM_KEY))
    return vol.Schema(
        {
            _opt(CONF_HEAT_PUMP_CLIMATE, cur): _entity("climate"),
            _opt(CONF_HEAT_PUMP_POWER, cur): _entity("sensor"),
            _opt(CONF_HEAT_PUMP_INTAKE_TEMP, cur): _entity("sensor"),
            _opt(CONF_HEAT_PUMP_INTAKE_RH, cur): _entity("sensor"),
            _opt(CONF_HEAT_PUMP_OUTLET_TEMP, cur): _entity("sensor"),
            _opt(CONF_HEAT_PUMP_AIRFLOW_CURVE, cur): _text(),
            _req(CONF_HEAT_PUMP_DUCT_FACTOR, cur, DEFAULT_HEAT_PUMP_DUCT_FACTOR): _number(0.3, 1.2, 0.01),
            _req(CONF_HEAT_PUMP_COP_PROFILE, cur, DEFAULT_HEAT_PUMP_COP_PROFILE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(cop_profiles.PROFILE_KEYS),
                    translation_key="cop_profile",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            **(
                {_opt(CONF_HEAT_PUMP_ROOM, cur): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=rooms, mode=selector.SelectSelectorMode.DROPDOWN)
                )}
                if rooms
                else {}
            ),
        }
    )


# spelled out: the room field only shows once rooms exist, but must still be cleared
HEAT_PUMP_KEYS = (
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_POWER,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_INTAKE_RH,
    CONF_HEAT_PUMP_OUTLET_TEMP,
    CONF_HEAT_PUMP_AIRFLOW_CURVE,
    CONF_HEAT_PUMP_DUCT_FACTOR,
    CONF_HEAT_PUMP_COP_PROFILE,
    CONF_HEAT_PUMP_ROOM,
)


def schema_outdoor(cur: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_OUTDOOR_TEMP_SENSORS, cur): _entity("sensor", multiple=True),
            _opt(CONF_OUTDOOR_RH_SENSOR, cur): _entity("sensor"),
            _opt(CONF_WEATHER_ENTITY, cur): _entity("weather"),
        }
    )


OUTDOOR_KEYS = keys_of(schema_outdoor)


SECTION_WINDOWS = "windows_preheat"
SECTION_RELEASE = "release"
SECTION_EXPERT = "expert"
SECTION_SWITCHING = "switching"
SECTION_DRYING = "drying"
SECTION_COOLING = "cooling"
SECTION_COP = "cop"
SECTION_ICING = "icing"


def schema_parameters(cur: Mapping[str, Any]) -> vol.Schema:
    """What applies to every installation: the values of everyday use first, the rest under headings.

    The heat pump has a form of its own (`schema_heat_pump_tuning`): it made up more than half of
    this one, and an installation without a heat pump saw all of it.
    """
    return vol.Schema(
        {
            _req(CONF_OVERRIDE_DEFAULT_MIN, cur, DEFAULT_OVERRIDE_DEFAULT_MIN): _number(5, 1440, 5, "min"),
            _req(CONF_BOOST_DURATION_MIN, cur, DEFAULT_BOOST_DURATION_MIN): _number(10, 180, 5, "min"),
            _req(CONF_AWAY_TEMP, cur, DEFAULT_AWAY_TEMP): _number(8, 20, 0.5, "°C"),
            _opt(CONF_AWAY_RETURN_PERSONS, cur): _entity("person", multiple=True),
            _req(CONF_FROST_TEMP, cur, DEFAULT_FROST_TEMP): _number(3, 12, 0.5, "°C"),
            vol.Required(SECTION_WINDOWS): _section(
                {
                    _req(CONF_WINDOW_GRACE_S, cur, DEFAULT_WINDOW_GRACE_S): _number(0, 900, 1, "s"),
                    _req(CONF_WINDOW_VALVE_ACTION, cur, WINDOW_VALVE_OFF): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[WINDOW_VALVE_OFF, WINDOW_VALVE_FROST], translation_key="window_valve_action",
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    _req(CONF_PREHEAT_MARGIN_MIN, cur, DEFAULT_PREHEAT_MARGIN_MIN): _number(0, 120, 5, "min"),
                    _req(CONF_DEFAULT_HEAT_RATE_RADIATOR, cur, DEFAULT_DEFAULT_HEAT_RATE_RADIATOR): _number(0.1, 5, 0.1, "°C/h"),
                }
            ),
            vol.Required(SECTION_RELEASE): _section(
                {
                    _req(CONF_ROOM_ALLOW_ACTIVE, cur, DEFAULT_ROOM_ALLOW_ACTIVE): selector.BooleanSelector(),
                    _req(CONF_ROOM_ECHO_FILTER, cur, DEFAULT_ROOM_ECHO_FILTER): selector.BooleanSelector(),
                }
            ),
            vol.Required(SECTION_EXPERT): _section(
                {
                    _req(CONF_TOLERANCE_BELOW_K, cur, 0.3): _number(0.2, 0.5, 0.1, "K"),
                    _req(CONF_TOLERANCE_ABOVE_K, cur, -0.1): _number(-0.1, 0.5, 0.1, "K"),
                    _req(CONF_DEMAND_FULL_DELTA, cur, DEFAULT_DEMAND_FULL_DELTA): _number(0.5, 5, 0.1, "°C"),
                    _req(CONF_TREND_WEIGHT_H, cur, DEFAULT_TREND_WEIGHT_H): _number(0, 2, 0.1, "h"),
                    _req(CONF_GAIN_THRESHOLD_W, cur, DEFAULT_GAIN_THRESHOLD_W): _number(0, 2000, 10, "W"),
                    _req(CONF_GAIN_FACTOR, cur, DEFAULT_GAIN_FACTOR): _number(0, 0.01, "any", "1/W"),
                    _req(CONF_SENSOR_MAX_AGE_MIN, cur, DEFAULT_SENSOR_MAX_AGE_MIN): _number(5, 720, 1, "min"),
                    _req(CONF_ROOM_SENSORS_ON_CHANGE, cur, DEFAULT_ROOM_SENSORS_ON_CHANGE): selector.BooleanSelector(),
                    _req(CONF_AWAY_DEWPOINT_MARGIN, cur, DEFAULT_AWAY_DEWPOINT_MARGIN): _number(1, 8, 0.5, "°C"),
                },
                collapsed=True,
            ),
        }
    )


def schema_heat_pump_tuning(cur: Mapping[str, Any], cooling: bool | None = None) -> vol.Schema:
    """How the heat pump is run: only offered once a heat pump is configured.

    The cooling section shows while cooling is no preview any more (`cooling` None) or when asked for.
    """
    shown = const.COOLING_PREVIEW if cooling is None else cooling
    return vol.Schema(
        {
            _req(CONF_HEAT_PUMP_ALLOW_ACTIVE, cur, DEFAULT_HEAT_PUMP_ALLOW_ACTIVE): selector.BooleanSelector(),
            _req(CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP, cur, DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP): _number(-20, 10, 0.5, "°C"),
            _req(CONF_DEFAULT_HEAT_RATE_HEAT_PUMP, cur, DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP): _number(0.1, 5, 0.1, "°C/h"),
            # without a central heat source the unit is always set higher, whatever is chosen here
            _req(CONF_HEAT_PUMP_SHORTFALL, cur, DEFAULT_HEAT_PUMP_SHORTFALL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SHORTFALL_CHOICES), translation_key="heat_pump_shortfall", mode=selector.SelectSelectorMode.LIST
                )
            ),
            _req(CONF_HEAT_PUMP_FAN_STAGE, cur, DEFAULT_HEAT_PUMP_FAN_STAGE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(FAN_STAGE_CHOICES),
                    translation_key="heat_pump_fan_stage",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(SECTION_SWITCHING): _section(
                {
                    _req(CONF_BREAK_EVEN_MARGIN_ON, cur, DEFAULT_BREAK_EVEN_MARGIN_ON): _number(0, 0.5, 0.01),
                    _req(CONF_BREAK_EVEN_MARGIN_OFF, cur, DEFAULT_BREAK_EVEN_MARGIN_OFF): _number(0, 0.5, 0.01),
                    _req(CONF_HEAT_PUMP_MIN_RUN_MIN, cur, DEFAULT_HEAT_PUMP_MIN_RUN_MIN): _number(5, 120, 1, "min"),
                    _req(CONF_HEAT_PUMP_MIN_OFF_MIN, cur, DEFAULT_HEAT_PUMP_MIN_OFF_MIN): _number(3, 120, 1, "min"),
                    _req(CONF_HEAT_PUMP_FAN_RUN_ON_MIN, cur, DEFAULT_HEAT_PUMP_FAN_RUN_ON_MIN): _number(0, 60, 1, "min"),
                    _req(CONF_HEAT_PUMP_LEARNING_RUNS, cur, DEFAULT_HEAT_PUMP_LEARNING_RUNS): selector.BooleanSelector(),
                }
            ),
            # cooling with the heat pump (docs/PHASE12_COOLING.md): off unless asked for, the heating stays as it is;
            # not offered at all while it is a preview (const.COOLING_PREVIEW)
            **({vol.Required(SECTION_COOLING): _section(
                {
                    _req(CONF_HEAT_PUMP_COOLING_ALLOWED, cur, False): selector.BooleanSelector(),
                    _req(CONF_COOL_AWAY_TEMP, cur, DEFAULT_COOL_AWAY_TEMP): _number(20, 32, 0.5, "°C"),
                    _req(CONF_COOL_FLOOR_TEMP, cur, DEFAULT_COOL_FLOOR_TEMP): _number(16, 26, 0.5, "°C"),
                    _req(CONF_HEAT_PUMP_COOL_SHORTFALL, cur, COOL_SHORTFALL_LIFT): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(COOL_SHORTFALL_CHOICES),
                            translation_key="heat_pump_cool_shortfall",
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    _req(CONF_HEAT_PUMP_COOL_FAN_STAGE, cur, "auto"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(COOL_FAN_STAGE_CHOICES),
                            translation_key="heat_pump_cool_fan_stage",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    # stage 2 (docs/PHASE12_COOLING.md §20): cheap power, and the automatic season's fallback and veto
                    _opt(CONF_COOLING_CHEAP_POWER_ENTITY, cur): _entity(list(CHEAP_POWER_DOMAINS)),
                    _req(CONF_COOLING_CHEAP_POWER_K, cur, DEFAULT_COOLING_CHEAP_POWER_K): _number(0.5, 3, 0.5, "°C"),
                    _req(CONF_COOLING_SEASON_FROM_C, cur, DEFAULT_COOLING_SEASON_FROM_C): _number(10, 30, 0.5, "°C"),
                    _req(CONF_HEATING_SEASON_FROM_C, cur, DEFAULT_HEATING_SEASON_FROM_C): _number(5, 25, 0.5, "°C"),
                    _req(CONF_COOLING_SEASON_VETO_C, cur, DEFAULT_COOLING_SEASON_VETO_C): _number(0, 25, 0.5, "°C"),
                },
                collapsed=True,
            )} if shown else {}),
            vol.Required(SECTION_DRYING): _section(
                {
                    _req(CONF_DRYING_TARGET_TEMP, cur, DEFAULT_DRYING_TARGET_TEMP): _number(18, 26, 0.5, "°C"),
                    _req(CONF_DRYING_ABS_HUMIDITY_RISE, cur, DEFAULT_DRYING_ABS_HUMIDITY_RISE): _number(0.5, 8, 0.1, "g/m³"),
                    _req(CONF_DRYING_MAX_MIN, cur, DEFAULT_DRYING_MAX_MIN): _number(10, 240, 5, "min"),
                },
                collapsed=True,
            ),
            vol.Required(SECTION_COP): _section(
                {
                    _req(CONF_COP_WARMUP_S, cur, DEFAULT_COP_WARMUP_S): _number(0, 1800, 10, "s"),
                    _req(CONF_COP_MIN_POWER_W, cur, DEFAULT_COP_MIN_POWER_W): _number(0, 2000, 10, "W"),
                    _req(CONF_COP_MIN_DELTA_T, cur, DEFAULT_COP_MIN_DELTA_T): _number(0, 15, 0.5, "°C"),
                    _req(CONF_COP_MIN_SAMPLES, cur, DEFAULT_COP_MIN_SAMPLES): _number(1, 120, 1),
                    _req(CONF_COP_SETTLE_MAX_K_PER_MIN, cur, DEFAULT_COP_SETTLE_MAX_K_PER_MIN): _number(0.1, 5, 0.1, "°C/min"),
                    _req(CONF_COP_OUTLET_MAX_AGE_MIN, cur, DEFAULT_COP_OUTLET_MAX_AGE_MIN): _number(5, 720, 5, "min"),
                    _req(CONF_COP_INTAKE_MAX_AGE_MIN, cur, DEFAULT_COP_INTAKE_MAX_AGE_MIN): _number(5, 720, 5, "min"),
                },
                collapsed=True,
            ),
            vol.Required(SECTION_ICING): _section(
                {
                    _req(CONF_ICING_BLOCK_MIN, cur, DEFAULT_ICING_BLOCK_MIN): _number(10, 720, 5, "min"),
                    _req(CONF_DEFROST_COIL_RISE_K, cur, DEFAULT_DEFROST_COIL_RISE_K): _number(2, 20, 0.5, "°C"),
                    _req(CONF_DEFROST_MAX_CYCLES_90MIN, cur, DEFAULT_DEFROST_MAX_CYCLES_90MIN): _number(1, 10, 1),
                    _req(CONF_ICING_COIL_DEPRESSION_K, cur, DEFAULT_ICING_COIL_DEPRESSION_K): _number(3, 20, 0.5, "°C"),
                },
                collapsed=True,
            ),
        }
    )


PARAMETER_KEYS = keys_of(schema_parameters)
# the cooling keys count while hidden too: a stored value is no leftover
HEAT_PUMP_TUNING_KEYS = keys_of(lambda cur: schema_heat_pump_tuning(cur, cooling=True))

# every top-level key the integration reads; anything else in a stored entry is a leftover
CONFIG_KEYS = frozenset(
    (
        *SYSTEM_KEYS, *PRICE_KEYS, *BOILER_KEYS, CONF_BOILER_PROFILE, *HEAT_PUMP_KEYS, *HEAT_PUMP_TUNING_KEYS, *OUTDOOR_KEYS,
        *PARAMETER_KEYS, CONF_ROOMS,
        CONF_BOILER_FILL_DECLINED,  # kept by the boiler step itself, never asked for
    )
)


def known_keys(config: Mapping[str, Any]) -> dict[str, Any]:
    """Only the keys the integration reads."""
    return {k: v for k, v in config.items() if k in CONFIG_KEYS}


def room_options(rooms: Iterable[Mapping[str, Any]]) -> list[selector.SelectOptionDict]:
    """Dropdown entries for rooms: the key is stored, the name is shown."""
    return [selector.SelectOptionDict(value=str(r[CONF_ROOM_KEY]), label=str(r.get(CONF_ROOM_NAME, r[CONF_ROOM_KEY]))) for r in rooms]


def has_heat_pump(cur: Mapping[str, Any]) -> bool:
    """Whether a heat pump is configured at all."""
    return bool(cur.get(CONF_HEAT_PUMP_CLIMATE))


# How the valves driven directly are controlled: the best way, or one forced for a test (review 2026-11-15 item 6). The own
# controller on the opening (way d) is no choice any more (§3.5): two controllers on one quantity, the curve would learn
# against it. A room stored with it opens with the best way, so that the form can be saved.
VALVE_CONTROL_CHOICES: Final = (VALVE_CONTROL_AUTO, VALVE_CONTROL_OFFSET, VALVE_CONTROL_SETPOINT)

SECTION_ROOM_HEAT_PUMP = "heat_pump"
# One field for the room thermostat or the valves driven directly: two fields invited bare valves in the thermostat's,
# where the window was left to them. Sorted by the integration on saving, stored as `climate_entity` or `valve_entities`.
ROOM_THERMOSTAT_OR_VALVES = "thermostat_or_valves"
ROOM_THERMOSTAT_PLATFORMS: Final = ("better_thermostat", "versatile_thermostat", "generic_thermostat")
SECTION_ROOM_TIMES = "times"
SECTION_ROOM_ADVANCED = "advanced"
ROOM_SECTIONS: Final = (SECTION_ROOM_HEAT_PUMP, SECTION_ROOM_TIMES, SECTION_ROOM_ADVANCED)


def schema_room(cur: Mapping[str, Any], allow_add_another: bool, heat_pump: bool = True) -> vol.Schema:
    """Fields for one room: what every room needs open, the rest under folded headings.

    Stored flat as before (`flatten_sections`). "Served by the heat pump" and bathroom drying only make sense once a
    heat pump exists (drying runs on the heat pump); offering the ticks without one invites settings that can never
    do anything. Their heading opens for a room the heat pump serves.
    """
    fields: dict[Any, Any] = {
        _req(CONF_ROOM_NAME, cur): _text(),
        # optional since 2026-11-15, as Better Thermostat asks for its external sensor: without one the valve measures
        _opt(CONF_ROOM_TEMP, cur): _entity("sensor"),
        _opt(CONF_ROOM_HUMIDITY, cur): _entity("sensor"),
        _opt(CONF_ROOM_WINDOWS, cur): _entity("binary_sensor", multiple=True),
        _opt(ROOM_THERMOSTAT_OR_VALVES, {ROOM_THERMOSTAT_OR_VALVES: thermostat_or_valves(cur) or None}): _entity(
            "climate", multiple=True
        ),
        _opt(CONF_ROOM_SCHEDULE_ENTITY, cur): _entity("schedule"),
        _req(CONF_ROOM_COMFORT_TEMP, cur, DEFAULT_ROOM_COMFORT_TEMP): _number(*ROOM_TEMP_RANGES["comfort"], 0.5, "°C"),
        _req(CONF_ROOM_SETBACK_TEMP, cur, DEFAULT_ROOM_SETBACK_TEMP): _number(*ROOM_TEMP_RANGES["setback"], 0.5, "°C"),
    }
    if heat_pump:
        fields[vol.Required(SECTION_ROOM_HEAT_PUMP)] = _section(
            {
                _req(CONF_ROOM_HEAT_PUMP, cur, False): selector.BooleanSelector(),
                _req(CONF_ROOM_DRYING, cur, False): selector.BooleanSelector(),
                _req(CONF_ROOM_DRYING_AUTO, cur, True): selector.BooleanSelector(),
                **({_opt(CONF_ROOM_COOLING_SCHEDULE_ENTITY, cur): _entity("schedule")} if const.COOLING_PREVIEW else {}),
            },
            collapsed=not cur.get(CONF_ROOM_HEAT_PUMP),
        )
    fields[vol.Required(SECTION_ROOM_TIMES)] = _section(
        {
            _req(CONF_ROOM_SCHEDULE_WEEKDAY, cur, DEFAULT_SCHEDULE_WEEKDAY): _text(),
            _req(CONF_ROOM_SCHEDULE_WEEKEND, cur, DEFAULT_SCHEDULE_WEEKEND): _text(),
            _opt(CONF_ROOM_PREHEAT_MAX_MIN, cur): _number(0, 720, 5, "min"),
        },
        collapsed=True,
    )
    fields[vol.Required(SECTION_ROOM_ADVANCED)] = _section(
        {
            _req(CONF_ROOM_PRIORITY, cur, 5): _number(1, 10, 1),
            _opt(CONF_ROOM_GAIN_SENSORS, cur): _entity("sensor", multiple=True),
            _req(CONF_ROOM_LEAD_K, cur, DEFAULT_ROOM_LEAD_K): _number(0.0, ROOM_LEAD_MAX_K, 0.5, "K"),
            vol.Required(
                CONF_ROOM_VALVE_CONTROL,
                default=cur.get(CONF_ROOM_VALVE_CONTROL) if cur.get(CONF_ROOM_VALVE_CONTROL) in VALVE_CONTROL_CHOICES
                else VALVE_CONTROL_AUTO,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(VALVE_CONTROL_CHOICES), translation_key="valve_control",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        },
        collapsed=True,
    )
    if allow_add_another:
        fields[vol.Required("add_another", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


def thermostat_or_valves(room: Mapping[str, Any]) -> list[str]:
    """What the one field shows: the stored room thermostat and valves driven directly."""
    climate = room.get(CONF_ROOM_CLIMATE)
    return [*([climate] if isinstance(climate, str) and climate else []), *_valves(room)]


def split_thermostat_or_valves(
    user_input: Mapping[str, Any], platform: Callable[[str], str | None]
) -> tuple[dict[str, Any], dict[str, str]]:
    """The answer with the one field sorted into `climate_entity` and `valve_entities`, and what is refused.

    `platform` names the integration of an entity (entity registry). Better, Versatile and Generic Thermostat are
    room thermostats; anything else (Zigbee2MQTT, ZHA, Z-Wave, Homematic IP ...) is a valve. An entity the registry
    does not know (set up in YAML without an id) stays a room thermostat, as the field of that name took it before.
    """
    flat = {k: v for k, v in user_input.items() if k not in (ROOM_THERMOSTAT_OR_VALVES, CONF_ROOM_CLIMATE, CONF_ROOM_VALVES)}
    chosen = list(dict.fromkeys(e for e in user_input.get(ROOM_THERMOSTAT_OR_VALVES) or () if isinstance(e, str) and e))
    known = {e: platform(e) for e in chosen}
    thermostats = [e for e in chosen if known[e] is None or known[e] in ROOM_THERMOSTAT_PLATFORMS]
    valves = [e for e in chosen if e not in thermostats]
    if thermostats:
        flat[CONF_ROOM_CLIMATE] = thermostats[0]
    if valves:
        flat[CONF_ROOM_VALVES] = valves
    if thermostats and valves:
        return flat, {ROOM_THERMOSTAT_OR_VALVES: "valves_and_thermostat"}
    if len(thermostats) > 1:
        return flat, {ROOM_THERMOSTAT_OR_VALVES: "two_room_thermostats"}
    return flat, {}


def thermostat_or_valves_errors(errors: Mapping[str, str]) -> dict[str, str]:
    """Messages about the room thermostat or the valves stand at the one field that took them."""
    moved = {k: v for k, v in errors.items() if k not in (CONF_ROOM_CLIMATE, CONF_ROOM_VALVES)}
    for key in (CONF_ROOM_CLIMATE, CONF_ROOM_VALVES):
        if key in errors:
            moved.setdefault(ROOM_THERMOSTAT_OR_VALVES, errors[key])
    return moved


def lift_section_errors(schema: vol.Schema, errors: Mapping[str, str]) -> dict[str, str]:
    """A message for a field under a heading stands above the form: a folded heading would hide it at its field."""
    inside = {
        str(m) for marker, value in schema.schema.items() if isinstance(value, section) for m in _markers(value.schema)
    }
    lifted = {key: code for key, code in errors.items() if key not in inside}
    for key, code in errors.items():
        if key in inside:
            lifted.setdefault("base", code)
    return lifted


# The keys of a room's cooling numbers begin with "cool_" (number.py): a room key that ends so collides with them.
ROOM_KEY_RESERVED_ENDING = "_cool"


def validate_room(
    user_input: Mapping[str, Any],
    existing: Iterable[Mapping[str, Any]] = (),
    heat_pump_climate: str | None = None,
    drives: Callable[[str], Iterable[str]] = lambda _: (),
    room_key: str | None = None,
    driver: Callable[[str], str | None] = lambda _: None,
) -> dict[str, str]:
    """Check one room; pass the other rooms, so its name stays unique (also when one is renamed).

    `drives` names the entities a room thermostat drives besides itself (Better Thermostat, Versatile
    Thermostat: adapters.inputs.driven_by). `room_key` is the key of a room that exists already, None for a new one.
    `driver` names the room thermostat of an active Better or Versatile Thermostat entry driving a valve, None if
    none does (adapters.inputs.valve_driver).
    """
    existing = list(existing)
    errors: dict[str, str] = {}
    for key in (CONF_ROOM_SCHEDULE_WEEKDAY, CONF_ROOM_SCHEDULE_WEEKEND):
        try:
            parse_schedule(user_input.get(key))
        except ValueError:
            errors[key] = "invalid_schedule"
    if float(user_input.get(CONF_ROOM_SETBACK_TEMP, 0)) > float(user_input.get(CONF_ROOM_COMFORT_TEMP, 0)):
        errors[CONF_ROOM_SETBACK_TEMP] = "setback_above_comfort"
    slug = slugify(str(user_input[CONF_ROOM_NAME]))
    if any(slug in (r[CONF_ROOM_KEY], slugify(str(r.get(CONF_ROOM_NAME, "")))) for r in existing):
        errors[CONF_ROOM_NAME] = "duplicate_room"
    elif room_key is None and slug.endswith(ROOM_KEY_RESERVED_ENDING):
        # A new room takes its key from its name. "Bad cool" would give its heating comfort the unique id of the
        # cooling comfort of "Bad", and Home Assistant drops one of the two (review 2026-11-13). A room that has
        # such a key already keeps it; a repair tells where it really collides (issues.py).
        errors[CONF_ROOM_NAME] = "room_name_ends_in_cool"
    elif room_key is None and any(r[CONF_ROOM_KEY] == slug + ROOM_KEY_RESERVED_ENDING for r in existing):
        errors[CONF_ROOM_NAME] = "room_name_before_cool"  # the same collision, from the other side
    if user_input.get(CONF_ROOM_DRYING):
        # drying heats with the heat pump and starts on a rise in humidity; without either it silently never runs
        if not user_input.get(CONF_ROOM_HEAT_PUMP):
            errors[CONF_ROOM_DRYING] = "drying_needs_heat_pump"
        if not user_input.get(CONF_ROOM_HUMIDITY):
            errors[CONF_ROOM_HUMIDITY] = "drying_needs_humidity"
        elif not user_input.get(CONF_ROOM_TEMP):
            # the humidity belongs to the room's air: with the valve's warm reading a drying started for nothing
            errors[CONF_ROOM_TEMP] = "drying_needs_temperature"
    # One climate entity, one controller (review 2026-11-08): the heat pump's unit as a room's thermostat,
    # or one thermostat in two rooms, got two setpoints a minute, each taken for a hand at the device.
    # A room thermostat in front of the unit (Better Thermostat around it) writes it all the same.
    climate = user_input.get(CONF_ROOM_CLIMATE)
    if climate and heat_pump_climate and (climate == heat_pump_climate or heat_pump_climate in drives(climate)):
        errors[CONF_ROOM_CLIMATE] = "climate_is_heat_pump"
    elif climate and any(
        climate == r.get(CONF_ROOM_CLIMATE) or climate in _valves(r) or set(drives(climate)) & set(_valves(r))
        for r in existing
    ):
        # also a Better Thermostat whose valve another room drives directly (review 2026-11-15)
        errors[CONF_ROOM_CLIMATE] = "climate_used_twice"
    if not user_input.get(CONF_ROOM_TEMP) and not climate and not _valves(user_input):
        errors.setdefault(CONF_ROOM_TEMP, "room_needs_a_temperature")  # nothing that measures the room
    # Valves driven directly (docs/PHASE13_VALVES.md §4, §7): one controller per valve as well
    valves = _valves(user_input)
    taken = {e for r in existing for e in (r.get(CONF_ROOM_CLIMATE), *_valves(r)) if e}
    if valves and climate:
        errors[CONF_ROOM_VALVES] = "valves_and_thermostat"
    elif heat_pump_climate and heat_pump_climate in valves:
        errors[CONF_ROOM_VALVES] = "climate_is_heat_pump"
    elif any(v in taken for v in valves):
        errors[CONF_ROOM_VALVES] = "valve_used_twice"
    elif any(driver(v) for v in valves):
        errors[CONF_ROOM_VALVES] = "valve_driven_elsewhere"
    return errors


def _valves(room: Mapping[str, Any]) -> tuple[str, ...]:
    raw = room.get(CONF_ROOM_VALVES) or ()
    return (raw,) if isinstance(raw, str) else tuple(v for v in raw if isinstance(v, str) and v)


_EBUS_CIRCUIT = re.compile(r"[A-Za-z0-9_.-]+")


def validate_heat_pump_tuning(user_input: Mapping[str, Any]) -> dict[str, str]:
    """The automatic season's fallback (docs/PHASE12_COOLING.md §20.5): cooling from at least 2 °C above heating from,
    the veto at most at heating from. Nothing to check while cooling is hidden."""
    cooling_from = user_input.get(CONF_COOLING_SEASON_FROM_C)
    heating_from = user_input.get(CONF_HEATING_SEASON_FROM_C)
    veto = user_input.get(CONF_COOLING_SEASON_VETO_C)
    if cooling_from is not None and heating_from is not None and float(cooling_from) < float(heating_from) + 2.0:
        return {"base": "season_means_too_close"}
    if veto is not None and heating_from is not None and float(veto) > float(heating_from):
        return {"base": "season_veto_above_heating"}
    return {}


def validate_boiler(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Refuse a curve lower in the cold than in mild weather, and a minimum above the maximum.

    Swapped limits pin the flow to one value and block the learning both ways.
    """

    def value(key: str, default: float) -> float:
        return float(user_input.get(key, default))

    errors: dict[str, str] = {}
    if value(CONF_BOILER_CURVE_COLD, DEFAULT_BOILER_CURVE_COLD) < value(CONF_BOILER_CURVE_WARM, DEFAULT_BOILER_CURVE_WARM):
        errors[CONF_BOILER_CURVE_COLD] = "curve_inverted"
    if value(CONF_BOILER_FLOW_MIN, DEFAULT_BOILER_FLOW_MIN) > value(CONF_BOILER_FLOW_MAX, DEFAULT_BOILER_FLOW_MAX):
        errors[CONF_BOILER_FLOW_MIN] = "flow_min_above_max"
    # A boiler addressed through entities needs both: a setpoint alone cannot block the heating,
    # and a switch alone leaves the flow to the boiler.
    setpoint, enable = user_input.get(CONF_BOILER_FLOW_SETPOINT_ENTITY), user_input.get(CONF_BOILER_HEATING_ENABLE_ENTITY)
    if bool(setpoint) != bool(enable):
        errors[CONF_BOILER_HEATING_ENABLE_ENTITY if setpoint else CONF_BOILER_FLOW_SETPOINT_ENTITY] = "boiler_entities_incomplete"
    # The circuit is one level of the topic SetMode is written to (ebusd/<circuit>/SetMode/set): a '+' or
    # '#' made every publish fail while the control was active, a '/' built another path (review 2026-11-08).
    if CONF_BOILER_EBUS_CIRCUIT in user_input and not _EBUS_CIRCUIT.fullmatch(str(user_input[CONF_BOILER_EBUS_CIRCUIT]).strip()):
        errors[CONF_BOILER_EBUS_CIRCUIT] = "invalid_ebus_circuit"
    return errors


def validate_heat_pump(
    user_input: Mapping[str, Any], rooms: Iterable[Mapping[str, Any]] = (), drives: Callable[[str], Iterable[str]] = lambda _: ()
) -> dict[str, str]:
    climate = user_input.get(CONF_HEAT_PUMP_CLIMATE)
    thermostats = [t for r in rooms if (t := r.get(CONF_ROOM_CLIMATE))]
    if climate and any(t == climate or climate in drives(t) for t in thermostats):
        return {CONF_HEAT_PUMP_CLIMATE: "climate_is_heat_pump"}  # see validate_room
    try:
        curve = parse_airflow_curve(user_input.get(CONF_HEAT_PUMP_AIRFLOW_CURVE))
    except (ValueError, TypeError):
        return {CONF_HEAT_PUMP_AIRFLOW_CURVE: "invalid_airflow_curve"}
    # One point draws no line, the engine divides by the lowest speed, and a speed given twice
    # leaves the flow open. An empty curve is allowed: the COP is then not measured.
    speeds = [rpm for rpm, _ in curve]
    if curve and (len(curve) < 2 or len(set(speeds)) < len(speeds) or min(speeds) <= 0 or min(flow for _, flow in curve) <= 0):
        return {CONF_HEAT_PUMP_AIRFLOW_CURVE: "invalid_airflow_curve"}
    return {}


def merge_step(target: dict[str, Any], keys: tuple[str, ...], user_input: Mapping[str, Any]) -> None:
    """Apply a step result: keys missing from user_input are cleared (None)."""
    for key in keys:
        target[key] = user_input.get(key)


def merge_present(target: dict[str, Any], keys: tuple[str, ...], user_input: Mapping[str, Any]) -> None:
    """Apply a step result without clearing fields the step did not show.

    Used where the schema varies by system type: a gas price the district
    schema never displayed must survive a switch back to gas.
    """
    for key in keys:
        if key in user_input:
            target[key] = user_input[key]


def room_from_input(user_input: Mapping[str, Any], key: str | None = None) -> dict[str, Any]:
    room = {k: v for k, v in user_input.items() if k != "add_another"}
    room[CONF_ROOM_KEY] = key or slugify(str(user_input[CONF_ROOM_NAME]))
    return room


