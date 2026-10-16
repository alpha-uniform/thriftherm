"""Gas for heating and gas for hot water, counted apart: by the gas meter, or by the boiler's own counters."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.const import (
    BOILER_REPORTS_HEATING,
    BOILER_REPORTS_HEATING_AFTER,
    BOILER_REPORTS_HOT_WATER,
    CONF_BOILER_NOMINAL_INPUT_KW,
    CONF_BOILER_STATE_NUMBER,
    CONF_GAS_VOLUME,
    DOMAIN,
)
from custom_components.thriftherm.engines.gas_split import (
    SOURCE_COUNTERS,
    SOURCE_METER,
    GasSplit,
    by_counters,
    by_meter,
    counter_kwh,
    hot_water_now,
    hot_water_share,
    meter_total_kwh,
)

from .test_integration import _entry_data, _set_states

NOW = 1_000_000.0
KWH_PER_M3 = 10.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ------------------------------------------------------------------ what the boiler burns for
def test_the_status_code_says_what_the_gas_is_burnt_for():
    assert hot_water_now(BOILER_REPORTS_HOT_WATER, False, None, NOW) is True
    assert hot_water_now(BOILER_REPORTS_HEATING, True, NOW - 10, NOW) is False  # it outranks every other sign
    # except a warm start: the boiler says heating while its heating is blocked (engines/boiler_control.py)
    assert hot_water_now(BOILER_REPORTS_HEATING, False, None, NOW, warm_start=True) is True
    assert hot_water_now(None, True, None, NOW) is True  # no code: the boiler's hot water state


def test_a_hot_water_sign_stands_for_two_minutes_not_for_ten():
    """The control shows hot water for ten minutes after a tap; a heating run in that time is heating."""
    assert hot_water_now(BOILER_REPORTS_HEATING_AFTER, False, NOW - 60, NOW) is True  # the meter reports a little late
    assert hot_water_now(BOILER_REPORTS_HEATING_AFTER, False, NOW - 300, NOW) is False
    assert hot_water_now(None, False, None, NOW) is False


# ------------------------------------------------------------------ with a gas meter
def test_each_step_of_the_meter_goes_to_what_the_boiler_does():
    split = by_meter(GasSplit(), 100.0, KWH_PER_M3, False, NOW)
    assert (split.heating_kwh, split.hot_water_kwh, split.source) == (0.0, 0.0, SOURCE_METER)  # counted from here
    split = by_meter(split, 100.05, KWH_PER_M3, False, NOW + 60)
    split = by_meter(split, 100.05, KWH_PER_M3, True, NOW + 120)  # nothing burnt: nothing credited
    split = by_meter(split, 100.07, KWH_PER_M3, True, NOW + 180)
    assert (split.heating_kwh, split.hot_water_kwh, split.unassigned_kwh) == (0.5, 0.2, 0.0)
    assert hot_water_share(split) == 0.286


def test_gas_burnt_while_nobody_watched_is_nobodys():
    split = by_meter(GasSplit(), 100.0, KWH_PER_M3, False, NOW)
    split = by_meter(split, 101.0, KWH_PER_M3, True, NOW + 3600)  # an hour without a look: restart, outage
    assert (split.heating_kwh, split.hot_water_kwh, split.unassigned_kwh) == (0.0, 0.0, 10.0)
    split = by_meter(split, 101.1, KWH_PER_M3, True, NOW + 3660)
    assert split.hot_water_kwh == 1.0  # watched again


def test_a_look_without_a_step_keeps_the_watch_alive():
    split = by_meter(GasSplit(), 100.0, KWH_PER_M3, False, NOW)
    for minute in range(1, 61):  # an idle hour, looked at every minute
        split = by_meter(split, 100.0, KWH_PER_M3, False, NOW + 60 * minute)
    split = by_meter(split, 100.05, KWH_PER_M3, False, NOW + 3660)
    assert (split.heating_kwh, split.unassigned_kwh) == (0.5, 0.0)


def test_a_meter_that_was_silent_does_not_bring_hours_of_gas_to_what_the_boiler_does_now():
    """Review 2026-10-13: three hours without a report, the entity keeping its last reading, then 2.4 m³ during a shower."""
    split = by_meter(GasSplit(), 500.0, KWH_PER_M3, False, NOW)
    for minute in range(1, 181):  # looked at every minute: the watch never lapsed
        split = by_meter(split, 500.0, KWH_PER_M3, False, NOW + 60 * minute)
    split = by_meter(split, 502.4, KWH_PER_M3, True, NOW + 60 * 181)
    # a boiler burns 0.1 m³ in a minute at most (6 m³/h, about 60 kW): that much is the shower's
    assert (split.heating_kwh, split.hot_water_kwh, split.unassigned_kwh) == (0.0, 1.0, 23.0)
    assert meter_total_kwh(split) == 5024.0  # and none of it is lost


def test_another_meter_starts_from_where_it_stands():
    split = by_meter(GasSplit(heating_kwh=7.0), 100.0, KWH_PER_M3, False, NOW)
    for reading in (3.0, 900.0):  # replaced: back to a small number; or a reading typed in by hand
        split = by_meter(split, reading, KWH_PER_M3, False, NOW + 60)
        assert (split.heating_kwh, split.unassigned_kwh, split.meter_m3) == (7.0, 0.0, reading)


def test_the_meters_total_starts_at_the_meter_and_never_goes_down():
    """Reading times today's calorific value rescaled the whole total with every change of that value."""
    assert meter_total_kwh(GasSplit()) is None
    split = by_meter(GasSplit(), 2000.0, 10.0, False, NOW)
    assert (split.meter_start_kwh, meter_total_kwh(split)) == (20000.0, 20000.0)
    split = by_meter(split, 2000.05, 10.0, False, NOW + 60)
    assert meter_total_kwh(split) == 20000.5
    seen = [meter_total_kwh(split)]
    looks = (
        (2000.05, 9.0, 120),  # the supplier's calorific value goes down ...
        (2000.10, 9.0, 180),  # ... and what is burnt from then on counts by it
        (2060.00, 9.0, 240),  # a jump that is no consumption
        (3.0, 9.0, 300),  # another meter, with a smaller reading
        (3.05, 11.0, 360),
        (4.0, 11.0, 4000),  # burnt while nobody watched: counted, for neither purpose
    )
    for reading, kwh_per_m3, after in looks:
        split = by_meter(split, reading, kwh_per_m3, after == 180, NOW + after)
        seen.append(meter_total_kwh(split))
    assert seen == sorted(seen) and seen[-1] == pytest.approx(20000.5 + 0.45 + 0.55 + 10.45)
    assert meter_total_kwh(GasSplit.from_storage(split.to_storage())) == seen[-1]
    assert meter_total_kwh(by_counters(split, {"a": 1.0}, {}, 10.0, NOW + 5000)) is None  # no meter any more


