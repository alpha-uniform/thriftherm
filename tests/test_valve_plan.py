"""What Thriftherm writes to a valve it drives directly, besides its setpoint (docs/PHASE13_VALVES.md §3, §5.2, §10).

Pure rules. The setpoint of the room's first valve goes the way of a room thermostat's (engines/room_control.py); this
is the rest: the room temperature into the valve's input (way a), its calibration offset (way b), the sensor selection,
and the setpoint of a room's further valves.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import valve_plan as vp
from custom_components.thriftherm.engines.valve_caps import ValveCaps, ValveQuirks, quirks_of
from custom_components.thriftherm.engines.valve_control import Correction
from custom_components.thriftherm.engines.valve_plan import ValveMemory, ValveView

NOW = 2_000_000.0
MIN = 60.0
V = "heizkorper_badezimmer"
TRVZB = ValveCaps(
    climate=f"climate.{V}",
    external_temperature=f"number.{V}_external_temperature_input",
    sensor_select=f"select.{V}_temperature_sensor_select",
    remote_option="remote_temperature",
    local_option="local_temperature",
    offset=f"number.{V}_local_temperature_calibration",
    child_lock=f"switch.{V}_child_lock",
    window_detection=f"switch.{V}_open_window",
)
TUYA = ValveCaps(climate="climate.tuya", offset="number.tuya_local_temperature_calibration")
HOMEMATIC = ValveCaps(climate="climate.hmip")


def view(caps=TRVZB, **kw) -> ValveView:
    base = {"caps": caps, "quirks": ValveQuirks(), "selection": "remote_temperature", "external_shown": 20.4,
            "offset_shown": 0.0, "offset_limits": (-10.0, 10.0, 0.2), "shown_c": 20.4, "target_shown": 21.0}
    return ValveView(**{**base, **kw})


def writes(plan) -> dict[str, object]:
    return {w.entity_id: w.value for w in plan.writes}


# ------------------------------------------------------------------ way a: the room temperature into the valve
def test_the_room_temperature_goes_to_the_valve_and_is_remembered():
    plan, mem = vp.plan(view(), ValveMemory(), room_c=20.43, room_fresh=True, now_ts=NOW)
    assert plan.way == "external" and writes(plan) == {TRVZB.external_temperature: 20.4}
    assert (mem.external_c, mem.external_ts) == (20.4, NOW)


def test_nothing_goes_out_between_a_change_and_the_refresh():
    _, mem = vp.plan(view(), ValveMemory(), 20.4, True, NOW)
    plan, _ = vp.plan(view(), mem, 20.44, True, NOW + 5 * MIN)
    assert plan.writes == ()
    plan, _ = vp.plan(view(), mem, 20.4, True, NOW + 25 * MIN)
    assert writes(plan) == {TRVZB.external_temperature: 20.4}  # the refresh, below the TRVZB's 2 h (Phase B)


def test_a_valve_that_lost_the_value_gets_it_at_once():
    """Seen 2026-11-13: the bathroom valve came back from a new start with an external value of 0."""
    _, mem = vp.plan(view(), ValveMemory(), 20.4, True, NOW)
    plan, _ = vp.plan(view(external_shown=0.0, selection="local_temperature"), mem, 20.4, True, NOW + 2 * MIN)
    assert writes(plan) == {TRVZB.external_temperature: 20.4, TRVZB.sensor_select: "remote_temperature"}


def test_the_selection_is_set_to_the_room_unless_a_person_chose_the_valves_own_sensor():
    plan, _ = vp.plan(view(selection="local_temperature"), ValveMemory(), 20.4, True, NOW)
    assert writes(plan)[TRVZB.sensor_select] == "remote_temperature"
    plan, _ = vp.plan(view(selection="local_temperature", selection_by_person=True), ValveMemory(), 20.4, True, NOW)
    assert TRVZB.sensor_select not in writes(plan) and TRVZB.external_temperature not in writes(plan)
    assert plan.way == "offset" and plan.reason == "own_sensor_by_hand"  # §5.2: the valve goes by b until set back


def test_a_valve_that_fell_back_by_itself_is_not_switched_but_given_the_value():
    """Phase B 2026-11-14: the TRVZB shows remote_source_offline after 2 h without a value and returns 2 s after one."""
    plan, _ = vp.plan(view(selection="remote_source_offline"), ValveMemory(), 20.7, True, NOW)
    assert writes(plan) == {TRVZB.external_temperature: 20.7}


def test_without_a_fresh_room_reading_the_valve_goes_by_its_own_sensor():
    plan, _ = vp.plan(view(), ValveMemory(external_c=20.4, external_ts=NOW - 40 * MIN), None, False, NOW)
    assert plan.way == "own_sensor" and plan.reason == "room_reading_stale"
    assert writes(plan) == {TRVZB.sensor_select: "local_temperature"}  # no dead value for the valve to hold


# ------------------------------------------------------------------ way b: the calibration offset
def test_a_valve_without_an_input_gets_an_offset_of_the_room_less_its_own_reading():
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600))  # the valve reads 1 K warm
    plan, mem = vp.plan(view(TUYA, shown_c=21.4, offset_shown=0.0), settled, 20.4, True, NOW)
    assert plan.way == "offset" and writes(plan) == {TUYA.offset: -1.0}
    assert mem.correction.applied_k == 1.0


def test_the_offset_is_not_written_again_while_the_valve_has_it():
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600, applied_k=1.0, applied_ts=NOW - 3600))
    plan, _ = vp.plan(view(TUYA, shown_c=20.4, offset_shown=-1.0), settled, 20.4, True, NOW)  # its own 21.4 less 1
    assert plan.writes == ()


def test_an_offset_stays_while_the_room_reading_is_stale():
    """§3.1: the offset left standing errs by what the difference drifts, the gentlest way to fail."""
    plan, _ = vp.plan(view(TUYA, offset_shown=-1.0), ValveMemory(), None, False, NOW)
    assert plan.writes == () and plan.way == "offset"


def test_an_inverted_offset_goes_out_with_its_sign():
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600))
    plan, _ = vp.plan(view(TUYA, quirks=ValveQuirks(offset_inverted=True), shown_c=21.4), settled, 20.4, True, NOW)
    assert writes(plan) == {TUYA.offset: 1.0}


# ------------------------------------------------------------------ what is never written
@pytest.mark.parametrize("caps", [TRVZB, TUYA, HOMEMATIC])
def test_the_child_lock_and_the_valves_window_detection_are_never_written(caps):
    """Decided 2026-11-13: what is set at the valve itself is the truth. Thriftherm only reads it (§5.2)."""
    for state in (view(caps), view(caps, selection="local_temperature"), view(caps, selection=None, offset_shown=None)):
        plan, _ = vp.plan(state, ValveMemory(), 20.4, True, NOW)
        assert all(w.domain in ("number", "select", "climate", "switch") for w in plan.writes)
        assert not {w.entity_id for w in plan.writes} & {TRVZB.child_lock, TRVZB.window_detection}


def test_a_valve_that_cannot_be_reached_gets_nothing():
    plan, mem = vp.plan(view(available=False), ValveMemory(), 20.4, True, NOW)
    assert plan.writes == () and plan.way == "unavailable" and mem == ValveMemory()


# ------------------------------------------------------------------ further valves of the room, remembered values
def test_a_further_valve_of_the_room_gets_the_rooms_setpoint_at_most_every_five_minutes():
    plan, mem = vp.plan(view(target_shown=20.0), ValveMemory(), 20.4, True, NOW, setpoint=21.0)
    assert writes(plan)[TRVZB.climate] == 21.0
    plan, _ = vp.plan(view(target_shown=20.0), mem, 20.4, True, NOW + 2 * MIN, setpoint=21.0)
    assert TRVZB.climate not in writes(plan)
    plan, _ = vp.plan(view(target_shown=21.0), mem, 20.4, True, NOW + 6 * MIN, setpoint=21.0)
    assert TRVZB.climate not in writes(plan)  # it has it


def test_the_valves_own_settings_are_remembered_on_first_sight_to_be_given_back():
    """§7: when Thriftherm leaves, the valve gets back its selection and its offset."""
    _, mem = vp.plan(view(selection="local_temperature", offset_shown=-0.6), ValveMemory(), 20.4, True, NOW)
    assert (mem.selection_before, mem.offset_before) == ("local_temperature", -0.6)
    _, mem = vp.plan(view(selection="remote_temperature", offset_shown=0.0), mem, 20.4, True, NOW + MIN)
    assert (mem.selection_before, mem.offset_before) == ("local_temperature", -0.6)


def test_the_memory_is_stored_and_a_damaged_one_is_empty():
    mem = ValveMemory(external_c=20.4, external_ts=NOW, correction=Correction(ema_k=1.0, ema_ts=NOW),
                      selection_before="local_temperature", offset_before=-0.6, setpoint_ts=NOW)
    assert ValveMemory.from_storage(mem.to_storage()) == mem
    assert ValveMemory.from_storage({"external_c": "x", "selection_before": 5}) == ValveMemory()
    assert ValveMemory.from_storage(None) == ValveMemory()
    assert replace(mem, external_c=None).external_c is None


# ------------------------------------------------------------------ the window (§5.1), through the room's setpoint
def _decide(result, mem=None, thermostat=21.0, now=NOW, **kw):
    from custom_components.thriftherm.const import CTRL_ACTIVE
    from custom_components.thriftherm.engines import room_control
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    return room_control.decide(
        "bad", CTRL_ACTIVE, result, True, "heat", thermostat, now, mem or RoomCtrlMemory(), **kw
    )


def _rr(window_open=False):
    from .conftest import room_result

    return room_result("bad", 21.0, 20.6, target_reason="schedule_comfort", deviation_k=-0.4,
                       heating_allowed=not window_open, window_open=window_open)


def test_an_open_window_takes_the_valve_down_to_the_frost_limit_and_back():
    """A valve driven directly has no Better Thermostat to shut it while the window is open."""
    cmd, mem = _decide(_rr(window_open=True), hold_c=7.0, lead_k=0.5)
    assert cmd.send and cmd.target == 7.0 and cmd.reason == "window_open_frost" and mem.sent_extra_k == 0.0
    cmd, mem = _decide(_rr(window_open=True), mem, thermostat=7.0, now=NOW + 30, hold_c=7.0)
    assert cmd.plan == "hold" and cmd.reason == "in_sync"
    cmd, _ = _decide(_rr(), mem, thermostat=7.0, now=NOW + 120)
    assert cmd.send and cmd.target == 21.0


def test_the_frost_limit_stays_within_what_the_valve_takes():
    cmd, _ = _decide(_rr(window_open=True), hold_c=4.0, min_temp=5.0)
    assert cmd.target == 5.0


# ------------------------------------------------------------------ review 2026-11-15
def test_a_new_room_setpoint_reaches_a_further_valve_at_once_a_repeat_waits():
    """Review 2026-11-15 (2): the window opened two minutes after a change, and the second valve heated on to five."""
    _, mem = vp.plan(view(target_shown=20.0), ValveMemory(), 20.4, True, NOW, setpoint=21.0)
    plan, mem = vp.plan(view(target_shown=21.0), mem, 20.4, True, NOW + 2 * MIN, setpoint=7.0)
    assert writes(plan)[TRVZB.climate] == 7.0
    plan, _ = vp.plan(view(target_shown=21.0), mem, 20.4, True, NOW + 3 * MIN, setpoint=7.0)
    assert TRVZB.climate not in writes(plan)  # the same one again only after five minutes


def test_only_a_valve_showing_no_value_has_lost_it():
    """Review 2026-11-15 (3): a valve rounding to its own step (20.3 written, 20.5 shown) was written every minute."""
    _, mem = vp.plan(view(), ValveMemory(), 20.3, True, NOW)
    plan, _ = vp.plan(view(external_shown=20.5), mem, 20.3, True, NOW + 2 * MIN)
    assert plan.writes == ()


def test_a_valve_that_fell_back_gets_the_value_at_once():
    """Review 2026-11-15 (9): without a change due it stayed on its own sensor up to the refresh."""
    _, mem = vp.plan(view(), ValveMemory(), 20.4, True, NOW)
    plan, _ = vp.plan(view(selection="remote_source_offline"), mem, 20.4, True, NOW + 2 * MIN)
    assert writes(plan) == {TRVZB.external_temperature: 20.4}


def test_a_choice_of_the_own_sensor_by_hand_outlasts_a_restart_and_ends_when_it_is_set_back():
    """Review 2026-11-15 (5): the context of a person is gone after a restart of Home Assistant."""
    by_hand = view(selection="local_temperature", selection_by_person=True)
    _, mem = vp.plan(by_hand, ValveMemory(), 20.4, True, NOW)
    assert mem.selection_by_hand
    plan, mem = vp.plan(view(selection="local_temperature"), mem, 20.4, True, NOW + MIN)  # restarted: no person
    assert TRVZB.sensor_select not in writes(plan) and plan.reason == "own_sensor_by_hand"
    _, mem = vp.plan(view(), mem, 20.4, True, NOW + 2 * MIN)  # set back to the room
    assert not mem.selection_by_hand
    assert ValveMemory.from_storage(replace(mem, selection_by_hand=True).to_storage()).selection_by_hand


def test_a_valve_gone_and_back_has_no_choice_by_hand_left():
    """A new start of the valve (a change of batteries) shows its own sensor without anyone choosing it."""
    _, mem = vp.plan(view(selection="local_temperature", selection_by_person=True), ValveMemory(), 20.4, True, NOW)
    _, mem = vp.plan(view(available=False), mem, 20.4, True, NOW + MIN)
    # back from its new start its input shows 0 (seen 2026-11-13); a radio gap alone keeps the choice (final review #8)
    plan, _ = vp.plan(view(selection="local_temperature", external_shown=0.0), mem, 20.4, True, NOW + 2 * MIN)
    assert writes(plan)[TRVZB.sensor_select] == "remote_temperature"


def test_a_valve_switched_off_gets_nothing():
    """Review 2026-11-15 (8): the mode at the valve is the truth (§5.2); some valves switch on with a setpoint."""
    plan, _ = vp.plan(view(mode="off", target_shown=5.0), ValveMemory(), 20.4, True, NOW, setpoint=21.0)
    assert plan.writes == () and plan.way == "off"


def test_a_sensor_switch_that_shows_nothing_is_not_written():
    """Review 2026-11-15 (10): an unavailable switch was written every cycle."""
    zha = ValveCaps(climate="climate.z", external_temperature="number.z_ext", sensor_switch="switch.z_ext")
    plan, _ = vp.plan(view(zha, selection=None), ValveMemory(external_c=20.4, external_ts=NOW), 20.4, True, NOW + MIN)
    assert plan.writes == ()
    plan, _ = vp.plan(view(zha, selection=None), ValveMemory(), None, False, NOW)
    assert plan.writes == ()


# ------------------------------------------------------------------ way c: the setpoint corrected (§3.1 c)
def test_a_valve_with_neither_input_nor_offset_gets_its_setpoint_corrected():
    """Every climate entity with a current temperature (Homematic IP, Z-Wave, Matter, tado): the fallback for all."""
    settled = ValveMemory(correction=Correction(ema_k=2.0, ema_ts=NOW - 3600))  # it reads 2 K warm
    k, mem = vp.setpoint_correction(view(HOMEMATIC, shown_c=22.6), settled, 20.6, True, 21.0, NOW)
    assert k == 2.0 and mem.correction.applied_k == 2.0


def test_a_valve_with_an_input_or_an_offset_gets_no_correction():
    settled = ValveMemory(correction=Correction(ema_k=2.0, ema_ts=NOW - 3600))
    assert vp.setpoint_correction(view(TRVZB, shown_c=22.6), settled, 20.6, True, 21.0, NOW)[0] == 0.0
    assert vp.setpoint_correction(view(TUYA, shown_c=22.6), settled, 20.6, True, 21.0, NOW)[0] == 0.0


def test_without_a_fresh_room_reading_the_setpoint_is_not_corrected():
    settled = ValveMemory(correction=Correction(ema_k=2.0, ema_ts=NOW - 3600, applied_k=2.0, applied_ts=NOW - 3600))
    assert vp.setpoint_correction(view(HOMEMATIC, shown_c=22.6), settled, None, False, 21.0, NOW)[0] == 0.0


def test_a_further_valve_on_way_c_gets_the_rooms_setpoint_with_its_own_correction():
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600))
    plan, _ = vp.plan(view(HOMEMATIC, shown_c=21.6, target_shown=21.0), settled, 20.6, True, NOW, setpoint=21.0,
                      target=21.0)
    assert writes(plan)[HOMEMATIC.climate] == 22.0


def test_the_correction_goes_out_with_the_setpoint_and_a_turn_is_taken_without_it():
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory, without_extra

    cmd, mem = _decide(_rr(), correction_k=-1.0)
    assert cmd.target == 20.0 and mem.sent_correction_k == -1.0
    cmd, mem = _decide(_rr(), mem, thermostat=20.0, now=NOW + 30, correction_k=-1.0)
    assert cmd.reason == "in_sync"
    assert without_extra(22.0, mem) == 23.0  # turned to 22 on a valve held 1 K below: 23 for the room
    assert RoomCtrlMemory.from_storage(mem.to_storage()) == mem


def test_the_lead_comes_on_top_of_the_correction_and_the_window_takes_neither():
    cmd, mem = _decide(_rr(), lead_k=0.5, correction_k=2.0)
    assert cmd.target == 23.5 and mem.sent_extra_k == 2.5 and mem.sent_correction_k == 2.0
    cmd, mem = _decide(_rr(window_open=True), mem, now=NOW + 120, hold_c=7.0, correction_k=2.0)
    assert cmd.target == 7.0 and mem.sent_correction_k == 0.0


# ------------------------------------------------------------------ way d: the opening (§3.4, a preview)
SONOFF = quirks_of("SONOFF", "TRVZB")  # its opening is a limit (opening_is_limit)
TRVZB_D = ValveCaps(**{**TRVZB.__dict__, "opening": f"number.{V}_heating_valve_position",
                       "closing": f"number.{V}_idle_valve_position"})


def test_a_room_on_its_own_controller_gets_the_opening_besides_the_room_temperature():
    plan, mem = vp.plan(view(TRVZB_D), ValveMemory(), 20.0, True, NOW, opening=81.0)
    assert writes(plan) == {TRVZB.external_temperature: 20.0, TRVZB_D.opening: 81.0}
    assert (mem.opening_pct, mem.opening_ts) == (81.0, NOW) and plan.way == "opening"


def test_the_idle_position_is_never_written_it_is_the_net():
    """With its setpoint on the room's target the valve shuts on its own once the room is there (§3.4)."""
    plan, _ = vp.plan(view(TRVZB_D), ValveMemory(), 20.0, True, NOW, opening=0.0)
    assert TRVZB_D.closing not in writes(plan)


