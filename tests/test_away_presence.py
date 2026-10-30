"""Away ends when one of the chosen people comes home, the way a planned return ends it.

Only an arrival counts: a change to "home" from a known place elsewhere. People switch away
mode on as they leave, so whoever is still home then does not end it at once. An unknown
state is no news, and the people seen at a start are taken as they are. Leaving never
starts away mode.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thriftherm.const import DOMAIN

from .test_integration import _set_states, _setup

MODE = "select.thriftherm_operating_mode"
RETURN = "datetime.thriftherm_planned_return"
NOW = "2026-10-30 15:00:00+01:00"
ALEX, SAM = "person.alex", "person.sam"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _today_at(hour: int) -> datetime:
    return datetime.combine(dt_util.now().date(), time(hour), tzinfo=dt_util.get_default_time_zone())


async def _start(hass: HomeAssistant, freezer, persons: list[str] | None, **where: str):
    """Thriftherm with these people chosen; where says where each one is at the start."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    for person, state in where.items():
        hass.states.async_set(f"person.{person}", state)
    entry = await _setup(hass, options={} if persons is None else {"away_return_persons": persons})
    return entry


async def _away(hass: HomeAssistant, return_time: datetime | None = None) -> None:
    data = {} if return_time is None else {"return_time": return_time.isoformat()}
    await hass.services.async_call(DOMAIN, "set_away", data, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(MODE).state == "away"


async def _goes(hass: HomeAssistant, person: str, state: str, **attributes) -> None:
    hass.states.async_set(person, state, attributes)
    await hass.async_block_till_done()


def _mode(hass: HomeAssistant) -> str:
    return hass.states.get(MODE).state


async def test_a_person_coming_home_ends_away_as_a_planned_return_does(hass: HomeAssistant, freezer) -> None:
    entry = await _start(hass, freezer, [ALEX], alex="not_home")
    await _away(hass, _today_at(22))
    assert hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature").attributes["reason"] == "mode_away"

    await _goes(hass, ALEX, "home")

    coordinator = entry.runtime_data
    assert _mode(hass) == "auto" and coordinator.mode == "auto"
    assert coordinator.away_return_ts is None and hass.states.get(RETURN).state == "unknown"
    # the rooms heat at once, not with the next cycle
    assert hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature").attributes["reason"] != "mode_away"


async def test_coming_home_from_a_zone_counts_too(hass: HomeAssistant, freezer) -> None:
    await _start(hass, freezer, [ALEX], alex="Arbeit")
    await _away(hass)
    await _goes(hass, ALEX, "home")
    assert _mode(hass) == "auto"


async def test_a_person_still_home_when_away_begins_does_not_end_it(hass: HomeAssistant, freezer) -> None:
    await _start(hass, freezer, [ALEX], alex="home")
    await _away(hass)

    await _goes(hass, ALEX, "home", gps_accuracy=12)  # still home: a new reading, no arrival
    assert _mode(hass) == "away"
    await _goes(hass, ALEX, "not_home")  # and leaving changes nothing either
    assert _mode(hass) == "away"

    await _goes(hass, ALEX, "home")  # back after leaving: that is an arrival
    assert _mode(hass) == "auto"


async def test_one_of_two_people_coming_home_is_enough(hass: HomeAssistant, freezer) -> None:
    await _start(hass, freezer, [ALEX, SAM], alex="not_home", sam="not_home")
    await _away(hass)
    await _goes(hass, SAM, "home")
    assert _mode(hass) == "auto"


async def test_an_unknown_or_missing_person_changes_nothing(hass: HomeAssistant, freezer) -> None:
    entry = await _start(hass, freezer, [ALEX, SAM], alex="unknown")  # Sam has no state at all
    await _away(hass, _today_at(16))

    await _goes(hass, ALEX, "home")  # where Alex was is not known: perhaps home all along
    assert _mode(hass) == "away"
    await _goes(hass, ALEX, "unavailable")
    assert _mode(hass) == "away"
    await _goes(hass, SAM, "unknown")
    assert _mode(hass) == "away"

    # the planned return works as before
    freezer.move_to(_today_at(16))
    _set_states(hass)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _mode(hass) == "auto"


async def test_the_last_known_place_counts_across_an_unavailable_state(hass: HomeAssistant, freezer) -> None:
    await _start(hass, freezer, [ALEX], alex="not_home")
    await _away(hass)
    await _goes(hass, ALEX, "unavailable")
    assert _mode(hass) == "away"
    await _goes(hass, ALEX, "home")
    assert _mode(hass) == "auto"


async def test_a_restart_takes_the_people_as_they_are(hass: HomeAssistant, freezer) -> None:
    entry = await _start(hass, freezer, [ALEX, SAM], alex="not_home", sam="not_home")
    await _away(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(ALEX, "home")  # came home while Thriftherm was not running
    hass.states.async_remove(SAM)  # and Sam's person shows again only after Thriftherm has started
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _mode(hass) == "away"

    await _goes(hass, SAM, "home")  # first seen at home: where she was before is not known
    assert _mode(hass) == "away"

    await _goes(hass, ALEX, "not_home")
    await _goes(hass, ALEX, "home")
    assert _mode(hass) == "auto"


async def test_without_chosen_people_nobody_ends_away(hass: HomeAssistant, freezer) -> None:
    await _start(hass, freezer, None, alex="not_home")
    await _away(hass)
    await _goes(hass, ALEX, "home")
    assert _mode(hass) == "away"


async def test_people_leaving_never_start_away(hass: HomeAssistant, freezer) -> None:
    entry = await _start(hass, freezer, [ALEX, SAM], alex="home", sam="home")
    await _goes(hass, ALEX, "not_home")
    await _goes(hass, SAM, "not_home")
    freezer.tick(timedelta(minutes=2))
    _set_states(hass)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _mode(hass) == "auto"
