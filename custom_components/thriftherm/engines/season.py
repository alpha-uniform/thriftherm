"""The automatic season (docs/PHASE12_COOLING.md §20.2): heating or cooling by how the rooms behave without either.

Pure logic, no Home Assistant import. A room that warms past its cooling comfort while nothing heats or cools it
speaks for cooling; one that cools a degree below its heating comfort the same way speaks for heating. Not a drift over
the day: over 24 hours the sun and the night nearly cancel out (22 → 24.5 → 22.2 °C is +0.01 K/h). Where it goes is
what counts, measured in seconds per calendar day.

Only rooms with a window contact count: without one an aired room on a summer night reads as a heating day. The
outdoor daily mean (18 °C cooling, 14 °C heating) is the fallback where too few days tell, and below 12 °C it vetoes
any switch to cooling (decided 2026-11-13: a sunny room in March, the PC in the study).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .common import as_dict, as_float

SEASON_HEATING = "heating"
SEASON_COOLING = "cooling"

# what a day must show, set before any measurement (§20.8): the numbers decided on 2026-11-13
FREE_MIN_S = 6 * 3600.0  # a day counts only where a room with a contact ran free this long
SIDE_MIN_S = 2 * 3600.0  # this long above the cooling comfort, or below the heating comfort less a degree
OTHER_SIDE_MAX_S = 3600.0  # heating (for a cooling day) or cooling (for a heating day) ran less than this
HEAT_BAND_K = 1.0  # below the heating comfort by this much a room would want heating
MIN_HOLD_S = 72 * 3600.0  # a season holds this long before the automatic switches again: against flapping in spring
DAYS_KEPT = 4  # today and the three days judged
DAYS_JUDGED = 3
DAYS_NEEDED = 2
MAX_STEP_S = 600.0  # a gap in the samples (a restart, a stalled cycle) counts for no more than this
EVALUATE_EVERY_S = 3600.0
OUTDOOR_WINDOW_S = 24 * 3600.0
OUTDOOR_HOURS_NEEDED = 18

VERDICT_COOLING = "cooling"
VERDICT_HEATING = "heating"
VERDICT_NEUTRAL = "neutral"

REASON_ROOMS_WARM_UP = "season_rooms_warm_up"
REASON_ROOMS_COOL_DOWN = "season_rooms_cool_down"
REASON_OUTDOOR_MEAN = "season_outdoor_mean"
REASON_FROST = "season_frost"
REASON_NO_EVIDENCE = "season_no_evidence"
REASONS = (REASON_ROOMS_WARM_UP, REASON_ROOMS_COOL_DOWN, REASON_OUTDOOR_MEAN, REASON_FROST, REASON_NO_EVIDENCE)


# ---------------------------------------------------------------------------
# What runs in a room
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnitSample:
    """The heat pump as one cycle saw it."""

    hvac_mode: str | None
    compressor_hz: float | None  # None: the unit shows no frequency
    resting: bool  # resting at its setpoint limit under our control
    run_on_after_cooling: bool  # the fan runs on after a cooling run (coordinator._run_on_after_cooling)


@dataclass(frozen=True)
class RoomOperation:
    heats: bool
    cools: bool
    free: bool


def _compressor_runs(unit: UnitSample) -> bool:
    if unit.compressor_hz is not None:
        return unit.compressor_hz > 0
    return not unit.resting  # without a frequency: running, except in our own rest


def room_operation(
    *, served: bool, unit: UnitSample, thermostat_heating: bool | None, calling: bool, boiler_released: bool,
    drying: bool, window_open: bool | None, own_reading: bool,
) -> RoomOperation:
    """Heating ran, cooling ran, or the room ran free (§20.2 "Betrieb, genau festgelegt").

    Resting at 30 °C in cool with the compressor at 0 Hz is no cooling: counted as cooling, the served rooms would never
    have run free in summer, and no heating day could have been seen (review of the draft, 2026-11-13).
    """
    by_unit_heat = served and unit.hvac_mode == "heat" and _compressor_runs(unit)
    # a silent thermostat (None) heats while its room calls and the boiler, or the central heating, is released
    radiator = thermostat_heating is True or (thermostat_heating is None and calling and boiler_released)
    heats = by_unit_heat or radiator or drying
    cools = served and (unit.run_on_after_cooling or (unit.hvac_mode in ("cool", "dry") and _compressor_runs(unit)))
    free = not heats and not cools and window_open is False and own_reading
    return RoomOperation(heats, cools, free)


# ---------------------------------------------------------------------------
# Seconds per day
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoomSample:
    key: str
    contact: bool  # it has a window contact: only such rooms are judged
    served: bool  # the heat pump serves it: only then can it be cooled, and its warm seconds count
    heats: bool
    cools: bool
    free: bool
    temperature: float | None
    heat_comfort: float  # the comforts in force at the moment: a comfort changed later changes no day gone by
    cool_comfort: float
    load: bool  # a bathroom drying or a humidity load: warm seconds then are the shower's
    drying: bool = False  # heats, but not for the flat: a daily drying would have made every day neutral (review)


@dataclass
class RoomDay:
    free_s: float = 0.0
    warm_s: float = 0.0
    cool_s: float = 0.0
    heat_s: float = 0.0
    cooled_s: float = 0.0


@dataclass
class Day:
    start_ts: float
    rooms: dict[str, RoomDay] = field(default_factory=dict)
    heat_s: float = 0.0  # anywhere in the flat, rooms without a contact included
    cooled_s: float = 0.0


_ROOM_FIELDS = ("free_s", "warm_s", "cool_s", "heat_s", "cooled_s")


def _midnight(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class FreeRunDays:
    """The seconds of the last days per room, the day each second lies in (a night splits over two days)."""

    days: dict[str, Day] = field(default_factory=dict)  # by ISO date
    last_ts: float | None = None

    def observe(self, now: datetime, rooms: list[RoomSample]) -> None:
        """Count the time since the last sample with what runs now."""
        now_ts = now.timestamp()
        last, self.last_ts = self.last_ts, now_ts
        if last is None or now_ts <= last:
            return
        begin = max(last, now_ts - MAX_STEP_S)
        midnight = _midnight(now)
        if begin < midnight.timestamp():
            self._add(midnight - timedelta(days=1), midnight.timestamp() - begin, rooms)
            self._add(midnight, now_ts - midnight.timestamp(), rooms)
        else:
            self._add(midnight, now_ts - begin, rooms)
        for gone in sorted(self.days)[:-DAYS_KEPT]:
            del self.days[gone]

    def _add(self, midnight: datetime, seconds: float, rooms: list[RoomSample]) -> None:
        if seconds <= 0:
            return
        day = self.days.setdefault(midnight.date().isoformat(), Day(midnight.timestamp()))
        if any(r.heats and not r.drying for r in rooms):
            day.heat_s += seconds
        if any(r.cools for r in rooms):
            day.cooled_s += seconds
        for r in rooms:
            if not r.contact:
                continue
            totals = day.rooms.setdefault(r.key, RoomDay())
            totals.heat_s += seconds if r.heats else 0.0
            totals.cooled_s += seconds if r.cools else 0.0
            if not r.free or r.temperature is None:
                continue
            totals.free_s += seconds
            if r.served and not r.load and r.temperature >= r.cool_comfort:
                totals.warm_s += seconds
            if r.temperature <= r.heat_comfort - HEAT_BAND_K:
                totals.cool_s += seconds

    def day_of(self, at: datetime) -> Day:
        midnight = _midnight(at)
        return self.days.get(midnight.date().isoformat()) or Day(midnight.timestamp())

    def verdicts(self, now: datetime) -> tuple[tuple[float, str | None], ...]:
        """Yesterday's verdict first, then the days before it: (start of the day, verdict or None where not counted)."""
        midnight = _midnight(now)
        out = []
        for back in range(1, DAYS_JUDGED + 1):
            day = self.days.get((midnight - timedelta(days=back)).date().isoformat())
            start = (midnight - timedelta(days=back)).timestamp()
            out.append((start, None if day is None else verdict(day)))
        return tuple(out)

    def clear(self) -> None:
        self.days.clear()
        self.last_ts = None

    def to_storage(self) -> dict:
        return {
            "last_ts": self.last_ts,
            "days": {
                key: {
                    "start_ts": day.start_ts, "heat_s": round(day.heat_s, 1), "cooled_s": round(day.cooled_s, 1),
                    "rooms": {r: [round(getattr(t, f), 1) for f in _ROOM_FIELDS] for r, t in day.rooms.items()},
                }
                for key, day in self.days.items()
            },
        }

    @classmethod
    def from_storage(cls, data: object) -> FreeRunDays:
        raw = as_dict(data)
        out = cls(last_ts=as_float(raw.get("last_ts")))
        for key, stored in as_dict(raw.get("days")).items():
            stored = as_dict(stored)
            start = as_float(stored.get("start_ts"))
            if start is None:
                continue
            day = Day(start, heat_s=as_float(stored.get("heat_s")) or 0.0, cooled_s=as_float(stored.get("cooled_s")) or 0.0)
            for room, values in as_dict(stored.get("rooms")).items():
                if isinstance(values, list) and len(values) == len(_ROOM_FIELDS):
                    numbers = [as_float(v) or 0.0 for v in values]
                    day.rooms[str(room)] = RoomDay(*numbers)
            out.days[str(key)] = day
        for gone in sorted(out.days)[:-DAYS_KEPT]:
            del out.days[gone]
        return out


