"""Phase 8: boiler controller (SetMode plan and curve learning)."""

from dataclasses import replace

import pytest

from custom_components.thriftherm.engines import boiler_learning as learning
from custom_components.thriftherm.engines import boiler_load as load
from custom_components.thriftherm.engines.boiler_control import decide
from custom_components.thriftherm.engines.boiler_learning import MIN_SAMPLES, SETTLE_S
from custom_components.thriftherm.engines.boiler_load import LoadLine
from custom_components.thriftherm.engines.boiler_memory import BoilerInputs, BoilerMemory, correction, curve_flow
from custom_components.thriftherm.models import RoomResult

from .conftest import PARAMS, room_result

NOW = 2_000_000.0


def rr(key="wohnzimmer", temp=19.0, target=20.0, demand=0.5, trend=None, allowed=True) -> RoomResult:
    return room_result(key, target, temp, demand=demand, trend_k_per_h=trend, window_open=not allowed, heating_allowed=allowed)


def corr(mem, outdoor=5.0):
    """What the learning adds to the curve at the tests' outdoor temperature: the line that sets the flow."""
    from custom_components.thriftherm.engines import boiler_load as load
    from custom_components.thriftherm.engines.boiler_memory import cold_weight

    if mem.class_in_use is None:
        return correction(mem, outdoor, None)
    cell = load.deciding_cell(mem.lines, mem.class_in_use, mem.cell_rooms, mem.offset_k, mem.slope_k, cold_weight(outdoor))
    return correction(mem, outdoor, cell)


def inp(rooms=(), *, mode="shadow", op="auto", safety="ok", available=True, frost=(), advice="boiler", outdoor=5.0,
        now=NOW, params=PARAMS, flow=None, ret=None, burner=False, learning=True, along=()):
    return BoilerInputs(now, mode, op, safety, available, tuple(rooms), tuple(frost), advice, outdoor, params,
                        flow_c=flow, return_c=ret, burner_heating=burner, learning_allowed=learning,
                        heating_along=tuple(along))


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


def test_neither_the_ramp_nor_the_lockout_hold_goes_below_a_minimum_raised_since():
    """Review 2026-11-08 (#2): the minimum raised from 30 to 45 °C, the ramp from the old 30 sent 35."""
    from custom_components.thriftherm.const import BOILER_REPORTS_HEATING_AFTER

    params = replace(PARAMS, boiler_flow_min=45.0)
    mem = replace(BoilerMemory(), last_flow=30.0, state="heat", state_since_ts=NOW - 600)
    cmd, _ = decide(inp([rr(demand=1.0)], outdoor=-5.0, params=params), mem)
    assert cmd.flow_setpoint >= 45.0
    lockout = replace(inp([rr(demand=1.0)], outdoor=-5.0, params=params), reported_mode=BOILER_REPORTS_HEATING_AFTER)
    cmd, _ = decide(lockout, mem)
    assert cmd.flow_setpoint >= 45.0


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
    """Measured 2026-10-11: 1 K steps, four a day, left the bathroom 0.6 K below target after eight hours.

    Seen 2026-10-22: +1, +2 and +4 K within two and a half hours, the last 46 minutes after the one before, while the
    trend that asked for it looks back 90 minutes: it had not seen yet what the step before did."""
    mem, sizes = BoilerMemory(), []
    for i in range(6):
        mem = _step(mem, 1, i * 91)
        sizes.append(mem.adjustment_steps[-1])
    assert sizes == [1.0, 2.0, 2.0, 2.0, 2.0, 1.0]  # doubling up to 2 K, then what the day's 10 K up leave
    assert mem.offset_k == 10.0 and learning.moved_last_day(mem, NOW + 30000, 1) == 10.0
    assert _step(mem, 1, 600) is mem  # the day's 10 K up are spent

    mem = _step(_step(BoilerMemory(), 1, 0), 1, 91)  # +1, +2 ...
    assert _step(mem, 1, 91 + 89) is mem  # the trend's 90 minutes between steps up: the room has to answer first
    assert _step(mem, -1, 91 + 55) is mem  # ... and a whole hour before a step down
    back = _step(mem, -1, 91 + 61)
    assert back.adjustment_steps[-1] == -1.0 and back.offset_k == 2.0  # against the last one: half its size
    again = _step(back, 1, 91 + 61 + 91)
    assert again.adjustment_steps[-1] == 0.5  # and half again: closing in
    assert _step(again, -1, 91 + 61 + 91 + 61).adjustment_steps[-1] == -0.5  # never below half a kelvin

    assert _step(mem, 1, 91 + 181).adjustment_steps[-1] == 1.0  # three hours on, a new run starts at the first step


def test_steps_down_never_grow_and_leave_the_steps_up_their_own_allowance():
    """Review 2026-10-11: doubling both ways lowered the curve by 10 K in an afternoon, and no step up was left."""
    mem, sizes = BoilerMemory(offset_k=8.0), []
    for i in range(6):
        mem = _step(mem, -1, i * 61)
        sizes.append(mem.adjustment_steps[-1] if len(mem.adjustment_steps) > len(sizes) else None)
    assert sizes == [-1.0, -1.0, -1.0, -1.0, None, None]  # 4 K down in a day, in single steps
    assert learning.moved_last_day(mem, NOW + 30000, -1) == 4.0 and learning.moved_last_day(mem, NOW + 30000, 1) == 0.0
    up = _step(mem, 1, 3 * 61 + 91)  # the last step down was at minute 183
    assert up.adjustment_steps[-1] == 0.5 and _step(up, 1, 3 * 61 + 182).adjustment_steps[-1] == 1.0  # and up again at once


def test_a_step_goes_no_further_than_the_flow_can_follow():
    """Review 2026-10-11: half a kelvin below the flow maximum a 4 K step put the correction 3.5 K beyond it."""
    hot = replace(PARAMS, boiler_flow_max=36.0)  # the curve gives 30 °C at +15 °C outdoor
    mem = BoilerMemory(offset_k=2.0, adjustments=(NOW - 5500,), adjustment_steps=(1.0,))  # planned 32 °C, next step 2 K
    assert _step(mem, 1, 0, params=hot).offset_k == 4.0  # 2 K fit below the maximum
    close = _step(replace(mem, offset_k=5.0), 1, 0, params=hot)
    assert close.adjustment_steps[-1] == 1.0 and close.offset_k == 6.0  # only the kelvin that is left to the maximum
    assert _step(replace(mem, offset_k=5.7), 1, 0, params=hot).adjustments == (NOW - 5500,)  # 0.3 K: not worth a step
    low = BoilerMemory(offset_k=0.6)  # planned 30.6 °C, the minimum is 30 °C
    assert _step(low, -1, 0).offset_k == pytest.approx(0.0)  # and the same downwards


def test_what_a_limit_leaves_of_a_step_is_taken():
    """Review 2026-10-11: at +8.5 K the next step was 4 K, only 1.5 K fitted, and it was refused for three hours."""
    mem = BoilerMemory(offset_k=8.5, adjustments=(NOW - 5500,), adjustment_steps=(1.0,))  # the next step is 2 K
    after = _step(mem, 1, 0)
    assert after.offset_k == 10.0 and after.adjustment_steps[-1] == 1.5
    assert _step(replace(mem, offset_k=9.7), 1, 0).adjustments == (NOW - 5500,)  # 0.3 K: not worth a step


def test_every_step_of_the_day_is_counted_and_a_stored_step_has_its_time_and_its_size():
    """Review 2026-10-11: with ten steps remembered, 28 small ones moved the curve by 20 K in a day."""
    times = tuple(NOW - 80000 + i * 2800 for i in range(28))
    many = BoilerMemory(adjustments=times, adjustment_steps=tuple(0.5 if i % 3 else -0.5 for i in range(28)))
    assert learning.moved_last_day(many, NOW, 1) == 9.0 and learning.moved_last_day(many, NOW, -1) == 5.0
    assert BoilerMemory.from_storage(many.to_storage()).adjustment_steps == many.adjustment_steps
    assert _step(many, -1, 0) is many  # more than the 4 K down are used

    # a store that is broken, or has more times than sizes: a step is its time and its size, the newest are kept
    stored = {"offset_k": "nan", "slope_k": float("inf"), "adjustments": [NOW - 9, NOW - 4, NOW - 3, "x", NOW - 1],
              "adjustment_steps": [1.0, float("nan"), 2.0, -0.5]}
    broken = BoilerMemory.from_storage(stored)
    assert (broken.offset_k, broken.slope_k) == (0.0, 0.0)
    assert (broken.adjustments, broken.adjustment_steps) == ((NOW - 4, NOW - 1), (1.0, -0.5))
    long = BoilerMemory.from_storage({"adjustments": list(range(60)), "adjustment_steps": [0.5] * 60})
    assert long.adjustments == tuple(float(i) for i in range(12, 60)) and len(long.adjustment_steps) == 48
    # built by hand with times only (as some tests here do): the steps that have a size are the newest
    mixed = BoilerMemory(adjustments=(NOW - 900, NOW - 600), adjustment_steps=(2.0,))
    assert learning.moved_last_day(mixed, NOW, 1) == 2.0
    assert learning.next_step(BoilerMemory(adjustments=(NOW - 600,)), 1, NOW) == 1.0


ADJUST_GAP = 3700.0


def test_refining_uses_smaller_steps():
    history = tuple(NOW - 3 * 24 * 3600 - i * 4000 for i in range(6))  # old enough not to hit the daily cap
    mem = heating_memory(start=NOW, offset_k=0.0, adjustments=history)
    assert learning.phase(mem, NOW) == "refining"
    _, new, _ = feed(mem, [rr(demand=0.5)], flow=45.0, ret=42.0)
    assert corr(new) == pytest.approx(-0.5)


# --- hot water must not teach the heating curve ---------------------------------------------

from custom_components.thriftherm.engines import boiler_control as _bc
from custom_components.thriftherm.engines.boiler_control import BoilerInputs as _BI
from custom_components.thriftherm.engines.boiler_control import BoilerMemory as _BM

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


