"""Phase 8: boiler controller (SetMode plan and curve learning)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import boiler_learning as learning
from custom_components.thriftherm.engines.boiler_control import decide
from custom_components.thriftherm.engines.boiler_learning import MIN_SAMPLES, SETTLE_S
from custom_components.thriftherm.engines.boiler_load import LoadLine
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
    """What the learning adds to the curve at the tests' outdoor temperature: the line that sets the flow."""
    from custom_components.thriftherm.engines import boiler_load as load
    from custom_components.thriftherm.engines.boiler_memory import cold_weight

    if mem.class_in_use is None:
        return correction(mem, outdoor, None)
    cell = load.deciding_cell(mem.lines, mem.class_in_use, mem.cell_rooms, mem.offset_k, mem.slope_k, cold_weight(outdoor))
    return correction(mem, outdoor, cell)


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
    mem = replace(BoilerMemory(), last_flow=35.0, state="heat", state_since_ts=NOW - 600)
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


def test_one_step_down_per_hour_and_four_kelvin_down_per_day():
    cmd, mem, now = feed(heating_memory(), [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert corr(mem) == pytest.approx(-1.0) and cmd.moved_last_day_k == 1.0
    _, mem, now = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + 60)
    assert corr(mem) == pytest.approx(-1.0)  # cooldown
    lowered = dict(adjustments=(now - 9000, now - 8000, now - 7000, now - 6000), adjustment_steps=(-1.0, -1.0, -1.0, -0.6))
    full = replace(mem, lines={}, **lowered)  # 3.6 K down within the day: less than a step is left
    _, capped, _ = feed(full, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + ADJUST_GAP)
    assert corr(capped) == pytest.approx(0.0)
    nearly = replace(full, adjustment_steps=(-1.0, -1.0, -1.0, -0.2))  # 3.2 K: what is left is taken, no more
    _, last, _ = feed(nearly, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + ADJUST_GAP)
    assert corr(last) == pytest.approx(-0.8)
    stepped_up = {"majority:wohnzimmer": LoadLine(last_step_k=1.0, last_step_ts=now - 6000)}  # this line's step before went up
    raised = replace(full, adjustment_steps=(4.0, 4.0, 1.0, 1.0), lines=stepped_up)  # steps up do not use up the steps down
    _, down, _ = feed(raised, [rr(demand=0.5)], flow=45.0, ret=42.0, start=now + ADJUST_GAP)
    assert corr(down) == pytest.approx(-0.5)  # against the step up before it: half its size


def _step(mem, direction, minutes, *, outdoor=15.0, params=PARAMS):
    """One step of the learning, so many minutes after NOW; at +15 °C a step is all level."""
    on = inp([rr(demand=0.5)], mode="active", outdoor=outdoor, now=NOW + minutes * 60, params=params)
    return learning.apply(on, mem, direction, "rooms_lagging")


def test_steps_up_grow_with_certainty_and_shrink_when_the_answer_was_overshot():
    """Measured 2026-10-11: 1 K steps, four a day, left the bathroom 0.6 K below target after eight hours."""
    mem, sizes = BoilerMemory(), []
    for i in range(4):
        mem = _step(mem, 1, i * 46)
        sizes.append(mem.adjustment_steps[-1])
    assert sizes == [1.0, 2.0, 4.0, 3.0]  # doubling up to 4 K, then what the day's 10 K up leave
    assert mem.offset_k == 10.0 and learning.moved_last_day(mem, NOW + 9000, 1) == 10.0
    assert _step(mem, 1, 300) is mem  # the day's 10 K up are spent

    mem = _step(_step(BoilerMemory(), 1, 0), 1, 46)  # +1, +2 ...
    assert _step(mem, 1, 46 + 40) is mem  # three quarters of an hour between steps up: the room has to answer first
    assert _step(mem, -1, 46 + 55) is mem  # ... and a whole hour before a step down
    back = _step(mem, -1, 46 + 61)
    assert back.adjustment_steps[-1] == -1.0 and back.offset_k == 2.0  # against the last one: half its size
    again = _step(back, 1, 46 + 61 + 46)
    assert again.adjustment_steps[-1] == 0.5  # and half again: closing in
    assert _step(again, -1, 46 + 61 + 46 + 61).adjustment_steps[-1] == -0.5  # never below half a kelvin

    assert _step(mem, 1, 46 + 181).adjustment_steps[-1] == 1.0  # three hours on, a new run starts at the first step


def test_steps_down_never_grow_and_leave_the_steps_up_their_own_allowance():
    """Review 2026-10-11: doubling both ways lowered the curve by 10 K in an afternoon, and no step up was left."""
    mem, sizes = BoilerMemory(offset_k=8.0), []
    for i in range(6):
        mem = _step(mem, -1, i * 61)
        sizes.append(mem.adjustment_steps[-1] if len(mem.adjustment_steps) > len(sizes) else None)
    assert sizes == [-1.0, -1.0, -1.0, -1.0, None, None]  # 4 K down in a day, in single steps
    assert learning.moved_last_day(mem, NOW + 30000, -1) == 4.0 and learning.moved_last_day(mem, NOW + 30000, 1) == 0.0
    up = _step(mem, 1, 3 * 61 + 50)  # the last step down was at minute 183
    assert up.adjustment_steps[-1] == 0.5 and _step(up, 1, 3 * 61 + 96).adjustment_steps[-1] == 1.0  # and up again at once


def test_a_step_goes_no_further_than_the_flow_can_follow():
    """Review 2026-10-11: half a kelvin below the flow maximum a 4 K step put the correction 3.5 K beyond it."""
    hot = replace(PARAMS, boiler_flow_max=36.0)  # the curve gives 30 °C at +15 °C outdoor
    mem = BoilerMemory(offset_k=2.0, adjustments=(NOW - 3000,), adjustment_steps=(2.0,))  # planned 32 °C, next step 4 K
    assert _step(mem, 1, 0, params=hot).offset_k == 6.0  # 4 K fit below the maximum
    close = _step(replace(mem, offset_k=5.0), 1, 0, params=hot)
    assert close.adjustment_steps[-1] == 1.0 and close.offset_k == 6.0  # only the kelvin that is left to the maximum
    assert _step(replace(mem, offset_k=5.7), 1, 0, params=hot).adjustments == (NOW - 3000,)  # 0.3 K: not worth a step
    low = BoilerMemory(offset_k=0.6)  # planned 30.6 °C, the minimum is 30 °C
    assert _step(low, -1, 0).offset_k == pytest.approx(0.0)  # and the same downwards


def test_what_a_limit_leaves_of_a_step_is_taken():
    """Review 2026-10-11: at +8.5 K the next step was 4 K, only 1.5 K fitted, and it was refused for three hours."""
    mem = BoilerMemory(offset_k=8.5, adjustments=(NOW - 3000,), adjustment_steps=(2.0,))
    after = _step(mem, 1, 0)
    assert after.offset_k == 10.0 and after.adjustment_steps[-1] == 1.5
    assert _step(replace(mem, offset_k=9.7), 1, 0).adjustments == (NOW - 3000,)  # 0.3 K: not worth a step


def test_every_step_of_the_day_is_counted_and_old_ones_are_taken_as_they_were():
    """Review 2026-10-11: with ten steps remembered, 28 small ones moved the curve by 20 K in a day."""
    times = tuple(NOW - 80000 + i * 2800 for i in range(28))
    many = BoilerMemory(adjustments=times, adjustment_steps=tuple(0.5 if i % 3 else -0.5 for i in range(28)))
    assert learning.moved_last_day(many, NOW, 1) == 9.0 and learning.moved_last_day(many, NOW, -1) == 5.0
    assert BoilerMemory.from_storage(many.to_storage()).adjustment_steps == many.adjustment_steps
    assert _step(many, -1, 0) is many  # more than the 4 K down are used

    old = BoilerMemory(adjustments=(NOW - 600,))  # stored before sizes were kept: 1 K, direction unknown
    assert learning.next_step(old, 1, NOW) == 1.0  # so nothing is doubled or halved on its account
    assert learning.moved_last_day(old, NOW, 1) == learning.moved_last_day(old, NOW, -1) == 1.0
    broken = BoilerMemory.from_storage({"offset_k": "nan", "slope_k": float("inf"), "adjustment_steps": [1.0, float("nan"), "x"]})
    assert (broken.offset_k, broken.slope_k, broken.adjustment_steps) == (0.0, 0.0, (1.0,))


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
    assert _bc.hot_water_suspected(64.0, 63.5, flow_max_c=60.0, heating_flow_c=55.0)  # far above any heating flow
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


def _warm_start_inputs(t0):
    # 2026-10-16 10:31: heating blocked for an hour, the combi boiler burnt a minute at S.4 with its
    # heating pump off to keep its hot water warm; the pump ran only afterwards
    blocked = _BM(state="block", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=40.0, block_since_ts=t0 - 3600)
    active = replace(_shower_inputs(t0, 24.3, 21.0, burner=False), control_mode="active", advice_source="none",
                     gas_power_w=7500.0, reported_mode="heating", status_known=True, heating_pump_on=False)
    return active, blocked


def test_a_warm_start_while_heating_is_blocked_is_hot_water_not_a_heating_burn():
    t0 = 1_000_000.0
    active, blocked = _warm_start_inputs(t0)
    cmd, mem = _bc.decide(active, blocked)
    assert cmd.warm_start and mem.warm_start_ts == t0
    assert mem.burn_since_ts is None  # not counted as a burner start of the heating
    assert mem.hot_water_seen_ts is None  # no hold-off of the learning: the circuit stayed cold

    # the gas meter's step comes a little after the burn: still the warm start's
    after = replace(active, now_ts=t0 + 90, gas_power_w=0.0, reported_mode="heating_after", heating_pump_on=True)
    cmd, mem = _bc.decide(after, mem)
    assert cmd.warm_start
    cmd, _ = _bc.decide(replace(after, now_ts=t0 + 60 + _bc.WARM_START_GAS_S), mem)
    assert not cmd.warm_start


def test_the_gas_meter_running_on_after_a_warm_start_is_no_heating_burn():
    # 10:32:10 S.7, the meter still read 3.8 kW until 10:32:16
    t0 = 1_000_000.0
    active, blocked = _warm_start_inputs(t0)
    _, mem = _bc.decide(active, blocked)
    cmd, mem = _bc.decide(replace(active, now_ts=t0 + 50, gas_power_w=3800.0, reported_mode="heating_after"), mem)
    assert mem.burn_since_ts is None and cmd.burner_starts_last_hour == 0


def test_a_burn_with_the_heating_pump_running_is_heating_although_blocked():
    # the block did not reach the boiler (SetMode writes failing): it heats on its knob, pump on
    t0 = 1_000_000.0
    active, blocked = _warm_start_inputs(t0)
    cmd, mem = _bc.decide(replace(active, heating_pump_on=True), blocked)
    assert not cmd.warm_start and mem.burn_since_ts == t0
    # without any pump sign nothing is taken for a warm start either
    cmd, _ = _bc.decide(replace(active, heating_pump_on=None), blocked)
    assert not cmd.warm_start


def test_after_a_warm_start_the_hot_exchanger_is_no_hot_water_without_a_gas_meter():
    t0 = 1_000_000.0
    active, blocked = _warm_start_inputs(t0)
    active = replace(active, gas_power_w=None)
    _, mem = _bc.decide(active, blocked)
    # S.31, burner off, flow 67 °C above the maximum of 60 °C while the block stands
    idle = replace(active, now_ts=t0 + 180, reported_mode=None, flow_c=67.0, return_c=40.0, heating_pump_on=True)
    cmd, mem = _bc.decide(idle, mem)
    assert mem.hot_water_seen_ts is None and not cmd.hot_water
    # a quarter of an hour later the flow rules count again
    _, mem = _bc.decide(replace(idle, now_ts=t0 + _bc.WARM_START_COOL_S + 60), mem)
    assert mem.hot_water_seen_ts is not None


def test_a_heating_run_right_after_a_warm_start_is_heating_gas():
    t0 = 1_000_000.0
    active, blocked = _warm_start_inputs(t0)
    _, mem = _bc.decide(active, blocked)
    # a room calls: the plan is heat now, the boiler burns for the heating with the pump on
    heating = replace(active, now_ts=t0 + 60, heating_pump_on=True, advice_source="boiler", rooms=(rr(temp=19.0, target=20.0),))
    mem = replace(mem, state="heat", state_since_ts=t0 + 30, block_since_ts=None)
    cmd, _ = _bc.decide(heating, mem)
    assert not cmd.warm_start


def test_a_heating_burn_is_no_warm_start():
    t0 = 1_000_000.0
    heating = _BM(state="heat", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=45.0)
    active = replace(_shower_inputs(t0, 44.0, 38.0, burner=False), control_mode="active", advice_source="boiler",
                     gas_power_w=7500.0, reported_mode="heating", status_known=True, heating_pump_on=False)
    cmd, mem = _bc.decide(active, heating)
    assert not cmd.warm_start and mem.burn_since_ts == t0
    # a block that has not stood long enough yet (the boiler may still be on its knob) is no warm start either
    fresh = _BM(state="block", state_since_ts=t0 - 30, last_send_ts=t0 - 30, block_since_ts=t0 - 30)
    cmd, _ = _bc.decide(replace(active, advice_source="none"), fresh)
    assert not cmd.warm_start


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
    assert learning.short_cycling(mem, now) is False

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
    # it got the kitchen's flow, its line and 1.6 K demand boost, and asks for one step more
    assert corr(mem) == pytest.approx(1.0 + 1.6)


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
    assert not learning.short_cycling(mem, now)
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
    assert back.burn_since_ts is None  # a burn in progress is not kept: nobody watched it meanwhile


def test_a_burn_in_progress_at_a_stop_does_not_last_until_the_restart():
    """Review 2026-10-13: Home Assistant stopped during a burn and came back six hours later: a burn of six hours."""
    stopped = BoilerMemory(state="heat", state_since_ts=NOW - 7 * 3600, burn_since_ts=NOW - 6 * 3600)
    old_store = {**stopped.to_storage(), "burn_since_ts": NOW - 6 * 3600, "last_flow_ts": NOW - 7 * 3600}  # as 0.6.0b2 wrote it
    for stored in (stopped.to_storage(), old_store):
        off = replace(inp([rr()], mode="active", flow=40.0, ret=35.0), gas_power_w=0.0)
        cmd, mem = decide(off, BoilerMemory.from_storage(stored))
        assert mem.burns == () and cmd.burner_share == 0.0 and cmd.burner_starts_last_hour == 0


def test_no_block_before_the_rooms_have_reported():
    # measured 2026-09-18: 38 s after a restart the bathroom sensor was back, but the block
    # decided in between held heating and pump off for the minimum state time
    mem = heating_memory()
    cmd, mem = decide(replace(inp([rr(temp=None, demand=None)], mode="active", advice="none"), room_data_pending=True), mem)
    assert cmd.plan == "hands_off" and cmd.send is False and cmd.payload is None
    assert "waiting_for_room_data" in cmd.blockers

    # once the rooms are there the heating goes on as before the restart
    cmd, _ = decide(inp([rr(temp=19.0, demand=0.5)], mode="active", now=NOW + 60.0), mem)
    assert cmd.plan == "heat" and cmd.waiting is None


def test_waiting_for_a_room_sensor_after_a_restart_does_not_free_the_boiler_to_toggle():
    """Review 2026-10-13: the one cycle of waiting wiped the stored state, and the boiler went block, hands off, heat."""
    stored = BoilerMemory(state="block", state_since_ts=NOW - 60, last_flow=30.0).to_storage()  # blocked a minute before the stop
    late = replace(inp([rr(temp=None, demand=None)], mode="active", advice="none", now=NOW + 30), room_data_pending=True)
    cmd, mem = decide(late, BoilerMemory.from_storage(stored))
    assert cmd.plan == "hands_off" and (mem.state, mem.state_since_ts) == ("block", NOW - 60)
    cmd, mem = decide(inp([rr()], mode="active", now=NOW + 90), mem)  # the sensor is back, and the room is cold
    assert cmd.plan == "block" and cmd.waiting == "min_state_time"
    cmd, _ = decide(inp([rr()], mode="active", now=NOW + 250), mem)
    assert cmd.plan == "heat"

    # an outage still lets go of the state: the boiler is back on its knob by then
    _, mem = decide(replace(late, boiler_available=False), BoilerMemory.from_storage(stored))
    assert mem.state is None and mem.state_since_ts is None


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
    now = NOW
    # runs of 900 s and 60 s within the hour, a third one burning since 10 s; 30 s belongs to an older start
    mem = replace(
        BoilerMemory(), burn_since_ts=now - 10,
        burns=((now - 7200, now - 7170), (now - 2000, now - 1100), (now - 500, now - 440)),
    )
    assert learning.short_cycling(mem, now) is False  # median of 900 and 60 s


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
                 state="heat", state_since_ts=NOW - 7200, spread_judging_since_ts=NOW - 7200,
                 learning_since_ts=NOW - 7200)

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
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0, last_send_ts=NOW - 60)
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
                 state="heat", state_since_ts=NOW - 7200, spread_judging_since_ts=NOW - 7200)
    on = inp([rr(demand=0.5)], mode="active", outdoor=14.0, burner=True, flow=50.0, ret=32.0)
    full = BoilerMemory(offset_k=10.0, **ready)
    after = learning.learn(on, full, [rr(demand=0.5)], True)
    assert (after.offset_k, after.slope_k, after.adjustments) == (10.0, 0.0, ())


