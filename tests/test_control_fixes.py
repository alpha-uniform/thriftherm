"""Regression tests for the control-logic review (2026-09-13)."""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.thriftherm.engines import advisor
from custom_components.thriftherm.engines import boiler_control as bc
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.room_control import RoomCtrlMemory, manual_change
from custom_components.thriftherm.models import DryingState, RoomResult, ScheduleState

from .conftest import PARAMS, room_config, room_state

P = replace(PARAMS, boiler_flow_max=60.0, boiler_flow_min=30.0)


def _room(demand: float = 1.0, trend: float = 0.0) -> RoomResult:
    return RoomResult(key="bad", target=21.0, target_reason="schedule_comfort", temperature=15.0, deviation_k=-6.0,
                      demand=demand, trend_k_per_h=trend, dew_point_c=None, abs_humidity_g_m3=None, window_open=False,
                      window_unknown=False, heating_allowed=True, internal_gain_w=None, drying=DryingState(),
                      heat_pump_allowed=False, boost_active=False)


def _boiler(t: float, advice: str, frost: tuple[str, ...] = (), outdoor: float = -10.0) -> bc.BoilerInputs:
    return bc.BoilerInputs(now_ts=t, control_mode="active", op_mode="auto", safety_state="ok", boiler_available=True,
                           rooms=(_room(),), frost_rooms=frost, advice_source=advice, outdoor_c=outdoor, params=P,
                           flow_c=40.0, return_c=33.0, burner_heating=False)


# --- boiler ---------------------------------------------------------------------------------------
def test_ramp_allows_five_kelvin_per_ten_minutes_not_per_cycle():
    t = 1_000_000.0
    _, mem = bc.decide(_boiler(t, "none"), bc.BoilerMemory())  # blocked, flow at minimum
    t += 400
    flows = []
    for minute in range(12):
        cmd, mem = bc.decide(_boiler(t + minute * 60, "boiler"), mem)
        flows.append(cmd.flow_setpoint)
    assert flows[:10] == [35.0] * 10  # the first ten minutes stay one step up
    assert flows[10] == 40.0  # the next window allows the next step


def test_frost_protection_is_not_ramped():
    t = 2_000_000.0
    # mild outside: the curve alone would ask for less than the 40 °C frost floor
    _, mem = bc.decide(_boiler(t, "none", outdoor=15.0), bc.BoilerMemory())
    cmd, _ = bc.decide(_boiler(t + 60, "none", frost=("bad",), outdoor=15.0), mem)
    assert cmd.plan == "heat" and cmd.flow_setpoint == 40.0 and cmd.waiting is None


def _learning_inputs(t: float) -> bc.BoilerInputs:
    return bc.BoilerInputs(now_ts=t, control_mode="active", op_mode="auto", safety_state="ok", boiler_available=True,
                           rooms=(_room(),), frost_rooms=(), advice_source="boiler", outdoor_c=-10.0, params=P,
                           flow_c=60.0, return_c=50.0, burner_heating=True, learning_allowed=True)


def test_offset_does_not_wind_up_while_the_flow_is_already_at_maximum():
    t = 3_000_000.0
    ready = dict(heating_since_ts=t - 7200, samples=50, spread_ema=10.0, last_review_ts=t - 7200)
    at_max = bc.BoilerMemory(offset_k=5.0, **ready)  # curve 55 + 5 = 60 = maximum
    below = bc.BoilerMemory(offset_k=0.0, **ready)
    assert bc._learn(_learning_inputs(t), at_max, [_room()]).offset_k == 5.0
    assert bc._learn(_learning_inputs(t), below, [_room()]).offset_k > 0.0


# --- the boiler may only rely on a heat pump that actually heats -----------------------------------
@pytest.mark.parametrize(
    ("plan", "hp_control", "boiler_control", "expected"),
    [
        ("heat", "active", "active", "midea"),    # both act: the heat pump really heats
        ("heat", "shadow", "shadow", "midea"),    # both only plan: the plan stays consistent
        ("heat", "shadow", "active", "boiler"),   # the boiler acts, the heat pump would not
        ("heat", "off", "active", "boiler"),
        ("hands_off", "active", "active", "boiler"),  # user runs the unit or it is unavailable
        ("off", "active", "active", "boiler"),        # waiting for min off time, intake unknown, ...
    ],
)
def test_boiler_source_follows_what_the_heat_pump_will_really_do(plan, hp_control, boiler_control, expected):
    assert advisor.boiler_source("midea", plan, hp_control, boiler_control) == expected


def test_other_advice_passes_through_unchanged():
    assert advisor.boiler_source("boiler", "off", "active", "active") == "boiler"
    assert advisor.boiler_source("none", "off", "active", "active") == "none"


# --- rooms ----------------------------------------------------------------------------------------
def test_schedule_preheat_holds_once_started_despite_sensor_ticks():
    tz = ZoneInfo("Europe/Berlin")
    cfg = room_config("wohnzimmer", comfort=21.0, setback=17.0)
    sched = ScheduleState(active=False, temperature=None, next_start=datetime(2026, 1, 12, 17, 30, tzinfo=tz))
    latch = None
    targets = []
    for i, temp in enumerate([18.0, 18.1, 18.0, 18.1, 18.0, 18.1, 18.0]):
        st = replace(room_state(cfg, temp=temp), schedule=sched, heat_rate_k_h=1.0, preheat_latch_ts=latch)
        res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 14, 10 + i, tzinfo=tz), "auto", PARAMS)
        targets.append(res.target)
        latch = res.preheat_for_ts
    assert targets == [21.0] * 7


def test_manual_change_before_our_value_was_echoed_is_still_recognised():
    pending = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=0.0, confirmed=False, thermostat_before_send=18.0)
    assert manual_change("active", 23.0, pending) == 23.0   # neither ours nor the old value: a person
    assert manual_change("active", 18.0, pending) is None   # still the old value, echo pending
    assert manual_change("active", 21.0, pending) is None   # our value arrived
