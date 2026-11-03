"""Adaptive statistics. Deterministic, inspectable, resettable.

* CopMap      – heat and electricity added up per outdoor-temperature band; falls back
                to a prior curve scaled by a factor learned from the measured bands
* HeatRateStats – heat-up rate per room and heat source in K/h from observed
                  episodes, used for the away-return and schedule preheat planning.

Everything serialises to plain dicts for Home Assistant storage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from statistics import median

from .common import as_dict, as_float, as_floats

BIN_WIDTH_K = 2.0

# A band forgets by the hours run in it, not by entries or by the calendar. Until 2026-11-02 it was a mean with
# an alpha of at least 0.2 and remembered about 45 minutes of running: 4.53 against 3.56 in energy on 2026-11-01 at
# +8..+10. A band saw 14-23 hours in the 30 days to 2026-11-02: this half-life spans a heating season and still
# follows a unit that changes, and a band that does not run for months keeps what it knows.
COP_BAND_HALF_LIFE_S = 100 * 3600.0
COP_BAND_LEARNED_S = 3 * 3600.0  # run time from which a band counts as known: estimate, calibration and learning runs
# Outside this the sum says more about the sensors than about the unit: an outlet reading frozen cold shows
# no heat under a running compressor, a wrong airflow curve an impossible COP. Such a band decides nothing.
COP_BAND_PLAUSIBLE = (1.0, 7.0)
# The table from before 2026-11-02 cannot be turned into sums: it holds a mean of the last entries, no energy.
# Its values bridge the hours until a band is learned, as worth this much run time of it.
LEGACY_WORTH_S = 3600.0
LEGACY_WORTH_WH = 340.0  # that hour in electricity, at the least speed (24 Hz, about 340 W, seen 2026-10-20 to 30)

BASIS_LEARNED = "learned"
BASIS_LEGACY = "legacy"
BASIS_LEARNING = "learning"  # a band on its way: shown, it decides nothing yet

EPISODE_MIN_S = 30 * 60.0
EPISODE_MIN_RISE_K = 0.5
HEAT_RATE_KEEP = 10
HEAT_RATE_MIN = 0.1
HEAT_RATE_MAX = 6.0

# Prior COP over outdoor temperature. Midea publishes only SCOP 4.0 (A+) and a heating range
# down to -10 °C, no COPd(Tj) table. These points follow typical A+ air-to-air product fiches
# (EN 14825 test points) and only shape the curve; the level is calibrated from measurements.
PRIOR_COP_POINTS: tuple[tuple[float, float], ...] = ((-10.0, 2.2), (-7.0, 2.5), (2.0, 3.7), (7.0, 4.9), (12.0, 6.0), (20.0, 6.0))
# Duct losses and real operation keep a portable split below its fiche: start at 80 %.
PRIOR_CALIBRATION_DEFAULT = 0.8
PRIOR_CALIBRATION_RANGE = (0.3, 1.5)
MIN_SAMPLES = 5  # entries from which a bin of a shipped map, or of the old table, counts as known


def prior_cop(outdoor_c: float) -> float:
    pts = PRIOR_COP_POINTS
    if outdoor_c <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if outdoor_c <= x1:
            return y0 + (y1 - y0) * (outdoor_c - x0) / (x1 - x0)
    return pts[-1][1]


def _calibration(bins: Iterable[tuple[int, float, float]]) -> float | None:
    """Measured / prior over the bins given, each with its weight."""
    num = den = 0.0
    for idx, mean, weight in bins:
        centre = idx * BIN_WIDTH_K + BIN_WIDTH_K / 2.0
        num += weight * mean / prior_cop(centre)
        den += weight
    if den == 0:
        return None
    lo, hi = PRIOR_CALIBRATION_RANGE
    return round(min(max(num / den, lo), hi), 3)


@dataclass
class CopBand:
    """What a band of outdoor temperature cost and gave, over all of its heating time.

    The heat is stored without the duct factor, which is applied when it is read: a change of the factor scales
    the band and takes nothing from it. Starts, run-on and defrosts are in the sums: a start is expensive
    (2.4-2.8 in its first ten minutes, 5-7.5 % of the electricity of an evening, seen 2026-10-30 and 11-01),
    a long run dilutes it by itself, and a unit that cycles shows what that truly costs.
    """

    heat_wh: float = 0.0
    electricity_wh: float = 0.0
    run_s: float = 0.0
    last_ts: float | None = None

    def add(self, heat_wh: float, electricity_wh: float, seconds: float, ts: float) -> None:
        keep = 0.5 ** (seconds / COP_BAND_HALF_LIFE_S)
        self.heat_wh = self.heat_wh * keep + heat_wh
        self.electricity_wh = self.electricity_wh * keep + electricity_wh
        self.run_s = self.run_s * keep + seconds
        self.last_ts = ts

    def cop(self, duct_factor: float) -> float | None:
        return None if self.electricity_wh <= 0.0 else self.heat_wh * duct_factor / self.electricity_wh

    def learned_cop(self, duct_factor: float) -> float | None:
        """Its COP once it counts as known, else None."""
        cop = self.cop(duct_factor)
        lo, hi = COP_BAND_PLAUSIBLE
        return cop if self.run_s >= COP_BAND_LEARNED_S and cop is not None and lo <= cop <= hi else None

    def to_storage(self) -> dict[str, float | None]:
        return {
            "heat_wh": round(self.heat_wh, 1),
            "electricity_wh": round(self.electricity_wh, 1),
            "run_s": round(self.run_s),
            "last_ts": None if self.last_ts is None else round(self.last_ts),
        }

    @classmethod
    def from_storage(cls, data: object) -> CopBand | None:
        stored = as_dict(data)
        values = [as_float(stored.get(key)) for key in ("heat_wh", "electricity_wh", "run_s")]
        if any(v is None or v < 0.0 for v in values):
            return None
        return cls(*values, as_float(stored.get("last_ts")))


@dataclass
class LegacyBin:
    """A bin of the table from before 2026-11-02. Frozen, and kept in the shape it had, for older versions to read."""

    count: int = 0
    mean: float = 0.0
    min: float = 0.0
    max: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {"count": self.count, "mean": round(self.mean, 3), "min": round(self.min, 3), "max": round(self.max, 3)}

    @classmethod
    def from_dict(cls, data: dict) -> LegacyBin:
        return cls(int(data.get("count", 0)), float(data.get("mean", 0.0)), float(data.get("min", 0.0)), float(data.get("max", 0.0)))


@dataclass
class CopMap:
    bands: dict[int, CopBand] = field(default_factory=dict)
    legacy: dict[int, LegacyBin] = field(default_factory=dict)
    # the fan stage of normal operation and the airflow curve the bands were measured with: heat is recomputed from neither
    measured_with: dict[str, str] | None = None
    legacy_duct_factor: float | None = None  # the factor the old values hold in them; a change of it used to forget them

    @staticmethod
    def bin_of(outdoor_c: float) -> int:
        return int(outdoor_c // BIN_WIDTH_K)

    @staticmethod
    def bin_label(index: int) -> str:
        lo = index * BIN_WIDTH_K
        return f"{lo:+.0f}..{lo + BIN_WIDTH_K:+.0f}"

    def bind(self, fan_stage: str, airflow_curve: tuple[tuple[float, float], ...], duct_factor: float) -> bool:
        """Note what the data is measured with. True: it was measured with something else, and the table is to restart.

        Nothing on record, as in a store from before these were kept, is taken for the setup of now.
        """
        current = {"fan_stage": fan_stage, "airflow_curve": ",".join(f"{rpm:g}:{flow:g}" for rpm, flow in airflow_curve)}
        if self.measured_with is None:
            self.measured_with = current
        if self.legacy_duct_factor is None and self.legacy:
            self.legacy_duct_factor = duct_factor
        return self.measured_with != current

    def add_interval(self, outdoor_c: float, heat_wh: float, electricity_wh: float, seconds: float, ts: float) -> None:
        """Add a stretch of heating, `heat_wh` without the duct factor, to the band of its outdoor temperature."""
        self.bands.setdefault(self.bin_of(outdoor_c), CopBand()).add(heat_wh, electricity_wh, seconds, ts)

    def learned(self, outdoor_c: float, duct_factor: float) -> bool:
        band = self.bands.get(self.bin_of(outdoor_c))
        return band is not None and band.learned_cop(duct_factor) is not None

    def expected(self, outdoor_c: float, duct_factor: float) -> float | None:
        """Expected COP at this outdoor temperature, from the band or its neighbours, when learned."""
        idx = self.bin_of(outdoor_c)
        for candidate in (idx, idx - 1, idx + 1):
            band = self.bands.get(candidate)
            cop = None if band is None else band.learned_cop(duct_factor)
            if cop is not None:
                return round(cop, 3)
        return None

    def _legacy_cop(self, idx: int, duct_factor: float) -> float | None:
        """The old value of a bin that had enough entries, with what its band has measured since."""
        old = self.legacy.get(idx)
        if old is None or old.count < MIN_SAMPLES:
            return None
        cop = old.mean
        if self.legacy_duct_factor:
            cop *= duct_factor / self.legacy_duct_factor
        band = self.bands.get(idx)
        if band is None or band.electricity_wh <= 0.0:
            return cop
        blend = (band.heat_wh * duct_factor + cop * LEGACY_WORTH_WH) / (band.electricity_wh + LEGACY_WORTH_WH)
        lo, hi = COP_BAND_PLAUSIBLE
        return blend if lo <= blend <= hi else None

    def calibration_factor(self, duct_factor: float) -> float | None:
        """Measured / prior, weighted by run time over all bands that are learned or have an old value."""

        # An old bin by its entries, as they weighed before the update, so that no band jumps with it: 0.83 against
        # 0.79 on the store of 2026-11-02. The fullest is worth the hour.
        fullest = max((old.count for old in self.legacy.values()), default=1)

        def weighted() -> Iterable[tuple[int, float, float]]:
            for idx in {*self.bands, *self.legacy}:
                band = self.bands.get(idx)
                cop = None if band is None else band.learned_cop(duct_factor)
                if cop is not None:
                    yield idx, cop, band.run_s
                elif (old := self._legacy_cop(idx, duct_factor)) is not None:
                    yield idx, old, LEGACY_WORTH_S * self.legacy[idx].count / fullest + (0.0 if band is None else band.run_s)

        return _calibration(weighted())

    def estimate(
        self, outdoor_c: float, duct_factor: float, shipped: Mapping[int, tuple[float, int]] | None = None
    ) -> tuple[float, str]:
        """Expected COP and its basis: learned band, old value, calibrated prior, shipped map of the model, or default prior.

        What this unit measured comes first. `shipped` is a map measured on the same model
        (`cop_profiles`): its bins, or the prior calibrated by them, stand in where the unit
        has measured nothing.
        """
        learned = self.expected(outdoor_c, duct_factor)
        if learned is not None:
            return learned, BASIS_LEARNED
        idx = self.bin_of(outdoor_c)
        for candidate in (idx, idx - 1, idx + 1):
            old = self._legacy_cop(candidate, duct_factor)
            if old is not None:
                return round(old, 3), BASIS_LEGACY
        factor = self.calibration_factor(duct_factor)
        if factor is not None:
            return round(prior_cop(outdoor_c) * factor, 3), "prior_calibrated"
        if shipped:
            for candidate in (idx, idx - 1, idx + 1):
                if candidate in shipped and shipped[candidate][1] >= MIN_SAMPLES:
                    return round(shipped[candidate][0], 3), "profile"
            factor = _calibration((i, mean, float(n)) for i, (mean, n) in shipped.items() if n >= MIN_SAMPLES)
            if factor is not None:
                return round(prior_cop(outdoor_c) * factor, 3), "profile"
        return round(prior_cop(outdoor_c) * PRIOR_CALIBRATION_DEFAULT, 3), "prior"

    def _shown(self, idx: int, duct_factor: float) -> dict | None:
        band = self.bands.get(idx)
        learned = band is not None and band.learned_cop(duct_factor) is not None
        old = None if learned else self._legacy_cop(idx, duct_factor)
        if band is None and old is None:
            return None  # an old bin with too few entries: as little to show as to go by
        cop = old if old is not None else None if band is None else band.cop(duct_factor)
        return {
            "runtime_h": 0.0 if band is None else round(band.run_s / 3600.0, 2),
            "heat_kwh": 0.0 if band is None else round(band.heat_wh * duct_factor / 1000.0, 2),
            "electricity_kwh": 0.0 if band is None else round(band.electricity_wh / 1000.0, 2),
            "cop": None if cop is None else round(cop, 3),
            "learned": learned,
            "basis": BASIS_LEARNED if learned else BASIS_LEGACY if old is not None else BASIS_LEARNING,
        }

    def to_dict(self, duct_factor: float) -> dict[str, dict]:
        shown = ((i, self._shown(i, duct_factor)) for i in sorted({*self.bands, *self.legacy}))
        return {self.bin_label(i): values for i, values in shown if values is not None}

    def to_storage(self) -> dict:
        return {
            "bands": {str(i): b.to_storage() for i, b in self.bands.items()},
            "bins": {str(i): b.to_dict() for i, b in self.legacy.items()},  # read by older versions as their table
            "measured_with": self.measured_with,
            "legacy_duct_factor": self.legacy_duct_factor,
        }

    @classmethod
    def from_storage(cls, data: dict | None) -> CopMap:
        """Read a store; one from before 2026-11-02 has only `bins`, the old table."""
        cm = cls()
        data = as_dict(data)
        for key, value in as_dict(data.get("bins")).items():
            try:
                old = LegacyBin.from_dict(value)
                if as_float(old.mean) is not None:
                    cm.legacy[int(key)] = old
            except (AttributeError, TypeError, ValueError):
                continue
        for key, value in as_dict(data.get("bands")).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            band = CopBand.from_storage(value)
            if band is not None:
                cm.bands[index] = band
        stored = data.get("measured_with")
        if isinstance(stored, dict):
            cm.measured_with = {str(k): str(v) for k, v in stored.items()}
        cm.legacy_duct_factor = as_float(data.get("legacy_duct_factor"))
        return cm


@dataclass
class Episode:
    """An ongoing heat-up or cool-down observation for one room."""

    start_ts: float
    start_temp: float
    last_ts: float
    last_temp: float
    gradient_sum: float = 0.0  # sum of (room - outdoor) over the samples
    samples: int = 0


# ---------------------------------------------------------------------------
# Cool-down learning
# ---------------------------------------------------------------------------
# How fast a room loses heat is the missing half for a sensible setback: only a
# room that really cools down saves anything by being set back further. The rate
# is stored as a coefficient k in 1/h, so that
#     temperature drop per hour = k * (room - outdoor)
# which stays comparable between a mild and a cold day.
COOL_EPISODE_MIN_S = 3600.0  # shorter stretches are noise from sensor rounding
COOL_MIN_DROP_K = 0.3
COOL_MIN_GRADIENT_K = 5.0  # indoors must be this much warmer than outdoors
COOL_K_MIN = 0.002  # 1/h; below this nothing measurable happens
COOL_K_MAX = 0.25  # a room that loses a quarter of its gradient per hour is a tent
COOL_KEEP = 10
COOL_END_RISE_K = 0.2  # this far above its lowest point the room is warming again, by sun or by itself


@dataclass
class CoolEpisode(Episode):
    """A cool-down ends at its lowest point: `last_ts`, `last_temp` and the gradients stand for the way down to it.

    What comes after (a room held at its setback temperature, a slow rise in the morning sun) is no
    cooling any more. Counted in, it stretched the time over the same drop and gave a coefficient
    several times too low. Its gradients wait here and count once the room falls further.
    """

    since_low_sum: float = 0.0
    since_low_samples: int = 0


@dataclass
class CoolRateStats:
    """Median cool-down coefficient per room (1/h) from the last episodes."""

    rates: dict[str, list[float]] = field(default_factory=dict)
    episodes: dict[str, CoolEpisode] = field(default_factory=dict)

    def observe(
        self, room: str, ts: float, temp: float | None, outdoor: float | None, heating: bool, window_open: bool
    ) -> float | None:
        """Feed one sample. Returns a newly learned coefficient when an episode closes."""
        ep = self.episodes.get(room)
        gradient = None if temp is None or outdoor is None else temp - outdoor
        if temp is None or heating or window_open or gradient is None or gradient < COOL_MIN_GRADIENT_K:
            return self._close(room, ep)
        if ep is None:
            self.episodes[room] = CoolEpisode(ts, temp, ts, temp, gradient, 1)
            return None
        if temp >= ep.last_temp + COOL_END_RISE_K:  # warming again: the cool-down ended at its lowest point
            learned = self._close(room, ep)
            self.episodes[room] = CoolEpisode(ts, temp, ts, temp, gradient, 1)
            return learned
        ep.since_low_sum += gradient
        ep.since_low_samples += 1
        if temp < ep.last_temp:
            ep.last_ts, ep.last_temp = ts, temp
            ep.gradient_sum += ep.since_low_sum
            ep.samples += ep.since_low_samples
            ep.since_low_sum, ep.since_low_samples = 0.0, 0
        return None

    def _close(self, room: str, ep: CoolEpisode | None) -> float | None:
        if ep is None:
            return None
        del self.episodes[room]
        duration = ep.last_ts - ep.start_ts
        drop = ep.start_temp - ep.last_temp
        if duration < COOL_EPISODE_MIN_S or drop < COOL_MIN_DROP_K or ep.samples == 0:
            return None
        mean_gradient = ep.gradient_sum / ep.samples
        if mean_gradient <= 0:
            return None
        k = (drop / (duration / 3600.0)) / mean_gradient
        if not COOL_K_MIN <= k <= COOL_K_MAX:
            return None
        lst = self.rates.setdefault(room, [])
        lst.append(round(k, 5))
        del lst[:-COOL_KEEP]
        return k

    def rate(self, room: str) -> float | None:
        lst = self.rates.get(room)
        return round(median(lst), 5) if lst else None

    def forget(self, rooms: Iterable[str] | None = None) -> None:
        if rooms is None:
            self.rates.clear()
            self.episodes.clear()
            return
        for room in rooms:
            self.rates.pop(room, None)
            self.episodes.pop(room, None)

    def to_dict(self) -> dict[str, dict]:
        return {room: {"k_per_h": self.rate(room), "episodes": len(lst)} for room, lst in self.rates.items()}

    def to_storage(self) -> dict:
        return {"rates": self.rates}

    @classmethod
    def from_storage(cls, data: dict | None) -> CoolRateStats:
        cs = cls()
        rates = as_dict(as_dict(data).get("rates"))
        cs.rates = {str(k): as_floats(v)[-COOL_KEEP:] for k, v in rates.items() if isinstance(v, list)}
        return cs


# ---------------------------------------------------------------------------
# Heat-up rate learning
# ---------------------------------------------------------------------------
# A room heats up at another pace from each source: seen 2026-10-20 to 30, the heat pump at its least
# speed warmed the bedroom 0.46-0.5 degrees an hour, the boiler 0.23-0.6, and a quick heat-up at
# 650-930 W 1.4-2.1. Kept in one list they planned a preheat with a pace neither source had.
HEAT_BY_BOILER = "boiler"  # the boiler (or the district heating) through the radiator
HEAT_BY_HEAT_PUMP = "heat_pump"  # the heat pump at the least speed it is held at
HEAT_BY_BOTH = "both"  # both at once: a rate of neither
HEAT_SOURCES = (HEAT_BY_BOILER, HEAT_BY_HEAT_PUMP)


@dataclass
class HeatEpisode(Episode):
    source: str = HEAT_BY_BOILER


@dataclass
class HeatRateStats:
    """Median heat-up rate per room and heat source (K/h) from the last episodes."""

    rates: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    # the mean indoor-outdoor gradient of each episode, in step with `rates`: a rate
    # measured in mild weather hides a small loss, one measured in frost a large one
    gradients: dict[str, dict[str, list[float | None]]] = field(default_factory=dict)
    episodes: dict[str, HeatEpisode] = field(default_factory=dict)

    def observe(
        self, room: str, ts: float, temp: float | None, source: str | None, window_open: bool, outdoor: float | None = None
    ) -> float | None:
        """Feed one sample: `source` is the heat source that heats the room now, None for none.

        Returns a newly learned rate when an episode closes, else None. A change of source, or both
        at once, ends the episode without a value: the radiator gives off its heat for a while after
        the boiler stopped, and the heat pump's first minutes after a start are no pace of its own.
        """
        ep = self.episodes.get(room)
        if ep is not None and source is not None and source != ep.source:
            del self.episodes[room]
            ep = None
        if temp is None or source not in HEAT_SOURCES or window_open:
            return self._close(room, ep)
        gradient = None if outdoor is None else temp - outdoor
        if ep is None or temp < ep.last_temp - 0.3:  # none yet, or the temperature fell: not a clean heat-up
            self.episodes[room] = HeatEpisode(ts, temp, ts, temp, gradient or 0.0, 1 if gradient is not None else 0, source)
            return None
        ep.last_ts, ep.last_temp = ts, temp
        if gradient is not None:
            ep.gradient_sum += gradient
            ep.samples += 1
        return None

    def _close(self, room: str, ep: HeatEpisode | None) -> float | None:
        if ep is None:
            return None
        del self.episodes[room]
        duration = ep.last_ts - ep.start_ts
        rise = ep.last_temp - ep.start_temp
        if duration < EPISODE_MIN_S or rise < EPISODE_MIN_RISE_K:
            return None
        rate = rise / (duration / 3600.0)
        if not HEAT_RATE_MIN <= rate <= HEAT_RATE_MAX:
            return None
        lst = self.rates.setdefault(room, {}).setdefault(ep.source, [])
        lst.append(round(rate, 3))
        del lst[:-HEAT_RATE_KEEP]
        grad = self.gradients.setdefault(room, {}).setdefault(ep.source, [])
        grad.append(None if ep.samples == 0 else round(ep.gradient_sum / ep.samples, 2))
        del grad[:-HEAT_RATE_KEEP]
        return rate

    def forget(self, rooms: Iterable[str] | None = None) -> None:
        """Drop learned rates and open episodes; all rooms when none are named."""
        if rooms is None:
            self.rates.clear()
            self.gradients.clear()
            self.episodes.clear()
            return
        for room in rooms:
            self.rates.pop(room, None)
            self.gradients.pop(room, None)
            self.episodes.pop(room, None)

    def rate(self, room: str, source: str) -> float | None:
        lst = self.rates.get(room, {}).get(source)
        return round(median(lst), 3) if lst else None

    def heating_power_k_h(self, room: str, source: str, cool_k: float | None) -> float | None:
        """Heat-up rate the room would reach from that source with no losses at all: rate + k * gradient.

        Unlike the measured rate this is a property of room and heat source rather than
        of the weather during the episode, so pre-heating can be planned in winter
        from episodes recorded in autumn. Needs the cool-down coefficient.
        """
        if cool_k is None:
            return None
        pairs = [
            (rate, grad)
            for rate, grad in zip(
                self.rates.get(room, {}).get(source, []), self.gradients.get(room, {}).get(source, []), strict=False
            )
            if grad is not None
        ]
        if not pairs:
            return None
        return round(median([rate + cool_k * grad for rate, grad in pairs]), 3)

    def planning_rate(
        self, room: str, source: str, cool_k: float | None, temp: float | None, outdoor: float | None
    ) -> float | None:
        """The pace a preheat from that source is planned with, for the weather of now; None: nothing learned.

        Where the heating power is known, the room reaches that less what it loses at the present gap to
        outdoors: at 3 °C the heat pump's minimum barely holds the bathroom, at 8 °C it adds about 0.5 an
        hour. Otherwise the median of the rates learned, whatever the weather was then. Used for the heat pump only:
        a boiler's heating curve raises its flow in frost, so its median is planned with (review 2026-10-30).
        """
        power = self.heating_power_k_h(room, source, cool_k)
        if power is None or cool_k is None or temp is None or outdoor is None:
            return self.rate(room, source)
        return round(max(power - cool_k * (temp - outdoor), HEAT_RATE_MIN), 3)

    def to_dict(self) -> dict[str, dict[str, dict]]:
        def shown(rates: list[float], grads: list[float | None]) -> dict:
            known = [g for g in grads if g is not None]
            return {
                "median_k_h": round(median(rates), 3) if rates else None,
                "episodes": len(rates),
                "mean_gradient_k": round(median(known), 1) if known else None,
            }

        return {
            room: {source: shown(lst, self.gradients.get(room, {}).get(source, [])) for source, lst in by_source.items()}
            for room, by_source in self.rates.items()
        }

    def to_storage(self) -> dict:
        return {"rates": self.rates, "gradients": self.gradients}

    @classmethod
    def from_storage(cls, data: dict | None, heat_pump_rooms: Iterable[str] = ()) -> HeatRateStats:
        """Read a store; one from before 0.7.0 holds a single list per room, of both sources.

        Those go to the boiler in a room the heat pump does not serve, which has no other source.
        In a room it serves they are dropped: which source each came from is not known, and the
        heat pump's were mostly quick heat-ups. The defaults bridge the days until new ones come.
        """
        hs = cls()
        data = as_dict(data)
        served = set(heat_pump_rooms)
        rates = as_dict(data.get("rates"))
        gradients = as_dict(data.get("gradients"))
        for room, stored in rates.items():
            room = str(room)
            if isinstance(stored, list):  # the old store
                if room in served:
                    continue
                by_source, grads_by_source = {HEAT_BY_BOILER: stored}, {HEAT_BY_BOILER: gradients.get(room)}
            else:
                by_source, grads_by_source = as_dict(stored), as_dict(gradients.get(room))
            for source, values in by_source.items():
                if source not in HEAT_SOURCES or not isinstance(values, list):
                    continue
                kept = as_floats(values)[-HEAT_RATE_KEEP:]
                if not kept:
                    continue
                grads = grads_by_source.get(source)
                grads = [as_float(x) for x in grads] if isinstance(grads, list) else []
                # older stores have no gradients: pad them
                hs.rates.setdefault(room, {})[source] = kept
                hs.gradients.setdefault(room, {})[source] = ([None] * max(0, len(kept) - len(grads)) + grads)[-HEAT_RATE_KEEP:]
        return hs