def test_a_block_sent_again_after_the_control_was_switched_off_counts_only_after_it_has_stood_a_while():
    # with the control switched off the boiler heats on its front knob, as after an eBUS outage
    t0 = 1_000_000.0
    blocked = _BM(state="block", state_since_ts=t0 - 7200, last_send_ts=t0 - 7200, last_flow=30.0, block_since_ts=t0 - 7300)
    knob = replace(_shower_inputs(t0, 45.0, 38.0, burner=False), control_mode="off", advice_source="none", gas_power_w=11300.0)
    _, mem = _bc.decide(knob, blocked)
    mem = replace(mem, last_payload=None, last_send_ts=None)  # what switching the control back to active does
    _, mem = _bc.decide(replace(knob, control_mode="active", now_ts=t0 + 60), mem)  # the first block goes out
    cmd, mem = _bc.decide(replace(knob, control_mode="active", now_ts=t0 + 120), mem)  # the knob's run still burns
    assert mem.hot_water_seen_ts is None and not cmd.hot_water


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
    """One burner run of burn_s followed by a pause, as a cycling boiler does it; on average near the flow sent, not at
    the boiler's own limit (test_boiler_limit.py)."""
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start, burner=True, flow=42.0, ret=39.0, learning=learning), mem)
    cmd, mem = _bc.decide(inp(rooms, mode="active", now=start + burn_s, burner=False, flow=38.0, ret=37.0, learning=learning), mem)
    return cmd, mem, start + burn_s + gap_s


# ------------------------------------------------------------------ a cycling boiler's curve comes down by its calls
def _holding_call(mem, start, *, minutes=18, pace=0.7, key="badezimmer", others=(), spoiled=None, burn_s=60.0, trend=0.3,
                  params=PARAMS, learning=True):
    """A room's call from `start`, the boiler burning `burn_s` every twelve minutes, until the room is at its target.

    Returns the command and the memory of the cycle in which the call has ended, and when that was.
    """
    from custom_components.thriftherm.models import EndedCall

    end, rise = start + minutes * 60.0, pace * minutes / 60.0
    calling = replace(rr(key=key, temp=round(20.9 - rise, 2), target=21.0, demand=0.1, trend=trend), boiler_call="calling")

    def cycle(rooms, now, burner, advice="boiler"):
        on = inp(rooms, mode="active", now=now, burner=burner, flow=40.0, ret=36.0, params=params, advice=advice)
        return decide(replace(on, learning_allowed=learning), mem)

    taken = {key, *(r.key for r in others)}
    idle = [k for k in ("kuche", "wohnzimmer") if k not in taken]
    rest = [replace(rr(key=k, temp=21.0, target=21.0, demand=0.0), boiler_call="idle") for k in idle]
    for burn in range(int(start), int(end - burn_s), max(720, int(burn_s) + 60)):
        _, mem = cycle([calling, *others, *rest], float(burn), True)
        _, mem = cycle([calling, *others, *rest], burn + burn_s, False)
    ended = EndedCall(start, end, calling.temperature, 20.9, spoiled)
    there = replace(calling, temperature=20.9, boiler_call="idle", last_call=ended)
    cmd, mem = cycle([there, *others, *rest], end, False, "boiler" if others else "none")
    return cmd, mem, end


def _ended_call(end, minutes=18, pace=0.7):
    from custom_components.thriftherm.models import EndedCall

    return EndedCall(end - minutes * 60.0, end, round(20.9 - pace * minutes / 60.0, 2), 20.9)


def _calls(mem, count, start=NOW, every=50, **call):
    """So many holding calls one after the other, a new one every `every` minutes."""
    cmd = None
    for i in range(count):
        cmd, mem, end = _holding_call(mem, start + i * every * 60.0, **call)
    return cmd, mem, end


BATH = "single:badezimmer"


def test_three_brisk_holding_calls_in_a_row_lower_a_cycling_boilers_curve():
    """The rise over the trend's 90 minutes only ever showed heat-ups (2026-10-23: a step down a kelvin below a target
    just raised, taken back 54 minutes later). A room holding its target swings by two tenths; what the flow does for it
    shows in how fast a call gets it from three tenths below to its target."""
    start = BoilerMemory(lines={BATH: LoadLine(level_k=3.0)})
    _, mem, _ = _calls(start, 2)
    assert mem.adjustments == () and mem.lines[BATH].fast_calls == 2
    cmd, mem, end = _holding_call(mem, NOW + 6000)
    assert mem.adjustment_steps == (-1.0,) and mem.last_adjust_reason == "short_cycling"
    assert correction(mem, 5.0, BATH) == pytest.approx(2.0) and mem.lines[BATH].fast_calls == 0
    assert mem.lines[BATH].trial == (None, 3.0, 0.0)  # a trial: where the line stood before
    _, more, _ = _calls(mem, 2, start=end + 1800)
    assert more.adjustments == mem.adjustments  # the next step takes three more

    _, slow, _ = _holding_call(_calls(start, 2)[1], NOW + 6000, pace=0.4)  # a call that is not brisk starts over
    assert slow.adjustments == () and slow.lines[BATH].fast_calls == 0
    # The test installation's bathroom on 2026-10-24/25: 0.4 K in 70 minutes with a line 10 K up, lagging in 63 % of its hours
    _, bath, _ = _calls(BoilerMemory(lines={BATH: LoadLine(level_k=9.66, slope_k=3.27)}), 4, every=120, minutes=70, pace=0.34)
    assert bath.adjustments == () and bath.lines[BATH].fast_calls == 0
    assert [row[-1] for row in bath.calls] == ["slow"] * 4


def test_a_call_that_says_nothing_about_the_flow_is_not_counted():
    """Seen 2026-10-23 11:14: the bathroom's target raised from 19 to 21 °C at 10:00, a kelvin below it and rising with
    0.5 K/h. That call is a heat-up, whenever it ends."""
    start = BoilerMemory(lines={BATH: LoadLine(level_k=3.0)})
    two = _calls(start, 2)[1]

    def third(**call):
        mem = _holding_call(two, NOW + 6000, **call)[1]
        return mem.adjustments, mem.lines[BATH].fast_calls, mem.calls[-1][-1]

    assert third() == ((NOW + 6000 + 1080,), 0, "fast")
    assert third(spoiled="target") == ((), 2, "target")  # not counted: neither brisk nor slow
    assert third(spoiled="helped") == ((), 2, "helped")
    assert third(minutes=30, burn_s=1500.0) == ((), 2, "not_cycling")  # the boiler ran through: the spread judges there
    assert third(learning=False) == ((), 2, "learning_paused")
    # a common correction learned while the curve per room could be switched off is where the room's line starts
    _, common, _ = _calls(BoilerMemory(offset_k=3.0), 3)
    assert common.adjustment_steps == (-1.0,) and common.offset_k == 3.0 and correction(common, 5.0, BATH) == pytest.approx(2.0)

    # with another room calling whose line sets the flow, the bathroom got more than its own line
    kitchen = replace(rr(key="kuche", temp=20.6, target=21.0, demand=0.2, trend=0.3), boiler_call="calling")
    both = {"majority:badezimmer": LoadLine(level_k=3.0), "majority:kuche": LoadLine(level_k=5.0)}
    _, mem, _ = _calls(BoilerMemory(lines=both), 3, others=[kitchen])
    assert mem.adjustments == () and mem.lines["majority:badezimmer"].fast_calls == 0
    assert [row[-1] for row in mem.calls] == ["not_setting_flow"] * 3


def _counted(*, pace=0.7, minutes=20, **memory):
    """The verdict on a bathroom call that has just ended, and the memory after it; `memory` alters what it meets."""
    start = NOW - minutes * 60.0
    mem = BoilerMemory(
        state="heat", state_since_ts=start, learning_since_ts=start - 3600, class_in_use="single", cell_rooms=("badezimmer",),
        burns=((start + 60, start + 120),), last_flow=43.0, lines={BATH: LoadLine(level_k=3.0, fast_calls=2, fast_call_ts=start)},
    )
    ended = _ended_call(NOW, minutes, pace)
    room = replace(rr(key="badezimmer", temp=20.9, target=21.0, demand=0.0), boiler_call="idle", last_call=ended)
    on = inp([room], mode="active", now=NOW, advice="none")
    after = learning.count_calls(on, replace(mem, **memory))
    assert learning.count_calls(on, after) is after  # a call is taken in once
    return after.calls[-1][-1], len(after.adjustments), after.lines[BATH].fast_calls


START = NOW - 1200.0


@pytest.mark.parametrize(("memory", "verdict"), [
    ({}, "fast"),
    ({"learning_since_ts": START + 60}, "learning_paused"),  # the learning was paused in the call
    ({"learning_since_ts": None}, "learning_paused"),
    ({"state": "block"}, "not_released"),
    ({"state_since_ts": None}, "not_released"),
    ({"state_since_ts": START + 301}, "not_released"),  # released long after the call began: the room rose by itself
    ({"state_since_ts": START + 299}, "fast"),  # the minimum time in a state may hold the release back
    ({"burns": ()}, "not_cycling"),  # no burn in it at all
    ({"burns": ((START - 600, START - 540),)}, "not_cycling"),  # a burn before it is not its own
    ({"burns": ((START + 60, START + 420),)}, "not_cycling"),  # six minutes of burner: it runs through
    ({"burn_since_ts": NOW - 400}, "not_cycling"),  # ... and so does a burn still going
    ({"outvoted": ("badezimmer",)}, "not_setting_flow"),
])
def test_a_call_counts_only_if_the_boiler_was_released_and_cycling_and_the_room_set_the_flow(memory, verdict):
    counted, steps, fast = _counted(**memory)
    assert (counted, steps, fast) == ((verdict, 1, 0) if verdict == "fast" else (verdict, 0, 2))


def test_a_call_is_judged_by_the_burns_of_the_last_hour_as_before():
    """The burns are kept three hours now, for the trials of a step up. A call is still judged by the last hour of them,
    as when no more were kept: a long burn early in a long call does not take it from the count (review 2026-11-15)."""
    early = ((NOW - 5000, NOW - 4600), (NOW - 600, NOW - 540))
    assert _counted(minutes=100, burns=early) == ("fast", 1, 0)


def test_brisk_is_six_tenths_of_a_kelvin_an_hour_and_three_calls_within_a_day():
    assert _counted()[:2] == ("fast", 1) and _counted(pace=0.6)[:2] == ("fast", 1)  # 0.2 K in 20 minutes
    assert _counted(pace=0.59, minutes=60) == ("slow", 0, 0)
    # Review 2026-11-13: two brisk calls in October and one in December are not three in a row
    old = {BATH: LoadLine(level_k=3.0, fast_calls=2, fast_call_ts=NOW - 86400 - 1300)}
    assert _counted(lines=old) == ("fast", 0, 1)


