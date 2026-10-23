"""Build an immutable HeatingSnapshot from Home Assistant state.

This is the module that reads the configured input entities.
Every value is validated here so the engines can trust `SensorReading.valid`.
"""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, time, timedelta
from operator import attrgetter, itemgetter
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_BOILER_CIRCULATION_L_H,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_STATE_NUMBER,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_FLAME,
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
from ..engines.cop import RH_MAX_AGE_S
from ..engines.room import WINDOW_CLOSED_STATES, WINDOW_OPEN_STATES

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
# Without a meter reading the flow's own age decides.
GAS_FLOW_STUCK_S = 600.0
# A window contact's last change is when Home Assistant started, if it was open then: the grace for
# short airing ran anew after every restart. Seen 2026-10-13: the study's door stood open all day and
# counted as shut for two minutes after each of three restarts, long enough for a room to call for heat.
# A contact first found open within this time after the start was open before it, and counts as open at
# once (the room sensors are back within it, see coordinator.ROOM_DATA_GRACE_S).
WINDOW_FOUND_OPEN_S = 300.0
# The heat pump's plug. Measured 2026-10-13 and 14 (Shelly Plus Plug S, every change in the history):
# while the unit ran, its draw changed all the time and the plug reported every 60 s (median of 634),
# 180 s apart at the longest. Six minutes is twice that, and more than the five minutes some plug firmware
# reports at by default (Tasmota); ten minutes let the draw of a plug gone quiet count that long after the
# compressor had stopped. A draw that does not change is not reported at all: a steady 8.2 W went 420 s
# without a report, plugs at a steady 0 W more than 20 minutes. So a low draw holds while the compressor
# stands; while it runs, a plug quiet for longer gives way to the unit's own value (`realtime_power`).
PLUG_POWER_MAX_AGE_S = 360.0
PLUG_STANDBY_MAX_W = 50.0  # standby here 1.3-1.8 W or 8-11 W, the fan alone about 35-50 W
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
    SettingChange,
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


_ENERGY_SUM = re.compile(r"PrEnergySum(Hc|Hwc)\d", re.IGNORECASE)  # Vaillant, engines/gas_split.py


def burning(value: str | None) -> bool | None:
    """The burner sign from a binary sensor (on/off) or from a burner output in percent (above 0)."""
    known = on_off(value)
    if known is not None or value is None:
        return known
    try:
        return float(value.strip().replace(",", ".")) > 0.0
    except ValueError:
        return None


# Versatile Thermostat: the managers that replace the requested target with one of their own
_OWN_TARGET_MANAGERS = (
    ("window_manager", "window_state"),
    ("window_manager", "window_auto_state"),
    ("power_manager", "overpowering_state"),
    ("safety_manager", "safety_state"),
)


def applies_own_target(attributes: Mapping[str, Any]) -> bool:
    """True while a room thermostat holds a target that nobody asked it for.

    Versatile Thermostat lowers its target by itself while a window is open (eco or frost
    temperature), while it sheds load and in its safety mode, and restores what was requested
    afterwards. It says so in its attributes: the target in force differs from the requested
    one, or one of those managers is on. Such a target is no turn of the knob.
    """

    def number(group: str) -> float | None:
        value = attributes.get(group)
        raw = value.get("target_temperature") if isinstance(value, Mapping) else None
        return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None

    in_force, requested = number("current_state"), number("requested_state")
    if in_force is not None and requested is not None and abs(in_force - requested) > 0.25:
        return True
    for group, key in _OWN_TARGET_MANAGERS:
        manager = attributes.get(group)
        if isinstance(manager, Mapping) and manager.get(key) == "on":
            if group == "window_manager" and manager.get("is_window_bypass") is True:
                continue
            return True
    return False


def whole_number(value: str | None) -> int | None:
    """A state like "8" or "8.0" as int, e.g. the boiler's status number."""
    try:
        return None if value is None else int(float(value))
    except (ValueError, OverflowError):  # "inf" passes float() but not int()
        return None


