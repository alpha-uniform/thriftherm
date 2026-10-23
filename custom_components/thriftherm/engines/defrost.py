"""Defrost and anti-icing detection for the Midea heat pump.

Midea AC LAN exposes no defrost flag, so defrost is inferred from patterns
Two outcomes:

* ``cycle``      – a normal reverse-cycle defrost: COP samples are discarded,
                   no error is raised.
* ``persistent`` – frequent cycles or a sustained iced/inefficient state: the
                   Midea is locked out for heating for ``icing_block_min``.

Indicators
  D1 reversal   : outdoor coil rises > coil_rise_k within 2 min while the
                  compressor runs and the indoor coil falls
  D2 cold outlet: heating_stable, outlet − intake < 1 K for > 3 min
  D3 iced coil  : outdoor coil < outdoor air − depression_k for > 10 min — only
                  counts together with D2: at full load in frost an evaporator
                  8–12 K below the air is normal operation, and so is the power
                  drop of D4 when the inverter slows down
  D4 power drop : electrical power falls > 40 % within 2 min, compressor on
  D5 environment: outdoor < 5 °C and RH > 80 % (shown only, it enters no decision)
  D6 reverse    : compressor runs and the indoor coil is more than 5 K colder than the
                  intake air, twice in a row – the circuit runs backwards

A cycle is counted once, however long it lasts.

D1, D4 and D6 are read in a heating run state only: a compressor starting to cool or to
dry shows the same coil temperatures and would be counted as a defrost.
"""

from __future__ import annotations

from ..const import HEAT_PUMP_HEATING_STABLE
from ..models import DefrostResult, HeatPumpSample, HeatPumpState, Parameters
from .heat_pump import HEATING_STATES

CYCLE_WINDOW_S = 90 * 60.0
CYCLE_MIN_GAP_S = 300.0  # one defrost shows up over several update cycles; it is counted once
REVERSAL_WINDOW_S = 120.0
COLD_OUTLET_S = 180.0
ICED_COIL_S = 600.0
POWER_DROP_FRACTION = 0.4
POWER_DROP_WINDOW_S = 120.0
INEFFICIENT_COP_S = 30 * 60.0
REVERSE_BELOW_INTAKE_K = 5.0
REVERSE_COIL_MAX_C = 5.0  # without an intake reading: a coil this cold heats no room
ECONOMIC_LOCKOUT = "sustained_inefficiency"  # the one lockout cause that is about cost, not about the unit


def _samples_within(history: tuple[HeatPumpSample, ...], now_ts: float, window_s: float) -> list[HeatPumpSample]:
    return [s for s in history if now_ts - s.ts <= window_s]


def _reversal(history: tuple[HeatPumpSample, ...], now_ts: float, rise_k: float) -> bool:
    recent = _samples_within(history, now_ts, REVERSAL_WINDOW_S)
    if len(recent) < 2:
        return False
    first, last = recent[0], recent[-1]
    if None in (first.outdoor_coil_temp, last.outdoor_coil_temp, last.compressor_hz):
        return False
    if last.compressor_hz <= 0:
        return False
    coil_rise = last.outdoor_coil_temp - first.outdoor_coil_temp
    indoor_falls = (
        first.indoor_coil_temp is not None
        and last.indoor_coil_temp is not None
        and last.indoor_coil_temp < first.indoor_coil_temp - 1.0
    )
    return coil_rise > rise_k and indoor_falls


def _reverse_cycle(history: tuple[HeatPumpSample, ...], now_ts: float) -> bool:
    """The indoor coil colder than the air it is to heat, the compressor running: a defrost.

    Seen 2026-10-13 on a Midea PortaSplit: 89 Hz, indoor fan stopped, indoor coil -15 °C for a
    quarter of an hour, while the outdoor coil rose by 6 K in five minutes, too slowly for D1.
    Two samples in a row, so that one odd reading does not count.
    """
    recent = _samples_within(history, now_ts, REVERSAL_WINDOW_S)[-2:]
    if len(recent) < 2:
        return False

    def backwards(s: HeatPumpSample) -> bool:
        if not s.compressor_hz or s.compressor_hz <= 0 or s.indoor_coil_temp is None:
            return False
        limit = s.intake_temp - REVERSE_BELOW_INTAKE_K if s.intake_temp is not None else REVERSE_COIL_MAX_C
        return s.indoor_coil_temp < limit

    return all(backwards(s) for s in recent)


def _cold_outlet(history: tuple[HeatPumpSample, ...], now_ts: float, run_state: str) -> bool:
    if run_state != HEAT_PUMP_HEATING_STABLE:
        return False
    recent = _samples_within(history, now_ts, COLD_OUTLET_S)
    if len(recent) < 3:
        return False
    deltas = [s.outlet_temp - s.intake_temp for s in recent if s.outlet_temp is not None and s.intake_temp is not None]
    if len(deltas) < 3:
        return False
    return max(deltas) < 1.0 and (recent[-1].ts - recent[0].ts) >= COLD_OUTLET_S * 0.8


def _compressor_on(s: HeatPumpSample) -> bool:
    return (s.compressor_hz or 0) > 0