def test_a_room_another_rooms_demand_outbids_does_not_lower_its_own_line():
    """Review 2026-11-13: the kitchen's line stood at 0 K, but its demand boost of 6.4 K set the flow; compared by
    their lines alone the bathroom's 3 K "set the flow", and three calls on the kitchen's flow lowered it."""
    kitchen = replace(rr(key="kuche", temp=19.4, target=21.0, demand=0.8, trend=0.3), boiler_call="calling")
    both = {"majority:badezimmer": LoadLine(level_k=3.0), "majority:kuche": LoadLine()}
    cmd, mem, _ = _calls(BoilerMemory(lines=both), 3, others=[kitchen])
    assert mem.adjustments == () and [row[-1] for row in mem.calls] == ["not_setting_flow"] * 3
    assert mem.lines["majority:badezimmer"].fast_calls == 0 and "badezimmer" not in mem.outvoted  # asked anew each call


def test_a_step_down_by_the_calls_keeps_the_waits_and_limits_of_every_step():
    start = BoilerMemory(lines={BATH: LoadLine(level_k=3.0)})
    waiting = replace(start, adjustments=(NOW + 6000 + 1080 - 1800,), adjustment_steps=(1.0,))  # a step half an hour before
    _, mem, end = _calls(waiting, 3)
    assert len(mem.adjustments) == 1 and mem.lines[BATH].fast_calls == 3  # the hour after a step: it waits, and is kept
    _, mem, _ = _holding_call(mem, end + 1800)
    assert mem.adjustment_steps == (1.0, -1.0) and mem.lines[BATH].fast_calls == 0  # with the next brisk call

    spent = replace(start, adjustments=(NOW - 30000,) * 4, adjustment_steps=(-1.0,) * 4)  # the day's 4 K down
    assert len(_calls(spent, 3)[1].adjustments) == 4

    # at the flow minimum the step would move nothing, and the learning state says so
    low = replace(PARAMS, boiler_flow_min=45.0)
    cmd, mem, end = _calls(BoilerMemory(lines={BATH: LoadLine()}), 3, params=low)
    assert mem.adjustments == () and cmd.held_back == "short_cycling_at_flow_min"
    # ... while that is what holds back: not a day later, with the boiler running through
    later = inp([], mode="active", now=end + 86400 + 60, params=low)
    assert learning.held_back(later, mem, BATH) is None

    # a room that lags starts the count over, and gets its step up as before
    _, two, end = _calls(start, 2)
    cmd, lagging = _bathroom_lagging(replace(two, state="heat", state_since_ts=end, learning_since_ts=NOW), end + 3600, 5.0)
    assert lagging.lines[BATH].fast_calls == 0 and lagging.adjustment_steps == (1.0,)
    # a line whose value was borrowed takes the step from that value and owns it from then on
    lent = {"majority:badezimmer": LoadLine(level_k=3.0), BATH: LoadLine(borrowed=True, hours=2.0)}
    _, mem, _ = _calls(BoilerMemory(lines=lent), 3)
    assert correction(mem, 5.0, BATH) == pytest.approx(2.0) and not mem.lines[BATH].borrowed


def test_a_step_down_that_leaves_the_room_lagging_is_taken_back():
    """Else the answer is a step up, and the curve goes up and down as before (five of six steps down in October)."""
    _, mem, end = _calls(BoilerMemory(lines={BATH: LoadLine(level_k=3.0)}), 3)
    heating = replace(mem, state="heat", state_since_ts=end, learning_since_ts=NOW)

    cmd, back = _bathroom_lagging(heating, end + 100 * 60, 5.0)  # within three hours the room does not get there
    line = back.lines[BATH]
    assert (line.level_k, line.slope_k, line.trial) == (3.0, 0.0, None) and line.no_less_ts == end + 100 * 60
    assert back.adjustment_steps == (-1.0, 1.0) and back.last_adjust_reason == "flow_less_hurt"  # no step up on top
    assert line.fast_calls == 0
    # until then the trial waits: a room that does not lag after 90 minutes may still do so in the third hour
    idle = replace(rr(key="badezimmer", temp=21.0, target=21.0, demand=0.0), boiler_call="idle")
    _, waiting = _cycling(heating, end + 95 * 60, 5.0, [idle])
    assert waiting.lines[BATH].trial == (None, 3.0, 0.0)
    assert _bathroom_lagging(waiting, end + 150 * 60, 5.0)[1].last_adjust_reason == "flow_less_hurt"
    # if the room goes on lagging, the step up that follows is a first step: what was taken back is no step to double
    _, up = _bathroom_lagging(back, end + 200 * 60, 5.0)
    assert up.adjustment_steps == (-1.0, 1.0, 1.0) and up.last_adjust_reason == "rooms_lagging"
    # for a day this line is not lowered again, however brisk the calls
    _, again, _ = _calls(back, 3, start=end + 4 * 3600)
    assert again.adjustment_steps == (-1.0, 1.0) and again.calls[-1][-1] == "fast"
    _, later, _ = _calls(back, 3, start=end + 100 * 60 + 86400)
    assert later.adjustment_steps[-1] < 0

    cmd, late = _bathroom_lagging(heating, end + 4 * 3600, 5.0)  # four hours on it is a new question: a step up
    assert correction(late, 5.0, BATH) == pytest.approx(3.0) and late.lines[BATH].no_less_ts is None
    assert late.last_adjust_reason == "rooms_lagging" and late.lines[BATH].trial[0] == 0.0  # and that one is on trial


def test_after_a_step_up_that_did_not_help_less_flow_is_tried():
    """The question of 2026-11-13: how does a value that was learned wrongly come down again? The bathroom's line
    stood 10 K up and the room still rose with 0.34 K/h only: more flow did nothing, and nobody asked whether less hurts."""
    def bath(mem):
        return mem.lines[BATH]

    def step(mem):  # a step up, on trial, the burner at 4.7 % of the hour as on 2026-10-22
        return _bathroom_lagging(mem, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S))

    def judged(mem):  # 91 minutes on: the burner ran longer (8 %), and the room rose no faster
        return _bathroom_lagging(mem, NOW + 5460, 6.5, runs=_runs(NOW + 5460, OCT22_BEFORE_S, OCT22_AFTER_S, NOW))

    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200, lines={BATH: LoadLine(level_k=6.0)})
    _, mem = step(mem)
    assert mem.adjustment_steps == (1.0,)
    cmd, mem = judged(mem)  # it did not help: taken back, and half a kelvin less is tried
    assert mem.adjustment_steps == (1.0, -0.5) and mem.last_adjust_reason == "flow_less_trial"
    assert correction(mem, 6.5, BATH) == pytest.approx(5.5) and bath(mem).trial == (0.0, 6.0, 0.0)
    assert bath(mem).no_help_ts == NOW + 5460 and cmd.held_back == "flow_no_help"  # no step up for a day, as before

    _, kept = _bathroom_lagging(mem, NOW + 2 * 5460, 6.5, trend=-0.05)  # the room does no worse: it stays
    assert correction(kept, 6.5, BATH) == pytest.approx(5.5) and bath(kept).trial is None and bath(kept).no_less_ts is None
    _, hurt = _bathroom_lagging(mem, NOW + 2 * 5460, 6.5, trend=-0.15)  # it rises 0.1 K/h slower than before: back
    assert (bath(hurt).level_k, bath(hurt).slope_k, bath(hurt).no_less_ts) == (6.0, 0.0, NOW + 2 * 5460)
    assert hurt.adjustment_steps == (1.0, -0.5, 0.5) and hurt.last_adjust_reason == "flow_less_hurt"
    # released minutes ago nobody can tell how the room does: the trial waits (review 2026-11-13: it was confirmed)
    fresh = replace(mem, state_since_ts=NOW + 2 * 5460 - 600)
    assert bath(_bathroom_lagging(fresh, NOW + 2 * 5460, 6.5, trend=-0.15)[1]).trial == (0.0, 6.0, 0.0)
    # a line lowered in vain within the day is not lowered again on trial
    blocked = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                           lines={BATH: LoadLine(level_k=6.0, no_less_ts=NOW - 3600)})
    _, again = judged(step(blocked)[1])
    assert again.adjustment_steps == (1.0,) and correction(again, 6.5, BATH) == pytest.approx(6.0)
    # the next try comes with the next step up that does not help, a day on at the earliest
    _, next_day = _bathroom_lagging(kept, NOW + 5460 + 86400 + 60, 6.5)
    assert next_day.adjustment_steps == (1.0, -0.5, 0.5) and bath(next_day).trial[0] == 0.0
    # never below the common correction: a line that stands there is not what holds the room back
    at_common = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200, offset_k=2.0)
    _, mem = judged(step(at_common)[1])
    assert mem.adjustment_steps == (1.0,) and correction(mem, 6.5, BATH) == pytest.approx(2.0)


def test_the_last_calls_of_each_room_are_kept_for_the_diagnostics():
    """What a threshold of 0.6 K/h is worth shows only in the calls themselves: none were recorded in October."""
    _, mem, end = _calls(BoilerMemory(lines={BATH: LoadLine(level_k=3.0)}), 12, every=40)
    last = mem.calls[-1]  # the room, when it ended, minutes, kelvin, K/h, the flow sent, burner starts, the verdict
    assert len(mem.calls) == 10 and (*last[:5], *last[6:]) == ("badezimmer", end, 18.0, 0.21, 0.7, 2, "fast")
    assert 30.0 <= last[5] <= 44.0  # ramped up from the minimum
    back = BoilerMemory.from_storage(mem.to_storage())
    assert back.calls == mem.calls and back.lines[BATH].fast_calls == mem.lines[BATH].fast_calls
    _, same = decide(inp([], mode="active", now=end + 60, advice="none"), mem)
    assert same.calls == mem.calls  # a call is counted once
    # after a reset the room still shows its last call: it ended long ago and is not taken in a second time
    done = replace(rr(key="badezimmer", temp=21.0, target=21.0, demand=0.0), boiler_call="idle", last_call=_ended_call(end))
    _, forgotten = decide(inp([done], mode="active", now=end + 3600, advice="none"), learning.reset(mem))
    assert forgotten.calls == ()
    good = ["bad", 1.0, 2.0, 0.2, 0.6, 40.0, 1, "fast"]
    broken = BoilerMemory.from_storage({"calls": [good, ["x", "y"], 5, ["bad", 1, 2, 3, 4, None, 1, 7]]})
    assert broken.calls == (("bad", 1.0, 2.0, 0.2, 0.6, 40.0, 1, "fast"),) and BoilerMemory.from_storage({}).calls == ()
    assert BoilerMemory.from_storage({"lines": {BATH: {"level_k": 1.0, "last_step_ts": 5.0}}}).lines[BATH].fast_calls == 0
    line = LoadLine(level_k=1.0, fast_calls=2, fast_call_ts=NOW, no_less_ts=NOW - 5, no_help_ts=NOW - 9, trial=(None, 2.0, 0.5))
    assert BoilerMemory.from_storage(BoilerMemory(lines={BATH: line}).to_storage()).lines[BATH] == line
    # a reset forgets the calls with the rest; one for single rooms those rooms' calls and heat-up extras
    from custom_components.thriftherm.engines.heat_up import HeatUp

    learned = replace(mem, heat_up={"badezimmer": HeatUp(extra_k=4.0), "kuche": HeatUp(extra_k=2.0)},
                      calls=(*mem.calls, ("kuche", 1.0, 18.0, 0.2, 0.7, 40.0, 1, "fast")))
    assert learning.reset(learned).calls == ()
    partly = learning.reset(learned, ["badezimmer"])
    assert set(partly.heat_up) == {"kuche"} and [row[0] for row in partly.calls] == ["kuche"] and BATH not in partly.lines


