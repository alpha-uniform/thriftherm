"""Phase 8: boiler controller (SetMode plan and curve learning)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import boiler_learning as learning
from custom_components.thriftherm.engines.boiler_control import decide
from custom_components.thriftherm.engines.boiler_learning import MIN_SAMPLES, SETTLE_S
from custom_components.thriftherm.engines.boiler_memory import BoilerInputs, BoilerMemory, correction, curve_flow
from custom_components.thriftherm.models import RoomResult

from .conftest import PARAMS

NOW = 2_000_000.0


def rr(key="wohnzimmer", temp=19.0, target=20.0, demand=0.5, trend=None, allowed=True) -> RoomResult:
    return RoomResult(
        key=key, target=target, target_reason="schedule_comfort", temperature=temp, deviation_k=None if temp is None else temp - target,
        demand=demand, trend_k_per_h=trend, dew_point_c=None, abs_humidity_g_m3=None, window_open=not allowed,
        window_unknown=False, heating_allowed=allowed, internal_gain_w=None,
    )


def corr(mem, outdoor=5.0):
    """What the learning adds to the curve at the tests' outdoor temperature."""
    return correction(mem, outdoor)


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


def test_hot_water_standby_can_be_switched_off():
    """Reported 2026-10-10: a combi boiler kept its exchanger warm and fired once an hour all night."""
    no_standby = replace(PARAMS, boiler_hot_water_standby=False)
    blocked, _ = decide(inp([rr(demand=0.0)], advice="none", params=no_standby), BoilerMemory())
    assert blocked.payload == "auto;30.0;-;-;1;0;1;0;0;0"
    heating, _ = decide(inp([rr()], params=no_standby), BoilerMemory())
    assert heating.plan == "heat" and heating.payload.endswith(";-;-;0;0;1;0;0;0")
    assert decide(inp([rr()]), BoilerMemory())[0].payload.endswith(";-;-;0;0;0;0;0;0")  # default: as before


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
    assert corr(mem) == pytest.approx(-1.0) and mem.last_adjust_reason == "spread_small"
    assert cmd.spread_k == pytest.approx(3.0, abs=0.2)
    assert cmd.learning_phase == "learning" and cmd.adjustments_last_day == 1


def test_large_spread_raises_only_with_demand():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.6)], flow=55.0, ret=37.0)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "spread_large"
    _, calm, _ = feed(heating_memory(), [rr(demand=0.05, trend=1.0)], flow=55.0, ret=37.0)
    assert corr(calm) == pytest.approx(-1.0) and calm.last_adjust_reason == "rooms_satisfied"


def test_slow_rooms_raise_the_curve_inside_the_dead_band():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.6, trend=0.05)], flow=50.0, ret=40.0)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "rooms_slow"


def test_no_learning_before_the_run_settles_or_without_samples():
    mem = heating_memory()
    cmd, mem = decide(inp([rr(demand=0.5)], flow=45.0, ret=42.0, burner=True, now=NOW + SETTLE_S - 30), mem)
    assert mem.spread_ema is None and corr(mem) == pytest.approx(0.0)
    _, mem, _ = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0, samples=MIN_SAMPLES - 5)
    assert corr(mem) == pytest.approx(0.0)  # not enough samples yet


def test_learning_paused_by_guard():
    _, mem, _ = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0, learning=False)
    assert corr(mem) == pytest.approx(0.0) and mem.spread_ema is None
    cmd, _ = decide(inp([rr()], flow=45.0, ret=42.0, burner=True, learning=False), heating_memory())
    assert cmd.learning_phase == "paused"


def test_one_adjustment_per_hour_and_four_per_day():
    cmd, mem, now = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert corr(mem) == pytest.approx(-1.0)
    _, mem, now = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + 60)
    assert corr(mem) == pytest.approx(-1.0)  # cooldown
    full = replace(mem, adjustments=(now - 100, now - 200, now - 300, now - 4000), offset_k=0.0, slope_k=0.0)
    _, capped, _ = feed(full, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + ADJUST_GAP)
    assert corr(capped) == pytest.approx(0.0)


ADJUST_GAP = 3700.0


def test_refining_uses_smaller_steps():
    history = tuple(NOW - 3 * 24 * 3600 - i * 4000 for i in range(6))  # old enough not to hit the daily cap
    mem = heating_memory(start=NOW, offset_k=0.0, adjustments=history)
    assert learning.phase(mem, NOW) == "refining"
    _, new, _ = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert corr(new) == pytest.approx(-0.5)


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
    blocked = _BM(state="block", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=30.0, block_since_ts=t0 - 3600)
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


