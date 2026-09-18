"""Advisory heat-source selection.

Produces the heat-source recommendation the arbiter would act on, together
with human-readable reasons. Hysteresis, minimum run times and probing runs
are handled by the controllers; the advisory keeps the priority
order Safety → Frost → Device protection → DHW → Mode → Comfort → Economics.
"""

from __future__ import annotations

from ..const import (
    CTRL_ACTIVE,
    HEAT_PUMP_PLAN_HEAT,
    MODE_AWAY,
    MODE_BOILER_ONLY,
    MODE_HEAT_PUMP_ONLY,
    MODE_OFF,
    SAFETY_FALLBACK,
    SOURCE_BOILER,
    SOURCE_BOTH,
    SOURCE_HEAT_PUMP,
    SOURCE_NONE,
)
from ..models import (
    Advice,
    BoilerResult,
    CopResult,
    EconomicsResult,
    HeatingSnapshot,
    HeatPumpResult,
    RoomResult,
    Reason,
    SafetyResult,
    reason,
)

DEMAND_THRESHOLD = 0.05


def boiler_source(advice_source: str, heat_pump_plan: str | None, heat_pump_control: str, boiler_control: str) -> str:
    """The source the boiler should act on.

    The advice is purely economic. If it names the heat pump but the heat pump
    is not going to heat — its plan is off, hands off or waiting, or it may only
    plan while the boiler really acts — blocking the boiler would leave the rooms
    with no heat from either source. Cost must never override comfort, so the
    boiler takes over in that case.
    """
    if advice_source != SOURCE_HEAT_PUMP:
        return advice_source
    delivers = heat_pump_plan == HEAT_PUMP_PLAN_HEAT and (heat_pump_control == CTRL_ACTIVE or boiler_control != CTRL_ACTIVE)
    return SOURCE_HEAT_PUMP if delivers else SOURCE_BOILER


