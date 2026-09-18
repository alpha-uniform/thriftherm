"""Build an immutable HeatingSnapshot from Home Assistant state.

This is the only module (besides the coordinator) that touches `hass.states`.
Every value is validated here so the engines can trust `SensorReading.valid`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from operator import attrgetter, itemgetter
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_BOILER_CIRCULATION_L_H,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_STATE_NUMBER,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_GAS_FLOW,
    CONF_GAS_VOLUME,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_INTAKE_RH,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_OUTLET_TEMP,
    CONF_HEAT_PUMP_POWER,
    CONF_OUTDOOR_RH_SENSOR,
    CONF_OUTDOOR_TEMP_SENSORS,
    CONF_ROOMS,
    CONF_WEATHER_ENTITY,
    DEFAULT_BOILER_CIRCULATION_L_H,
    RANGE_FLOW_TEMP,
    RANGE_GAS_FLOW_M3H,
    RANGE_HUMIDITY,
    RANGE_OUTDOOR_TEMP,
    RANGE_POWER_W,
    RANGE_ROOM_TEMP,
)
from ..engines import psychrometrics as psy
from ..engines.room import WINDOW_OPEN_STATES

# A room sensor that has been quiet for hours is not a measurement any more: room
# temperatures decide the heating, so they get a tighter limit than the general one
# (battery sensors here report every one to two hours).
ROOM_TEMP_MAX_AGE_S = 6 * 3600.0
from ..models import (
    BoilerState,
    DryingState,
    HeatingSnapshot,
    Override,
    HeatPumpSample,
    HeatPumpState,
    Parameters,
    Prices,
    RoomConfig,
    RoomState,
    ScheduleState,
    SensorReading,
)
from .config import parameters_from_config, prices_from_config, room_configs_from_config

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, "", "none", "None")


def on_off(value: str | None) -> bool | None:
    """An on/off state as bool; anything else is unknown."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in ("on", "true", "1"):
        return True
    if text in ("off", "false", "0"):
        return False
    return None


def whole_number(value: str | None) -> int | None:
    """A state like "8" or "8.0" as int, e.g. the boiler's status number."""
    try:
        return None if value is None else int(float(value))
    except ValueError:
        return None


def _state_age_s(state: State, now: datetime) -> float:
    ts = getattr(state, "last_reported", None) or state.last_updated
    return max((now - ts).total_seconds(), 0.0)


def _trim(history: deque, cutoff: float, ts: Callable[[Any], float] = itemgetter(0)) -> None:
    """Drop the entries older than `cutoff` from a history kept in time order."""
    while history and ts(history[0]) < cutoff:
        history.popleft()


