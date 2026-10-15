"""Boiler controller: flow temperature and heating release via ebusd SetMode.

Pure logic, no Home Assistant import. Like the Midea controller it runs in
shadow mode (plan published and logged) or active mode (SetMode sent).

Principles
* SetMode is the only operational lever of the atmoTEC without a controller.
  It has to be repeated; without it the boiler returns to its front knob after
  9-16 minutes (measured). We resend every 2 minutes, so a lost telegram is
  harmless and a dead Home Assistant falls back to the knob.
* Hands off (nothing sent) when the boiler data is not trustworthy or the
  safety state is "fallback": the boiler then runs on its knob.
* Heating is released only when a room needs the boiler (advisor: boiler or
  both, or frost protection). Otherwise heating is blocked with disablehc, so
  the boiler stops keeping the circuit hot. Hot water is left to the boiler: both
  hot water temperatures are sent as "-" (no value), tapping is never disabled,
  and only with "Keep hot water ready" switched off disablehwcload stops what the
  boiler heats without a tap open (see `payload`).
* Flow temperature = heating curve (two points, -10 °C and +15 °C outdoor)
  + what the room that needs the most asks for: its learned line, and while it
  calls the demand boost and its heat-up extra (`heat_up`); limited to the
  configured range and ramped up by at most 5 °C per 10 minutes.

The learning of the curve lives in `boiler_learning`, what is remembered in `boiler_memory`.
"""

from __future__ import annotations

from dataclasses import replace

from ..const import (
    BOILER_PLAN_BLOCK,
    BOILER_PLAN_DISABLED,
    BOILER_PLAN_HANDS_OFF,
    BOILER_PLAN_HEAT,
    BOILER_REPORTS_HEATING,
    BOILER_REPORTS_HEATING_AFTER,
    BOILER_REPORTS_HOT_WATER,
    CTRL_ACTIVE,
    CTRL_OFF,
    MODE_OFF,
    SAFETY_FALLBACK,
    SOURCE_BOILER,
    SOURCE_BOTH,
)
from ..models import BoilerCommand
from . import boiler_learning as learning
from . import boiler_load as load
from . import heat_up
from .boiler_learning import BURNING_GAS_W
from .boiler_memory import BoilerInputs, BoilerMemory, cold_weight, correction, curve_flow
from .common import round_half

RESEND_INTERVAL_S = 120.0  # boiler falls back to its knob after 9-16 min without SetMode (measured)
MIN_STATE_S = 300.0  # no heat/block toggling faster than this (frost excepted)
RAMP_UP_K = 5.0
RAMP_WINDOW_S = 600.0
DEMAND_BOOST_K = 8.0  # added at 100 % room demand

# Hot water on a combi boiler heats the primary exchanger, and the circuit sensors
# see it: return above flow, flow far above anything the heating curve asks for.
# ebusd does not reliably report the hot-water state (measured 2026-09-12: pump
# state stayed "off" through a shower), so it is recognised from the temperatures
# themselves and the circuit is given time to cool back before spread is trusted.
HOT_WATER_REVERSE_SPREAD_K = 1.0
HOT_WATER_ABOVE_FLOW_MAX_K = 2.0
# The hold-off is about the circuit's temperatures: `decide` counts no heating run in it, so no spread
# is collected and no run is reviewed (what such a review says by the rooms' progress waits with it).
# The cycling rule and the hours booked to a load class rest on the rooms alone and do not wait for it:
# they stop only with `learning_allowed`.
HOT_WATER_HOLDOFF_S = 3600.0
# ebusd polls flow and return only every few minutes, so the temperature rules above
# notice a shower late. Two signals are faster:
HOT_WATER_GAS_W = BURNING_GAS_W  # gas burning although space heating was blocked: only hot water is left
# A block counts only once it has been sent without a gap for this long. After an eBUS
# outage the boiler is back on its front knob and may fire for space heating while the
# first block is still on its way (measured 2026-09-19 00:59: adapter gone for 12 min,
# burner ran 20 s in S.4 and was taken for hot water).
BLOCK_IN_FORCE_S = 120.0
HOT_WATER_ABOVE_SETPOINT_K = 15.0  # flow far above the heating flow we sent (burner overshoot stays below)
HOT_WATER_SHOWN_S = 600.0  # how long "hot water active" stays on after the last sign, given the polling lag
# With a status code the boiler says hot water itself for as long as it lasts, so there is no gap
# between signs to bridge: two minutes cover a status read that fails or comes late.
HOT_WATER_SHOWN_WITH_STATUS_S = 120.0
# A guess (gas under a block, temperatures) is taken back when the boiler's status code then says
# "after heating" (S.5-S.8): after hot water it shows S.15-S.17. Measured 2026-09-28 00:44: the block
# had been sent for two minutes, but ebusd, restarted after an address change, could not write yet;
# the boiler fired once more by its knob and went to S.7.
HOT_WATER_RETRACT_S = 300.0