def test_a_count_begun_before_the_total_was_kept_continues_from_the_meters_energy():
    stored = {"heating_kwh": 300.0, "hot_water_kwh": 100.0, "unassigned_kwh": 12.0, "source": "gas_meter",
              "meter_m3": 2000.0, "counted_ts": NOW, "counters": {}}  # as written before 2026-10-13
    split = GasSplit.from_storage(stored)
    assert split.meter_start_kwh is None and meter_total_kwh(split) is None
    split = by_meter(split, 2000.05, 10.0, False, NOW + 60)
    assert split.heating_kwh == 300.5 and meter_total_kwh(split) == 20000.5  # what the meter shows as energy now
    assert split.meter_start_kwh == 20000.0 - 412.0


# ------------------------------------------------------------------ without one: the boiler's counters
def test_the_counters_follow_the_gas_meter_of_the_test_installation():
    """2026-10-09: the meter counted 6.896 kWh; PrEnergySumHc1 moved by 94555, Hc3 by 6000, Hwc1 by 135."""
    before = {"prenergysumhc1": 615009487.0, "prenergysumhc2": 8140840.0, "prenergysumhc3": 18211773.0}
    water = {"prenergysumhwc1": 12829739.0}
    split = by_counters(GasSplit(), before, water, 22.2, NOW)
    assert (split.heating_kwh, split.hot_water_kwh, split.source) == (0.0, 0.0, SOURCE_COUNTERS)  # counted from here
    after = {"prenergysumhc1": 615104042.0, "prenergysumhc2": 8140840.0, "prenergysumhc3": 18217773.0}
    split = by_counters(split, after, {"prenergysumhwc1": 12829874.0}, 22.2, NOW + 86400)
    assert split.heating_kwh == pytest.approx(6.883, abs=0.001) and split.hot_water_kwh == pytest.approx(0.009, abs=0.001)
    assert split.heating_kwh + split.hot_water_kwh == pytest.approx(6.896, abs=0.01)
    assert split.unassigned_kwh == 0.0