def verdict(day: Day) -> str | None:
    """Cooling, heating or neutral; None for a day on which no room with a contact ran free long enough."""
    if not any(r.free_s >= FREE_MIN_S for r in day.rooms.values()):
        return None
    cooling = any(r.warm_s >= SIDE_MIN_S for r in day.rooms.values()) and day.heat_s < OTHER_SIDE_MAX_S
    heating = any(r.cool_s >= SIDE_MIN_S for r in day.rooms.values()) and day.cooled_s < OTHER_SIDE_MAX_S
    if cooling == heating:
        return VERDICT_NEUTRAL  # neither, or both: one room warm and another cool says nothing for the flat
    return VERDICT_COOLING if cooling else VERDICT_HEATING


# ---------------------------------------------------------------------------
# The outdoor daily mean, the fallback and the veto
# ---------------------------------------------------------------------------
@dataclass
class OutdoorHours:
    """Hourly means of the outdoor temperature over the last day."""

    hours: list[tuple[float, float]] = field(default_factory=list)  # (start of the hour, mean)
    current: tuple[float, float, int] | None = None  # the hour under way: start, sum, samples

    def observe(self, now_ts: float, value: float | None) -> None:
        if value is None:
            return
        start = now_ts - now_ts % 3600.0
        if self.current is None or self.current[0] != start:
            if self.current is not None and self.current[2]:
                self.hours.append((self.current[0], self.current[1] / self.current[2]))
            self.current = (start, 0.0, 0)
        self.current = (start, self.current[1] + value, self.current[2] + 1)
        self.hours = [(s, m) for s, m in self.hours if s >= now_ts - OUTDOOR_WINDOW_S]

    def mean(self, now_ts: float) -> float | None:
        """The mean of the last 24 whole hours, where at least 18 of them are known."""
        known = [m for s, m in self.hours if s >= now_ts - OUTDOOR_WINDOW_S]
        return sum(known) / len(known) if len(known) >= OUTDOOR_HOURS_NEEDED else None

    def to_storage(self) -> dict:
        return {"hours": [[s, round(m, 2)] for s, m in self.hours], "current": list(self.current) if self.current else None}

    @classmethod
    def from_storage(cls, data: object) -> OutdoorHours:
        raw = as_dict(data)
        out = cls()
        hours = raw.get("hours")
        for item in hours if isinstance(hours, list) else []:
            if isinstance(item, list) and len(item) == 2:
                s, m = as_float(item[0]), as_float(item[1])
                if s is not None and m is not None:
                    out.hours.append((s, m))
        cur = raw.get("current")
        if isinstance(cur, list) and len(cur) == 3:
            s, total, n = (as_float(v) for v in cur)
            if s is not None and total is not None and n is not None and n >= 1:
                out.current = (s, total, int(n))
        return out


