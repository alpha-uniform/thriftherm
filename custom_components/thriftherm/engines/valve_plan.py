"""What Thriftherm writes to a valve it drives directly, besides the room's setpoint (docs/PHASE13_VALVES.md §3, §10).

Pure logic, no Home Assistant import. The setpoint of a room's first valve goes the way of a room thermostat's
(engines/room_control.py: hand, reappearance, lead); this plans the rest for each valve:
* way a, the room temperature into the valve's input, with the sensor selection on it (valve_control.external_due);
* way b, the calibration offset, for a valve without an input (valve_control.offset_for);
* the setpoint of a room's further valves, the same as the first one's.

What is set at the valve itself is the truth (§5.2): its child lock, its own window detection, its schedule and its mode
are never written. The sensor selection is, unless a person chose the valve's own sensor. Written for Thriftherm; no code
of Better Thermostat (AGPL-3.0) is in here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from . import valve_control, valve_tpi
from .valve_caps import WAY_EXTERNAL, WAY_OFFSET, WAY_SETPOINT, ValveCaps, ValveQuirks
from .valve_control import Correction

# A valve showing an external value of about 0 lost it (a new start, 2026-11-13): written again at once. Not any other
# difference: a valve rounding to a step of its own was written every minute (review 2026-11-15)
LOST_BELOW_C = 0.05
SETPOINT_RESEND_S = 300.0  # a further valve not showing the room's setpoint gets the same one again after this
# What the TRVZB shows once it went back to its own sensor by itself (Phase B 2026-11-14): no choice of a person, it
# takes the room again 2 s after the next value
FELL_BACK = "remote_source_offline"

WAY_OWN_SENSOR = "own_sensor"  # the room reading is stale: the valve goes by its own sensor and the room's target
WAY_SETPOINT_ONLY = "setpoint"  # the valve takes a setpoint only (the correction of way c is not wired yet)
WAY_UNAVAILABLE = "unavailable"
WAY_OPENING = "opening"  # way d: Thriftherm's own controller sets the opening (engines/valve_tpi.py, a preview)
WAY_OFF = "off"  # switched off at the valve: the mode is the person's (§5.2)
WAY_WINDOW_OFF = "window_off"  # switched off by Thriftherm while the window is open (§5.1)


@dataclass(frozen=True)
class ValveView:
    """A valve as the adapter reads it."""

    caps: ValveCaps
    quirks: ValveQuirks = ValveQuirks()
    available: bool = True
    selection: str | None = None  # the state of its sensor selection (a select), or "on"/"off" of its sensor switch
    selection_by_person: bool = False  # a person set the selection last
    external_shown: float | None = None
    offset_shown: float | None = None
    offset_limits: tuple[float, float, float] = (-5.0, 5.0, 0.5)  # low, high, step of its offset
    shown_c: float | None = None  # the temperature its climate entity shows
    target_shown: float | None = None
    mode: str | None = None  # its hvac mode
    setpoint_step: float = 0.5  # the steps its setpoint goes in
    opening_shown: float | None = None  # the opening its opening entity has (way d)
    hvac_action: str | None = None  # what its climate entity says it does
    setpoint_limits: tuple[float, float] = (5.0, 30.0)  # the lowest and highest setpoint it takes
    hysteresis_k: float = 0.0  # it opens only this far below its setpoint (TRVZB temperature_accuracy), as a positive K
    force: str | None = None  # a way forced for a test (review 2026-11-15 item 6): "offset" or "setpoint"; None: the best


@dataclass(frozen=True)
class ValveMemory:
    external_c: float | None = None  # the room temperature last written to it
    external_ts: float | None = None
    correction: Correction = Correction()  # the damped difference of its own reading to the room's (way b)
    selection_before: str | None = None  # its sensor selection and its offset when Thriftherm first saw it (§7)
    offset_before: float | None = None
    setpoint_ts: float | None = None  # when a further valve was last sent the room's setpoint
    setpoint_c: float | None = None  # and which
    # a person chose the valve's own sensor; kept, as the person's context is gone after a restart (review 2026-11-15)
    selection_by_hand: bool = False
    opening_pct: float | None = None  # the opening last written (way d)
    opening_ts: float | None = None
    opening_before: float | None = None  # what it had when Thriftherm first set it (§7)
    # the opening we had set before the last write: shown still, that write did not arrive, it is no person's value
    opening_prev: float | None = None
    opened_ts: float | None = None  # when it was last seen open, or moved (§5.6)
    exercise_step: int = 0  # 1 to 4 while it is moved: open, shut, open, shut
    exercise_ts: float | None = None
    off_by_window: bool = False  # Thriftherm switched it off for an open window: switched on again once it is shut
    offset_forced: bool = False  # it got an offset while a test forced way b: back on way a it gets its offset of before

    def to_storage(self) -> dict:
        return {"external_c": self.external_c, "external_ts": self.external_ts,
                "correction": self.correction.to_storage(), "selection_before": self.selection_before,
                "offset_before": self.offset_before, "setpoint_ts": self.setpoint_ts, "setpoint_c": self.setpoint_c,
                "selection_by_hand": self.selection_by_hand, "opening_pct": self.opening_pct,
                "opening_ts": self.opening_ts, "opening_before": self.opening_before, "opening_prev": self.opening_prev,
                "opened_ts": self.opened_ts,
                "exercise_step": self.exercise_step, "exercise_ts": self.exercise_ts,
                "off_by_window": self.off_by_window, "offset_forced": self.offset_forced}

    @classmethod
    def from_storage(cls, data: object) -> ValveMemory:
        if not isinstance(data, dict):
            return cls()

        def number(key: str) -> float | None:
            value = data.get(key)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            return float(value) if ok else None

        selection = data.get("selection_before")
        return cls(
            external_c=number("external_c"),
            external_ts=number("external_ts"),
            correction=Correction.from_storage(data.get("correction")),
            selection_before=selection if isinstance(selection, str) else None,
            offset_before=number("offset_before"),
            setpoint_ts=number("setpoint_ts"),
            setpoint_c=number("setpoint_c"),
            selection_by_hand=data.get("selection_by_hand") is True,
            opening_pct=number("opening_pct"),
            opening_ts=number("opening_ts"),
            opening_before=number("opening_before"),
            opening_prev=number("opening_prev"),
            opened_ts=number("opened_ts"),
            exercise_step=step if isinstance(step := data.get("exercise_step"), int) and 0 <= step <= 4 else 0,
            exercise_ts=number("exercise_ts"),
            off_by_window=data.get("off_by_window") is True,
            offset_forced=data.get("offset_forced") is True,
        )


@dataclass(frozen=True)
class Write:
    entity_id: str
    domain: str
    service: str
    value: float | str
    # the memory fields this write stands for: sent, they are kept even where a later write of the same plan was not
    # (review 2026-11-18: the opening sent, the setpoint held back, and the whole memory went back to before)
    memo: tuple[str, ...] = ()

    def data(self) -> dict:
        if self.service == "set_hvac_mode":
            return {"entity_id": self.entity_id, "hvac_mode": self.value}
        if self.domain == "climate":
            return {"entity_id": self.entity_id, "temperature": self.value}
        if self.domain == "select":
            return {"entity_id": self.entity_id, "option": self.value}
        if self.domain == "switch":
            return {"entity_id": self.entity_id}
        return {"entity_id": self.entity_id, "value": self.value}


@dataclass(frozen=True)
class ValvePlan:
    valve: str
    way: str
    reason: str
    writes: tuple[Write, ...] = ()


def _select(caps: ValveCaps, view: ValveView, remote: bool) -> Write | None:
    """The write that puts the valve on the room's temperature (`remote`) or on its own sensor, None if it is there."""
    if caps.sensor_select:
        option = caps.remote_option if remote else caps.local_option
        if option is None or view.selection == option or (remote and view.selection == FELL_BACK):
            return None
        return Write(caps.sensor_select, "select", "select_option", option)
    if caps.sensor_switch:
        if view.selection is None or view.selection == ("on" if remote else "off"):  # nothing shown: nothing known
            return None
        return Write(caps.sensor_switch, "switch", "turn_on" if remote else "turn_off", "on" if remote else "off")
    return None


