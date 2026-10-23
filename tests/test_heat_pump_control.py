"""Phase 7: Midea controller decisions (pure engine)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
from custom_components.thriftherm.engines.heat_pump_control import ControlInputs, ControlMemory, decide, strongest_fan
from custom_components.thriftherm.models import DryingState, RoomResult, SettingChange

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
    assert new.manual_until_ts == pytest.approx(NOW + 2 * 3600) and new.manual_by == "device"  # as long as a room override
    assert (cmd.manual_until_ts, cmd.manual_by) == (new.manual_until_ts, "device")
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
        # satisfied within the minimum run: keep heating at the last setpoint (the fan is not ours to set)
        ("midea", 3.5, 300, ("heat", "none", "heat", "target_reached", 21.0, None, "eco", "min_run_time")),
        # economics turned within the minimum run: finish it in eco
        ("boiler", 2.5, 300, ("heat", "none", "heat", "gas_cheaper", 21.0, None, "eco", "min_run_time")),
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
    assert cmd.delivers and mem.last_target == 18.0  # the home is warmer than that: the unit idles by itself, no room lacks heat


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


# A served room the unit does not get warm: the unit is set higher in small steps, the boiler comes last.

LONG_RUN = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
HOLDS_BACK = replace(heat_pump_state(intake=(22.0, 45.0)), indoor_temp=22.0)  # at its setpoint of 22 °C where it stands


def lagging_room(now: float, mem: ControlMemory, unit=HOLDS_BACK, trend: float | None = 0.1, temp: float = 19.8, mode: str = "active"):
    return decide(inp([rr(temp=temp, trend=trend)], mode=mode, heat_pump=unit, params=FIXED, now=now), mem)


def test_following_the_rooms_a_room_on_its_way_needs_neither_a_higher_setpoint_nor_the_boiler():
    """Seen 2026-10-13: the unit at its setpoint where it stands, the bathroom rising by 2 K an hour, the boiler called."""
    cmd, mem = lagging_room(NOW, LONG_RUN, trend=2.0)
    assert (cmd.plan, cmd.action, cmd.delivers) == ("heat", "none", True)
    assert mem.offset_k is None and mem.lagging_since_ts is None
    cmd, mem = lagging_room(NOW, LONG_RUN, trend=None)  # an unknown trend is no evidence
    assert cmd.delivers and mem.offset_k is None and mem.lagging_since_ts is None


def test_following_the_rooms_a_lagging_room_sets_the_unit_higher_half_a_degree_at_a_time():
    cmd, mem = lagging_room(NOW, LONG_RUN)
    assert (cmd.action, cmd.delivers, mem.offset_k, mem.lagging_since_ts) == ("none", True, None, NOW)  # it lags since now
    cmd, mem = lagging_room(NOW + 1140, mem)
    assert cmd.action == "none" and mem.offset_k is None  # not yet
    cmd, mem = lagging_room(NOW + 1200, mem)
    assert (cmd.action, cmd.target_temp, cmd.delivers, mem.offset_k) == ("adjust", 22.5, True, 1.5)
    cmd, mem = lagging_room(NOW + 1800, mem, unit=replace(HOLDS_BACK, target_temp=22.5))
    assert cmd.action == "none" and mem.offset_k == 1.5  # the step gets its time before the next one
    cmd, mem = lagging_room(NOW + 2400, mem, unit=replace(HOLDS_BACK, target_temp=22.5))
    assert (cmd.action, cmd.target_temp, cmd.delivers, mem.offset_k) == ("adjust", 23.0, True, 2.0)


def test_following_the_rooms_the_boiler_helps_once_the_higher_setpoint_is_used_up():
    mem, now, seen = LONG_RUN, NOW, []
    for _ in range(8):  # the unit's room follows its setpoint: it keeps holding back
        setpoint = 21.0 + (mem.offset_k if mem.offset_k is not None else 1.0)
        unit = replace(heat_pump_state(intake=(setpoint, 45.0)), target_temp=setpoint, indoor_temp=setpoint)
        cmd, mem = lagging_room(now, mem, unit=unit)
        seen.append((cmd.target_temp, cmd.delivers))
        now += 1200
    assert [target for target, _ in seen] == [22.0, 22.5, 23.0, 23.5, 24.0, 24.0, 24.0, 24.0]  # two degrees at most
    assert [delivers for _, delivers in seen] == [True] * 5 + [False] * 3
    assert mem.boiler_helps and mem.offset_k == 3.0

    # the boiler makes the room rise: it goes on helping until the room is there, not only while it lags
    at_24 = replace(heat_pump_state(intake=(24.0, 45.0)), target_temp=24.0, indoor_temp=24.0)
    cmd, mem = lagging_room(now, mem, unit=at_24, trend=1.2)
    assert cmd.delivers is False
    cmd, mem = lagging_room(now + 60, mem, unit=at_24, trend=0.5, temp=21.0)
    assert cmd.delivers and not mem.boiler_helps and mem.offset_k == 3.0
    # and the setpoint comes back in the same small steps, two hours after the last step at the earliest
    cmd, mem = lagging_room(now + 600, mem, unit=at_24, trend=0.0, temp=21.2)
    assert cmd.action == "none" and mem.offset_k == 3.0
    cmd, mem = lagging_room(now + 3600, mem, unit=at_24, trend=0.0, temp=21.2)
    assert (cmd.action, cmd.target_temp, mem.offset_k) == ("adjust", 23.5, 2.5)


def test_following_the_rooms_a_unit_working_flat_out_is_not_set_higher():
    """Its own reading far below its setpoint, its compressor running: a higher setpoint changes nothing, it does not manage."""
    flat_out = replace(heat_pump_state(intake=(20.0, 45.0)), indoor_temp=20.0)
    cmd, mem = lagging_room(NOW, LONG_RUN, unit=flat_out)
    assert cmd.delivers and not mem.boiler_helps  # a room lags for a while before anything is concluded
    cmd, mem = lagging_room(NOW + 1200, mem, unit=flat_out)
    assert (cmd.action, cmd.delivers, mem.offset_k, mem.boiler_helps) == ("none", False, None, True)


def test_following_the_rooms_a_unit_standing_at_its_own_reading_is_set_higher_whatever_the_intake_sensor_says():
    """Seen 2026-10-17: the unit read 22 °C at its setpoint of 22 °C and stood, the intake sensor beside it
    20.3 °C. Taken for working flat out, the unit was left at 22 °C and the boiler called for the bathroom."""
    standing = replace(heat_pump_state(intake=(20.3, 52.0), compressor_hz=0.0, running_s=None), indoor_temp=22.0)
    cmd, mem = lagging_room(NOW, LONG_RUN, unit=standing)
    cmd, mem = lagging_room(NOW + 1200, mem, unit=standing)
    assert (cmd.action, cmd.target_temp, cmd.delivers, mem.boiler_helps) == ("adjust", 22.5, True, False)
    # the compressor standing is enough on its own: the unit has nothing left to give at this setpoint
    no_reading = replace(standing, indoor_temp=None)
    cmd, mem = lagging_room(NOW + 1200, replace(LONG_RUN, lagging_since_ts=NOW), unit=no_reading)
    assert (cmd.action, cmd.target_temp, mem.boiler_helps) == ("adjust", 22.5, False)


def test_following_the_rooms_a_unit_standing_while_the_boiler_helps_is_set_higher_and_the_boiler_stops():
    """The evening of 2026-10-17 after the update: the boiler helped, wrongly concluded, and the unit stood at 22 °C."""
    standing = replace(heat_pump_state(intake=(20.3, 52.0), compressor_hz=0.0, running_s=None), indoor_temp=22.0)
    helped = replace(LONG_RUN, boiler_helps=True, lagging_since_ts=NOW - 1260, last_offset_review_ts=NOW - 60)
    cmd, mem = lagging_room(NOW, helped, unit=standing)
    assert (cmd.action, cmd.delivers, mem.boiler_helps) == ("none", False, True)  # the last review was a minute ago
    cmd, mem = lagging_room(NOW + 1140, mem, unit=standing)
    assert (cmd.action, cmd.target_temp, cmd.delivers, mem.boiler_helps, mem.offset_k) == ("adjust", 22.5, True, False, 1.5)


def test_following_the_rooms_a_unit_held_up_far_below_its_setpoint_is_not_set_higher():
    """It stands although it reads 19 °C at 22 °C (a protection stop, a fault without a code): the boiler helps."""
    held_up = replace(heat_pump_state(intake=(19.0, 45.0), compressor_hz=0.0, running_s=None), indoor_temp=19.0)
    cmd, mem = lagging_room(NOW, LONG_RUN, unit=held_up)
    cmd, mem = lagging_room(NOW + 1200, mem, unit=held_up)
    assert (cmd.action, cmd.delivers, mem.offset_k, mem.boiler_helps) == ("none", False, None, True)


def test_following_the_rooms_a_running_unit_near_its_setpoint_does_not_take_the_room_back_from_the_boiler():
    """Its room warmed by the radiator brings it near its setpoint, but it runs: the boiler goes on helping."""
    running = replace(heat_pump_state(intake=(21.5, 45.0)), indoor_temp=21.5)
    helped = replace(LONG_RUN, boiler_helps=True, lagging_since_ts=NOW - 2400, last_offset_review_ts=NOW - 1200)
    cmd, mem = lagging_room(NOW, helped, unit=running)
    assert (cmd.action, cmd.delivers, mem.offset_k, mem.boiler_helps) == ("none", False, None, True)


def test_following_the_rooms_a_room_that_is_dried_does_not_set_the_unit_higher():
    """Its window may be open and its target is raised: cold and not rising says nothing about the unit."""
    mem = replace(LONG_RUN, lagging_since_ts=NOW - 3600)
    dried = [rr(temp=19.0, target=22.0, trend=-1.0, drying=True)]
    cmd, mem = decide(inp(dried, mode="active", heat_pump=HOLDS_BACK, params=FIXED), mem)
    assert cmd.delivers and mem.offset_k is None and mem.lagging_since_ts is None


def test_following_the_rooms_only_a_real_run_is_judged_and_a_new_run_starts_clean():
    cmd, mem = lagging_room(NOW, replace(LONG_RUN, lagging_since_ts=NOW - 3600), mode="shadow")
    assert cmd.delivers and mem.offset_k is None and not mem.boiler_helps
    helped = replace(LONG_RUN, boiler_helps=True, lagging_since_ts=NOW - 3600, offset_k=3.0)
    cmd, mem = decide(inp([resting(temp=20.0, target=17.0)], mode="active", heat_pump=HOLDS_BACK, params=FIXED, cheaper=True), helped)
    assert cmd.action == "stop" and not mem.boiler_helps and mem.lagging_since_ts is None
    assert mem.offset_k == 3.0  # what the rooms asked for is kept for the next run


def test_without_fixed_setpoint_the_lever_stays():
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=off_heat_pump(intake=(18.0, 45.0))), ControlMemory())
    assert cmd.target_temp == 19.0 and cmd.delivers is True


def test_active_control_takes_over_a_unit_found_heating():
    at_20 = replace(heat_pump_state(), target_temp=20.0)
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=at_20, params=FIXED), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "adjust", 22.0, None)
    assert mem.running_since_ts == NOW and mem.last_target == 22.0 and mem.last_command_ts == NOW


def test_a_unit_taken_over_is_not_sent_the_setpoint_it_shows_and_a_change_by_hand_after_it_counts():
    """Seen 2026-10-13: after each of three restarts an "adjust" to exactly what the unit had, and the lift clock began anew."""
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=heat_pump_state(), params=FIXED), ControlMemory())
    assert (cmd.plan, cmd.action, cmd.target_temp) == ("heat", "none", 22.0)
    assert (mem.running_since_ts, mem.last_target, mem.last_command_ts) == (NOW, 22.0, NOW)  # as if sent now
    turned_up = replace(heat_pump_state(), target_temp=25.0, setting_change=SettingChange(NOW + 300, "person"))
    cmd, mem = decide(inp([rr(temp=19.0)], mode="active", heat_pump=turned_up, params=FIXED, now=NOW + 360), mem)
    assert (cmd.plan, cmd.reason, mem.manual_by) == ("hands_off", "manual_takeover", "person")


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
    restarted = replace(heat_pump_state(), settings_watched_since_ts=NOW - 60)
    for params in (PARAMS, FIXED):
        cmd, mem = decide(inp(unknown, mode="active", heat_pump=restarted, params=params), long_run)
        assert (cmd.plan, cmd.action, cmd.waiting) == ("heat", "none", "room_data")
        assert mem == long_run


def test_a_restart_counts_although_the_watch_on_the_unit_was_carried_across_it():
    """Seen 2026-10-13: the bathroom had no temperature yet, the other room's setback became the setpoint, 21 -> 18.5 °C."""
    long_run = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    night = replace(rr(key="schlafzimmer", temp=20.0, target=18.5), target_reason="schedule_setback")
    rooms = [rr(key="badezimmer", temp=None), night]
    watched_for_hours = replace(heat_pump_state(), settings_watched_since_ts=NOW - 7200)
    restarted = replace(inp(rooms, mode="active", heat_pump=watched_for_hours, params=FIXED, cheaper=True), started_ts=NOW - 5)
    cmd, mem = decide(restarted, long_run)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.waiting) == ("heat", "none", 22.0, "room_data")
    assert mem == long_run
    cmd, _ = decide(replace(restarted, started_ts=NOW - 660), long_run)  # a sensor that stays silent is not waited for
    assert cmd.waiting != "room_data"