def payload(flow: float, disable_hc: bool, disable_hwc_load: bool = False) -> str:
    # hcmode;flowtempdesired;hwctempdesired;hwcflowtempdesired;disablehc;disablehwctapping;
    # disablehwcload;remoteControlHcPump;releaseBackup;releaseCooling
    # disablehwcload stops what the boiler heats without a tap open: the keep-warm of a combi
    # boiler (reported 2026-10-10: one burner start an hour all night) or the loading of a cylinder.
    # Hot water gets "-" (no value): tested 13.09.2026, the boiler takes the heating
    # setpoint and hot water keeps following the knob, so control never changes it.
    return f"auto;{flow:.1f};-;-;{1 if disable_hc else 0};0;{1 if disable_hwc_load else 0};0;0;0"


def hot_water_suspected(
    flow_c: float | None,
    return_c: float | None,
    flow_max_c: float,
    *,
    gas_w: float | None = None,
    heating_blocked: bool = False,
    heating_flow_c: float | None = None,
    reported_mode: str | None = None,
    status_known: bool = False,
) -> bool:
    """True when the boiler can only be making hot water.

    `heating_blocked` and `heating_flow_c` describe what was really sent to the
    boiler (active control); in plan-only mode the knob rules and neither applies.
    The boiler's own status code, where it is known, outranks every guess.

    Without a status code a flow above the configured maximum is only a sign while that
    maximum is in force, which is while one of the two was sent. A boiler on its own knob
    (plan-only, hands off) heats as hot as the knob says: with the knob at 66 °C and a
    maximum of 60 °C every heating run read as hot water, its burns were left out and all
    its gas was counted as hot water (review 2026-10-13).
    """
    if reported_mode == BOILER_REPORTS_HOT_WATER:
        return True
    if reported_mode == BOILER_REPORTS_HEATING:
        return False
    burning = gas_w is not None and gas_w > HOT_WATER_GAS_W
    if reported_mode == BOILER_REPORTS_HEATING_AFTER and not burning:
        # overrun after a heating run; with gas burning the code lags behind a new start
        return False
    if heating_blocked and burning:
        return True
    if flow_c is None:
        return False
    # A hot flow is a sign of hot water being made, so not without a flame where a gas meter
    # tells: after a tap the exchanger stays hot with the pump standing. Seen 2026-10-13: a
    # shower ended at 03:00, the flow stood above 57 °C until 03:05 (maximum 55 °C), and hot
    # water was shown until 03:15.
    flame_possible = gas_w is None or burning
    limit_in_force = heating_blocked or heating_flow_c is not None
    if flame_possible and flow_c > flow_max_c + HOT_WATER_ABOVE_FLOW_MAX_K and (status_known or limit_in_force):
        return True
    if flame_possible and heating_flow_c is not None and flow_c > heating_flow_c + HOT_WATER_ABOVE_SETPOINT_K:
        return True
    if status_known:
        # Return above flow is also what the circuit shows after every heating run while the
        # pump keeps running (S.7): the exchanger cools first. With a status code at hand the
        # boiler says hot water itself, so this rule only stands in for boilers without one.
        return False
    return return_c is not None and return_c - flow_c > HOT_WATER_REVERSE_SPREAD_K


def _heating_after_all(inp: BoilerInputs, mem: BoilerMemory, now: float) -> bool:
    """A recent hot water guess that the boiler's status code now contradicts."""
    seen = mem.hot_water_seen_ts
    if not mem.hot_water_guessed or seen is None or now - seen > HOT_WATER_RETRACT_S:
        return False
    burning = inp.gas_power_w is not None and inp.gas_power_w > HOT_WATER_GAS_W
    return inp.reported_mode == BOILER_REPORTS_HEATING or (inp.reported_mode == BOILER_REPORTS_HEATING_AFTER and not burning)


