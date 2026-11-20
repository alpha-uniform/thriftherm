"""Build an immutable HeatingSnapshot from Home Assistant state.

This is the module that reads the configured input entities.
Every value is validated here so the engines can trust `SensorReading.valid`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, time
from operator import attrgetter, itemgetter
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_BOILER_CIRCULATION_L_H,
    CONF_BOILER_FLAME,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_BOILER_STATE_NUMBER,
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
from ..engines.room_cooling import CoolSchedule
from ..engines.safety import HEAT_PUMP_DROPOUT_S
from ..engines.schedule_week import block_starting, comfort_run_end, next_level_change
from ..engines.valve_caps import Entity, caps_of, quirks_of
from ..engines.valve_control import EXTERNAL_MAX_AGE_S
from ..engines.valve_plan import ValveView

# A room sensor that has been quiet for hours is not a measurement any more: room
# temperatures decide the heating, so they get a tighter limit than the general one
# (battery sensors here report every one to two hours).
ROOM_TEMP_MAX_AGE_S = 6 * 3600.0
# Many sensors report only when the value changes: a quiet living room kept 20.0 °C from
# 07:42 to 15:42 on 2026-09-23 and was taken for dead. While the same device still reports
# something else (humidity, as a rule), its temperature still holds, up to this age.
ROOM_TEMP_UNCHANGED_MAX_S = 24 * 3600.0
# A broker or Zigbee2MQTT restart takes the entities of a valve away for a minute and brings them back with the values
# they had: that is no report of the valve (2026-11-13: 40 s at 15:28; the valve had last spoken at 11:00). Gone longer,
# a valve that answers again with the same values has spoken. The values come back one by one within the burst.
VALVE_REPLAY_GONE_MAX_S = 600.0
VALVE_REPLAY_BURST_S = 120.0
# The outlet moves with every burst of the unit: two hours unchanged was the longest seen in a run (2026-11-04).
# Longer than this with its device alive it hangs, and the table of the COP would learn from it.
OUTLET_UNCHANGED_MAX_S = 6 * 3600.0
# The outdoor sensors: an hour without a report, unless their device is alive; unchanged for longer than
# this with it alive they hang. The coordinator keeps the last known value for six hours after that.
OUTDOOR_MAX_AGE_S = 3600.0
OUTDOOR_UNCHANGED_MAX_S = 6 * 3600.0
# a valve driven directly on this little battery may not move its motor any more (review 2026-11-15 item 4): its room's
# hours say nothing about the flow
VALVE_BATTERY_FLAT_PCT = 10.0
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
# ZHA leaves the timestamps of a device's entities alone while their values stay the same (issue #2,
# 2026-10-26): a valve ZHA had seen two minutes before had entities 2.5 hours old and counted as silent,
# a thermometer seen 42 seconds before had a temperature six hours old and counted as stale. So for a
# device ZHA pairs, its own "last seen" is a report as well. No other integration is asked: Zigbee2MQTT
# has a *Last seen* entity of its own for this (see the guide), which counts like any entity of the device.
ZHA = "zha"
from ..models import (
    BoilerState,
    DryingState,
    HeatingSnapshot,
    HeatPumpSample,
    HeatPumpState,
    Override,
    Parameters,
    Prices,
    RoomConfig,
    RoomState,
    ScheduleState,
    SensorReading,
    SettingChange,
    Week,
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


# a power sensor's unit -> its factor to W; None: no unit given, taken as W
_LOGGER = logging.getLogger(__name__)

_POWER_UNITS: dict[str | None, float] = {None: 1.0, "": 1.0, "W": 1.0, "kW": 1000.0, "mW": 0.001, "MW": 1e6}
# units written by hand (LocalTuya, Tasmota, MQTT templates), in any case; "mw" is milli or mega, and no unit
_POWER_UNITS_ANY_CASE: dict[str, float] = {
    "w": 1.0, "watt": 1.0, "watts": 1.0, "kw": 1000.0, "kilowatt": 1000.0, "kilowatts": 1000.0,
}


# A gas meter's reading in m³ and a gas flow in m³/h, by the unit the sensor names; none is taken as that, as before.
_GAS_VOLUME_UNITS: dict[str, float] = {"m³": 1.0, "m3": 1.0, "m^3": 1.0, "l": 0.001, "dm³": 0.001, "dm3": 0.001}
_GAS_FLOW_UNITS: dict[str, float] = {"m³/h": 1.0, "m3/h": 1.0, "m^3/h": 1.0, "l/min": 0.06, "l/h": 0.001}


def _power_factor(unit: object) -> float | None:
    """The factor to W of a power sensor's unit, None for a unit that is no power."""
    if unit is None:
        return 1.0
    if not isinstance(unit, str):
        return None
    unit = unit.strip()
    factor = _POWER_UNITS.get(unit)
    return factor if factor is not None else _POWER_UNITS_ANY_CASE.get(unit.lower())