# ------------------------------------------------------------------ review 2026-10-13
def test_the_smallest_step_is_taken_at_every_outdoor_temperature():
    """Shared between level and slope and added up again, half a kelvin came out as 0.49999999999999994 and was dropped."""
    refining = BoilerMemory(adjustments=tuple(NOW - 10 * 86400 - i for i in range(6, 0, -1)), adjustment_steps=(1.0,) * 6)
    wide = replace(PARAMS, boiler_flow_min=0.0, boiler_flow_max=100.0)  # the flow limits are not what this is about
    assert learning.next_step(refining, 1, NOW) == 0.5
    for tenth in range(-100, 151):
        for direction in (1, -1):
            after = _step(refining, direction, 0, outdoor=tenth / 10, params=wide)
            assert after.adjustment_steps[-1] == direction * 0.5, tenth / 10
    crooked = replace(refining, offset_k=2.779, slope_k=2.1101969518129122)  # as earlier steps leave them
    assert _step(crooked, 1, 0, outdoor=-3.1, params=wide).adjustment_steps[-1] == 0.5


def test_a_room_two_tenths_below_target_that_does_not_move_is_lagging_not_satisfied():
    """21.0 - 20.8 is 0.1999999999999993: the room the rule was made for did not lag, and its demand of 0.1 read as satisfied."""
    bath = replace(rr(key="badezimmer", demand=0.1, temp=20.8, target=21.0, trend=0.0), boiler_call="calling")
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0)
    now = NOW
    for _ in range(3):
        _, mem, now = _burst(mem, [bath], now, burn_s=41.0)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "rooms_lagging"  # a boiler that cycles

    _, through, _ = feed(heating_memory(), [bath], flow=40.0, ret=31.0)  # a boiler that runs through
    assert through.adjustments == ()  # was a step down for "rooms_satisfied", every hour
    _, there, _ = feed(heating_memory(), [replace(bath, temperature=20.9)], flow=40.0, ret=31.0)
    assert corr(there) == pytest.approx(-1.0) and there.last_adjust_reason == "rooms_satisfied"  # a tenth below is there


