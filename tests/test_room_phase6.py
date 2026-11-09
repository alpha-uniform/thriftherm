from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.learning import HEAT_BY_BOILER as BOILER
from custom_components.thriftherm.engines.learning import HEAT_BY_BOTH as BOTH
from custom_components.thriftherm.engines.learning import HEAT_BY_HEAT_PUMP as HEAT_PUMP
from custom_components.thriftherm.engines.learning import HeatRateStats
from custom_components.thriftherm.models import DryingState, Override, RoomConfig, ScheduleState

from .conftest import MISSING, PARAMS, room_config, room_state

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


def test_away_return_target_holds_across_the_clock_change():
    # code audit 2026-09-28 (F16): the return was found by adding seconds to the clock, an hour off
    # across the end of summer time; with the text windows that can pick the wrong target
    berlin = ZoneInfo("Europe/Berlin")
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)  # weekdays comfort from 17:30
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=berlin)  # summer time
    return_ts = datetime(2026, 10, 26, 17, 0, tzinfo=berlin).timestamp()  # Monday, winter time: still setback
    res = room_engine.evaluate_room(room_state(cfg, temp=15.0), saturday, "away", PARAMS, away_return_ts=return_ts)
    # its preheat aims at the window from 17:30 (since 2026-11-02), not at the return taken for 18:00, in comfort
    window = datetime(2026, 10, 26, 17, 30, tzinfo=berlin).timestamp()
    deficit = 21.0 - 15.0
    rate = PARAMS.default_heat_rate_radiator_k_h
    assert res.preheat_start_ts == pytest.approx(window - deficit / rate * 3600 - PARAMS.preheat_margin_s, abs=1)


def test_away_humidity_guard():
    cfg = room_config("badezimmer", heat_pump=True)
    st = room_state(cfg, temp=16.0, rh=90.0)  # dew point ≈ 14.4 -> margin 1.6 K < 3 K
    res = room_engine.evaluate_room(st, MON_NOON, "away", PARAMS)
    assert res.target_reason == "away_humidity_guard"
    assert res.target == pytest.approx(17.0)


def _after_the_shower(state, at: datetime, minutes: int = 4, **kwargs):
    """Minute by minute with the humidity standing where it is: the drying starts once it climbs no more."""
    res = None
    for minute in range(minutes + 1):
        res = room_engine.evaluate_room(state, at + timedelta(minutes=minute), "auto", PARAMS, **kwargs)
        state = _with(state, drying=res.drying)
    return res


def test_drying_mode_starts_on_humidity_load_and_keeps_heating_with_window_open():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    # 3 h of baseline at 9 g/m³ (22 °C / 46 %), then a shower: 22 °C / 85 % ≈ 16.5 g/m³
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=20.5, rh=85.0, window="on", window_open_since=300.0), abs_humidity_history=baseline)
    res = _after_the_shower(st, MON_EVENING, minutes=3)  # three minutes without a new highest value
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


def test_drying_mode_ends_at_its_time_limit_and_goes_on_with_the_window_open():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline, drying=DryingState(True, now_ts - 4000, 9.0))
    assert room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS).drying_reason == "drying_ended_timeout"
    # a window wide open for 25 minutes is how a bathroom is dried (the owner, 2026-10-29): it goes on
    st2 = _with(
        room_state(cfg, temp=22.0, rh=85.0, window="on", window_open_since=1500.0),
        abs_humidity_history=baseline,
        drying=DryingState(True, now_ts - 1500, 9.0),
    )
    res = room_engine.evaluate_room(st2, MON_EVENING, "auto", PARAMS)
    assert res.drying.active and res.drying_reason == "drying_active"
    assert res.heating_allowed is False and res.heat_pump_allowed is True  # the radiator follows the window, the warm air goes on


def test_drying_does_not_start_again_right_after_its_time_limit():
    # measured 2026-09-25: the baseline still held the air from before the shower, so the
    # drying started again a minute after its 60 min ran out
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline, drying=DryingState(True, now_ts - 4000, 9.0))
    ended = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS)
    assert ended.drying_reason == "drying_ended_timeout" and ended.drying.ended_ts == now_ts

    later = datetime.fromtimestamp(now_ts + 60)
    again = room_engine.evaluate_room(_with(st, drying=ended.drying), later, "auto", PARAMS)
    assert not again.drying.active and again.target_reason != "bathroom_drying"
    after_window = datetime.fromtimestamp(now_ts + 3 * 3600 + 60)
    fresh = tuple((now_ts + 3600 + i * 300, 9.0) for i in range(30))
    st3 = _with(st, drying=again.drying, abs_humidity_history=fresh)
    st3 = _with(st3, window_readings=(("binary_sensor.badezimmer_window", "on", 120.0),))  # dried with the window open
    assert _after_the_shower(st3, after_window).drying.active  # a new shower dries again


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
        assert hs.observe("kueche", t0 + i * 300, 18.0 + i * 0.2, source=BOILER, window_open=False) is None
    learned = hs.observe("kueche", t0 + 2700, 19.6, source=None, window_open=False)
    assert learned == pytest.approx(2.4, abs=0.15)
    assert hs.rate("kueche", BOILER) == pytest.approx(learned, abs=0.01)
    restored = HeatRateStats.from_storage(hs.to_storage())
    assert restored.rate("kueche", BOILER) == hs.rate("kueche", BOILER)


