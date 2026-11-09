"""Forgetting learned values: by hand per area, and automatically after a relevant change."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.thriftherm import learning_reset
from custom_components.thriftherm.const import DEFAULT_HEAT_PUMP_DUCT_FACTOR, DOMAIN, SERVICE_RESET_LEARNING
from custom_components.thriftherm.engines.boiler_control import BoilerMemory
from custom_components.thriftherm.engines.heat_pump_control import ControlMemory
from custom_components.thriftherm.engines.learning import HeatRateStats

from .test_integration import _entry_data, _setup

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ---------------------------------------------------------------- pure comparison
def test_first_start_without_a_stored_basis_forgets_nothing() -> None:
    assert learning_reset.invalidated(None, learning_reset.basis(_entry_data())) == (set(), set())


def test_unrelated_changes_keep_everything() -> None:
    before = learning_reset.basis(_entry_data())
    config = {**_entry_data(), "electricity_price": 0.5, "boiler_curve_flow_at_minus10": 55.0}  # 55 is the default
    assert learning_reset.invalidated(before, learning_reset.basis(config)) == (set(), set())


def test_the_airflow_curve_invalidates_only_the_heat_pump() -> None:
    before = learning_reset.basis(_entry_data())
    after = learning_reset.basis({**_entry_data(), "midea_airflow_curve": "500:300,700:450"})
    assert learning_reset.invalidated(before, after) == ({"heat_pump"}, set())


def test_the_duct_factor_invalidates_nothing_any_more() -> None:
    # The COP table holds its heat without the factor and scales. A basis stored with the factor in it still matches.
    config = _entry_data()
    before = learning_reset.basis(config)
    assert learning_reset.invalidated(before, learning_reset.basis({**config, "midea_duct_factor": 0.8})) == (set(), set())
    values = json.loads(before["heat_pump"])
    stored = {**before, "heat_pump": json.dumps({**values, "duct_factor": 0.9}, sort_keys=True)}
    assert learning_reset.invalidated(stored, learning_reset.basis({**config, "midea_duct_factor": 0.8})) == (set(), set())


def test_the_stored_fingerprint_stays_as_an_older_version_writes_it() -> None:
    # It compares the duct factor as well: a basis without it, and it forgets the old table it was promised to read.
    config = {
        "midea_climate": "climate.x", "midea_intake_temp": "sensor.a", "midea_outlet_temp": "sensor.b",
        "midea_airflow_curve": "500:300, 600:350", "midea_duct_factor": 0.9, "midea_room": "wohnzimmer",
        "rooms": [{"key": "wohnzimmer", "served_by_midea": True}],
    }
    older = {
        "climate": "climate.x", "intake": "sensor.a", "outlet": "sensor.b", "airflow_curve": "500:300,600:350",
        "duct_factor": 0.9, "installed_in": "wohnzimmer", "serves": ["wohnzimmer"],
    }
    assert learning_reset.basis(config)["heat_pump"] == json.dumps(older, sort_keys=True)
    assert json.loads(learning_reset.basis({})["heat_pump"])["duct_factor"] == DEFAULT_HEAT_PUMP_DUCT_FACTOR


def test_a_basis_stored_without_the_duct_factor_forgets_nothing() -> None:
    config = {"midea_climate": "climate.x", "midea_duct_factor": 0.9}
    current = learning_reset.basis(config)
    values = json.loads(current["heat_pump"])
    del values["duct_factor"]
    stored = {**current, "heat_pump": json.dumps(values, sort_keys=True)}
    assert learning_reset.invalidated(stored, current) == (set(), set())


def test_heating_curve_invalidates_only_the_boiler() -> None:
    before = learning_reset.basis(_entry_data())
    after = learning_reset.basis({**_entry_data(), "boiler_curve_flow_at_minus10": 50.0})
    assert learning_reset.invalidated(before, after) == ({"boiler"}, set())


def test_flow_limits_keep_the_boiler_correction() -> None:
    # audit 2026-09-28: the reset on 18.09. came from raising the minimum 30 -> 45 °C alone
    before = learning_reset.basis(_entry_data())
    after = learning_reset.basis({**_entry_data(), "boiler_flow_min": 45.0, "boiler_flow_max": 55.0})
    assert learning_reset.invalidated(before, after) == (set(), set())


def _with_flow_limits(basis: dict, curve_cold: float = 55.0) -> dict:
    """A basis as stored before the flow limits left the boiler fingerprint."""
    boiler = {"curve_cold": curve_cold, "curve_warm": 30.0, "flow_min": 45.0, "flow_max": 60.0}
    return {**basis, "boiler": json.dumps(boiler, sort_keys=True)}


def test_a_stored_basis_with_the_flow_limits_forgets_nothing() -> None:
    current = learning_reset.basis({**_entry_data(), "boiler_flow_min": 45.0})
    assert learning_reset.invalidated(_with_flow_limits(current), current) == (set(), set())
    assert learning_reset.invalidated(_with_flow_limits(current, curve_cold=50.0), current) == ({"boiler"}, set())


def test_new_room_sensor_invalidates_that_room_and_serving_change_the_heat_pump() -> None:
    config = _entry_data()
    before = learning_reset.basis(config)
    rooms = [dict(r) for r in config["rooms"]]
    rooms[1]["temperature_sensor"] = "sensor.new_living_temp"
    rooms[1]["served_by_midea"] = True
    scopes, rooms_hit = learning_reset.invalidated(before, learning_reset.basis({**config, "rooms": rooms}))
    assert scopes == {"heat_pump", "rooms"}
    assert rooms_hit == {"wohnzimmer"}


def test_heat_rates_forget_single_room() -> None:
    stats = HeatRateStats(rates={"a": {"boiler": [1.0]}, "b": {"heat_pump": [2.0]}})
    stats.forget(["a"])
    assert stats.rates == {"b": {"heat_pump": [2.0]}}
    stats.forget()
    assert stats.rates == {}


# ---------------------------------------------------------------- integration
def _learned(coordinator) -> None:
    coordinator.boiler_memory = replace(BoilerMemory(), offset_k=3.0, adjustments=(1.0, 2.0), last_flow=40.0)
    coordinator.control_memory = ControlMemory(
        running_since_ts=5.0, last_hvac_mode="heat", learning_bins=((0, 1.0),), manual_until_ts=9e9
    )
    coordinator.heat_rates = HeatRateStats(rates={"badezimmer": {"heat_pump": [1.5]}, "wohnzimmer": {"boiler": [0.8]}})
    coordinator.cop_map.add_interval(4.0, 3500.0 * 3.1, 1000.0 * 3.1, 3.1 * 3600.0, 0.0)


async def test_service_resets_only_the_chosen_area_and_keeps_run_state(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)

    await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["heat_pump"]}, blocking=True)

    assert coordinator.control_memory.learning_bins == ()
    assert coordinator.control_memory.manual_until_ts == 9e9
    assert coordinator.cop_map.to_dict(1.0) == {}
    assert coordinator.boiler_memory.offset_k == 3.0
    assert coordinator.heat_rates.rates["badezimmer"] == {"heat_pump": [1.5]}
    assert coordinator.last_learning_reset["scopes"] == ["heat_pump"]
    assert hass.states.get("sensor.thriftherm_learning_state").attributes["last_reset"]["reason"] == "user"


async def test_only_an_administrator_may_reset_the_learning(hass: HomeAssistant, hass_read_only_user) -> None:
    """Review 2026-11-08 (#12): a household account or a wall tablet's token wiped months of learning, with no undo."""
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["heat_pump"]}, blocking=True, context=Context(user_id=hass_read_only_user.id)
        )
    assert coordinator.control_memory.learning_bins == ((0, 1.0),)


