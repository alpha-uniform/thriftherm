from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import (
    CONF_COP_INTAKE_MAX_AGE_MIN,
    CONF_COP_OUTLET_MAX_AGE_MIN,
    CONF_HEAT_PUMP_INTAKE_RH,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_OUTLET_TEMP,
)
from custom_components.thriftherm.engines import cop as cop_engine
from custom_components.thriftherm.engines.heat_pump_control import ControlMemory, ref_reading
from custom_components.thriftherm.models import Parameters

from .conftest import PARAMS, heat_pump_state, reading
from .test_integration import _entry_data, _set_states


def test_parse_and_interpolate_airflow_curve():
    curve = cop_engine.parse_airflow_curve("900:700, 300:200,600:450")
    assert curve == ((300.0, 200.0), (600.0, 450.0), (900.0, 700.0))
    assert cop_engine.airflow_from_rpm(curve, 450.0) == pytest.approx(325.0)
    assert cop_engine.airflow_from_rpm(curve, 300.0) == 200.0
    # just outside the curve the flow follows fan speed (fan law), not the end value
    assert cop_engine.airflow_from_rpm(curve, 1000.0) == pytest.approx(700.0 * 1000.0 / 900.0)
    assert cop_engine.airflow_from_rpm(curve, 260.0) == pytest.approx(200.0 * 260.0 / 300.0)
    assert cop_engine.airflow_from_rpm(curve, 1200.0) is None
    assert cop_engine.airflow_from_rpm(curve, None) is None
    assert cop_engine.airflow_from_rpm((), 500.0) is None
    assert cop_engine.parse_airflow_curve("") == ()


def test_instant_valid_sample():
    m = heat_pump_state()
    res = cop_engine.instant(m, "heating_stable", 800.0, PARAMS)
    assert res.gate_reasons == ()
    assert res.raw is not None
    assert 2.5 < res.raw < 4.5
    assert res.thermal_power_w == pytest.approx(res.raw * 800.0, rel=0.01)


def test_instant_gates_without_calibration():
    params = Parameters()  # no airflow curve
    res = cop_engine.instant(heat_pump_state(), "heating_stable", 800.0, params)
    assert "airflow_curve_not_calibrated" in res.gate_reasons
    assert res.raw is None


def test_instant_gates_missing_sensors_and_state():
    m = heat_pump_state(intake=None, outlet=None)
    res = cop_engine.instant(m, "heating_stable", 800.0, PARAMS)
    assert any(g.startswith("intake_temp_invalid") for g in res.gate_reasons)
    assert any(g.startswith("outlet_temp_invalid") for g in res.gate_reasons)
    res2 = cop_engine.instant(heat_pump_state(), "heating_warming_up", 800.0, PARAMS)
    assert "warming_up" in res2.gate_reasons
    res3 = cop_engine.instant(heat_pump_state(), "heating_stable", 50.0, PARAMS)
    assert "electrical_power_below_minimum" in res3.gate_reasons
    res4 = cop_engine.instant(heat_pump_state(), "cooling_user", 800.0, PARAMS)
    assert "not_heating:cooling_user" in res4.gate_reasons


def test_instant_rejects_implausible_values():
    # tiny electrical power with big delta T -> absurd COP
    res = cop_engine.instant(heat_pump_state(), "heating_stable", 160.0, PARAMS)
    assert "raw_cop_out_of_range" in res.gate_reasons or "exceeds_carnot_limit" in res.gate_reasons
    # small delta T
    res2 = cop_engine.instant(heat_pump_state(outlet=(21.0, None)), "heating_stable", 800.0, PARAMS)
    assert "delta_t_below_minimum" in res2.gate_reasons


def test_aggregate_requires_min_samples_and_filters_outliers():
    params = Parameters(cop_min_samples=6, cop_ema_tau_s=300.0)
    base = 1000.0
    samples = [(base + i * 10, 3.0) for i in range(10)]
    samples[4] = (base + 40, 12.0)  # outlier
    value, n = cop_engine.aggregate(samples, base + 100, params)
    assert n == 10
    assert value == pytest.approx(3.0, abs=0.05)
    value2, n2 = cop_engine.aggregate(samples[:3], base + 100, params)
    assert value2 is None and n2 == 3


def test_carnot_limit():
    assert cop_engine.carnot_limit(45.0, 0.0) == pytest.approx(7.07, abs=0.01)
    assert cop_engine.carnot_limit(45.0, 44.0) is None
    assert cop_engine.carnot_limit(None, 0.0) is None