def test_an_hour_at_full_output_is_the_nominal_input_by_the_upper_calorific_value():
    assert counter_kwh(360000.0, 22.2) == pytest.approx(24.642)


def test_a_counter_that_went_back_or_is_away_loses_nothing_else():
    split = by_counters(GasSplit(), {"a": 1000.0, "b": 500.0}, {"w": 100.0}, 10.0, NOW)
    split = by_counters(split, {"a": 20.0}, {"w": 36100.0}, 10.0, NOW + 600)  # "a" reset, "b" not read this time
    assert split.heating_kwh == 0.0 and split.hot_water_kwh == pytest.approx(counter_kwh(36000.0, 10.0))
    split = by_counters(split, {"a": 36020.0, "b": 500.0}, {"w": 36100.0}, 10.0, NOW + 1200)
    assert split.heating_kwh == pytest.approx(counter_kwh(36000.0, 10.0))


def test_a_counter_cannot_rise_faster_than_a_burner_at_full_output():
    """Review 2026-10-13: a counter at 3.0e9 that read 0 once and came back was credited with 205,350 kWh."""
    split = by_counters(GasSplit(), {"a": 3.0e9}, {}, 22.2, NOW)
    split = by_counters(split, {"a": 0.0}, {}, 22.2, NOW + 60)
    split = by_counters(split, {"a": 3.0e9 + 2000.0}, {}, 22.2, NOW + 120)
    assert split.heating_kwh == 0.0 and split.counters["a"] == 3.0e9 + 2000.0  # only its place moved
    split = by_counters(split, {"a": 3.0e9 + 8000.0}, {}, 22.2, NOW + 180)  # a minute at full output
    assert split.heating_kwh == pytest.approx(counter_kwh(6000.0, 22.2))


def test_a_counter_read_every_few_minutes_brings_all_those_minutes():
    """ebusd reads its values in turn: a counter's entity stands still for minutes, then moves by all of them."""
    split = by_counters(GasSplit(), {"a": 1000.0}, {}, 22.2, NOW)
    for minute in range(1, 8):  # looked at every minute, the reading still the one of eight minutes ago
        split = by_counters(split, {"a": 1000.0}, {}, 22.2, NOW + 60 * minute)
    split = by_counters(split, {"a": 49000.0}, {}, 22.2, NOW + 480)  # eight minutes at full output
    assert split.heating_kwh == pytest.approx(counter_kwh(48000.0, 22.2))
    back = GasSplit.from_storage(split.to_storage())
    assert back == split and back.counters_ts == {"a": NOW + 480}


def test_a_meter_added_later_takes_over_and_keeps_what_was_counted():
    counted = GasSplit(heating_kwh=3.0, hot_water_kwh=1.0, source=SOURCE_COUNTERS, counters={"a": 5.0})
    split = by_counters(counted, {"a": 5.0}, {}, 10.0, NOW)
    split = by_meter(split, 100.0, KWH_PER_M3, False, NOW + 60)
    assert (split.heating_kwh, split.hot_water_kwh, split.source, dict(split.counters)) == (3.0, 1.0, SOURCE_METER, {})
    assert meter_total_kwh(split) == 1000.0  # the meter's total is the meter's: what the counters counted is not on top
    split = by_meter(split, 100.1, KWH_PER_M3, False, NOW + 120)
    assert split.heating_kwh == 4.0 and meter_total_kwh(split) == 1001.0


