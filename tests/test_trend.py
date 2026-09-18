"""Temperature trend against a quantised sensor.

A Zigbee sensor reporting in 0.1 K steps turns a smooth drift into a staircase.
Regressing over a short window then measures the staircase, not the drift.
"""

import pytest

from custom_components.thriftherm.engines.room import trend_k_per_h


def staircase(drift_k_per_h: float, minutes: int, resolution: float = 0.1, start: float = 22.0):
    """One sample per minute of a sensor that quantises a steady drift."""
    out = []
    for i in range(minutes):
        ts = 1_000_000.0 + i * 60.0
        true_value = start + drift_k_per_h * (i / 60.0)
        out.append((ts, round(true_value / resolution) * resolution))
    return tuple(out)


def _sweep(history, window_s: float, min_span_s: float):
    """Trend as computed minute by minute over the second half of the history."""
    values = []
    for i in range(len(history) // 2, len(history)):
        now = history[i][0]
        values.append(trend_k_per_h(history[: i + 1], now, window_s=window_s, min_span_s=min_span_s))
    return [v for v in values if v is not None]


def test_short_window_turns_quantisation_into_a_sawtooth():
    """The old 30-minute window: the answer swings around the truth instead of tracking it.

    A steady 0.2 K/h drift reads as anything between 0.0 and ~0.29 K/h depending
    only on where the 0.1 K steps sit inside the window. Real sensors are worse
    still, because they also skip minutes.
    """
    history = staircase(drift_k_per_h=0.2, minutes=240)
    values = _sweep(history, window_s=1800.0, min_span_s=600.0)
    assert min(values) <= 0.0  # flat stretches read as exactly zero
    assert max(values) > 0.28  # ~45 % above the real drift when a step passes through
    assert max(values) - min(values) > 0.28


def test_ninety_minute_window_tracks_the_real_drift():
    history = staircase(drift_k_per_h=0.2, minutes=240)
    values = _sweep(history, window_s=5400.0, min_span_s=1800.0)
    assert all(0.1 < v < 0.3 for v in values), (min(values), max(values))
    assert max(values) - min(values) < 0.15


def test_a_genuinely_flat_room_still_reads_zero():
    history = staircase(drift_k_per_h=0.0, minutes=240)
    values = _sweep(history, window_s=5400.0, min_span_s=1800.0)
    assert all(v == pytest.approx(0.0, abs=1e-9) for v in values)


def test_a_fast_rise_is_not_flattened_away():
    history = staircase(drift_k_per_h=2.0, minutes=240)
    values = _sweep(history, window_s=5400.0, min_span_s=1800.0)
    assert all(1.8 < v < 2.2 for v in values), (min(values), max(values))


def test_too_short_a_span_reports_nothing_rather_than_a_guess():
    history = staircase(drift_k_per_h=0.2, minutes=240)[:20]  # 20 minutes
    assert trend_k_per_h(history, history[-1][0], window_s=5400.0, min_span_s=1800.0) is None
