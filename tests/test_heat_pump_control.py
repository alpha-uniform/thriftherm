"""Phase 7: Midea controller decisions (pure engine)."""

from dataclasses import replace
from itertools import product

import pytest

from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
from custom_components.thriftherm.engines.heat_pump_control import (
    ControlInputs,
    ControlMemory,
    compressor_heats,
    decide,
    heats_at_minimum,
    strongest_fan,
    why_above_minimum,
)
from custom_components.thriftherm.models import DryingState, HeatPumpCommand, HeatPumpSample, RoomResult, SettingChange

from .conftest import PARAMS, heat_pump_state, room_result

NOW = 1_000_000.0


def rr(key="badezimmer", temp=19.0, target=21.0, trend=None, heat_pump_allowed=True, drying=False, boost=False) -> RoomResult:
    return room_result(
        key, target, temp, trend_k_per_h=trend, heating_allowed=heat_pump_allowed, heat_pump_allowed=heat_pump_allowed,
        drying=DryingState(True, NOW - 300, 9.0) if drying else DryingState(), boost_active=boost,
    )


def off_heat_pump(**kw):
    kw.setdefault("hvac_mode", "off")
    kw.setdefault("compressor_hz", 0.0)
    kw.setdefault("running_s", None)
    return heat_pump_state(**kw)


def inp(rooms, *, mode="shadow", op="auto", heat_pump=None, advice="midea", blocked=None, safety="ok", outdoor=5.0, cop=3.5,
        bin_count=10, now=NOW, params=PARAMS, cheaper=False, defrosting=False, boiler=True, lift=True, floor=False):
    heat_pump = heat_pump or off_heat_pump()
    return ControlInputs(
        now_ts=now,
        control_mode=mode,
        op_mode=op,
        heat_pump=heat_pump,
        run_state=heat_pump_engine.run_state(heat_pump, params),
        rooms=tuple(rooms),
        advice_source=advice,
        blocked_reason=blocked,
        safety_state=safety,
        outdoor_c=outdoor,
        expected_cop=cop,
        break_even_cop=2.89,
        outdoor_bin=None if outdoor is None else int(outdoor // 2),
        bin_count=bin_count,
        params=params,
        cheaper=cheaper,
        defrosting=defrosting,
        boiler_can_help=boiler,
        may_lift=lift,
        help_at_floor=floor,
    )


RUNNING = ControlMemory(
    running_since_ts=NOW - 300, phase_since_ts=NOW - 300, last_command_ts=NOW - 300, last_hvac_mode="heat", last_target=21.0
)
PAST_MIN_RUN = replace(RUNNING, running_since_ts=NOW - 1300, phase_since_ts=NOW - 1300)
LONG_RUN = replace(RUNNING, running_since_ts=NOW - 7200, phase_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
# A unit that cannot rest below its own reading: it reads the floor of 17 °C already, so warm rooms stop it
COLD_OFFICE = replace(heat_pump_state(), indoor_temp=17.0)
AT_21 = replace(heat_pump_state(), target_temp=21.0)  # as RUNNING last set it


def test_control_off_is_disabled():
    cmd, _ = decide(inp([rr()], mode="off"), ControlMemory())
    assert cmd.plan == "disabled" and cmd.action == "none"


@pytest.mark.parametrize("hvac", ["fan_only", "cool", "dry", "auto"])
def test_hands_off_while_user_runs_the_heat_pump(hvac):
    cmd, mem = decide(inp([rr()], heat_pump=off_heat_pump(hvac_mode=hvac)), RUNNING)
    assert cmd.plan == "hands_off" and cmd.action == "none"
    assert cmd.reason == f"manual_mode_{hvac}"
    assert mem.running_since_ts is None


def test_starts_in_eco_when_cheaper_and_room_cold():
    cmd, mem = decide(inp([rr(temp=19.0)]), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.load) == ("heat", "start", "eco")
    assert cmd.target_temp == 20.5  # off for long: the intake sensor's 20 °C + half a degree
    assert cmd.fan_mode == "auto" and cmd.reason == "cheaper_than_gas"
    assert mem.running_since_ts == NOW and mem.last_target == 20.5


@pytest.mark.parametrize(
    ("room", "kw", "expected"),
    [
        (rr(temp=21.2), {}, ("off", "none", "no_demand")),  # above its target a room is not short: no start just to rest
        (rr(temp=18.0, heat_pump_allowed=False), {}, ("off", "none", "no_demand")),  # window open: the room is ignored
        (rr(temp=21.5), {"advice": "none", "cop": 4.8}, ("off", "none", "no_demand")),
        (rr(temp=None), {}, ("off", "none", "no_demand")),  # no room data: not started
        (rr(), {"advice": "boiler", "cop": 2.5, "bin_count": 10}, ("off", "none", "gas_cheaper")),
        (rr(), {"advice": "boiler", "cop": 2.0, "bin_count": 0}, ("off", "none", "gas_cheaper")),  # no learning run far below
        (rr(), {"outdoor": -9.5}, ("heat", "start", "cheaper_than_gas")),  # the outdoor limit is -10 °C by default
        # a radiator thermostat switched off by hand: the room never calls, and the heat pump could heat it alone
        (rr(), {"mode": "active", "advice": "none", "cheaper": True}, ("heat", "start", "cheaper_than_gas")),
        (rr(), {"mode": "active", "advice": "none"}, ("off", "none", "gas_cheaper")),  # neither cheaper nor recommended
    ],
)
def test_a_unit_that_is_off_starts_only_for_a_short_room_and_cheaper_heat(room, kw, expected):
    cmd, _ = decide(inp([room], **kw), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.reason) == expected


@pytest.mark.parametrize(
    ("mem", "unit", "temp", "kw", "expected"),
    [
        (ControlMemory(), off_heat_pump(), 19.0, {"safety": "fallback", "blocked": "safety_fallback"},
         ("none", None, ("safety_fallback",))),  # the safety fallback is named once
        (ControlMemory(), off_heat_pump(), 19.0, {"op": "off"}, ("none", None, ("mode_off",))),
        (ControlMemory(), off_heat_pump(), 19.0, {"op": "boiler_only"}, ("none", None, ("mode_boiler_only",))),
        (ControlMemory(), off_heat_pump(), 19.0, {"blocked": "icing_lockout_active"}, ("none", None, ("icing_lockout_active",))),
        (ControlMemory(), replace(off_heat_pump(), outdoor_temp=None), 19.0, {"outdoor": None},
         ("none", None, ("outdoor_temperature_unknown",))),
        (ControlMemory(), off_heat_pump(error_code=5), 19.0, {}, ("none", None, ("error_code_5",))),
        # a blocker stops a running unit at once, within its minimum run, and without room data
        (RUNNING, AT_21, 19.0, {"outdoor": -12.0}, ("stop", "fan_only", ("outdoor_below_-10C",))),
        (LONG_RUN, heat_pump_state(), None, {"outdoor": -12.0}, ("stop", "fan_only", ("outdoor_below_-10C",))),
        (RUNNING, AT_21, 19.0, {"safety": "fallback"}, ("stop", "fan_only", ("safety_fallback",))),
    ],
)
def test_neither_a_recommendation_nor_cheaper_heat_gets_past_what_blocks_the_unit(mem, unit, temp, kw, expected):
    for mode, advice, cheaper in product(("shadow", "active"), ("midea", "none"), (False, True)):
        cmd, new = decide(inp([rr(temp=temp)], mode=mode, heat_pump=unit, advice=advice, cheaper=cheaper, **kw), mem)
        assert (cmd.plan, cmd.reason, (cmd.action, cmd.hvac_mode, cmd.blockers)) == ("off", "blocked", expected), mode
        if cmd.action == "stop":  # the compressor stops, the fan runs on
            assert new.running_since_ts is None and new.stopped_since_ts == NOW


def test_learning_run_in_unknown_bin_once_per_day():
    cmd, mem = decide(inp([rr()], mode="active", advice="boiler", cop=2.6, bin_count=0), ControlMemory())
    assert cmd.action == "start" and cmd.load == "learning" and cmd.reason == "learning_run"
    assert dict(mem.learning_bins)[2] == NOW
    # stop after learning, try again 2 h later in the same bin: no new learning run
    stopped = replace(mem, running_since_ts=None, stopped_since_ts=NOW + 3600, learning_until_ts=None, last_hvac_mode="off")
    cmd2, _ = decide(inp([rr()], mode="active", advice="boiler", cop=2.6, bin_count=1, now=NOW + 7200), stopped)
    assert cmd2.plan == "off" and cmd2.reason == "gas_cheaper"


def test_a_stop_after_the_minimum_run_waits_out_the_fan_run_on_and_the_minimum_off_time():
    # within the minimum run nothing stops: see test_a_run_that_should_end_respects_the_minimum_run
    cmd, mem = decide(inp([rr(temp=21.5)], heat_pump=COLD_OFFICE), PAST_MIN_RUN)  # too cold where it stands to rest by setpoint
    assert cmd.action == "stop" and cmd.reason == "target_reached"
    cmd, mem = decide(inp([rr(temp=19.0)], now=NOW + 300), mem)
    assert cmd.plan == "off" and cmd.waiting == "fan_run_on"
    cmd, mem = decide(inp([rr(temp=19.0)], now=NOW + 900), mem)
    assert (cmd.action, cmd.hvac_mode) == ("stop", "off")
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 960), mem)
    assert cmd.action == "start"  # the minimum off time counts from the end of the heating


def test_without_a_fan_run_on_the_unit_is_switched_off_at_once_and_waits_its_minimum_off_time():
    at_once = replace(PARAMS, heat_pump_fan_run_on_s=0.0)
    cmd, mem = decide(inp([rr(temp=21.5)], heat_pump=COLD_OFFICE, params=at_once), PAST_MIN_RUN)
    assert (cmd.action, cmd.hvac_mode) == ("stop", "off") and mem.fan_until_ts is None
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 300, params=at_once), mem)
    assert cmd.plan == "off" and cmd.waiting == "min_off_time"
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 700, params=at_once), mem)
    assert cmd.action == "start"


def test_the_fan_runs_on_for_a_quarter_of_an_hour_before_the_unit_is_switched_off():
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=COLD_OFFICE), LONG_RUN)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "fan_only")
    assert mem.fan_until_ts == NOW + 900 and mem.last_hvac_mode == "fan_only" and mem.running_since_ts is None
    fan = heat_pump_state(hvac_mode="fan_only", compressor_hz=0.0, running_s=None)
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 600), mem)
    assert (cmd.plan, cmd.action, cmd.waiting) == ("off", "none", "fan_run_on")  # ours, not a hand on the remote
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 900), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "off")
    assert mem.fan_until_ts is None and mem.last_hvac_mode == "off"


def test_a_mode_set_by_hand_during_the_fan_run_on_ends_it():
    _, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=COLD_OFFICE), LONG_RUN)
    assert mem.fan_until_ts == NOW + 900
    cooling = heat_pump_state(hvac_mode="cool")
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=cooling, now=NOW + 600), mem)
    assert cmd.plan == "hands_off" and mem.fan_until_ts is None


def test_quick_heat_up_runs_full_power_even_if_gas_cheaper():
    cmd, _ = decide(inp([rr(temp=19.0, boost=True)], advice="boiler", cop=2.5), ControlMemory())
    assert (cmd.action, cmd.load, cmd.target_temp, cmd.fan_mode) == ("start", "boost", 30.0, "high")
    assert cmd.reason == "quick_heat_up"


def test_active_mode_detects_manual_takeover():
    mem = replace(RUNNING, last_command_ts=NOW - 600)
    user_changed = off_heat_pump(hvac_mode="heat", compressor_hz=40.0)
    user_changed = replace(user_changed, target_temp=25.0)
    cmd, new = decide(inp([rr()], mode="active", heat_pump=user_changed), mem)
    assert cmd.plan == "hands_off" and cmd.reason == "manual_takeover"
    assert new.manual_until_ts == pytest.approx(NOW + 2 * 3600) and new.manual_by == "device"  # as long as a room override
    assert (cmd.manual_until_ts, cmd.manual_by) == (new.manual_until_ts, "device")
    cmd2, _ = decide(inp([rr()], mode="active", heat_pump=user_changed, now=NOW + 60), new)
    assert cmd2.plan == "hands_off"


