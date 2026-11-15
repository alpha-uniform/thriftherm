"""The heating side in the cooling season (docs/PHASE12_COOLING.md §2.3): frost protection only, whatever asks for heat."""

from dataclasses import replace
from datetime import datetime

from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.models import DryingState, Override

from .conftest import PARAMS, room_config, room_state

EVENING = datetime(2026, 7, 13, 19, 0)  # in a comfort window of the test rooms


def test_in_the_cooling_season_a_room_gets_the_frost_temperature():
    st = room_state(room_config("wohnzimmer", comfort=21.0), temp=18.0)
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, cooling_season=True)
    assert (res.target, res.target_reason, res.demand) == (PARAMS.frost_temp, "cooling_season", 0.0)


def test_a_heating_override_does_not_heat_in_the_cooling_season():
    ts = EVENING.timestamp()
    st = replace(room_state(room_config("wohnzimmer"), temp=18.0), override=Override(23.0, ts + 3600, ts))
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, cooling_season=True)
    assert res.target == PARAMS.frost_temp


def test_a_quick_heat_up_does_not_heat_in_the_cooling_season():
    st = replace(room_state(room_config("wohnzimmer"), temp=18.0), boost_until_ts=EVENING.timestamp() + 3600)
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, cooling_season=True)
    assert res.target == PARAMS.frost_temp and not res.boost_active


def test_no_drying_starts_in_the_cooling_season():
    cfg = replace(room_config("badezimmer", heat_pump=True), bathroom_drying=True)
    st = replace(room_state(cfg, temp=24.0, rh=90.0), drying=DryingState(True, EVENING.timestamp() - 300, 9.0, manual=True))
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, heat_pump_heat_possible=False, cooling_season=True)
    assert not res.drying.active


def test_in_the_heating_season_nothing_changes():
    st = room_state(room_config("wohnzimmer", comfort=21.0), temp=18.0)
    assert room_engine.evaluate_room(st, EVENING, "auto", PARAMS) == room_engine.evaluate_room(
        st, EVENING, "auto", PARAMS, cooling_season=False
    )


def test_a_thermostat_turned_by_hand_in_the_cooling_season_is_left_alone():
    from custom_components.thriftherm.engines import room_control
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    st = room_state(room_config("schlafzimmer"), temp=21.0)
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, cooling_season=True)
    cmd, _ = room_control.decide(
        "schlafzimmer", "active", res, True, "heat", 22.0, EVENING.timestamp(), RoomCtrlMemory(), held_by_hand=True
    )
    assert (cmd.plan, cmd.reason, cmd.send) == ("hands_off", "heating_by_hand_in_cooling", False)


def test_in_the_cooling_season_a_served_room_keeps_its_reason_while_the_unit_cools():
    """Review 2026-11-13 (K12): it said "heat pump cooling: radiator at setback", and the radiator is at frost."""
    st = room_state(room_config("schlafzimmer", heat_pump=True), temp=24.0)
    res = room_engine.evaluate_room(st, EVENING, "auto", PARAMS, cooling_season=True, heat_pump_cooling=True)
    assert (res.target, res.target_reason) == (PARAMS.frost_temp, "cooling_season")
    assert res.heating_allowed is False  # as before: no radiator heats against the unit
