"""Coordinator: one snapshot per cycle, all engines, one result set."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DOMAIN, ATTR_ENTITY_ID, ATTR_SERVICE_DATA, EVENT_CALL_SERVICE
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import boiler_profiles, executors, issues, learning_reset, persistence
from .adapters.config import system_type_from_config
from .adapters.inputs import SnapshotBuilder
from .const import (
    CONF_BOILER_ALLOW_ACTIVE,
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_FLOW_SETPOINT_ENTITY,
    CONF_BOILER_HEATING_ENABLE_ENTITY,
    CONF_BOILER_NOMINAL_INPUT_KW,
    CONF_BOILER_PROFILE,
    CONF_BOOST_DURATION_MIN,
    CONF_GAS_VOLUME,
    CONF_HEAT_PUMP_ALLOW_ACTIVE,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_ROOM,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_ROOM_ALLOW_ACTIVE,
    CONF_ROOM_ECHO_FILTER,
    BOILER_PLAN_BLOCK,
    BOILER_PLAN_HEAT,
    CTRL_ACTIVE,
    CTRL_OFF,
    CTRL_SHADOW,
    DEFAULT_BOILER_ALLOW_ACTIVE,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DEFAULT_BOOST_DURATION_MIN,
    DEFAULT_HEAT_PUMP_ALLOW_ACTIVE,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_ROOM_ALLOW_ACTIVE,
    DEFAULT_ROOM_ECHO_FILTER,
    DOMAIN,
    HEAT_PUMP_COOLING_USER,
    HEAT_PUMP_PLAN_HEAT,
    HEAT_PUMP_UNAVAILABLE,
    MODES,
    MODE_AUTO,
    MODE_AWAY,
    MODE_BOILER_ONLY,
    MODE_HEAT_PUMP_ONLY,
    MODE_OFF,
    PUMP_STATE_ON,
    SAFETY_FALLBACK,
    SAFETY_OK,
    SOURCE_HEAT_PUMP,
    SOURCE_NONE,
    STORAGE_KEY,
    STORAGE_VERSION,
    SYSTEM_GAS,
    SYSTEM_NONE,
    UPDATE_INTERVAL_S,
)
from .engines import (
    hand,
    advisor,
    boiler as boiler_engine,
    boiler_control,
    boiler_learning,
    economics,
    gas_split,
    heat_call,
    heat_pump as heat_pump_engine,
    heat_pump_control,
    room as room_engine,
    room_control,
    safety,
)
from .engines.heat_pump_tracking import HeatPumpTracker
from .engines.safety import HEAT_PUMP_ISSUE_PREFIX
from .engines import cop_profiles
from .engines.learning import CoolRateStats, CopMap, HeatRateStats
from .engines.boiler_memory import BoilerInputs, BoilerMemory, curve_flow
from .engines.common import round_half
from .models import SettingChange
from .engines.heat_pump_control import ControlInputs, ControlMemory
from .engines.room_control import RoomCtrlMemory
from .models import (
    Advice,
    BoilerCommand,
    BoilerResult,
    DryingState,
    EconomicsResult,
    HeatingSnapshot,
    HeatPumpCommand,
    HeatPumpResult,
    Override,
    RoomCommand,
    RoomResult,
    SafetyResult,
    reason,
)
from .plan_log import PlanLog

_LOGGER = logging.getLogger(__name__)

EBUS_PRIORITY_REFRESH_S = 600.0  # a restart of ebusd forgets the priority; renew it so a gap lasts minutes, not an hour
EBUS_READ_WHILE_BURNING_S = 60.0  # fresh values once a minute while gas is burning, all the time without a gas meter
OUTDOOR_FALLBACK_MAX_AGE_S = 6 * 3600.0  # how long the last known outdoor temperature stands in
OUTDOOR_LAST_KNOWN = "last_known"  # the outdoor source shown while it does
# After a restart the Zigbee room sensors come back one by one (measured 2026-09-18: the bathroom
# after 38 s). Until they have, the boiler gets no command instead of a block decided on gaps.
ROOM_DATA_GRACE_S = 300.0
# The cycles the boiler learning by load is judged on: about a day at one row a minute (extra
# cycles started by user actions take their rows from it).
LOAD_LOG_ROWS = 1440


STORE_SAVE_INTERVAL_S = 600.0
SETPOINT_SETTLE_S = 1200.0  # after a setpoint change the rooms and their thermostats settle first

type ThrifthermConfigEntry = ConfigEntry["ThrifthermCoordinator"]


def _modes(allow_active: bool) -> list[str]:
    """Control modes on offer: "active" only once released in the options."""
    return [CTRL_OFF, CTRL_SHADOW, CTRL_ACTIVE] if allow_active else [CTRL_OFF, CTRL_SHADOW]


_GONE = ("unavailable", "unknown")
_BROAD_TARGETS = ("area_id", "device_id", "label_id", "floor_id")


@callback
def _is_climate_call(data: Any) -> bool:
    return data.get(ATTR_DOMAIN) == "climate"


def _runs_through(advice: Advice, setpoint: float | None) -> Advice:
    """The advice of a cycle in which a unit that follows the rooms heats on with no room calling.

    It does so by design and regulates by itself. "No room needs heat" next to it was taken for a
    fault three times on the test day of 2026-10-13: the reason says that it runs through instead.
    """
    runs = reason("heat_pump_runs_through", target_c=setpoint)
    return replace(advice, reasons=tuple(runs if item.code == "no_demand" else item for item in advice.reasons))


class ThrifthermCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the state between cycles, runs the engines once per cycle and sends what is released."""

    def __init__(self, hass: HomeAssistant, entry: ThrifthermConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_S),
            config_entry=entry,
        )
        self.entry = entry
        self.config: dict[str, Any] = {**entry.data, **entry.options}
        self.builder = SnapshotBuilder(hass, self.config)
        self.mode: str = MODE_AUTO
        self.plan_log = PlanLog()
        self.heat_pump_tracker = HeatPumpTracker()
        self._last_cheaper: str | None = None
        self._prices_unusable = False  # warned once until the prices work again
        self._last_drying: dict[str, bool] = {}
        # room engine state
        self.overrides: dict[str, Override] = {}
        self.drying_states: dict[str, DryingState] = {}
        self.away_return_ts: float | None = None
        # only the date of the return is picked yet (23:59 stands in for the time): away ends then, no pre-heating
        self.away_return_provisional = False
        # entities that show away mode and its return straight from here, written the moment either changes
        self._away_listeners: list[CALLBACK_TYPE] = []
        # learning
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
        self.cop_map = CopMap()
        self.heat_rates = HeatRateStats()
        self.cool_rates = CoolRateStats()
        self._last_store_save: float = 0.0
        self._store_dirty = False
        # heat pump control
        self.control_mode: str = CTRL_SHADOW
        self.control_memory = ControlMemory()
        self.boosts: dict[str, float] = {}
        # boiler control
        self.boiler_control_mode: str = CTRL_SHADOW
        self.boiler_memory = BoilerMemory()
        # what the boiler learning by load will be judged on: one compact row per cycle, about a day
        self.load_log: deque[list] = deque(maxlen=LOAD_LOG_ROWS)
        self.last_learning_reset: dict[str, Any] | None = None
        # gas for heating and gas for hot water, counted apart (engines/gas_split.py)
        self.gas_split = gas_split.GasSplit()
        # room control via the room thermostats
        self.room_control_mode: str = CTRL_SHADOW
        self.room_ctrl_memory: dict[str, RoomCtrlMemory] = {}
        # which rooms call the boiler (hysteresis, stall, end of comfort), kept over a restart
        self.call_memory: dict[str, heat_call.CallMemory] = {}
        # echo filter: setpoint changes of each room thermostat and the valves behind it
        self._valves: dict[str, tuple[str, tuple[str, ...]]] = {}  # room -> (thermostat, valves)
        # Who changed what we command (engines/hand.py): the contexts of our own service calls,
        # and the last change of mode or setpoint of every thermostat and of the heat pump.
        self._own_contexts: deque[str] = deque(maxlen=64)
        self._asked_contexts: deque[str] = deque(maxlen=64)  # service calls others made on what we command
        self._commanded: frozenset[str] = frozenset()
        self._last_settings: dict[str, tuple[str, Any]] = {}  # entity -> (mode, setpoint) when last reachable
        self._setting_changes: dict[str, SettingChange] = {}
        self._settings_watched_since_ts: float | None = None
        self._stored_heat_pump_watch: dict[str, Any] = {}  # from the store, until the watch is taken up again
        self._room_memory_before: dict[str, RoomCtrlMemory] = {}  # as it was before this cycle's plan
        # Knobs turned at the valves behind a room thermostat (room_control.knob_turn)
        self._valve_room: dict[str, str] = {}  # valve -> room
        # what was asked of the valves lately: (ts, context, valve or None for all, setpoint asked)
        self._valve_calls: deque[tuple[float, str, str | None, float | None]] = deque(maxlen=128)
        self._knob_turns: dict[str, tuple[float, float]] = {}  # room -> (ts, by how much), until the next cycle takes it
        self._knob_timer: CALLBACK_TYPE | None = None
        self._setpoints: dict[str, deque[tuple[float, float]]] = {}  # entity -> (ts, setpoint)
        self._setpoints_unsub: CALLBACK_TYPE | None = None
        self.echoes_ignored: dict[str, tuple[float, float]] = {}  # room -> (ts, value) of the last one
        self._preheat_latch: dict[str, float] = {}
        self.expected_unique_ids: set[str] = set()  # filled by the entities; the rest is stale
        self.issue_since: dict[str, float] = {}
        self._ebus_priority_ts: float | None = None
        self._boiler_send_failed = False  # the last write to a boiler through entities did not go through
        self._heat_pump_call_failures = 0  # starts or adjustments in a row whose call failed
        self._heat_pump_retry_ts = 0.0  # no further try before then
        self._ebus_signal_lost_since: float | None = None
        self._last_outdoor: tuple[float, float] | None = None  # (°C, timestamp) for sensor outages
        self._ebus_read_ts = 0.0
        # a boiler addressed through entities keeps what it was sent: None = not yet known this run
        self._boiler_entities_released: bool | None = None
        self._boiler_handed_back_flow: float | None = None
        self._boiler_let_go = False  # handed back for good: this coordinator commands no more
        self._boiler_lock = asyncio.Lock()  # one write to the boiler's entities at a time
        self._started_ts = time.time()
        self._frost_rooms: tuple[str, ...] = ()
        self._last_room_plans: dict[str, tuple[str, float | None]] = {}
        self._last_targets: dict[str, float] = {}
        self._last_target_change_ts: float = 0.0
        self._last_target_change_rooms: tuple[str, ...] = ()  # whose setpoint started the learning pause
        self.room_temps: dict[str, dict[str, float]] = {}

    @property
    def room_allow_active(self) -> bool:
        return bool(self.config.get(CONF_ROOM_ALLOW_ACTIVE, DEFAULT_ROOM_ALLOW_ACTIVE))

    @property
    def room_control_modes(self) -> list[str]:
        return _modes(self.room_allow_active)

    @property
    def system_type(self) -> str:
        return system_type_from_config(self.config)

    @property
    def has_boiler(self) -> bool:
        """True only for an own boiler we can read and address, over ebusd or through entities.

        District heating and heat-pump-only installations have no boiler of
        their own, so no boiler command may ever be built or sent for them.
        """
        return self.system_type == SYSTEM_GAS

    @property
    def has_heat_source(self) -> bool:
        """True when there is a central heat source to compare the heat pump against."""
        return self.system_type != SYSTEM_NONE

    @property
    def boiler_allow_active(self) -> bool:
        return self.has_boiler and bool(self.config.get(CONF_BOILER_ALLOW_ACTIVE, DEFAULT_BOILER_ALLOW_ACTIVE))

    @property
    def boiler_control_modes(self) -> list[str]:
        if not self.has_boiler:
            return [CTRL_OFF]
        return _modes(self.boiler_allow_active)

    @property
    def boiler_profile(self) -> boiler_profiles.BoilerProfile:
        return boiler_profiles.profile(self.config.get(CONF_BOILER_PROFILE))

    @property
    def boiler_entities(self) -> tuple[str, str] | None:
        """(flow setpoint number, heating switch) of a boiler addressed through entities, else None: ebusd."""
        setpoint = self.config.get(CONF_BOILER_FLOW_SETPOINT_ENTITY)
        enable = self.config.get(CONF_BOILER_HEATING_ENABLE_ENTITY)
        return (str(setpoint), str(enable)) if setpoint and enable else None

    @property
    def boiler_setmode_topic(self) -> str | None:
        if self.boiler_entities:
            return None
        return self._ebus_topic(self.boiler_profile.setmode_message, "set")

    def _ebus_topic(self, message: str, verb: str) -> str:
        circuit = str(self.config.get(CONF_BOILER_EBUS_CIRCUIT) or DEFAULT_BOILER_EBUS_CIRCUIT).strip()
        return f"ebusd/{circuit}/{message}/{verb}"

    @property
    def has_heat_pump(self) -> bool:
        return self.builder.has_heat_pump

    @property
    def allow_active(self) -> bool:
        return bool(self.config.get(CONF_HEAT_PUMP_ALLOW_ACTIVE, DEFAULT_HEAT_PUMP_ALLOW_ACTIVE))

    @property
    def control_modes(self) -> list[str]:
        return _modes(self.allow_active)

    @property
    def modes(self) -> list[str]:
        """Operating modes on offer: "heat pump only" needs a heat pump, "boiler only" a central heat source.

        Either without its source would leave nothing to heat but the frost protection.
        """
        return [
            mode
            for mode in MODES
            if (mode != MODE_HEAT_PUMP_ONLY or self.has_heat_pump) and (mode != MODE_BOILER_ONLY or self.has_heat_source)
        ]

    @property
    def _override_default_min(self) -> int:
        return int(self.config.get(CONF_OVERRIDE_DEFAULT_MIN, DEFAULT_OVERRIDE_DEFAULT_MIN))

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            persistence.restore(self, data)
            scopes, rooms = learning_reset.invalidated(data.get("learning_basis"), learning_reset.basis(self.config))
            if scopes:
                _LOGGER.warning("thriftherm: settings changed, forgetting learned %s %s", sorted(scopes), sorted(rooms))
                self._forget(scopes, rooms or None, learning_reset.REASON_CONFIGURATION)
            if data.get("learning_basis") != learning_reset.basis(self.config):
                self._store_dirty = True
        if not self.has_boiler:
            # no own boiler: never plan or send a SetMode, whatever was stored
            self.boiler_control_mode = CTRL_OFF
        watched = self.builder.watched_entities()
        if watched:
            self.entry.async_on_unload(async_track_state_change_event(self.hass, list(watched), self._on_watched_change))
        self._track_setpoints()
        self.entry.async_on_unload(self._untrack_setpoints)
        self.entry.async_on_unload(self._cancel_knob_timer)
        self._track_settings()

    def _track_settings(self) -> None:
        """Record who changes the mode or the setpoint of what the control commands."""
        commanded = {room.climate_entity for room in self.builder.rooms if room.climate_entity}
        if self.has_heat_pump and self.config.get(CONF_HEAT_PUMP_CLIMATE):
            commanded.add(self.config[CONF_HEAT_PUMP_CLIMATE])
        if not commanded:
            return
        self._commanded = frozenset(commanded)
        for entity_id in commanded:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in _GONE:
                self._last_settings[entity_id] = (state.state, state.attributes.get("temperature"))
        # their state changes are put on record by _on_watched_change: every commanded entity is watched
        self.entry.async_on_unload(self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._on_climate_call, _is_climate_call))
        self._settings_watched_since_ts = time.time()
        self._resume_heat_pump_watch()

    def _resume_heat_pump_watch(self) -> None:
        """Take up the watch over the heat pump's settings where the last run of Home Assistant left it.

        Whether a unit that differs from our last command was touched by somebody is told by
        what was seen since that command. A restart broke that off: an "off" after the fan
        run-on that the unit had not taken read, two minutes and a restart later, as somebody's
        fan-only mode, for good. If the unit stands as it was last seen, nothing happened in
        between and the watch goes on from when it began; if it does not, somebody changed it
        while nobody watched.
        """
        stored, self._stored_heat_pump_watch = self._stored_heat_pump_watch, {}
        entity_id = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        last, since = stored.get("last"), stored.get("since")
        if not entity_id or stored.get("entity") != entity_id or not isinstance(since, (int, float)):
            return
        if not (isinstance(last, list) and len(last) == 2):
            return
        before, now = (last[0], last[1]), self._last_settings.get(entity_id)
        if now is None:
            self._last_settings[entity_id] = before  # away just now: judged when it is back, against what it had
        elif now != before:
            change = SettingChange(time.time(), hand.DEVICE, setpoint=before[1] != now[1], mode=before[0] != now[0])
            self._setting_changes[entity_id] = change
            return
        self._settings_watched_since_ts = float(since)
        kept = stored.get("change")
        if isinstance(kept, dict) and isinstance(kept.get("ts"), (int, float)) and kept.get("origin") in (hand.OURS, *hand.SHOWN):
            self._setting_changes[entity_id] = SettingChange(
                float(kept["ts"]), kept["origin"], setpoint=kept.get("setpoint") is True, mode=kept.get("mode") is True
            )

    def heat_pump_watch(self) -> dict[str, Any]:
        """What the store keeps of the watch over the heat pump's settings."""
        entity_id = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        last = self._last_settings.get(entity_id) if entity_id else None
        if last is None or self._settings_watched_since_ts is None:
            return {}
        change = self._setting_changes.get(entity_id)
        kept = None
        if change is not None:
            kept = {"ts": change.ts, "origin": change.origin, "setpoint": change.setpoint, "mode": change.mode}
        return {"entity": entity_id, "since": self._settings_watched_since_ts, "last": list(last), "change": kept}

    @callback
    def _on_climate_call(self, event: Event) -> None:
        """Remember a service call somebody else makes on an entity we command.

        An automation triggered by the clock carries neither a person nor a trigger; its call
        is all that tells the change it causes from one the device made itself.
        """
        ours = event.context.id in self._own_contexts
        data = event.data.get(ATTR_SERVICE_DATA) or {}
        targets = data.get(ATTR_ENTITY_ID)
        entities = [targets] if isinstance(targets, str) else list(targets or ())
        # A call that names entities counts when one of them is commanded: a thermostat wrapper
        # writing to its own valve names the valve, and must not be taken for somebody asking.
        # A call by area, device, label, floor or "all" may reach ours; whether it did shows in
        # the state change that carries its context.
        broad = "all" in entities or any(key in data for key in _BROAD_TARGETS)
        if not ours and (broad or any(entity in self._commanded for entity in entities)):
            self._asked_contexts.append(event.context.id)
        # What is asked of a valve behind a room thermostat (by that thermostat, or by anybody):
        # a setpoint that follows from it is no turn of the knob. That goes for calls in our own
        # context too: Better Thermostat passes the context of the call it received on to the
        # call it makes on its valve (adapters/generic.py of 1.9.3), and a valve that answers
        # after more than five seconds does so in a context of its own.
        asked = data.get("temperature")
        setpoint = float(asked) if isinstance(asked, (int, float)) and not isinstance(asked, bool) else None
        for valve in [None] if broad else [entity for entity in entities if entity in self._valve_room]:
            self._valve_calls.append((time.time(), event.context.id, valve, setpoint))
            # The room thermostat sets its valve again right after a turn of the knob: the turn
            # is its to judge. Taken over, its own target says so; written back over (Better
            # Thermostat with its child lock on), it is not ours to take over against it.
            room = self._valve_room.get(valve) if valve is not None and setpoint is not None else None
            if room is not None and self._knob_turns.pop(room, None) is not None:
                _LOGGER.info("thriftherm room %s: the room thermostat set %s after the turn, the turn is left to it", room, valve)

    def _note_setting_change(self, event: Event[EventStateChangedData]) -> SettingChange | None:
        """Put a change of mode or setpoint on record; returns the record made for this event, if any."""
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or new.state in _GONE:
            return None
        setting = (new.state, new.attributes.get("temperature"))
        before = self._last_settings.get(new.entity_id)
        self._last_settings[new.entity_id] = setting
        ts = new.last_updated.timestamp()
        if old is None or old.state in _GONE:
            # Back from being unreachable: nobody's change in itself. But if the device returns with
            # another setting than it left with, somebody changed it meanwhile, at the device.
            if before is None or before == setting:
                return None
            change = SettingChange(ts, hand.DEVICE, setpoint=before[1] != setting[1], mode=before[0] != setting[0])
            self._setting_changes[new.entity_id] = change
            return change
        mode, setpoint = new.state != old.state, setting[1] != old.attributes.get("temperature")
        if not mode and not setpoint:
            return None
        context = event.context
        origin = hand.origin(context.id, context.user_id, context.parent_id, self._own_contexts, self._asked_contexts)
        change = SettingChange(ts, origin, setpoint=setpoint, mode=mode)
        self._setting_changes[new.entity_id] = change
        return change

    def _asked_meanwhile(self, entity_id: str, since_ts: float, setpoint_only: bool = False) -> bool:
        """Somebody asked for another setting in Home Assistant since this cycle read the states.

        The command about to go out was planned without it and would overwrite it without a
        trace; left unsent, the next cycle finds the change and takes it for what it is. A change
        of the same instant counts: had the plan known it, there would be nothing to send.
        """
        change = self._setting_changes.get(entity_id)
        if change is None or change.origin not in hand.DELIBERATE or (setpoint_only and not change.setpoint):
            return False
        return change.ts >= since_ts

    def _own_context(self) -> Context:
        """A context for a command of ours: the change it causes is then known as ours."""
        context = Context()
        self._own_contexts.append(context.id)
        return context

    def _track_setpoints(self) -> None:
        """Record every setpoint change of the room thermostats and the valves behind them.

        Run at the start and after every cycle: a valve that is replaced, and the room
        thermostat pointed at the new one, must be watched without a reload. Until this was
        looked up again, the echo filter and the knob kept listening to the valve that was gone.
        """
        valves_of: dict[str, tuple[str, tuple[str, ...]]] = {}
        for room in self.builder.rooms:
            if not room.climate_entity:
                continue
            behind = self.builder.valves_behind(room.climate_entity)
            valves = tuple(e for e in behind if e.startswith("climate.") and e != room.climate_entity)
            if valves:  # only a thermostat that drives other valves can echo
                valves_of[room.key] = (room.climate_entity, valves)
        if valves_of == self._valves:
            return
        self._untrack_setpoints()
        self._valves = valves_of
        self._valve_room = {valve: room for room, (_, valves) in valves_of.items() for valve in valves}
        tracked = {e for thermostat, valves in valves_of.values() for e in (thermostat, *valves)}
        self._setpoints = {entity_id: self._setpoints.get(entity_id, deque(maxlen=60)) for entity_id in tracked}
        self._knob_turns = {room: turn for room, turn in self._knob_turns.items() if room in valves_of}
        if tracked:
            self._setpoints_unsub = async_track_state_change_event(self.hass, list(tracked), self._on_setpoint_change)

    def _untrack_setpoints(self) -> None:
        if self._setpoints_unsub is not None:
            self._setpoints_unsub()
            self._setpoints_unsub = None

    def _cancel_knob_timer(self) -> None:
        if self._knob_timer is not None:
            self._knob_timer()
            self._knob_timer = None

    @callback
    def _on_setpoint_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if new is None:
            return
        value = new.attributes.get("temperature")
        if not isinstance(value, (int, float)) or (old is not None and old.attributes.get("temperature") == value):
            return
        history = self._setpoints.get(new.entity_id)
        if history is None:
            return
        ts = new.last_updated.timestamp()
        before = tuple(history)
        history.append((ts, float(value)))
        room = self._valve_room.get(new.entity_id)
        previous = old.attributes.get("temperature") if old is not None else None
        if room is None or old is None or old.state != new.state or not isinstance(previous, (int, float)):
            return
        # A valve's setpoint moved. With a service call behind it, it was commanded (by the room
        # thermostat in front of it, or by somebody in Home Assistant). Without one, somebody
        # turned its knob - unless it is the valve's late answer or a late echo.
        context = event.context
        recent = ts - room_control.ECHO_WINDOW_S
        calls = [call for call in self._valve_calls if call[2] in (None, new.entity_id) and call[0] >= recent]
        if context.user_id or context.parent_id or context.id in self._own_contexts or any(c[1] == context.id for c in calls):
            return
        asked = tuple(call[3] for call in calls if call[3] is not None)
        turned = room_control.knob_turn(float(previous), float(value), before, ts, asked)
        if turned is None:
            return
        self._knob_turns[room] = (ts, turned)  # the latest counts: each is measured from what the thermostat had set
        _LOGGER.info("thriftherm room %s: knob of %s turned by %+.1f K", room, new.entity_id, turned)
        # judged once the room thermostat has had its chance to pass the turn on by itself
        self._cancel_knob_timer()
        self._knob_timer = async_call_later(self.hass, room_control.KNOB_WAIT_S + 1.0, self._after_knob_turn)

    @callback
    def _after_knob_turn(self, _now: Any) -> None:
        self._knob_timer = None
        if self._knob_turns:
            self.hass.async_create_task(self.async_request_refresh())

    def _is_echo(self, room: str, value: float, now_ts: float) -> bool:
        """Whether a changed room thermostat only repeats a late echo of its own valve."""
        if not self.config.get(CONF_ROOM_ECHO_FILTER, DEFAULT_ROOM_ECHO_FILTER) or room not in self._valves:
            return False
        thermostat, valves = self._valves[room]
        jumps = [ts for ts, v in self._setpoints.get(thermostat, ()) if abs(v - value) < 0.05]
        changed_ts = jumps[-1] if jumps else now_ts
        valve_history = tuple(sorted(item for valve in valves for item in self._setpoints.get(valve, ())))
        return room_control.is_echo(value, valve_history, changed_ts)

    async def async_save_store(self, force: bool = False) -> None:
        now = time.time()
        if not force and not self._store_dirty and now - self._last_store_save < STORE_SAVE_INTERVAL_S:
            return
        self._last_store_save = now
        self._store_dirty = False
        await self._store.async_save(persistence.to_store(self))

    @callback
    def _on_watched_change(self, event: Event[EventStateChangedData]) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        # Who changed it has to be on record before the cycle this very change starts: that cycle
        # runs at once, ahead of any listener registered later. (Seen in a test: a unit switched
        # off while unreachable came back, and the cycle judged it before the change was noted.)
        noted = self._note_setting_change(event) if new is not None and new.entity_id in self._commanded else None
        # Attribute-only updates (e.g. the Midea climate refreshing every second) must not
        # trigger a cycle; only a changed state (window opened, hvac mode switched) does - and a
        # setpoint somebody else set on what we command. Reported 2026-10-12: a thermostat turned
        # by hand showed as an override only with the next cycle, up to a minute later.
        by_others = noted is not None and noted.origin != hand.OURS
        if old is not None and new is not None and old.state == new.state and not by_others:
            return
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ user actions
    async def _async_changed(self) -> None:
        """A user action changed stored state: keep it and show its effect at once."""
        self._store_dirty = True
        await self.async_refresh()

    @callback
    def async_add_away_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update each time away mode or its planned return changes; returns the removal."""
        self._away_listeners.append(update)
        return lambda: self._away_listeners.remove(update)

    @callback
    def _async_show_away(self) -> None:
        """Write away mode and its return to the entities that show them now, not when the cycle ends.

        The cycle writes its entities only after its slow part: thermostat and heat pump commands,
        MQTT, the store. Home Assistant's date field computes what it sends from the state it shows,
        and *Planned return* reads the value against that state, so it has to be the current one.
        """
        for update in list(self._away_listeners):
            try:
                update()
            except Exception:  # showing must not break the action or the cycle
                _LOGGER.exception("thriftherm: could not show the away state")

    def set_mode(self, mode: str) -> None:
        if mode not in self.modes:
            raise ValueError(f"mode {mode} not offered")
        if mode != self.mode:
            _LOGGER.info("thriftherm: operating mode changed %s -> %s", self.mode, mode)
            self.mode = mode
            if mode != MODE_AWAY:
                self.away_return_ts = None
                self.away_return_provisional = False
            self._store_dirty = True
            self._async_show_away()

    def _switch_mode(self, label: str, current: str, mode: str, allowed: list[str]) -> bool:
        """Check and log a control-mode change; True when the caller has to apply it."""
        if mode not in allowed:
            raise ValueError(f"{label} control mode {mode} not allowed")
        if mode == current:
            return False
        _LOGGER.warning("thriftherm: %s control %s -> %s", label, current, mode)
        self._store_dirty = True
        return True

    def set_control_mode(self, mode: str) -> None:
        if self._switch_mode("midea", self.control_mode, mode, self.control_modes):
            self.control_mode = mode
            # a planned run never touched the unit; carrying it over looked like a manual change
            self.control_memory = self.control_memory.fresh_run_state()

    def set_boiler_control_mode(self, mode: str) -> None:
        if self._switch_mode("boiler", self.boiler_control_mode, mode, self.boiler_control_modes):
            self.boiler_control_mode = mode
            # a fresh telegram right away when switching to active
            self.boiler_memory = replace(self.boiler_memory, last_payload=None, last_send_ts=None)

    def set_room_control_mode(self, mode: str) -> None:
        if self._switch_mode("room", self.room_control_mode, mode, self.room_control_modes):
            self.room_control_mode = mode
            self.room_ctrl_memory.clear()

    def room_temperature(self, room: str, kind: str) -> float | None:
        """Comfort or setback temperature: the adjusted value, else the configured one."""
        stored = self.room_temps.get(room, {}).get(kind)
        if stored is not None:
            return stored
        cfg = next((r for r in self.builder.rooms if r.key == room), None)
        if cfg is None:
            return None
        return cfg.comfort_temp if kind == "comfort" else cfg.setback_temp

    async def async_set_room_temperature(self, room: str, kind: str, value: float) -> None:
        other = self.room_temperature(room, "setback" if kind == "comfort" else "comfort")
        if kind == "setback" and other is not None and value > other:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="setback_above_comfort", translation_placeholders={"room": room}
            )
        temps = self.room_temps.setdefault(room, {})
        if kind == "comfort" and other is not None and value < other:
            # Turning comfort down is what a room thermostat does; the setback follows instead of
            # refusing it (from a template thermostat the refusal only reached the log).
            temps["setback"] = float(value)
            _LOGGER.info("thriftherm %s: setback temperature follows comfort down to %.1f °C", room, value)
        temps[kind] = float(value)
        _LOGGER.info("thriftherm %s: %s temperature set to %.1f °C", room, kind, value)
        await self._async_changed()

    async def async_boost(self, room: str, duration_min: int | None) -> None:
        minutes = duration_min or int(self.config.get(CONF_BOOST_DURATION_MIN, DEFAULT_BOOST_DURATION_MIN))
        self.boosts[room] = time.time() + minutes * 60.0
        _LOGGER.info("thriftherm quick heat-up: %s for %d min", room, minutes)
        await self._async_changed()

    async def async_set_override(self, room: str, temperature: float, duration_min: int | None) -> None:
        minutes = duration_min or self._override_default_min
        now = time.time()
        self.overrides[room] = Override(target=float(temperature), until_ts=now + minutes * 60.0, set_at_ts=now, by=hand.ACTION)
        _LOGGER.info("thriftherm override: %s -> %.1f °C for %d min", room, temperature, minutes)
        await self._async_changed()

    async def async_clear_override(self, room: str | None) -> None:
        """End what was changed by hand: one room's override, or everything and the heat pump's too."""
        if room is None:
            self.overrides.clear()
            self.boosts.clear()
            if self.control_memory.manual_until_ts is not None:
                # back to automatic: a unit left heating is taken over by the next cycle
                self.control_memory = replace(self.control_memory, manual_until_ts=None, manual_by=None)
        else:
            self.overrides.pop(room, None)
            self.boosts.pop(room, None)
        for key, drying in self.drying_states.items():
            if drying.manual and room in (None, key):
                self.drying_states[key] = DryingState(ended_ts=drying.ended_ts)  # a drying asked for by hand ends too
        _LOGGER.info("thriftherm override cleared: %s", room or "all rooms")
        await self._async_changed()

    async def async_start_drying(self, room: str) -> None:
        """Dry a room now, for the time limit of a drying: the button, for when no shower started it."""
        before = self.drying_states.get(room, DryingState())
        self.drying_states[room] = DryingState(True, time.time(), before.baseline_abs_humidity, before.ended_ts, manual=True)
        _LOGGER.info("thriftherm %s: drying started by hand", room)
        await self._async_changed()

    async def async_set_away(self, return_ts: float | None, clear_return: bool = False, provisional: bool = False) -> None:
        """Switch to away mode. Without a return time a planned one is kept.

        The dashboard button calls this with no arguments; discarding a return
        time that was set for a trip would silently drop the pre-heating for it.
        A provisional return (only its date picked in Home Assistant's field)
        ends away mode like any other, but the rooms do not pre-heat for it;
        every return time that comes with a real time of day lifts that.
        """
        if return_ts is not None and return_ts <= time.time():
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="return_time_in_past")
        self.mode = MODE_AWAY
        if return_ts is not None:
            self.away_return_ts = return_ts
            self.away_return_provisional = provisional
        elif clear_return or (self.away_return_ts is not None and self.away_return_ts <= time.time()):
            self.away_return_ts = None
            self.away_return_provisional = False
        _LOGGER.info("thriftherm away mode set, return=%s provisional=%s", self.away_return_ts, self.away_return_provisional)
        self._async_show_away()
        await self._async_changed()

    async def async_clear_away(self) -> None:
        if self.mode == MODE_AWAY:
            self.mode = MODE_AUTO
        self.away_return_ts = None
        self.away_return_provisional = False
        _LOGGER.info("thriftherm away mode cleared")
        self._async_show_away()
        await self._async_changed()

    async def async_reset_learning(self, scopes: Iterable[str] | None = None, rooms: Iterable[str] | None = None) -> None:
        """Forget what was learned in the chosen areas; rooms narrows the room area."""
        chosen = learning_reset.normalise_scopes(scopes)
        room_keys = None if rooms is None else list(rooms)
        known = {r.key for r in self.builder.rooms}
        if room_keys is not None and (unknown := set(room_keys) - known):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_room", translation_placeholders={"room": ", ".join(sorted(unknown))}
            )
        self._forget(chosen, room_keys, learning_reset.REASON_USER)
        _LOGGER.warning("thriftherm: learning data reset by user: %s %s", list(chosen), room_keys or "")
        await self.async_save_store(force=True)
        await self.async_refresh()

    def _forget(self, scopes: Iterable[str], rooms: Iterable[str] | None, reason: str) -> None:
        # Only learned values go. Run state, lockout timers of the controllers and
        # manual-change holds stay, so a reset can never make a unit cycle.
        scopes = set(scopes)
        if learning_reset.SCOPE_BOILER in scopes:
            self.boiler_memory = boiler_learning.reset(self.boiler_memory)
        if learning_reset.SCOPE_HEAT_PUMP in scopes:
            self.cop_map = CopMap()
            self.heat_pump_tracker.reset()
            self.control_memory = replace(
                self.control_memory, offset_k=None, learning_bins=(), last_offset_review_ts=None, learning_until_ts=None
            )
        if learning_reset.SCOPE_ROOMS in scopes:
            self.heat_rates.forget(None if rooms is None else list(rooms))
            self.cool_rates.forget(None if rooms is None else list(rooms))
        self.last_learning_reset = {
            "scopes": [s for s in learning_reset.SCOPES if s in scopes],
            "rooms": None if rooms is None or learning_reset.SCOPE_ROOMS not in scopes else sorted(rooms),
            "reason": reason,
            "at": dt_util.utcnow().isoformat(timespec="seconds"),
        }
        self._store_dirty = True

    # ------------------------------------------------------------------ cycle
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            now = time.time()
            self.overrides = {k: o for k, o in self.overrides.items() if o.until_ts > now}
            self.boosts = {k: t for k, t in self.boosts.items() if t > now}
            if self.mode == MODE_AWAY and self.away_return_ts is not None and self.away_return_ts <= now:
                _LOGGER.info("thriftherm: return time reached, leaving away mode")
                self.mode = MODE_AUTO
                self.away_return_ts = None
                self.away_return_provisional = False
                self._store_dirty = True
                self._async_show_away()  # before the slow part of this cycle
            snapshot = self.builder.build(
                self.mode,
                overrides=self.overrides,
                drying_states=self.drying_states,
                heat_rates={r.key: v for r in self.builder.rooms if (v := self.heat_rates.rate(r.key)) is not None},
                # the return the rooms pre-heat for: none while it is provisional
                away_return_ts=None if self.away_return_provisional else self.away_return_ts,
                boosts=self.boosts,
                room_temps=self.room_temps,
                setting_changes=self._setting_changes,
                settings_watched_since_ts=self._settings_watched_since_ts,
            )
            self._track_setpoints()  # the build looked the valves up anew
            memory_before = self.control_memory  # the heat pump decision moves it on as if its command went through
            result = self._evaluate(snapshot)
        except Exception as err:  # surface to HA as UpdateFailed
            if self.last_update_success:
                # Home Assistant logs the message only, the traceback on debug: once per series here
                _LOGGER.exception("thriftherm: evaluation failed")
            # nothing below runs: a boiler addressed through entities must not keep a block meanwhile
            await self.async_release_boiler()
            raise UpdateFailed(f"evaluation failed: {err}") from err
        # The executors never raise and give up after a timeout, so every command below is tried and
        # the store and the repairs still follow. The boiler goes first: a heat pump that does not
        # answer must not delay its SetMode.
        boiler_cmd: BoilerCommand = result["boiler_command"]
        if self.has_boiler and self.boiler_entities:
            await self._async_command_boiler_entities(boiler_cmd)
        elif self.has_boiler and self.boiler_control_mode == CTRL_ACTIVE and boiler_cmd.send and boiler_cmd.payload:
            await executors.async_send_setmode(self.hass, self.boiler_setmode_topic, boiler_cmd.payload)
        command: HeatPumpCommand = result["midea_command"]
        heat_pump_entity = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        cycle_ts = snapshot.now.timestamp()
        if self.control_mode == CTRL_ACTIVE and self.has_heat_pump and heat_pump_entity and command.action != "none":
            stop = command.action == "stop"
            if self._asked_meanwhile(heat_pump_entity, cycle_ts):
                # planned without what somebody just asked for: not sent, and not on record as sent
                self.control_memory = memory_before
                self.plan_log.not_sent()
            elif not stop and cycle_ts < self._heat_pump_retry_ts:
                # a call failed lately: the next try waits, see below
                self.control_memory = replace(memory_before, last_hvac_mode=None)
                self.plan_log.not_sent()
            elif (
                calls := await executors.async_execute_heat_pump(self.hass, heat_pump_entity, command, self._own_context())
            ) is not None:
                self._heat_pump_call_failures = 0
                if not calls:
                    # The unit had all of it already: nothing went out, and the record of commands says
                    # so. The memory keeps the command as carried out, the unit shows it.
                    self.plan_log.nothing_sent()
                    result["command_log"] = list(self.plan_log.command_log)
            elif stop:
                # A stop reported as failed may have gone through all the same (the answer was lost,
                # the call timed out). The memory stays as decided, and it covers both: a unit
                # that did switch runs its fan on and is switched off, one that still heats is
                # asked again as not carried out. Rolled back, a unit that had switched stayed in
                # fan only for good, taken for a mode somebody had chosen.
                self.plan_log.not_sent()
            else:
                # A start or an adjustment that failed did not happen: the memory goes back, or
                # the control would count the unit as running. A call that went through before
                # the failure leaves the unit's mode open, so no judgement about a change by hand
                # until a command goes through. The next try waits 2, 4, 8 ... minutes, an hour
                # at most: sent again every cycle, each try held the cycle up for its timeout.
                self.control_memory = replace(memory_before, last_hvac_mode=None)
                self.plan_log.not_sent()
                wait = heat_pump_control.MANUAL_DETECT_GRACE_S * 2 ** min(self._heat_pump_call_failures, 8)
                self._heat_pump_retry_ts = cycle_ts + min(wait, heat_pump_control.RETRY_MAX_S)
                self._heat_pump_call_failures += 1
        if self.has_boiler and not self.boiler_entities:
            await self._async_keep_boiler_readings_fresh(result["boiler"].gas_power_w, now)
        if self.room_control_mode == CTRL_ACTIVE:
            climate_of = {r.key: r.climate_entity for r in self.builder.rooms}
            for room_cmd in result["room_commands"].values():
                entity = climate_of.get(room_cmd.room)
                if not (room_cmd.send and room_cmd.target is not None and entity):
                    continue
                if self._asked_meanwhile(entity, cycle_ts, setpoint_only=True):
                    # planned without what somebody just asked for: not sent, and not on record as sent
                    if room_cmd.room in self._room_memory_before:
                        self.room_ctrl_memory[room_cmd.room] = self._room_memory_before[room_cmd.room]
                    continue
                await executors.async_set_thermostat(self.hass, entity, room_cmd, self._own_context())
        await self.async_save_store()
        await issues.async_sync(self.hass, self, result)
        return result

    def _holds_boiler(self, held: bool) -> None:
        """Remember across restarts whether the entities hold something of ours."""
        if self._boiler_entities_released is not (not held):
            self._boiler_entities_released = not held
            self._store_dirty = True

    async def _async_command_boiler_entities(self, cmd: BoilerCommand) -> None:
        """Carry out the plan on a boiler addressed through a number and a switch.

        A SetMode telegram runs out and the boiler returns to its knob; an entity keeps what
        it was sent. So whenever the control stops commanding (not active, no boiler data,
        waiting for the rooms) the boiler is handed back: heating on, flow from the plain curve,
        which keeps following the outdoor temperature while Home Assistant runs.
        """
        entities = self.boiler_entities
        if entities is None:
            return
        async with self._boiler_lock:  # see async_release_boiler
            if self._boiler_let_go:
                return
            active = self.boiler_control_mode == CTRL_ACTIVE
            if self._boiler_entities_released is None:
                self._holds_boiler(active)  # only an active control has left anything behind
            if active and cmd.plan in (BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK):
                taking_over = self._boiler_entities_released  # what the entities hold is not ours: send whatever is due
                self._holds_boiler(True)
                if cmd.send or taking_over:
                    flow = cmd.flow_setpoint if cmd.flow_setpoint is not None else self.builder.params.boiler_flow_min
                    level = self._boiler_log_level
                    sent = await executors.async_send_boiler_entities(self.hass, *entities, flow, not cmd.disable_hc, level)
                    self._boiler_sent(sent)
                    if not sent:
                        # not carried out: due again next cycle, and no block counts as in force
                        self.boiler_memory = replace(
                            self.boiler_memory, last_payload=None, last_send_ts=None, block_since_ts=None
                        )
                return
            off = self.boiler_control_mode == CTRL_OFF
            # A control that is switched off leaves the maximum behind, as a knob would stand, and
            # writes nothing more. One that only plans keeps the flow on the plain curve.
            flow = self.builder.params.boiler_flow_max if off else self._hand_back_flow()
            if not self._boiler_entities_released:
                await self._async_hand_back(flow)
            elif not off and self._boiler_handed_back_flow is not None and abs(flow - self._boiler_handed_back_flow) >= 1.0:
                # Only a boiler this control handed back follows the curve; entities it never
                # commanded are left alone. The switch was set once with the hand-back and is the
                # user's since: somebody who switches the heating off for the summer keeps it off.
                await self._async_hand_back(flow, heating_on=None)

    def _hand_back_flow(self) -> float:
        """The flow a handed-back boiler gets: the plain curve, the maximum when the outdoor temperature is unknown."""
        params = self.builder.params
        # the last known value stands in for six hours in the evaluation, and no longer here:
        # a curve value from a mild day would cap the boiler in the cold
        if self._last_outdoor is None or time.time() - self._last_outdoor[1] > OUTDOOR_FALLBACK_MAX_AGE_S:
            return params.boiler_flow_max
        return round_half(curve_flow(self._last_outdoor[0], params))

    @property
    def _boiler_log_level(self) -> int:
        """A boiler that cannot be reached is warned about once per outage, not every cycle."""
        return logging.DEBUG if self._boiler_send_failed else logging.WARNING

    def _boiler_sent(self, sent: bool) -> None:
        self._boiler_send_failed = not sent

    async def _async_hand_back(self, flow: float, heating_on: bool | None = True) -> None:
        entities = self.boiler_entities
        if entities is None:
            return
        sent = await executors.async_send_boiler_entities(self.hass, *entities, flow, heating_on, self._boiler_log_level)
        self._boiler_sent(sent)
        if sent:
            if not self._boiler_entities_released:
                _LOGGER.warning("thriftherm: boiler handed back (heating on, flow %.1f °C)", flow)
            self._holds_boiler(False)
            if self._boiler_handed_back_flow != flow:
                self._boiler_handed_back_flow = flow
                self._store_dirty = True

    async def async_release_boiler(self, final: bool = False) -> None:
        """Hand a boiler addressed through entities back, if they hold something of ours.

        `final`: nothing of ours runs afterwards (Home Assistant stops, the entry is unloaded).
        The flow maximum is left behind then, as a knob would be: a curve value frozen on a mild
        day would cap the boiler in the cold. That goes for a boiler handed back earlier as well,
        whose flow has followed the curve since. No later cycle may command again, or a block sent
        after the hand-back would stay for good. That goes for a cycle under way as well: the
        hand-back waits until its write is done (review finding C10: a block written after it).
        """
        if self.boiler_entities is None or not self.has_boiler or self._boiler_let_go:
            return
        if final:
            self._boiler_let_go = True
        async with self._boiler_lock:
            flow_max = self.builder.params.boiler_flow_max
            if self._boiler_entities_released is False:
                await self._async_hand_back(flow_max if final else self._hand_back_flow())
            elif final and self._boiler_entities_released and self._boiler_handed_back_flow not in (None, flow_max):
                # Handed back earlier, it has followed the curve since, and nothing follows it once we stop.
                # The switch was the user's from the hand-back on and stays as it is.
                await self._async_hand_back(flow_max, heating_on=None)
            # never commanded: the entities are not ours to write

    async def _async_keep_boiler_readings_fresh(self, gas_power_w: float | None, now: float) -> None:
        """Keep flow and return current: high poll priority always, direct reads while gas burns.

        Without a gas meter nothing but these very values tells that the burner has started, and
        between two priority rounds ebusd works through the rest of its list (minutes with the
        default list): they are then read once a minute all the time.
        """
        topics = [self._ebus_topic(name, "get") for name in self.boiler_profile.fast_messages]
        if self._ebus_priority_ts is None or now - self._ebus_priority_ts >= EBUS_PRIORITY_REFRESH_S:
            if all([await executors.async_request_ebus_read(self.hass, topic, "?1") for topic in topics]):
                self._ebus_priority_ts = now
        burning = gas_power_w is None or gas_power_w > boiler_control.HOT_WATER_GAS_W
        if burning and now - self._ebus_read_ts >= EBUS_READ_WHILE_BURNING_S:
            for topic in topics:
                await executors.async_request_ebus_read(self.hass, topic)
            self._ebus_read_ts = now

    def _count_gas(self, snapshot: HeatingSnapshot, boiler_res: BoilerResult, now_ts: float, warm_start: bool = False) -> None:
        """Count the gas for heating and the gas for hot water apart, by what the installation has."""
        boiler = snapshot.boiler
        if not self.has_boiler:
            return
        if self.config.get(CONF_GAS_VOLUME):
            if not boiler.gas_volume_m3.valid:
                return  # the meter is away: its next reading is counted from the last one
            reported = None if self.boiler_entities else self.boiler_profile.reported_mode(boiler_res.state_number)
            seen = self.boiler_memory.hot_water_seen_ts
            hot_water = gas_split.hot_water_now(reported, bool(boiler_res.hwc_active), seen, now_ts, warm_start)
            prices = snapshot.prices
            kwh_per_m3 = economics.gas_energy_kwh(1.0, prices.gas_z_factor, prices.gas_calorific_kwh_m3)
            self.gas_split = gas_split.by_meter(self.gas_split, boiler.gas_volume_m3.value, kwh_per_m3, hot_water, now_ts)
            return
        try:
            nominal_kw = float(self.config.get(CONF_BOILER_NOMINAL_INPUT_KW) or 0.0)
        except (TypeError, ValueError):
            nominal_kw = 0.0
        if nominal_kw > 0.0 and (boiler.energy_sums_heating or boiler.energy_sums_hot_water):
            self.gas_split = gas_split.by_counters(
                self.gas_split, dict(boiler.energy_sums_heating), dict(boiler.energy_sums_hot_water), nominal_kw, now_ts
            )

    def _evaluate(self, snapshot: HeatingSnapshot) -> dict[str, Any]:
        params = snapshot.params
        now_ts = snapshot.now.timestamp()
        snapshot = self._adopt_manual_thermostat_changes(snapshot, now_ts)
        signal_ok, self._ebus_signal_lost_since = boiler_engine.signal_with_grace(
            snapshot.boiler.signal_ok, self._ebus_signal_lost_since, now_ts
        )
        if signal_ok != snapshot.boiler.signal_ok:
            snapshot = replace(snapshot, boiler=replace(snapshot.boiler, signal_ok=signal_ok))
        if self._preheat_latch:
            snapshot = replace(
                snapshot,
                rooms={k: replace(s, preheat_latch_ts=self._preheat_latch.get(k)) for k, s in snapshot.rooms.items()},
            )
        heat_pump_res: HeatPumpResult = heat_pump_engine.evaluate(snapshot.heat_pump, params)
        held = self.control_memory.manual_until_ts is not None and self.control_memory.manual_until_ts > now_ts
        if held and snapshot.heat_pump.hvac_mode != "heat":
            # During the time in hand nothing is sent: a unit somebody switched off heats no room.
            # A drying started on it would raise the target for the radiator (as on 2026-09-25).
            heat_pump_res = replace(heat_pump_res, heating_available=False)

        # --- defrost / icing -------------------------------------------------
        defrost_res, lockout_changed = self.heat_pump_tracker.defrost(snapshot, heat_pump_res.run_state, params, now_ts)
        if lockout_changed:
            self._store_dirty = True

        outdoor = snapshot.outdoor_temp.value_or_none
        if outdoor is not None:
            self._last_outdoor = (outdoor, now_ts)
        elif self._last_outdoor is not None and now_ts - self._last_outdoor[1] < OUTDOOR_FALLBACK_MAX_AGE_S:
            # neither sensor nor weather service: the weather does not jump, so the last
            # known value beats guessing, and after six hours the curve falls back to its middle
            outdoor = self._last_outdoor[0]
        outdoor_for_limit = outdoor if outdoor is not None else snapshot.heat_pump.outdoor_temp
        heat_pump_heat_possible = (
            self.has_heat_pump
            and heat_pump_res.heating_available
            and defrost_res.block_reason is None
            and snapshot.mode not in (MODE_OFF, MODE_BOILER_ONLY)
            # only a heat pump that really acts dries the bathroom: while it plans, the raised
            # target went to the radiator (measured 2026-09-25: 2 hours of gas at 22 °C)
            and self.control_mode == CTRL_ACTIVE
            and outdoor_for_limit is not None
            and outdoor_for_limit >= params.heat_pump_min_outdoor_temp
        )
        rooms: dict[str, RoomResult] = {
            key: room_engine.evaluate_room(
                state,
                snapshot.now,
                snapshot.mode,
                params,
                snapshot.away_return_ts,
                heat_pump_heat_possible=heat_pump_heat_possible,
                heat_pump_cooling=heat_pump_res.run_state == HEAT_PUMP_COOLING_USER,
            )
            for key, state in snapshot.rooms.items()
        }
        self._preheat_latch = {key: res.preheat_for_ts for key, res in rooms.items() if res.preheat_for_ts is not None}
        for key, res in rooms.items():
            self.drying_states[key] = res.drying
            was = self._last_drying.get(key, False)
            if res.drying.active != was:
                _LOGGER.info("thriftherm bathroom drying %s: %s (%s)", key, "started" if res.drying.active else "ended", res.drying_reason)
                self._last_drying[key] = res.drying.active

        safety_res: SafetyResult = safety.evaluate(snapshot, rooms, self._frost_rooms)
        self._frost_rooms = safety_res.frost_rooms
        boiler_res: BoilerResult = boiler_engine.evaluate(snapshot.boiler, snapshot.prices)

        # --- COP ---------------------------------------------------------------
        cop_res = self.heat_pump_tracker.cop(snapshot, heat_pump_res, defrost_res, params, now_ts)

        # --- economics: measured COP while running, else learned / prior estimate --------
        if outdoor is not None:
            estimate, estimate_basis = self.cop_map.estimate(outdoor, cop_profiles.profile(params.heat_pump_cop_profile))
        else:
            estimate, estimate_basis = None, None
        decision_cop = cop_res.value if cop_res.value is not None else estimate
        decision_basis = "measured" if cop_res.value is not None else estimate_basis
        try:
            econ: EconomicsResult = economics.evaluate(
                snapshot.prices, decision_cop, params.break_even_margin_on, params.break_even_margin_off, self._last_cheaper
            )
            self._prices_unusable = False
        except (ValueError, ZeroDivisionError) as err:
            # A price of 0 (stored before the form refused it) must never stop the control:
            # no cost decision, while the boiler and the frost protection carry on.
            if not self._prices_unusable:
                _LOGGER.warning("thriftherm: prices cannot be used, no cost comparison until they are corrected: %s", err)
            self._prices_unusable = True
            econ = EconomicsResult(
                gas_cost_per_kwh_thermal=None,
                heat_pump_cost_per_kwh_thermal=None,
                break_even_cop=None,
                cheaper_source="unknown",
                saving_pct=None,
            )
        if econ.cheaper_source != "unknown":
            self._last_cheaper = econ.cheaper_source

        self.heat_pump_tracker.track_efficiency(cop_res.value, econ.break_even_cop, params.break_even_margin_off, now_ts)

        # --- learning: COP map ---------------------------------------------------
        cycle_cop = self.heat_pump_tracker.take_cycle_cop()
        if outdoor is not None and cycle_cop is not None:
            self.cop_map.add(outdoor, cycle_cop)
            self._store_dirty = True
        elif outdoor is not None and self.heat_pump_tracker.map_entry_due(cop_res, now_ts):
            self.cop_map.add(outdoor, cop_res.value)
            self._store_dirty = True

        # --- boiler calls: hysteresis, stalled rooms, end of the comfort window ------------
        call_states: dict[str, str] = {}
        for key, res in rooms.items():
            state, memory = heat_call.update(res, self.call_memory.get(key, heat_call.CallMemory()), now_ts, res.comfort_end_ts)
            if memory != self.call_memory.get(key):
                self._store_dirty = True
            self.call_memory[key] = memory
            call_states[key] = state
        rooms = {key: replace(res, boiler_call=call_states[key]) for key, res in rooms.items()}
        heat_pump_mode = self.control_mode if self.has_heat_pump else CTRL_OFF
        # while the heat pump only plans and the boiler really heats, its cost edge decides nothing
        heat_pump_acts = heat_pump_mode == CTRL_ACTIVE or self.boiler_control_mode != CTRL_ACTIVE

        # --- advice ------------------------------------------------------------
        advice: Advice = advisor.advise(
            snapshot,
            rooms,
            econ,
            cop_res,
            heat_pump_res,
            boiler_res,
            safety_res,
            heat_pump_block_reason=defrost_res.block_reason,
            decision_cop=decision_cop,
            decision_cop_basis=decision_basis,
            boiler_calls=frozenset(k for k, state in call_states.items() if state == heat_call.CALL_CALLING),
            heat_pump_acts=heat_pump_acts,
        )

        # --- midea control (phase 7): shadow or active --------------------------------------
        heat_pump_room_keys = [r.key for r in self.builder.rooms if r.served_by_heat_pump]
        blocked = advice.blocked.get("midea")
        command, self.control_memory = heat_pump_control.decide(
            ControlInputs(
                now_ts=now_ts,
                control_mode=heat_pump_mode,
                op_mode=snapshot.mode,
                heat_pump=snapshot.heat_pump,
                run_state=heat_pump_res.run_state,
                rooms=tuple(rooms[k] for k in heat_pump_room_keys if k in rooms),
                advice_source=advice.source,
                blocked_reason=None if blocked in (None, heat_pump_res.run_state, "mode_boiler_only", "outdoor_too_cold") else blocked,
                safety_state=safety_res.state,
                outdoor_c=outdoor,
                expected_cop=decision_cop,
                break_even_cop=econ.break_even_cop,
                outdoor_bin=None if outdoor is None else CopMap.bin_of(outdoor),
                bin_count=0 if outdoor is None else self.cop_map.bin_count(outdoor),
                params=params,
                cheaper=econ.cheaper_source == SOURCE_HEAT_PUMP,
                started_ts=self._started_ts,
            ),
            self.control_memory,
        )
        if self.plan_log.heat_pump(command, now_ts, self.control_mode == CTRL_ACTIVE):
            self._store_dirty = True
        if params.heat_pump_follow_rooms and command.plan == HEAT_PUMP_PLAN_HEAT and heat_pump_acts:
            advice = _runs_through(advice, command.target_temp)

        # --- learning guard: never learn while setpoints move or special modes run ---------------
        changed = [key for key, res in rooms.items() if self._last_targets.get(key) != res.target]
        if changed:
            # rooms seen for the first time (after a start) changed nothing a user did
            moved = tuple(key for key in changed if key in self._last_targets)
            for key in changed:
                self._last_targets[key] = rooms[key].target
            self._last_target_change_ts = now_ts
            self._last_target_change_rooms = moved
        learning_blocked: list[str] = []
        # The heat pump's own faults (switched-off plug, no heat pump at all) say nothing about
        # the boiler: measured 2026-09-26, "midea_unavailable" alone paused the learning for 31 h.
        boiler_issues = [i for i in safety_res.issues if not i.startswith(HEAT_PUMP_ISSUE_PREFIX)]
        if safety_res.state == SAFETY_FALLBACK or (safety_res.state != SAFETY_OK and boiler_issues):
            learning_blocked.append(f"safety_{safety_res.state}")
        settling = now_ts - self._last_target_change_ts < SETPOINT_SETTLE_S
        if settling:
            learning_blocked.append("setpoint_recently_changed")
        if any(r.boost_active for r in rooms.values()):
            learning_blocked.append("quick_heat_up")
        if any(r.drying.active for r in rooms.values()):
            learning_blocked.append("drying_mode")
        if self.boiler_control_mode != CTRL_ACTIVE:
            # while only planning, the front knob sets the flow: the offset changes nothing
            learning_blocked.append("boiler_control_not_active")
        learning_allowed = not learning_blocked
        # For an hour after hot water the circuit's spread says nothing, and the boiler control
        # holds the spread back itself for that long. What the rooms show stays evidence: with
        # the whole learning switched off for that hour, a combi boiler with its hourly keep-warm,
        # or a household that taps hot water more often than that, never learned.
        seen = self.boiler_memory.hot_water_seen_ts
        hot_water_recent = seen is not None and now_ts - seen < boiler_control.HOT_WATER_HOLDOFF_S

        # --- boiler control: plan only or active ----------------------------------------------
        # Right after a start, a heat pump whose run is kept while its entity is not loaded yet
        # counts as data still missing, like a room sensor that has not reported: the advice would
        # name the boiler for the rooms the unit serves, and the boiler would hold that for its
        # minimum time in state once the unit is back (review 2026-10-14).
        heat_pump_pending = heat_pump_res.run_state == HEAT_PUMP_UNAVAILABLE and self.control_memory.running_since_ts is not None
        boiler_cmd, self.boiler_memory = boiler_control.decide(
            BoilerInputs(
                now_ts=now_ts,
                control_mode=self.boiler_control_mode,
                op_mode=snapshot.mode,
                safety_state=safety_res.state,
                boiler_available=boiler_res.available,
                room_data_pending=now_ts - self._started_ts < ROOM_DATA_GRACE_S
                and (heat_pump_pending or any(r.temperature is None for r in rooms.values())),
                rooms=tuple(rooms.values()),
                frost_rooms=safety_res.frost_rooms,
                advice_source=advisor.boiler_source(
                    advice.source,
                    command.plan,
                    heat_pump_mode,
                    self.boiler_control_mode,
                    command.delivers,
                ),
                outdoor_c=outdoor,
                params=params,
                flow_c=snapshot.boiler.flow_temp.value_or_none,
                return_c=snapshot.boiler.return_temp.value_or_none,
                gas_power_w=boiler_res.gas_power_w,
                # the status codes are those of an ebusd profile; a boiler addressed through entities has none
                reported_mode=None if self.boiler_entities else self.boiler_profile.reported_mode(boiler_res.state_number),
                status_known=not self.boiler_entities and boiler_res.state_number is not None,
                # The coarsest burner sign, for installations with neither a gas meter nor a status
                # code; the learning ranks the three itself (boiler_learning.flame). The boiler's own
                # flame sign (OpenTherm) where there is one, else the pump state.
                burner_heating=(
                    snapshot.boiler.flame if snapshot.boiler.flame is not None else snapshot.boiler.pump_state == "on"
                )
                and not bool(boiler_res.hwc_active),
                hot_water_active=bool(boiler_res.hwc_active),
                heating_pump_on=(
                    snapshot.boiler.pump_running
                    if snapshot.boiler.pump_running is not None
                    else None if snapshot.boiler.pump_state is None else snapshot.boiler.pump_state == PUMP_STATE_ON
                ),
                learning_allowed=learning_allowed,
            ),
            self.boiler_memory,
        )
        if hot_water_recent and boiler_cmd.learning_phase == boiler_learning.PHASE_PAUSED:
            learning_blocked.append("hot_water_recent")  # shown as what the paused learning waits for
        self.plan_log.boiler(boiler_cmd, now_ts, self.boiler_control_mode == CTRL_ACTIVE)
        self._count_gas(snapshot, boiler_res, now_ts, boiler_cmd.warm_start)
        if self.has_boiler:
            flow_c, return_c = snapshot.boiler.flow_temp.value_or_none, snapshot.boiler.return_temp.value_or_none
            self.load_log.append([
                round(now_ts), outdoor, flow_c, return_c,
                None if flow_c is None or return_c is None else round(flow_c - return_c, 2),
                boiler_cmd.calling_rooms, boiler_cmd.rooms_total, round(boiler_cmd.burner_share, 3),
                boiler_cmd.offset_k,
                # target minus actual per room, in the order of the configured rooms
                [None if res.temperature is None else round(res.target - res.temperature, 2) for res in rooms.values()],
            ])

        # --- heat along: while the boiler runs anyway, more open radiators mean fewer starts ---
        sent_rooms = rooms
        if boiler_cmd.plan == BOILER_PLAN_HEAT:
            comfort = {
                key: state.config.comfort_temp if state.schedule_next_temp is None else state.schedule_next_temp
                for key, state in snapshot.rooms.items()
            }
            sent_rooms = heat_call.join(rooms, call_states, comfort, now_ts)

        # --- room control via the room thermostats ----------------------------------------------
        room_commands: dict[str, RoomCommand] = {}
        for cfg in self.builder.rooms:
            state = snapshot.rooms.get(cfg.key)
            res = sent_rooms.get(cfg.key)
            if state is None or res is None:
                continue
            before = self.room_ctrl_memory.get(cfg.key, RoomCtrlMemory())
            self._room_memory_before[cfg.key] = before
            room_cmd, self.room_ctrl_memory[cfg.key] = room_control.decide(
                cfg.key,
                self.room_control_mode,
                res,
                bool(cfg.climate_entity),
                state.trv_hvac_mode,
                state.trv_target_temp,
                now_ts,
                before,
                state.thermostat_own_target,
                min_temp=state.thermostat_min_temp,
                max_temp=state.thermostat_max_temp,
            )
            if self.room_ctrl_memory[cfg.key].last_sent_target != before.last_sent_target:
                # A sent setpoint must survive a restart at once: measured 2026-09-26, the store
                # still held 21.0 while the thermostat had 21.5 from us, and the restart took it
                # for a change by hand (a two-hour override).
                self._store_dirty = True
            room_commands[cfg.key] = room_cmd
            plan = (room_cmd.plan, room_cmd.target)
            if plan != self._last_room_plans.get(cfg.key) and room_cmd.plan == "set":
                _LOGGER.info(
                    "thriftherm room %s %s: %.1f °C (%s)",
                    cfg.key,
                    "command" if self.room_control_mode == CTRL_ACTIVE else "plan (not sent)",
                    room_cmd.target,
                    room_cmd.reason,
                )
            self._last_room_plans[cfg.key] = plan

        # --- learning: heat-up rates (heating = a source is advised for the room) ----
        for key, res in rooms.items():
            # a shower warms the bathroom by itself within minutes; that is not a heat-up rate
            heating = advice.source != SOURCE_NONE and (res.demand or 0.0) > 0.0 and not res.drying.active
            # the valve's own reading stands in for a failed room sensor, but it sits at the
            # radiator: rates learned from it would not be the room's
            measured = None if any(i.startswith("using_trv_local_temperature") for i in res.issues) else res.temperature
            learned = self.heat_rates.observe(key, now_ts, measured, heating, bool(res.window_open), outdoor)
            if learned is not None:
                _LOGGER.info("thriftherm learned heat-up rate %s: %.2f K/h", key, learned)
                self._store_dirty = True
            # how fast the room loses heat: the basis for a setback that really saves
            cooled = self.cool_rates.observe(key, now_ts, measured, outdoor, heating, bool(res.window_open))
            if cooled is not None:
                _LOGGER.info("thriftherm learned cool-down coefficient %s: %.4f per h", key, cooled)
                self._store_dirty = True

        self.plan_log.changes(advice, safety_res, defrost_res)

        return {
            "snapshot": snapshot,  # what the cycle read; nothing in the integration uses it, the tests look at it
            "rooms": sent_rooms,
            "safety": safety_res,
            "boiler": boiler_res,
            "gas_split": self.gas_split,
            "midea": heat_pump_res,
            "cop": cop_res,
            "econ": econ,
            "defrost": defrost_res,
            "advice": advice,
            "cop_map": self.cop_map.to_dict(),
            "expected_cop": estimate,
            "duct_factor": params.duct_factor,
            # what the heating curve and the heat pump limits use, the last known value included
            "outdoor_c": outdoor,
            "outdoor_source": snapshot.outdoor_temp_source or (OUTDOOR_LAST_KNOWN if outdoor is not None else None),
            "midea_rooms": heat_pump_room_keys,
            "midea_room": self.config.get(CONF_HEAT_PUMP_ROOM),
            "overrides": {k: {"target": o.target, "until_ts": o.until_ts} for k, o in self.overrides.items()},
            "away_return_ts": self.away_return_ts,
            "away_return_provisional": self.away_return_provisional,
            "heat_rates": self.heat_rates.to_dict(),
            "cool_rates": self.cool_rates.to_dict(),
            "heating_power": {
                r.key: value
                for r in self.builder.rooms
                if (value := self.heat_rates.heating_power_k_h(r.key, self.cool_rates.rate(r.key))) is not None
            },
            "expected_cop_basis": estimate_basis,
            "decision_cop": decision_cop,
            "decision_cop_basis": decision_basis,
            "prior_calibration_factor": self.cop_map.calibration_factor(),
            "midea_command": command,
            "control_mode": heat_pump_mode,
            # a unit that follows the rooms: the configured offset plus what the rooms asked for on top
            "eco_offset_k": heat_pump_control.follow_offset(params, self.control_memory)
            if params.heat_pump_follow_rooms or self.control_memory.offset_k is None
            else self.control_memory.offset_k,
            "command_log": list(self.plan_log.command_log),
            "boosts": dict(self.boosts),
            "boiler_command": boiler_cmd,
            "boiler_control_mode": self.boiler_control_mode,
            "boiler_log": list(self.plan_log.boiler_log),
            "boiler_setmode_topic": self.boiler_setmode_topic,
            "learning_blocked_by": learning_blocked,
            "learning_paused_until_ts": self._last_target_change_ts + SETPOINT_SETTLE_S if settling else None,
            "setpoint_changed_in": list(self._last_target_change_rooms) if settling else [],
            "learning_reset": self.last_learning_reset,
            "language": self.hass.config.language,
            "room_names": {r.key: r.name for r in self.builder.rooms},
            "room_commands": room_commands,
            "echoes_ignored": dict(self.echoes_ignored),
            "room_control_mode": self.room_control_mode,
        }

    def _adopt_manual_thermostat_changes(self, snapshot: HeatingSnapshot, now_ts: float) -> HeatingSnapshot:
        """Turn a manual change on a room thermostat into an override (active room control only)."""
        if self.room_control_mode != CTRL_ACTIVE:
            self._knob_turns.clear()
            return snapshot
        changed = {}
        for key, state in snapshot.rooms.items():
            turn = self._knob_turns.get(key)
            passed_on = (
                turn is not None
                and state.thermostat_change is not None
                and state.thermostat_change.setpoint
                and state.thermostat_change.ts >= turn[0] - 1.0
            )
            if turn is not None and (passed_on or now_ts - turn[0] >= room_control.KNOB_WAIT_S):
                del self._knob_turns[key]  # passed on: the thermostat's own target tells, by the rules below
            if turn is not None and not passed_on and key not in self._knob_turns:
                usable = state.trv_target_temp is not None and state.trv_hvac_mode not in (None, "off")
                # A knob turned at the valve itself, and the room thermostat kept its target: so
                # much warmer or colder than it has now. It gets the new target from us, as any override.
                limits = (state.thermostat_min_temp, state.thermostat_max_temp)
                value = room_control.thermostat_setpoint(state.trv_target_temp + turn[1], *limits) if usable else None
                if value is not None and abs(value - state.trv_target_temp) > room_control.TOLERANCE_K:
                    minutes = self._override_default_min
                    override = Override(value, now_ts + minutes * 60.0, now_ts, hand.DEVICE)
                    self.overrides[key] = override
                    changed[key] = replace(state, override=override)
                    self._store_dirty = True
                    _LOGGER.info(
                        "thriftherm room %s: knob turned by %+.1f K -> override %.1f °C for %d min", key, turn[1], value, minutes
                    )
                    continue
            mem = self.room_ctrl_memory.get(key)
            if mem is None:
                continue
            # The thermostat's answer to our own setpoint is nobody's hand, whatever value it shows.
            if room_control.own_answer(state.trv_target_temp, mem, state.thermostat_change):
                if mem.answered_with != state.trv_target_temp:
                    self.room_ctrl_memory[key] = replace(mem, answered_with=state.trv_target_temp, confirmed=True)
                    self._store_dirty = True
                continue
            # Asked for in Home Assistant by a person or an automation: a change by hand for certain.
            # Anything else is one unless the thermostat explains it itself (a rule of its own, an echo).
            asked = hand.deliberate_since(state.thermostat_change, mem.last_sent_ts, setpoint_only=True)
            if asked is None and state.thermostat_own_target:
                continue
            value = room_control.manual_change(self.room_control_mode, state.trv_target_temp, mem, asked is not None)
            if value is None:
                continue
            if asked is None and self._is_echo(key, value, now_ts):
                # not a hand on the knob: keep the plan, room control sends our setpoint again
                if self.echoes_ignored.get(key, (0.0, None))[1] != value:
                    _LOGGER.info("thriftherm room %s: thermostat jumped to %.1f °C, a late radio echo -> ignored", key, value)
                self.echoes_ignored[key] = (now_ts, value)
                continue
            minutes = self._override_default_min
            override = Override(value, now_ts + minutes * 60.0, now_ts, asked or hand.DEVICE)
            self.overrides[key] = override
            self.room_ctrl_memory[key] = RoomCtrlMemory(last_sent_target=value, last_sent_ts=now_ts, confirmed=True)
            changed[key] = replace(state, override=override)
            self._store_dirty = True
            _LOGGER.info(
                "thriftherm room %s: thermostat set to %.1f °C by hand (%s) -> override for %d min",
                key, value, override.by, minutes,
            )
        if not changed:
            return snapshot
        return replace(snapshot, rooms={**snapshot.rooms, **changed})
