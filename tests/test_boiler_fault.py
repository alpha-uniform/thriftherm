"""A boiler far above what it was asked for (issue #5): shown as it is, warned about, and no new release for a while."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm.boiler_profiles import PROFILES
from custom_components.thriftherm.const import BOILER_REPORTS_FAULT, DOMAIN
from custom_components.thriftherm.engines import boiler as boiler_engine
from custom_components.thriftherm.engines import boiler_control as bc
from custom_components.thriftherm.engines import boiler_memory as bm
from custom_components.thriftherm.engines.boiler import BoilerFault, no_new_release, overheated, watch_fault

from .test_boiler_control import NOW, inp, rr
from .test_coordinator_actions import _running_unit
from .test_integration import _setup

RELEASED = {"midea_allow_active_control": True, "boiler_allow_active_control": True, "room_allow_active_control": True}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.mark.parametrize("code", [53, 54, 96, 98])
def test_the_codes_of_a_fault_are_told_apart_and_s97_before_every_start_is_not(code):
    profile = PROFILES["vaillant_bai"]
    assert profile.reported_mode(code) == BOILER_REPORTS_FAULT
    assert profile.reported_mode(97) is None


def test_a_fault_code_is_never_taken_for_hot_water():
    """29.10.: S.98 with a flow of 99 °C and no gas meter showed "hot water" for ten minutes, and paused the learning."""
    assert not bc.hot_water_suspected(99.0, 69.9, 60.0, reported_mode=BOILER_REPORTS_FAULT, status_known=True)
    assert not bc.hot_water_suspected(99.0, 69.9, 60.0, heating_flow_c=45.0, reported_mode=BOILER_REPORTS_FAULT, status_known=True)
    assert bc.hot_water_suspected(99.0, 69.9, 60.0, reported_mode=None, status_known=True)  # the rule itself stands


def test_a_flow_of_90_or_a_wait_for_water_holds_back_a_new_release_for_half_an_hour():
    fault = watch_fault(BoilerFault(), 89.9, False, NOW)
    assert not no_new_release(fault, NOW)
    fault = watch_fault(fault, 90.0, False, NOW)
    assert no_new_release(fault, NOW) and no_new_release(fault, NOW + 1799) and not no_new_release(fault, NOW + 1800)
    assert no_new_release(watch_fault(BoilerFault(), 40.0, True, NOW), NOW)  # S.53 at a cool flow
    assert not overheated(fault, NOW)  # 90 °C is no alarm: a knob at 85 °C overshoots that far


def test_a_stuck_reading_holds_back_a_release_for_four_hours_at_most():
    """A single failing sensor never disables heating for good (engines/safety.py)."""
    fault, now = BoilerFault(), NOW
    for _ in range(4 * 60 + 2):
        fault = watch_fault(fault, 113.0, False, now)
        hold = no_new_release(fault, now)
        now += 60.0
    assert not hold
    assert overheated(fault, now)  # it stays reported: nobody is to think it went away


def test_the_warning_shows_the_highest_flow_for_a_day_after_the_last_such_reading():
    fault = watch_fault(BoilerFault(), 106.4, False, NOW)
    fault = watch_fault(fault, 113.25, False, NOW + 22)
    fault = watch_fault(fault, 99.0, False, NOW + 60)
    assert (fault.overheat_c, fault.overheat_ts) == (113.25, NOW + 22)
    assert overheated(fault, NOW + 22 + 86399) and not overheated(fault, NOW + 22 + 86400)
    lower = watch_fault(fault, 105.0, False, NOW + 82800)  # a lower one the next day keeps it going, with the peak shown
    assert (lower.overheat_c, lower.overheat_ts) == (113.25, NOW + 22) and overheated(lower, NOW + 82800 + 86399)
    later = watch_fault(fault, 101.0, False, NOW + 90000)
    assert later.overheat_c == 101.0  # a new event, not the old peak


def test_a_fault_survives_a_restart_and_rubbish_in_the_store_does_not():
    fault = watch_fault(BoilerFault(), 113.25, False, NOW)
    assert BoilerFault.from_storage(fault.to_storage()) == fault
    assert BoilerFault.from_storage(None) == BoilerFault()
    assert BoilerFault.from_storage({"overheat_c": "hot", "since_ts": True}) == BoilerFault()
    half = BoilerFault.from_storage({"overheat_ts": NOW, "overheat_c": None, "overheat_last_ts": NOW})
    assert not overheated(half, NOW) and watch_fault(half, 105.0, False, NOW).overheat_c == 105.0  # no crash


def test_the_hold_keeps_a_block_but_does_not_take_back_a_release():
    """Blocking in the middle of it does not help: on 29.10. the boiler fired after over an hour of disablehc=1."""
    room = rr()
    blocked = replace(bm.BoilerMemory(), state="block", state_since_ts=NOW - 3600)
    cmd, mem = bc.decide(replace(inp([room], mode="active"), no_new_release=True), blocked)
    assert (cmd.plan, cmd.reason, mem.state) == ("block", "boiler_fault_hold", "block")
    released = replace(bm.BoilerMemory(), state="heat", state_since_ts=NOW - 3600)
    cmd, mem = bc.decide(replace(inp([room], mode="active"), no_new_release=True), released)
    assert (cmd.plan, mem.state) == ("heat", "heat")
    cmd, _ = bc.decide(inp([room], mode="active"), blocked)
    assert cmd.plan == "heat"  # without the hold it goes as ever


def test_a_hold_does_not_release_after_a_gap_either():
    """After hands off the plan in force is forgotten: the first plan would have been a release at once."""
    cmd, mem = bc.decide(replace(inp([rr()], mode="active"), no_new_release=True), bm.BoilerMemory())
    assert (cmd.plan, mem.state) == ("block", "block")


def test_frost_goes_before_the_hold():
    blocked = replace(bm.BoilerMemory(), state="block", state_since_ts=NOW - 3600)
    cmd, _ = bc.decide(replace(inp([rr()], mode="active", frost=("wohnzimmer",)), no_new_release=True), blocked)
    assert cmd.plan == "heat"


async def test_a_flow_of_113_is_read_warned_about_and_kept_over_a_restart(hass: HomeAssistant) -> None:
    async_mock_service(hass, "mqtt", "publish")
    entry = await _setup(hass, options=RELEASED)
    coordinator = entry.runtime_data
    hass.states.async_set("sensor.flow", "113.25", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    data = coordinator.data
    assert data["boiler"].available is True  # it was "data missing", and the boiler went to its knob
    assert "boiler_overheated" in data["safety"].issues and data["safety"].state == "degraded"
    assert not any(i.startswith("boiler_flow_temp_invalid") for i in data["safety"].issues)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "thriftherm_boiler_overheated")
    assert issue is not None and issue.translation_placeholders["flow"] == "113"
    assert issue.translation_placeholders["time"]

    hass.states.async_set("sensor.flow", "45.0", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    assert "boiler_overheated" in coordinator.data["safety"].issues  # a day long

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.boiler_fault.overheat_c == 113.25
    assert no_new_release(entry.runtime_data.boiler_fault, entry.runtime_data.boiler_fault.seen_ts)


async def test_learning_is_held_back_during_a_fault(hass: HomeAssistant) -> None:
    async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    assert "boiler_fault" in coordinator.data["learning_blocked_by"]
    assert boiler_engine.no_new_release(coordinator.boiler_fault, coordinator.boiler_fault.seen_ts)


async def test_a_spike_between_two_cycles_is_still_seen_at_its_peak(hass: HomeAssistant) -> None:
    """01.11.: 112.94 °C came 160 ms after one cycle and was gone before the next, which saw 96 and 94 °C: no warning at all."""
    async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    await coordinator.async_refresh()
    for value in ("96.25", "112.94", "93.75"):
        hass.states.async_set("sensor.flow", value, {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
    assert coordinator.boiler_fault.overheat_c == 112.94
    await coordinator.async_refresh()
    assert "boiler_overheated" in coordinator.data["safety"].issues
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "thriftherm_boiler_overheated")
    assert issue.translation_placeholders["flow"] == "113"


async def test_a_short_wait_for_water_between_two_cycles_is_seen(hass: HomeAssistant) -> None:
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set("sensor.statenumber", "31")
    options = {**RELEASED, "boiler_state_number": "sensor.statenumber"}
    coordinator = (await _setup(hass, options=options)).runtime_data
    await coordinator.async_refresh()
    assert coordinator.boiler_fault == BoilerFault()
    hass.states.async_set("sensor.statenumber", "53")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.statenumber", "3")
    await hass.async_block_till_done()
    assert coordinator.boiler_fault.seen_ts is not None


async def test_the_store_is_not_written_every_cycle_while_a_sign_lasts(hass: HomeAssistant, freezer) -> None:
    async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    coordinator._store_dirty = False
    freezer.tick(60)
    await coordinator.async_refresh()
    assert coordinator.boiler_fault.seen_ts is not None and not coordinator._store_dirty


async def test_during_a_hold_the_heat_pump_heats_its_rooms_even_if_gas_is_cheaper(hass: HomeAssistant) -> None:
    """Review: the boiler held and the unit resting for gas cheaper left the rooms with neither."""
    coordinator = await _running_unit(hass, "boiler_helps", electricity_price=2.0)
    hass.states.async_set("sensor.bath_temp", "19.0")
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    coordinator.boiler_memory = replace(bm.BoilerMemory(), state="block", state_since_ts=0.0)
    await coordinator.async_refresh()
    assert coordinator.data["boiler_command"].reason == "boiler_fault_hold"
    command = coordinator.data["midea_command"]
    assert (command.plan, command.reason, command.delivers) == ("heat", "boiler_fault_hold", True)
    assert command.target_temp != 17.0  # no rest for gas cheaper


@pytest.mark.parametrize("released", [False, True])
async def test_only_a_boiler_really_held_back_makes_the_heat_pump_heat_at_any_price(hass: HomeAssistant, released: bool) -> None:
    coordinator = await _running_unit(hass, "boiler_helps", electricity_price=2.0)
    hass.states.async_set("sensor.bath_temp", "19.0")
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    state = "heat" if released else "block"
    coordinator.boiler_memory = replace(bm.BoilerMemory(), state=state, state_since_ts=0.0)
    await coordinator.async_refresh()
    assert (coordinator.data["midea_command"].reason == "boiler_fault_hold") is not released


async def test_a_boiler_in_shadow_is_not_held_back_so_the_heat_pump_goes_by_price(hass: HomeAssistant) -> None:
    coordinator = await _running_unit(hass, "boiler_helps", electricity_price=2.0)
    coordinator.set_boiler_control_mode("shadow")
    hass.states.async_set("sensor.bath_temp", "19.0")
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].reason != "boiler_fault_hold"


async def test_a_lockout_for_dear_heat_does_not_leave_the_rooms_with_neither_source_during_a_hold(hass: HomeAssistant) -> None:
    coordinator = await _running_unit(hass, "boiler_helps", electricity_price=2.0)
    hass.states.async_set("sensor.bath_temp", "19.0")
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    coordinator.boiler_memory = replace(bm.BoilerMemory(), state="block", state_since_ts=0.0)
    await coordinator.async_refresh()
    coordinator.heat_pump_tracker.inefficient_since = time.time() - 7200
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].plan == "heat"


@pytest.mark.parametrize("state", ["nan", "inf", "-inf", "1e400"])
async def test_a_status_that_is_no_number_is_ignored_between_cycles(hass: HomeAssistant, state: str, caplog) -> None:
    hass.states.async_set("sensor.statenumber", "31")
    coordinator = (await _setup(hass, options={**RELEASED, "boiler_state_number": "sensor.statenumber"})).runtime_data
    hass.states.async_set("sensor.statenumber", state)
    await hass.async_block_till_done()
    assert coordinator.boiler_fault == BoilerFault() and "Error while dispatching" not in caplog.text


async def test_without_an_own_boiler_no_signs_are_noted(hass: HomeAssistant) -> None:
    coordinator = (await _setup(hass, options={**RELEASED, "system_type": "district"})).runtime_data
    hass.states.async_set("sensor.flow", "95.0", {"unit_of_measurement": "°C"})
    await coordinator.async_refresh()
    assert coordinator.boiler_fault == BoilerFault()
