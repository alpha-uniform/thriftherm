"""The cooling target of a room (docs/PHASE12_COOLING.md §3): cooling schedule, away, pre-cooling, override, floor."""

from datetime import datetime, time, timedelta, timezone

import pytest

from custom_components.thriftherm.engines import room_cooling as rc
from custom_components.thriftherm.models import Override

TZ = timezone(timedelta(hours=2))
MON_15 = datetime(2026, 7, 13, 15, 0, tzinfo=TZ)  # a Monday afternoon in summer
NIGHT_BLOCK = ((time(0, 0), time(7, 0), None), (time(21, 0), time.max, None))
WEEK = tuple(NIGHT_BLOCK for _ in range(7))


def inputs(**changes) -> rc.CoolTargetInputs:
    base = dict(
        now=MON_15, mode="auto", temperature=27.0, comfort=25.0, outside=28.0, away=29.0, floor=20.0,
        schedule=None, override=None, away_return_ts=None, away_provisional=False, latch_ts=None, margin_s=1200.0,
    )
    return rc.CoolTargetInputs(**{**base, **changes})


def test_without_a_schedule_the_comfort_holds_all_day():
    for hour in (3, 15, 22):
        res = rc.cooling_target(inputs(now=MON_15.replace(hour=hour)))
        assert (res.target, res.reason) == (25.0, "cool_schedule_comfort")


def test_a_cooling_schedule_gives_comfort_in_its_blocks_and_outside_elsewhere():
    sched = rc.CoolSchedule(week=WEEK)
    res = rc.cooling_target(inputs(schedule=sched, temperature=28.0))
    assert (res.target, res.reason) == (28.0, "cool_schedule_outside")
    night = rc.cooling_target(inputs(schedule=sched, now=MON_15.replace(hour=22)))
    assert (night.target, night.reason) == (25.0, "cool_schedule_comfort")


def test_a_block_temperature_is_the_target_of_the_block():
    week = tuple(((time(21, 0), time.max, 24.0),) for _ in range(7))
    res = rc.cooling_target(inputs(schedule=rc.CoolSchedule(week=week), now=MON_15.replace(hour=22)))
    assert res.target == 24.0


def test_the_floor_is_never_undercut():
    week = tuple(((time(21, 0), time.max, 18.0),) for _ in range(7))
    res = rc.cooling_target(inputs(schedule=rc.CoolSchedule(week=week), now=MON_15.replace(hour=22)))
    assert res.target == 20.0
    over = Override(target=17.0, until_ts=MON_15.timestamp() + 3600, set_at_ts=MON_15.timestamp())
    assert rc.cooling_target(inputs(override=over)).target == 20.0


def test_mode_off_and_boiler_only_cool_nothing():
    for mode in ("off", "boiler_only"):
        res = rc.cooling_target(inputs(mode=mode))
        assert (res.target, res.reason) == (None, "cool_off")


def test_an_override_holds_until_it_ends():
    now = MON_15.timestamp()
    over = Override(target=23.0, until_ts=now + 3600, set_at_ts=now)
    assert (rc.cooling_target(inputs(override=over)).target, rc.cooling_target(inputs(override=over)).reason) == (
        23.0, "cool_override"
    )
    ended = Override(target=23.0, until_ts=now - 1, set_at_ts=now - 3600)
    assert rc.cooling_target(inputs(override=ended)).reason == "cool_schedule_comfort"


def test_away_without_a_return_holds_the_away_target():
    res = rc.cooling_target(inputs(mode="away"))
    assert (res.target, res.reason) == (29.0, "cool_away")


def test_precooling_for_the_return_starts_at_most_four_hours_ahead():
    # 28 °C, 25 at the return: 3 K at 0.5 K/h is 6 h plus the margin, capped at 4 h
    ret = MON_15.timestamp() + 6 * 3600 + 1200
    res = rc.cooling_target(inputs(mode="away", temperature=28.0, away_return_ts=ret))
    assert (res.target, res.reason) == (29.0, "cool_away")
    assert res.precool_start_ts == pytest.approx(ret - 4 * 3600)
    later = MON_15 + timedelta(hours=2, minutes=21)
    res = rc.cooling_target(inputs(mode="away", temperature=28.0, away_return_ts=ret, now=later))
    assert (res.target, res.reason, res.precool_for_ts) == (25.0, "cool_precool", ret)


def test_a_short_precool_starts_late():
    ret = MON_15.timestamp() + 3 * 3600
    # half a degree: an hour plus the margin of 20 minutes
    res = rc.cooling_target(inputs(mode="away", temperature=25.5, away_return_ts=ret))
    assert res.precool_start_ts == pytest.approx(ret - 3600 - 1200)
    assert res.reason == "cool_away"


def test_a_provisional_return_is_not_precooled_for():
    ret = MON_15.timestamp() + 1800
    res = rc.cooling_target(inputs(mode="away", away_return_ts=ret, away_provisional=True))
    assert res.reason == "cool_away" and res.precool_start_ts is None