def test_a_burner_run_after_an_ebus_outage_is_not_hot_water():
    # 2026-09-19 00:59: the adapter was gone for 12 min, the boiler fell back to its knob and
    # fired for heating (S.4) just as the first block went out again
    t0 = 1_000_000.0
    active = replace(_shower_inputs(t0, 25.8, 25.5, burner=False), control_mode="active", advice_source="none")
    _, mem = _bc.decide(replace(active, boiler_available=False), _BM(state="block", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, block_since_ts=t0 - 3600))
    assert mem.block_since_ts is None  # hands off: the block is no longer in force

    _, mem = _bc.decide(replace(active, now_ts=t0 + 60), mem)  # signal back, block sent again
    cmd, mem = _bc.decide(replace(active, now_ts=t0 + 90, gas_power_w=11300.0), mem)
    assert mem.hot_water_seen_ts is None and not cmd.hot_water

    # once the block has stood for a while, gas means hot water again
    _, mem = _bc.decide(replace(active, now_ts=t0 + 60 + _bc.BLOCK_IN_FORCE_S, gas_power_w=22600.0), mem)
    assert mem.hot_water_seen_ts is not None


def test_the_boilers_own_status_outranks_the_guesses():
    assert not _bc.hot_water_suspected(35.0, 33.3, 60.0, gas_w=11300.0, heating_blocked=True, reported_mode="heating")
    assert not _bc.hot_water_suspected(64.0, 63.5, 60.0, reported_mode="heating")  # knob at 75 °C after a fallback
    assert _bc.hot_water_suspected(35.0, 33.3, 60.0, reported_mode="hot_water")
    assert _bc.hot_water_suspected(35.0, 33.3, 60.0, gas_w=22600.0, heating_blocked=True, reported_mode=None)


def test_vaillant_status_codes():
    from custom_components.thriftherm.boiler_profiles import profile

    vaillant = profile("vaillant_bai")
    assert vaillant.reported_mode(4) == "heating"
    assert vaillant.reported_mode(14) == "hot_water"
    assert vaillant.reported_mode(24) == "hot_water"
    for code in (5, 6, 7, 8):
        assert vaillant.reported_mode(code) == "heating_after"
    for code in (None, 0, 31, 97):
        assert vaillant.reported_mode(code) is None


def test_the_overrun_after_a_heating_run_is_not_hot_water():
    # 2026-09-25 17:45:03, S.7 after a 55 s heating burst: return 51.2 °C, flow already 36.5 °C
    assert not _bc.hot_water_suspected(36.5, 51.2, 60.0, gas_w=0.0, reported_mode="heating_after", status_known=True)
    # 18:10:37, same pattern, both readings within 44 s of each other
    assert not _bc.hot_water_suspected(33.7, 36.9, 60.0, gas_w=0.0, reported_mode="heating_after", status_known=True)
    # without a status code the old rule still stands in
    assert _bc.hot_water_suspected(36.5, 51.2, 60.0, gas_w=0.0)


def test_a_status_code_that_lags_behind_gas_gives_no_verdict():
    # S.8 still shown, but gas burns again: the code has not caught up with the new start
    assert not _bc.hot_water_suspected(40.0, 38.0, 60.0, gas_w=11300.0, reported_mode="heating_after", status_known=True)
    # ... so a burner under an active block is still hot water
    assert _bc.hot_water_suspected(40.0, 38.0, 60.0, gas_w=22600.0, heating_blocked=True, reported_mode="heating_after", status_known=True)
    # and a flow far above the heating setpoint too
    assert _bc.hot_water_suspected(62.0, 45.0, 60.0, gas_w=15000.0, heating_flow_c=45.0, reported_mode="heating_after", status_known=True)


def test_idle_codes_leave_return_above_flow_alone_when_the_status_is_known():
    # S.31 (no demand) with the circuit cooling: return a little warmer than flow
    assert not _bc.hot_water_suspected(31.0, 33.0, 60.0, gas_w=0.0, reported_mode=None, status_known=True)