def test_outlet_humidity_does_not_enter_the_heating_balance():
    """Heating adds no water: the thermal power rests on the intake humidity alone.

    A stale outlet RH once turned COP 3.4 into 8.1, so the heat pump state no longer
    carries an outlet humidity at all; the engine keeps the intake humidity ratio.
    """
    from dataclasses import fields

    from custom_components.thriftherm.engines import psychrometrics as psy
    from custom_components.thriftherm.models import HeatPumpState

    assert "outlet_rh" not in {f.name for f in fields(HeatPumpState)}

    res = cop_engine.instant(heat_pump_state(intake=(20.0, 40.0), outlet=(35.0, 40.0)), "heating_stable", 600.0, PARAMS)
    assert res.gate_reasons == ()
    constant_ratio = psy.air_heating_power_w(20.0, 40.0, 35.0, res.airflow_m3h)
    assert res.thermal_power_w == pytest.approx(constant_ratio, abs=0.5)
    # the intake humidity does enter the balance
    humid = cop_engine.instant(heat_pump_state(intake=(20.0, 80.0), outlet=(35.0, None)), "heating_stable", 600.0, PARAMS)
    assert humid.thermal_power_w != res.thermal_power_w


def test_a_cop_from_an_ended_run_is_not_reported_as_measured():
    samples = [(1_000.0 + i * 60, 3.0) for i in range(20)]
    last = samples[-1][0]
    assert cop_engine.aggregate(samples, last + 30, PARAMS)[0] is not None
    assert cop_engine.aggregate(samples, last + cop_engine.COP_STALE_S + 1, PARAMS)[0] is None


def test_an_intake_reading_counts_for_as_long_as_the_adapter_hands_it_over_as_valid():
    """Seen 2026-10-11: in a steady run the humidity did not move for 35 minutes and the COP went unknown."""
    unit = heat_pump_state()
    old_rh = replace(unit, intake_rh=replace(unit.intake_rh, age_s=2 * 3600.0))
    assert cop_engine.instant(old_rh, "heating_stable", 800.0, PARAMS).gate_reasons == ()
    too_old = replace(unit, intake_rh=reading(45.0, valid=False))  # what the adapter makes of it after six hours
    assert "intake_rh_invalid:stale" in cop_engine.instant(too_old, "heating_stable", 800.0, PARAMS).gate_reasons
    # The outlet sensor too: 41 % of stable heating was thrown away with a five minute limit (2026-10-31 to 11-02).
    quiet_outlet = replace(unit, outlet_temp=replace(unit.outlet_temp, age_s=25 * 60.0))
    assert cop_engine.instant(quiet_outlet, "heating_stable", 800.0, PARAMS).gate_reasons == ()
    too_old_outlet = replace(unit, outlet_temp=reading(38.0, valid=False))  # what the adapter makes of it past its limit
    assert "outlet_temp_invalid:stale" in cop_engine.instant(too_old_outlet, "heating_stable", 800.0, PARAMS).gate_reasons


def test_a_frozen_cold_outlet_gives_no_sample_while_its_report_counts():
    unit = heat_pump_state(outlet=(21.0, None))
    frozen = replace(unit, outlet_temp=replace(unit.outlet_temp, age_s=30 * 60.0))
    res = cop_engine.instant(frozen, "heating_stable", 800.0, PARAMS)
    assert res.raw is None and "delta_t_below_minimum" in res.gate_reasons


def _unit(builder: SnapshotBuilder):
    """The heat pump as the adapter reads it from Home Assistant."""
    snap = builder.build("auto", overrides={}, drying_states={}, away_return_ts=None, boosts={}, room_temps={})
    return snap.heat_pump


def _sensor_builder(hass, **options) -> SnapshotBuilder:
    _set_states(hass)
    hass.states.async_set("sensor.intake_temp", "20.0")
    hass.states.async_set("sensor.intake_rh", "45")
    hass.states.async_set("sensor.outlet_temp", "34.0")
    return SnapshotBuilder(hass, {
        **_entry_data(), CONF_HEAT_PUMP_INTAKE_TEMP: "sensor.intake_temp", CONF_HEAT_PUMP_INTAKE_RH: "sensor.intake_rh",
        CONF_HEAT_PUMP_OUTLET_TEMP: "sensor.outlet_temp", **options,
    })


START = datetime(2026, 1, 15, 12, 0, tzinfo=dt_util.UTC)


async def test_the_adapter_lets_the_intake_humidity_be_six_hours_old_and_the_temperature_three(hass, freezer):
    # HA drops the hourly report of an unchanged value: the intake stood still for up to 108 minutes (2026-10-31)
    freezer.move_to(START)
    builder = _sensor_builder(hass)

    def unit_after(hours: float):
        freezer.move_to(START + timedelta(hours=hours))
        return _unit(builder)

    assert unit_after(2.9).intake_temp.valid
    past_limit = unit_after(3.01)
    assert past_limit.intake_rh.valid and not past_limit.intake_temp.valid and past_limit.intake_temp.reason == "stale"
    assert cop_engine.RH_MAX_AGE_S == 6 * 3600.0 and not unit_after(6.1).intake_rh.valid


