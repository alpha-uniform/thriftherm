"""The boiler's own limit (19.11.2026): a boiler cycling on its least power delivers far less on average than the flow it
is sent. Then the curve is not too low (no step up, lifts made so go back), and the flow sent rises step by step until
on average the room gets what it needs, up to the most allowed (lower for a condensing boiler).

Measured 18.11. 19:07 to 19.11. 01:07, the bathroom alone calling: 43 °C sent, 28-34 °C on average, burns of 94 s, a
lockout of 7.5 minutes, the bathroom 0.8 K below its target falling 0.2 K an hour.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.thriftherm.engines import boiler_learning as learning
from custom_components.thriftherm.engines import boiler_limit as limit
from custom_components.thriftherm.engines import boiler_load as load
from custom_components.thriftherm.engines.boiler_control import decide
from custom_components.thriftherm.engines.boiler_load import LoadLine
from custom_components.thriftherm.engines.boiler_memory import BoilerMemory

from .conftest import PARAMS
from .test_boiler_control import NOW, heating_memory, inp, rr


def _cycling(now=NOW, burn_s=94.0, every_s=810.0, n=5) -> tuple[tuple[float, float], ...]:
    """The burns of the last hour of a boiler on its least power: short, every 13.5 minutes."""
    return tuple((now - (n - i) * every_s, now - (n - i) * every_s + burn_s) for i in range(n))


def _limited_memory(now=NOW, sent=43.0, mean=30.0, **kw) -> BoilerMemory:
    return replace(heating_memory(now - 7200), last_flow=sent, flow_mean_c=mean, flow_mean_ts=now - 60,
                   burns=_cycling(now), **kw)


def _bath(temp=19.2, trend=-0.2):
    return replace(rr(key="badezimmer", temp=temp, target=20.0, demand=0.8, trend=trend), boiler_call="calling",
                   calling_s=7200.0, target_steady_s=7200.0)


def test_the_mean_flow_follows_the_flow_over_half_an_hour():
    mean, ts = limit.follow_mean(None, None, 45.0, NOW)
    assert (mean, ts) == (45.0, NOW)
    mean, ts = limit.follow_mean(30.0, NOW, 50.0, NOW + limit.FLOW_MEAN_TAU_S)
    assert 42.0 < mean < 43.0  # 1 - e^-1 of the way
    assert limit.follow_mean(30.0, NOW, None, NOW + 60) == (30.0, NOW)  # no reading: as it was


def test_a_boiler_cycling_short_far_below_the_flow_sent_is_at_its_limit():
    assert limit.limited(_limited_memory(), NOW)
    assert not limit.limited(_limited_memory(mean=37.0), NOW)  # 6 K below: it gets there more or less
    long_burns = replace(_limited_memory(), burns=_cycling(burn_s=900.0, every_s=1200.0, n=3))
    assert not limit.limited(long_burns, NOW)  # it burns on: not cycling on its least power
    fresh = replace(_limited_memory(), state_since_ts=NOW - 600)
    assert not limit.limited(fresh, NOW)  # not long enough heating to tell
    assert not limit.limited(replace(_limited_memory(), flow_mean_c=None), NOW)


def test_the_compensation_rises_a_step_at_a_time_while_a_room_is_short_and_holds_otherwise():
    mem = _limited_memory()
    mem = limit.compensate(mem, True, True, False, NOW, room_k=20.0)
    assert (mem.comp_k, mem.comp_ts) == (limit.COMP_UP_K, NOW)
    assert limit.compensate(mem, True, True, False, NOW + 900, room_k=20.0).comp_k == limit.COMP_UP_K  # not yet
    mem = limit.compensate(mem, True, True, False, NOW + limit.COMP_UP_S, room_k=20.0)
    assert mem.comp_k == 2 * limit.COMP_UP_K
    assert limit.compensate(mem, True, True, False, NOW + 2 * limit.COMP_UP_S, room_k=0.5).comp_k == 4.5  # the most
    assert limit.compensate(mem, True, True, False, NOW + 2 * limit.COMP_UP_S, room_k=0.0).comp_k == 4.0  # at the most
    # no room short, none rising with room to spare, or none to judge: it holds, no day-long up and down (19.11. 02:10)
    assert limit.compensate(mem, True, False, False, NOW + 4 * limit.COMP_UP_S, room_k=20.0).comp_k == 4.0
    assert limit.compensate(mem, False, False, False, NOW + 4 * limit.COMP_UP_S, room_k=20.0).comp_k == 4.0
    # not at its limit, a room short: the curve's to learn, nothing added
    assert limit.compensate(_limited_memory(), False, True, False, NOW, room_k=20.0).comp_k == 0.0


def test_the_compensation_comes_down_once_the_rooms_rise_reliably_but_no_lower_than_they_still_do():
    """Decision 19.11. 02:10: down already while the rooms rise as planned with room to spare, half an hour on end and
    three quarters after the last step; once a step down leaves a room short, back up, and no lower again."""
    mem = replace(_limited_memory(), comp_k=5.0, comp_ts=NOW)
    TICK = 900.0  # judged every quarter of an hour
    t = NOW + TICK
    mem = limit.compensate(mem, True, False, True, t, room_k=20.0)
    assert mem.comp_k == 5.0 and mem.comp_ample_ts == t  # rising reliably since now: not yet
    t = NOW + limit.COMP_DOWN_S
    mem = limit.compensate(mem, True, False, True, t, room_k=20.0)
    assert mem.comp_k == 4.0  # half an hour reliably, 45 minutes since the last step
    mem = limit.compensate(mem, True, False, False, t + TICK, room_k=20.0)
    assert mem.comp_k == 4.0 and mem.comp_ample_ts is None  # at need, no more: it holds
    assert limit.compensate(mem, True, True, False, t + 2 * TICK, room_k=20.0).comp_k == 4.0  # its effect not shown yet
    mem = limit.compensate(mem, True, True, False, t + limit.COMP_UP_S, room_k=20.0)
    assert (mem.comp_k, mem.comp_floor_k) == (5.0, 5.0)  # short after the step down: back by that step, the floor
    t2 = t + limit.COMP_UP_S
    mem = limit.compensate(mem, True, False, True, t2 + TICK, room_k=20.0)
    mem = limit.compensate(mem, True, False, True, t2 + 4 * TICK, room_k=20.0)
    assert mem.comp_k == 5.0  # rising reliably again: not below what it fell short at


def _judged(**fields):
    base = {"boiler_call": "calling", "calling_s": 7200.0, "target_steady_s": 7200.0, "temperature_age_s": 300.0}
    return replace(rr(key=fields.pop("key", "badezimmer"), temp=fields.pop("temp", 19.7), target=fields.pop("target", 21.0),
                      trend=fields.pop("trend", 0.0)), **{**base, **fields})


def test_a_room_set_by_hand_far_below_its_target_is_judged_by_the_compensation():
    """Tonight (19.11. 02:27): the bathroom set by hand to 21.0 °C at 19.7, 1.3 K below and flat. For the curve a target
    set by hand counts only within 1 K (it is heated up to, review 2026-10-13), so the package of 64d3d2b1 added
    nothing; the compensation judges every calling room below target against the pace it is planned with."""
    bath = _judged(target_reason="override", heat_rate_k_h=0.1, heating_up=True)
    assert not load.lagging((bath,), load.lag_deficit_k(PARAMS.call_on_k))  # the curve's rule, unchanged
    assert limit.verdict((bath,), load.lag_deficit_k(PARAMS.call_on_k)) == (True, False)  # short


def test_rising_reliably_is_measured_against_the_planned_pace():
    lag = load.lag_deficit_k(PARAMS.call_on_k)
    # a heat-up planned at 0.5 K/h: 0.4 is short, 0.55 on plan, 0.65 with room to spare
    planned = {"heat_rate_k_h": 0.5, "heating_up": True}
    assert limit.verdict((_judged(trend=0.4, **planned),), lag) == (True, False)
    assert limit.verdict((_judged(trend=0.55, **planned),), lag) == (False, False)
    assert limit.verdict((_judged(trend=0.65, **planned),), lag) == (False, True)
    # holding (no heat-up): it has to rise at LAG_RATE_K_H at least, the plan of a heat-up does not apply
    assert limit.verdict((_judged(temp=20.6, trend=0.25, heat_rate_k_h=0.8),), lag) == (False, False)
    assert limit.verdict((_judged(temp=20.6, trend=0.1, heat_rate_k_h=0.8),), lag) == (True, False)
    # a pace learned below the floor of the planning is planned at the floor (room.PLAN_RATE_MIN_K_H)
    assert limit.verdict((_judged(trend=0.15, heat_rate_k_h=0.1, heating_up=True),), lag) == (True, False)
    # a preheat is judged by its pace since it began, not by the trend of before
    pre = _judged(trend=0.6, heat_rate_k_h=0.3, preheat_for_ts=NOW + 3600, preheat_pace_k_per_h=0.1)
    assert limit.verdict((pre,), lag) == (True, False)
    # one short and one rising reliably: short, and nothing to spare
    assert limit.verdict((_judged(trend=0.0), _judged(key="wohnzimmer", trend=0.9)), lag) == (True, False)


def test_what_says_nothing_about_the_flow_is_not_judged():
    lag = load.lag_deficit_k(PARAMS.call_on_k)
    nothing = (False, False)
    assert limit.verdict((_judged(boost_active=True),), lag) == nothing  # a quick heat-up has its own extra
    assert limit.verdict((_judged(temp=20.9),), lag) == nothing  # (almost) there
    assert limit.verdict((_judged(boiler_call="idle"),), lag) == nothing  # not calling
    assert limit.verdict((_judged(calling_s=600.0),), lag) == nothing  # calling for ten minutes only
    assert limit.verdict((_judged(target_steady_s=1800.0),), lag) == nothing  # the trend still holds the target before
    assert limit.verdict((_judged(temperature_age_s=7200.0),), lag) == nothing  # a silent sensor
    assert limit.verdict((_judged(trend=None),), lag) == nothing
    assert limit.verdict((_judged(thermostat_heating=False),), lag) == nothing  # its valve shut by its thermostat


def test_the_most_the_compensation_may_send_is_lower_for_a_condensing_boiler():
    assert limit.most_c(replace(PARAMS, boiler_condensing=True)) == limit.COMP_MAX_CONDENSING_C == 55.0
    assert limit.most_c(replace(PARAMS, boiler_condensing=False)) == PARAMS.boiler_flow_max


def test_at_its_limit_the_boiler_is_sent_more_than_the_curve_and_the_curve_is_not_lifted():
    """The bathroom alone, 0.8 K short and falling: the flow sent rises by 2 K every 45 minutes, the line stays."""
    params = replace(PARAMS, boiler_condensing=False)
    line = LoadLine(level_k=2.0, slope_k=0.0, hours=10.0)
    mem = _limited_memory(lines={"single:badezimmer": line}, class_in_use="single", cell_rooms=("badezimmer",),
                          learning_since_ts=NOW - 7200)
    sent = []
    now = NOW
    for i in range(5):
        now = NOW + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(now), flow_mean_ts=now - 60, flow_mean_c=30.0)
        cmd, mem = decide(replace(inp([_bath()], mode="active", now=now, flow=30.0, ret=29.0), params=params), mem)
        sent.append(cmd.flow_setpoint)
    assert sent[-1] - sent[0] >= 3.0  # rising with the compensation
    assert mem.comp_k >= 4.0
    assert mem.lines["single:badezimmer"].level_k <= 2.0  # no step up of the curve
    assert learning.held_back(replace(inp([_bath()], mode="active", now=now), params=params), mem, "single:badezimmer",
                              limited=True) == "boiler_limit"


def test_a_trial_up_under_the_limit_goes_back_unjudged_and_a_lifted_line_eases_down():
    """The lifts made while the boiler was at its limit go back: the trial to its start without a hold, a line lifted
    before by a step at a time, the compensation taking over what is needed."""
    line = LoadLine(level_k=3.0, slope_k=0.0, hours=10.0, last_step_k=0.5, last_step_ts=NOW - 5460,
                    trial=(0.0, 2.0, 0.0), trial_share=0.1, trial_run_k=1.0)
    mem = _limited_memory(lines={"single:badezimmer": line}, class_in_use="single", cell_rooms=("badezimmer",),
                          learning_since_ts=NOW - 7200)
    after = learning.review_trials(inp([_bath()], mode="active", now=NOW), mem, "single:badezimmer", limited=True)
    back = after.lines["single:badezimmer"]
    assert (back.level_k, back.trial, back.no_help_ts) == (2.0, None, None)
    eased = learning.learn(inp([_bath()], mode="active", now=NOW), after, [_bath()], False, "single:badezimmer",
                           limited=True)
    assert eased.lines["single:badezimmer"].level_k < 2.0 and eased.last_adjust_reason == "boiler_limit"


def test_the_compensation_is_stored_and_the_mean_is_not():
    mem = replace(BoilerMemory(), comp_k=3.0, comp_ts=NOW, flow_mean_c=31.0, flow_mean_ts=NOW)
    back = BoilerMemory.from_storage(mem.to_storage())
    assert (back.comp_k, back.comp_ts, back.flow_mean_c) == (3.0, NOW, None)


def test_a_boiler_with_a_long_lockout_starting_twice_an_hour_is_at_its_limit_as_well():
    """d.02 = 30 min and 8 kW (19.11.): the model has it start 2.7 times an hour with burns of 170-265 s. Too few starts
    for the cycling of the curve's learning (three), still the boiler's own limit: two short burns in the hour do."""
    two = replace(_limited_memory(), burns=_cycling(burn_s=240.0, every_s=1500.0, n=2))
    assert limit.limited(two, NOW)
    one = replace(_limited_memory(), burns=_cycling(burn_s=240.0, every_s=1500.0, n=1))
    assert not limit.limited(one, NOW)  # one burn says nothing yet


# ---------------------------------------------------------------------------
# Learned for good (decision 19.11. 02:10): what the rooms rose reliably with, per room's line and outdoor band
# ---------------------------------------------------------------------------
def test_the_outdoor_band_and_the_key_of_what_is_learned():
    assert limit.band(4.9) == 0 and limit.band(5.0) == 5 and limit.band(-3.0) == -5
    assert limit.band(-30.0) == -10 and limit.band(30.0) == 15 and limit.band(None) is None
    assert limit.key("single:badezimmer", 4.0) == "single:badezimmer@0"
    assert limit.key("single:badezimmer", None) == "single:badezimmer"
    assert limit.key(None, 4.0) is None


def test_one_phase_moves_nothing_two_that_agree_do_slowly_and_within_the_band_nothing_does():
    line = limit.CompLine()
    line = limit.record(line, 4.0)
    assert line.value_k == 0.0 and line.phases == (4.0,)  # one phase says little, either way
    line = limit.record(line, 5.0)
    assert line.value_k == limit.COMP_SHIFT_MAX_K and line.phases == ()  # two agree: up by what both say, at most 2 K
    line = limit.record(limit.record(line, 4.0), 4.5)
    assert line.value_k == 4.0
    assert limit.record(limit.record(line, 4.5), 3.5).value_k == 4.0  # within the band: it stays
    assert limit.record(limit.record(line, 6.0), 2.0).value_k == 4.0  # they disagree: it stays
    down = limit.record(limit.record(line, 2.5), 2.0)
    assert down.value_k == 2.5  # down as well, by what both say
    assert limit.record(limit.record(line, 2.5), 6.0).phases == (2.5, 6.0)


def test_a_phase_gives_the_least_the_rooms_rose_reliably_with_and_more_than_they_fell_short_at():
    assert limit.phase_value(ok_k=3.0, short_k=None) == 3.0
    assert limit.phase_value(ok_k=None, short_k=3.0) == 3.0 + limit.COMP_STEP_K
    assert limit.phase_value(ok_k=2.0, short_k=4.0) == 4.0 + limit.COMP_STEP_K
    assert limit.phase_value(ok_k=4.0, short_k=2.0) == 4.0
    assert limit.phase_value(ok_k=None, short_k=None) is None


def test_a_settled_judgement_is_evidence_of_the_phase():
    mem = replace(_limited_memory(), comp_k=3.0, comp_ts=NOW)
    assert limit.observe(mem, True, True, False, NOW + 600) == mem  # a step a few minutes ago says nothing yet
    t = NOW + limit.COMP_SETTLE_S
    short = limit.observe(mem, True, True, False, t)
    assert (short.comp_short_k, short.comp_ok_k) == (3.0, None)
    assert limit.observe(mem, False, True, False, t).comp_short_k is None  # short, not at its limit: the curve's
    ok = limit.observe(mem, True, False, False, t)
    assert (ok.comp_short_k, ok.comp_ok_k) == (None, 3.0)
    assert limit.observe(replace(ok, comp_k=2.0, comp_ts=NOW), True, False, True, t).comp_ok_k == 2.0  # the least
    nothing = limit.observe(mem, True, False, False, t, judged=False)
    assert (nothing.comp_short_k, nothing.comp_ok_k) == (None, None)


def test_the_start_of_a_heating_takes_what_was_learned_for_its_line_and_band_a_neighbour_band_else():
    lines = {"single:badezimmer@0": limit.CompLine(value_k=4.0), "single:badezimmer@10": limit.CompLine(value_k=1.0)}
    assert limit.learned(lines, "single:badezimmer@0") == 4.0
    assert limit.learned(lines, "single:badezimmer@5") == 4.0  # the nearer, the colder of two as near
    assert limit.learned(lines, "single:badezimmer@-5") == 4.0
    assert limit.learned(lines, "single:badezimmer@-10") is None  # two bands away: nothing
    assert limit.learned(lines, "few:badezimmer@0") is None
    assert limit.learned(lines, None) is None


def _heat(mem, rooms, now, **kw):
    params = kw.pop("params", replace(PARAMS, boiler_condensing=False))
    return decide(replace(inp(rooms, mode="active", now=now, flow=kw.pop("flow", 30.0), ret=29.0, **kw), params=params), mem)


def test_a_heating_begins_with_what_was_learned_and_a_block_records_its_phase_and_ends_it():
    learned = {"single:badezimmer@5": limit.CompLine(value_k=4.0)}
    mem = replace(BoilerMemory(), state="block", state_since_ts=NOW - 7200, comp_lines=learned, last_flow=30.0)
    idle = [replace(rr(key=k, temp=20.5, target=20.0), boiler_call="idle") for k in ("wohnzimmer", "kuche", "arbeitszimmer")]
    cmd, mem = _heat(mem, [_bath(), *idle], NOW)
    assert cmd.plan == "heat" and mem.comp_k == 4.0 and mem.comp_key == "single:badezimmer@5"
    # a phase in which the bathroom rose reliably with 3 K and fell short with 2: it ends with the block
    mem = replace(mem, comp_ok_k=3.0, comp_short_k=2.0, state_since_ts=NOW - 7200)
    cmd, mem = _heat(mem, [replace(_bath(), temperature=20.2, boiler_call="idle", demand=0.0)], NOW + 7200, advice="none")
    assert cmd.plan == "block"
    assert mem.comp_k == 0.0 and mem.comp_key is None and (mem.comp_ok_k, mem.comp_short_k) == (None, None)
    assert mem.comp_lines["single:badezimmer@5"].phases == (3.0,)  # one phase: recorded, nothing moved
    assert mem.comp_lines["single:badezimmer@5"].value_k == 4.0


def test_frost_alone_begins_without_what_was_learned():
    learned = {"single:badezimmer@5": limit.CompLine(value_k=6.0)}
    mem = replace(BoilerMemory(), state="block", state_since_ts=NOW - 7200, comp_lines=learned, last_flow=30.0)
    cold = replace(_bath(temp=6.0), target=7.0)
    cmd, mem = _heat(mem, [cold], NOW, op="heat_pump_only", frost=("badezimmer",), advice="heat_pump")
    assert cmd.plan == "heat" and mem.comp_k == 0.0  # the frost floor is enough, no gas for the comfort learned
    assert cmd.flow_setpoint >= 40.0


def test_the_learned_start_rises_with_the_ramp_and_stays_below_the_most_for_a_condensing_boiler():
    learned = {"single:badezimmer@5": limit.CompLine(value_k=30.0)}
    mem = replace(BoilerMemory(), state="block", state_since_ts=NOW - 7200, comp_lines=learned, last_flow=30.0)
    params = replace(PARAMS, boiler_condensing=True, boiler_flow_max=70.0)
    sent = []
    for i in range(8):
        cmd, mem = _heat(mem, [_bath()], NOW + i * 600, params=params)
        sent.append(cmd.flow_setpoint)
    assert sent[1] - sent[0] <= 5.0  # the ramp: 5 K per 10 minutes at most
    assert max(sent) <= limit.COMP_MAX_CONDENSING_C


def test_the_learning_survives_a_restart_and_garbage_is_dropped():
    mem = replace(BoilerMemory(), comp_lines={"single:badezimmer@0": limit.CompLine(value_k=3.5, phases=(4.0,))},
                  comp_key="single:badezimmer@0", comp_ok_k=3.0, comp_short_k=2.0, comp_floor_k=3.0, comp_ample_ts=NOW,
                  comp_last_up=True)
    back = BoilerMemory.from_storage(mem.to_storage())
    assert back.comp_lines == mem.comp_lines
    assert (back.comp_key, back.comp_ok_k, back.comp_short_k, back.comp_floor_k, back.comp_ample_ts, back.comp_last_up) == (
        "single:badezimmer@0", 3.0, 2.0, 3.0, NOW, True)
    stored = mem.to_storage()
    stored["comp_lines"] = {"single:badezimmer@0": {"k": "x"}, "junk": 3, "few:kuche@5": {"k": 99.0, "p": ["a", 2.0]},
                            "few:wohnzimmer@5": {"k": 2.0, "p": [1.0, "b", 3.0]}}
    assert BoilerMemory.from_storage(stored).comp_lines == {"few:wohnzimmer@5": limit.CompLine(value_k=2.0, phases=(1.0, 3.0))}


def test_forgetting_a_room_forgets_what_its_lines_learned_here_too():
    mem = replace(BoilerMemory(), comp_lines={"single:badezimmer@0": limit.CompLine(value_k=3.5),
                                              "few:wohnzimmer@0": limit.CompLine(value_k=1.0)})
    assert set(learning.reset(mem, ["badezimmer"]).comp_lines) == {"few:wohnzimmer@0"}
    assert learning.reset(mem).comp_lines == {}


# ---------------------------------------------------------------------------
# The cases of the last days
# ---------------------------------------------------------------------------
def test_regression_tonight_four_rooms_set_by_hand_the_living_room_sets_the_flow_the_bathroom_short():
    """19.11. 02:27: all rooms set by hand till 10:00, four calling (majority), the living room's line sets the flow,
    the bathroom at 19.7 for 21.0 and flat; 64d3d2b1 kept boiler_limit_k at 0. Now it rises, the lines stay."""
    rooms = [
        replace(_judged(key="badezimmer", temp=19.7, target=21.0, trend=0.0, target_reason="override", heating_up=True,
                        heat_rate_k_h=0.1), demand=0.9),
        replace(_judged(key="schlafzimmer", temp=20.5, target=21.0, trend=0.1, target_reason="override"), demand=0.5),
        replace(_judged(key="wohnzimmer", temp=19.4, target=19.5, trend=0.0, target_reason="override"), demand=0.2),
        replace(_judged(key="arbeitszimmer", temp=19.8, target=20.0, trend=0.1, target_reason="override"), demand=0.3),
        replace(_judged(key="kuche", temp=19.3, target=19.5, trend=0.0, target_reason="override"), boiler_call="idle"),
    ]
    lines = {"majority:wohnzimmer": LoadLine(level_k=8.0, slope_k=0.9, hours=12.0),
             "majority:badezimmer": LoadLine(level_k=0.6, slope_k=0.1, hours=14.0)}
    mem = _limited_memory(sent=46.0, mean=36.0, lines=lines, class_in_use="majority",
                          cell_rooms=("arbeitszimmer", "badezimmer", "schlafzimmer", "wohnzimmer"),
                          learning_since_ts=NOW - 7200)
    for i in range(6):
        now = NOW + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(now), flow_mean_ts=now - 60, flow_mean_c=36.0)
        cmd, mem = _heat(mem, rooms, now)
    assert mem.comp_k >= 4.0
    assert mem.lines["majority:wohnzimmer"].level_k <= 8.0 and mem.lines["majority:badezimmer"].level_k <= 0.6


def test_regression_the_bathroom_alone_the_compensation_rises_and_once_it_rises_reliably_comes_down_a_step():
    """18./19.11.: the bathroom alone 0.8 K below and falling at 43 °C. With more sent it rises with room to spare, and
    the compensation gives a step back instead of riding on to the most."""
    line = LoadLine(level_k=2.0, slope_k=0.0, hours=10.0)
    mem = _limited_memory(lines={"single:badezimmer": line}, class_in_use="single", cell_rooms=("badezimmer",),
                          learning_since_ts=NOW - 7200)
    now = NOW
    for i in range(6):
        now = NOW + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(now), flow_mean_ts=now - 60, flow_mean_c=30.0)
        _, mem = _heat(mem, [_bath()], now)
    top = mem.comp_k
    assert top >= 5.0
    rising = replace(_bath(trend=0.5), temperature=19.6)
    for i in range(1, 6):
        t = now + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(t), flow_mean_ts=t - 60, flow_mean_c=34.0)
        _, mem = _heat(mem, [rising], t)
    assert top - 2.0 <= mem.comp_k < top
    assert mem.comp_ok_k is not None


def test_regression_the_return_from_away_behind_its_pace_at_the_limit_gets_more_and_the_curve_stays():
    """The return from away preheats at the pace planned: behind it at the boiler's limit the compensation helps, the
    curve is not lifted (16.11.: a preheat counted for the curve lifted the study's line to +4.6 K)."""
    back = replace(_judged(temp=18.0, target=20.0, trend=0.05, heat_rate_k_h=0.3, preheat_for_ts=NOW + 4 * 3600,
                           preheat_pace_k_per_h=0.05, target_reason="away_return"), demand=0.9)
    line = LoadLine(level_k=2.0, slope_k=0.0, hours=10.0)
    mem = _limited_memory(lines={"single:badezimmer": line}, class_in_use="single", cell_rooms=("badezimmer",),
                          learning_since_ts=NOW - 7200)
    for i in range(4):
        now = NOW + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(now), flow_mean_ts=now - 60, flow_mean_c=30.0)
        _, mem = _heat(mem, [back], now)
    assert mem.comp_k >= 3.0 and mem.lines["single:badezimmer"].level_k <= 2.0


