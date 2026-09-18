"""Phase 8: boiler controller (SetMode plan and curve learning)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines.boiler_control import (
    MIN_SAMPLES,
    SETTLE_S,
    BoilerInputs,
    BoilerMemory,
    curve_flow,
    decide,
)
from custom_components.thriftherm.models import RoomResult

from .conftest import PARAMS

NOW = 2_000_000.0


def rr(key="wohnzimmer", temp=19.0, target=20.0, demand=0.5, trend=None, allowed=True) -> RoomResult:
    return RoomResult(
        key=key, target=target, target_reason="schedule_comfort", temperature=temp, deviation_k=None if temp is None else temp - target,
        demand=demand, trend_k_per_h=trend, dew_point_c=None, abs_humidity_g_m3=None, window_open=not allowed,
        window_unknown=False, heating_allowed=allowed, internal_gain_w=None,
    )


def inp(rooms=(), *, mode="shadow", op="auto", safety="ok", available=True, frost=(), advice="boiler", outdoor=5.0,
        now=NOW, params=PARAMS, flow=None, ret=None, burner=False, learning=True):
    return BoilerInputs(now, mode, op, safety, available, tuple(rooms), tuple(frost), advice, outdoor, params,
                        flow_c=flow, return_c=ret, burner_heating=burner, learning_allowed=learning)


def feed(mem, rooms, *, flow, ret, start=NOW, samples=22, step=60.0, learning=True):
    """Run the controller through a heating run long enough for a full review window."""
    now = start
    cmd = None
    for i in range(samples):
        now = start + SETTLE_S + i * step
        cmd, mem = decide(inp(rooms, flow=flow, ret=ret, burner=True, now=now, learning=learning), mem)
    return cmd, mem, now


def heating_memory(start=NOW, **kw):
    return replace(BoilerMemory(), state="heat", state_since_ts=start, heating_since_ts=start, **kw)


def test_curve_points_and_limits():
    assert curve_flow(-10.0, PARAMS) == 55.0
    assert curve_flow(15.0, PARAMS) == 30.0
    assert curve_flow(2.5, PARAMS) == pytest.approx(42.5)
    assert curve_flow(25.0, PARAMS) == 30.0
    assert curve_flow(-25.0, PARAMS) == 60.0
    assert curve_flow(None, PARAMS) == 42.5  # no outdoor temperature: the middle of the curve


def test_control_off_sends_nothing():
    cmd, _ = decide(inp([rr()], mode="off"), BoilerMemory())
    assert cmd.plan == "disabled" and cmd.send is False and cmd.payload is None


@pytest.mark.parametrize(("available", "safety"), [(False, "ok"), (True, "fallback")])
def test_hands_off_when_data_not_trustworthy(available, safety):
    cmd, _ = decide(inp([rr()], available=available, safety=safety), BoilerMemory())
    assert cmd.plan == "hands_off" and cmd.send is False and cmd.payload is None


def test_heat_with_curve_and_demand_boost():
    cmd, mem = decide(inp([rr(demand=0.5)], outdoor=5.0), BoilerMemory())
    assert cmd.plan == "heat" and cmd.flow_setpoint == 44.0 and cmd.disable_hc is False
    assert cmd.payload == "auto;44.0;-;-;0;0;0;0;0;0"
    assert cmd.send is True and mem.last_payload == cmd.payload


def test_block_when_no_room_needs_boiler():
    cmd, _ = decide(inp([rr(demand=0.0)], advice="none"), BoilerMemory())
    assert cmd.plan == "block" and cmd.disable_hc is True and cmd.flow_setpoint is None
    assert cmd.payload == "auto;30.0;-;-;1;0;0;0;0;0"
    cmd2, _ = decide(inp([rr(demand=0.0)], advice="midea"), BoilerMemory())
    assert cmd2.plan == "block"


def test_summer_mode_blocks_heating():
    cmd, _ = decide(inp([rr()], op="off", advice="none"), BoilerMemory())
    assert cmd.plan == "block" and cmd.reason == "summer_mode"


def test_resend_every_two_minutes_and_on_change():
    cmd, mem = decide(inp([rr()]), BoilerMemory())
    assert cmd.send
    cmd, mem = decide(inp([rr()], now=NOW + 100), mem)
    assert not cmd.send
    cmd, mem = decide(inp([rr()], now=NOW + 121), mem)
    assert cmd.send  # keep-alive well before the 9-16 min fallback


def test_no_fast_toggling_but_frost_is_immediate():
    _, mem = decide(inp([rr()]), BoilerMemory())
    cmd, mem = decide(inp([rr(demand=0.0)], advice="none", now=NOW + 60), mem)
    assert cmd.plan == "heat" and cmd.waiting == "min_state_time"
    cmd, _ = decide(inp([rr(demand=0.0)], advice="none", now=NOW + 400), mem)
    assert cmd.plan == "block"
    blocked = replace(BoilerMemory(), state="block", state_since_ts=NOW)
    cmd, _ = decide(inp([rr(temp=6.0)], op="off", frost=("kueche",), advice="none", now=NOW + 10), blocked)
    assert cmd.plan == "heat" and cmd.reason == "frost_protection" and cmd.flow_setpoint >= 40.0


def test_ramp_limits_flow_increase():
    mem = replace(BoilerMemory(), last_flow=35.0, last_flow_ts=NOW - 60, state="heat", state_since_ts=NOW - 600)
    cmd, _ = decide(inp([rr(demand=1.0)], outdoor=-10.0), mem)
    assert cmd.flow_setpoint == 40.0 and cmd.waiting == "ramp_limit"


# --- learning ---------------------------------------------------------------------------------
def test_small_spread_lowers_the_curve():
    cmd, mem, _ = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert mem.offset_k == -1.0 and mem.last_adjust_reason == "spread_small"
    assert cmd.spread_k == pytest.approx(3.0, abs=0.2)
    assert cmd.learning_phase == "learning" and cmd.adjustments_last_day == 1


def test_large_spread_raises_only_with_demand():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.6)], flow=55.0, ret=37.0)
    assert mem.offset_k == 1.0 and mem.last_adjust_reason == "spread_large"
    _, calm, _ = feed(heating_memory(), [rr(demand=0.05, trend=1.0)], flow=55.0, ret=37.0)
    assert calm.offset_k == -1.0 and calm.last_adjust_reason == "rooms_satisfied"


def test_slow_rooms_raise_the_curve_inside_the_dead_band():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.6, trend=0.05)], flow=50.0, ret=40.0)
    assert mem.offset_k == 1.0 and mem.last_adjust_reason == "rooms_slow"


def test_no_learning_before_the_run_settles_or_without_samples():
    mem = heating_memory()
    cmd, mem = decide(inp([rr(demand=0.5)], flow=45.0, ret=42.0, burner=True, now=NOW + SETTLE_S - 30), mem)
    assert mem.spread_ema is None and mem.offset_k == 0.0
    _, mem, _ = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0, samples=MIN_SAMPLES - 5)
    assert mem.offset_k == 0.0  # not enough samples yet


def test_learning_paused_by_guard():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0, learning=False)
    assert mem.offset_k == 0.0 and mem.spread_ema is None
    cmd, _ = decide(inp([rr()], flow=45.0, ret=42.0, burner=True, learning=False), heating_memory())
    assert cmd.learning_phase == "paused"


def test_one_adjustment_per_hour_and_four_per_day():
    cmd, mem, now = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert mem.offset_k == -1.0
    _, mem, now = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + 60)
    assert mem.offset_k == -1.0  # cooldown
    full = replace(mem, adjustments=(now - 100, now - 200, now - 300, now - 4000), offset_k=0.0)
    _, capped, _ = feed(full, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + ADJUST_GAP)
    assert capped.offset_k == 0.0


ADJUST_GAP = 3700.0


def test_refining_uses_smaller_steps():
    history = tuple(NOW - 3 * 24 * 3600 - i * 4000 for i in range(6))  # old enough not to hit the daily cap
    mem = heating_memory(start=NOW, offset_k=0.0, adjustments=history)
    assert mem.phase(NOW) == "refining"
    _, new, _ = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert new.offset_k == -0.5


def test_offset_survives_storage_roundtrip():
    mem = replace(BoilerMemory(), offset_k=2.5, adjustments=(NOW,), last_adjust_reason="spread_small")
    back = BoilerMemory.from_storage(mem.to_storage())
    assert back.offset_k == 2.5 and back.adjustments == (NOW,) and back.last_adjust_reason == "spread_small"


# --- hot water must not teach the heating curve ---------------------------------------------

from custom_components.thriftherm.engines import boiler_control as _bc
from custom_components.thriftherm.engines.boiler_control import BoilerInputs as _BI, BoilerMemory as _BM
from .conftest import PARAMS as _PARAMS


def _shower_inputs(now: float, flow: float, ret: float, burner: bool = True) -> _BI:
    return _BI(now_ts=now, control_mode="shadow", op_mode="auto", safety_state="ok", boiler_available=True,
               rooms=(), frost_rooms=(), advice_source="boiler", outdoor_c=5.0, params=_PARAMS,
               flow_c=flow, return_c=ret, burner_heating=burner, learning_allowed=True)


def test_hot_water_is_recognised_from_the_temperatures():
    # the shower on 2026-09-12 21:10: return 66.1 °C above flow 61.7 °C
    assert _bc.hot_water_suspected(61.7, 66.1, flow_max_c=60.0)
    assert _bc.hot_water_suspected(64.0, 63.5, flow_max_c=60.0)  # far above any heating flow
    assert not _bc.hot_water_suspected(45.0, 37.0, flow_max_c=60.0)  # an ordinary heating run
    assert not _bc.hot_water_suspected(None, 37.0, flow_max_c=60.0)


def test_a_shower_during_heating_discards_the_run_and_holds_off_learning():
    t0 = 1_000_000.0
    running = _BM(heating_since_ts=t0 - 3600, samples=40, spread_ema=9.0)
    _, mem = _bc.decide(_shower_inputs(t0, 61.7, 66.1), running)
    assert mem.hot_water_seen_ts == t0
    assert mem.heating_since_ts is None and mem.samples == 0 and mem.spread_ema is None

    # half an hour later the circuit heats normally again, but is still cooling back
    _, mem = _bc.decide(_shower_inputs(t0 + 1800, 42.0, 35.0), mem)
    assert mem.heating_since_ts is None

    # after the hold-off a fresh heating run starts, with its own settle time
    later = t0 + _bc.HOT_WATER_HOLDOFF_S + 60
    _, mem = _bc.decide(_shower_inputs(later, 42.0, 35.0), mem)
    assert mem.heating_since_ts == later


def test_gas_while_heating_is_blocked_can_only_be_hot_water():
    assert _bc.hot_water_suspected(35.0, 33.3, 60.0, gas_w=22600.0, heating_blocked=True)
    assert not _bc.hot_water_suspected(35.0, 33.3, 60.0, gas_w=22600.0, heating_blocked=False)
    assert not _bc.hot_water_suspected(35.0, 33.3, 60.0, gas_w=0.0, heating_blocked=True)


def test_flow_far_above_the_heating_setpoint_is_hot_water():
    # the shower on 2026-09-13 13:57: flow 58.8 °C while 30 °C had been sent, return reading still stale
    assert _bc.hot_water_suspected(58.8, 33.3, 60.0, heating_flow_c=30.0)
    assert not _bc.hot_water_suspected(44.0, 36.0, 60.0, heating_flow_c=35.0)  # an ordinary overshoot


def test_active_block_plus_gas_marks_hot_water_at_once():
    t0 = 1_000_000.0
    blocked = _BM(state="block", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=30.0)
    active = replace(_shower_inputs(t0, 35.0, 33.3, burner=False), control_mode="active", advice_source="none", gas_power_w=22600.0)
    cmd, mem = _bc.decide(active, blocked)
    assert mem.hot_water_seen_ts == t0
    assert cmd.hot_water and cmd.hot_water_seen_ts == t0

    # in plan-only mode the knob rules: gas may be space heating, so nothing is concluded
    _, mem = _bc.decide(replace(active, control_mode="shadow"), blocked)
    assert mem.hot_water_seen_ts is None

    # the flag drops after a few minutes; the learning hold-off lasts longer
    later = t0 + _bc.HOT_WATER_SHOWN_S + 60
    cmd, _ = _bc.decide(replace(active, now_ts=later, gas_power_w=0.0), replace(blocked, hot_water_seen_ts=t0))
    assert not cmd.hot_water


# ------------------------------------------------------------------ short cycling
def _burst(mem, rooms, start, burn_s=60.0, gap_s=900.0, learning=True):
    """One burner run of burn_s followed by a pause, as a cycling boiler does it."""
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start, burner=True, flow=33.0, ret=30.0, learning=learning), mem)
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start + burn_s, burner=False, flow=25.0, ret=24.0, learning=learning), mem)
    return cmd, mem, start + burn_s + gap_s


def test_short_cycling_lowers_the_curve():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0)]
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(2):
        cmd, mem, now = _burst(mem, rooms, now)
    assert mem.offset_k == 0.0  # two short runs are not a pattern yet
    assert _bc.short_cycling(mem, now) is False

    cmd, mem, now = _burst(mem, rooms, now)  # the third start makes it one
    assert mem.offset_k == -1.0
    assert mem.last_adjust_reason == "short_cycling"
    assert mem.burn_starts == ()  # counting starts over, so it lowers at most once per pattern


def test_a_boiler_that_runs_through_is_not_cycling():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0)]
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):  # 20 minutes of burner each time
        cmd, mem, now = _burst(mem, rooms, now, burn_s=1200.0, gap_s=600.0)
    assert not _bc.short_cycling(mem, now)
    assert mem.offset_k == 0.0


def test_cycling_respects_the_guards():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0)]
    # an offset the flow minimum already swallows must not grow further
    low = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=30.0, offset_k=-10.0)
    now = NOW
    for _ in range(3):
        cmd, low, now = _burst(low, rooms, now)
    assert low.offset_k == -10.0  # the flow minimum already swallows it

    # and nothing is learned while learning is blocked (setpoint just moved, drying, ...)
    blocked = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):
        cmd, blocked, now = _burst(blocked, rooms, now, learning=False)
    assert blocked.offset_k == 0.0


def test_the_cycling_memory_survives_a_restart():
    mem = _bc.BoilerMemory(burn_starts=(1.0, 2.0), burn_durations=(60.0, 65.0))
    back = _bc.BoilerMemory.from_storage(mem.to_storage())
    assert back.burn_starts == (1.0, 2.0) and back.burn_durations == (60.0, 65.0)


def test_no_block_before_the_rooms_have_reported():
    # measured 2026-09-18: 38 s after a restart the bathroom sensor was back, but the block
    # decided in between held heating and pump off for the minimum state time
    mem = heating_memory()
    cmd, mem = decide(replace(inp([rr(temp=None, demand=None)], mode="active", advice="none"), room_data_pending=True), mem)
    assert cmd.plan == "hands_off" and cmd.send is False and cmd.payload is None
    assert "waiting_for_room_data" in cmd.blockers

    # once the rooms are there the decision is free again: no minimum state time to sit out
    cmd, _ = decide(inp([rr(temp=19.0, demand=0.5)], mode="active", now=NOW + 60.0), mem)
    assert cmd.plan == "heat" and cmd.waiting is None