def test_a_trvzb_is_never_shut_below_three_percent():
    plan, _ = vp.plan(view(TRVZB_D, quirks=ValveQuirks(min_opening_pct=3.0)), ValveMemory(), 22.0, True, NOW, opening=0.0)
    assert writes(plan)[TRVZB_D.opening] == 3.0


def test_the_opening_waits_its_cycle():
    _, mem = vp.plan(view(TRVZB_D), ValveMemory(), 20.0, True, NOW, opening=81.0)
    plan, _ = vp.plan(view(TRVZB_D), mem, 20.0, True, NOW + 2 * MIN, opening=40.0)
    assert TRVZB_D.opening not in writes(plan)


def test_a_valve_without_an_opening_ignores_the_wish():
    plan, _ = vp.plan(view(), ValveMemory(), 20.0, True, NOW, opening=81.0)
    assert plan.way == "external" and TRVZB_D.opening not in writes(plan)


def test_the_opening_it_had_is_remembered_to_be_given_back():
    _, mem = vp.plan(view(TRVZB_D, opening_shown=100.0), ValveMemory(), 20.0, True, NOW, opening=81.0)
    assert mem.opening_before == 100.0
    assert ValveMemory.from_storage(mem.to_storage()) == mem


# ------------------------------------------------------------------ the valve exercise (§5.6)
DAY = 86400.0


