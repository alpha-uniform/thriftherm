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
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..const import CTRL_ACTIVE, CTRL_OFF, ROOM_PLAN_DISABLED, ROOM_PLAN_HANDS_OFF, ROOM_PLAN_HOLD, ROOM_PLAN_SET
from .common import as_float, round_half
from ..models import RoomCommand, RoomResult

TOLERANCE_K = 0.25
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

    def to_storage(self) -> dict:
        # Kept over a restart so the rate limit still holds and a setpoint that
        # changed meanwhile is still recognised as a manual change.
        return {
            "last_sent_target": self.last_sent_target,
            "last_sent_ts": self.last_sent_ts,
            "confirmed": self.confirmed,
            "thermostat_before_send": self.thermostat_before_send,
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
        )


def thermostat_setpoint(target: float) -> float:
    """Better Thermostat works in 0.5 °C steps between 5 and 30 °C."""
    return min(max(round_half(target), SETPOINT_MIN), SETPOINT_MAX)


def manual_change(control_mode: str, thermostat_target: float | None, mem: RoomCtrlMemory) -> float | None:
    """A confirmed setpoint that changed on the thermostat itself: someone turned it by hand.

    Checked before the room targets are computed, so the resulting override is
    applied in the same cycle. Unconfirmed values (just sent, echo pending) never count.
    """
    if control_mode != CTRL_ACTIVE or thermostat_target is None or mem.last_sent_target is None:
        return None
    if abs(thermostat_target - mem.last_sent_target) <= TOLERANCE_K:
        return None
    if mem.confirmed:
        return thermostat_target
    # Not yet echoed: the old value or a late echo is not a user change, but a value
    # that is neither what we sent nor what the thermostat showed before must be one.
    before = mem.thermostat_before_send
    if before is not None and abs(thermostat_target - before) > TOLERANCE_K:
        return thermostat_target
    return None


def decide(
    room: str,
    control_mode: str,
    result: RoomResult,
    has_thermostat: bool,
    hvac_mode: str | None,
    thermostat_target: float | None,
    now_ts: float,
    mem: RoomCtrlMemory,
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

    if active and thermostat_target is not None and mem.last_sent_target is not None and not mem.confirmed:
        if abs(thermostat_target - mem.last_sent_target) <= TOLERANCE_K:
            mem = replace(mem, confirmed=True)

    if thermostat_target is not None and abs(thermostat_target - desired) <= TOLERANCE_K:
        if active and mem.last_sent_target != desired:
            mem = replace(mem, last_sent_target=desired, confirmed=True)
        return cmd(ROOM_PLAN_HOLD, "in_sync", desired), mem

    if active and mem.last_sent_target == desired and mem.last_sent_ts is not None:
        wait = RETRY_UNCONFIRMED_S if not mem.confirmed else MIN_RESEND_S
        if now_ts - mem.last_sent_ts < wait:
            return cmd(ROOM_PLAN_HOLD, "waiting_for_thermostat", desired), mem
    if active and mem.last_sent_ts is not None and now_ts - mem.last_sent_ts < MIN_RESEND_S:
        return cmd(ROOM_PLAN_HOLD, "rate_limit", desired), mem

    if active:
        mem = RoomCtrlMemory(last_sent_target=desired, last_sent_ts=now_ts, confirmed=False, thermostat_before_send=thermostat_target)
    return cmd(ROOM_PLAN_SET, result.target_reason, desired, send=True), mem
