"""How Thriftherm drives a radiator valve without a room thermostat in between (docs/PHASE13_VALVES.md §3, §5.1).

Pure logic, no Home Assistant import. Written for Thriftherm; the idea of correcting a valve by the difference between
its own reading and the room's is common to Better Thermostat and Versatile Thermostat, no code of either is in here.

Three ways, the most exact one the valve offers first (valve_caps.ValveCaps.ways):
* external: the room temperature into the valve's input, the valve controls on it (`external_due`, `external_fresh`,
  `takes_external`).
* offset: the valve's calibration offset is the room less its own reading (`offset_for`, `own_reading`).
* setpoint: the valve's setpoint is the room's target plus its own reading less the room's (`corrected_setpoint`), for
  every valve that measures.

Both corrections learn the same damped difference (`learn`): a valve on the radiator reads warm while it heats and the
difference moves with it, so a single pair says little. A correction changes by a whole step of the valve at most every
CHANGE_EVERY_S, a new target goes out at once. Upwards the setpoint correction never reaches past the target once the
room is there (CUT_*): a room sensor reading cold cannot have the valve heat on. Below it the correction is rounded up to
the valve's next step (UP_DOWN_STEPS): the valve shuts no earlier than the room's sensor says.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .valve_caps import EXTERNAL_REFRESH_S

DAMPING_S = 1800.0
CHANGE_EVERY_S = 600.0
CORRECTION_MIN_K = -2.0
CORRECTION_MAX_K = 3.0
# the upward correction is cut once the room reaches its target, and given back only this far below it: cut and given
# back at the target, a sensor between 20.9 and 21.0 changed the setpoint every minute (review 2026-11-13)
CUT_BACK_BELOW_K = 0.2
# Below its target a room's valve on way c gets its correction rounded up to the next step (2026-11-16: rounded to the
# nearest, a TRVZB in 0.5 K steps stood at +0 with the valve reading 0.45 K warm and shut 13 minutes with the room 0.45
# below). It comes down only this many steps below the one in force: a difference wobbling around a step changes it once.
UP_DOWN_STEPS = 1.5
EXTERNAL_STEP_K = 0.1
EXTERNAL_MIN_GAP_S = 60.0
# A room reading older than this does not go to a valve, unless its device is alive: the 6 hours a room temperature holds
# for the call would have the valve control on a dead value. Two hours, as the TRVZB holds a value itself (Phase B,
# 2026-11-14): with half an hour a bathroom sensor whose values stood 48 minutes (Home Assistant does not write an
# unchanged value anew) put the valve on its own sensor and back again and again (2026-11-15).
EXTERNAL_MAX_AGE_S = 7200.0
TAKES_WITHIN_K = 0.3


@dataclass(frozen=True)
class Correction:
    """The damped difference between a valve's own reading and the room's, and what of it is in force."""

    ema_k: float | None = None  # valve less room, damped
    ema_ts: float | None = None
    applied_k: float | None = None  # the difference the valve has been given, in its steps
    applied_ts: float | None = None
    cut: bool = False  # the room is at its target: no correction upwards

    def to_storage(self) -> dict:
        return {"ema_k": self.ema_k, "ema_ts": self.ema_ts, "applied_k": self.applied_k, "applied_ts": self.applied_ts,
                "cut": self.cut}

    @classmethod
    def from_storage(cls, data: object) -> Correction:
        if not isinstance(data, dict):
            return cls()

        def number(key: str) -> float | None:
            value = data.get(key)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            return float(value) if ok else None

        ema = number("ema_k")
        return cls(
            ema_k=ema if ema is None or abs(ema) <= 15.0 else None,
            ema_ts=number("ema_ts"),
            applied_k=number("applied_k"),
            applied_ts=number("applied_ts"),
            cut=data.get("cut") is True,
        )


def learn(
    c: Correction, valve_c: float | None, room_c: float | None, now_ts: float, last_room_c: float | None = None
) -> Correction:
    """The difference with this pair learned. A missing room reading is taken from the last valid one (`last_room_c`)."""
    room = room_c if room_c is not None else last_room_c
    if valve_c is None or room is None:
        return c
    diff = valve_c - room
    if c.ema_k is None or c.ema_ts is None:
        return replace(c, ema_k=round(diff, 3), ema_ts=now_ts)
    weight = 1.0 - math.exp(-max(now_ts - c.ema_ts, 0.0) / DAMPING_S)
    return replace(c, ema_k=round(c.ema_k + weight * (diff - c.ema_k), 3), ema_ts=now_ts)


def _in_steps(value: float, step: float) -> float:
    step = step if step > 0.0 else 0.5
    return round(round(value / step) * step, 2)


def _up_in_steps(value: float, step: float) -> float:
    step = step if step > 0.0 else 0.5
    return round(math.ceil(value / step - 1e-6) * step, 2)


def _applied(c: Correction, now_ts: float, step: float, low: float, high: float, up: bool = False) -> Correction:
    """The difference in force: the damped one once it moved a step away, at most every CHANGE_EVERY_S.

    `up`: rounded up to the valve's next step (a room below its target, way c), up as soon as the damped difference
    passes the step in force, down only once it lies UP_DOWN_STEPS below it.
    """
    if c.ema_k is None:
        return replace(c, applied_k=0.0) if c.applied_k is None else c
    waited = c.applied_ts is None or now_ts - c.applied_ts >= CHANGE_EVERY_S
    if up and c.ema_k > 0.0:
        wanted = min(max(_up_in_steps(c.ema_k, step), low), high)
        if c.applied_k is None:
            return replace(c, applied_k=wanted, applied_ts=now_ts)
        rises = wanted > c.applied_k + 1e-9
        falls = wanted < c.applied_k - 1e-9 and c.ema_k <= c.applied_k - UP_DOWN_STEPS * step + 1e-6
        return replace(c, applied_k=wanted, applied_ts=now_ts) if (rises or falls) and waited else c
    wanted = min(max(_in_steps(c.ema_k, step), low), high)
    if c.applied_k is None:
        return replace(c, applied_k=wanted, applied_ts=now_ts)
    moved = abs(c.ema_k - c.applied_k) >= step - 1e-6 and wanted != c.applied_k
    return replace(c, applied_k=wanted, applied_ts=now_ts) if moved and waited else c


def corrected_setpoint(
    c: Correction, target: float, room_c: float | None, now_ts: float, step: float = 0.5
) -> tuple[float, Correction]:
    """Way "setpoint": the valve's setpoint for the room's `target`, and the correction kept."""
    if room_c is None:
        return target, replace(c, applied_k=0.0, applied_ts=now_ts if c.applied_k else c.applied_ts)
    cut = True if room_c >= target else False if room_c <= target - CUT_BACK_BELOW_K else c.cut
    # below the target the valve's view is rounded up: rounded to the nearest step it shut while the room was short
    c = replace(_applied(c, now_ts, step, CORRECTION_MIN_K, CORRECTION_MAX_K, up=not cut), cut=cut)
    applied = c.applied_k or 0.0
    if c.cut:
        applied = min(applied, 0.0)
    return round(target + applied, 2), c


def offset_for(
    c: Correction, now_ts: float, step: float, low: float, high: float, inverted: bool = False
) -> tuple[float, Correction]:
    """Way "offset": the valve's calibration offset (room less its own reading), within its range and in its steps."""
    c = _applied(c, now_ts, step, -high if not inverted else low, -low if not inverted else high)
    offset = -(c.applied_k or 0.0)
    if inverted:
        offset = -offset
    return round(min(max(offset, low), high), 2), c


