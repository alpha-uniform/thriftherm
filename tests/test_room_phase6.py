from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.learning import HeatRateStats
from custom_components.thriftherm.models import DryingState, Override, RoomConfig

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
    deficit = 17.0 - 15.0  # not 21.0 - 15.0 for 18:00
    rate = PARAMS.default_heat_rate_radiator_k_h
    assert res.preheat_start_ts == pytest.approx(return_ts - deficit / rate * 3600 - PARAMS.preheat_margin_s, abs=1)


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


def _humid_bathroom(drying: DryingState, **room):
    """A bathroom with drying mode after a shower (85 %), over three hours of dry air before it."""
    cfg = RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})
    now_ts = MON_EVENING.timestamp()
    baseline = tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))
    return _with(room_state(cfg, temp=22.0, rh=85.0, **room), abs_humidity_history=baseline, drying=drying)


def test_a_drying_the_open_window_ended_does_not_start_again_every_cycle():
    # found in the code 2026-10-13: 39 starts and stops in 40 minutes with the window open and the air above the baseline
    now_ts = MON_EVENING.timestamp()
    drying, changes = DryingState(True, now_ts - 1500, 9.0), 0
    for minute in range(40):
        st = _humid_bathroom(drying, window="on", window_open_since=1500.0 + minute * 60)
        res = room_engine.evaluate_room(st, MON_EVENING + timedelta(minutes=minute), "auto", PARAMS)
        changes += res.drying.active != drying.active
        drying = res.drying
    assert changes == 1 and not drying.active and drying.ended_ts == now_ts

    # the window shut and the guard time over: a new shower dries again
    fresh = tuple((now_ts + 3600 + i * 300, 9.0) for i in range(30))
    st = _with(_humid_bathroom(drying), abs_humidity_history=fresh)
    assert _after_the_shower(st, MON_EVENING + timedelta(hours=3, minutes=1)).drying.active


def test_no_drying_starts_while_the_window_has_been_open_for_long():
    # it would end as "window ineffective" a cycle later and leave its guard time behind
    st = _humid_bathroom(DryingState(), window="on", window_open_since=1500.0)
    assert room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS).drying == DryingState()
    shut = _humid_bathroom(DryingState())
    assert _after_the_shower(shut, MON_EVENING).drying.active


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
    blind = _with(_humid_bathroom(running), humidity=MISSING)
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
    st = _with(room_state(cfg, temp=22.0, rh=rh), abs_humidity_history=baseline, drying=drying)
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

