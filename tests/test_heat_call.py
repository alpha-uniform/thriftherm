"""Which rooms call the boiler, and which heat along: measured cases from 2026-09-24 to 26."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.thriftherm.engines import advisor, heat_call
from custom_components.thriftherm.engines.heat_call import CallMemory
from custom_components.thriftherm.engines.room import comfort_end, evaluate_room, parse_schedule
from custom_components.thriftherm.models import ScheduleState

from .conftest import PARAMS, room_config, room_result, room_state, snapshot

NOW = 2_000_000.0
HOUR = 3600.0


def rr(key="badezimmer", temp=20.0, target=21.0, reason="schedule_comfort", trend=None, allowed=True, demand=None, preheat=None):
    return room_result(
        key, target, temp, target_reason=reason, deviation_k=None if temp is None else round(temp - target, 2),
        demand=demand if demand is not None else (0.0 if temp is None else max(0.0, (target - temp) / 2.0)),
        trend_k_per_h=trend, window_open=not allowed, heating_allowed=allowed, preheat_start_ts=preheat,
    )


def step(room, mem, now=NOW, end=None):
    return heat_call.update(room, mem, now, end)


# ------------------------------------------------------------------ hysteresis
def test_a_room_starts_calling_at_three_tenths_and_stops_at_one_tenth():
    state, mem = step(rr(temp=20.8), CallMemory())  # 0.2 K below: the old rule already called here
    assert state == "idle" and not mem.calling
    state, mem = step(rr(temp=20.7), mem)
    assert state == "calling" and mem.calling and mem.since_ts == NOW
    state, mem = step(rr(temp=20.85), mem, NOW + 60)  # still 0.15 K below: keeps calling
    assert state == "calling" and mem.since_ts == NOW
    state, mem = step(rr(temp=20.9), mem, NOW + 120)
    assert state == "idle" and mem == CallMemory()


def test_a_lower_target_is_judged_again():
    # 2026-09-26 12:35: an override of 21.5 °C was lifted, the bathroom (20.8 °C) kept calling
    # for its 21.0 °C because the call had begun 0.7 K below the higher target
    _, mem = step(rr(temp=20.8, target=21.5, reason="override"), CallMemory(), NOW)
    assert mem.calling and mem.target == 21.5
    state, mem = step(rr(temp=20.8, target=21.0), mem, NOW + 60)
    assert state == "idle" and mem == CallMemory()
    # still far enough below the lower target: a fresh call
    _, mem = step(rr(temp=20.4, target=21.5, reason="override"), CallMemory(), NOW)
    state, mem = step(rr(temp=20.4, target=21.0), mem, NOW + 60)
    assert state == "calling" and mem.since_ts == NOW + 60 and mem.target == 21.0
    # a higher target keeps the running call
    state, mem = step(rr(temp=20.5, target=22.0), mem, NOW + 120)
    assert state == "calling" and mem.since_ts == NOW + 60


# ------------------------------------------------------------------ stall
def test_a_room_that_hangs_just_below_target_counts_as_reached():
    # 2026-09-26: the bathroom sat at 20.7-20.8 °C from 08:54 to 11:32, the boiler fired 13 times
    _, mem = step(rr(temp=19.7), CallMemory(), NOW)
    state, mem = step(rr(temp=20.7, trend=0.1), mem, NOW + HOUR - 60)
    assert state == "calling"  # not an hour yet
    state, mem = step(rr(temp=20.8, trend=0.1), mem, NOW + HOUR)
    assert state == "stalled" and mem.stalled_target == 21.0 and not mem.calling

    # it stays reached while it drifts a little ...
    state, mem = step(rr(temp=20.6, trend=-0.1), mem, NOW + 2 * HOUR)
    assert state == "stalled"
    # ... and calls again once it has fallen half a degree below target
    state, mem = step(rr(temp=20.5, trend=-0.1), mem, NOW + 3 * HOUR)
    assert state == "calling" and mem.stalled_target is None


def test_a_room_still_rising_is_not_stalled():
    _, mem = step(rr(temp=19.7), CallMemory(), NOW)
    state, _ = step(rr(temp=20.8, trend=0.4), mem, NOW + HOUR)
    assert state == "calling"


def test_a_new_target_ends_the_stall():
    mem = CallMemory(stalled_target=21.0)
    state, mem = step(rr(temp=20.8, target=22.0), mem)
    assert state == "calling" and mem.stalled_target is None


def test_quick_heat_up_ignores_stall_and_end_of_comfort():
    _, mem = step(rr(temp=19.7, reason="boost"), CallMemory(), NOW)
    state, mem = step(rr(temp=20.8, trend=0.0, reason="boost"), mem, NOW + 2 * HOUR, end=NOW + 2 * HOUR + 600)
    assert state == "calling"
    state, _ = step(rr(temp=20.6, reason="schedule_comfort+boost"), CallMemory(stalled_target=21.0), NOW)
    assert state == "calling"


# ------------------------------------------------------------------ end of comfort
def test_no_call_in_the_last_half_hour_of_comfort():
    # 2026-09-25: a heating phase from 07:25:39 to 07:30:40, five minutes before the setback
    state, mem = step(rr(temp=20.0), CallMemory(calling=True, since_ts=NOW - 600), NOW, end=NOW + 25 * 60)
    assert state == "comfort_ending" and mem == CallMemory()
    state, _ = step(rr(temp=20.0), CallMemory(), NOW, end=NOW + 45 * 60)
    assert state == "calling"
    # preheating and override are not a comfort window: no end rule
    state, _ = step(rr(temp=20.0, reason="schedule_preheat"), CallMemory(), NOW, end=NOW + 60)
    assert state == "calling"


def test_a_comfort_block_that_ended_a_moment_ago_starts_no_call_while_its_helper_has_not_switched_yet():
    # seen 2026-10-21 22:00 and 2026-10-22 07:30: the bedroom's schedule helper switched 15 ms before the
    # bathroom's. The cycle it set off still saw the bathroom's block running (21 °C, its end passed by
    # 15 ms), the bathroom called, and the boiler lit once for a target that fell to 17 °C seconds later.
    cfg = room_config("badezimmer", comfort=21.0, setback=17.0)
    end = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)  # 22:00 in Berlin
    bath = replace(room_state(cfg, temp=20.5), schedule=ScheduleState(active=True, temperature=21.0, next_end=end))
    for late in (timedelta(milliseconds=15), timedelta(seconds=90)):
        res = evaluate_room(bath, end + late, "auto", PARAMS)
        assert (res.target, res.target_reason, res.comfort_end_ts) == (21.0, "schedule_comfort", end.timestamp())
        state, mem = heat_call.update(res, CallMemory(), (end + late).timestamp(), res.comfort_end_ts)
        assert state == "comfort_ending" and mem == CallMemory()


def test_a_comfort_end_long_past_a_new_block_or_a_quick_heat_up_still_calls():
    # the grace after a comfort end covers the moment until the schedule helper switches, no longer
    state, _ = step(rr(temp=20.5), CallMemory(), NOW, end=NOW - heat_call.COMFORT_END_PASSED_S - 1)
    assert state == "calling"
    # a new block that has begun brings its own end, far enough ahead
    state, _ = step(rr(temp=20.5), CallMemory(), NOW, end=NOW + 2 * HOUR)
    assert state == "calling"
    # quick heat-up, drying and preheat ask for heat now, whatever the comfort end
    for reason in ("boost", "schedule_comfort+boost", "bathroom_drying", "schedule_preheat"):
        state, _ = step(rr(temp=20.5, reason=reason), CallMemory(), NOW, end=NOW - 0.015)
        assert state == "calling", reason


def test_comfort_end_of_text_windows():
    cfg = replace(room_config("badezimmer"), schedule_weekday=parse_schedule("05:15-07:30,17:30-22:00"))
    tz = UTC
    wednesday = datetime(2026, 9, 23, 18, 0, tzinfo=tz)
    assert comfort_end(cfg, wednesday) == datetime(2026, 9, 23, 22, 0, tzinfo=tz)
    assert comfort_end(cfg, wednesday.replace(hour=12)) is None
    # windows that meet are one; comfort around the clock never ends
    joined = replace(cfg, schedule_weekday=parse_schedule("17:30-22:00,22:00-23:00"))
    assert comfort_end(joined, wednesday) == datetime(2026, 9, 23, 23, 0, tzinfo=tz)
    always = replace(cfg, schedule_weekday=parse_schedule("00:00-23:59"), schedule_weekend=parse_schedule("00:00-23:59"))
    assert comfort_end(always, wednesday) is None


def test_comfort_end_of_a_schedule_helper():
    cfg = room_config("badezimmer")
    now = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
    end = now + timedelta(hours=4)
    assert comfort_end(cfg, now, ScheduleState(active=True, next_end=end)) == end
    assert comfort_end(cfg, now, ScheduleState(active=False, next_start=end)) is None


# ------------------------------------------------------------------ other states
def test_window_open_or_thermostat_off_never_calls():
    state, mem = step(rr(temp=18.0, allowed=False), CallMemory(calling=True, since_ts=NOW))
    assert state == "not_allowed" and mem == CallMemory()


def test_without_a_temperature_the_demand_decides():
    state, _ = step(rr(temp=None, demand=0.4), CallMemory())
    assert state == "calling"
    state, _ = step(rr(temp=None, demand=0.0), CallMemory())
    assert state == "idle"


def test_a_stall_outlasts_a_restart_whose_first_cycle_has_no_room_temperature():
    # the Zigbee sensors report one by one after a restart, the bathroom after 38 s
    _, mem = step(rr(temp=19.7), CallMemory(), NOW)
    state, mem = step(rr(temp=20.8, trend=0.1), mem, NOW + HOUR)
    assert state == "stalled"
    restored = CallMemory.from_storage(mem.to_storage())
    _, mem = step(rr(temp=None), restored, NOW + HOUR + 60)  # the room sensor has not reported since the restart
    state, mem = step(rr(temp=20.6, trend=None), mem, NOW + HOUR + 120)  # 0.4 K below: inside the stall's release
    assert state == "stalled" and mem.stalled_target == 21.0


def test_a_call_keeps_its_hysteresis_and_its_age_through_a_cycle_without_a_temperature():
    _, mem = step(rr(temp=20.6), CallMemory(), NOW)
    _, mem = step(rr(temp=None), mem, NOW + 60)
    state, mem = step(rr(temp=20.8), mem, NOW + 120)  # 0.2 K below: a running call goes on to 0.1 K
    assert state == "calling" and mem.since_ts == NOW


@pytest.mark.parametrize("stored", [None, 3, "x", {"calling": "yes", "since_ts": "a", "stalled_target": True}])
def test_stored_call_memory_that_is_not_valid_is_empty(stored):
    assert CallMemory.from_storage(stored) == CallMemory()


def test_call_memory_round_trip():
    mem = CallMemory(calling=True, since_ts=NOW, stalled_target=None, target=21.5)
    assert CallMemory.from_storage(mem.to_storage()) == mem
    stalled = CallMemory(stalled_target=21.0, stalled_helped=True)
    assert CallMemory.from_storage(stalled.to_storage()) == stalled


# ------------------------------------------------------------------ heat along
def test_rooms_heat_along_while_the_boiler_runs():
    rooms = {
        "badezimmer": rr("badezimmer", temp=19.7, target=21.0),  # calls
        "wohnzimmer": rr("wohnzimmer", temp=18.3, target=18.5),  # in comfort, just below
        "kuche": rr("kuche", temp=18.9, target=18.5),  # already above comfort
        "schlafzimmer": rr("schlafzimmer", temp=18.0, target=17.0, reason="schedule_setback", preheat=NOW + 1800),
        "arbeitszimmer": rr("arbeitszimmer", temp=18.0, target=17.0, reason="schedule_setback", preheat=NOW + 3 * HOUR),
    }
    states = {"badezimmer": "calling", "wohnzimmer": "idle", "kuche": "idle", "schlafzimmer": "idle", "arbeitszimmer": "idle"}
    comfort = {"badezimmer": 21.0, "wohnzimmer": 18.5, "kuche": 18.5, "schlafzimmer": 20.0, "arbeitszimmer": 18.5}
    out = heat_call.join(rooms, states, comfort, NOW)

    assert out["badezimmer"] is rooms["badezimmer"]
    assert (out["wohnzimmer"].target, out["wohnzimmer"].target_reason) == (19.0, "joined_boiler_run")
    assert out["wohnzimmer"].deviation_k == -0.7
    assert out["kuche"] is rooms["kuche"]
    # the bedroom would start preheating in half an hour: it starts now, with the bathroom
    assert (out["schlafzimmer"].target, out["schlafzimmer"].target_reason) == (20.0, "joined_preheat")
    assert out["arbeitszimmer"] is rooms["arbeitszimmer"]  # three hours away: not yet


def test_rooms_that_may_not_heat_do_not_join():
    rooms = {"wohnzimmer": rr("wohnzimmer", temp=18.0, target=18.5, allowed=False), "kuche": rr("kuche", temp=None, target=18.5)}
    out = heat_call.join(rooms, {}, {"wohnzimmer": 18.5, "kuche": 18.5}, NOW)
    assert out == rooms


def test_an_override_does_not_join():
    rooms = {"wohnzimmer": rr("wohnzimmer", temp=18.0, target=18.5, reason="override")}
    assert heat_call.join(rooms, {}, {"wohnzimmer": 18.5}, NOW) == rooms


def test_a_room_whose_thermostat_holds_its_valve_shut_does_not_heat_along():
    """Review 2026-10-30: raised by half a degree, the thermostat opened the valve that issue #3 keeps shut, and the
    next cycle the room called the boiler itself and lengthened the run."""
    rooms = {"wohnzimmer": replace(rr("wohnzimmer", temp=18.0, target=18.5), thermostat_heating=False)}
    states = {"wohnzimmer": heat_call.CALL_THERMOSTAT_IDLE}
    assert heat_call.join(rooms, states, {"wohnzimmer": 18.5}, NOW) == rooms


# ------------------------------------------------------------------ advisor
def _advise(rooms, calls, **kw):
    from custom_components.thriftherm.models import BoilerResult, CopResult, EconomicsResult, HeatPumpResult, SafetyResult

    states = {k: room_state(room_config(k)) for k in rooms}
    snap = snapshot(states)
    return advisor.advise(
        snap, rooms,
        EconomicsResult(cheaper_source="boiler", break_even_cop=3.0, heat_pump_cost_per_kwh_thermal=None,
                        gas_cost_per_kwh_thermal=0.1, saving_pct=None),
        CopResult(None, None, None, None, None, 0, ()),
        HeatPumpResult(run_state="off", electrical_power_w=None, electrical_power_source=None, heating_available=False),
        BoilerResult(available=True, hc_active=False, hwc_active=False, delta_t_k=None, thermal_power_estimate_w=None,
                     gas_power_w=0.0, gas_energy_kwh=None),
        SafetyResult(state="ok", issues=(), frost_rooms=()),
        boiler_calls=calls, **kw,
    )


def test_the_boiler_follows_the_calls_not_the_raw_demand():
    rooms = {"wohnzimmer": rr("wohnzimmer", temp=18.3, target=18.5, demand=0.1)}
    assert _advise(rooms, frozenset()).source == "none"
    assert _advise(rooms, frozenset({"wohnzimmer"})).source == "boiler"


def test_a_heat_pump_room_just_below_target_does_not_start_the_boiler():
    # 2026-09-26 12:20, right after the update: the bathroom (a heat pump room) at 20.8 °C of 21.0,
    # "warm enough" by its call state, still started the boiler through the heat pump path
    from custom_components.thriftherm.models import BoilerResult, CopResult, EconomicsResult, HeatPumpResult, SafetyResult

    rooms = {"badezimmer": rr("badezimmer", temp=20.8, target=21.0, demand=0.1)}
    states = {"badezimmer": room_state(room_config("badezimmer", heat_pump=True))}
    snap = snapshot(states)
    snap = replace(snap, heat_pump=replace(snap.heat_pump, configured=True))

    def advise(calls):
        return advisor.advise(
            snap, rooms,
            EconomicsResult(cheaper_source="midea", break_even_cop=2.9, heat_pump_cost_per_kwh_thermal=0.076,
                            gas_cost_per_kwh_thermal=0.106, saving_pct=28.0),
            CopResult(None, None, None, None, None, 0, ()),
            HeatPumpResult(run_state="off", electrical_power_w=None, electrical_power_source=None, heating_available=True),
            BoilerResult(available=True, hc_active=False, hwc_active=False, delta_t_k=None, thermal_power_estimate_w=None,
                         gas_power_w=0.0, gas_energy_kwh=None),
            SafetyResult(state="ok", issues=(), frost_rooms=()),
            boiler_calls=calls, heat_pump_acts=False,
        )

    assert advise(frozenset()).source == "none"
    assert advise(frozenset({"badezimmer"})).source in ("midea", "both")


def test_an_unknown_trend_after_a_restart_is_no_stall():
    # code review 2026-09-28: the call's start survives a restart, the trend needs half an hour
    _, mem = step(rr(temp=20.6), CallMemory())
    state, _ = step(rr(temp=20.8, trend=None), mem, NOW + 2 * HOUR)
    assert state == "calling"
    state, _ = step(rr(temp=20.8, trend=0.05), mem, NOW + 2 * HOUR)
    assert state == "stalled"


# ---- the thermostat's own word (issue #3, 2026-10-28) ----------------------------------------------------------
def test_a_room_whose_thermostat_reports_idle_does_not_call_the_boiler():
    """Thriftherm's call alone could start the boiler for a room whose valve its thermostat holds shut."""
    shut = replace(rr(temp=20.6), thermostat_heating=False)  # 0.4 K below: Thriftherm would call
    state, mem = step(shut, CallMemory())
    assert state == heat_call.CALL_THERMOSTAT_IDLE
    state, _ = step(replace(shut, thermostat_heating=True), mem, now=NOW + 60)
    assert state == heat_call.CALL_CALLING