def test_a_quick_heat_up_alone_moves_nothing():
    quick = replace(_bath(), boost_active=True)
    _, mem = _heat(replace(_limited_memory(), class_in_use="single", cell_rooms=("badezimmer",)), [quick], NOW - 60)
    mem = replace(mem, comp_k=2.0, comp_ts=NOW - 3600)
    for i in range(4):
        now = NOW + i * limit.COMP_UP_S
        mem = replace(mem, burns=_cycling(now), flow_mean_ts=now - 60, flow_mean_c=30.0)
        _, mem = _heat(mem, [quick], now)
    assert mem.comp_k == 2.0


def test_a_change_of_line_within_a_heating_keeps_what_is_sent_and_its_step_time():
    """Seen 19.11.2026 06:42: the kitchen called along, the bathroom's line went from few to single, and that line's 0
    (one phase, nothing moved) took the 2 K down to 0; every change began the quarter of an hour anew. The boiler's
    limit is the same whichever room sets the flow: a change within a heating keeps what it sends, more only if the new
    line learned more."""
    lines = {"single:badezimmer@5": limit.CompLine(value_k=0.0, phases=(1.0,)), "few:kuche@5": limit.CompLine(value_k=3.0)}
    mem = replace(_limited_memory(), comp_k=2.0, comp_ts=NOW - 600, comp_key="few:badezimmer@5", comp_lines=lines)
    kept = limit.begin(mem, "single:badezimmer@5", NOW)
    assert (kept.comp_k, kept.comp_ts, kept.comp_key) == (2.0, NOW - 600, "single:badezimmer@5")
    more = limit.begin(mem, "few:kuche@5", NOW)
    assert (more.comp_k, more.comp_ts) == (3.0, NOW)
    # the first heating after a block still begins with its line's own value, a 0 as well
    fresh = limit.begin(replace(mem, comp_key=None), "single:badezimmer@5", NOW)
    assert fresh.comp_k == 0.0