def _offset(
    view: ValveView, mem: ValveMemory, room_c: float | None, fresh: bool, now_ts: float
) -> tuple[Write | None, ValveMemory]:
    caps, sign = view.caps, -1.0 if view.quirks.offset_inverted else 1.0
    if caps.offset is None or view.offset_shown is None or not fresh or room_c is None or view.shown_c is None:
        return None, mem  # left as it stands (§3.1: it errs by what the difference drifts)
    own = valve_control.own_reading(view.shown_c, sign * view.offset_shown)
    c = valve_control.learn(mem.correction, own, room_c, now_ts)
    low, high, step = view.offset_limits
    offset, c = valve_control.offset_for(c, now_ts, step, low, high, inverted=view.quirks.offset_inverted)
    mem = replace(mem, correction=c)
    if abs(offset - view.offset_shown) < max(step, 0.1) / 2.0:
        return None, mem
    return Write(caps.offset, "number", "set_value", offset), mem


def hand_back(
    view: ValveView, mem: ValveMemory, target: float | None = None, ours_c: float | None = None
) -> tuple[Write, ...]:
    """What a valve driven directly gets back when Thriftherm goes (concept §7): its own sensor (way a), the offset it had
    before (way b), fully open where Thriftherm set its opening (way d). Thriftherm's own states go as well (review
    2026-11-15, second round): switched on again where only the window had it off, and the room's `target` where it
    holds a setpoint of Thriftherm's own (`ours_c`, the last one sent: a margin, a correction, the frost limit) or the end
    of an exercise. A setpoint or an off of a person stays, the child lock is never touched. An unavailable valve gets
    nothing: it falls back by itself (TRVZB after 2 hours)."""
    if not view.available:
        return ()
    caps, writes = view.caps, []
    if mem.off_by_window and view.mode == "off":
        writes.append(Write(caps.climate, "climate", "set_hvac_mode", "heat"))
    shown = view.target_shown
    ours = mem.exercise_step or (ours_c is not None and shown is not None and abs(shown - ours_c) < 0.05)
    if target is not None and shown is not None and abs(shown - target) >= 0.05 and ours:
        writes.append(Write(caps.climate, "climate", "set_temperature", target))
    if (sensor := _select(caps, view, remote=False)) is not None:
        writes.append(sensor)
    if caps.offset and mem.offset_before is not None and view.offset_shown is not None and (
        abs(view.offset_shown - mem.offset_before) >= 0.05
    ):
        writes.append(Write(caps.offset, "number", "set_value", mem.offset_before))
    if caps.opening and mem.opening_ts is not None and view.opening_shown is not None and (
        abs(view.opening_shown - _opening_back(mem)) >= 0.5
    ):
        writes.append(Write(caps.opening, "number", "set_value", _opening_back(mem)))
    return tuple(writes)


