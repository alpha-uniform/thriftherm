"""System types: own boiler, district heating with an allocation key, none."""

import pytest

from custom_components.thriftherm.const import SYSTEM_DISTRICT, SYSTEM_GAS, SYSTEM_NONE
from custom_components.thriftherm.engines import economics as ec
from custom_components.thriftherm.models import Prices

GAS = Prices(
    electricity_eur_kwh=0.306,
    gas_eur_kwh=0.0889,
    boiler_efficiency=0.84,
    gas_calorific_kwh_m3=11.45,
    gas_z_factor=0.9381,
    system_type=SYSTEM_GAS,
)


def district(consumption_pct: float = 70.0, ownership_pct: float = 0.0, price: float = 0.12) -> Prices:
    return Prices(**{**GAS.__dict__, "system_type": SYSTEM_DISTRICT, "heat_eur_kwh": price,
                     "heat_consumption_share_pct": consumption_pct, "heat_ownership_share_pct": ownership_pct})


def test_gas_keeps_the_previous_arithmetic():
    assert ec.heat_cost_per_kwh_thermal(GAS) == pytest.approx(0.0889 / 0.84)
    assert ec.break_even_cop(GAS) == pytest.approx(0.306 * 0.84 / 0.0889)


def test_allocation_factor_counts_the_ownership_share_of_the_area_part():
    # 70 % by consumption, 5 % of the building's floor area
    assert ec.allocation_factor(district(70.0, 5.0)) == pytest.approx(0.715)
    # billed purely by consumption: a saved kWh saves the full price
    assert ec.allocation_factor(district(100.0, 5.0)) == pytest.approx(1.0)
    # billed purely by area: only the own ownership fraction reacts at all
    assert ec.allocation_factor(district(0.0, 5.0)) == pytest.approx(0.05)


def test_allocation_key_raises_the_break_even_cop():
    """Saving one kWh saves only the consumption share, so the heat pump must do better."""
    full = district(100.0, 0.0)
    split = district(70.0, 5.0)
    assert ec.heat_cost_per_kwh_thermal(split) == pytest.approx(0.12 * 0.715)
    assert ec.break_even_cop(split) == pytest.approx(0.306 / (0.12 * 0.715))
    assert ec.break_even_cop(split) > ec.break_even_cop(full)
    assert ec.break_even_cop(split) / ec.break_even_cop(full) == pytest.approx(1 / 0.715)


def test_district_shares_are_clamped_not_trusted_blindly():
    assert ec.allocation_factor(district(-20.0, 0.0)) == pytest.approx(0.0)
    assert ec.allocation_factor(district(150.0, 0.0)) == pytest.approx(1.0)
    assert ec.allocation_factor(district(70.0, 150.0)) == pytest.approx(1.0)


def test_district_without_a_price_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        ec.heat_cost_per_kwh_thermal(district(70.0, 0.0, price=0.0))
    with pytest.raises(ValueError):
        ec.heat_cost_per_kwh_thermal(district(0.0, 0.0, price=0.12))


def test_without_a_central_source_the_heat_pump_always_wins():
    none = Prices(**{**GAS.__dict__, "system_type": SYSTEM_NONE})
    assert ec.break_even_cop(none) == 0.0
    result = ec.evaluate(none, cop=1.2, margin_on=0.1, margin_off=0.1, current=None)
    assert result.cheaper_source == "midea"


def test_evaluate_uses_the_district_price_for_the_comparison():
    result = ec.evaluate(district(70.0, 5.0), cop=4.0, margin_on=0.1, margin_off=0.1, current=None)
    assert result.gas_cost_per_kwh_thermal == pytest.approx(0.12 * 0.715)
    assert result.heat_pump_cost_per_kwh_thermal == pytest.approx(0.306 / 4.0)
    # 0.0765 €/kWh vs 0.0858 €/kWh: the heat pump is cheaper, but only just
    assert result.cheaper_source == "midea"
    assert result.saving_pct == pytest.approx((0.0858 - 0.0765) / 0.0858 * 100, abs=0.5)