def test_a_step_up_waits_until_the_last_one_can_show_and_each_is_judged():
    """Seen 19.11.2026: a step of 1 K every quarter of an hour, judged by a trend over 90 minutes, went from 1 to 12 K
    while the bathroom already rose from about 8 on, and no step stood the 45 minutes its judgement needs: what the
    phase learned was the overshoot. Up now 2 K once the last step has stood COMP_SETTLE_S; back up after a step down
    by that step only."""
    assert limit.COMP_UP_S == limit.COMP_SETTLE_S and limit.COMP_UP_K == 2.0
    mem = limit.compensate(_limited_memory(), True, True, False, NOW, room_k=20.0)
    assert mem.comp_k == 2.0
    for wait in (900.0, 1800.0, limit.COMP_SETTLE_S - 60.0):
        assert limit.compensate(mem, True, True, False, NOW + wait, room_k=20.0).comp_k == 2.0  # its effect not shown yet
    # the step has stood: its judgement is evidence of the phase, and then the next one
    t = NOW + limit.COMP_SETTLE_S
    mem = limit.observe(mem, True, True, False, t)
    assert mem.comp_short_k == 2.0
    mem = limit.compensate(mem, True, True, False, t, room_k=20.0)
    assert mem.comp_k == 4.0
    # rising reliably at 4: down a step, short again: back by that one step, not two
    mem = limit.observe(mem, True, False, True, t + limit.COMP_SETTLE_S)
    assert mem.comp_ok_k == 4.0
    mem = limit.compensate(mem, True, False, True, t + limit.COMP_SETTLE_S, room_k=20.0)
    mem = limit.compensate(mem, True, False, True, t + limit.COMP_SETTLE_S + limit.COMP_AMPLE_S, room_k=20.0)
    assert mem.comp_k == 3.0
    back = t + limit.COMP_SETTLE_S + limit.COMP_AMPLE_S + limit.COMP_SETTLE_S
    mem = limit.compensate(mem, True, True, False, back, room_k=20.0)
    assert (mem.comp_k, mem.comp_floor_k) == (4.0, 4.0)
    assert limit.phase_value(mem.comp_ok_k, mem.comp_short_k) == 4.0  # what it learns: what was enough, no overshoot


