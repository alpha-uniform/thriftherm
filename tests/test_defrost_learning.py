import pytest

from custom_components.thriftherm.engines import cop as cop_engine, defrost
from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker
from custom_components.thriftherm.engines.learning import CopMap
from custom_components.thriftherm.models import HeatPumpSample, Parameters

from .conftest import PARAMS, heat_pump_state, snapshot

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


def test_a_compressor_start_in_cooling_or_drying_is_no_defrost():
    # the same coil temperatures as a defrost: four such starts in 90 minutes locked the heating out for two hours
    hist = _hist([(110, 50, 800, -4.0, 44, 20, 38), (60, 50, 700, 0.0, 40, 20, 34), (0, 50, 400, 6.0, 30, 20, 25)])
    starts = (NOW - 80 * 60, NOW - 55 * 60, NOW - 30 * 60)
    for run_state in ("cooling_user", "fan_only", "off"):
        res = defrost.evaluate(_state(hist), run_state, 2.0, 60.0, NOW, PARAMS, starts, None, None)
        assert (res.suspected, res.indicators, res.block_reason) == (False, (), None)
    heating = defrost.evaluate(_state(hist), "heating_stable", 2.0, 60.0, NOW, PARAMS, starts, None, None)
    assert {"D1_coil_reversal", "D4_power_drop"} <= set(heating.indicators) and heating.block_reason == "frequent_defrost_cycles"


def test_a_heat_pump_that_is_the_only_source_allowed_is_not_locked_out_for_being_dear():
    hist = _hist([(60, 50, 800, 0.0, 44, 20, 38), (0, 50, 800, 0.0, 44, 20, 38)])
    dear_since = NOW - 31 * 60
    res = defrost.evaluate(_state(hist), "heating_stable", 3.0, 60.0, NOW, PARAMS, (), dear_since, None, economic_lockout=False)
    assert (res.block_reason, res.block_until_ts) == (None, None)
    tracker = HeatPumpTracker()
    tracker.inefficient_since = NOW - 31 * 60
    res, changed = tracker.defrost(snapshot({}, heat_pump=_state(hist), mode="midea_only"), "heating_stable", PARAMS, NOW)
    assert (res.block_reason, tracker.block_until, changed) == (None, None, False)
    res, changed = tracker.defrost(snapshot({}, heat_pump=_state(hist), mode="auto"), "heating_stable", PARAMS, NOW)
    assert res.block_reason == "sustained_inefficiency" and tracker.block_until == NOW + PARAMS.icing_block_min * 60 and changed


def test_a_lockout_for_dear_heat_ends_once_only_the_heat_pump_may_heat():
    """Switched to "heat pump only" during it, it ran on to its end: no heat at all instead of dear heat."""
    hist = _hist([(60, 50, 800, 0.0, 44, 20, 38), (0, 50, 800, 0.0, 44, 20, 38)])
    tracker = HeatPumpTracker()
    tracker.inefficient_since = NOW - 31 * 60
    res, _ = tracker.defrost(snapshot({}, heat_pump=_state(hist), mode="auto"), "heating_stable", PARAMS, NOW)
    assert res.block_reason == "sustained_inefficiency" and tracker.block_reason == "sustained_inefficiency"
    res, changed = tracker.defrost(snapshot({}, heat_pump=_state(hist), mode="midea_only"), "heating_stable", PARAMS, NOW + 60)
    assert (res.block_reason, res.block_until_ts, changed) == (None, None, True)
    assert (tracker.block_until, tracker.block_reason) == (None, None)


def test_a_lockout_for_dear_heat_keeps_its_own_cause_while_it_runs():
    """Review 2026-10-14: from its second cycle on it was shown as an icing lockout."""
    from custom_components.thriftherm import texts

    hist = _hist([(60, 50, 800, 0.0, 44, 20, 38), (0, 50, 800, 0.0, 44, 20, 38)])
    tracker = HeatPumpTracker()
    tracker.inefficient_since = NOW - 31 * 60
    res, _ = tracker.defrost(snapshot({}, heat_pump=_state(hist), mode="auto"), "heating_stable", PARAMS, NOW)
    assert res.block_reason == "sustained_inefficiency"
    tracker.track_efficiency(None, 2.9, 0.05, NOW + 60)  # stopped by the lockout: no COP, the timer ends
    stopped = _hist([(0, 0, 5, 0.0, 20, 20, 20)])
    res, _ = tracker.defrost(snapshot({}, heat_pump=_state(stopped), mode="auto"), "off", PARAMS, NOW + 60)
    assert (res.block_reason, res.block_until_ts) == ("sustained_inefficiency", NOW + PARAMS.icing_block_min * 60)
    assert texts.label(res.block_reason, "de") == "Wärme zu teuer (COP unter Break-even)"
    assert texts.label(res.block_reason, "en") == "Heat too expensive (COP below break-even)"