async def test_a_quiet_intake_temperature_holds_while_its_device_reports(hass, freezer):
    """2026-11-03: 20.2 °C unchanged for over four hours in a steady run, the humidity of the same sensor moving: the COP
    was unknown from 18:33 to 19:48 and the table lost that hour. Only a device that fell silent is past the limit."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="zigbee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("zigbee", "intake")})
    registry = er.async_get(hass)
    for uid, object_id in (("temp", "intake_temp"), ("rh", "intake_rh")):
        registry.async_get_or_create("sensor", "zigbee", uid, suggested_object_id=object_id, device_id=device.id, config_entry=entry)
    freezer.move_to(START)
    builder = _sensor_builder(hass)
    freezer.move_to(START + timedelta(hours=4))
    hass.states.async_set("sensor.intake_rh", "44")
    assert _unit(builder).intake_temp.valid
    freezer.move_to(START + timedelta(hours=7, minutes=1))  # three hours and a minute after the humidity: all of it silent
    past_limit = _unit(builder).intake_temp
    assert not past_limit.valid and past_limit.reason == "stale"


async def test_an_outlet_reading_counts_for_an_hour_without_a_report(hass, freezer):
    freezer.move_to(START)
    builder = _sensor_builder(hass)
    # 25 minutes unchanged in a steady run (seen up to 27 on 2026-10-31): a sample like any other
    freezer.move_to(START + timedelta(minutes=25))
    quiet = _unit(builder)
    assert quiet.outlet_temp.valid and quiet.intake_temp.valid
    unit = replace(quiet, fan_rpm=600.0, indoor_coil_temp=45.0, outdoor_coil_temp=0.0, outlet_temp_history=())
    res = cop_engine.instant(unit, "heating_stable", 800.0, PARAMS)
    assert res.gate_reasons == () and res.raw is not None

    freezer.move_to(START + timedelta(minutes=59))
    assert _unit(builder).outlet_temp.valid
    freezer.move_to(START + timedelta(hours=2))
    old = _unit(builder)
    assert not old.outlet_temp.valid and old.outlet_temp.reason == "stale" and old.outlet_temp.value == 34.0
    res = cop_engine.instant(replace(old, fan_rpm=600.0, outlet_temp_history=()), "heating_stable", 800.0, PARAMS)
    assert res.raw is None and "outlet_temp_invalid:stale" in res.gate_reasons


async def test_a_report_of_the_same_value_renews_a_reading_and_an_unavailable_sensor_counts_at_once(hass, freezer):
    freezer.move_to(START)
    builder = _sensor_builder(hass)
    freezer.move_to(START + timedelta(minutes=50))
    hass.states.async_set("sensor.outlet_temp", "34.0")  # the same value, reported
    freezer.move_to(START + timedelta(minutes=100))
    assert _unit(builder).outlet_temp.valid

    hass.states.async_set("sensor.outlet_temp", "unavailable")
    hass.states.async_set("sensor.intake_temp", "unknown")
    unit = _unit(builder)
    assert not unit.outlet_temp.valid and unit.outlet_temp.reason == "unavailable"
    assert not unit.intake_temp.valid and unit.intake_temp.reason == "unknown"


async def test_the_limits_of_the_options_take_effect(hass, freezer):
    freezer.move_to(START)
    builder = _sensor_builder(hass, **{CONF_COP_OUTLET_MAX_AGE_MIN: 10, CONF_COP_INTAKE_MAX_AGE_MIN: 30})
    freezer.move_to(START + timedelta(minutes=9))
    assert _unit(builder).outlet_temp.valid
    freezer.move_to(START + timedelta(minutes=11))
    unit = _unit(builder)
    assert not unit.outlet_temp.valid and unit.intake_temp.valid
    freezer.move_to(START + timedelta(minutes=31))
    assert not _unit(builder).intake_temp.valid


async def test_a_quiet_intake_sensor_goes_on_setting_the_start_of_a_unit_off_for_long(hass, freezer):
    """Its own reading stands still when off (2026-10-28), so the intake counts for its whole limit, then its own after all."""
    freezer.move_to(START)
    builder = _sensor_builder(hass)
    freezer.move_to(START + timedelta(hours=2.9))
    unit = replace(_unit(builder), hvac_mode="off", indoor_temp=21.9)
    assert ref_reading(unit, ControlMemory(), START.timestamp()) == 20.0
    freezer.move_to(START + timedelta(hours=3.01))
    unit = replace(_unit(builder), hvac_mode="off", indoor_temp=21.9)
    assert ref_reading(unit, ControlMemory(), START.timestamp()) == 21.9


async def test_the_adapter_reads_the_setpoint_step_and_the_modes_of_the_unit(hass):
    _set_states(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    unit = _unit(builder)
    assert unit.hvac_modes == ("off", "cool", "heat", "fan_only") and unit.target_temp_step is None
    hass.states.async_set("climate.midea", "heat", {"temperature": 22, "target_temp_step": 1, "hvac_modes": "nonsense"})
    unit = _unit(builder)
    assert unit.target_temp_step == 1.0 and unit.hvac_modes == ()


def test_no_power_and_a_fan_that_stands_divide_nothing_by_zero():
    no_minimum = replace(PARAMS, cop_min_power_w=0.0)
    res = cop_engine.instant(heat_pump_state(plug_power=0.0), "heating_stable", 0.0, no_minimum)
    assert res.raw is None and "electrical_power_below_minimum" in res.gate_reasons
    from_zero = replace(PARAMS, airflow_curve=cop_engine.parse_airflow_curve("0:0,600:450,900:700"))
    assert cop_engine.airflow_from_rpm(from_zero.airflow_curve, 0.0) is None
    assert cop_engine.airflow_from_rpm(PARAMS.airflow_curve, 0.0) is None
    for params in (PARAMS, from_zero):
        assert cop_engine.instant(heat_pump_state(fan_rpm=0.0), "heating_stable", 800.0, params).raw is None
        assert cop_engine.free_power_now(heat_pump_state(fan_rpm=0.0), params) == 0.0


def test_the_heat_of_the_moment_is_unknown_without_a_fan_speed_or_with_an_old_outlet_reading():
    assert cop_engine.free_power_now(heat_pump_state(), PARAMS) > 1000.0
    assert cop_engine.free_power_now(heat_pump_state(fan_rpm=None), PARAMS) is None  # not "the fan stands"
    assert cop_engine.free_power_now(heat_pump_state(fan_rpm=0.0), PARAMS) == 0.0
    # a quiet sensor within its limit counts: the age is the adapter's to judge
    quiet = heat_pump_state()
    quiet = replace(quiet, outlet_temp=replace(quiet.outlet_temp, age_s=25 * 60.0))
    assert cop_engine.free_power_now(quiet, PARAMS) > 1000.0
    old = replace(heat_pump_state(), outlet_temp=reading(38.0, valid=False))  # past its limit
    assert cop_engine.free_power_now(old, PARAMS) is None
    # at rest the sensor has nothing to report: an old reading that shows no heat is still no heat
    at_rest = replace(heat_pump_state(compressor_hz=0.0), outlet_temp=reading(20.0, valid=False))
    assert cop_engine.free_power_now(at_rest, PARAMS) == 0.0
    assert cop_engine.free_power_now(replace(at_rest, compressor_hz=30.0), PARAMS) is None
    # only for a reading that is old: not for one that shows heat, is out of range or gone
    warm = replace(at_rest, outlet_temp=reading(30.0, valid=False))
    assert cop_engine.free_power_now(warm, PARAMS) is None
    implausible = replace(at_rest, outlet_temp=reading(20.0, valid=False, reason="out_of_range"))
    assert cop_engine.free_power_now(implausible, PARAMS) is None
    assert cop_engine.free_power_now(replace(at_rest, outlet_temp=reading(None)), PARAMS) is None


# ---- a COP map measured on the same model (2026-10-15)
def test_without_own_measurements_the_shipped_map_of_the_model_counts():
    from custom_components.thriftherm.engines import cop_profiles
    from custom_components.thriftherm.engines.learning import CopMap, prior_cop

    portasplit = cop_profiles.profile("midea_portasplit")
    empty = CopMap()
    assert empty.estimate(3.0, 1.0, portasplit) == (3.61, "profile")  # the bin measured on the model
    assert empty.estimate(13.0, 1.0, portasplit) == (4.42, "profile")
    cold, basis = empty.estimate(-8.0, 1.0, portasplit)  # beyond what was measured: the curve, calibrated by the model
    assert basis == "profile" and cold < prior_cop(-8.0) and cold > prior_cop(-8.0) * 0.5
    assert empty.estimate(3.0, 1.0) == (round(prior_cop(3.0) * 0.8, 3), "prior")  # none chosen: as before
    assert cop_profiles.profile("none") is None and cop_profiles.profile("unknown") is None

    own = CopMap()
    own.add_interval(3.0, 2900.0 * 3.1, 1000.0 * 3.1, 3.1 * 3600.0, 0.0)
    assert own.estimate(3.0, 1.0, portasplit) == (2.9, "learned")  # what the unit measured comes first
    assert own.estimate(-8.0, 1.0, portasplit)[1] == "prior_calibrated"  # and calibrates the curve itself
