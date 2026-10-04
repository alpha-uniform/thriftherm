"""Setting the planned return the way Home Assistant's own date and time fields do it.

The more-info dialog and the entities card send the date and the time each on
its own (frontend src/dialogs/more-info/controls/more-info-datetime.ts): a new
date keeps the time of day of the current state, or is midnight while the state
is "unknown"; a new time keeps the date and the seconds, and fails in the browser
while the state is "unknown". Neither field sends the value it already shows.
`_date_step` and `_time_step` repeat that arithmetic.

A date without a time is a placeholder: 23:59 of that day, provisional. Away mode
ends then, but nothing pre-heats for it until a time is picked. Any other past
value is refused.

The fields compute from the state they show, so a value is read against that
state, and every change of the return or of away mode is shown at once, not
only when the cycle ends after its thermostat commands and store write. A step
the browser computed before the latest state reached it is read against that
latest state all the same: a documented limit, the push latency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta

import pytest
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.const import (
    CONF_ROOM_ALLOW_ACTIVE,
    CONF_ROOM_CLIMATE,
    CONF_ROOM_SCHEDULE_ENTITY,
    CONF_ROOMS,
    DOMAIN,
)
from custom_components.thriftherm.datetime import return_meant

from .test_integration import _entry_data, _set_states, _setup

RETURN = "datetime.thriftherm_planned_return"
MODE = "select.thriftherm_operating_mode"
NOW = "2026-09-28 15:00:00+02:00"  # Monday afternoon, summer time in Berlin
TODAY = date(2026, 9, 28)
TOMORROW = date(2026, 9, 29)
OCTOBER_3 = date(2026, 10, 3)
OCTOBER_5 = date(2026, 10, 5)
ROOMS = ("badezimmer", "wohnzimmer")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _local(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=dt_util.get_default_time_zone())


def _state_of(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _sent(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # Date.toISOString()


def _date_step(state: str, day: date) -> str | None:
    """Midnight of the day while the state is "unknown", else the day at the time shown; nothing for the date shown.

    The browser reads a time the clock change skips or repeats with the offset from before the change (fold 0).
    """
    if state == "unknown":
        return _sent(_local(day, 0))
    shown = dt_util.as_local(dt_util.parse_datetime(state))
    if shown.date() == day:
        return None
    return _sent(datetime.combine(day, shown.time().replace(fold=0), tzinfo=dt_util.get_default_time_zone()))


def _time_step(state: str, hour: int, minute: int = 0) -> str | None:
    """The date and the seconds shown at the picked hour and minute; nothing for the time shown."""
    assert state != "unknown", "the browser throws before anything is sent"
    shown = dt_util.as_local(dt_util.parse_datetime(state))
    picked = shown.replace(hour=hour, minute=minute, fold=0)
    return None if picked == shown else _sent(picked)


async def _pick(hass: HomeAssistant, sent: str | None) -> str:
    assert sent is not None, "the field sends nothing for the value it already shows"
    await hass.services.async_call("datetime", "set_value", {"entity_id": RETURN, "datetime": sent}, blocking=True)
    await hass.async_block_till_done()
    return hass.states.get(RETURN).state


async def _start(hass: HomeAssistant, freezer, now: str = NOW):
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(now)
    entry = await _setup(hass)
    return entry.runtime_data


async def _start_with_schedule_helpers(hass: HomeAssistant, freezer, now: str = NOW):
    """Every room follows a schedule helper, as in a home with a schedule.heizplan_* per room."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(now)
    _set_states(hass)
    data = _entry_data()
    for room in data[CONF_ROOMS]:
        helper = f"schedule.heizplan_{room['key']}"
        hass.states.async_set(helper, "off", {"next_event": _local(TODAY + timedelta(days=1), 6).isoformat()})
        room[CONF_ROOM_SCHEDULE_ENTITY] = helper
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def _later(hass: HomeAssistant, freezer, coordinator, at: datetime) -> None:
    freezer.move_to(at)
    _set_states(hass)  # the sensors keep reporting
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def _target(hass: HomeAssistant, room: str):
    return hass.states.get(f"sensor.thriftherm_{room}_target_temperature")


def _preheat_start(hass: HomeAssistant, room: str) -> float | None:
    return _target(hass, room).attributes["preheat_start_ts"]


def _provisional(hass: HomeAssistant) -> bool:
    flag = hass.states.get(RETURN).attributes["provisional"]
    assert hass.states.get(MODE).attributes["away_return_provisional"] is flag
    return flag


def _hold_the_cycle(coordinator) -> tuple[asyncio.Event, asyncio.Event]:
    """The next cycle stops at its store write until released: a slow cycle.

    Stands in for any of its awaits, the thermostat and heat pump commands, the MQTT
    publishes, the store; the cycle writes its entities only after them.
    """
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(force: bool = False) -> None:
        del coordinator.async_save_store  # only this once
        entered.set()
        await release.wait()
        await coordinator.async_save_store(force)

    coordinator.async_save_store = held
    return entered, release


async def _meanwhile() -> None:
    """Let calls started while a cycle is held run until they wait for it."""
    for _ in range(50):
        await asyncio.sleep(0)


def _set_value(hass: HomeAssistant, sent: str | None) -> asyncio.Task:
    """The field's call, which the browser does not wait for before the next pick."""
    assert sent is not None, "the field sends nothing for the value it already shows"
    return hass.async_create_task(
        hass.services.async_call("datetime", "set_value", {"entity_id": RETURN, "datetime": sent}, blocking=True)
    )


