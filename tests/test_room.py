from datetime import datetime, time

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.models import Parameters, TimeWindow

from .conftest import PARAMS, room_config, room_state


def test_parse_schedule():
    windows = room_engine.parse_schedule("05:15-07:30, 17:30-22:00")
    assert windows == (TimeWindow(time(5, 15), time(7, 30)), TimeWindow(time(17, 30), time(22, 0)))
    assert room_engine.parse_schedule("") == ()
    with pytest.raises(ValueError):
        room_engine.parse_schedule("5-7")


def test_time_window_across_midnight():
    w = TimeWindow(time(22, 0), time(6, 0))
    assert w.contains(time(23, 30))
    assert w.contains(time(1, 0))
    assert not w.contains(time(12, 0))


def test_target_follows_schedule_and_mode():
    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)
    st = room_state(cfg, temp=20.0)
    monday_evening = datetime(2026, 1, 12, 18, 0)
    monday_noon = datetime(2026, 1, 12, 12, 0)
    saturday_noon = datetime(2026, 1, 17, 12, 0)
    tt = room_engine.target_temperature
    assert tt(st, monday_evening, "auto", PARAMS, 20.0, 9.0, None)[:2] == (21.0, "schedule_comfort")
    assert tt(st, monday_noon, "auto", PARAMS, 20.0, 9.0, None)[:2] == (17.0, "schedule_setback")
    assert tt(st, saturday_noon, "auto", PARAMS, 20.0, 9.0, None)[:2] == (21.0, "schedule_comfort")
    assert tt(st, monday_evening, "away", PARAMS, 20.0, 9.0, None)[:2] == (PARAMS.away_temp, "mode_away")
    assert tt(st, monday_evening, "off", PARAMS, 20.0, 9.0, None)[:2] == (PARAMS.frost_temp, "mode_off_frost_protection")


def test_trend_regression():
    now = 10_000.0
    hist = tuple((now - 1800 + i * 300, 18.0 + i * 0.1) for i in range(7))  # +0.1 K per 5 min = 1.2 K/h
    assert room_engine.trend_k_per_h(hist, now) == pytest.approx(1.2, abs=0.01)
    assert room_engine.trend_k_per_h(hist[:2], now) is None
    assert room_engine.trend_k_per_h((), now) is None


def test_window_status_grace_and_unknown():
    assert room_engine.window_status((("b.w", "on", 120.0),), 90.0) == (True, False, 120.0)
    assert room_engine.window_status((("b.w", "on", 30.0),), 90.0) == (False, False, None)
    assert room_engine.window_status((("b.w", "off", None),), 90.0) == (False, False, None)
    assert room_engine.window_status((("b.w", None, None),), 90.0) == (None, True, None)
    assert room_engine.window_status((("b.a", None, None), ("b.b", "off", None)), 90.0) == (False, False, None)
    assert room_engine.window_status((), 90.0) == (None, False, None)


def test_demand_scaling_and_internal_gains():
    p = Parameters(demand_full_delta_k=2.0, trend_weight_h=0.5, gain_threshold_w=150.0, gain_factor_per_w=0.001)
    assert room_engine.demand(21.0, 19.0, None, None, p) == 1.0
    assert room_engine.demand(21.0, 20.0, None, None, p) == 0.5
    assert room_engine.demand(21.0, 21.5, None, None, p) == 0.0
    # rising trend of 1 K/h with 0.5 h look-ahead reduces demand by 0.25
    assert room_engine.demand(21.0, 20.0, 1.0, None, p) == pytest.approx(0.25)
    # 650 W of PC/server heat: (650-150)*0.001 = 0.5 demand reduction
    assert room_engine.demand(21.0, 19.0, None, 650.0, p) == pytest.approx(0.5)
    assert room_engine.demand(21.0, None, None, None, p) is None


def test_evaluate_room_window_open_blocks_heating():
    cfg = room_config("kueche", comfort=18.5)
    st = room_state(cfg, temp=16.0, window="on", window_open_since=300.0)
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 18, 0), "auto", PARAMS)
    assert res.window_open is True
    assert res.heating_allowed is False
    assert res.demand == 0.0
    assert res.deviation_k == pytest.approx(-2.5)


def test_evaluate_room_uses_trv_fallback_and_reports_issue():
    cfg = room_config("kueche")
    st = room_state(cfg, temp=None, trv_local=19.0)
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 18, 0), "auto", PARAMS)
    assert res.temperature == 19.0
    assert any(i.startswith("using_trv_local_temperature") for i in res.issues)


def test_evaluate_room_dew_point_present():
    cfg = room_config("badezimmer")
    st = room_state(cfg, temp=22.0, rh=70.0)
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 18, 0), "auto", PARAMS)
    assert res.dew_point_c == pytest.approx(16.3, abs=0.3)
    assert res.abs_humidity_g_m3 == pytest.approx(13.6, abs=0.3)
