"""The COP table: heat and electricity added up per band of outdoor temperature, over all of the heating time."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.thriftherm.engines import cop as cop_engine
from custom_components.thriftherm.engines import psychrometrics as psy
from custom_components.thriftherm.engines.heat_pump_tracking import HeatPumpTracker
from custom_components.thriftherm.engines.learning import (
    COP_BAND_HALF_LIFE_S,
    COP_BAND_LEARNED_S,
    LEGACY_WORTH_WH,
    PRIOR_CALIBRATION_DEFAULT,
    CopMap,
    prior_cop,
)
from custom_components.thriftherm.models import DefrostResult, HeatPumpResult

from .conftest import PARAMS, heat_pump_state, reading

NO_DEFROST = DefrostResult(
    suspected=False, kind="none", indicators=(), cycles_last_90min=0, block_reason=None, block_until_ts=None
)
DEFROST = replace(NO_DEFROST, suspected=True, kind="cycle")
INTAKE, RH, RPM = 21.0, 45.0, 600.0
DUCT = 0.9
PARAMS_DUCT = replace(PARAMS, duct_factor=DUCT)
HOUR = 3600.0


def outlet_for(heat_w: float) -> float:
    """The outlet temperature at which the air carries this heat; the heat is linear in it, closely enough for two steps."""
    airflow = cop_engine.airflow_from_rpm(PARAMS_DUCT.airflow_curve, RPM) * DUCT
    t = INTAKE + 10.0
    for _ in range(4):
        t = INTAKE + (t - INTAKE) * heat_w / psy.air_heating_power_w(INTAKE, RH, t, airflow)
    return t


def tick(
    tracker, ts, hz, power, heat=None, *, mode="heat", outlet=None, above=None, defrost=NO_DEFROST, fan=RPM, run_state=None,
    running=900.0,
):
    """One coordinator cycle; `outlet` is the reading as the sensor has it, otherwise the temperature for this heat."""
    unit = heat_pump_state(
        hvac_mode=mode, compressor_hz=hz, running_s=running if hz else None, plug_power=power,
        intake=(INTAKE, RH), outlet=(INTAKE, None), fan_rpm=fan,
    )
    unit = replace(unit, outlet_temp=outlet if outlet is not None else reading(outlet_for(heat) if heat else INTAKE))
    state = run_state or {"heat": "heating_stable" if hz else "heating_idle", "fan_only": "fan_only", "off": "off"}[mode]
    res = HeatPumpResult(state, power, "plug", True)
    tracker.cop(SimpleNamespace(heat_pump=unit), res, defrost, PARAMS_DUCT, ts, above_minimum=above)
    return tracker.band_interval


def feed(tracker, cm, ts, outdoor, *args, **kwargs):
    interval = tick(tracker, ts, *args, **kwargs)
    if interval is not None:
        cm.add_interval(outdoor, *interval, ts)
    return interval


# ------------------------------------------------------------------ the fixed time series asked for
def long_run(minutes=120):
    """10 minutes of start at 700 W / 1700 W, then 310 W / 1270 W; a sample a minute: (hz, W, heat W)."""
    return [(60.0, 700.0, 1700.0) if m < 10 else (24.0, 310.0, 1270.0) for m in range(minutes)]


def short_cycle():
    """15 minutes on, then 5 minutes of run-on while the heat fades."""
    on = [(60.0, 700.0, 1700.0) if m < 10 else (24.0, 310.0, 1270.0) for m in range(15)]
    return on + [(0.0, 30.0, 1270.0 * (5 - m) / 6.0) for m in range(5)]


def run_series(ticks, duct=DUCT):
    """The series through the tracker into the table. The outlet sensor holds its reading for 40 % of the time:
    two minutes in every five, as seen 2026-10-31 to 11-02. Returns the table and the minute it counted as learned."""
    tracker, cm = HeatPumpTracker(), CopMap()
    feed(tracker, cm, 0.0, 5.0, 0.0, 12.0)  # stands before the first start
    learned_at, held = None, None
    for minute, (hz, power, heat) in enumerate(ticks, start=1):
        true_outlet = outlet_for(heat)
        quiet = minute % 5 < 2 and held is not None and hz == 24.0
        outlet = reading(held if quiet else true_outlet, age_s=(minute % 5 + 1) * 60.0 if quiet else 10.0)
        held = held if quiet else true_outlet
        feed(tracker, cm, minute * 60.0, 5.0, hz, power, heat, outlet=outlet)
        if learned_at is None and cm.learned(5.0, duct):
            learned_at = minute
    return cm, learned_at


def energy_weighted(ticks):
    return sum(t[2] for t in ticks) / sum(t[1] for t in ticks)


def old_table(entries):
    """What the bin did until 2026-11-02: a running mean that becomes an EMA with an alpha of 0.2 after five entries."""
    mean = 0.0
    for count, value in enumerate(entries):
        mean += max(1.0 / (count + 1), 0.2) * (value - mean)
    return mean


SHORT_FIRST = short_cycle() * 3 + long_run()
LONG_FIRST = long_run() + short_cycle() * 3


@pytest.mark.parametrize("ticks", [SHORT_FIRST, LONG_FIRST], ids=["short_first", "long_first"])
def test_the_band_is_within_five_percent_of_the_energy_weighted_cop(ticks):
    cm, _ = run_series(ticks)
    value = cm.to_dict(DUCT)["+4..+6"]["cop"]
    assert value == pytest.approx(energy_weighted(ticks), rel=0.05)


def test_the_old_table_missed_the_same_series_by_more_than_five_percent():
    # Each short cycle one entry, then one settled value every five minutes from twelve samples on: the band
    # forgot the short cycles after about nine entries and stood at the COP of the steady run.
    cycle_cop = energy_weighted(short_cycle())
    settled = 1270.0 / 310.0
    old = old_table([cycle_cop] * 3 + [energy_weighted(long_run())] + [settled] * ((110 - 12) // 5))
    assert abs(old / energy_weighted(SHORT_FIRST) - 1.0) > 0.05
    assert old == pytest.approx(settled, rel=0.01)  # 4.1 against 3.4
    # the new one holds the same series to within five percent, in either order
    cm, _ = run_series(SHORT_FIRST)
    assert abs(cm.to_dict(DUCT)["+4..+6"]["cop"] / energy_weighted(SHORT_FIRST) - 1.0) < 0.05


def test_the_band_counts_as_learned_from_three_hours_of_run_time():
    cm, learned_at = run_series(SHORT_FIRST + [(24.0, 310.0, 1270.0)] * 5)
    assert COP_BAND_LEARNED_S == 3 * HOUR
    assert learned_at == 182  # every minute is run time, the run-on included; the fading takes two minutes of it
    partial, _ = run_series(SHORT_FIRST[:170])
    assert not partial.learned(5.0, DUCT) and partial.expected(5.0, DUCT) is None
    assert partial.to_dict(DUCT)["+4..+6"]["learned"] is False
    assert cm.to_dict(DUCT)["+4..+6"]["learned"] is True


def test_a_change_of_the_duct_factor_scales_the_cop_linearly():
    cm, _ = run_series(SHORT_FIRST + [(24.0, 310.0, 1270.0)] * 5)
    at_09 = cm.to_dict(0.9)["+4..+6"]
    at_08 = cm.to_dict(0.8)["+4..+6"]
    assert at_08["cop"] == pytest.approx(at_09["cop"] * 0.8 / 0.9, abs=0.002)
    assert at_08["electricity_kwh"] == at_09["electricity_kwh"] and at_08["runtime_h"] == at_09["runtime_h"]
    assert at_08["heat_kwh"] == pytest.approx(at_09["heat_kwh"] * 0.8 / 0.9, abs=0.01)
    assert cm.expected(5.0, 0.8) == pytest.approx(cm.expected(5.0, 0.9) * 0.8 / 0.9, abs=0.002)


# ------------------------------------------------------------------ what counts
def test_the_start_and_the_run_on_count():
    tracker, cm = HeatPumpTracker(), CopMap()
    feed(tracker, cm, 0.0, 5.0, 0.0, 12.0)
    assert feed(tracker, cm, 60.0, 5.0, 60.0, 700.0, 1700.0) is not None  # the first minute of a start
    assert feed(tracker, cm, 120.0, 5.0, 0.0, 30.0, 800.0) is not None  # stopped, the air still warm
    band = cm.bands[2]
    assert band.run_s == pytest.approx(120.0, abs=0.01)
    assert band.electricity_wh == pytest.approx((12 + 700) / 2 / 60 + (700 + 30) / 2 / 60, abs=0.01)


def test_the_heat_is_stored_without_the_duct_factor():
    tracker, cm = HeatPumpTracker(), CopMap()
    feed(tracker, cm, 0.0, 5.0, 24.0, 310.0, 1270.0)
    feed(tracker, cm, 60.0, 5.0, 24.0, 310.0, 1270.0)
    assert cm.bands[2].heat_wh == pytest.approx(1270.0 / DUCT / 60.0, rel=0.01)


def test_the_fan_run_on_before_the_unit_is_switched_off_counts():
    tracker, cm = HeatPumpTracker(), CopMap()
    feed(tracker, cm, 0.0, 5.0, 24.0, 310.0, 1270.0)
    assert feed(tracker, cm, 60.0, 5.0, 0.0, 30.0, 600.0, mode="fan_only") is not None
    assert feed(tracker, cm, 120.0, 5.0, 0.0, 30.0, 300.0, mode="fan_only") is not None
    assert feed(tracker, cm, 180.0, 5.0, 0.0, 2.0, mode="off") is None  # off: over
    assert tracker.band_last is None


def test_a_long_rest_adds_neither_run_time_nor_electricity():
    # Resting in heat a unit draws a few watts and gives no heat. Counted, a band it rests in day after day
    # would be "learned" with a COP of 0, and the mild bands would hold more standby than heat.
    tracker, cm = HeatPumpTracker(), CopMap()
    for minute in range(600):
        feed(tracker, cm, minute * 60.0, 15.0, 0.0, 12.0)
    assert cm.bands == {} and not cm.learned(15.0, DUCT)


def test_a_rest_with_an_outlet_reading_a_little_above_the_intake_is_no_run_either():
    # Two sensors never read alike: past the run-on a few tenths of "heat" are the difference of the sensors.
    tracker, cm = HeatPumpTracker(), CopMap()
    feed(tracker, cm, 0.0, 15.0, 24.0, 310.0, 1270.0)
    counted = [feed(tracker, cm, minute * 60.0, 15.0, 0.0, 12.0, 40.0) for minute in range(1, 120)]
    assert counted[0] is not None and counted[-1] is None
    assert cm.bands[7].run_s <= 900.0


def test_a_hole_of_more_than_three_minutes_and_a_missing_value_are_skipped():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 310.0, 1270.0)
    assert tick(tracker, 240.0, 24.0, 310.0, 1270.0) is None
    assert tick(tracker, 300.0, 24.0, 310.0, 1270.0) is not None
    assert tick(tracker, 360.0, 24.0, None, 1270.0, run_state="heating_stable") is None  # no power reading
    assert tick(tracker, 420.0, 24.0, 310.0, 1270.0) is None  # nor the interval from it
    assert tick(tracker, 480.0, 24.0, 310.0, 1270.0, outlet=reading(None)) is None
    assert tick(tracker, 540.0, 24.0, 310.0, 1270.0) is None
    assert tick(tracker, 600.0, 24.0, 310.0, 1270.0) is not None


def test_a_defrost_counts_with_its_electricity_and_no_heat():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 89.0, 900.0, fan=0.0, defrost=DEFROST)
    heat_wh, electricity_wh, seconds = tick(tracker, 60.0, 89.0, 900.0, fan=0.0, defrost=DEFROST)
    assert heat_wh == 0.0 and electricity_wh == pytest.approx(15.0) and seconds == 60.0


def test_a_defrost_with_the_compressor_standing_counts_too():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 0.0, 200.0, fan=0.0, defrost=DEFROST)
    assert tick(tracker, 60.0, 0.0, 200.0, fan=0.0, defrost=DEFROST) is not None


@pytest.mark.parametrize("why", ["quick_heat_up", "bathroom_drying", "lift"])
def test_running_above_the_least_speed_stays_out(why):
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 310.0, 1270.0)
    assert tick(tracker, 60.0, 60.0, 800.0, 2400.0, above=why) is None
    assert tick(tracker, 120.0, 60.0, 800.0, 2400.0, above=why) is None
    assert tick(tracker, 180.0, 24.0, 310.0, 1270.0) is None  # the stretch from it still is such a run
    assert tick(tracker, 240.0, 24.0, 310.0, 1270.0) is not None


def test_an_outlet_hung_cold_under_a_settled_compressor_counts_as_nothing():
    # Valid for up to an hour, and heat 0 with all the electricity would stay in the band for good: learned below
    # the off limit it is not run again, so nothing that follows could lift it. The old table had a gate for this.
    tracker, cm = HeatPumpTracker(), CopMap()
    for second in range(0, 59 * 60, 60):
        # no heat at all, then less than a running unit ever gives
        hung = INTAKE - 0.5 if second < 30 * 60 else INTAKE + 2.0
        assert feed(tracker, cm, float(second), 1.0, 24.0, 310.0, outlet=reading(hung, age_s=second + 1.0)) is None
    assert cm.bands == {}
    start = 3600.0
    for minute in range(186):
        feed(tracker, cm, start + minute * 60.0, 1.0, 24.0, 310.0, 1270.0)
    assert cm.bands[0].run_s == pytest.approx(185 * 60.0, rel=0.02)
    assert cm.estimate(1.0, DUCT) == (pytest.approx(1270.0 / 310.0, rel=0.005), "learned")


def _settled_cop(outlet):
    tracker, result = HeatPumpTracker(), None
    for minute in range(14):
        unit = heat_pump_state(compressor_hz=24.0, running_s=1320.0 + minute * 60.0, plug_power=310.0, intake=(INTAKE, RH), outlet=(INTAKE, None), fan_rpm=RPM)
        unit = replace(unit, outlet_temp=outlet(minute))
        res = HeatPumpResult("heating_stable", 310.0, "plug", True)
        result = tracker.cop(SimpleNamespace(heat_pump=unit), res, NO_DEFROST, PARAMS_DUCT, minute * 60.0)
    return tracker, result


def test_an_outlet_silent_since_before_the_start_gives_no_settled_cop():
    """Kept valid while its device reports, it would decide between heat pump and gas with a value from before this run."""
    tracker, result = _settled_cop(lambda minute: reading(INTAKE + 8.0, age_s=7200.0 + minute * 60.0))
    assert not tracker.cop_settled and "outlet_temp_silent" in result.gate_reasons


def test_an_outlet_quiet_within_the_run_still_gives_a_settled_cop():
    tracker, _ = _settled_cop(lambda minute: reading(INTAKE + 8.0, age_s=600.0 + minute * 60.0))
    assert tracker.cop_settled


def test_a_hung_outlet_does_not_pull_an_old_value_down_either():
    cm = CopMap.from_storage({"bins": {"2": {"count": 40, "mean": 3.73, "min": 2.0, "max": 5.0}}})
    tracker = HeatPumpTracker()
    for minute in range(40):
        feed(tracker, cm, minute * 60.0, 5.0, 24.0, 310.0, outlet=reading(INTAKE - 0.5, age_s=minute * 60.0))
    assert cm.estimate(5.0, DUCT) == (3.73, "legacy")


def learned_band():
    """Three hours of truth at 340 W and a COP of 4.0, then a rest past the run-on."""
    tracker, cm = HeatPumpTracker(), CopMap()
    for minute in range(190):
        feed(tracker, cm, minute * 60.0, 5.0, 24.0, 340.0, 1360.0)
    for minute in range(190, 210):
        feed(tracker, cm, minute * 60.0, 5.0, 0.0, 12.0, fan=330.0)
    return tracker, cm, 210 * 60.0


@pytest.mark.parametrize("rise", [20.0, 25.0])
def test_an_outlet_hung_warm_under_a_settled_compressor_counts_as_nothing(rise):
    # A sensor dying in a hard start stays valid for an hour on a reading the unit does not give at 24 Hz. Counted, it
    # learned a band at 5.3 for a true 4.0, and that starts the unit from gas until the half-life has diluted it.
    tracker, cm = HeatPumpTracker(), CopMap()
    for minute in range(60):
        hung = reading(INTAKE + rise, age_s=minute * 60.0)
        assert feed(tracker, cm, minute * 60.0, 5.0, 24.0, 340.0, outlet=hung, running=2 * HOUR + minute * 60.0) is None
    assert cm.bands == {}
    for minute in range(60, 190):
        feed(tracker, cm, minute * 60.0, 5.0, 24.0, 340.0, 1360.0)
    assert cm.to_dict(DUCT)["+4..+6"]["cop"] == pytest.approx(4.0, abs=0.01)


def test_only_a_settled_compressor_is_held_to_the_highest_cop():
    # In the first minutes the electricity falls ahead of the outlet, and what a start gave still comes in the run-on.
    tracker = HeatPumpTracker()
    warm = reading(INTAKE + 20.0)
    tick(tracker, 0.0, 24.0, 340.0, outlet=warm, running=300.0)
    assert tick(tracker, 60.0, 24.0, 340.0, outlet=warm, running=360.0) is not None
    tick(tracker, 120.0, 0.0, 30.0, outlet=warm)
    assert tick(tracker, 180.0, 0.0, 30.0, outlet=warm) is not None
    # and up to the limit a settled one counts: a value high but possible is no sign of a hung sensor
    tracker = HeatPumpTracker()
    high = reading(INTAKE + 15.0)
    tick(tracker, 0.0, 24.0, 340.0, outlet=high)
    assert tick(tracker, 60.0, 24.0, 340.0, outlet=high) is not None


def short_runs_on_a_dead_outlet(tracker, cm, start, outlet_c, runs):
    """Runs of 15 minutes with rests of 5, the outlet sensor silent since before the first of them."""
    now, age = start, 60.0
    for _ in range(runs):
        for _ in range(5):
            now, age = now + 60.0, age + 60.0
            feed(tracker, cm, now, 5.0, 0.0, 12.0, outlet=reading(outlet_c, age_s=age), fan=330.0)
        for minute in range(15):
            now, age = now + 60.0, age + 60.0
            hz, power = (60.0, 1000.0) if minute < 3 else (24.0, 340.0)
            feed(tracker, cm, now, 5.0, hz, power, outlet=reading(outlet_c, age_s=age), running=(minute + 1) * 60.0)


@pytest.mark.parametrize("outlet_c", [INTAKE, INTAKE + 8.0], ids=["cold", "warm"])
@pytest.mark.parametrize("runs", [1, 3])
def test_a_run_the_outlet_sensor_never_reacted_to_stays_out_whatever_it_shows(outlet_c, runs):
    # All 17 starts of 2026-10-31 to 11-01 moved the sensor within 61 seconds. Each start counted on a dead one took
    # 7 % off a learned band, three of them 19 %: one more and the band no longer starts the unit.
    tracker, cm, start = learned_band()
    run_s = cm.bands[2].run_s
    short_runs_on_a_dead_outlet(tracker, cm, start, outlet_c, runs)
    assert cm.bands[2].run_s == pytest.approx(run_s)


def test_the_run_on_of_a_run_with_a_hung_outlet_stays_out_with_it():
    # Above the limit under the compressor, and the same reading after the stop is no heat either.
    tracker = HeatPumpTracker()
    hung = reading(INTAKE + 20.0, age_s=600.0)
    tick(tracker, 0.0, 24.0, 340.0, outlet=hung, running=HOUR)
    tick(tracker, 60.0, 24.0, 340.0, outlet=hung, running=HOUR + 60.0)
    assert tick(tracker, 120.0, 0.0, 30.0, outlet=hung) is None
    assert tick(tracker, 180.0, 0.0, 30.0, outlet=hung) is None
    # And a short run the sensor did not react to.
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 0.0, 12.0, outlet=reading(INTAKE + 8.0, age_s=900.0))
    tick(tracker, 60.0, 60.0, 700.0, outlet=reading(INTAKE + 8.0, age_s=960.0), running=30.0)
    tick(tracker, 120.0, 60.0, 700.0, outlet=reading(INTAKE + 8.0, age_s=1020.0), running=90.0)
    assert tick(tracker, 180.0, 0.0, 30.0, outlet=reading(INTAKE + 8.0, age_s=1080.0)) is None
    assert tick(tracker, 240.0, 0.0, 30.0, outlet=reading(INTAKE + 8.0, age_s=1140.0)) is None


def test_a_start_is_held_until_the_sensor_has_reacted_and_then_counts_in_full():
    tracker = HeatPumpTracker()
    quiet = 900.0
    tick(tracker, 0.0, 0.0, 12.0, outlet=reading(INTAKE, age_s=quiet))
    assert tick(tracker, 60.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=quiet + 60.0), running=30.0) is None
    assert tick(tracker, 120.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=quiet + 120.0), running=90.0) is None
    moved = reading(outlet_for(1360.0), age_s=20.0)
    heat, electricity, dt = tick(tracker, 180.0, 60.0, 700.0, 1360.0, outlet=moved, running=150.0)
    assert dt == 180.0
    assert electricity == pytest.approx(((12.0 + 700.0) / 2.0 + 700.0 + 700.0) / 60.0)
    assert heat == pytest.approx(1360.0 / DUCT / 2.0 / 60.0, rel=0.01)  # only the last stretch shows any
    assert tracker.band_held is None
    assert tick(tracker, 240.0, 60.0, 700.0, 1360.0, running=210.0)[2] == 60.0  # from here on as ever


def test_a_short_run_that_stops_before_the_sensor_reacted_stays_out_and_one_it_reacted_to_at_the_stop_counts():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 0.0, 12.0, outlet=reading(INTAKE, age_s=900.0))
    tick(tracker, 60.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=960.0), running=30.0)
    tick(tracker, 120.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=1020.0), running=90.0)
    assert tick(tracker, 180.0, 0.0, 30.0, outlet=reading(INTAKE, age_s=1080.0)) is None
    assert tracker.band_held is None
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 0.0, 12.0, outlet=reading(INTAKE, age_s=900.0))
    tick(tracker, 60.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=960.0), running=30.0)
    tick(tracker, 120.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=1020.0), running=90.0)
    assert tick(tracker, 180.0, 0.0, 30.0, 1360.0, outlet=reading(outlet_for(1360.0), age_s=5.0))[2] == 180.0


def test_a_start_held_when_the_unit_is_switched_off_goes_with_it():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 0.0, 12.0, outlet=reading(INTAKE, age_s=900.0))
    tick(tracker, 60.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=960.0), running=30.0)
    assert tracker.band_held is not None
    assert tick(tracker, 120.0, 0.0, 5.0, mode="off") is None
    assert tracker.band_held is None


def test_a_quiet_sensor_is_not_hung_where_nothing_moves_the_air_past_it_or_the_heat_is_truly_gone():
    silent = reading(INTAKE, age_s=HOUR / 2.0)
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 340.0, outlet=silent, defrost=DEFROST)
    assert tick(tracker, 60.0, 24.0, 340.0, outlet=silent, defrost=DEFROST)[1] == pytest.approx(340.0 / 60.0)
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 340.0, outlet=silent, fan=0.0)
    assert tick(tracker, 60.0, 24.0, 340.0, outlet=silent, fan=0.0)[1] == pytest.approx(340.0 / 60.0)
    # in a long run a report of half an hour ago says nothing: bursts come about every 32 minutes
    quiet = reading(outlet_for(1360.0), age_s=HOUR / 2.0)
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 340.0, outlet=quiet, running=HOUR)
    assert tick(tracker, 60.0, 24.0, 340.0, outlet=quiet, running=HOUR + 60.0) is not None


def test_a_start_and_a_defrost_count_though_they_show_no_heat():
    # At a start the sensor has not seen the warm air yet, and what it misses it shows after the stop.
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 60.0, 700.0, running=60.0)
    interval = tick(tracker, 60.0, 60.0, 700.0, running=120.0)
    assert interval[0] == 0.0 and interval[1] == pytest.approx(700.0 / 60.0)
    # A defrost: settled, the coil reversed, the outlet cold, with the fan on or off.
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 60.0, 700.0, defrost=DEFROST)
    assert tick(tracker, 60.0, 60.0, 700.0, defrost=DEFROST)[1] == pytest.approx(700.0 / 60.0)
    # A unit whose defrost goes unseen stops its fan for it: no air, no heat, as a plain fact.
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 60.0, 700.0, fan=0.0)
    assert tick(tracker, 60.0, 60.0, 700.0, fan=0.0)[1] == pytest.approx(700.0 / 60.0)


def test_the_run_on_of_a_run_above_the_least_speed_stays_out_with_it():
    # Its heat with next to no electricity, and the electricity of the run stays out: a daily drying over a month would
    # lift the bands by up to 5 %. The memory of the drying ends with the stop, so this is kept here.
    tracker = HeatPumpTracker()
    for minute in range(5):
        assert tick(tracker, minute * 60.0, 40.0, 800.0, 2500.0, above="bathroom_drying") is None
    for minute in range(5, 20):
        assert tick(tracker, minute * 60.0, 0.0, 15.0, 2000.0, mode="fan_only") is None
    # The next run at the least speed counts from the sample after its start.
    assert tick(tracker, 20 * 60.0, 24.0, 310.0, 1270.0, running=60.0) is None
    assert tick(tracker, 21 * 60.0, 24.0, 310.0, 1270.0, running=120.0) is not None


def test_a_run_that_falls_back_to_the_least_speed_counts_from_then_on():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 40.0, 800.0, 2500.0, above="lift")
    assert tick(tracker, 60.0, 24.0, 310.0, 1270.0) is None
    assert tick(tracker, 120.0, 24.0, 310.0, 1270.0) is not None
    tick(tracker, 180.0, 0.0, 15.0, 800.0, mode="fan_only")  # and its stop like any other
    assert tick(tracker, 240.0, 0.0, 15.0, 800.0, mode="fan_only") is not None


def test_each_interval_goes_to_the_band_of_its_outdoor_temperature():
    tracker, cm = HeatPumpTracker(), CopMap()
    for minute, outdoor in enumerate((5.5, 5.5, 5.5, 6.5, 6.5)):
        feed(tracker, cm, minute * 60.0, outdoor, 24.0, 310.0, 1270.0)
    assert cm.bands[2].run_s == pytest.approx(120.0, abs=0.01) and cm.bands[3].run_s == pytest.approx(120.0, abs=0.01)
    assert cm.bands[3].last_ts == 240.0


def test_reset_forgets_the_interval_under_way():
    tracker = HeatPumpTracker()
    tick(tracker, 0.0, 24.0, 310.0, 1270.0)
    tick(tracker, 60.0, 24.0, 310.0, 1270.0)
    tracker.reset()
    assert tracker.band_interval is None and tracker.band_last is None
    tick(tracker, 120.0, 0.0, 12.0, outlet=reading(INTAKE, age_s=900.0))
    tick(tracker, 180.0, 60.0, 700.0, outlet=reading(INTAKE, age_s=960.0), running=30.0)
    assert tracker.band_held is not None
    tracker.reset()
    assert tracker.band_held is None


# ------------------------------------------------------------------ forgetting
def run(cm, outdoor, cop, hours, start=0.0, power=310.0, step=60.0):
    """Hours of run at this COP (with duct factor 1), added up in steps as a coordinator would."""
    for i in range(round(hours * HOUR / step)):
        cm.add_interval(outdoor, power * cop * step / HOUR, power * step / HOUR, step, start + i * step)


def test_old_data_fades_with_the_run_time_of_its_band():
    cm = CopMap()
    assert COP_BAND_HALF_LIFE_S == 100 * HOUR
    run(cm, 5.0, 4.0, 100.0, step=600.0)
    run(cm, 5.0, 2.0, 100.0, step=600.0)
    # 100 hours later the first hundred count half: (4 x 1/4 + 2 x 1/2) / (1/4 + 1/2)
    assert cm.expected(5.0, 1.0) == pytest.approx(8.0 / 3.0, abs=0.01)


def test_a_band_idle_for_a_season_keeps_its_data_and_stays_learned():
    cm = CopMap()
    run(cm, 5.0, 4.0, 4.0)
    before = cm.to_dict(1.0)["+4..+6"]
    run(cm, 15.0, 5.0, 400.0, start=1e7, step=600.0)  # a whole season elsewhere
    assert cm.to_dict(1.0)["+4..+6"] == before and cm.learned(5.0, 1.0)


def test_the_run_time_of_a_band_levels_off_where_the_fading_balances_it():
    cm = CopMap()
    run(cm, 5.0, 4.0, 2000.0, step=3600.0)
    assert cm.bands[2].run_s == pytest.approx(COP_BAND_HALF_LIFE_S / 0.6931, rel=0.01)
    assert cm.learned(5.0, 1.0)


# ------------------------------------------------------------------ reading the table
PORTASPLIT = {1: (3.61, 133), 2: (3.73, 111)}


def test_what_stands_in_until_a_band_is_learned_comes_in_the_order_of_old():
    cm = CopMap()
    assert cm.estimate(5.0, 1.0, PORTASPLIT) == (3.73, "profile")
    assert cm.estimate(5.0, 1.0) == (round(prior_cop(5.0) * PRIOR_CALIBRATION_DEFAULT, 3), "prior")
    cm = CopMap.from_storage({"bins": {"2": {"count": 20, "mean": 3.5}}})
    assert cm.estimate(5.0, 1.0, PORTASPLIT) == (3.5, "legacy")
    run(cm, 7.0, 4.0, 3.1)  # the neighbour is learned: before what is left of the old table
    assert cm.estimate(5.0, 1.0, PORTASPLIT) == (4.0, "learned")
    run(cm, 5.0, 3.0, 3.1)  # and its own band before the neighbour
    assert cm.estimate(5.0, 1.0, PORTASPLIT) == (3.0, "learned")


def test_a_band_on_the_way_does_not_decide_by_itself():
    cm = CopMap()
    run(cm, 5.0, 2.0, 2.0)
    assert cm.estimate(5.0, 1.0, PORTASPLIT) == (3.73, "profile")
    assert cm.calibration_factor(1.0) is None
    assert cm.to_dict(1.0)["+4..+6"]["basis"] == "learning"


def test_the_calibration_is_weighted_by_run_time():
    cm = CopMap()
    run(cm, 5.0, prior_cop(5.0) * 0.5, 3.1)  # the centre of its band
    run(cm, 7.0, prior_cop(7.0) * 1.0, 9.3)
    assert cm.calibration_factor(1.0) == pytest.approx((3.1 * 0.5 + 9.3 * 1.0) / 12.4, abs=0.003)
    assert cm.estimate(-5.0, 1.0)[1] == "prior_calibrated"


def test_a_band_with_an_impossible_cop_does_not_count_as_learned():
    # A frozen cold outlet reading shows no heat under a running compressor: 3 hours of that say the sensor is wrong.
    cm = CopMap()
    run(cm, 5.0, 0.2, 4.0)
    assert not cm.learned(5.0, 1.0) and cm.expected(5.0, 1.0) is None
    assert cm.to_dict(1.0)["+4..+6"]["learned"] is False


def test_the_attributes_of_a_band():
    cm = CopMap()
    run(cm, 5.0, 3.0, 3.0, power=300.0)
    shown = cm.to_dict(0.5)
    assert set(shown["+4..+6"]) == {"runtime_h", "heat_kwh", "electricity_kwh", "cop", "learned", "basis"}
    assert shown["+4..+6"]["cop"] == 1.5
    assert shown["+4..+6"]["runtime_h"] == pytest.approx(3.0, abs=0.05)
    assert shown["+4..+6"]["electricity_kwh"] == pytest.approx(0.9, abs=0.02)
    assert shown["+4..+6"]["heat_kwh"] == pytest.approx(1.35, abs=0.03)


# ------------------------------------------------------------------ the old table
OLD = {
    "bins": {"2": {"count": 111, "mean": 3.73, "min": 2.278, "max": 4.9}, "3": {"count": 3, "mean": 5.0, "min": 5.0, "max": 5.0}}
}


def test_a_store_of_the_old_format_is_kept_as_a_weak_basis():
    cm = CopMap.from_storage(OLD)
    assert cm.estimate(5.0, 0.9) == (3.73, "legacy")  # no jump after the update
    assert cm.estimate(3.0, 0.9) == (3.73, "legacy")  # nor for its neighbours, as before
    assert not cm.learned(5.0, 0.9)
    shown = cm.to_dict(0.9)["+4..+6"]
    assert shown["basis"] == "legacy" and shown["cop"] == 3.73 and shown["runtime_h"] == 0.0 and shown["learned"] is False


def test_an_old_bin_with_too_few_entries_counts_as_little_as_it_did():
    cm = CopMap.from_storage(OLD)
    assert "+6..+8" not in cm.to_dict(0.9)
    assert cm.estimate(7.0, 0.9) == (3.73, "legacy")  # the neighbour, not 5.0 of three entries


def test_the_old_value_weighs_one_hour_against_what_the_band_measured():
    cm = CopMap.from_storage(OLD)
    run(cm, 5.0, 3.0, 2.0, power=LEGACY_WORTH_WH)
    run_h = cm.bands[2].run_s / HOUR
    expected = (3.0 * run_h + 3.73 * 1.0) / (run_h + 1.0)
    assert cm.estimate(5.0, 1.0) == (pytest.approx(expected, abs=0.002), "legacy")


def test_the_old_value_keeps_its_worth_when_the_band_has_drawn_next_to_nothing():
    # A band with nothing but a run-on after a stop at its edge: its mean draw is no measure of an hour's worth.
    cm = CopMap.from_storage(OLD)
    cm.add_interval(5.0, 200.0, 15.0, 900.0, 0.0)
    assert cm.estimate(5.0, 1.0)[0] == pytest.approx((200.0 + 3.73 * LEGACY_WORTH_WH) / (15.0 + LEGACY_WORTH_WH), abs=0.002)
    assert cm.estimate(5.0, 1.0)[0] < 4.5


@pytest.mark.parametrize("cop", [0.5, 12.0])
def test_an_old_value_blended_out_of_the_plausible_range_decides_nothing(cop):
    # As for a learned band: it says more about the sensors than about the unit, so the next in line stands in.
    cm = CopMap.from_storage(OLD)
    run(cm, 5.0, cop, 10.0)
    value, basis = cm.estimate(5.0, 1.0)
    assert basis == "prior"
    assert value == pytest.approx(prior_cop(5.0) * PRIOR_CALIBRATION_DEFAULT, abs=0.002)
    assert cm.to_dict(1.0)["+4..+6"]["basis"] == "learning"


def test_the_old_values_calibrate_the_curve_as_they_did_before_the_update():
    # By their entries, or the bands that have measured nothing jump at the update: 0.83 to 0.79 on the store of 2026-11-02,
    # which took -2..0 from 2.74 to 2.59 and over the line between heat pump and gas.
    bins = {
        "-1": {"count": 30, "mean": 3.2, "min": 3.0, "max": 3.4},
        "2": {"count": 111, "mean": 3.73, "min": 2.0, "max": 5.0},
        "4": {"count": 9, "mean": 4.9, "min": 4.0, "max": 5.5},
    }
    cm = CopMap.from_storage({"bins": bins})
    entries = [(int(i), b["mean"], b["count"]) for i, b in bins.items()]
    by_count = sum(n * mean / prior_cop(i * 2.0 + 1.0) for i, mean, n in entries) / sum(n for _, _, n in entries)
    assert cm.calibration_factor(1.0) == pytest.approx(by_count, abs=0.001)


def test_no_old_bin_weighs_more_than_an_hour_in_the_calibration():
    # One bin that was fed all season must not outweigh what was measured since.
    cm = CopMap.from_storage({"bins": {"2": {"count": 500, "mean": 3.73, "min": 2.0, "max": 5.0}}})
    run(cm, 9.0, prior_cop(9.0), 3.1)
    measured = cm.bands[4].run_s
    expected = (3.73 / prior_cop(5.0) * HOUR + measured) / (HOUR + measured)
    assert cm.calibration_factor(1.0) == pytest.approx(expected, abs=0.003)


def test_once_learned_a_band_stands_on_what_it_measured():
    cm = CopMap.from_storage(OLD)
    run(cm, 5.0, 3.0, 3.1)
    assert cm.estimate(5.0, 1.0) == (3.0, "learned")
    assert cm.to_dict(1.0)["+4..+6"]["basis"] == "learned"


def test_the_old_values_scale_with_the_duct_factor_they_were_measured_with():
    # They cannot be taken apart, but a change of the factor used to forget the table: all of them have the one on record.
    cm = CopMap.from_storage(OLD)
    cm.bind("auto", PARAMS.airflow_curve, 0.9)
    assert cm.estimate(5.0, 0.8) == (pytest.approx(3.73 * 0.8 / 0.9, abs=0.002), "legacy")
    assert cm.to_dict(0.8)["+4..+6"]["cop"] == pytest.approx(3.73 * 0.8 / 0.9, abs=0.002)


def test_the_old_values_calibrate_the_curve_with_the_weight_of_an_hour():
    cm = CopMap.from_storage(OLD)
    assert cm.calibration_factor(0.9) == pytest.approx(3.73 / prior_cop(5.0), abs=0.002)
    assert cm.estimate(-5.0, 0.9)[1] == "prior_calibrated"
    run(cm, 7.0, prior_cop(7.0), 3.1)
    assert cm.calibration_factor(1.0) == pytest.approx((3.73 / prior_cop(5.0) + 3.05 * 1.0) / 4.05, abs=0.003)


@pytest.mark.parametrize("damaged", ["nan", "inf", "-inf"])
def test_a_damaged_number_in_the_store_is_dropped_and_never_stops_a_save(damaged):
    # round() raises on these, on every save: the store would not be written again and a reload would fail.
    good = {"heat_wh": 100.0, "electricity_wh": 30.0, "run_s": 60.0, "last_ts": damaged}
    cm = CopMap.from_storage({
        "bands": {"1": good, "2": {"heat_wh": 100.0, "electricity_wh": 30.0, "run_s": damaged}},
        "bins": {"3": {"count": 40, "mean": damaged, "min": 2.0, "max": 5.0}},
        "legacy_duct_factor": damaged,
    })
    assert set(cm.bands) == {1} and cm.bands[1].last_ts is None
    assert cm.legacy == {} and cm.legacy_duct_factor is None
    assert cm.to_storage()["bands"]["1"]["run_s"] == 60


def test_the_store_round_trip_keeps_bands_and_old_values():
    cm = CopMap.from_storage(OLD)
    run(cm, 5.0, 3.0, 3.5)
    cm.bind("auto", PARAMS.airflow_curve, 0.9)
    stored = cm.to_storage()
    assert stored["bins"] == OLD["bins"]  # an older version reads these, as they were
    assert set(stored["bands"]["2"]) == {"heat_wh", "electricity_wh", "run_s", "last_ts"}
    restored = CopMap.from_storage(stored)
    assert restored.to_dict(0.9) == cm.to_dict(0.9)
    assert restored.measured_with == cm.measured_with and restored.legacy_duct_factor == 0.9


@pytest.mark.parametrize(
    "garbage",
    [
        None, 5, "x", [], {"bins": 5}, {"bands": 5}, {"bands": {"x": {}}}, {"bands": {"2": "x"}},
        {"bands": {"2": {"heat_wh": "x"}}}, {"bins": {"2": 7}}, {"measured_with": 5, "legacy_duct_factor": "x"},
        {"bands": {"2": {"heat_wh": -5.0, "electricity_wh": -1.0, "run_s": -1.0}}},
    ],
)
def test_a_damaged_store_is_read_without_harm(garbage):
    cm = CopMap.from_storage(garbage)
    assert cm.estimate(5.0, 0.9)[1] == "prior"
    assert not cm.learned(5.0, 0.9)
    cm.to_dict(0.9)
    cm.to_storage()


def test_bind_reports_a_change_of_what_the_data_was_measured_with():
    curve = PARAMS.airflow_curve
    cm = CopMap()
    assert cm.bind("auto", curve, 0.9) is False  # first seen: taken as it is
    assert cm.bind("auto", curve, 0.8) is False  # the duct factor scales
    assert cm.bind("high", curve, 0.9) is True
    assert cm.bind("auto", curve[:2], 0.9) is True
