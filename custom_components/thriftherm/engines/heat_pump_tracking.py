"""Heat pump observations that span update cycles.

Defrost cycles, the icing lockout, the inefficiency timer and the COP samples
all need memory between cycles. The engines they feed are pure; this class
holds that memory so the coordinator only has to call it.
"""

from __future__ import annotations

from dataclasses import replace

from ..const import (
    HEAT_PUMP_FAN_ONLY,
    HEAT_PUMP_HEATING_STABLE,
    HEAT_PUMP_HEATING_WARMING_UP,
    MODE_HEAT_PUMP_ONLY,
    UPDATE_INTERVAL_S,
)
from ..models import CopResult, DefrostResult, HeatingSnapshot, HeatPumpResult, HeatPumpState, Parameters
from . import cop as cop_engine
from . import defrost as defrost_engine
from .heat_pump import HEATING_STATES as _HEATING
from .heat_pump_control import SETTLED_RUN_S

COP_SAMPLE_RETENTION_S = 1800.0
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
CYCLE_COP_FRESH_S = 3600.0  # how long the sensor shows the last cycle's COP; the economics go by the COP table
# The stretches a band of the COP table adds up. The fan run-on counts: what the outlet sensor misses at a start it shows then.
BAND_STATES = (*_HEATING, HEAT_PUMP_FAN_ONLY)
# After a stop heat counts this long, as long as the default fan run-on. Past it a few tenths between the two
# sensors look like heat all through a rest, for hours of "run time" on a few watts.
BAND_RUN_ON_S = 900.0
# A unit stopped by a settled COP too low rests and, its band still high, runs again: no value between two runs, 32
# minutes apart. The timer for dear heat outlasts a rest of this. A longer one says nothing about the unit of now.
INEFFICIENT_REST_S = 3600.0