async def test_back_today_at_22(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)
    assert hass.states.get(RETURN).state == "unknown" and _provisional(hass) is False

    # the date first: midnight today has passed, so the return is today at 23:59 for now
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert state == _state_of(_local(TODAY, 23, 59))
    assert coordinator.mode == "away" and _provisional(hass) is True
    # no hour is chosen yet, so nothing pre-heats for 23:59
    for room in ROOMS:
        assert _target(hass, room).attributes["reason"] == "mode_away" and _preheat_start(hass, room) is None

    # then the time: exactly 22:00 today
    state = await _pick(hass, _time_step(state, 22))
    assert state == _state_of(_local(TODAY, 22))
    assert coordinator.away_return_ts == _local(TODAY, 22).timestamp() and _provisional(hass) is False
    # pre-heating now aims at 22:00: 1.5 K at the starting rate of 0.8 K/h plus 20 minutes
    assert _preheat_start(hass, "wohnzimmer") == pytest.approx(_local(TODAY, 22).timestamp() - 1.5 / 0.8 * 3600 - 20 * 60)
    assert _target(hass, "wohnzimmer").attributes["reason"] == "mode_away"

    # a time of today that has passed is a typing error, not a new provisional return
    with pytest.raises(ServiceValidationError) as err:
        await _pick(hass, _time_step(state, 12))
    assert err.value.translation_key == "return_time_in_past"
    assert hass.states.get(RETURN).state == _state_of(_local(TODAY, 22)) and _provisional(hass) is False


async def test_back_on_3_october_at_noon(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)

    # the date first: only a date, so 23:59 that day stands in for the time, and nothing pre-heats for it
    state = await _pick(hass, _date_step("unknown", OCTOBER_3))
    assert state == _state_of(_local(OCTOBER_3, 23, 59)) and _provisional(hass) is True
    assert coordinator.mode == "away" and {_preheat_start(hass, room) for room in ROOMS} == {None}
    state = await _pick(hass, _time_step(state, 12))
    assert state == _state_of(_local(OCTOBER_3, 12))
    assert coordinator.mode == "away" and coordinator.away_return_ts == _local(OCTOBER_3, 12).timestamp()
    assert _provisional(hass) is False

    # moving that return to today keeps 12:00, which has passed: 23:59 until the time is picked
    state = await _pick(hass, _date_step(state, TODAY))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True
    state = await _pick(hass, _time_step(state, 18, 30))
    assert state == _state_of(_local(TODAY, 18, 30)) and _provisional(hass) is False

    # a real return moved to another day keeps its time and stays real
    state = await _pick(hass, _date_step(state, OCTOBER_5))
    assert state == _state_of(_local(OCTOBER_5, 18, 30)) and _provisional(hass) is False


async def test_only_a_date_is_the_same_placeholder_whichever_way_it_is_picked(hass: HomeAssistant, freezer) -> None:
    """3 October picked straight away, or today first and then 3 October: 23:59 that day, provisional."""
    await _start(hass, freezer)
    direct = await _pick(hass, _date_step("unknown", OCTOBER_3))
    assert _provisional(hass) is True

    await hass.services.async_call(DOMAIN, "clear_away", {}, blocking=True)
    via_today = await _pick(hass, _date_step("unknown", TODAY))
    via_today = await _pick(hass, _date_step(via_today, OCTOBER_3))
    assert direct == via_today == _state_of(_local(OCTOBER_3, 23, 59)) and _provisional(hass) is True


