"""Which rooms call the boiler, and which heat along: measured cases from 2026-09-24 to 26."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.thriftherm.engines import advisor, heat_call
from custom_components.thriftherm.engines.heat_call import CallMemory
from custom_components.thriftherm.engines.room import comfort_end, parse_schedule
from custom_components.thriftherm.models import RoomResult, ScheduleState

from .conftest import room_config, room_state, snapshot

NOW = 2_000_000.0
HOUR = 3600.0


def rr(key="badezimmer", temp=20.0, target=21.0, reason="schedule_comfort", trend=None, allowed=True, demand=None, preheat=None):
    return RoomResult(
        key=key, target=target, target_reason=reason, temperature=temp,
        deviation_k=None if temp is None else round(temp - target, 2),
        demand=demand if demand is not None else (0.0 if temp is None else max(0.0, (target - temp) / 2.0)),
        trend_k_per_h=trend, dew_point_c=None, abs_humidity_g_m3=None, window_open=not allowed, window_unknown=False,
        heating_allowed=allowed, internal_gain_w=None, preheat_start_ts=preheat,
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


@pytest.mark.parametrize("stored", [None, 3, "x", {"calling": "yes", "since_ts": "a", "stalled_target": True}])
def test_stored_call_memory_that_is_not_valid_is_empty(stored):
    assert CallMemory.from_storage(stored) == CallMemory()


def test_call_memory_round_trip():
    mem = CallMemory(calling=True, since_ts=NOW, stalled_target=None, target=21.5)
    assert CallMemory.from_storage(mem.to_storage()) == mem


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
    assert _advise(rooms, None).source == "boiler"  # without calls: the old threshold


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