class HeatPumpTracker:
    def __init__(self) -> None:
        self.defrost_cycle_starts: list[float] = []
        self.defrost_running = False  # the cycle under way is counted already (not stored)
        self.inefficient_since: float | None = None  # set while the COP of this moment is too low, for the lockout
        self.inefficient_span: tuple[float, float] | None = None  # too low first and last, with no good value between
        self.block_until: float | None = None
        self.block_reason: str | None = None  # what started or renewed the lockout under way; None: none, or not known
        self.cop_samples: list[tuple[float, float]] = []
        self.cop_settled = False  # the COP last reported came from settled samples, not from a whole cycle
        # the on/off cycle being added up, and the COP of the last whole one
        self.cycle_start_ts: float | None = None
        self.cycle_heat_wh = 0.0
        self.cycle_electricity_wh = 0.0
        self.cycle_spoiled = False
        self.cycle_last: tuple[float, float | None, float | None, bool] | None = None  # ts, heat W, electricity W, compressor on
        self.cycle_cop: tuple[float, float] | None = None  # (closed at, COP)
        # the stretch of this cycle for the COP table: heat Wh without the duct factor, electricity Wh, seconds
        self.band_interval: tuple[float, float, float] | None = None
        # ts, heat W without the duct factor, electricity W, compressor on, above its least speed or hung or in the run-on of that
        self.band_last: tuple[float, float | None, float | None, bool, bool] | None = None
        self.band_on_ts: float | None = None  # when its compressor last ran
        self.band_held: tuple[float, float, float, float] | None = None  # a start the sensor has not reacted to: since, as above

    def reset(self) -> None:
        """Forget what was learned; an icing lockout and the defrost count protect the unit and stay."""
        self.cop_samples = []
        self.cop_settled = False
        self.inefficient_since = self.inefficient_span = None
        self._drop_cycle()
        self.cycle_cop = None
        self.band_interval = self.band_last = self.band_on_ts = self.band_held = None

    def defrost(
        self, snapshot: HeatingSnapshot, run_state: str, params: Parameters, now_ts: float, boiler_held: bool = False
    ) -> tuple[DefrostResult, bool]:
        """Classify defrost/icing. The flag tells whether the lockout changed (worth saving)."""
        # with the heat pump as the only source allowed, or the boiler held back by a fault, a dear heat still beats none
        economic = snapshot.mode != MODE_HEAT_PUMP_ONLY and not boiler_held
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

    @staticmethod
    def _outlet_silent(unit: HeatPumpState, defrost_res: DefrostResult) -> bool:
        """No report from the outlet sensor since before this run started, with air going past it.

        However quiet it was, a live one reacts to a start: within 61 seconds at all 17 starts of 2026-10-31 to 11-01, one
        from a reading 57 minutes old. A later silence in a run says nothing, the unit may hold its outlet for half an hour.
        """
        running, age = unit.compressor_running_s, unit.outlet_temp.age_s
        if not unit.compressor_hz or running is None or age is None:
            return False
        return unit.outlet_temp.valid and bool(unit.fan_rpm) and not defrost_res.suspected and age >= running

    @classmethod
    def _outlet_hung(
        cls, unit: HeatPumpState, free_heat: float | None, electricity: float | None, defrost_res: DefrostResult,
        params: Parameters,
    ) -> bool:
        """What a settled compressor with the fan turning cannot give, or no reaction to its start at all: it is the sensor.

        It stays valid while its device reports, for up to six hours. Counted as no heat under all the electricity it could put a band below the off
        limit, where the unit does not run again and nothing lifts it; counted as more than a unit gives it could start
        the unit from gas until the half-life has diluted it. A start is left in, its sensor lags and shows at the stop
        what it missed, and so is a defrost, which truly gives no heat.
        """
        running = unit.compressor_running_s
        settled = bool(unit.compressor_hz) and running is not None and running >= SETTLED_RUN_S
        if not settled or defrost_res.suspected or not unit.fan_rpm or not unit.outlet_temp.valid or not unit.intake_temp.valid:
            return False
        if cls._outlet_silent(unit, defrost_res) or unit.outlet_temp.value - unit.intake_temp.value < params.cop_min_delta_t_k:
            return True
        if free_heat is None or electricity is None or electricity <= 0:
            return False
        return free_heat * params.duct_factor / electricity > params.cop_max_raw

    def _band_stretch(
        self, unit: HeatPumpState, run_state: str, free_heat: float | None, electricity: float | None, defrost_res: DefrostResult,
        above_minimum: str | None, params: Parameters, now_ts: float,
    ) -> tuple[float, float, float] | None:
        """The stretch since the last sample as the sensors have it; only what is invalid stays out.

        Start, defrost (its electricity, no heat) and run-on are in. A run above the least speed is not: the
        table describes normal operation. Nor is a rest: compressor standing, no heat. Its few watts would pile
        up in a band it rests in day after day, without a run to weigh them.
        """
        if run_state not in BAND_STATES:
            self.band_last = None
            return None
        hung = self._outlet_hung(unit, free_heat, electricity, defrost_res, params)
        if hung:
            free_heat = None
        on = bool(unit.compressor_hz)
        if on:
            self.band_on_ts = now_ts
        last = self.band_last
        # The run-on of such a run is its heat as well, though what kept it above its least speed ends with the stop.
        # And what a hung sensor shows in the run-on is no more true than in the run.
        out = above_minimum is not None or hung or (not on and last is not None and last[4])
        self.band_last = (now_ts, free_heat, electricity, on, out)
        if last is None or free_heat is None or electricity is None or last[1] is None or last[2] is None:
            return None
        dt = now_ts - last[0]
        if dt <= 0 or dt > CYCLE_MAX_GAP_S or out or last[4]:
            return None
        since_on = None if self.band_on_ts is None else now_ts - self.band_on_ts
        giving_heat = (free_heat > 0.0 or last[1] > 0.0) and since_on is not None and since_on <= BAND_RUN_ON_S
        if on or last[3] or defrost_res.kind == "cycle" or giving_heat:
            return ((last[1] + free_heat) / 2.0 * dt / 3600.0, (last[2] + electricity) / 2.0 * dt / 3600.0, dt)
        return None

    def _band(
        self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, free_heat: float | None, defrost_res: DefrostResult,
        above_minimum: str | None, params: Parameters, now_ts: float,
    ) -> None:
        """The stretch for the band of the COP table, held back at a start until the outlet sensor has reacted to it.

        Whether a start counts shows only after it: a dead sensor shows nothing for all of its electricity, 7 % off a
        learned band with one such start. A live one reacts before the next sample or so, and all that was held counts.
        """
        unit = snapshot.heat_pump
        interval = self._band_stretch(
            unit, heat_pump_res.run_state, free_heat, heat_pump_res.electrical_power_w, defrost_res, above_minimum, params,
            now_ts,
        )
        held = self.band_held
        if heat_pump_res.run_state not in BAND_STATES:
            self.band_held = None
        elif self._outlet_silent(unit, defrost_res) and unit.compressor_running_s < SETTLED_RUN_S:
            if interval is not None:
                since, heat, electricity, seconds = held if held is not None else (now_ts, 0.0, 0.0, 0.0)
                self.band_held = (since, heat + interval[0], electricity + interval[1], seconds + interval[2])
            interval = None
        elif held is not None:
            self.band_held = None
            age = unit.outlet_temp.age_s
            if age is None or age >= now_ts - held[0]:
                interval = None  # the run is over, or ten minutes old, and the sensor has not said a word
                if self.band_last is not None:
                    self.band_last = (*self.band_last[:4], True)  # its run-on goes with it
            else:
                before = interval if interval is not None else (0.0, 0.0, 0.0)
                interval = (held[1] + before[0], held[2] + before[1], held[3] + before[2])
        self.band_interval = interval

    def _cycle(
        self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, heat: float | None, spoiled: bool, params: Parameters,
        now_ts: float,
    ) -> None:
        """Add this sample to the on/off cycle in progress; a compressor start closes one and begins the next."""
        if heat_pump_res.run_state not in _HEATING:
            self._drop_cycle()
            return
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
        self.cycle_start_ts = now_ts
        self.cycle_heat_wh = self.cycle_electricity_wh = 0.0
        self.cycle_spoiled = False

    def cop(
        self, snapshot: HeatingSnapshot, heat_pump_res: HeatPumpResult, defrost_res: DefrostResult, params: Parameters,
        now_ts: float, above_minimum: str | None = None,
    ) -> CopResult:
        """The COP of the moment, from samples at the least speed the unit is held at, and the stretch for the COP table.

        It decides between heat pump and boiler for the speed the unit actually runs at (24 Hz, about 340 W,
        seen 2026-10-20 to 30). A start from off boosts to 60-79 Hz in its first minutes, and a quick heat-up
        or a drying (`above_minimum`, why_above_minimum) runs it at 650-930 W: none of that enters, also with
        a warm-up time set shorter than ten minutes. The table adds the starts up with the rest (`_band`).
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
        # Not from a value older than this run, kept valid only because its device reports: a live sensor reacts to a start.
        silent = self._outlet_silent(unit, defrost_res)
        if instant.raw is not None and settled and silent:
            gates.append("outlet_temp_silent")
        if instant.raw is not None and settled and above_minimum is None and not defrost_res.suspected and not silent:
            self.cop_samples.append((now_ts, instant.raw))
        if heat_pump_res.run_state not in (HEAT_PUMP_HEATING_STABLE, HEAT_PUMP_HEATING_WARMING_UP):
            # samples from a finished run must not blend into the next one
            self.cop_samples = []
        self.cop_samples = [(ts, v) for ts, v in self.cop_samples if now_ts - ts <= COP_SAMPLE_RETENTION_S]
        value, n = cop_engine.aggregate(self.cop_samples, now_ts, params)
        self.cop_settled = value is not None
        if value is None and not gates:
            gates.append(f"insufficient_samples:{n}/{params.cop_min_samples}")
        free_heat = cop_engine.free_power_now(unit, params)
        heat = None if free_heat is None else free_heat * params.duct_factor
        self._cycle(snapshot, heat_pump_res, heat, defrost_res.suspected or above_minimum is not None, params, now_ts)
        self._band(snapshot, heat_pump_res, free_heat, defrost_res, above_minimum, params, now_ts)
        cycle_cop = None
        if self.cycle_cop is not None and heat_pump_res.run_state in _HEATING:
            cycle_cop = self.cycle_cop[1] if now_ts - self.cycle_cop[0] <= CYCLE_COP_FRESH_S else None
        if value is None and cycle_cop is not None:
            # A unit that cycles never gives a settled sample: the whole cycle is what the sensor shows. It decides
            # nothing (cop_settled): on 2026-10-31 a 2.3 from a short run chose gas, which stopped the unit, which made
            # the next short run. What short runs truly cost is in the sums of the table.
            value, gates = cycle_cop, []
        return CopResult(
            value=value, raw=instant.raw, thermal_power_w=instant.thermal_power_w, electrical_power_w=heat_pump_res.electrical_power_w,
            airflow_m3h=instant.airflow_m3h, samples=n, gate_reasons=tuple(gates), cycle_cop=cycle_cop,
        )

    def track_efficiency(self, cop_value: float | None, break_even_cop: float | None, params: Parameters, now_ts: float) -> None:
        """Run the inefficiency timer while the settled COP stays below break-even, also over the rests between runs.

        Only a value that is too low now can end it in a lockout, not a lone one with the time passing after it.
        """
        self.inefficient_since = None
        span = self.inefficient_span
        # Between two values lie the tick of the stop, the rest and what the next run takes to give one: settling and samples.
        gap = INEFFICIENT_REST_S + SETTLED_RUN_S + (params.cop_min_samples + 1) * UPDATE_INTERVAL_S
        if break_even_cop is None:
            self.inefficient_span = None
        elif cop_value is None:
            if span is not None and now_ts - span[1] > gap:
                self.inefficient_span = None
        elif cop_value >= break_even_cop * (1.0 - params.break_even_margin_off):
            self.inefficient_span = None
        else:
            first = span[0] if span is not None and now_ts - span[1] <= gap else now_ts
            self.inefficient_span = (first, now_ts)
            self.inefficient_since = first