def test_the_count_survives_a_restart():
    split = by_meter(by_meter(GasSplit(), 100.0, KWH_PER_M3, False, NOW), 100.5, KWH_PER_M3, True, NOW + 60)
    assert GasSplit.from_storage(split.to_storage()) == split
    assert GasSplit.from_storage({"heating_kwh": "x", "source": "nonsense", "counters": [1]}) == GasSplit()
    assert GasSplit.from_storage(None) == GasSplit()


# ------------------------------------------------------------------ through Home Assistant
HEATING, HOT_WATER = "sensor.thriftherm_gas_for_heating", "sensor.thriftherm_gas_for_hot_water"


async def _setup(hass: HomeAssistant, data: dict, options: dict | None = None):
    _set_states(hass)
    config = {**_entry_data(), **data}
    entry = MockConfigEntry(domain=DOMAIN, data=config, options=options or {}, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_with_a_gas_meter_the_gas_is_split_by_the_boilers_status(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("sensor.gas_volume", "100.0")
    hass.states.async_set("sensor.statenumber", "4")  # S.4: heating, burner on
    coordinator = await _setup(hass, {CONF_GAS_VOLUME: "sensor.gas_volume", CONF_BOILER_STATE_NUMBER: "sensor.statenumber"})
    assert (hass.states.get(HEATING).state, hass.states.get(HOT_WATER).state) == ("0.0", "0.0")
    assert hass.states.get(HEATING).attributes["source"] == "gas_meter"
    kwh_per_m3 = 0.9381 * 11.45  # the defaults: z factor and calorific value

    freezer.tick(60)
    hass.states.async_set("sensor.gas_volume", "100.05")
    await coordinator.async_refresh()
    freezer.tick(60)
    hass.states.async_set("sensor.statenumber", "14")  # S.14: hot water, burner on
    hass.states.async_set("sensor.gas_volume", "100.07")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(HEATING).state) == pytest.approx(0.05 * kwh_per_m3, abs=0.001)
    assert float(hass.states.get(HOT_WATER).state) == pytest.approx(0.02 * kwh_per_m3, abs=0.001)
    attributes = hass.states.get(HOT_WATER).attributes
    assert (attributes["hot_water_share_pct"], attributes["unassigned_kwh"]) == (28.6, 0.0)
    assert hass.states.get(HEATING).attributes["state_class"] == "total_increasing"


async def test_a_warm_starts_gas_goes_to_hot_water_although_the_boiler_reports_heating(
    hass: HomeAssistant, freezer, monkeypatch
) -> None:
    """The coordinator hands the control's warm start to the gas split (engines/boiler_control.py)."""
    from dataclasses import replace as _replace

    from custom_components.thriftherm.engines import boiler_control

    real_decide = boiler_control.decide

    def decide_with_warm_start(inp, mem):
        cmd, mem = real_decide(inp, mem)
        return _replace(cmd, warm_start=True), mem

    monkeypatch.setattr(boiler_control, "decide", decide_with_warm_start)
    hass.states.async_set("sensor.gas_volume", "100.0")
    hass.states.async_set("sensor.statenumber", "4")  # S.4 says heating, the control says warm start
    coordinator = await _setup(hass, {CONF_GAS_VOLUME: "sensor.gas_volume", CONF_BOILER_STATE_NUMBER: "sensor.statenumber"})

    freezer.tick(60)
    hass.states.async_set("sensor.gas_volume", "100.02")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(HEATING).state) == 0.0
    assert float(hass.states.get(HOT_WATER).state) == pytest.approx(0.02 * 0.9381 * 11.45, abs=0.001)