def advise(
    snapshot: HeatingSnapshot,
    rooms: dict[str, RoomResult],
    econ: EconomicsResult,
    cop: CopResult,
    heat_pump: HeatPumpResult,
    boiler: BoilerResult,
    safety: SafetyResult,
    heat_pump_block_reason: str | None = None,
    decision_cop: float | None = None,
    decision_cop_basis: str | None = None,
) -> Advice:
    """`decision_cop` is the COP the economics used: measured while the heat pump runs,
    otherwise the learned or prior estimate (`decision_cop_basis`)."""
    reasons: list[Reason] = []
    blocked: dict[str, str] = {}
    mode = snapshot.mode
    if heat_pump_block_reason and snapshot.heat_pump.configured:
        blocked["midea"] = heat_pump_block_reason
        reasons.append(reason("heat_pump_blocked", reason=heat_pump_block_reason))

    heat_pump_rooms = {k: r for k, r in rooms.items() if snapshot.rooms[k].config.served_by_heat_pump}
    boiler_only_rooms = {k: r for k, r in rooms.items() if k not in heat_pump_rooms}
    # only radiators that may open can take the boiler's heat (window shut, thermostat not off)
    boiler_only_radiator = {k: r for k, r in boiler_only_rooms.items() if r.heating_allowed}

    def demand_of(group: dict[str, RoomResult]) -> float:
        return max((r.demand or 0.0) for r in group.values()) if group else 0.0

    # radiator demand of Midea rooms (excludes rooms only drying via Midea with the window open)
    heat_pump_rooms_radiator = {k: r for k, r in heat_pump_rooms.items() if r.heating_allowed}
    cop_used = decision_cop if decision_cop is not None else cop.value
    cop_basis = "measured" if decision_cop is None or decision_cop_basis is None else decision_cop_basis

    def room_line(r: RoomResult) -> Reason:
        return reason("room_below_target", room=r.key, temp_c=r.temperature, target_c=r.target)

    # 1. Safety fallback
    if safety.state == SAFETY_FALLBACK:
        reasons.append(reason("safety_fallback"))
        src = SOURCE_BOILER if boiler.available else SOURCE_NONE
        return Advice(source=src, reasons=tuple(reasons), blocked={"midea": "safety_fallback"})

    # 2. Frost protection overrides mode off
    if safety.frost_rooms:
        reasons.append(reason("frost_protection", rooms=tuple(safety.frost_rooms)))

    # 3. Device protection / user modes on the Midea (only when the add-on is used)
    has_heat_pump = snapshot.heat_pump.configured
    if not has_heat_pump:
        blocked["midea"] = "not_configured"
    elif not heat_pump.heating_available:
        blocked["midea"] = heat_pump.run_state
        reasons.append(reason("heat_pump_unavailable", state=heat_pump.run_state))
    out_t = snapshot.outdoor_temp.value_or_none
    if has_heat_pump and out_t is not None and out_t < snapshot.params.heat_pump_min_outdoor_temp:
        blocked["midea"] = "outdoor_too_cold"
        reasons.append(reason("heat_pump_outdoor_too_cold", outdoor_c=out_t, limit_c=snapshot.params.heat_pump_min_outdoor_temp))

    # 4. DHW
    if boiler.hwc_active:
        reasons.append(reason("hot_water_priority"))

    # 5. Modes
    if mode == MODE_OFF and not safety.frost_rooms:
        reasons.append(reason("mode_off"))
        return Advice(source=SOURCE_NONE, reasons=tuple(reasons), blocked=blocked)

    heat_pump_demand = demand_of(heat_pump_rooms)
    boiler_demand = demand_of(boiler_only_radiator)
    any_frost = bool(safety.frost_rooms)

    if mode == MODE_BOILER_ONLY:
        blocked.setdefault("midea", "mode_boiler_only")
        need = demand_of(heat_pump_rooms_radiator) > DEMAND_THRESHOLD or boiler_demand > DEMAND_THRESHOLD or any_frost
        reasons.append(reason("mode_boiler_only"))
        return Advice(source=SOURCE_BOILER if need else SOURCE_NONE, reasons=tuple(reasons), blocked=blocked)

    heat_pump_wanted = False
    if heat_pump_demand > DEMAND_THRESHOLD and "midea" not in blocked:
        if mode == MODE_HEAT_PUMP_ONLY:
            heat_pump_wanted = True
            reasons.append(reason("mode_heat_pump_only"))
        elif econ.cheaper_source == SOURCE_HEAT_PUMP:
            heat_pump_wanted = True
            reasons.append(reason("cop_above_break_even", basis=cop_basis, cop=cop_used, break_even=econ.break_even_cop))
            reasons.append(
                reason(
                    "heat_pump_cheaper",
                    heat_pump_eur=econ.heat_pump_cost_per_kwh_thermal,
                    central_eur=econ.gas_cost_per_kwh_thermal,
                    saving_pct=econ.saving_pct,
                )
            )
        elif econ.cheaper_source == SOURCE_BOILER:
            reasons.append(reason("cop_below_break_even", basis=cop_basis, cop=cop_used, break_even=econ.break_even_cop))
        else:
            reasons.append(reason("cop_unknown", gates=tuple(cop.gate_reasons[:3])))
    elif heat_pump_demand > DEMAND_THRESHOLD and has_heat_pump:
        reasons.append(reason("heat_pump_rooms_blocked"))

    for r in heat_pump_rooms.values():
        if (r.demand or 0.0) > DEMAND_THRESHOLD:
            reasons.append(room_line(r))
        elif r.window_open:
            reasons.append(reason("room_window_open", room=r.key))

    boiler_wanted = boiler_demand > DEMAND_THRESHOLD or any_frost
    if mode == MODE_HEAT_PUMP_ONLY:
        boiler_wanted = any_frost
    if demand_of(heat_pump_rooms_radiator) > DEMAND_THRESHOLD and not heat_pump_wanted and mode != MODE_HEAT_PUMP_ONLY:
        boiler_wanted = True
    for r in boiler_only_rooms.values():
        if r.heating_allowed and (r.demand or 0.0) > DEMAND_THRESHOLD:
            reasons.append(room_line(r))
        elif r.window_open:
            reasons.append(reason("room_window_open", room=r.key))

    if mode == MODE_AWAY:
        reasons.append(reason("mode_away"))

    if boiler_wanted and not boiler.available:
        reasons.append(reason("boiler_data_unavailable"))

    if heat_pump_wanted and boiler_wanted:
        src = SOURCE_BOTH
    elif heat_pump_wanted:
        src = SOURCE_HEAT_PUMP
    elif boiler_wanted:
        src = SOURCE_BOILER
    else:
        src = SOURCE_NONE
        reasons.append(reason("no_demand"))

    return Advice(source=src, reasons=tuple(reasons), blocked=blocked)
