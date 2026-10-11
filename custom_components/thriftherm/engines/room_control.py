"""Room control: hand the room targets to Better Thermostat.

Pure logic, no Home Assistant import. Better Thermostat keeps doing the local
work (external room sensor, window contacts, valve calibration); this module
only decides which setpoint each room thermostat should have.

Principles
* Only the target temperature is written. The thermostat's mode stays with
  the user and Better Thermostat: a thermostat that is "off" (summer) is left
  alone.
* A setpoint is sent when it differs from the thermostat by more than
  rounding, at most once a minute; an unconfirmed setpoint is retried after
  five minutes.
* A manual change on the thermostat (after our value had been confirmed)
  becomes a temporary override in the integration instead of being fought.
* A target the thermostat applies by itself (Versatile Thermostat with an open
  window, shedding load or in safety mode) is neither a manual change nor
  written against: the thermostat restores the requested target afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..const import CTRL_ACTIVE, CTRL_OFF, ROOM_PLAN_DISABLED, ROOM_PLAN_HANDS_OFF, ROOM_PLAN_HOLD, ROOM_PLAN_SET
from . import hand
from .common import as_float, round_half
from ..models import RoomCommand, RoomResult, SettingChange

TOLERANCE_K = 0.25
ANSWER_WITHIN_K = 1.0  # a thermostat's own rendering of a setpoint lies this close to it (its step, its limit by a step)
MIN_RESEND_S = 60.0
RETRY_UNCONFIRMED_S = 300.0
SETPOINT_MIN = 5.0
SETPOINT_MAX = 30.0


@dataclass(frozen=True)
class RoomCtrlMemory:
    last_sent_target: float | None = None
    last_sent_ts: float | None = None
    confirmed: bool = False  # the thermostat reported our last value back
    thermostat_before_send: float | None = None  # what the thermostat showed when we sent
    # What the thermostat showed in answer to our setpoint when that was something else: its own
    # step or limit (21.5 sent, 22 shown). It counts as our setpoint until the next one is sent.
    answered_with: float | None = None

    def to_storage(self) -> dict:
        # Kept over a restart so the rate limit still holds and a setpoint that
        # changed meanwhile is still recognised as a manual change.
        return {
            "last_sent_target": self.last_sent_target,
            "last_sent_ts": self.last_sent_ts,
            "confirmed": self.confirmed,
            "thermostat_before_send": self.thermostat_before_send,
            "answered_with": self.answered_with,
        }

    @classmethod
    def from_storage(cls, data: dict | None) -> RoomCtrlMemory:
        if not isinstance(data, dict):
            return cls()
        return cls(
            last_sent_target=as_float(data.get("last_sent_target")),
            last_sent_ts=as_float(data.get("last_sent_ts")),
            confirmed=bool(data.get("confirmed")),
            thermostat_before_send=as_float(data.get("thermostat_before_send")),
            answered_with=as_float(data.get("answered_with")),
        )


def thermostat_setpoint(target: float) -> float:
    """Better Thermostat works in 0.5 °C steps between 5 and 30 °C."""
    return min(max(round_half(target), SETPOINT_MIN), SETPOINT_MAX)


# Better Thermostat, calibrating by target temperature, rewrites the valve setpoint every few
# seconds. Over Zigbee a reply can arrive after the next write; Better Thermostat then takes
# the stale value for a turn of the knob and adopts it as its own target (measured
# 2026-09-27 07:01: valve 16 -> 16.5 -> 16 within ten seconds, Better Thermostat 17 -> 16).
ECHO_WINDOW_S = 60.0


def is_echo(value: float, valve_history: tuple[tuple[float, float], ...], changed_ts: float) -> bool:
    """True when `value` is a late radio echo, not a turn of the knob.

    `valve_history` holds (timestamp, setpoint) of the valve behind the thermostat,
    `changed_ts` is when the room thermostat jumped to `value`. An echo is a value the
    valve showed within the minute before, left for another one, and showed again:
    16 -> 16.5 -> 16. A turn of the knob brings a value that was not there before:
    17 -> 16.5 -> 16. Turning the knob to a value and back within a minute looks like
    an echo too and is not taken over; turning it again does.
    """
    sequence: list[float] = []
    for ts, setpoint in valve_history:
        if changed_ts - ECHO_WINDOW_S <= ts <= changed_ts + 1.0 and (not sequence or sequence[-1] != setpoint):
            sequence.append(setpoint)
    return sum(1 for setpoint in sequence if abs(setpoint - value) < 0.05) >= 2


def manual_change(
    control_mode: str, thermostat_target: float | None, mem: RoomCtrlMemory, deliberate: bool = False
) -> float | None:
    """A confirmed setpoint that changed on the thermostat itself: someone turned it by hand.

    Checked before the room targets are computed, so the resulting override is
    applied in the same cycle. Unconfirmed values (just sent, echo pending) never count.

    `deliberate`: Home Assistant says a person or an automation asked for the value after
    our last command (engines/hand.py). That needs no confirmation and no waiting.
    """
    if control_mode != CTRL_ACTIVE or thermostat_target is None or mem.last_sent_target is None:
        return None
    if abs(thermostat_target - mem.last_sent_target) <= TOLERANCE_K:
        return None
    if deliberate:
        return thermostat_target
    if mem.answered_with is not None and abs(thermostat_target - mem.answered_with) <= TOLERANCE_K:
        return None  # still its own rendering of what we sent
    if mem.confirmed:
        return thermostat_target
    # Not yet echoed: the old value or a late echo is not a user change, but a value
    # that is neither what we sent nor what the thermostat showed before must be one.
    before = mem.thermostat_before_send
    if before is not None and abs(thermostat_target - before) > TOLERANCE_K:
        return thermostat_target
    return None


def own_answer(thermostat_target: float | None, mem: RoomCtrlMemory, change: SettingChange | None) -> bool:
    """True when the thermostat answered our own setpoint with its own rendering of it.

    Home Assistant marks the change as ours (engines/hand.py): no hand turned anything. A
    thermostat in whole degrees shows 22 for a 21.5 sent; that is our setpoint as it can hold it.

    Only a value next to what was sent counts. Home Assistant keeps our context on the entity
    for a few seconds, so a knob turned to 24 right after our 22 is marked as ours too, and so
    is a thermostat falling back to what it had: neither is a rendering, and the rules for a
    change at the device and for an unconfirmed setpoint go on applying to them.
    """
    if change is None or change.origin != hand.OURS or not change.setpoint:
        return False
    if thermostat_target is None or mem.last_sent_target is None or mem.last_sent_ts is None:
        return False
    if change.ts < mem.last_sent_ts:  # the answer to an earlier setpoint
        return False
    off_by = abs(thermostat_target - mem.last_sent_target)
    fell_back = mem.thermostat_before_send is not None and abs(thermostat_target - mem.thermostat_before_send) <= TOLERANCE_K
    return TOLERANCE_K < off_by <= ANSWER_WITHIN_K and not fell_back


def decide(
    room: str,
    control_mode: str,
    result: RoomResult,
    has_thermostat: bool,
    hvac_mode: str | None,
    thermostat_target: float | None,
    now_ts: float,
    mem: RoomCtrlMemory,
    own_target: bool = False,
) -> tuple[RoomCommand, RoomCtrlMemory]:
    def cmd(plan: str, reason: str, target: float | None = None, send: bool = False, manual: float | None = None) -> RoomCommand:
        return RoomCommand(room, plan, target, send, reason, thermostat_target, manual)

    if control_mode == CTRL_OFF:
        return cmd(ROOM_PLAN_DISABLED, "control_off"), mem
    if not has_thermostat:
        return cmd(ROOM_PLAN_HANDS_OFF, "no_thermostat"), mem
    if hvac_mode is None:
        return cmd(ROOM_PLAN_HANDS_OFF, "thermostat_unavailable"), mem
    if hvac_mode == "off":
        return cmd(ROOM_PLAN_HANDS_OFF, "thermostat_off"), mem

    desired = thermostat_setpoint(result.target)
    active = control_mode == CTRL_ACTIVE
    if own_target:
        # The thermostat holds a target of its own (Versatile Thermostat: window open, load
        # shedding, safety) and returns to the requested one by itself. Writing against it
        # would be answered with the same target again.
        return cmd(ROOM_PLAN_HOLD, "thermostat_own_rule", desired), mem

    if active and thermostat_target is not None and mem.last_sent_target is not None and not mem.confirmed:
        if abs(thermostat_target - mem.last_sent_target) <= TOLERANCE_K:
            mem = replace(mem, confirmed=True)

    if thermostat_target is not None and abs(thermostat_target - desired) <= TOLERANCE_K:
        if active and mem.last_sent_target != desired:
            mem = replace(mem, last_sent_target=desired, confirmed=True)
        return cmd(ROOM_PLAN_HOLD, "in_sync", desired), mem
    if (
        active
        and thermostat_target is not None
        and mem.answered_with is not None
        and mem.last_sent_target == desired
        and abs(thermostat_target - mem.answered_with) <= TOLERANCE_K
    ):
        return cmd(ROOM_PLAN_HOLD, "in_sync", desired), mem  # the thermostat's own rendering of what we sent

    if active and mem.last_sent_target == desired and mem.last_sent_ts is not None:
        wait = RETRY_UNCONFIRMED_S if not mem.confirmed else MIN_RESEND_S
        if now_ts - mem.last_sent_ts < wait:
            return cmd(ROOM_PLAN_HOLD, "waiting_for_thermostat", desired), mem
    if active and mem.last_sent_ts is not None and now_ts - mem.last_sent_ts < MIN_RESEND_S:
        return cmd(ROOM_PLAN_HOLD, "rate_limit", desired), mem

    if active:
        mem = RoomCtrlMemory(last_sent_target=desired, last_sent_ts=now_ts, confirmed=False, thermostat_before_send=thermostat_target)
    return cmd(ROOM_PLAN_SET, result.target_reason, desired, send=True), mem