def _opening_back(mem: ValveMemory) -> float:
    """What an opening Thriftherm set goes back to: what the valve had before, the person's, or fully open."""
    return mem.opening_before if mem.opening_before is not None else 100.0


# Heating along (heat_call.join), a valve with an opening limit (TRVZB, TRVZBT: valve_opening_degree, shown as
# heating_valve_position) opens in part, by how little the room lacks of its own target (decided 16.11., "anteilig wie
# bei BT"): it takes the heat over the whole burner run instead of reaching its target at once and shutting. Coarse on
# purpose: two stages below fully open. A room sensor in tenths lacks 0.1 (30 %) or 0.2 to 0.3 (60 %); more than that
# and the valve is open as the person set it, as for a room that calls. The 30 % lies well above the TRVZB's 3 % below
# which it loses its calibration; whether 30 and 60 take the heat as hoped is measured (docs/PHASE13_VALVES.md).
JOIN_OPENINGS = ((0.15, 30.0), (0.35, 60.0))  # (lacking less than this many K, opening in %)
JOIN_DOWN_K = 0.1  # a smaller stage only this far past its bound ...
JOIN_HOLD_S = 600.0  # ... and this long after the last change; a larger one at once
OPENING_ANSWER_S = 300.0  # a valve showing another opening this long after ours: a person set it
OPENING_MEMO = ("opening_pct", "opening_ts", "opening_before", "opening_prev")