def test_a_begun_precool_stays_latched():
    ret = MON_15.timestamp() + 3 * 3600
    # the room fell a little: the start moves later, but the precool goes on
    res = rc.cooling_target(inputs(mode="away", temperature=25.2, away_return_ts=ret, latch_ts=ret))
    assert (res.target, res.reason) == (25.0, "cool_precool")


def test_precooling_before_a_block():
    sched = rc.CoolSchedule(week=WEEK)
    at = MON_15.replace(hour=19, minute=0)  # block at 21:00, room 28 against 25: 4 h cap, so from 17:00
    res = rc.cooling_target(inputs(schedule=sched, now=at, temperature=28.0))
    assert (res.target, res.reason) == (25.0, "cool_precool")
    assert res.precool_for_ts == MON_15.replace(hour=21).timestamp()


def test_no_precool_where_the_room_is_cool_enough_already():
    sched = rc.CoolSchedule(week=WEEK)
    at = MON_15.replace(hour=20, minute=30)
    res = rc.cooling_target(inputs(schedule=sched, now=at, temperature=24.0))
    assert res.reason == "cool_schedule_outside"


def test_the_return_aims_at_the_schedule_of_that_time():
    sched = rc.CoolSchedule(week=WEEK)
    ret = MON_15.replace(hour=16).timestamp()  # back at 16:00: outside a block, 28
    res = rc.cooling_target(inputs(mode="away", schedule=sched, temperature=29.5, away_return_ts=ret))
    assert (res.target, res.reason) == (28.0, "cool_precool")


def test_a_helper_without_blocks_uses_its_state():
    running = rc.CoolSchedule(active=True, temperature=24.5)
    assert rc.cooling_target(inputs(schedule=running)).target == 24.5
    soon = MON_15 + timedelta(hours=1)
    waiting = rc.CoolSchedule(active=False, next_start=soon)
    res = rc.cooling_target(inputs(schedule=waiting, temperature=26.0))
    assert (res.target, res.reason) == (25.0, "cool_precool")
    far = rc.CoolSchedule(active=False, next_start=MON_15 + timedelta(hours=10))
    assert rc.cooling_target(inputs(schedule=far, temperature=26.0)).reason == "cool_schedule_outside"


def test_unknown_temperature_begins_no_precool():
    sched = rc.CoolSchedule(week=WEEK)
    at = MON_15.replace(hour=20)
    assert rc.cooling_target(inputs(schedule=sched, now=at, temperature=None)).reason == "cool_schedule_outside"
    latched = rc.cooling_target(inputs(schedule=sched, now=at, temperature=None, latch_ts=MON_15.replace(hour=21).timestamp()))
    assert latched.reason == "cool_precool"


# --- stage 2: cheap power lowers the cooling targets (docs/PHASE12_COOLING.md §20.1) ---


def test_cheap_power_lowers_the_cooling_target():
    res = rc.cooling_target(inputs(cheap_k=1.0))
    assert (res.target, res.reason) == (24.0, "cool_cheap_power")


def test_cheap_power_never_goes_below_the_floor():
    res = rc.cooling_target(inputs(comfort=20.5, cheap_k=1.0))
    assert (res.target, res.reason) == (20.0, "cool_cheap_power")


def test_cheap_power_leaves_an_override_alone():
    now = MON_15.timestamp()
    over = Override(target=26.0, until_ts=now + 3600, set_at_ts=now)
    res = rc.cooling_target(inputs(override=over, cheap_k=1.0))
    assert (res.target, res.reason) == (26.0, "cool_override")


def test_cheap_power_does_not_turn_no_cooling_target_into_one():
    """K7: 32 °C outside the schedule is no target; cheap power must not make 31 °C of it."""
    res = rc.cooling_target(inputs(schedule=rc.CoolSchedule(week=WEEK), outside=32.0, cheap_k=1.0))
    assert (res.target, res.reason) == (None, "cool_off")


def test_without_cheap_power_nothing_changes():
    assert rc.cooling_target(inputs(cheap_k=0.0)) == rc.cooling_target(inputs())


def test_a_learned_cooling_rate_sets_the_lead_of_a_precool():
    """§20.3: from three episodes the median rate; 1 K/h halves the lead of 0.5 K/h."""
    ret = MON_15.timestamp() + 6 * 3600
    base = dict(mode="away", away_return_ts=ret, temperature=28.0)
    slow = rc.cooling_target(inputs(**base))
    fast = rc.cooling_target(inputs(**base, rate_k_h=1.0))
    assert slow.precool_start_ts == pytest.approx(ret - 4 * 3600)  # 3 K at 0.5 K/h: six hours, held to four
    assert fast.precool_start_ts == pytest.approx(ret - 3 * 3600 - 1200)  # 3 K at 1 K/h and the margin
