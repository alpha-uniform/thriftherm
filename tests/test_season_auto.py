"""The automatic season (docs/PHASE12_COOLING.md §20.2): how the rooms behave without heating or cooling decides.

Pure logic of engines/season.py; the wiring in a Home Assistant test instance is in test_cooling_integration.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.thriftherm.engines import season as sn

TZ = timezone(timedelta(hours=2))
DAY0 = datetime(2026, 6, 1, 0, 0, tzinfo=TZ)
H = 3600.0


def unit(**changes) -> sn.UnitSample:
    base = dict(hvac_mode="off", compressor_hz=None, resting=False, run_on_after_cooling=False)
    return sn.UnitSample(**{**base, **changes})


def operation(**changes) -> sn.RoomOperation:
    base = dict(
        served=True, unit=unit(), thermostat_heating=False, calling=False, boiler_released=False, drying=False,
        window_open=False, own_reading=True,
    )
    return sn.room_operation(**{**base, **changes})


# --- what counts as heating, cooling and running free (tests 7-17) ---


def test_a_unit_resting_in_cool_at_0_hz_leaves_its_room_free():
    assert operation(unit=unit(hvac_mode="cool", compressor_hz=0.0, resting=True)).free


def test_a_compressor_cooling_at_30_hz_is_cooling():
    op = operation(unit=unit(hvac_mode="cool", compressor_hz=30.0))
    assert (op.cools, op.free) == (True, False)


def test_the_fan_running_on_after_cooling_is_cooling():
    op = operation(unit=unit(hvac_mode="fan_only", compressor_hz=0.0, run_on_after_cooling=True))
    assert (op.cools, op.free) == (True, False)


def test_cool_without_a_frequency_is_cooling_unless_it_is_our_rest():
    assert operation(unit=unit(hvac_mode="cool")).cools
    assert operation(unit=unit(hvac_mode="cool", resting=True)).free


def test_a_unit_cools_only_the_rooms_it_serves():
    assert operation(served=False, unit=unit(hvac_mode="cool", compressor_hz=30.0)).free


def test_a_thermostat_that_reports_heating_is_heating_whatever_thriftherm_sees():
    op = operation(thermostat_heating=True)
    assert (op.heats, op.free) == (True, False)


def test_a_silent_thermostat_heats_while_its_room_calls_and_the_boiler_is_released():
    assert operation(thermostat_heating=None, calling=True, boiler_released=True).heats
    assert operation(thermostat_heating=None, calling=True, boiler_released=False).free
    assert operation(thermostat_heating=None, calling=False, boiler_released=True).free


def test_a_bathroom_drying_is_heating():
    assert operation(drying=True).heats


def test_a_compressor_heating_its_rooms_is_heating():
    assert operation(unit=unit(hvac_mode="heat", compressor_hz=24.0)).heats
    assert operation(unit=unit(hvac_mode="heat", compressor_hz=0.0)).free


@pytest.mark.parametrize("window", [True, None], ids=["open", "unknown"])
def test_an_open_or_unknown_window_is_not_free(window):
    assert not operation(window_open=window).free


def test_the_valve_standing_in_for_the_room_sensor_is_not_free():
    assert not operation(own_reading=False).free


# --- seconds per day (tests 17-22) ---


def sample(key="schlafzimmer", **changes) -> sn.RoomSample:
    base = dict(
        key=key, contact=True, served=True, heats=False, cools=False, free=True, temperature=23.0,
        heat_comfort=21.0, cool_comfort=25.0, load=False,
    )
    return sn.RoomSample(**{**base, **changes})


def feed(days: sn.FreeRunDays, start: datetime, hours: float, step_s: float = 300.0, **changes) -> datetime:
    """`hours` of samples every `step_s` from `start`; returns the time after them."""
    now = start
    days.observe(now, [sample(**changes)])
    for _ in range(round(hours * H / step_s)):
        now += timedelta(seconds=step_s)
        days.observe(now, [sample(**changes)])
    return now


def test_free_seconds_and_seconds_at_the_cooling_comfort_are_counted():
    days = sn.FreeRunDays()
    t = feed(days, DAY0.replace(hour=8), 4.5)
    feed(days, t, 2.5, temperature=25.3)
    room = days.day_of(DAY0).rooms["schlafzimmer"]
    assert (room.free_s, room.warm_s) == pytest.approx((25200.0, 9000.0))


def test_a_comfort_changed_later_does_not_change_the_days_counted():
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=8), 2.5, temperature=25.3)
    feed(days, DAY0.replace(hour=11), 1.0, temperature=25.3, cool_comfort=26.0)
    assert days.day_of(DAY0).rooms["schlafzimmer"].warm_s == pytest.approx(9000.0)


def test_a_stretch_over_midnight_is_split_between_the_days():
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=22), 8.0)
    first, second = days.day_of(DAY0), days.day_of(DAY0 + timedelta(days=1))
    assert first.rooms["schlafzimmer"].free_s == pytest.approx(2 * H)
    assert second.rooms["schlafzimmer"].free_s == pytest.approx(6 * H)


def test_a_gap_in_the_samples_counts_for_no_more_than_one_step():
    days = sn.FreeRunDays()
    days.observe(DAY0.replace(hour=8), [sample()])
    days.observe(DAY0.replace(hour=12), [sample()])
    assert days.day_of(DAY0).rooms["schlafzimmer"].free_s == pytest.approx(sn.MAX_STEP_S)


def test_below_the_heating_comfort_less_a_degree_counts_as_cooling_down():
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=1), 3.0, temperature=19.5)
    feed(days, DAY0.replace(hour=5), 1.0, temperature=20.5)  # within a degree: says nothing
    assert days.day_of(DAY0).rooms["schlafzimmer"].cool_s == pytest.approx(3 * H)


def test_heating_and_cooling_are_counted_for_the_whole_flat_also_in_rooms_without_a_contact():
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=8), 1.0, key="kueche", contact=False, heats=True, free=False)
    day = days.day_of(DAY0)
    assert day.heat_s == pytest.approx(H) and "kueche" not in day.rooms


def test_the_days_survive_the_store_and_only_four_are_kept():
    days = sn.FreeRunDays()
    for d in range(6):
        feed(days, DAY0 + timedelta(days=d, hours=8), 1.0)
    again = sn.FreeRunDays.from_storage(days.to_storage())
    assert len(again.days) == sn.DAYS_KEPT
    assert again.to_storage() == days.to_storage()


def test_a_damaged_store_is_taken_as_empty():
    assert sn.FreeRunDays.from_storage({"days": {"x": "y"}, "last_ts": "z"}).days == {}
    assert sn.FreeRunDays.from_storage(None).days == {}


# --- the verdict of a day (tests 23-29) ---


def day_with(**rooms_and_flat) -> sn.Day:
    flat = {k: rooms_and_flat.pop(k) for k in ("heat_s", "cooled_s") if k in rooms_and_flat}
    return sn.Day(start_ts=DAY0.timestamp(), rooms=rooms_and_flat, **flat)


def test_a_room_warm_without_heating_or_cooling_makes_a_cooling_day():
    day = day_with(schlafzimmer=sn.RoomDay(free_s=8 * H, warm_s=2.5 * H), heat_s=20 * 60.0)
    assert sn.verdict(day) == sn.VERDICT_COOLING


def test_heating_for_two_hours_makes_it_neutral():
    day = day_with(schlafzimmer=sn.RoomDay(free_s=8 * H, warm_s=2.5 * H), heat_s=2 * H)
    assert sn.verdict(day) == sn.VERDICT_NEUTRAL


def test_warm_seconds_of_a_room_not_served_do_not_count():
    """warm_s is counted for served rooms only: the engine is told so by the sample."""
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=8), 8.0, served=False, temperature=26.0)
    assert days.day_of(DAY0).rooms["schlafzimmer"].warm_s == 0.0


def test_warm_seconds_under_a_humidity_load_do_not_count():
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=8), 8.0, temperature=26.0, load=True)
    assert days.day_of(DAY0).rooms["schlafzimmer"].warm_s == 0.0


def test_a_room_cooling_down_without_cooling_makes_a_heating_day():
    day = day_with(schlafzimmer=sn.RoomDay(free_s=8 * H, cool_s=3 * H))
    assert sn.verdict(day) == sn.VERDICT_HEATING


def test_a_room_between_the_comforts_says_nothing():
    assert sn.verdict(day_with(schlafzimmer=sn.RoomDay(free_s=10 * H))) == sn.VERDICT_NEUTRAL


def test_one_room_warm_and_another_cool_is_neutral():
    day = day_with(
        schlafzimmer=sn.RoomDay(free_s=8 * H, warm_s=3 * H), wohnzimmer=sn.RoomDay(free_s=8 * H, cool_s=3 * H)
    )
    assert sn.verdict(day) == sn.VERDICT_NEUTRAL


def test_a_day_on_which_no_room_ran_free_for_six_hours_is_not_counted():
    day = day_with(schlafzimmer=sn.RoomDay(free_s=5.5 * H, warm_s=5 * H))
    assert sn.verdict(day) is None


def test_cooling_for_an_hour_or_more_takes_the_heating_day_away():
    day = day_with(schlafzimmer=sn.RoomDay(free_s=8 * H, cool_s=3 * H), cooled_s=H)
    assert sn.verdict(day) == sn.VERDICT_NEUTRAL


# --- the outdoor daily mean ---


def test_the_outdoor_mean_needs_18_of_24_hours():
    hours = sn.OutdoorHours()
    start = DAY0.timestamp()
    for i in range(17 * 6 + 1):
        hours.observe(start + i * 600.0, 18.0)
    assert hours.mean(start + 17 * H + 60) is None
    for i in range(6):
        hours.observe(start + 17 * H + i * 600.0, 18.0)
    hours.observe(start + 18 * H, 18.0)
    assert hours.mean(start + 18 * H + 60) == pytest.approx(18.0)


def test_the_outdoor_mean_looks_back_one_day_only():
    hours = sn.OutdoorHours()
    start = DAY0.timestamp()
    for i in range(48 * 6 + 1):
        hours.observe(start + i * 600.0, 10.0 if i < 24 * 6 else 20.0)
    assert hours.mean(start + 48 * H) == pytest.approx(20.0)
    assert sn.OutdoorHours.from_storage(hours.to_storage()).mean(start + 48 * H) == pytest.approx(20.0)


# --- switching (tests 30-41) ---

NOW = DAY0 + timedelta(days=10, hours=12)
SINCE_LONG_AGO = (NOW - timedelta(days=5)).timestamp()


def days_back(*verdicts: str | None) -> tuple[tuple[float, str | None], ...]:
    """The verdicts of yesterday, the day before and the one before that, with their starts."""
    midnight = NOW.replace(hour=0)
    return tuple(((midnight - timedelta(days=i + 1)).timestamp(), v) for i, v in enumerate(verdicts))


def decide(**changes) -> sn.SeasonDecision:
    base = dict(
        now_ts=NOW.timestamp(), season="heating", since_ts=SINCE_LONG_AGO,
        verdicts=days_back(sn.VERDICT_COOLING, sn.VERDICT_COOLING, sn.VERDICT_NEUTRAL), outdoor_mean=15.0,
        frost=False, unit_can_cool=True, cooling_from=18.0, heating_from=14.0, veto=12.0,
    )
    return sn.decide(sn.SeasonInputs(**{**base, **changes}))


def test_two_cooling_days_of_three_switch_to_cooling():
    assert (decide().season, decide().reason) == ("cooling", sn.REASON_ROOMS_WARM_UP)


def test_below_the_veto_it_never_switches_to_cooling():
    assert decide(outdoor_mean=10.0).season == "heating"


def test_a_heating_day_among_them_keeps_the_season():
    res = decide(verdicts=days_back(sn.VERDICT_COOLING, sn.VERDICT_HEATING, sn.VERDICT_COOLING))
    assert res.season == "heating"


def test_the_season_holds_for_72_hours():
    assert decide(since_ts=NOW.timestamp() - 60 * H).season == "heating"
    assert decide(since_ts=NOW.timestamp() - 73 * H).season == "cooling"


def test_only_days_wholly_after_the_last_change_count():
    # the day before yesterday began before the change: one cooling day after it is not enough
    since = (NOW.replace(hour=0) - timedelta(days=2) + timedelta(hours=1)).timestamp()
    res = decide(since_ts=since, now_ts=since + 73 * H)
    assert res.season == "heating"


def test_two_heating_days_switch_back_to_heating():
    res = decide(season="cooling", verdicts=days_back(sn.VERDICT_HEATING, sn.VERDICT_NEUTRAL, sn.VERDICT_HEATING))
    assert (res.season, res.reason) == ("heating", sn.REASON_ROOMS_COOL_DOWN)


def test_frost_switches_to_heating_at_once():
    res = decide(season="cooling", since_ts=NOW.timestamp() - 2 * H, frost=True, verdicts=days_back(None, None, None))
    assert (res.season, res.reason) == ("heating", sn.REASON_FROST)


def test_frost_keeps_it_from_switching_to_cooling():
    assert decide(frost=True).season == "heating"


def test_with_too_few_days_counted_the_outdoor_mean_decides():
    res = decide(verdicts=days_back(sn.VERDICT_NEUTRAL, None, None), outdoor_mean=18.0)
    assert (res.season, res.reason) == ("cooling", sn.REASON_OUTDOOR_MEAN)
    res = decide(season="cooling", verdicts=days_back(None, sn.VERDICT_NEUTRAL, None), outdoor_mean=14.0)
    assert (res.season, res.reason) == ("heating", sn.REASON_OUTDOOR_MEAN)


def test_between_the_two_means_the_season_stays():
    res = decide(verdicts=days_back(None, None, None), outdoor_mean=16.0)
    assert res.season == "heating"


def test_without_a_mean_and_too_few_days_the_season_stays():
    res = decide(verdicts=days_back(sn.VERDICT_COOLING, None, None), outdoor_mean=None)
    assert (res.season, res.reason) == ("heating", sn.REASON_NO_EVIDENCE)


def test_neutral_days_are_counted_days_and_bring_no_fallback():
    res = decide(verdicts=days_back(sn.VERDICT_NEUTRAL, sn.VERDICT_NEUTRAL, sn.VERDICT_NEUTRAL), outdoor_mean=20.0)
    assert res.season == "heating"


def test_never_into_a_season_the_unit_cannot_do():
    assert decide(unit_can_cool=False).season == "heating"
    assert decide(unit_can_cool=False, verdicts=days_back(None, None, None), outdoor_mean=25.0).season == "heating"


def test_the_fallback_holds_for_72_hours_too():
    res = decide(verdicts=days_back(None, None, None), outdoor_mean=20.0, since_ts=NOW.timestamp() - 10 * H)
    assert res.season == "heating"


def test_a_decision_that_keeps_the_season_says_it_did_not_change():
    assert decide().changed and not decide(outdoor_mean=10.0).changed


def test_a_bathroom_drying_does_not_count_as_heating_for_the_whole_flat():
    """Review: a daily drying of an hour would have made every day neutral, and the automatic would never cool."""
    days = sn.FreeRunDays()
    feed(days, DAY0.replace(hour=8), 1.5, key="badezimmer", heats=True, free=False, drying=True)
    assert days.day_of(DAY0).heat_s == 0.0


def test_a_damaged_outdoor_store_is_taken_as_empty():
    assert sn.OutdoorHours.from_storage({"hours": 5, "current": "x"}).hours == []
