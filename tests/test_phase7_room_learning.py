"""Phase 7: drying only via Midea, schedule preheat, quick heat-up, COP prior."""

from datetime import datetime, timedelta

import pytest

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.learning import PRIOR_CALIBRATION_DEFAULT, CopMap, prior_cop
from custom_components.thriftherm.models import DryingState, RoomConfig

from .conftest import PARAMS, room_config, room_state

MON_EVENING = datetime(2026, 1, 12, 18, 0)


def _with(state, **changes):
    return state.__class__(**{**state.__dict__, **changes})


def _bath():
    return RoomConfig(**{**room_config("badezimmer", heat_pump=True).__dict__, "bathroom_drying": True})


def _baseline(now_ts):
    return tuple((now_ts - 3 * 3600 + i * 300, 9.0) for i in range(30))


def test_drying_does_not_start_without_heat_pump():
    now_ts = MON_EVENING.timestamp()
    st = _with(room_state(_bath(), temp=20.5, rh=85.0, window="on", window_open_since=300.0), abs_humidity_history=_baseline(now_ts))
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS, heat_pump_heat_possible=False)
    assert res.drying.active is False
    assert res.heating_allowed is False and res.heat_pump_allowed is False
    assert res.demand == 0.0


def test_a_drying_under_way_waits_for_the_heat_pump_and_goes_on_with_the_time_it_has_left():
    """Seen 2026-10-13: the unit was not there yet in the first cycle after a restart, and the drying was gone."""
    now_ts = MON_EVENING.timestamp()
    history = _baseline(now_ts)
    by_hand = DryingState(True, now_ts - 600, 9.0, manual=True)
    st = _with(room_state(_bath(), temp=22.0, rh=85.0), abs_humidity_history=history, drying=by_hand)
    res = room_engine.evaluate_room(st, MON_EVENING, "auto", PARAMS, heat_pump_heat_possible=False)
    # without effect meanwhile: no raised target the radiator would heat for
    assert res.drying.active is False and res.drying_reason == "drying_waiting_for_the_heat_pump"
    assert res.target_reason != "bathroom_drying"
    assert res.drying.started_ts == now_ts - 600 and res.drying.manual

    back = room_engine.evaluate_room(_with(st, drying=res.drying), MON_EVENING, "auto", PARAMS)
    assert back.drying == by_hand and back.drying_reason == "drying_active" and back.target_reason == "bathroom_drying"

    # its time limit counts from its start, also while it waits
    late = MON_EVENING + timedelta(seconds=PARAMS.drying_max_s + 1)
    ended = room_engine.evaluate_room(_with(st, drying=res.drying), late, "auto", PARAMS, heat_pump_heat_possible=False)
    assert ended.drying == DryingState() and ended.drying_reason == "drying_ended_timeout"


def test_schedule_preheat_reaches_comfort_at_window_start():
    cfg = room_config("badezimmer", heat_pump=True, comfort=21.0, setback=17.0)  # weekday window starts 05:15
    st = room_state(cfg, temp=19.5)
    # deficit 1.5 K at 0.5 K/h (heat pump default: what it gives at its minimum) = 3 h + 20 min margin -> start 01:55
    early = room_engine.evaluate_room(st, datetime(2026, 1, 12, 1, 0), "auto", PARAMS)
    assert early.target == 17.0 and early.target_reason == "schedule_setback"
    assert early.preheat_start_ts == pytest.approx(datetime(2026, 1, 12, 1, 55).timestamp(), abs=1)
    later = room_engine.evaluate_room(st, datetime(2026, 1, 12, 2, 0), "auto", PARAMS)
    assert later.target == 21.0 and later.target_reason == "schedule_preheat"


def test_schedule_preheat_uses_learned_rate():
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    st = _with(room_state(cfg, temp=18.0), heat_rate_k_h=3.0)  # 1 h + 20 min before 05:15
    assert room_engine.evaluate_room(st, datetime(2026, 1, 12, 3, 30), "auto", PARAMS).target_reason == "schedule_setback"
    assert room_engine.evaluate_room(st, datetime(2026, 1, 12, 4, 0), "auto", PARAMS).target_reason == "schedule_preheat"


def test_quick_heat_up_raises_target_to_comfort():
    cfg = room_config("badezimmer", heat_pump=True, comfort=21.0, setback=17.0)
    noon = datetime(2026, 1, 12, 12, 0)
    # 19.5: at the heat pump's 0.5 °C/h a colder room would already preheat for the evening at noon
    st = _with(room_state(cfg, temp=19.5), boost_until_ts=noon.timestamp() + 1800)
    res = room_engine.evaluate_room(st, noon, "auto", PARAMS)
    assert res.target == 21.0 and res.target_reason == "boost" and res.boost_active is True
    expired = _with(st, boost_until_ts=noon.timestamp() - 1)
    assert room_engine.evaluate_room(expired, noon, "auto", PARAMS).boost_active is False


def test_prior_curve_and_default_estimate():
    assert prior_cop(-10.0) == pytest.approx(2.2)
    assert prior_cop(-20.0) == pytest.approx(2.2)
    assert prior_cop(0.0) == pytest.approx(3.7 - 2 / 9 * 1.2)
    value, basis = CopMap().estimate(7.0)
    assert basis == "prior" and value == pytest.approx(4.9 * PRIOR_CALIBRATION_DEFAULT)


def test_prior_is_calibrated_by_measured_bins():
    cm = CopMap()
    for _ in range(6):
        cm.add(17.0, 3.3)  # bin 16..18, prior at 17 °C = 6.0
    assert cm.calibration_factor() == pytest.approx(0.55, abs=0.001)
    value, basis = cm.estimate(5.0)
    assert basis == "prior_calibrated"
    assert value == pytest.approx(prior_cop(5.0) * 0.55, abs=0.01)
    assert cm.estimate(17.0) == (3.3, "learned")
    assert cm.bin_count(17.0) == 6 and cm.bin_count(5.0) == 0
