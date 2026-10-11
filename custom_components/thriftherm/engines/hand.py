"""Who changed a setting of a device the control commands.

A control that writes setpoints has to tell its own writes from everybody else's, or it
either fights the people it works for or takes its own echo for a hand. Guessing that from
values alone ("the device shows something else than was sent") cannot tell a turn of the
knob from a command that was lost, a rounded value, or a rule the device applies by itself.

Home Assistant says who it was: every state change carries a context. A service call made
by a person carries that person, one made by an automation or script its trigger, and one
made by the control the context the control gave it. A change that comes from the device
itself (its knob, its remote, its own app, a rule of its own) carries none of them.

    ours        the control's own command, or the device's answer to it
    person      somebody in Home Assistant: a dashboard, an app, a voice assistant
    automation  an automation or a script
    device      at the device itself, or by the device itself

The first is never a change by hand, the next two always are and need no further proof.
Only the last has to be judged: it is a hand unless the device explains the change itself
(a rule of its own, an echo of its valve).

The context has limits, and the rules built on it keep to them. A device that writes its
state within a few seconds of a service call passes that call's context on, whatever the
write was about; one that answers later passes none. So a context says who asked last, not
that the device did as asked. What follows from a change by hand is the same
for every device: it holds for the same time, is shown with who and until when, and ends
by itself or with the same action.

Pure logic, no Home Assistant import.
"""

from __future__ import annotations

from collections.abc import Collection

from ..models import SettingChange

OURS = "ours"
PERSON = "person"
AUTOMATION = "automation"
DEVICE = "device"
ACTION = "action"  # asked of the control itself: its own service or entity

DELIBERATE = (PERSON, AUTOMATION)  # asked for in Home Assistant, by whomever: a change by hand for certain
SHOWN = (PERSON, AUTOMATION, DEVICE, ACTION)  # who a change by hand can be shown as


def origin(
    context_id: str | None, user_id: str | None, parent_id: str | None, own: Collection[str], asked: Collection[str] = ()
) -> str:
    """Where a state change comes from, read from the context Home Assistant gave it.

    `own` are the contexts of the control's own commands. `asked` are those of service calls
    somebody else made on a commanded entity: an automation triggered by the clock carries
    neither a person nor a trigger, and its call is all that tells it from the device itself.
    What a command of ours sets off in turn (an automation reacting to it) is not ours.
    """
    if context_id is not None and context_id in own:
        return OURS
    if user_id:
        return PERSON
    if parent_id or (context_id is not None and context_id in asked):
        return AUTOMATION
    return DEVICE


def deliberate_since(change: SettingChange | None, since_ts: float | None, setpoint_only: bool = False) -> str | None:
    """Who asked for a setting in Home Assistant after `since_ts` (our last command), None if nobody did.

    `setpoint_only`: a change of the mode alone does not count, for where only a setpoint
    can be taken over (a room thermostat switched off by a person asks for no temperature).
    """
    if change is None or change.origin not in DELIBERATE or (setpoint_only and not change.setpoint):
        return None
    return change.origin if since_ts is None or change.ts > since_ts else None