def _cycled(rooms, released_s=3600.0, gap_s=900.0, **memory):
    """The memory after three short burner runs with these rooms."""
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - released_s, last_flow=35.0, **memory)
    now = NOW
    for _ in range(3):
        _, mem, now = _burst(mem, rooms, now, gap_s=gap_s)
    return mem


def test_a_room_whose_target_just_moved_does_not_raise_the_curve():
    """Seen 2026-10-22: target 21 °C from 03:07, +1 K at 04:11: the trend's 90 minutes still held the flat hour before
    the target moved, so the room read as not rising while it had only begun to."""
    bath = replace(rr(key="badezimmer", demand=0.5, temp=20.0, target=21.0, trend=0.1), boiler_call="calling")
    assert _cycled([replace(bath, target_steady_s=3840.0)]).adjustments == ()
    assert _cycled([replace(bath, target_steady_s=5400.0)]).adjustment_steps == (1.0,)
    assert _cycled([bath]).adjustment_steps == (1.0,)  # nothing known about its target: as before
    # a boiler that runs through asks the rooms the same way
    slow = replace(rr(demand=0.6, trend=0.05), target_steady_s=3840.0)
    _, mem, _ = feed(heating_memory(), [slow], flow=50.0, ret=40.0)
    assert mem.adjustments == ()


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


def test_the_cycling_memory_survives_a_restart():
    mem = _bc.BoilerMemory(burns=((1.0, 61.0), (900.0, 965.0)), slope_k=1.5, burn_since_ts=1800.0, offset_k=2.5,
                           adjustments=(NOW,), adjustment_steps=(-1.0,), last_adjust_reason="spread_small")
    back = _bc.BoilerMemory.from_storage(mem.to_storage())
    assert back.burns == ((1.0, 61.0), (900.0, 965.0)) and (back.offset_k, back.slope_k) == (2.5, 1.5)
    assert (back.adjustments, back.adjustment_steps, back.last_adjust_reason) == ((NOW,), (-1.0,), "spread_small")
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


def _cycling(mem, now, outdoor, rooms, runs=None, along=()):
    """One cycle of the control while the boiler cycles (three short burner runs in the hour) for these rooms."""
    runs = tuple((now - 3000 + i * 900, now - 2950 + i * 900) for i in range(3)) if runs is None else runs
    return decide(inp(rooms, mode="active", outdoor=outdoor, now=now, along=along), replace(mem, burns=runs))


def _bathroom_lagging(mem, now, outdoor, trend=0.0, temp=20.5, runs=None, along=()):
    """One cycle of the control while the boiler cycles and the bathroom alone calls and does not get there."""
    bath = replace(rr(key="badezimmer", temp=temp, target=21.0, demand=0.0, trend=trend), boiler_call="calling")
    idle = [replace(rr(key=key, temp=21.0, target=21.0, demand=0.0), boiler_call="idle") for key in ("kuche", "wohnzimmer")]
    return _cycling(mem, now, outdoor, [bath, *idle], runs, along)


def _runs(now, before_s, after_s=None, step_ts=None, cycle_s=900.0):
    """The burner's runs of the last three hours, one every `cycle_s`: `before_s` long each, `after_s` from `step_ts` on."""
    out, start = [], now - 3 * 3600.0 + 30.0
    while start < now:
        length = before_s if step_ts is None or start < step_ts else after_s
        if start + length < now:
            out.append((start, start + length))
        start += cycle_s
    return tuple(out)


# Burner seconds every 15 minutes, from a gas meter: 2026-10-22 with the flow set to 40-41 °C (3.6-5.8 % of the
# hour) and to 45-48 °C (6.7-9.4 %); 2026-11-14 an hour before the step of 00:42 (11.1 %) and in its trial (7.8 %);
# 2026-11-15 an hour before the step of 09:36 (21.7 %) and in its trial (23.2 %).
OCT22_BEFORE_S, OCT22_AFTER_S = 42.0, 72.0
NOV14_BEFORE_S, NOV14_AFTER_S = 100.0, 70.0
NOV15_BEFORE_S, NOV15_AFTER_S = 195.0, 209.0


def test_a_step_up_that_did_not_help_a_cycling_boilers_room_is_taken_back():
    """Seen 2026-10-22: +1, +2, +4 K and on to the day's 10 K for the bathroom, which stayed 0.9 K below target at a flow
    set to 40 as at 47 °C: the boiler burnt a minute at a time whatever was asked. By 2026-11-12 its line stood at
    +10.8 K, and the bathroom still lagged in 63 % of its hours."""
    from custom_components.thriftherm import texts

    def bath(mem):
        return mem.lines["single:badezimmer"]

    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    cmd, mem = _bathroom_lagging(mem, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S))
    assert mem.adjustment_steps == (1.0,) and bath(mem).trial == (0.0, 0.0, 0.0)  # its rise then, and the line before
    assert bath(BoilerMemory.from_storage(mem.to_storage())).trial == (0.0, 0.0, 0.0)
    raised = (bath(mem).level_k, bath(mem).slope_k)

    cmd, after = _bathroom_lagging(mem, NOW + 3000, 6.5)  # too early to tell: the trend still holds the time before
    assert (bath(after).level_k, bath(after).slope_k) == raised and bath(after).trial and cmd.held_back is None
    more_heat = _runs(NOW + 5460, OCT22_BEFORE_S, OCT22_AFTER_S, NOW)  # the burner ran longer, as on 2026-10-22
    for flicker in (0.0, 0.05):  # 91 minutes on the room rises no faster than before; a sensor's flicker is no answer
        cmd, back = _bathroom_lagging(mem, NOW + 5460, 6.5, trend=flicker, runs=more_heat)
        assert (bath(back).level_k, bath(back).slope_k, bath(back).trial) == (0.0, 0.0, None)  # the step is taken back
        assert back.adjustments == mem.adjustments and cmd.offset_k == 0.0
        assert cmd.held_back == "flow_no_help" and cmd.last_adjust_reason == "flow_no_help"
    assert all(texts.label("flow_no_help", lang) != "flow no help" for lang in texts.LANGUAGES)  # worded in both
    assert bath(BoilerMemory.from_storage(back.to_storage())).no_help_ts == NOW + 5460

    cmd, later = _bathroom_lagging(back, NOW + 5460 + 86400 - 60, 6.5)  # and for a day no step up is tried for it
    assert later.adjustments == mem.adjustments and bath(later).level_k == 0.0 and cmd.held_back == "flow_no_help"
    cmd, later = _bathroom_lagging(back, NOW + 5460 + 86400 + 60, 6.5)  # then things may have changed: one more try
    assert len(later.adjustments) == 2 and bath(later).trial is not None and cmd.held_back is None

    cmd, more = _bathroom_lagging(mem, NOW + 5460, 6.5, trend=0.15)  # it helped, though not enough: on with it
    assert more.adjustment_steps == (1.0, 2.0) and cmd.held_back is None
    assert bath(more).trial == (0.15, *raised)  # the next step is measured against this
    cmd, there = _bathroom_lagging(mem, NOW + 5460, 6.5, temp=20.9)  # the room got there: the step stays
    assert (bath(there).level_k, bath(there).slope_k, bath(there).trial) == (*raised, None) and bath(there).no_help_ts is None
    # Three hours on nobody can tell what the step did (the heating was blocked, the room aired): it stays, and the
    # room lagging then is a new question.
    cmd, late = _bathroom_lagging(mem, NOW + 3 * 3600 + 60, 6.5)
    assert late.adjustment_steps == (1.0, 1.0) and bath(late).no_help_ts is None and bath(late).level_k > raised[0]

    # a step down is no step to measure anything against
    down = learning.apply(inp([], mode="active", outdoor=6.5, now=NOW + 3660), mem, -1, "spread_small", "single:badezimmer")
    assert down.adjustment_steps == (1.0, -0.5) and bath(down).trial is None


def _bath(mem):
    return mem.lines["single:badezimmer"]


@pytest.mark.parametrize(("before_s", "after_s"), [(NOV14_BEFORE_S, NOV14_AFTER_S), (NOV15_BEFORE_S, NOV15_AFTER_S)])
def test_a_step_up_that_brought_no_more_heat_says_nothing_and_stays(before_s, after_s):
    """Seen 2026-11-14 and 15: the bathroom alone, the boiler at its lockout (S.8) a third of the time and more; a step of
    half a kelvin up made each burn a few seconds longer, and 90 minutes on the burner had run no longer than before.
    Judged by the room alone the step was taken back as no help, and the bathroom stayed 0.2 to 0.3 K below target
    for days. A step that brought no more heat says nothing about whether heat helps: it stays, is not doubled, and
    the next step is a trial again, measured against the same start."""
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    cmd, mem = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, before_s))
    assert mem.adjustment_steps == (1.0,) and _bath(mem).trial_share == pytest.approx(4 * before_s / 3600, abs=0.01)
    raised = (_bath(mem).level_k, _bath(mem).slope_k)

    later = NOW + 5460
    cmd, kept = _bathroom_lagging(mem, later, 6.5, runs=_runs(later, before_s, after_s, NOW))
    assert _bath(kept).no_help_ts is None and cmd.held_back is None and cmd.last_adjust_reason != "flow_no_help"
    assert kept.adjustment_steps == (1.0, 1.0)  # the step stays, and the next one is no larger
    assert _bath(kept).level_k > raised[0] and _bath(kept).trial == (0.0, 0.0, 0.0)  # measured against the start
    assert _bath(kept).trial_share == _bath(mem).trial_share
    stored = _bath(BoilerMemory.from_storage(kept.to_storage()))
    assert (stored.trial, stored.trial_share, stored.trial_open) == (_bath(kept).trial, _bath(kept).trial_share, False)