def test_a_small_spread_does_not_lower_the_curve_while_the_rooms_do_not_get_there():
    """Three of three rooms 1.2 K below target and not rising, spread 3.5 K: 4 K down a day, the same cycles booked as lagging."""
    def rooms(trend):
        return [replace(rr(key=key, temp=19.8, target=21.0, demand=0.6, trend=trend), boiler_call="calling") for key in "abc"]

    _, stuck, _ = feed(heating_memory(), rooms(0.0), flow=44.0, ret=40.5)
    assert stuck.adjustments == () and stuck.spread_ema == pytest.approx(3.5)
    _, brisk, _ = feed(heating_memory(), rooms(0.6), flow=44.0, ret=40.5)
    assert brisk.last_adjust_reason == "spread_small"  # rooms that get there leave the spread its say

    assert learning.steady_direction(3.5, rooms(0.0)) == (0, None)  # demanding and slow
    stalled = [replace(rr(temp=20.7, target=21.0, demand=0.15, trend=0.0), boiler_call="stalled")]
    assert learning.steady_direction(3.5, [], lagging=True) == (0, None)  # a stalled room calls no more, and still lags
    assert learning.steady_direction(3.5, stalled) == (-1, "spread_small")


def test_the_hold_off_after_hot_water_holds_back_the_spread_and_not_the_rooms():
    """With a tap or a keep-warm burn every hour nothing was ever learned: the hold-off is about the circuit's temperatures."""
    bath = replace(rr(key="badezimmer", demand=0.15, temp=20.7, target=21.0, trend=0.1), boiler_call="calling")
    on = replace(inp([bath], mode="active", flow=45.0, ret=38.0), status_known=True, reported_mode="heating", gas_power_w=9000.0)
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=35.0, hot_water_seen_ts=NOW - 60)
    for i in range(3):  # a cycling boiler in the hour after a tap, the boiler itself saying: heating
        start = NOW + i * 900.0
        _, mem = decide(replace(on, now_ts=start), mem)
        cmd, mem = decide(replace(on, now_ts=start + 41.0, reported_mode="heating_after", gas_power_w=0.0), mem)
    assert corr(mem) == pytest.approx(1.0) and mem.last_adjust_reason == "rooms_lagging"  # what the rooms say counts
    assert mem.lines["majority:badezimmer"].lag_hours > 0.0  # and the operation is booked, to the room's own line
    assert cmd.learning_phase == "learning"

    after_tap = replace(heating_memory(), hot_water_seen_ts=NOW)  # a boiler that runs through, in the same hour
    _, held, _ = feed(after_tap, [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert held.adjustments == () and held.spread_ema is None and held.heating_since_ts is None  # no spread is trusted yet
    _, blocked, _ = feed(after_tap, [bath], flow=45.0, ret=42.0, learning=False)
    assert blocked.lines == {}  # and `learning_allowed` still stops all of it


def test_on_its_own_knob_a_flow_above_the_maximum_is_no_sign_of_hot_water():
    """Plan-only without a status code, the knob at 66 °C and a maximum of 60 °C: every heating run read as hot water."""
    assert not _bc.hot_water_suspected(66.0, 52.0, 60.0, gas_w=15000.0)
    assert _bc.hot_water_suspected(66.0, 52.0, 60.0, gas_w=15000.0, heating_flow_c=58.0)  # sent: the maximum is in force
    assert _bc.hot_water_suspected(66.0, 52.0, 60.0, heating_blocked=True)
    assert _bc.hot_water_suspected(66.0, 52.0, 60.0, status_known=True)  # with a status code: as before
    assert _bc.hot_water_suspected(52.0, 66.0, 60.0)  # return above flow says it on any knob

    knob = replace(inp([rr()], flow=66.0, ret=52.0, burner=True), gas_power_w=15000.0)  # plan-only
    mem = BoilerMemory()
    for minute in range(30):
        cmd, mem = decide(replace(knob, now_ts=NOW + minute * 60), mem)
    assert mem.hot_water_seen_ts is None and not cmd.hot_water
    assert cmd.burner_share == pytest.approx(29 / 60)  # the burn is the heating's


def test_a_step_is_a_step_once_the_list_of_steps_is_full():
    """Told by the length of the list, a step with 48 remembered looked like none, and the cycle went on to sample the spread."""
    full = tuple(NOW - 10 * 86400 + i for i in range(48))
    burns = tuple((NOW - 3000 + i * 900, NOW - 2940 + i * 900) for i in range(3))
    bath = replace(rr(temp=20.7, target=21.0, trend=0.1), boiler_call="calling")
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, burns=burns, adjustments=full, adjustment_steps=(0.5,) * 48,
                       heating_since_ts=NOW - 900, samples=5, spread_ema=9.0, last_review_ts=NOW - 100,
                       spread_judging_since_ts=NOW - 900, learning_since_ts=NOW - 7200)
    after = learning.learn(inp([bath], mode="active", burner=True, flow=45.0, ret=38.0), mem, [bath], True)
    assert len(after.adjustments) == 48 and after.adjustments[-1] == NOW and after.last_adjust_reason == "rooms_lagging"
    assert (after.samples, after.spread_ema) == (0, 9.0)  # the step ends the cycle


# ---- hot water that is over (seen 2026-10-13: a shower ended at 03:00 and was shown until 03:15)
def test_the_heat_left_in_the_boiler_after_a_tap_is_no_hot_water():
    """The exchanger stays hot with the pump standing: flow 57.5 °C at a maximum of 55 °C, and no flame."""
    after = {"heating_blocked": True, "reported_mode": None, "status_known": True}
    assert not _bc.hot_water_suspected(57.5, 58.5, 55.0, gas_w=0.0, **after)
    assert not _bc.hot_water_suspected(57.5, 58.5, 55.0, gas_w=0.0, heating_flow_c=40.0, reported_mode=None, status_known=True)
    # with the flame on it is a sign as before (the status code lags behind the start of a tap)
    assert _bc.hot_water_suspected(57.5, 45.0, 55.0, gas_w=15000.0, heating_flow_c=40.0, reported_mode=None, status_known=True)
    # and without a gas meter nothing tells that the flame is off: as before
    assert _bc.hot_water_suspected(57.5, 58.5, 55.0, **after)


def test_with_a_status_code_hot_water_is_shown_two_minutes_after_its_end_not_ten():
    t0 = 1_000_000.0
    blocked = _BM(state="block", state_since_ts=t0 - 3600, last_send_ts=t0 - 60, last_flow=40.0, block_since_ts=t0 - 3600)
    idle = replace(_shower_inputs(t0, 57.5, 58.5, burner=False), control_mode="active", advice_source="none", gas_power_w=0.0)
    with_code = replace(idle, status_known=True, reported_mode="hot_water")
    cmd, mem = _bc.decide(with_code, blocked)  # S.14: the boiler says it itself
    assert cmd.hot_water and mem.hot_water_seen_ts == t0
    over = replace(with_code, reported_mode=None)  # S.31, the flow still hot
    cmd, mem = _bc.decide(replace(over, now_ts=t0 + 60), mem)
    assert cmd.hot_water and mem.hot_water_seen_ts == t0  # no new sign, and still inside the two minutes
    cmd, mem = _bc.decide(replace(over, now_ts=t0 + 180), mem)
    assert not cmd.hot_water
    # a boiler without a status code keeps the ten minutes: its signs come with gaps
    cmd, _ = _bc.decide(replace(idle, now_ts=t0 + 180), replace(blocked, hot_water_seen_ts=t0))
    assert cmd.hot_water



# ---- what the learning may learn from (test day 2026-10-13: four rooms set 3 K up, the curve for most rooms +7.5 K)
def test_a_target_set_by_hand_or_by_a_quick_heat_up_is_no_evidence_that_the_curve_is_too_low():
    from custom_components.thriftherm.engines import boiler_load as load

    behind = replace(rr(temp=21.4, target=23.5, trend=0.0), boiler_call="calling")
    assert load.lagging((behind,))
    assert not load.lagging((replace(behind, target_reason="override"),))
    assert not load.lagging((replace(behind, target_reason="schedule_comfort+boost", boost_active=True),))

    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                       burns=tuple((NOW - 3000 + i * 900, NOW - 2950 + i * 900) for i in range(3)))
    by_hand = replace(behind, target_reason="override")
    after = learning.learn(inp([by_hand], mode="active", flow=45.0, ret=40.0), mem, [by_hand], False)
    assert after.adjustments == ()  # cycling, the room behind its wish: no step up


