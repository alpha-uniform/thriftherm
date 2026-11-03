"""The COP table in the running integration: what decides, what a change of settings does, and an update of the store."""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.const import (
    DOMAIN,
    SERVICE_RESET_LEARNING,
    SOURCE_BOILER,
    SOURCE_HEAT_PUMP,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from custom_components.thriftherm.diagnostics import async_get_config_entry_diagnostics
from custom_components.thriftherm.engines.learning import COP_BAND_LEARNED_S
from custom_components.thriftherm.models import CopResult
from custom_components.thriftherm.persistence import migrate_store

from .test_integration import _entry_data, _set_states, _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def learned_band(coordinator, outdoor: float, cop: float) -> None:
    hours = COP_BAND_LEARNED_S / 3600.0 + 0.1
    coordinator.cop_map.add_interval(outdoor, 310.0 * cop * hours, 310.0 * hours, hours * 3600.0, 0.0)


def heating(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.midea", "heat", {**hass.states.get("climate.midea").attributes})


# ------------------------------------------------------------------ what decides
async def test_the_cop_of_a_short_run_does_not_decide_the_economics(hass: HomeAssistant) -> None:
    # 2026-10-31 04:35-06:45: a 2.3 from a short run chose gas, which stopped the unit, which made the next short run.
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    heating(hass)
    learned_band(coordinator, 4.0, 3.8)
    coordinator.heat_pump_tracker.cycle_cop = (time.time(), 2.3)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    data = coordinator.data
    assert data["cop"].value == 2.3 and data["cop"].cycle_cop == 2.3  # the sensor may show it
    assert float(hass.states.get("sensor.thriftherm_heat_pump_cop").state) == 2.3
    assert data["decision_cop"] == 3.8 and data["decision_cop_basis"] == "learned"
    assert data["econ"].cheaper_source == SOURCE_HEAT_PUMP
    assert data["midea_command"].reason != "gas_cheaper"
    assert coordinator.heat_pump_tracker.inefficient_since is None  # nor does it start the lockout for dear heat


async def test_a_unit_that_truly_cycles_badly_still_loses_through_the_sums_of_its_band(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    heating(hass)
    learned_band(coordinator, 4.0, 2.3)
    coordinator.heat_pump_tracker.cycle_cop = (time.time(), 2.3)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["decision_cop"] == 2.3 and coordinator.data["decision_cop_basis"] == "learned"
    assert coordinator.data["econ"].cheaper_source == SOURCE_BOILER
    assert coordinator.data["midea_command"].reason == "gas_cheaper"


async def test_a_settled_value_decides_while_there_is_one(hass: HomeAssistant, monkeypatch) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    heating(hass)
    learned_band(coordinator, 4.0, 3.8)
    tracker = coordinator.heat_pump_tracker
    settled = CopResult(
        value=3.1, raw=3.1, thermal_power_w=1000.0, electrical_power_w=310.0, airflow_m3h=400.0, samples=20, gate_reasons=()
    )

    def cop(*args, **kwargs):
        tracker.cop_settled = True
        return settled

    monkeypatch.setattr(tracker, "cop", cop)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["decision_cop"] == 3.1 and coordinator.data["decision_cop_basis"] == "measured"


async def test_a_band_counts_for_the_learning_runs_from_its_hours(hass: HomeAssistant, monkeypatch) -> None:
    from custom_components.thriftherm import coordinator as coordinator_module

    seen = []
    decide = coordinator_module.heat_pump_control.decide

    def spy(inputs, memory):
        seen.append(inputs.bin_learned)
        return decide(inputs, memory)

    monkeypatch.setattr(coordinator_module.heat_pump_control, "decide", spy)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.cop_map.add_interval(4.0, 310.0 * 3.8, 310.0, 3600.0, 0.0)
    await coordinator.async_refresh()
    assert seen[-1] is False
    learned_band(coordinator, 4.0, 3.8)
    await coordinator.async_refresh()
    assert seen[-1] is True


# ------------------------------------------------------------------ settings
async def test_a_change_of_the_duct_factor_keeps_the_table_and_scales_it(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    learned_band(entry.runtime_data, 4.0, 3.8)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "midea"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"midea_climate": "climate.midea", "midea_duct_factor": 0.5}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    coordinator = entry.runtime_data  # reloaded
    assert coordinator.cop_map.expected(4.0, 0.5) == pytest.approx(1.9, abs=0.01)
    assert coordinator.last_learning_reset is None


async def test_a_change_of_the_fan_stage_restarts_the_table(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    learned_band(coordinator, 4.0, 3.8)
    await coordinator.async_save_store(force=True)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "heat_pump_fan_stage": "high"})
    await hass.async_block_till_done()

    coordinator = entry.runtime_data  # reloaded
    assert coordinator.cop_map.bands == {} and not coordinator.cop_map.learned(4.0, 1.0)
    assert coordinator.cop_map.measured_with["fan_stage"] == "high"
    assert coordinator.last_learning_reset["reason"] == "configuration_changed"

    learned_band(coordinator, 4.0, 3.8)  # a plain restart keeps what was measured with it
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.cop_map.learned(4.0, 1.0)


async def test_the_reset_clears_the_table_and_its_old_values(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.engines.learning import CopMap

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.cop_map = CopMap.from_storage({"bins": {"2": {"count": 50, "mean": 3.7}}})
    learned_band(coordinator, 4.0, 3.8)
    await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"scope": ["heat_pump"]}, blocking=True)
    assert coordinator.cop_map.bands == {} and coordinator.cop_map.legacy == {}
    assert coordinator.cop_map.measured_with is not None
    assert coordinator.cop_map.to_storage()["bins"] == {}


# ------------------------------------------------------------------ the store
OLD_MAP = {"bins": {"2": {"count": 111, "mean": 3.73, "min": 2.278, "max": 4.9}}}


async def _with_store(hass: HomeAssistant, hass_storage, data: dict, minor: int = 1) -> MockConfigEntry:
    key = f"{STORAGE_KEY}.stored"
    hass_storage[key] = {"version": STORAGE_VERSION, "minor_version": minor, "key": key, "data": data}
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), entry_id="stored", unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_an_update_keeps_the_old_table_as_a_weak_basis(hass: HomeAssistant, hass_storage) -> None:
    entry = await _with_store(hass, hass_storage, {"cop_map": OLD_MAP, "mode": "auto"})
    coordinator = entry.runtime_data
    assert coordinator.data["expected_cop"] == 3.73 and coordinator.data["expected_cop_basis"] == "legacy"
    attributes = hass.states.get("sensor.thriftherm_heat_pump_cop").attributes
    shown = attributes["cop_map_by_outdoor_temp"]["+4..+6"]
    assert shown["basis"] == "legacy" and shown["learned"] is False and shown["cop"] == 3.73

    coordinator.cop_map.add_interval(4.0, 1000.0, 300.0, 3600.0, 1.0)
    await coordinator.async_save_store(force=True)
    await hass.async_block_till_done()
    stored = hass_storage[f"{STORAGE_KEY}.stored"]
    assert stored["version"] == STORAGE_VERSION and stored["minor_version"] == STORAGE_MINOR_VERSION == 2
    assert stored["data"]["cop_map"]["bins"] == OLD_MAP["bins"]  # nothing deleted
    assert stored["data"]["cop_map"]["bands"]["2"]["run_s"] == 3600