# room thermostats that drive valves of their own and show the room sensor as their temperature
_WRAPPER_PLATFORMS = ("better_thermostat", "versatile_thermostat")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _state_age_s(state: State, now: datetime) -> float:
    ts = getattr(state, "last_reported", None) or state.last_updated
    return max((now - ts).total_seconds(), 0.0)


def _fingerprint(state: State) -> str:
    """A state with its attributes, short: a valve's setpoint is an attribute of its climate entity."""
    text = json.dumps([state.state, sorted(state.attributes.items())], default=str, ensure_ascii=False)
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]


def _reported_ts(state: State) -> float:
    return (getattr(state, "last_reported", None) or state.last_updated).timestamp()


def _zha_device_seen_s(hass: HomeAssistant, device_id: str, now: datetime) -> float | None:
    """How long ago ZHA last saw one of its devices; None while ZHA calls it unavailable, or without a usable answer.

    ZHA's helper belongs to ZHA and may change with any Home Assistant version. ZHA not set up, a device it
    does not know, another name, signature or shape of the helper, a time it never saw the device, one that
    is no number or lies ahead: all of it is no sign, never an error in the control (issue #2, 2026-10-26).
    """
    try:
        from homeassistant.components.zha.helpers import async_get_zha_device_proxy

        device = async_get_zha_device_proxy(hass, device_id).device
        available, last_seen = device.available, device.last_seen
        if available is not True or isinstance(last_seen, bool) or not isinstance(last_seen, (int, float)):
            return None
        age = now.timestamp() - last_seen  # zigpy keeps it as a Unix timestamp in seconds
    except Exception:  # whatever ZHA raises, the entities' own timestamps still count
        return None
    return age if math.isfinite(age) and age >= 0.0 else None


def _driven(hass: HomeAssistant, climate_entity: str) -> tuple[str, ...] | None:
    """The entities a room thermostat drives, itself for a plain one; None while its entries cannot be read.

    Better Thermostat names its real thermostats under "thermostat", Versatile Thermostat
    under "underlying_entity_ids"; any other climate entity drives itself.
    """
    entry = er.async_get(hass).async_get(climate_entity)
    if entry is None:
        return None
    if entry.platform == "better_thermostat" and entry.config_entry_id:
        bt = hass.config_entries.async_get_entry(entry.config_entry_id)
        if bt is None:
            return None
        raw = bt.options.get("thermostat") or bt.data.get("thermostat") or []
        items = raw if isinstance(raw, list) else [raw]
        valves = [i if isinstance(i, str) else i.get("trv") for i in items if isinstance(i, (str, dict))]
        return tuple(v for v in valves if isinstance(v, str))
    if entry.platform == "versatile_thermostat" and entry.config_entry_id:
        # Versatile Thermostat keeps what it drives (valves, climates, switches) in a list;
        # entries made by older versions have one key per entity
        vt = hass.config_entries.async_get_entry(entry.config_entry_id)
        if vt is None:
            return None
        stored = {**vt.data, **vt.options}
        raw = stored.get("underlying_entity_ids") or []
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        for kind in ("climate", "valve", "heater"):
            items.extend(stored.get(f"{kind}_entity{n}_id") for n in ("", "2", "3", "4"))
        return tuple(v for v in items if isinstance(v, str) and v) or (climate_entity,)
    return (climate_entity,)


def driven_by(hass: HomeAssistant, climate_entity: str) -> tuple[str, ...]:
    """The entities a room thermostat drives besides itself (Better or Versatile Thermostat), () for a plain one.

    The setup asks it, so that a room thermostat in front of the heat pump's unit is refused as the
    unit itself is (review 2026-11-08).
    """
    return tuple(dict.fromkeys(e for e in _driven(hass, climate_entity) or () if e != climate_entity))


