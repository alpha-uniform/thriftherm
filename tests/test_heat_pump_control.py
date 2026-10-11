"""Phase 7: Midea controller decisions (pure engine)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
from custom_components.thriftherm.engines.heat_pump_control import ControlInputs, ControlMemory, decide
from custom_components.thriftherm.models import DryingState, RoomResult

from .conftest import PARAMS, heat_pump_state

NOW = 1_000_000.0


def rr(key="badezimmer", temp=19.0, target=21.0, trend=None, heat_pump_allowed=True, drying=False, boost=False) -> RoomResult:
    return RoomResult(
        key=key,
        target=target,
        target_reason="schedule_comfort",
        temperature=temp,
        deviation_k=None if temp is None else temp - target,
        demand=0.5,
        trend_k_per_h=trend,
        dew_point_c=None,
        abs_humidity_g_m3=None,
        window_open=False,
        window_unknown=False,
        heating_allowed=heat_pump_allowed,
        internal_gain_w=None,
        drying=DryingState(True, NOW - 300, 9.0) if drying else DryingState(),
        heat_pump_allowed=heat_pump_allowed,
        boost_active=boost,
    )


def off_heat_pump(**kw):
    kw.setdefault("hvac_mode", "off")
    kw.setdefault("compressor_hz", 0.0)
    kw.setdefault("running_s", None)
    return heat_pump_state(**kw)


def inp(rooms, *, mode="shadow", op="auto", heat_pump=None, advice="midea", blocked=None, safety="ok", outdoor=5.0, cop=3.5, bin_count=10, now=NOW, params=PARAMS, cheaper=False):
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
    )


RUNNING = ControlMemory(running_since_ts=NOW - 300, last_command_ts=NOW - 300, last_hvac_mode="heat", last_target=21.0)


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
    assert cmd.target_temp == 21.0  # intake 20 °C + offset 1 K
    assert cmd.fan_mode == "auto" and cmd.reason == "cheaper_than_gas"
    assert mem.running_since_ts == NOW and mem.last_target == 21.0


def test_no_start_inside_hysteresis():
    cmd, _ = decide(inp([rr(temp=20.8)]), ControlMemory())
    assert cmd.plan == "off" and cmd.action == "none"


def test_gas_cheaper_and_bin_known_keeps_heat_pump_off():
    cmd, _ = decide(inp([rr()], advice="boiler", cop=2.5, bin_count=10), ControlMemory())
    assert cmd.plan == "off" and cmd.reason == "gas_cheaper"


def test_learning_run_in_unknown_bin_once_per_day():
    cmd, mem = decide(inp([rr()], mode="active", advice="boiler", cop=2.6, bin_count=0), ControlMemory())
    assert cmd.action == "start" and cmd.load == "learning" and cmd.reason == "learning_run"
    assert dict(mem.learning_bins)[2] == NOW
    # stop after learning, try again 2 h later in the same bin: no new learning run
    stopped = replace(mem, running_since_ts=None, stopped_since_ts=NOW + 3600, learning_until_ts=None, last_hvac_mode="off")
    cmd2, _ = decide(inp([rr()], mode="active", advice="boiler", cop=2.6, bin_count=1, now=NOW + 7200), stopped)
    assert cmd2.plan == "off" and cmd2.reason == "gas_cheaper"


def test_no_learning_run_when_estimate_far_below_break_even():
    cmd, _ = decide(inp([rr()], advice="boiler", cop=2.0, bin_count=0), ControlMemory())
    assert cmd.plan == "off"


def test_blocker_stops_immediately_even_within_min_run():
    cmd, mem = decide(inp([rr()], outdoor=-12.0), RUNNING)
    assert cmd.action == "stop" and cmd.hvac_mode == "fan_only"  # the compressor stops, the fan runs on
    assert "outdoor_below_-10C" in cmd.blockers
    assert mem.running_since_ts is None and mem.stopped_since_ts == NOW


def test_outdoor_limit_is_minus_ten_by_default():
    cmd, _ = decide(inp([rr()], outdoor=-9.5), ControlMemory())
    assert cmd.action == "start"


def test_min_run_then_stop_then_min_off():
    satisfied = [rr(temp=21.5)]
    cmd, mem = decide(inp(satisfied), RUNNING)  # 5 min into the run
    assert cmd.plan == "heat" and cmd.action == "none" and cmd.waiting == "min_run_time"
    long_run = replace(RUNNING, running_since_ts=NOW - 1300)
    cmd, mem = decide(inp(satisfied), long_run)
    assert cmd.action == "stop" and cmd.reason == "target_reached"
    cmd, mem = decide(inp([rr(temp=19.0)], now=NOW + 300), mem)
    assert cmd.plan == "off" and cmd.waiting == "fan_run_on"
    cmd, mem = decide(inp([rr(temp=19.0)], now=NOW + 900), mem)
    assert (cmd.action, cmd.hvac_mode) == ("stop", "off")
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 960), mem)
    assert cmd.action == "start"  # the minimum off time counts from the end of the heating


def test_without_a_fan_run_on_the_unit_is_switched_off_at_once_and_waits_its_minimum_off_time():
    at_once = replace(PARAMS, heat_pump_fan_run_on_s=0.0)
    cmd, mem = decide(inp([rr(temp=21.5)], params=at_once), replace(RUNNING, running_since_ts=NOW - 1300))
    assert (cmd.action, cmd.hvac_mode) == ("stop", "off") and mem.fan_until_ts is None
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 300, params=at_once), mem)
    assert cmd.plan == "off" and cmd.waiting == "min_off_time"
    cmd, _ = decide(inp([rr(temp=19.0)], now=NOW + 700, params=at_once), mem)
    assert cmd.action == "start"


LONG_RUN = replace(RUNNING, running_since_ts=NOW - 1300, last_target=22.0)  # the unit reports 22 °C as its setpoint


def test_the_fan_runs_on_for_a_quarter_of_an_hour_before_the_unit_is_switched_off():
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=heat_pump_state()), LONG_RUN)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "fan_only")
    assert mem.fan_until_ts == NOW + 900 and mem.last_hvac_mode == "fan_only" and mem.running_since_ts is None
    fan = heat_pump_state(hvac_mode="fan_only", compressor_hz=0.0, running_s=None)
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 600), mem)
    assert (cmd.plan, cmd.action, cmd.waiting) == ("off", "none", "fan_run_on")  # ours, not a hand on the remote
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=fan, now=NOW + 900), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "off")
    assert mem.fan_until_ts is None and mem.last_hvac_mode == "off"


def test_a_mode_set_by_hand_during_the_fan_run_on_ends_it():
    _, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=heat_pump_state()), LONG_RUN)
    assert mem.fan_until_ts == NOW + 900
    cooling = heat_pump_state(hvac_mode="cool")
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=cooling, now=NOW + 600), mem)
    assert cmd.plan == "hands_off" and mem.fan_until_ts is None


def test_quick_heat_up_runs_full_power_even_if_gas_cheaper():
    cmd, _ = decide(inp([rr(temp=19.0, boost=True)], advice="boiler", cop=2.5), ControlMemory())
    assert (cmd.action, cmd.load, cmd.target_temp, cmd.fan_mode) == ("start", "boost", 30.0, "high")
    assert cmd.reason == "quick_heat_up"


def test_drying_uses_heat_pump_even_if_gas_cheaper():
    cmd, _ = decide(inp([rr(temp=22.0, target=22.0, drying=True)], advice="boiler", cop=2.5), ControlMemory())
    assert cmd.action == "start" and cmd.reason == "bathroom_drying" and cmd.load == "eco"


def test_room_with_window_open_is_ignored():
    cmd, _ = decide(inp([rr(temp=18.0, heat_pump_allowed=False)]), ControlMemory())
    assert cmd.plan == "off" and cmd.action == "none"


def test_active_mode_detects_manual_takeover():
    mem = replace(RUNNING, last_command_ts=NOW - 600)
    user_changed = off_heat_pump(hvac_mode="heat", compressor_hz=40.0)
    user_changed = replace(user_changed, target_temp=25.0)
    cmd, new = decide(inp([rr()], mode="active", heat_pump=user_changed), mem)
    assert cmd.plan == "hands_off" and cmd.reason == "manual_takeover"
    assert new.manual_until_ts == pytest.approx(NOW + 3 * 3600)
    cmd2, _ = decide(inp([rr()], mode="active", heat_pump=user_changed, now=NOW + 60), new)
    assert cmd2.plan == "hands_off"


def test_shadow_mode_ignores_state_mismatch():
    # in shadow mode nothing is sent, so the real Midea state never matches the plan
    mem = replace(RUNNING, last_command_ts=NOW - 600)
    cmd, _ = decide(inp([rr()], mode="shadow"), mem)
    assert cmd.plan == "heat"


def test_eco_offset_rises_when_rooms_warm_too_slowly():
    mem = replace(RUNNING, running_since_ts=NOW - 2000, last_command_ts=NOW - 2000, last_target=22.0)
    cmd, new = decide(inp([rr(temp=19.0, trend=0.1)], mode="active", heat_pump=heat_pump_state()), mem)
    assert new.offset_k == 1.5
    assert cmd.action == "adjust" and cmd.target_temp == 21.5


def test_eco_offset_falls_when_rooms_warm_fast():
    mem = replace(RUNNING, running_since_ts=NOW - 2000, last_command_ts=NOW - 2000, offset_k=2.0, last_target=22.0)
    _, new = decide(inp([rr(temp=19.0, trend=2.0)], mode="active", heat_pump=heat_pump_state()), mem)
    assert new.offset_k == 1.5


def test_memory_roundtrip():
    mem = ControlMemory(offset_k=1.5, learning_bins=((2, NOW),), manual_until_ts=NOW + 10)
    back = ControlMemory.from_storage(mem.to_storage())
    assert back.offset_k == 1.5 and back.learning_bins == ((2, NOW),) and back.manual_until_ts == NOW + 10


def test_no_demand_is_reported_as_such():
    cmd, _ = decide(inp([rr(temp=21.5)], advice="none", cop=4.8), ControlMemory())
    assert cmd.plan == "off" and cmd.reason == "no_demand"


def test_drying_keeps_heating_past_the_target():
    """Warm air carries the moisture out, so drying does not stop at the setpoint."""
    room = rr(temp=22.0, target=22.0, drying=True)
    cmd, _ = decide(inp([room]), ControlMemory())
    assert cmd.action == "start"
    assert cmd.reason == "bathroom_drying"
    assert cmd.load == "eco"


def test_drying_stops_once_the_room_is_well_past_the_target():
    """Without a ceiling the bathroom would be driven up for the full hour."""
    room = rr(temp=23.6, target=22.0, drying=True)  # 1.6 K over, past the 1.5 K ceiling
    cmd, _ = decide(inp([room], advice="boiler"), ControlMemory())
    assert cmd.action != "start"
    assert cmd.reason != "bathroom_drying"


# --- review fixes -------------------------------------------------------------------------------
def test_planned_runs_do_not_teach_the_eco_offset():
    """In shadow mode nothing heats the rooms, so their slow warm-up says nothing about the offset."""
    mem = replace(RUNNING, running_since_ts=NOW - 2000, last_command_ts=NOW - 2000, last_target=22.0)
    _, shadow = decide(inp([rr(temp=19.0, trend=0.0)], mode="shadow", heat_pump=heat_pump_state()), mem)
    _, active = decide(inp([rr(temp=19.0, trend=0.0)], mode="active", heat_pump=heat_pump_state()), mem)
    assert shadow.offset_k == mem.offset_k
    assert active.offset_k == 1.5


def test_a_planned_learning_run_does_not_use_up_the_bin():
    cmd, mem = decide(inp([rr()], mode="shadow", advice="boiler", cop=2.6, bin_count=0), ControlMemory())
    assert cmd.load == "learning"
    assert mem.learning_bins == ()


def test_run_state_survives_a_restart_and_a_blocker_still_stops_the_unit():
    running = replace(RUNNING, offset_k=1.5)
    restored = ControlMemory.from_storage(running.to_storage())
    assert restored.running_since_ts == running.running_since_ts and restored.last_hvac_mode == "heat"
    cmd, _ = decide(inp([rr()], mode="shadow", heat_pump=heat_pump_state(), safety="fallback"), restored)
    assert cmd.action == "stop" and cmd.reason == "blocked"


def test_corrupt_stored_run_state_is_ignored():
    restored = ControlMemory.from_storage({"running_since_ts": "nonsense", "last_hvac_mode": 7, "offset_k": 1.0})
    assert restored.running_since_ts is None and restored.last_hvac_mode is None and restored.offset_k == 1.0


def test_fresh_run_state_keeps_only_what_was_learned():
    mem = replace(RUNNING, offset_k=2.5, learning_bins=((2, NOW),), manual_until_ts=NOW + 100)
    fresh = mem.fresh_run_state()
    assert fresh.offset_k == 2.5 and fresh.learning_bins == ((2, NOW),)
    assert fresh.running_since_ts is None and fresh.last_hvac_mode is None and fresh.manual_until_ts is None


def test_eco_setpoint_never_below_what_split_units_accept():
    from custom_components.thriftherm.engines.heat_pump_control import eco_setpoint

    assert eco_setpoint(heat_pump_state(intake=(14.0, 45.0)), 1.0) == 17.0


def test_eco_setpoint_falls_back_to_the_unit_reading_without_an_intake_sensor():
    from custom_components.thriftherm.engines.heat_pump_control import eco_setpoint
    from .conftest import reading

    assert eco_setpoint(heat_pump_state(intake=(20.0, 45.0)), 1.0) == 21.0
    assert eco_setpoint(heat_pump_state(intake=None), 1.0) == 22.0  # the unit's indoor_temp (21 °C)
    stale = replace(heat_pump_state(), intake_temp=reading(18.0, valid=False))
    assert eco_setpoint(stale, 1.0) == 22.0
    assert eco_setpoint(replace(heat_pump_state(intake=None), indoor_temp=None), 1.0) is None


@pytest.mark.parametrize(
    ("advice", "cop", "run_s", "expected"),
    [
        # satisfied within the minimum run: keep heating at the last setpoint
        ("midea", 3.5, 300, ("heat", "none", "heat", "target_reached", 21.0, "auto", "eco", "min_run_time")),
        # economics turned within the minimum run: finish it in eco
        ("boiler", 2.5, 300, ("heat", "none", "heat", "gas_cheaper", 21.0, "auto", "eco", "min_run_time")),
        # and after it
        ("midea", 3.5, 1300, ("off", "stop", "fan_only", "target_reached", None, None, None, None)),
        ("boiler", 2.5, 1300, ("off", "stop", "fan_only", "gas_cheaper", None, None, None, None)),
    ],
)
def test_a_run_that_should_end_respects_the_minimum_run(advice, cop, run_s, expected):
    mem = replace(RUNNING, running_since_ts=NOW - run_s, last_target=21.0)
    cmd, new = decide(inp([rr(temp=21.5)], advice=advice, cop=cop, bin_count=10), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason, cmd.target_temp, cmd.fan_mode, cmd.load, cmd.waiting) == expected
    assert (new is mem) == (cmd.action == "none")


# ---- setpoint follows the rooms: the unit gets their target plus one degree and regulates by itself

FIXED = replace(PARAMS, heat_pump_follow_rooms=True)  # rooms at 21 °C: the unit gets 22 °C
WARM = [rr(temp=21.8, target=21.0)]  # in its comfort time, nothing to heat


def resting(**kw) -> RoomResult:
    return replace(rr(**kw), target_reason="mode_away")


def test_following_the_rooms_is_sent_whatever_the_intake_air_reads():
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=off_heat_pump(intake=(18.0, 45.0)), params=FIXED), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "start", 22.0, "auto")


def test_following_the_rooms_quick_heat_up_only_raises_the_fan():
    cold_unit = off_heat_pump(intake=(18.0, 45.0))
    cmd, _ = decide(inp([rr(temp=19.0, boost=True)], heat_pump=cold_unit, params=FIXED), ControlMemory())
    assert (cmd.target_temp, cmd.fan_mode, cmd.load) == (22.0, "high", "boost")


def test_following_the_rooms_starts_for_a_room_in_its_comfort_time_even_if_it_is_warm():
    cmd, _ = decide(inp(WARM, advice="none", params=FIXED, cheaper=True), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.reason) == ("heat", "start", "cheaper_than_gas")


def test_following_the_rooms_runs_through_although_every_room_is_warm():
    running = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    cmd, _ = decide(inp(WARM, advice="none", heat_pump=heat_pump_state(), params=FIXED, cheaper=True), running)
    assert (cmd.plan, cmd.action) == ("heat", "none")


def test_following_the_rooms_holds_the_setback_plus_one_degree_at_night():
    running = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    night = [replace(rr(temp=20.0, target=17.0), target_reason="schedule_setback")]
    cmd, mem = decide(inp(night, advice="none", heat_pump=heat_pump_state(), params=FIXED, cheaper=True), running)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.reason) == ("heat", "adjust", 18.0, "cheaper_than_gas")
    assert cmd.delivers is False and mem.last_target == 18.0  # the home is warmer than that: the unit idles by itself


def test_following_the_rooms_takes_the_highest_target_of_the_served_rooms():
    rooms = [rr(key="badezimmer", temp=19.0, target=21.0), rr(key="schlafzimmer", temp=19.0, target=20.0)]
    cmd, _ = decide(inp(rooms, heat_pump=off_heat_pump(intake=(18.0, 45.0)), params=FIXED), ControlMemory())
    assert cmd.target_temp == 22.0
    open_window = [replace(rooms[0], heat_pump_allowed=False), rooms[1]]  # the bathroom does not count with its window open
    cmd, _ = decide(inp(open_window, heat_pump=off_heat_pump(intake=(18.0, 45.0)), params=FIXED), ControlMemory())
    assert cmd.target_temp == 21.0


def test_following_the_rooms_switches_off_once_nobody_wants_the_served_rooms_warm():
    running = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    night = [resting(temp=20.0, target=17.0)]
    cmd, _ = decide(inp(night, advice="none", heat_pump=heat_pump_state(), params=FIXED, cheaper=True), running)
    assert (cmd.plan, cmd.action, cmd.reason) == ("off", "stop", "comfort_time_over")
    cmd, _ = decide(inp(night, advice="none", params=FIXED, cheaper=True), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.reason) == ("off", "none", "no_demand")


def test_following_the_rooms_stops_when_gas_becomes_cheaper():
    running = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    cmd, _ = decide(inp(WARM, advice="none", heat_pump=heat_pump_state(), params=FIXED, cheaper=False), running)
    assert (cmd.plan, cmd.action, cmd.reason) == ("off", "stop", "gas_cheaper")


def test_following_the_rooms_unit_idling_at_its_setpoint_does_not_deliver():
    running = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=heat_pump_state(intake=(22.0, 45.0)), params=FIXED), running)
    assert cmd.plan == "heat" and cmd.delivers is False  # the unit stays on, the boiler brings what the room lacks
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=heat_pump_state(intake=(21.5, 45.0)), params=FIXED), running)
    assert cmd.plan == "heat" and cmd.delivers is True


def test_following_the_rooms_never_adapts_the_offset():
    running = replace(RUNNING, running_since_ts=NOW - 2000, last_command_ts=NOW - 2000, last_target=22.0)
    unit = heat_pump_state(intake=(20.0, 45.0))
    _, mem = decide(inp([rr(temp=19.0, trend=0.0)], mode="active", heat_pump=unit, params=FIXED), running)
    assert mem.offset_k is None


def test_without_fixed_setpoint_the_lever_stays():
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=off_heat_pump(intake=(18.0, 45.0))), ControlMemory())
    assert cmd.target_temp == 19.0 and cmd.delivers is True


def test_active_control_takes_over_a_unit_found_heating():
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=heat_pump_state(), params=FIXED), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "adjust", 22.0, "auto")
    assert mem.running_since_ts == NOW and mem.last_target == 22.0 and mem.last_command_ts == NOW


def test_a_unit_taken_over_with_nothing_to_do_is_stopped_after_the_minimum_run():
    rooms = [resting(temp=20.0, target=17.0)]
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=heat_pump_state(), params=FIXED), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.waiting) == ("heat", "none", "min_run_time")
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=heat_pump_state(), params=FIXED, now=NOW + 1300), mem)
    assert (cmd.plan, cmd.action, cmd.hvac_mode) == ("off", "stop", "fan_only")


def test_planning_only_never_takes_a_running_unit_over():
    _, mem = decide(inp([resting(temp=20.0, target=17.0)], heat_pump=heat_pump_state(), params=FIXED), ControlMemory())
    assert mem.running_since_ts is None


# ---- a restart of Home Assistant changes nothing for a running unit ----------------------

def test_a_running_unit_is_not_stopped_while_the_room_sensors_have_not_reported():
    # seen 2026-10-11: after a restart the room temperatures were unknown, "no room is cold" was read
    # from that, and the unit was sent to its fan run-on
    long_run = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    unknown = [rr(temp=None)]
    for params in (PARAMS, FIXED):
        cmd, mem = decide(inp(unknown, mode="active", heat_pump=heat_pump_state(), params=params), long_run)
        assert (cmd.plan, cmd.action, cmd.waiting) == ("heat", "none", "room_data")
        assert mem == long_run


def test_a_blocker_still_stops_a_running_unit_without_room_data():
    long_run = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    cmd, _ = decide(inp([rr(temp=None)], mode="active", heat_pump=heat_pump_state(), outdoor=-12.0), long_run)
    assert cmd.action == "stop"


def test_a_unit_that_is_off_is_not_started_without_room_data_in_lever_mode():
    cmd, _ = decide(inp([rr(temp=None)]), ControlMemory())
    assert cmd.plan == "off" and cmd.action == "none"