def test_a_valve_not_opened_for_a_week_is_exercised_twice_open_and_shut():
    mem = ValveMemory(opened_ts=NOW - 7 * DAY)
    steps = []
    for n in range(6):
        step, mem = vp.exercise(view(hvac_action="idle"), mem, NOW + n * 2 * MIN)
        steps.append(step)
    assert steps == ["open", "shut", "open", "shut", None, None]
    assert mem.opened_ts == NOW + 6 * MIN  # done: counted as opened


def test_a_valve_that_opened_within_the_week_is_left_alone_and_its_opening_is_noted():
    step, mem = vp.exercise(view(hvac_action="heating"), ValveMemory(opened_ts=NOW - 8 * DAY), NOW)
    assert step is None and mem.opened_ts == NOW
    step, _ = vp.exercise(view(hvac_action="idle"), ValveMemory(opened_ts=NOW - 6 * DAY), NOW)
    assert step is None


def test_a_valve_first_seen_starts_counting():
    step, mem = vp.exercise(view(hvac_action="idle"), ValveMemory(), NOW)
    assert step is None and mem.opened_ts == NOW


def test_the_trvzb_is_exercised_every_84_hours():
    """A fact from Better Thermostat's model fixes (AGPL-3.0, no code), written anew."""
    trvzb = ValveQuirks(unreliable_hvac_action=True, min_opening_pct=3.0, exercise_s=84 * 3600.0)
    idle = view(quirks=trvzb, hvac_action="idle", shown_c=21.0, target_shown=21.0)  # at its setpoint: shut
    step, _ = vp.exercise(idle, ValveMemory(opened_ts=NOW - 85 * 3600), NOW)
    assert step == "open"