def test_a_room_sensor_that_stays_silent_does_not_keep_the_unit_on_for_good():
    long_run = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=21.0)
    rooms = [rr(temp=23.0), rr(key="flur", temp=None)]  # one room 2 K above its target, one that never reports
    watched = replace(heat_pump_state(), target_temp=21.0, settings_watched_since_ts=NOW - 540)
    cmd, _ = decide(inp(rooms, mode="active", advice="boiler", heat_pump=watched), long_run)
    assert cmd.waiting == "room_data"  # nine minutes after the restart: still waiting
    watched = replace(watched, settings_watched_since_ts=NOW - 660)
    cmd, _ = decide(inp(rooms, mode="active", advice="boiler", heat_pump=watched), long_run)
    assert (cmd.action, cmd.reason) == ("stop", "gas_cheaper")


def test_a_blocker_still_stops_a_running_unit_without_room_data():
    long_run = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)
    cmd, _ = decide(inp([rr(temp=None)], mode="active", heat_pump=heat_pump_state(), outdoor=-12.0), long_run)
    assert cmd.action == "stop"


def test_a_unit_that_is_off_is_not_started_without_room_data_in_lever_mode():
    cmd, _ = decide(inp([rr(temp=None)]), ControlMemory())
    assert cmd.plan == "off" and cmd.action == "none"


