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
preheat would start within PULL_FORWARD_S starts it now. More open radiators
take more of the burner's minimum output, so each run lasts longer and fewer
starts are needed. Joined rooms never call by themselves, so they cannot keep
the boiler running. A room whose thermostat holds its valve shut does not join.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..models import RoomResult

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
COMFORT_END_GUARD_S = 1800.0
# seen 2026-10-21 22:00 and 2026-10-22 07:30: one schedule helper switched 15 ms before another, and the
# cycle it set off still saw the other room's block running with its end just passed. That room called,
# the boiler lit once for nothing, and the target fell to setback seconds later. An end this recent still
# counts as ending; a new block brings an end of its own, so this never holds back a comfort that began.
COMFORT_END_PASSED_S = 120.0
JOIN_K = 0.5  # Better Thermostat gets half-degree steps
PULL_FORWARD_S = 3600.0
DEMAND_FALLBACK = 0.05  # without a temperature: the room's demand decides, as before

CALL_IDLE = "idle"
CALL_CALLING = "calling"
CALL_STALLED = "stalled"
CALL_COMFORT_ENDING = "comfort_ending"
CALL_NOT_ALLOWED = "not_allowed"
# Thriftherm would call, but the room thermostat reports it does not heat, its valve shut (issue #3)
CALL_THERMOSTAT_IDLE = "thermostat_idle"

JOINED_COMFORT = "joined_boiler_run"
JOINED_PREHEAT = "joined_preheat"

_COMFORT_REASONS = ("schedule_comfort",)
_URGENT_REASONS = ("boost",)  # quick heat-up asks for heat now, no stall or end-of-window rule


@dataclass(frozen=True)
class CallMemory:
    calling: bool = False
    since_ts: float | None = None
    stalled_target: float | None = None  # the target at which a stall was accepted
    target: float | None = None  # the target the running call is heading for
    stalled_helped: bool = False  # the stall was accepted while the boiler helped: its help going on does not end it

    def to_storage(self) -> dict:
        return {
            "calling": self.calling, "since_ts": self.since_ts, "stalled_target": self.stalled_target, "target": self.target,
            "stalled_helped": self.stalled_helped,
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
        )


def update(
    room: RoomResult, mem: CallMemory, now_ts: float, comfort_end_ts: float | None = None, helping: bool = False
) -> tuple[str, CallMemory]:
    """The room's call state after this cycle; `helping`: the boiler adds the peak for the heat pump in this room."""
    if not room.heating_allowed:
        return CALL_NOT_ALLOWED, CallMemory()
    if room.temperature is None:
        # A cycle without a reading tells nothing about the call, so the memory waits for the next one:
        # after a restart the sensors report one by one, and a stall or a running call must outlast that.
        calling = (room.demand or 0.0) > DEMAND_FALLBACK
        return (CALL_CALLING if calling else CALL_IDLE), mem

    deficit = round(room.target - room.temperature, 2)  # sensors report tenths: 21.0 - 20.9 must be 0.1
    urgent = any(room.target_reason.startswith(r) or room.target_reason.endswith("+" + r) for r in _URGENT_REASONS)

    # a stall from before the help ends with it; one judged while helped holds
    release_k = CALL_ON_K if mem.stalled_helped else STALL_RELEASE_K
    released = mem.stalled_target != room.target or deficit >= release_k or urgent or (helping and not mem.stalled_helped)
    if mem.stalled_target is not None and released:
        mem = replace(mem, stalled_target=None, stalled_helped=False)
    if mem.stalled_target is not None:
        return CALL_STALLED, replace(mem, calling=False, since_ts=None)

    if not urgent and room.target_reason in _COMFORT_REASONS and comfort_end_ts is not None:
        left = comfort_end_ts - now_ts
        # a helped room calls in the last half hour as well, but not past the end
        if -COMFORT_END_PASSED_S <= left < (0.0 if helping else COMFORT_END_GUARD_S):
            return CALL_COMFORT_ENDING, CallMemory()

    if mem.calling and mem.target is not None and room.target < mem.target:
        mem = CallMemory()  # the target went down: start over as if the call were new
    on, off = (HELP_ON_K, 0.0) if helping else (CALL_ON_K, CALL_OFF_K)
    calling = deficit > off if mem.calling else deficit >= on
    if not calling:
        return CALL_IDLE, CallMemory()
    since = mem.since_ts if mem.calling and mem.since_ts is not None else now_ts
    if (
        not urgent
        and now_ts - since >= (HELP_STALL_S if helping else STALL_S)
        and deficit <= (HELP_ON_K if helping else STALL_BAND_K)
        # an unknown trend is no evidence of a stall: after a restart the call is old, the trend not yet
        and room.trend_k_per_h is not None
        and room.trend_k_per_h < STALL_RATE_K_H
    ):
        return CALL_STALLED, CallMemory(stalled_target=room.target, stalled_helped=helping)
    memory = CallMemory(calling=True, since_ts=since, target=room.target)
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
            and r.preheat_start_ts is not None
            and r.preheat_start_ts - now_ts <= PULL_FORWARD_S
            and r.temperature < level
        ):
            out[key] = replace(r, target=level, target_reason=JOINED_PREHEAT, deviation_k=round(r.temperature - level, 2))
    return out