def test_with_an_unreliable_hvac_action_a_setpoint_above_its_reading_counts_as_open():
    trvzb = ValveQuirks(unreliable_hvac_action=True)
    _, mem = vp.exercise(view(quirks=trvzb, hvac_action="idle", shown_c=19.0, target_shown=21.0),
                         ValveMemory(opened_ts=NOW - DAY), NOW)
    assert mem.opened_ts == NOW


def test_a_valve_unavailable_or_switched_off_is_not_exercised():
    for state in (view(available=False), view(mode="off")):
        step, _ = vp.exercise(state, ValveMemory(opened_ts=NOW - 30 * DAY), NOW)
        assert step is None


def test_an_exercise_is_stored():
    mem = ValveMemory(opened_ts=NOW, exercise_step=2, exercise_ts=NOW)
    assert ValveMemory.from_storage(mem.to_storage()) == mem


def test_a_further_valve_and_one_on_its_own_controller_are_exercised_by_setpoint_and_opening():
    plan, _ = vp.plan(view(TRVZB_D, target_shown=21.0), ValveMemory(), 20.0, True, NOW, setpoint=21.0,
                      exercise="open", setpoint_limits=(5.0, 30.0), opening=10.0)
    assert writes(plan)[TRVZB.climate] == 30.0 and writes(plan)[TRVZB_D.opening] == 100.0
    plan, _ = vp.plan(view(TRVZB_D, target_shown=30.0), ValveMemory(), 20.0, True, NOW, setpoint=21.0,
                      exercise="shut", setpoint_limits=(5.0, 30.0), opening=10.0)
    assert writes(plan)[TRVZB.climate] == 5.0 and writes(plan)[TRVZB_D.opening] == 0.0