async def test_only_a_date_for_tomorrow_does_not_preheat_tonight(hass: HomeAssistant, freezer) -> None:
    """Taken as midnight, tomorrow would have pre-heated at once at 22:30.

    Rooms with a schedule helper assume comfort at the return: the bathroom from 21:35 (2.5 K at
    1.2 K/h plus 20 minutes before midnight), the living room from 21:47. Their comfort targets would
    have gone out at once, for a time nobody had picked yet.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer, "2026-09-28 22:30:00+02:00")
    state = await _pick(hass, _date_step("unknown", TOMORROW))
    assert state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True
    for room in ROOMS:
        target = _target(hass, room)
        assert float(target.state) == 15.0  # the away temperature
        assert target.attributes["reason"] == "mode_away" and target.attributes["preheat_start_ts"] is None

    await _pick(hass, _time_step(state, 7))
    assert coordinator.away_return_ts == _local(TOMORROW, 7).timestamp() and _provisional(hass) is False
    assert _preheat_start(hass, "badezimmer") == pytest.approx(_local(TOMORROW, 7).timestamp() - 2.5 / 1.2 * 3600 - 20 * 60)


async def test_only_a_date_for_today_after_23_59_is_refused(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer, "2026-09-28 23:59:30+02:00")
    with pytest.raises(ServiceValidationError) as err:
        await _pick(hass, _date_step("unknown", TODAY))
    assert err.value.translation_key == "return_time_in_past"
    assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown"

    state = await _pick(hass, _date_step("unknown", TOMORROW))
    assert state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True


async def test_a_future_midnight_is_a_placeholder_for_automations_too(hass: HomeAssistant, freezer) -> None:
    """datetime.set_value cannot tell an automation from the date field; set_away takes midnight as it is."""
    coordinator = await _start(hass, freezer)
    state = await _pick(hass, _sent(_local(OCTOBER_3, 0)))
    assert state == _state_of(_local(OCTOBER_3, 23, 59)) and _provisional(hass) is True

    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(OCTOBER_3, 0).isoformat()}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.away_return_ts == _local(OCTOBER_3, 0).timestamp() and _provisional(hass) is False

    # with that real plan the date step keeps midnight, and it stays real
    state = await _pick(hass, _date_step(hass.states.get(RETURN).state, OCTOBER_5))
    assert state == _state_of(_local(OCTOBER_5, 0)) and _provisional(hass) is False


async def test_a_real_return_at_23_59_takes_another_minute_first(hass: HomeAssistant, freezer) -> None:
    """The time field sends nothing for the 23:59 it already shows, and a 23:59 sent anyway stays provisional."""
    coordinator = await _start_with_schedule_helpers(hass, freezer)
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert _time_step(state, 23, 59) is None
    state = await _pick(hass, _sent(_local(TODAY, 23, 59)))  # an automation, say
    assert _provisional(hass) is True

    state = await _pick(hass, _time_step(state, 23, 58))
    state = await _pick(hass, _time_step(state, 23, 59))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is False
    assert coordinator.away_return_ts == _local(TODAY, 23, 59).timestamp()
    assert _preheat_start(hass, "badezimmer") == pytest.approx(_local(TODAY, 23, 59).timestamp() - 2.5 / 1.2 * 3600 - 20 * 60)


async def test_a_reached_return_is_no_plan_for_a_past_value(hass: HomeAssistant, freezer) -> None:
    """Until the next cycle ends away mode, a reached return is still the state; a past value is read as after that cycle.

    Setting the reached time again, as an automation might, is refused like any past time. Taken as the plan,
    it would look like the date step for today and quietly become 23:59, keeping away mode on. Only midnight
    becomes the 23:59 placeholder, as it would after the cycle.
    """
    coordinator = await _start(hass, freezer)
    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(TODAY, 15, 30).isoformat()}, blocking=True)
    await hass.async_block_till_done()

    freezer.move_to(_local(TODAY, 15, 30, 20))  # no cycle yet
    with pytest.raises(ServiceValidationError) as err:
        await _pick(hass, _sent(_local(TODAY, 15, 30)))
    assert err.value.translation_key == "return_time_in_past"
    assert coordinator.away_return_ts == _local(TODAY, 15, 30).timestamp() and _provisional(hass) is False

    await _later(hass, freezer, coordinator, _local(TODAY, 15, 31))
    assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown"

    # a reached midnight set again is the exception: the placeholder, as after the cycle, and away mode stays on
    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(TOMORROW, 0).isoformat()}, blocking=True)
    await hass.async_block_till_done()
    freezer.move_to(_local(TOMORROW, 0, 0, 20))  # no cycle yet
    state = await _pick(hass, _sent(_local(TOMORROW, 0)))
    assert state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True and coordinator.mode == "away"


async def test_midnight_after_a_reached_placeholder_is_refused(hass: HomeAssistant, freezer) -> None:
    """00:00 in the minute after a return is reached becomes the placeholder only while 23:59 is still ahead.

    The placeholder itself is reached at 23:59, so 00:00 picked then would stand for 23:59 today, which has
    passed: refused like any past time, and the next cycle ends away mode.
    """
    coordinator = await _start(hass, freezer, "2026-09-28 20:00:00+02:00")
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    freezer.move_to(_local(TODAY, 23, 59, 20))  # reached, no cycle yet
    with pytest.raises(ServiceValidationError) as err:
        await _pick(hass, _time_step(state, 0))
    assert err.value.translation_key == "return_time_in_past"
    assert hass.states.get(RETURN).state == state and _provisional(hass) is True and coordinator.mode == "away"

    await _later(hass, freezer, coordinator, _local(TODAY, 23, 59, 40))
    assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown"


async def test_a_date_step_right_after_a_provisional_return_is_reached_stays_provisional(hass: HomeAssistant, freezer) -> None:
    """Until the next cycle the reached 23:59 is still shown, and the date field keeps it.

    Read as no plan, the 23:59 moved to tomorrow would be a real return: the bathroom would pre-heat
    from 21:34 (2.5 K at 1.2 K/h plus 20 minutes), for a time nobody picked.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer, "2026-09-28 20:00:00+02:00")
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    freezer.move_to(_local(TODAY, 23, 59, 20))  # reached, no cycle yet
    state = await _pick(hass, _date_step(state, TOMORROW))
    assert state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True
    assert coordinator.mode == "away" and {_preheat_start(hass, room) for room in ROOMS} == {None}

    await _later(hass, freezer, coordinator, _local(TOMORROW, 22))
    assert {_target(hass, room).attributes["reason"] for room in ROOMS} == {"mode_away"}


async def test_a_date_step_right_after_a_real_midnight_is_reached_stays_real(hass: HomeAssistant, freezer) -> None:
    """The date field keeps the midnight shown until the next cycle; moved to another day, it is no placeholder."""
    coordinator = await _start(hass, freezer, "2026-09-28 23:00:00+02:00")
    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(TOMORROW, 0).isoformat()}, blocking=True)
    await hass.async_block_till_done()

    freezer.move_to(_local(TOMORROW, 0, 0, 20))  # reached, no cycle yet
    state = await _pick(hass, _date_step(hass.states.get(RETURN).state, OCTOBER_3))
    assert state == _state_of(_local(OCTOBER_3, 0)) and _provisional(hass) is False
    assert coordinator.mode == "away" and coordinator.away_return_ts == _local(OCTOBER_3, 0).timestamp()


async def test_a_date_step_on_the_day_summer_time_starts(hass: HomeAssistant, freezer) -> None:
    """The clock skips 02:00-03:00: a planned 02:30 moved to that day arrives as 03:30, 01:30 UTC."""
    march_29 = date(2026, 3, 29)
    await _start(hass, freezer, "2026-03-29 15:00:00+02:00")
    await hass.services.async_call(DOMAIN, "set_away", {"return_time": "2026-04-02T02:30:00+02:00"}, blocking=True)
    await hass.async_block_till_done()

    sent = _date_step(hass.states.get(RETURN).state, march_29)
    assert sent == "2026-03-29T01:30:00.000Z"  # what the browser sends (checked with node, TZ=Europe/Berlin)
    state = await _pick(hass, sent)
    assert state == _state_of(_local(march_29, 23, 59)) and _provisional(hass) is True


