"""Which rooms call the boiler, and which rooms heat along while it runs.

Pure logic, no Home Assistant import.

Measured 2026-09-24 to 26 (one radiator open, 17 °C outside): the boiler fires
with at least 8 kW, one radiator takes about 1 kW. It burns for a minute, then
waits in its lockout for twelve. 88 starts in 44 hours, and the bathroom spent
2.5 hours climbing the last 0.3 K while the boiler kept doing that. Four rules
answer it:

* Hysteresis: a room starts calling at CALL_ON_K below its target and stops
  within CALL_OFF_K. The old rule called at 0.1 K below target (0.05 demand).
* Stall: a call that has run for an hour, is within STALL_BAND_K of the target
  and barely rises any more counts as reached. It calls again only when the
  room falls STALL_RELEASE_K below target or the target changes.
* End of comfort: no call in the last half hour of a comfort window. The room
  cools by a tenth of a degree in that time; the heat would arrive too late.
  Nor in the moment after its end, while the schedule helper has not switched yet.
* A lower target is a new question: a call that began for a higher target is
  judged again by the start threshold (seen 2026-09-26: an override of 21.5 °C
  was lifted, and the bathroom kept calling 0.2 K below its 21.0 °C).
* The boiler helps a heat pump room (`helping`): the heat pump stays at its least
  speed while a served room lags, and the boiler adds the peak. Such a room calls
  from HELP_ON_K below its target until it is there. Seen in the simulation of
  2026-10-20 to 30: waiting for the call at CALL_ON_K, the rooms missed their
  comfort target by 0.3 K and more for 249 minutes in ten days. Called from any
  shortfall, a room flickering between 20.9 and 21.0 °C switched the boiler every
  five minutes (review 2026-10-30). A helped call is judged stalled only after
  HELP_STALL_S: the heat pump stays at its least speed for it, so nothing else adds
  the peak (replayed 2026-10-30, eight degrees colder: stalled after an hour, the
  bath stood 0.13 K short for 78 minutes). Never judged stalled, a room that hangs
  below its target had the boiler fire for one radiator all comfort long, as in
  2026-09 (review 2026-10-30). Such a stall needs the room within HELP_ON_K, holds
  while the boiler goes on helping and ends at CALL_ON_K below. Replayed ten degrees
  colder, that missed the comfort target as often as no stall at all (367 against
  373 minutes in ten days); ended half a degree below as other stalls, 439.
  For the same reason it calls in the last half hour of comfort too: replayed five
  degrees colder, the bath fell 0.5 K short then, and let through, the comfort
  minutes missed fell from 71 to 53 in ten days at the same cost.
* The thermostat's own word: a room whose thermostat reports it does not heat
  (`hvac_action` idle or off) does not call; heat for a shut valve helps no room
  (issue #3). The call's hysteresis runs on meanwhile, and a thermostat that reports
  heating never calls by itself. One that reports nothing changes nothing.

While the boiler runs anyway, other rooms heat along (`join`): a room in its
comfort window that is below comfort gets comfort + JOIN_K, and a room whose
preheat would start within PULL_FORWARD_S starts it now. A room in its setback that
is below its setback target heats along up to it, never past it (decided 16.11.: the
kitchen at 18.4 of 18.5 lacked the heat anyway; heated along it needs no start of its
own later): its target stays, the coordinator opens a valve it drives directly until
the target, in part where the valve has an opening limit (valve_plan.join_opening). More open radiators
take more of the burner's minimum output, so each run lasts longer and fewer
starts are needed. Joined rooms never call by themselves, so they cannot keep
the boiler running. A room whose thermostat holds its valve shut does not join.

The lead (`lead_k`, docs/PHASE13_VALVES.md §6): a room thermostat with a control of its own (Better Thermostat,
Versatile Thermostat, a valve) throttles its valve shortly before its setpoint. The boiler heats a room from
CALL_ON_K to CALL_OFF_K below its target, and in that band the valve should be open. While the boiler runs for a
room, its thermostat is asked for the target plus the lead; a room heating along gets the larger of that and its
joined target, never both. The lead ends with the call, so the thermostat shuts its valve at the target as before.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..models import EndedCall, RoomResult

CALL_ON_K = 0.3
CALL_OFF_K = 0.1
# a helped call starts where a heat pump room begins to lag (heat_pump_control.LAG_BELOW_K), two sensor steps
# below its target, and ends at it
HELP_ON_K = 0.2
STALL_S = 3600.0
HELP_STALL_S = 7200.0
STALL_BAND_K = 0.3
STALL_RATE_K_H = 0.15
STALL_RELEASE_K = 0.5
# A call that begins further below its target than this is a heat-up, not the holding of a temperature: the
# hysteresis starts a call at CALL_ON_K, a sensor in steps of 0.2 K a step later.
HOLD_START_K = 0.5
COMFORT_END_GUARD_S = 1800.0
SHORT_PREHEAT_NEW_CALL_S = 300.0  # a release holds this long (MIN_STATE_S): begun later it outlasts the block
# seen 2026-10-21 22:00 and 2026-10-22 07:30: one schedule helper switched 15 ms before another, and the
# cycle it set off still saw the other room's block running with its end just passed. That room called,
# the boiler lit once for nothing, and the target fell to setback seconds later. An end this recent still
# counts as ending; a new block brings an end of its own, so this never holds back a comfort that began.
COMFORT_END_PASSED_S = 120.0
JOIN_K = 0.5  # Better Thermostat gets half-degree steps
PULL_FORWARD_S = 3600.0

CALL_IDLE = "idle"
CALL_CALLING = "calling"
CALL_STALLED = "stalled"
CALL_COMFORT_ENDING = "comfort_ending"
CALL_NOT_ALLOWED = "not_allowed"
# Thriftherm would call, but the room thermostat reports it does not heat, its valve shut (issue #3)
CALL_THERMOSTAT_IDLE = "thermostat_idle"

JOINED_COMFORT = "joined_boiler_run"
JOINED_PREHEAT = "joined_preheat"
JOINED_SETBACK = "joined_setback"
JOINED_REASONS = (JOINED_COMFORT, JOINED_PREHEAT, JOINED_SETBACK)

_COMFORT_REASONS = ("schedule_comfort",)
_URGENT_REASONS = ("boost",)  # quick heat-up asks for heat now, no stall or end-of-window rule


@dataclass(frozen=True)
class CallMemory:
    calling: bool = False
    since_ts: float | None = None
    stalled_target: float | None = None  # the target at which a stall was accepted
    target: float | None = None  # the target the running call is heading for
    stalled_helped: bool = False  # the stall was accepted while the boiler helped: its help going on does not end it
    # For the boiler's heating curve (boiler_learning.count_calls): the room's temperature when the running call
    # began, why the call says nothing about the flow (see `EndedCall`), and the last call that ended (not stored).
    # Between calls `target` is the target last seen, so a call that begins with a new one is told apart.
    start_c: float | None = None
    spoiled: str | None = None
    last: EndedCall | None = None
    # the reading last seen in the running call and how often it changed: a call that is one step of the sensor
    # (down to start it, up to end it) says how long the room was below a threshold, not how fast it rose
    seen_c: float | None = None
    readings: int = 0
    # since when the room's target has stood as it is (None: not seen yet, or heating not allowed): the heating
    # curve learns from a room once it has for the trend's 90 minutes, and a restart must not start them over
    target_since_ts: float | None = None

    def to_storage(self) -> dict:
        return {
            "calling": self.calling, "since_ts": self.since_ts, "stalled_target": self.stalled_target, "target": self.target,
            "stalled_helped": self.stalled_helped, "start_c": self.start_c, "spoiled": self.spoiled,
            "target_since_ts": self.target_since_ts, "seen_c": self.seen_c, "readings": self.readings,
        }

    @classmethod
    def from_storage(cls, data: object) -> CallMemory:
        if not isinstance(data, dict):
            return cls()

        def number(key: str) -> float | None:
            value = data.get(key)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        return cls(
            calling=data.get("calling") is True,
            since_ts=number("since_ts"),
            stalled_target=number("stalled_target"),
            target=number("target"),
            stalled_helped=data.get("stalled_helped") is True,
            start_c=number("start_c"),
            target_since_ts=number("target_since_ts"),
            seen_c=number("seen_c"),
            readings=int(number("readings") or 0),
            spoiled=data.get("spoiled") if isinstance(data.get("spoiled"), str) else None,
        )


def _ended(mem: CallMemory, room: RoomResult, now_ts: float, why: str | None = None) -> EndedCall | None:
    """The running call as one that ends now (`why`: other than by reaching its target); else the last one before."""
    if not mem.calling or mem.since_ts is None or mem.start_c is None or room.temperature is None:
        return mem.last
    one_step = mem.readings + (room.temperature != mem.seen_c) < 2
    spoiled = why or mem.spoiled or ("one_step" if one_step else None)
    return EndedCall(mem.since_ts, now_ts, mem.start_c, room.temperature, spoiled)


def update(
    room: RoomResult, mem: CallMemory, now_ts: float, comfort_end_ts: float | None = None, helping: bool = False,
    call_on_k: float = CALL_ON_K, call_off_k: float = CALL_OFF_K,
) -> tuple[str, CallMemory]:
    """The room's call state after this cycle; `helping`: the boiler adds the peak for the heat pump in this room.

    `call_on_k` and `call_off_k` are the band of the call (a setting since 2026-11-15): it begins `call_on_k` below the
    target and ends once the room is less than `call_off_k` below it (negative: above it)."""
    if not room.heating_allowed:
        # no target is kept: the call after an airing is a recovery, not the holding of a temperature
        return CALL_NOT_ALLOWED, CallMemory(last=_ended(mem, room, now_ts, "not_allowed"))
    if room.temperature is None:
        # A cycle without a reading tells nothing about the call, so the memory waits for the next one:
        # after a restart the sensors report one by one, and a stall or a running call must outlast that.
        # Nor does the room call: without a temperature it has no demand either (room.demand), so the
        # fallback on it that stood here never called (review 2026-11-08).
        return CALL_IDLE, mem

    stood = mem.target_since_ts if mem.target == room.target and mem.target_since_ts is not None else now_ts
    mem = replace(mem, target_since_ts=stood)
    deficit = round(room.target - room.temperature, 2)  # sensors report tenths: 21.0 - 20.9 must be 0.1
    urgent = any(room.target_reason.startswith(r) or room.target_reason.endswith("+" + r) for r in _URGENT_REASONS)

    # a stall from before the help ends with it; one judged while helped holds
    release_k = CALL_ON_K if mem.stalled_helped else STALL_RELEASE_K
    released = mem.stalled_target != room.target or deficit >= release_k or urgent or (helping and not mem.stalled_helped)
    if mem.stalled_target is not None and released:
        mem = replace(mem, stalled_target=None, stalled_helped=False)
    if mem.stalled_target is not None:
        return CALL_STALLED, replace(mem, calling=False, since_ts=None, target=room.target)

    if not urgent and room.target_reason in _COMFORT_REASONS and comfort_end_ts is not None:
        left = comfort_end_ts - now_ts
        # a helped room calls in the last half hour as well, but not past the end
        # A limited preheat (issue #6) has its heat to come in the block itself, however short that is. Only a call
        # that begins too late to light the burner for the room is left out.
        guard = COMFORT_END_GUARD_S
        if helping:
            guard = 0.0
        elif room.short_preheat:
            guard = 0.0 if mem.calling else SHORT_PREHEAT_NEW_CALL_S
        if -COMFORT_END_PASSED_S <= left < guard:
            ended = _ended(mem, room, now_ts, "comfort_end")
            return CALL_COMFORT_ENDING, CallMemory(target=room.target, last=ended, target_since_ts=stood)

    if mem.calling and mem.target is not None and room.target < mem.target:
        # the target went down: start over as if the call were new
        mem = CallMemory(target=mem.target, last=_ended(mem, room, now_ts, "target"), target_since_ts=stood)
    on, off = (HELP_ON_K, 0.0) if helping else (call_on_k, call_off_k)
    calling = deficit > off if mem.calling else deficit >= on
    if not calling:
        return CALL_IDLE, CallMemory(target=room.target, last=_ended(mem, room, now_ts), target_since_ts=stood)
    since = mem.since_ts if mem.calling and mem.since_ts is not None else now_ts
    if (
        not urgent
        and now_ts - since >= (HELP_STALL_S if helping else STALL_S)
        and deficit <= (HELP_ON_K if helping else STALL_BAND_K)
        # an unknown trend is no evidence of a stall: after a restart the call is old, the trend not yet
        and room.trend_k_per_h is not None
        and room.trend_k_per_h < STALL_RATE_K_H
    ):
        ended = _ended(mem, room, now_ts, "stalled")
        return CALL_STALLED, CallMemory(
            stalled_target=room.target, stalled_helped=helping, target=room.target, last=ended, target_since_ts=stood
        )
    # what the call's pace will say about the flow: nothing, once anything but the radiator moved the room
    moved = None if mem.target == room.target else "target"
    if mem.calling:
        start_c, spoiled, readings = mem.start_c, mem.spoiled or moved, mem.readings + (room.temperature != mem.seen_c)
    else:
        # a call within the band's start (+ a sensor step) holds a temperature: the threshold follows the band (review
        # 2026-11-15 §3.8; with a call beginning 0.5 below, every call counted as a heat-up)
        hold_start = max(HOLD_START_K, round(call_on_k + 0.2, 2))
        start_c, spoiled, readings = room.temperature, moved or ("heat_up" if deficit > hold_start else None), 0
    if urgent:
        spoiled = spoiled or "boost"
    elif helping:
        spoiled = spoiled or "helped"
    elif room.thermostat_heating is False:
        spoiled = spoiled or "valve_shut"
    memory = CallMemory(
        calling=True, since_ts=since, target=room.target, start_c=start_c, spoiled=spoiled, last=mem.last,
        target_since_ts=stood, seen_c=room.temperature, readings=readings,
    )
    if room.thermostat_heating is False:
        return CALL_THERMOSTAT_IDLE, memory
    return CALL_CALLING, memory


def join(
    rooms: dict[str, RoomResult],
    states: dict[str, str],
    comfort: dict[str, float],
    now_ts: float,
) -> dict[str, RoomResult]:
    """Raise the targets of rooms that can take heat while the boiler runs anyway.

    `states` are this cycle's call states, `comfort` the comfort temperature per room.
    """
    out = dict(rooms)
    for key, r in rooms.items():
        # A room whose thermostat holds its valve shut stays as it is (review 2026-10-30): raised by half a degree, the
        # thermostat opened the valve, and the next cycle the room called the boiler itself, the heat issue #3 avoids.
        skip = states.get(key) in (CALL_CALLING, CALL_THERMOSTAT_IDLE)
        if skip or not r.heating_allowed or r.temperature is None or key not in comfort:
            continue
        level = comfort[key]
        if r.target_reason in _COMFORT_REASONS and r.target >= level and r.temperature < r.target:
            target = r.target + JOIN_K
            out[key] = replace(r, target=target, target_reason=JOINED_COMFORT, deviation_k=round(r.temperature - target, 2))
        elif (
            r.target_reason == "schedule_setback"
            and not r.short_preheat  # its limit says how early it begins, with or without a run to join
            and r.preheat_start_ts is not None
            and r.preheat_start_ts - now_ts <= PULL_FORWARD_S
            and r.temperature < level
        ):
            out[key] = replace(r, target=level, target_reason=JOINED_PREHEAT, deviation_k=round(r.temperature - level, 2))
        elif r.target_reason == "schedule_setback" and r.temperature < r.target:
            out[key] = replace(r, target_reason=JOINED_SETBACK)  # up to its target, never past it
    return out


def heats_along_to(sent: RoomResult, own: RoomResult) -> float | None:
    """The temperature a room heating along (`join`) heats to, None for one that does not.

    Its own target, as the join ends there (comfort, setback); the comfort it pulls forward for a preheat begun early
    (16.11.: measured to its setback, a room 0.2 above it had its valve at 30 % for a preheat to 20.5)."""
    if sent.target_reason == JOINED_PREHEAT:
        return sent.target
    if sent.target_reason in JOINED_REASONS:
        return own.target
    return None


def lead_k(  # noqa: PLR0913 - every condition of the rule
    state: str,
    sent: RoomResult,
    own: RoomResult,
    option_k: float,
    boiler_heats: bool,
    left_to_unit: bool,
    leading: bool = False,
    held_k: float | None = None,
) -> float:
    """How far above `sent.target` the room thermostat is asked for while the boiler runs for the room (see the top).

    `state` is the room's call state, `sent` the room as handed to its thermostat (joined or not), `own` the room as
    it is by itself, `option_k` the room's lead, `boiler_heats` whether the boiler may heat now and `left_to_unit`
    whether the heat pump serves the room instead. A thermostat that reports its valve shut gets none (issue #3, the
    rule of `join`): a higher setpoint would open it and have the room call by itself. Unless it has the lead already
    (`leading`) and the call runs: then its own control shut for a moment, and taking the lead away and giving it back
    sent a setpoint every minute (review 2026-11-13).

    `held_k`: the boiler waits for its data (after a restart, an ebusd gap), its plan says nothing. The lead the
    thermostat has is held while the room still calls or has no reading yet, not dropped and sent again.
    """
    if option_k <= 0.0 or left_to_unit or not own.heating_allowed:
        return 0.0
    if not boiler_heats:
        if held_k is None or held_k <= 0.0:
            return 0.0
        calls = state in (CALL_CALLING, CALL_THERMOSTAT_IDLE) or own.temperature is None
        return min(held_k, option_k) if calls else 0.0
    if sent.target_reason == JOINED_SETBACK:
        return 0.0  # up to its own target: a thermostat with a control of its own opens in part by itself (decided 16.11.)
    if sent.target_reason in (JOINED_COMFORT, JOINED_PREHEAT):
        # A stalled room or one at the end of its comfort heats along too (`join`), but it is as warm as the boiler gets
        # it or about to cool down: JOIN_K, nothing more (review 2026-11-13).
        if state != CALL_IDLE:
            return 0.0
        return max(0.0, round(own.target + option_k - sent.target, 2))
    if state == CALL_CALLING or (state == CALL_THERMOSTAT_IDLE and leading):
        return option_k
    return 0.0