def test_the_thermostat_heating_does_not_make_a_room_call_by_itself():
    """Thriftherm's own call stays in charge: a thermostat reporting heating inside the band calls nothing."""
    state, _ = step(replace(rr(temp=20.9), thermostat_heating=True), CallMemory())  # 0.1 K below: no call
    assert state == heat_call.CALL_IDLE


def test_a_thermostat_without_a_usable_hvac_action_changes_nothing():
    state, _ = step(replace(rr(temp=20.6), thermostat_heating=None), CallMemory())
    assert state == heat_call.CALL_CALLING


def test_the_hysteresis_runs_on_while_the_thermostat_holds_its_valve_shut():
    """Called from 0.3 K below, the call holds to 0.1 K below; an idle report in between pauses it, it does not end it."""
    state, mem = step(rr(temp=20.7), CallMemory())
    assert state == heat_call.CALL_CALLING
    state, mem = step(replace(rr(temp=20.8), thermostat_heating=False), mem, now=NOW + 60)
    assert state == heat_call.CALL_THERMOSTAT_IDLE
    state, _ = step(replace(rr(temp=20.8), thermostat_heating=True), mem, now=NOW + 120)
    assert state == heat_call.CALL_CALLING  # 0.2 K below, inside the band: the call that began goes on


# ---- the boiler adds the peak for a heat pump room that lags (2026-10-30) --------------------------------------
def test_a_room_the_boiler_helps_calls_from_two_tenths_below_until_its_target():
    """In the simulation of 2026-10-20 to 30 the rooms waited for the call at 0.3 below: 249 comfort minutes missed."""
    state, mem = heat_call.update(rr(temp=20.8), CallMemory(), NOW, None, helping=True)
    assert state == "calling"
    state, mem = heat_call.update(rr(temp=20.95), mem, NOW + 60, None, helping=True)
    assert state == "calling"  # within the tenth that ends a call otherwise
    state, mem = heat_call.update(rr(temp=21.0), mem, NOW + 120, None, helping=True)
    assert state == "idle" and not mem.calling
    state, _ = heat_call.update(rr(temp=21.0), CallMemory(), NOW + 180, None, helping=True)
    assert state == "idle"  # at its target a room does not begin to call either
    state, _ = step(rr(temp=20.8), CallMemory())  # without the help: not yet, as before
    assert state == "idle"


