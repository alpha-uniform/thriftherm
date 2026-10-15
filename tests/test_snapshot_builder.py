"""The snapshot builder's rolling histories and outdoor humidity sources."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import CONF_HEAT_PUMP_OUTLET_TEMP, CONF_OUTDOOR_RH_SENSOR, CONF_WEATHER_ENTITY
from custom_components.thriftherm.engines import room as room_engine

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


async def test_fast_cycles_do_not_shrink_the_room_histories(hass: HomeAssistant, freezer) -> None:
    # code review 2026-09-28: the gas meter starts a cycle every 10 s while the burner runs;
    # the buffers were sized for one cycle a minute and dropped the older part of the trend
    freezer.move_to(START)
    _set_states(hass)
    builder = _builder(hass)
    t0 = START.timestamp()
    for i in range(2 * 360 + 1):  # two hours of 10 s cycles
        freezer.move_to(START + timedelta(seconds=10 * i))
        hass.states.async_set("sensor.bath_temp", str(18.0 + i / 1000))
        snap = _build(builder)
    temps = snap.rooms["badezimmer"].temperature_history
    assert (temps[-1][0] - temps[0][0]) / 60 >= 115  # two hours, not 40 minutes
    assert all(b[0] - a[0] >= 30 for a, b in zip(temps, temps[1:]))
    humid = snap.rooms["badezimmer"].abs_humidity_history
    assert (humid[-1][0] - t0) / 60 == pytest.approx(120, abs=1) and humid[0][0] == t0


async def test_a_value_that_is_not_finite_is_no_reading(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.adapters.inputs import whole_number

    builder = _builder(hass)
    hass.states.async_set("climate.x", "heat", {"current_temperature": "nan", "temperature": "inf"})
    state = hass.states.get("climate.x")
    assert builder.read_attr_float(state, "current_temperature") is None
    assert builder.read_attr_float(state, "temperature") is None
    assert whole_number("inf") is None and whole_number("8.0") == 8


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


def _register_bath_sensor(hass: HomeAssistant) -> None:
    """Temperature and humidity of the bathroom as two sensors of one device."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="zigbee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("zigbee", "t_h_bath")})
    registry = er.async_get(hass)
    for uid, object_id in (("temp", "bath_temp"), ("rh", "bath_rh")):
        registry.async_get_or_create(
            "sensor", "zigbee", uid, suggested_object_id=object_id, device_id=device.id, config_entry=entry
        )


async def test_a_quiet_temperature_holds_while_its_device_reports(hass: HomeAssistant, freezer) -> None:
    # 2026-09-23: the living room stayed at 20.0 °C for eight hours, only the humidity moved
    _register_bath_sensor(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "20.0")
    builder = _builder(hass)

    freezer.move_to(START + timedelta(hours=7))
    hass.states.async_set("sensor.bath_rh", "51")
    reading = _build(builder).rooms["badezimmer"].temperature
    assert reading.valid and reading.value == 20.0 and reading.reason is None

    # the whole device fell silent: stale as before
    freezer.move_to(START + timedelta(hours=14))
    reading = _build(builder).rooms["badezimmer"].temperature
    assert not reading.valid and reading.reason == "stale"

    # humidity keeps coming, but a temperature a day old is not trusted any more
    freezer.move_to(START + timedelta(hours=25))
    hass.states.async_set("sensor.bath_rh", "52")
    reading = _build(builder).rooms["badezimmer"].temperature
    assert not reading.valid and reading.reason == "stale"


async def test_a_quiet_temperature_without_a_device_stays_stale(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "20.0")
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=7))
    hass.states.async_set("sensor.bath_rh", "51")
    reading = _build(builder).rooms["badezimmer"].temperature
    assert not reading.valid and reading.reason == "stale"


async def test_an_unchanged_flow_temperature_holds_while_the_ebus_signal_is_configured(hass: HomeAssistant, freezer) -> None:
    # measured 2026-10-03: a cold boiler kept 22.25 °C from 08:19 to 14:32; ebusd publishes only
    # changes, the six-hour bound took the boiler data for lost and the knob took over and fired
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.flow", "22.25")
    hass.states.async_set("sensor.return", "22.0")
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=7))
    hass.states.async_set("binary_sensor.ebus", "on", force_update=True)
    boiler = _build(builder).boiler
    assert boiler.flow_temp.valid and boiler.flow_temp.value == 22.25 and boiler.return_temp.valid

    config = {**_entry_data(), "boiler_signal": None}  # no signal sensor: the age bound stays
    assert not SnapshotBuilder(hass, config).build("auto", {}, {}, {}, None, {}, {}).boiler.flow_temp.valid


