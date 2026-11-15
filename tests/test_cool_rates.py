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


def test_a_cool_down_ends_at_its_lowest_point_whatever_the_room_does_after():
    # 21 -> 19 °C in eight hours at 4 °C outside: 0.25 K/h at a gradient of 16 K, k about 0.0156 per hour
    night = [round(21.0 - 2.0 * i / 480, 1) for i in range(481)]
    sun = [round(19.0 + 1.5 * i / 180, 1) for i in range(1, 181)]  # then the morning sun, slowly: no jump to restart on
    stats = CoolRateStats()
    learned = [stats.observe("wohnzimmer", i * 60.0, temp, 4.0, False, False) for i, temp in enumerate(night + sun)]
    assert [k for k in learned if k is not None] == [pytest.approx(0.0156, abs=0.001)]  # was 0.0029 with the rise counted in

    held = CoolRateStats()  # three hours down to the setback, then held there for six
    down = [round(21.0 - 1.5 * i / 180, 1) for i in range(181)]
    assert _cool(held, "bad", 0.0, down + [19.5] * 360, 4.0, step=60.0) == pytest.approx(1.5 / 3.0 / 16.25, abs=0.001)


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
    from custom_components.thriftherm.engines.learning import HEAT_BY_BOILER, HeatRateStats

    stats = HeatRateStats()
    # 19 -> 21 °C in two hours at 5 °C outside: net 1.0 K/h at a mean gradient of 15 K
    for i, temp in enumerate([19.0, 19.5, 20.0, 20.5, 21.0]):
        stats.observe("wohnzimmer", i * 1800.0, temp, source=HEAT_BY_BOILER, window_open=False, outdoor=5.0)
    learned = stats.observe("wohnzimmer", 5 * 1800.0, 21.0, source=None, window_open=False, outdoor=5.0)
    assert learned == pytest.approx(1.0, abs=0.05)
    assert stats.to_dict()["wohnzimmer"][HEAT_BY_BOILER]["mean_gradient_k"] == pytest.approx(15.0, abs=0.5)

    # with the room's cool-down coefficient the loss can be added back in
    assert stats.heating_power_k_h("wohnzimmer", HEAT_BY_BOILER, 0.02) == pytest.approx(1.3, abs=0.05)
    assert stats.heating_power_k_h("wohnzimmer", HEAT_BY_BOILER, None) is None  # unknown before winter
    assert stats.heating_power_k_h("kuche", HEAT_BY_BOILER, 0.02) is None


def test_heat_up_rates_from_an_older_store_still_load():
    from custom_components.thriftherm.engines.learning import HEAT_BY_BOILER, HeatRateStats

    old = HeatRateStats.from_storage({"rates": {"bad": [0.8, 0.9]}})  # written before gradients existed
    assert old.rate("bad", HEAT_BY_BOILER) == 0.85
    assert old.gradients["bad"][HEAT_BY_BOILER] == [None, None]
    assert old.heating_power_k_h("bad", HEAT_BY_BOILER, 0.02) is None  # nothing to correct with


# ---------------------------------------------------------------------------
# Stage 2 of cooling (docs/PHASE12_COOLING.md §20.3): how fast the air conditioner cools a room at its least speed
# ---------------------------------------------------------------------------
from custom_components.thriftherm.engines.learning import AcCoolRateStats, ac_cool_learnable  # noqa: E402

MIN = 60.0


def _ac(stats: AcCoolRateStats, temps, step: float = 5 * MIN, start: float = 0.0, outdoor: float = 30.0) -> list:
    """Samples every `step` while the unit cools at its least speed; returns what each one learned."""
    return [stats.observe("schlafzimmer", start + i * step, t, outdoor, True) for i, t in enumerate(temps)]


def test_a_fall_at_the_least_speed_is_learned_as_its_rate_where_the_room_rises_again():
    stats = AcCoolRateStats()
    learned = _ac(stats, [26.0, 25.9, 25.8, 25.7, 25.6, 25.5, 25.45, 25.4, 25.5, 25.6])  # low after 35 min, then +0.2
    assert [x for x in learned if x is not None] == [pytest.approx(0.6 / (35 / 60))]


def test_the_rate_of_test_47_of_the_concept():
    stats = AcCoolRateStats()
    learned = _ac(stats, [26.0, 25.925, 25.85, 25.775, 25.7, 25.625, 25.55, 25.475, 25.4, 25.6], step=5 * MIN)
    assert [x for x in learned if x is not None] == [pytest.approx(0.9)]  # 0.6 K in 40 min