def test_more_flow_that_never_brings_more_heat_is_taken_back_whole_and_held_back_a_day():
    """The line of 2026-10-22 must not come back by steps that each said nothing: two kelvin more flow that left the
    burner running no longer is no help either. The run goes back to where it started, and the room gets no step up
    for a day (flow_no_help), as after a step that brought heat and no rise."""
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    _, mem = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, NOV15_BEFORE_S))
    t1 = NOW + 5460
    _, mem = _bathroom_lagging(mem, t1, 6.5, runs=_runs(t1, NOV15_BEFORE_S, NOV15_AFTER_S, NOW))
    assert mem.adjustment_steps == (1.0, 1.0)
    t2 = t1 + 5460
    cmd, back = _bathroom_lagging(mem, t2, 6.5, runs=_runs(t2, NOV15_BEFORE_S, NOV15_AFTER_S, NOW))
    assert (_bath(back).level_k, _bath(back).slope_k, _bath(back).trial) == (0.0, 0.0, None)  # the whole run
    assert back.adjustment_steps == (1.0, 1.0) and _bath(back).no_help_ts == t2
    assert cmd.held_back == "flow_no_help" and cmd.last_adjust_reason == "flow_no_help"
    # the day's hold: no step up for the room for 24 hours, then one more try
    cmd, held = _bathroom_lagging(back, t2 + 86400 - 60, 6.5, runs=_runs(t2 + 86400 - 60, NOV15_BEFORE_S))
    assert held.adjustments == back.adjustments and _bath(held).level_k == 0.0 and cmd.held_back == "flow_no_help"
    cmd, again = _bathroom_lagging(back, t2 + 86400 + 60, 6.5, runs=_runs(t2 + 86400 + 60, NOV15_BEFORE_S))
    assert len(again.adjustments) == 3 and _bath(again).trial is not None and cmd.held_back is None


def test_a_run_of_steps_that_brings_heat_but_no_rise_goes_back_to_its_start():
    """After a step that said nothing the next one brings heat (2026-10-22: 4.7 % of the hour to 8 %) and the room
    still does not rise: both go back, to the line before the first, and the day's hold follows."""
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    _, mem = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S))
    t1 = NOW + 5460
    _, mem = _bathroom_lagging(mem, t1, 6.5, runs=_runs(t1, OCT22_BEFORE_S, OCT22_BEFORE_S, NOW))  # nothing more yet
    assert mem.adjustment_steps == (1.0, 1.0) and _bath(mem).no_help_ts is None
    t2 = t1 + 5460
    cmd, back = _bathroom_lagging(mem, t2, 6.5, runs=_runs(t2, OCT22_BEFORE_S, OCT22_AFTER_S, t1))
    assert (_bath(back).level_k, _bath(back).slope_k, _bath(back).trial) == (0.0, 0.0, None)
    assert _bath(back).no_help_ts == t2 and cmd.held_back == "flow_no_help"


def test_a_run_of_steps_that_helped_stays():
    """The other way: after a step that said nothing the room rises: what the run raised stays, and the trial ends."""
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    _, mem = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, NOV15_BEFORE_S))
    t1 = NOW + 5460
    _, mem = _bathroom_lagging(mem, t1, 6.5, runs=_runs(t1, NOV15_BEFORE_S, NOV15_AFTER_S, NOW))
    raised = (_bath(mem).level_k, _bath(mem).slope_k)
    t2 = t1 + 5460
    cmd, kept = _bathroom_lagging(mem, t2, 6.5, trend=0.15, runs=_runs(t2, NOV15_BEFORE_S, NOV15_AFTER_S, NOW))
    assert (_bath(kept).level_k, _bath(kept).slope_k) >= raised and _bath(kept).no_help_ts is None
    assert cmd.held_back is None and cmd.last_adjust_reason != "flow_no_help"


def test_an_open_trial_that_runs_out_unjudged_goes_back_to_its_start():
    """Review 2026-11-15: the next step of an open trial may never come (another room lags further and gets it, the flow
    stands at its maximum). Run out after three hours, the steps that said nothing would stay, and a new trial would
    start from above them. With the room still lagging they go back to the start, without the day's hold: nothing was
    shown. A room that got there keeps them."""
    raised = LoadLine(level_k=0.85, slope_k=0.44, last_step_k=1.0, last_step_ts=NOW - 3 * 3600 - 60,
                      trial=(0.0, 0.0, 0.0), trial_share=0.2, trial_open=True, trial_run_k=1.0)
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                       lines={"single:badezimmer": raised}, adjustments=(NOW - 600,), adjustment_steps=(1.0,))
    cmd, back = _bathroom_lagging(mem, NOW, 6.5, runs=_runs(NOW, NOV15_BEFORE_S))
    assert (_bath(back).level_k, _bath(back).slope_k, _bath(back).trial) == (0.0, 0.0, None)
    assert _bath(back).no_help_ts is None and cmd.held_back is None
    _, there = _bathroom_lagging(mem, NOW, 6.5, temp=20.9, runs=_runs(NOW, NOV15_BEFORE_S))
    assert (_bath(there).level_k, _bath(there).slope_k, _bath(there).trial) == (0.85, 0.44, None)


def test_the_lift_to_the_flow_sent_is_no_part_of_a_trials_two_kelvin():
    """Review 2026-11-15: a room below the line that set the flow is lifted to the flow sent before its step (`apply`).
    That lift is what the room already got; counted into the 2 K of a trial, the first step without more heat was taken
    back at once for every room but the one setting the flow."""
    def room(key, temp):
        return replace(rr(key=key, temp=temp, target=21.0, demand=0.0, trend=0.0), boiler_call="calling")

    idle = replace(rr(key="wohnzimmer", temp=21.0, target=21.0), boiler_call="idle")
    rooms = [room("badezimmer", 20.5), room("kuche", 20.95), idle]
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                         lines={"majority:kuche": LoadLine(level_k=3.0)})
    _, mem = _cycling(start, NOW, 6.5, rooms, _runs(NOW, NOV15_BEFORE_S))
    bath = mem.lines["majority:badezimmer"]
    assert bath.trial == (0.0, 0.0, 0.0) and bath.trial_run_k == 1.0  # lifted by 3 K, stepped by 1 K
    later = NOW + 5460
    cmd, kept = _cycling(mem, later, 6.5, rooms, _runs(later, NOV15_BEFORE_S, NOV15_AFTER_S, NOW))
    assert kept.lines["majority:badezimmer"].no_help_ts is None and cmd.held_back is None
    assert kept.lines["majority:badezimmer"].level_k >= bath.level_k


def test_a_trial_stored_before_the_heat_was_measured_is_judged_as_before():
    """A line stored by an older version has no burner share for its trial: judged by the room alone, as then, so no
    one-off correction of what was learned is needed."""
    line = LoadLine(level_k=0.6, slope_k=0.75, last_step_k=1.0, last_step_ts=NOW, trial=(0.0, 0.0, 0.0))
    stored = LoadLine.from_storage({k: v for k, v in line.to_storage().items() if k not in ("trial_share", "trial_open")})
    assert stored.trial_share is None and stored.trial_open is False
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200, lines={"single:badezimmer": stored})
    later = NOW + 5460
    cmd, back = _bathroom_lagging(mem, later, 6.5, runs=_runs(later, NOV15_BEFORE_S))
    assert (_bath(back).level_k, _bath(back).slope_k) == (0.0, 0.0) and cmd.held_back == "flow_no_help"


def test_a_room_more_flow_did_not_help_does_not_hold_back_the_rooms_it_may_help():
    """Review 2026-11-12: judged by the room furthest behind alone, the bathroom's verdict kept the kitchen from its step."""
    def room(key, temp, **fields):
        return replace(rr(key=key, temp=temp, target=21.0, demand=0.0, trend=0.0), boiler_call="calling", **fields)

    idle = replace(rr(key="wohnzimmer", temp=21.0, target=21.0), boiler_call="idle")
    rooms = [room("badezimmer", 20.1), room("kuche", 20.6), idle]
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    judged = replace(start, lines={"majority:badezimmer": LoadLine(no_help_ts=NOW - 600)})
    cmd, mem = _cycling(judged, NOW, 6.5, rooms)
    assert mem.lines["majority:kuche"].last_step_k == 1.0 and mem.lines["majority:badezimmer"].level_k == 0.0
    assert cmd.held_back is None
    both = {"majority:badezimmer": LoadLine(no_help_ts=NOW - 600), "majority:kuche": LoadLine(no_help_ts=NOW - 900)}
    cmd, mem = _cycling(replace(start, lines=both), NOW, 6.5, rooms)
    assert mem.adjustments == () and cmd.held_back == "flow_no_help"
    # nor does a burner run long enough to be reviewed raise such a room's line in that day (rooms_slow)
    slow = [replace(r, demand=0.6, trend_k_per_h=0.05) for r in rooms[:2]] + rooms[2:]
    _, mem, _ = feed(replace(heating_memory(), lines=both), slow, flow=50.0, ret=40.0)
    assert mem.adjustments == ()
    _, mem, _ = feed(replace(heating_memory(), lines=judged.lines), slow, flow=50.0, ret=40.0)
    assert mem.last_adjust_reason == "rooms_slow" and mem.lines["majority:kuche"].last_step_k == 1.0


def test_a_step_the_correction_limit_swallows_is_shown_as_held_back():
    """Seen 2026-10-23: the bathroom's line at +9.01 K level and +3.44 K slope, +10.18 K at 6.5 °C outside.

    Once the ±10 K limit bit, its steps were swallowed while the learning state gave no reason, only that the room lagged.
    """
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                       lines={"single:badezimmer": LoadLine(level_k=9.01, slope_k=3.44)})
    cmd, mem = _bathroom_lagging(mem, NOW, 6.5)
    line = mem.lines["single:badezimmer"]
    assert mem.adjustment_steps == (1.0,) and line.level_k < 10.0  # a step of 1 K still fits under the limit
    assert cmd.held_back is None

    cmd, after = _bathroom_lagging(mem, NOW + 5460, 6.5, trend=0.15)  # the step helped; the next, 2 K, takes the level past +10 K
    # swallowed as before: the flow at 6.5 °C would feel less than the smallest step
    assert after.adjustments == mem.adjustments
    assert (after.lines["single:badezimmer"].level_k, after.lines["single:badezimmer"].slope_k) == (line.level_k, line.slope_k)
    assert cmd.held_back == "correction_limit"
    from custom_components.thriftherm import texts

    assert all(texts.label("correction_limit", lang) != "correction limit" for lang in texts.LANGUAGES)  # worded in both