async def test_a_gas_flow_left_hanging_is_no_reading(hass: HomeAssistant, freezer) -> None:
    # code audit 2026-09-28 (F15): the flow had no age limit, so a meter that dropped off with a
    # flow above 0 meant "burning" for good: a hot water guess and a learning pause
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.gas_volume", "1000.00")
    builder = SnapshotBuilder(hass, {**_entry_data(), "gas_volume": "sensor.gas_volume"})
    # a long burn at one rate: the flow stays 1.2 for an hour, the meter reading moves
    for minutes in range(5, 65, 5):
        freezer.move_to(START + timedelta(minutes=minutes))
        hass.states.async_set("sensor.gas_volume", str(round(1000 + minutes / 50, 2)))
    boiler = _build(builder).boiler
    assert boiler.gas_flow_m3h.valid and boiler.gas_flow_m3h.value == 1.2

    # the meter dropped off: neither value changes any more
    freezer.move_to(START + timedelta(minutes=71))
    boiler = _build(builder).boiler
    assert not boiler.gas_flow_m3h.valid and boiler.gas_flow_m3h.reason == "stale"

    # an idle 0 stands for hours and still counts
    hass.states.async_set("sensor.gas_flow", "0")
    freezer.move_to(START + timedelta(hours=8))
    boiler = _build(builder).boiler
    assert boiler.gas_flow_m3h.valid and boiler.gas_flow_m3h.value == 0.0


async def test_a_schedule_block_to_midnight_has_no_comfort_end(hass: HomeAssistant, freezer) -> None:
    """A block to 24:00 means "all day": midnight would end every boiler call at 23:30 (found in the code 2026-10-09)."""
    now = dt_util.as_local(START).replace(hour=23, minute=40)
    freezer.move_to(now)
    _set_states(hass)
    config = _entry_data()
    config["rooms"][0]["schedule_entity"] = "schedule.bath"
    builder = SnapshotBuilder(hass, config)

    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0)
    hass.states.async_set("schedule.bath", "on", {"next_event": midnight.isoformat()})
    schedule = _build(builder).rooms["badezimmer"].schedule
    assert schedule.active and schedule.next_end is None

    evening = now.replace(hour=23, minute=55)
    hass.states.async_set("schedule.bath", "on", {"next_event": evening.isoformat()})
    assert _build(builder).rooms["badezimmer"].schedule.next_end == evening


async def test_a_gas_flow_without_a_meter_reading_goes_by_its_own_age(hass: HomeAssistant, freezer) -> None:
    # found in the code 2026-10-13: with no meter reading configured the check was skipped, and a
    # flow of 1.2 m³/h still meant "burning" ten hours after its last report
    freezer.move_to(START)
    _set_states(hass)
    builder = SnapshotBuilder(hass, _entry_data())  # a gas flow, no meter reading
    freezer.move_to(START + timedelta(minutes=9))
    assert _build(builder).boiler.gas_flow_m3h.valid
    freezer.move_to(START + timedelta(hours=10))
    flow = _build(builder).boiler.gas_flow_m3h
    assert not flow.valid and flow.reason == "stale"
    hass.states.async_set("sensor.gas_flow", "1.3")
    assert _build(builder).boiler.gas_flow_m3h.valid


async def test_the_weather_gives_the_humidity_beside_a_working_outdoor_sensor(hass: HomeAssistant, freezer) -> None:
    # found in the code 2026-10-13: the weather's humidity was only used together with its temperature
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("weather.home", "cloudy", {"temperature": 3.0, "humidity": 90})
    snap = _build(SnapshotBuilder(hass, {**_entry_data(), CONF_WEATHER_ENTITY: "weather.home"}))  # no humidity sensor
    assert (snap.outdoor_temp.value, snap.outdoor_temp_source) == (4.0, "sensor.outdoor")
    assert (snap.outdoor_rh.value, snap.outdoor_rh.entity_id, snap.outdoor_rh.valid) == (90.0, "weather.home", True)


async def test_the_limits_of_a_room_thermostat_are_read(hass: HomeAssistant) -> None:
    _set_states(hass)
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "min_temp": 8, "max_temp": 28})
    config = _entry_data()
    config["rooms"][0]["climate_entity"] = "climate.bath_trv"
    rooms = _build(SnapshotBuilder(hass, config)).rooms
    assert (rooms["badezimmer"].thermostat_min_temp, rooms["badezimmer"].thermostat_max_temp) == (8.0, 28.0)
    assert (rooms["wohnzimmer"].thermostat_min_temp, rooms["wohnzimmer"].thermostat_max_temp) == (None, None)


BERLIN = dt_util.get_time_zone("Europe/Berlin")


