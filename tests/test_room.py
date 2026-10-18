from dataclasses import replace
from datetime import datetime, time

import pytest

from custom_components.thriftherm.engines import psychrometrics as psy
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.models import Parameters, TimeWindow

from .conftest import PARAMS, reading, room_config, room_state


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
    assert room_engine.window_status((("b.a", None, None), ("b.b", "off", None)), 90.0) == (False, True, None)
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


def test_rooms_of_a_cooling_heat_pump_take_no_heat():
    # hard rule: cooling never collides with heating (code audit 2026-09-28, F8)
    evening = datetime(2026, 1, 12, 18, 0)  # comfort window
    served = room_state(room_config("badezimmer", heat_pump=True, comfort=21.0, setback=17.0), temp=16.0)
    res = room_engine.evaluate_room(served, evening, "auto", PARAMS, heat_pump_heat_possible=False, heat_pump_cooling=True)
    assert res.target == 17.0 and res.target_reason == "heat_pump_cooling"
    assert res.heating_allowed is False and res.demand == 0.0  # not even below setback
    # a lower target stays: summer mode keeps the frost temperature
    off = room_engine.evaluate_room(served, evening, "off", PARAMS, heat_pump_heat_possible=False, heat_pump_cooling=True)
    assert off.target == PARAMS.frost_temp and off.heating_allowed is False
    # a radiator room the heat pump does not serve heats as before
    radiator = room_state(room_config("wohnzimmer", comfort=21.0), temp=16.0)
    other = room_engine.evaluate_room(radiator, evening, "auto", PARAMS, heat_pump_heat_possible=False, heat_pump_cooling=True)
    assert other.target == 21.0 and other.heating_allowed is True and other.demand > 0.0


def test_a_window_to_2359_covers_the_last_minute():
    parse_schedule = room_engine.parse_schedule
    all_day, evening = parse_schedule("00:00-23:59")[0], parse_schedule("17:30-22:00")[0]
    assert all_day.contains(time(23, 59, 30)) and all_day.contains(time(0, 0))
    assert not evening.contains(time(22, 0))
    late = parse_schedule("18:00-23:59")[0]
    assert late.contains(time(23, 59, 59)) and not late.contains(time(17, 59))


def test_a_dead_contact_among_several_is_reported_and_the_room_keeps_heating():
    # found in the code 2026-10-13: a dead contact beside a closed one counted as closed and raised nothing
    dead, shut, open_ = ("b.a", None, None), ("b.b", "off", None), ("b.c", "on", 120.0)
    assert room_engine.window_status((dead, shut), 90.0) == (False, True, None)
    assert room_engine.window_status((dead, shut, open_), 90.0) == (True, True, 120.0)  # an open one still means open
    assert room_engine.window_status((("b.a", "opening", None), shut), 90.0) == (False, True, None)

    st = replace(room_state(room_config("kueche"), temp=16.0), window_readings=(dead, shut))
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 18, 0), "auto", PARAMS)
    assert res.window_unknown and "window_contacts_unavailable" in res.issues
    assert res.window_open is False and res.heating_allowed and res.demand > 0.0


def test_a_window_to_2359_ends_at_midnight_also_during_its_last_minute():
    # found in the code 2026-10-13: at 23:59 the end lay a day ahead, and the room called the boiler for that minute
    parse = room_engine.parse_schedule
    cfg = replace(room_config("wohnzimmer"), schedule_weekday=parse("06:00-23:59"), schedule_weekend=parse("08:00-23:59"))
    for clock in (time(23, 20), time(23, 59), time(23, 59, 30)):
        tuesday = datetime.combine(datetime(2026, 10, 13), clock)
        assert room_engine.comfort_end(cfg, tuesday) == datetime(2026, 10, 14, 0, 0)
    all_day = replace(cfg, schedule_weekday=parse("00:00-23:59"), schedule_weekend=parse("00:00-23:59"))
    assert room_engine.comfort_end(all_day, datetime(2026, 10, 13, 23, 59, 30)) is None


