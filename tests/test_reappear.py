"""A room thermostat that comes back is no hand on its setpoint (docs/PHASE13_VALVES.md §5.3).

Measured 2026-11-13 (docs/auswertungen/2026-11-13-bt-abschalttest-bad.md):
* the bathroom valve, out of reach for 11 hours, came back restarted with a setpoint of 15 °C;
* Better Thermostat, switched on again, came back with the 21 °C it had stored while the plan said 20, and Thriftherm
  took that for a turn of the knob: a two-hour override.
A knob turned while the device was out of reach is lost that way; turned again once it is back, it counts.
"""

from __future__ import annotations

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

from .test_hand import _cycle, _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def _away_and_back(hass, coordinator, bath, freezer, target: float) -> None:
    bath._attr_available = False
    bath.async_write_ha_state()
    await _cycle(hass, coordinator, freezer, 600)
    bath._attr_available = True
    bath.at_the_device(mode=HVACMode.HEAT, target=target)
    await hass.async_block_till_done()


async def test_a_thermostat_back_with_its_factory_setpoint_gets_ours_again(hass: HomeAssistant, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    assert coordinator.room_ctrl_memory["badezimmer"].confirmed and bath.target_temperature == 21.0
    await _away_and_back(hass, coordinator, bath, freezer, 15.0)
    await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides
    await _cycle(hass, coordinator, freezer, 70)
    assert bath.target_temperature == 21.0


async def test_a_thermostat_removed_and_added_again_with_a_setpoint_of_its_own_is_no_hand(hass: HomeAssistant, freezer) -> None:
    """Better Thermostat's entry disabled and enabled: its entity is gone from the states, not unavailable."""
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    hass.states.async_remove("climate.bath")
    await _cycle(hass, coordinator, freezer, 600)
    bath.at_the_device(target=23.0)
    await hass.async_block_till_done()
    await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides


async def test_a_knob_turned_once_it_is_back_is_a_hand(hass: HomeAssistant, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    await _away_and_back(hass, coordinator, bath, freezer, 15.0)
    await _cycle(hass, coordinator, freezer, 70)
    await _cycle(hass, coordinator, freezer, 70)
    assert bath.target_temperature == 21.0
    await _cycle(hass, coordinator, freezer, 70)  # ours confirmed
    bath.at_the_device(target=23.0)
    await _cycle(hass, coordinator, freezer, 70)
    assert coordinator.overrides["badezimmer"].target == 23.0 and coordinator.overrides["badezimmer"].by == "device"


async def test_the_memory_of_another_thermostat_is_dropped(hass: HomeAssistant, freezer) -> None:
    """Review 2026-11-13: the memory stayed when the room got another thermostat in the form; a value the new one had
    differing from what the old one confirmed was taken for a hand."""
    coordinator, _, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    assert coordinator.room_ctrl_memory["badezimmer"].thermostat == "climate.bath"
    coordinator.room_ctrl_memory["badezimmer"] = RoomCtrlMemory(
        last_sent_target=30.0, last_sent_ts=0.0, confirmed=True, thermostat="climate.other"
    )
    await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides
    assert coordinator.room_ctrl_memory["badezimmer"].thermostat == "climate.bath"
    assert coordinator.room_ctrl_memory["badezimmer"].last_sent_target != 30.0


def test_the_thermostat_of_a_memory_is_stored():
    mem = RoomCtrlMemory(last_sent_target=21.0, thermostat="climate.bath")
    assert RoomCtrlMemory.from_storage(mem.to_storage()) == mem
    assert RoomCtrlMemory.from_storage({"thermostat": 5}).thermostat is None
