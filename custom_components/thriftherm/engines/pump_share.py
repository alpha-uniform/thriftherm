"""The boiler pump's electricity per kWh of boiler heat.

The pump runs while the boiler heats, and in a permanent pump mode for as long as heating is released:
33 W at the test installation, about 0.5 kWh a day for 16 kWh of heat (measured 2026-10-17 to 26). A heat
pump that takes over the heating saves that electricity too. With the pump's power set, its running time and
the gas for heating are summed, the older days fading with a time constant of four days, so that a new pump
setting counts within a week or two. Below a few kWh of heat there is nothing sound to divide by.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

FADE_S = 4 * 86400.0  # time constant of the fading
MIN_HEAT_KWH = 5.0  # the least boiler heat the ratio is taken from
# The most the ratio says. Measured 2026-10-17 to 26: 33 W, about 0.5 kWh of pump for 16 kWh of heat a day, 0.03;
# a permanent pump on such a day makes 0.05. While the heat pump heats, the boiler's heat runs low and a permanent
# pump, which goes on running whoever heats, divided by it, made 0.58 (review 2026-10-30): 17 cents more on each kWh
# of boiler heat, and the more the heat pump took over, the dearer the boiler looked. A higher least heat would only
# move that edge; the bound holds at any heat.
MAX_KWH_PER_KWH = 0.06
# Cycles come a minute apart. A longer gap (restart, outage) counts neither pump nor heat: the pump's time in it is
# unknown, and its heat against a guess biased the share.
MAX_GAP_S = 600.0


@dataclass(frozen=True)
class PumpShare:
    pump_kwh: float = 0.0  # the pump's electricity, faded
    heat_kwh: float = 0.0  # the boiler's heat in the same time, faded

    def to_storage(self) -> dict[str, Any]:
        return {"pump_kwh": self.pump_kwh, "heat_kwh": self.heat_kwh}

    @classmethod
    def from_storage(cls, raw: Any) -> PumpShare:
        if not isinstance(raw, dict):
            return cls()
        values = [raw.get("pump_kwh"), raw.get("heat_kwh")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in values):
            return cls()
        return cls(float(values[0]), float(values[1]))


def add(share: PumpShare, pump_on_s: float, power_w: float, heat_kwh: float, dt_s: float) -> PumpShare:
    """`dt_s` more seconds: the pump ran `pump_on_s` of them at `power_w`, the boiler gave `heat_kwh`."""
    keep = math.exp(-max(dt_s, 0.0) / FADE_S)
    pump = max(pump_on_s, 0.0) * max(power_w, 0.0) / 3_600_000.0
    return PumpShare(share.pump_kwh * keep + pump, share.heat_kwh * keep + max(heat_kwh, 0.0))


def count(share: PumpShare, gap_s: float, pump_on: bool, power_w: float, heat_kwh: float) -> PumpShare:
    """One cycle `gap_s` after the last: the pump ran through it or not, the boiler gave `heat_kwh` for heating."""
    if gap_s <= 0.0 or gap_s > MAX_GAP_S:
        return share
    return add(share, gap_s if pump_on else 0.0, power_w, heat_kwh, gap_s)


def kwh_per_kwh(share: PumpShare) -> float | None:
    """The pump's kWh per kWh of boiler heat, None without enough heat to tell."""
    if share.heat_kwh < MIN_HEAT_KWH:
        return None
    return min(share.pump_kwh / share.heat_kwh, MAX_KWH_PER_KWH)
