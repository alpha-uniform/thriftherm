"""Late radio echoes of Better Thermostat must not become overrides; a turn of the knob must.

Measured 2026-09-27: Better Thermostat rewrote the bathroom valve every few seconds, a
stale reply came back after the next write, and Better Thermostat adopted it as its own
target. Thriftherm turned that into two-hour overrides (07:01, 14:31, 14:34) and the
Sunday preheat was lost.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN
from custom_components.thriftherm.engines.room_control import is_echo

from .test_integration import _entry_data, _set_states

T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def history(*pairs):
    return tuple((T0 + s, v) for s, v in pairs)


# ------------------------------------------------------------------ the rule, on measured sequences
def test_measured_echoes_are_recognised():
    # 07:01: 17 -> 16 (write) -> 16.5 (write) -> 16 (late reply); BT jumps to 16 at +15 s
    assert is_echo(16.0, history((0, 17.0), (32, 16.0), (42.3, 16.5), (42.6, 16.0), (48.4, 15.0)), T0 + 47.6)
    # 14:31: 20.5 -> 20 -> 20.5, BT jumps to 20.5
    assert is_echo(20.5, history((35.9, 20.5), (45.8, 20.0), (49.05, 20.5), (49.9, 19.5)), T0 + 49.1)
    # 14:34: 19.5 -> 20 -> 19.5, BT jumps to 19.5
    assert is_echo(19.5, history((0, 19.5), (9.6, 20.0), (10.4, 19.5), (45.5, 19.5), (46.1, 18.5)), T0 + 45.5)


def test_a_turn_of_the_knob_is_not_an_echo():
    # 17 -> 16.5 -> 16 by hand; Better Thermostat then writes its calibrated 15
    assert not is_echo(16.0, history((0, 17.0), (20, 16.5), (21, 16.0), (22, 15.0)), T0 + 21.5)
    # a value last seen more than a minute ago is new again
    assert not is_echo(19.0, history((0, 19.0), (5, 19.5), (100, 19.0)), T0 + 100.5)
    # no valve history at all: nothing to compare with
    assert not is_echo(19.0, (), T0)


# ------------------------------------------------------------------ through the coordinator
def _register(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    zigbee = MockConfigEntry(domain="mqtt")
    zigbee.add_to_hass(hass)
    valve = dr.async_get(hass).async_get_or_create(config_entry_id=zigbee.entry_id, identifiers={("mqtt", "trv_bath")})
    registry.async_get_or_create(
        "climate", "mqtt", "trv", suggested_object_id="bath_trv", device_id=valve.id, config_entry=zigbee
    )
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath_trv"]})
    bt.add_to_hass(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt", config_entry=bt)


async def _setup_room_control(hass: HomeAssistant, echo_filter: bool = True):
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm",
        options={"room_allow_active_control": True, "room_echo_filter": echo_filter},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_room_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()
    return entry.runtime_data


def _valve(hass: HomeAssistant, freezer, seconds: float, setpoint: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": setpoint, "current_temperature": 20.0})


def _thermostat(hass: HomeAssistant, freezer, seconds: float, setpoint: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": setpoint, "current_temperature": 18.5})


@pytest.fixture
def start(freezer):
    freezer.move_to(datetime(2026, 9, 27, 14, 30, tzinfo=dt_util.UTC))
    return freezer


@pytest.mark.parametrize("enabled", [True, False])
async def test_an_echo_is_ignored_and_a_knob_turn_is_taken(hass: HomeAssistant, start, enabled: bool) -> None:
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    _register(hass)
    _set_states(hass)
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.5, "current_temperature": 20.0})
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 17.0, "current_temperature": 18.5})
    coordinator = await _setup_room_control(hass, echo_filter=enabled)
    assert set_temp[-1].data == {"entity_id": "climate.bath_bt", "temperature": 21.0}
    _thermostat(hass, start, 5, 21.0)  # Better Thermostat confirms
    await coordinator.async_refresh()

    # Better Thermostat calibrates: 20.5, 20, and a late 20.5 that it adopts as its target
    _valve(hass, start, 60, 20.5)
    _valve(hass, start, 10, 20.0)
    _valve(hass, start, 3, 20.5)
    _thermostat(hass, start, 0.2, 20.5)
    start.tick(timedelta(seconds=70))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    plan = hass.states.get("sensor.thriftherm_badezimmer_thermostat_plan")
    if enabled:
        assert target.attributes["reason"] != "override" and float(target.state) == 21.0
        assert plan.attributes["echo_ignored_value"] == 20.5 and plan.attributes["echo_ignored_at"]
        assert set_temp[-1].data == {"entity_id": "climate.bath_bt", "temperature": 21.0}  # ours again
    else:
        assert target.attributes["reason"] == "override" and float(target.state) == 20.5
        return

    # later somebody really turns the knob: a value the valve never had in that minute
    _thermostat(hass, start, 5, 21.0)
    await coordinator.async_refresh()
    _valve(hass, start, 120, 22.0)
    _valve(hass, start, 1, 22.5)
    _thermostat(hass, start, 0.5, 22.5)
    start.tick(timedelta(seconds=70))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert target.attributes["reason"] == "override" and float(target.state) == 22.5
