"""A valve is silent by what it last said itself, not by what a broker or Home Assistant repeated for it.

Measured 2026-11-13 (bathroom, docs/auswertungen/2026-11-13-bt-abschalttest-bad.md): the valve last reported at 11:00:01.
At 15:28 Zigbee2MQTT restarted, every entity of the valve was unavailable for 40 s and came back with its stored values.
Those counted as a report, and the room was taken for silent at 21:29 instead of 17:00. A valve that really restarts
comes back with other values (setpoint 15, external temperature 0, battery 98): that is a report, and it is back.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder

from .test_integration import _entry_data, _set_states
from .test_thermostat_silent import START, _register_bt_and_valve

BT, TRV, BATTERY = "climate.bath_bt", "climate.bath_trv", "sensor.bath_trv_battery"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _valve(hass: HomeAssistant, setpoint: float = 18.5, battery: str = "94") -> None:
    hass.states.async_set(TRV, "heat", {"temperature": setpoint})
    hass.states.async_set(BATTERY, battery)


def _gone(hass: HomeAssistant) -> None:
    hass.states.async_set(TRV, "unavailable")
    hass.states.async_set(BATTERY, "unavailable")


def _silent(builder: SnapshotBuilder) -> float:
    return builder.thermostat_silent_s(BT, dt_util.utcnow())


async def _heard_then_restarted_broker(hass: HomeAssistant, freezer, **back) -> SnapshotBuilder:
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set(BT, "heat", {"temperature": 21.0})
    _valve(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    assert _silent(builder) == pytest.approx(0.0, abs=1)

    freezer.move_to(START + timedelta(hours=4, minutes=28))  # the broker restarts
    _gone(hass)
    assert _silent(builder) == pytest.approx(4 * 3600 + 28 * 60, abs=1)
    freezer.tick(timedelta(seconds=40))
    _valve(hass, **back)
    return builder


async def test_values_repeated_after_a_broker_restart_are_no_report(hass: HomeAssistant, freezer) -> None:
    builder = await _heard_then_restarted_broker(hass, freezer)
    assert _silent(builder) == pytest.approx(4 * 3600 + 28 * 60 + 40, abs=1)
    freezer.tick(timedelta(seconds=30))  # still within the burst: one more repetition is no report either
    hass.states.async_set(BATTERY, "94", force_update=True)
    assert _silent(builder) == pytest.approx(4 * 3600 + 29 * 60 + 10, abs=1)

    # six hours after the valve last spoke itself, not after the restart
    freezer.move_to(START + timedelta(hours=6, minutes=1))
    assert _silent(builder) > 6 * 3600


async def test_a_report_after_the_repetition_counts_again(hass: HomeAssistant, freezer) -> None:
    builder = await _heard_then_restarted_broker(hass, freezer)
    freezer.tick(timedelta(minutes=10))
    hass.states.async_set(BATTERY, "94", force_update=True)  # the valve reports, the same value as before
    assert _silent(builder) == pytest.approx(0.0, abs=1)


async def test_a_valve_that_restarted_comes_back_with_other_values(hass: HomeAssistant, freezer) -> None:
    builder = await _heard_then_restarted_broker(hass, freezer, setpoint=15.0, battery="98")
    assert _silent(builder) == pytest.approx(0.0, abs=1)


async def test_values_that_differ_later_in_the_burst_are_a_report(hass: HomeAssistant, freezer) -> None:
    """The entities come back one by one; the first ones as before, a later one other: the valve did restart."""
    builder = await _heard_then_restarted_broker(hass, freezer)
    assert _silent(builder) > 3600
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(TRV, "heat", {"temperature": 15.0})
    assert _silent(builder) == pytest.approx(0.0, abs=1)


async def test_a_long_absence_is_no_repetition(hass: HomeAssistant, freezer) -> None:
    """Gone for more than ten minutes, a valve that comes back with the same values has spoken: a broker restart is over
    in a minute or two; a valve out of reach for an hour that answers again is heard."""
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set(BT, "heat", {"temperature": 21.0})
    _valve(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    _silent(builder)
    freezer.tick(timedelta(hours=2))
    _gone(hass)
    _silent(builder)
    freezer.tick(timedelta(minutes=11))
    _valve(hass)
    assert _silent(builder) == pytest.approx(0.0, abs=1)


async def test_what_was_heard_outlasts_a_restart_of_home_assistant(hass: HomeAssistant, freezer) -> None:
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set(BT, "heat", {"temperature": 21.0})
    _valve(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    _silent(builder)
    stored = builder.valve_memory()

    freezer.move_to(START + timedelta(hours=3))  # Home Assistant restarts; the broker repeats what it had
    _gone(hass)
    _valve(hass)
    fresh = SnapshotBuilder(hass, _entry_data())
    fresh.restore_valve_memory(stored, dt_util.utcnow().timestamp())
    assert _silent(fresh) == pytest.approx(3 * 3600, abs=1)
    assert SnapshotBuilder(hass, _entry_data()).thermostat_silent_s(BT, dt_util.utcnow()) == pytest.approx(0.0, abs=1)


async def test_a_damaged_memory_is_none() -> None:
    builder = SnapshotBuilder.__new__(SnapshotBuilder)
    SnapshotBuilder._init_valve_memory(builder)
    builder.restore_valve_memory({"climate.x": {"heard": "nonsense", "values": 3}, "climate.y": 5}, 1.0)
    assert builder.valve_memory() == {}


async def test_the_store_keeps_what_was_heard_of_the_valves(hass: HomeAssistant, hass_storage, freezer) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm import persistence
    from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN

    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set(BT, "heat", {"temperature": 21.0})
    _valve(hass)
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = BT
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    stored = persistence.to_store(coordinator)
    assert stored["valve_memory"][BT]["heard"] == pytest.approx(START.timestamp(), abs=1)

    freezer.move_to(START + timedelta(hours=3))
    coordinator.builder = SnapshotBuilder(hass, data)  # as after a restart
    persistence.restore(coordinator, stored)
    assert coordinator.builder.thermostat_silent_s(BT, dt_util.utcnow()) == pytest.approx(3 * 3600, abs=1)