def test_heat_rate_rejects_short_or_falling_episodes():
    hs = HeatRateStats()
    t0 = 1_000_000.0
    hs.observe("bad", t0, 18.0, BOILER, False)
    hs.observe("bad", t0 + 600, 18.4, BOILER, False)
    assert hs.observe("bad", t0 + 900, 18.5, None, False) is None  # < 30 min
    assert hs.rate("bad", BOILER) is None
    hs.observe("bad", t0 + 1000, 18.0, BOILER, False)
    hs.observe("bad", t0 + 2000, 17.0, BOILER, False)  # falling -> restart
    assert hs.observe("bad", t0 + 4000, 17.2, None, False) is None



def test_a_heat_up_ends_at_its_highest_point_not_on_the_plateau_after_it():
    # Review 2026-11-08: the heat pump at its least speed for a bathroom 0.1-0.2 below its target, 1 degree in the
    # first hour, then four hours just below the target with the source still on: 0.2 an hour was stored for 1.0.
    hs = HeatRateStats()
    for i in range(13):  # 19.0 -> 20.0 in an hour, a sample every five minutes
        assert hs.observe("bad", i * 300.0, 19.0 + i / 12, HEAT_PUMP, False, 5.0) is None
    learned = []
    for i in range(13, 61):  # four hours on the plateau, the sensor flickering by a tenth
        learned.append(hs.observe("bad", i * 300.0, 20.0 - 0.1 * (i % 2), HEAT_PUMP, False, 5.0))
    learned.append(hs.observe("bad", 61 * 300.0, 20.0, None, False, 5.0))
    assert [rate for rate in learned if rate is not None] == [pytest.approx(1.0, abs=0.01)]  # one value, closed on the plateau
    assert hs.rate("bad", HEAT_PUMP) == pytest.approx(1.0, abs=0.01)
    assert hs.to_dict()["bad"][HEAT_PUMP]["mean_gradient_k"] == pytest.approx(14.5, abs=0.1)  # of the way up


def test_a_heat_up_that_falls_back_from_its_highest_point_keeps_the_way_up():
    hs = HeatRateStats()
    for i in range(7):  # an hour of boiler heat, 0.6 degrees up
        hs.observe("wz", i * 600.0, 19.0 + i * 0.1, BOILER, False)
    assert hs.observe("wz", 7 * 600.0, 19.2, BOILER, False) == pytest.approx(0.6, abs=0.01)  # 0.4 below its top
    assert hs.episodes["wz"].start_temp == 19.2  # and counted on from there

def _heat_up(hs: HeatRateStats, room: str, source: str, t0: float, start: float, rise_k_h: float, outdoor=None) -> float | None:
    """An hour of heat-up from `source` in 10-minute samples, then the source goes off."""
    for i in range(7):
        hs.observe(room, t0 + i * 600, start + rise_k_h * i / 6, source, False, outdoor)
    return hs.observe(room, t0 + 3600, start + rise_k_h, None, False, outdoor)


def test_heat_rates_are_kept_per_source():
    # seen 2026-10-20 to 30: the heat pump at its least speed warmed the bedroom 0.46-0.5 degrees an hour, the
    # boiler 0.23-0.6, and quick heat-ups at 650-930 W 1.4-2.1: one list for both mixed them up
    hs = HeatRateStats()
    assert _heat_up(hs, "bad", BOILER, 0.0, 19.0, 0.6) == pytest.approx(0.6, abs=0.01)
    assert _heat_up(hs, "bad", HEAT_PUMP, 10_000.0, 19.0, 1.2) == pytest.approx(1.2, abs=0.01)
    assert (hs.rate("bad", BOILER), hs.rate("bad", HEAT_PUMP)) == (pytest.approx(0.6, abs=0.01), pytest.approx(1.2, abs=0.01))
    assert hs.rate("kueche", BOILER) is None
    restored = HeatRateStats.from_storage(hs.to_storage(), heat_pump_rooms={"bad"})
    assert restored.rates == hs.rates and restored.gradients == hs.gradients
    shown = hs.to_dict()["bad"]
    assert shown[BOILER]["median_k_h"] == pytest.approx(0.6, abs=0.01) and shown[HEAT_PUMP]["episodes"] == 1


