"""Heat pump observations that span update cycles.

Defrost cycles, the icing lockout, the inefficiency timer and the COP samples
all need memory between cycles. The engines they feed are pure; this class
holds that memory so the coordinator only has to call it.
"""

from __future__ import annotations

from dataclasses import replace

from ..const import HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP, MODE_HEAT_PUMP_ONLY
from ..models import CopResult, DefrostResult, HeatingSnapshot, HeatPumpResult, Parameters
from . import cop as cop_engine
from . import defrost as defrost_engine
from .heat_pump import HEATING_STATES as _HEATING
from .heat_pump_control import SETTLED_RUN_S

COP_SAMPLE_RETENTION_S = 1800.0
COP_MAP_MIN_SAMPLES_FOR_ENTRY = 12  # only feed the map with a settled, filtered COP
COP_MAP_ENTRY_INTERVAL_S = 300.0
# While its cause lasts, a lockout is renewed every cycle, and the store is written whenever its end
# moves: the whole store once a minute. The end follows in steps of this instead.
LOCKOUT_RENEW_STEP_S = 600.0
# A unit that reaches its setpoint switches its compressor on and off by itself (measured
# 2026-10-11, 8 °C outside, setpoint 22 °C: ten minutes on, ten off). No run is long enough for a
# settled sample then, so heat and electricity are added up from one compressor start to the next.
CYCLE_MIN_S = 600.0
CYCLE_MAX_S = 4 * 3600.0
CYCLE_MAX_GAP_S = 180.0  # a longer hole between two samples spoils the sum
CYCLE_MIN_ENERGY_WH = 20.0
CYCLE_COP_FRESH_S = 3600.0  # how long the last cycle's COP stands for the unit