def test_an_exercise_starts_only_when_allowed_and_one_begun_runs_to_its_end():
    """The motor is heard: an exercise begins in the day only (the coordinator: 10 to 18 o'clock)."""
    due = ValveMemory(opened_ts=NOW - 8 * DAY)
    assert vp.exercise(view(hvac_action="idle"), due, NOW, may_start=False)[0] is None
    step, mem = vp.exercise(view(hvac_action="idle"), due, NOW, may_start=True)
    assert step == "open"
    assert vp.exercise(view(hvac_action="idle"), mem, NOW + 2 * MIN, may_start=False)[0] == "shut"


# ------------------------------------------------------------------ window open: off by default (2026-11-15)
def test_an_open_window_switches_the_valve_off_and_back_on():
    """Decided 2026-11-15: off, not the frost limit; else a sensor by the window at −20 °C outside fires the boiler."""
    plan, mem = vp.plan(view(mode="heat"), ValveMemory(), 20.4, True, NOW, window_off=True)
    assert [(w.service, w.value) for w in plan.writes] == [("set_hvac_mode", "off")]
    assert plan.way == "window_off" and mem.off_by_window
    plan, mem = vp.plan(view(mode="off"), mem, 20.4, True, NOW + MIN, window_off=True)
    assert plan.writes == ()
    plan, mem = vp.plan(view(mode="off"), mem, 20.4, True, NOW + 2 * MIN)
    assert ("set_hvac_mode", "heat") in [(w.service, w.value) for w in plan.writes]
    assert not mem.off_by_window
    assert vp.Write(TRVZB.climate, "climate", "set_hvac_mode", "off").data() == {
        "entity_id": TRVZB.climate, "hvac_mode": "off"
    }


def test_a_valve_a_person_switched_off_stays_off_after_the_window():
    plan, _ = vp.plan(view(mode="off"), ValveMemory(), 20.4, True, NOW)
    assert plan.writes == () and plan.way == "off"


def test_the_off_of_the_window_is_stored():
    assert ValveMemory.from_storage(ValveMemory(off_by_window=True).to_storage()).off_by_window


# ------------------------------------------------------------------ review 2026-11-15, second round
def test_an_exercise_step_lasts_longer_than_the_rate_limit_of_the_setpoint():
    """Review 2 (6): a step of 30 s with a setpoint held 60 s apart let a "shut" never go out."""
    from custom_components.thriftherm.engines.room_control import MIN_RESEND_S

    assert vp.EXERCISE_STEP_S > MIN_RESEND_S
    _, mem = vp.exercise(view(hvac_action="idle"), ValveMemory(opened_ts=NOW - 8 * DAY), NOW)
    assert vp.exercise(view(hvac_action="idle"), mem, NOW + MIN)[0] == "open"  # still the first step


def test_an_open_valve_is_noted_at_most_hourly():
    """Review 2 (8): noted every cycle, the store was written every minute."""
    step, mem = vp.exercise(view(hvac_action="heating"), ValveMemory(opened_ts=NOW - 10 * MIN), NOW)
    assert mem.opened_ts == NOW - 10 * MIN
    _, mem = vp.exercise(view(hvac_action="heating"), ValveMemory(opened_ts=NOW - 2 * 3600), NOW)
    assert mem.opened_ts == NOW


def test_an_opening_counts_as_open_only_where_thriftherm_set_it():
    """Final review 2026-11-15 #3: a TRVZB's opening is its limit, 100 % from the factory, and counted as open the
    exercise never ran. An opening Thriftherm set (way d) above the least one is the valve open."""
    _, mem = vp.exercise(view(hvac_action="idle", opening_shown=100.0), ValveMemory(opened_ts=NOW - 8 * DAY), NOW)
    assert mem.exercise_step == 1
    set_by_us = ValveMemory(opened_ts=NOW - 8 * DAY, opening_pct=40.0, opening_ts=NOW - 600)
    _, mem = vp.exercise(view(hvac_action="idle", opening_shown=40.0), set_by_us, NOW)
    assert mem.opened_ts == NOW and mem.exercise_step == 0


def test_the_lead_shown_holds_no_correction():
    cmd, _ = _decide(_rr(), lead_k=0.5, correction_k=2.0)
    assert cmd.lead_k == 0.5


# ------------------------------------------------------------------ lead and hysteresis together (2026-11-15)
@pytest.mark.parametrize(
    ("lead", "hysteresis", "on_top"),
    [
        (0.5, 0.4, 0.5),  # the lead alone opens a valve 0.4 K below its setpoint: at target + 0.1, at every call
        (0.5, 1.0, 1.0),  # a coarse valve needs its whole threshold, or it opens only at target − 0.5
        (0.0, 0.4, 0.4),  # no lead: the threshold alone
        (0.5, 0.0, 0.5),
    ],
)
def test_lead_and_hysteresis_are_not_added_the_larger_counts(lead, hysteresis, on_top):
    """Decided 2026-11-15: "doppelt gemoppelt". Target + the larger opens the valve at the target or above it at every
    call; the sum (+1 K) let a room heating along overshoot by nearly 1 K."""
    assert vp.on_top_while_calling(lead, hysteresis) == on_top


# ------------------------------------------------------------------ one setpoint rule for direct valves (review §3.4)
@pytest.mark.parametrize(
    ("wants", "hysteresis", "end_above", "on_top"),
    [
        (False, 0.4, 0.5, 0.0),  # wants no heat: the target
        (True, 0.4, -0.1, 0.4),  # the band of before ends 0.1 below: the valve's threshold holds it open
        (True, 0.0, -0.1, 0.1),  # no known threshold: just above where the call ends
        (True, 0.4, 0.5, 0.7),  # the call ends 0.5 above: the valve open past it (review §3.8, the fault in 263d6e5)
        (True, 1.0, 0.0, 1.0),
    ],
)
def test_a_direct_valve_wanting_heat_stands_safely_above_where_it_shuts(wants, hysteresis, end_above, on_top):
    """Review 2026-11-15 §3.4: will the room heat (it calls or heats along), the valve gets the target plus the largest
    of its threshold and where the call ends + 0.2, else the target. The lead of a room thermostat is not needed."""
    assert vp.direct_on_top(wants, hysteresis, end_above) == pytest.approx(on_top)