async def _rooms_with_schedules(hass: HomeAssistant, freezer, now: datetime, bath: list[dict], living: list[dict]):
    """Both rooms on a schedule helper of their own, each with the same blocks every day."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(now)
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    helpers = {key: {"name": key, **dict.fromkeys(days, blocks)} for key, blocks in (("bath", bath), ("living", living))}
    assert await async_setup_component(hass, "schedule", {"schedule": helpers})
    await hass.async_block_till_done()
    _set_states(hass)
    config = _entry_data()
    config["rooms"][0]["schedule_entity"] = "schedule.bath"
    config["rooms"][1]["schedule_entity"] = "schedule.living"
    snap = _build(SnapshotBuilder(hass, config))
    for helper in ("schedule.bath", "schedule.living"):  # each keeps a timer for its next block
        await hass.data[DATA_INSTANCES]["schedule"].get_entity(helper).async_remove()
    return snap, snap.rooms["badezimmer"], snap.rooms["wohnzimmer"]


def _block(start: str, end: str, temperature: float | None = None) -> dict:
    return {"from": start, "to": end, **({} if temperature is None else {"data": {"temperature": temperature}})}


async def test_schedule_blocks_that_follow_each_other_are_one_comfort_time(hass: HomeAssistant, freezer) -> None:
    # found in the code 2026-10-13: 06:00-12:00 at 20 °C and 12:00-22:00 at 21 °C; 12:00 counted as
    # the comfort end, so the room did not call the boiler from 11:30 to 12:00 every day
    tuesday = datetime(2026, 10, 13, 11, 40, tzinfo=BERLIN)
    warmer = [_block("06:00:00", "12:00:00", 20), _block("12:00:00", "22:00:00", 21)]
    colder = [_block("06:00:00", "12:00:00"), _block("12:00:00", "22:00:00", 17)]
    snap, bath, living = await _rooms_with_schedules(hass, freezer, tuesday, warmer, colder)
    assert bath.schedule.active and bath.schedule.temperature == 20.0
    assert bath.schedule.next_end == tuesday.replace(hour=22, minute=0)
    assert room_engine.evaluate_room(bath, snap.now, "auto", snap.params).comfort_end_ts == bath.schedule.next_end.timestamp()
    # a block that asks for less than the one before it does end the comfort
    assert living.schedule.active and living.schedule.next_end == tuesday.replace(hour=12, minute=0)


async def test_a_block_to_2359_goes_on_into_the_next_days_block_at_midnight(hass: HomeAssistant, freezer) -> None:
    """Test of 2026-10-15: two days of 00:00-23:59 ended the comfort at 23:59; the room stopped calling at 23:29."""
    wednesday = datetime(2026, 10, 14, 23, 20, tzinfo=BERLIN)
    all_day = [_block("00:00:00", "23:59:00")]
    evening = [_block("17:30:00", "23:59:00")]
    snap, bath, living = await _rooms_with_schedules(hass, freezer, wednesday, all_day, evening)
    assert bath.schedule.active and bath.schedule.next_end is None  # comfort all week
    assert room_engine.evaluate_room(bath, snap.now, "auto", snap.params).comfort_end_ts is None
    assert living.schedule.next_end == wednesday.replace(hour=23, minute=59)  # no block the next morning: it ends


async def test_a_schedule_block_to_midnight_ends_where_the_next_day_does_not_go_on(hass: HomeAssistant, freezer) -> None:
    tuesday = datetime(2026, 10, 13, 23, 40, tzinfo=BERLIN)
    all_day = [_block("00:00:00", "24:00:00")]
    evening = [_block("06:00:00", "08:00:00"), _block("18:00:00", "24:00:00")]
    _, bath, living = await _rooms_with_schedules(hass, freezer, tuesday, all_day, evening)
    assert bath.schedule.active and bath.schedule.next_end is None  # comfort all week
    assert living.schedule.active and living.schedule.next_end == datetime(2026, 10, 14, 0, 0, tzinfo=BERLIN)


async def test_preheating_for_a_schedule_block_goes_to_the_temperature_it_names(hass: HomeAssistant, freezer) -> None:
    tuesday = datetime(2026, 10, 13, 5, 40, tzinfo=BERLIN)
    named = [_block("06:00:00", "12:00:00", 20)]
    plain = [_block("06:00:00", "12:00:00")]
    snap, bath, living = await _rooms_with_schedules(hass, freezer, tuesday, named, plain)
    assert not bath.schedule.active and bath.schedule.next_start == tuesday.replace(hour=6, minute=0)
    assert bath.schedule_next_temp == 20.0 and living.schedule_next_temp is None
    res = room_engine.evaluate_room(bath, snap.now, "auto", snap.params)  # 18.5 °C in the room, comfort temperature 21
    assert (res.target, res.target_reason) == (20.0, "schedule_preheat")
    res = room_engine.evaluate_room(living, snap.now, "auto", snap.params)  # 17.0 °C in the room, comfort temperature 18.5
    assert (res.target, res.target_reason) == (18.5, "schedule_preheat")


async def test_a_window_open_when_the_control_starts_counts_as_open_at_once(hass: HomeAssistant, freezer) -> None:
    """Seen 2026-10-13: the study's door stood open all day and counted as shut for two minutes after each restart."""
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("binary_sensor.bath_window", "unavailable")  # not back yet after the restart
    builder = _builder(hass)
    grace = builder.params.window_grace_s

    def window(seconds: float, state: str | None = None):
        freezer.move_to(START + timedelta(seconds=seconds))
        if state is not None:
            hass.states.async_set("binary_sensor.bath_window", state)
        return room_engine.window_status(_build(builder).rooms["badezimmer"].window_readings, grace)

    assert window(0)[0] is None
    assert window(40, "on") == (True, False, grace)  # back, open: its last change is the restart, not an opening
    assert window(600) == (True, False, 560.0)  # and it is timed from then on (drying: "window open long")
    # shut and opened again while the control runs: that is an opening, with its grace
    assert window(700, "off")[0] is False
    assert window(760, "on")[0] is False and window(849)[0] is False and window(850)[0] is True

    # shut when the control started and opened in its first minutes: the grace applies as well
    hass.states.async_set("binary_sensor.bath_window", "off")
    builder = _builder(hass)
    assert window(1000)[0] is False
    assert window(1030, "on")[0] is False and window(1120)[0] is True


