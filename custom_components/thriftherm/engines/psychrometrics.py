"""Moist-air property functions (Magnus formula, ASHRAE style enthalpy).

All temperatures in °C, relative humidity in %, pressure in Pa.
"""

from __future__ import annotations

import math

R_DRY_AIR = 287.05  # J/(kg·K)
CP_DRY_AIR = 1006.0  # J/(kg·K)
CP_VAPOUR = 1860.0  # J/(kg·K)
H_VAPORISATION = 2_501_000.0  # J/kg at 0 °C
STANDARD_PRESSURE_PA = 101_325.0


def saturation_vapour_pressure_pa(t_c: float) -> float:
    """Magnus formula over water (Sonntag 1990 coefficients)."""
    return 611.2 * math.exp(17.62 * t_c / (243.12 + t_c))


def vapour_pressure_pa(t_c: float, rh_pct: float) -> float:
    rh = min(max(rh_pct, 0.0), 100.0) / 100.0
    return rh * saturation_vapour_pressure_pa(t_c)


def dew_point_c(t_c: float, rh_pct: float) -> float | None:
    if rh_pct <= 0:
        return None
    pv = vapour_pressure_pa(t_c, rh_pct)
    ln_ratio = math.log(pv / 611.2)
    return 243.12 * ln_ratio / (17.62 - ln_ratio)


def absolute_humidity_g_m3(t_c: float, rh_pct: float) -> float:
    """Water vapour mass per volume of moist air in g/m³."""
    pv = vapour_pressure_pa(t_c, rh_pct)
    # ideal gas for water vapour: rho = pv / (Rv * T), Rv = 461.5 J/(kg K)
    return pv / (461.5 * (t_c + 273.15)) * 1000.0


def humidity_ratio(t_c: float, rh_pct: float, p_pa: float = STANDARD_PRESSURE_PA) -> float:
    """Mass of water per mass of dry air (kg/kg)."""
    pv = vapour_pressure_pa(t_c, rh_pct)
    pv = min(pv, p_pa * 0.99)
    return 0.622 * pv / (p_pa - pv)


def specific_enthalpy_j_kg(t_c: float, x: float) -> float:
    """Enthalpy of moist air per kg dry air."""
    return CP_DRY_AIR * t_c + x * (H_VAPORISATION + CP_VAPOUR * t_c)


def moist_air_density_kg_m3(t_c: float, rh_pct: float, p_pa: float = STANDARD_PRESSURE_PA) -> float:
    pv = vapour_pressure_pa(t_c, rh_pct)
    t_k = t_c + 273.15
    return (p_pa - pv) / (R_DRY_AIR * t_k) + pv / (461.5 * t_k)


def air_heating_power_w(
    intake_t: float,
    intake_rh: float,
    outlet_t: float,
    outlet_rh: float | None,
    airflow_m3h: float,
    p_pa: float = STANDARD_PRESSURE_PA,
) -> float:
    """Sensible+latent heating power delivered to an air stream.

    Uses the enthalpy difference per kg of dry air and the intake-side density
    (the airflow is measured/estimated on the intake side). If the outlet
    humidity is unknown, the humidity ratio is assumed constant across the
    unit, which holds for a heat pump in heating mode.
    """
    x_in = humidity_ratio(intake_t, intake_rh, p_pa)
    x_out = humidity_ratio(outlet_t, outlet_rh, p_pa) if outlet_rh is not None else x_in
    rho_in = moist_air_density_kg_m3(intake_t, intake_rh, p_pa)
    mass_flow_moist = airflow_m3h / 3600.0 * rho_in
    mass_flow_dry = mass_flow_moist / (1.0 + x_in)
    dh = specific_enthalpy_j_kg(outlet_t, x_out) - specific_enthalpy_j_kg(intake_t, x_in)
    return mass_flow_dry * dh