def test_shadow_mode_ignores_state_mismatch():
    # in shadow mode nothing is sent, so the real Midea state never matches the plan
    mem = replace(RUNNING, last_command_ts=NOW - 600)
    cmd, _ = decide(inp([rr()], mode="shadow"), mem)
    assert cmd.plan == "heat"


@pytest.mark.parametrize(("advice", "cop"), [("midea", 3.5), ("boiler", 2.5)])
def test_drying_keeps_heating_past_the_target_even_if_gas_is_cheaper(advice, cop):
    """Warm air carries the moisture out, so drying does not stop at the setpoint."""
    cmd, _ = decide(inp([rr(temp=22.0, target=22.0, drying=True)], advice=advice, cop=cop), ControlMemory())
    assert (cmd.action, cmd.reason, cmd.load) == ("start", "bathroom_drying", "eco")


def test_drying_stops_once_the_room_is_well_past_the_target():
    """Without a ceiling the bathroom would be driven up for the full hour."""
    room = rr(temp=23.6, target=22.0, drying=True)  # 1.6 K over, past the 1.5 K ceiling
    cmd, _ = decide(inp([room], advice="boiler"), ControlMemory())
    assert cmd.action != "start"
    assert cmd.reason != "bathroom_drying"


def test_fresh_run_state_keeps_only_what_was_learned():
    mem = replace(RUNNING, learning_bins=((2, NOW),), manual_until_ts=NOW + 100, resting=True, phase_since_ts=NOW)
    fresh = mem.fresh_run_state()
    assert fresh.learning_bins == ((2, NOW),)
    assert fresh.running_since_ts is None and fresh.last_hvac_mode is None and fresh.manual_until_ts is None
    assert not fresh.resting and fresh.phase_since_ts is None


@pytest.mark.parametrize(
    ("advice", "cop", "run_s", "expected"),
    [
        # satisfied within the minimum run: keep heating at the last setpoint (the fan is not ours to set)
        ("midea", 3.5, 300, ("heat", "none", "heat", "target_reached", 21.0, None, "eco", "min_run_time")),
        # economics turned within the minimum run: finish it in eco
        ("boiler", 2.5, 300, ("heat", "none", "heat", "gas_cheaper", 21.0, None, "eco", "min_run_time")),
        # and after it: the rooms warm, the unit rests at the lowest setpoint; gas cheaper, it stops
        ("midea", 3.5, 1300, ("heat", "adjust", "heat", "rooms_warm_compressor_rests", 17.0, None, "eco", None)),
        ("boiler", 2.5, 1300, ("off", "stop", "fan_only", "gas_cheaper", None, None, None, None)),
    ],
)
def test_a_run_that_should_end_respects_the_minimum_run(advice, cop, run_s, expected):
    mem = replace(RUNNING, running_since_ts=NOW - run_s, phase_since_ts=NOW - run_s, last_target=21.0)
    cmd, new = decide(inp([rr(temp=21.5)], advice=advice, cop=cop, bin_count=10), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason, cmd.target_temp, cmd.fan_mode, cmd.load, cmd.waiting) == expected
    assert (new is mem) == (cmd.action == "none")


# ---- a unit found heating is taken over -------------------------------------------------------------
def resting(**kw) -> RoomResult:
    return replace(rr(**kw), target_reason="mode_away")


# seen 2026-10-13: after each of three restarts an "adjust" to exactly what the unit had: a unit a degree below
# its own reading (21 °C) already is not sent the setpoint it shows, and its memory reads as if sent now
@pytest.mark.parametrize(("shown", "action"), [(22.0, "adjust"), (20.0, "none")])
def test_active_control_takes_over_a_unit_found_heating(shown, action):
    found = replace(heat_pump_state(), target_temp=shown)
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=found), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", action, 20.0, None)
    assert (mem.running_since_ts, mem.last_target, mem.last_command_ts) == (NOW, 20.0, NOW)


def test_a_change_by_hand_after_a_takeover_counts():
    at_20 = replace(heat_pump_state(), target_temp=20.0)
    _, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=at_20), ControlMemory())
    turned_up = replace(heat_pump_state(), target_temp=25.0, setting_change=SettingChange(NOW + 300, "person"))
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=turned_up, now=NOW + 360), mem)
    assert (cmd.plan, cmd.reason, mem.manual_by) == ("hands_off", "manual_takeover", "person")


def test_a_unit_taken_over_with_nothing_to_do_is_stopped_after_the_minimum_run():
    rooms = [resting(temp=20.0, target=17.0)]
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=heat_pump_state()), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.waiting) == ("heat", "none", "min_run_time")
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=heat_pump_state(), now=NOW + 1300), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "fan_only")


def test_planning_only_never_takes_a_running_unit_over():
    _, mem = decide(inp([resting(temp=20.0, target=17.0)], heat_pump=heat_pump_state()), ControlMemory())
    assert mem.running_since_ts is None


# ---- a restart of Home Assistant changes nothing for a running unit ----------------------

def test_a_running_unit_is_not_stopped_while_the_room_sensors_have_not_reported():
    # seen 2026-10-11: after a restart the room temperatures were unknown, "no room is cold" was read
    # from that, and the unit was sent to its fan run-on
    restarted = replace(heat_pump_state(), settings_watched_since_ts=NOW - 60)
    cmd, mem = decide(inp([rr(temp=None)], mode="active", heat_pump=restarted), LONG_RUN)
    assert (cmd.plan, cmd.action, cmd.waiting) == ("heat", "none", "room_data")
    assert mem == LONG_RUN


def test_a_restart_counts_although_the_watch_on_the_unit_was_carried_across_it():
    """Seen 2026-10-13: the bathroom had no temperature yet, the other room's setback became the setpoint, 21 -> 18.5 °C."""
    night = replace(rr(key="schlafzimmer", temp=20.0, target=18.5), target_reason="schedule_setback")
    rooms = [rr(key="badezimmer", temp=None), night]
    watched_for_hours = replace(heat_pump_state(), settings_watched_since_ts=NOW - 7200)
    restarted = replace(inp(rooms, mode="active", heat_pump=watched_for_hours, cheaper=True), started_ts=NOW - 5)
    cmd, mem = decide(restarted, LONG_RUN)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.waiting) == ("heat", "none", 22.0, "room_data")
    assert mem == LONG_RUN
    cmd, _ = decide(replace(restarted, started_ts=NOW - 660), LONG_RUN)  # a sensor that stays silent is not waited for
    assert cmd.waiting != "room_data"


def test_a_room_sensor_that_stays_silent_does_not_keep_the_unit_on_for_good():
    long_run = replace(LONG_RUN, last_target=21.0)
    rooms = [rr(temp=23.0), rr(key="flur", temp=None)]  # one room 2 K above its target, one that never reports
    watched = replace(heat_pump_state(), target_temp=21.0, settings_watched_since_ts=NOW - 540)
    cmd, _ = decide(inp(rooms, mode="active", advice="boiler", heat_pump=watched), long_run)
    assert cmd.waiting == "room_data"  # nine minutes after the restart: still waiting
    watched = replace(watched, settings_watched_since_ts=NOW - 660)
    cmd, _ = decide(inp(rooms, mode="active", advice="boiler", heat_pump=watched), long_run)
    assert (cmd.action, cmd.reason) == ("stop", "gas_cheaper")


# ---- drying a room: the strongest fan, and afterwards the stage the unit had
MIDEA_FANS = ("silent", "low", "medium", "high", "full", "auto")
AT_23 = replace(RUNNING, last_target=23.0)  # held a degree below its own reading of 24 °C


def _unit(fan: str | None, fans=MIDEA_FANS, target: float = 23.0):
    return replace(heat_pump_state(), target_temp=target, indoor_temp=24.0, fan_mode=fan, fan_modes=fans)  # as last commanded


def _drying(fan: str | None, mem: ControlMemory, *, drying=True, now=NOW, fans=MIDEA_FANS, temp: float | None = None):
    """A bathroom at 24 °C, past the ceiling for the heat of a drying at 22 °C: the fan still matters.

    Afterwards at 22.2 °C, between its target and half a degree above: nothing to heat up, nothing to rest for.
    """
    room = rr(temp=temp if temp is not None else (24.0 if drying else 22.2), target=22.0, drying=drying)
    return decide(inp([room], mode="active", heat_pump=_unit(fan, fans), cheaper=True, now=now), mem)


def test_the_strongest_fan_is_named_as_the_unit_names_it():
    assert strongest_fan(MIDEA_FANS) == "full"
    assert strongest_fan(("auto", "low", "high")) == "high"
    assert strongest_fan(("Auto", "Turbo", "High")) == "Turbo"
    assert strongest_fan(("1", "2", "3")) is None and strongest_fan(()) is None


def test_a_drying_turns_the_fan_up_at_once_and_back_afterwards():
    cmd, mem = _drying("auto", AT_23)
    assert (cmd.action, cmd.fan_mode, cmd.target_temp) == ("adjust", "full", 23.0)  # 300 s after the last command
    assert mem.fan_turned_up_from == "auto"
    cmd, mem = _drying("full", mem, now=NOW + 60)
    assert (cmd.action, cmd.fan_mode) == ("none", "full")

    cmd, mem = _drying("full", mem, drying=False, now=NOW + 1800)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "auto")
    cmd, mem = _drying("auto", mem, drying=False, now=NOW + 1860)
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("none", "auto", None)


@pytest.mark.parametrize(
    ("before", "fans", "turned_up", "shown_after", "afterwards"),
    [
        # the stage somebody had chosen comes back
        ("silent", MIDEA_FANS, ("adjust", "full", "silent", "full"), "full", ("adjust", "silent", "silent", "full")),
        # a stage chosen during the drying is left alone: turned down by remote meanwhile ...
        ("auto", MIDEA_FANS, ("adjust", "full", "auto", "full"), "medium", ("none", None, None, None)),
        # ... or "high", which was taken for the quick heat-up's stage, and "auto" was sent at its end
        ("auto", MIDEA_FANS, ("adjust", "full", "auto", "full"), "high", ("none", None, None, None)),
        ("full", MIDEA_FANS, ("none", "full", "full", "full"), "full", ("none", "full", None, None)),  # at the top already
        ("2", ("1", "2", "3"), ("none", None, None, None), "2", ("none", None, None, None)),  # no known top stage: kept
    ],
)
def test_after_a_drying_the_fan_gets_back_the_stage_it_had_unless_one_was_chosen_meanwhile(
    before, fans, turned_up, shown_after, afterwards
):
    cmd, mem = _drying(before, AT_23, fans=fans)
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == turned_up
    cmd, mem = _drying(shown_after, mem, drying=False, now=NOW + 1800, fans=fans)
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == afterwards
    cmd, _ = _drying(cmd.fan_mode or shown_after, mem, drying=False, now=NOW + 1860, fans=fans)
    assert cmd.action == "none"  # and nothing more is sent


def test_a_unit_that_does_not_turn_its_fan_is_asked_again_at_the_pace_of_an_adjustment():
    _, mem = _drying("auto", AT_23)
    cmd, mem = _drying("auto", mem, now=NOW + 60)
    assert cmd.action == "none"  # just asked
    cmd, mem = _drying("auto", mem, now=NOW + 700)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "full")
    cmd, mem = _drying("full", mem, drying=False, now=NOW + 760)  # the drying ends right after
    assert cmd.action == "none"  # the turn back waits its ten minutes as well
    cmd, mem = _drying("full", mem, drying=False, now=NOW + 1400)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "auto")


def test_a_unit_started_for_a_drying_starts_with_the_strongest_fan():
    room = rr(temp=21.0, target=22.0, drying=True)
    unit = replace(off_heat_pump(), fan_mode="auto", fan_modes=MIDEA_FANS)
    cmd, mem = decide(inp([room], mode="active", heat_pump=unit), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("start", "full", "auto")


def test_a_quick_heat_up_during_a_drying_does_not_turn_the_fan_down():
    room = rr(temp=20.0, target=22.0, drying=True, boost=True)
    turned_up = replace(AT_23, fan_turned_up_from="auto", fan_turned_up_to="full")  # by the drying
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full"), cheaper=True), turned_up)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 30.0, "full")  # not down to the quick heat-up's "high"
    assert (mem.fan_turned_up_from, mem.fan_turned_up_to) == ("auto", "full")  # nothing is put back in the middle of it


def test_a_drying_sends_the_setpoint_due_and_the_fan_together_however_recent_the_last_command():
    # a setpoint went out five minutes ago, and the hold is set once more (the first cycle after the update)
    at_22 = replace(RUNNING, last_target=22.0, last_command_ts=NOW - 300, phase_since_ts=None)
    unit = replace(heat_pump_state(), target_temp=22.0, fan_mode="auto", fan_modes=MIDEA_FANS)
    room = rr(temp=21.0, target=22.0, drying=True)
    cmd, mem = decide(inp([room], mode="active", heat_pump=unit, cheaper=True), at_22)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 20.0, "full")  # its own reading 21 °C - 1
    assert (mem.last_target, mem.fan_turned_up_from) == (20.0, "auto")