# room thermostats that drive valves of their own and show the room sensor as their temperature
_WRAPPER_PLATFORMS = ("better_thermostat", "versatile_thermostat")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# a block of a schedule helper: start, end (time.max: to midnight), the temperature it names
Block = tuple[time, time, float | None]


def block_starting(week: tuple[tuple[Block, ...], ...], at: datetime) -> tuple[datetime, float | None] | None:
    """The block of a schedule helper that starts at `at` (local time), or a minute after: its end and its temperature.

    A minute between two blocks is no gap: "to 23:59" is how a block to the end of the day is often
    written, and the next day's block starts at 00:00 (test of 2026-10-15: two days of 00:00-23:59
    ended the comfort at 23:59, and the room did not call the boiler from 23:29 to midnight).
    """
    for begin in (at, at.replace(second=0, microsecond=0) + timedelta(minutes=1)):
        for start, end, temperature in week[begin.weekday()]:
            if start == begin.time():
                if end == time.max:
                    return datetime.combine(begin.date() + timedelta(days=1), time(), tzinfo=at.tzinfo), temperature
                return datetime.combine(begin.date(), end, tzinfo=at.tzinfo), temperature
    return None


def comfort_run_end(
    week: tuple[tuple[Block, ...], ...], block_end: datetime, level: float, comfort_temp: float
) -> datetime | None:
    """Where the comfort of a schedule helper ends, from the end of its running block; None: not within a week.

    The helper's next event is the end of the running block only. A block that starts at the same
    instant carries the comfort on (06:00-12:00 at 20 °C, 12:00-22:00 at 21 °C: taken as the comfort
    end, 12:00 kept the room from calling the boiler from 11:30 to 12:00 every day), unless it asks
    for less than the block before it (`level`). A block that names no temperature asks for `comfort_temp`.
    """
    end = block_end
    while end - block_end < timedelta(days=7):
        following = block_starting(week, end)
        if following is None or following[0] <= end:
            return end
        asked = comfort_temp if following[1] is None else following[1]
        if asked < level - 0.01:
            return end
        end, level = following[0], asked
    return None


