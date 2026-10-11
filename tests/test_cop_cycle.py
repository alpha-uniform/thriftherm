"""COP of a unit that switches its compressor on and off by itself: added up over whole cycles."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker
from custom_components.thriftherm.models import DefrostResult, HeatPumpResult

from .conftest import PARAMS, heat_pump_state

NO_DEFROST = DefrostResult(
    suspected=False, kind="none", indicators=(), cycles_last_90min=0, block_reason=None, block_until_ts=None
)
ON_W, OFF_W = 360.0, 12.0


def sample(tracker: HeatPumpTracker, ts: float, on: bool, defrost: DefrostResult = NO_DEFROST, outlet: float | None = None):
    unit = heat_pump_state(
        compressor_hz=24.0 if on else 0.0,
        running_s=300.0 if on else None,
        plug_power=ON_W if on else OFF_W,
        intake=(22.0, 45.0),
        outlet=((outlet if outlet is not None else (34.0 if on else 22.0)), None),
        fan_rpm=600.0,
    )
    res = HeatPumpResult(
        run_state="heating_warming_up" if on else "heating_idle",
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
    assert tracker.take_cycle_cop() == res.cycle_cop and tracker.take_cycle_cop() is None  # once, for the map


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
    assert tracker.cycle_cop is None and tracker.take_cycle_cop() is None and tracker.cycle_start_ts is None


@pytest.mark.parametrize("cycles", [2, 3])
def test_every_whole_cycle_is_measured_anew(cycles: int):
    tracker = HeatPumpTracker()
    res = run_cycles(tracker, cycles)
    assert res.cycle_cop is not None and tracker.cycle_cop[0] == cycles * 20 * 60.0
