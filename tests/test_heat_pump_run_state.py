"""The run state of the heat pump across a restart and a defrost: a run goes on, it does not warm up anew."""

from dataclasses import replace
from types import SimpleNamespace

from custom_components.thriftherm import texts
from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker
from custom_components.thriftherm.models import DefrostResult, HeatPumpResult, HeatPumpSample

from .conftest import PARAMS, heat_pump_state

NOW = 1_000_000.0
NO_DEFROST = DefrostResult(suspected=False, kind="none", indicators=(), cycles_last_90min=0, block_reason=None, block_until_ts=None)
DEFROST = replace(NO_DEFROST, suspected=True, kind="cycle", indicators=("D6_reverse_cycle",), cycles_last_90min=1)


def sample(ts: float, hz: float, outlet: float = 34.0) -> HeatPumpSample:
    return HeatPumpSample(ts=ts, compressor_hz=hz, power_w=350.0 if hz else 12.0, outdoor_coil_temp=-2.0,
                          indoor_coil_temp=38.0, intake_temp=21.0, outlet_temp=outlet if hz else 21.0)


def unit(hz: float, running_s: float | None, history: tuple[HeatPumpSample, ...]):
    return replace(heat_pump_state(compressor_hz=hz, running_s=running_s, intake=(21.0, 45.0)), history=history)


# ---- a restart of Home Assistant in the middle of a run (seen 2026-11-06 11:25-11:36: "warming up" for ten minutes) ----

def test_a_run_found_heating_after_a_restart_is_taken_over_as_settled():
    tracker = HeatPumpTracker()
    history = (sample(NOW, 28.0),)  # the first sample after the restart: running, its outlet warm
    taken = tracker.taken_over(unit(28.0, 0.0, history), PARAMS, NOW)
    assert taken.compressor_running_s >= PARAMS.cop_warmup_s
    assert heat_pump_engine.run_state(taken, PARAMS) == "heating_stable"
    history += (sample(NOW + 60, 28.0),)
    later = tracker.taken_over(unit(28.0, 60.0, history), PARAMS, NOW + 60)
    assert later.compressor_running_s == taken.compressor_running_s + 60.0


def test_a_run_found_heating_once_the_unit_is_loaded_after_a_restart_is_taken_over_as_settled():
    """Review 2026-11-08 (#R10): after a full restart the first cycle comes before the unit's entity is loaded. Its sample
    without a frequency made the run that showed up a minute later a new start, ten minutes of warming up."""
    tracker = HeatPumpTracker()
    gone = replace(sample(NOW - 60, 0.0), compressor_hz=None, outlet_temp=None, intake_temp=None)
    not_loaded = replace(unit(0.0, None, (gone,)), compressor_hz=None)
    assert tracker.taken_over(not_loaded, PARAMS, NOW - 60).compressor_running_s is None
    history = (gone, sample(NOW, 28.0))
    taken = tracker.taken_over(unit(28.0, 0.0, history), PARAMS, NOW)
    assert taken.compressor_running_s >= PARAMS.cop_warmup_s
    # one that stood when it showed up, or stopped in between, was seen to start
    tracker = HeatPumpTracker()
    seen = (gone, sample(NOW - 60, 0.0), sample(NOW, 28.0))
    assert tracker.taken_over(unit(28.0, 0.0, seen), PARAMS, NOW).compressor_running_s == 0.0
    tracker = HeatPumpTracker()
    between = (sample(NOW - 120, 28.0), gone, sample(NOW, 28.0))
    assert tracker.taken_over(unit(28.0, 0.0, between), PARAMS, NOW).compressor_running_s == 0.0


def test_a_start_that_was_seen_or_has_a_cold_outlet_still_warms_up():
    tracker = HeatPumpTracker()
    seen = (sample(NOW - 60, 0.0), sample(NOW, 60.0))
    assert tracker.taken_over(unit(60.0, 0.0, seen), PARAMS, NOW).compressor_running_s == 0.0
    tracker = HeatPumpTracker()
    cold = (sample(NOW, 60.0, outlet=22.0),)  # it starts just now: the outlet has not warmed yet
    assert tracker.taken_over(unit(60.0, 0.0, cold), PARAMS, NOW).compressor_running_s == 0.0
    tracker = HeatPumpTracker()
    reported = (sample(NOW, 28.0),)  # its start is known from before the watch: taken as it is
    assert tracker.taken_over(unit(28.0, 240.0, reported), PARAMS, NOW).compressor_running_s == 240.0


# ---- a defrost: 89 Hz for 3-4 minutes between two stops of a minute (seen 7 times in the week to 2026-11-08) ------------

def step(tracker, ts, hz, running_s, defrost=NO_DEFROST, history=()):
    u = unit(hz, running_s, history or (sample(ts, hz),))
    return tracker.run_state(u, heat_pump_engine.run_state(u, PARAMS), defrost, PARAMS, ts)