def test_the_margin_is_kept_in_the_valves_steps_rounded_up():
    """A valve in half degrees given 0.7 would round to 0.5 and shut right where the call ends."""
    assert vp.direct_on_top(True, 0.4, 0.5, step=0.5) == 1.0
    assert vp.direct_on_top(True, 0.4, -0.1, step=0.5) == 0.5
    assert vp.direct_on_top(False, 0.4, 0.5, step=0.5) == 0.0


# ------------------------------------------------------------------ concept §7: when Thriftherm goes
def test_a_valve_handed_back_goes_on_its_own_sensor_and_its_offset_of_before():
    mem = ValveMemory(selection_before="local_temperature", offset_before=0.4)
    writes = vp.hand_back(view(offset_shown=-0.6), mem)
    assert [(w.entity_id, w.value) for w in writes] == [
        (f"select.{V}_temperature_sensor_select", "local_temperature"),
        (f"number.{V}_local_temperature_calibration", 0.4),
    ]
    # as it was: nothing to write; the child lock and the mode are never touched
    assert vp.hand_back(view(selection="local_temperature", offset_shown=0.4), mem) == ()
    assert vp.hand_back(view(available=False), mem) == ()


def test_a_valve_whose_opening_thriftherm_set_is_handed_back_fully_open():
    mem = ValveMemory(opening_pct=30.0, opening_ts=NOW, opening_before=100.0)
    writes = vp.hand_back(view(TRVZB_D, selection="local_temperature", opening_shown=30.0), mem)
    assert [(w.entity_id, w.value) for w in writes] == [(f"number.{V}_heating_valve_position", 100.0)]
    # an opening Thriftherm never set stays as it is
    assert vp.hand_back(view(TRVZB_D, selection="local_temperature", opening_shown=30.0), ValveMemory()) == ()


# ------------------------------------------------------------------ review 2026-11-15 item 6: a way forced for a test
def test_a_trvzb_forced_to_the_offset_goes_on_its_own_sensor_and_gets_the_offset():
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600))  # it reads 1 K warm
    plan, _ = vp.plan(view(shown_c=21.4, offset_shown=0.0, force="offset"), settled, 20.4, True, NOW)
    assert plan.way == "offset"
    assert writes(plan) == {TRVZB.sensor_select: "local_temperature", TRVZB.offset: -1.0}


def test_a_trvzb_forced_to_the_corrected_setpoint_goes_on_its_own_sensor_and_gets_the_correction():
    settled = ValveMemory(correction=Correction(ema_k=2.0, ema_ts=NOW - 3600))
    forced = view(shown_c=22.6, force="setpoint")
    assert vp.setpoint_correction(forced, settled, 20.6, True, 21.0, NOW)[0] == 2.0
    plan, _ = vp.plan(forced, settled, 20.6, True, NOW)
    assert plan.way == "setpoint" and writes(plan) == {TRVZB.sensor_select: "local_temperature"}


def test_a_way_the_valve_does_not_have_cannot_be_forced():
    plan, _ = vp.plan(view(HOMEMATIC, shown_c=20.4, force="offset"), ValveMemory(), 20.4, True, NOW)
    assert plan.way == "setpoint"  # no offset: its own fallback


def test_back_from_a_forced_offset_the_valve_gets_its_offset_of_before():
    """Item 6: after the day on the offset the room temperature goes to the valve again, and its offset back."""
    after_b = ValveMemory(offset_before=0.0, offset_forced=True,
                          correction=Correction(ema_k=1.0, ema_ts=NOW - 3600, applied_k=1.0, applied_ts=NOW - 600))
    plan, mem = vp.plan(view(selection="local_temperature", offset_shown=-1.0), after_b, 20.4, True, NOW)
    assert plan.way == "external" and writes(plan)[TRVZB.offset] == 0.0
    assert mem.correction.applied_ts is None
    # once: an offset a person sets on way a afterwards is theirs
    plan, _ = vp.plan(view(offset_shown=-0.6), mem, 20.4, True, NOW + 60)
    assert TRVZB.offset not in writes(plan)


def test_a_valve_handed_back_is_switched_on_after_a_window_and_gets_the_target_for_thriftherms_setpoints():
    """Review 2026-11-15 (2) #1: off for the window, the frost limit, an exercise end or a margin stayed for good."""
    by_window = ValveMemory(off_by_window=True)
    writes_ = vp.hand_back(view(selection="local_temperature", mode="off", target_shown=21.0), by_window, 21.0)
    assert [(w.service, w.value) for w in writes_] == [("set_hvac_mode", "heat")]
    # our own setpoint (a margin, the frost limit) goes back to the target; the exercise's end as well
    ours = ValveMemory(setpoint_c=21.5)
    writes_ = vp.hand_back(view(selection="local_temperature", target_shown=21.5), ours, 21.0, ours_c=21.5)
    assert [(w.service, w.value) for w in writes_] == [("set_temperature", 21.0)]
    moving = ValveMemory(exercise_step=1)
    writes_ = vp.hand_back(view(selection="local_temperature", target_shown=30.0), moving, 21.0)
    assert [(w.service, w.value) for w in writes_] == [("set_temperature", 21.0)]
    # a setpoint a person set stays theirs; an off of a person as well
    assert vp.hand_back(view(selection="local_temperature", target_shown=23.0), ours, 21.0, ours_c=21.5) == ()
    assert vp.hand_back(view(selection="local_temperature", mode="off"), ValveMemory(), 21.0) == ()