def test_heating_bursts_with_overrun_keep_the_learning_running():
    # a cycling day as measured on 2026-09-25: burst in S.4, overrun in S.7 with return above flow
    t0 = 1_000_000.0
    heat = replace(_shower_inputs(t0, 50.0, 40.0), control_mode="active", status_known=True)
    mem = _BM(state="heat", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=47.0)
    for i in range(6):
        start = t0 + i * 770.0
        _, mem = _bc.decide(replace(heat, now_ts=start, reported_mode="heating", gas_power_w=11300.0), mem)
        _, mem = _bc.decide(replace(heat, now_ts=start + 90, flow_c=36.5, return_c=51.2, burner_heating=False,
                                    reported_mode="heating_after", gas_power_w=0.0), mem)
    assert mem.hot_water_seen_ts is None


# ------------------------------------------------------------------ short cycling
def _burst(mem, rooms, start, burn_s=60.0, gap_s=900.0, learning=True):
    """One burner run of burn_s followed by a pause, as a cycling boiler does it."""
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start, burner=True, flow=33.0, ret=30.0, learning=learning), mem)
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start + burn_s, burner=False, flow=25.0, ret=24.0, learning=learning), mem)
    return cmd, mem, start + burn_s + gap_s


def test_short_cycling_lowers_the_curve():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0, trend=0.8)]  # warming briskly: the flow is ample
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(2):
        cmd, mem, now = _burst(mem, rooms, now)
    assert corr(mem) == pytest.approx(0.0)  # two short runs are not a pattern yet
    assert _bc.short_cycling(mem, now) is False

    cmd, mem, now = _burst(mem, rooms, now)  # the third start makes it one
    assert corr(mem) == pytest.approx(-1.0)
    assert mem.last_adjust_reason == "short_cycling"
    cmd, mem, now = _burst(mem, rooms, now)
    assert corr(mem) == pytest.approx(-1.0)  # the next step waits an hour, however the boiler cycles


def test_a_cycling_boiler_whose_room_does_not_get_there_raises_the_curve():
    """Measured 2026-10-10 at a 35 °C flow minimum: 41 s of burner every 15 minutes, and the
    bathroom hung 0.2-0.3 K below target for 14 hours while the old rule wanted to lower the flow."""
    bath = replace(rr(key="badezimmer", demand=0.15, temp=20.7, target=21.0, trend=0.1), boiler_call="calling")
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):
        cmd, mem, now = _burst(mem, [bath], now, burn_s=41.0)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "rooms_lagging"
    assert cmd.held_back is None

    # a room the stall rule has given up on still counts: it is the evidence
    stalled = replace(bath, boiler_call="stalled")
    other = replace(rr(key="kuche", demand=0.2, temp=20.6, target=21.0, trend=0.3), boiler_call="calling")
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):
        cmd, mem, now = _burst(mem, [stalled, other], now, burn_s=41.0)
    assert corr(mem) == pytest.approx(1.0)


def test_a_cycling_boiler_holds_the_curve_while_the_rooms_are_neither_slow_nor_brisk():
    def run(room, released_s=3600.0):
        mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - released_s, last_flow=35.0)
        now = NOW
        for _ in range(3):
            _, mem, now = _burst(mem, [replace(room, boiler_call="calling")], now, gap_s=300.0)
        return corr(mem)

    assert run(rr(temp=20.5, target=21.0, trend=0.3)) == 0.0  # getting there, not briskly: leave it
    assert run(rr(temp=20.5, target=21.0, trend=None)) == 0.0  # no trend yet is no evidence
    assert run(rr(temp=20.95, target=21.0, trend=0.0)) == 0.0  # as good as there
    assert run(rr(temp=20.5, target=21.0, trend=0.1), released_s=0.0) == 0.0  # released minutes ago


def test_a_boiler_that_runs_through_is_not_cycling():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0)]
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):  # 20 minutes of burner each time
        cmd, mem, now = _burst(mem, rooms, now, burn_s=1200.0, gap_s=600.0)
    assert not _bc.short_cycling(mem, now)
    assert corr(mem) == pytest.approx(0.0)


def test_cycling_respects_the_guards():
    rooms = [rr(demand=0.6, temp=21.0, target=23.0)]
    # an offset the flow minimum already swallows must not grow further
    low = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=30.0, offset_k=-10.0)
    now = NOW
    for _ in range(3):
        cmd, low, now = _burst(low, rooms, now)
    assert corr(low) == pytest.approx(-10.0)  # the flow minimum already swallows it

    # and nothing is learned while learning is blocked (setpoint just moved, drying, ...)
    blocked = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):
        cmd, blocked, now = _burst(blocked, rooms, now, learning=False)
    assert corr(blocked) == pytest.approx(0.0)


