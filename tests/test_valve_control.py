"""How Thriftherm drives a valve directly (docs/PHASE13_VALVES.md §3.3, §3.1 b and c, §5.1): pure rules."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import valve_control as vc
from custom_components.thriftherm.engines.valve_control import Correction

NOW = 2_000_000.0
MIN = 60.0


# ------------------------------------------------------------------ the difference between valve and room, damped
def test_the_difference_is_damped_over_half_an_hour():
    c = vc.learn(Correction(), valve_c=23.0, room_c=21.0, now_ts=NOW)
    assert c.ema_k == 2.0  # the first pair is taken as it is
    c = vc.learn(c, valve_c=21.0, room_c=21.0, now_ts=NOW + 30 * MIN)  # a jump to 0 moves it by 1 - e^-1
    assert c.ema_k == pytest.approx(2.0 * 0.3679, abs=0.01)


def test_pairs_come_from_the_last_valid_readings():
    """Review 2026-11-13: the TRVZB reports hourly, a Shelly sensor on change: two reports within ten minutes are rare."""
    c = vc.learn(Correction(), valve_c=None, room_c=21.0, now_ts=NOW)
    assert c.ema_k is None
    c = vc.learn(c, valve_c=22.0, room_c=None, now_ts=NOW + MIN, last_room_c=21.0)
    assert c.ema_k == 1.0


# ------------------------------------------------------------------ way c: the setpoint corrected
def _settled(diff_k: float) -> Correction:
    return Correction(ema_k=diff_k, ema_ts=NOW - 3600)


def test_a_valve_measuring_warm_gets_a_higher_setpoint():
    sp, c = vc.corrected_setpoint(_settled(2.0), target=21.0, room_c=20.5, now_ts=NOW)
    assert sp == 23.0 and c.applied_k == 2.0


def test_the_correction_is_held_within_its_limits():
    assert vc.corrected_setpoint(_settled(5.0), 21.0, 20.0, NOW)[0] == 24.0  # +3 K at most
    assert vc.corrected_setpoint(_settled(-4.0), 21.0, 20.0, NOW)[0] == 19.0  # -2 K at most


def test_a_small_wobble_changes_nothing_and_a_change_waits_ten_minutes():
    _, c = vc.corrected_setpoint(_settled(2.0), 21.0, 20.5, NOW)
    sp, c2 = vc.corrected_setpoint(replace(c, ema_k=1.6), 21.0, 20.5, NOW + 15 * MIN)
    assert sp == 23.0 and c2.applied_k == 2.0  # less than a step and a half down
    sp, c3 = vc.corrected_setpoint(replace(c, ema_k=3.0), 21.0, 20.5, NOW + 5 * MIN)
    assert sp == 23.0  # a step, but not ten minutes yet
    sp, _ = vc.corrected_setpoint(replace(c, ema_k=3.0), 21.0, 20.5, NOW + 11 * MIN)
    assert sp == 24.0


def test_at_its_target_the_room_gets_no_correction_upwards_and_it_does_not_flicker():
    """Review 2026-11-13: cut at the target and given back at once, a sensor between 20.9 and 21.0 changed the setpoint
    every minute. Cut at the target at once (no overheating), given back only 0.2 K below it."""
    _, c = vc.corrected_setpoint(_settled(2.0), 21.0, 20.5, NOW)
    sp, c = vc.corrected_setpoint(c, 21.0, 21.0, NOW + MIN)
    assert sp == 21.0 and c.cut  # at once, not after ten minutes
    sp, c = vc.corrected_setpoint(c, 21.0, 20.9, NOW + 2 * MIN)
    assert sp == 21.0 and c.cut
    sp, c = vc.corrected_setpoint(c, 21.0, 20.8, NOW + 13 * MIN)
    assert sp == 23.0 and not c.cut


def test_a_negative_correction_holds_at_the_target_too():
    sp, _ = vc.corrected_setpoint(_settled(-1.0), 21.0, 21.2, NOW)
    assert sp == 20.0


def test_without_a_room_reading_the_valve_goes_by_its_own_sensor():
    sp, c = vc.corrected_setpoint(_settled(2.0), 21.0, None, NOW)
    assert sp == 21.0 and c.applied_k == 0.0


def test_a_new_target_goes_out_at_once_with_the_correction_it_has():
    _, c = vc.corrected_setpoint(_settled(2.0), 21.0, 20.5, NOW)
    sp, _ = vc.corrected_setpoint(c, 19.0, 18.5, NOW + MIN)
    assert sp == 21.0


def test_the_correction_goes_in_the_valves_steps():
    sp, _ = vc.corrected_setpoint(_settled(1.3), 21.0, 20.5, NOW, step=0.5)
    assert sp == 22.5
    sp, _ = vc.corrected_setpoint(_settled(1.3), 21.0, 20.5, NOW, step=1.0)
    assert sp == 23.0  # below its target, up to the next step (2026-11-16)


def test_below_its_target_the_correction_goes_up_to_the_next_step():
    """15.11. 21:30, bedroom on way c (TRVZB, 0.5 K steps): 20.55 °C at 21.0, the valve read 21.3 on the hot radiator, its
    damped difference was a little under a step. Rounded to the nearest step the correction stood at 0, the setpoint at
    21.0, and the valve shut for 13 minutes with the room 0.45 below its target. Below the target the valve's view is
    rounded up: it shuts no earlier than the room's sensor says."""
    held = Correction(ema_k=0.45, ema_ts=NOW - MIN, applied_k=0.0, applied_ts=NOW - 3600)
    sp, c = vc.corrected_setpoint(held, target=21.0, room_c=20.55, now_ts=NOW)
    assert sp == 21.5 and c.applied_k == 0.5
    # at its target the cut holds as before: nothing upwards
    sp, c = vc.corrected_setpoint(c, 21.0, 21.0, NOW + MIN)
    assert sp == 21.0 and c.cut