def test_a_change_of_source_ends_the_episode_without_a_value():
    hs = HeatRateStats()
    for i in range(7):  # an hour of boiler heat, 0.6 degrees up
        hs.observe("bad", i * 600.0, 19.0 + i * 0.1, BOILER, False)
    for i in range(7, 14):  # then the heat pump takes over, as the boiler stops helping
        hs.observe("bad", i * 600.0, 19.0 + i * 0.1, HEAT_PUMP, False)
    assert hs.rates == {}  # the boiler's hour went without a value: the radiator still gave off its heat
    assert hs.observe("bad", 14 * 600.0, 20.4, None, False) == pytest.approx(0.6, abs=0.01)  # the heat pump's own hour counts
    assert hs.rate("bad", BOILER) is None


def test_both_sources_at_once_teach_neither():
    hs = HeatRateStats()
    for i in range(7):
        hs.observe("bad", i * 600.0, 19.0 + i * 0.1, HEAT_PUMP, False)
    for i in range(7, 14):  # the boiler adds the peak
        hs.observe("bad", i * 600.0, 19.0 + i * 0.2, BOTH, False)
    assert hs.observe("bad", 14 * 600.0, 22.0, None, False) is None
    assert hs.rates == {} and hs.episodes == {}


def test_old_flat_rates_go_to_the_boiler_for_rooms_without_a_heat_pump_and_those_of_heat_pump_rooms_are_dropped():
    # which source a heat pump room's came from is not known, and the heat pump's were mostly quick heat-ups
    old = {"rates": {"wohnzimmer": [0.6, 0.8], "badezimmer": [1.5, 0.6]},
           "gradients": {"wohnzimmer": [12.0, None], "badezimmer": [14.0, 15.0]}}
    hs = HeatRateStats.from_storage(old, heat_pump_rooms={"badezimmer"})
    assert hs.rates == {"wohnzimmer": {BOILER: [0.6, 0.8]}}
    assert hs.gradients == {"wohnzimmer": {BOILER: [12.0, None]}}
    assert hs.rate("wohnzimmer", BOILER) == 0.7 and hs.rate("wohnzimmer", HEAT_PUMP) is None


def test_the_planned_rate_is_projected_onto_the_weather_where_the_heating_power_is_known():
    # at 3 °C outside the heat pump's minimum barely holds the bathroom, at 8 °C it adds about 0.5 an hour
    hs = HeatRateStats()
    _heat_up(hs, "bad", HEAT_PUMP, 0.0, 19.5, 0.5, outdoor=5.0)  # mean gradient 15 K
    power = hs.heating_power_k_h("bad", HEAT_PUMP, 0.02)
    assert power == pytest.approx(0.8, abs=0.01)  # 0.5 + 0.02 * 15
    assert hs.planning_rate("bad", HEAT_PUMP, 0.02, 20.0, 0.0) == pytest.approx(0.4, abs=0.01)  # colder: slower
    assert hs.planning_rate("bad", HEAT_PUMP, 0.02, 20.0, 10.0) == pytest.approx(0.6, abs=0.01)  # milder: faster
    assert hs.planning_rate("bad", HEAT_PUMP, 0.02, 20.0, -40.0) == 0.1  # never below the least rate learned
    assert hs.planning_rate("bad", HEAT_PUMP, None, 20.0, 0.0) == 0.5  # no cool-down known: the median
    assert hs.planning_rate("bad", HEAT_PUMP, 0.02, 20.0, None) == 0.5  # no outdoor temperature: the median
    assert hs.planning_rate("bad", BOILER, 0.02, 20.0, 0.0) is None  # nothing learned: the default applies


def _away_return_planned_by_each_source(temp: float, now: datetime):
    """A heat pump bathroom planned for the return on Monday evening, by the heat pump and by the boiler."""
    st = room_state(room_config("badezimmer", heat_pump=True, comfort=21.0, setback=17.0), temp=temp)
    return_ts = MON_EVENING.timestamp()
    by_heat_pump, by_boiler = (
        room_engine.evaluate_room(_with(st, preheat_by_heat_pump=flag), now, "away", PARAMS, away_return_ts=return_ts)
        for flag in (True, False)
    )
    return st, return_ts, by_heat_pump, by_boiler


def test_preheat_uses_the_rate_of_the_source_expected_to_heat():
    early = datetime(2026, 1, 12, 12, 0)
    st, return_ts, by_heat_pump, by_boiler = _away_return_planned_by_each_source(19.5, early)
    margin = PARAMS.preheat_margin_s
    assert by_heat_pump.preheat_start_ts == pytest.approx(return_ts - 1.5 / 0.5 * 3600 - margin, abs=1)  # 0.5 an hour
    assert by_boiler.preheat_start_ts == pytest.approx(return_ts - 1.5 / 0.8 * 3600 - margin, abs=1)  # the radiator's 0.8
    unsaid = room_engine.evaluate_room(st, early, "away", PARAMS, away_return_ts=return_ts)
    assert unsaid.preheat_start_ts == by_heat_pump.preheat_start_ts  # not said: the room's own heat source