def test_the_cycling_memory_survives_a_restart():
    mem = _bc.BoilerMemory(burns=((1.0, 61.0), (900.0, 965.0)), slope_k=1.5, burn_since_ts=1800.0)
    back = _bc.BoilerMemory.from_storage(mem.to_storage())
    assert back.burns == ((1.0, 61.0), (900.0, 965.0)) and back.slope_k == 1.5
    assert back.burn_since_ts == 1800.0  # a burn in progress stays one burn across the restart


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


def test_cycling_at_the_flow_minimum_is_shown_not_hidden():
    # 2026-09-24..26: 88 short runs at 17 °C outside, the curve (30 °C) below the 45 °C minimum,
    # so the lowering step was swallowed without a trace
    params = replace(PARAMS, boiler_flow_min=45.0)
    rooms = [rr(demand=0.6, temp=21.0, target=23.0, trend=0.8)]
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=45.0)
    now = NOW
    for _ in range(3):
        on = inp(rooms, mode="active", now=now, burner=True, flow=50.0, ret=40.0, params=params, outdoor=17.0)
        cmd, mem = _bc.decide(on, mem)
        cmd, mem = _bc.decide(replace(on, now_ts=now + 60, burner_heating=False, flow_c=40.0, return_c=39.0), mem)
        now += 770.0
    assert corr(mem) == pytest.approx(0.0)
    assert cmd.held_back == "short_cycling_at_flow_min"


def test_a_hot_water_guess_is_taken_back_when_the_boiler_says_it_heated():
    # 2026-09-28 00:44: the block had gone out for two minutes, but ebusd could not write yet.
    # The boiler fired once more by its knob; gas under the "block" looked like hot water,
    # then the status code showed S.7 (overrun after heating), not S.17.
    t0 = 1_000_000.0
    earlier_shower = t0 - 7200.0
    blocked = _BM(state="block", state_since_ts=t0 - 600, last_send_ts=t0 - 60, last_flow=45.0,
                  block_since_ts=t0 - 160, hot_water_seen_ts=earlier_shower)
    base = replace(_shower_inputs(t0, 22.0, 21.5, burner=False), control_mode="active", advice_source="none", status_known=True)

    cmd, mem = _bc.decide(replace(base, gas_power_w=3770.0, reported_mode=None), blocked)  # S.31 not updated yet
    assert mem.hot_water_seen_ts == t0 and cmd.hot_water
    _, mem = _bc.decide(replace(base, now_ts=t0 + 1, gas_power_w=3770.0, reported_mode="heating_after"), mem)
    assert mem.hot_water_seen_ts == t0 + 1  # S.7 while gas still burns: the code may lag, no verdict yet
    cmd, mem = _bc.decide(replace(base, now_ts=t0 + 20, gas_power_w=0.0, reported_mode="heating_after"), mem)
    assert mem.hot_water_seen_ts == earlier_shower and not cmd.hot_water  # taken back
    assert not mem.hot_water_guessed


def test_the_boilers_own_hot_water_code_is_never_taken_back():
    t0 = 1_000_000.0
    base = replace(_shower_inputs(t0, 50.0, 40.0, burner=False), control_mode="active", status_known=True)
    _, mem = _bc.decide(replace(base, gas_power_w=22600.0, reported_mode="hot_water"), _BM())
    _, mem = _bc.decide(replace(base, now_ts=t0 + 120, gas_power_w=0.0, reported_mode="heating_after"), mem)
    assert mem.hot_water_seen_ts == t0


def test_a_guess_stands_once_the_retract_window_has_passed():
    t0 = 1_000_000.0
    blocked = _BM(state="block", state_since_ts=t0 - 600, last_send_ts=t0 - 60, last_flow=45.0, block_since_ts=t0 - 600)
    base = replace(_shower_inputs(t0, 22.0, 21.5, burner=False), control_mode="active", advice_source="none", status_known=True)
    _, mem = _bc.decide(replace(base, gas_power_w=15000.0), blocked)
    later = t0 + _bc.HOT_WATER_RETRACT_S + 60
    _, mem = _bc.decide(replace(base, now_ts=later, gas_power_w=0.0, reported_mode="heating_after"), mem)
    assert mem.hot_water_seen_ts == t0