def test_rounded_up_the_correction_does_not_flicker():
    """It goes up once the damped difference passes the step it has, and down only a step and a half below it: a
    difference wobbling around a step changes the setpoint once, not every ten minutes."""
    c = Correction(ema_k=0.45, ema_ts=NOW, applied_k=0.5, applied_ts=NOW)
    for i, ema in enumerate((0.4, 0.5, 0.35, 0.5, 0.3), start=1):
        sp, c = vc.corrected_setpoint(replace(c, ema_k=ema), 21.0, 20.6, NOW + i * 11 * MIN)
        assert sp == 21.5
    sp, c = vc.corrected_setpoint(replace(c, ema_k=0.55), 21.0, 20.6, NOW + 70 * MIN)
    assert sp == 22.0  # past the step it had: up to the next
    for i, ema in enumerate((0.45, 0.5, 0.3), start=1):
        sp, c = vc.corrected_setpoint(replace(c, ema_k=ema), 21.0, 20.6, NOW + (70 + i * 11) * MIN)
        assert sp == 22.0
    sp, c = vc.corrected_setpoint(replace(c, ema_k=0.25), 21.0, 20.6, NOW + 120 * MIN)
    assert sp == 21.5  # a step and a half below: down


def test_a_valve_reading_cold_keeps_its_correction_rounded_as_before():
    """Rounded up, a negative correction would shrink and the valve heat on past the target: only the upward side rounds up."""
    sp, _ = vc.corrected_setpoint(_settled(-0.6), 21.0, 20.5, NOW)
    assert sp == 20.5


# ------------------------------------------------------------------ way b: the calibration offset
def test_the_offset_is_the_room_less_the_valves_own_reading():
    """The valve shows its own reading plus the offset set: that offset comes off before the difference is taken."""
    off, c = vc.offset_for(_settled(2.0), now_ts=NOW, step=0.2, low=-12.7, high=12.7)
    assert off == -2.0 and c.applied_k == 2.0
    assert vc.own_reading(shown_c=19.0, offset_set=-2.0) == 21.0


