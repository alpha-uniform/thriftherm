from datetime import datetime

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.learning import HeatRateStats
from custom_components.thriftherm.models import DryingState, Override, RoomConfig

from .conftest import PARAMS, room_config, room_state

MON_EVENING = datetime(2026, 1, 12, 18, 0)  # inside comfort window
MON_NOON = datetime(2026, 1, 12, 12, 0)


def _with(state, **changes):
    return state.__class__(**{**state.__dict__, **changes})


def test_override_beats_schedule_and_expires():
    cfg = room_config("kueche", comfort=18.5)
    now_ts = MON_EVENING.timestamp()
    st = _with(room_state(cfg, temp=19.0), override=Override(22.0, now_ts + 600, now_ts - 60))
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS)
    assert res.target == 22.0 and res.target_reason == "override"
    assert res.override_until_ts == now_ts + 600
    expired = _with(st, override=Override(22.0, now_ts - 1, now_ts - 7200))
    res2 = room_engine.evaluate_room(expired, MON_EVENING, "auto", PARAMS)
    assert res2.target == 18.5 and res2.override_until_ts is None


def test_override_also_applies_in_away_mode():
    cfg = room_config("kueche")
    now_ts = MON_EVENING.timestamp()
    st = _with(room_state(cfg, temp=16.0), override=Override(21.0, now_ts + 600, now_ts))
    res = room_engine.evaluate_room(st, MON_EVENING, "away", PARAMS)
    assert res.target == 21.0 and res.target_reason == "override"


def test_away_preheat_starts_early_enough():
    cfg = room_config("wohnzimmer", comfort=18.5, setback=17.0)
    # return at 18:00 Monday (comfort target 18.5); room at 15 °C; default radiator rate 0.8 K/h
    return_ts = MON_EVENING.timestamp()
    st = room_state(cfg, temp=15.0)
    # 5 h before return: deficit 3.5 K / 0.8 K/h = 4.375 h + 20 min margin -> preheat starts ~4.7 h before
    six_h_before = datetime(2026, 1, 12, 12, 0)
    res = room_engine.evaluate_room(st, six_h_before, "away", PARAMS, away_return_ts=return_ts)
    assert res.target_reason == "mode_away" and res.target == PARAMS.away_temp
    assert res.preheat_start_ts == pytest.approx(return_ts - 4.375 * 3600 - 1200, abs=1)
    four_h_before = datetime(2026, 1, 12, 14, 0)
    res2 = room_engine.evaluate_room(st, four_h_before, "away", PARAMS, away_return_ts=return_ts)
    assert res2.target_reason == "away_preheat" and res2.target == 18.5


def test_away_preheat_uses_learned_rate():
    cfg = room_config("badezimmer", heat_pump=True, comfort=21.0)
    return_ts = MON_EVENING.timestamp()
    st = _with(room_state(cfg, temp=15.0), heat_rate_k_h=3.0)  # fast room
    two_h_before = datetime(2026, 1, 12, 16, 0)
    res = room_engine.evaluate_room(st, two_h_before, "away", PARAMS, away_return_ts=return_ts)
    # deficit 6 K / 3 K/h = 2 h + 20 min -> should already be preheating at 2 h before
    assert res.target_reason == "away_preheat"


def test_away_humidity_guard():
    cfg = room_config("badezimmer", heat_pump=True)
    st = room_state(cfg, temp=16.0, rh=90.0)  # dew point ≈ 14.4 -> margin 1.6 K < 3 K
    res = room_engine.evaluate_room(st, MON_NOON, "away", PARAMS)
    assert res.target_reason == "away_humidity_guard"
    assert res.target == pytest.approx(17.0)


def test_drying_mode_starts_on_humidity_load_and_keeps_heating_with_window_open():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    # 3 h of baseline at 9 g/m³ (22 °C / 46 %), then a shower: 22 °C / 85 % ≈ 16.5 g/m³
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=20.5, rh=85.0, window="on", window_open_since=300.0), abs_humidity_history=baseline)
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS)
    assert res.drying.active is True
    assert res.drying_reason == "drying_started_humidity_load"
    assert res.heat_pump_allowed is True  # Midea keeps heating despite the open window
    assert res.heating_allowed is False  # radiator follows the window (Better Thermostat)
    assert res.target == PARAMS.drying_target_temp and res.target_reason == "bathroom_drying"
    assert res.demand is not None and res.demand > 0


def test_drying_mode_ends_when_humidity_normal():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    active = DryingState(True, now_ts - 900, 9.0)
    st = _with(room_state(cfg, temp=22.0, rh=48.0), abs_humidity_history=baseline, drying=active)
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS)
    assert res.drying.active is False and res.drying_reason == "drying_ended_humidity_normal"


def test_drying_mode_timeout_and_window_ineffective():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline, drying=DryingState(True, now_ts - 4000, 9.0))
    assert room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS).drying_reason == "drying_ended_timeout"
    st2 = _with(
        room_state(cfg, temp=22.0, rh=85.0, window="on", window_open_since=1500.0),
        abs_humidity_history=baseline,
        drying=DryingState(True, now_ts - 1500, 9.0),
    )
    res = room_engine.evaluate_room(st2, MON_EVENING, "auto", PARAMS)
    assert res.drying_reason == "drying_ended_window_ineffective"
    assert res.heating_allowed is False  # back to normal window logic


def test_drying_mode_not_for_rooms_without_flag():
    cfg = room_config("badezimmer", heat_pump=True)  # flag False
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline)
    assert room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS).drying.active is False


def test_heat_rate_learning_from_episode():
    hs = HeatRateStats()
    t0 = 1_000_000.0
    # 40 min heating, 18.0 -> 19.6 °C = 2.4 K/h
    for i in range(9):
        assert hs.observe("kueche", t0 + i * 300, 18.0 + i * 0.2, heating=True, window_open=False) is None
    learned = hs.observe("kueche", t0 + 2700, 19.6, heating=False, window_open=False)
    assert learned == pytest.approx(2.4, abs=0.15)
    assert hs.rate("kueche") == pytest.approx(learned, abs=0.01)
    restored = HeatRateStats.from_storage(hs.to_storage())
    assert restored.rate("kueche") == hs.rate("kueche")


def test_heat_rate_rejects_short_or_falling_episodes():
    hs = HeatRateStats()
    t0 = 1_000_000.0
    hs.observe("bad", t0, 18.0, True, False)
    hs.observe("bad", t0 + 600, 18.4, True, False)
    assert hs.observe("bad", t0 + 900, 18.5, False, False) is None  # < 30 min
    assert hs.rate("bad") is None
    hs.observe("bad", t0 + 1000, 18.0, True, False)
    hs.observe("bad", t0 + 2000, 17.0, True, False)  # falling -> restart
    assert hs.observe("bad", t0 + 4000, 17.2, False, False) is None


def test_preheat_start_without_temperature_falls_back_to_margin():
    ts = room_engine.preheat_start(1000.0, 21.0, None, 1.0, 600.0)
    assert ts == 400.0
