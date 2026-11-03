"""COP of a unit that switches its compressor on and off by itself: added up over whole cycles."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker
from custom_components.thriftherm.models import DefrostResult, HeatPumpResult

from .conftest import PARAMS, heat_pump_state, reading

NO_DEFROST = DefrostResult(
    suspected=False, kind="none", indicators=(), cycles_last_90min=0, block_reason=None, block_until_ts=None
)
ON_W, OFF_W = 360.0, 12.0


def sample(
    tracker: HeatPumpTracker, ts: float, on: bool, defrost: DefrostResult = NO_DEFROST, outlet: float | None = None,
    fan_rpm: float | None = 600.0, outlet_reading=None, run_state: str | None = None,
):
    unit = heat_pump_state(
        compressor_hz=24.0 if on else 0.0,
        running_s=300.0 if on else None,
        plug_power=ON_W if on else OFF_W,
        intake=(22.0, 45.0),
        outlet=((outlet if outlet is not None else (34.0 if on else 22.0)), None),
        fan_rpm=fan_rpm,
    )
    if outlet_reading is not None:
        unit = replace(unit, outlet_temp=outlet_reading)
    res = HeatPumpResult(
        run_state=run_state or ("heating_warming_up" if on else "heating_idle"),
        electrical_power_w=ON_W if on else OFF_W,
        electrical_power_source="plug",
        heating_available=True,
        issues=(),
    )
    return tracker.cop(SimpleNamespace(heat_pump=unit), res, defrost, PARAMS, ts)


def run_cycles(tracker: HeatPumpTracker, cycles: int, start: float = 0.0, **kw):
    """Ten minutes on, ten off, a sample a minute; ends with the compressor start after the last cycle."""
    res = None
    for minute in range(cycles * 20 + 1):
        res = sample(tracker, start + minute * 60.0, on=(minute % 20) < 10, **kw)
    return res


def test_no_settled_sample_and_no_cop_before_a_whole_cycle():
    tracker = HeatPumpTracker()
    res = None
    for minute in range(15):
        res = sample(tracker, minute * 60.0, on=minute < 10)
    assert res.value is None and res.cycle_cop is None and res.gate_reasons


def test_a_whole_cycle_gives_the_cop_of_heat_over_electricity_including_the_off_time():
    tracker = HeatPumpTracker()
    res = run_cycles(tracker, 1)
    on_only = sample(HeatPumpTracker(), 0.0, on=True)  # the same unit, for its heat while on
    assert res.cycle_cop is not None and res.value == res.cycle_cop and res.gate_reasons == ()
    # standby electricity and the ramps count: clearly below heat-while-on over power-while-on, but a real COP
    assert 2.0 < res.cycle_cop < 6.0
    assert on_only.value is None


def test_a_hole_in_the_samples_spoils_the_cycle():
    tracker = HeatPumpTracker()
    for minute in list(range(0, 5)) + list(range(12, 21)):
        res = sample(tracker, minute * 60.0, on=(minute % 20) < 10)
    assert res.cycle_cop is None


def test_a_defrost_spoils_the_cycle():
    tracker = HeatPumpTracker()
    for minute in range(21):
        defrost = NO_DEFROST if minute != 5 else replace(NO_DEFROST, suspected=True, kind="cycle")
        res = sample(tracker, minute * 60.0, on=(minute % 20) < 10, defrost=defrost)
    assert res.cycle_cop is None


def test_the_cycle_cop_goes_stale_and_is_dropped_when_the_unit_is_off():
    tracker = HeatPumpTracker()
    run_cycles(tracker, 1)
    late = None
    for minute in range(21, 21 + 70):  # the unit idles on for more than an hour without a new start
        late = sample(tracker, minute * 60.0, on=False)
    assert late.cycle_cop is None and late.value is None


def test_reset_forgets_the_cycle():
    tracker = HeatPumpTracker()
    run_cycles(tracker, 1)
    tracker.reset()
    assert tracker.cycle_cop is None and tracker.cycle_start_ts is None


@pytest.mark.parametrize("cycles", [2, 3])
def test_every_whole_cycle_is_measured_anew(cycles: int):
    tracker = HeatPumpTracker()
    res = run_cycles(tracker, cycles)
    assert res.cycle_cop is not None and tracker.cycle_cop[0] == cycles * 20 * 60.0


def test_a_cycle_with_an_unknown_fan_speed_is_spoiled_not_counted_as_without_heat():
    tracker = HeatPumpTracker()
    for minute in range(21):
        res = sample(tracker, minute * 60.0, on=(minute % 20) < 10, fan_rpm=None if minute == 5 else 600.0)
    assert res.cycle_cop is None


def test_the_cop_of_a_cycle_is_shown_but_does_not_count_as_settled():
    tracker = HeatPumpTracker()
    res = run_cycles(tracker, 1)
    assert res.value == res.cycle_cop is not None and tracker.cop_settled is False


def test_a_quiet_outlet_phase_does_not_spoil_the_cycle():
    # Seen 2026-10-31 to 11-02: the sensor reports on a change of 0.2 degrees, up to 27 minutes apart in a steady run.
    tracker = HeatPumpTracker()
    res = None
    for minute in range(21):
        on = (minute % 20) < 10
        quiet = reading(34.0 if on else 22.0, age_s=25 * 60.0)
        res = sample(tracker, minute * 60.0, on=on, outlet_reading=quiet)
    assert res.cycle_cop is not None and 2.0 < res.cycle_cop < 6.0


def test_a_long_rest_with_the_fan_turning_and_an_outlet_past_its_limit_does_not_spoil_the_cycle():
    # At rest nothing changes for the sensor to report; the reading shows no heat, so there is none to add up.
    def cycle(rest_reading):
        tracker = HeatPumpTracker()
        res = None
        for minute in range(21):
            on = (minute % 20) < 10
            res = sample(tracker, minute * 60.0, on=on, outlet_reading=None if on else rest_reading)
        return res

    assert cycle(reading(21.0, valid=False)).cycle_cop is not None  # no heat in it
    assert cycle(reading(30.0, valid=False)).cycle_cop is None  # says heat, but past its limit


def test_a_defrost_dip_is_discarded_however_young_the_report_of_the_outlet():
    # The age no longer keeps it out: the operating state does.
    tracker = HeatPumpTracker()
    defrost = replace(NO_DEFROST, suspected=True, kind="cycle")
    dip = reading(30.0, age_s=60.0)
    res = sample(tracker, 0.0, on=True, defrost=defrost, outlet_reading=dip, run_state="heating_stable")
    assert "defrost_cycle" in res.gate_reasons and tracker.cop_samples == []


def test_a_fan_turning_below_the_calibrated_range_between_two_starts_does_not_spoil_the_cycle():
    # The PortaSplit keeps its fan at 328-344 rpm between two starts, below 0.8 x its lowest calibrated 496 rpm;
    # here 200 rpm against the lowest calibrated 300 rpm, with the outlet still warm for a few minutes after the stop.
    tracker = HeatPumpTracker()
    res = None
    for minute in range(21):
        on = (minute % 20) < 10
        outlet = 34.0 if on else (26.0 if minute < 14 else 22.0)
        res = sample(tracker, minute * 60.0, on=on, outlet=outlet, fan_rpm=600.0 if on else 200.0)
    assert res.cycle_cop is not None and res.value == res.cycle_cop
    assert 2.0 < res.cycle_cop < 6.0


def settled(tracker: HeatPumpTracker, ts: float, running_s: float, params=PARAMS, above_minimum: str | None = None):
    """A sample of a unit its control counts as heating steadily."""
    unit = heat_pump_state(running_s=running_s)
    stable = HeatPumpResult("heating_stable", 800.0, "plug", True)
    return tracker.cop(SimpleNamespace(heat_pump=unit), stable, NO_DEFROST, params, ts, above_minimum=above_minimum)


def test_a_cop_sample_counts_only_once_the_compressor_has_run_ten_minutes_however_short_the_warm_up_is_set():
    # A start from off boosts to 60-79 Hz within 1-3 minutes (seen 2026-10-20 to 30): not the speed it is held at.
    tracker = HeatPumpTracker()
    short_warm_up = replace(PARAMS, cop_warmup_s=120.0)
    res = settled(tracker, 0.0, running_s=300.0, params=short_warm_up)
    assert tracker.cop_samples == [] and "warming_up" in res.gate_reasons
    settled(tracker, 60.0, running_s=600.0, params=short_warm_up)
    assert len(tracker.cop_samples) == 1


@pytest.mark.parametrize("why", ["quick_heat_up", "bathroom_drying"])
def test_a_quick_heat_up_or_a_drying_gives_no_cop_sample(why: str):
    tracker = HeatPumpTracker()
    res = settled(tracker, 0.0, running_s=900.0, above_minimum=why)
    assert tracker.cop_samples == [] and why in res.gate_reasons


def test_a_cycle_with_a_quick_heat_up_is_not_measured():
    tracker = HeatPumpTracker()
    res = None
    for minute in range(21):
        on = (minute % 20) < 10
        unit = heat_pump_state(
            compressor_hz=24.0 if on else 0.0, running_s=300.0 if on else None, plug_power=ON_W if on else OFF_W,
            intake=(22.0, 45.0), outlet=(34.0 if on else 22.0, None),
        )
        run = HeatPumpResult("heating_warming_up" if on else "heating_idle", ON_W if on else OFF_W, "plug", True)
        res = tracker.cop(SimpleNamespace(heat_pump=unit), run, NO_DEFROST, PARAMS, minute * 60.0,
                          above_minimum="quick_heat_up" if minute == 5 else None)
    assert res.cycle_cop is None
