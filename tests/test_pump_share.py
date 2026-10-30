"""The boiler pump's electricity per kWh of boiler heat, learned from the pump's running time and the gas for heating."""

from __future__ import annotations

import pytest

from custom_components.thriftherm.engines import pump_share
from custom_components.thriftherm.engines.pump_share import PumpShare

DAY = 86400.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _days(share: PumpShare, days: int, pump_h_a_day: float, heat_kwh_a_day: float, power_w: float = 33.0) -> PumpShare:
    """Hour by hour: the pump runs its share of each hour, the boiler gives its share of the heat."""
    for _ in range(days * 24):
        pump_on_s, heat_kwh = pump_h_a_day / 24 * 3600, heat_kwh_a_day / 24
        share = pump_share.add(share, pump_on_s=pump_on_s, power_w=power_w, heat_kwh=heat_kwh, dt_s=3600)
    return share


def test_the_ratio_is_the_pump_energy_over_the_heat_of_the_last_days():
    share = _days(PumpShare(), 7, pump_h_a_day=15.0, heat_kwh_a_day=16.0)
    assert pump_share.kwh_per_kwh(share) == pytest.approx(15 * 0.033 / 16, rel=0.01)


def test_without_enough_heat_there_is_no_ratio():
    """Days of the heat pump alone: the pump ran a little for hot water, the boiler heated nothing to divide by."""
    share = _days(PumpShare(), 1, pump_h_a_day=1.0, heat_kwh_a_day=1.0)
    assert pump_share.kwh_per_kwh(share) is None


def test_old_days_fade_so_a_new_pump_setting_counts_within_a_week_or_two():
    before = _days(PumpShare(), 14, pump_h_a_day=24.0, heat_kwh_a_day=16.0)  # permanent
    after = _days(before, 14, pump_h_a_day=6.0, heat_kwh_a_day=16.0)  # with the burner only
    assert pump_share.kwh_per_kwh(after) == pytest.approx(6 * 0.033 / 16, rel=0.25)


def test_the_share_survives_a_restart():
    share = _days(PumpShare(), 3, pump_h_a_day=15.0, heat_kwh_a_day=16.0)
    assert PumpShare.from_storage(share.to_storage()) == share
    assert PumpShare.from_storage({"pump_kwh": "x", "heat_kwh": None}) == PumpShare()
    assert PumpShare.from_storage(None) == PumpShare()


async def test_with_the_pump_power_set_the_learned_share_raises_the_boiler_heat_price(hass) -> None:
    from custom_components.thriftherm.const import CONF_BOILER_PUMP_POWER_W

    from .test_integration import _setup

    plain = await _setup(hass)
    base = float(hass.states.get("sensor.thriftherm_heat_cost_per_kwh_boiler").state)
    assert await hass.config_entries.async_remove(plain.entry_id)

    entry = await _setup(hass, options={CONF_BOILER_PUMP_POWER_W: 33})
    coordinator = entry.runtime_data
    coordinator.pump_share = PumpShare(pump_kwh=0.5, heat_kwh=16.0)  # as learned over some days
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    cost = float(hass.states.get("sensor.thriftherm_heat_cost_per_kwh_boiler").state)
    assert cost == pytest.approx(base + 0.5 / 16.0 * 0.306, abs=0.002)

    await coordinator.async_save_store(force=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.pump_share.heat_kwh > 15.0  # kept through a restart


def test_with_little_boiler_heat_a_permanent_pump_does_not_price_the_boiler_out():
    """Review 2026-10-30: the heat pump heated, the boiler gave little more than the least heat to divide by, and a pump
    in permanent mode made 3.2 kWh over 5.5 kWh, 17 cents on each kWh of boiler heat."""
    assert pump_share.kwh_per_kwh(PumpShare(pump_kwh=3.2, heat_kwh=5.5)) == pump_share.MAX_KWH_PER_KWH
    assert pump_share.MAX_KWH_PER_KWH < 0.1


def test_a_gap_between_cycles_counts_neither_pump_nor_heat():
    """Review 2026-10-30: ten minutes of pump against the heat of a whole outage biased the share low."""
    share = PumpShare(pump_kwh=0.5, heat_kwh=16.0)
    assert pump_share.count(share, gap_s=3 * 3600, pump_on=True, power_w=33.0, heat_kwh=4.0) == share
    counted = pump_share.count(share, gap_s=60, pump_on=True, power_w=33.0, heat_kwh=0.1)
    assert counted == pump_share.add(share, 60, 33.0, 0.1, 60)
    stopped = pump_share.count(share, gap_s=60, pump_on=False, power_w=33.0, heat_kwh=0.1)
    assert stopped == pump_share.add(share, 0.0, 33.0, 0.1, 60)


def test_the_pump_counts_for_heating_in_its_overrun_but_not_for_hot_water():
    from dataclasses import replace

    from custom_components.thriftherm.engines import boiler as boiler_engine

    from .conftest import boiler_state

    for status, counts in (("on", True), ("overrun", True), ("hwc", False), ("off", False)):
        assert boiler_engine.pump_heating(boiler_state(pump=status)) is counts, status
    # the pump itself where it is configured: it runs while Status01 says off (measured 2026-09-18)
    assert boiler_engine.pump_heating(replace(boiler_state(pump="off"), pump_running=True)) is True
    assert boiler_engine.pump_heating(replace(boiler_state(pump="hwc"), pump_running=True)) is False
    assert boiler_engine.pump_heating(replace(boiler_state(pump="on", hwc_mode="on"), pump_running=True)) is False
