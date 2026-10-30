"""COP maps measured on real units, shipped for those who cannot measure their own.

Measuring the COP needs a smart plug and sensors at the unit's intake and outlet. Without them
Thriftherm decides "heat pump or boiler" on a general starting curve. A map measured on the same
model is closer to what that unit does; it is offered in the heat pump form and used only where
the installation has not measured a bin of its own (`learning.CopMap.estimate`).

Each map: outdoor-temperature bin (index = outdoor // 2 °C, as `CopMap.bin_of`) -> (mean COP, samples).
"""

from __future__ import annotations

from collections.abc import Mapping

PROFILE_NONE = "none"

# Midea PortaSplit, one unit, heating a flat through two hoses, first week of the heating season
# (2026-10-11 to 2026-10-30): intake and outlet air temperature and humidity, airflow from the fan
# speed, power from a smart plug. Steady runs only.
MIDEA_PORTASPLIT: dict[int, tuple[float, int]] = {
    1: (3.59, 126),  # +2..+4 °C
    2: (3.57, 72),  # +4..+6
    3: (3.77, 64),  # +6..+8
    4: (3.83, 68),  # +8..+10
    5: (4.44, 60),  # +10..+12
    6: (4.42, 46),  # +12..+14
    7: (3.96, 13),  # +14..+16
}

PROFILES: Mapping[str, Mapping[int, tuple[float, int]]] = {"midea_portasplit": MIDEA_PORTASPLIT}
PROFILE_KEYS = (PROFILE_NONE, *PROFILES)


def profile(key: str | None) -> Mapping[int, tuple[float, int]] | None:
    """The shipped map of a model, None for none or an unknown key."""
    return PROFILES.get(key or PROFILE_NONE)