# ---- drying a room: the strongest fan, and afterwards the stage the unit had
MIDEA_FANS = ("silent", "low", "medium", "high", "full", "auto")
AT_23 = replace(RUNNING, last_target=23.0)  # following the rooms: the drying target of 22 °C plus one degree


def _unit(fan: str | None, fans=MIDEA_FANS):
    return replace(heat_pump_state(), target_temp=23.0, fan_mode=fan, fan_modes=fans)  # as last commanded


def _drying(fan: str | None, mem: ControlMemory, *, drying=True, now=NOW, fans=MIDEA_FANS, temp=24.0):
    """A bathroom at 24 °C, past the ceiling for the heat of a drying at 22 °C: the fan still matters."""
    room = rr(temp=temp, target=22.0, drying=drying)
    return decide(inp([room], mode="active", heat_pump=_unit(fan, fans), params=FIXED, cheaper=True, now=now), mem)


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


def test_after_a_drying_the_fan_gets_the_stage_somebody_had_chosen():
    cmd, mem = _drying("silent", AT_23)
    assert (cmd.fan_mode, mem.fan_turned_up_from) == ("full", "silent")
    cmd, mem = _drying("full", mem, drying=False, now=NOW + 1800)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "silent")


def test_a_stage_chosen_during_the_drying_is_left_alone_afterwards():
    _, mem = _drying("auto", AT_23)
    cmd, mem = _drying("medium", mem, drying=False, now=NOW + 1800)  # turned down by remote meanwhile
    assert (cmd.action, mem.fan_turned_up_from) == ("none", None)
    cmd, _ = _drying("medium", mem, drying=False, now=NOW + 1860)
    assert cmd.action == "none"


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


