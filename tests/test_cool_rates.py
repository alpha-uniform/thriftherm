"""Learning how fast a room loses heat.

The coefficient is the basis for judging a setback temperature later: only a room
that really cools down saves anything by being set back further.
"""

from __future__ import annotations

import pytest

from custom_components.thriftherm.engines.learning import CoolRateStats

HOUR = 3600.0


def _cool(stats: CoolRateStats, room: str, start_ts: float, temps, outdoor: float, step: float = HOUR) -> float | None:
    """Feed a cool-down, then end it by starting to heat; returns what was learned."""
    for i, temp in enumerate(temps):
        stats.observe(room, start_ts + i * step, temp, outdoor, heating=False, window_open=False)
    return stats.observe(room, start_ts + len(temps) * step, temps[-1], outdoor, heating=True, window_open=False)


def test_a_clean_cool_down_is_learned():
    stats = CoolRateStats()
    # 21.0 -> 20.0 °C in three hours at 5 °C outside: gradient about 15.5 K
    learned = _cool(stats, "wohnzimmer", 1_000_000.0, [21.0, 20.7, 20.3, 20.0], 5.0)
    assert learned is not None
    assert stats.rate("wohnzimmer") == pytest.approx(0.0215, abs=0.002)
    # what that means in practice: at a 16 K gradient the room loses about a third of a degree per hour
    assert stats.drop_per_hour("wohnzimmer", 21.0, 5.0) == pytest.approx(0.34, abs=0.05)


def test_heating_window_and_small_gradient_are_not_cool_downs():
    stats = CoolRateStats()
    assert _cool(stats, "bad", 1_000_000.0, [21.0, 20.5, 20.0], 5.0) is not None

    warm = CoolRateStats()  # only 3 K warmer than outside: the drop says nothing
    assert _cool(warm, "bad", 1_000_000.0, [21.0, 20.5, 20.0], 18.0) is None

    window = CoolRateStats()
    for i, temp in enumerate([21.0, 20.0, 19.0]):
        window.observe("bad", 1_000_000.0 + i * HOUR, temp, 5.0, heating=False, window_open=True)
    assert window.observe("bad", 1_000_000.0 + 3 * HOUR, 19.0, 5.0, heating=True, window_open=False) is None


def test_a_room_that_warms_up_again_restarts_the_episode():
    stats = CoolRateStats()
    stats.observe("kuche", 0.0, 21.0, 5.0, heating=False, window_open=False)
    stats.observe("kuche", HOUR, 20.5, 5.0, heating=False, window_open=False)
    stats.observe("kuche", 2 * HOUR, 21.2, 5.0, heating=False, window_open=False)  # sun, cooking, a guest
    assert stats.episodes["kuche"].start_temp == 21.2
    assert stats.observe("kuche", 3 * HOUR, 21.2, 5.0, heating=True, window_open=False) is None


def test_too_short_or_too_small_is_ignored():
    stats = CoolRateStats()
    assert _cool(stats, "bad", 0.0, [21.0, 20.95], 5.0, step=600.0) is None  # ten minutes, 0.05 K
    assert stats.rate("bad") is None


def test_storage_round_trip_and_forget():
    stats = CoolRateStats()
    _cool(stats, "bad", 0.0, [21.0, 20.7, 20.3, 20.0], 5.0)
    back = CoolRateStats.from_storage(stats.to_storage())
    assert back.rate("bad") == stats.rate("bad")
    assert back.to_dict()["bad"]["episodes"] == 1

    back.forget(["bad"])
    assert back.rate("bad") is None
    assert CoolRateStats.from_storage(None).rates == {}


# ------------------------------------------------------------------ heating power
def test_the_heat_up_rate_carries_its_gradient_and_yields_a_heating_power():
    from custom_components.thriftherm.engines.learning import HeatRateStats

    stats = HeatRateStats()
    # 19 -> 21 °C in two hours at 5 °C outside: net 1.0 K/h at a mean gradient of 15 K
    for i, temp in enumerate([19.0, 19.5, 20.0, 20.5, 21.0]):
        stats.observe("wohnzimmer", i * 1800.0, temp, heating=True, window_open=False, outdoor=5.0)
    learned = stats.observe("wohnzimmer", 5 * 1800.0, 21.0, heating=False, window_open=False, outdoor=5.0)
    assert learned == pytest.approx(1.0, abs=0.05)
    assert stats.to_dict()["wohnzimmer"]["mean_gradient_k"] == pytest.approx(15.0, abs=0.5)

    # with the room's cool-down coefficient the loss can be added back in
    assert stats.heating_power_k_h("wohnzimmer", 0.02) == pytest.approx(1.3, abs=0.05)
    assert stats.heating_power_k_h("wohnzimmer", None) is None  # unknown before winter
    assert stats.heating_power_k_h("kuche", 0.02) is None


def test_heat_up_rates_from_an_older_store_still_load():
    from custom_components.thriftherm.engines.learning import HeatRateStats

    old = HeatRateStats.from_storage({"rates": {"bad": [0.8, 0.9]}})  # written before gradients existed
    assert old.rate("bad") == 0.85
    assert old.gradients["bad"] == [None, None]
    assert old.heating_power_k_h("bad", 0.02) is None  # nothing to correct with
