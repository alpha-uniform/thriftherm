"""Room targets from a Home Assistant schedule helper (Phase 10)."""

from datetime import datetime, timedelta

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.models import ScheduleState

from .conftest import PARAMS, room_config, room_state

MON_NIGHT = datetime(2026, 1, 12, 3, 0)


def _with(state, **changes):
    return state.__class__(**{**state.__dict__, **changes})


def test_active_block_uses_comfort_temperature():
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    st = _with(room_state(cfg, temp=20.0), schedule=ScheduleState(active=True))
    res = room_engine.evaluate_room(st, MON_NIGHT, "auto", PARAMS)
    assert res.target == 21.0 and res.target_reason == "schedule_comfort"


def test_block_temperature_wins_over_comfort():
    cfg = room_config("wohnzimmer", comfort=21.0)
    st = _with(room_state(cfg, temp=20.0), schedule=ScheduleState(active=True, temperature=19.5))
    assert room_engine.evaluate_room(st, MON_NIGHT, "auto", PARAMS).target == 19.5


def test_outside_a_block_the_setback_applies():
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    far = MON_NIGHT + timedelta(hours=8)
    st = _with(room_state(cfg, temp=18.0), schedule=ScheduleState(active=False, next_start=far))
    res = room_engine.evaluate_room(st, MON_NIGHT, "auto", PARAMS)
    assert res.target == 17.0 and res.target_reason == "schedule_setback"
    # the preheat start is published even while the setback is still active
    assert res.preheat_start_ts == pytest.approx(far.timestamp() - 3.75 * 3600 - 1200, abs=1)


def test_preheating_starts_from_the_next_block():
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    soon = MON_NIGHT + timedelta(hours=1)
    st = _with(room_state(cfg, temp=18.0), schedule=ScheduleState(active=False, next_start=soon))
    res = room_engine.evaluate_room(st, MON_NIGHT, "auto", PARAMS)
    assert res.target == 21.0 and res.target_reason == "schedule_preheat"


def test_without_next_start_no_preheating():
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    st = _with(room_state(cfg, temp=18.0), schedule=ScheduleState(active=False))
    res = room_engine.evaluate_room(st, MON_NIGHT, "auto", PARAMS)
    assert res.target == 17.0 and res.preheat_start_ts is None


def test_unavailable_schedule_falls_back_to_the_windows():
    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)  # weekday window 05:15-07:30
    st = room_state(cfg, temp=18.0)  # schedule None: helper missing or unavailable
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 6, 0), "auto", PARAMS)
    assert res.target == 21.0 and res.target_reason == "schedule_comfort"


def test_schedule_entity_is_watched_for_instant_updates(hass=None):
    """A change to the schedule helper must trigger a new cycle, not wait for the next minute."""
    from custom_components.thriftherm.adapters.config import room_configs_from_config

    rooms = room_configs_from_config([
        {"key": "bad", "name": "Bad", "temperature_sensor": "sensor.t", "schedule_entity": "schedule.heizplan_bad"}
    ])
    assert rooms[0].schedule_entity == "schedule.heizplan_bad"
