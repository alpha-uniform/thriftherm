"""The snapshot builder's rolling histories and outdoor humidity sources."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import CONF_HEAT_PUMP_OUTLET_TEMP, CONF_OUTDOOR_RH_SENSOR, CONF_WEATHER_ENTITY

from .test_integration import _entry_data, _set_states

START = datetime(2026, 1, 15, 12, 0, tzinfo=dt_util.UTC)


def _builder(hass: HomeAssistant) -> SnapshotBuilder:
    config = {
        **_entry_data(),
        CONF_HEAT_PUMP_OUTLET_TEMP: "sensor.outlet",
        CONF_OUTDOOR_RH_SENSOR: "sensor.outdoor_rh",
        CONF_WEATHER_ENTITY: "weather.home",
    }
    return SnapshotBuilder(hass, config)


def _build(builder: SnapshotBuilder):
    return builder.build("auto", overrides={}, drying_states={}, heat_rates={}, away_return_ts=None, boosts={}, room_temps={})


async def test_histories_keep_their_windows(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(START)
    _set_states(hass)
    builder = _builder(hass)
    t0 = START.timestamp()
    # minutes after the start; the last step lands exactly on the older windows' edges
    steps = [0, 10, 20, 30, 40, 100, 130, 190, 200]
    snaps = []
    for i, minutes in enumerate(steps):
        freezer.move_to(START + timedelta(minutes=minutes))
        hass.states.async_set("sensor.bath_temp", str(18.0 + i / 10))
        hass.states.async_set("sensor.bath_rh", str(50 + i))
        hass.states.async_set("sensor.outlet", str(30.0 + i))
        snaps.append(_build(builder))

    def minutes_of(history):
        return [round((entry[0] - t0) / 60) for entry in history]

    last = snaps[-1]
    bath = last.rooms["badezimmer"]
    # temperatures: two hours back (200 - 120 = 80), values as read
    assert minutes_of(bath.temperature_history) == [100, 130, 190, 200]
    assert bath.temperature_history[-1][1] == pytest.approx(18.8)
    # absolute humidity: three hours and ten minutes back (200 - 190 = 10, inclusive)
    assert minutes_of(bath.abs_humidity_history) == [10, 20, 30, 40, 100, 130, 190, 200]
    # outlet and heat pump history: half an hour, the edge itself included
    assert minutes_of(last.heat_pump.outlet_temp_history) == [190, 200]
    assert [round((s.ts - t0) / 60) for s in last.heat_pump.history] == [190, 200]
    assert last.heat_pump.outlet_temp_history[-1][1] == 38.0
    # a room without a humidity sensor collects no absolute humidity
    assert snaps[-1].rooms["wohnzimmer"].abs_humidity_history == ()
    # at 40 min the 30 min edge (10 min) is still inside
    assert minutes_of(snaps[4].heat_pump.outlet_temp_history) == [10, 20, 30, 40]

    # an invalid reading is not added, but the window still moves on
    freezer.move_to(START + timedelta(minutes=260))
    hass.states.async_set("sensor.bath_temp", "unavailable")
    hass.states.async_set("sensor.outlet", "unavailable")
    snap = _build(builder)
    assert minutes_of(snap.rooms["badezimmer"].temperature_history) == [190, 200]
    assert minutes_of(snap.heat_pump.outlet_temp_history) == []
    assert [round((s.ts - t0) / 60) for s in snap.heat_pump.history] == [260]


async def test_outdoor_humidity_sources(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.outdoor_rh", "70")
    hass.states.async_set("weather.home", "cloudy", {"temperature": 3.0, "humidity": 90})
    builder = _builder(hass)

    # the outdoor sensor works: its humidity sensor goes with it
    snap = _build(builder)
    assert (snap.outdoor_temp.value, snap.outdoor_temp_source) == (4.0, "sensor.outdoor")
    assert (snap.outdoor_rh.value, snap.outdoor_rh.valid) == (70.0, True)

    # the weather entity stands in: the humidity sensor still wins
    hass.states.async_set("sensor.outdoor", "unavailable")
    snap = _build(builder)
    assert (snap.outdoor_temp.value, snap.outdoor_temp_source) == (3.0, "weather:weather.home")
    assert snap.outdoor_rh.value == 70.0 and snap.outdoor_rh.entity_id == "sensor.outdoor_rh"

    # ... unless it fails too, then the weather's humidity is used
    hass.states.async_set("sensor.outdoor_rh", "unknown")
    snap = _build(builder)
    assert snap.outdoor_rh.value == 90.0 and snap.outdoor_rh.entity_id == "weather.home" and snap.outdoor_rh.valid

    # nothing works: the humidity sensor's own (invalid) reading is reported
    hass.states.async_set("weather.home", "unavailable", {})
    snap = _build(builder)
    assert snap.outdoor_temp.valid is False and snap.outdoor_temp_source is None
    assert snap.outdoor_temp.reason == "unavailable"
    assert snap.outdoor_rh.entity_id == "sensor.outdoor_rh" and snap.outdoor_rh.valid is False

    # the humidity sensor works again while the temperature is still missing
    hass.states.async_set("sensor.outdoor_rh", "65")
    snap = _build(builder)
    assert (snap.outdoor_rh.value, snap.outdoor_rh.valid) == (65.0, True)
