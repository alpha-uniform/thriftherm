"""Adaptive statistics (architecture §14a). Deterministic, inspectable, resettable.

* CopMap      – mean COP per outdoor-temperature bin ; falls back to a
                prior curve scaled by a factor learned from the measured bins
* HeatRateStats – heat-up rate per room in K/h from observed episodes,
                  used for the away-return and schedule preheat planning.

Everything serialises to plain dicts for Home Assistant storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import median

from .common import as_dict, as_float, as_floats

BIN_WIDTH_K = 2.0
MAX_ALPHA = 0.2  # running mean becomes an EMA once the bin has 1/MAX_ALPHA samples

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
MIN_BIN_COUNT = 5


def prior_cop(outdoor_c: float) -> float:
    pts = PRIOR_COP_POINTS
    if outdoor_c <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if outdoor_c <= x1:
            return y0 + (y1 - y0) * (outdoor_c - x0) / (x1 - x0)
    return pts[-1][1]


@dataclass
class CopBin:
    count: int = 0
    mean: float = 0.0
    min: float = 0.0
    max: float = 0.0

    def add(self, value: float) -> None:
        if self.count == 0:
            self.mean = self.min = self.max = value
        else:
            alpha = max(1.0 / (self.count + 1), MAX_ALPHA)
            self.mean += alpha * (value - self.mean)
            self.min = min(self.min, value)
            self.max = max(self.max, value)
        self.count += 1

    def to_dict(self) -> dict[str, float | int]:
        return {"count": self.count, "mean": round(self.mean, 3), "min": round(self.min, 3), "max": round(self.max, 3)}

    @classmethod
    def from_dict(cls, data: dict) -> CopBin:
        return cls(int(data.get("count", 0)), float(data.get("mean", 0.0)), float(data.get("min", 0.0)), float(data.get("max", 0.0)))


@dataclass
class CopMap:
    bins: dict[int, CopBin] = field(default_factory=dict)

    @staticmethod
    def bin_of(outdoor_c: float) -> int:
        return int(outdoor_c // BIN_WIDTH_K)

    @staticmethod
    def bin_label(index: int) -> str:
        lo = index * BIN_WIDTH_K
        return f"{lo:+.0f}..{lo + BIN_WIDTH_K:+.0f}"

    def add(self, outdoor_c: float, cop: float) -> None:
        self.bins.setdefault(self.bin_of(outdoor_c), CopBin()).add(cop)

    def expected(self, outdoor_c: float, min_count: int = 5) -> float | None:
        """Expected COP at this outdoor temperature, from the bin or its neighbours."""
        idx = self.bin_of(outdoor_c)
        for candidate in (idx, idx - 1, idx + 1):
            b = self.bins.get(candidate)
            if b is not None and b.count >= min_count:
                return round(b.mean, 3)
        return None

    def bin_count(self, outdoor_c: float) -> int:
        b = self.bins.get(self.bin_of(outdoor_c))
        return 0 if b is None else b.count

    def calibration_factor(self, min_count: int = MIN_BIN_COUNT) -> float | None:
        """Measured / prior, weighted by samples over all bins with enough data."""
        num = den = 0.0
        for idx, b in self.bins.items():
            if b.count < min_count:
                continue
            centre = idx * BIN_WIDTH_K + BIN_WIDTH_K / 2.0
            num += b.count * b.mean / prior_cop(centre)
            den += b.count
        if den == 0:
            return None
        lo, hi = PRIOR_CALIBRATION_RANGE
        return round(min(max(num / den, lo), hi), 3)

    def estimate(self, outdoor_c: float) -> tuple[float, str]:
        """Expected COP and its basis: learned bin, calibrated prior, or default prior."""
        learned = self.expected(outdoor_c)
        if learned is not None:
            return learned, "learned"
        factor = self.calibration_factor()
        if factor is not None:
            return round(prior_cop(outdoor_c) * factor, 3), "prior_calibrated"
        return round(prior_cop(outdoor_c) * PRIOR_CALIBRATION_DEFAULT, 3), "prior"

    def to_dict(self) -> dict[str, dict]:
        return {self.bin_label(i): b.to_dict() for i, b in sorted(self.bins.items())}

    def to_storage(self) -> dict:
        return {"bins": {str(i): b.to_dict() for i, b in self.bins.items()}}

    @classmethod
    def from_storage(cls, data: dict | None) -> CopMap:
        cm = cls()
        for key, value in as_dict(as_dict(data).get("bins")).items():
            try:
                cm.bins[int(key)] = CopBin.from_dict(value)
            except (AttributeError, TypeError, ValueError):
                continue
        return cm


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


@dataclass
class CoolEpisode:
    """An ongoing cool-down observation for one room."""

    start_ts: float
    start_temp: float
    last_ts: float
    last_temp: float
    gradient_sum: float = 0.0
    samples: int = 0


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
            return self._close(room, ep, ts)
        if ep is None:
            self.episodes[room] = CoolEpisode(ts, temp, ts, temp, gradient, 1)
            return None
        if temp > ep.last_temp + 0.3:  # warmed up again: not a clean cool-down
            self.episodes[room] = CoolEpisode(ts, temp, ts, temp, gradient, 1)
            return None
        ep.last_ts, ep.last_temp = ts, temp
        ep.gradient_sum += gradient
        ep.samples += 1
        return None

    def _close(self, room: str, ep: CoolEpisode | None, ts: float) -> float | None:
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

    def drop_per_hour(self, room: str, room_temp: float, outdoor: float) -> float | None:
        """What the room loses in an hour at this gradient, in K."""
        k = self.rate(room)
        return None if k is None else round(k * max(room_temp - outdoor, 0.0), 2)

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
@dataclass
class Episode:
    """An ongoing heat-up observation for one room."""

    start_ts: float
    start_temp: float
    last_ts: float
    last_temp: float
    gradient_sum: float = 0.0  # sum of (room - outdoor) over the samples
    samples: int = 0


@dataclass
class HeatRateStats:
    """Median heat-up rate per room (K/h) from the last episodes."""

    rates: dict[str, list[float]] = field(default_factory=dict)
    # the mean indoor-outdoor gradient of each episode, in step with `rates`: a rate
    # measured in mild weather hides a small loss, one measured in frost a large one
    gradients: dict[str, list[float | None]] = field(default_factory=dict)
    episodes: dict[str, Episode] = field(default_factory=dict)

    def observe(
        self, room: str, ts: float, temp: float | None, heating: bool, window_open: bool, outdoor: float | None = None
    ) -> float | None:
        """Feed one sample. Returns a newly learned rate when an episode closes, else None."""
        ep = self.episodes.get(room)
        if temp is None or not heating or window_open:
            return self._close(room, ep, ts)
        gradient = None if outdoor is None else temp - outdoor
        if ep is None:
            self.episodes[room] = Episode(ts, temp, ts, temp, gradient or 0.0, 1 if gradient is not None else 0)
            return None
        if temp < ep.last_temp - 0.3:  # temperature fell: not a clean heat-up
            self.episodes[room] = Episode(ts, temp, ts, temp, gradient or 0.0, 1 if gradient is not None else 0)
            return None
        ep.last_ts, ep.last_temp = ts, temp
        if gradient is not None:
            ep.gradient_sum += gradient
            ep.samples += 1
        return None

    def _close(self, room: str, ep: Episode | None, ts: float) -> float | None:
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
        lst = self.rates.setdefault(room, [])
        lst.append(round(rate, 3))
        del lst[:-HEAT_RATE_KEEP]
        grad = self.gradients.setdefault(room, [])
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

    def rate(self, room: str) -> float | None:
        lst = self.rates.get(room)
        return round(median(lst), 3) if lst else None

    def heating_power_k_h(self, room: str, cool_k: float | None) -> float | None:
        """Heat-up rate the room would reach with no losses at all: rate + k * gradient.

        Unlike the measured rate this is a property of room and radiator rather than
        of the weather during the episode, so pre-heating can be planned in winter
        from episodes recorded in autumn. Needs the cool-down coefficient.
        """
        if cool_k is None:
            return None
        pairs = [
            (rate, grad)
            for rate, grad in zip(self.rates.get(room, []), self.gradients.get(room, []), strict=False)
            if grad is not None
        ]
        if not pairs:
            return None
        return round(median([rate + cool_k * grad for rate, grad in pairs]), 3)

    def to_dict(self) -> dict[str, dict]:
        return {
            room: {
                "median_k_h": self.rate(room),
                "episodes": len(lst),
                "mean_gradient_k": (
                    round(median([g for g in self.gradients.get(room, []) if g is not None]), 1)
                    if any(g is not None for g in self.gradients.get(room, []))
                    else None
                ),
            }
            for room, lst in self.rates.items()
        }

    def to_storage(self) -> dict:
        return {"rates": self.rates, "gradients": self.gradients}

    @classmethod
    def from_storage(cls, data: dict | None) -> HeatRateStats:
        hs = cls()
        data = as_dict(data)
        rates = as_dict(data.get("rates"))
        hs.rates = {str(k): as_floats(v)[-HEAT_RATE_KEEP:] for k, v in rates.items() if isinstance(v, list)}
        gradients = as_dict(data.get("gradients"))
        hs.gradients = {str(k): [as_float(x) for x in v][-HEAT_RATE_KEEP:] for k, v in gradients.items() if isinstance(v, list)}
        for room, rates in hs.rates.items():  # older stores have no gradients: pad them
            grads = hs.gradients.setdefault(room, [])
            hs.gradients[room] = ([None] * max(0, len(rates) - len(grads)) + grads)[-HEAT_RATE_KEEP:]
        return hs