def test_after_a_quick_heat_up_during_a_drying_the_fan_gets_back_what_it_had_before_both():
    unit = replace(off_heat_pump(), fan_mode="auto", fan_modes=MIDEA_FANS)
    both = rr(temp=20.0, target=22.0, drying=True, boost=True)
    cmd, mem = decide(inp([both], mode="active", heat_pump=unit, cheaper=True), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("start", "high", "auto")
    at_30 = replace(_unit("high"), target_temp=30.0)
    dried = inp([rr(temp=21.0, target=22.0, drying=True)], mode="active", heat_pump=at_30, cheaper=True, now=NOW + 1200)
    cmd, mem = decide(dried, mem)  # the quick heat-up is over, the drying goes on: held at its least speed
    assert (cmd.action, cmd.target_temp, cmd.fan_mode, mem.fan_turned_up_from) == ("adjust", 23.0, "full", "auto")
    cmd, mem = _drying("full", mem, drying=False, temp=21.0, now=NOW + 3600)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "auto")  # not the "high" of the quick heat-up


# ---- the fan stage in normal operation (option, 2026-10-30)
def staged(stage: str):
    return replace(PARAMS, heat_pump_fan_stage=stage)


@pytest.mark.parametrize(
    ("stage", "fans", "started_with"),
    [
        ("auto", MIDEA_FANS, "auto"),
        ("full", MIDEA_FANS, "full"),
        ("medium", MIDEA_FANS, "medium"),
        ("full", ("Auto", "Low", "High"), "High"),  # its top stage, as it spells it
        ("medium", ("Auto", "Low", "High"), "Auto"),  # a stage it does not offer: auto
    ],
)
def test_a_start_sets_the_fan_stage_chosen_for_normal_operation(stage, fans, started_with):
    unit = replace(off_heat_pump(), fan_mode="Auto", fan_modes=fans)
    cmd, mem = decide(inp([rr(temp=20.0)], mode="active", heat_pump=unit, params=staged(stage)), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_normal) == ("start", started_with, started_with)


def test_after_a_drying_the_fan_gets_back_the_stage_chosen_for_normal_operation():
    params = staged("medium")
    turned_up = replace(
        AT_23, last_command_ts=NOW - 1200, fan_normal="medium", fan_turned_up_from="medium", fan_turned_up_to="full"
    )
    room = rr(temp=22.2, target=22.0)
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full"), cheaper=True, params=params), turned_up)
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("adjust", "medium", "medium")
    # turned up from the stage chosen before: it gets the one chosen now
    earlier = replace(turned_up, fan_normal="auto", fan_turned_up_from="auto")
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full"), cheaper=True, params=params), earlier)
    assert (cmd.action, cmd.fan_mode, mem.fan_normal) == ("adjust", "medium", "medium")


@pytest.mark.parametrize(
    ("given", "shown", "stage", "sent"),
    [
        (None, "auto", "full", "full"),  # chosen in the options while it runs: from what every start set before
        ("auto", "auto", "full", "full"),
        ("full", "full", "full", None),  # on the chosen stage already
        ("auto", "silent", "full", None),  # chosen by hand: it stays
        (None, "Auto", "medium", None),  # a stage the unit does not offer: auto, which it has
    ],
)
def test_a_running_unit_gets_the_fan_stage_chosen_for_normal_operation_once_unless_one_was_chosen_by_hand(
    given, shown, stage, sent
):
    fans = ("Auto", "Low", "High") if shown == "Auto" else MIDEA_FANS
    mem, at = replace(AT_23, fan_normal=given, last_command_ts=NOW - 1200), NOW
    for _ in range(3):
        cmd, mem = decide(
            inp([rr(temp=22.2, target=22.0)], mode="active", heat_pump=_unit(shown, fans), params=staged(stage), now=at),
            mem,
        )
        assert (cmd.action, cmd.fan_mode) == (("adjust", sent) if sent else ("none", None))
        sent, at = None, at + 700  # asked once: a unit that did not follow is not asked again


def _fan_steps(mem: ControlMemory, shown: str, steps) -> tuple[list, ControlMemory, str]:
    """Cycles ten minutes apart, each (stage chosen, drying or not); the unit follows every fan stage sent."""
    sent, at = [], NOW
    for stage, drying in steps:
        room = rr(temp=22.2, target=22.0, drying=drying)
        cmd, mem = decide(inp([room], mode="active", heat_pump=_unit(shown), params=staged(stage), cheaper=True, now=at), mem)
        if cmd.action == "adjust" and cmd.fan_mode:
            sent.append(cmd.fan_mode)
            shown = cmd.fan_mode
        at += 700
    return sent, mem, shown


def test_a_drying_that_turns_the_fan_up_to_the_stage_chosen_since_leaves_it_there_and_knows_it_as_given():
    """Review 2026-10-30: the turn up to "full" was taken for the normal stage given, "auto" was put back after the
    drying, and the unit stayed on "auto" with "full" chosen; chosen "auto" again, nothing was sent."""
    mem = replace(AT_23, fan_normal="auto", last_command_ts=NOW - 1200)
    sent, mem, shown = _fan_steps(mem, "auto", [("full", True), ("full", False), ("full", False)])
    assert (sent, shown, mem.fan_normal) == (["full"], "full", "full")
    sent, mem, shown = _fan_steps(mem, shown, [("auto", False), ("auto", False)])
    assert (sent, shown, mem.fan_normal) == (["auto"], "auto", "auto")


def test_a_fan_found_on_the_stage_chosen_after_a_drying_counts_as_given():
    """Review 2026-10-30: chosen "full" during a drying, the fan was on it already when the drying ended and nothing was
    sent; "auto" stayed on record, and with "auto" chosen again the unit was left on "full"."""
    mem = replace(AT_23, fan_normal="auto", fan_turned_up_from="auto", fan_turned_up_to="full", last_command_ts=NOW - 1200)
    sent, mem, shown = _fan_steps(mem, "full", [("full", False), ("full", False)])
    assert (sent, shown, mem.fan_normal) == ([], "full", "full")
    sent, mem, shown = _fan_steps(mem, shown, [("auto", False), ("auto", False)])
    assert (sent, shown, mem.fan_normal) == (["auto"], "auto", "auto")


# ---- a quick heat-up: the fan goes up at once and comes back, whether the setpoint moves or not
def test_a_fan_stage_chosen_by_hand_stays_when_the_setpoint_changes():
    """Seen 2026-10-13: "full" chosen by hand stayed for 45 minutes, until a setpoint of ours took "auto" along."""
    full = replace(heat_pump_state(), fan_mode="full", fan_modes=MIDEA_FANS, settings_watched_since_ts=NOW - 86400)
    night = [replace(rr(temp=20.0, target=17.0), target_reason="schedule_setback")]
    cmd, mem = decide(inp(night, mode="active", heat_pump=full, cheaper=True), LONG_RUN)  # rooms warm: a rest
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "adjust", 17.0, None)
    assert mem.manual_until_ts is None and mem.fan_turned_up_from is None  # no change by hand, nothing to put back
    # turned down by hand while a drying had it turned up: left as it is, also with a setpoint going out
    medium, dried = replace(full, fan_mode="medium"), replace(LONG_RUN, fan_turned_up_from="auto")
    cmd, mem = decide(inp(night, mode="active", heat_pump=medium, cheaper=True), dried)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode, mem.fan_turned_up_from) == ("adjust", 17.0, None, None)


def test_a_unit_that_shows_no_fan_stage_is_asked_once_for_the_turn_up_and_once_for_the_turn_back():
    actions = []
    mem = LONG_RUN
    for minute, boost in ((0, True), (1, True), (30, True), (31, False), (32, False), (60, False)):
        rooms = [rr(temp=20.0 if boost else 20.8, boost=boost)]
        at, shown = NOW + minute * 60, replace(heat_pump_state(), target_temp=mem.last_target)  # it follows, fan unknown
        cmd, mem = decide(inp(rooms, mode="active", heat_pump=shown, cheaper=True, now=at), mem)
        actions.append((cmd.action, cmd.fan_mode))
    assert actions[:3] == [("adjust", "high"), ("none", "high"), ("none", "high")]
    assert actions[3:] == [("adjust", "auto"), ("none", None), ("none", None)]  # asked once: the fan is the unit's again


def test_what_the_fan_is_to_get_back_ends_with_a_stop():
    room = rr(temp=24.0, target=22.0, heat_pump_allowed=False)  # window open: the unit is stopped
    long_running = replace(
        AT_23, running_since_ts=NOW - 7200, phase_since_ts=NOW - 7200, fan_turned_up_from="auto", fan_turned_up_to="full"
    )
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full")), long_running)
    assert cmd.action == "stop" and (mem.fan_turned_up_from, mem.fan_turned_up_to) == (None, None)


# ---- a stage chosen by hand is not overridden, and stages go out as the unit spells them (review 2026-10-14)
BY_HAND = replace(heat_pump_state(), target_temp=22.0, fan_modes=MIDEA_FANS, settings_watched_since_ts=NOW - 86400)


def _following(rooms, fan: str, mem: ControlMemory, now=NOW):
    """The unit shows the setpoint last sent, and the fan stage given."""
    unit = replace(BY_HAND, fan_mode=fan, target_temp=mem.last_target)
    return decide(inp(rooms, mode="active", heat_pump=unit, cheaper=True, now=now), mem)


def test_a_quick_heat_up_does_not_turn_a_stronger_stage_chosen_by_hand_down():
    """"full" chosen by hand went down to "high" at once."""
    cmd, mem = _following([rr(temp=20.0, boost=True)], "full", replace(LONG_RUN, last_command_ts=NOW - 60))
    assert (cmd.target_temp, cmd.fan_mode, cmd.load) == (30.0, None, "boost")
    assert (mem.fan_turned_up_from, mem.fan_turned_up_to) == (None, None)  # nothing to put back afterwards
    cmd, mem = _following([rr(temp=20.8)], "full", mem, now=NOW + 1800)  # the quick heat-up is over
    assert (cmd.target_temp, cmd.fan_mode) == (20.0, None)


def test_a_stage_chosen_during_a_quick_heat_up_is_not_put_back():
    """"full" chosen during a quick heat-up was taken for its own stage, and "auto" was sent at its end."""
    boost, over = [rr(temp=20.0, boost=True)], [rr(temp=20.8)]
    cmd, turned_up = _following(boost, "auto", LONG_RUN)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "high")
    assert (turned_up.fan_turned_up_from, turned_up.fan_turned_up_to) == ("auto", "high")
    cmd, mem = _following(over, "full", turned_up, now=NOW + 1800)  # chosen by hand, and the quick heat-up ends
    assert (cmd.target_temp, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == (20.0, None, None, None)
    cmd, mem = _following(boost, "full", turned_up, now=NOW + 900)  # chosen while it still runs: not sent back to "high"
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("none", None, None)
    cmd, mem = _following(over, "full", mem, now=NOW + 1800)
    assert (cmd.target_temp, cmd.fan_mode) == (20.0, None)


def test_a_unit_that_spells_its_stages_with_capitals_gets_them_as_it_spells_them():
    """It never got "auto" back after a drying that began with a start: a stage it does not offer is not sent."""
    fans = ("Auto", "Low", "Medium", "High", "Turbo")
    off = replace(off_heat_pump(), fan_mode="Auto", fan_modes=fans)
    cmd, mem = decide(inp([rr(temp=21.0, target=22.0, drying=True)], mode="active", heat_pump=off), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == ("start", "Turbo", "Auto", "Turbo")
    turbo = replace(heat_pump_state(), target_temp=mem.last_target, fan_mode="Turbo", fan_modes=fans)
    dried = inp([rr(temp=22.5, target=22.0)], mode="active", heat_pump=turbo, cheaper=True, now=NOW + 3600)
    cmd, mem = decide(dried, mem)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "Auto")
    cmd, _ = decide(inp([rr(temp=19.0)], mode="active", heat_pump=off), ControlMemory())
    assert cmd.fan_mode == "Auto"  # a start sets the fan in the unit's spelling
    cmd, _ = decide(inp([rr(temp=19.0, boost=True)], mode="active", heat_pump=off), ControlMemory())
    assert cmd.fan_mode == "High"  # and so does a quick heat-up


def test_a_unit_without_an_auto_stage_gets_back_the_stage_it_had_before_a_drying_that_began_with_a_start():
    """It was to get "auto" back, a stage it does not offer: nothing was sent, and it stayed on its loudest stage."""
    fans = ("silent", "low", "medium", "high", "full")
    off = replace(off_heat_pump(), fan_mode="low", fan_modes=fans)
    cmd, mem = decide(inp([rr(temp=21.0, target=22.0, drying=True)], mode="active", heat_pump=off), ControlMemory())
    assert (cmd.action, cmd.fan_mode) == ("start", "full")
    full = replace(heat_pump_state(), target_temp=mem.last_target, fan_mode="full", fan_modes=fans)
    dried = inp([rr(temp=22.5, target=22.0)], mode="active", heat_pump=full, cheaper=True, now=NOW + 3600)
    cmd, _ = decide(dried, mem)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "low")