async def test_a_new_calorific_value_does_not_rescale_the_gas_already_counted(hass: HomeAssistant, freezer) -> None:
    """The gas energy was reading times calorific value: a new value moved the whole total, and the
    statistics booked the difference (thousands of kWh on an old meter) as that hour's consumption."""
    total = "sensor.thriftherm_gas_energy"
    hass.states.async_set("sensor.gas_volume", "12000.0")
    await _setup(hass, {CONF_GAS_VOLUME: "sensor.gas_volume"})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    before = float(hass.states.get(total).state)
    assert before == pytest.approx(12000.0 * 0.9381 * 11.45, abs=0.01)  # it starts at what the meter shows

    hass.config_entries.async_update_entry(entry, options={**entry.options, "gas_calorific_value_kwh_m3": 11.2})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(total).state) == pytest.approx(before, abs=0.01)  # was 2 % less: -2900 kWh
    freezer.tick(60)
    hass.states.async_set("sensor.gas_volume", "12000.05")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    # new gas counts at the new value
    assert float(hass.states.get(total).state) == pytest.approx(before + 0.05 * 0.9381 * 11.2, abs=0.01)


def _ebusd_boiler(hass: HomeAssistant, sums: dict[str, str]) -> None:
    """The boiler as ebusd announces it: one device, the message name in each unique id."""
    registry = er.async_get(hass)
    mqtt = MockConfigEntry(domain="mqtt")
    mqtt.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=mqtt.entry_id, identifiers={("mqtt", "ebusd_bai")})
    on_boiler = {"device_id": device.id, "config_entry": mqtt}
    registry.async_get_or_create("sensor", "mqtt", "ebusd_bai_FlowTemp_temp", suggested_object_id="flow", **on_boiler)
    for name, value in sums.items():
        ids = {"unique_id": f"ebusd_bai_{name}_value", "suggested_object_id": f"bai_{name.lower()}"}
        entry = registry.async_get_or_create("sensor", "mqtt", **ids, **on_boiler)
        hass.states.async_set(entry.entity_id, value)


async def test_without_a_gas_meter_the_boilers_own_counters_are_read(hass: HomeAssistant, freezer) -> None:
    sums = {"PrEnergySumHc1": "1000", "PrEnergySumHc2": "0", "PrEnergySumHc3": "50", "PrEnergySumHwc1": "200",
            "PrEnergySumHwc2": "unknown", "PrEnergyCountHc1": "77", "HcHours": "4191"}
    _ebusd_boiler(hass, sums)
    coordinator = await _setup(hass, {CONF_BOILER_NOMINAL_INPUT_KW: 22.2})
    boiler = coordinator.data["snapshot"].boiler
    assert boiler.energy_sums_heating == (("prenergysumhc1", 1000.0), ("prenergysumhc2", 0.0), ("prenergysumhc3", 50.0))
    assert boiler.energy_sums_hot_water == (("prenergysumhwc1", 200.0),)  # no counts, no hours, nothing unreadable
    assert (hass.states.get(HEATING).state, hass.states.get(HEATING).attributes["source"]) == ("0.0", "boiler_counters")

    freezer.tick(3600)
    hass.states.async_set("sensor.bai_prenergysumhc1", "361000")  # an hour at full output
    hass.states.async_set("sensor.bai_prenergysumhwc1", "180200")  # half an hour of hot water
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(HEATING).state) == pytest.approx(24.642, abs=0.001)
    assert float(hass.states.get(HOT_WATER).state) == pytest.approx(12.321, abs=0.001)
    assert hass.states.get(HOT_WATER).attributes["unassigned_kwh"] is None  # the counters leave nothing open


async def test_without_a_meter_and_without_the_nominal_input_there_are_no_such_sensors(hass: HomeAssistant) -> None:
    _ebusd_boiler(hass, {"PrEnergySumHc1": "1000"})
    coordinator = await _setup(hass, {})
    assert hass.states.get(HEATING) is None and hass.states.get(HOT_WATER) is None
    assert coordinator.gas_split == GasSplit()  # and nothing is counted