def test_an_inverted_offset_and_its_limits():
    assert vc.offset_for(_settled(2.0), NOW, step=0.5, low=-9.0, high=9.0, inverted=True)[0] == 2.0
    assert vc.offset_for(_settled(8.0), NOW, step=1.0, low=-5.0, high=5.0)[0] == -5.0


def test_the_offset_changes_by_a_step_and_at_most_every_ten_minutes():
    off, c = vc.offset_for(_settled(2.0), NOW, step=0.2, low=-12.7, high=12.7)
    assert vc.offset_for(replace(c, ema_k=2.1), NOW + 20 * MIN, 0.2, -12.7, 12.7)[0] == -2.0
    assert vc.offset_for(replace(c, ema_k=2.6), NOW + 5 * MIN, 0.2, -12.7, 12.7)[0] == -2.0
    assert vc.offset_for(replace(c, ema_k=2.6), NOW + 11 * MIN, 0.2, -12.7, 12.7)[0] == -2.6


# ------------------------------------------------------------------ way a: the room temperature to the valve
def test_the_room_temperature_goes_out_on_a_change_and_is_refreshed():
    assert vc.external_due(None, None, 20.43, NOW) == 20.4
    assert vc.external_due(20.4, NOW, 20.44, NOW + 30) is None  # rounds to the same
    assert vc.external_due(20.4, NOW, 20.52, NOW + 30) is None  # a change, but within the minute
    assert vc.external_due(20.4, NOW, 20.52, NOW + 70) == 20.5
    assert vc.external_due(20.4, NOW, 20.4, NOW + 24 * MIN) is None
    assert vc.external_due(20.4, NOW, 20.4, NOW + 25 * MIN) == 20.4  # refreshed


def test_only_a_fresh_room_reading_goes_to_the_valve():
    assert vc.external_fresh(age_s=29 * MIN, alive=False)
    # Seen 2026-11-15 in the bathroom: a sensor whose values stood 48 minutes (Home Assistant does not write an unchanged
    # value anew) put the valve on its own sensor and back three times an hour. Two hours, as the TRVZB itself holds a
    # value (Phase B): a dead sensor leaves the valve where it would be without Home Assistant.
    assert vc.external_fresh(age_s=90 * MIN, alive=False)
    assert not vc.external_fresh(age_s=121 * MIN, alive=False)
    assert vc.external_fresh(age_s=3 * 3600, alive=True)  # a sensor that reports only on a change, its device alive
    assert not vc.external_fresh(age_s=None, alive=True)


def test_the_valve_takes_the_value_when_it_shows_it():
    assert vc.takes_external(written_c=20.4, written_ts=NOW, shown_c=20.5, shown_ts=NOW + 3000) is True
    assert vc.takes_external(20.4, NOW, 21.4, NOW + 3000) is False
    assert vc.takes_external(20.4, NOW, 21.4, NOW - 10) is None  # shown before it was written: no answer


# ------------------------------------------------------------------ the window
@pytest.mark.parametrize(("window_open", "target", "sent"), [(True, 21.0, 7.0), (False, 21.0, 21.0)])
def test_an_open_window_shuts_the_valve_down_to_the_frost_limit(window_open, target, sent):
    assert vc.valve_target(target, window_open, frost_c=7.0) == sent


def test_the_frost_limit_stays_within_what_the_valve_takes():
    assert vc.valve_target(21.0, True, frost_c=4.0, min_setpoint=4.5) == 4.5


def test_the_correction_is_stored_and_a_damaged_one_is_none():
    c = Correction(ema_k=1.25, ema_ts=NOW, applied_k=1.0, applied_ts=NOW, cut=True)
    assert Correction.from_storage(c.to_storage()) == c
    assert Correction.from_storage({"ema_k": 99, "cut": "yes", "applied_k": "x"}) == Correction()
    assert Correction.from_storage(None) == Correction()
