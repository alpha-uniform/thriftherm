import pytest

from custom_components.thriftherm.engines import cop as cop_engine
from custom_components.thriftherm.models import Parameters

from .conftest import PARAMS, heat_pump_state


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
    constant_ratio = psy.air_heating_power_w(20.0, 40.0, 35.0, None, res.airflow_m3h)
    assert res.thermal_power_w == pytest.approx(constant_ratio, abs=0.5)
    # what the old outlet reading of 40 % RH at 35 °C would have claimed: far more heat
    assert psy.air_heating_power_w(20.0, 40.0, 35.0, 40.0, res.airflow_m3h) > 2 * constant_ratio
    # the intake humidity does enter the balance
    humid = cop_engine.instant(heat_pump_state(intake=(20.0, 80.0), outlet=(35.0, None)), "heating_stable", 600.0, PARAMS)
    assert humid.thermal_power_w != res.thermal_power_w


def test_a_cop_from_an_ended_run_is_not_reported_as_measured():
    samples = [(1_000.0 + i * 60, 3.0) for i in range(20)]
    last = samples[-1][0]
    assert cop_engine.aggregate(samples, last + 30, PARAMS)[0] is not None
    assert cop_engine.aggregate(samples, last + cop_engine.COP_STALE_S + 1, PARAMS)[0] is None