def test_an_episode_ends_where_the_conditions_end_and_counts_with_0_3_k_in_30_min():
    stats = AcCoolRateStats()
    _ac(stats, [26.0, 25.95, 25.9, 25.85, 25.8, 25.75, 25.7])  # 0.3 K in 30 min
    assert stats.observe("schlafzimmer", 35 * MIN, 25.7, 30.0, False) == pytest.approx(0.6)


def test_too_short_or_too_little_is_no_rate():
    stats = AcCoolRateStats()
    _ac(stats, [26.0, 25.9, 25.8, 25.7])  # 15 min
    assert stats.observe("schlafzimmer", 20 * MIN, 25.7, 30.0, False) is None
    _ac(stats, [26.0, 25.95, 25.9, 25.9, 25.85, 25.85, 25.8, 25.8], start=HOUR)  # 0.2 K in 35 min
    assert stats.observe("schlafzimmer", HOUR + 40 * MIN, 25.8, 30.0, False) is None


def test_a_rate_beyond_three_degrees_an_hour_is_discarded():
    stats = AcCoolRateStats()
    _ac(stats, [26.0 - 0.375 * i for i in range(8)])  # 4.5 K/h
    assert stats.observe("schlafzimmer", 40 * MIN, 23.0, 30.0, False) is None and stats.rate("schlafzimmer") is None


def test_the_rate_is_used_from_three_episodes_as_their_median():
    stats = AcCoolRateStats(rates={"schlafzimmer": [0.8, 1.2]})
    assert stats.rate("schlafzimmer") is None  # two: the default holds
    stats.rates["schlafzimmer"].append(1.0)
    assert stats.rate("schlafzimmer") == pytest.approx(1.0)


def test_the_difference_indoors_outdoors_is_kept_alongside():
    stats = AcCoolRateStats()
    _ac(stats, [26.0, 25.9, 25.8, 25.7, 25.6, 25.5, 25.4], outdoor=32.0)
    stats.observe("schlafzimmer", 35 * MIN, 25.4, 32.0, False)
    assert stats.gradients["schlafzimmer"] == [pytest.approx(25.7 - 32.0, abs=0.1)]


def test_forgetting_and_the_store():
    stats = AcCoolRateStats(rates={"a": [1.0, 1.1, 0.9], "b": [0.5]}, gradients={"a": [-5.0, None, -4.0], "b": [None]})
    again = AcCoolRateStats.from_storage(stats.to_storage())
    assert again.rates == stats.rates and again.gradients == stats.gradients
    stats.forget(["a"])
    assert "a" not in stats.rates and "b" in stats.rates
    stats.forget(None)
    assert stats.rates == {}
    damaged = AcCoolRateStats.from_storage({"rates": {"a": [9.0, "x", 1.0]}, "gradients": "y"})
    assert damaged.rates == {"a": [1.0]}


@pytest.mark.parametrize(
    ("changes", "learnable"),
    [
        ({}, True),
        ({"own_cool_run": False}, False),
        ({"resting": True}, False),
        ({"lifted": True}, False),
        ({"compressor_ran_s": 9 * MIN}, False),
        ({"compressor_ran_s": None}, False),
        ({"override": True}, False),
        ({"window_open": True}, False),
        ({"cheap_power": True}, False),
    ],
    ids=["all_fine", "not_ours", "resting", "lifted", "compressor_new", "unknown_run", "override", "window", "cheap_power"],
)
def test_only_an_own_run_at_the_least_speed_teaches(changes, learnable):
    base = dict(
        own_cool_run=True, resting=False, lifted=False, compressor_ran_s=10 * MIN, override=False, window_open=False,
        cheap_power=False,
    )
    assert ac_cool_learnable(**{**base, **changes}) is learnable


def test_how_long_the_compressor_has_run():
    from dataclasses import replace

    from custom_components.thriftherm.engines.heat_pump_control import ControlMemory, ran_s
    from custom_components.thriftherm.models import HeatPumpSample

    from .conftest import heat_pump_state

    def sample(ts, hz):
        return HeatPumpSample(ts, hz, None, None, None, None, None)

    now = 10_000.0
    history = (sample(now - 900, 0.0), sample(now - 720, 24.0), sample(now, 24.0))
    unit = replace(heat_pump_state(), compressor_hz=24.0, history=history)
    assert ran_s(unit, ControlMemory(), now) == pytest.approx(720.0)
    assert ran_s(replace(unit, compressor_hz=0.0), ControlMemory(), now) == 0.0
    # without a frequency: the run of ours under way, as long as its phase
    blind = replace(unit, compressor_hz=None, history=())
    assert ran_s(blind, ControlMemory(phase_since_ts=now - 660), now) == pytest.approx(660.0)
    assert ran_s(blind, ControlMemory(), now) is None