class HeatPumpTracker:
    def __init__(self) -> None:
        self.defrost_cycle_starts: list[float] = []
        self.defrost_running = False  # the cycle under way is counted already (not stored)
        self.inefficient_since: float | None = None
        self.block_until: float | None = None
        self.block_reason: str | None = None  # what started or renewed the lockout under way; None: none, or not known
        self.cop_samples: list[tuple[float, float]] = []
        self.cop_settled = False  # the COP last reported came from settled samples, not from a whole cycle
        self.last_map_entry_ts = 0.0
        # the on/off cycle being added up, and the COP of the last whole one
        self.cycle_start_ts: float | None = None
        self.cycle_heat_wh = 0.0
        self.cycle_electricity_wh = 0.0
        self.cycle_spoiled = False
        self.cycle_last: tuple[float, float | None, float | None, bool] | None = None  # ts, heat W, electricity W, compressor on
        self.cycle_cop: tuple[float, float] | None = None  # (closed at, COP)
        self.cycle_cop_new: float | None = None  # not yet taken into the map

    def reset(self) -> None:
        """Forget what was learned; an icing lockout and the defrost count protect the unit and stay."""
        self.cop_samples = []
        self.cop_settled = False
        self.last_map_entry_ts = 0.0
        self.inefficient_since = None
        self._drop_cycle()
        self.cycle_cop = self.cycle_cop_new = None

    def defrost(self, snapshot: HeatingSnapshot, run_state: str, params: Parameters, now_ts: float) -> tuple[DefrostResult, bool]:
        """Classify defrost/icing. The flag tells whether the lockout changed (worth saving)."""
        # with the heat pump as the only source allowed, a dear heat still beats none
        economic = snapshot.mode != MODE_HEAT_PUMP_ONLY
        # and a lockout for dear heat that runs already ends with the switch (it used to run on to its end)
        block_until = self.block_until if economic or self.block_reason != defrost_engine.ECONOMIC_LOCKOUT else None
        res = defrost_engine.evaluate(
            snapshot.heat_pump, run_state, snapshot.outdoor_temp.value_or_none, snapshot.outdoor_rh.value_or_none,
            now_ts, params, tuple(self.defrost_cycle_starts), self.inefficient_since, block_until,
            economic_lockout=economic,
            counted=self.defrost_running,
            lockout_cause=self.block_reason,
        )
        starts = self.defrost_cycle_starts
        new = res.kind == "cycle" and not self.defrost_running
        if new and (not starts or now_ts - starts[-1] > defrost_engine.CYCLE_MIN_GAP_S):
            starts.append(now_ts)
        self.defrost_running = res.kind == "cycle"
        self.defrost_cycle_starts = [t for t in starts if now_ts - t <= defrost_engine.CYCLE_WINDOW_S]
        end, running = res.block_until_ts, self.block_until
        under_way = running is not None and running > now_ts
        if end is not None and under_way and 0.0 <= end - running < LOCKOUT_RENEW_STEP_S:
            end = running  # renewed by less than a step: the end on record stands
            res = replace(res, block_until_ts=end)
        dear = res.block_reason == defrost_engine.ECONOMIC_LOCKOUT
        if res.kind == "persistent" and not (dear and under_way and self.block_reason != defrost_engine.ECONOMIC_LOCKOUT):
            self.block_reason = res.block_reason  # dear heat does not take over a lockout that protects the unit
        elif end is None:
            self.block_reason = None
        changed = end != self.block_until
        self.block_until = end
        return res, changed

    def _drop_cycle(self) -> None:
        self.cycle_start_ts = None
        self.cycle_heat_wh = self.cycle_electricity_wh = 0.0
        self.cycle_spoiled = False
        self.cycle_last = None

    def _cycle(
        self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, spoiled: bool, params: Parameters, now_ts: float
    ) -> None:
        """Add this sample to the on/off cycle in progress; a compressor start closes one and begins the next."""
        if heat_pump_res.run_state not in _HEATING:
            self._drop_cycle()
            return
        heat = cop_engine.thermal_power_now(snapshot.heat_pump, params)
        electricity = heat_pump_res.electrical_power_w
        on = bool(snapshot.heat_pump.compressor_hz)
        last = self.cycle_last
        self.cycle_last = (now_ts, heat, electricity, on)
        if last is not None and self.cycle_start_ts is not None:
            dt = now_ts - last[0]
            if dt <= 0 or dt > CYCLE_MAX_GAP_S or last[1] is None or last[2] is None or heat is None or electricity is None:
                self.cycle_spoiled = True
            else:  # trapezoid between the two samples
                self.cycle_heat_wh += (last[1] + heat) / 2.0 * dt / 3600.0
                self.cycle_electricity_wh += (last[2] + electricity) / 2.0 * dt / 3600.0
        if spoiled:
            self.cycle_spoiled = True
        if not (on and (last is None or not last[3])):
            return
        # the compressor has just started: the cycle that began with the start before is whole
        if self.cycle_start_ts is not None and last is not None:
            length = now_ts - self.cycle_start_ts
            enough = self.cycle_electricity_wh >= CYCLE_MIN_ENERGY_WH
            if not self.cycle_spoiled and CYCLE_MIN_S <= length <= CYCLE_MAX_S and enough:
                value = self.cycle_heat_wh / self.cycle_electricity_wh
                if params.cop_min_raw <= value <= params.cop_max_raw:
                    self.cycle_cop = (now_ts, round(value, 3))
                    self.cycle_cop_new = round(value, 3)
        self.cycle_start_ts = now_ts
        self.cycle_heat_wh = self.cycle_electricity_wh = 0.0
        self.cycle_spoiled = False

    def take_cycle_cop(self) -> float | None:
        """The COP of a cycle that has just closed, once: for the map."""
        value, self.cycle_cop_new = self.cycle_cop_new, None
        return value

    def cop(
        self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, defrost_res: DefrostResult, params: Parameters,
        now_ts: float, above_minimum: str | None = None,
    ) -> CopResult:
        """The COP of the moment, from samples at the least speed the unit is held at.

        The map it feeds decides between heat pump and boiler for the speed the unit actually runs at
        (24 Hz, about 340 W, seen 2026-10-20 to 30). A start from off boosts to 60-79 Hz in its first
        minutes, and a quick heat-up or a drying (`above_minimum`, why_above_minimum) runs it at
        650-930 W: none of that enters, also with a warm-up time set shorter than ten minutes.
        """
        unit = snapshot.heat_pump
        instant = cop_engine.instant(unit, heat_pump_res.run_state, heat_pump_res.electrical_power_w, params)
        gates = list(instant.gate_reasons)
        if defrost_res.suspected:
            gates.append(f"defrost_{defrost_res.kind}")
        settled = unit.compressor_running_s is not None and unit.compressor_running_s >= SETTLED_RUN_S
        if instant.raw is not None and not settled and "warming_up" not in gates:
            gates.append("warming_up")
        if above_minimum is not None:
            gates.append(above_minimum)
        if instant.raw is not None and settled and above_minimum is None and not defrost_res.suspected:
            self.cop_samples.append((now_ts, instant.raw))
        if heat_pump_res.run_state not in (HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP):
            # samples from a finished run must not blend into the next one
            self.cop_samples = []
        self.cop_samples = [(ts, v) for ts, v in self.cop_samples if now_ts - ts <= COP_SAMPLE_RETENTION_S]
        value, n = cop_engine.aggregate(self.cop_samples, now_ts, params)
        self.cop_settled = value is not None
        if value is None and not gates:
            gates.append(f"insufficient_samples:{n}/{params.cop_min_samples}")
        self._cycle(snapshot, heat_pump_res, defrost_res.suspected or above_minimum is not None, params, now_ts)
        cycle_cop = None
        if self.cycle_cop is not None and heat_pump_res.run_state in _HEATING:
            cycle_cop = self.cycle_cop[1] if now_ts - self.cycle_cop[0] <= CYCLE_COP_FRESH_S else None
        if value is None and cycle_cop is not None:
            # a unit that cycles never gives a settled sample: the whole cycle is its measurement
            value, gates = cycle_cop, []
        return CopResult(
            value=value, raw=instant.raw, thermal_power_w=instant.thermal_power_w, electrical_power_w=heat_pump_res.electrical_power_w,
            airflow_m3h=instant.airflow_m3h, samples=n, gate_reasons=tuple(gates), cycle_cop=cycle_cop,
        )

    def track_efficiency(self, cop_value: float | None, break_even_cop: float | None, margin_off: float, now_ts: float) -> None:
        """Start the inefficiency timer while the measured COP stays below break-even."""
        if cop_value is not None and break_even_cop is not None and cop_value < break_even_cop * (1.0 - margin_off):
            self.inefficient_since = self.inefficient_since or now_ts
        else:
            self.inefficient_since = None

    def map_entry_due(self, cop_res: CopResult, now_ts: float) -> bool:
        """A settled COP may enter the map at most every few minutes.

        The COP of a whole cycle is no such value: it enters the map once, when its cycle closes
        (take_cycle_cop), however many older samples the result still counts.
        """
        if not self.cop_settled or cop_res.value is None or cop_res.samples < COP_MAP_MIN_SAMPLES_FOR_ENTRY:
            return False
        if now_ts - self.last_map_entry_ts < COP_MAP_ENTRY_INTERVAL_S:
            return False
        self.last_map_entry_ts = now_ts
        return True