def test_a_unit_without_a_known_top_stage_keeps_its_fan():
    cmd, mem = _drying("2", AT_23, fans=("1", "2", "3"))
    assert (cmd.action, mem.fan_turned_up_from) == ("none", None)


def test_a_fan_already_at_the_top_stays_there():
    cmd, mem = _drying("full", AT_23)
    assert (cmd.action, mem.fan_turned_up_from) == ("none", "full")
    cmd, mem = _drying("full", mem, drying=False, now=NOW + 1800)
    assert (cmd.action, mem.fan_turned_up_from) == ("none", None)


def test_a_unit_started_for_a_drying_starts_with_the_strongest_fan():
    room = rr(temp=21.0, target=22.0, drying=True)
    unit = replace(off_heat_pump(), fan_mode="auto", fan_modes=MIDEA_FANS)
    cmd, mem = decide(inp([room], mode="active", heat_pump=unit, params=FIXED), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("start", "full", "auto")


def test_a_quick_heat_up_during_a_drying_does_not_turn_the_fan_down():
    room = rr(temp=20.0, target=22.0, drying=True, boost=True)
    turned_up = replace(AT_23, fan_turned_up_from="auto", fan_turned_up_to="full")  # by the drying
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full"), params=FIXED, cheaper=True), turned_up)
    assert (cmd.action, cmd.fan_mode) == ("none", "full")  # not down to the quick heat-up's "high"
    assert (mem.fan_turned_up_from, mem.fan_turned_up_to) == ("auto", "full")  # nothing is put back in the middle of it


def test_a_drying_sends_the_raised_target_and_the_fan_together_however_recent_the_last_command():
    at_22 = replace(RUNNING, last_target=22.0, last_command_ts=NOW - 300)  # a setpoint went out five minutes ago
    unit = replace(heat_pump_state(), target_temp=22.0, fan_mode="auto", fan_modes=MIDEA_FANS)
    room = rr(temp=21.0, target=22.0, drying=True)  # the drying raised the room's target: the unit is to get 23 °C
    cmd, mem = decide(inp([room], mode="active", heat_pump=unit, params=FIXED, cheaper=True), at_22)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 23.0, "full")
    assert (mem.last_target, mem.fan_turned_up_from) == (23.0, "auto")


def test_after_a_quick_heat_up_during_a_drying_the_fan_gets_back_what_it_had_before_both():
    unit = replace(off_heat_pump(), fan_mode="auto", fan_modes=MIDEA_FANS)
    both = rr(temp=20.0, target=22.0, drying=True, boost=True)
    cmd, mem = decide(inp([both], mode="active", heat_pump=unit, params=FIXED, cheaper=True), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("start", "high", "auto")
    cmd, mem = _drying("high", mem, temp=21.0, now=NOW + 1200)  # the quick heat-up is over, the drying goes on
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("adjust", "full", "auto")
    cmd, mem = _drying("full", mem, drying=False, temp=21.0, now=NOW + 3600)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "auto")  # not the "high" of the quick heat-up