def valve_driver(hass: HomeAssistant, valve: str) -> str | None:
    """The room thermostat of an enabled Better or Versatile Thermostat entry that drives `valve`, None if none does.

    The setup refuses such a valve for a room driven directly (docs/PHASE13_VALVES.md §4): two controllers at one valve
    sent two setpoints a minute. A disabled entry drives nothing.
    """
    registry = er.async_get(hass)
    own = registry.async_get(valve)
    if own is not None and own.platform in ("better_thermostat", "versatile_thermostat"):
        return valve  # a room thermostat of its own, no valve (review 2026-11-15)
    for entry in registry.entities.values():
        if entry.domain != "climate" or entry.platform not in ("better_thermostat", "versatile_thermostat"):
            continue
        owner = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
        if owner is None or owner.disabled_by is not None or entry.entity_id == valve:
            continue
        if valve in (_driven(hass, entry.entity_id) or ()):
            return entry.entity_id
    return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valve_view(hass: HomeAssistant, valve: str) -> ValveView:
    """A valve driven directly as Home Assistant shows it: what its device can do (engines/valve_caps.py) and what it has.

    Read anew each time from the registries: a valve replaced, or an entity switched on in ZHA, is seen at once.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(valve)
    device = None if entry is None or entry.device_id is None else dr.async_get(hass).async_get(entry.device_id)
    found: list[Entity] = []
    # with the disabled ones: a selection switched off is to be told, not taken for none (2026-11-15)
    entries = er.async_entries_for_device(registry, device.id, include_disabled_entities=True) if device else ()
    for item in entries:
        state = hass.states.get(item.entity_id)
        attrs = state.attributes if state is not None else {}
        options = attrs.get("options") or ()
        found.append(Entity(
            entity_id=item.entity_id, domain=item.domain, key=item.translation_key or item.unique_id or "",
            unit=attrs.get("unit_of_measurement") or item.unit_of_measurement,
            options=tuple(o for o in options if isinstance(o, str)),
            writable=item.domain in ("number", "select", "switch", "lock"), enabled=item.disabled_by is None,
        ))
    climate = hass.states.get(valve)
    shown_c = None if climate is None else _float(climate.attributes.get("current_temperature"))
    caps = caps_of(tuple(found), valve, measures=shown_c is not None or climate is None)

    def value(entity_id: str | None) -> State | None:
        state = hass.states.get(entity_id) if entity_id else None
        return None if state is None or state.state in BAD_STATES else state

    selection = value(caps.sensor_select or caps.sensor_switch)
    offset = value(caps.offset)
    limits = (-5.0, 5.0, 0.5)
    if offset is not None:
        low, high, step = (_float(offset.attributes.get(k)) for k in ("min", "max", "step"))
        limits = (low if low is not None else -5.0, high if high is not None else 5.0, step or 0.5)
    external = value(caps.external_temperature)
    opening = value(caps.opening)
    hysteresis = value(caps.hysteresis)
    hysteresis_k = abs(_float(hysteresis.state) or 0.0) if hysteresis is not None else 0.0
    if entry is not None and entry.platform in _WRAPPER_PLATFORMS:
        # Better or Versatile Thermostat decide themselves when their valves open: a threshold on their device would
        # set their word aside (issues #3, #8; check of 2026-11-15). Only a valve's own is Thriftherm's to allow for.
        hysteresis_k = 0.0
    return ValveView(
        caps=caps,
        quirks=quirks_of(device.manufacturer if device else None, device.model if device else None),
        available=climate is not None and climate.state not in BAD_STATES,
        selection=None if selection is None else selection.state,
        # a person chose it: a context of a user (a restart of the valve, or Thriftherm, has none)
        selection_by_person=selection is not None and selection.context.user_id is not None,
        external_shown=None if external is None else _float(external.state),
        offset_shown=None if offset is None else _float(offset.state),
        offset_limits=limits,
        shown_c=shown_c,
        target_shown=None if climate is None else _float(climate.attributes.get("temperature")),
        mode=None if climate is None or climate.state in BAD_STATES else climate.state,
        opening_shown=None if opening is None else _float(opening.state),
        hvac_action=None if climate is None else climate.attributes.get("hvac_action"),
        hysteresis_k=min(hysteresis_k, 3.0),
        setpoint_limits=(
            (_float(climate.attributes.get("min_temp")) if climate is not None else None) or 5.0,
            (_float(climate.attributes.get("max_temp")) if climate is not None else None) or 30.0,
        ),
        setpoint_step=(_float(climate.attributes.get("target_temp_step")) if climate is not None else None) or 0.5,
    )


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
        # The humidity a shower is told by, only where a drying starts by itself: no other part reads it.
        self._abs_hum_history: dict[str, deque[tuple[float, float]]] = {
            r.key: deque(maxlen=400) for r in self.rooms if r.bathroom_drying and r.served_by_heat_pump and r.drying_automatic
        }
        self._wrong_units: set[tuple[str | None, Any]] = set()  # warned about once: (entity, unit)
        self._compressor_started_at: datetime | None = None
        self._heat_pump_gone_since: datetime | None = None  # while its climate entity is away
        self._outlet_history: deque[tuple[float, float]] = deque(maxlen=120)
        self._heat_pump_history: deque[HeatPumpSample] = deque(maxlen=240)
        self._device_sensors: dict[str, tuple[str, ...]] = {}
        self._valve_entities: dict[str, tuple[str, ...]] = {}  # room thermostat -> its valves, for one build
        self._init_valve_memory()
        self._zha_devices: dict[str, str | None] = {}  # entity -> its device if ZHA pairs it, for one build
        self._started_at: datetime | None = None  # the first snapshot
        self._windows_seen: set[str] = set()  # contacts that have said open or shut since the start
        self._open_from_start: set[str] = set()  # contacts first found open at the start, not shut since
        self._opened_at: dict[str, datetime] = {}  # open contacts: their last change when first seen open, until seen shut

    def _init_valve_memory(self) -> None:
        self._valve_heard: dict[str, float] = {}  # climate entity -> when its valve last reported (ts)
        # what its valve's entities showed when last available, since when all of them are gone, and until when what
        # they report is the repetition of that by a broker or Home Assistant (VALVE_REPLAY_*)
        self._valve_values: dict[str, dict[str, str]] = {}
        self._valve_gone_at: dict[str, float] = {}
        self._valve_replay_until: dict[str, float] = {}

    def valve_memory(self) -> dict[str, dict[str, Any]]:
        """What is kept of the valves over a restart of Home Assistant: when each was heard, and what it showed."""
        return {
            climate: {"heard": heard, "values": dict(self._valve_values.get(climate, {}))}
            for climate, heard in self._valve_heard.items()
        }

    def restore_valve_memory(self, data: Any, now_ts: float) -> None:
        """Take back `valve_memory` after a restart: the values Home Assistant and the broker bring back now are theirs."""
        if not isinstance(data, Mapping):
            return
        for climate, item in data.items():
            if not isinstance(climate, str) or not isinstance(item, Mapping):
                continue
            heard, values = item.get("heard"), item.get("values")
            if isinstance(heard, bool) or not isinstance(heard, (int, float)) or not math.isfinite(heard):
                continue
            if not isinstance(values, Mapping):
                continue
            self._valve_heard[climate] = float(heard)
            self._valve_values[climate] = {str(k): str(v) for k, v in values.items()}
            self._valve_gone_at[climate] = now_ts  # as if gone until now: what comes back first is a repetition

    def _note_valve_values(self, climate_entity: str, states: list[State], now_ts: float) -> float | None:
        """Watch the valve's values for a repetition (VALVE_REPLAY_*); the time up to which reports are no report."""
        shown = {state.entity_id: _fingerprint(state) for state in states if state.state not in BAD_STATES}
        if not shown:
            self._valve_gone_at.setdefault(climate_entity, now_ts)
            return self._valve_replay_until.get(climate_entity)
        before = self._valve_values.get(climate_entity)
        gone = self._valve_gone_at.pop(climate_entity, None)
        same = before is not None and all(before.get(entity_id) == value for entity_id, value in shown.items())
        if gone is not None and now_ts - gone <= VALVE_REPLAY_GONE_MAX_S and same:
            self._valve_replay_until[climate_entity] = now_ts + VALVE_REPLAY_BURST_S
        until = self._valve_replay_until.get(climate_entity)
        if until is not None and now_ts <= until and not same:
            until = self._valve_replay_until.pop(climate_entity)  # another value within the burst: the valve restarted
            until = None
        self._valve_values[climate_entity] = {**(before or {}), **shown}
        return until

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

    def read_power(self, entity_id: str | None, now: datetime, max_age_s: float | None = None) -> SensorReading:
        """A power in W, whatever unit the sensor reports it in; one that is no power is no reading.

        A plug in kW gave 0.35 "W", valid: the COP was never learned and nothing said why (review 2026-11-08).
        A sensor without a unit is taken as W, as before.
        """
        state = self._get(entity_id)
        unit = None if state is None else state.attributes.get("unit_of_measurement")
        factor = _power_factor(unit)
        if factor is None:
            # Whatever else is wrong with it: a number in another unit is no power, also once it has gone quiet. Named
            # only while it was fresh, 8 VA counted as 8 W from the plug six minutes later (review 2026-11-13).
            reading = self.read_number(entity_id, RANGE_POWER_W, now, max_age_s)
            return reading if reading.value is None else replace(reading, valid=False, reason="wrong_unit", unit=str(unit))
        reading = self.read_number(entity_id, (RANGE_POWER_W[0] / factor, RANGE_POWER_W[1] / factor), now, max_age_s)
        return reading if reading.value is None else replace(reading, value=reading.value * factor)

    def read_in_unit(
        self, entity_id: str | None, rng: tuple[float, float], now: datetime, units: Mapping[str, float], max_age_s: float | None
    ) -> SensorReading:
        """A reading in the unit of `units` whose factor is 1, whatever of them the sensor reports in.

        A sensor in another unit is no reading ("wrong_unit"): a gas meter in kWh was taken as m³ and multiplied by
        the calorific value once more, one in litres counted a thousandfold (review 2026-11-13). One without a unit
        is taken as it is, as before.
        """
        state = self._get(entity_id)
        unit = None if state is None else state.attributes.get("unit_of_measurement")
        factor = 1.0 if unit in (None, "") else units.get(str(unit).strip().lower())
        if factor is None:
            reading = self.read_number(entity_id, rng, now, max_age_s)
            if reading.value is None:
                return reading
            if (entity_id, unit) not in self._wrong_units:
                self._wrong_units.add((entity_id, unit))
                known = " / ".join(units)
                _LOGGER.warning("thriftherm: %s reports in %s, which is none of %s: not read", entity_id, unit, known)
            return replace(reading, valid=False, reason="wrong_unit", unit=str(unit))
        reading = self.read_number(entity_id, (rng[0] / factor, rng[1] / factor), now, max_age_s)
        return reading if reading.value is None else replace(reading, value=reading.value * factor)

    def _unchanged_s(self, reading: SensorReading, now: datetime) -> float | None:
        """How long a reading's value has not changed, None without a value."""
        state = self._get(reading.entity_id)
        if reading.age_s is None or state is None:
            return None
        return max((now - state.last_changed).total_seconds(), 0.0)

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

    def _zha_device(self, entity_id: str) -> str | None:
        """The device of an entity if ZHA pairs it (a "zha" identifier in the device registry), else None."""
        if entity_id in self._zha_devices:
            return self._zha_devices[entity_id]
        entry = er.async_get(self.hass).async_get(entity_id)
        device = None if entry is None or entry.device_id is None else dr.async_get(self.hass).async_get(entry.device_id)
        found = device.id if device is not None and any(ident[:1] == (ZHA,) for ident in device.identifiers) else None
        self._zha_devices[entity_id] = found
        return found

    def _zha_seen_s(self, entity_ids: Iterable[str], now: datetime) -> float | None:
        """How long ago ZHA last saw the devices of these entities (the newest), None without such a sign (see `ZHA`).

        Asked only while ZHA is loaded: then its helpers are imported already, and an installation
        without ZHA does no more than before.
        """
        if ZHA not in self.hass.config.components:
            return None
        devices = dict.fromkeys(device for entity_id in entity_ids if (device := self._zha_device(entity_id)) is not None)
        ages = [age for device in devices if (age := _zha_device_seen_s(self.hass, device, now)) is not None]
        return min(ages, default=None)

    def valves_behind(self, climate_entity: str) -> tuple[str, ...]:
        """Every entity of the valve devices a room thermostat drives (see `_driven`); a plain one is its own valve.

        Looked up anew for every snapshot (`build` empties the cache): kept for good, a broken
        valve replaced and Better Thermostat pointed at the new one left the old entities
        watched, and the room counted as silent until the integration was reloaded.
        """
        cached = self._valve_entities.get(climate_entity)
        if cached is not None:
            return cached
        registry = er.async_get(self.hass)
        valves = _driven(self.hass, climate_entity)
        if valves is None:
            return ()  # not cached: the registry may not be loaded yet
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

    def wraps_thermostats(self, climate_entity: str | None) -> bool:
        """True when a room thermostat drives thermostats of its own (Better Thermostat, Versatile Thermostat over a climate)."""
        if not climate_entity:
            return False
        behind = self.valves_behind(climate_entity)
        return climate_entity not in behind and any(e.startswith("climate.") for e in behind)

    def _a_valve_flat(self, valves: tuple[str, ...]) -> bool:
        """Whether a valve's battery reads `VALVE_BATTERY_FLAT_PCT` or less (an unknown reading is no flat one)."""
        for valve in valves:
            entity = valve_view(self.hass, valve).caps.battery
            state = self.hass.states.get(entity) if entity else None
            try:
                if state is not None and float(state.state) <= VALVE_BATTERY_FLAT_PCT:
                    return True
            except ValueError:
                continue
        return False

    def thermostat_silent_s(self, climate_entity: str | None, now: datetime) -> float | None:
        """How long the valve behind a room thermostat has been silent, None if unknown.

        Measured 2026-09-27: the bathroom valve dropped out of the Zigbee network at 15:50
        and kept its last setpoint; Better Thermostat went on reporting "heat" at 21 °C.
        A healthy valve that reports what it had leaves no trace either (Zigbee2MQTT, ZHA):
        silence alone counts only after long (room.THERMOSTAT_SILENT_S).

        A valve paired with ZHA was also heard when ZHA last saw it, unless ZHA calls it
        unavailable: its entities keep their timestamps while the values stay the same (see `ZHA`).
        """
        if not climate_entity:
            return None
        entities = self.valves_behind(climate_entity)
        if not entities:
            return None
        states = [state for entity_id in entities if (state := self._get(entity_id)) is not None]
        repeated_until = self._note_valve_values(climate_entity, states, now.timestamp())
        ages = [
            _state_age_s(state, now)
            for state in states
            if state.state not in BAD_STATES
            # a value repeated by a broker or Home Assistant restart is no report (see VALVE_REPLAY_*)
            and (repeated_until is None or _reported_ts(state) > repeated_until)
        ]
        if (seen := self._zha_seen_s(entities, now)) is not None:
            ages.append(seen)
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

    def thermostat_gone_s(self, climate_entity: str | None, now: datetime) -> float | None:
        """How long every entity of the valve behind a room thermostat has been unavailable or unknown, None if not.

        An unavailable valve is silent at once, however recently it reported (review 2026-11-08):
        the silence alone counts only after hours. Not while ZHA still sees the device.
        """
        if not climate_entity:
            return None
        entities = self.valves_behind(climate_entity)
        states = [state for entity_id in entities if (state := self._get(entity_id)) is not None]
        if not states or any(state.state not in BAD_STATES for state in states):
            return None
        if self._zha_seen_s(entities, now) is not None:
            return None
        return min(max((now - state.last_changed).total_seconds(), 0.0) for state in states)

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
        """A room temperature, still valid while its device keeps reporting other values.

        A device paired with ZHA is alive as well while ZHA has seen it within the same age (see `ZHA`).
        The reading keeps its own age, and a day without a change is too long either way. Six hours at most,
        unless the room sensors report only on a change (option): then up to the max. sensor age. `heard_s` is when
        its device was last heard.
        """
        max_age = self.params.sensor_max_age_s
        if not self.params.room_sensors_on_change:
            max_age = min(max_age, ROOM_TEMP_MAX_AGE_S)
        reading = self._read_quiet_temperature(entity_id, now, max_age)
        if reading.age_s is None or entity_id is None:
            return reading
        # when the device was last heard: Zigbee2MQTT writes no unchanged value anew, its humidity goes on
        others = (self._device_age_s(entity_id, now), self._zha_seen_s((entity_id,), now))
        ages = [reading.age_s, *(a for a in others if a is not None)]
        return replace(reading, heard_s=min(ages))

    def _read_quiet_temperature(
        self, entity_id: str | None, now: datetime, max_age: float, value_range: tuple[float, float] = RANGE_ROOM_TEMP,
        unchanged_max: float = ROOM_TEMP_UNCHANGED_MAX_S,
    ) -> SensorReading:
        """A temperature past `max_age` that its device shows to be alive: its value just does not change."""
        reading = self.read_number(entity_id, value_range, now, max_age_s=max_age)
        if reading.reason != "stale" or entity_id is None or (reading.age_s or 0.0) > unchanged_max:
            return reading
        alive = [age for age in (self._device_age_s(entity_id, now), self._zha_seen_s((entity_id,), now)) if age is not None]
        if alive and min(alive) <= max_age:
            return replace(reading, valid=True, reason=None)
        return reading

    def valve_room_reading(self, entity_id: str | None, now: datetime) -> tuple[float | None, bool]:
        """The room temperature for a valve driven directly, and whether it may go to the valve.

        Fresh within two hours (valve_control.EXTERNAL_MAX_AGE_S), or older while the sensor's device reports other
        values: Home Assistant does not write an unchanged Zigbee2MQTT value anew, and measured 2026-11-14 the bathroom
        sensor showed 20.2 °C unchanged for two hours while it reported every half hour (Phase B). With sensors that
        report only on a change (option), as for the room's call.
        """
        if self.params.room_sensors_on_change:
            # a sensor that reports only on a change (issue #7) holds for the valve as for the call: its silence is the
            # room staying within its step, not a dead sensor (the rückfall check of 2026-11-15, R2)
            reading = self.read_room_temperature(entity_id, now)
        else:
            # past half an hour only while its device shows it alive, as long as for the call (24 h): with 6 h a ZHA or
            # Zigbee2MQTT sensor alive but unchanged put the valve on its own sensor (issue #2; check of 2026-11-15, R1)
            reading = self._read_quiet_temperature(entity_id, now, EXTERNAL_MAX_AGE_S)
        return reading.value_or_none, reading.valid

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

    def _schedule_blocks(self, entity_id: str) -> Week | None:
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

    def cool_schedule(self, cfg: RoomConfig) -> CoolSchedule | None:
        """The room's cooling schedule helper: its week where it tells its blocks, else its state and next start.

        None without one, or while it is unavailable: the cooling comfort then holds all day (engines/room_cooling.py).
        """
        if not cfg.cooling_schedule_entity:
            return None
        state = self._get(cfg.cooling_schedule_entity)
        if state is None or state.state in BAD_STATES:
            return None
        week = self._schedule_blocks(cfg.cooling_schedule_entity)
        if week is not None:
            return CoolSchedule(week=week)
        raw = state.attributes.get("next_event")
        parsed = None if raw is None else dt_util.parse_datetime(str(raw))
        active = state.state == "on"
        return CoolSchedule(
            active=active,
            temperature=self.read_attr_float(state, "temperature"),
            next_start=None if parsed is None or active else dt_util.as_local(parsed),
        )

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
        # the room engine reads the target at a later time from it, as at the return from away
        week = self._schedule_blocks(cfg.schedule_entity)
        raw = state.attributes.get("next_event")
        parsed = None if raw is None else dt_util.parse_datetime(str(raw))
        if parsed is not None:
            # while a block runs, the next event is its end; otherwise the next start
            event = dt_util.as_local(parsed)
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
            next_change=change, next_change_target=change_target, week=week,
        )

    def _room_state(
        self,
        cfg: RoomConfig,
        now: datetime,
        override: Override | None = None,
        drying: DryingState | None = None,
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
        # Better Thermostat 2.0 names why it holds the valve shut (devices released, valve maintenance, startup),
        # sometimes before `hvac_action` leaves heating (issue #8). Without the attribute nothing changes.
        if heating is not None and climate is not None and climate.attributes.get("heating_blocked_by") not in (None, "", "none"):
            heating = False
        if cfg.direct or (cfg.climate_entity and valve_view(self.hass, cfg.climate_entity).hysteresis_k > 0.0):
            # A valve says idle while its own hysteresis keeps it shut (seen 2026-11-15: a TRVZB opening 1 K below its
            # setpoint only). Thriftherm opens it by its setpoint instead (coordinator: hysteresis on top), driven
            # directly or entered as the room's thermostat. Better Thermostat's idle stays: it is its own decision.
            heating = None
        own_target = trv_mode is not None and climate is not None and applies_own_target(climate.attributes)
        schedule = self._schedule_state(cfg)

        gain: float | None = None
        for entity_id in cfg.internal_gain_sensors:
            reading = self.read_power(entity_id, now)
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

        abs_hist = self._abs_hum_history.get(cfg.key)
        if abs_hist is not None:
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
            abs_humidity_history=() if abs_hist is None else tuple(abs_hist),
            override=override,
            drying=drying or DryingState(),
            boost_until_ts=boost_until,
            trv_hvac_mode=trv_mode,
            thermostat_heating=heating,
            thermostat_own_target=own_target,
            thermostat_silent_s=self.thermostat_silent_s(cfg.climate_entity, now),
            valve_unable="battery" if cfg.direct and self._a_valve_flat(cfg.valve_entities) else None,
            thermostat_gone_s=self.thermostat_gone_s(cfg.climate_entity, now),
            # Better Thermostat (or Versatile Thermostat over a climate) takes a setpoint at once and passes it
            # on to the thermostats it drives; a switch it drives takes none, and need not report a new target
            thermostat_wrapped=self.wraps_thermostats(cfg.climate_entity),
            thermostat_min_temp=self.read_attr_float(climate, "min_temp"),
            thermostat_max_temp=self.read_attr_float(climate, "max_temp"),
            thermostat_step=self.read_attr_float(climate, "target_temp_step"),
        )

    # ------------------------------------------------------------------ midea
    def _heat_pump_state(self, now: datetime) -> HeatPumpState:
        cfg = self.config
        climate = self._get(cfg.get(CONF_HEAT_PUMP_CLIMATE))
        available = climate is not None and climate.state not in BAD_STATES
        attrs = climate.attributes if climate is not None else {}

        compressor_hz = self.read_attr_float(climate, "compressor_frequency")
        self._heat_pump_gone_since = None if available else self._heat_pump_gone_since or now
        # A dropout of its bridge for ten seconds at most goes on with the run, as the safety and the control bridge it
        # (review 2026-11-08: its count began anew, ten minutes of warming up and no COP).
        dropout = not available and (now - self._heat_pump_gone_since).total_seconds() <= HEAT_PUMP_DROPOUT_S
        if available and compressor_hz is not None and compressor_hz > 0:
            if self._compressor_started_at is None:
                self._compressor_started_at = now
        elif not dropout:
            self._compressor_started_at = None
        running_s = None
        if self._compressor_started_at is not None:
            running_s = (now - self._compressor_started_at).total_seconds()

        error_code = attrs.get("error_code")
        try:
            error_code = None if error_code is None else int(error_code)
        except (TypeError, ValueError, OverflowError):  # "inf" is no error code, and must not end the cycle
            error_code = None

        plug_power = self.read_power(cfg.get(CONF_HEAT_PUMP_POWER), now, max_age_s=PLUG_POWER_MAX_AGE_S)
        if plug_power.reason == "stale" and plug_power.value <= PLUG_STANDBY_MAX_W and running_s is None:
            plug_power = replace(plug_power, valid=True, reason=None)  # the compressor stands: a steady low draw is not reported
        # past these limits a reading reaches the COP and the control as invalid
        intake_age, outlet_age = self.params.cop_intake_max_age_s, self.params.cop_outlet_max_age_s
        intake_temp = self._read_quiet_temperature(cfg.get(CONF_HEAT_PUMP_INTAKE_TEMP), now, intake_age)
        outlet_temp = self._read_quiet_temperature(cfg.get(CONF_HEAT_PUMP_OUTLET_TEMP), now, outlet_age, (-10.0, 80.0), OUTLET_UNCHANGED_MAX_S)

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
                # the defrost signs read these: a report older than its limit stays out, or a cold hang locks the unit out
                outlet_temp=outlet_temp.value_or_none if (outlet_temp.age_s or 0.0) <= outlet_age else None,
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
        gas_volume = self.read_in_unit(cfg.get(CONF_GAS_VOLUME), (0.0, 1e7), now, _GAS_VOLUME_UNITS, max_age_s=0)
        gas_flow = self.read_in_unit(cfg.get(CONF_GAS_FLOW), RANGE_GAS_FLOW_M3H, now, _GAS_FLOW_UNITS, max_age_s=0)
        # no meter reading (age None) cannot speak for the flow: 1.2 m³/h still meant "burning" after 10 h
        # Whether a value moved goes by its last change, not its last report: a meter that sends its
        # unchanged reading every minute (force_update, Modbus, REST) kept a flow of 1.2 valid for good
        # (review 2026-11-08).
        volume_unchanged_s = self._unchanged_s(gas_volume, now)
        volume_quiet = volume_unchanged_s is None or volume_unchanged_s > GAS_FLOW_STUCK_S
        flow_unchanged_s = self._unchanged_s(gas_flow, now) or 0.0
        if gas_flow.valid and gas_flow.value > 0 and flow_unchanged_s > GAS_FLOW_STUCK_S and volume_quiet:
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
        # Read as the room thermometers are (review 2026-11-08): a still night held 4.0 °C for over the
        # hour, ZHA and Zigbee2MQTT left its timestamps alone, and a sensor seen minutes before was stale.
        rh = self._read_quiet_temperature(
            self.config.get(CONF_OUTDOOR_RH_SENSOR), now, OUTDOOR_MAX_AGE_S, RANGE_HUMIDITY, OUTDOOR_UNCHANGED_MAX_S
        )
        weather = self._get(self.config.get(CONF_WEATHER_ENTITY))
        if not rh.valid:
            # the weather entity's humidity stands in for a humidity sensor, wherever the temperature comes from
            w_rh = self.read_attr_float(weather, "humidity")
            if weather is not None and w_rh is not None and RANGE_HUMIDITY[0] <= w_rh <= RANGE_HUMIDITY[1]:
                rh = SensorReading(weather.entity_id, w_rh, _state_age_s(weather, now), True, None)
        for entity_id in sensors:
            reading = self._read_quiet_temperature(entity_id, now, OUTDOOR_MAX_AGE_S, RANGE_OUTDOOR_TEMP, OUTDOOR_UNCHANGED_MAX_S)
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
        self._zha_devices.clear()  # a device paired anew, with ZHA or elsewhere, counts from the next build
        if self._started_at is None:
            self._started_at = now
        rooms = {
            cfg.key: replace(
                self._room_state(
                    cfg, now, overrides.get(cfg.key), drying_states.get(cfg.key), boosts.get(cfg.key),
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
            if cfg.cooling_schedule_entity:
                watched.add(cfg.cooling_schedule_entity)
        # The gas meter and the pump say when the burner runs. A short burn lasts about
        # as long as one update cycle, so waiting for the next cycle loses it - and a lost
        # burn is a lost cycling signal. Both are quiet while the burner is off.
        for key in (CONF_HEAT_PUMP_CLIMATE, CONF_GAS_FLOW, CONF_BOILER_PUMP_STATE, CONF_BOILER_PUMP_RUNNING, CONF_BOILER_FLAME):
            if self.config.get(key):
                watched.add(self.config[key])
        return watched