def join_opening(deficit_k: float, held_pct: float | None, held_ts: float | None, now_ts: float) -> float | None:
    """The opening limit for a room heating along `deficit_k` below its own target, None for open as the person set it.

    `held_pct` is the limit in force since `held_ts` (None: none yet). A larger stage comes at once; a smaller one once the
    room lacks JOIN_DOWN_K less than its bound, and not within JOIN_HOLD_S of the last change."""
    wanted = next((pct for bound, pct in JOIN_OPENINGS if deficit_k < bound), 100.0)
    if held_pct is None or wanted >= held_pct:
        return None if wanted >= 100.0 else wanted
    bound = next(b for b, pct in JOIN_OPENINGS if pct == wanted)
    if deficit_k > bound - JOIN_DOWN_K + 1e-6 or (held_ts is not None and now_ts - held_ts < JOIN_HOLD_S):
        return held_pct
    return wanted


def _opening_limit(
    view: ValveView, mem: ValveMemory, deficit_k: float | None, now_ts: float
) -> tuple[list[Write], ValveMemory]:
    """The opening limit of a room heating along (`join_opening`), or the opening given back once it no longer does.

    The value the valve had is the person's and the most it gets (a person's 60 % stays the top); it is remembered with
    the valve (persisted) and given back when the room calls, stops heating along, its window opens, the valve is
    exercised or Thriftherm goes (`hand_back`)."""
    caps = view.caps
    if not caps.opening or not view.quirks.opening_is_limit:
        return [], mem  # a position, not a limit (review 2026-11-18): the valve's own control keeps it
    shown = view.opening_shown
    ours = mem.opening_ts is not None
    again = False  # our last write did not arrive: it goes out once more
    if ours and shown is not None and mem.opening_pct is not None and abs(shown - mem.opening_pct) >= 0.5 and (
        now_ts - mem.opening_ts >= OPENING_ANSWER_S
    ):
        if mem.opening_prev is not None and abs(shown - mem.opening_prev) < 0.5:
            # still what we had set before (review 2026-11-18): the write was lost, no person's value
            again = True
        else:
            # not what we set, long after: a person's, the new top
            mem = replace(mem, opening_before=shown, opening_pct=shown, opening_prev=None)
    limit = None if deficit_k is None or shown is None else join_opening(
        deficit_k, mem.opening_pct if ours else None, mem.opening_ts if ours else None, now_ts
    )
    top = mem.opening_before if ours and mem.opening_before is not None else shown
    if limit is not None and top is not None:
        value = max(min(limit, top), view.quirks.min_opening_pct)
        if value < top - 0.5:
            if ours and mem.opening_pct == value:
                if not again:
                    return [], mem
                return [Write(caps.opening, "number", "set_value", value, OPENING_MEMO)], replace(mem, opening_ts=now_ts)
            prev = mem.opening_pct if ours else shown
            mem = replace(mem, opening_before=top, opening_pct=value, opening_ts=now_ts, opening_prev=prev)
            return [Write(caps.opening, "number", "set_value", value, OPENING_MEMO)], mem
    if not ours:
        return [], mem
    back = _opening_back(mem)
    if shown is not None and abs(shown - back) < 0.5:
        # it shows what it had: forgotten only now (review 2026-11-18: a give-back lost left it at 30 % for good)
        return [], replace(mem, opening_pct=None, opening_ts=None, opening_before=None, opening_prev=None)
    if mem.opening_pct == back and not again:
        return [], mem  # given back, waiting for its answer
    prev = mem.opening_prev if mem.opening_pct == back else mem.opening_pct
    write = Write(caps.opening, "number", "set_value", back, OPENING_MEMO)
    return [write], replace(mem, opening_pct=back, opening_ts=now_ts, opening_prev=prev)