# ---- a run of ours stays ours across an interruption ------------------------------------------
HALF_HOUR_RUN = ControlMemory(running_since_ts=NOW - 1800, last_command_ts=NOW - 1800, last_hvac_mode="heat", last_target=21.0)
AS_SENT = replace(heat_pump_state(), target_temp=21.0, settings_watched_since_ts=NOW - 86400)


def test_a_unit_that_comes_back_heating_is_taken_over_again():
    # one cycle out of reach, then heating at our setpoint as before: nothing stopped it any more
    gone = replace(AS_SENT, available=False, hvac_mode=None)
    cmd, mem = decide(inp([rr(temp=20.9)], mode="active", heat_pump=gone), HALF_HOUR_RUN)
    assert cmd.reason == "midea_unavailable" and mem.running_since_ts is None
    cmd, mem = decide(inp([rr(temp=20.9)], mode="active", heat_pump=AS_SENT, now=NOW + 60), mem)
    assert cmd.plan == "heat" and mem.running_since_ts == NOW + 60
    cmd, mem = decide(inp([rr(temp=22.5)], mode="active", heat_pump=AS_SENT, now=NOW + 120, op="off"), mem)
    assert (cmd.action, cmd.blockers) == ("stop", ("mode_off",))


def test_a_unit_not_loaded_yet_right_after_a_start_keeps_its_run():
    """Seen 2026-10-13: after a restart its entity was not loaded yet, and the next cycle took the running unit over anew."""
    gone = replace(AS_SENT, available=False, hvac_mode=None)
    starting = replace(inp([rr(temp=20.9)], mode="active", heat_pump=gone), started_ts=NOW - 10)
    cmd, mem = decide(starting, HALF_HOUR_RUN)
    assert (cmd.plan, cmd.action, cmd.reason) == ("hands_off", "none", "midea_unavailable") and mem == HALF_HOUR_RUN
    _, mem = decide(replace(starting, started_ts=NOW - 660), HALF_HOUR_RUN)  # still away ten minutes on: the run is over
    assert mem.running_since_ts is None


def test_a_unit_back_from_a_mode_set_by_hand_is_taken_over_again():
    drying = replace(AS_SENT, hvac_mode="dry")
    _, mem = decide(inp([rr(temp=20.9)], mode="active", heat_pump=drying), HALF_HOUR_RUN)
    back = replace(AS_SENT, setting_change=SettingChange(NOW + 100, "device", setpoint=False, mode=True))
    cmd, mem = decide(inp([rr(temp=22.5)], mode="active", heat_pump=back, now=NOW + 400, blocked="outdoor_coil_iced"), mem)
    assert (cmd.action, cmd.reason) == ("stop", "blocked")


def test_a_unit_that_comes_back_with_another_setpoint_was_changed_by_hand():
    gone = replace(AS_SENT, available=False, hvac_mode=None)
    _, mem = decide(inp([rr(temp=20.9)], mode="active", heat_pump=gone), HALF_HOUR_RUN)
    turned_up = replace(AS_SENT, target_temp=24.0, setting_change=SettingChange(NOW + 30, "device"))
    cmd, mem = decide(inp([rr(temp=20.9)], mode="active", heat_pump=turned_up, now=NOW + 60), mem)
    assert (cmd.plan, cmd.reason, mem.manual_by) == ("hands_off", "manual_takeover", "device")


# ---- the fan run-on survives a switch of the control mode and a restart ---------------------------
def test_a_fan_run_on_is_finished_after_the_control_was_switched_to_planning_and_back():
    _, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=COLD_OFFICE), LONG_RUN)
    fan = off_heat_pump(hvac_mode="fan_only")
    mem = mem.fresh_run_state()  # what a switch of the control mode does
    assert mem.fan_until_ts == NOW + 900 and mem.stopped_since_ts == NOW and mem.last_hvac_mode is None
    cmd, mem = decide(inp([rr(temp=21.5)], mode="shadow", heat_pump=fan, now=NOW + 1800), mem)
    assert (cmd.action, cmd.waiting, mem.fan_until_ts) == ("none", "fan_run_on", NOW + 900)  # planning sends nothing: still owed
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 3600), mem.fresh_run_state())
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "off")


def test_a_fan_run_on_carried_over_leaves_a_unit_alone_that_is_not_in_it():
    carried = ControlMemory(fan_until_ts=NOW + 600, stopped_since_ts=NOW - 300)
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=off_heat_pump()), carried)
    assert cmd.action == "none" and mem.fan_until_ts is None and mem.last_hvac_mode is None
    resting_room = [resting(temp=20.0, target=17.0)]
    cmd, mem = decide(inp(resting_room, mode="active", heat_pump=heat_pump_state()), carried)
    assert mem.fan_until_ts is None and mem.running_since_ts == NOW  # found heating: taken over, as without a run-on


def test_the_off_after_the_fan_run_on_is_repeated_after_a_restart_right_behind_it():
    after = ControlMemory(stopped_since_ts=NOW - 900, last_command_ts=NOW, last_hvac_mode="off")
    restored = ControlMemory.from_storage(after.to_storage())
    fan = replace(off_heat_pump(hvac_mode="fan_only"), settings_watched_since_ts=NOW + 30)  # restarted half a minute later
    cmd, _ = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 3600), restored)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "off")
    long_after = replace(fan, settings_watched_since_ts=NOW + 3000)  # nobody watched for most of an hour: left alone
    cmd, _ = decide(inp([rr(temp=21.5)], mode="active", heat_pump=long_after, now=NOW + 3600), restored)
    assert (cmd.plan, cmd.reason) == ("hands_off", "manual_mode_fan_only")


# ---- a stop of a unit that does not heat, and of one without a fan-only mode ---------------------
def test_a_unit_that_is_off_already_gets_no_fan_run_on():
    # taken over without a command of ours, then switched off at the unit: its fan started and ran 15 minutes
    taken_over = ControlMemory(running_since_ts=NOW - 1300, last_hvac_mode="heat")
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=off_heat_pump()), taken_over)
    assert (cmd.plan, cmd.action, cmd.reason) == ("off", "none", "target_reached")
    assert (mem.last_hvac_mode, mem.fan_until_ts, mem.running_since_ts, mem.stopped_since_ts) == ("off", None, None, NOW)


def test_a_unit_without_a_fan_only_mode_is_switched_off_at_once():
    no_fan_only = replace(COLD_OFFICE, hvac_modes=("off", "heat", "cool"))
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=no_fan_only), LONG_RUN)
    assert (cmd.action, cmd.hvac_mode, mem.fan_until_ts, mem.last_hvac_mode) == ("stop", "off", None, "off")
    with_fan_only = replace(COLD_OFFICE, hvac_modes=("off", "heat", "fan_only"))
    cmd, _ = decide(inp([rr(temp=21.5)], mode="active", heat_pump=with_fan_only), LONG_RUN)
    assert (cmd.action, cmd.hvac_mode) == ("stop", "fan_only")


# ---- a unit that takes whole degrees ---------------------------------------------------------------
def test_a_unit_with_whole_degree_setpoints_is_sent_whole_degrees():
    whole = replace(off_heat_pump(intake=(20.8, 45.0)), target_temp_step=1.0)
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=whole), ControlMemory())
    assert cmd.target_temp == 21.0  # the intake sensor's 20.8 °C + half a degree: in half degrees it would be 21.5 °C
    whole = replace(whole, intake_temp=replace(whole.intake_temp, value=21.0))
    cmd, mem = decide(inp([rr(temp=20.0, target=20.5)], heat_pump=whole, cheaper=True), ControlMemory())
    assert cmd.target_temp == 22.0 and mem.last_target == 22.0  # 21.5 °C: halves go up, as the unit rounds them


def test_a_setpoint_the_unit_rounded_to_its_step_is_not_a_command_it_ignored():
    sent_before = replace(LONG_RUN, last_target=21.5)
    shows_22 = replace(heat_pump_state(), target_temp=22.0, target_temp_step=1.0, settings_watched_since_ts=NOW - 86400)
    rooms = [rr(temp=20.0, target=20.5)]
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=shows_22, cheaper=True), sent_before)
    assert mem.not_carried_out == 0 and cmd.reason != "not_carried_out"
    half_degrees = replace(shows_22, target_temp_step=0.5)  # a unit that could show 21.5 °C did not follow
    _, mem = decide(inp(rooms, mode="active", heat_pump=half_degrees, cheaper=True), sent_before)
    assert mem.not_carried_out == 1


# ---- learning runs ---------------------------------------------------------------------------------
def test_a_learning_run_ends_after_its_hour_although_the_weather_moved_into_the_next_bin():
    def at(outdoor: float, mem: ControlMemory, **kw):
        return decide(inp([rr(temp=19.0)], mode="active", advice="boiler", outdoor=outdoor, cop=2.6, bin_count=0, **kw), mem)

    cmd, mem = at(4.2, ControlMemory())
    assert cmd.load == "learning" and mem.learning_until_ts == NOW + 3600
    unit = replace(heat_pump_state(), target_temp=cmd.target_temp)
    cmd, mem = at(4.1, mem, heat_pump=unit, now=NOW + 1800)
    assert (cmd.plan, cmd.load) == ("heat", "learning")
    cmd, mem = at(3.9, mem, heat_pump=unit, now=NOW + 3660)
    assert (cmd.action, cmd.reason) == ("stop", "gas_cheaper")  # bin +2..+4 was never run in, and still no new run


def test_a_planned_learning_run_has_its_hour_as_well():
    cmd, mem = decide(inp([rr()], mode="shadow", advice="boiler", cop=2.6, bin_count=0), ControlMemory())
    assert (cmd.load, mem.learning_until_ts, mem.learning_bins) == ("learning", NOW + 3600, ())  # planned: the bin is not used up
    cmd, _ = decide(inp([rr()], mode="shadow", advice="boiler", cop=2.6, bin_count=0, now=NOW + 1800), mem)
    assert (cmd.plan, cmd.load) == ("heat", "learning")


# ---- switched by the rooms (not following them): cheaper heat is worth running, called for or not
def test_a_unit_switched_by_the_rooms_heats_on_once_the_room_no_longer_calls_the_boiler():
    """The recommendation names a source only while a room calls; the run ended there as "gas cheaper"."""
    almost = rr(temp=20.95, target=21.0)  # no call any more, and not yet half a degree above
    cmd, _ = decide(inp([almost], mode="active", heat_pump=heat_pump_state(), advice="none", cheaper=True), LONG_RUN)
    assert (cmd.plan, cmd.reason) == ("heat", "cheaper_than_gas") and cmd.action != "stop"
    reached = rr(temp=21.5, target=21.0)
    cmd, _ = decide(inp([reached], mode="active", heat_pump=heat_pump_state(), advice="none", cheaper=True), LONG_RUN)
    assert cmd.plan == "heat" and cmd.reason == "rooms_warm_compressor_rests"  # resting, not stopped (2026-10-29)
    cmd, _ = decide(inp([reached], mode="active", heat_pump=COLD_OFFICE, advice="none", cheaper=True), LONG_RUN)
    assert (cmd.action, cmd.reason) == ("stop", "target_reached")