def test_after_a_pause_the_learning_waits_a_whole_window_and_judges_only_what_came_since():
    """Seen 2026-10-13: back from "plan only", the curve went up by 4 K in the same cycle, on the knob's burner runs."""
    behind = replace(rr(temp=20.5, target=21.0, trend=0.0), boiler_call="calling")
    knob_runs = tuple((NOW - 3000 + i * 600, NOW - 2950 + i * 600) for i in range(4))  # short runs during the pause
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, burns=knob_runs, last_review_ts=NOW - 3600)

    was_learning = replace(mem, learning_since_ts=NOW - 9000)
    paused = learning.learn(inp([behind], mode="active", learning=False), was_learning, [behind], False)
    assert paused.learning_since_ts is None and paused.adjustments == ()

    back = learning.learn(inp([behind], mode="active", now=NOW), paused, [behind], False)
    assert back.learning_since_ts == NOW and back.adjustments == () and back.last_review_ts is None
    later = learning.learn(inp([behind], mode="active", now=NOW + 1300), back, [behind], False)
    assert later.adjustments == ()  # the knob's runs before NOW do not count as cycling under our curve

    ours = tuple((NOW + 200 + i * 300, NOW + 250 + i * 300) for i in range(3))  # three short runs since
    cycling = learning.learn(inp([behind], mode="active", now=NOW + 1300), replace(back, burns=knob_runs + ours), [behind], False)
    assert cycling.adjustments == (NOW + 1300,) and cycling.last_adjust_reason == "rooms_lagging"


