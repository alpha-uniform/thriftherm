"""Heat pump observations that span update cycles.

Defrost cycles, the icing lockout, the inefficiency timer and the COP samples
all need memory between cycles. The engines they feed are pure; this class
holds that memory so the coordinator only has to call it.
"""

from __future__ import annotations

from ..const import HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP
from ..models import CopResult, DefrostResult, HeatingSnapshot, HeatPumpResult, Parameters
from . import cop as cop_engine
from . import defrost as defrost_engine

COP_SAMPLE_RETENTION_S = 1800.0
COP_MAP_MIN_SAMPLES_FOR_ENTRY = 12  # only feed the map with a settled, filtered COP
COP_MAP_ENTRY_INTERVAL_S = 300.0
DEFROST_CYCLE_MIN_GAP_S = 300.0  # one defrost shows up over several cycles; count it once


class HeatPumpTracker:
    def __init__(self) -> None:
        self.defrost_cycle_starts: list[float] = []
        self.inefficient_since: float | None = None
        self.block_until: float | None = None
        self.cop_samples: list[tuple[float, float]] = []
        self.last_map_entry_ts = 0.0

    def reset(self) -> None:
        self.__init__()

    def defrost(self, snapshot: HeatingSnapshot, run_state: str, params: Parameters, now_ts: float) -> tuple[DefrostResult, bool]:
        """Classify defrost/icing. The flag tells whether the lockout changed (worth saving)."""
        res = defrost_engine.evaluate(
            snapshot.heat_pump, run_state, snapshot.outdoor_temp.value_or_none, snapshot.outdoor_rh.value_or_none,
            now_ts, params, tuple(self.defrost_cycle_starts), self.inefficient_since, self.block_until,
        )
        if res.kind == "cycle" and (not self.defrost_cycle_starts or now_ts - self.defrost_cycle_starts[-1] > DEFROST_CYCLE_MIN_GAP_S):
            self.defrost_cycle_starts.append(now_ts)
        self.defrost_cycle_starts = [t for t in self.defrost_cycle_starts if now_ts - t <= defrost_engine.CYCLE_WINDOW_S]
        changed = res.block_until_ts != self.block_until
        self.block_until = res.block_until_ts
        return res, changed

    def cop(self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, defrost_res: DefrostResult, params: Parameters, now_ts: float) -> CopResult:
        instant = cop_engine.instant(snapshot.heat_pump, heat_pump_res.run_state, heat_pump_res.electrical_power_w, params)
        gates = list(instant.gate_reasons)
        if defrost_res.suspected:
            gates.append(f"defrost_{defrost_res.kind}")
        if instant.raw is not None and not defrost_res.suspected:
            self.cop_samples.append((now_ts, instant.raw))
        if heat_pump_res.run_state not in (HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP):
            # samples from a finished run must not blend into the next one
            self.cop_samples = []
        self.cop_samples = [(ts, v) for ts, v in self.cop_samples if now_ts - ts <= COP_SAMPLE_RETENTION_S]
        value, n = cop_engine.aggregate(self.cop_samples, now_ts, params)
        if value is None and not gates:
            gates.append(f"insufficient_samples:{n}/{params.cop_min_samples}")
        return CopResult(
            value=value, raw=instant.raw, thermal_power_w=instant.thermal_power_w, electrical_power_w=heat_pump_res.electrical_power_w,
            airflow_m3h=instant.airflow_m3h, samples=n, gate_reasons=tuple(gates),
        )

    def track_efficiency(self, cop_value: float | None, break_even_cop: float, margin_off: float, now_ts: float) -> None:
        """Start the inefficiency timer while the measured COP stays below break-even."""
        if cop_value is not None and cop_value < break_even_cop * (1.0 - margin_off):
            self.inefficient_since = self.inefficient_since or now_ts
        else:
            self.inefficient_since = None

    def map_entry_due(self, cop_res: CopResult, now_ts: float) -> bool:
        """A settled COP may enter the map at most every few minutes."""
        if cop_res.value is None or cop_res.samples < COP_MAP_MIN_SAMPLES_FOR_ENTRY or now_ts - self.last_map_entry_ts < COP_MAP_ENTRY_INTERVAL_S:
            return False
        self.last_map_entry_ts = now_ts
        return True