# ---- hold at minimum, the rooms decide the rest (2026-10-30) -------------------------------------------
# Seen the night of 2026-10-29/30: set a degree below its own reading, the PortaSplit held its least speed
# (24 Hz, about 340 W) all night, and the rooms drifted slowly instead of rising. Following the rooms (+1 above
# its reading) its own controller climbed to 600-690 W, and the setpoint chasing its reading cost 20 beeps.
LATCHED = ControlMemory(
    running_since_ts=NOW - 3600, phase_since_ts=NOW - 3600, last_command_ts=NOW - 3600, last_hvac_mode="heat", last_target=20.0
)
AT_20 = replace(heat_pump_state(), target_temp=20.0, settings_watched_since_ts=NOW - 86400)  # its own reading 21 °C, runs
SELF_STOPPED = replace(AT_20, indoor_temp=20.5)  # its own reading falls 1.5 degrees within minutes of a stop
RESTING_AT_17 = replace(LATCHED, resting=True, phase_since_ts=NOW - 3600, last_target=17.0)
AT_17 = replace(AT_20, target_temp=17.0, compressor_hz=0.0, compressor_running_s=None, indoor_temp=20.0)


def stood_for(unit, seconds: float, now: float = NOW):
    """The unit as it shows `seconds` after its compressor stopped."""
    def sample(ts, hz):
        return HeatPumpSample(ts=ts, compressor_hz=hz, power_w=None, outdoor_coil_temp=None, indoor_coil_temp=None,
                              intake_temp=None, outlet_temp=None)
    history = (sample(now - seconds - 60, 24.0), sample(now - seconds, 0.0), sample(now, 0.0))
    return replace(unit, compressor_hz=0.0, compressor_running_s=None, history=history)


def held(rooms, mem, *, unit=AT_20, now=NOW, **kw):
    return decide(inp(rooms, mode="active", heat_pump=unit, now=now, **kw), mem)


def test_a_running_unit_is_set_a_degree_below_its_own_reading():
    first = replace(LATCHED, phase_since_ts=None, last_target=22.0)  # the first cycle after the update
    cmd, mem = held([rr(temp=20.8)], first, unit=replace(AT_20, target_temp=22.0))
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.reason) == ("heat", "adjust", 20.0, "holding_at_minimum")
    assert mem.phase_since_ts == LATCHED.running_since_ts and not mem.resting
    cmd, _ = held([rr(temp=20.8)], first, unit=replace(AT_20, target_temp=22.0, indoor_temp=21.5))
    assert cmd.target_temp == 20.5


def test_a_standing_unit_is_started_half_a_degree_above_its_own_reading():
    """Started at a degree and more above it, the PortaSplit boosted to 60-79 Hz within three minutes."""
    stopped = ControlMemory(stopped_since_ts=NOW - 1200, last_command_ts=NOW - 1200, last_hvac_mode="off")
    cmd, mem = decide(inp([rr(temp=21.0)], mode="active", heat_pump=off_heat_pump()), stopped)
    assert (cmd.plan, cmd.action, cmd.target_temp) == ("heat", "start", 21.5)  # its own reading 21 °C
    assert (mem.phase_since_ts, mem.resting, mem.last_target) == (NOW, False, 21.5)


def test_a_unit_off_for_long_is_started_from_the_intake_sensor():
    """Off for long its own reading stands still, 1.9 degrees above the intake sensor (seen 2026-10-28)."""
    frozen = off_heat_pump(intake=(18.5, 45.0))  # its own reading 21 °C
    cmd, _ = decide(inp([rr(temp=20.0)], heat_pump=frozen), ControlMemory())
    assert (cmd.action, cmd.target_temp) == ("start", 19.0)
    cmd, _ = decide(inp([rr(temp=20.0)], heat_pump=off_heat_pump(intake=None)), ControlMemory())
    assert cmd.target_temp == 21.5  # no intake sensor: its own reading after all
    # stopped by us half an hour ago: its own reading has stood still since
    for stopped_s_ago, start_at in ((1900, 19.0), (1700, 21.5)):
        stopped = ControlMemory(stopped_since_ts=NOW - stopped_s_ago, last_command_ts=NOW - stopped_s_ago, last_hvac_mode="off")
        cmd, _ = decide(inp([rr(temp=20.0)], heat_pump=frozen), stopped)
        assert (cmd.action, cmd.target_temp) == ("start", start_at), stopped_s_ago


def test_a_rising_own_reading_never_raises_the_setpoint():
    """The night of 2026-10-29/30: the setpoint chased the unit's own reading, 19.5 and 20 by turns, 20 beeps."""
    for own in (21.5, 22.0, 22.5, 23.0):
        cmd, _ = held([rr(temp=20.8)], LATCHED, unit=replace(AT_20, indoor_temp=own))
        assert (cmd.plan, cmd.action, cmd.target_temp) == ("heat", "none", 20.0), own


def test_a_target_change_sends_nothing_while_running():
    for target in (20.5, 21.0, 21.2):  # setback, comfort, and the coldest room a little short
        cmd, _ = held([rr(temp=20.6, target=target)], LATCHED)
        assert (cmd.action, cmd.target_temp) == ("none", 20.0), target


def test_rooms_half_a_degree_warm_rest_at_the_floor_in_heat_mode():
    """In heat mode it restarts softly; from off or fan only every start boosted to 60-79 Hz."""
    cmd, mem = held([rr(temp=21.5), rr(key="schlafzimmer", temp=19.0, target=18.5)], LATCHED)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.target_temp) == ("heat", "adjust", "heat", 17.0)
    assert cmd.reason == "rooms_warm_compressor_rests"
    assert (mem.resting, mem.phase_since_ts, mem.last_target) == (True, NOW, 17.0)
    cmd, _ = held([rr(temp=21.5), rr(key="schlafzimmer", temp=18.9, target=18.5)], LATCHED)
    assert cmd.action == "none"  # the coldest room decides


def test_the_coldest_room_at_its_target_resumes_after_the_minimum_off_time():
    just = replace(RESTING_AT_17, phase_since_ts=NOW - 300)
    cmd, mem = held([rr(temp=21.0)], just, unit=AT_17)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.waiting) == ("heat", "none", 17.0, "min_off_time")
    cmd, mem = held([rr(temp=21.0), rr(key="schlafzimmer", temp=21.6)], RESTING_AT_17, unit=AT_17)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 20.5, "resumed_at_minimum")  # its reading 20 °C
    assert (mem.resting, mem.phase_since_ts) == (False, NOW)


def test_between_target_and_half_a_degree_above_nothing_is_sent():
    for temp in (21.1, 21.3, 21.45):
        cmd, mem = held([rr(temp=temp)], LATCHED)
        assert (cmd.action, mem.resting) == ("none", False), temp
        cmd, mem = held([rr(temp=temp)], RESTING_AT_17, unit=AT_17)
        assert (cmd.action, mem.resting, cmd.reason) == ("none", True, "rooms_warm_compressor_rests"), temp


def test_rest_waits_for_the_minimum_run():
    cmd, mem = held([rr(temp=21.6)], replace(LATCHED, phase_since_ts=NOW - 600))
    assert (cmd.action, cmd.waiting, mem.resting) == ("none", "min_run_time", False)
    cmd, mem = held([rr(temp=21.6)], replace(LATCHED, phase_since_ts=NOW - 1200))
    assert (cmd.action, cmd.target_temp, mem.resting) == ("adjust", 17.0, True)


def test_a_cold_office_rests_by_mode_not_by_setpoint():
    """Its own reading below 19 °C: at the floor of 17 °C it would not stop, its hysteresis is about 2 degrees."""
    cold = replace(AT_20, indoor_temp=18.5, target_temp=17.5)
    cmd, mem = held([rr(temp=21.6)], replace(LATCHED, last_target=17.5), unit=cold)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason) == ("off", "stop", "fan_only", "target_reached")
    assert not mem.resting and mem.running_since_ts is None


def test_a_floor_that_does_not_stop_the_compressor_stops_the_unit_by_mode():
    runs_on = replace(AT_17, compressor_hz=24.0, compressor_running_s=900.0)
    cmd, _ = held([rr(temp=21.3)], replace(RESTING_AT_17, phase_since_ts=NOW - 600), unit=runs_on)
    assert cmd.action == "none"  # not yet
    cmd, mem = held([rr(temp=21.3)], replace(RESTING_AT_17, phase_since_ts=NOW - 900), unit=runs_on)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason) == ("off", "stop", "fan_only", "rest_not_reached")
    cmd, _ = held([rr(temp=21.3)], replace(RESTING_AT_17, phase_since_ts=NOW - 900), unit=runs_on, defrosting=True)
    assert cmd.action == "none"  # a defrost is no evidence


def test_a_unit_that_stopped_itself_while_a_room_is_short_is_relatched():
    """Its own reading rose with the office (a PC of up to 460 W) to 1.75 above its setpoint, and it stopped."""
    cmd, _ = held([rr(temp=20.9)], LATCHED, unit=stood_for(SELF_STOPPED, 200))
    assert cmd.action == "none"  # it may be a pause of a few minutes only
    cmd, mem = held([rr(temp=20.9)], LATCHED, unit=stood_for(SELF_STOPPED, 400))
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 21.0, "relatched_unit_stopped")


def test_a_unit_that_stopped_itself_while_the_rooms_are_warm_enough_is_left_alone():
    cmd, mem = held([rr(temp=21.2)], LATCHED, unit=stood_for(SELF_STOPPED, 1200))
    assert (cmd.action, cmd.target_temp, mem.resting) == ("none", 20.0, False)  # its own thermostat rests it, silently


def test_a_unit_that_stops_itself_right_after_each_setpoint_keeps_its_setpoint_a_degree_below_its_reading():
    """No aim is learned from short stops (2026-10-30): on the PortaSplit they come from the office warming, and
    half a degree higher every start would boost to 60-79 Hz."""
    recent = replace(LATCHED, last_command_ts=NOW - 1200)
    cmd, mem = held([rr(temp=20.9)], recent, unit=stood_for(SELF_STOPPED, 400))
    assert (cmd.action, cmd.target_temp) == ("adjust", 21.0)
    again = replace(SELF_STOPPED, target_temp=21.0, indoor_temp=21.0)
    cmd, mem = held([rr(temp=20.9)], mem, unit=stood_for(again, 400, now=NOW + 1200), now=NOW + 1200)
    assert (cmd.action, cmd.target_temp) == ("adjust", 21.5)  # its standing reading 21 + half a degree, as before
    stopped = ControlMemory(stopped_since_ts=NOW - 1200, last_command_ts=NOW - 1200, last_hvac_mode="off")
    cmd, _ = decide(inp([rr(temp=21.0)], mode="active", heat_pump=off_heat_pump()), stopped)
    assert (cmd.action, cmd.target_temp) == ("start", 21.5)  # never more than half a degree above, or it boosts


def test_a_falling_own_reading_relatches_down_after_ten_minutes_above_the_cap():
    """The office cools (a door open): set a degree above its reading, its own controller climbs to 48 Hz and more."""
    cooled = replace(AT_20, indoor_temp=19.0)
    cmd, mem = held([rr(temp=20.8)], LATCHED, unit=cooled)
    assert (cmd.action, mem.above_cap_since_ts) == ("none", NOW)
    cmd, mem = held([rr(temp=20.8)], mem, unit=cooled, now=NOW + 540)
    assert cmd.action == "none"
    cmd, mem = held([rr(temp=20.8)], mem, unit=cooled, now=NOW + 600)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 18.0, "relatched_reading_fell")
    assert mem.above_cap_since_ts is None
    cmd, mem = held([rr(temp=20.8)], LATCHED, unit=replace(AT_20, indoor_temp=19.5))
    assert mem.above_cap_since_ts is None  # half a degree above it is still the least speed


