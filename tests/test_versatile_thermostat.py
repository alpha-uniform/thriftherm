"""Versatile Thermostat as the room thermostat: its own rules are no turn of the knob."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.thriftherm.adapters.inputs import applies_own_target
from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN
from custom_components.thriftherm.engines.room_control import RoomCtrlMemory, decide

from .test_integration import _entry_data, _set_states
from .test_room_control import NOW, rr


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# what Versatile Thermostat publishes (documentation/en/reference.md), reduced to what is read
def vtherm(in_force: float, requested: float, **managers) -> dict:
    return {
        "temperature": in_force,
        "current_temperature": 18.5,
        "current_state": {"hvac_mode": "heat", "target_temperature": in_force, "preset": "none"},
        "requested_state": {"hvac_mode": "heat", "target_temperature": requested, "preset": "none"},
        **managers,
    }


def test_a_target_that_differs_from_the_requested_one_is_the_thermostats_own():
    assert applies_own_target(vtherm(17.0, 21.0)) is True  # window open: eco temperature in force
    assert applies_own_target(vtherm(21.0, 21.0)) is False
    assert applies_own_target({"temperature": 21.0}) is False  # Better Thermostat, a plain valve: no such attributes


@pytest.mark.parametrize(
    ("manager", "own"),
    [
        ({"window_manager": {"window_state": "on", "is_window_bypass": False}}, True),
        ({"window_manager": {"window_state": "on", "is_window_bypass": True}}, False),
        ({"window_manager": {"window_state": "off", "window_auto_state": "on"}}, True),
        ({"power_manager": {"overpowering_state": "on"}}, True),
        ({"safety_manager": {"safety_state": "on"}}, True),
        ({"window_manager": {"window_state": "off"}, "safety_manager": {"safety_state": "off"}}, False),
        ({"window_manager": "broken"}, False),
    ],
)
def test_the_managers_that_hold_a_target_of_their_own(manager: dict, own: bool):
    assert applies_own_target(vtherm(21.0, 21.0, **manager)) is own


def test_nothing_is_written_against_the_thermostats_own_target():
    mem = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 3600, confirmed=True)
    cmd, after = decide("bad", "active", rr(target=21.0), True, "heat", 17.0, NOW, mem, own_target=True)
    assert (cmd.plan, cmd.send, cmd.reason) == ("hold", False, "thermostat_own_rule") and after == mem
    cmd, _ = decide("bad", "active", rr(target=21.0), True, "heat", 17.0, NOW, mem)
    assert cmd.send is True  # a plain thermostat that shows 17 gets our 21 again


# ------------------------------------------------------------------ through the coordinator
def _register(hass: HomeAssistant, stored: dict) -> None:
    registry = er.async_get(hass)
    zigbee = MockConfigEntry(domain="mqtt")
    zigbee.add_to_hass(hass)
    valve = dr.async_get(hass).async_get_or_create(config_entry_id=zigbee.entry_id, identifiers={("mqtt", "trv_bath")})
    on_valve = {"device_id": valve.id, "config_entry": zigbee}
    registry.async_get_or_create("climate", "mqtt", "trv", suggested_object_id="bath_trv", **on_valve)
    registry.async_get_or_create("sensor", "mqtt", "trv_battery", suggested_object_id="bath_trv_battery", **on_valve)
    vt = MockConfigEntry(domain="versatile_thermostat", data=stored)
    vt.add_to_hass(hass)
    registry.async_get_or_create("climate", "versatile_thermostat", "vt_bath", suggested_object_id="bath_vt", config_entry=vt)


async def _setup_room_control(hass: HomeAssistant):
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_vt"
    released = {"room_allow_active_control": True}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm", options=released)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    active = {"entity_id": "select.thriftherm_room_control", "option": "active"}
    await hass.services.async_call("select", "select_option", active, blocking=True)
    await hass.async_block_till_done()
    return entry.runtime_data


@pytest.mark.parametrize("stored", [{"underlying_entity_ids": ["climate.bath_trv"]}, {"climate_entity_id": "climate.bath_trv"}])
async def test_the_valves_behind_versatile_thermostat_are_found(hass: HomeAssistant, stored: dict) -> None:
    _register(hass, stored)
    _set_states(hass)
    hass.states.async_set("climate.bath_vt", "heat", vtherm(17.0, 17.0))
    coordinator = await _setup_room_control(hass)
    assert set(coordinator.builder.valves_behind("climate.bath_vt")) == {"climate.bath_trv", "sensor.bath_trv_battery"}


async def test_an_open_window_in_versatile_thermostat_is_no_turn_of_the_knob(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(datetime(2026, 9, 27, 14, 30, tzinfo=dt_util.UTC))
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    _register(hass, {"underlying_entity_ids": ["climate.bath_trv"]})
    _set_states(hass)
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "current_temperature": 20.0})
    hass.states.async_set("climate.bath_vt", "heat", vtherm(17.0, 17.0))
    coordinator = await _setup_room_control(hass)
    assert set_temp[-1].data == {"entity_id": "climate.bath_vt", "temperature": 21.0}
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set("climate.bath_vt", "heat", vtherm(21.0, 21.0))  # confirmed
    await coordinator.async_refresh()
    sent = len(set_temp)

    # its window rule puts the eco temperature in force; what was requested stays 21
    freezer.tick(timedelta(minutes=10))
    window = {"window_manager": {"window_state": "on", "is_window_bypass": False}}
    hass.states.async_set("climate.bath_vt", "heat", vtherm(17.0, 21.0, **window))
    await coordinator.async_refresh()
    assert "badezimmer" not in coordinator.overrides  # no override for two hours at 17 °C
    assert coordinator.data["room_commands"]["badezimmer"].reason == "thermostat_own_rule"
    assert len(set_temp) == sent  # and nothing written against it

    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("climate.bath_vt", "heat", vtherm(21.0, 21.0))  # window shut: it restores the 21 itself
    await coordinator.async_refresh()
    assert coordinator.data["room_commands"]["badezimmer"].reason == "in_sync" and len(set_temp) == sent

    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("climate.bath_vt", "heat", vtherm(22.5, 22.5))  # somebody asks for 22.5: that is a hand
    await coordinator.async_refresh()
    assert coordinator.overrides["badezimmer"].target == 22.5