def test_a_heating_after_a_short_block_begins_a_step_below_what_the_last_one_ended_with():
    """Seen 19.11.2026 16:12: the bathroom and the bedroom reached their targets with 12 K above the curve, the block
    took that to 0, and at 16:45 they called again with nothing above it: a burn of 50 s, the lockout, the bathroom
    falling, and 4-5 hours of steps to climb back while it learned a heat-up too slow. The boiler's limit did not change
    in half an hour: a heating within COMP_CARRY_S of the last, in the same outdoor band, begins a step below what that
    one ended with, unless its line learned more. Later, in another band or for frost, it begins as before."""
    ended = limit.end(replace(_limited_memory(), comp_k=12.0, comp_ts=NOW - 3000, comp_key="few:schlafzimmer@10"), NOW)
    assert (ended.comp_k, ended.comp_ts) == (0.0, None)  # nothing more is sent while blocked
    later = NOW + 2000
    carried = limit.begin(ended, "few:badezimmer@10", later)
    assert (carried.comp_k, carried.comp_ts) == (11.0, later)  # its step time: the next step waits until it can show
    assert limit.begin(ended, "few:badezimmer@5", later).comp_k == 0.0  # another band
    assert limit.begin(ended, "few:badezimmer@10", NOW + limit.COMP_CARRY_S + 60).comp_k == 0.0  # too long ago
    assert limit.begin(ended, "few:badezimmer@10", later, start_learned=False).comp_k == 0.0  # frost alone
    more = replace(ended, comp_lines={"few:badezimmer@10": limit.CompLine(value_k=13.0)})
    assert limit.begin(more, "few:badezimmer@10", later).comp_k == 13.0
    # once begun, the carry is spent: a second block after a heating that sent nothing carries nothing
    assert limit.begin(limit.end(replace(carried, comp_k=0.0), later + 600), "few:badezimmer@10", later + 900).comp_k == 0.0
    # a restart in the block keeps it
    restored = BoilerMemory.from_storage(ended.to_storage())
    assert limit.begin(restored, "few:badezimmer@10", later).comp_k == 11.0
