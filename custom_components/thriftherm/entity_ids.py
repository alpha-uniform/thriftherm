"""English object ids per translation key.

Home Assistant derives entity ids from the translated name in the system
language, so a German installation got `sensor.thriftherm_gunstigere_warmequelle`
and an English one something else entirely. Fixing the object id makes ids
identical everywhere — docs, issues and examples then work for everyone —
while display names still follow the user's language.

Generated from translations/en.json; tests/test_entity_ids.py keeps them in step.
"""

from __future__ import annotations

from typing import Final

OBJECT_IDS: Final[dict[str, dict[str, str]]] = {
    "sensor": {
        "midea_cop": "heat_pump_cop",
        "midea_thermal_power": "heat_pump_thermal_power",
        "midea_electrical_power": "heat_pump_electrical_power",
        "midea_airflow_estimate": "heat_pump_airflow_estimate",
        "midea_cost_per_kwh_thermal": "heat_cost_per_kwh_heat_pump",
        "gas_cost_per_kwh_thermal": "heat_cost_per_kwh_boiler",
        "break_even_cop": "break_even_cop",
        "cheaper_source": "cheaper_heat_source",
        "gas_power": "gas_power",
        "gas_energy": "gas_energy",
        "boiler_thermal_power_estimate": "boiler_thermal_power_estimate",
        "boiler_delta_t": "boiler_flow_return_spread",
        "active_source_advice": "recommended_heat_source",
        "decision_reason": "decision_reason",
        "automation_state": "automation_state",
        "safety_state": "safety_state",
        "midea_run_state": "heat_pump_run_state",
        "outdoor_temperature": "outdoor_temperature_selected_source",
        "room_target": "target_temperature",
        "room_deviation": "deviation_from_target",
        "room_demand": "heat_demand",
        "room_trend": "temperature_trend",
        "room_dew_point": "dew_point",
        "room_abs_humidity": "absolute_humidity",
        "midea_command": "heat_pump_command",
        "boiler_command": "boiler_command",
        "boiler_flow_setpoint": "boiler_flow_setpoint_planned",
        "room_thermostat_plan": "thermostat_plan",
        "learning_state": "learning_state",
    },
    "binary_sensor": {
        "boiler_heating_active": "boiler_space_heating_active",
        "boiler_hwc_active": "boiler_hot_water_active",
        "boiler_available": "boiler_data_available",
        "midea_blocked": "heat_pump_blocked_for_heating",
        "midea_heating_available": "heat_pump_available_for_heating",
        "room_window_open": "window_open",
        "room_heating_allowed": "heating_allowed",
        "defrost_suspected": "defrost_or_icing_suspected",
        "override_active": "override_active",
        "room_drying_mode": "drying_mode",
    },
    "select": {
        "mode": "operating_mode",
        "midea_control": "heat_pump_control",
        "boiler_control": "boiler_control",
        "room_control": "room_control",
    },
    "button": {
        "room_boost": "quick_heat_up",
    },
    "datetime": {
        "away_return": "planned_return",
    },
    "number": {
        "room_comfort_temp": "comfort_temperature",
        "room_setback_temp": "setback_temperature",
    },
}