# ---- the limits apply to the flow that is sent (test day 2026-10-13: one room heated with 53 instead of 43.5 °C)
def test_the_flow_minimum_limits_the_sum_and_is_no_base_for_the_correction_and_the_demand():
    p = replace(PARAMS, boiler_flow_min=40.0, boiler_flow_max=60.0, boiler_curve_flow_cold=55.0, boiler_curve_flow_warm=30.0)
    kitchen = replace(rr(temp=20.6, target=23.5, demand=1.0), boiler_call="calling")
    cmd, _ = decide(inp([kitchen], mode="active", outdoor=15.0, params=p), replace(BoilerMemory(), offset_k=4.8))
    assert cmd.curve_flow == 30.0  # shown as it is
    assert cmd.flow_setpoint == 43.0  # 30 + 4.8 + 8 = 42.8, not 40 + 4.8 + 8

    cmd, _ = decide(inp([replace(kitchen, demand=0.1)], mode="active", outdoor=15.0, params=p), BoilerMemory())
    assert cmd.flow_setpoint == 40.0  # 30.8 lies below the minimum: the minimum is the floor of what is sent
    cmd, _ = decide(inp([kitchen], mode="active", outdoor=-15.0, params=p), replace(BoilerMemory(), offset_k=4.8))
    assert cmd.flow_setpoint == 60.0  # 60 + 4.8 + 8: the maximum as before


