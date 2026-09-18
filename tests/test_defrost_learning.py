import pytest

from custom_components.thriftherm.engines import cop as cop_engine, defrost
from custom_components.thriftherm.engines.learning import CopMap
from custom_components.thriftherm.models import HeatPumpSample, Parameters

from .conftest import PARAMS, heat_pump_state

NOW = 100_000.0


def _hist(points):
    """points: list of (dt_s_before_now, hz, power, out_coil, in_coil, intake, outlet)."""
    return tuple(HeatPumpSample(NOW - dt, hz, p, oc, ic, ti, to) for dt, hz, p, oc, ic, ti, to in points)


def _state(history):
    m = heat_pump_state()
    return m.__class__(**{**m.__dict__, "history": history})


def test_no_indicators_in_normal_heating():
    hist = _hist([(120, 50, 800, 0.0, 44, 20, 38), (60, 50, 800, 0.2, 44, 20, 38), (0, 50, 800, 0.1, 44, 20, 38)])
    res = defrost.evaluate(_state(hist), "heating_stable", 5.0, 70.0, NOW, PARAMS, (), None, None)
    assert res.suspected is False and res.kind == "none" and res.block_reason is None


def test_reversal_detected_as_cycle():
    hist = _hist([(110, 50, 800, -4.0, 44, 20, 38), (60, 50, 700, 0.0, 40, 20, 34), (0, 50, 500, 6.0, 30, 20, 25)])
    res = defrost.evaluate(_state(hist), "heating_stable", 2.0, 85.0, NOW, PARAMS, (), None, None)
    assert "D1_coil_reversal" in res.indicators
    assert "D5_icing_conditions" in res.indicators
    assert res.kind == "cycle" and res.suspected
    assert res.block_reason is None  # a single cycle is normal


def test_frequent_cycles_lock_out():
    hist = _hist([(110, 50, 800, -4.0, 44, 20, 38), (0, 50, 500, 6.0, 30, 20, 25)])
    starts = (NOW - 80 * 60, NOW - 55 * 60, NOW - 30 * 60)
    res = defrost.evaluate(_state(hist), "heating_stable", 2.0, 85.0, NOW, PARAMS, starts, None, None)
    assert res.kind == "persistent"
    assert res.block_reason == "frequent_defrost_cycles"
    assert res.block_until_ts == pytest.approx(NOW + PARAMS.icing_block_min * 60)


def test_cold_evaporator_alone_is_normal_full_load_operation():
    """12 K below the air at full load in frost is how an evaporator works, not ice."""
    pts = [(dt, 50, 800, -12.0, 40, 20, 30) for dt in range(600, -1, -60)]
    res = defrost.evaluate(_state(_hist(pts)), "heating_stable", 0.0, 60.0, NOW, PARAMS, (), None, None)
    assert "D3_iced_coil" in res.indicators
    assert res.block_reason is None and res.block_until_ts is None


def test_iced_coil_locks_out_once_heat_delivery_collapses():
    # same cold coil, but the outlet air is barely warmer than the intake
    pts = [(dt, 50, 800, -12.0, 40, 20, 20.5) for dt in range(600, -1, -60)]
    res = defrost.evaluate(_state(_hist(pts)), "heating_stable", 0.0, 60.0, NOW, PARAMS, (), None, None)
    assert {"D3_iced_coil", "D2_cold_outlet"} <= set(res.indicators)
    assert res.block_reason == "outdoor_coil_iced"


def test_sustained_inefficiency_locks_out():
    hist = _hist([(60, 50, 800, 0.0, 44, 20, 38), (0, 50, 800, 0.0, 44, 20, 38)])
    res = defrost.evaluate(_state(hist), "heating_stable", 3.0, 60.0, NOW, PARAMS, (), NOW - 31 * 60, None)
    assert res.block_reason == "sustained_inefficiency"


def test_existing_lockout_persists_until_expiry():
    hist = _hist([(0, 50, 800, 0.0, 44, 20, 38)])
    res = defrost.evaluate(_state(hist), "heating_stable", 3.0, 60.0, NOW, PARAMS, (), None, NOW + 600)
    assert res.block_reason == "icing_lockout_active" and res.block_until_ts == NOW + 600
    res2 = defrost.evaluate(_state(hist), "heating_stable", 3.0, 60.0, NOW, PARAMS, (), None, NOW - 1)
    assert res2.block_reason is None and res2.block_until_ts is None


def test_duct_factor_scales_airflow_and_cop():
    m = heat_pump_state()
    base = cop_engine.instant(m, "heating_stable", 800.0, PARAMS)
    scaled = cop_engine.instant(m, "heating_stable", 800.0, Parameters(airflow_curve=PARAMS.airflow_curve, duct_factor=0.75))
    assert scaled.airflow_m3h == pytest.approx(base.airflow_m3h * 0.75, rel=0.01)
    assert scaled.raw == pytest.approx(base.raw * 0.75, rel=0.01)


def test_settle_gate_blocks_rising_outlet():
    m = heat_pump_state()
    rising = tuple((NOW - 120 + i * 10, 25.0 + i * 0.5) for i in range(13))  # 3 K/min
    m_rising = m.__class__(**{**m.__dict__, "outlet_temp_history": rising})
    res = cop_engine.instant(m_rising, "heating_stable", 800.0, PARAMS)
    assert "outlet_temp_not_settled" in res.gate_reasons
    flat = tuple((NOW - 120 + i * 10, 38.0 + (i % 2) * 0.1) for i in range(13))
    m_flat = m.__class__(**{**m.__dict__, "outlet_temp_history": flat})
    assert "outlet_temp_not_settled" not in cop_engine.instant(m_flat, "heating_stable", 800.0, PARAMS).gate_reasons
    assert cop_engine.outlet_slope_k_per_min(()) == 0.0


def test_cop_map_bins_and_expected():
    cm = CopMap()
    for v in (3.0, 3.2, 3.1, 2.9, 3.0):
        cm.add(4.5, v)
    assert cm.expected(5.0) == pytest.approx(3.04, abs=0.02)
    assert cm.expected(12.0) is None  # no data there
    cm.add(6.5, 3.5)
    assert cm.expected(7.0) == pytest.approx(3.04, abs=0.02)  # neighbour bin (4..6) has enough samples, 6..8 not yet
    restored = CopMap.from_storage(cm.to_storage())
    assert restored.to_dict() == cm.to_dict()
    assert "+4..+6" in cm.to_dict()
    assert CopMap.from_storage(None).to_dict() == {}