# ---- a quick heat-up: the fan goes up at once and comes back, whether the setpoint moves or not
AT_22_FOR_HOURS = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=22.0)


def test_following_the_rooms_a_quick_heat_up_raises_the_fan_of_a_unit_that_runs_already():
    at_22 = replace(AT_22_FOR_HOURS, last_command_ts=NOW - 60)
    unit = replace(heat_pump_state(), fan_mode="auto", fan_modes=MIDEA_FANS)
    cmd, mem = decide(inp([rr(temp=20.0, boost=True)], mode="active", heat_pump=unit, params=FIXED, cheaper=True), at_22)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 22.0, "high")  # the setpoint is as it was
    assert mem.fan_turned_up_from == "auto"


def test_following_the_rooms_the_fan_comes_back_once_the_quick_heat_up_is_over():
    boosted = replace(AT_22_FOR_HOURS, last_command_ts=NOW - 2700, fan_turned_up_from="auto", fan_turned_up_to="high")
    high = replace(heat_pump_state(), fan_mode="high", fan_modes=MIDEA_FANS)
    cmd, mem = decide(inp([rr(temp=20.8)], mode="active", heat_pump=high, params=FIXED, cheaper=True), boosted)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 22.0, "auto")
    back = replace(high, fan_mode="auto")
    cmd, mem = decide(inp([rr(temp=20.8)], mode="active", heat_pump=back, params=FIXED, cheaper=True, now=NOW + 60), mem)
    assert (cmd.action, mem.fan_turned_up_from) == ("none", None)


def test_a_fan_stage_chosen_by_hand_stays_when_the_setpoint_changes():
    """Seen 2026-10-13: "full" chosen by hand stayed for 45 minutes, until a setpoint of ours took "auto" along."""
    full = replace(heat_pump_state(), fan_mode="full", fan_modes=MIDEA_FANS, settings_watched_since_ts=NOW - 86400)
    night = [replace(rr(temp=20.0, target=17.0), target_reason="schedule_setback")]
    cmd, mem = decide(inp(night, mode="active", heat_pump=full, params=FIXED, cheaper=True), AT_22_FOR_HOURS)
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode) == ("heat", "adjust", 18.0, None)
    assert mem.manual_until_ts is None and mem.fan_turned_up_from is None  # no change by hand, nothing to put back
    # turned down by hand while a drying had it turned up: left as it is, also with a setpoint going out
    medium, dried = replace(full, fan_mode="medium"), replace(AT_22_FOR_HOURS, fan_turned_up_from="auto")
    cmd, mem = decide(inp(night, mode="active", heat_pump=medium, params=FIXED, cheaper=True), dried)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode, mem.fan_turned_up_from) == ("adjust", 18.0, None, None)


def test_a_unit_that_shows_no_fan_stage_is_asked_once_for_the_turn_up_and_once_for_the_turn_back():
    actions = []
    mem = AT_22_FOR_HOURS
    for minute, boost in ((0, True), (1, True), (30, True), (31, False), (32, False), (60, False)):
        rooms = [rr(temp=20.0 if boost else 20.8, boost=boost)]
        at = NOW + minute * 60
        cmd, mem = decide(inp(rooms, mode="active", heat_pump=heat_pump_state(), params=FIXED, cheaper=True, now=at), mem)
        actions.append((cmd.action, cmd.fan_mode))
    assert actions[:3] == [("adjust", "high"), ("none", "high"), ("none", "high")]
    assert actions[3:] == [("adjust", "auto"), ("none", None), ("none", None)]  # asked once: the fan is the unit's again


def test_a_quick_heat_up_that_reaches_its_target_inside_the_minimum_run_goes_down_to_eco():
    cmd, mem = decide(inp([rr(temp=20.0, boost=True)], mode="active"), ControlMemory())
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("start", 30.0, "high")
    full_power = replace(heat_pump_state(), target_temp=30.0, fan_mode="high", fan_modes=MIDEA_FANS)
    cmd, mem = decide(inp([rr(temp=21.4, boost=True)], mode="active", heat_pump=full_power, now=NOW + 300), mem)
    # five minutes into the minimum run and after the last command: no waiting at 30 °C
    assert (cmd.plan, cmd.action, cmd.target_temp, cmd.fan_mode, cmd.waiting) == ("heat", "adjust", 21.0, "auto", "min_run_time")
    assert mem.last_target == 21.0


def test_what_the_fan_is_to_get_back_survives_a_restart_and_ends_with_a_stop():
    turned_up = replace(AT_23, fan_turned_up_from="silent", fan_turned_up_to="full")
    restored = ControlMemory.from_storage(turned_up.to_storage())
    assert (restored.fan_turned_up_from, restored.fan_turned_up_to) == ("silent", "full")
    assert ControlMemory.from_storage({"fan_turned_up_from": 3, "fan_turned_up_to": 4}) == ControlMemory()
    room = rr(temp=24.0, target=22.0, heat_pump_allowed=False)  # window open: the unit is stopped
    long_running = replace(AT_23, running_since_ts=NOW - 7200, fan_turned_up_from="auto", fan_turned_up_to="full")
    cmd, mem = decide(inp([room], mode="active", heat_pump=_unit("full"), params=FIXED), long_running)
    assert cmd.action == "stop" and (mem.fan_turned_up_from, mem.fan_turned_up_to) == (None, None)


