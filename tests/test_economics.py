import pytest

from custom_components.thriftherm.engines import economics as eco
from custom_components.thriftherm.models import Prices

from .conftest import PRICES


def test_break_even_formula():
    # 0.306 * 0.84 / 0.0889 = 2.891
    assert eco.break_even_cop(PRICES) == pytest.approx(2.891, abs=0.005)
    p88 = Prices(0.306, 0.0889, 0.88, 11.45, 0.9381)
    assert eco.break_even_cop(p88) == pytest.approx(3.03, abs=0.01)


def test_cost_symmetry_at_break_even():
    be = eco.break_even_cop(PRICES)
    assert eco.heat_pump_cost_per_kwh_thermal(PRICES, be) == pytest.approx(eco.gas_cost_per_kwh_thermal(PRICES))


def test_gas_energy_conversion():
    assert eco.gas_energy_kwh(1.0, 0.9381, 11.45) == pytest.approx(10.741, abs=0.001)
    assert eco.gas_power_w(1.2, 0.9381, 11.45) == pytest.approx(12889, rel=0.001)


def test_cheaper_source_hysteresis():
    be = 3.0
    # from boiler, need +10 %
    assert eco.cheaper_source(3.2, be, 0.10, 0.05, current="boiler") == "boiler"
    assert eco.cheaper_source(3.31, be, 0.10, 0.05, current="boiler") == "midea"
    # from midea, only fall back below -5 %
    assert eco.cheaper_source(2.9, be, 0.10, 0.05, current="midea") == "midea"
    assert eco.cheaper_source(2.84, be, 0.10, 0.05, current="midea") == "boiler"
    assert eco.cheaper_source(None, be, 0.10, 0.05, current="midea") == "unknown"
    assert eco.cheaper_source(0.0, be, 0.10, 0.05) == "unknown"


def test_evaluate_reports_saving():
    res = eco.evaluate(PRICES, 3.5, 0.10, 0.05, None)
    assert res.cheaper_source == "midea"
    assert res.saving_pct is not None and res.saving_pct > 0
    res2 = eco.evaluate(PRICES, None, 0.10, 0.05, None)
    assert res2.cheaper_source == "unknown"
    assert res2.heat_pump_cost_per_kwh_thermal is None
    assert res2.saving_pct is None


def test_invalid_prices_raise():
    with pytest.raises(ValueError):
        eco.break_even_cop(Prices(0.3, 0.0, 0.84, 11.0, 0.94))
    with pytest.raises(ValueError):
        eco.gas_cost_per_kwh_thermal(Prices(0.3, 0.09, 0.0, 11.0, 0.94))