def test_a_lockout_that_protects_the_unit_runs_to_its_end_also_where_the_heat_was_dear_as_well():
    iced = _hist([(dt, 50, 800, -12.0, 40, 20, 20.5) for dt in range(600, -1, -60)])  # its heat output collapsed with it
    tracker = HeatPumpTracker()
    tracker.inefficient_since = NOW - 31 * 60
    res, _ = tracker.defrost(snapshot({}, heat_pump=_state(iced), mode="auto"), "heating_stable", PARAMS, NOW)
    assert res.block_reason == "outdoor_coil_iced" and tracker.block_reason == "outdoor_coil_iced"  # the cause that protects
    thawed = _hist([(0, 50, 800, 0.0, 44, 20, 38)])
    res, changed = tracker.defrost(snapshot({}, heat_pump=_state(thawed), mode="midea_only"), "heating_stable", PARAMS, NOW + 60)
    assert (res.block_reason, res.block_until_ts, changed) == ("icing_lockout_active", NOW + PARAMS.icing_block_min * 60, False)


def test_a_lockout_whose_cause_lasts_moves_its_end_in_steps_not_every_cycle():
    # the end was renewed every minute, and with it the whole store was written every minute
    tracker = HeatPumpTracker()
    tracker.inefficient_since = NOW - 31 * 60
    ends, changes = [], 0
    for minute in range(31):
        res, changed = tracker.defrost(snapshot({}), "heating_stable", PARAMS, NOW + minute * 60.0)
        ends.append(res.block_until_ts)
        changes += changed
    hold = PARAMS.icing_block_min * 60
    assert changes == 4 and sorted(set(ends)) == [NOW + hold + step * 600.0 for step in range(4)]
    assert tracker.block_until == ends[-1] == NOW + hold + 1800.0  # never more than ten minutes behind


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


# ---- a defrost of the Midea PortaSplit, 2026-10-13 09:58-10:13 (HA history)
def test_the_circuit_running_backwards_is_a_defrost():
    """89 Hz, indoor fan stopped, indoor coil -15 °C; the outdoor coil rose by 6 K in five minutes, too slowly for D1."""
    heating = _hist([(60, 28, 380, -3.5, 30.0, 21.5, 35.0), (0, 28, 385, -3.0, 30.0, 21.5, 35.0)])
    res = defrost.evaluate(_state(heating), "heating_stable", 4.5, 70.0, NOW, PARAMS, (), None, None)
    assert "D6_reverse_cycle" not in res.indicators and res.kind == "none"

    backwards = _hist([(47, 56, 300, 0.5, -7.0, 21.5, 22.0), (0, 80, 400, 1.0, -15.0, 21.5, 21.0)])
    res = defrost.evaluate(_state(backwards), "heating_stable", 4.5, 70.0, NOW, PARAMS, (), None, None)
    assert "D6_reverse_cycle" in res.indicators and "D1_coil_reversal" not in res.indicators
    assert (res.kind, res.suspected, res.cycles_last_90min) == ("cycle", True, 1)

    def indicators(hist):
        return defrost.evaluate(_state(hist), "heating_stable", 4.5, 70.0, NOW, PARAMS, (), None, None).indicators

    once = _hist([(60, 28, 380, -3.5, 30.0, 21.5, 35.0), (0, 80, 400, 1.0, -15.0, 21.5, 21.0)])  # one odd reading
    assert "D6_reverse_cycle" not in indicators(once)
    no_intake = _hist([(47, 56, 300, 0.5, -7.0, None, None), (0, 80, 400, 1.0, -15.0, None, None)])
    assert "D6_reverse_cycle" in indicators(no_intake)


def test_a_defrost_is_counted_once_however_long_it_lasts():
    """Counted every five minutes, a defrost of a quarter of an hour made three, and one more within 90 minutes a lockout."""
    tracker = HeatPumpTracker()
    for minute in range(16):  # the whole defrost long, the coil stays cold
        t = NOW + minute * 60.0
        hist = tuple(HeatPumpSample(t - dt, 89, 400, 1.0, -15.0, 21.5, 21.0) for dt in (60.0, 0.0))
        res, _ = tracker.defrost(snapshot({}, heat_pump=_state(hist)), "heating_stable", PARAMS, t)
    assert tracker.defrost_cycle_starts == [NOW] and res.cycles_last_90min == 1 and res.block_reason is None
    for minute in range(16, 20):  # heating again
        t = NOW + minute * 60.0
        hist = tuple(HeatPumpSample(t - dt, 40, 500, -2.0, 38.0, 21.5, 36.0) for dt in (60.0, 0.0))
        tracker.defrost(snapshot({}, heat_pump=_state(hist)), "heating_stable", PARAMS, t)
    t = NOW + 3600.0  # the next defrost an hour later counts again
    hist = tuple(HeatPumpSample(t - dt, 89, 400, 1.0, -15.0, 21.5, 21.0) for dt in (60.0, 0.0))
    res, _ = tracker.defrost(snapshot({}, heat_pump=_state(hist)), "heating_stable", PARAMS, t)
    assert tracker.defrost_cycle_starts == [NOW, t] and res.cycles_last_90min == 2