async def test_a_provisional_return_moved_to_another_day_stays_provisional(hass: HomeAssistant, freezer) -> None:
    """23:59 was never picked, so a new date alone does not make it a return to pre-heat for."""
    coordinator = await _start(hass, freezer)
    state = await _pick(hass, _date_step("unknown", TODAY))
    state = await _pick(hass, _date_step(state, OCTOBER_3))
    assert state == _state_of(_local(OCTOBER_3, 23, 59)) and _provisional(hass) is True

    state = await _pick(hass, _time_step(state, 12))
    assert coordinator.away_return_ts == _local(OCTOBER_3, 12).timestamp() and _provisional(hass) is False


async def test_a_return_on_a_past_day_is_refused(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)
    for sent in (_date_step("unknown", TODAY - timedelta(days=1)), _sent(_local(TODAY, 23, 59) - timedelta(days=1))):
        with pytest.raises(ServiceValidationError) as err:
            await _pick(hass, sent)
        assert err.value.translation_key == "return_time_in_past"
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None
    assert hass.states.get(RETURN).state == "unknown"


async def test_an_automation_setting_a_past_time_today_gets_an_error(hass: HomeAssistant, freezer) -> None:
    """Only the value the date field sends is taken leniently; any other past time is refused."""
    coordinator = await _start(hass, freezer)

    # nothing planned: the date field sends midnight, so 10:00 today comes from somewhere else
    with pytest.raises(ServiceValidationError) as err:
        await _pick(hass, _sent(_local(TODAY, 10)))
    assert err.value.translation_key == "return_time_in_past"
    assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown"

    # planned for 3 October at 12:00: the date field sends 12:00, so 10:00 and midnight today are refused
    await _pick(hass, _sent(_local(OCTOBER_3, 12)))
    for past in (_local(TODAY, 10), _local(TODAY, 0)):
        with pytest.raises(ServiceValidationError) as err:
            await _pick(hass, _sent(past))
        assert err.value.translation_key == "return_time_in_past"
    assert hass.states.get(RETURN).state == _state_of(_local(OCTOBER_3, 12)) and _provisional(hass) is False

    # while today at 12:00 is what the date field sends for today
    state = await _pick(hass, _sent(_local(TODAY, 12)))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True


async def test_a_provisional_return_does_not_preheat_rooms_with_a_schedule_helper(hass: HomeAssistant, freezer) -> None:
    """A schedule helper cannot be read ahead, so its rooms assume comfort at the return.

    For the provisional 23:59 that would mean heating from 21:34 (bathroom: 2.5 K at 1.2 K/h plus
    20 minutes) for a time nobody chose. Away mode still ends at 23:59 if no time is ever picked.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer)
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    await _later(hass, freezer, coordinator, _local(TODAY, 23))
    assert coordinator.mode == "away"
    for room in ROOMS:
        target = _target(hass, room)
        assert float(target.state) == 15.0  # the away temperature
        assert target.attributes["reason"] == "mode_away" and target.attributes["preheat_start_ts"] is None

    await _later(hass, freezer, coordinator, _local(TODAY, 23, 59))
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None
    assert hass.states.get(RETURN).state == "unknown" and _provisional(hass) is False


async def test_rooms_with_a_schedule_helper_preheat_once_the_time_is_picked(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start_with_schedule_helpers(hass, freezer)
    state = await _pick(hass, _date_step("unknown", TODAY))
    await _pick(hass, _time_step(state, 22))
    assert _provisional(hass) is False

    # comfort is assumed at 22:00: bathroom 2.5 K at 1.2 K/h, living room 1.5 K at 0.8 K/h, each plus 20 minutes
    back = _local(TODAY, 22).timestamp()
    assert _preheat_start(hass, "badezimmer") == pytest.approx(back - 2.5 / 1.2 * 3600 - 20 * 60)
    assert _preheat_start(hass, "wohnzimmer") == pytest.approx(back - 1.5 / 0.8 * 3600 - 20 * 60)
    assert {_target(hass, room).attributes["reason"] for room in ROOMS} == {"mode_away"}

    await _later(hass, freezer, coordinator, _local(TODAY, 20))
    assert {_target(hass, room).attributes["reason"] for room in ROOMS} == {"away_preheat"}


async def test_set_away_with_a_time_makes_the_return_real(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)
    await _pick(hass, _date_step("unknown", TODAY))
    assert _provisional(hass) is True

    # set_away without a time (the dashboard button) keeps the return as it is, provisional too
    await hass.services.async_call(DOMAIN, "set_away", {}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.away_return_ts == _local(TODAY, 23, 59).timestamp() and _provisional(hass) is True

    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(TODAY, 21).isoformat()}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.away_return_ts == _local(TODAY, 21).timestamp() and _provisional(hass) is False
    assert _preheat_start(hass, "wohnzimmer") == pytest.approx(_local(TODAY, 21).timestamp() - 1.5 / 0.8 * 3600 - 20 * 60)

    # leaving away mode leaves nothing provisional behind
    await hass.services.async_call(DOMAIN, "clear_away", {}, blocking=True)
    for leave in (
        lambda: hass.services.async_call(DOMAIN, "clear_away", {}, blocking=True),
        lambda: hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "auto"}, blocking=True),
    ):
        await _pick(hass, _date_step("unknown", TODAY))
        assert _provisional(hass) is True
        await leave()
        await hass.async_block_till_done()
        assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown" and _provisional(hass) is False


async def test_the_provisional_mark_survives_a_restart(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)
    await _pick(hass, _date_step("unknown", TODAY))
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restarted = entry.runtime_data
    assert restarted is not coordinator
    assert restarted.mode == "away" and restarted.away_return_ts == _local(TODAY, 23, 59).timestamp()
    assert restarted.away_return_provisional is True and _provisional(hass) is True
    assert {_preheat_start(hass, room) for room in ROOMS} == {None}


async def test_the_set_away_action_stays_strict(hass: HomeAssistant, freezer) -> None:
    """Automations get an error for a past time, even today; only the entity's date step is lenient."""
    coordinator = await _start(hass, freezer)
    for past in (_local(TODAY, 0), _local(TODAY, 14, 59)):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "set_away", {"return_time": past.isoformat()}, blocking=True)
        assert err.value.translation_key == "return_time_in_past"
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None


