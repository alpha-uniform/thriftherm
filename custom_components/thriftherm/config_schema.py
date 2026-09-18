"""Form schemas, field groups, validation and merging for the config and options flow."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_AWAY_DEWPOINT_MARGIN,
    CONF_AWAY_TEMP,
    CONF_BOILER_CIRCULATION_L_H,
    CONF_BOILER_EFFICIENCY,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_STATE_NUMBER,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_BREAK_EVEN_MARGIN_OFF,
    CONF_BREAK_EVEN_MARGIN_ON,
    CONF_COP_MIN_DELTA_T,
    CONF_COP_MIN_POWER_W,
    CONF_COP_MIN_SAMPLES,
    CONF_COP_SETTLE_MAX_K_PER_MIN,
    CONF_COP_WARMUP_S,
    CONF_DEFROST_COIL_RISE_K,
    CONF_DEFAULT_HEAT_RATE_HEAT_PUMP,
    CONF_DEFAULT_HEAT_RATE_RADIATOR,
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
    CONF_SYSTEM_TYPE,
    CONF_ICING_BLOCK_MIN,
    CONF_ICING_COIL_DEPRESSION_K,
    CONF_HEAT_PUMP_AIRFLOW_CURVE,
    CONF_HEAT_PUMP_DUCT_FACTOR,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_INTAKE_RH,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    CONF_HEAT_PUMP_OUTLET_TEMP,
    CONF_HEAT_PUMP_ROOM,
    CONF_HEAT_PUMP_POWER,
    CONF_HEAT_PUMP_SETPOINT_OFFSET,
    CONF_OUTDOOR_RH_SENSOR,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_BOOST_DURATION_MIN,
    CONF_BOILER_ALLOW_ACTIVE,
    CONF_BOILER_CURVE_COLD,
    CONF_BOILER_CURVE_WARM,
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_FLOW_MAX,
    CONF_BOILER_FLOW_MIN,
    CONF_HEAT_PUMP_ALLOW_ACTIVE,
    CONF_ROOM_ALLOW_ACTIVE,
    CONF_HEAT_PUMP_LEARNING_RUNS,
    CONF_HEAT_PUMP_MIN_OFF_MIN,
    CONF_HEAT_PUMP_MIN_RUN_MIN,
    CONF_PREHEAT_MARGIN_MIN,
    CONF_OUTDOOR_TEMP_SENSORS,
    CONF_ROOM_CLIMATE,
    CONF_ROOM_COMFORT_TEMP,
    CONF_ROOM_DRYING,
    CONF_ROOM_GAIN_SENSORS,
    CONF_ROOM_HUMIDITY,
    CONF_ROOM_KEY,
    CONF_ROOM_HEAT_PUMP,
    CONF_ROOM_NAME,
    CONF_ROOM_PRIORITY,
    CONF_ROOM_SCHEDULE_ENTITY,
    CONF_ROOM_SCHEDULE_WEEKDAY,
    CONF_ROOM_SCHEDULE_WEEKEND,
    CONF_ROOM_SETBACK_TEMP,
    CONF_ROOM_TEMP,
    CONF_ROOM_WINDOWS,
    CONF_ROOMS,
    CONF_SENSOR_MAX_AGE_MIN,
    CONF_TREND_WEIGHT_H,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_GRACE_S,
    DEFAULT_AWAY_DEWPOINT_MARGIN,
    DEFAULT_AWAY_TEMP,
    DEFAULT_BOILER_CIRCULATION_L_H,
    DEFAULT_BOILER_EFFICIENCY,
    DEFAULT_BREAK_EVEN_MARGIN_OFF,
    DEFAULT_BREAK_EVEN_MARGIN_ON,
    DEFAULT_COP_MIN_DELTA_T,
    DEFAULT_COP_MIN_POWER_W,
    DEFAULT_COP_MIN_SAMPLES,
    DEFAULT_COP_SETTLE_MAX_K_PER_MIN,
    DEFAULT_COP_WARMUP_S,
    DEFAULT_DEFROST_COIL_RISE_K,
    DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP,
    DEFAULT_DEFAULT_HEAT_RATE_RADIATOR,
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
    DEFAULT_SYSTEM_TYPE,
    SYSTEM_DISTRICT,
    SYSTEM_GAS,
    SYSTEM_TYPES,
    DEFAULT_ICING_BLOCK_MIN,
    DEFAULT_ICING_COIL_DEPRESSION_K,
    DEFAULT_HEAT_PUMP_DUCT_FACTOR,
    DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP,
    DEFAULT_HEAT_PUMP_SETPOINT_OFFSET,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_BOOST_DURATION_MIN,
    DEFAULT_BOILER_ALLOW_ACTIVE,
    DEFAULT_BOILER_CURVE_COLD,
    DEFAULT_BOILER_CURVE_WARM,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DEFAULT_BOILER_FLOW_MAX,
    DEFAULT_BOILER_FLOW_MIN,
    DEFAULT_HEAT_PUMP_ALLOW_ACTIVE,
    DEFAULT_ROOM_ALLOW_ACTIVE,
    DEFAULT_HEAT_PUMP_LEARNING_RUNS,
    DEFAULT_HEAT_PUMP_MIN_OFF_MIN,
    DEFAULT_HEAT_PUMP_MIN_RUN_MIN,
    DEFAULT_PREHEAT_MARGIN_MIN,
    DEFAULT_ROOM_COMFORT_TEMP,
    DEFAULT_ROOM_SETBACK_TEMP,
    DEFAULT_SCHEDULE_WEEKDAY,
    DEFAULT_SCHEDULE_WEEKEND,
    DEFAULT_SENSOR_MAX_AGE_MIN,
    DEFAULT_TREND_WEIGHT_H,
    DEFAULT_WINDOW_GRACE_S,
)
from .adapters.config import system_type_from_config
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


def keys_of(schema_fn: Callable[[Mapping[str, Any]], vol.Schema]) -> tuple[str, ...]:
    """The keys a step stores, read off its form.

    Only for steps whose fields never depend on the current configuration.
    """
    return tuple(marker.schema for marker in schema_fn({}).schema)


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
        fields[_req(CONF_GAS_PRICE, cur, DEFAULT_GAS_PRICE)] = _number(0, 1, "any", "€/kWh")
        fields[_req(CONF_BOILER_EFFICIENCY, cur, DEFAULT_BOILER_EFFICIENCY)] = _number(0.5, 1.1, 0.01)
        fields[_req(CONF_GAS_CALORIFIC_VALUE, cur, DEFAULT_GAS_CALORIFIC_VALUE)] = _number(8, 14, 0.001, "kWh/m³")
        fields[_req(CONF_GAS_Z_FACTOR, cur, DEFAULT_GAS_Z_FACTOR)] = _number(0.8, 1.1, "any")
    elif system == SYSTEM_DISTRICT:
        fields[_req(CONF_HEAT_PRICE, cur, DEFAULT_HEAT_PRICE)] = _number(0, 1, "any", "€/kWh")
        fields[_req(CONF_HEAT_CONSUMPTION_SHARE, cur, DEFAULT_HEAT_CONSUMPTION_SHARE)] = _number(0, 100, 1, "%")
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


def schema_boiler(cur: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_BOILER_FLOW_TEMP, cur): _entity("sensor"),
            _opt(CONF_BOILER_RETURN_TEMP, cur): _entity("sensor"),
            _opt(CONF_BOILER_PUMP_STATE, cur): _entity("sensor"),
            _opt(CONF_BOILER_PUMP_RUNNING, cur): _entity(["sensor", "binary_sensor"]),
            _opt(CONF_BOILER_STATE_NUMBER, cur): _entity("sensor"),
            _opt(CONF_BOILER_HWC_MODE, cur): _entity("sensor"),
            _opt(CONF_BOILER_SIGNAL, cur): _entity("binary_sensor"),
            _req(CONF_BOILER_CIRCULATION_L_H, cur, DEFAULT_BOILER_CIRCULATION_L_H): _number(100, 5000, 1, "l/h"),
            _opt(CONF_GAS_VOLUME, cur): _entity("sensor"),
            _opt(CONF_GAS_FLOW, cur): _entity("sensor"),
            _req(CONF_BOILER_EBUS_CIRCUIT, cur, DEFAULT_BOILER_EBUS_CIRCUIT): _text(),
            _req(CONF_BOILER_CURVE_COLD, cur, DEFAULT_BOILER_CURVE_COLD): _number(30, 75, 0.5, "°C"),
            _req(CONF_BOILER_CURVE_WARM, cur, DEFAULT_BOILER_CURVE_WARM): _number(30, 60, 0.5, "°C"),
            _req(CONF_BOILER_FLOW_MIN, cur, DEFAULT_BOILER_FLOW_MIN): _number(30, 50, 0.5, "°C"),
            _req(CONF_BOILER_FLOW_MAX, cur, DEFAULT_BOILER_FLOW_MAX): _number(35, 75, 0.5, "°C"),
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


def schema_parameters(cur: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _req(CONF_SENSOR_MAX_AGE_MIN, cur, DEFAULT_SENSOR_MAX_AGE_MIN): _number(5, 720, 1, "min"),
            _req(CONF_WINDOW_GRACE_S, cur, DEFAULT_WINDOW_GRACE_S): _number(0, 900, 1, "s"),
            _req(CONF_FROST_TEMP, cur, DEFAULT_FROST_TEMP): _number(3, 12, 0.5, "°C"),
            _req(CONF_AWAY_TEMP, cur, DEFAULT_AWAY_TEMP): _number(8, 20, 0.5, "°C"),
            _req(CONF_DEMAND_FULL_DELTA, cur, DEFAULT_DEMAND_FULL_DELTA): _number(0.5, 5, 0.1, "°C"),
            _req(CONF_TREND_WEIGHT_H, cur, DEFAULT_TREND_WEIGHT_H): _number(0, 2, 0.1, "h"),
            _req(CONF_GAIN_THRESHOLD_W, cur, DEFAULT_GAIN_THRESHOLD_W): _number(0, 2000, 10, "W"),
            _req(CONF_GAIN_FACTOR, cur, DEFAULT_GAIN_FACTOR): _number(0, 0.01, "any", "1/W"),
            _req(CONF_BREAK_EVEN_MARGIN_ON, cur, DEFAULT_BREAK_EVEN_MARGIN_ON): _number(0, 0.5, 0.01),
            _req(CONF_BREAK_EVEN_MARGIN_OFF, cur, DEFAULT_BREAK_EVEN_MARGIN_OFF): _number(0, 0.5, 0.01),
            _req(CONF_HEAT_PUMP_MIN_OUTDOOR_TEMP, cur, DEFAULT_HEAT_PUMP_MIN_OUTDOOR_TEMP): _number(-20, 10, 0.5, "°C"),
            _req(CONF_HEAT_PUMP_SETPOINT_OFFSET, cur, DEFAULT_HEAT_PUMP_SETPOINT_OFFSET): _number(0, 5, 0.5, "°C"),
            _req(CONF_COP_WARMUP_S, cur, DEFAULT_COP_WARMUP_S): _number(0, 1800, 10, "s"),
            _req(CONF_COP_MIN_POWER_W, cur, DEFAULT_COP_MIN_POWER_W): _number(0, 2000, 10, "W"),
            _req(CONF_COP_MIN_DELTA_T, cur, DEFAULT_COP_MIN_DELTA_T): _number(0, 15, 0.5, "°C"),
            _req(CONF_COP_MIN_SAMPLES, cur, DEFAULT_COP_MIN_SAMPLES): _number(1, 120, 1),
            _req(CONF_COP_SETTLE_MAX_K_PER_MIN, cur, DEFAULT_COP_SETTLE_MAX_K_PER_MIN): _number(0.1, 5, 0.1, "°C/min"),
            _req(CONF_ICING_BLOCK_MIN, cur, DEFAULT_ICING_BLOCK_MIN): _number(10, 720, 5, "min"),
            _req(CONF_DEFROST_COIL_RISE_K, cur, DEFAULT_DEFROST_COIL_RISE_K): _number(2, 20, 0.5, "°C"),
            _req(CONF_DEFROST_MAX_CYCLES_90MIN, cur, DEFAULT_DEFROST_MAX_CYCLES_90MIN): _number(1, 10, 1),
            _req(CONF_ICING_COIL_DEPRESSION_K, cur, DEFAULT_ICING_COIL_DEPRESSION_K): _number(3, 20, 0.5, "°C"),
            _req(CONF_DRYING_TARGET_TEMP, cur, DEFAULT_DRYING_TARGET_TEMP): _number(18, 26, 0.5, "°C"),
            _req(CONF_DRYING_ABS_HUMIDITY_RISE, cur, DEFAULT_DRYING_ABS_HUMIDITY_RISE): _number(0.5, 8, 0.1, "g/m³"),
            _req(CONF_DRYING_MAX_MIN, cur, DEFAULT_DRYING_MAX_MIN): _number(10, 240, 5, "min"),
            _req(CONF_AWAY_DEWPOINT_MARGIN, cur, DEFAULT_AWAY_DEWPOINT_MARGIN): _number(1, 8, 0.5, "°C"),
            _req(CONF_PREHEAT_MARGIN_MIN, cur, DEFAULT_PREHEAT_MARGIN_MIN): _number(0, 120, 5, "min"),
            _req(CONF_DEFAULT_HEAT_RATE_RADIATOR, cur, DEFAULT_DEFAULT_HEAT_RATE_RADIATOR): _number(0.1, 5, 0.1, "°C/h"),
            _req(CONF_DEFAULT_HEAT_RATE_HEAT_PUMP, cur, DEFAULT_DEFAULT_HEAT_RATE_HEAT_PUMP): _number(0.1, 5, 0.1, "°C/h"),
            _req(CONF_OVERRIDE_DEFAULT_MIN, cur, DEFAULT_OVERRIDE_DEFAULT_MIN): _number(5, 1440, 5, "min"),
            _req(CONF_HEAT_PUMP_MIN_RUN_MIN, cur, DEFAULT_HEAT_PUMP_MIN_RUN_MIN): _number(5, 120, 1, "min"),
            _req(CONF_HEAT_PUMP_MIN_OFF_MIN, cur, DEFAULT_HEAT_PUMP_MIN_OFF_MIN): _number(3, 120, 1, "min"),
            _req(CONF_BOOST_DURATION_MIN, cur, DEFAULT_BOOST_DURATION_MIN): _number(10, 180, 5, "min"),
            _req(CONF_HEAT_PUMP_LEARNING_RUNS, cur, DEFAULT_HEAT_PUMP_LEARNING_RUNS): selector.BooleanSelector(),
            _req(CONF_HEAT_PUMP_ALLOW_ACTIVE, cur, DEFAULT_HEAT_PUMP_ALLOW_ACTIVE): selector.BooleanSelector(),
            _req(CONF_ROOM_ALLOW_ACTIVE, cur, DEFAULT_ROOM_ALLOW_ACTIVE): selector.BooleanSelector(),
        }
    )


PARAMETER_KEYS = keys_of(schema_parameters)


def room_options(rooms: Iterable[Mapping[str, Any]]) -> list[selector.SelectOptionDict]:
    """Dropdown entries for rooms: the key is stored, the name is shown."""
    return [selector.SelectOptionDict(value=str(r[CONF_ROOM_KEY]), label=str(r.get(CONF_ROOM_NAME, r[CONF_ROOM_KEY]))) for r in rooms]


def has_heat_pump(cur: Mapping[str, Any]) -> bool:
    """Whether a heat pump is configured at all."""
    return bool(cur.get(CONF_HEAT_PUMP_CLIMATE))


def schema_room(cur: Mapping[str, Any], allow_add_another: bool, heat_pump: bool = True) -> vol.Schema:
    """Fields for one room.

    "Served by the heat pump" and bathroom drying only make sense once a heat pump
    exists (drying runs on the heat pump); offering the ticks without one invites
    settings that can never do anything.
    """
    fields: dict[Any, Any] = {
        _req(CONF_ROOM_NAME, cur): _text(),
        _req(CONF_ROOM_PRIORITY, cur, 5): _number(1, 10, 1),
        _req(CONF_ROOM_TEMP, cur): _entity("sensor"),
        _opt(CONF_ROOM_HUMIDITY, cur): _entity("sensor"),
        _opt(CONF_ROOM_WINDOWS, cur): _entity("binary_sensor", multiple=True),
        _opt(CONF_ROOM_CLIMATE, cur): _entity("climate"),
    }
    if heat_pump:
        fields[_req(CONF_ROOM_HEAT_PUMP, cur, False)] = selector.BooleanSelector()
        fields[_req(CONF_ROOM_DRYING, cur, False)] = selector.BooleanSelector()
    fields.update({
        _opt(CONF_ROOM_GAIN_SENSORS, cur): _entity("sensor", multiple=True),
        _opt(CONF_ROOM_SCHEDULE_ENTITY, cur): _entity("schedule"),
        _req(CONF_ROOM_COMFORT_TEMP, cur, DEFAULT_ROOM_COMFORT_TEMP): _number(10, 28, 0.5, "°C"),
        _req(CONF_ROOM_SETBACK_TEMP, cur, DEFAULT_ROOM_SETBACK_TEMP): _number(5, 25, 0.5, "°C"),
        _req(CONF_ROOM_SCHEDULE_WEEKDAY, cur, DEFAULT_SCHEDULE_WEEKDAY): _text(),
        _req(CONF_ROOM_SCHEDULE_WEEKEND, cur, DEFAULT_SCHEDULE_WEEKEND): _text(),
    })
    if allow_add_another:
        fields[vol.Required("add_another", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


def validate_room(user_input: Mapping[str, Any], existing: Iterable[Mapping[str, Any]] = ()) -> dict[str, str]:
    """Check one room; pass the existing rooms when a new one is added, so its name stays unique."""
    errors: dict[str, str] = {}
    for key in (CONF_ROOM_SCHEDULE_WEEKDAY, CONF_ROOM_SCHEDULE_WEEKEND):
        try:
            parse_schedule(user_input.get(key))
        except ValueError:
            errors[key] = "invalid_schedule"
    if float(user_input.get(CONF_ROOM_SETBACK_TEMP, 0)) > float(user_input.get(CONF_ROOM_COMFORT_TEMP, 0)):
        errors[CONF_ROOM_SETBACK_TEMP] = "setback_above_comfort"
    if any(r[CONF_ROOM_KEY] == slugify(str(user_input[CONF_ROOM_NAME])) for r in existing):
        errors[CONF_ROOM_NAME] = "duplicate_room"
    return errors


def validate_heat_pump(user_input: Mapping[str, Any]) -> dict[str, str]:
    try:
        parse_airflow_curve(user_input.get(CONF_HEAT_PUMP_AIRFLOW_CURVE))
    except (ValueError, TypeError):
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


