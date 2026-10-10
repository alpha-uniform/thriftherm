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
  the boiler stops keeping the circuit hot. Hot water fields are always sent
  with fixed, configured values and are never disabled.
* Flow temperature = heating curve (two points, -10 °C and +15 °C outdoor)
  + learned offset + demand boost, limited to the configured range and ramped
  up by at most 5 °C per 10 minutes.

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
from .boiler_learning import BURNING_GAS_W, PHASE_LEARNING, PHASE_PAUSED, PHASE_REFINING, short_cycling  # noqa: F401
from .boiler_memory import BoilerInputs, BoilerMemory, correction, curve_flow  # noqa: F401
from .common import round_half
from .heat_call import CALL_CALLING

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
    if flow_c > flow_max_c + HOT_WATER_ABOVE_FLOW_MAX_K:
        return True
    if heating_flow_c is not None and flow_c > heating_flow_c + HOT_WATER_ABOVE_SETPOINT_K:
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
    cmd, mem = _decide(inp, mem, burning and not cooling_back)
    share, starts = learning.burner_share(mem, now)
    cmd = replace(cmd, burner_share=share, burner_starts_last_hour=starts)
    recent = mem.hot_water_seen_ts is not None and now - mem.hot_water_seen_ts < HOT_WATER_SHOWN_S
    return replace(cmd, hot_water=recent, hot_water_seen_ts=mem.hot_water_seen_ts), mem


def _decide(inp: BoilerInputs, mem: BoilerMemory, heating_run: bool) -> tuple[BoilerCommand, BoilerMemory]:
    p = inp.params
    now = inp.now_ts

    def command(plan: str, send: bool, text: str | None, flow: float | None, disable: bool, reason: str, *, curve=None, blockers=(), waiting=None) -> BoilerCommand:
        return BoilerCommand(
            plan=plan,
            send=send,
            payload=text,
            flow_setpoint=flow,
            disable_hc=disable,
            reason=reason,
            curve_flow=curve,
            offset_k=round(correction(mem, inp.outdoor_c), 2),
            offset_level_k=round(mem.offset_k, 2),
            offset_slope_k=round(mem.slope_k, 2),
            blockers=tuple(blockers),
            waiting=waiting,
            spread_k=None if mem.spread_ema is None else round(mem.spread_ema, 1),
            learning_phase=learning.shown_phase(inp, mem, heating_run),
            adjustments_last_day=learning.adjustments_last_day(mem, now),
            last_adjust_reason=mem.last_adjust_reason,
            held_back=learning.held_back(inp, mem),
        )

    if inp.control_mode == CTRL_OFF:
        return command(BOILER_PLAN_DISABLED, False, None, None, False, "control_off"), mem

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
        return (
            command(BOILER_PLAN_HANDS_OFF, False, None, None, False, "hands_off", blockers=blockers),
            replace(mem, state=None, state_since_ts=None, block_since_ts=None),
        )

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

    # only rooms that call raise the flow: one hanging below target (stalled) or at the end of its
    # comfort period does not ask the boiler for anything
    heating_rooms = [
        r for r in inp.rooms if r.heating_allowed and r.temperature is not None and r.boiler_call in (None, CALL_CALLING)
    ]
    curve = round(curve_flow(inp.outdoor_c, p), 1)
    if wanted_state == BOILER_PLAN_HEAT:
        mem = learning.learn(inp, mem, heating_rooms, heating_run)
        demand = max(((r.demand or 0.0) for r in heating_rooms), default=0.0)
        flow = curve + correction(mem, inp.outdoor_c) + DEMAND_BOOST_K * demand
        if frost:
            flow = max(flow, 40.0)
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
        # the next heating run ramps from its own start, not from a rise that ended with this block
        mem = replace(mem, ramp_from=None, ramp_since_ts=None)

    if mem.last_flow is None or flow != mem.last_flow:
        mem = replace(mem, last_flow=flow, last_flow_ts=now)
    text = payload(flow, disable, not p.boiler_hot_water_standby)
    due = text != mem.last_payload or mem.last_send_ts is None or now - mem.last_send_ts >= RESEND_INTERVAL_S
    if due:
        mem = replace(mem, last_send_ts=now, last_payload=text)
    if not disable or inp.control_mode != CTRL_ACTIVE:
        mem = replace(mem, block_since_ts=None)
    elif mem.block_since_ts is None:
        mem = replace(mem, block_since_ts=now)
    return command(wanted_state, due, text, None if disable else flow, disable, reason, curve=curve, waiting=waiting), mem