# ------------------------------------------------------------------ code review 2026-09-28 (F4, F5, F14)
def test_an_unknown_trend_does_not_count_as_slow():
    # after a restart the trend needs half an hour of history; until then nothing is known
    _, mem, _ = feed(heating_memory(), [rr(demand=0.6, trend=None)], flow=50.0, ret=40.0)
    assert corr(mem) == pytest.approx(0.0)


def test_only_calling_rooms_raise_the_flow():
    calling = replace(rr(key="badezimmer", temp=20.6, target=21.0, demand=0.2), boiler_call="calling")
    ending = replace(rr(key="kuche", temp=19.0, target=21.0, demand=1.0), boiler_call="comfort_ending")
    alone, _ = decide(inp([calling], outdoor=5.0), BoilerMemory())
    both, _ = decide(inp([calling, ending], outdoor=5.0), BoilerMemory())
    assert both.flow_setpoint == alone.flow_setpoint == 41.5  # curve 40 + 8 K x 0.2


def test_a_run_still_burning_is_not_counted_as_a_short_one():
    import custom_components.thriftherm.engines.boiler_control as bc

    now = NOW
    # runs of 900 s and 60 s within the hour, a third one burning since 10 s; 30 s belongs to an older start
    mem = replace(
        BoilerMemory(), burn_since_ts=now - 10,
        burns=((now - 7200, now - 7170), (now - 2000, now - 1100), (now - 500, now - 440)),
    )
    assert bc.short_cycling(mem, now) is False  # median of 900 and 60 s


def test_the_burner_share_of_the_last_hour_is_kept():
    """What a cycling boiler delivers is the share of time its burner runs (2026-10-10: 41 s of 15.7 min)."""
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0)
    rooms = [replace(rr(temp=20.5, target=21.0, trend=0.3), boiler_call="calling")]
    cmd = None
    for i in range(4):  # four burns of 45 s, a quarter of an hour apart, told by the gas meter
        start = NOW + i * 900.0
        on = replace(inp(rooms, mode="active", now=start, flow=38.0, ret=34.0), gas_power_w=9000.0)
        _, mem = _bc.decide(on, mem)
        cmd, mem = _bc.decide(replace(on, now_ts=start + 45.0, gas_power_w=0.0), mem)
    assert cmd.burner_starts_last_hour == 4
    assert cmd.burner_share == pytest.approx(4 * 45.0 / 3600.0)

    later, mem = _bc.decide(replace(inp(rooms, mode="active", now=NOW + 3 * 900.0 + 3700.0), gas_power_w=0.0), mem)
    assert later.burner_starts_last_hour == 0 and later.burner_share == 0.0  # an hour on, nothing is left

    shower = replace(inp(rooms, mode="active", now=NOW + 9000.0), gas_power_w=20000.0, reported_mode="hot_water")
    tapped, mem = _bc.decide(shower, mem)
    assert tapped.burner_starts_last_hour == 0  # hot water is not the heating


def test_a_step_on_a_mild_day_moves_the_level_and_one_in_the_cold_the_slope():
    """As a heating curve is set by hand: too cold only in winter means steeper, in between means higher."""
    ready = dict(heating_since_ts=NOW - 7200, samples=50, spread_ema=18.0, last_review_ts=NOW - 7200,
                 state="heat", state_since_ts=NOW - 7200)

    def step(outdoor):  # a large spread with demand: the curve is too low
        on = inp([rr(demand=0.5)], mode="active", outdoor=outdoor, burner=True, flow=50.0, ret=32.0)
        return learning.learn(on, BoilerMemory(**ready), [rr(demand=0.5)], True)

    mild, cold, between = step(15.0), step(-10.0), step(2.5)
    assert (mild.offset_k, mild.slope_k) == (1.0, 0.0)
    assert (cold.offset_k, cold.slope_k) == (0.0, 1.0)
    assert between.offset_k == pytest.approx(between.slope_k)  # halfway: both alike
    for mem, outdoor in ((mild, 15.0), (cold, -10.0), (between, 2.5)):
        assert correction(mem, outdoor) == pytest.approx(1.0)  # the flow there moves by the full step
    assert correction(cold, 15.0) == 0.0  # what the cold taught leaves the mild days alone


