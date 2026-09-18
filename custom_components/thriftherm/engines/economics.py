"""Cost comparison between the central heat source and the heat pump.

    c_heat  = p_gas / eta_boiler          [€/kWh thermal]   own gas or oil boiler
            = p_heat * allocation_factor  [€/kWh thermal]   district heating
    c_midea = p_el  / COP                 [€/kWh thermal]
    Midea cheaper  <=>  COP > p_el / c_heat  =: COP_break_even

Boiler efficiency is defined relative to the gross calorific value (Hs),
because the gas price is billed per kWh Hs.

For district heating the German Heizkostenverordnung splits the building bill
into a consumption share (typically 70 %) and a floor-area share (the rest).
Only the consumption share follows this flat's own meter; of the area share the
flat carries its ownership fraction (Miteigentumsanteil). One saved kWh
therefore saves less than the headline price, which *raises* the COP the heat
pump must reach — ignoring this would make the heat pump look better than it is.
"""

from __future__ import annotations

from ..const import SYSTEM_DISTRICT, SYSTEM_NONE
from ..models import EconomicsResult, Prices


def gas_energy_kwh(volume_m3: float, z_factor: float, calorific_kwh_m3: float) -> float:
    return volume_m3 * z_factor * calorific_kwh_m3


def gas_power_w(flow_m3h: float, z_factor: float, calorific_kwh_m3: float) -> float:
    return gas_energy_kwh(flow_m3h, z_factor, calorific_kwh_m3) * 1000.0


def allocation_factor(prices: Prices) -> float:
    """Share of the district heat price that one saved kWh actually saves.

    consumption share + area share * own ownership fraction, both as fractions.
    A building billed purely by consumption gives 1.0; a 70/30 split in a flat
    holding 5 % of the floor area gives 0.715.
    """
    consumption = min(max(prices.heat_consumption_share_pct, 0.0), 100.0) / 100.0
    ownership = min(max(prices.heat_ownership_share_pct, 0.0), 100.0) / 100.0
    return consumption + (1.0 - consumption) * ownership


def heat_cost_per_kwh_thermal(prices: Prices) -> float:
    """Marginal cost of one kWh of heat from the central heat source."""
    if prices.system_type == SYSTEM_DISTRICT:
        factor = allocation_factor(prices)
        if prices.heat_eur_kwh <= 0 or factor <= 0:
            raise ValueError("district heat price and allocation factor must be > 0")
        return prices.heat_eur_kwh * factor
    if prices.boiler_efficiency <= 0:
        raise ValueError("boiler efficiency must be > 0")
    return prices.gas_eur_kwh / prices.boiler_efficiency


def gas_cost_per_kwh_thermal(prices: Prices) -> float:
    """Backwards-compatible alias kept for callers that predate district heating."""
    return heat_cost_per_kwh_thermal(prices)


def heat_pump_cost_per_kwh_thermal(prices: Prices, cop: float | None) -> float | None:
    if cop is None or cop <= 0:
        return None
    return prices.electricity_eur_kwh / cop


def break_even_cop(prices: Prices) -> float:
    """COP at which the heat pump costs the same as the central heat source."""
    if prices.system_type == SYSTEM_NONE:
        # nothing to compare against: the heat pump is the only source
        return 0.0
    if prices.system_type != SYSTEM_DISTRICT and prices.gas_eur_kwh <= 0:
        raise ValueError("gas price must be > 0")
    return prices.electricity_eur_kwh / heat_cost_per_kwh_thermal(prices)


def cheaper_source(
    cop: float | None,
    be_cop: float,
    margin_on: float,
    margin_off: float,
    current: str | None = None,
) -> str:
    """Decide the cheaper source with hysteresis around the break-even COP.

    `current` is the source currently favoured (None when unknown). Switching
    to Midea requires COP above break-even plus `margin_on`; falling back to
    the boiler requires COP below break-even minus `margin_off`.
    """
    if cop is None or cop <= 0:
        return "unknown"
    if current == "midea":
        return "midea" if cop >= be_cop * (1.0 - margin_off) else "boiler"
    return "midea" if cop >= be_cop * (1.0 + margin_on) else "boiler"


def evaluate(prices: Prices, cop: float | None, margin_on: float, margin_off: float, current: str | None) -> EconomicsResult:
    c_gas = heat_cost_per_kwh_thermal(prices)
    c_heat_pump = heat_pump_cost_per_kwh_thermal(prices, cop)
    be = break_even_cop(prices)
    if prices.system_type == SYSTEM_NONE:
        # no alternative source, so the heat pump is always the one to use
        cheaper = "midea"
    else:
        cheaper = cheaper_source(cop, be, margin_on, margin_off, current)
    saving = None
    if c_heat_pump is not None:
        # positive = Midea cheaper by this many percent relative to gas
        saving = (c_gas - c_heat_pump) / c_gas * 100.0
    return EconomicsResult(
        gas_cost_per_kwh_thermal=c_gas,
        heat_pump_cost_per_kwh_thermal=c_heat_pump,
        break_even_cop=be,
        cheaper_source=cheaper,
        saving_pct=saving,
    )