UNANSWERED_FIRST_S = 300.0  # a value a valve did not take goes out again at once, then after this ...
UNANSWERED_MAX_S = 3600.0  # ... doubling up to this


def unanswered_wait_s(times: int) -> float:
    """How long the same write waits after it went out `times` times without the valve showing it."""
    if times <= 1:
        return 0.0
    return min(UNANSWERED_FIRST_S * 2 ** (times - 2), UNANSWERED_MAX_S)


END_MARGIN_K = 0.2  # a direct valve stays open this far past where the room's call ends


def direct_on_top(wants_heat: bool, hysteresis_k: float, end_above_k: float, step: float = 0.0) -> float:
    """What a valve driven directly gets above the room's target (review 2026-11-15 §3.4, one rule for three numbers).

    While the room wants heat (it calls, or heats along) the valve's setpoint stands safely above where it shuts: its own
    threshold, and where the call ends (`end_above_k` above the target, negative below) + END_MARGIN_K, the larger.
    Otherwise the target. An end above the target with a smaller threshold shut the valve before the call ended, and the
    boiler heated against it (§3.8). The lead of a room thermostat is no number of its own here.
    """
    if not wants_heat:
        return 0.0
    on_top = max(hysteresis_k, end_above_k + END_MARGIN_K, 0.0)
    if step > 0.0:
        on_top = math.ceil(on_top / step - 1e-6) * step  # rounded down, the margin would be lost in the valve's step
    return round(on_top, 2)


def on_top_while_calling(lead_k: float, hysteresis_k: float) -> float:
    """What a calling room's valve gets above the target: the larger of the lead and its own threshold, not the sum.

    A valve opens `hysteresis_k` below its setpoint: at target + max it opens at the target or above, so at every call
    (the call begins 0.3 K below), and the lead alone does that once it is at least the threshold. The sum (decided
    2026-11-15: "doppelt gemoppelt") let a room heating along overshoot by nearly 1 K.
    """
    return max(lead_k, hysteresis_k)


EXERCISE_STEPS = ("open", "shut", "open", "shut")
# each step held at least this long: past the minute a setpoint is held before the next (room_control.MIN_RESEND_S), or a
# step could go by unsent (review 2026-11-15)
EXERCISE_STEP_S = 90.0
OPENED_NOTED_S = 3600.0  # an open valve is noted anew at most this often: the store is not written every cycle


def exercise(
    view: ValveView, mem: ValveMemory, now_ts: float, may_start: bool = True
) -> tuple[str | None, ValveMemory]:
    """The valve exercise (§5.6): a valve that did not open for `quirks.exercise_s` is opened and shut twice.

    Returns the step to hold now ("open", "shut") or None. Each step lasts at least EXERCISE_STEP_S (a cycle). One that
    is due begins only while `may_start` (its motor is heard: in the day); one begun runs to its end.
    """
    if not view.available or view.mode == "off":
        return None, mem
    if mem.exercise_step:
        if mem.exercise_ts is not None and now_ts - mem.exercise_ts < EXERCISE_STEP_S:
            return EXERCISE_STEPS[mem.exercise_step - 1], mem
        if mem.exercise_step >= len(EXERCISE_STEPS):
            return None, replace(mem, exercise_step=0, exercise_ts=None)
        step = mem.exercise_step + 1
        mem = replace(mem, exercise_step=step, exercise_ts=now_ts)
        if step == len(EXERCISE_STEPS):
            mem = replace(mem, opened_ts=now_ts)
        return EXERCISE_STEPS[step - 1], mem
    # the opening entity of a TRVZB is its limit (100 % from the factory): open only where Thriftherm set it (way d)
    ours_open = mem.opening_ts is not None and (mem.opening_pct or 0.0) > view.quirks.min_opening_pct
    opened = view.hvac_action == "heating" or ours_open or (
        view.quirks.unreliable_hvac_action and view.target_shown is not None and view.shown_c is not None
        and view.target_shown >= view.shown_c + 0.5
    )
    if mem.opened_ts is None or (opened and now_ts - mem.opened_ts >= OPENED_NOTED_S):
        return None, replace(mem, opened_ts=now_ts)
    if opened:
        return None, mem
    if now_ts - mem.opened_ts < view.quirks.exercise_s or not may_start:
        return None, mem
    return EXERCISE_STEPS[0], replace(mem, exercise_step=1, exercise_ts=now_ts)


