import pytest

from custom_components.thriftherm.engines import boiler as boiler_engine, heat_pump as heat_pump_engine
from custom_components.thriftherm.models import Parameters

from .conftest import PARAMS, PRICES, boiler_state, heat_pump_state


def test_boiler_heating_active_and_power_estimate():
    res = boiler_engine.evaluate(boiler_state(flow=45.0, ret=38.0, pump="on"), PRICES)
    assert res.available
    assert res.hc_active is True
    assert res.hwc_active is False
    assert res.delta_t_k == pytest.approx(7.0)
    # 860 l/h * 4186 J/kgK * 7 K / 3600 ≈ 7000 W
    assert res.thermal_power_estimate_w == pytest.approx(7000, rel=0.01)
    assert res.gas_power_w == pytest.approx(12889, rel=0.001)
    assert res.gas_energy_kwh == pytest.approx(1074.1, abs=0.1)


def test_boiler_hwc_priority():
    res = boiler_engine.evaluate(boiler_state(pump="hwc"), PRICES)
    assert res.hwc_active is True
    assert res.hc_active is False


def test_boiler_pump_off_means_no_heating():
    res = boiler_engine.evaluate(boiler_state(pump="off", flow=25.0, ret=24.0), PRICES)
    assert res.hc_active is False
    assert res.thermal_power_estimate_w is None


def test_boiler_signal_lost():
    res = boiler_engine.evaluate(boiler_state(signal=False), PRICES)
    assert not res.available
    assert "ebusd_signal_lost" in res.issues


def test_boiler_unknown_pump_state_yields_none():
    res = boiler_engine.evaluate(boiler_state(pump=None, hwc_mode=None), PRICES)
    assert res.hc_active is None
    assert res.hwc_active is None


def test_heat_pump_run_states():
    p = Parameters(cop_warmup_s=300)
    assert heat_pump_engine.run_state(heat_pump_state(available=False), p) == "unavailable"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="off"), p) == "off"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="cool"), p) == "cooling_user"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="dry"), p) == "cooling_user"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="fan_only"), p) == "fan_only"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="heat", compressor_hz=0), p) == "heating_idle"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="heat", running_s=60), p) == "heating_warming_up"
    assert heat_pump_engine.run_state(heat_pump_state(hvac_mode="heat", running_s=900), p) == "heating_stable"


def test_heat_pump_cooling_is_never_available_for_heating():
    res = heat_pump_engine.evaluate(heat_pump_state(hvac_mode="cool"), PARAMS)
    assert res.heating_available is False
    res2 = heat_pump_engine.evaluate(heat_pump_state(hvac_mode="heat"), PARAMS)
    assert res2.heating_available is True
    res3 = heat_pump_engine.evaluate(heat_pump_state(error_code=7), PARAMS)
    assert res3.heating_available is False
    assert "error_code:7" in res3.issues


def test_heat_pump_power_source_preference_and_plausibility():
    res = heat_pump_engine.evaluate(heat_pump_state(plug_power=800.0, realtime_power=790.0), PARAMS)
    assert res.electrical_power_w == 800.0 and res.electrical_power_source == "plug"
    res2 = heat_pump_engine.evaluate(heat_pump_state(plug_power=None, realtime_power=790.0), PARAMS)
    assert res2.electrical_power_w == 790.0 and res2.electrical_power_source == "midea_internal"
    res3 = heat_pump_engine.evaluate(heat_pump_state(plug_power=800.0, realtime_power=500.0), PARAMS)
    assert any(i.startswith("power_mismatch") for i in res3.issues)


def test_a_short_signal_dropout_is_not_a_data_outage():
    """The adapter drops its signal for about a second every few minutes."""
    from custom_components.thriftherm.engines import boiler as boiler_engine

    ok, since = boiler_engine.signal_with_grace(False, None, 1000.0)
    assert ok is True and since == 1000.0
    ok, since = boiler_engine.signal_with_grace(False, since, 1000.0 + 60)
    assert ok is True and since == 1000.0
    # a real outage still gets through
    ok, since = boiler_engine.signal_with_grace(False, since, 1000.0 + boiler_engine.EBUS_SIGNAL_GRACE_S)
    assert ok is False
    # and the timer restarts once the signal is back
    assert boiler_engine.signal_with_grace(True, since, 2000.0) == (True, None)
    assert boiler_engine.signal_with_grace(None, since, 2000.0) == (None, None)
