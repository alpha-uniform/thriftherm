"""Cooling with the heat pump (docs/PHASE12_COOLING.md §4-6): run, rest, more power, run-on, hand, safety."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import hand
from custom_components.thriftherm.engines.heat_pump_control import ControlMemory
from custom_components.thriftherm.engines.heat_pump_cooling import CoolInputs, decide
from custom_components.thriftherm.models import HeatPumpSample, SettingChange

from .conftest import PARAMS, heat_pump_state, room_result

NOW = 1_000_000.0


def room(key="schlafzimmer", temp=26.0, target=25.0, allowed=True, trend=None):
    return room_result(
        key, 21.0, temp, cool_target=target, cooling_allowed=allowed and target is not None and temp is not None,
        trend_k_per_h=trend,
    )


def unit(mode="off", hz=0.0, reading=27.0, setpoint=26.5, running_s=None, **kw):
    kw.setdefault("intake", None)
    state = heat_pump_state(hvac_mode=mode, compressor_hz=hz, running_s=running_s, **kw)
    modes = ("off", "auto", "cool", "dry", "heat", "fan_only")
    return replace(state, indoor_temp=reading, target_temp=setpoint, hvac_modes=modes)


def cin(rooms, *, mode="active", heat_pump=None, now=NOW, **kw):
    return CoolInputs(now_ts=now, control_mode=mode, heat_pump=heat_pump or unit(), rooms=tuple(rooms), params=PARAMS, **kw)


# a run of ours at its least speed: compressor 30 Hz, its reading 25, held a degree above it
RUN = ControlMemory(
    running_since_ts=NOW - 3600, phase_since_ts=NOW - 3600, last_command_ts=NOW - 3600, last_hvac_mode="cool", last_target=26.0
)
RUNNING_UNIT = unit("cool", 30.0, reading=25.0, setpoint=26.0, running_s=3600.0)
REST = replace(RUN, resting=True, last_target=30.0, phase_since_ts=NOW - 3600)
RESTING_UNIT = unit("cool", 0.0, reading=26.0, setpoint=30.0)


# ---- start --------------------------------------------------------------------------------------
def test_a_warm_room_starts_cooling_half_a_degree_below_the_units_reading():
    cmd, mem = decide(cin([room(temp=26.0)]), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.target_temp) == ("cool", "start", "cool", 26.5)
    assert cmd.fan_mode == "auto" and mem.last_hvac_mode == "cool" and mem.running_since_ts == NOW


def test_the_fan_stage_of_cooling_is_its_own_option():
    cmd, _ = decide(cin([room()], fan_stage="full"), ControlMemory())
    assert cmd.fan_mode == "full"


def test_no_start_within_the_minimum_pause():
    cmd, _ = decide(cin([room()]), ControlMemory(stopped_since_ts=NOW - 60))
    assert cmd.action == "none" and cmd.waiting == "min_off_time"


def test_no_start_within_the_pause_after_a_change_of_season():
    cmd, _ = decide(cin([room()], season_since_ts=NOW - 600), ControlMemory(stopped_since_ts=NOW - 600))
    assert cmd.action == "none" and cmd.waiting == "season_switch_pause"
    # the boiler may have heated until the change, with nothing of the unit's to stop: the radiators still give off heat
    cmd, _ = decide(cin([room()], season_since_ts=NOW - 600), ControlMemory())
    assert cmd.waiting == "season_switch_pause"
    cmd, _ = decide(cin([room()], season_since_ts=NOW - 1500), ControlMemory(stopped_since_ts=NOW - 1500))
    assert cmd.action == "start"


def test_a_stop_late_in_the_cooling_season_waits_the_minimum_pause_only():
    cmd, _ = decide(cin([room()], season_since_ts=NOW - 2 * 86400), ControlMemory(stopped_since_ts=NOW - 720))
    assert cmd.action == "start"


def test_a_heating_run_of_ours_is_ended_in_the_cooling_season():
    heating = unit("heat", 30.0, reading=22.0, setpoint=21.0, running_s=3600.0)
    ours = replace(RUN, last_hvac_mode="heat", last_target=21.0)
    cmd, mem = decide(cin([room()], heat_pump=heating), ours)
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "fan_only", "season_ended")
    assert mem.stopped_since_ts == NOW and mem.running_since_ts is None


def test_a_room_below_its_target_starts_nothing():
    cmd, _ = decide(cin([room(temp=24.8)]), ControlMemory())
    assert cmd.action == "none" and cmd.plan == "off"


def test_a_planned_start_sends_nothing_but_shows_the_plan():
    cmd, _ = decide(cin([room()], mode="shadow"), ControlMemory())
    assert cmd.plan == "cool" and cmd.action == "start"  # the coordinator sends only in active mode


# ---- rest and resume ---------------------------------------------------------------------------------
def test_all_rooms_half_a_degree_below_their_target_rest_at_the_highest_setpoint():
    cmd, mem = decide(cin([room(temp=24.5)], heat_pump=RUNNING_UNIT), RUN)
    assert (cmd.plan, cmd.hvac_mode, cmd.target_temp, cmd.reason) == ("cool", "cool", 30.0, "rooms_cool_compressor_rests")
    assert mem.resting


def test_a_rest_waits_for_the_minimum_run():
    young = replace(RUN, running_since_ts=NOW - 300, phase_since_ts=NOW - 300)
    cmd, mem = decide(cin([room(temp=24.4)], heat_pump=RUNNING_UNIT), young)
    assert not mem.resting and cmd.waiting == "min_run_time"


def test_a_resting_unit_resumes_once_a_room_reaches_its_target():
    cmd, mem = decide(cin([room(temp=25.0)], heat_pump=RESTING_UNIT), REST)
    assert cmd.target_temp == 25.5 and cmd.reason == "resumed_at_minimum" and not mem.resting


def test_a_rest_that_does_not_stop_the_compressor_is_stopped_by_mode():
    hot = unit("cool", 25.0, reading=29.5, setpoint=30.0, running_s=3600.0)
    cmd, mem = decide(cin([room(temp=24.6)], heat_pump=hot), replace(REST, phase_since_ts=NOW - 1000))
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "fan_only", "rest_not_reached")


def test_no_room_to_cool_stops_at_once_with_a_run_on():
    cmd, mem = decide(cin([room(target=None)], heat_pump=RESTING_UNIT), REST)
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "fan_only", "cool_no_room")
    assert mem.fan_until_ts == NOW + PARAMS.heat_pump_fan_run_on_s


# ---- a room reading that is away (review 2026-11-13, K5) ---------------------------------------------
def test_a_run_whose_only_room_loses_its_reading_rests_instead_of_stopping():
    # Zigbee restarting takes every room sensor away for a minute: no stop, no quarter of an hour of fan, no hard start
    cmd, mem = decide(cin([room(temp=None)], heat_pump=RUNNING_UNIT, started_ts=NOW - 86400), RUN)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.reason) == ("cool", "adjust", 30.0, "waiting_for_room_data")
    assert mem.resting and mem.running_since_ts is not None and mem.cool_blind_since_ts == NOW


def test_a_resting_unit_whose_room_loses_its_reading_goes_on_resting():
    cmd, mem = decide(cin([room(temp=None)], heat_pump=RESTING_UNIT, started_ts=NOW - 86400), REST)
    assert (cmd.plan, cmd.action, cmd.reason) == ("cool", "none", "waiting_for_room_data")
    assert mem.resting and mem.cool_blind_since_ts == NOW


def test_ten_minutes_without_a_room_reading_stop_the_unit():
    blind = replace(REST, cool_blind_since_ts=NOW - 601)
    cmd, mem = decide(cin([room(temp=None)], heat_pump=RESTING_UNIT, started_ts=NOW - 86400), blind)
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "fan_only", "cool_no_room")
    assert mem.cool_blind_since_ts is None


def test_the_reading_back_ends_the_wait_and_the_rest_goes_on_as_any_other():
    blind = replace(REST, cool_blind_since_ts=NOW - 120, phase_since_ts=NOW - 120)
    cmd, mem = decide(cin([room(temp=26.0)], heat_pump=RESTING_UNIT), blind)
    assert mem.cool_blind_since_ts is None and mem.resting and cmd.waiting == "min_off_time"


def test_a_room_with_its_window_open_or_switched_off_is_no_missing_reading():
    open_window = replace(room(temp=None), window_open=True)
    cmd, _ = decide(cin([open_window], heat_pump=RUNNING_UNIT, started_ts=NOW - 86400), RUN)
    assert (cmd.action, cmd.reason) == ("stop", "cool_no_room")
    by_hand = replace(room(temp=None), cool_target_reason="heating_by_hand_in_cooling")
    cmd, _ = decide(cin([by_hand], heat_pump=RUNNING_UNIT, started_ts=NOW - 86400), RUN)
    assert (cmd.action, cmd.reason) == ("stop", "cool_no_room")


def test_a_missing_reading_where_the_highest_setpoint_would_not_rest_the_unit_stops_it():
    hot = unit("cool", 30.0, reading=29.5, setpoint=29.5, running_s=3600.0)
    cmd, _ = decide(cin([room(temp=None)], heat_pump=hot, started_ts=NOW - 86400), replace(RUN, last_target=29.5))
    assert (cmd.action, cmd.reason) == ("stop", "cool_no_room")


# ---- floor ------------------------------------------------------------------------------------------
def test_a_room_below_the_floor_rests_the_unit_while_another_is_too_warm():
    rooms = [room("schlafzimmer", 19.9, 20.0), room("badezimmer", 26.0, 25.0)]
    cmd, mem = decide(cin(rooms, heat_pump=RUNNING_UNIT, floor=20.0), RUN)
    assert cmd.reason == "cool_floor_reached" and mem.resting
    rooms = [room("schlafzimmer", 20.3, 20.0), room("badezimmer", 26.0, 25.0)]
    _, mem = decide(cin(rooms, heat_pump=RESTING_UNIT, floor=20.0), mem)
    assert mem.resting
    rooms = [room("schlafzimmer", 20.5, 20.0), room("badezimmer", 26.0, 25.0)]
    _, mem = decide(cin(rooms, heat_pump=RESTING_UNIT, floor=20.0), replace(mem, phase_since_ts=NOW - 3600))
    assert not mem.resting


# ---- blockers ------------------------------------------------------------------------------------------
def test_an_error_code_stops_at_once():
    cmd, _ = decide(cin([room()], heat_pump=replace(RUNNING_UNIT, error_code=5)), replace(RUN, phase_since_ts=NOW - 60))
    assert cmd.action == "stop" and "error_code_5" in cmd.blockers


@pytest.mark.parametrize(
    ("kw", "blocker"),
    [
        pytest.param({"frost": True}, "frost", id="frost"),
        pytest.param({"safety_state": "fallback"}, "safety_fallback", id="the_safety_fallback"),
    ],
)
def test_frost_or_the_safety_fallback_stops_cooling(kw, blocker):
    cmd, _ = decide(cin([room()], heat_pump=RUNNING_UNIT, **kw), RUN)
    assert cmd.action == "stop" and blocker in cmd.blockers


def test_an_open_window_where_the_unit_stands_stops_it():
    cmd, _ = decide(cin([room()], heat_pump=RUNNING_UNIT, unit_window_open=True), RUN)
    assert (cmd.action, cmd.reason) == ("stop", "cool_window_open_unit_room")


def test_no_cooling_without_any_room_reading():
    cmd, _ = decide(cin([room(temp=None)]), ControlMemory())
    assert cmd.action == "none"


# ---- holding at its least speed ---------------------------------------------------------------------
def test_a_held_setpoint_is_not_followed_by_every_half_degree():
    falls = replace(RUNNING_UNIT, indoor_temp=24.5)
    cmd, _ = decide(cin([room(temp=25.6)], heat_pump=falls), RUN)
    assert cmd.action == "none" and cmd.target_temp == 26.0


def test_a_unit_that_stopped_itself_while_a_room_is_warm_is_set_once_anew():
    history = (
        HeatPumpSample(NOW - 900, 30.0, 400.0, None, None, None, None),
        HeatPumpSample(NOW - 600, 0.0, 20.0, None, None, None, None),
        HeatPumpSample(NOW - 60, 0.0, 20.0, None, None, None, None),
    )
    stood = replace(unit("cool", 0.0, reading=24.0, setpoint=26.0), history=history)
    cmd, mem = decide(cin([room(temp=25.5)], heat_pump=stood), RUN)
    assert cmd.action == "adjust" and cmd.target_temp == 23.5 and cmd.reason == "relatched_unit_stopped"
    cmd, _ = decide(cin([room(temp=25.5)], heat_pump=stood, now=NOW + 700), replace(mem, last_target=23.5))
    assert cmd.action == "none"


def test_its_reading_risen_well_above_the_setpoint_sets_it_anew():
    risen = replace(RUNNING_UNIT, indoor_temp=27.0)  # setpoint 26: the unit's own controller would climb
    mem = replace(RUN, above_cap_since_ts=NOW - 700)
    cmd, _ = decide(cin([room(temp=25.2)], heat_pump=risen), mem)
    assert cmd.action == "adjust" and cmd.target_temp == 28.0


# ---- a room that stays too warm -------------------------------------------------------------------------
def test_a_room_that_stays_too_warm_turns_the_unit_up_half_a_degree_at_a_time():
    warm = room(temp=25.5, trend=-0.1)
    mem = replace(RUN, cool_warm_since_ts=NOW - 700)
    cmd, mem = decide(cin([warm], heat_pump=RUNNING_UNIT), mem)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 25.5, "cool_lift") and mem.cool_lifted
    cmd, mem = decide(cin([warm], heat_pump=replace(RUNNING_UNIT, target_temp=25.5), now=NOW + 700), mem)
    assert cmd.target_temp == 25.0


def test_turning_up_goes_no_further_than_three_degrees_below_its_reading():
    warm = room(temp=25.5, trend=-0.1)
    mem = replace(RUN, cool_warm_since_ts=NOW - 700, cool_lifted=True, last_target=22.0)
    cmd, _ = decide(cin([warm], heat_pump=replace(RUNNING_UNIT, target_temp=22.0)), mem)
    assert cmd.action == "none" and cmd.target_temp == 22.0


def test_a_room_two_degrees_too_warm_turns_it_up_fully_at_once():
    cmd, mem = decide(cin([room(temp=27.2, trend=-0.5)], heat_pump=RUNNING_UNIT), RUN)
    assert cmd.target_temp == 22.0 and cmd.reason == "cool_lift" and mem.cool_lifted


def test_at_its_target_a_turned_up_unit_goes_back_to_its_least_speed():
    mem = replace(RUN, cool_lifted=True, last_target=22.0)
    cmd, mem = decide(cin([room(temp=24.9)], heat_pump=replace(RUNNING_UNIT, target_temp=22.0)), mem)
    assert (cmd.target_temp, cmd.reason) == (26.0, "holding_at_minimum") and not mem.cool_lifted


def test_quiet_stays_at_its_least_speed():
    warm = room(temp=25.5, trend=-0.1)
    cmd, mem = decide(cin([warm], heat_pump=RUNNING_UNIT, shortfall="quiet"), replace(RUN, cool_warm_since_ts=NOW - 700))
    assert cmd.action == "none" and cmd.target_temp == 26.0 and cmd.reason == "cool_too_warm_quiet" and not mem.cool_lifted


# ---- run-on --------------------------------------------------------------------------------------------
def test_a_run_on_follows_a_stop_also_after_a_long_rest():
    cmd, mem = decide(cin([room(target=None)], heat_pump=RESTING_UNIT), replace(REST, phase_since_ts=NOW - 7200))
    assert cmd.hvac_mode == "fan_only" and mem.fan_until_ts == NOW + 900


def test_after_the_run_on_the_unit_is_switched_off():
    mem = replace(ControlMemory(), fan_until_ts=NOW - 1, last_hvac_mode="fan_only", last_command_ts=NOW - 900)
    cmd, mem = decide(cin([room(target=None)], heat_pump=unit("fan_only")), mem)
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "off", "fan_run_on")


def test_no_run_on_where_it_is_set_to_nought():
    params = replace(PARAMS, heat_pump_fan_run_on_s=0.0)
    inputs = replace(cin([room(target=None)], heat_pump=RUNNING_UNIT), params=params)
    cmd, _ = decide(inputs, RUN)
    assert cmd.hvac_mode == "off"


# ---- hand ----------------------------------------------------------------------------------------------
def test_a_setpoint_set_by_a_person_is_respected():
    changed = replace(RUNNING_UNIT, target_temp=22.0, setting_change=SettingChange(NOW - 30, hand.PERSON))
    cmd, mem = decide(cin([room()], heat_pump=changed), RUN)
    assert cmd.plan == "hands_off" and cmd.reason == "manual_takeover"
    assert mem.manual_until_ts == NOW + PARAMS.manual_hold_s


def test_heat_or_dry_by_hand_is_left_alone():
    for mode in ("heat", "dry"):
        cmd, _ = decide(cin([room()], heat_pump=unit(mode, 30.0, running_s=600.0)), ControlMemory())
        assert cmd.plan == "hands_off" and cmd.reason == f"manual_mode_{mode}"


def test_a_cooling_unit_found_after_the_time_in_hand_is_taken_over():
    found = unit("cool", 30.0, reading=25.0, setpoint=22.0, running_s=3600.0)
    cmd, mem = decide(cin([room(temp=25.5)], heat_pump=found), ControlMemory())
    assert mem.running_since_ts == NOW and mem.last_hvac_mode == "cool"
    assert cmd.plan == "cool" and cmd.target_temp == 26.0  # set once to its least speed


def test_a_fan_stage_chosen_by_hand_stays():
    silent = replace(RUNNING_UNIT, fan_mode="silent", fan_modes=("silent", "auto", "full"))
    cmd, _ = decide(cin([room(temp=25.2)], heat_pump=silent), replace(RUN, fan_normal="auto"))
    assert cmd.fan_mode in (None, "silent")


def test_a_command_the_unit_ignores_is_repeated_ever_more_rarely():
    ignored = replace(unit("off"), setting_change=None, settings_watched_since_ts=NOW - 7200)
    mem = replace(RUN, last_command_ts=NOW - 200)
    cmd, mem = decide(cin([room()], heat_pump=ignored), mem)
    assert mem.not_carried_out == 1


# ---- control off and let go -------------------------------------------------------------------------------
def test_control_off_is_disabled():
    cmd, _ = decide(cin([room()], mode="off"), RUN)
    assert cmd.plan == "disabled"


# ---- the heating control in the heating season --------------------------------------------------------------
def heating_inputs(heat_pump, rooms=None):
    from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
    from custom_components.thriftherm.engines.heat_pump_control import ControlInputs

    return ControlInputs(
        now_ts=NOW, control_mode="active", op_mode="auto", heat_pump=heat_pump,
        run_state=heat_pump_engine.run_state(heat_pump, PARAMS), rooms=tuple(rooms or [room_result("badezimmer", 21.0, 19.0)]),
        advice_source="midea", blocked_reason=None, safety_state="ok", outdoor_c=5.0, expected_cop=3.5, break_even_cop=2.9,
        outdoor_bin=2, bin_learned=True, params=PARAMS, cheaper=True,
    )


def test_a_cooling_run_of_ours_is_ended_in_the_heating_season():
    from custom_components.thriftherm.engines import heat_pump_control

    cmd, mem = heat_pump_control.decide(heating_inputs(RUNNING_UNIT), RUN)
    assert (cmd.action, cmd.hvac_mode, cmd.reason) == ("stop", "fan_only", "season_ended")
    assert mem.last_hvac_mode == "fan_only" and mem.running_since_ts is None


def test_cool_by_hand_in_the_heating_season_stays_hands_off():
    from custom_components.thriftherm.engines import heat_pump_control

    cmd, _ = heat_pump_control.decide(heating_inputs(RUNNING_UNIT), ControlMemory())
    assert cmd.plan == "hands_off" and cmd.reason == "manual_mode_cool"


def test_the_heating_control_never_asks_for_cool():
    from itertools import product

    from custom_components.thriftherm.engines import heat_pump_control

    modes = ("off", "heat", "cool", "fan_only", "dry")
    units = [unit(m, hz, reading=r) for m, hz, r in product(modes, (0.0, 30.0), (18.0, 24.0))]
    memories = [ControlMemory(), RUN, REST, replace(RUN, last_hvac_mode="heat"), replace(RUN, fan_until_ts=NOW + 60)]
    temps = (16.0, 20.5, 23.0)
    for hp, mem, t in product(units, memories, temps):
        cmd, _ = heat_pump_control.decide(heating_inputs(hp, [room_result("badezimmer", 21.0, t)]), mem)
        assert cmd.hvac_mode != "cool", (hp.hvac_mode, mem, t)


def test_the_heating_control_waits_the_pause_after_a_change_back_to_heating():
    from custom_components.thriftherm.engines import heat_pump_control

    off = unit("off", 0.0, reading=19.0)
    cmd, _ = heat_pump_control.decide(replace(heating_inputs(off), season_since_ts=NOW - 600), ControlMemory())
    assert cmd.action == "none" and cmd.waiting == "season_switch_pause"
    cmd, _ = heat_pump_control.decide(replace(heating_inputs(off), season_since_ts=NOW - 1600), ControlMemory())
    assert cmd.action == "start"