def test_heat_pump_preheat_lead_is_capped_at_four_hours():
    # simulated 2026-10-20 to 30: beyond four hours the boiler adds the peak at less cost than a heat pump
    # warming from the small hours on (the behind check calls it); where no boiler helps, four hours cost less
    # than twelve and keep the night setback (2026-10-30)
    _, return_ts, by_heat_pump, by_boiler = _away_return_planned_by_each_source(16.0, datetime(2026, 1, 12, 6, 0))  # 10 h at 0.5
    assert by_heat_pump.preheat_start_ts == pytest.approx(return_ts - 4 * 3600, abs=1)
    assert by_boiler.preheat_start_ts == pytest.approx(return_ts - 5.0 / 0.8 * 3600 - PARAMS.preheat_margin_s, abs=1)


def test_preheat_start_without_temperature_falls_back_to_margin():
    ts = room_engine.preheat_start(1000.0, 21.0, None, 1.0, 600.0)
    assert ts == 400.0


def _humid_bathroom(drying: DryingState, **room):
    """A bathroom with drying mode after a shower (85 %), over three hours of dry air before it."""
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    return _with(room_state(cfg, temp=22.0, rh=85.0, **room), abs_humidity_history=baseline, drying=drying)


def test_a_drying_with_the_window_open_goes_on_without_stopping_and_starting():
    # found in the code 2026-10-13: 39 starts and stops in 40 minutes with the window open; since 2026-10-29 an open
    # window is how the bathroom is dried, so it simply goes on until the air is dry or its time is up
    now_ts = MON_EVENING.timestamp()
    drying, changes = DryingState(True, now_ts - 1500, 9.0), 0
    for minute in range(30):
        st = _humid_bathroom(drying, window="on", window_open_since=1500.0 + minute * 60)
        res = room_engine.evaluate_room(st, MON_EVENING + timedelta(minutes=minute), "auto", PARAMS)
        changes += res.drying.active != drying.active
        drying = res.drying
    assert changes == 0 and drying.active


def test_a_drying_starts_also_when_the_window_has_been_open_for_long():
    opened = _humid_bathroom(DryingState(), window="on", window_open_since=1500.0)
    assert _after_the_shower(opened, MON_EVENING).drying.active
    shut = _humid_bathroom(DryingState())
    assert not _after_the_shower(shut, MON_EVENING).drying.active  # waits for the window


def test_the_guard_time_after_a_drying_survives_a_cycle_without_heat_pump_or_humidity():
    # found in the code 2026-10-13: one such cycle forgot the guard, and the same air started another hour
    ended = DryingState(ended_ts=MON_EVENING.timestamp() - 60)
    st = _humid_bathroom(ended)
    no_heat_pump = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS, heat_pump_heat_possible=False)
    no_humidity = room_engine.evaluate_room(_with(st, humidity=MISSING), MON_EVENING, "auto", PARAMS)
    for res in (no_heat_pump, no_humidity):
        assert res.drying == ended
        again = room_engine.evaluate_room(_with(st, drying=res.drying), MON_EVENING + timedelta(minutes=1), "auto", PARAMS)
        assert not again.drying.active


def test_a_missing_reading_does_not_restart_the_time_limit_of_a_drying():
    # found in the code 2026-10-13: a cycle without humidity ended the drying, the next started it with a new hour
    running = DryingState(True, MON_EVENING.timestamp() - 3000, 9.0)
    blind = _with(_humid_bathroom(running, window="on", window_open_since=120.0), humidity=MISSING)
    res = room_engine.evaluate_room(blind, MON_EVENING, "auto", PARAMS)
    assert res.drying == running and res.target_reason == "bathroom_drying"
    late = room_engine.evaluate_room(blind, MON_EVENING + timedelta(minutes=11), "auto", PARAMS)
    assert late.drying_reason == "drying_ended_timeout" and late.drying.ended_ts is not None


def test_the_away_humidity_guard_stops_at_the_comfort_temperature():
    # found in the code 2026-10-13: a humidity sensor stuck near 100 % pulled the target up with the room, to 30 °C
    cfg = room_config("badezimmer", heat_pump=True, comfort=21.0)
    temp = PARAMS.away_temp
    for _ in range(20):  # the room follows its target
        res = room_engine.evaluate_room(room_state(cfg, temp=temp, rh=99.0), MON_NOON, "away", PARAMS)
        temp = res.target
    assert res.target_reason == "away_humidity_guard" and res.target == 21.0


