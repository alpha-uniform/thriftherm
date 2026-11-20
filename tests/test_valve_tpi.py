"""Way d (docs/PHASE13_VALVES.md §3.4): Thriftherm's own controller sets the valve's opening (TPI, a preview).

The formula and the starting coefficients are those of Versatile Thermostat (MIT, © 2022 Jean-Marc Collin), written anew
(THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

import pytest

from custom_components.thriftherm.engines import valve_tpi as tpi

NOW = 2_000_000.0
MIN = 60.0


@pytest.mark.parametrize(
    ("room_c", "outdoor_c", "pct"),
    [
        (20.0, 0.0, 81.0),  # 0.6 · 1 K + 0.01 · 21 K = 0.81
        (21.0, 0.0, 21.0),  # at the target only the loss to outside
        (19.0, -10.0, 100.0),  # held at 100
        (22.0, 10.0, 0.0),  # above the target: closed
    ],
)
def test_the_opening_is_the_difference_to_the_target_and_to_outside(room_c, outdoor_c, pct):
    assert tpi.opening(21.0, room_c, outdoor_c) == pct


def test_without_an_outdoor_temperature_the_room_alone_counts():
    assert tpi.opening(21.0, 20.0, None) == 60.0


def test_a_room_that_does_not_want_heat_gets_its_valve_closed():
    """§3.4: without a call and without heating along no room draws heat it did not ask for."""
    assert tpi.opening(21.0, 20.0, 0.0, wants_heat=False) == 0.0


def test_the_opening_goes_out_on_a_change_of_ten_points_or_to_an_end_at_most_every_five_minutes():
    assert tpi.due(40.0, None, None, NOW) == 40.0
    assert tpi.due(45.0, 40.0, NOW - 10 * MIN, NOW) is None  # less than ten points
    assert tpi.due(55.0, 40.0, NOW - 2 * MIN, NOW) is None  # not five minutes yet
    assert tpi.due(55.0, 40.0, NOW - 5 * MIN, NOW) == 55.0
    assert tpi.due(0.0, 4.0, NOW - 5 * MIN, NOW) == 0.0  # closed, however small the step
    assert tpi.due(100.0, 95.0, NOW - 5 * MIN, NOW) == 100.0


def test_a_valve_with_a_least_opening_never_gets_less():
    """Versatile Thermostat's note on the Sonoff TRVZB: below 3 % it loses its calibration."""
    assert tpi.due(0.0, None, None, NOW, least_pct=3.0) == 3.0
    assert tpi.due(0.0, 3.0, NOW - 10 * MIN, NOW, least_pct=3.0) is None


def test_the_opening_is_written_in_whole_percent():
    assert tpi.due(81.4, None, None, NOW) == 81.0
