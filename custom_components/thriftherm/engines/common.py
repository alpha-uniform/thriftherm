"""Small helpers shared by the engines."""

from __future__ import annotations

from collections.abc import Iterable


def round_half(value: float) -> float:
    """Round to the 0.5 K steps boilers, heat pumps and thermostats accept."""
    return round(value * 2.0) / 2.0


def as_float(value: object) -> float | None:
    """A stored or reported value as a number; anything that is not one becomes None."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def as_dict(value: object) -> dict:
    """A stored mapping, or an empty one when the store holds something else."""
    return value if isinstance(value, dict) else {}


def as_floats(values: Iterable[object] | None) -> list[float]:
    """The numbers in a stored list, skipping entries that are not numbers."""
    if not isinstance(values, (list, tuple)):
        return []
    return [v for item in values if (v := as_float(item)) is not None]