def _own_by_hand(view: ValveView, mem: ValveMemory) -> bool:
    own = view.selection is not None and view.selection in (view.caps.local_option, "off")
    return own and (view.selection_by_person or mem.selection_by_hand)


def _ways(view: ValveView) -> tuple[str, ...]:
    """The valve's ways (ValveCaps.ways), less the better ones while a test forces a worse one it has (item 6)."""
    ways = view.caps.ways()
    if view.force == WAY_OFFSET and WAY_OFFSET in ways:
        return tuple(w for w in ways if w != WAY_EXTERNAL)
    if view.force == WAY_SETPOINT and WAY_SETPOINT in ways:
        return (WAY_SETPOINT,)
    return ways


def _way_c(view: ValveView, mem: ValveMemory) -> bool:
    """The valve takes a setpoint only: neither the room's temperature (or a person chose its own sensor) nor an offset."""
    ways = _ways(view)
    if not view.caps.measures or WAY_OFFSET in ways:
        return False
    return WAY_EXTERNAL not in ways or _own_by_hand(view, mem)


def setpoint_correction(
    view: ValveView, mem: ValveMemory, room_c: float | None, room_fresh: bool, target: float, now_ts: float
) -> tuple[float, ValveMemory]:
    """Way c (§3.1): how far the valve's setpoint lies off the room's `target`, its own reading less the room's, damped.

    The fallback for every valve that shows a current temperature and takes neither the room's temperature nor an
    offset. 0 without a fresh room reading: the valve goes by its own sensor and the room's target.
    """
    if not view.available or not _way_c(view, mem) or not room_fresh or room_c is None or view.shown_c is None:
        return 0.0, mem
    c = valve_control.learn(mem.correction, view.shown_c, room_c, now_ts)
    setpoint, c = valve_control.corrected_setpoint(c, target, room_c, now_ts, view.setpoint_step)
    return round(setpoint - target, 2), replace(mem, correction=c)