class SnapshotBuilder:
    """Reads configured entities and produces a HeatingSnapshot."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self.has_heat_pump = bool(config.get(CONF_HEAT_PUMP_CLIMATE))
        rooms = room_configs_from_config(config.get(CONF_ROOMS, []))
        if not self.has_heat_pump:
            # without the heat pump add-on every room is a radiator room
            rooms = tuple(replace(r, served_by_heat_pump=False) for r in rooms)
        self.rooms: tuple[RoomConfig, ...] = rooms
        self.prices: Prices = prices_from_config(config)
        self.params: Parameters = parameters_from_config(config)
        self._history: dict[str, deque[tuple[float, float]]] = {r.key: deque(maxlen=240) for r in self.rooms}
        self._abs_hum_history: dict[str, deque[tuple[float, float]]] = {r.key: deque(maxlen=400) for r in self.rooms}
        self._compressor_started_at: datetime | None = None
        self._window_open_since: dict[str, datetime] = {}
        self._outlet_history: deque[tuple[float, float]] = deque(maxlen=120)
        self._heat_pump_history: deque[HeatPumpSample] = deque(maxlen=240)

    # ------------------------------------------------------------------ readers
    def _get(self, entity_id: str | None) -> State | None:
        if not entity_id:
            return None
        return self.hass.states.get(entity_id)

    def read_number(
        self,
        entity_id: str | None,
        rng: tuple[float, float],
        now: datetime,
        max_age_s: float | None = None,
    ) -> SensorReading:
        if not entity_id:
            return SensorReading.missing(None, "not_configured")
        state = self._get(entity_id)
        if state is None:
            return SensorReading.missing(entity_id, "entity_not_found")
        if state.state in BAD_STATES:
            return SensorReading(entity_id, None, _state_age_s(state, now), False, state.state or "empty")
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return SensorReading(entity_id, None, _state_age_s(state, now), False, "not_numeric")
        age = _state_age_s(state, now)
        if not rng[0] <= value <= rng[1]:
            return SensorReading(entity_id, value, age, False, "out_of_range")
        max_age = self.params.sensor_max_age_s if max_age_s is None else max_age_s
        if max_age and age > max_age:
            return SensorReading(entity_id, value, age, False, "stale")
        return SensorReading(entity_id, value, age, True, None)

    def read_text(self, entity_id: str | None) -> str | None:
        state = self._get(entity_id)
        if state is None or state.state in BAD_STATES:
            return None
        return state.state

    def read_attr_float(self, state: State | None, attr: str) -> float | None:
        if state is None:
            return None
        value = state.attributes.get(attr)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ rooms
    def _window_readings(self, cfg: RoomConfig, now: datetime) -> tuple[tuple[str, str | None, float | None], ...]:
        out: list[tuple[str, str | None, float | None]] = []
        for entity_id in cfg.window_sensors:
            state = self._get(entity_id)
            if state is None or state.state in BAD_STATES:
                out.append((entity_id, None, None))
                continue
            open_since = None
            if state.state.lower() in WINDOW_OPEN_STATES:
                open_since = (now - state.last_changed).total_seconds()
            out.append((entity_id, state.state, open_since))
        return tuple(out)

    def _schedule_state(self, cfg: RoomConfig, now: datetime) -> ScheduleState | None:
        """State of the assigned schedule helper: active block, its temperature, next start."""
        if not cfg.schedule_entity:
            return None
        state = self._get(cfg.schedule_entity)
        if state is None or state.state in BAD_STATES:
            return None
        active = state.state == "on"
        temp = self.read_attr_float(state, "temperature")
        next_start = None
        raw = state.attributes.get("next_event")
        if not active and raw is not None:
            parsed = dt_util.parse_datetime(str(raw))
            if parsed is not None:
                next_start = dt_util.as_local(parsed)
        return ScheduleState(active=active, temperature=temp, next_start=next_start)

    def _room_state(
        self,
        cfg: RoomConfig,
        now: datetime,
        override: Override | None = None,
        drying: DryingState | None = None,
        heat_rate: float | None = None,
        boost_until: float | None = None,
        temps: Mapping[str, float] | None = None,
    ) -> RoomState:
        if temps:
            cfg = replace(cfg, comfort_temp=temps.get("comfort", cfg.comfort_temp), setback_temp=temps.get("setback", cfg.setback_temp))
        temp = self.read_number(cfg.temperature_sensor, RANGE_ROOM_TEMP, now, max_age_s=min(self.params.sensor_max_age_s, ROOM_TEMP_MAX_AGE_S))
        hum = self.read_number(cfg.humidity_sensor, RANGE_HUMIDITY, now)
        climate = self._get(cfg.climate_entity)
        trv_local = self.read_attr_float(climate, "current_temperature")
        trv_target = self.read_attr_float(climate, "temperature")
        trv_mode = None if climate is None or climate.state in BAD_STATES else climate.state

        gain: float | None = None
        for entity_id in cfg.internal_gain_sensors:
            reading = self.read_number(entity_id, RANGE_POWER_W, now)
            if reading.valid:
                gain = (gain or 0.0) + max(reading.value, 0.0)

        now_ts = now.timestamp()
        history = self._history[cfg.key]
        if temp.valid:
            history.append((now_ts, temp.value))
        # two hours, so the 90-minute trend window is always fully covered
        _trim(history, now_ts - 7200.0)

        abs_hist = self._abs_hum_history[cfg.key]
        if temp.valid and hum.valid:
            abs_hist.append((now_ts, psy.absolute_humidity_g_m3(temp.value, hum.value)))
        _trim(abs_hist, now_ts - 3 * 3600.0 - 600.0)

        return RoomState(
            config=cfg,
            schedule=self._schedule_state(cfg, now),
            temperature=temp,
            humidity=hum,
            window_readings=self._window_readings(cfg, now),
            trv_local_temp=trv_local,
            trv_target_temp=trv_target,
            internal_gain_w=gain,
            temperature_history=tuple(history),
            abs_humidity_history=tuple(abs_hist),
            override=override,
            drying=drying or DryingState(),
            heat_rate_k_h=heat_rate,
            boost_until_ts=boost_until,
            trv_hvac_mode=trv_mode,
        )

    # ------------------------------------------------------------------ midea
    def _heat_pump_state(self, now: datetime) -> HeatPumpState:
        cfg = self.config
        climate = self._get(cfg.get(CONF_HEAT_PUMP_CLIMATE))
        available = climate is not None and climate.state not in BAD_STATES
        attrs = climate.attributes if climate is not None else {}

        compressor_hz = self.read_attr_float(climate, "compressor_frequency")
        if available and compressor_hz is not None and compressor_hz > 0:
            if self._compressor_started_at is None:
                self._compressor_started_at = now
        else:
            self._compressor_started_at = None
        running_s = None
        if self._compressor_started_at is not None:
            running_s = (now - self._compressor_started_at).total_seconds()

        error_code = attrs.get("error_code")
        try:
            error_code = None if error_code is None else int(error_code)
        except (TypeError, ValueError):
            error_code = None

        cop_age = 3600.0  # validity window; the freshness gates are applied in the cop engine
        plug_power = self.read_number(cfg.get(CONF_HEAT_PUMP_POWER), RANGE_POWER_W, now, max_age_s=600)
        intake_temp = self.read_number(cfg.get(CONF_HEAT_PUMP_INTAKE_TEMP), RANGE_ROOM_TEMP, now, max_age_s=cop_age)
        outlet_temp = self.read_number(cfg.get(CONF_HEAT_PUMP_OUTLET_TEMP), (-10.0, 80.0), now, max_age_s=cop_age)

        now_ts = now.timestamp()
        if outlet_temp.valid:
            self._outlet_history.append((now_ts, outlet_temp.value))
        _trim(self._outlet_history, now_ts - 1800.0)
        self._heat_pump_history.append(
            HeatPumpSample(
                ts=now_ts,
                compressor_hz=compressor_hz,
                power_w=plug_power.value_or_none if plug_power.valid else self.read_attr_float(climate, "realtime_power"),
                outdoor_coil_temp=self.read_attr_float(climate, "outdoor_coil_temperature"),
                indoor_coil_temp=self.read_attr_float(climate, "indoor_coil_temperature"),
                intake_temp=intake_temp.value_or_none,
                outlet_temp=outlet_temp.value_or_none,
            )
        )
        _trim(self._heat_pump_history, now_ts - 1800.0, attrgetter("ts"))

        return HeatPumpState(
            available=available,
            hvac_mode=climate.state if available else None,
            hvac_action=attrs.get("hvac_action"),
            target_temp=self.read_attr_float(climate, "temperature"),
            indoor_temp=self.read_attr_float(climate, "indoor_temperature") or self.read_attr_float(climate, "current_temperature"),
            outdoor_temp=self.read_attr_float(climate, "outdoor_temperature"),
            indoor_coil_temp=self.read_attr_float(climate, "indoor_coil_temperature"),
            outdoor_coil_temp=self.read_attr_float(climate, "outdoor_coil_temperature"),
            compressor_hz=compressor_hz,
            fan_rpm=self.read_attr_float(climate, "indoor_fan_speed"),
            realtime_power_w=self.read_attr_float(climate, "realtime_power"),
            error_code=error_code,
            plug_power=plug_power,
            intake_temp=intake_temp,
            intake_rh=self.read_number(cfg.get(CONF_HEAT_PUMP_INTAKE_RH), RANGE_HUMIDITY, now, max_age_s=cop_age),
            outlet_temp=outlet_temp,
            compressor_running_s=running_s,
            outlet_temp_history=tuple(self._outlet_history),
            history=tuple(self._heat_pump_history),
            configured=self.has_heat_pump,
        )

    # ------------------------------------------------------------------ boiler
    def _boiler_state(self, now: datetime) -> BoilerState:
        cfg = self.config
        signal_state = self.read_text(cfg.get(CONF_BOILER_SIGNAL))
        signal_ok: bool | None
        if signal_state is None:
            signal_ok = None if not cfg.get(CONF_BOILER_SIGNAL) else False
        else:
            signal_ok = signal_state.lower() in ("on", "true", "connected")
        # ebusd republishes a value only when it changes, so age is not a liveness signal;
        # liveness comes from the eBUS signal binary sensor. Keep a generous upper bound.
        max_age = 6 * 3600.0
        return BoilerState(
            signal_ok=signal_ok,
            flow_temp=self.read_number(cfg.get(CONF_BOILER_FLOW_TEMP), RANGE_FLOW_TEMP, now, max_age),
            return_temp=self.read_number(cfg.get(CONF_BOILER_RETURN_TEMP), RANGE_FLOW_TEMP, now, max_age),
            pump_state=self.read_text(cfg.get(CONF_BOILER_PUMP_STATE)),
            hwc_mode=self.read_text(cfg.get(CONF_BOILER_HWC_MODE)),
            gas_volume_m3=self.read_number(cfg.get(CONF_GAS_VOLUME), (0.0, 1e7), now, max_age_s=0),
            gas_flow_m3h=self.read_number(cfg.get(CONF_GAS_FLOW), RANGE_GAS_FLOW_M3H, now, max_age_s=0),
            circulation_l_h=float(cfg.get(CONF_BOILER_CIRCULATION_L_H, DEFAULT_BOILER_CIRCULATION_L_H)),
            pump_running=on_off(self.read_text(cfg.get(CONF_BOILER_PUMP_RUNNING))),
            state_number=whole_number(self.read_text(cfg.get(CONF_BOILER_STATE_NUMBER))),
        )

    # ------------------------------------------------------------------ outdoor
    def _outdoor(self, now: datetime) -> tuple[SensorReading, str | None, SensorReading]:
        sensors: Iterable[str] = self.config.get(CONF_OUTDOOR_TEMP_SENSORS) or []
        last_reason = "not_configured"
        rh = self.read_number(self.config.get(CONF_OUTDOOR_RH_SENSOR), RANGE_HUMIDITY, now, max_age_s=3600)
        for entity_id in sensors:
            reading = self.read_number(entity_id, RANGE_OUTDOOR_TEMP, now, max_age_s=3600)
            if reading.valid:
                return reading, entity_id, rh
            last_reason = reading.reason or "invalid"
        weather = self._get(self.config.get(CONF_WEATHER_ENTITY))
        temp = self.read_attr_float(weather, "temperature")
        if weather is not None and temp is not None and RANGE_OUTDOOR_TEMP[0] <= temp <= RANGE_OUTDOOR_TEMP[1]:
            reading = SensorReading(weather.entity_id, temp, _state_age_s(weather, now), True, None)
            if not rh.valid:
                w_rh = self.read_attr_float(weather, "humidity")
                if w_rh is not None:
                    rh = SensorReading(weather.entity_id, w_rh, _state_age_s(weather, now), True, None)
            return reading, f"weather:{weather.entity_id}", rh
        return SensorReading.missing(None, last_reason), None, rh

    # ------------------------------------------------------------------ build
    def build(
        self,
        mode: str,
        overrides: Mapping[str, Override],
        drying_states: Mapping[str, DryingState],
        heat_rates: Mapping[str, float],
        away_return_ts: float | None,
        boosts: Mapping[str, float],
        room_temps: Mapping[str, Mapping[str, float]],
    ) -> HeatingSnapshot:
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        rooms = {
            cfg.key: self._room_state(
                cfg, now, overrides.get(cfg.key), drying_states.get(cfg.key), heat_rates.get(cfg.key), boosts.get(cfg.key),
                room_temps.get(cfg.key),
            )
            for cfg in self.rooms
        }
        outdoor, outdoor_source, outdoor_rh = self._outdoor(now)
        return HeatingSnapshot(
            now=local_now,
            mode=mode,
            rooms=rooms,
            heat_pump=self._heat_pump_state(now),
            boiler=self._boiler_state(now),
            outdoor_temp=outdoor,
            outdoor_temp_source=outdoor_source,
            outdoor_rh=outdoor_rh,
            prices=self.prices,
            params=self.params,
            away_return_ts=away_return_ts,
        )

    def watched_entities(self) -> set[str]:
        """Entities whose state changes should trigger an early refresh."""
        watched: set[str] = set()
        for cfg in self.rooms:
            watched.update(cfg.window_sensors)
            if cfg.climate_entity:
                watched.add(cfg.climate_entity)
            if cfg.schedule_entity:
                watched.add(cfg.schedule_entity)
        # The gas meter and the pump say when the burner runs. A short burn lasts about
        # as long as one update cycle, so waiting for the next cycle loses it - and a lost
        # burn is a lost cycling signal. Both are quiet while the burner is off.
        for key in (CONF_HEAT_PUMP_CLIMATE, CONF_GAS_FLOW, CONF_BOILER_PUMP_STATE, CONF_BOILER_PUMP_RUNNING):
            if self.config.get(key):
                watched.add(self.config[key])
        return watched