# ---- drying comes after the shower, not during it
def _shower(rh: float, drying: DryingState, minute: int):
    """The bathroom `minute` minutes after the humidity began to climb, at `rh` percent."""
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    # the window wide open, as a bathroom is dried (a shut one is test_with_a_window_contact_the_drying_runs_only_...)
    opened = room_state(cfg, temp=22.0, rh=rh, window="on", window_open_since=120.0)
    st = _with(opened, abs_humidity_history=baseline, drying=drying)
    return room_engine.evaluate_room(st, MON_EVENING + timedelta(minutes=minute), "auto", PARAMS)


def test_the_drying_waits_while_the_humidity_still_climbs():
    """The strongest fan and a raised target on wet skin are no comfort: the load only gives notice."""
    drying = DryingState()
    for minute, rh in enumerate((70.0, 78.0, 84.0, 88.0, 91.0, 93.0)):  # the shower runs
        res = _shower(rh, drying, minute)
        drying = res.drying
        assert not drying.active and res.drying_reason == "drying_waiting_for_the_shower_to_end"
        assert res.target_reason != "bathroom_drying" and res.target == 21.0  # nothing is raised yet
    for minute in (6, 7):  # water off: the humidity stands, then falls a little
        res = _shower(92.0, drying, minute)
        drying = res.drying
        assert not drying.active
    res = _shower(90.0, drying, 8)  # three minutes without a new highest value
    assert res.drying.active and res.drying_reason == "drying_started_humidity_load"
    assert res.drying.baseline_abs_humidity == 9.0  # the air from before the shower
    assert res.target == PARAMS.drying_target_temp


def test_a_shower_that_goes_on_and_on_is_dried_after_twenty_minutes_at_the_latest():
    drying, started = DryingState(), None
    for minute in range(25):
        res = _shower(70.0 + minute, drying, minute)  # climbing all the time
        drying = res.drying
        if drying.active and started is None:
            started = minute
    assert started == 20


def test_air_that_clears_by_itself_needs_no_drying():
    waiting = _shower(85.0, DryingState(), 0).drying
    assert waiting.wet_since_ts is not None and not waiting.active
    res = _shower(48.0, waiting, 1)  # a door opened, the damp is gone
    assert res.drying == DryingState() and res.drying_reason is None


def test_a_shower_noticed_before_a_restart_is_still_dried_after_it():
    # the store keeps the wait (test_drying_button), the humidity history starts empty after a restart
    waiting = _shower(85.0, DryingState(), 0).drying
    assert waiting.wet_since_ts is not None and waiting.baseline_abs_humidity == 9.0
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    drying = waiting
    for minute in range(1, 5):  # the humidity stands: three minutes without a new highest value
        restarted = _with(
            room_state(cfg, temp=22.0, rh=85.0, window="on", window_open_since=120.0), abs_humidity_history=(), drying=drying
        )
        drying = room_engine.evaluate_room(restarted, MON_EVENING + timedelta(minutes=minute), "auto", PARAMS).drying
    assert drying.active and drying.baseline_abs_humidity == 9.0  # measured against the air from before the shower


def test_a_drying_asked_for_by_hand_runs_its_time_whatever_the_humidity():
    now_ts = MON_EVENING.timestamp()
    asked = DryingState(True, now_ts, manual=True)
    res = _shower(46.0, asked, 0)  # dry air: an automatic drying would end at once
    assert res.drying == asked and res.target == PARAMS.drying_target_temp and res.heat_pump_allowed
    late = _shower(46.0, asked, 61)
    assert late.drying_reason == "drying_ended_timeout"
    assert late.drying == DryingState()  # and it leaves no guard time: a shower afterwards is dried


def test_with_the_automatic_switched_off_only_the_button_dries():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True, "drying_automatic": False})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    shower = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline)
    res = _after_the_shower(shower, MON_EVENING, minutes=25)
    assert res.drying == DryingState() and res.target_reason != "bathroom_drying"  # no shower starts it

    asked = DryingState(True, now_ts, manual=True)
    res = room_engine.evaluate_room(_with(shower, drying=asked), MON_EVENING, "auto", PARAMS)
    assert res.drying == asked and res.target == PARAMS.drying_target_temp  # the button does

    by_a_shower = DryingState(True, now_ts - 600, 9.0)  # under way when the automatic was switched off
    res = room_engine.evaluate_room(_with(shower, drying=by_a_shower), MON_EVENING, "auto", PARAMS)
    assert not res.drying.active