def test_a_heat_pump_room_the_boiler_helps_is_judged_stalled_only_after_two_hours():
    """Replayed 2026-10-30, 8 degrees colder: the bath, helped for an hour, was judged stalled 0.13 below its target,
    while the unit stayed at its least speed for it. Neither source added the peak. Never judged stalled, a room that
    hangs below its target (a sensor placed apart from the valve) had the boiler fire for one radiator all comfort
    long, the pattern of 2026-09 (review 2026-10-30)."""
    old = CallMemory(calling=True, since_ts=NOW - 2 * HOUR + 60, target=21.0)
    state, mem = heat_call.update(rr(temp=20.9, trend=0.05), old, NOW, None, helping=True)
    assert state == "calling" and mem.stalled_target is None
    state, mem = heat_call.update(rr(temp=20.9, trend=0.05), old, NOW + 60, None, helping=True)
    assert (state, mem.stalled_target) == ("stalled", 21.0)
    state, mem = heat_call.update(rr(temp=20.8, trend=0.05), mem, NOW + 120, None, helping=True)
    assert state == "stalled"  # the help it was judged under does not end it
    state, mem = heat_call.update(rr(temp=20.7, trend=0.05), mem, NOW + 180, None, helping=True)
    assert state == "calling"  # three tenths below, where a room the boiler does not help calls
    state, _ = heat_call.update(rr(temp=20.9, trend=0.2), old, NOW + 60, None, helping=True)
    assert state == "calling"  # still rising
    state, _ = heat_call.update(rr(temp=20.7, trend=0.05), old, NOW + 60, None, helping=True)
    assert state == "calling"  # three tenths below is no stall while helped
    state, mem = heat_call.update(rr(temp=20.75, trend=0.05), CallMemory(stalled_target=21.0), NOW, None, helping=True)
    assert state == "calling" and mem.stalled_target is None  # a stall from before the help ends with it