# ---------------------------------------------------------------------------
# Switching
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeasonInputs:
    now_ts: float
    season: str  # the season in force
    since_ts: float | None  # its last real change
    verdicts: tuple[tuple[float, str | None], ...]  # yesterday first: (start of the day, verdict)
    outdoor_mean: float | None
    frost: bool  # a room is on the frost list
    unit_can_cool: bool  # as far as last seen: an unreachable unit is no reason to give up cooling
    cooling_from: float
    heating_from: float
    veto: float


@dataclass(frozen=True)
class SeasonDecision:
    season: str
    reason: str | None  # None: nothing new to say, the season stays
    changed: bool


def decide(inp: SeasonInputs) -> SeasonDecision:
    cur = inp.season

    def stay(reason: str | None = None) -> SeasonDecision:
        return SeasonDecision(cur, reason, False)

    def switch(season: str, reason: str) -> SeasonDecision:
        return SeasonDecision(season, reason, True)

    if cur == SEASON_COOLING and inp.frost:
        return switch(SEASON_HEATING, REASON_FROST)  # at once, whatever has held how long
    held = inp.since_ts is None or inp.now_ts - inp.since_ts >= MIN_HOLD_S
    can_cool = inp.unit_can_cool and not inp.frost
    judged = inp.verdicts[:DAYS_JUDGED]
    counted = [v for _, v in judged if v is not None]
    # only days wholly after the last change: the season before it shaped them
    after = [v for start, v in judged if v is not None and (inp.since_ts is None or start >= inp.since_ts)]
    if len(counted) >= DAYS_NEEDED:
        vetoed = inp.outdoor_mean is not None and inp.outdoor_mean < inp.veto
        if (
            cur == SEASON_HEATING and held and can_cool and not vetoed
            and after.count(VERDICT_COOLING) >= DAYS_NEEDED and VERDICT_HEATING not in counted
        ):
            return switch(SEASON_COOLING, REASON_ROOMS_WARM_UP)
        if (
            cur == SEASON_COOLING and held
            and after.count(VERDICT_HEATING) >= DAYS_NEEDED and VERDICT_COOLING not in counted
        ):
            return switch(SEASON_HEATING, REASON_ROOMS_COOL_DOWN)
        return stay()
    if inp.outdoor_mean is None:
        return stay(REASON_NO_EVIDENCE)
    if not held:
        return stay()
    if cur == SEASON_HEATING and can_cool and inp.outdoor_mean >= inp.cooling_from:
        return switch(SEASON_COOLING, REASON_OUTDOOR_MEAN)
    if cur == SEASON_COOLING and inp.outdoor_mean <= inp.heating_from:
        return switch(SEASON_HEATING, REASON_OUTDOOR_MEAN)
    return stay()
