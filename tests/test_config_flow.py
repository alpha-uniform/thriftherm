"""Config and options flow: which fields each step shows and what it stores.

These pin today's behaviour so the flow code can be simplified without the
stored options drifting: a key that a step forgets to clear, or a default
that changes, would silently alter the running configuration.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.thriftherm import config_schema
from custom_components.thriftherm.const import DOMAIN

from .test_ebusd_detect import _add, _boiler, _gas_volume, _rename
from .test_integration import _entry_data, _set_states, _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


BOILER_FIELDS = [
    "boiler_flow_temp", "boiler_return_temp", "boiler_pump_state", "boiler_pump_running", "boiler_state_number",
    "boiler_hwc_mode", "boiler_signal", "boiler_circulation_l_h", "gas_volume", "gas_flow", "boiler_ebus_circuit",
    "boiler_curve_flow_at_minus10", "boiler_curve_flow_at_plus15", "boiler_flow_min", "boiler_flow_max",
    "boiler_allow_active_control",
]
OUTDOOR_FIELDS = ["outdoor_temp_sensors", "outdoor_rh_sensor", "weather_entity"]
PARAMETER_DEFAULTS = {
    "sensor_max_age_min": 720.0, "window_grace_s": 90.0, "frost_protection_temp": 7.0, "away_temp": 15.0,
    "demand_full_delta_k": 2.0, "trend_weight_h": 0.5, "internal_gain_threshold_w": 150.0,
    "internal_gain_factor_per_w": 0.001, "break_even_margin_on": 0.1, "break_even_margin_off": 0.05,
    "midea_min_outdoor_temp": -10.0, "midea_setpoint_offset_k": 1.0, "cop_warmup_s": 600.0, "cop_min_power_w": 150.0,
    "cop_min_delta_t_k": 3.0, "cop_min_samples": 12.0, "cop_settle_max_k_per_min": 0.5, "icing_block_min": 120.0,
    "defrost_coil_rise_k": 5.0, "defrost_max_cycles_90min": 3.0, "icing_coil_depression_k": 8.0,
    "drying_target_temp": 22.0, "drying_abs_humidity_rise_g_m3": 2.5, "drying_max_min": 60.0,
    "away_dewpoint_margin_k": 3.0, "preheat_margin_min": 20.0, "default_heat_rate_radiator_k_h": 0.8,
    "default_heat_rate_midea_k_h": 1.2, "override_default_min": 120.0, "midea_min_run_min": 20.0,
    "midea_min_off_min": 10.0, "boost_duration_min": 45.0, "midea_learning_runs": True,
    "midea_allow_active_control": False, "room_allow_active_control": False,
}
ROOM_CHOICES = [{"value": "badezimmer", "label": "Badezimmer"}, {"value": "wohnzimmer", "label": "Wohnzimmer"}]


def _fields(result) -> list[str]:
    return [str(marker) for marker in result["data_schema"].schema]


def _selector_options(result, field: str) -> list:
    marker = next(m for m in result["data_schema"].schema if str(m) == field)
    return list(result["data_schema"].schema[marker].config["options"])


async def _open(hass: HomeAssistant, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


# ------------------------------------------------------------------ field groups
def test_step_keys_are_exactly_the_fields_they_clear():
    """merge_step clears every key of the group that the form left empty; the groups must not drift."""
    assert config_schema.SYSTEM_KEYS == ("system_type",)
    assert config_schema.PRICE_KEYS == (
        "electricity_price", "gas_price", "boiler_efficiency", "gas_calorific_value_kwh_m3", "gas_z_factor",
        "heat_price", "heat_consumption_share_pct", "heat_ownership_share_pct",
    )
    assert config_schema.BOILER_KEYS == tuple(BOILER_FIELDS)
    assert config_schema.HEAT_PUMP_KEYS == (
        "midea_climate", "midea_power", "midea_intake_temp", "midea_intake_rh", "midea_outlet_temp",
        "midea_airflow_curve", "midea_duct_factor", "midea_room",
    )
    assert config_schema.OUTDOOR_KEYS == tuple(OUTDOOR_FIELDS)
    assert config_schema.PARAMETER_KEYS == tuple(PARAMETER_DEFAULTS)


# ------------------------------------------------------------------ options steps
async def test_options_system_step_stores_the_type(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["system", "prices", "boiler", "midea", "outdoor", "parameters", "rooms", "reset_learning"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system"})
    assert result["step_id"] == "system" and _fields(result) == ["system_type"]
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"system_type": "district"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options == {**_entry_data(), "system_type": "district"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "boiler" not in result["menu_options"]  # no own boiler to configure


async def test_options_prices_step_keeps_fields_it_did_not_show(hass: HomeAssistant) -> None:
    entry = await _setup(hass, options={"system_type": "district", "gas_price": 0.2})
    result = await _open(hass, entry, "prices")
    assert _fields(result) == ["electricity_price", "heat_price", "heat_consumption_share_pct", "heat_ownership_share_pct"]
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"electricity_price": 0.3})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["gas_price"] == 0.2  # survives a switch back to gas
    assert entry.options["heat_price"] == 0.12 and entry.options["heat_ownership_share_pct"] == 0.0
    assert "boiler_efficiency" not in entry.options


async def test_options_boiler_step_clears_what_was_left_empty(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry, "boiler")
    assert result["step_id"] == "boiler" and _fields(result) == BOILER_FIELDS
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"boiler_flow_temp": "sensor.flow2"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert {k: entry.options[k] for k in BOILER_FIELDS} == {
        "boiler_flow_temp": "sensor.flow2", "boiler_return_temp": None, "boiler_pump_state": None,
        "boiler_pump_running": None, "boiler_state_number": None, "boiler_hwc_mode": None, "boiler_signal": None,
        "boiler_circulation_l_h": 860.0, "gas_volume": None, "gas_flow": None, "boiler_ebus_circuit": "bai",
        "boiler_curve_flow_at_minus10": 55.0, "boiler_curve_flow_at_plus15": 30.0, "boiler_flow_min": 30.0,
        "boiler_flow_max": 60.0, "boiler_allow_active_control": False,
    }
    assert entry.options["midea_climate"] == "climate.midea"  # other groups untouched


# ------------------------------------------------------------------ boiler found over ebusd
def _prefilled(result) -> dict:
    """What the form shows filled in, i.e. what the frontend sends back when the user only confirms."""
    shown = {}
    for marker in result["data_schema"].schema:
        if marker.description and "suggested_value" in marker.description:
            shown[str(marker)] = marker.description["suggested_value"]
        elif marker.default is not vol.UNDEFINED:
            shown[str(marker)] = marker.default()
    return shown


async def _config_flow_to_boiler(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"system_type": "gas"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "boiler"
    return result


async def _confirm_boiler_and_finish(hass: HomeAssistant, result, boiler: dict) -> dict:
    """Send the boiler step, skip heat pump and outdoor, add one room; returns the stored data."""
    _set_states(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], boiler)
    assert result["step_id"] == "midea"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    room = {"name": "Bad", "temperature_sensor": "sensor.bath_temp", "add_another": False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], room)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    return result["data"]


async def test_config_flow_fills_in_the_boiler_found(hass: HomeAssistant) -> None:
    found = _boiler(hass)
    found["boiler_flow_temp"] = _rename(hass, found["boiler_flow_temp"], "sensor.vorlauf")
    signal = _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    _gas_volume(hass, "sensor.gas_meter_volume")
    result = await _config_flow_to_boiler(hass)
    assert result["description_placeholders"] == {"detected": "Found: Vaillant (bai), 6 of 6 values — please check."}
    shown = _prefilled(result)
    assert {k: shown.get(k) for k in BOILER_FIELDS if k in found or k in ("boiler_signal", "gas_volume", "gas_flow")} == {
        **found, "boiler_signal": signal, "gas_volume": "sensor.gas_meter_volume", "gas_flow": None,
    }
    assert shown["boiler_ebus_circuit"] == "bai" and shown["boiler_flow_min"] == 30.0

    data = await _confirm_boiler_and_finish(hass, result, shown)  # the user only confirms
    assert data["boiler_profile"] == "vaillant_bai" and data["boiler_ebus_circuit"] == "bai"
    assert {k: data[k] for k in found} == found
    assert data["boiler_signal"] == signal and data["gas_volume"] == "sensor.gas_meter_volume" and data["gas_flow"] is None


async def test_config_flow_names_a_partial_find_in_german(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    _boiler(hass, "bai2", roles=("boiler_flow_temp", "boiler_return_temp", "boiler_hwc_mode"))
    result = await _config_flow_to_boiler(hass)
    assert result["description_placeholders"] == {"detected": "Gefunden: Vaillant (bai2), 3 von 6 Werten – bitte prüfen."}
    shown = _prefilled(result)
    assert shown["boiler_ebus_circuit"] == "bai2"
    assert "boiler_pump_state" not in shown and shown["boiler_hwc_mode"] == "sensor.heating_ebusd_bai2_status02_hwcmode"


async def test_config_flow_without_ebusd_asks_for_the_entities(hass: HomeAssistant) -> None:
    result = await _config_flow_to_boiler(hass)
    assert result["description_placeholders"] == {"detected": "No ebusd boiler found — select the entities yourself."}
    assert _prefilled(result) == {
        "boiler_circulation_l_h": 860.0, "boiler_ebus_circuit": "bai", "boiler_curve_flow_at_minus10": 55.0,
        "boiler_curve_flow_at_plus15": 30.0, "boiler_flow_min": 30.0, "boiler_flow_max": 60.0,
        "boiler_allow_active_control": False,
    }
    data = await _confirm_boiler_and_finish(hass, result, _prefilled(result))
    assert "boiler_profile" not in data and data["boiler_flow_temp"] is None  # nothing found, nothing stored


async def test_options_boiler_step_fills_only_empty_fields(hass: HomeAssistant) -> None:
    entry = await _setup(hass)  # flow, return, Status01, signal and gas flow are configured
    found = _boiler(hass)
    _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    _gas_volume(hass, "sensor.gas_meter_volume")
    result = await _open(hass, entry, "boiler")
    assert result["description_placeholders"] == {"detected": "Found: Vaillant (bai), 6 of 6 values — please check."}
    shown = _prefilled(result)
    assert {k: shown.get(k) for k in BOILER_FIELDS[:10] if k != "boiler_circulation_l_h"} == {
        "boiler_flow_temp": "sensor.flow", "boiler_return_temp": "sensor.return", "boiler_pump_state": "sensor.pump",
        "boiler_pump_running": found["boiler_pump_running"], "boiler_state_number": found["boiler_state_number"],
        "boiler_hwc_mode": found["boiler_hwc_mode"], "boiler_signal": "binary_sensor.ebus",
        "gas_volume": "sensor.gas_meter_volume", "gas_flow": "sensor.gas_flow",
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], shown)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["boiler_flow_temp"] == "sensor.flow" and entry.options["boiler_pump_running"] == found["boiler_pump_running"]
    assert "boiler_profile" not in entry.options  # the configured boiler was only completed


async def test_options_boiler_step_leaves_a_configured_boiler_alone(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    _boiler(hass, "bai2")  # a boiler on another circuit than the configured one
    result = await _open(hass, entry, "boiler")
    assert result["description_placeholders"] == {"detected": "Found: Vaillant (bai2), 6 of 6 values — please check."}
    shown = _prefilled(result)
    assert shown["boiler_ebus_circuit"] == "bai"
    for key in ("boiler_pump_running", "boiler_state_number", "boiler_hwc_mode"):
        assert key not in shown  # still empty: not taken from the other boiler


async def test_options_outdoor_step_clears_what_was_left_empty(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry, "outdoor")
    assert result["step_id"] == "outdoor" and _fields(result) == OUTDOOR_FIELDS
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"weather_entity": "weather.home"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert {k: entry.options[k] for k in OUTDOOR_FIELDS} == {
        "outdoor_temp_sensors": None, "outdoor_rh_sensor": None, "weather_entity": "weather.home",
    }


async def test_options_parameters_step_fills_defaults(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry, "parameters")
    assert result["step_id"] == "parameters" and _fields(result) == list(PARAMETER_DEFAULTS)
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"frost_protection_temp": 8})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert {k: entry.options[k] for k in PARAMETER_DEFAULTS} == {**PARAMETER_DEFAULTS, "frost_protection_temp": 8.0}


async def test_options_midea_step_validates_and_offers_the_rooms(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry, "midea")
    assert result["step_id"] == "midea" and result["errors"] == {}
    assert _fields(result)[-1] == "midea_room"
    assert _selector_options(result, "midea_room") == ROOM_CHOICES
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"midea_airflow_curve": "bad"})
    assert result["errors"] == {"midea_airflow_curve": "invalid_airflow_curve"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"midea_climate": "climate.midea"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["midea_power"] is None and entry.options["midea_room"] is None


async def test_room_choices_in_rooms_and_reset_steps(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await _open(hass, entry, "rooms")
    assert _selector_options(result, "room") == [*ROOM_CHOICES, {"value": "add-room", "label": "+"}]
    result = await _open(hass, entry, "reset_learning")
    assert _selector_options(result, "rooms") == ROOM_CHOICES


# ------------------------------------------------------------------ duplicate rooms
async def test_options_refuses_a_second_room_with_the_same_name(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    room = {"name": "Badezimmer", "priority": 2, "temperature_sensor": "sensor.x", "comfort_temp": 21.0, "setback_temp": 17.0}
    result = await _open(hass, entry, "rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room": "add-room", "action": "edit"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**room, "schedule_weekday": "x"})
    assert result["errors"] == {"schedule_weekday": "invalid_schedule", "name": "duplicate_room"}

    # editing a room under its own name is no duplicate
    result = await _open(hass, entry, "rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room": "badezimmer", "action": "edit"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], room)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert [(r["key"], r["priority"]) for r in entry.options["rooms"]] == [("badezimmer", 2), ("wohnzimmer", 3)]


async def test_config_flow_refuses_a_second_room_with_the_same_name(hass: HomeAssistant) -> None:
    _set_states(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"system_type": "none"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"electricity_price": 0.3})
    assert result["step_id"] == "midea"  # no boiler step without an own boiler
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "room"
    room = {"name": "Bad", "temperature_sensor": "sensor.x", "add_another": True}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], room)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**room, "name": "bad"})
    assert result["errors"] == {"name": "duplicate_room"}
    assert result["description_placeholders"] == {"count": "1"}


# ------------------------------------------------------------------ services
async def test_room_services_reject_an_unknown_room(hass: HomeAssistant) -> None:
    await _setup(hass)
    for service, data in (("set_override", {"temperature": 20}), ("boost", {})):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, service, {"room": "keller", **data}, blocking=True)
        assert err.value.translation_key == "unknown_room"
        assert err.value.translation_placeholders == {"room": "keller"}


async def test_boost_service_starts_a_quick_heat_up(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "boost", {"room": "badezimmer", "duration_min": 30}, blocking=True)
    assert entry.runtime_data.boosts["badezimmer"] == pytest.approx(dt_util.utcnow().timestamp() + 1800, abs=5)


async def test_set_away_service_checks_and_localises_the_return_time(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    past = (dt_util.now() - timedelta(hours=1)).replace(tzinfo=None, microsecond=0)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_away", {"return_time": past.isoformat()}, blocking=True)
    assert err.value.translation_key == "return_time_in_past"
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None

    # a time without zone is meant in the zone of the installation
    later = (dt_util.now() + timedelta(days=1)).replace(tzinfo=None, microsecond=0)
    await hass.services.async_call(DOMAIN, "set_away", {"return_time": later.isoformat()}, blocking=True)
    assert coordinator.mode == "away"
    assert coordinator.away_return_ts == later.replace(tzinfo=dt_util.get_default_time_zone()).timestamp()


def test_heat_pump_ticks_appear_only_with_a_heat_pump():
    # bathroom drying runs on the heat pump; without one the tick could never do anything
    from custom_components.thriftherm.config_schema import schema_room
    from custom_components.thriftherm.const import CONF_ROOM_DRYING, CONF_ROOM_HEAT_PUMP

    def fields(heat_pump: bool) -> set[str]:
        return {str(key) for key in schema_room({}, allow_add_another=False, heat_pump=heat_pump).schema}

    assert {CONF_ROOM_DRYING, CONF_ROOM_HEAT_PUMP} <= fields(True)
    assert not {CONF_ROOM_DRYING, CONF_ROOM_HEAT_PUMP} & fields(False)
