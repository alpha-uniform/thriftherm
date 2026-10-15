"""Drying a room by hand: the button, what ends it, and what a restart keeps of a drying."""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm import persistence
from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN
from custom_components.thriftherm.models import DryingState

from .test_integration import _entry_data, _set_states

BUTTON = "button.thriftherm_badezimmer_dry_now"
DRYING = "binary_sensor.thriftherm_badezimmer_drying_mode"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def _setup(hass: HomeAssistant, drying: bool = True):
    _set_states(hass)
    attributes = dict(hass.states.get("climate.midea").attributes)
    hass.states.async_set("climate.midea", "off", attributes)  # off, so it can heat (fan only is somebody's own mode)
    data = _entry_data()
    data[CONF_ROOMS][0]["bathroom_drying_mode"] = drying
    released = {"midea_allow_active_control": True}
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=released, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.set_control_mode("active")  # only a heat pump that really acts dries a room
    await coordinator.async_refresh()
    return coordinator


async def test_the_button_dries_a_room_at_once_and_back_to_automatic_ends_it(hass: HomeAssistant) -> None:
    coordinator = await _setup(hass)
    assert hass.states.get(DRYING).state == "off"  # 60 % at 18.5 °C: no shower in sight
    assert hass.states.get("button.thriftherm_wohnzimmer_dry_now") is None  # only where the drying mode is switched on

    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(DRYING).state == "on" and coordinator.drying_states["badezimmer"].manual
    target = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert (float(target.state), target.attributes["reason"]) == (22.0, "bathroom_drying")
    await coordinator.async_refresh()
    assert hass.states.get(DRYING).state == "on"  # dry air does not end it

    await hass.services.async_call("button", "press", {"entity_id": "button.thriftherm_back_to_automatic"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(DRYING).state == "off" and not coordinator.drying_states["badezimmer"].active


async def test_a_room_without_the_drying_mode_has_no_button(hass: HomeAssistant) -> None:
    await _setup(hass, drying=False)
    assert hass.states.get(BUTTON) is None


async def test_a_drying_keeps_its_start_its_guard_time_and_its_wait_across_a_restart(hass: HomeAssistant) -> None:
    coordinator = await _setup(hass)
    now = time.time()
    running = DryingState(True, now - 600, 9.0, manual=True)
    coordinator.drying_states = {"badezimmer": running, "wohnzimmer": DryingState()}
    stored = persistence.to_store(coordinator)
    assert list(stored["drying_states"]) == ["badezimmer"]  # nothing is written for a room that has nothing going
    coordinator.drying_states = {}
    persistence.restore(coordinator, stored)
    assert coordinator.drying_states == {"badezimmer": running}
    # one waiting for the heat pump stays one asked for with the button
    held = DryingState(False, now - 600, 9.0, manual=True)
    coordinator.drying_states = {"badezimmer": held}
    persistence.restore(coordinator, persistence.to_store(coordinator))
    assert coordinator.drying_states == {"badezimmer": held}

    guard = {"ended_ts": now - 60}
    waiting = {"wet_since_ts": now - 120, "peak_abs_humidity": 15.5, "peak_ts": now - 60, "baseline_abs_humidity": 9.0}
    broken = {"active": True, "started_ts": "x", "manual": True}  # active without a start is nothing
    persistence.restore(coordinator, {"drying_states": {"badezimmer": guard, "wohnzimmer": waiting, "gone": guard}})
    assert coordinator.drying_states == {"badezimmer": DryingState(ended_ts=now - 60), "wohnzimmer": DryingState(**waiting)}
    persistence.restore(coordinator, {"drying_states": {"badezimmer": broken, "wohnzimmer": "x"}})
    assert coordinator.drying_states == {}