def test_a_step_the_correction_limit_cuts_short_still_moves_up_to_it_and_says_so():
    """At 0 °C outside a step of 1 K from a level of +9.8 K: the level to +10 K, the slope as before, and the reason shown."""
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                       lines={"single:badezimmer": LoadLine(level_k=9.8, slope_k=2.0)})
    cmd, after = _bathroom_lagging(mem, NOW, 0.0)
    line = after.lines["single:badezimmer"]
    assert line.level_k == 10.0 and line.slope_k == pytest.approx(2.789, abs=1e-3)
    assert after.adjustment_steps == (0.674,)  # what the limit left of the step, felt at 0 °C
    assert cmd.held_back == "correction_limit"


def test_the_correction_limit_is_shown_only_while_it_holds_steps_back():
    """A step within the limit takes the reason back, and so does a while without a step that met it.

    Review 2026-10-23: after a step that was taken, the next try comes only once the wait after a step up is
    over, 90 minutes and a review window or two with the cycles drifting; an hour's reason went blank before it."""
    def held(mem, minutes):
        return learning.held_back(inp([rr(demand=0.5)], mode="active", outdoor=15.0, now=NOW + minutes * 60), mem)

    cut = _step(BoilerMemory(offset_k=9.5), 1, 0)  # at +15 °C all level: 0.5 K of the 1 K step fit
    assert (cut.offset_k, cut.adjustment_steps) == (10.0, (0.5,))
    assert held(cut, 0) == "correction_limit" and held(cut, 65) == "correction_limit"
    assert held(cut, 129) == "correction_limit" and held(cut, 131) is None
    down = _step(cut, -1, 81)
    assert down.offset_k == 9.5 and held(down, 81) is None
    assert held(learning.reset(cut), 0) is None  # nothing learned is nothing at its limit


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
    assert not _bc.hot_water_suspected(66.0, 52.0, 60.0, status_known=True)  # with a status code too (#7, 26.11.2026)
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
    behind = replace(rr(temp=21.4, target=23.5, trend=0.0), boiler_call="calling")
    mem = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200,
                       burns=tuple((NOW - 3000 + i * 900, NOW - 2950 + i * 900) for i in range(3)))
    by_hand = replace(behind, target_reason="override")
    after = learning.learn(inp([by_hand], mode="active", flow=45.0, ret=40.0), mem, [by_hand], False)
    assert after.adjustments == ()  # cycling, the room behind its wish: no step up


def test_after_a_pause_the_learning_waits_a_whole_window_and_judges_only_what_came_since():
    """Seen 2026-10-13: back from "observe only", the curve went up by 4 K in the same cycle, on the knob's burner runs."""
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


def test_the_heating_starts_of_the_day_are_counted_from_midnight_and_kept_through_a_restart():
    """Seen 2026-10-22: 66 starts in 23 hours on gas alone; the owner wants the starts of each day as a sensor."""
    midnight = NOW - 3600.0
    mem = _bc.BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0)
    rooms = [replace(rr(temp=20.5, target=21.0, trend=0.3), boiler_call="calling")]
    cmd = None
    for i in range(3):  # three burns of 45 s, told by the gas meter
        start = NOW + i * 900.0
        on = replace(inp(rooms, mode="active", now=start, flow=38.0, ret=34.0), gas_power_w=9000.0, day_start_ts=midnight)
        _, mem = _bc.decide(on, mem)
        cmd, mem = _bc.decide(replace(on, now_ts=start + 45.0, gas_power_w=0.0), mem)
    assert cmd.starts_today == 3  # each one counted as its burn ends

    restored = _bc.BoilerMemory.from_storage(mem.to_storage())
    shower = replace(inp(rooms, mode="active", now=NOW + 3000.0), gas_power_w=20000.0, reported_mode="hot_water")
    shower = replace(shower, day_start_ts=midnight)
    cmd, restored = _bc.decide(shower, restored)
    assert cmd.starts_today == 3  # kept through a restart, and hot water is no start of the heating

    next_day = replace(inp(rooms, mode="active", now=midnight + 86400.0 + 60.0), gas_power_w=0.0, day_start_ts=midnight + 86400.0)
    cmd, _ = _bc.decide(next_day, restored)
    assert cmd.starts_today == 0  # a new day begins at nought


def test_a_burn_that_turned_out_to_be_hot_water_is_no_start_of_the_heating():
    mem = learning.track_burns(_bc.BoilerMemory(), True, NOW, day_start_ts=NOW - 60.0)
    assert mem.starts_today == 0  # not yet: the burn may still turn out to be hot water
    dropped = learning.track_burns(learning.drop_burn_in_progress(mem), False, NOW + 10.0, day_start_ts=NOW - 60.0)
    assert dropped.starts_today == 0 and dropped.burns == ()
    heated = learning.track_burns(mem, False, NOW + 45.0, day_start_ts=NOW - 60.0)
    assert heated.starts_today == 1 and heated.burns == ((NOW, NOW + 45.0),)  # it ended as a heating burn: a start


def test_a_burn_the_boiler_then_reports_as_hot_water_never_takes_a_start_of_the_day_back():
    """Seen 2026-10-22 to 25: the starts of the day went from 71 to 72 and ten seconds later back to 71.

    The gas meter saw the flame before the status code said hot water. Home Assistant warns when a total_increasing
    sensor goes down, and a drop of more than a tenth (3 to 2 early in the day) its statistics take for a new meter.
    """
    midnight = NOW - 3600.0
    heating = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0, last_send_ts=NOW - 60)
    flame = replace(inp([rr()], mode="active", flow=40.0, ret=36.0), gas_power_w=20000.0, status_known=True)
    flame = replace(flame, day_start_ts=midnight)
    shown = []
    mem = heating
    for cycle in (
        flame,  # the flame, the status code not read yet
        replace(flame, now_ts=NOW + 10.0, reported_mode="hot_water"),  # ten seconds later: hot water
        replace(flame, now_ts=NOW + 300.0, gas_power_w=0.0, reported_mode=None),  # the tap is shut
        replace(flame, now_ts=NOW + 900.0, gas_power_w=9000.0, reported_mode="heating"),  # a burn for the heating
        replace(flame, now_ts=NOW + 945.0, gas_power_w=0.0, reported_mode="heating_after"),
    ):
        cmd, mem = decide(cycle, mem)
        shown.append(cmd.starts_today)
    assert shown == [0, 0, 0, 0, 1]  # the hot water never counted, the heating burn once as it ended
    assert mem.burns == ((NOW + 900.0, NOW + 945.0),)


def test_over_random_days_the_starts_never_go_down_and_are_the_heating_burns_that_ended_that_day():
    """Burner on and off, hot water dropping the burn in progress, restarts and midnights in any order, called as `decide` does.

    A restart changes nothing in the count: a burn it interrupted is judged at the first cycle after it like any other.
    """
    import random

    for seed in range(300):
        rnd = random.Random(seed)
        mem, now, day = BoilerMemory(), NOW, NOW - 3600.0
        burning_before, counted, shown = False, 0, 0
        for _ in range(300):
            now += rnd.choice((5.0, 10.0, 60.0, 600.0, 3600.0))
            while now >= day + 86400.0:  # local midnight: a new day begins at nought
                day, counted, shown = day + 86400.0, 0, 0
            if rnd.random() < 0.05:  # Home Assistant restarts, now and then twice in a row
                mem = BoilerMemory.from_storage(mem.to_storage())
            hot_water = rnd.random() < 0.15
            burning = not hot_water and rnd.random() < 0.5
            if hot_water:  # the burn in progress was hot water: dropped, and this cycle burns for no heating
                mem = learning.drop_burn_in_progress(mem)
                burning_before = False
            mem = learning.track_burns(mem, burning, now, day)
            if burning_before and not burning:  # a heating burn has ended
                counted += 1
            burning_before = burning
            assert mem.starts_today >= shown, f"seed {seed}: the starts of the day went down"
            assert mem.starts_today == counted, f"seed {seed}"
            shown = mem.starts_today


def test_a_burn_running_across_midnight_counts_once_on_the_day_it_ends():
    midnight, yesterday = NOW, NOW - 86400.0
    mem = learning.track_burns(BoilerMemory(), True, midnight - 600.0, yesterday)
    mem = learning.track_burns(mem, False, midnight - 555.0, yesterday)
    mem = learning.track_burns(mem, True, midnight - 30.0, yesterday)  # half a minute before midnight
    assert mem.starts_today == 1  # the day's last value: the burn still running is not in it
    mem = learning.track_burns(mem, True, midnight + 30.0, midnight)
    assert mem.starts_today == 0  # a new day begins at nought
    ended = learning.track_burns(mem, False, midnight + 90.0, midnight)
    assert ended.starts_today == 1 and ended.burns[-1] == (midnight - 30.0, midnight + 90.0)
    # one that turns out to be hot water after midnight counts on neither day
    dropped = learning.track_burns(learning.drop_burn_in_progress(mem), False, midnight + 90.0, midnight)
    assert dropped.starts_today == 0


def test_a_burn_in_progress_when_home_assistant_stops_counts_once_whether_it_ends_during_the_restart_or_after():
    """Its times are not kept (review 2026-10-13), but that it was burning is: so it is neither lost nor counted twice."""
    day = NOW - 3600.0
    mem = learning.track_burns(BoilerMemory(), True, NOW - 120.0, day)
    mem = learning.track_burns(mem, False, NOW - 75.0, day)  # one start
    mem = learning.track_burns(mem, True, NOW, day)  # burning as Home Assistant stops
    stored = mem.to_storage()

    ended = learning.track_burns(BoilerMemory.from_storage(stored), False, NOW + 120.0, day)
    assert ended.starts_today == 2  # it ended while Home Assistant was away: counted at the first cycle after
    assert ended.burns == ((NOW - 120.0, NOW - 75.0),)  # nobody watched it, so it is no burn of the burner share
    goes_on = learning.track_burns(BoilerMemory.from_storage(stored), True, NOW + 120.0, day)
    assert goes_on.starts_today == 1  # still burning: the same burn, counted as it ends
    assert learning.track_burns(goes_on, False, NOW + 600.0, day).starts_today == 2
    # stored again before the first cycle (a second restart right after the first) it is still not lost
    again = BoilerMemory.from_storage(BoilerMemory.from_storage(stored).to_storage())
    assert learning.track_burns(again, False, NOW + 120.0, day).starts_today == 2
    # and one the boiler reports as hot water at the first cycle is no start of the heating, as without a restart
    shower = learning.drop_burn_in_progress(BoilerMemory.from_storage(stored))
    assert learning.track_burns(shower, False, NOW + 120.0, day).starts_today == 1


