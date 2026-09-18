from dataclasses import replace
from datetime import datetime

from custom_components.thriftherm.engines import (
    advisor,
    boiler as boiler_engine,
    economics,
    heat_pump as heat_pump_engine,
    room as room_engine,
    safety,
)
from custom_components.thriftherm.models import CopResult

from .conftest import PARAMS, PRICES, boiler_state, heat_pump_state, room_config, room_state, snapshot

NOW = datetime(2026, 1, 12, 18, 0)


def _codes(adv):
    return [r.code for r in adv.reasons]


def _run(snap, cop_value=None, gates=("airflow_curve_not_calibrated",), current=None):
    rooms = {k: room_engine.evaluate_room(s, NOW, snap.mode, PARAMS) for k, s in snap.rooms.items()}
    saf = safety.evaluate(snap, rooms)
    boil = boiler_engine.evaluate(snap.boiler, PRICES)
    mid = heat_pump_engine.evaluate(snap.heat_pump, PARAMS)
    cop = CopResult(cop_value, cop_value, None, mid.electrical_power_w, None, 20 if cop_value else 0, () if cop_value else gates)
    econ = economics.evaluate(PRICES, cop_value, PARAMS.break_even_margin_on, PARAMS.break_even_margin_off, current)
    return advisor.advise(snap, rooms, econ, cop, mid, boil, saf)


def test_heat_pump_chosen_when_cop_good(two_rooms):
    # bath (midea room) cold, living room cold -> both
    adv = _run(snapshot(two_rooms), cop_value=3.6)
    assert adv.source == "both"
    assert "cop_above_break_even" in _codes(adv)
    assert any(r.code == "room_below_target" and r.param("room") == "badezimmer" for r in adv.reasons)


def test_boiler_when_cop_below_break_even(two_rooms):
    adv = _run(snapshot(two_rooms), cop_value=2.0)
    assert adv.source == "boiler"
    assert "cop_below_break_even" in _codes(adv)


def test_boiler_default_when_cop_unknown(two_rooms):
    adv = _run(snapshot(two_rooms))
    assert adv.source == "boiler"
    assert "cop_unknown" in _codes(adv)


def test_cooling_user_blocks_heat_pump(two_rooms):
    adv = _run(snapshot(two_rooms, heat_pump=heat_pump_state(hvac_mode="cool")), cop_value=4.0)
    assert adv.source == "boiler"
    assert adv.blocked["midea"] == "cooling_user"


def test_outdoor_too_cold_blocks_heat_pump(two_rooms):
    adv = _run(snapshot(two_rooms, outdoor=-12.0), cop_value=4.0)  # limit -10 °C
    assert adv.blocked["midea"] == "outdoor_too_cold"
    assert adv.source == "boiler"


def test_mode_off_only_frost_protection(two_rooms):
    adv = _run(snapshot(two_rooms, mode="off"), cop_value=4.0)
    assert adv.source == "none"
    cold = {"kueche": room_state(room_config("kueche"), temp=4.0)}
    adv2 = _run(snapshot(cold, mode="off"))
    assert adv2.source == "boiler"
    assert "frost_protection" in _codes(adv2)


def test_mode_boiler_only_and_heat_pump_only(two_rooms):
    adv = _run(snapshot(two_rooms, mode="boiler_only"), cop_value=5.0)
    assert adv.source == "boiler"
    assert adv.blocked["midea"] == "mode_boiler_only"
    adv2 = _run(snapshot(two_rooms, mode="midea_only"))
    assert adv2.source == "midea"  # living room demand is ignored except frost


def test_safety_fallback_uses_boiler(two_rooms):
    for k in two_rooms:
        two_rooms[k] = room_state(two_rooms[k].config, temp=None)
    adv = _run(snapshot(two_rooms), cop_value=5.0)
    assert adv.source == "boiler"
    assert adv.blocked["midea"] == "safety_fallback"
    adv2 = _run(snapshot(two_rooms, boiler=boiler_state(signal=False)), cop_value=5.0)
    assert adv2.source == "none"


def test_no_demand_gives_none():
    warm = {
        "badezimmer": room_state(room_config("badezimmer", heat_pump=True), temp=22.0),
        "wohnzimmer": room_state(room_config("wohnzimmer", comfort=18.5), temp=19.5),
    }
    adv = _run(snapshot(warm), cop_value=4.0)
    assert adv.source == "none"
    assert "no_demand" in _codes(adv)


def test_window_open_suspends_room(two_rooms):
    cfg = two_rooms["badezimmer"].config
    two_rooms["badezimmer"] = room_state(cfg, temp=18.0, window="on", window_open_since=600.0)
    adv = _run(snapshot(two_rooms), cop_value=4.0)
    assert adv.source == "boiler"  # only living room remains
    assert "room_window_open" in _codes(adv)


def test_hwc_reason_reported(two_rooms):
    adv = _run(snapshot(two_rooms, boiler=boiler_state(pump="hwc")), cop_value=4.0)
    assert "hot_water_priority" in _codes(adv)


def test_thermostat_switched_off_asks_nothing_of_the_boiler():
    # measured 2026-09-18: the bathroom thermostat was off, yet its override kept the boiler cycling
    rooms = {
        "badezimmer": room_state(room_config("badezimmer", heat_pump=True), temp=22.0),
        "wohnzimmer": replace(room_state(room_config("wohnzimmer", comfort=21.0), temp=17.0), trv_hvac_mode="off"),
    }
    adv = _run(snapshot(rooms), cop_value=2.0)
    assert adv.source == "none"
    assert not any(r.code == "room_below_target" and r.param("room") == "wohnzimmer" for r in adv.reasons)


def test_switched_off_radiator_in_a_heat_pump_room_does_not_call_the_boiler():
    rooms = {
        "badezimmer": replace(room_state(room_config("badezimmer", heat_pump=True), temp=17.0), trv_hvac_mode="off"),
        "wohnzimmer": room_state(room_config("wohnzimmer", comfort=18.5), temp=19.5),
    }
    adv = _run(snapshot(rooms), cop_value=2.0)  # Midea not worth it, so only the radiator could heat
    assert adv.source == "none"


def test_a_thermostat_that_is_merely_unavailable_still_counts():
    # no answer is not "off": the valve keeps regulating on its own, comfort comes first
    rooms = {
        "badezimmer": room_state(room_config("badezimmer", heat_pump=True), temp=22.0),
        "wohnzimmer": replace(room_state(room_config("wohnzimmer", comfort=21.0), temp=17.0), trv_hvac_mode=None),
    }
    assert _run(snapshot(rooms), cop_value=2.0).source == "boiler"
