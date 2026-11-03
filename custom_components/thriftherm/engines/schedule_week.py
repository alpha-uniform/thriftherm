"""The week of a Home Assistant schedule helper: which block runs when, and where its target changes.

The adapter reads the blocks (adapters/inputs.py), the room engine evaluates them for a later time,
as for the return from away. Nothing here imports Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from ..models import Week


def _end_of(day: datetime, end: time) -> datetime:
    """A block's end on the day of `day`; time.max is midnight, the start of the next day."""
    if end == time.max:
        return datetime.combine(day.date() + timedelta(days=1), time(), tzinfo=day.tzinfo)
    return datetime.combine(day.date(), end, tzinfo=day.tzinfo)


def block_starting(week: Week, at: datetime) -> tuple[datetime, float | None] | None:
    """The block of a schedule helper that starts at `at` (local time), or a minute after: its end and its temperature.

    A minute between two blocks is no gap: "to 23:59" is how a block to the end of the day is often
    written, and the next day's block starts at 00:00 (test of 2026-10-15: two days of 00:00-23:59
    ended the comfort at 23:59, and the room did not call the boiler from 23:29 to midnight).
    """
    for begin in (at, at.replace(second=0, microsecond=0) + timedelta(minutes=1)):
        for start, end, temperature in week[begin.weekday()]:
            if start == begin.time():
                return _end_of(begin, end), temperature
    return None


def comfort_run_end(week: Week, block_end: datetime, level: float, comfort_temp: float) -> datetime | None:
    """Where the comfort of a schedule helper ends, from the end of its running block; None: not within a week.

    The helper's next event is the end of the running block only. A block that starts at the same
    instant carries the comfort on (06:00-12:00 at 20 °C, 12:00-22:00 at 21 °C: taken as the comfort
    end, 12:00 kept the room from calling the boiler from 11:30 to 12:00 every day), unless it asks
    for less than the block before it (`level`). A block that names no temperature asks for `comfort_temp`.
    """
    end = block_end
    while end - block_end < timedelta(days=7):
        following = block_starting(week, end)
        if following is None or following[0] <= end:
            return end
        asked = comfort_temp if following[1] is None else following[1]
        if asked < level - 0.01:
            return end
        end, level = following[0], asked
    return None


def next_level_change(
    week: Week, block_end: datetime, level: float, comfort_temp: float, setback_temp: float
) -> tuple[datetime, float] | None:
    """Where the target of a schedule helper changes next, from the end of its running block, and to what.

    A block that starts at that instant at the same temperature carries it on; one at another
    temperature is the change (to its temperature, `comfort_temp` when it names none), no block the
    change to `setback_temp`. None: not within a week.
    """
    at = block_end
    while at - block_end < timedelta(days=7):
        following = block_starting(week, at)
        if following is None:
            return at, setback_temp
        asked = comfort_temp if following[1] is None else following[1]
        if abs(asked - level) > 0.01 or following[0] <= at:
            return at, asked
        at = following[0]
    return None


def block_running(week: Week, at: datetime) -> tuple[datetime, float | None] | None:
    """The block of a schedule helper running at `at` (local time): its end and its temperature; None between blocks.

    In the minute after a block "to 23:59" the next day's block at 00:00 runs already (block_starting).
    """
    clock = at.time()
    for start, end, temperature in week[at.weekday()]:
        if start <= clock and (end == time.max or clock < end):
            return _end_of(at, end), temperature
    if any(end == clock.replace(second=0, microsecond=0) for _, end, _ in week[at.weekday()]):
        return block_starting(week, at)
    return None


def level_at(
    week: Week, at: datetime, comfort_temp: float, setback_temp: float
) -> tuple[float, tuple[datetime, float] | None]:
    """The target a schedule helper gives at `at`, and where it changes next from there and to what.

    Outside of a block that is the setback, and the change is not looked for (None). Inside one it is the
    block's temperature (`comfort_temp` when it names none), and the change is next_level_change from the
    block's end: None when there is none within a week.
    """
    running = block_running(week, at)
    if running is None:
        return setback_temp, None
    end, temperature = running
    level = comfort_temp if temperature is None else temperature
    return level, next_level_change(week, end, level, comfort_temp, setback_temp)


def next_block_start(week: Week, at: datetime, comfort_temp: float) -> tuple[datetime, float] | None:
    """The first block of a schedule helper that starts after `at` (local time), and the temperature it asks for
    (`comfort_temp` when it names none); None: none within a week."""
    for ahead in range(8):
        day = at.date() + timedelta(days=ahead)
        for start, _, temperature in sorted(week[day.weekday()], key=lambda block: block[0]):
            begin = datetime.combine(day, start, tzinfo=at.tzinfo)
            if begin > at:
                return begin, comfort_temp if temperature is None else temperature
    return None