def next_level_change(
    week: tuple[tuple[Block, ...], ...], block_end: datetime, level: float, comfort_temp: float, setback_temp: float
) -> tuple[datetime, float] | None:
    """Where the target of a schedule helper changes next, from the end of its running block, and to what.

    A block that starts at that instant at the same temperature carries it on; one at another
    temperature is the change (to its temperature, `comfort_temp` when it names none), no block the
    change to `setback_temp`. None: not within a week.
    """
    at = block_end
    while at - block_end < timedelta(days=7):
        following = block_starting(week, at)
        if following is None:
            return at, setback_temp
        asked = comfort_temp if following[1] is None else following[1]
        if abs(asked - level) > 0.01 or following[0] <= at:
            return at, asked
        at = following[0]
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
        self._outlet_history: deque[tuple[float, float]] = deque(maxlen=120)
        self._heat_pump_history: deque[HeatPumpSample] = deque(maxlen=240)
        self._device_sensors: dict[str, tuple[str, ...]] = {}
        self._valve_entities: dict[str, tuple[str, ...]] = {}  # room thermostat -> its valves, for one build
        self._valve_heard: dict[str, float] = {}  # climate entity -> when its valve last reported (ts)
        self._started_at: datetime | None = None  # the first snapshot
        self._windows_seen: set[str] = set()  # contacts that have said open or shut since the start
        self._open_from_start: set[str] = set()  # contacts first found open at the start, not shut since
        self._opened_at: dict[str, datetime] = {}  # open contacts: their last change when first seen open, until seen shut

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

        Better Thermostat names its real thermostats under "thermostat", Versatile Thermostat
        under "underlying_entity_ids"; any other climate entity is its own valve.

        Looked up anew for every snapshot (`build` empties the cache): kept for good, a broken
        valve replaced and Better Thermostat pointed at the new one left the old entities
        watched, and the room counted as silent until the integration was reloaded.
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
        elif entry.platform == "versatile_thermostat" and entry.config_entry_id:
            # Versatile Thermostat keeps what it drives (valves, climates, switches) in a list;
            # entries made by older versions have one key per entity
            vt = self.hass.config_entries.async_get_entry(entry.config_entry_id)
            if vt is None:
                return ()
            stored = {**vt.data, **vt.options}
            raw = stored.get("underlying_entity_ids") or []
            items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            for kind in ("climate", "valve", "heater"):
                items.extend(stored.get(f"{kind}_entity{n}_id") for n in ("", "2", "3", "4"))
            valves = [v for v in items if isinstance(v, str) and v] or [climate_entity]
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
        return self.valve_reading(climate_entity)[0]

    def valve_reading(self, climate_entity: str | None) -> tuple[float | None, str | None]:
        """The valves' temperature and the one entity it comes from (None when several are averaged)."""
        if not climate_entity:
            return None, None
        measured = [
            (value, entity_id)
            for entity_id in self.valves_behind(climate_entity)
            if entity_id != climate_entity and entity_id.startswith("climate.")
            and (state := self._get(entity_id)) is not None and state.state not in BAD_STATES
            and (value := self.read_attr_float(state, "current_temperature")) is not None
        ]
        if measured:
            temps = [value for value, _ in measured]
            return round(sum(temps) / len(temps), 2), measured[0][1] if len(measured) == 1 else None
        entry = er.async_get(self.hass).async_get(climate_entity)
        if entry is not None and entry.platform in _WRAPPER_PLATFORMS:
            # no valve behind it measures (Versatile Thermostat over a switch, a valve unavailable):
            # its own temperature is the room sensor again, not a stand-in for it
            return None, None
        # a valve of its own
        value = self.read_attr_float(self._get(climate_entity), "current_temperature")
        return value, None if value is None else climate_entity

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
        """Each contact's state and, while open, for how long.

        A contact first found open in the first minutes (`WINDOW_FOUND_OPEN_S`) counts as open for
        at least the grace, until it is seen shut: its last change is the restart, not an opening.
        One opened while the control runs is timed from its change and gets the grace. One that drops
        out and comes back open (a broker reconnect makes every contact unavailable for about 45 s) has
        its last change at the return, yet stood open all along: it stays timed from its opening.
        """
        starting = self._started_at is None or (now - self._started_at).total_seconds() < WINDOW_FOUND_OPEN_S
        out: list[tuple[str, str | None, float | None]] = []
        for entity_id in cfg.window_sensors:
            state = self._get(entity_id)
            if state is None or state.state in BAD_STATES:
                out.append((entity_id, None, None))
                continue
            text = state.state.lower()
            open_since = None
            if text in WINDOW_CLOSED_STATES:
                self._windows_seen.add(entity_id)
                self._open_from_start.discard(entity_id)
                self._opened_at.pop(entity_id, None)
            elif text in WINDOW_OPEN_STATES:
                if starting and entity_id not in self._windows_seen:
                    self._open_from_start.add(entity_id)
                self._windows_seen.add(entity_id)
                open_since = (now - self._opened_at.setdefault(entity_id, state.last_changed)).total_seconds()
                if entity_id in self._open_from_start:
                    open_since = max(open_since, self.params.window_grace_s)
            out.append((entity_id, state.state, open_since))
        return tuple(out)

    def _schedule_blocks(self, entity_id: str) -> tuple[tuple[Block, ...], ...] | None:
        """The week of a schedule helper, day by day; None for an entity that does not tell its blocks.

        The helper's state names its next event only. Its blocks are what its `schedule.get_schedule`
        action answers with; an action cannot be awaited while the snapshot is built, so the entity
        itself is asked.
        """
        component = self.hass.data.get(DATA_INSTANCES, {}).get("schedule")
        entity = None if component is None else component.get_entity(entity_id)
        read = getattr(entity, "get_schedule", None)
        if read is None:
            return None
        try:
            days = read()
            week = tuple(
                tuple(
                    (block["from"], block["to"], self._block_temperature(block.get("data")))
                    for block in days.get(day) or ()
                )
                for day in _WEEKDAYS
            )
        except (AttributeError, KeyError, TypeError):
            return None  # not the schedule helper we know: its state alone has to do
        if not all(isinstance(limit, time) for day in week for block in day for limit in block[:2]):
            return None
        return week

    @staticmethod
    def _block_temperature(data: Any) -> float | None:
        raw = data.get("temperature") if isinstance(data, Mapping) else None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _schedule_next_temperature(self, cfg: RoomConfig, schedule: ScheduleState | None) -> float | None:
        """The temperature the coming block of the schedule helper names, None if it names none."""
        if not cfg.schedule_entity or schedule is None or schedule.next_start is None:
            return None
        week = self._schedule_blocks(cfg.schedule_entity)
        following = None if week is None else block_starting(week, schedule.next_start)
        return None if following is None else following[1]

    def _schedule_state(self, cfg: RoomConfig) -> ScheduleState | None:
        """State of the assigned schedule helper: active block, its temperature, next start."""
        if not cfg.schedule_entity:
            return None
        state = self._get(cfg.schedule_entity)
        if state is None or state.state in BAD_STATES:
            return None
        active = state.state == "on"
        temp = self.read_attr_float(state, "temperature")
        next_start = next_end = change = None
        change_target: float | None = None
        raw = state.attributes.get("next_event")
        parsed = None if raw is None else dt_util.parse_datetime(str(raw))
        if parsed is not None:
            # while a block runs, the next event is its end; otherwise the next start
            event = dt_util.as_local(parsed)
            week = self._schedule_blocks(cfg.schedule_entity)
            if active:
                next_end = event
                if week is not None:
                    level = cfg.comfort_temp if temp is None else temp
                    next_end = comfort_run_end(week, event, level, cfg.comfort_temp)
                    found = next_level_change(week, event, level, cfg.comfort_temp, cfg.setback_temp)
                    if found is not None:
                        change, change_target = found
                elif event.time() in (time(), time(23, 59), time(23, 59, 59)):
                    # A block to 24:00 (or to 23:59) is how a schedule says "all day"; an entity that
                    # does not tell its blocks does not tell whether the next day starts at 00:00
                    # again, so midnight is no comfort end (it would end every boiler call at 23:30).
                    next_end = None
                else:
                    change, change_target = event, cfg.setback_temp
            else:
                next_start = change = event
                following = None if week is None else block_starting(week, event)
                change_target = cfg.comfort_temp if following is None or following[1] is None else following[1]
        return ScheduleState(
            active=active, temperature=temp, next_start=next_start, next_end=next_end,
            next_change=change, next_change_target=change_target,
        )

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
        trv_local, trv_local_entity = self.valve_reading(cfg.climate_entity)
        trv_target = self.read_attr_float(climate, "temperature")
        trv_mode = None if climate is None or climate.state in BAD_STATES else climate.state
        # Better Thermostat and Versatile Thermostat say whether they heat right now, that is whether
        # the valve is open; a plain valve may say it as well. Anything else is no answer.
        action = None if trv_mode is None or climate is None else climate.attributes.get("hvac_action")
        heating = True if action in ("heating", "preheating") else (False if action in ("idle", "off") else None)
        own_target = trv_mode is not None and climate is not None and applies_own_target(climate.attributes)
        schedule = self._schedule_state(cfg)

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
            schedule=schedule,
            schedule_next_temp=self._schedule_next_temperature(cfg, schedule),
            temperature=temp,
            humidity=hum,
            window_readings=self._window_readings(cfg, now),
            trv_local_temp=trv_local,
            trv_local_entity=trv_local_entity,
            trv_target_temp=trv_target,
            internal_gain_w=gain,
            temperature_history=tuple(history),
            abs_humidity_history=tuple(abs_hist),
            override=override,
            drying=drying or DryingState(),
            heat_rate_k_h=heat_rate,
            boost_until_ts=boost_until,
            trv_hvac_mode=trv_mode,
            thermostat_heating=heating,
            thermostat_own_target=own_target,
            thermostat_silent_s=self.thermostat_silent_s(cfg.climate_entity, now),
            thermostat_min_temp=self.read_attr_float(climate, "min_temp"),
            thermostat_max_temp=self.read_attr_float(climate, "max_temp"),
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

        cop_age = 3600.0  # validity window; the outlet sensor's freshness is gated in the cop engine
        plug_power = self.read_number(cfg.get(CONF_HEAT_PUMP_POWER), RANGE_POWER_W, now, max_age_s=PLUG_POWER_MAX_AGE_S)
        if plug_power.reason == "stale" and plug_power.value <= PLUG_STANDBY_MAX_W and running_s is None:
            plug_power = replace(plug_power, valid=True, reason=None)  # the compressor stands: a steady low draw is not reported
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

        fan_mode, fan_modes, hvac_modes = attrs.get("fan_mode"), attrs.get("fan_modes"), attrs.get("hvac_modes")
        return HeatPumpState(
            available=available,
            hvac_mode=climate.state if available else None,
            target_temp=self.read_attr_float(climate, "temperature"),
            indoor_temp=self.read_attr_float(climate, "indoor_temperature") or self.read_attr_float(climate, "current_temperature"),
            outdoor_temp=self.read_attr_float(climate, "outdoor_temperature"),
            indoor_coil_temp=self.read_attr_float(climate, "indoor_coil_temperature"),
            outdoor_coil_temp=self.read_attr_float(climate, "outdoor_coil_temperature"),
            compressor_hz=compressor_hz,
            fan_rpm=self.read_attr_float(climate, "indoor_fan_speed"),
            fan_mode=fan_mode if isinstance(fan_mode, str) else None,
            fan_modes=tuple(m for m in fan_modes if isinstance(m, str)) if isinstance(fan_modes, (list, tuple)) else (),
            hvac_modes=tuple(str(m) for m in hvac_modes if isinstance(m, str)) if isinstance(hvac_modes, (list, tuple)) else (),
            target_temp_step=self.read_attr_float(climate, "target_temp_step"),
            realtime_power_w=self.read_attr_float(climate, "realtime_power"),
            error_code=error_code,
            plug_power=plug_power,
            intake_temp=intake_temp,
            intake_rh=self.read_number(cfg.get(CONF_HEAT_PUMP_INTAKE_RH), RANGE_HUMIDITY, now, max_age_s=RH_MAX_AGE_S),
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
        # no meter reading (age None) cannot speak for the flow: 1.2 m³/h still meant "burning" after 10 h
        volume_quiet = gas_volume.age_s is None or gas_volume.age_s > GAS_FLOW_STUCK_S
        if gas_flow.valid and gas_flow.value > 0 and (gas_flow.age_s or 0.0) > GAS_FLOW_STUCK_S and volume_quiet:
            gas_flow = replace(gas_flow, valid=False, reason="stale")
        sums = self._boiler_energy_sums()
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
            flame=burning(self.read_text(cfg.get(CONF_BOILER_FLAME))),
            energy_sums_heating=sums["hc"],
            energy_sums_hot_water=sums["hwc"],
        )

    def _boiler_energy_sums(self) -> dict[str, tuple[tuple[str, float], ...]]:
        """The boiler's own sums of burner output, found beside its flow temperature.

        ebusd announces every message of a boiler as an entity of one device, with the message
        name in the unique id. Vaillant: PrEnergySumHc1-3 (heating), PrEnergySumHwc1-3 (hot water).
        """
        found: dict[str, list[tuple[str, float]]] = {"hc": [], "hwc": []}
        registry = er.async_get(self.hass)
        flow = registry.async_get(self.config.get(CONF_BOILER_FLOW_TEMP) or "")
        if flow is None or flow.device_id is None:
            return {purpose: () for purpose in found}
        for entry in er.async_entries_for_device(registry, flow.device_id):
            match = _ENERGY_SUM.search(entry.unique_id or "")
            state = self.hass.states.get(entry.entity_id) if match else None
            if state is None:
                continue
            try:
                value = float(state.state)
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0.0:
                found[match.group(1).lower()].append((match.group(0).lower(), value))
        return {purpose: tuple(sorted(readings)) for purpose, readings in found.items()}

    # ------------------------------------------------------------------ outdoor
    def _outdoor(self, now: datetime) -> tuple[SensorReading, str | None, SensorReading]:
        sensors: Iterable[str] = self.config.get(CONF_OUTDOOR_TEMP_SENSORS) or []
        last_reason = "not_configured"
        rh = self.read_number(self.config.get(CONF_OUTDOOR_RH_SENSOR), RANGE_HUMIDITY, now, max_age_s=3600)
        weather = self._get(self.config.get(CONF_WEATHER_ENTITY))
        if not rh.valid:
            # the weather entity's humidity stands in for a humidity sensor, wherever the temperature comes from
            w_rh = self.read_attr_float(weather, "humidity")
            if weather is not None and w_rh is not None and RANGE_HUMIDITY[0] <= w_rh <= RANGE_HUMIDITY[1]:
                rh = SensorReading(weather.entity_id, w_rh, _state_age_s(weather, now), True, None)
        for entity_id in sensors:
            reading = self.read_number(entity_id, RANGE_OUTDOOR_TEMP, now, max_age_s=3600)
            if reading.valid:
                return reading, entity_id, rh
            last_reason = reading.reason or "invalid"
        temp = self.read_attr_float(weather, "temperature")
        if weather is not None and temp is not None and RANGE_OUTDOOR_TEMP[0] <= temp <= RANGE_OUTDOOR_TEMP[1]:
            reading = SensorReading(weather.entity_id, temp, _state_age_s(weather, now), True, None)
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
        setting_changes: Mapping[str, SettingChange] | None = None,
        settings_watched_since_ts: float | None = None,
    ) -> HeatingSnapshot:
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        changes = setting_changes or {}
        self._valve_entities.clear()  # see valves_behind
        if self._started_at is None:
            self._started_at = now
        rooms = {
            cfg.key: replace(
                self._room_state(
                    cfg, now, overrides.get(cfg.key), drying_states.get(cfg.key), heat_rates.get(cfg.key), boosts.get(cfg.key),
                    room_temps.get(cfg.key),
                ),
                thermostat_change=changes.get(cfg.climate_entity) if cfg.climate_entity else None,
            )
            for cfg in self.rooms
        }
        outdoor, outdoor_source, outdoor_rh = self._outdoor(now)
        return HeatingSnapshot(
            now=local_now,
            mode=mode,
            rooms=rooms,
            heat_pump=replace(
                self._heat_pump_state(now),
                setting_change=changes.get(str(self.config.get(CONF_HEAT_PUMP_CLIMATE))),
                settings_watched_since_ts=settings_watched_since_ts,
            ),
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
        for key in (CONF_HEAT_PUMP_CLIMATE, CONF_GAS_FLOW, CONF_BOILER_PUMP_STATE, CONF_BOILER_PUMP_RUNNING, CONF_BOILER_FLAME):
            if self.config.get(key):
                watched.add(self.config[key])
        return watched