def test_with_a_window_contact_the_drying_runs_only_while_the_window_is_open():
    """The owner, 2026-10-29: with the window shut the moisture has nowhere to go; he opens it wide and dries."""
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    shut = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline, drying=DryingState(True, now_ts - 600, 9.0))
    res = room_engine.evaluate_room(shut, MON_EVENING, "auto", PARAMS)
    assert not res.drying.active and res.drying.started_ts == now_ts - 600  # under way, its time running
    assert res.drying_reason == "drying_waiting_for_open_window" and res.target_reason != "bathroom_drying"
    opened = _with(
        room_state(cfg, temp=22.0, rh=85.0, window="on", window_open_since=120.0),
        abs_humidity_history=baseline,
        drying=res.drying,
    )
    res = room_engine.evaluate_room(opened, MON_EVENING, "auto", PARAMS)
    assert res.drying.active and res.drying_reason == "drying_active" and res.target_reason == "bathroom_drying"


def test_a_shower_found_with_the_window_shut_waits_for_it_to_open():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=20.5, rh=85.0), abs_humidity_history=baseline)
    res = _after_the_shower(st, MON_EVENING, minutes=3)
    assert res.drying.started_ts is not None and not res.drying.active
    assert res.drying_reason == "drying_waiting_for_open_window"


def test_without_a_window_contact_the_drying_runs_with_warm_air_as_before():
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    st = _with(room_state(cfg, temp=22.0, rh=85.0), abs_humidity_history=baseline, drying=DryingState(True, now_ts - 600, 9.0))
    st = _with(st, config=RoomConfig(**{**cfg.__dict__, "window_sensors": ()}), window_readings=())
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS)
    assert res.drying.active and res.drying_reason == "drying_active"


def test_a_drying_waiting_for_the_window_keeps_waiting_through_a_cycle_without_humidity():
    """Review 2026-10-30: a cycle without a reading took the wait for the open window as a drying under way,
    and the unit blew warm air into the shut bathroom."""
    waiting = DryingState(False, MON_EVENING.timestamp() - 600, 9.0)
    blind = _with(_humid_bathroom(waiting), humidity=MISSING)
    res = room_engine.evaluate_room(blind, MON_EVENING, "auto", PARAMS)
    assert not res.drying.active and res.drying.started_ts == waiting.started_ts
    assert res.drying_reason == "drying_waiting_for_open_window" and res.target_reason != "bathroom_drying"


# ---------------------------------------------------------------------------
# Away: the preheat aims at the target the schedule gives at the return (seen 2026-11-01)
# ---------------------------------------------------------------------------
SUNDAY = datetime(2026, 11, 1)  # weekend comfort 09:00-22:00 (conftest.room_config)


def _at(hour: int, minute: int = 0, day: datetime = SUNDAY) -> datetime:
    return day.replace(hour=hour, minute=minute)


def _aim(
    schedule, back: datetime, now: datetime | None = None, setback: float = 17.0, temp: float = 12.0
) -> tuple[float, str]:
    """The target a radiator room far below it heats to before the return, by default one minute before it.

    At 12 °C its preheat for the next comfort start begins up to 11.6 hours ahead; a warmer room (`temp`) shows the
    target at the return alone, with no comfort start after it to preheat for yet.
    """
    cfg = room_config("badezimmer", comfort=21.0, setback=setback)
    st = _with(room_state(cfg, temp=temp), schedule=schedule)
    now = back - timedelta(minutes=1) if now is None else now
    target, reason, _ = room_engine.target_temperature(st, now, "away", PARAMS, temp, None, back.timestamp())
    return target, reason


def _running_until(end: datetime, after: float | None = None, temp: float | None = None):
    return ScheduleState(active=True, temperature=temp, next_end=end, next_change=end, next_change_target=after)


def test_away_return_at_the_end_of_the_comfort_block_aims_at_the_setback():
    # 2026-11-01: away until 22:00, the weekend block ran to 22:00; the rooms preheated to 21 from 18:00
    assert _aim(_running_until(_at(22), 17.0), _at(22)) == (17.0, "away_preheat")
    # without a target named for the change, what follows a block is the setback
    assert _aim(_running_until(_at(22)), _at(22)) == (17.0, "away_preheat")


def test_away_return_inside_the_block_aims_at_its_comfort():
    assert _aim(_running_until(_at(22), 17.0), _at(21, 29)) == (21.0, "away_preheat")
    assert _aim(_running_until(_at(22), 17.0, temp=22.5), _at(21)) == (22.5, "away_preheat")


def test_away_return_in_the_last_half_hour_of_the_block_aims_at_what_follows():
    assert _aim(_running_until(_at(22), 17.0), _at(21, 31)) == (17.0, "away_preheat")
    # a warmer block that follows is what the room is back to
    assert _aim(_running_until(_at(22), 22.5), _at(21, 45)) == (22.5, "away_preheat")