async def test_an_older_version_still_loads_the_store_and_reads_its_table(hass: HomeAssistant, hass_storage) -> None:
    # An older version asks for 1.1: Home Assistant refuses a store only over another major version.
    entry = await _with_store(hass, hass_storage, {"cop_map": OLD_MAP})
    await entry.runtime_data.async_save_store(force=True)
    await hass.async_block_till_done()
    data = await Store(hass, 1, f"{STORAGE_KEY}.stored").async_load()
    assert data["cop_map"]["bins"]["2"]["mean"] == 3.73


async def test_a_store_of_this_format_is_read_as_it_is(hass: HomeAssistant, hass_storage) -> None:
    band = {"heat_wh": 3800.0, "electricity_wh": 1000.0, "run_s": 4 * 3600.0, "last_ts": 1.0}
    entry = await _with_store(hass, hass_storage, {"cop_map": {"bands": {"2": band}, "bins": {}}}, minor=2)
    assert entry.runtime_data.data["expected_cop"] == 3.8 and entry.runtime_data.data["expected_cop_basis"] == "learned"


def test_the_migration():
    assert migrate_store(1, 1, {"cop_map": OLD_MAP, "mode": "auto"}) == {"cop_map": {**OLD_MAP, "bands": {}}, "mode": "auto"}
    assert migrate_store(1, 1, {"mode": "auto"}) == {"cop_map": {"bands": {}}, "mode": "auto"}
    assert migrate_store(1, 1, {"cop_map": 5}) == {"cop_map": {"bands": {}}}
    assert migrate_store(1, 1, 5) == 5  # whatever a broken disk left: read defensively later
    newer = {"cop_map": {"bands": {"2": {}}, "future": 1}}
    assert migrate_store(1, 3, newer) is newer  # written by a newer version with the same major
    with pytest.raises(NotImplementedError):
        migrate_store(2, 1, newer)


async def test_the_diagnostics_show_the_bands(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    learned_band(coordinator, 4.0, 3.8)
    await coordinator.async_refresh()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["learning"]["cop_map"]["bands"]["2"]["run_s"] > 0
    assert diag["learning"]["cop_map_by_outdoor_temp"]["+4..+6"]["cop"] == 3.8
