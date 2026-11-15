"""Findings of the engine review of 2026-11-13: each test failed for the fault its docstring names, until it was fixed."""

from dataclasses import replace
from datetime import datetime

from custom_components.thriftherm.engines import gas_split, room_control, safety
from custom_components.thriftherm.engines import heat_pump as heat_pump_engine
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.heat_pump_control import SETPOINT_MIN, ControlInputs, ControlMemory, decide

from .conftest import PARAMS, heat_pump_state, room_config, room_result, room_state, snapshot

MONDAY_NIGHT = datetime(2026, 1, 12, 23, 30)  # outside every comfort window, no preheat within reach
NOW = 1_000_000.0


def test_a_frost_room_can_leave_the_frost_list_by_being_heated():
    """Finding 1a (safety.py:19/88, room.py:337): a room stays on the frost list until 1 K above the frost limit, and
    the boiler is released for as long as it is on it. Its thermostat is only ever given the frost limit itself (mode
    "off": 7 °C), so the valve shuts at 7 °C, the room never reaches 8 °C, and the release never ends by the heating
    it causes: only by the weather."""
    cfg = room_config("keller")
    temp, frost = 6.9, ()
    for _ in range(60):
        state = room_state(cfg, temp=temp)
        result = room_engine.evaluate_room(state, MONDAY_NIGHT, "off", PARAMS, frost="keller" in frost)
        frost = safety.evaluate(snapshot({"keller": state}, mode="off", now=MONDAY_NIGHT), {"keller": result}, frost).frost_rooms
        setpoint = room_control.thermostat_setpoint(result.target)
        if frost and temp < setpoint:  # the boiler is released, and the valve is open below its setpoint
            temp = round(temp + 0.1, 1)
    assert frost == (), f"still on the frost list at {temp} °C with the thermostat at {setpoint} °C"
    # half a degree above where it leaves the list: a valve that shuts a little early still gets the room there
    listed = room_engine.evaluate_room(room_state(cfg, temp=7.9), MONDAY_NIGHT, "off", PARAMS, frost=True)
    assert listed.target == PARAMS.frost_temp + safety.FROST_HYSTERESIS_K + 0.5


def test_a_room_below_the_frost_limit_is_never_given_a_target_below_it():
    """Finding 1b (room.py:334-351, 353): override, away temperature and setback go to the thermostat as they are,
    also below the frost limit (away 8-20 °C, setback and override from 5 °C, frost limit up to 12 °C; no form
    compares them). The room is on the frost list and the boiler released for it, while its valve stays shut."""
    cfg = room_config("keller", setback=5.0)
    params = replace(PARAMS, frost_temp=10.0, away_temp=8.0)
    for mode, temp in (("away", 9.5), ("auto", 6.5)):
        state = room_state(cfg, temp=temp)
        result = room_engine.evaluate_room(state, MONDAY_NIGHT, mode, params)
        snap = snapshot({"keller": state}, mode=mode, now=MONDAY_NIGHT, params=params)
        frost = safety.evaluate(snap, {"keller": result}).frost_rooms
        assert frost == ("keller",)
        assert result.target >= params.frost_temp, f"mode {mode}: target {result.target} °C ({result.target_reason}) at {temp} °C"


def test_the_heat_pump_is_never_sent_a_setpoint_below_its_minimum():
    """Finding 2 (heat_pump_control.py:478-483): `_held` caps a setpoint at the warmest wanted target + 3 K after it
    raised it to the minimum, so with every served room wanted below 14 °C (a setback of 12 °C) the unit is sent
    15 °C. SETPOINT_MIN is 17 °C because units refuse less: Home Assistant rejects the call, or the unit shows 17 °C
    and the command counts as not carried out, again and again."""
    room = room_result("schlafzimmer", 12.0, 11.8, target_reason="schedule_setback")
    unit = replace(heat_pump_state(hvac_mode="off", compressor_hz=0.0, running_s=None, intake=(12.0, 45.0)), indoor_temp=12.0)
    inputs = ControlInputs(
        now_ts=NOW, control_mode="active", op_mode="auto", heat_pump=unit, run_state=heat_pump_engine.run_state(unit, PARAMS),
        rooms=(room,), advice_source="midea", blocked_reason=None, safety_state="ok", outdoor_c=5.0, expected_cop=3.5,
        break_even_cop=2.89, outdoor_bin=2, bin_learned=True, params=PARAMS, cheaper=True,
    )
    command, _ = decide(inputs, ControlMemory())
    assert command.action == "start"
    assert command.target_temp >= SETPOINT_MIN, f"sent {command.target_temp} °C"