# ---- a stage chosen by hand is not overridden, and stages go out as the unit spells them (review 2026-10-14)
BY_HAND = replace(heat_pump_state(), target_temp=22.0, fan_modes=MIDEA_FANS, settings_watched_since_ts=NOW - 86400)


def _following(rooms, fan: str, mem: ControlMemory, now=NOW):
    return decide(inp(rooms, mode="active", heat_pump=replace(BY_HAND, fan_mode=fan), params=FIXED, cheaper=True, now=now), mem)


def test_a_quick_heat_up_does_not_turn_a_stronger_stage_chosen_by_hand_down():
    """"full" chosen by hand went down to "high" at once."""
    cmd, mem = _following([rr(temp=20.0, boost=True)], "full", replace(AT_22_FOR_HOURS, last_command_ts=NOW - 60))
    assert (cmd.action, cmd.fan_mode, cmd.load) == ("none", None, "boost")
    assert (mem.fan_turned_up_from, mem.fan_turned_up_to) == (None, None)  # nothing to put back afterwards
    cmd, mem = _following([rr(temp=20.8)], "full", mem, now=NOW + 1800)  # the quick heat-up is over
    assert (cmd.action, cmd.fan_mode) == ("none", None)


def test_a_stage_chosen_during_a_quick_heat_up_is_not_put_back():
    """"full" chosen during a quick heat-up was taken for its own stage, and "auto" was sent at its end."""
    boost, over = [rr(temp=20.0, boost=True)], [rr(temp=20.8)]
    cmd, turned_up = _following(boost, "auto", AT_22_FOR_HOURS)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "high")
    assert (turned_up.fan_turned_up_from, turned_up.fan_turned_up_to) == ("auto", "high")
    cmd, mem = _following(over, "full", turned_up, now=NOW + 1800)  # chosen by hand, and the quick heat-up ends
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == ("none", None, None, None)
    cmd, mem = _following(boost, "full", turned_up, now=NOW + 900)  # chosen while it still runs: not sent back to "high"
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from) == ("none", None, None)
    cmd, mem = _following(over, "full", mem, now=NOW + 1800)
    assert (cmd.action, cmd.fan_mode) == ("none", None)


def test_a_quick_heat_up_stage_chosen_during_a_drying_is_not_put_back():
    """"high" chosen during a drying was taken for the quick heat-up's stage, and "auto" was sent at its end."""
    _, mem = _drying("auto", AT_23)
    assert (mem.fan_turned_up_from, mem.fan_turned_up_to) == ("auto", "full")
    cmd, mem = _drying("high", mem, drying=False, now=NOW + 1800)
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == ("none", None, None, None)


def test_a_unit_that_spells_its_stages_with_capitals_gets_them_as_it_spells_them():
    """It never got "auto" back after a drying that began with a start: a stage it does not offer is not sent."""
    fans = ("Auto", "Low", "Medium", "High", "Turbo")
    off = replace(off_heat_pump(), fan_mode="Auto", fan_modes=fans)
    cmd, mem = decide(inp([rr(temp=21.0, target=22.0, drying=True)], mode="active", heat_pump=off, params=FIXED), ControlMemory())
    assert (cmd.action, cmd.fan_mode, mem.fan_turned_up_from, mem.fan_turned_up_to) == ("start", "Turbo", "Auto", "Turbo")
    turbo = replace(heat_pump_state(), target_temp=23.0, fan_mode="Turbo", fan_modes=fans)
    dried = inp([rr(temp=22.5, target=22.0)], mode="active", heat_pump=turbo, params=FIXED, cheaper=True, now=NOW + 3600)
    cmd, mem = decide(dried, mem)
    assert (cmd.action, cmd.fan_mode) == ("adjust", "Auto")
    cmd, _ = decide(inp([rr(temp=19.0)], mode="active", heat_pump=off, params=FIXED), ControlMemory())
    assert cmd.fan_mode == "Auto"  # a start sets the fan in the unit's spelling
    cmd, _ = decide(inp([rr(temp=19.0, boost=True)], mode="active", heat_pump=off, params=FIXED), ControlMemory())
    assert cmd.fan_mode == "High"  # and so does a quick heat-up