def plan(
    view: ValveView,
    mem: ValveMemory,
    room_c: float | None,
    room_fresh: bool,
    now_ts: float,
    setpoint: float | None = None,
    target: float | None = None,
    opening: float | None = None,
    exercise: str | None = None,
    setpoint_limits: tuple[float, float] = (5.0, 30.0),
    window_off: bool = False,
    room_sensor: bool = True,
    window_open: bool = False,
    join_deficit_k: float | None = None,
) -> tuple[ValvePlan, ValveMemory]:
    """What to write to one valve now and what to remember of it.

    `room_c` is the room's temperature and `room_fresh` whether it may go to a valve (valve_control.external_fresh).
    `setpoint` is the room's setpoint for a further valve of the room, None for the first one, whose setpoint is sent as a
    room thermostat's; `target` is the room's own target, from which a further valve on way c gets its correction.
    `opening` is the opening Thriftherm's own controller asks for in a room set to it (way d), None otherwise. Only the
    opening is written, never the position the valve takes once its own sensor reaches the target: that is the net.
    `exercise` is a step of the valve exercise ("open", "shut"): a further valve gets the end of `setpoint_limits`, one on
    the own controller the full opening or none. `window_off`: the room's window is open past its delay and the valves
    are to be off (§5.1, the default since 2026-11-15); switched on again once it is shut. An off of a person stays.
    `join_deficit_k`: the room heats along (heat_call.join) this far below its own target, None otherwise: a valve with
    an opening limit opens in part (`_opening_limit`), on every other way only.
    """
    caps = view.caps
    if not view.available:
        # A person's choice outlasts a radio gap or a restart of Home Assistant (final review #8): only a new start of
        # the valve, which shows its input at 0, takes it (below).
        return ValvePlan(caps.climate, WAY_UNAVAILABLE, "valve_unavailable"), mem
    if mem.selection_by_hand and view.external_shown is not None and abs(view.external_shown) < LOST_BELOW_C:
        # back from a new start (a change of batteries) it shows its own sensor without anyone having chosen it
        mem = replace(mem, selection_by_hand=False)
    if window_off:
        # an opening in part goes back as well: open as it was once the window is shut
        back, mem = _opening_limit(view, mem, None, now_ts) if opening is None else ([], mem)
        if view.mode == "off":
            return ValvePlan(caps.climate, WAY_WINDOW_OFF, "window_open_off", tuple(back)), mem
        off = Write(caps.climate, "climate", "set_hvac_mode", "off")
        return ValvePlan(caps.climate, WAY_WINDOW_OFF, "window_open_off", (off, *back)), replace(mem, off_by_window=True)
    back_on: list[Write] = []
    if mem.off_by_window:
        if view.mode == "off":
            back_on.append(Write(caps.climate, "climate", "set_hvac_mode", "heat"))
            view = replace(view, mode="heat")
        mem = replace(mem, off_by_window=False)
    if view.mode == "off":
        return ValvePlan(caps.climate, WAY_OFF, "valve_off"), mem
    if mem.selection_before is None and view.selection is not None and view.selection != FELL_BACK:
        mem = replace(mem, selection_before=view.selection)
    if mem.offset_before is None and view.offset_shown is not None:
        mem = replace(mem, offset_before=view.offset_shown)

    out: list[Write] = back_on
    ways = _ways(view)
    if WAY_EXTERNAL in caps.ways() and WAY_EXTERNAL not in ways:
        # forced off the room's temperature for a test: on its own sensor, or it held the last value it got
        on_room = view.selection is not None and view.selection in (caps.remote_option, "on", FELL_BACK)
        if on_room and (switch := _select(caps, view, remote=False)):
            out.append(switch)
    own = view.selection is not None and view.selection in (caps.local_option, "off")
    own_by_hand = _own_by_hand(view, mem)
    if own_by_hand != mem.selection_by_hand and (own_by_hand or not own):
        mem = replace(mem, selection_by_hand=own_by_hand)
    if WAY_EXTERNAL in ways and not own_by_hand:
        if mem.offset_forced and caps.offset and mem.offset_before is not None and view.offset_shown is not None:
            # back from a forced offset (item 6): the offset it had, once; one a person sets afterwards is theirs
            if abs(view.offset_shown - mem.offset_before) >= 0.05:
                out.append(Write(caps.offset, "number", "set_value", mem.offset_before))
            mem = replace(mem, offset_forced=False, correction=replace(mem.correction, applied_k=None, applied_ts=None))
        if room_fresh and room_c is not None:
            way, reason = WAY_EXTERNAL, "room_temperature_to_valve"
            value = valve_control.external_due(mem.external_c, mem.external_ts, room_c, now_ts)
            lost = (
                mem.external_c is not None and abs(mem.external_c) >= LOST_BELOW_C
                and view.external_shown is not None and abs(view.external_shown) < LOST_BELOW_C
            )
            # back on its own sensor by itself (FELL_BACK): it takes the room again on the next value (Phase B)
            if value is None and (lost or view.selection == FELL_BACK):
                value = round(room_c, 1)
            if value is not None:
                out.append(Write(caps.external_temperature, "number", "set_value", value, ("external_c", "external_ts")))
                mem = replace(mem, external_c=value, external_ts=now_ts)
            if (switch := _select(caps, view, remote=True)) is not None:
                out.append(switch)
        else:
            # no dead value for the valve to hold (§3.3): it goes by its own sensor until the room reports again
            way, reason = WAY_OWN_SENSOR, "room_reading_stale" if room_sensor else "no_room_sensor"
            on_room = view.selection is not None and view.selection in (caps.remote_option, "on")
            if on_room and (switch := _select(caps, view, remote=False)):
                out.append(switch)
    elif WAY_OFFSET in ways:
        way, reason = WAY_OFFSET, "own_sensor_by_hand" if own_by_hand else "offset_to_room"
        write, mem = _offset(view, mem, room_c, room_fresh, now_ts)
        if write is not None:
            out.append(write)
            if view.force == WAY_OFFSET and WAY_EXTERNAL in caps.ways():
                mem = replace(mem, offset_forced=True)
    else:
        way, reason = WAY_SETPOINT_ONLY, "own_sensor_by_hand" if own_by_hand else "setpoint_only"

    # not learned while the window is open: cold air at the room sensor (as for the first valve; final review #7)
    if exercise is None and setpoint is not None and target is not None and _way_c(view, mem) and not window_open:
        correction, mem = setpoint_correction(view, mem, room_c, room_fresh, target, now_ts)
        setpoint = round(setpoint + correction, 2)
    own_controller = opening is not None  # way d: its opening is the controller's, not a limit of heating along
    if exercise is not None and opening is not None and caps.opening:
        end = 100.0 if exercise == "open" else max(0.0, view.quirks.min_opening_pct)
        value = None if end == mem.opening_pct else end
        if value is not None:
            out.append(Write(caps.opening, "number", "set_value", value))
            mem = replace(mem, opening_pct=value, opening_ts=now_ts)
        opening = None  # the exercise in place of the controller
        way, reason = WAY_OPENING, "valve_exercise"
    if exercise is not None and setpoint is not None:
        setpoint = setpoint_limits[1] if exercise == "open" else setpoint_limits[0]
    if not own_controller:
        # Heating along, in part; else back to what it had. Also no more on way d (a room stored with it, saved as
        # Automatic): open as before, its own control is the net (review 2026-11-15, second round #3: left at 3 % the
        # room hardly heated). An exercise gets it open as it was, as does a room that calls.
        deficit = join_deficit_k if exercise is None and not window_open else None
        limit_writes, mem = _opening_limit(view, mem, deficit, now_ts)
        out.extend(limit_writes)
    if opening is not None and caps.opening:
        value = valve_tpi.due(opening, mem.opening_pct, mem.opening_ts, now_ts, view.quirks.min_opening_pct)
        if mem.opening_before is None and view.opening_shown is not None:
            mem = replace(mem, opening_before=view.opening_shown)
        way, reason = WAY_OPENING, "own_controller"
        if value is not None:
            out.append(Write(caps.opening, "number", "set_value", value))
            mem = replace(mem, opening_pct=value, opening_ts=now_ts)
    if setpoint is not None:
        # within its range and in its steps (final review #5): refused out of range, the plan waited; in other steps the
        # value never shown was sent anew every few minutes
        low, high = view.setpoint_limits
        setpoint = min(max(setpoint, low), high)
        if view.setpoint_step > 0:
            setpoint = round(round(setpoint / view.setpoint_step) * view.setpoint_step, 2)
    differs = setpoint is not None and (view.target_shown is None or abs(view.target_shown - setpoint) > 0.05)
    # a new setpoint at once (review 2026-11-15: a window open two minutes after a change), the same one again later
    new = setpoint != mem.setpoint_c
    if differs and (new or mem.setpoint_ts is None or now_ts - mem.setpoint_ts >= SETPOINT_RESEND_S):
        out.append(Write(caps.climate, "climate", "set_temperature", setpoint))
        mem = replace(mem, setpoint_ts=now_ts, setpoint_c=setpoint)
    return ValvePlan(caps.climate, way, reason, tuple(out)), mem