def test_below_the_flow_minimum_the_curve_takes_no_step_up():
    """Test of 2026-10-15: at 16 °C outside a step from the minimum moved a line by 4.4 K, three took it to +9.3 K.

    The gap to the minimum says nothing about the curve on a colder day. A room that does not get
    there at the minimum is shown as held back; the minimum is the user's to raise.
    """
    p = replace(PARAMS, boiler_flow_min=40.0, boiler_flow_max=60.0, boiler_curve_flow_cold=55.0, boiler_curve_flow_warm=30.0)
    room = replace(rr(temp=20.5, target=21.0, trend=0.0, demand=0.0), boiler_call="calling")
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200, offset_k=4.0,
                       burns=tuple((NOW - 3000 + i * 900, NOW - 2950 + i * 900) for i in range(3)))
    on = inp([room], mode="active", outdoor=15.0, params=p, flow=40.0, ret=36.0)
    after = learning.learn(on, mem, [room], False)
    assert after.adjustments == () and correction(after, 15.0) == 4.0  # 30 + 4 lies below the minimum of 40
    cmd, _ = decide(on, replace(mem, state=None, state_since_ts=None))
    assert cmd.flow_setpoint == 40.0 and cmd.held_back == "rooms_lagging_at_flow_min"
    # where the curve lies above the minimum the room is learned from as before
    cold = learning.learn(replace(on, outdoor_c=0.0), mem, [room], False)
    assert cold.last_adjust_reason == "rooms_lagging" and cold.adjustment_steps == (1.0,)
