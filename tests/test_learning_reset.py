"""Forgetting learned values: by hand per area, and automatically after a relevant change."""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.thriftherm import learning_reset
from custom_components.thriftherm.const import DOMAIN, SERVICE_RESET_LEARNING
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


def test_duct_factor_invalidates_only_the_heat_pump() -> None:
    before = learning_reset.basis(_entry_data())
    after = learning_reset.basis({**_entry_data(), "midea_duct_factor": 0.8})
    assert learning_reset.invalidated(before, after) == ({"heat_pump"}, set())


def test_heating_curve_invalidates_only_the_boiler() -> None:
    before = learning_reset.basis(_entry_data())
    after = learning_reset.basis({**_entry_data(), "boiler_curve_flow_at_minus10": 50.0})
    assert learning_reset.invalidated(before, after) == ({"boiler"}, set())


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
    stats = HeatRateStats(rates={"a": [1.0], "b": [2.0]})
    stats.forget(["a"])
    assert stats.rates == {"b": [2.0]}
    stats.forget()
    assert stats.rates == {}


# ---------------------------------------------------------------- integration
def _learned(coordinator) -> None:
    coordinator.boiler_memory = replace(BoilerMemory(), offset_k=3.0, adjustments=(1.0, 2.0), last_flow=40.0)
    coordinator.control_memory = ControlMemory(running_since_ts=5.0, last_hvac_mode="heat", offset_k=2.5, learning_bins=((0, 1.0),), manual_until_ts=9e9)
    coordinator.heat_rates = HeatRateStats(rates={"badezimmer": [1.5], "wohnzimmer": [0.8]})
    coordinator.cop_map.add(4.0, 3.5)


async def test_service_resets_only_the_chosen_area_and_keeps_run_state(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    _learned(coordinator)

    await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["heat_pump"]}, blocking=True)

    assert coordinator.control_memory.offset_k is None
    assert coordinator.control_memory.learning_bins == ()
    assert coordinator.control_memory.manual_until_ts == 9e9
    assert coordinator.cop_map.to_dict() == {}
    assert coordinator.boiler_memory.offset_k == 3.0
    assert coordinator.heat_rates.rates["badezimmer"] == [1.5]
    assert coordinator.last_learning_reset["scopes"] == ["heat_pump"]
    assert hass.states.get("sensor.thriftherm_learning_state").attributes["last_reset"]["reason"] == "user"


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
    assert coordinator.heat_rates.rates == {"badezimmer": [1.5]}
    assert coordinator.control_memory.offset_k == 2.5

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


async def test_changing_the_duct_factor_forgets_the_heat_pump_on_reload(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    _learned(entry.runtime_data)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "midea"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"midea_climate": "climate.midea", "midea_duct_factor": 0.8})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    coordinator = entry.runtime_data  # reloaded
    assert coordinator.control_memory.offset_k is None
    assert coordinator.cop_map.to_dict() == {}
    assert coordinator.boiler_memory.offset_k == 3.0
    assert coordinator.heat_rates.rates == {"badezimmer": [1.5], "wohnzimmer": [0.8]}
    assert coordinator.last_learning_reset["reason"] == "configuration_changed"

    # a plain restart with unchanged settings forgets nothing more
    coordinator.control_memory = replace(coordinator.control_memory, offset_k=1.0)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.control_memory.offset_k == 1.0
