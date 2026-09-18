from datetime import datetime

from custom_components.thriftherm.engines import room as room_engine, safety

from .conftest import PARAMS, boiler_state, heat_pump_state, room_config, room_state, snapshot

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