async def test_clear_away_refuses_fields_it_does_not_have(hass: HomeAssistant) -> None:
    """Review 2026-11-08 (#14): a return time meant for set_away ended the absence without a word."""
    import voluptuous as vol

    entry = await _setup(hass)
    await entry.runtime_data.async_set_away(None)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "clear_away", {"return_time": "2099-12-24 16:00"}, blocking=True)
    assert entry.runtime_data.mode == "away"


async def test_reset_keeps_the_run_state(hass: HomeAssistant) -> None:
    # checked without a cycle in between: the controller may end a run on its own
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)
    coordinator._forget(learning_reset.SCOPES, None, learning_reset.REASON_USER)
    assert coordinator.control_memory.running_since_ts == 5.0
    assert coordinator.control_memory.last_hvac_mode == "heat"
    assert coordinator.boiler_memory.last_flow == 40.0  # ramp base stays


async def test_service_resets_boiler_and_one_room(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)

    await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["boiler", "rooms"], "rooms": ["wohnzimmer"]}, blocking=True)
    assert coordinator.boiler_memory.offset_k == 0.0
    assert coordinator.boiler_memory.adjustments == ()
    assert coordinator.heat_rates.rates == {"badezimmer": {"heat_pump": [1.5]}}
    assert coordinator.control_memory.learning_bins == ((0, 1.0),)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["rooms"], "rooms": ["keller"]}, blocking=True)


async def test_options_flow_reset_step(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "reset_learning" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "reset_learning"})
    assert result["step_id"] == "reset_learning"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scope": []})
    assert result["errors"] == {"scope": "no_scope"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scope": ["rooms"]})
    assert result["type"] == "abort" and result["reason"] == "learning_reset"
    assert coordinator.heat_rates.rates == {}
    assert coordinator.boiler_memory.offset_k == 3.0


async def test_changing_the_airflow_curve_forgets_the_heat_pump_on_reload(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    _learned(entry.runtime_data)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "midea"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"midea_climate": "climate.midea", "midea_airflow_curve": "500:300,700:450"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    coordinator = entry.runtime_data  # reloaded
    assert coordinator.control_memory.learning_bins == ()
    assert coordinator.cop_map.to_dict(1.0) == {}
    assert coordinator.boiler_memory.offset_k == 3.0
    assert coordinator.heat_rates.rates == {"badezimmer": {"heat_pump": [1.5]}, "wohnzimmer": {"boiler": [0.8]}}
    assert coordinator.last_learning_reset["reason"] == "configuration_changed"

    # a plain restart with unchanged settings forgets nothing more
    coordinator.control_memory = replace(coordinator.control_memory, learning_bins=((1, 2.0),))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.control_memory.learning_bins == ((1, 2.0),)


def test_forgetting_the_heat_pump_learning_keeps_an_icing_lockout():
    # code review 2026-09-28: the reset also lifted an active icing lockout
    from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker

    tracker = HeatPumpTracker()
    tracker.block_until = 5_000.0
    tracker.defrost_cycle_starts = [1_000.0, 2_000.0]
    tracker.cop_samples = [(1_000.0, 3.2)]
    tracker.inefficient_since = 1_500.0
    tracker.reset()
    assert tracker.block_until == 5_000.0 and tracker.defrost_cycle_starts == [1_000.0, 2_000.0]
    assert tracker.cop_samples == [] and tracker.inefficient_since is None