def test_a_unit_without_an_auto_stage_gets_back_the_stage_it_had_before_a_drying_that_began_with_a_start():
    """It was to get "auto" back, a stage it does not offer: nothing was sent, and it stayed on its loudest stage."""
    fans = ("silent", "low", "medium", "high", "full")
    off = replace(off_heat_pump(), fan_mode="low", fan_modes=fans)
    cmd, mem = decide(inp([rr(temp=21.0, target=22.0, drying=True)], mode="active", heat_pump=off, params=FIXED), ControlMemory())
    assert (cmd.action, cmd.fan_mode) == ("start", "full")
    full = replace(heat_pump_state(), target_temp=23.0, fan_mode="full", fan_modes=fans)
    dried = inp([rr(temp=22.5, target=22.0)], mode="active", heat_pump=full, params=FIXED, cheaper=True, now=NOW + 3600)
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
    _, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=heat_pump_state()), LONG_RUN)
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
    cmd, mem = decide(inp(resting_room, mode="active", heat_pump=heat_pump_state(), params=FIXED), carried)
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
    no_fan_only = replace(heat_pump_state(), hvac_modes=("off", "heat", "cool"))
    cmd, mem = decide(inp([rr(temp=21.5)], mode="active", heat_pump=no_fan_only), LONG_RUN)
    assert (cmd.action, cmd.hvac_mode, mem.fan_until_ts, mem.last_hvac_mode) == ("stop", "off", None, "off")
    with_fan_only = replace(heat_pump_state(), hvac_modes=("off", "heat", "fan_only"))
    cmd, _ = decide(inp([rr(temp=21.5)], mode="active", heat_pump=with_fan_only), LONG_RUN)
    assert (cmd.action, cmd.hvac_mode) == ("stop", "fan_only")


# ---- a unit that takes whole degrees ---------------------------------------------------------------
def test_a_unit_with_whole_degree_setpoints_is_sent_whole_degrees():
    whole = replace(off_heat_pump(intake=(20.3, 45.0)), target_temp_step=1.0)
    cmd, _ = decide(inp([rr(temp=19.0)], heat_pump=whole), ControlMemory())
    assert cmd.target_temp == 21.0  # intake 20.3 °C + 1 K: in half degrees it would be 21.5 °C
    cmd, mem = decide(inp([rr(temp=20.0, target=20.5)], heat_pump=whole, params=FIXED, cheaper=True), ControlMemory())
    assert cmd.target_temp == 22.0 and mem.last_target == 22.0  # 21.5 °C: halves go up, as the unit rounds them


def test_a_setpoint_the_unit_rounded_to_its_step_is_not_a_command_it_ignored():
    sent_before = replace(RUNNING, running_since_ts=NOW - 7200, last_command_ts=NOW - 7200, last_target=21.5)
    shows_22 = replace(heat_pump_state(), target_temp=22.0, target_temp_step=1.0, settings_watched_since_ts=NOW - 86400)
    rooms = [rr(temp=20.0, target=20.5)]
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=shows_22, params=FIXED, cheaper=True), sent_before)
    assert mem.not_carried_out == 0 and cmd.reason != "not_carried_out"
    half_degrees = replace(shows_22, target_temp_step=0.5)  # a unit that could show 21.5 °C did not follow
    _, mem = decide(inp(rooms, mode="active", heat_pump=half_degrees, params=FIXED, cheaper=True), sent_before)
    assert mem.not_carried_out == 1


# ---- learning runs, the eco offset, blockers ---------------------------------------------------------
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
    assert mem.learning_until_ts == NOW + 3600 and mem.learning_bins == ()
    cmd, _ = decide(inp([rr()], mode="shadow", advice="boiler", cop=2.6, bin_count=0, now=NOW + 1800), mem)
    assert (cmd.plan, cmd.load) == ("heat", "learning")


def test_a_room_held_just_below_its_target_does_not_raise_the_eco_offset():
    mem = replace(RUNNING, running_since_ts=NOW - 2000, last_command_ts=NOW - 2000, last_target=21.0)
    held = [rr(temp=20.9, trend=0.05)]  # inside the band that keeps a run going: held, not warmed
    _, new = decide(inp(held, mode="active", heat_pump=replace(heat_pump_state(), target_temp=21.0)), mem)
    assert new.offset_k is None and new.last_offset_review_ts is None


def test_the_safety_fallback_is_named_once_among_the_blockers():
    cmd, _ = decide(inp([rr()], safety="fallback", blocked="safety_fallback"), ControlMemory())
    assert cmd.blockers == ("safety_fallback",)


# ---- switched by the rooms (not following them): cheaper heat is worth running, called for or not
def test_a_unit_switched_by_the_rooms_heats_on_once_the_room_no_longer_calls_the_boiler():
    """The recommendation names a source only while a room calls; the run ended there as "gas cheaper"."""
    almost = rr(temp=20.95, target=21.0)  # inside the band: no call any more, and not yet 0.2 K above
    cmd, _ = decide(inp([almost], mode="active", heat_pump=heat_pump_state(), advice="none", cheaper=True), LONG_RUN)
    assert (cmd.plan, cmd.reason) == ("heat", "cheaper_than_gas") and cmd.action != "stop"
    reached = rr(temp=21.25, target=21.0)
    cmd, _ = decide(inp([reached], mode="active", heat_pump=heat_pump_state(), advice="none", cheaper=True), LONG_RUN)
    assert (cmd.action, cmd.reason) == ("stop", "target_reached")


