"""A valve that fell out of the radio network, and heat pump faults that must not stop the boiler learning.

Measured 2026-09-27: the bathroom valve went silent at 15:50 and kept 18.5 °C while Better
Thermostat reported "heat" at 21 °C. Since 2026-09-26 13:18 "midea_unavailable" alone had
paused the boiler learning for 31 hours.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines import safety
from custom_components.thriftherm.engines.room import THERMOSTAT_SILENT_S

from .conftest import PARAMS, room_config, room_state, snapshot
from .test_integration import _entry_data, _set_states, _setup

START = datetime(2026, 9, 27, 15, 0, tzinfo=dt_util.UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ------------------------------------------------------------------ room engine
def test_a_silent_valve_takes_the_room_out_of_the_boiler_demand():
    cfg = room_config("badezimmer")
    now = snapshot({}).now
    def evaluate(silent_s):
        return room_engine.evaluate_room(replace(room_state(cfg, temp=18.0), thermostat_silent_s=silent_s), now, "auto", PARAMS)

    quiet = evaluate(THERMOSTAT_SILENT_S - 60)
    assert quiet.heating_allowed and "thermostat_silent" not in quiet.issues
    silent = evaluate(THERMOSTAT_SILENT_S + 60)
    assert not silent.heating_allowed and "thermostat_silent" in silent.issues


def test_a_silent_valve_is_reported_by_the_safety_state(two_rooms):
    two_rooms["badezimmer"] = replace(two_rooms["badezimmer"], thermostat_silent_s=THERMOSTAT_SILENT_S * 2)
    snap = snapshot(two_rooms)
    rooms = {k: room_engine.evaluate_room(s, snap.now, snap.mode, PARAMS) for k, s in snap.rooms.items()}
    res = safety.evaluate(snap, rooms)
    assert res.state == "degraded" and "room_thermostat_silent:badezimmer" in res.issues


# ------------------------------------------------------------------ finding the valve behind Better Thermostat
def _register_bt_and_valve(hass: HomeAssistant) -> None:
    """climate.bath_bt from Better Thermostat, driving climate.bath_trv (a Zigbee valve with a battery sensor)."""
    registry = er.async_get(hass)
    zigbee = MockConfigEntry(domain="mqtt")
    zigbee.add_to_hass(hass)
    valve = dr.async_get(hass).async_get_or_create(config_entry_id=zigbee.entry_id, identifiers={("mqtt", "trv_bath")})
    entities = (
        ("climate", "trv", "bath_trv"), ("sensor", "trv_battery", "bath_trv_battery"), ("update", "trv_update", "bath_trv")
    )
    for domain, uid, object_id in entities:
        registry.async_get_or_create(domain, "mqtt", uid, suggested_object_id=object_id, device_id=valve.id, config_entry=zigbee)
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath_trv"]})
    bt.add_to_hass(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt", config_entry=bt)


async def test_the_valve_behind_better_thermostat_is_watched(hass: HomeAssistant, freezer) -> None:
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 18.5})
    hass.states.async_set("sensor.bath_trv_battery", "100")
    hass.states.async_set("update.bath_trv", "off")
    builder = SnapshotBuilder(hass, _entry_data())

    freezer.move_to(START + timedelta(hours=2))
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.5})  # BT itself keeps reporting
    hass.states.async_set("update.bath_trv", "on")  # the bridge refreshes this without the device
    assert builder.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(7200.0, abs=1)

    hass.states.async_set("sensor.bath_trv_battery", "99")  # the valve itself speaks again
    assert builder.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(0.0, abs=1)


async def test_a_valve_gone_unavailable_stays_silent(hass: HomeAssistant, freezer) -> None:
    # code review 2026-09-28: an unavailable valve used to count as "unknown", so a silent one
    # was taken back into the boiler demand as soon as the bridge marked it unavailable
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 18.5})
    hass.states.async_set("sensor.bath_trv_battery", "100")
    builder = SnapshotBuilder(hass, _entry_data())
    assert builder.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(0.0, abs=1)

    freezer.move_to(START + timedelta(hours=2))
    hass.states.async_set("climate.bath_trv", "unavailable")
    hass.states.async_set("sensor.bath_trv_battery", "unavailable")
    assert builder.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(7200.0, abs=1)

    # after a restart nothing was heard yet: the silence counts from the change to unavailable
    fresh = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(hours=2, minutes=30))
    assert fresh.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(1800.0, abs=1)


async def test_a_stale_room_sensor_falls_back_to_the_valve_not_to_better_thermostat(hass: HomeAssistant, freezer) -> None:
    # measured 2026-09-29: Better Thermostat repeated the silent bathroom sensor's 20.5 °C
    # for hours while the valve behind it measured the room warming to 22.5 °C
    _register_bt_and_valve(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "20.5")
    hass.states.async_set("sensor.bath_rh", "55")
    data = _entry_data()
    data["rooms"][0]["climate_entity"] = "climate.bath_bt"
    builder = SnapshotBuilder(hass, data)

    freezer.move_to(START + timedelta(hours=7))  # the room sensor's device has been silent since
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 20.5})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 23.0, "current_temperature": 22.4})
    snap = builder.build("auto", {}, {}, {}, None, {}, {})
    bath = snap.rooms["badezimmer"]
    assert not bath.temperature.valid and bath.trv_local_temp == 22.4
    result = room_engine.evaluate_room(bath, snap.now, snap.mode, PARAMS)
    assert result.temperature == 22.4 and any(i.startswith("using_trv_local_temperature") for i in result.issues)


async def test_a_plain_thermostat_is_its_own_valve(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(START)
    er.async_get(hass).async_get_or_create("climate", "mqtt", "plain", suggested_object_id="plain_trv")
    hass.states.async_set("climate.plain_trv", "heat", {"temperature": 20.0})
    builder = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(minutes=30))
    assert builder.thermostat_silent_s("climate.plain_trv", dt_util.utcnow()) == pytest.approx(1800.0, abs=1)
    assert builder.thermostat_silent_s(None, dt_util.utcnow()) is None


# ------------------------------------------------------------------ heat pump faults and the boiler learning
async def test_an_unavailable_heat_pump_does_not_pause_the_boiler_learning(hass: HomeAssistant) -> None:
    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    coordinator._last_target_change_ts = 0.0
    hass.states.async_set("sensor.midea_power", "unavailable")  # plug switched off
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # listed, but a heat pump fault alone does not degrade a boiler installation
    assert hass.states.get("sensor.thriftherm_safety_state").state == "ok"
    assert any(i.startswith("midea_") for i in hass.states.get("sensor.thriftherm_safety_state").attributes["issues"])
    assert not any(b.startswith("safety_") for b in coordinator.data["learning_blocked_by"])

    # a boiler-side fault still pauses it
    hass.states.async_set("sensor.living_temp", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "safety_degraded" in coordinator.data["learning_blocked_by"]


async def test_a_silent_valve_raises_a_repair(hass: HomeAssistant) -> None:
    from types import SimpleNamespace

    from custom_components.thriftherm import issues

    bath = replace(room_config("badezimmer"), schedule_weekday=(), schedule_weekend=(), schedule_entity="schedule.bath")
    hass.states.async_set("schedule.bath", "off")
    state = replace(room_state(bath), thermostat_silent_s=THERMOSTAT_SILENT_S * 2)
    result = room_engine.evaluate_room(state, snapshot({}).now, "auto", PARAMS)
    coordinator = SimpleNamespace(
        data={"rooms": {"badezimmer": result}}, builder=SimpleNamespace(rooms=(bath,), params=PARAMS),
        has_heat_pump=False, has_boiler=False, config={}, issue_since={},
    )
    found = issues.detect(hass, coordinator)
    assert found["thermostat_silent_badezimmer"] == {"translation_key": "thermostat_silent", "room": bath.name}


# ------------------------------------------------------------------ learning only from the room sensor
async def test_rates_are_not_learned_from_the_valve_reading(hass: HomeAssistant) -> None:
    """The valve's own reading keeps a room going without its sensor, but it sits at the radiator."""
    from custom_components.thriftherm.const import DOMAIN

    _set_states(hass)
    data = _entry_data()
    data["rooms"][0]["climate_entity"] = "climate.bath_trv"
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "current_temperature": 18.0})
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert "badezimmer" in coordinator.heat_rates.episodes  # the room sensor counts

    hass.states.async_set("sensor.bath_temp", "unavailable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["rooms"]["badezimmer"].temperature == 18.0  # the valve keeps the room going
    assert "badezimmer" not in coordinator.heat_rates.episodes
    assert "badezimmer" not in coordinator.cool_rates.episodes