def test_the_burner_is_told_by_the_best_sign_and_never_by_hot_water():
    base = inp([rr()], mode="active")
    pump = replace(base, burner_heating=True)
    assert learning.flame(pump)  # neither a gas meter nor a status code: the pump state
    assert not learning.flame(replace(pump, status_known=True, reported_mode="heating_after"))  # overrun is no flame
    assert learning.flame(replace(base, status_known=True, reported_mode="heating"))
    assert not learning.flame(replace(pump, status_known=True, reported_mode="heating", gas_power_w=0.0))  # the meter knows best
    assert learning.flame(replace(base, gas_power_w=9000.0))
    assert not learning.flame(replace(pump, gas_power_w=20000.0, reported_mode="hot_water"))

    # a shower without a status code and without a gas meter: the temperatures give it away, and
    # the burn is not counted as a heating burn
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0)
    _, mem = decide(replace(pump, flow_c=64.0, return_c=63.5), mem)
    assert mem.burn_since_ts is None and mem.hot_water_seen_ts == NOW


# ------------------------------------------------------------------ hot water never counts (review 2026-10-11)
def test_hot_water_told_by_the_pump_state_is_no_heating_burn_even_with_a_gas_meter():
    """A gas meter sees a flame, not what it burns for: 45 minutes of cylinder loading were learned from."""
    loading = replace(inp([rr(demand=0.0)], mode="active", flow=45.0, ret=42.0), gas_power_w=9000.0, hot_water_active=True)
    assert not learning.flame(loading)
    mem = heating_memory()
    for i in range(46):
        cmd, mem = decide(replace(loading, now_ts=NOW + i * 60.0), mem)
    assert mem.burns == () and mem.burn_since_ts is None
    assert (mem.offset_k, mem.slope_k) == (0.0, 0.0) and cmd.burner_share == 0.0


def test_the_start_of_a_shower_is_not_kept_as_a_short_heating_burn():
    on = replace(inp([rr()], mode="active", flow=40.0, ret=36.0), gas_power_w=20000.0)  # gas first...
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0, last_send_ts=NOW - 60)
    _, mem = decide(on, mem)
    assert mem.burn_since_ts == NOW
    _, mem = decide(replace(on, now_ts=NOW + 90, flow_c=64.0, return_c=63.5), mem)  # ...the temperatures 90 s later
    assert mem.burns == () and mem.burn_since_ts is None


def test_taps_in_the_hour_after_hot_water_are_left_out_unless_the_boiler_says_what_it_burns_for():
    def taps(status_known):
        base = replace(inp([rr()], mode="active", flow=40.0, ret=36.0), status_known=status_known,
                       reported_mode="heating" if status_known else None)
        mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, last_flow=40.0, hot_water_seen_ts=NOW - 600)
        for i in range(3):
            start = NOW + i * 600.0
            _, mem = decide(replace(base, now_ts=start, gas_power_w=9000.0), mem)
            _, mem = decide(replace(base, now_ts=start + 60, gas_power_w=0.0,
                                    reported_mode="heating_after" if status_known else None), mem)
        return mem

    assert taps(status_known=False).burns == ()  # a guess stays out of the burner's record
    assert len(taps(status_known=True).burns) == 3  # the boiler itself said: heating


def test_the_phase_shown_is_paused_once_the_heating_is_blocked():
    burns = tuple((NOW - 3000 + i * 900, NOW - 2940 + i * 900) for i in range(3))
    cycled = BoilerMemory(state="block", state_since_ts=NOW - 60, burns=burns)
    assert learning.short_cycling(cycled, NOW)
    cmd, _ = decide(inp([rr(demand=0.0)], mode="active", advice="none"), cycled)
    assert cmd.plan == "block" and cmd.learning_phase == "paused"


def test_a_step_the_limit_swallows_is_not_spent():
    """At 14 °C a level already at +10 K leaves a step almost nothing to move, yet it used up the hour and the day's count."""
    ready = dict(heating_since_ts=NOW - 7200, samples=50, spread_ema=18.0, last_review_ts=NOW - 7200,
                 state="heat", state_since_ts=NOW - 7200)
    on = inp([rr(demand=0.5)], mode="active", outdoor=14.0, burner=True, flow=50.0, ret=32.0)
    full = BoilerMemory(offset_k=10.0, **ready)
    after = learning.learn(on, full, [rr(demand=0.5)], True)
    assert (after.offset_k, after.slope_k, after.adjustments) == (10.0, 0.0, ())