def test_a_unit_switched_by_the_rooms_starts_for_a_cold_room_that_calls_no_boiler():
    """A radiator thermostat switched off by hand: the room never calls, and the heat pump could heat it alone."""
    cold = rr(temp=19.0, target=21.0)
    cmd, _ = decide(inp([cold], mode="active", advice="none", cheaper=True), ControlMemory())
    assert (cmd.action, cmd.reason) == ("start", "cheaper_than_gas")
    cmd, _ = decide(inp([cold], mode="active", advice="none", cheaper=False), ControlMemory())
    assert cmd.action == "none" and cmd.plan == "off"  # neither cheaper nor recommended: it stays off


def test_cheaper_heat_does_not_get_past_what_blocks_the_unit():
    cold = rr(temp=19.0, target=21.0)
    for blocked in ({"op": "off"}, {"op": "boiler_only"}, {"blocked": "icing_lockout_active"}, {"safety": "fallback"}):
        cmd, _ = decide(inp([cold], mode="active", advice="none", cheaper=True, **blocked), ControlMemory())
        assert cmd.action == "none" and cmd.plan == "off", blocked



def test_following_the_rooms_rooms_clearly_too_warm_take_the_setpoint_back_at_the_pace_it_went_up():
    """Seen 2026-10-13: 1.7 h at the raised setpoint with every served room 1-2 K above its target."""
    raised = replace(LONG_RUN, offset_k=2.0, last_offset_review_ts=NOW - 1300, last_target=23.0)
    at_23 = replace(heat_pump_state(intake=(22.0, 45.0)), target_temp=23.0)
    cmd, mem = lagging_room(NOW, raised, unit=at_23, trend=0.0, temp=21.2)  # at its target only: two hours
    assert cmd.action == "none" and mem.offset_k == 2.0
    cmd, mem = lagging_room(NOW, raised, unit=at_23, trend=0.0, temp=22.0)  # 1 K above it: 20 minutes
    assert (cmd.action, cmd.target_temp, mem.offset_k, mem.last_offset_review_ts) == ("adjust", 22.5, 1.5, NOW)
    cmd, mem = lagging_room(NOW + 600, mem, unit=replace(at_23, target_temp=22.5), trend=0.0, temp=22.0)
    assert mem.offset_k == 1.5  # the next one in 20 minutes, not at once


def test_following_the_rooms_a_drying_sets_the_unit_above_its_own_reading_so_that_it_runs():
    """Seen 2026-10-18: 22.5 °C read where the unit stands, 22 °C set for the bathroom being dried: it stood, fan and all."""
    warm_study = replace(heat_pump_state(compressor_hz=0.0, running_s=None), indoor_temp=22.5, target_temp=23.0,
                         fan_mode="auto", fan_modes=MIDEA_FANS)
    wet = [rr(temp=21.0, target=22.0, drying=True)]  # below its ceiling: the drying still asks for heat
    cmd, mem = decide(inp(wet, mode="active", heat_pump=warm_study, params=FIXED, cheaper=True), AT_23)
    assert (cmd.action, cmd.target_temp, cmd.fan_mode) == ("adjust", 23.5, "full")  # its reading plus one degree
    # never more than two degrees above the drying target, however warm the unit's room
    hot_study = replace(warm_study, indoor_temp=23.8)
    cmd, _ = decide(inp(wet, mode="active", heat_pump=hot_study, params=FIXED, cheaper=True), AT_23)
    assert cmd.target_temp == 24.0
    # no drying: the rooms' setpoint as before
    cmd, _ = decide(inp([rr(temp=21.0, target=22.0)], mode="active", heat_pump=warm_study, params=FIXED, cheaper=True), AT_23)
    assert cmd.target_temp == 23.0


def test_following_the_rooms_a_drying_does_not_make_the_unit_look_flat_out_and_call_the_boiler_for_another_room():
    """22.3 °C read: the drying's lead gave 23.5 °C, and the unit read more than a degree below that looked flat out."""
    unit = replace(heat_pump_state(), indoor_temp=22.3, target_temp=23.5, fan_mode="full", fan_modes=MIDEA_FANS)
    mem = replace(LONG_RUN, last_target=23.5, lagging_since_ts=NOW - 1200, fan_turned_up_from="auto", fan_turned_up_to="full")
    rooms = [
        rr(key="badezimmer", temp=21.0, target=22.0, drying=True),
        rr(key="schlafzimmer", temp=19.0, target=20.0, trend=0.1),  # lags for 20 minutes
    ]
    cmd, mem = decide(inp(rooms, mode="active", heat_pump=unit, params=FIXED, cheaper=True), mem)
    assert cmd.delivers and not mem.boiler_helps and mem.offset_k == 1.5  # set higher, as without the drying