def test_a_burn_still_going_after_a_restart_counts_once_although_the_boiler_is_not_readable_in_the_first_cycle():
    """Review 2026-10-26: after a restart the first cycle often came seconds before the boiler's entities.

    Taken in that cycle for a burn that had ended, the interrupted burn was counted at once and again as it ended.
    """
    midnight = NOW - 3600.0
    heating = BoilerMemory(state="heat", state_since_ts=NOW - 3600, last_flow=40.0, last_send_ts=NOW - 60)
    flame = replace(inp([rr()], mode="active", flow=40.0, ret=36.0), gas_power_w=9000.0, status_known=True)
    flame = replace(flame, day_start_ts=midnight, reported_mode="heating")
    _, mem = decide(flame, heating)  # burning as Home Assistant stops
    mem = BoilerMemory.from_storage(mem.to_storage())
    shown = []
    for cycle in (
        replace(flame, now_ts=NOW + 60.0, boiler_available=False, gas_power_w=None, status_known=False, reported_mode=None),
        replace(flame, now_ts=NOW + 64.0),  # the boiler's entities are back: still the same burn
        replace(flame, now_ts=NOW + 120.0, gas_power_w=0.0, reported_mode="heating_after"),
    ):
        cmd, mem = decide(cycle, mem)
        shown.append(cmd.starts_today)
    assert shown == [0, 0, 1]


def _rising(at):
    """A rise that has begun: 30 °C sent before, 35 °C now, the ramp's first window open."""
    cmd, mem = decide(at(NOW), replace(BoilerMemory(), state="block", state_since_ts=NOW - 600, last_flow=30.0))
    assert cmd.flow_setpoint == 35.0 and cmd.waiting == "ramp_limit"
    return mem


def _one_step_a_window(at, mem, start):
    """From `start` on, the rise opens a window of its own: 40 °C for ten minutes, then 45 °C."""
    flows = []
    for seconds in (0, 40, 80, 300, 590, 600):  # cycles also come 35-40 s apart when entities change (seen 2026-10-22)
        cmd, mem = decide(at(start + seconds), mem)
        flows.append(cmd.flow_setpoint)
    assert flows == [40.0, 40.0, 40.0, 40.0, 40.0, 45.0]


@pytest.mark.parametrize(("available", "safety"), [(False, "ok"), (True, "fallback")])
def test_after_an_outage_the_next_rise_starts_its_own_ramp_from_the_last_flow_sent(available, safety):
    """Review 2026-10-22: after a long hands-off the windows that had passed let the flow go from 35 to 60 °C in one cycle."""
    cold = [rr(demand=1.0)]

    def at(t, **kw):
        return inp(cold, mode="active", outdoor=-10.0, now=t, **kw)

    mem = _rising(at)
    for minute in (1, 90, 179):  # three hours with nothing sent: the boiler runs on its knob
        cmd, mem = decide(at(NOW + minute * 60.0, available=available, safety=safety), mem)
        assert cmd.plan == "hands_off"
    cmd, _ = decide(at(NOW + 3 * 3600.0), mem)
    assert cmd.flow_setpoint == 40.0 and cmd.waiting == "ramp_limit"  # one step above the last flow sent
    _one_step_a_window(at, mem, NOW + 3 * 3600.0)


def test_after_hours_with_the_control_off_the_rise_goes_on_one_step_at_a_time():
    """The same stale window, with the control switched off in the middle of a rise."""
    cold = [rr(demand=1.0)]
    mem = _rising(lambda t: inp(cold, mode="active", outdoor=-10.0, now=t))
    cmd, mem = decide(inp(cold, mode="off", outdoor=-10.0, now=NOW + 60.0), mem)
    assert cmd.plan == "disabled"
    cmd, _ = decide(inp(cold, mode="active", outdoor=-10.0, now=NOW + 3 * 3600.0), mem)
    assert cmd.flow_setpoint == 40.0 and cmd.waiting == "ramp_limit"
    _one_step_a_window(lambda t: inp(cold, mode="active", outdoor=-10.0, now=t), mem, NOW + 3 * 3600.0)


def test_a_rise_restored_after_hours_of_home_assistant_being_down_goes_on_one_step_at_a_time():
    """The stored window survives a restart; after hours of downtime it must not let the whole rise out at once."""
    stored = BoilerMemory(state="heat", state_since_ts=NOW - 600, last_flow=35.0, ramp_from=30.0, ramp_since_ts=NOW)
    restored = BoilerMemory.from_storage(stored.to_storage())
    cmd, _ = decide(inp([rr(demand=1.0)], mode="active", outdoor=-10.0, now=NOW + 3 * 3600.0), restored)
    assert cmd.flow_setpoint == 40.0 and cmd.waiting == "ramp_limit"
    _one_step_a_window(lambda t: inp([rr(demand=1.0)], mode="active", outdoor=-10.0, now=t), restored, NOW + 3 * 3600.0)


def test_a_restart_inside_the_first_window_gives_no_second_step_at_once():
    """A real restart begins with the boiler data missing and the rooms not read yet (seen at every restart
    from 2026-10-13 to 10-22); the boiler still holds the 35 °C sent before it, so the rise stays in its window."""
    stored = BoilerMemory(state="heat", state_since_ts=NOW - 120, last_flow=35.0, ramp_from=30.0, ramp_since_ts=NOW - 120)
    mem = BoilerMemory.from_storage(stored.to_storage())
    waiting = replace(inp([rr(temp=None, demand=None)], mode="active", outdoor=-10.0, now=NOW + 5, available=False),
                      room_data_pending=True)
    cmd, mem = decide(waiting, mem)
    assert cmd.plan == "hands_off"
    cmd, mem = decide(replace(waiting, boiler_available=True, now_ts=NOW + 30), mem)
    assert cmd.plan == "hands_off"
    cmd, _ = decide(inp([rr(demand=1.0)], mode="active", outdoor=-10.0, now=NOW + 90), mem)
    assert cmd.flow_setpoint == 35.0 and cmd.waiting == "ramp_limit"


def test_the_starts_say_what_they_are_counted_from():
    """Decided 15.11.: not everyone has a gas meter. The best sign counts; where it is coarse, the sensor says so."""
    cmd, _ = decide(replace(inp((rr(),)), gas_power_w=0.0), heating_memory())
    assert (cmd.starts_source, cmd.short_runs_may_be_missed) == ("gas_meter", False)
    cmd, _ = decide(replace(inp((rr(),)), status_known=True), heating_memory())
    assert (cmd.starts_source, cmd.short_runs_may_be_missed) == ("status_code", True)
    cmd, _ = decide(inp((rr(),)), heating_memory())
    assert (cmd.starts_source, cmd.short_runs_may_be_missed) == ("burner_sign", True)


def test_a_trial_judged_while_other_rooms_began_to_heat_along_says_nothing():
    """Heating along (16.11.) runs the burner longer for other radiators: a step judged by the burner after a room
    began to heat along, or opened further, would be taken back for heat the bathroom never got. Then the trial is
    left unjudged: the step stays, the next one is no larger, and no hold follows."""
    start = BoilerMemory(state="heat", state_since_ts=NOW - 7200, learning_since_ts=NOW - 7200)
    _, mem = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S))
    assert _bath(mem).trial is not None and _bath(mem).trial_along == ()
    t1 = NOW + 5460
    runs = _runs(t1, OCT22_BEFORE_S, OCT22_AFTER_S, NOW)  # more burner time ...
    cmd, mem = _bathroom_lagging(mem, t1, 6.5, runs=runs, along=(("kuche", 30.0),))  # ... with the kitchen heating along
    line = _bath(mem)
    assert line.no_help_ts is None and line.level_k > 0.0 and cmd.held_back != "flow_no_help"
    assert line.trial_open or len(mem.adjustments) == 2  # unjudged: it goes on, at most one more step of the same size
    if len(mem.adjustments) == 2:
        assert mem.adjustment_steps[-1] == mem.adjustment_steps[0]
    # an opening stage that changed is a change as well
    _, mem2 = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S), along=(("kuche", 30.0),))
    _, mem2 = _bathroom_lagging(mem2, t1, 6.5, runs=runs, along=(("kuche", 60.0),))
    assert _bath(mem2).no_help_ts is None
    # unchanged, the trial is judged as before: more heat and no rise goes back with the day's hold
    _, mem3 = _bathroom_lagging(start, NOW, 6.5, runs=_runs(NOW, OCT22_BEFORE_S), along=(("kuche", 30.0),))
    _, mem3 = _bathroom_lagging(mem3, t1, 6.5, runs=runs, along=(("kuche", 30.0),))
    assert _bath(mem3).no_help_ts == t1


def test_the_rooms_heating_along_at_a_trial_are_stored_with_it():
    line = LoadLine(level_k=1.0, trial=(0.0, 0.0, 0.0), trial_share=0.05, trial_along=(("kuche", 30.0),))
    back = BoilerMemory.from_storage(BoilerMemory(lines={"single:badezimmer": line}).to_storage())
    assert back.lines["single:badezimmer"].trial_along == (("kuche", 30.0),)


# ------------------------------------------------------------------ heating up is no holding (16.11.)
def _study(temp=19.4, target=20.0, trend=0.15, **kw):
    """The study at noon on 16.11.: preheating for 17:30, its call running for 90 minutes and more."""
    base = replace(rr(key="arbeitszimmer", temp=temp, target=target, demand=0.3, trend=trend), boiler_call="calling",
                   calling_s=7200.0, target_steady_s=7200.0)
    if "trend" in kw:
        kw["trend_k_per_h"] = kw.pop("trend")
    return replace(base, **kw)