def test_away_return_after_the_next_block_starts_aims_at_that_block():
    nine = _at(9)
    waiting = ScheduleState(active=False, next_start=nine, next_change=nine, next_change_target=21.0)
    assert _aim(waiting, _at(10)) == (21.0, "away_preheat")
    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)
    st = _with(room_state(cfg, temp=12.0), schedule=waiting, schedule_next_temp=22.5)
    target, _, _ = room_engine.target_temperature(st, _at(9, 59), "away", PARAMS, 12.0, None, _at(10).timestamp())
    assert target == 22.5


def test_away_return_before_the_next_block_starts_aims_at_the_setback():
    tomorrow = _at(17, 0, SUNDAY + timedelta(days=1))  # its preheat begins after the return
    waiting = ScheduleState(active=False, next_start=tomorrow, next_change=tomorrow, next_change_target=21.0)
    assert _aim(waiting, _at(22, 30)) == (17.0, "away_preheat")


def test_away_return_follows_the_comfort_windows_the_same_way():
    # at 16 °C the preheat for Monday 05:15 begins at 22:40
    assert _aim(None, _at(22), temp=16.0) == (17.0, "away_preheat")  # the window ends at 22:00
    assert _aim(None, _at(21, 29)) == (21.0, "away_preheat")
    assert _aim(None, _at(21, 31), temp=16.0) == (17.0, "away_preheat")  # its last half hour
    assert _aim(None, _at(9, 15)) == (21.0, "away_preheat")  # just after the start is comfort
    assert _aim(None, _at(8), temp=20.9) == (17.0, "away_preheat")  # at 20.9 °C the preheat for 09:00 begins at 08:32


def _week(*blocks: tuple[int, int, float | None]) -> tuple:
    """A schedule helper's week with the same blocks every day: (from hour, to hour (24: midnight), temperature)."""
    day = tuple((time(a), time.max if b == 24 else time(b), t) for a, b, t in blocks)
    return (day,) * 7


def _waiting(start: datetime, week: tuple) -> ScheduleState:
    return ScheduleState(active=False, next_start=start, next_change=start, next_change_target=21.0, week=week)


def test_away_return_after_the_helpers_block_has_ended_aims_at_the_setback():
    """Review 2026-11-02: only the next start was checked, so a block that ends before the return still preheated.

    A radiator room from 12 to 21 °C at 0.8 degrees an hour preheats more than eleven hours ahead: one block
    can begin and end between now and the return.
    """
    evening = _week((17, 22, None))
    assert _aim(_waiting(_at(17), evening), _at(22), now=_at(16)) == (17.0, "away_preheat")
    morning = _week((6, 8, None))
    assert _aim(_waiting(_at(6), morning), _at(12), now=_at(5, 30)) == (17.0, "away_preheat")
    assert _aim(_waiting(_at(17), evening), _at(20), now=_at(12)) == (21.0, "away_preheat")


def test_away_return_after_two_changes_aims_at_the_block_running_then():
    """Review 2026-11-02: blocks 09-12 and 14-22, back at 18:00. At 10:00 the next change (12:00) lay before the
    return, so the room aimed at the setback, though comfort runs again at 18:00."""
    week = _week((9, 12, None), (14, 22, 22.5))
    running = ScheduleState(active=True, next_end=_at(12), next_change=_at(12), next_change_target=17.0, week=week)
    assert _aim(running, _at(18), now=_at(10)) == (22.5, "away_preheat")
    # the last half hour of the 14-22 block; at 16 °C the preheat for 09:00 begins at 02:25
    assert _aim(running, _at(21, 45), now=_at(21), temp=16.0) == (17.0, "away_preheat")


def test_away_return_follows_the_helpers_blocks_to_what_follows_them():
    # a block at the same temperature that follows is no change; one at another temperature is
    same = _week((6, 12, 20.0), (12, 22, 20.0))
    assert _aim(_waiting(_at(6), same), _at(11, 45), now=_at(5)) == (20.0, "away_preheat")
    warmer = _week((6, 12, 20.0), (12, 22, 22.0))
    assert _aim(_waiting(_at(6), warmer), _at(11, 45), now=_at(5)) == (22.0, "away_preheat")
    # a block to 23:59 goes on into the next day's block at midnight: the minute between them is no gap
    all_day = (((time(0), time(23, 59), None),),) * 7
    on = ScheduleState(active=True, week=all_day)
    assert _aim(on, _at(23, 59), now=_at(20)) == (21.0, "away_preheat")


def test_away_return_exactly_half_an_hour_before_the_end_aims_at_comfort():
    """Review 2026-11-02: the helper took 30 minutes left as the end (>=), the windows and the heat call do not (<)."""
    assert _aim(None, _at(21, 30)) == (21.0, "away_preheat")
    assert _aim(_running_until(_at(22), 17.0), _at(21, 30)) == (21.0, "away_preheat")  # a helper without its blocks
    week = _week((9, 22, None))
    with_blocks = ScheduleState(active=True, next_end=_at(22), next_change=_at(22), next_change_target=17.0, week=week)
    assert _aim(with_blocks, _at(21, 30)) == (21.0, "away_preheat")
    assert _aim(with_blocks, _at(21, 31), temp=16.0) == (17.0, "away_preheat")