def test_a_valve_whose_opening_thriftherm_set_gets_it_full_once_the_room_is_no_more_on_way_d():
    """Review (2) #3: a room stored with way d saved as Automatic kept its valve at 3 % for good."""
    left = ValveMemory(opening_pct=3.0, opening_ts=NOW - 600, opening_before=100.0)
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=3.0), left, 20.4, True, NOW)
    assert writes(plan)[TRVZB_D.opening] == 100.0 and mem.opening_before == 100.0  # kept until the valve shows it
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), mem, 20.4, True, NOW + 60)
    assert TRVZB_D.opening not in writes(plan) and mem.opening_ts is None


def test_the_offset_of_before_comes_back_only_after_a_forced_offset():
    """Review (2) #7: after a day on the corrected setpoint a person's offset was overwritten once."""
    after_c = ValveMemory(offset_before=0.0, correction=Correction(ema_k=1.0, ema_ts=NOW - 3600, applied_k=1.0,
                                                                   applied_ts=NOW - 600))
    plan, _ = vp.plan(view(offset_shown=-0.6), after_c, 20.4, True, NOW)
    assert TRVZB.offset not in writes(plan)
    # forced to the offset, the mark is set
    settled = ValveMemory(correction=Correction(ema_k=1.0, ema_ts=NOW - 3600))
    _, mem = vp.plan(view(shown_c=21.4, offset_shown=0.0, force="offset"), settled, 20.4, True, NOW)
    assert mem.offset_forced
    assert vp.ValveMemory.from_storage(mem.to_storage()).offset_forced


def test_a_further_valve_gets_its_setpoint_within_its_limits_and_in_its_steps():
    """Final review 2026-11-15 #5: refused out of its range the whole plan waited, and in other steps it was sent anew
    every five minutes."""
    plan, _ = vp.plan(view(setpoint_limits=(5.0, 30.0)), ValveMemory(), 20.4, True, NOW, setpoint=35.0)
    assert writes(plan)[f"climate.{V}"] == 30.0
    plan, _ = vp.plan(view(setpoint_step=0.5, target_shown=21.0), ValveMemory(), 20.4, True, NOW, setpoint=21.3)
    assert writes(plan)[f"climate.{V}"] == 21.5
    plan, _ = vp.plan(view(setpoint_step=0.5, target_shown=21.5), ValveMemory(setpoint_c=21.5, setpoint_ts=NOW - 60),
                      20.4, True, NOW, setpoint=21.3)
    assert f"climate.{V}" not in writes(plan)  # shown in its steps: nothing to send again


def test_a_further_valve_learns_no_correction_while_the_window_is_open():
    """Final review 2026-11-15 #7: cold air at the room sensor, as for the first valve."""
    settled = ValveMemory(correction=Correction(ema_k=2.0, ema_ts=NOW - 3600))
    _, mem = vp.plan(view(HOMEMATIC, shown_c=22.6), settled, 18.0, True, NOW, setpoint=21.0, target=21.0,
                     window_open=True)
    assert mem.correction == settled.correction


def test_a_persons_own_sensor_outlasts_a_short_drop_out_and_goes_with_a_new_start():
    """Final review 2026-11-15 #8: a radio gap reset the person's choice, and the valve went back on the room."""
    chosen = ValveMemory(selection_by_hand=True)
    _, mem = vp.plan(view(available=False), chosen, 20.4, True, NOW)
    assert mem.selection_by_hand
    plan, mem = vp.plan(view(selection="local_temperature", external_shown=20.4), mem, 20.4, True, NOW + 60)
    assert TRVZB.sensor_select not in writes(plan) and mem.selection_by_hand
    # back from a new start (a change of batteries) its input shows 0: its own sensor is no choice of anyone's
    plan, mem = vp.plan(view(selection="local_temperature", external_shown=0.0), mem, 20.4, True, NOW + 120)
    assert not mem.selection_by_hand and writes(plan)[TRVZB.sensor_select] == "remote_temperature"


# ------------------------------------------------------------------ heating along: the opening in part (decided 16.11.)
def test_a_room_heating_along_opens_its_valve_in_part_by_how_little_it_lacks():
    """A room that heats along takes the heat over the whole burner run instead of reaching its target at once and
    shutting. Its opening limit goes down by stages, from the value the person set, and is remembered for the way back."""
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), ValveMemory(), 18.4, True, NOW, join_deficit_k=0.1)
    assert writes(plan)[TRVZB_D.opening] == 30.0 and (mem.opening_pct, mem.opening_before) == (30.0, 100.0)
    plan, mem2 = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), ValveMemory(), 18.3, True, NOW, join_deficit_k=0.2)
    assert writes(plan)[TRVZB_D.opening] == 60.0
    plan, mem3 = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), ValveMemory(), 18.0, True, NOW, join_deficit_k=0.5)
    assert TRVZB_D.opening not in writes(plan) and mem3.opening_ts is None  # further below: fully open, as it was
    # a valve without an opening limit goes by its setpoint as before
    plan, _ = vp.plan(view(), ValveMemory(), 18.4, True, NOW, join_deficit_k=0.1)
    assert all("valve_position" not in w.entity_id for w in plan.writes)


def test_the_opening_in_part_changes_seldom():
    """Up at once (the room lacks more), down only 0.1 K past its bound and ten minutes on: a sensor flickering between
    two tenths does not write every minute."""
    _, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), ValveMemory(), 18.3, True, NOW, join_deficit_k=0.2)
    assert mem.opening_pct == 60.0
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=60.0), mem, 18.4, True, NOW + MIN, join_deficit_k=0.1)
    assert TRVZB_D.opening not in writes(plan) and mem.opening_pct == 60.0
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=60.0), mem, 18.45, True, NOW + 11 * MIN, join_deficit_k=0.05)
    assert writes(plan)[TRVZB_D.opening] == 30.0
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.3, True, NOW + 12 * MIN, join_deficit_k=0.2)
    assert writes(plan)[TRVZB_D.opening] == 60.0  # up at once


