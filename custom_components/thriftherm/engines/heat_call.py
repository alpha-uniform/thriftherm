"""Which rooms call the boiler, and which rooms heat along while it runs.

Pure logic, no Home Assistant import.

Measured 2026-09-24 to 26 (one radiator open, 17 °C outside): the boiler fires
with at least 8 kW, one radiator takes about 1 kW. It burns for a minute, then
waits in its lockout for twelve. 88 starts in 44 hours, and the bathroom spent
2.5 hours climbing the last 0.3 K while the boiler kept doing that. Three rules
answer it:

* Hysteresis: a room starts calling at CALL_ON_K below its target and stops
  within CALL_OFF_K. The old rule called at 0.1 K below target (0.05 demand).
* Stall: a call that has run for an hour, is within STALL_BAND_K of the target
  and barely rises any more counts as reached. It calls again only when the
  room falls STALL_RELEASE_K below target or the target changes.
* End of comfort: no call in the last half hour of a comfort window. The room
  cools by a tenth of a degree in that time; the heat would arrive too late.
* A lower target is a new question: a call that began for a higher target is
  judged again by the start threshold (seen 2026-09-26: an override of 21.5 °C
  was lifted, and the bathroom kept calling 0.2 K below its 21.0 °C).

While the boiler runs anyway, other rooms heat along (`join`): a room in its
comfort window that is below comfort gets comfort + JOIN_K, and a room whose
preheat would start within PULL_FORWARD_S starts it now. More open radiators
take more of the burner's minimum output, so each run lasts longer and fewer
starts are needed. Joined rooms never call by themselves, so they cannot keep
the boiler running.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..models import RoomResult

CALL_ON_K = 0.3
CALL_OFF_K = 0.1
STALL_S = 3600.0
STALL_BAND_K = 0.3
STALL_RATE_K_H = 0.15
STALL_RELEASE_K = 0.5
COMFORT_END_GUARD_S = 1800.0
JOIN_K = 0.5  # Better Thermostat gets half-degree steps
PULL_FORWARD_S = 3600.0
DEMAND_FALLBACK = 0.05  # without a temperature: the room's demand decides, as before

CALL_IDLE = "idle"
CALL_CALLING = "calling"
CALL_STALLED = "stalled"
CALL_COMFORT_ENDING = "comfort_ending"
CALL_NOT_ALLOWED = "not_allowed"

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

    def to_storage(self) -> dict:
        return {"calling": self.calling, "since_ts": self.since_ts, "stalled_target": self.stalled_target, "target": self.target}

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
        )


def update(room: RoomResult, mem: CallMemory, now_ts: float, comfort_end_ts: float | None = None) -> tuple[str, CallMemory]:
    """The room's call state after this cycle."""
    if not room.heating_allowed:
        return CALL_NOT_ALLOWED, CallMemory()
    if room.temperature is None:
        calling = (room.demand or 0.0) > DEMAND_FALLBACK
        return (CALL_CALLING if calling else CALL_IDLE), CallMemory(calling=calling, since_ts=mem.since_ts if calling else None)

    deficit = round(room.target - room.temperature, 2)  # sensors report tenths: 21.0 - 20.9 must be 0.1
    urgent = any(room.target_reason.startswith(r) or room.target_reason.endswith("+" + r) for r in _URGENT_REASONS)

    if mem.stalled_target is not None and (mem.stalled_target != room.target or deficit >= STALL_RELEASE_K or urgent):
        mem = replace(mem, stalled_target=None)
    if mem.stalled_target is not None:
        return CALL_STALLED, replace(mem, calling=False, since_ts=None)

    if (
        not urgent
        and room.target_reason in _COMFORT_REASONS
        and comfort_end_ts is not None
        and 0.0 <= comfort_end_ts - now_ts < COMFORT_END_GUARD_S
    ):
        return CALL_COMFORT_ENDING, CallMemory()

    if mem.calling and mem.target is not None and room.target < mem.target:
        mem = CallMemory()  # the target went down: start over as if the call were new
    calling = deficit > CALL_OFF_K if mem.calling else deficit >= CALL_ON_K
    if not calling:
        return CALL_IDLE, CallMemory()
    since = mem.since_ts if mem.calling and mem.since_ts is not None else now_ts
    if (
        not urgent
        and now_ts - since >= STALL_S
        and deficit <= STALL_BAND_K
        # an unknown trend is no evidence of a stall: after a restart the call is old, the trend not yet
        and room.trend_k_per_h is not None
        and room.trend_k_per_h < STALL_RATE_K_H
    ):
        return CALL_STALLED, CallMemory(stalled_target=room.target)
    return CALL_CALLING, CallMemory(calling=True, since_ts=since, target=room.target)


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
        if states.get(key) == CALL_CALLING or not r.heating_allowed or r.temperature is None or key not in comfort:
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