def test_a_meter_that_reports_every_five_minutes_has_its_gas_credited():
    """Finding 3 (gas_split.py:137-140): a look that finds the meter unchanged still moves `counted_ts`, so a step
    is capped by the flow of one cycle (6 m³/h for 60 s: 0.1 m³), not of the time the meter took to report it. A
    meter that reports every five minutes while the burner takes 2.4 m³/h has half of its gas put to "unassigned"."""
    split, meter, kwh_per_m3 = gas_split.GasSplit(), 100.0, 10.0
    for minute in range(61):
        if minute and minute % 5 == 0:
            meter = round(meter + 0.2, 1)
        split = gas_split.by_meter(split, meter, kwh_per_m3, False, NOW + minute * 60.0)
    assert (split.heating_kwh, split.unassigned_kwh) == (24.0, 0.0)


def test_a_damaged_store_never_stops_the_control_memory_from_loading():
    """Finding 5 (heat_pump_control.py:305-309): `learning_bins` entries that are mappings raise KeyError, which
    the loop does not catch; persistence.restore promises "a bad value is dropped, never fatal"."""
    memory = ControlMemory.from_storage({"learning_bins": [{"bin": 2}, [3, 1000.0]], "last_target": 20.0})
    assert memory.learning_bins == ((3, 1000.0),) and memory.last_target == 20.0


def test_stored_rates_outside_what_can_be_learned_are_dropped_on_loading():
    """Finding 5, the rest: `observe` keeps a heat-up rate only between 0.1 and 6 K/h and a cool-down coefficient
    only between 0.002 and 0.25 per hour, but a store was taken as it stood. A rate of 0 or below planned every
    preheat at the margin alone (20 minutes), and a count of "inf" in the old COP table raised OverflowError."""
    from custom_components.thriftherm.engines.learning import HEAT_BY_BOILER, CoolRateStats, CopMap, HeatRateStats

    heat = HeatRateStats.from_storage(
        {"rates": {"bad": {HEAT_BY_BOILER: [0.0, 0.8, -1.0, 99.0, 1.2]}}, "gradients": {"bad": {HEAT_BY_BOILER: [1, 2, 3, 4, 5]}}}
    )
    assert heat.rates["bad"][HEAT_BY_BOILER] == [0.8, 1.2]
    assert heat.gradients["bad"][HEAT_BY_BOILER] == [2.0, 5.0]  # each rate keeps its own gradient
    # an entry that is no number goes with its own gradient, not with its neighbour's
    gap = HeatRateStats.from_storage(
        {"rates": {"bad": {HEAT_BY_BOILER: [0.8, None, 1.2]}}, "gradients": {"bad": {HEAT_BY_BOILER: [1, 2, 3]}}}
    )
    assert (gap.rates["bad"][HEAT_BY_BOILER], gap.gradients["bad"][HEAT_BY_BOILER]) == ([0.8, 1.2], [1.0, 3.0])
    nothing = HeatRateStats.from_storage({"rates": {"bad": {HEAT_BY_BOILER: [0.0, -3.0]}}})
    assert "bad" not in nothing.rates and "bad" not in nothing.gradients

    cool = CoolRateStats.from_storage({"rates": {"bad": [0.02, 0.0, -0.1, 5.0, 0.03], "flur": [9.0]}})
    assert cool.rates == {"bad": [0.02, 0.03]}

    old_table = CopMap.from_storage({"bins": {"2": {"count": float("inf"), "mean": 3.0}, "3": {"count": 4, "mean": 3.1}}})
    assert list(old_table.legacy) == [3]