def own_reading(shown_c: float, offset_set: float) -> float:
    """What the valve measures itself: it shows its reading plus the offset it has."""
    return round(shown_c - offset_set, 2)


def external_due(last_c: float | None, last_ts: float | None, room_c: float, now_ts: float) -> float | None:
    """Way "external": the value to write now, None when nothing is due (a change of 0.1 K, a minute apart, a refresh)."""
    value = round(room_c, 1)
    if last_c is None or last_ts is None or now_ts - last_ts >= EXTERNAL_REFRESH_S:
        return value
    if abs(value - last_c) >= EXTERNAL_STEP_K - 1e-6 and now_ts - last_ts >= EXTERNAL_MIN_GAP_S:
        return value
    return None


def external_fresh(age_s: float | None, alive: bool) -> bool:
    """Whether the room reading may go to a valve: recent, or from a sensor reporting on change whose device is alive."""
    if age_s is None:
        return False
    return age_s <= EXTERNAL_MAX_AGE_S or alive


def takes_external(written_c: float, written_ts: float, shown_c: float, shown_ts: float) -> bool | None:
    """Whether a valve showing `shown_c` took the value written: None for a reading from before it was written."""
    if shown_ts < written_ts:
        return None
    return abs(shown_c - written_c) <= TAKES_WITHIN_K


def valve_target(target: float, window_open: bool, frost_c: float, min_setpoint: float | None = None) -> float:
    """What the valve is to hold: the room's target, the frost limit while a window is open (§5.1)."""
    if not window_open:
        return target
    return max(frost_c, min_setpoint) if min_setpoint is not None else frost_c
