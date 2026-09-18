import math

import pytest

from custom_components.thriftherm.engines import psychrometrics as psy


def test_saturation_pressure_known_points():
    # 20 °C -> 2339 Pa, 0 °C -> 611 Pa (VDI/ASHRAE tables)
    assert psy.saturation_vapour_pressure_pa(20.0) == pytest.approx(2339, rel=0.01)
    assert psy.saturation_vapour_pressure_pa(0.0) == pytest.approx(611.2, rel=0.001)


def test_dew_point_matches_table():
    # 22 °C / 50 % rF -> Taupunkt ≈ 11.1 °C
    assert psy.dew_point_c(22.0, 50.0) == pytest.approx(11.1, abs=0.2)
    assert psy.dew_point_c(22.0, 100.0) == pytest.approx(22.0, abs=0.05)
    assert psy.dew_point_c(22.0, 0.0) is None


def test_absolute_humidity_reference():
    # 20 °C / 50 % -> 8.65 g/m³
    assert psy.absolute_humidity_g_m3(20.0, 50.0) == pytest.approx(8.65, abs=0.1)


def test_humidity_ratio_and_enthalpy():
    x = psy.humidity_ratio(20.0, 50.0)
    assert x == pytest.approx(0.00726, rel=0.02)
    h = psy.specific_enthalpy_j_kg(20.0, x)
    # Mollier: ≈ 38.5 kJ/kg
    assert h / 1000 == pytest.approx(38.5, abs=0.6)


def test_air_heating_power_sensible_case():
    # 500 m³/h heated from 20 °C to 38 °C at constant humidity ratio ≈ 3.0 kW
    p = psy.air_heating_power_w(20.0, 45.0, 38.0, None, 500.0)
    assert p == pytest.approx(3000, rel=0.06)
    assert p > 0


def test_air_heating_power_zero_when_no_delta():
    assert psy.air_heating_power_w(20.0, 45.0, 20.0, None, 500.0) == pytest.approx(0.0, abs=1e-6)


def test_density_decreases_with_temperature():
    assert psy.moist_air_density_kg_m3(0.0, 50.0) > psy.moist_air_density_kg_m3(30.0, 50.0)
    assert math.isclose(psy.moist_air_density_kg_m3(20.0, 0.0), 1.204, rel_tol=0.01)
