"""Gas by purpose: how much went into the heating, how much into hot water.

Two ways to know, by what the installation has:

A gas meter measures all of it; each step of the meter goes to what the boiler was doing
when it came. What came while nobody watched (restart, outage), and what one look brings
beyond what a boiler can burn since the look before, is nobody's: it is counted, but for
neither purpose. A reading that went back, or one that jumped as no consumption does
(another meter, a value set by hand), is not counted at all: the count goes on from it.
The meter's total as energy (`meter_total_kwh`) is where the meter stood when the count
began plus everything counted since, so it never goes down.

Without a meter, Vaillant boilers keep the split themselves: the ebusd messages
PrEnergySumHc1-3 and PrEnergySumHwc1-3 add up the burner output in percent of the nominal
heat input, once a second (their companions PrEnergyCount* count those seconds, and add up
to the burner hours). Checked on the test installation against its gas meter, 2026-09-12 to
2026-10-13, 95.8 kWh: the sums follow the meter best with 24.4 kW per 100 %. The type plate
(atmoTEC plus VCW 194/4-5) says 22.2 kW of heat input by the lower calorific value, 24.6 kW
by the upper one a gas bill counts in; with that the estimate was 1 % above the meter and
off by no more than 0.3 kWh on any day.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Any

from ..const import BOILER_REPORTS_HEATING, BOILER_REPORTS_HOT_WATER
from .common import as_dict, as_float

SOURCE_METER = "gas_meter"
SOURCE_COUNTERS = "boiler_counters"

# A meter step is credited to what the boiler does now. After a longer gap that is not known.
MAX_GAP_S = 900.0
# One hot water sign stands for this long: the meter reports its step a little after the flame.
HOT_WATER_SIGN_S = 120.0
# More than this in one step is no consumption: another meter, a reading set by hand.
MAX_STEP_M3 = 50.0
# What a household boiler burns at most: 6 m³/h is about 60 kW. One look credits no more than that for
# the time since the look before; the rest of the step was burnt earlier, nobody knows what for. Review
# 2026-10-13: a meter silent for three hours, its entity keeping the last reading all the while, then
# reported 2.4 m³ at once while somebody showered, and all of it was counted as hot water.
MAX_FLOW_M3_H = 6.0
PERCENT_SECONDS_PER_HOUR = 360000.0  # 100 % for 3600 s
# A burner adds at most 100 percent-seconds a second to its counters; half as much again is allowed.
MAX_COUNTER_RATE = 150.0
UPPER_PER_LOWER_CALORIFIC = 1.11  # natural gas: a type plate counts by the lower value, the bill by the upper


@dataclass(frozen=True)
class GasSplit:
    heating_kwh: float = 0.0
    hot_water_kwh: float = 0.0
    unassigned_kwh: float = 0.0  # burnt while nobody watched what for (gas meter only)
    source: str | None = None
    meter_m3: float | None = None  # the meter reading counted up to
    meter_start_kwh: float | None = None  # the meter's energy when the count began (gas meter only): `meter_total_kwh`
    counted_ts: float | None = None  # when the meter or the counters were last looked at
    counters: Mapping[str, float] = field(default_factory=dict)  # each boiler counter's reading counted up to
    counters_ts: Mapping[str, float] = field(default_factory=dict)  # ... and when that reading was first seen

    def to_storage(self) -> dict[str, Any]:
        return {
            "heating_kwh": self.heating_kwh,
            "hot_water_kwh": self.hot_water_kwh,
            "unassigned_kwh": self.unassigned_kwh,
            "source": self.source,
            "meter_m3": self.meter_m3,
            "meter_start_kwh": self.meter_start_kwh,
            "counted_ts": self.counted_ts,
            "counters": dict(self.counters),
            "counters_ts": dict(self.counters_ts),
        }

    @classmethod
    def from_storage(cls, data: Any) -> GasSplit:
        raw = as_dict(data)

        def numbers(key: str) -> dict[str, float]:
            return {str(k): v for k, value in as_dict(raw.get(key)).items() if (v := as_float(value)) is not None}

        source = raw.get("source")
        start = as_float(raw.get("meter_start_kwh"))
        return cls(
            heating_kwh=max(0.0, as_float(raw.get("heating_kwh")) or 0.0),
            hot_water_kwh=max(0.0, as_float(raw.get("hot_water_kwh")) or 0.0),
            unassigned_kwh=max(0.0, as_float(raw.get("unassigned_kwh")) or 0.0),
            source=source if source in (SOURCE_METER, SOURCE_COUNTERS) else None,
            meter_m3=as_float(raw.get("meter_m3")),
            meter_start_kwh=start if start is not None and isfinite(start) else None,
            counted_ts=as_float(raw.get("counted_ts")),
            counters=numbers("counters"),
            counters_ts=numbers("counters_ts"),
        )


def hot_water_now(
    reported_mode: str | None, hot_water_active: bool, hot_water_seen_ts: float | None, now_ts: float, warm_start: bool = False
) -> bool:
    """Whether gas burnt now is burnt for hot water.

    The boiler's status code says it where it is known. Otherwise the boiler's hot water state
    counts, or the control's own last hot water sign while it is fresh (it shows hot water for
    ten minutes after a tap; that long a time would take a heating run for hot water). A warm
    start (engines/boiler_control.py) is hot water although the boiler reports heating.
    """
    if warm_start:
        return True
    if reported_mode == BOILER_REPORTS_HEATING:
        return False
    if reported_mode == BOILER_REPORTS_HOT_WATER or hot_water_active:
        return True
    return hot_water_seen_ts is not None and 0.0 <= now_ts - hot_water_seen_ts < HOT_WATER_SIGN_S


def by_meter(split: GasSplit, meter_m3: float, kwh_per_m3: float, hot_water: bool, now_ts: float) -> GasSplit:
    """Credit what the meter moved since the last look to what the boiler is doing now."""
    first = split.source != SOURCE_METER or split.meter_m3 is None or split.counted_ts is None
    looked = replace(split, source=SOURCE_METER, meter_m3=meter_m3, counted_ts=now_ts, counters={}, counters_ts={})
    if looked.meter_start_kwh is None:
        # The total starts at the meter's energy: of this reading at the first look, and for a count that
        # began before the total was kept, of the reading it stands at less what it has counted.
        counted = split.heating_kwh + split.hot_water_kwh + split.unassigned_kwh
        looked = replace(looked, meter_start_kwh=round((meter_m3 if first else split.meter_m3) * kwh_per_m3 - counted, 4))
    if first:
        return looked  # first look: count from here
    step = meter_m3 - split.meter_m3
    if step <= 0.0 or step > MAX_STEP_M3:
        return looked  # nothing burnt, or not the same meter any more: counted from here, nothing credited
    elapsed = now_ts - split.counted_ts
    told = 0.0 if elapsed > MAX_GAP_S else min(step, MAX_FLOW_M3_H * max(elapsed, 0.0) / 3600.0)
    looked = replace(looked, unassigned_kwh=round(split.unassigned_kwh + (step - told) * kwh_per_m3, 4))
    if hot_water:
        return replace(looked, hot_water_kwh=round(split.hot_water_kwh + told * kwh_per_m3, 4))
    return replace(looked, heating_kwh=round(split.heating_kwh + told * kwh_per_m3, 4))


def meter_total_kwh(split: GasSplit) -> float | None:
    """All the gas the meter has counted, as energy; None without a gas meter.

    Where the meter stood when the count began plus what was counted since, each step at the
    calorific value of its day. Reading times today's calorific value instead rescales every
    year before with each change of that value; this total only ever grows, also when another
    meter with a smaller reading takes over or a step is left out as no consumption.
    """
    if split.source != SOURCE_METER or split.meter_start_kwh is None:
        return None
    return round(split.meter_start_kwh + split.heating_kwh + split.hot_water_kwh + split.unassigned_kwh, 4)


def counter_kwh(percent_seconds: float, nominal_input_kw: float) -> float:
    """Gas energy by the upper calorific value for what the boiler's counters added up."""
    return percent_seconds / PERCENT_SECONDS_PER_HOUR * nominal_input_kw * UPPER_PER_LOWER_CALORIFIC


