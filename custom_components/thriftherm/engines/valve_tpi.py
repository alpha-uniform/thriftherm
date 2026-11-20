"""Way d (docs/PHASE13_VALVES.md §3.4): Thriftherm's own controller sets the opening of a valve (a preview).

Time proportional and integral (TPI) as Versatile Thermostat does it: the opening is a share of the difference to the
target plus a share of the difference to outside. The formula and the starting coefficients (0.6, 0.01, a cycle of five
minutes, ten points before a change) are Versatile Thermostat's (MIT, © 2022 Jean-Marc Collin; see
THIRD_PARTY_NOTICES.md), the code is written for Thriftherm.

The valve keeps the room's target as its setpoint, so its own control closes it once its sensor reaches the target, also
while Home Assistant stands: the opening only limits it.
"""

from __future__ import annotations

COEF_INT = 0.6  # per kelvin below the target
COEF_EXT = 0.01  # per kelvin of the target above outside
CYCLE_S = 300.0  # a new opening at most this often
MIN_CHANGE_PCT = 10.0  # and only when it moved this far, or to an end


def opening(
    target: float, room_c: float, outdoor_c: float | None, wants_heat: bool = True,
    coef_int: float = COEF_INT, coef_ext: float = COEF_EXT,
) -> float:
    """The opening in percent for the room at `room_c`; 0 for a room that does not want heat (§3.4)."""
    if not wants_heat:
        return 0.0
    share = coef_int * (target - room_c)
    if outdoor_c is not None:
        share += coef_ext * (target - outdoor_c)
    return round(min(max(share, 0.0), 1.0) * 100.0, 1)


def due(
    pct: float, last_pct: float | None, last_ts: float | None, now_ts: float, least_pct: float | None = None
) -> float | None:
    """The opening to write now, None when nothing is due: a change of ten points or to an end, five minutes apart."""
    value = float(round(max(pct, least_pct or 0.0)))
    if last_pct is None or last_ts is None:
        return value
    if value == last_pct or now_ts - last_ts < CYCLE_S:
        return None
    to_end = value in (round(least_pct or 0.0), 100.0)
    return value if to_end or abs(value - last_pct) >= MIN_CHANGE_PCT else None