def test_the_hours_after_midnight_belong_to_the_evening_before():
    # found in the code 2026-10-13: the rest of a window across midnight was judged by the new day's windows
    parse = room_engine.parse_schedule
    cfg = replace(room_config("wohnzimmer"), schedule_weekday=parse("22:00-02:00"), schedule_weekend=parse("08:00-23:00"))
    friday_late, saturday_early = datetime(2026, 10, 16, 23, 30), datetime(2026, 10, 17, 0, 30)
    sunday_late, monday_early = datetime(2026, 10, 18, 23, 30), datetime(2026, 10, 19, 0, 30)

    def reason(at: datetime) -> str:
        return room_engine.scheduled_target(cfg, at)[1]

    # Friday's window runs to 02:00 on Saturday, whatever the weekend says
    assert reason(friday_late) == reason(saturday_early) == "schedule_comfort"
    saturday_2 = datetime(2026, 10, 17, 2, 0)
    assert room_engine.comfort_end(cfg, friday_late) == room_engine.comfort_end(cfg, saturday_early) == saturday_2
    assert reason(saturday_2) == "schedule_setback"
    # Sunday has no window across midnight: Monday's comfort starts at 22:00, and it is preheated for
    assert reason(sunday_late) == reason(monday_early) == "schedule_setback"
    monday_22 = datetime(2026, 10, 19, 22, 0)
    assert room_engine.next_comfort_start(cfg, sunday_late) == room_engine.next_comfort_start(cfg, monday_early) == monday_22
    # within the week nothing changes
    wednesday = datetime(2026, 10, 14, 0, 30)
    assert reason(wednesday) == "schedule_comfort" and reason(wednesday.replace(hour=12)) == "schedule_setback"


def test_humidity_values_come_from_the_room_sensor_alone():
    # found in the code 2026-10-13: with the room temperature stale and its humidity still valid, the valve's
    # temperature (at the radiator) made the absolute humidity jump, and a drying started
    cfg = replace(room_config("badezimmer", heat_pump=True), bathroom_drying=True)
    now = datetime(2026, 1, 12, 18, 0)
    usual = psy.absolute_humidity_g_m3(20.5, 75.0)
    history = tuple((now.timestamp() - 3 * 3600 + i * 300, usual) for i in range(30))
    st = replace(
        room_state(cfg, temp=20.5, rh=75.0, trv_local=22.5), temperature=reading(20.5, valid=False), abs_humidity_history=history
    )
    res = room_engine.evaluate_room(st, now, "auto", PARAMS)
    assert res.temperature == 22.5 and res.dew_point_c is None and res.abs_humidity_g_m3 is None
    assert not res.drying.active


def test_the_next_schedule_change_and_the_target_after_it():
    """What a dashboard shows as the next change: the end of the comfort running now, or the next start."""
    from custom_components.thriftherm.models import Override, ScheduleState

    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)
    st = room_state(cfg, temp=20.0)
    change = room_engine.next_schedule_change
    monday_evening, monday_noon = datetime(2026, 1, 12, 18, 0), datetime(2026, 1, 12, 12, 0)
    friday_night = datetime(2026, 1, 16, 23, 0)
    assert change(st, monday_evening, "auto") == (datetime(2026, 1, 12, 22, 0).timestamp(), 17.0)
    assert change(st, monday_noon, "auto") == (datetime(2026, 1, 12, 17, 30).timestamp(), 21.0)
    assert change(st, friday_night, "auto") == (datetime(2026, 1, 17, 9, 0).timestamp(), 21.0)  # Saturday's window
    # a change by hand does not move what the schedule does next; away and off leave the schedule aside
    hand = Override(target=23.0, until_ts=monday_evening.timestamp() + 3600, set_at_ts=monday_evening.timestamp())
    by_hand = replace(st, override=hand)
    assert change(by_hand, monday_evening, "auto") == change(st, monday_evening, "auto")
    assert change(st, monday_evening, "away") == change(st, monday_evening, "off") == (None, None)
    # a schedule helper: the change its blocks make next, as the adapter found it (test_snapshot_builder)
    four = datetime(2026, 1, 12, 16, 0)
    helper = replace(st, schedule=ScheduleState(active=False, next_start=four, next_change=four, next_change_target=22.5))
    assert change(helper, monday_noon, "auto") == (four.timestamp(), 22.5)
    nine = datetime(2026, 1, 12, 21, 0)
    running = replace(st, schedule=ScheduleState(active=True, next_end=nine, next_change=nine, next_change_target=17.0))
    assert change(running, monday_evening, "auto") == (nine.timestamp(), 17.0)
    all_week = replace(st, schedule=ScheduleState(active=True))
    assert change(all_week, monday_evening, "auto") == (None, None)
    result = room_engine.evaluate_room(st, monday_evening, "auto", PARAMS)
    assert result.next_change_ts == datetime(2026, 1, 12, 22, 0).timestamp()