def by_counters(
    split: GasSplit, heating: Mapping[str, float], hot_water: Mapping[str, float], nominal_input_kw: float, now_ts: float
) -> GasSplit:
    """Credit what the boiler's own counters moved; each counter is followed by itself.

    A counter read for the first time, or one that went back (it overflows after years and can
    be reset), starts from where it stands. So does one that rose by more than a burner can add
    in the time since its reading last changed: read as 0 once, a counter at 3.0e9 came back as
    205,350 kWh (review 2026-10-13). That time is the counter's own, not the time since the last
    look: ebusd reads these values in turn, each one every few minutes, and a reading then brings
    the minutes since the one before. One that is not there this time keeps its place.
    """
    watching = split.source == SOURCE_COUNTERS and split.counted_ts is not None
    known = dict(split.counters) if watching else {}
    seen = dict(split.counters_ts) if watching else {}
    look_s = max(now_ts - split.counted_ts, 0.0) if watching else 0.0
    moved = {"heating": 0.0, "hot_water": 0.0}
    for purpose, readings in (("heating", heating), ("hot_water", hot_water)):
        for name, value in readings.items():
            before = known.get(name)
            if value == before:
                continue
            if before is not None and value > before:
                # Since the reading before was first seen, and one look more: it may have been that old by
                # then. A count stored before these times were kept allows the one look only.
                quiet_s = max(now_ts - seen[name], 0.0) if name in seen else 0.0
                if value - before <= MAX_COUNTER_RATE * (quiet_s + look_s):
                    moved[purpose] += value - before
            known[name], seen[name] = value, now_ts
    return replace(
        split,
        source=SOURCE_COUNTERS,
        meter_m3=None,
        counted_ts=now_ts,
        counters=known,
        counters_ts=seen,
        heating_kwh=round(split.heating_kwh + counter_kwh(moved["heating"], nominal_input_kw), 4),
        hot_water_kwh=round(split.hot_water_kwh + counter_kwh(moved["hot_water"], nominal_input_kw), 4),
    )


def hot_water_share(split: GasSplit) -> float | None:
    """Hot water's part of what could be told apart, 0..1."""
    told = split.heating_kwh + split.hot_water_kwh
    return None if told <= 0.0 else round(split.hot_water_kwh / told, 3)