def test_the_opening_comes_back_when_the_room_calls_stops_heating_along_or_its_window_opens():
    """A room that calls gets its valve open as the person set it (the one rule); so does one that stops heating along."""
    _, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), ValveMemory(), 18.4, True, NOW, join_deficit_k=0.1)
    plan, back = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.1, True, NOW + MIN)  # it calls: no limit
    assert writes(plan)[TRVZB_D.opening] == 100.0 and back.opening_before == 100.0  # forgotten once it shows it
    plan, back = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), back, 18.1, True, NOW + 2 * MIN)
    assert TRVZB_D.opening not in writes(plan) and (back.opening_ts, back.opening_before) == (None, None)  # once
    plan, _ = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.4, True, NOW + MIN, window_off=True)
    assert writes(plan)[TRVZB_D.opening] == 100.0  # the window: off, and open as it was for when it is shut


def test_a_valve_the_person_throttled_keeps_that_as_its_most():
    """A person's 60 % is the most Thriftherm opens it to; it gets the 60 back, never 100."""
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=60.0), ValveMemory(), 18.3, True, NOW, join_deficit_k=0.2)
    assert TRVZB_D.opening not in writes(plan)  # 60 % is what it has
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=60.0), mem, 18.4, True, NOW + 11 * MIN, join_deficit_k=0.05)
    assert writes(plan)[TRVZB_D.opening] == 30.0 and mem.opening_before == 60.0
    plan, _ = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.6, True, NOW + 12 * MIN)
    assert writes(plan)[TRVZB_D.opening] == 60.0
    left = view(TRVZB_D, quirks=SONOFF, selection="local_temperature", opening_shown=30.0)
    assert [(w.entity_id, w.value) for w in vp.hand_back(left, mem)] == [(TRVZB_D.opening, 60.0)]
    # one the person changes meanwhile is theirs: the new most
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=80.0), mem, 18.3, True, NOW + 25 * MIN, join_deficit_k=0.2)
    assert mem.opening_before == 80.0 and writes(plan)[TRVZB_D.opening] == 60.0


def test_the_opening_in_part_keeps_the_valves_least_opening():
    quirky = ValveQuirks(min_opening_pct=40.0, opening_is_limit=True)
    plan, _ = vp.plan(view(TRVZB_D, quirks=quirky, opening_shown=100.0), ValveMemory(), 18.4, True, NOW,
                      join_deficit_k=0.1)
    assert writes(plan)[TRVZB_D.opening] == 40.0


def test_only_a_sonoff_trvzb_or_trvzbt_has_its_opening_limited_while_heating_along():
    """Review 2026-11-18: the number of a valve's position (valve_position, a Shelly's) was taken as an opening limit,
    written down while heating along and its old position written back later, against the valve's own control. Only
    on the Sonoff TRVZB and TRVZBT is it a limit (heating_valve_position, valve_opening_degree)."""
    from custom_components.thriftherm.engines.valve_caps import quirks_of

    sonoff = quirks_of("SONOFF", "TRVZB")
    assert sonoff.opening_is_limit and quirks_of("SONOFF", "Zigbee thermostatic radiator valve").opening_is_limit
    assert not quirks_of("Shelly", "SHTRV-01").opening_is_limit and not ValveQuirks().opening_is_limit
    plan, mem = vp.plan(view(TRVZB_D, opening_shown=45.0), ValveMemory(), 18.4, True, NOW, join_deficit_k=0.1)
    assert TRVZB_D.opening not in writes(plan) and mem.opening_before is None
    plan, mem = vp.plan(view(TRVZB_D, quirks=sonoff, opening_shown=100.0), ValveMemory(), 18.4, True, NOW, join_deficit_k=0.1)
    assert writes(plan)[TRVZB_D.opening] == 30.0 and mem.opening_before == 100.0


def _ours(pct: float, before: float = 100.0, ts: float = NOW - 600.0) -> ValveMemory:
    return ValveMemory(opening_pct=pct, opening_ts=ts, opening_before=before)


def test_an_opening_given_back_is_forgotten_only_once_the_valve_shows_it():
    """Review 2026-11-18: the give-back was written once and the memory emptied in the same go. A Zigbee command that
    did not arrive left the valve at 30 % for good, and the next heating along took 30 for the person's value."""
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), _ours(30.0), 18.4, True, NOW)
    assert writes(plan)[TRVZB_D.opening] == 100.0 and mem.opening_before == 100.0
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.4, True, NOW + 60)
    assert TRVZB_D.opening not in writes(plan) and mem.opening_before == 100.0  # waiting for its answer
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=30.0), mem, 18.4, True, NOW + 360)
    assert writes(plan)[TRVZB_D.opening] == 100.0 and mem.opening_before == 100.0  # not taken: again, no person's 30
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=100.0), mem, 18.4, True, NOW + 420)
    assert TRVZB_D.opening not in writes(plan) and (mem.opening_pct, mem.opening_ts, mem.opening_before) == (None, None, None)


@pytest.mark.parametrize(("held", "deficit", "wanted"), [(30.0, 0.2, 60.0), (60.0, 0.01, 30.0)])
def test_a_stage_change_that_did_not_arrive_is_sent_again_and_not_taken_for_the_persons(held, deficit, wanted):
    """Review 2026-11-18: 30 to 60 written, not arrived; five minutes later the valve still showing 30 was taken for a
    person's 30, and the person's 100 were gone. The value we had before is ours, never a person's."""
    start = _ours(held, ts=NOW - 1200.0)
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=held), start, 18.4, True, NOW, join_deficit_k=deficit)
    assert writes(plan)[TRVZB_D.opening] == wanted
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=held), mem, 18.4, True, NOW + 360, join_deficit_k=deficit)
    assert writes(plan)[TRVZB_D.opening] == wanted and mem.opening_before == 100.0
    plan, mem = vp.plan(view(TRVZB_D, quirks=SONOFF, opening_shown=50.0), mem, 18.4, True, NOW + 1200, join_deficit_k=deficit)
    # a value of neither: a person's, the new top - 30 below it, or nothing of ours left where 60 is above it
    assert (mem.opening_before, writes(plan).get(TRVZB_D.opening)) == ((50.0, 30.0) if wanted < 50.0 else (None, None))
