"""Sensor entities for Thriftherm."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AUTOMATION_ACTIVE,
    AUTOMATION_OBSERVING,
    AUTOMATION_PLANNING,
    BOILER_PLANS,
    ROOM_PLANS,
    CTRL_ACTIVE,
    CTRL_SHADOW,
    HEAT_PUMP_PLANS,
    HEAT_PUMP_RUN_STATES,
    SAFETY_DEGRADED,
    SAFETY_FALLBACK,
    SAFETY_OK,
    SOURCE_BOILER,
    SOURCE_BOTH,
    SOURCE_HEAT_PUMP,
    SOURCE_NONE,
    SOURCE_UNKNOWN,
)
from . import texts
from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .engines.boiler_control import PHASE_LEARNING, PHASE_PAUSED, PHASE_REFINING
from .entity import DescribedEntity, ThrifthermEntity, is_wanted

Data = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class ThrifthermSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Data], Any]
    attr_fn: Callable[[Data], dict[str, Any]] | None = None
    heat_pump_addon: bool = False  # only created when a Midea heat pump is configured
    boiler_only: bool = False  # needs an own boiler on ebusd (flow/return, gas meter, SetMode)
    heat_source: bool = False  # needs any central heat source to compare against


def _cop_attrs(d: Data) -> dict[str, Any]:
    c = d["cop"]
    return {
        "raw_sample": c.raw,
        "samples": c.samples,
        "gate_reasons": list(c.gate_reasons),
        "airflow_m3h": c.airflow_m3h,
        "duct_factor": d.get("duct_factor"),
        "expected_cop_at_current_outdoor": d.get("expected_cop"),
        "expected_cop_basis": d.get("expected_cop_basis"),
        "prior_calibration_factor": d.get("prior_calibration_factor"),
        "cop_map_by_outdoor_temp": d.get("cop_map"),
    }


def _automation_state(d: Data) -> str:
    modes = (d.get("control_mode"), d.get("boiler_control_mode"), d.get("room_control_mode"))
    if CTRL_ACTIVE in modes:
        return AUTOMATION_ACTIVE
    if CTRL_SHADOW in modes:
        return AUTOMATION_PLANNING
    return AUTOMATION_OBSERVING


def _boiler_attrs(d: Data) -> dict[str, Any]:
    c = d["boiler_command"]
    return {
        "control_mode": d.get("boiler_control_mode"),
        "commands_sent": d.get("boiler_control_mode") == CTRL_ACTIVE,
        "setmode_payload": c.payload,
        "setmode_topic": d.get("boiler_setmode_topic"),
        "heating_blocked": c.disable_hc,
        "reason": c.reason,
        "blockers": list(c.blockers),
        "waiting": c.waiting,
        "heating_curve_flow": c.curve_flow,
        "learned_offset_k": c.offset_k,
        "flow_return_spread_k": c.spread_k,
        "learning_phase": c.learning_phase,
        "pump_running": d["boiler"].pump_running,
        "boiler_status": None if d["boiler"].state_number is None else f"S.{d['boiler'].state_number}",
        "recent_plans": d.get("boiler_log"),
    }


def _command_attrs(d: Data) -> dict[str, Any]:
    c = d["midea_command"]
    return {
        "control_mode": d.get("control_mode"),
        "commands_sent": d.get("control_mode") == CTRL_ACTIVE,
        # which rooms this heat pump is supposed to serve, and where it stands
        "served_rooms": list(d.get("midea_rooms") or ()),
        "installed_in_room": d.get("midea_room"),
        "action": c.action,
        "target_temperature": c.target_temp,
        "fan_mode": c.fan_mode,
        "load": c.load,
        "reason": c.reason,
        "blockers": list(c.blockers),
        "waiting": c.waiting,
        "eco_offset_k": d.get("eco_offset_k"),
        "decision_cop": d.get("decision_cop"),
        "decision_cop_basis": d.get("decision_cop_basis"),
        "break_even_cop": d["econ"].break_even_cop,
        "quick_heat_up_until": d.get("boosts"),
        "recent_commands": d.get("command_log"),
    }


def _learning_attrs(d: Data) -> dict[str, Any]:
    c = d["boiler_command"]
    return {
        "heating_curve_offset_k": c.offset_k,
        "adjustments_last_24h": c.adjustments_last_day,
        "last_adjustment_reason": c.last_adjust_reason,
        "flow_return_spread_k": c.spread_k,
        "paused_because": d.get("learning_blocked_by"),
        "room_heat_up_rates": d.get("heat_rates"),
        "room_cooling_rates": d.get("cool_rates"),
        "room_heating_power_k_h": d.get("heating_power"),
        "cop_bins": len(d.get("cop_map") or {}),
        "cop_basis": d.get("expected_cop_basis"),
        "midea_eco_offset_k": d.get("eco_offset_k"),
        "last_reset": d.get("learning_reset"),
    }


def _reason_texts(d: Data) -> list[str]:
    return texts.render_reasons(d["advice"].reasons, d.get("language"), d.get("room_names") or {})


def _reason_state(d: Data) -> str:
    text = " | ".join(_reason_texts(d))
    return text[:252] + "..." if len(text) > 255 else (text or "no data")


def _desc(key: str, **kwargs: Any) -> ThrifthermSensorDescription:
    return ThrifthermSensorDescription(key=key, translation_key=key, **kwargs)


def _room_desc(key: str, **kwargs: Any) -> ThrifthermSensorDescription:
    return ThrifthermSensorDescription(key=key, translation_key=f"room_{key}", **kwargs)


def _power(key: str, **kwargs: Any) -> ThrifthermSensorDescription:
    return _desc(
        key,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        **kwargs,
    )


SYSTEM_SENSORS: tuple[ThrifthermSensorDescription, ...] = (
    _desc(
        "midea_cop",
        heat_pump_addon=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:heat-pump",
        value_fn=lambda d: d["cop"].value,
        attr_fn=_cop_attrs,
    ),
    _power(
        "midea_thermal_power",
        heat_pump_addon=True,
        value_fn=lambda d: d["cop"].thermal_power_w,
    ),
    _power(
        "midea_electrical_power",
        heat_pump_addon=True,
        value_fn=lambda d: d["midea"].electrical_power_w,
        attr_fn=lambda d: {"source": d["midea"].electrical_power_source},
    ),
    _desc(
        "midea_airflow_estimate",
        heat_pump_addon=True,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        icon="mdi:fan",
        value_fn=lambda d: d["cop"].airflow_m3h,
    ),
    _desc(
        "midea_cost_per_kwh_thermal",
        heat_pump_addon=True,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:cash",
        value_fn=lambda d: d["econ"].heat_pump_cost_per_kwh_thermal,
    ),
    _desc(
        "gas_cost_per_kwh_thermal",
        heat_source=True,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        icon="mdi:cash",
        value_fn=lambda d: d["econ"].gas_cost_per_kwh_thermal,
    ),
    _desc(
        "break_even_cop",
        heat_pump_addon=True,
        heat_source=True,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:scale-balance",
        value_fn=lambda d: d["econ"].break_even_cop,
    ),
    _desc(
        "cheaper_source",
        heat_pump_addon=True,
        heat_source=True,
        device_class=SensorDeviceClass.ENUM,
        options=[SOURCE_HEAT_PUMP, SOURCE_BOILER, SOURCE_UNKNOWN],
        icon="mdi:swap-horizontal",
        value_fn=lambda d: d["econ"].cheaper_source,
        attr_fn=lambda d: {"saving_pct": d["econ"].saving_pct},
    ),
    _power(
        "gas_power",
        boiler_only=True,
        value_fn=lambda d: d["boiler"].gas_power_w,
    ),
    _desc(
        "gas_energy",
        boiler_only=True,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d["boiler"].gas_energy_kwh,
    ),
    _power(
        "boiler_thermal_power_estimate",
        boiler_only=True,
        value_fn=lambda d: d["boiler"].thermal_power_estimate_w,
        attr_fn=lambda d: {"basis": "nominal_circulation_estimate"},
    ),
    _desc(
        "boiler_delta_t",
        boiler_only=True,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:delta",
        value_fn=lambda d: d["boiler"].delta_t_k,
    ),
    _desc(
        "active_source_advice",
        device_class=SensorDeviceClass.ENUM,
        options=[SOURCE_BOILER, SOURCE_HEAT_PUMP, SOURCE_BOTH, SOURCE_NONE],
        icon="mdi:fire",
        value_fn=lambda d: d["advice"].source,
        attr_fn=lambda d: {"blocked": d["advice"].blocked, "observation_only": d.get("control_mode") != CTRL_ACTIVE},
    ),
    _desc(
        "decision_reason",
        icon="mdi:comment-question",
        value_fn=_reason_state,
        attr_fn=lambda d: {"reasons": _reason_texts(d), "reason_codes": [r.code for r in d["advice"].reasons]},
    ),
    _desc(
        "automation_state",
        device_class=SensorDeviceClass.ENUM,
        options=[AUTOMATION_OBSERVING, AUTOMATION_PLANNING, AUTOMATION_ACTIVE],
        icon="mdi:eye",
        value_fn=_automation_state,
    ),
    _desc(
        "learning_state",
        device_class=SensorDeviceClass.ENUM,
        options=[PHASE_LEARNING, PHASE_REFINING, PHASE_PAUSED],
        icon="mdi:school",
        value_fn=lambda d: d["boiler_command"].learning_phase,
        attr_fn=_learning_attrs,
    ),
    _desc(
        "boiler_command",
        boiler_only=True,
        device_class=SensorDeviceClass.ENUM,
        options=BOILER_PLANS,
        icon="mdi:water-boiler",
        value_fn=lambda d: d["boiler_command"].plan,
        attr_fn=_boiler_attrs,
    ),
    _desc(
        "boiler_flow_setpoint",
        boiler_only=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer-chevron-up",
        value_fn=lambda d: d["boiler_command"].flow_setpoint,
    ),
    _desc(
        "midea_command",
        heat_pump_addon=True,
        device_class=SensorDeviceClass.ENUM,
        options=HEAT_PUMP_PLANS,
        icon="mdi:heat-pump-outline",
        value_fn=lambda d: d["midea_command"].plan,
        attr_fn=_command_attrs,
    ),
    _desc(
        "safety_state",
        device_class=SensorDeviceClass.ENUM,
        options=[SAFETY_OK, SAFETY_DEGRADED, SAFETY_FALLBACK],
        icon="mdi:shield-check",
        value_fn=lambda d: d["safety"].state,
        attr_fn=lambda d: {"issues": list(d["safety"].issues), "frost_rooms": list(d["safety"].frost_rooms)},
    ),
    _desc(
        "midea_run_state",
        heat_pump_addon=True,
        device_class=SensorDeviceClass.ENUM,
        options=HEAT_PUMP_RUN_STATES,
        icon="mdi:air-conditioner",
        value_fn=lambda d: d["midea"].run_state,
        attr_fn=lambda d: {"issues": list(d["midea"].issues)},
    ),
    _desc(
        "outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d["snapshot"].outdoor_temp.value_or_none,
        attr_fn=lambda d: {"source": d["snapshot"].outdoor_temp_source},
    ),
)


ROOM_SENSORS: tuple[ThrifthermSensorDescription, ...] = (
    _room_desc(
        "target",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda r: r.target,
        attr_fn=lambda r: {
            "reason": r.target_reason,
            "quick_heat_up": r.boost_active,
            "override_until_ts": r.override_until_ts,
            "preheat_start_ts": r.preheat_start_ts,
        },
    ),
    _room_desc(
        "deviation",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer-lines",
        value_fn=lambda r: r.deviation_k,
    ),
    _room_desc(
        "demand",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:radiator",
        value_fn=lambda r: None if r.demand is None else round(r.demand * 100.0, 0),
        attr_fn=lambda r: {"issues": list(r.issues), "internal_gain_w": r.internal_gain_w},
    ),
    _room_desc(
        "trend",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="°C/h",
        icon="mdi:trending-up",
        value_fn=lambda r: r.trend_k_per_h,
    ),
    _room_desc(
        "dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda r: r.dew_point_c,
    ),
    _room_desc(
        "abs_humidity",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="g/m³",
        icon="mdi:water-percent",
        value_fn=lambda r: r.abs_humidity_g_m3,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [DescribedSensor(coordinator, d) for d in SYSTEM_SENSORS if is_wanted(coordinator, d)]
    for room in coordinator.builder.rooms:
        entities.extend(DescribedSensor(coordinator, d, room.key) for d in ROOM_SENSORS)
        if room.climate_entity:
            entities.append(ThermostatPlanSensor(coordinator, room.key))
    async_add_entities(entities)


class DescribedSensor(DescribedEntity, SensorEntity):
    entity_description: ThrifthermSensorDescription

    @property
    def native_value(self) -> Any:
        return self._value()


class ThermostatPlanSensor(ThrifthermEntity, SensorEntity):
    """What the integration hands to the room thermostat (Better Thermostat)."""

    _attr_translation_key = "room_thermostat_plan"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ROOM_PLANS
    _attr_icon = "mdi:thermostat"

    def __init__(self, coordinator: ThrifthermCoordinator, room_key: str) -> None:
        super().__init__(coordinator, "thermostat_plan", room_key)
        self.suggest_english_entity_id()

    def _cmd(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("room_commands", {}).get(self._room_key)

    @property
    def native_value(self) -> Any:
        cmd = self._cmd()
        return None if cmd is None else cmd.plan

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cmd = self._cmd()
        if cmd is None:
            return None
        return {
            "control_mode": self.coordinator.data.get("room_control_mode"),
            "setpoint": cmd.target,
            "thermostat_setpoint": cmd.thermostat_target,
            "reason": cmd.reason,
            "commands_sent": self.coordinator.data.get("room_control_mode") == "active",
        }