async def test_away_without_a_return(hass: HomeAssistant, freezer) -> None:
    coordinator = await _start(hass, freezer)

    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "away"}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.mode == "away" and coordinator.away_return_ts is None
    assert hass.states.get(RETURN).state == "unknown"
    target = _target(hass, "wohnzimmer")
    assert target.attributes["reason"] == "mode_away" and target.attributes["preheat_start_ts"] is None

    # a return planned later can be dropped again without leaving away mode
    await _pick(hass, _sent(_local(TODAY, 22)))
    await hass.services.async_call(DOMAIN, "set_away", {"clear_return_time": True}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.mode == "away" and hass.states.get(RETURN).state == "unknown"

    # leaving away mode drops a planned return; choosing away again starts without one
    await _pick(hass, _sent(_local(TODAY, 22)))
    for option in ("auto", "away"):
        await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": option}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.mode == "away" and hass.states.get(RETURN).state == "unknown"


SHOWN = pytest.mark.parametrize("at_once", [True, False], ids=["shown_at_once", "shown_when_the_cycle_ends"])


@SHOWN
async def test_a_date_step_while_the_cycle_ends_a_reached_provisional_return(
    hass: HomeAssistant, freezer, monkeypatch, at_once: bool
) -> None:
    """The cycle has ended the reached 23:59 and still waits for a thermostat: the date step for tomorrow stays provisional.

    Read against the coordinator, which has dropped the return already, the 23:59 the date field keeps would
    become a real return tomorrow, pre-heated from 21:34. The end of away mode shows at once, so the field
    sends midnight. Shown only when the cycle ends, as it was before, the field would send the 23:59 it still
    shows, and that is read against the same state: provisional either way.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer, "2026-09-28 20:00:00+02:00")
    if not at_once:
        monkeypatch.setattr(coordinator, "_async_show_away", lambda: None)
    state = await _pick(hass, _date_step("unknown", TODAY))
    assert state == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    freezer.move_to(_local(TODAY, 23, 59, 20))  # reached
    entered, release = _hold_the_cycle(coordinator)
    cycle = hass.async_create_task(coordinator.async_refresh())
    await entered.wait()
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None
    shown = hass.states.get(RETURN).state
    assert shown == ("unknown" if at_once else _state_of(_local(TODAY, 23, 59)))

    call = _set_value(hass, _date_step(shown, TOMORROW))
    await _meanwhile()
    assert coordinator.away_return_ts == _local(TOMORROW, 23, 59).timestamp() and coordinator.away_return_provisional is True
    release.set()
    await cycle
    await call
    await hass.async_block_till_done()

    assert hass.states.get(RETURN).state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True
    assert coordinator.mode == "away"
    await _later(hass, freezer, coordinator, _local(TOMORROW, 22))
    assert {_target(hass, room).attributes["reason"] for room in ROOMS} == {"mode_away"}


@SHOWN
async def test_a_second_date_step_while_the_first_one_is_still_in_the_cycle(
    hass: HomeAssistant, freezer, monkeypatch, at_once: bool
) -> None:
    """Today picked, then tomorrow, while the cycle of the first pick still waits for a thermostat.

    Read against the coordinator, which holds the provisional 23:59 today already, the midnight a field
    still showing "unknown" sends would become a real return at midnight, pre-heated from 21:35 tonight.
    The first pick shows at once, so the field keeps its 23:59. Shown only when the cycle ends, the field
    would send midnight, read against the "unknown" it computed from: provisional either way.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer, "2026-09-28 20:00:00+02:00")
    if not at_once:
        monkeypatch.setattr(coordinator, "_async_show_away", lambda: None)
    entered, release = _hold_the_cycle(coordinator)
    first = _set_value(hass, _date_step("unknown", TODAY))
    await entered.wait()
    shown = hass.states.get(RETURN).state
    assert shown == (_state_of(_local(TODAY, 23, 59)) if at_once else "unknown") and _provisional(hass) is at_once

    second = _set_value(hass, _date_step(shown, TOMORROW))
    await _meanwhile()
    assert coordinator.away_return_ts == _local(TOMORROW, 23, 59).timestamp() and coordinator.away_return_provisional is True
    release.set()
    await first
    await second
    await hass.async_block_till_done()

    assert hass.states.get(RETURN).state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is True
    await _later(hass, freezer, coordinator, _local(TODAY, 22))
    assert {_target(hass, room).attributes["reason"] for room in ROOMS} == {"mode_away"}