def test_away_return_never_aims_below_the_away_temperature():
    """Review 2026-11-02: a setback below the away temperature (15) would have set the room lower before the return."""
    assert _aim(None, _at(23), setback=14.0, temp=17.0) == (15.0, "away_preheat")  # its preheat for 05:15 from 23:55
    assert _aim(_waiting(_at(17), _week((17, 22, None))), _at(22), now=_at(21), setback=14.0) == (15.0, "away_preheat")
    assert _aim(_running_until(_at(22)), _at(22), setback=14.0) == (15.0, "away_preheat")


def test_away_return_shortly_before_a_comfort_start_preheats_for_it():
    """Review 2026-11-02: back at 16:45 for a block from 17:00, the room aimed at the setback, the preheat for 17:00
    began only at the return, and a radiator room reached comfort at 21:41. Where the preheat for the next comfort
    start must begin before the return, that start is aimed at, as without away."""
    evening = _week((17, 22, None))
    assert _aim(_waiting(_at(17), evening), _at(16, 45), now=_at(12)) == (21.0, "away_preheat")
    warmer = _week((17, 22, 22.5))
    assert _aim(_waiting(_at(17), warmer), _at(16, 45), now=_at(12)) == (22.5, "away_preheat")
    assert _aim(_waiting(_at(17), None), _at(16, 45), now=_at(12)) == (21.0, "away_preheat")  # a helper without its blocks
    assert _aim(None, _at(8, 45), now=_at(4)) == (21.0, "away_preheat")  # the time ranges: weekend from 09:00
    # back at 22:30 for the next morning: from 12 °C at 0.8 degrees an hour its preheat for 05:15 begins at 17:40
    monday = _at(5, 15, SUNDAY + timedelta(days=1))
    assert _aim(None, _at(22, 30), now=_at(20)) == (21.0, "away_preheat")
    assert _aim(_waiting(monday, None), _at(22, 30), now=_at(20)) == (21.0, "away_preheat")
    # a preheat that may begin after the return waits for it: from 05:25 for 17:00
    assert _aim(_waiting(_at(17), evening), _at(4), now=_at(3)) == (17.0, "away_preheat")


def test_away_preheat_for_a_comfort_start_after_the_return_is_judged_against_that_start():
    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)
    st = _with(room_state(cfg, temp=12.0), schedule=_waiting(_at(17), _week((17, 22, None))))
    res = room_engine.evaluate_room(st, _at(12), "away", PARAMS, away_return_ts=_at(16, 45).timestamp())
    assert (res.target, res.target_reason, res.preheat_for_ts) == (21.0, "away_preheat", _at(17).timestamp())
    # at 20.8 °C the preheat for 17:00 may begin at 16:25, after a return at 16:00; once begun it is not undone
    warmer = _with(room_state(cfg, temp=20.8), schedule=st.schedule)
    res = room_engine.evaluate_room(warmer, _at(15, 45), "away", PARAMS, away_return_ts=_at(16).timestamp())
    assert (res.target, res.preheat_for_ts) == (17.0, _at(16).timestamp())
    latched = _with(warmer, preheat_latch_ts=_at(17).timestamp())
    res = room_engine.evaluate_room(latched, _at(15, 45), "away", PARAMS, away_return_ts=_at(16).timestamp())
    assert (res.target, res.preheat_for_ts) == (21.0, _at(17).timestamp())


def test_away_return_half_an_hour_before_the_end_counts_real_time_across_the_clock_change():
    """Review 2026-11-02: the guard measured the clock. 2026-10-25, block 00:00-03:00, back 02:45 summer time: 75
    minutes really remain, comfort; 2027-03-28 back 01:45 winter time: 15 minutes remain, the setback."""
    berlin = ZoneInfo("Europe/Berlin")
    night = _week((0, 3, None))
    autumn = datetime(2026, 10, 25, 2, 45, tzinfo=berlin)  # fold 0: the first 02:45, summer time
    running = ScheduleState(active=True, next_end=autumn.replace(hour=3), next_change=autumn.replace(hour=3), week=night)
    assert _aim(running, autumn) == (21.0, "away_preheat")
    assert _aim(_running_until(autumn.replace(hour=3)), autumn) == (21.0, "away_preheat")  # a helper without its blocks
    spring = datetime(2027, 3, 28, 1, 45, tzinfo=berlin)
    running = ScheduleState(active=True, next_end=spring.replace(hour=3), next_change=spring.replace(hour=3), week=night)
    assert _aim(running, spring) == (17.0, "away_preheat")