def test_no_relatch_during_a_defrost_and_a_quarter_hour_after():
    """A defrost stops the compressor and its own reading falls to 15.5 °C (seen 2026-10-29)."""
    defrost = stood_for(replace(AT_20, indoor_temp=15.5), 400)
    cmd, mem = held([rr(temp=20.8)], LATCHED, unit=defrost, defrosting=True)
    assert (cmd.action, mem.defrost_until_ts) == ("none", NOW + 900)
    after = stood_for(replace(AT_20, indoor_temp=18.5), 400, now=NOW + 600)
    cmd, mem = held([rr(temp=20.8)], mem, unit=after, now=NOW + 600)
    assert cmd.action == "none"
    later = stood_for(replace(AT_20, indoor_temp=18.5), 400, now=NOW + 960)
    cmd, _ = held([rr(temp=20.8)], mem, unit=later, now=NOW + 960)
    assert (cmd.action, cmd.reason) == ("adjust", "relatched_unit_stopped")


def test_a_lagging_room_lets_the_boiler_add_the_peak_and_the_heat_pump_stays_at_minimum():
    """Above its least speed the extra heat costs more than the boiler's (COP 2.7 at 800 W, break-even 2.89)."""
    cmd, mem = held([rr(temp=20.8, trend=0.1)], LATCHED)
    assert (cmd.action, cmd.delivers, mem.lagging_since_ts) == ("none", True, NOW)
    cmd, mem = held([rr(temp=20.8, trend=0.1)], mem, now=NOW + 600)
    assert (cmd.action, cmd.target_temp, cmd.delivers, cmd.reason) == ("none", 20.0, False, "boiler_adds_peak")
    assert mem.boiler_helps
    _, mem = held([rr(temp=20.85, trend=0.1)], LATCHED)
    assert mem.lagging_since_ts is None  # less than 0.2 degrees short
    _, mem = held([rr(temp=20.0, trend=None)], LATCHED)
    assert mem.lagging_since_ts is None  # an unknown trend is no evidence


def preheating_room(temp: float, pace: float | None, began_s_ago: float = 1800.0, for_in_s: float = 3600.0, trend=None):
    room = replace(rr(temp=temp, trend=trend), preheat_start_ts=NOW - 60, preheat_for_ts=NOW + for_in_s)
    return replace(room, preheat_began_ts=NOW - began_s_ago, preheat_pace_k_per_h=pace)


def test_a_preheat_projected_short_of_its_comfort_start_lets_the_boiler_help():
    cmd, mem = held([preheating_room(20.0, 0.5)], LATCHED)  # 20.5 °C by the comfort start, 21 °C wanted
    assert (cmd.action, cmd.delivers, mem.boiler_helps) == ("none", False, True)
    cmd, _ = held([preheating_room(20.0, 1.0)], LATCHED)  # gets there
    assert cmd.delivers
    # within a tenth of its target by then is close enough, more than that is behind
    cmd, _ = held([preheating_room(20.0, 0.85)], LATCHED)  # 0.15 short
    assert not cmd.delivers
    cmd, _ = held([preheating_room(20.0, 0.95)], LATCHED)  # 0.05 short
    assert cmd.delivers


def test_a_preheat_is_judged_by_its_pace_since_it_began_not_by_the_trend_of_the_last_ninety_minutes():
    """Review 2026-10-30: half an hour in, that trend still held an hour of the flat time before the preheat."""
    cmd, mem = held([preheating_room(20.2, 0.5, for_in_s=6600, trend=0.12)], LATCHED)
    assert cmd.delivers and not mem.boiler_helps


@pytest.mark.parametrize(
    ("pace", "began_s_ago", "lagging"),
    [
        (None, 600.0, False),  # ten minutes in: no pace yet, and the trend still holds the flat time before
        (0.1, 1200.0, False),  # judged half an hour after it began, as for "behind"
        (0.6, 1800.0, False),  # rises fast since it began, however flat the last ninety minutes were
        (0.1, 1800.0, True),  # barely rises since it began
        (None, 7200.0, False),  # an unknown pace is no evidence
    ],
)
def test_a_preheating_room_lags_by_its_pace_since_it_began_and_not_before_half_an_hour(pace, began_s_ago, lagging):
    """Review 2026-10-30: its 90-minute trend still held the flat setback time, so every preheat lagged after ten
    minutes and the boiler helped (19 to 24 of about 60 helps began within half an hour of a raised target)."""
    room = preheating_room(19.5, pace, began_s_ago=began_s_ago, for_in_s=4 * 3600, trend=0.12)
    _, mem = held([room], LATCHED)
    assert (mem.lagging_since_ts is not None) is lagging


def test_a_preheat_without_a_known_pace_is_not_judged_behind():
    """After a restart the time it began is restored, its history is not: an unknown pace is no evidence."""
    cmd, mem = held([preheating_room(20.0, None, began_s_ago=7200, for_in_s=10800)], LATCHED)
    assert cmd.delivers and not mem.boiler_helps
    cmd, mem = held([preheating_room(20.0, None, began_s_ago=7200, for_in_s=10800)], LATCHED, boiler=False)
    assert (cmd.action, mem.lifted) == ("none", False)


def test_a_preheat_is_judged_half_an_hour_after_it_began_though_its_planned_start_keeps_moving_up():
    """Review 2026-10-30: the planned start is recomputed from the temperature every cycle and moves towards now
    while the room climbs. Judged from it, a room rising 0.4 K/h was judged after 127 minutes instead of 30."""
    cmd, mem = held([preheating_room(20.0, 0.25)], LATCHED)  # 20.25 °C by the comfort start, 21 °C wanted
    assert (cmd.delivers, mem.boiler_helps) == (False, True)
    cmd, _ = held([preheating_room(20.0, 0.25, began_s_ago=1200)], LATCHED)
    assert cmd.delivers


def test_the_boiler_stops_helping_once_every_room_is_at_its_target():
    helped = replace(LATCHED, boiler_helps=True, lagging_since_ts=NOW - 1200)
    cmd, mem = held([rr(temp=20.95, trend=0.5)], helped)
    assert (cmd.delivers, mem.boiler_helps) == (False, True)
    cmd, mem = held([rr(temp=21.0, trend=0.5)], helped)
    assert (cmd.delivers, mem.boiler_helps, mem.lagging_since_ts) == (True, False, None)


def test_the_boiler_helps_only_rooms_whose_radiator_may_heat():
    """A room with its radiator thermostat switched off by hand takes no boiler heat: it kept the help on, and the heat
    pump saying the boiler takes the peak, until every room was at its target."""
    radiator_off = replace(rr(key="buero", temp=20.5, trend=0.1), heating_allowed=False)
    _, mem = held([radiator_off], LATCHED)
    cmd, mem = held([radiator_off], mem, now=NOW + 600)
    assert (cmd.delivers, mem.boiler_helps, mem.lagging_since_ts) == (True, False, None)
    late = replace(preheating_room(19.5, 0.4, began_s_ago=3600, for_in_s=7200), heating_allowed=False)
    cmd, mem = held([late], LATCHED)
    assert (cmd.delivers, mem.boiler_helps) == (True, False)
    # a helped room at its target ends the help, whatever a room the boiler cannot heat shows
    helped = replace(LATCHED, boiler_helps=True, lagging_since_ts=NOW - 1200)
    cmd, mem = held([rr(temp=21.0, trend=0.5), radiator_off], helped)
    assert (cmd.delivers, mem.boiler_helps, mem.lagging_since_ts) == (True, False, None)
    # without a boiler to help the unit itself is set higher for it, as for any room
    cmd, mem = held([radiator_off], replace(LATCHED, lagging_since_ts=NOW - 600), boiler=False)
    assert (cmd.action, cmd.reason, mem.lifted) == ("adjust", "heat_pump_only_lift", True)


def test_without_a_boiler_a_lagging_room_lifts_the_setpoint_half_a_degree_per_ten_minutes_up_to_the_lift_cap():
    mem, now, seen, shown = LATCHED, NOW, [], 20.0
    for _ in range(8):
        cmd, mem = held([rr(temp=20.5, trend=0.1)], mem, unit=replace(AT_20, target_temp=shown), now=now, boiler=False)
        seen.append((cmd.action, cmd.target_temp))
        assert cmd.delivers
        shown, now = cmd.target_temp, now + 600
    assert seen == [("none", 20.0), ("adjust", 20.5), ("adjust", 21.0), ("adjust", 21.5), ("adjust", 22.0),
                    ("adjust", 22.5), ("none", 22.5), ("none", 22.5)]  # its own reading 21 °C + 1.5 at most
    assert not mem.boiler_helps  # there is no boiler to help
    cmd, mem = held([rr(temp=20.5, trend=0.1)], replace(LATCHED, boiler_helps=True), boiler=False)  # switched to "heat pump only"
    assert cmd.delivers and not mem.boiler_helps
    cmd, mem = held([rr(temp=20.5, trend=0.1)], replace(LATCHED, lagging_since_ts=NOW - 600), boiler=False)
    assert (cmd.action, cmd.reason) == ("adjust", "heat_pump_only_lift")
    # the rooms there: the cap is half a degree again, and the setpoint comes down to the least speed
    lifted = replace(LATCHED, last_target=22.5, lagging_since_ts=None)
    cmd, mem = held([rr(temp=21.1)], lifted, unit=replace(AT_20, target_temp=22.5), boiler=False)
    cmd, mem = held([rr(temp=21.1)], mem, unit=replace(AT_20, target_temp=22.5), now=NOW + 600, boiler=False)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 20.0, "relatched_reading_fell")


def test_a_lift_that_ends_sets_the_unit_back_to_its_least_speed_at_once():
    """Review 2026-10-30: the rooms there, the setpoint stayed half a degree above its reading, within the cap, and the
    unit ran on at 52 Hz and about 730 W for two hours, every room warm."""
    lifted = replace(LATCHED, last_target=21.5, lifted=True, last_command_ts=NOW - 300)
    at_215 = replace(AT_20, target_temp=21.5)  # its own reading 21 °C
    cmd, mem = held([rr(temp=21.0)], lifted, unit=at_215, boiler=False)
    assert (cmd.action, cmd.target_temp, cmd.reason, mem.lifted) == ("adjust", 20.0, "holding_at_minimum", False)
    cmd, mem = held([rr(temp=20.9, trend=0.5)], lifted, unit=at_215, boiler=False)  # not there yet: the lift goes on
    assert (cmd.action, mem.lifted) == ("none", True)


@pytest.mark.parametrize(
    ("run", "unit"),
    [
        (LATCHED, replace(AT_20, available=False, hvac_mode=None)),  # out of reach, long after Home Assistant started
        (LATCHED, replace(AT_20, hvac_mode="fan_only")),  # somebody ventilates
        # back from out of reach, found off while gas is cheaper: nothing starts it
        (replace(LATCHED, running_since_ts=None, last_hvac_mode=None, last_command_ts=None), off_heat_pump()),
    ],
)
def test_a_run_that_is_not_ours_any_more_leaves_no_boiler_help_behind(run, unit):
    """Review 2026-10-30: a help kept through such an exit let every heat pump room call the boiler from two tenths
    below, the boiler heating them alone for the whole outage."""
    helped = replace(run, boiler_helps=True, lagging_since_ts=NOW - 1200, lifted=True)
    cmd, mem = held([rr(temp=20.5, trend=0.1)], helped, unit=unit, advice="boiler")
    assert (cmd.delivers, mem.boiler_helps, mem.lagging_since_ts, mem.lifted) == (True, False, None, False)


def test_without_a_boiler_a_preheat_that_is_behind_lifts_the_setpoint_though_it_climbs():
    """Review 2026-10-30: 19.5 °C rising 0.4 K/h, two hours before 21 °C, held at the least speed and 0.7 K short."""
    cmd, mem = held([preheating_room(19.5, 0.4, began_s_ago=3600, for_in_s=7200)], LATCHED, boiler=False)
    assert (cmd.action, cmd.target_temp, cmd.reason, mem.lifted) == ("adjust", 20.5, "heat_pump_only_lift", True)


@pytest.mark.parametrize(
    "room",
    [rr(temp=20.5, trend=0.1), preheating_room(19.5, 0.4, began_s_ago=3600, for_in_s=7200)],
    ids=["lagging", "preheat_behind"],
)
def test_warming_up_slower_keeps_the_unit_at_its_least_speed_with_neither_a_lift_nor_the_boiler(room):
    """The option "warm up slower": a lagging room or a late preheat changes nothing, the rooms just take longer."""
    mem, now = replace(LATCHED, lagging_since_ts=NOW - 1200, lifted=True), NOW
    for _ in range(4):
        cmd, mem = held([room], mem, now=now, boiler=False, lift=False)
        assert (cmd.action, cmd.target_temp, cmd.delivers) == ("none", 20.0, True)
        assert not mem.lifted and not mem.boiler_helps
        now += 600