def test_a_helped_room_flickering_at_its_target_does_not_call_again_until_two_tenths_below():
    """Review 2026-10-30: a helped call from any shortfall switched the boiler with every 20.9/21.0 reading."""
    state, mem = heat_call.update(rr(temp=20.9), CallMemory(), NOW, None, helping=True)
    assert state == "idle"
    state, mem = heat_call.update(rr(temp=20.8), mem, NOW + 60, None, helping=True)
    assert state == "calling"
    state, mem = heat_call.update(rr(temp=20.95), mem, NOW + 120, None, helping=True)
    assert state == "calling"  # within the tenth that ends a call otherwise
    state, mem = heat_call.update(rr(temp=21.0), mem, NOW + 180, None, helping=True)
    assert state == "idle"
    state, mem = heat_call.update(rr(temp=20.9), mem, NOW + 240, None, helping=True)
    assert state == "idle"


def test_a_helped_room_calls_the_boiler_in_the_last_half_hour_of_comfort_too():
    """Replayed 2026-10-30, five degrees colder: with the unit held at its least speed for it, the bath fell to 0.5 K
    below its target from 07:01 to 07:29 while no call could go out. Let through, the comfort minutes missed fell
    from 71 to 53 in ten days at the same cost."""
    state, _ = heat_call.update(rr(temp=20.7), CallMemory(), NOW, NOW + 25 * 60, helping=True)
    assert state == "calling"
    state, _ = heat_call.update(rr(temp=20.7), CallMemory(), NOW, NOW + 25 * 60, helping=False)
    assert state == "comfort_ending"
    state, _ = heat_call.update(rr(temp=20.7), CallMemory(), NOW, NOW - 60, helping=True)
    assert state == "comfort_ending"  # its end just passed, the schedule helper not switched yet