async def test_the_plug_power_of_a_running_heat_pump_counts_for_six_minutes(hass: HomeAssistant, freezer) -> None:
    """Review 2026-10-13: ten minutes kept the draw of a plug gone quiet long after the compressor had stopped."""
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.midea_power", "380.0")
    running = {**hass.states.get("climate.midea").attributes, "compressor_frequency": 52, "realtime_power": 365.0}
    hass.states.async_set("climate.midea", "heat", running)
    builder = _builder(hass)

    def heat_pump(seconds: float):
        freezer.move_to(START + timedelta(seconds=seconds))
        return _build(builder).heat_pump

    pump = heat_pump(180)  # the longest the plug was quiet while the unit ran (measured 2026-10-13)
    assert pump.plug_power.valid and pump.history[-1].power_w == 380.0
    pump = heat_pump(361)
    assert not pump.plug_power.valid and pump.plug_power.reason == "stale"
    assert pump.history[-1].power_w == 365.0  # the unit's own value stands in

    # stopped, the plug still showing the draw from before: that is no steady standby, it stays stale
    hass.states.async_set("climate.midea", "heat", {**running, "compressor_frequency": 0})
    assert not heat_pump(420).plug_power.valid


async def test_a_steady_standby_draw_holds_while_the_compressor_stands(hass: HomeAssistant, freezer) -> None:
    """A plug reports only what changes: measured 2026-10-14, plugs at a steady 0 W said nothing for more than 20 minutes."""
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.midea_power", "1.6")  # the unit stands (fan_only, compressor at 0)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=2))
    plug = _build(builder).heat_pump.plug_power
    assert plug.valid and plug.value == 1.6 and plug.reason is None

    # the compressor runs, yet the plug still says 1.6 W: that is a plug gone quiet
    attributes = {**hass.states.get("climate.midea").attributes, "compressor_frequency": 40}
    hass.states.async_set("climate.midea", "heat", attributes)
    plug = _build(builder).heat_pump.plug_power
    assert not plug.valid and plug.reason == "stale"


async def test_a_thermostat_says_whether_its_valve_is_open(hass: HomeAssistant) -> None:
    """Better Thermostat and Versatile Thermostat name it in `hvac_action`; a room whose valve is shut does not lag."""
    _set_states(hass)
    config = _entry_data()
    config["rooms"][0]["climate_entity"] = "climate.bath_trv"
    builder = SnapshotBuilder(hass, config)
    seen = []
    for action in ("heating", "preheating", "idle", "off", None, "cooling"):
        said = {} if action is None else {"hvac_action": action}
        hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, **said})
        seen.append(_build(builder).rooms["badezimmer"].thermostat_heating)
    assert seen == [True, True, False, False, None, None]
    hass.states.async_set("climate.bath_trv", "unavailable", {"hvac_action": "heating"})
    assert _build(builder).rooms["badezimmer"].thermostat_heating is None