def decide(inp: BoilerInputs, mem: BoilerMemory) -> tuple[BoilerCommand, BoilerMemory]:
    """Plan the boiler command, keeping hot-water episodes out of the learning."""
    now = inp.now_ts
    sent = inp.control_mode == CTRL_ACTIVE and mem.last_send_ts is not None
    block_in_force = (
        sent
        and mem.state == BOILER_PLAN_BLOCK
        and inp.boiler_available
        and mem.block_since_ts is not None
        and now - mem.block_since_ts >= BLOCK_IN_FORCE_S
    )
    hot_water_now = False
    if hot_water_suspected(
        inp.flow_c,
        inp.return_c,
        inp.params.boiler_flow_max,
        gas_w=inp.gas_power_w,
        heating_blocked=block_in_force,
        heating_flow_c=mem.last_flow if sent and mem.state == BOILER_PLAN_HEAT else None,
        reported_mode=inp.reported_mode,
        status_known=inp.status_known,
    ):
        # discard whatever this run had collected: those samples already carry the shower
        guessed = inp.reported_mode != BOILER_REPORTS_HOT_WATER
        before = (mem.hot_water_before_guess_ts if mem.hot_water_guessed else mem.hot_water_seen_ts) if guessed else None
        mem = learning.forget_run(mem)
        mem = replace(mem, hot_water_seen_ts=now, hot_water_guessed=guessed, hot_water_before_guess_ts=before)
        hot_water_now = True
    elif _heating_after_all(inp, mem, now):
        # the boiler says it heated: the guess was wrong, the hot water hold-off is taken back
        mem = replace(
            mem, hot_water_seen_ts=mem.hot_water_before_guess_ts, hot_water_guessed=False, hot_water_before_guess_ts=None
        )
    # One burner sign for everything, hot water never in it: the burn itself is counted (cycling,
    # burner share), and it is a heating run to learn from unless the circuit is still cooling
    # back from hot water.
    burning = learning.flame(inp) and not hot_water_now
    cooling_back = mem.hot_water_seen_ts is not None and now - mem.hot_water_seen_ts < HOT_WATER_HOLDOFF_S
    if hot_water_now:
        # the burn in progress was the start of this hot water: it is dropped, not kept as a short heating burn
        mem = learning.drop_burn_in_progress(mem)
    # Where the boiler does not say itself what it burns for, a tap in the hour after hot water is
    # likely more hot water that the temperatures missed: those burns are left out as well.
    mem = learning.track_burns(mem, burning and (inp.status_known or not cooling_back), now)
    cmd, mem = _decide(inp, mem, burning and not cooling_back, burning)
    share, starts = learning.burner_share(mem, now)
    cmd = replace(cmd, burner_share=share, burner_starts_last_hour=starts)
    shown_for = HOT_WATER_SHOWN_WITH_STATUS_S if inp.status_known else HOT_WATER_SHOWN_S
    recent = mem.hot_water_seen_ts is not None and now - mem.hot_water_seen_ts < shown_for
    return replace(cmd, hot_water=recent, hot_water_seen_ts=mem.hot_water_seen_ts), mem


NO_CLASS = {"class_in_use": None, "class_candidate": None, "class_candidate_since_ts": None, "cell_rooms": ()}


def _boosts(mem: BoilerMemory, calling: list) -> dict[str, float]:
    """What each calling room asks for on top of its line: the demand boost and its heat-up extra."""
    return {r.key: DEMAND_BOOST_K * (r.demand or 0.0) + heat_up.extra_k(mem.heat_up, r) for r in calling}


def _need(inp: BoilerInputs, mem: BoilerMemory, boosts: dict[str, float], cell: str | None) -> tuple[float, str | None]:
    """What is added to the curve, and the room that asks for it.

    Each room that wants heat needs its own line; while it calls, its boosts come on top. The room
    that needs the most sets the flow, the others' valves take less. Without a room asking, the line
    in use (`cell`) holds.
    """
    base = correction(mem, inp.outdoor_c, cell)
    best, set_by = base, load.room_of(cell)
    group = load.class_of(cell) if load.room_of(cell) is not None else None
    for r in load.wanting_rooms(inp.rooms):
        line = base if group is None else correction(mem, inp.outdoor_c, load.room_cell(group, r.key))
        if line + boosts.get(r.key, 0.0) > best + 1e-9:
            best, set_by = line + boosts.get(r.key, 0.0), r.key
    return best, set_by