SLOWER = {"lift": False, "floor": True}  # "warm up slower" with a boiler to help at the comfort floor


def test_warming_up_slower_lets_the_boiler_help_only_from_half_a_degree_below_a_target_until_the_room_is_there():
    """Simulated 2026-10-30: plain "slower" left the rooms 0.3-0.6 degrees below target on average, up to 1.7 in frost."""
    mem, now = LATCHED, NOW
    for _ in range(4):  # 0.4 below and flat: it lags, but takes no help, and no lift
        cmd, mem = held([rr(temp=20.6, trend=0.0)], mem, now=now, **SLOWER)
        assert (cmd.action, cmd.target_temp, cmd.delivers, mem.boiler_helps, mem.lifted) == ("none", 20.0, True, False, False)
        now += 600
    cmd, mem = held([rr(temp=20.5, trend=0.6)], LATCHED, **SLOWER)  # half a degree below: at once, however fast it climbs
    assert (cmd.action, cmd.target_temp, cmd.delivers, mem.boiler_helps) == ("none", 20.0, False, True)
    assert cmd.reason == "boiler_adds_peak"
    cmd, mem = held([rr(temp=20.8, trend=0.6)], mem, **SLOWER)  # the help holds until the room is there
    assert (cmd.delivers, mem.boiler_helps) == (False, True)
    cmd, mem = held([rr(temp=21.0, trend=0.6)], mem, **SLOWER)
    assert (cmd.delivers, mem.boiler_helps) == (True, False)
    _, mem = held([rr(temp=19.5, target=20.0)], LATCHED, **SLOWER)  # a setback target alike
    assert mem.boiler_helps


@pytest.mark.parametrize(
    ("room", "helps"),
    [
        (preheating_room(20.0, 0.4), True),  # 20.4 °C by the comfort start: 0.6 short
        (preheating_room(20.0, 0.7), False),  # 0.3 short: it only comes a little late
        (preheating_room(20.0, None, began_s_ago=600), False),  # a degree below as it begins: judged by its projection only
        (replace(rr(temp=20.0, trend=0.0), heating_allowed=False), False),  # a radiator switched off by hand takes no help
        (rr(temp=20.0, target=21.5, trend=0.0, drying=True), False),  # a dried room's target is raised, its window maybe open
    ],
    ids=["preheat_0.6_short", "preheat_0.3_short", "preheat_begins", "radiator_off", "drying"],
)
def test_warming_up_slower_lets_the_boiler_help_a_preheat_projected_half_a_degree_short_and_no_room_it_cannot_heat(room, helps):
    cmd, mem = held([room], LATCHED, **SLOWER)
    assert (cmd.action, cmd.delivers, mem.boiler_helps, mem.lifted) == ("none", not helps, helps, False)


def test_a_quick_heat_up_ends_in_a_latch_or_a_rest():
    cmd, mem = decide(inp([rr(temp=20.0, boost=True)], mode="active"), ControlMemory())
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("start", 30.0, "high")
    full_power = replace(AT_20, target_temp=30.0, fan_mode="high", fan_modes=MIDEA_FANS)
    # five minutes on, the room warm: down at once, to the least speed until the minimum run is over
    cmd, mem = held([rr(temp=21.6, boost=True)], mem, unit=full_power, now=NOW + 300)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "adjust", 20.0, "auto")
    assert cmd.reason == "holding_at_minimum"
    at_20 = replace(full_power, target_temp=20.0, fan_mode="auto")
    cmd, mem = held([rr(temp=21.6)], mem, unit=at_20, now=NOW + 1300)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 17.0, "rooms_warm_compressor_rests")
    # a quick heat-up out of a rest: full power at once, and the rooms decide again at its end
    cmd, mem = held([rr(temp=20.0, boost=True)], RESTING_AT_17, unit=AT_17)
    assert (cmd.action, cmd.target_temp, mem.resting) == ("adjust", 30.0, False)


def test_a_drying_never_rests_and_keeps_the_compressor_at_minimum():
    cmd, mem = held([rr(temp=22.8, target=22.0, drying=True)], LATCHED)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.reason, mem.resting) == ("heat", "none", 20.0, "bathroom_drying", False)
    cmd, _ = decide(inp([rr(temp=22.8, target=22.0, drying=True)], heat_pump=off_heat_pump(intake=None)), ControlMemory())
    assert (cmd.action, cmd.target_temp) == ("start", 21.5)  # its own reading 21 °C + half a degree


def test_a_unit_taken_over_at_the_old_follow_setpoint_is_latched_once():
    """Following the rooms it was at 22 °C, a degree above its own reading: after the update it is latched once."""
    follow = replace(AT_20, target_temp=22.0)
    cmd, mem = held([rr(temp=20.8)], ControlMemory(), unit=follow)
    assert (cmd.plan, cmd.action, cmd.target_temp) == ("heat", "adjust", 20.0)
    assert (mem.running_since_ts, mem.phase_since_ts) == (NOW, NOW)
    cmd, mem = held([rr(temp=20.8)], mem, unit=AT_20, now=NOW + 600)
    assert cmd.action == "none"
    warm = held([rr(temp=21.6)], replace(LATCHED, phase_since_ts=None, last_target=22.0), unit=follow)[0]
    assert (warm.action, warm.target_temp) == ("adjust", 17.0)  # run long enough: the rooms are warm, it rests


def test_a_restart_sends_nothing_to_a_unit_at_its_latched_setpoint():
    restored = ControlMemory.from_storage(LATCHED.to_storage())
    cmd, mem = held([rr(temp=20.8)], restored)
    assert (cmd.plan, cmd.action, cmd.target_temp) == ("heat", "none", 20.0)
    assert mem.phase_since_ts == LATCHED.phase_since_ts


def test_a_restart_during_a_rest_keeps_resting():
    restored = ControlMemory.from_storage(RESTING_AT_17.to_storage())
    cmd, mem = held([rr(temp=21.3)], restored, unit=AT_17)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.reason) == ("heat", "none", 17.0, "rooms_warm_compressor_rests")
    assert mem.resting


def test_gas_cheaper_after_a_long_rest_switches_off_without_a_fan_run_on():
    """Its fan has run for most of an hour already: a quarter of an hour more blows nothing warm into the rooms."""
    def gas_cheaper(stood_s: float):
        return held([rr(temp=21.3)], RESTING_AT_17, unit=stood_for(AT_17, stood_s), advice="boiler", cop=2.5)

    cmd, mem = gas_cheaper(1800)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason) == ("off", "stop", "off", "gas_cheaper")
    assert (mem.fan_until_ts, mem.resting, mem.last_hvac_mode) == (None, False, "off")
    cmd, mem = gas_cheaper(300)
    assert (cmd.action, cmd.hvac_mode, mem.fan_until_ts) == ("stop", "fan_only", NOW + 900)


def test_memory_roundtrip():
    """After a restart mid-run the control still knows it started the unit, or no blocker and no rest stops it."""
    stored = ControlMemory(
        running_since_ts=NOW - 300, stopped_since_ts=NOW - 7200, last_command_ts=NOW - 60, last_hvac_mode="heat",
        last_target=20.0, learning_until_ts=NOW + 3600, learning_bins=((2, NOW),), manual_until_ts=NOW + 10, manual_by="device",
        fan_until_ts=NOW + 900, fan_turned_up_from="silent", fan_turned_up_to="full", lagging_since_ts=NOW - 600,
        boiler_helps=True, resting=True, lifted=True, phase_since_ts=NOW - 60, fan_normal="full",
    )
    assert ControlMemory.from_storage(stored.to_storage()) == stored
    within_a_run = replace(
        stored, not_carried_out=2, stop_noted_ts=NOW, above_cap_since_ts=NOW, defrost_until_ts=NOW
    )
    assert ControlMemory.from_storage(within_a_run.to_storage()) == stored  # not stored


@pytest.mark.parametrize(
    ("data", "kept"),
    [
        ({"running_since_ts": "nonsense", "last_hvac_mode": 7}, ControlMemory()),
        ({"aim_k": -0.5, "lifted": True}, ControlMemory(lifted=True)),  # an aim stored by an earlier build is ignored
        ({"fan_turned_up_from": 3, "fan_turned_up_to": 4, "fan_normal": 5}, ControlMemory()),
        ({"offset_k": 1.5, "last_offset_review_ts": NOW, "resting": "yes"}, ControlMemory()),  # before 0.7.0
    ],
)
def test_a_corrupt_or_old_stored_memory_keeps_only_what_reads_right(data, kept):
    assert ControlMemory.from_storage(data) == kept


def test_only_a_settled_run_without_boost_or_drying_heats_at_the_least_speed():
    """What a heat-up rate of the heat pump is learned from: the speed a preheat plans with."""
    held = HeatPumpCommand("heat", "none", "heat", 20.0, "auto", "eco", "holding_at_minimum")
    settled = heat_pump_state(compressor_hz=24.0, running_s=900.0)
    assert heats_at_minimum(held, RUNNING, settled, (rr(),))
    assert heats_at_minimum(replace(held, load="learning"), RUNNING, settled, (rr(),))  # a learning run is held alike
    # the first ten minutes boost to 60-79 Hz after a start from off
    assert not heats_at_minimum(held, RUNNING, replace(settled, compressor_running_s=300.0), (rr(),))
    assert not heats_at_minimum(held, RUNNING, replace(settled, compressor_hz=0.0, compressor_running_s=None), (rr(),))
    assert not heats_at_minimum(held, replace(RUNNING, resting=True), settled, (rr(),))
    assert not heats_at_minimum(replace(held, target_temp=30.0, load="boost"), RUNNING, settled, (rr(),))
    assert not heats_at_minimum(held, RUNNING, settled, (rr(drying=True),))  # the strongest fan gives more heat
    assert not heats_at_minimum(replace(held, plan="off", action="stop"), RUNNING, settled, (rr(),))
    assert not heats_at_minimum(replace(held, plan="hands_off"), RUNNING, settled, (rr(),))


def test_a_lifted_run_does_not_heat_at_the_least_speed():
    """Review 2026-10-30: runs at 600-1000 W were learned as its least speed, 0.8-0.9 an hour instead of 0.18, the
    preheat started too late, and the lift came back."""
    held = HeatPumpCommand("heat", "none", "heat", 20.0, "auto", "eco", "holding_at_minimum")
    settled = heat_pump_state(compressor_hz=24.0, running_s=900.0)  # its own reading 21 °C
    assert not heats_at_minimum(replace(held, target_temp=22.0), replace(RUNNING, lifted=True), settled, (rr(),))
    # set above its reading by more than the cap, as on the way down from a lift: above its least speed too
    assert not heats_at_minimum(replace(held, target_temp=22.0), RUNNING, settled, (rr(),))
    assert heats_at_minimum(replace(held, target_temp=21.5), RUNNING, settled, (rr(),))  # within the cap


def test_a_quick_heat_up_or_a_drying_runs_the_unit_above_its_least_speed():
    """What keeps a COP sample out of the map: the map stands for the speed the unit is held at."""
    held = heat_pump_state(compressor_hz=24.0)
    assert why_above_minimum(held, (rr(),)) is None
    assert why_above_minimum(held, (rr(boost=True),)) == "quick_heat_up"
    assert why_above_minimum(replace(held, target_temp=30.0), (rr(),)) == "quick_heat_up"  # held at 30 to its minimum run
    assert why_above_minimum(held, (rr(drying=True),)) == "bathroom_drying"
    assert why_above_minimum(held, (rr(drying=True, heat_pump_allowed=False),)) is None  # window open: the unit does not dry it


def test_a_lifted_run_keeps_its_cop_out_of_the_map():
    """Release check 2026-10-30: a lift (600-1000 W) kept out of the heat-up rates still entered the COP map."""
    held = heat_pump_state(compressor_hz=24.0)  # its own reading 21 °C
    assert why_above_minimum(held, (rr(),), replace(RUNNING, lifted=True)) == "lift"
    # Not by the gap to its reading: after a stop the reading falls 1.5 for some minutes, and a re-latch at reading
    # + 0.5 sat a degree above it when the unit started again at its least speed (seen 2026-10-30 14:59-15:08).
    assert why_above_minimum(replace(held, target_temp=22.0), (rr(),), RUNNING) is None


