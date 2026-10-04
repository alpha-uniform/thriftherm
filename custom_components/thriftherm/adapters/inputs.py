"""Build an immutable HeatingSnapshot from Home Assistant state.

This is the only module (besides the coordinator) that touches `hass.states`.
Every value is validated here so the engines can trust `SensorReading.valid`.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from operator import attrgetter, itemgetter
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
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
# Many sensors report only when the value changes: a quiet living room kept 20.0 °C from
# 07:42 to 15:42 on 2026-09-23 and was taken for dead. While the same device still reports
# something else (humidity, as a rule), its temperature still holds, up to this age.
ROOM_TEMP_UNCHANGED_MAX_S = 24 * 3600.0
SAMPLE_SPACING_S = 30.0  # room histories: 240 samples cover two hours, 400 the humidity's 3 h 10 min
# A gas flow above 0 that has not changed for this long, while the meter reading has not moved
# either, was left behind by a meter that dropped off: burning gas moves the reading within minutes.
GAS_FLOW_STUCK_S = 600.0
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
    except (ValueError, OverflowError):  # "inf" passes float() but not int()
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
        self._device_sensors: dict[str, tuple[str, ...]] = {}
        self._valve_entities: dict[str, tuple[str, ...]] = {}
        self._valve_heard: dict[str, float] = {}  # climate entity -> when its valve last reported (ts)

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

    def _device_age_s(self, entity_id: str, now: datetime) -> float | None:
        """Age of the newest report among the other sensors on the same device."""
        siblings = self._device_sensors.get(entity_id)
        if siblings is None:
            registry = er.async_get(self.hass)
            entry = registry.async_get(entity_id)
            if entry is None or entry.device_id is None:
                return None  # not cached: the registry may not be loaded yet
            siblings = tuple(
                e.entity_id
                for e in er.async_entries_for_device(registry, entry.device_id)
                if e.entity_id != entity_id and e.domain == "sensor"
            )
            self._device_sensors[entity_id] = siblings
        ages = [
            _state_age_s(state, now)
            for sibling in siblings
            if (state := self._get(sibling)) is not None and state.state not in BAD_STATES
        ]
        return min(ages, default=None)

    def valves_behind(self, climate_entity: str) -> tuple[str, ...]:
        """Every entity of the valve devices a room thermostat drives.

        Better Thermostat names its real thermostats under "thermostat"; any other
        climate entity is its own valve.
        """
        cached = self._valve_entities.get(climate_entity)
        if cached is not None:
            return cached
        registry = er.async_get(self.hass)
        entry = registry.async_get(climate_entity)
        if entry is None:
            return ()  # not cached: the registry may not be loaded yet
        valves: list[str] = [climate_entity]
        if entry.platform == "better_thermostat" and entry.config_entry_id:
            bt = self.hass.config_entries.async_get_entry(entry.config_entry_id)
            if bt is None:
                return ()
            raw = bt.options.get("thermostat") or bt.data.get("thermostat") or []
            items = raw if isinstance(raw, list) else [raw]
            valves = [i if isinstance(i, str) else i.get("trv") for i in items if isinstance(i, (str, dict))]
            valves = [v for v in valves if isinstance(v, str)]
        found: list[str] = []
        for valve in valves:
            valve_entry = registry.async_get(valve)
            if valve_entry is not None and valve_entry.device_id is not None:
                # an update entity can be refreshed by the Zigbee bridge without the device
                found.extend(
                    e.entity_id for e in er.async_entries_for_device(registry, valve_entry.device_id) if e.domain != "update"
                )
            else:
                found.append(valve)
        result = tuple(dict.fromkeys(found))
        self._valve_entities[climate_entity] = result
        return result

    def thermostat_silent_s(self, climate_entity: str | None, now: datetime) -> float | None:
        """How long the valve behind a room thermostat has been silent, None if unknown.

        Measured 2026-09-27: the bathroom valve dropped out of the Zigbee network at 15:50
        and kept its last setpoint; Better Thermostat went on reporting "heat" at 21 °C.
        Healthy valves here report at least every 15 minutes.
        """
        if not climate_entity:
            return None
        entities = self.valves_behind(climate_entity)
        if not entities:
            return None
        states = [state for entity_id in entities if (state := self._get(entity_id)) is not None]
        ages = [_state_age_s(state, now) for state in states if state.state not in BAD_STATES]
        if ages:
            self._valve_heard[climate_entity] = now.timestamp() - min(ages)
            return min(ages)
        # Every entity of the valve unavailable is no news from it: the silence goes on (a broker
        # reconnect takes 45 s; a valve dropped from the network stays away).
        heard = self._valve_heard.get(climate_entity)
        if heard is not None:
            return max(now.timestamp() - heard, 0.0)
        since = [max((now - state.last_changed).total_seconds(), 0.0) for state in states]
        return min(since) if since else None

    def valve_temperature(self, climate_entity: str | None) -> float | None:
        """What the valves themselves measure, the stand-in for a room sensor that failed.

        Better Thermostat shows the room sensor as its own current temperature and keeps
        showing it when that sensor goes quiet. Measured 2026-09-29: the bathroom sensor was
        silent from 10:40 to 21:02, Better Thermostat kept 20.5 °C, the valve behind it saw
        the room warm to 22.5 °C, and the boiler heated for 4.5 hours.
        """
        if not climate_entity:
            return None
        temps = [
            value
            for entity_id in self.valves_behind(climate_entity)
            if entity_id != climate_entity and entity_id.startswith("climate.")
            and (state := self._get(entity_id)) is not None and state.state not in BAD_STATES
            and (value := self.read_attr_float(state, "current_temperature")) is not None
        ]
        if temps:
            return round(sum(temps) / len(temps), 2)
        # a valve of its own, or the registry not loaded yet
        return self.read_attr_float(self._get(climate_entity), "current_temperature")

    def read_room_temperature(self, entity_id: str | None, now: datetime) -> SensorReading:
        """A room temperature, still valid while its device keeps reporting other values."""
        max_age = min(self.params.sensor_max_age_s, ROOM_TEMP_MAX_AGE_S)
        reading = self.read_number(entity_id, RANGE_ROOM_TEMP, now, max_age_s=max_age)
        if reading.reason != "stale" or entity_id is None or (reading.age_s or 0.0) > ROOM_TEMP_UNCHANGED_MAX_S:
            return reading
        alive = self._device_age_s(entity_id, now)
        if alive is not None and alive <= max_age:
            return replace(reading, valid=True, reason=None)
        return reading

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
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

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
        next_start = next_end = None
        raw = state.attributes.get("next_event")
        parsed = None if raw is None else dt_util.parse_datetime(str(raw))
        if parsed is not None:
            # while a block runs, the next event is its end; otherwise the next start
            if active:
                next_end = dt_util.as_local(parsed)
            else:
                next_start = dt_util.as_local(parsed)
        return ScheduleState(active=active, temperature=temp, next_start=next_start, next_end=next_end)

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
        temp = self.read_room_temperature(cfg.temperature_sensor, now)
        hum = self.read_number(cfg.humidity_sensor, RANGE_HUMIDITY, now)
        climate = self._get(cfg.climate_entity)
        trv_local = self.valve_temperature(cfg.climate_entity)
        trv_target = self.read_attr_float(climate, "temperature")
        trv_mode = None if climate is None or climate.state in BAD_STATES else climate.state

        gain: float | None = None
        for entity_id in cfg.internal_gain_sensors:
            reading = self.read_number(entity_id, RANGE_POWER_W, now)
            if reading.valid:
                gain = (gain or 0.0) + max(reading.value, 0.0)

        now_ts = now.timestamp()
        history = self._history[cfg.key]
        # Every gas meter tick starts a cycle, up to every 10 s while the burner runs: one sample per
        # half minute keeps two hours in the buffer and no burst outweighs the rest of the trend.
        if temp.valid and (not history or now_ts - history[-1][0] >= SAMPLE_SPACING_S):
            history.append((now_ts, temp.value))
        # two hours, so the 90-minute trend window is always fully covered
        _trim(history, now_ts - 7200.0)

        abs_hist = self._abs_hum_history[cfg.key]
        if temp.valid and hum.valid and (not abs_hist or now_ts - abs_hist[-1][0] >= SAMPLE_SPACING_S):
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
            thermostat_silent_s=self.thermostat_silent_s(cfg.climate_entity, now),
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
        # liveness comes from the eBUS signal binary sensor (with its grace period for one-second
        # dropouts). Where it is configured, a value that has not changed is still the value: a
        # cold boiler kept 22.25 °C from 08:19 to 14:32 on 2026-10-03, the six-hour bound took
        # the boiler data for lost, and the knob took over and fired. Without a signal the bound stays.
        max_age = 0.0 if cfg.get(CONF_BOILER_SIGNAL) else 6 * 3600.0
        # The meter publishes changes only, so an idle 0 stands for hours and age alone says nothing.
        # A flow above 0 left hanging would mean "burning" for good (hot water guess, learning pause).
        gas_volume = self.read_number(cfg.get(CONF_GAS_VOLUME), (0.0, 1e7), now, max_age_s=0)
        gas_flow = self.read_number(cfg.get(CONF_GAS_FLOW), RANGE_GAS_FLOW_M3H, now, max_age_s=0)
        if (
            gas_flow.valid
            and gas_flow.value > 0
            and (gas_flow.age_s or 0.0) > GAS_FLOW_STUCK_S
            and (gas_volume.age_s or 0.0) > GAS_FLOW_STUCK_S
        ):
            gas_flow = replace(gas_flow, valid=False, reason="stale")
        return BoilerState(
            signal_ok=signal_ok,
            flow_temp=self.read_number(cfg.get(CONF_BOILER_FLOW_TEMP), RANGE_FLOW_TEMP, now, max_age),
            return_temp=self.read_number(cfg.get(CONF_BOILER_RETURN_TEMP), RANGE_FLOW_TEMP, now, max_age),
            pump_state=self.read_text(cfg.get(CONF_BOILER_PUMP_STATE)),
            hwc_mode=self.read_text(cfg.get(CONF_BOILER_HWC_MODE)),
            gas_volume_m3=gas_volume,
            gas_flow_m3h=gas_flow,
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