def test_a_defrost_is_its_own_run_state_and_the_run_goes_on_after_it():
    tracker = HeatPumpTracker()
    assert step(tracker, NOW, 28.0, 1800.0) == "heating_stable"
    assert step(tracker, NOW + 60, 0.0, None) == "heating_idle"  # the stop before it: not known yet as a defrost
    assert step(tracker, NOW + 120, 89.0, 0.0) == "heating_warming_up"
    for minute in (3, 4, 5):
        assert step(tracker, NOW + minute * 60, 89.0, minute * 60 - 120.0, DEFROST) == "defrosting"
    assert step(tracker, NOW + 360, 0.0, None) == "defrosting"  # the stop after it
    assert step(tracker, NOW + 420, 30.0, 0.0) == "heating_stable"  # not a new start that warms up
    assert tracker.run_age_s(unit(30.0, 0.0, ()), NOW + 420) >= 1800.0
    assert (texts.label("defrosting", "en"), texts.label("defrosting", "de")) == ("Defrosting", "Abtauen")


def test_a_long_stop_after_a_defrost_and_a_stop_without_one_end_the_run():
    tracker = HeatPumpTracker()
    step(tracker, NOW, 89.0, 1800.0, DEFROST)
    assert step(tracker, NOW + 60, 0.0, None) == "defrosting"
    assert step(tracker, NOW + 300, 0.0, None) == "heating_idle"  # stood longer than a defrost stops it
    assert step(tracker, NOW + 360, 30.0, 0.0) == "heating_warming_up"
    tracker = HeatPumpTracker()
    step(tracker, NOW, 28.0, 1800.0)
    assert step(tracker, NOW + 60, 0.0, None) == "heating_idle"
    assert step(tracker, NOW + 120, 30.0, 0.0) == "heating_warming_up"


def test_after_a_defrost_the_cop_counts_without_a_new_warm_up():
    tracker = HeatPumpTracker()
    step(tracker, NOW, 89.0, 1800.0, DEFROST)
    step(tracker, NOW + 60, 0.0, None)
    restarted = unit(30.0, 60.0, (sample(NOW + 60, 0.0), sample(NOW + 120, 30.0)))
    state = tracker.run_state(restarted, heat_pump_engine.run_state(restarted, PARAMS), NO_DEFROST, PARAMS, NOW + 120)
    res = HeatPumpResult(run_state=state, electrical_power_w=350.0, electrical_power_source="plug", heating_available=True,
                         issues=())
    cop = tracker.cop(SimpleNamespace(heat_pump=restarted), res, NO_DEFROST, PARAMS, NOW + 120)
    assert "warming_up" not in cop.gate_reasons


def test_a_dropout_of_ten_seconds_at_most_goes_on_with_the_run_and_its_cop_samples():
    """Review 2026-11-08 (#R6): one cycle with the unit away (its count of the run bridged by the snapshot builder) ended
    the run, and the COP samples of half an hour were lost with it."""
    tracker = HeatPumpTracker()
    history = (sample(NOW - 60, 28.0),)
    taken = tracker.taken_over(unit(28.0, 0.0, history), PARAMS, NOW - 60)  # a run taken over after a restart
    assert step(tracker, NOW - 60, 28.0, taken.compressor_running_s) == "heating_stable"
    tracker.cop_samples = [(NOW - 60, 3.4)]
    gone = replace(unit(0.0, 60.0, history + (sample(NOW, 0.0),)), available=False, hvac_mode=None, compressor_hz=None)
    gone = tracker.taken_over(gone, PARAMS, NOW)
    state = heat_pump_engine.run_state(gone, PARAMS)
    assert tracker.run_state(gone, state, NO_DEFROST, PARAMS, NOW) == "unavailable"
    res = HeatPumpResult(run_state="unavailable", electrical_power_w=None, electrical_power_source=None,
                         heating_available=False, issues=())
    tracker.cop(SimpleNamespace(heat_pump=gone), res, NO_DEFROST, PARAMS, NOW)
    assert tracker.cop_samples == [(NOW - 60, 3.4)]
    back = tracker.taken_over(unit(28.0, 70.0, history + (sample(NOW + 10, 28.0),)), PARAMS, NOW + 10)
    assert back.compressor_running_s == taken.compressor_running_s + 70.0  # still past its warm-up
    assert step(tracker, NOW + 10, 28.0, back.compressor_running_s) == "heating_stable"
    assert tracker.run_age_s(back, NOW + 10) == back.compressor_running_s


def test_a_normal_stop_long_after_a_defrost_is_no_defrost_and_a_start_after_it_is_a_new_one():
    """Review 2026-11-08 (#R12): a stop two hours after the one defrost of a run showed "defrosting" for three minutes, and a
    start 160 s after it went on with the run, settled at once, its start boost in the COP."""
    tracker = HeatPumpTracker()
    assert step(tracker, NOW, 28.0, 3600.0) == "heating_stable"
    for minute in (1, 2, 3):
        step(tracker, NOW + minute * 60, 89.0, 3600.0 + minute * 60, DEFROST)
    later = NOW + 2 * 3600
    assert step(tracker, later, 28.0, 3600.0 + 2 * 3600) == "heating_stable"
    assert step(tracker, later + 60, 0.0, None) == "heating_idle"  # the rooms are warm: an ordinary stop
    assert step(tracker, later + 180, 0.0, None) == "heating_idle"
    assert step(tracker, later + 220, 60.0, 0.0) == "heating_warming_up"  # a start by hand: a new run
    assert tracker.run_age_s(unit(60.0, 0.0, ()), later + 220) == 0.0