def _book_operation(inp: BoilerInputs, mem: BoilerMemory, cell: str | None) -> BoilerMemory:
    """Book the time since the last cycle to the lines in use, with whether a called room lags."""
    now = inp.now_ts
    gap = 0.0 if mem.lines_seen_ts is None else now - mem.lines_seen_ts
    if not inp.learning_allowed or not 0.0 < gap <= load.MAX_GAP_S:
        return replace(mem, lines_seen_ts=now if inp.learning_allowed else None)
    released_for = 0.0 if mem.state_since_ts is None else now - mem.state_since_ts
    given_time = released_for >= learning.LAG_MIN_HEAT_S
    calling = load.calling_rooms(inp.rooms)
    behind = {r.key for r in load.lagging_rooms(inp.rooms)} if given_time else set()
    judged = given_time and bool(calling)
    hours = gap / 3600.0
    if load.room_of(cell) is not None:
        # Each room that wants heat books the time to its own line, with whether it got there. A room
        # whose progress says nothing (a wish by hand still far off, its valve shut) books no verdict:
        # it would count as calm and its line as found.
        lines = mem.lines
        for r in load.wanting_rooms(inp.rooms):
            own = load.room_cell(load.class_of(cell), r.key)
            lines = load.observe(lines, own, hours, r.key in behind, mem.offset_k, mem.slope_k, judged and load.counts(r))
    else:
        lines = load.observe(mem.lines, cell, hours, bool(behind), mem.offset_k, mem.slope_k, judged)
    return replace(mem, lines=lines, lines_seen_ts=now)