def _iced_coil(history: tuple[HeatPumpSample, ...], now_ts: float, outdoor_air: float | None, depression_k: float) -> bool:
    if outdoor_air is None:
        return False
    recent = _samples_within(history, now_ts, ICED_COIL_S)
    if len(recent) < 3 or (recent[-1].ts - recent[0].ts) < ICED_COIL_S * 0.8:
        return False
    # Once the compressor has stopped, the cold coil of the run that ended is no ice: the coil warms up now.
    if not _compressor_on(recent[-1]):
        return False
    coils = [s.outdoor_coil_temp for s in recent if s.outdoor_coil_temp is not None and _compressor_on(s)]
    if len(coils) < 3:
        return False
    return max(coils) < outdoor_air - depression_k


def _power_drop(history: tuple[HeatPumpSample, ...], now_ts: float) -> bool:
    recent = _samples_within(history, now_ts, POWER_DROP_WINDOW_S)
    # The plug and the unit's frequency update at different moments, so the sample at a stop often still says
    # the compressor runs while the plug reads the fan alone. Judged without the stopped sample after it, that
    # one stayed the last power with the compressor on, and an ordinary stop looked like a drop for two minutes.
    if not recent or not _compressor_on(recent[-1]):
        return False
    powers = [(s.ts, s.power_w) for s in recent if s.power_w is not None and _compressor_on(s)]
    if len(powers) < 2:
        return False
    peak = max(p for _, p in powers)
    last = powers[-1][1]
    return peak > 300.0 and last < peak * (1.0 - POWER_DROP_FRACTION)


def evaluate(
    heat_pump: HeatPumpState,
    run_state: str,
    outdoor_air_c: float | None,
    outdoor_rh_pct: float | None,
    now_ts: float,
    params: Parameters,
    cycle_starts: tuple[float, ...],
    inefficient_since_ts: float | None,
    existing_block_until_ts: float | None,
    economic_lockout: bool = True,
    counted: bool = False,
    lockout_cause: str | None = None,
) -> DefrostResult:
    """Classify the current state. `cycle_starts` are timestamps of previously detected cycles.

    `counted`: the cycle under way is among them already. A defrost of a quarter of an hour
    counted every five minutes made three, and with one more within 90 minutes a lockout.

    `economic_lockout`: a measured COP that stays below break-even locks the unit out. False
    where the heat pump is the only source allowed to heat: a dear heat still beats none.

    `lockout_cause`: what started the lockout under way (`existing_block_until_ts`), None when
    that is not known.
    """
    indicators: list[str] = []
    hist = heat_pump.history
    heating = run_state in HEATING_STATES

    if heating and _reversal(hist, now_ts, params.defrost_coil_rise_k):
        indicators.append("D1_coil_reversal")
    if _cold_outlet(hist, now_ts, run_state):
        indicators.append("D2_cold_outlet")
    if _iced_coil(hist, now_ts, outdoor_air_c, params.icing_coil_depression_k):
        indicators.append("D3_iced_coil")
    if heating and _power_drop(hist, now_ts):
        indicators.append("D4_power_drop")
    if heating and _reverse_cycle(hist, now_ts):
        indicators.append("D6_reverse_cycle")
    if outdoor_air_c is not None and outdoor_rh_pct is not None and outdoor_air_c < 5.0 and outdoor_rh_pct > 80.0:
        indicators.append("D5_icing_conditions")

    cycle_now = (
        "D1_coil_reversal" in indicators
        or "D6_reverse_cycle" in indicators
        or ("D2_cold_outlet" in indicators and "D4_power_drop" in indicators)
    )
    recent_cycles = [t for t in cycle_starts if now_ts - t <= CYCLE_WINDOW_S]
    new_cycle = cycle_now and not counted and (not recent_cycles or now_ts - recent_cycles[-1] > CYCLE_MIN_GAP_S)
    cycles = len(recent_cycles) + (1 if new_cycle else 0)

    block_reason: str | None = None
    block_until = existing_block_until_ts if existing_block_until_ts and existing_block_until_ts > now_ts else None

    persistent = False
    # The economic cause first: where the unit is to be protected as well, that is the cause named.
    # A lockout for dear heat alone ends once only the heat pump may heat, one that protects the unit does not.
    if economic_lockout and inefficient_since_ts is not None and now_ts - inefficient_since_ts >= INEFFICIENT_COP_S:
        persistent = True
        block_reason = ECONOMIC_LOCKOUT
    if cycles > params.defrost_max_cycles_90min:
        persistent = True
        block_reason = "frequent_defrost_cycles"
    # Iced only once the heat output collapses as well. A power drop is no sign of it: the PortaSplit on 2026-10-20
    # slowed from 39 to 24 Hz, 630 to 348 W, and went on heating. In frost that locked it out as iced.
    if "D3_iced_coil" in indicators and "D2_cold_outlet" in indicators:
        persistent = True
        block_reason = "outdoor_coil_iced"

    if persistent:
        block_until = now_ts + params.icing_block_min * 60.0
    elif block_until is not None:
        # A lockout for dear heat is no icing lockout: it keeps its own cause while it runs. Shown as
        # one from its second cycle on, the unit stopped by it looked iced (review 2026-10-14).
        block_reason = ECONOMIC_LOCKOUT if lockout_cause == ECONOMIC_LOCKOUT else "icing_lockout_active"

    kind = "persistent" if persistent else ("cycle" if cycle_now else "none")
    return DefrostResult(
        suspected=cycle_now or persistent,
        kind=kind,
        indicators=tuple(indicators),
        cycles_last_90min=cycles,
        block_reason=block_reason,
        block_until_ts=block_until,
    )