# ---- review 2026-10-30 ------------------------------------------------------------------------------------
def test_a_unit_out_of_reach_for_a_moment_during_a_rest_rests_on_when_it_is_back():
    """Its entity drops out now and then (Midea AC LAN): taken over at the floor, it was resumed for no room."""
    gone = replace(AT_17, available=False, hvac_mode=None)
    for temp in (21.6, 21.3):  # warm, and between the target and half a degree above
        _, mem = held([rr(temp=temp)], RESTING_AT_17, unit=gone)
        assert mem.running_since_ts is None
        cmd, mem = held([rr(temp=temp)], mem, unit=stood_for(AT_17, 1200, now=NOW + 60), now=NOW + 60)
        assert (cmd.plan, cmd.action, cmd.target_temp, mem.resting) == ("heat", "none", 17.0, True), temp


def test_a_unit_left_at_the_floor_by_0_6_8_rests_on_after_the_update():
    """0.6.8 rested the unit at 17 °C without a rest on record: the first cycle started it for no room."""
    old = ControlMemory.from_storage(
        {"running_since_ts": NOW - 7200, "last_command_ts": NOW - 3600, "last_hvac_mode": "heat", "last_target": 17.0}
    )
    cmd, mem = held([rr(temp=21.3)], old, unit=stood_for(AT_17, 1200))
    assert (cmd.action, cmd.target_temp, mem.resting, mem.phase_since_ts) == ("none", 17.0, True, NOW)
    cmd, mem = held([rr(temp=21.0)], old, unit=stood_for(AT_17, 1200))  # a room at its target: it heats
    assert (cmd.action, cmd.target_temp, mem.resting) == ("adjust", 20.5, False)


def test_without_a_boiler_a_room_a_little_short_that_does_not_lag_keeps_the_unit_at_its_least_speed():
    """The lift is for a room that lags; one that climbs, a little short after a resume, leaves the cap at half a degree."""
    cooled = replace(AT_20, indoor_temp=19.0)  # the office cools: a degree above its reading, 48 Hz and 624 W
    climbing = [rr(temp=20.9, trend=0.5)]
    _, mem = held(climbing, LATCHED, unit=cooled, boiler=False)
    cmd, mem = held(climbing, mem, unit=cooled, now=NOW + 600, boiler=False)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 18.0, "relatched_reading_fell")


def test_without_a_boiler_a_lift_holds_while_the_room_climbs_and_a_falling_reading_keeps_it_lifted():
    mem, shown = LATCHED, 20.0
    for i in range(5):  # lagging: lifted to 22.0, a degree above its reading of 21 °C
        cmd, mem = held([rr(temp=20.5, trend=0.1)], mem, unit=replace(AT_20, target_temp=shown), now=NOW + 600 * i, boiler=False)
        shown = cmd.target_temp
    assert shown == 22.0
    for i in (5, 6, 7):  # it climbs now, still short: the lift stays
        cmd, mem = held([rr(temp=20.7, trend=0.6)], mem, unit=replace(AT_20, target_temp=shown), now=NOW + 600 * i, boiler=False)
        assert (cmd.action, cmd.target_temp) == ("none", 22.0), i
    # lifted to 22.5 and the office cools to 20.5 °C while the room still lags: set anew a degree and a half above it
    lifted = replace(LATCHED, last_target=22.5, lagging_since_ts=NOW - 300)
    cooled = replace(AT_20, target_temp=22.5, indoor_temp=20.5)
    _, mem = held([rr(temp=20.5, trend=0.1)], lifted, unit=cooled, boiler=False)
    cmd, _ = held([rr(temp=20.5, trend=0.1)], mem, unit=cooled, now=NOW + 600, boiler=False)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 22.0, "relatched_reading_fell")


def test_a_self_stop_whose_relatch_is_the_setpoint_it_has_is_left_to_its_own_thermostat_and_not_set_lower_later():
    """Its relatch came out at the setpoint it had: nothing is sent, and its reading falling on is no new stop."""
    sent = replace(LATCHED, last_target=21.0, last_command_ts=NOW - 1200)
    standing = replace(SELF_STOPPED, target_temp=21.0)  # its reading 20.5 °C: the relatch would be 21.0 again
    cmd, mem = held([rr(temp=20.9)], sent, unit=stood_for(standing, 400))
    assert (cmd.action, mem.stop_noted_ts) == ("none", NOW)
    colder = replace(standing, indoor_temp=20.0)
    cmd, mem = held([rr(temp=20.9)], mem, unit=stood_for(colder, 1000, now=NOW + 600), now=NOW + 600)
    assert (cmd.action, cmd.target_temp) == ("none", 21.0)


def test_a_self_stop_is_answered_once():
    _, mem = held([rr(temp=20.9)], LATCHED, unit=stood_for(SELF_STOPPED, 400))
    assert mem.last_target == 21.0
    for later, own in ((700, 20.0), (1400, 19.5)):
        standing = stood_for(replace(SELF_STOPPED, target_temp=21.0, indoor_temp=own), 400 + later, now=NOW + later)
        cmd, mem = held([rr(temp=20.9)], mem, unit=standing, now=NOW + later)
        assert (cmd.action, cmd.target_temp) == ("none", 21.0), later


def test_a_unit_that_shows_no_compressor_frequency_is_held_like_a_running_one():
    """A climate entity without Midea's frequency: after a quick heat-up it was left far above its reading."""
    no_hz = replace(AT_20, compressor_hz=None, compressor_running_s=None, target_temp=30.0, indoor_temp=24.0)
    boosted = replace(LATCHED, last_target=30.0, last_command_ts=NOW - 1300)
    cmd, mem = held([rr(temp=21.0)], boosted, unit=no_hz)
    assert (cmd.action, cmd.target_temp) == ("adjust", 23.0)
    cooled = replace(no_hz, target_temp=23.0, indoor_temp=21.5)  # its reading falls: set anew from it after ten minutes
    _, mem = held([rr(temp=21.0)], mem, unit=cooled, now=NOW + 600)
    cmd, _ = held([rr(temp=21.0)], mem, unit=cooled, now=NOW + 1200)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 20.5, "relatched_reading_fell")


def test_a_resume_with_the_compressor_still_running_is_set_a_degree_below_its_reading():
    still = replace(AT_17, compressor_hz=24.0, compressor_running_s=700.0)  # its own reading 20 °C, running
    cmd, mem = held([rr(temp=21.0)], RESTING_AT_17, unit=still)
    assert (cmd.action, cmd.target_temp, cmd.reason, mem.resting) == ("adjust", 19.0, "resumed_at_minimum", False)


def test_a_unit_that_stood_already_rests_by_setpoint_where_its_reading_would_be_high_enough_running():
    """Its reading fell 1.5 degrees with the stop: 18.6 °C standing is 20.1 °C running, enough to rest at 17 °C."""
    cmd, mem = held([rr(temp=21.6)], LATCHED, unit=stood_for(replace(AT_20, indoor_temp=18.6), 600))
    assert (cmd.action, cmd.target_temp, mem.resting) == ("adjust", 17.0, True)
    cmd, mem = held([rr(temp=21.6)], LATCHED, unit=stood_for(replace(AT_20, indoor_temp=17.3), 600))
    assert (cmd.action, cmd.reason, mem.resting) == ("stop", "target_reached", False)


def test_a_run_resumed_out_of_a_rest_has_its_minimum_run_before_gas_cheaper_stops_it():
    resumed = replace(RESTING_AT_17, resting=False, phase_since_ts=NOW - 120, last_target=20.5, last_command_ts=NOW - 120)
    at_20_5 = replace(AT_20, target_temp=20.5)
    cmd, _ = held([rr(temp=21.0)], resumed, unit=at_20_5, advice="boiler", cop=2.5)
    assert (cmd.plan, cmd.action, cmd.reason, cmd.waiting) == ("heat", "none", "gas_cheaper", "min_run_time")
    cmd, _ = held([rr(temp=21.0)], replace(resumed, phase_since_ts=NOW - 1200), unit=at_20_5, advice="boiler", cop=2.5)
    assert (cmd.action, cmd.reason) == ("stop", "gas_cheaper")


@pytest.mark.parametrize(
    ("room", "kw", "reason"),
    [(rr(temp=20.5), {"advice": "boiler", "cop": 2.5}, "gas_cheaper"), (resting(temp=20.5), {}, "comfort_time_over")],
)
def test_a_quick_heat_up_that_ends_inside_the_minimum_run_drops_to_the_least_speed_at_once(room, kw, reason):
    """Left at 30 °C the unit would run at 650-930 W for the rest of its minimum run."""
    boosted = replace(RUNNING, last_target=30.0)
    cmd, mem = held([room], boosted, unit=replace(AT_20, target_temp=30.0), **kw)
    assert (cmd.action, cmd.target_temp, cmd.reason, cmd.waiting) == ("adjust", 20.0, reason, "min_run_time")
    assert mem.last_target == 20.0


def test_a_hold_is_never_the_rest_setpoint_or_far_above_the_warmest_wanted_target():
    for own, target in ((17.8, 17.5), (17.3, 17.5), (28.0, 24.0)):  # a cold office, and a reading stuck high
        first = replace(LATCHED, phase_since_ts=None, last_target=22.0)
        cmd, _ = held([rr(temp=20.8)], first, unit=replace(AT_20, target_temp=22.0, indoor_temp=own))
        assert cmd.target_temp == target, own


def test_a_floor_that_holds_the_compressor_once_it_restarted_is_not_taken_for_a_failed_rest():
    restarted = replace(AT_17, compressor_hz=24.0, compressor_running_s=120.0)  # by its own thermostat, a minute ago
    cmd, mem = held([rr(temp=21.3)], RESTING_AT_17, unit=restarted)
    assert (cmd.action, cmd.reason, mem.resting) == ("none", "rooms_warm_compressor_rests", True)


def test_a_learning_run_does_not_rest():
    learning = replace(LATCHED, learning_until_ts=NOW + 1200)
    cmd, mem = held([rr(temp=21.6)], learning, advice="boiler", cop=2.6, bin_count=0)
    assert (cmd.action, cmd.target_temp, mem.resting) == ("none", 20.0, False)


def test_a_room_being_dried_or_a_planned_run_lets_no_boiler_help():
    """A dried room has a raised target and maybe an open window; a planned run heats nothing to judge."""
    dried = rr(temp=21.0, target=22.0, trend=0.1, drying=True)
    _, mem = held([dried], LATCHED)
    _, mem = held([dried], mem, now=NOW + 600)
    assert (mem.lagging_since_ts, mem.boiler_helps) == (None, False)
    planned = inp([rr(temp=20.8, trend=0.1)], mode="shadow", heat_pump=AT_20)
    _, mem = decide(planned, LATCHED)
    cmd, mem = decide(replace(planned, now_ts=NOW + 600), mem)
    assert (cmd.delivers, mem.boiler_helps) == (True, False)


def test_a_unit_without_a_compressor_frequency_heats_at_its_least_speed_once_the_phase_has_settled():
    held_cmd = HeatPumpCommand("heat", "none", "heat", 20.0, "auto", "eco", "holding_at_minimum")
    no_hz = heat_pump_state(compressor_hz=None, running_s=None)
    assert heats_at_minimum(held_cmd, replace(RUNNING, phase_since_ts=NOW - 900), no_hz, (rr(),), NOW)
    assert not heats_at_minimum(held_cmd, RUNNING, no_hz, (rr(),), NOW)  # five minutes in: a start may still boost
    assert not heats_at_minimum(held_cmd, replace(RUNNING, phase_since_ts=NOW - 900), no_hz, (rr(),))  # no time: not known


def test_a_unit_without_a_compressor_frequency_heats_no_room_while_it_rests_at_the_lowest_setpoint():
    """Review 2026-10-30: held at its least speed it is in heat mode nearly all season, and taken for heating all
    along its rooms never learned how fast they cool down."""
    no_hz = heat_pump_state(compressor_hz=None, running_s=None)
    assert compressor_heats(no_hz, LATCHED, active=True)
    assert not compressor_heats(no_hz, RESTING_AT_17, active=True)
    assert compressor_heats(no_hz, RESTING_AT_17, active=False)  # a rest only planned sets nothing: it heats as it likes
    assert not compressor_heats(AT_17, LATCHED, active=True)  # a frequency tells: 0 Hz heats nothing
    assert not compressor_heats(replace(no_hz, hvac_mode="cool"), LATCHED, active=True)