def test_a_room_heating_up_on_its_plan_does_not_lag_one_behind_it_does():
    """The curve is for holding a temperature (16.11.: the study, preheating at its planned 0.1-0.2 K/h, counted as
    not getting there and its line went from 0 to +4.6 K). Preheating, after a raised target or in a quick heat-up, a
    room lags only once it rises slower than the pace its heat-up is planned with."""
    on_plan = _study(preheat_for_ts=NOW + 5 * 3600, preheat_pace_k_per_h=0.15, heat_rate_k_h=0.117)
    assert load.heats_up(on_plan) and load.lagging_rooms((on_plan,)) == []
    behind = replace(on_plan, heat_rate_k_h=0.57)  # planned with the mixed rate: it is behind
    assert load.lagging_rooms((behind,)) == [behind]
    # the end of a setback without a preheat, a target raised: the same, by its trend
    raised = _study(heating_up=True, trend=0.3, heat_rate_k_h=0.3)
    assert load.lagging_rooms((raised,)) == []
    assert load.lagging_rooms((replace(raised, trend_k_per_h=0.1),)) == [replace(raised, trend_k_per_h=0.1)]
    # holding: as before
    holding = _study(trend=0.0)
    assert not load.heats_up(holding) and load.lagging_rooms((holding,)) == [holding]


def test_a_raised_target_is_heated_up_to_until_the_room_gets_there():
    room = rr(key="arbeitszimmer", temp=19.0, target=20.0)
    assert load.heating_up(False, 19.0, room, reached_k=0.1)  # from 19 to 20: a heat-up begins
    assert not load.heating_up(False, 19.8, replace(room, temperature=19.9), reached_k=0.1)  # 0.1 below a raise: holding
    assert load.heating_up(True, 20.0, replace(room, temperature=19.6), reached_k=0.1)  # it goes on
    assert not load.heating_up(True, 20.0, replace(room, temperature=19.9), reached_k=0.1)  # there
    assert not load.heating_up(True, 20.0, replace(room, temperature=18.0, target=19.0), reached_k=0.1)  # the target went down


def test_a_trial_taken_while_the_room_heated_up_is_never_judged_helped_and_goes_back_after():
    """16.11. 11:50: a step for the study in its preheat, lifted from 0 to +4.6 K. Stored before this rule, it is found
    heating up at its review: unjudged, it waits; once the heat-up is over or the trial runs out, the line goes back to
    where it started, without a day's hold."""
    line = LoadLine(level_k=4.42, slope_k=1.13, hours=13.0, last_step_k=0.5, last_step_ts=NOW - 5460,
                    trial=(0.103, 0.0, 0.0), trial_share=0.12, trial_run_k=0.5)
    start = replace(heating_memory(NOW - 7200), learning_since_ts=NOW - 7200, lines={"few:arbeitszimmer": line},
                    class_in_use="few", cell_rooms=("arbeitszimmer", "badezimmer"))
    bath = replace(rr(key="badezimmer", temp=19.0, target=20.0, demand=0.4, trend=0.0), boiler_call="calling",
                   calling_s=7200.0, target_steady_s=7200.0)
    up = _study(trend=0.4, preheat_for_ts=NOW + 4 * 3600, preheat_pace_k_per_h=0.4, heat_rate_k_h=0.57)
    mem = learning.review_trials(inp([up, bath], mode="active", now=NOW), start, "few:badezimmer")
    kept = mem.lines["few:arbeitszimmer"]
    assert (kept.level_k, kept.slope_k, kept.no_help_ts) == (4.42, 1.13, None)  # not "helped" by a heat-up
    assert kept.trial_heat_up and kept.trial_open
    there = _study(temp=20.0, trend=0.4)  # comfort reached, the preheat over
    mem = learning.review_trials(inp([there, bath], mode="active", now=NOW + 1800), mem, "few:badezimmer")
    back = mem.lines["few:arbeitszimmer"]
    assert (back.level_k, back.slope_k, back.no_help_ts, back.trial) == (0.0, 0.0, None, None)


def test_the_heat_up_mark_of_a_trial_is_stored():
    line = LoadLine(level_k=1.0, trial=(0.1, 0.0, 0.0), trial_heat_up=True)
    back = BoilerMemory.from_storage(BoilerMemory(lines={"few:arbeitszimmer": line}).to_storage())
    assert back.lines["few:arbeitszimmer"].trial_heat_up


def test_a_trial_heating_up_goes_no_further_than_a_trial_holding():
    """Review 2026-11-18: in the heat-up branch the trial was set open and passed over, RUN_MAX_K never checked: a
    bathroom 0.3 below its raised target at a cycling boiler had its line go up by 1 K every 91 minutes, +10 K in 13.7
    hours, and "more flow did not help" never showed. At RUN_MAX_K it goes back to its start and is held for a day."""
    line = LoadLine(level_k=2.0, slope_k=0.5, hours=13.0, last_step_k=1.0, last_step_ts=NOW - 5460,
                    trial=(0.0, 0.0, 0.0), trial_share=0.12, trial_run_k=2.0, trial_heat_up=True, trial_open=True)
    start = replace(heating_memory(NOW - 7200), learning_since_ts=NOW - 7200, lines={"single:badezimmer": line},
                    class_in_use="single", cell_rooms=("badezimmer",))
    bath = _study(temp=20.7, target=21.0, trend=0.0, heating_up=True, heat_rate_k_h=0.2)
    bath = replace(bath, key="badezimmer")
    mem = learning.review_trials(inp([bath], mode="active", now=NOW), start, "single:badezimmer")
    back = mem.lines["single:badezimmer"]
    assert (back.level_k, back.slope_k, back.trial) == (0.0, 0.0, None)
    assert back.no_help_ts == NOW and mem.last_adjust_reason == "flow_no_help"


def test_a_raised_target_the_room_stands_still_below_is_held_not_heated_up_to():
    """Review 2026-11-18: the heat-up mark ended only 0.1 K below the target, so a bathroom standing 0.2-0.3 below all day
    counted as heating up. An hour on its target with no rise (below heat_call.STALL_RATE_K_H) is holding."""
    room = rr(key="badezimmer", temp=20.7, target=21.0, trend=0.05)
    assert load.heating_up(True, 21.0, room, reached_k=0.1, steady_s=1800.0)  # not long enough to tell
    assert not load.heating_up(True, 21.0, room, reached_k=0.1, steady_s=3700.0)  # an hour without a rise
    assert load.heating_up(True, 21.0, replace(room, trend_k_per_h=0.3), reached_k=0.1, steady_s=3700.0)  # rising


# ------------------------------------------------------------------ the flow each room needs itself (0.8.2)
def _two(living_temp=20.7, living_demand=0.1, **living):
    bath = room_result("badezimmer", 21.0, 20.0, demand=1.0, boiler_call="calling")
    living_room = room_result("wohnzimmer", 21.0, living_temp, **{"demand": living_demand, "boiler_call": "calling", **living})
    return [bath, living_room]


def test_the_boiler_says_which_flow_each_room_needs_itself_and_who_may_take_less():
    """The small bathroom radiator sets the flow; the living room calls as well and would need far less. Its valve
    may open in part (engines/valve_plan.flow_share); the bathroom's stays fully open."""
    cmd, _ = decide(inp(_two()), BoilerMemory())
    assert cmd.flow_set_by == "badezimmer" and cmd.flow_setpoint == 48.0
    assert cmd.room_flows == {"badezimmer": 48.0, "wohnzimmer": 40.8}
    assert cmd.flow_for_rooms == 48.0 and cmd.throttle_rooms == ("wohnzimmer",)


@pytest.mark.parametrize("living", [
    {"living_temp": 20.5},  # half a degree below its target
    {"boost_active": True},  # a quick heat-up
    {"calling_s": 1200.0, "trend_k_per_h": 0.1},  # calling for a quarter of an hour and hardly rising
    {"boiler_call": "stalled"},
    {"heating_up": True, "heat_rate_k_h": 1.0, "trend_k_per_h": 0.5},  # behind the pace its heat-up is planned with
    {"valve_unable": "exercise"},
])
def test_a_room_falling_behind_or_heating_up_quickly_keeps_its_valve_fully_open(living):
    temp = living.pop("living_temp", 20.7)
    behind = not {"boost_active", "valve_unable", "boiler_call"} & living.keys()  # a stalled room calls no more
    cmd, _ = decide(inp(_two(temp, **living)), BoilerMemory())
    assert cmd.throttle_rooms == () and cmd.room_flows["wohnzimmer"] < cmd.room_flows["badezimmer"]
    # fallen behind on a flow that would allow less: what the opening learns from (engines/flow_throttle.py)
    assert cmd.throttle_behind == (("wohnzimmer",) if behind else ())


def test_a_room_rising_as_planned_takes_less_also_after_a_quarter_of_an_hour():
    cmd, _ = decide(inp(_two(calling_s=1200.0, trend_k_per_h=0.3)), BoilerMemory())
    assert cmd.throttle_rooms == ("wohnzimmer",)


def test_no_room_takes_less_at_frost_while_drying_or_without_a_heating_boiler():
    from custom_components.thriftherm.models import DryingState

    assert decide(inp(_two(), frost=("badezimmer",)), BoilerMemory())[0].throttle_rooms == ()
    assert decide(inp(_two(drying=DryingState(active=True))), BoilerMemory())[0].throttle_rooms == ()
    blocked, _ = decide(inp(_two(), advice="none"), BoilerMemory())
    assert blocked.plan == "block" and blocked.throttle_rooms == () and blocked.room_flows is None


def test_what_is_sent_for_the_boilers_own_limit_is_not_the_rooms_to_throttle_against():
    """Sent above the curve because the boiler cycles (engines/boiler_limit.py), the mean flow is not higher: the other
    rooms measure against what the room that sets the flow asks for."""
    cmd, _ = decide(inp(_two()), replace(BoilerMemory(), comp_k=4.0))
    assert cmd.flow_for_rooms <= 48.0


def test_a_flow_held_back_by_the_ramp_is_what_the_rooms_measure_against():
    mem = replace(heating_memory(), last_flow=41.0, last_send_ts=NOW - 60)
    cmd, _ = decide(inp(_two()), mem)
    assert cmd.flow_setpoint == 46.0 and cmd.flow_for_rooms == 46.0


def test_the_room_that_sets_the_flow_is_not_one_that_fell_behind_a_limit():
    cmd, _ = decide(inp(_two()), BoilerMemory())
    assert cmd.throttle_behind == ()
    alone, _ = decide(inp([_two()[0]]), BoilerMemory())
    assert alone.throttle_behind == () and alone.throttle_rooms == ()