def _decide(inp: BoilerInputs, mem: BoilerMemory, heating_run: bool, burning: bool = False) -> tuple[BoilerCommand, BoilerMemory]:
    p = inp.params
    now = inp.now_ts
    # only rooms that call count: one hanging below target (stalled), at the end of its comfort
    # period or merely heating along does not ask the boiler for anything
    heating_rooms = load.calling_rooms(inp.rooms)
    able = len(load.able_rooms(inp.rooms))  # rooms whose radiator could take heat now: the total they are measured against
    share = p.boiler_spread_min_room_share
    seen = load.load_class(len(heating_rooms), able, share)
    in_use, candidate, candidate_since = load.settled_class(
        mem.class_in_use, mem.class_candidate, mem.class_candidate_since_ts, seen, now
    )
    mem = replace(mem, class_in_use=in_use, class_candidate=candidate, class_candidate_since_ts=candidate_since)
    cold = cold_weight(inp.outdoor_c)
    # The line that is used and learned: within the load class each room has a line of its own, and
    # of the rooms that call the one that needs the most sets the flow. None: one common correction.
    cell = in_use if p.boiler_load_model else None
    if cell is not None:
        wanting = load.wanting_rooms(inp.rooms)
        if heating_rooms and wanting:
            mem = replace(mem, cell_rooms=tuple(sorted(r.key for r in wanting)))
        cell = load.deciding_cell(mem.lines, cell, mem.cell_rooms, mem.offset_k, mem.slope_k, cold)

    def command(plan: str, send: bool, text: str | None, flow: float | None, disable: bool, reason: str, *, curve=None, blockers=(), waiting=None) -> BoilerCommand:
        # what is shown is the line in use: the class's own, or the common correction
        if flow_cell:
            shown = load.line_of(mem.lines, flow_cell, mem.offset_k, mem.slope_k)
        else:
            shown = load.LoadLine(mem.offset_k, mem.slope_k)
        return BoilerCommand(
            plan=plan,
            send=send,
            payload=text,
            flow_setpoint=flow,
            disable_hc=disable,
            reason=reason,
            curve_flow=curve,
            offset_k=round(correction(mem, inp.outdoor_c, flow_cell), 2),
            offset_level_k=round(shown.level_k, 2),
            offset_slope_k=round(shown.slope_k, 2),
            blockers=tuple(blockers),
            waiting=waiting,
            spread_k=None if mem.spread_ema is None else round(mem.spread_ema, 1),
            learning_phase=learning.shown_phase(taught, mem, heating_run),
            adjustments_last_day=learning.adjustments_last_day(mem, now),
            moved_last_day_k=round(learning.moved_last_day(mem, now), 2),
            last_adjust_reason=mem.last_adjust_reason,
            held_back=learning.held_back(inp, mem, flow_cell, asked),
            calling_rooms=len(heating_rooms),
            rooms_total=able,
            spread_rule=load.spread_rule(len(heating_rooms), able, share) if plan == BOILER_PLAN_HEAT else None,
            load_class=in_use,
            flow_set_by=set_by,
            curve_by_load=load.summary(mem.lines, cold) if p.boiler_load_model else None,
            heat_up_k=(
                {room: round(s.extra_k, 1) for room, s in mem.heat_up.items() if s.extra_k > 0.0} or None
                if p.boiler_heat_up_rate_k_h > 0.0
                else None
            ),
            heating_up=heating_up,
        )

    # what the learning is given: while a room heats up on an extra, the flow is not the curve's
    taught = inp
    # shown only while the boiler heats: the room that sets the flow, and the rooms heating up on an extra
    set_by: str | None = None
    heating_up: tuple[str, ...] = ()
    asked: float | None = None  # the flow the rooms ask for, before the limits
    flow_cell = cell  # the line of the room that sets the flow: the one the learning moves

    def not_ours(mem: BoilerMemory) -> BoilerMemory:
        # a heat-up's rise in this time says nothing about the flow: it is measured afresh
        return replace(mem, heat_up=heat_up.follow(mem.heat_up, inp.rooms, p.boiler_heat_up_rate_k_h, now, False, False))

    if inp.control_mode == CTRL_OFF:
        return command(BOILER_PLAN_DISABLED, False, None, None, False, "control_off"), not_ours(mem)

    mem = learning.follow_heating_run(mem, heating_run, now)
    blockers: list[str] = []
    if not inp.boiler_available:
        blockers.append("boiler_data_unavailable")
    if inp.safety_state == SAFETY_FALLBACK:
        blockers.append("safety_fallback")
    if inp.room_data_pending:
        blockers.append("waiting_for_room_data")
    if blockers:
        # send nothing: the boiler returns to its front knob after ~10 min
        cmd = command(BOILER_PLAN_HANDS_OFF, False, None, None, False, "hands_off", blockers=blockers)
        mem = not_ours(replace(mem, block_since_ts=None, **NO_CLASS))
        if blockers != ["waiting_for_room_data"]:
            return cmd, replace(mem, state=None, state_since_ts=None)
        # Waiting for the rooms right after a start is no outage: the boiler still does what was sent
        # before the restart. The plan in force and its age are kept, or the one cycle of waiting wipes
        # what was stored for exactly this and the boiler goes from block to heat inside the minimum
        # state time (review 2026-10-13; a room sensor came back 38 s after a restart on 2026-09-18).
        return cmd, mem

    frost = bool(inp.frost_rooms)
    need = frost or (inp.op_mode != MODE_OFF and inp.advice_source in (SOURCE_BOILER, SOURCE_BOTH))
    reason = "frost_protection" if frost and inp.advice_source not in (SOURCE_BOILER, SOURCE_BOTH) else ("room_demand" if need else "no_room_needs_boiler")
    if inp.op_mode == MODE_OFF and not frost:
        reason = "summer_mode"

    waiting = None
    # the state held against toggling is the plan itself: heat or block
    wanted_state = BOILER_PLAN_HEAT if need else BOILER_PLAN_BLOCK
    if mem.state is not None and wanted_state != mem.state and not frost:
        if mem.state_since_ts is not None and now - mem.state_since_ts < MIN_STATE_S:
            wanted_state, waiting = mem.state, "min_state_time"
    if wanted_state != mem.state:
        mem = replace(mem, state=wanted_state, state_since_ts=now, last_review_ts=None, samples=0)

    # the curve as it is: the limits apply to the flow that is sent, not to a part of it
    curve = round(curve_flow(inp.outdoor_c, p, limited=False), 1)
    judging = inp.control_mode == CTRL_ACTIVE and wanted_state == BOILER_PLAN_HEAT and not frost
    # The heat-up's own rule: the rooms' rise, judged on any heating burn. The hour after hot water
    # holds back the spread, not this (see HOT_WATER_HOLDOFF_S).
    mem = replace(
        mem, heat_up=heat_up.follow(mem.heat_up, inp.rooms, p.boiler_heat_up_rate_k_h, now, burning, judging, mem.flow_held)
    )
    if wanted_state == BOILER_PLAN_HEAT:
        heating_up = tuple(heat_up.in_force(mem.heat_up, inp.rooms))
        if heating_up:
            # a boiler cycling on the higher flow would teach the curve to go down
            taught = replace(inp, learning_allowed=False)
        boosts = _boosts(mem, heating_rooms)
        # The learning moves the line of the room that sets the flow: a step on another's would not
        # move the flow at all (review 2026-10-14: three steps up left it where it was for two hours).
        _, set_by = _need(inp, mem, boosts, cell)
        if load.room_of(cell) is not None and set_by is not None:
            flow_cell = load.room_cell(load.class_of(cell), set_by)
        mem = _book_operation(taught, mem, cell)
        mem = learning.learn(taught, mem, heating_rooms, heating_run, flow_cell, boosts)
        if load.room_of(cell) is not None:
            cell = load.deciding_cell(mem.lines, load.class_of(cell), mem.cell_rooms, mem.offset_k, mem.slope_k, cold)
        need, set_by = _need(inp, mem, boosts, cell)
        if load.room_of(cell) is not None and set_by is not None:
            flow_cell = load.room_cell(load.class_of(cell), set_by)
        flow = asked = curve + need
        if frost:
            flow = max(flow, 40.0)
        # more would not reach the radiators: at the maximum, or swallowed by the minimum
        held = not p.boiler_flow_min <= curve + need < p.boiler_flow_max
        flow = round_half(min(max(flow, p.boiler_flow_min), p.boiler_flow_max))
        if frost:
            # frost protection goes straight to its floor; a ramp would only delay it
            mem = replace(mem, ramp_from=None, ramp_since_ts=None)
        elif mem.last_flow is not None and flow > mem.last_flow:
            # at most RAMP_UP_K per window, counted from where the rise began: the window
            # must not restart with every cycle, or 5 K per 10 min becomes 5 K per minute
            base = mem.ramp_from if mem.ramp_from is not None else mem.last_flow
            since = mem.ramp_since_ts if mem.ramp_since_ts is not None else now
            limit = base + RAMP_UP_K * (1 + int((now - since) // RAMP_WINDOW_S))
            if flow > limit:
                flow = limit
                waiting = waiting or "ramp_limit"
                held = True
            mem = replace(mem, ramp_from=base, ramp_since_ts=since)
        elif (
            mem.ramp_from is None
            or flow <= mem.ramp_from
            or (mem.ramp_since_ts is not None and now - mem.ramp_since_ts >= RAMP_WINDOW_S)
        ):
            # A dip within the first window keeps the rise where it began. Measured 2026-10-02:
            # the bathroom target was clicked up and down for 25 s, every dip restarted the ramp,
            # and the flow went from 45 to 53 °C in 21 seconds.
            mem = replace(mem, ramp_from=None, ramp_since_ts=None)
        disable = False
    else:
        flow = p.boiler_flow_min
        disable = True
        held = False
        # blocked time is no operation of any class, and none is in use
        mem = replace(mem, lines_seen_ts=None, **NO_CLASS)
        # the next heating run ramps from its own start, not from a rise that ended with this block
        mem = replace(mem, ramp_from=None, ramp_since_ts=None)

    if mem.last_flow is None or flow != mem.last_flow:
        mem = replace(mem, last_flow=flow)
    mem = replace(mem, flow_held=not disable and held)
    text = payload(flow, disable, not p.boiler_hot_water_standby)
    due = text != mem.last_payload or mem.last_send_ts is None or now - mem.last_send_ts >= RESEND_INTERVAL_S
    if due:
        mem = replace(mem, last_send_ts=now, last_payload=text)
    if not disable or inp.control_mode != CTRL_ACTIVE:
        mem = replace(mem, block_since_ts=None)
    elif mem.block_since_ts is None:
        mem = replace(mem, block_since_ts=now)
    return command(wanted_state, due, text, None if disable else flow, disable, reason, curve=curve, waiting=waiting), mem
