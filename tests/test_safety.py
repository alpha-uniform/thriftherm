from dataclasses import replace
from datetime import datetime

from custom_components.thriftherm.engines import room as room_engine, safety

from .conftest import MISSING, PARAMS, boiler_state, heat_pump_state, reading, room_config, room_state, snapshot

NOW = datetime(2026, 1, 12, 18, 0)


def _rooms_results(snap):
    return {k: room_engine.evaluate_room(s, NOW, "auto", PARAMS) for k, s in snap.rooms.items()}


def test_all_ok(two_rooms):
    snap = snapshot(two_rooms)
    res = safety.evaluate(snap, _rooms_results(snap))
    assert res.state == "ok"
    assert res.issues == ()


def test_single_sensor_failure_degrades_not_fallback():
    bath = room_config("badezimmer", heat_pump=True)
    living = room_config("wohnzimmer")
    rooms = {"badezimmer": room_state(bath, temp=None), "wohnzimmer": room_state(living, temp=18.0)}
    snap = snapshot(rooms)
    res = safety.evaluate(snap, _rooms_results(snap))
    assert res.state == "degraded"
    assert "room_temperature_missing:badezimmer" in res.issues


def test_all_sensors_missing_is_fallback():
    bath = room_config("badezimmer", heat_pump=True)
    living = room_config("wohnzimmer")
    rooms = {"badezimmer": room_state(bath, temp=None), "wohnzimmer": room_state(living, temp=None)}
    snap = snapshot(rooms)
    res = safety.evaluate(snap, _rooms_results(snap))
    assert res.state == "fallback"
    assert "all_room_temperatures_missing" in res.issues


def test_frost_rooms_detected(two_rooms):
    cold = room_state(room_config("kueche"), temp=5.0)
    two_rooms["kueche"] = cold
    snap = snapshot(two_rooms)
    res = safety.evaluate(snap, _rooms_results(snap))
    assert res.frost_rooms == ("kueche",)


def test_ebusd_and_heat_pump_issues(two_rooms):
    snap = snapshot(two_rooms, heat_pump=heat_pump_state(available=False, error_code=None), boiler=boiler_state(signal=False))
    res = safety.evaluate(snap, _rooms_results(snap))
    assert "ebusd_signal_lost" in res.issues
    assert "midea_unavailable" in res.issues
    assert res.state == "degraded"


def test_a_heat_pump_fault_alone_is_listed_but_does_not_degrade(two_rooms):
    # measured 2026-09-26 to 2026-10-03: the unplugged heat pump kept "degraded" on for a week
    snap = snapshot(two_rooms, heat_pump=heat_pump_state(available=False, error_code=None))
    res = safety.evaluate(snap, _rooms_results(snap))
    assert res.state == "ok" and "midea_unavailable" in res.issues
    alone = replace(snap, prices=replace(snap.prices, system_type="none"))  # the heat pump is the only heat source
    assert safety.evaluate(alone, _rooms_results(alone)).state == "degraded"


def test_an_unreachable_heat_pump_is_listed_with_or_without_a_plug_that_measures_it(two_rooms):
    no_plug = replace(heat_pump_state(available=False, error_code=None), plug_power=MISSING)
    snap = snapshot(two_rooms, heat_pump=no_plug)
    assert safety.evaluate(snap, _rooms_results(snap)).issues == ("midea_unavailable",)
    unused = replace(no_plug, configured=False)  # the add-on is not used: nothing to report
    snap = snapshot(two_rooms, heat_pump=unused)
    assert safety.evaluate(snap, _rooms_results(snap)).issues == ()


def test_without_rooms_hands_off():
    snap = snapshot({})
    res = safety.evaluate(snap, {})
    assert res.state == "fallback" and "no_rooms_configured" in res.issues
    assert "all_room_temperatures_missing" not in res.issues


def test_window_unknown_degrades(two_rooms):
    cfg = room_config("kueche")
    two_rooms["kueche"] = room_state(cfg, temp=19.0, window=None)
    snap = snapshot(two_rooms)
    res = safety.evaluate(snap, _rooms_results(snap))
    assert "window_state_unknown:kueche" in res.issues


def test_frost_room_stays_a_frost_room_until_one_kelvin_above_the_limit(two_rooms):
    """Without hysteresis a room hovering at the limit toggles the boiler every few minutes."""
    def at(temp):
        two_rooms["kueche"] = room_state(room_config("kueche"), temp=temp)
        snap = snapshot(two_rooms)
        return snap, _rooms_results(snap)

    snap, rooms = at(PARAMS.frost_temp + 0.5)
    assert safety.evaluate(snap, rooms).frost_rooms == ()  # not yet a frost room
    assert safety.evaluate(snap, rooms, previous_frost_rooms=("kueche",)).frost_rooms == ("kueche",)  # stays one
    snap, rooms = at(PARAMS.frost_temp + 1.0)
    assert safety.evaluate(snap, rooms, previous_frost_rooms=("kueche",)).frost_rooms == ()  # released


# ---------------------------------------------------------------- a start, and short dropouts (I4, 2026-11-08)
def _restart(two_rooms, reason="unavailable"):
    """As seen after every restart: one room sensor, its window and the boiler's flow not back yet."""
    two_rooms["badezimmer"] = room_state(two_rooms["badezimmer"].config, temp=None, window=None)
    two_rooms["badezimmer"] = replace(two_rooms["badezimmer"], temperature=reading(None, reason=reason))
    boiler = replace(boiler_state(), flow_temp=reading(None, entity_id="sensor.flow", reason="unknown"))
    return snapshot(two_rooms, boiler=boiler)


def test_inputs_that_have_not_arrived_yet_are_no_degradation_while_starting(two_rooms):
    """For 50-60 s after each restart "degraded" with 9-10 issues, and a warning in the log."""
    snap = _restart(two_rooms)
    assert safety.evaluate(snap, _rooms_results(snap)).state == "degraded"  # as before, once started
    res = safety.evaluate(snap, _rooms_results(snap), arrived=frozenset())
    assert res.state == "ok" and res.issues == ()


def test_a_reading_that_is_there_but_wrong_degrades_while_starting_as_well(two_rooms):
    snap = _restart(two_rooms, reason="out_of_range")
    res = safety.evaluate(snap, _rooms_results(snap), arrived=frozenset())
    assert res.state == "degraded" and "room_temperature_missing:badezimmer" in res.issues


def test_while_starting_no_room_temperature_at_all_stays_hands_off(two_rooms):
    for key, state in two_rooms.items():
        two_rooms[key] = room_state(state.config, temp=None)
    snap = snapshot(two_rooms)
    assert safety.evaluate(snap, _rooms_results(snap), arrived=frozenset()).state == "fallback"


def test_a_heat_pump_away_for_ten_seconds_or_less_is_no_degradation(two_rooms):
    snap = snapshot(two_rooms, heat_pump=heat_pump_state(available=False, error_code=None))
    alone = replace(snap, prices=replace(snap.prices, system_type="none"))  # where it counts: the only heat source
    assert safety.evaluate(alone, _rooms_results(alone), heat_pump_gone_s=8.0).state == "ok"
    assert safety.evaluate(alone, _rooms_results(alone), heat_pump_gone_s=11.0).state == "degraded"