async def test_a_slow_thermostat_does_not_hold_back_the_planned_return(hass: HomeAssistant, freezer) -> None:
    """With room control active the first date pick drops the bathroom to 15 °C, and that command takes its time.

    The pick shows before the command is sent, so a second date step keeps the provisional 23:59.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-09-28 20:00:00+02:00")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_set_temperature(call) -> None:
        if call.data["temperature"] == 15.0 and not entered.is_set():
            entered.set()
            await release.wait()

    hass.services.async_register("climate", "set_temperature", slow_set_temperature)
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5})
    data = _entry_data()
    data[CONF_ROOMS][0][CONF_ROOM_CLIMATE] = "climate.bath_bt"
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, options={CONF_ROOM_ALLOW_ACTIVE: True}, unique_id=DOMAIN, title="Thriftherm"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_room_control", "option": "active"}, blocking=True
    )
    await hass.async_block_till_done()

    first = _set_value(hass, _date_step("unknown", TODAY))
    await asyncio.wait_for(entered.wait(), 5)  # the cycle waits for the thermostat
    shown = hass.states.get(RETURN).state
    assert shown == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    second = _set_value(hass, _date_step(shown, TOMORROW))
    await _meanwhile()
    release.set()
    await first
    await second
    await hass.async_block_till_done()
    assert coordinator.away_return_ts == _local(TOMORROW, 23, 59).timestamp() and _provisional(hass) is True


async def test_every_change_of_away_mode_shows_at_once_and_only_once(hass: HomeAssistant, freezer) -> None:
    """Planned return and the operating mode change the moment an action or the cycle changes them.

    Each is written before the slow part of the cycle, the return together with its mark, and the write
    when the cycle ends changes nothing: one change per entity and action, no state in between.
    """
    coordinator = await _start(hass, freezer)
    changes: list[tuple[str, str, bool]] = []

    @callback
    def record(event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        flag = new.attributes["provisional" if new.entity_id == RETURN else "away_return_provisional"]
        changes.append((new.entity_id, new.state, flag))

    async_track_state_change_event(hass, [RETURN, MODE], record)

    async def step(action: Callable[[], Awaitable], *shown: tuple[str, str, bool]) -> None:
        entered, release = _hold_the_cycle(coordinator)
        task = hass.async_create_task(action())
        await entered.wait()
        assert sorted(changes) == sorted(shown)  # before the cycle's slow part
        release.set()
        await task
        await hass.async_block_till_done()
        assert sorted(changes) == sorted(shown)  # the cycle's own write changed nothing
        changes.clear()

    def call(domain: str, service: str, data: dict) -> Callable[[], Awaitable]:
        return lambda: hass.services.async_call(domain, service, data, blocking=True)

    def set_value(value: datetime) -> Callable[[], Awaitable]:
        return call("datetime", "set_value", {"entity_id": RETURN, "datetime": _sent(value)})

    today_23_59 = _state_of(_local(TODAY, 23, 59))
    await step(set_value(_local(TODAY, 0)), (RETURN, today_23_59, True), (MODE, "away", True))
    await step(set_value(_local(TODAY, 22)), (RETURN, _state_of(_local(TODAY, 22)), False), (MODE, "away", False))
    await step(call(DOMAIN, "set_away", {"clear_return_time": True}), (RETURN, "unknown", False), (MODE, "away", False))
    await step(
        call(DOMAIN, "set_away", {"return_time": _local(OCTOBER_3, 12).isoformat()}),
        (RETURN, _state_of(_local(OCTOBER_3, 12)), False),
        (MODE, "away", False),
    )
    await step(
        call("select", "select_option", {"entity_id": MODE, "option": "auto"}), (RETURN, "unknown", False), (MODE, "auto", False)
    )
    await step(call(DOMAIN, "set_away", {}), (MODE, "away", False))  # away without a return: the return stays empty
    await step(set_value(_local(TODAY, 0)), (RETURN, today_23_59, True), (MODE, "away", True))
    await step(call(DOMAIN, "clear_away", {}), (RETURN, "unknown", False), (MODE, "auto", False))

    # the cycle that finds the return reached shows the end of away mode before its own slow part
    await step(set_value(_local(TODAY, 0)), (RETURN, today_23_59, True), (MODE, "away", True))
    freezer.move_to(_local(TODAY, 23, 59, 20))
    await step(coordinator.async_refresh, (RETURN, "unknown", False), (MODE, "auto", False))


async def test_a_second_date_step_sent_before_the_first_one_shows(hass: HomeAssistant, freezer) -> None:
    """A documented limit, the push latency: a step is read against the state it arrives at.

    The browser still showed "unknown" and sends midnight for tomorrow, but the first date step has made
    today's 23:59 the state by then. Against that provisional plan midnight is a real return, and the rooms
    pre-heat for it tonight: the bathroom from 21:35 (2.5 K at 1.2 K/h plus 20 minutes). The value would give
    this case away, but a rule for it would break the rules for set_value from automations, so none is made.
    """
    await _start_with_schedule_helpers(hass, freezer, "2026-09-28 20:00:00+02:00")
    first, second = _date_step("unknown", TODAY), _date_step("unknown", TOMORROW)  # both before the first shows
    assert await _pick(hass, first) == _state_of(_local(TODAY, 23, 59)) and _provisional(hass) is True

    state = await _pick(hass, second)
    assert state == _state_of(_local(TOMORROW, 0)) and _provisional(hass) is False
    assert _preheat_start(hass, "badezimmer") == pytest.approx(_local(TOMORROW, 0).timestamp() - 2.5 / 1.2 * 3600 - 20 * 60)


async def test_a_date_step_sent_just_as_the_cycle_ends_a_reached_return(hass: HomeAssistant, freezer) -> None:
    """A documented limit, the push latency: one date step at that moment is enough.

    The cycle has ended the reached provisional 23:59 and shown "unknown", but the browser still showed
    23:59 and sends 23:59 tomorrow. Read as no plan, that is a real return, pre-heated tomorrow from 21:34.
    """
    coordinator = await _start_with_schedule_helpers(hass, freezer, "2026-09-28 20:00:00+02:00")
    shown = await _pick(hass, _date_step("unknown", TODAY))
    await _later(hass, freezer, coordinator, _local(TODAY, 23, 59, 20))
    assert coordinator.mode == "auto" and hass.states.get(RETURN).state == "unknown"

    state = await _pick(hass, _date_step(shown, TOMORROW))  # computed from the 23:59 the browser still showed
    assert state == _state_of(_local(TOMORROW, 23, 59)) and _provisional(hass) is False
    back = _local(TOMORROW, 23, 59).timestamp()
    assert _preheat_start(hass, "badezimmer") == pytest.approx(back - 2.5 / 1.2 * 3600 - 20 * 60)


async def test_the_return_can_be_set_while_the_cycle_fails(hass: HomeAssistant, freezer, monkeypatch) -> None:
    """A failed cycle leaves *Planned return* available (code audit 2026-09-28, H3).

    It shows Thriftherm's own setting, not a result of the cycle. While it turned unavailable with the
    sensors, Home Assistant dropped datetime.set_value for it with only a warning in the log.
    """
    coordinator = await _start(hass, freezer)
    await _pick(hass, _sent(_local(OCTOBER_3, 12)))
    build = coordinator.builder.build

    def broken(*args, **kwargs):
        raise RuntimeError("a sensor gone missing")

    monkeypatch.setattr(coordinator.builder, "build", broken)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    state = hass.states.get(RETURN).state
    assert state == _state_of(_local(OCTOBER_3, 12))

    # the date step keeps the planned time, as always, and shows at once
    assert await _pick(hass, _date_step(state, OCTOBER_5)) == _state_of(_local(OCTOBER_5, 12))
    assert coordinator.away_return_ts == _local(OCTOBER_5, 12).timestamp() and coordinator.away_return_provisional is False

    await hass.services.async_call(DOMAIN, "set_away", {"return_time": _local(OCTOBER_5, 18).isoformat()}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(RETURN).state == _state_of(_local(OCTOBER_5, 18))

    monkeypatch.setattr(coordinator.builder, "build", build)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(RETURN).state == _state_of(_local(OCTOBER_5, 18)) and _provisional(hass) is False


@pytest.mark.parametrize(
    ("now", "value", "planned", "provisional", "meant", "meant_provisional"),
    [
        # any other future value is taken as sent
        ("2026-09-28 15:00", "2026-09-28 22:00", None, False, "2026-09-28 22:00", False),
        ("2026-09-28 15:00", "2026-10-03 12:00", None, False, "2026-10-03 12:00", False),
        # only a date, nothing planned: 23:59 that day, provisional, today as on any later day
        ("2026-09-28 15:00", "2026-09-28 00:00", None, False, "2026-09-28 23:59", True),
        ("2026-09-28 15:00", "2026-09-29 00:00", None, False, "2026-09-29 23:59", True),
        ("2026-09-28 15:00", "2026-10-03 00:00", None, False, "2026-10-03 23:59", True),
        # ... also for a past day and for today after 23:59, where that 23:59 has passed and is refused
        ("2026-09-28 15:00", "2026-09-27 00:00", None, False, "2026-09-27 23:59", True),
        ("2026-09-28 23:59:30", "2026-09-28 00:00", None, False, "2026-09-28 23:59", True),
        # a real plan: the date step keeps the planned time and stays real, midnight too
        ("2026-09-28 15:00", "2026-10-05 12:00", "2026-10-03 12:00", False, "2026-10-05 12:00", False),
        ("2026-09-28 15:00", "2026-10-05 00:00", "2026-10-03 00:00", False, "2026-10-05 00:00", False),
        # the time picked for a provisional return makes it real, midnight too
        ("2026-09-28 15:00", "2026-09-28 22:00", "2026-09-28 23:59", True, "2026-09-28 22:00", False),
        ("2026-09-28 15:00", "2026-10-03 00:00", "2026-10-03 23:59", True, "2026-10-03 00:00", False),
        # a provisional return moved to another day keeps 23:59 and stays provisional; a real 23:59 stays real
        ("2026-09-28 15:00", "2026-10-03 23:59", "2026-09-28 23:59", True, "2026-10-03 23:59", True),
        ("2026-09-28 15:00", "2026-10-03 23:59", "2026-09-28 23:59", False, "2026-10-03 23:59", False),
        # today, already past, as the date field sends it with a plan: the planned time of day
        ("2026-09-28 15:00", "2026-09-28 12:00", "2026-10-03 12:00", False, "2026-09-28 23:59", True),
        ("2026-09-28 15:00", "2026-09-28 12:00:30", "2026-10-03 12:00:30", False, "2026-09-28 23:59", True),
        # today, already past, any other time (an automation, the time field): passed on and refused
        ("2026-09-28 15:00", "2026-09-28 10:00", None, False, "2026-09-28 10:00", False),
        ("2026-09-28 15:00", "2026-09-28 10:00", "2026-10-03 12:00", False, "2026-09-28 10:00", False),
        ("2026-09-28 15:00", "2026-09-28 12:00", "2026-10-03 12:00:30", False, "2026-09-28 12:00", False),
        ("2026-09-28 15:00", "2026-09-28 12:00", "2026-09-28 23:59", True, "2026-09-28 12:00", False),
        ("2026-09-28 15:00", "2026-09-28 00:00", "2026-09-28 22:00", False, "2026-09-28 00:00", False),
        ("2026-09-28 15:00", "2026-09-27 23:00", None, False, "2026-09-27 23:00", False),
        # a return already reached, before the cycle ends away mode: a past value is read as after the cycle ...
        ("2026-09-28 15:30:20", "2026-09-28 15:30", "2026-09-28 15:30", False, "2026-09-28 15:30", False),
        ("2026-09-28 00:00:20", "2026-09-28 00:00", "2026-09-28 00:00", False, "2026-09-28 23:59", True),
        # (00:00 picked in the time field while the reached 15:30 is still shown, too; with the reached 23:59
        # the placeholder has passed itself, and the coordinator refuses it)
        ("2026-09-28 15:30:20", "2026-09-28 00:00", "2026-09-28 15:30", False, "2026-09-28 23:59", True),
        ("2026-09-28 23:59:20", "2026-09-28 00:00", "2026-09-28 23:59", True, "2026-09-28 23:59", True),
        # ... a future value against the return still shown, whose time of day the date field keeps
        ("2026-09-28 23:59:20", "2026-09-29 23:59", "2026-09-28 23:59", True, "2026-09-29 23:59", True),
        ("2026-09-28 00:00:20", "2026-09-29 00:00", "2026-09-28 00:00", False, "2026-09-29 00:00", False),
        ("2026-09-28 15:30:20", "2026-09-29 15:30", "2026-09-28 15:30", False, "2026-09-29 15:30", False),
        # a browser in another time zone sends its own midnight, which is no placeholder here:
        # London (01:00 in Berlin) today is refused, on a later day it is a real return at 01:00 ...
        ("2026-09-28 15:00", "2026-09-28 01:00", None, False, "2026-09-28 01:00", False),
        ("2026-09-28 15:00", "2026-10-03 01:00", None, False, "2026-10-03 01:00", False),
        # ... and Helsinki's 3 October is 23:00 on 2 October in Berlin
        ("2026-09-28 15:00", "2026-10-02 23:00", None, False, "2026-10-02 23:00", False),
        # the day summer time ends: 23:59 in winter time
        ("2026-10-25 15:00", "2026-10-25 00:00", None, False, "2026-10-25 23:59", True),
    ],
)
async def test_the_return_a_field_means(
    hass: HomeAssistant, now: str, value: str, planned: str | None, provisional: bool, meant: str, meant_provisional: bool
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")

    def local(text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=dt_util.get_default_time_zone())

    planned_utc = None if planned is None else local(planned).astimezone(UTC)
    result = return_meant(local(value).astimezone(UTC), planned_utc, local(now), provisional)
    assert result == (local(meant), meant_provisional)
    if meant.startswith("2026-10-25"):
        assert result[0].astimezone(UTC).hour == 22  # UTC+1 again


@pytest.mark.parametrize(
    ("zone", "now", "sent", "planned", "placeholder"),
    [
        # Berlin, summer time starts on 29 March 2026: a planned 02:30 moved to that day arrives as 03:30
        ("Europe/Berlin", "2026-03-29T13:00Z", "2026-03-29T01:30Z", "2026-04-02T00:30Z", "2026-03-29T21:59Z"),
        # Berlin, summer time ends on 25 October 2026: the browser takes the first 02:30 ...
        ("Europe/Berlin", "2026-10-25T14:00Z", "2026-10-25T00:30Z", "2026-11-01T01:30Z", "2026-10-25T22:59Z"),
        # ... so the second one is not the date step: a past time, refused
        ("Europe/Berlin", "2026-10-25T14:00Z", "2026-10-25T01:30Z", "2026-11-01T01:30Z", None),
        # Santiago skips midnight on 6 September 2026: the date field's midnight arrives as 01:00, picked before or on that day
        ("America/Santiago", "2026-09-05T19:00Z", "2026-09-06T04:00Z", None, "2026-09-07T02:59Z"),
        ("America/Santiago", "2026-09-06T18:00Z", "2026-09-06T04:00Z", None, "2026-09-07T02:59Z"),
    ],
)
async def test_the_date_step_on_a_day_the_clock_changes(
    hass: HomeAssistant, zone: str, now: str, sent: str, planned: str | None, placeholder: str | None
) -> None:
    """The browser reads a skipped or repeated time with the offset from before the change (checked with node)."""
    await hass.config.async_set_time_zone(zone)
    parse = datetime.fromisoformat
    result = return_meant(parse(sent), None if planned is None else parse(planned), parse(now))
    assert result == ((parse(sent), False) if placeholder is None else (parse(placeholder), True))


@pytest.mark.parametrize(
    ("now", "planned", "sent"),
    [
        # a browser in New York, which moves its clock on 1 November, a week after Berlin: the provisional
        # 26 October 23:59 shows as 18:59 there, and 18:59 on 3 November is 00:59 on 4 November in Berlin
        ("2026-10-26T11:00Z", "2026-10-26T22:59Z", "2026-11-03T23:59Z"),
        # a browser in Dubai, which never moves its clock: 20 October 23:59 shows as 21 October 01:59 there,
        # and 01:59 on 28 October is 22:59 on 27 October in Berlin, which moved its clock on 25 October
        ("2026-10-20T10:00Z", "2026-10-20T21:59Z", "2026-10-27T21:59Z"),
    ],
)
async def test_a_browser_changing_its_clock_on_another_day_moves_the_placeholder_by_an_hour(
    hass: HomeAssistant, now: str, planned: str, sent: str
) -> None:
    """A documented limit: the date step keeps the browser's time of day, which is no longer 23:59 in Home Assistant.

    Without the browser's zone that cannot be told from a time picked on purpose, so it arrives as a real
    return an hour off, and the rooms pre-heat for it (sent values checked with node in the browser's zone).
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    parse = datetime.fromisoformat
    assert return_meant(parse(sent), parse(planned), parse(now), True) == (parse(sent), False)
