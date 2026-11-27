"""Coordinator: one snapshot per cycle, all engines, one result set."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DOMAIN, ATTR_ENTITY_ID, ATTR_SERVICE_DATA, EVENT_CALL_SERVICE, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import boiler_profiles, const, executors, issues, learning_reset, persistence
from .adapters.config import system_type_from_config
from .adapters.inputs import SnapshotBuilder, valve_driver, valve_view, whole_number
from .const import (
    CONF_COOL_AWAY_TEMP,
    CONF_COOL_FLOOR_TEMP,
    CONF_COOLING_CHEAP_POWER_ENTITY,
    CONF_COOLING_CHEAP_POWER_K,
    CONF_COOLING_SEASON_FROM_C,
    CONF_COOLING_SEASON_VETO_C,
    CONF_HEATING_SEASON_FROM_C,
    CONF_HEAT_PUMP_COOL_FAN_STAGE,
    CONF_HEAT_PUMP_COOL_SHORTFALL,
    CONF_HEAT_PUMP_COOLING_ALLOWED,
    COOL_SHORTFALL_CHOICES,
    COOL_SHORTFALL_LIFT,
    DEFAULT_COOL_AWAY_TEMP,
    DEFAULT_COOL_COMFORT_TEMP,
    DEFAULT_COOL_FLOOR_TEMP,
    DEFAULT_COOLING_CHEAP_POWER_K,
    DEFAULT_COOLING_SEASON_FROM_C,
    DEFAULT_COOLING_SEASON_VETO_C,
    DEFAULT_HEATING_SEASON_FROM_C,
    DEFAULT_COOL_OUTSIDE_TEMP,
    HEAT_PUMP_PLAN_COOL,
    SEASON_AUTO,
    SEASON_COOLING,
    SEASON_HEATING,
    BOILER_REPORTS_FAULT,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_STATE_NUMBER,
    RANGE_FLOW_TEMP,
    CONF_AWAY_RETURN_PERSONS,
    CONF_BOILER_ALLOW_ACTIVE,
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_FLOW_SETPOINT_ENTITY,
    CONF_BOILER_HEATING_ENABLE_ENTITY,
    CONF_BOILER_NOMINAL_INPUT_KW,
    CONF_BOILER_PUMP_POWER_W,
    CONF_BOILER_PROFILE,
    CONF_BOOST_DURATION_MIN,
    CONF_GAS_FLOW,
    CONF_GAS_VOLUME,
    CONF_HEAT_PUMP_ALLOW_ACTIVE,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_ROOM,
    CONF_HEAT_PUMP_SHORTFALL,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_ROOM_ALLOW_ACTIVE,
    CONF_ROOM_ECHO_FILTER,
    BOILER_PLAN_BLOCK,
    BOILER_PLAN_HANDS_OFF,
    ROOM_PLAN_HOLD,
    BOILER_PLAN_HEAT,
    BOILER_REPORTS_HEATING,
    CTRL_ACTIVE,
    CTRL_OFF,
    CTRL_SHADOW,
    DEFAULT_BOILER_ALLOW_ACTIVE,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DEFAULT_BOOST_DURATION_MIN,
    DEFAULT_HEAT_PUMP_ALLOW_ACTIVE,
    DEFAULT_HEAT_PUMP_SHORTFALL,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_ROOM_ALLOW_ACTIVE,
    DEFAULT_ROOM_ECHO_FILTER,
    DOMAIN,
    HEAT_PUMP_COOLING_USER,
    HEAT_PUMP_OFF,
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
    SHORTFALL_ALONE,
    SHORTFALL_BOILER_HELPS,
    SHORTFALL_SLOWER,
    SOURCE_BOILER,
    SOURCE_BOTH,
    SOURCE_HEAT_PUMP,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    SYSTEM_GAS,
    SYSTEM_NONE,
    UPDATE_INTERVAL_S,
)
from .engines import (
    hand,
    presence,
    advisor,
    boiler as boiler_engine,
    boiler_control,
    boiler_learning,
    boiler_load,
    flow_throttle,
    economics,
    gas_split,
    heat_call,
    heat_pump as heat_pump_engine,
    heat_pump_control,
    pump_share,
    room as room_engine,
    room_control,
    safety,
    valve_plan,
    valve_tpi,
)
from .engines import season as season_engine
from .engines.heat_pump_tracking import HeatPumpTracker
from .engines.safety import HEAT_PUMP_DROPOUT_S, HEAT_PUMP_ISSUE_PREFIX
from .engines import cop_profiles
from .engines.learning import (
    HEAT_BY_BOILER,
    HEAT_BY_BOTH,
    HEAT_BY_HEAT_PUMP,
    HEAT_SOURCES,
    AcCoolRateStats,
    CoolRateStats,
    CopMap,
    HeatRateStats,
    ac_cool_learnable,
)
from .engines.boiler_memory import BoilerInputs, BoilerMemory, curve_flow
from .engines.common import as_float, round_half
from .models import SettingChange
from .engines.heat_pump_control import ControlInputs, ControlMemory
from .engines import heat_pump_cooling, room_cooling
from .engines.room_control import RoomCtrlMemory
from .models import (
    ROOM_OFF_UNTIL_TS,
    Advice,
    BoilerCommand,
    BoilerResult,
    DryingState,
    EconomicsResult,
    HeatingSnapshot,
    HeatPumpCommand,
    HeatPumpResult,
    Override,
    Reason,
    RoomCommand,
    RoomConfig,
    RoomResult,
    RoomState,
    SafetyResult,
    reason,
)
from .plan_log import PlanLog

_LOGGER = logging.getLogger(__name__)

EBUS_PRIORITY_REFRESH_S = 600.0  # a restart of ebusd forgets the priority; renew it so a gap lasts minutes, not an hour
# Fresh values once a minute while gas is burning, all the time without a gas meter. Home Assistant starts the
# cycles 60 s apart give or take a few milliseconds, so a full 60 s would skip the read every other cycle.
EBUS_READ_WHILE_BURNING_S = 55.0
OUTDOOR_FALLBACK_MAX_AGE_S = 6 * 3600.0  # how long the last known outdoor temperature stands in
OUTDOOR_LAST_KNOWN = "last_known"  # the outdoor source shown while it does
# After a restart the Zigbee room sensors come back one by one (measured 2026-09-18: the bathroom
# after 38 s). Until they have, the boiler gets no command instead of a block decided on gaps.
ROOM_DATA_GRACE_S = 300.0
# The boiler's load log, one row a cycle: as many as are stored (`LOAD_LOG_STORED_ROWS`, about half a day).
# 1440 rows were kept, 720 stored and 120 shown in the diagnostics; nothing read the rest (measured 2026-10-23).
LOAD_LOG_ROWS = 720


STORE_SAVE_INTERVAL_S = 600.0
# a humidity load the bathroom gives notice of: warm seconds then are the shower's (docs/PHASE12_COOLING.md §20.2)
_HUMIDITY_LOAD_REASONS = (
    "drying_waiting_for_the_shower_to_end", "drying_waiting_for_open_window", "drying_waiting_for_the_heat_pump",
    "drying_started_humidity_load", "drying_active",
)
SETTING_PAIR_S = 15.0  # a setpoint and a mode changed this close may reach one cycle: its debouncer waits 10 s
SETPOINT_SETTLE_S = 1200.0  # after a setpoint change the rooms and their thermostats settle first
# How long an unload waits for the cycle under way: one call times out after 30 s, and none follows it
UNLOAD_WAIT_S = 120.0
EVALUATION_FAILURES_LET_GO = 3  # cycles in a row whose evaluation raised: the heat pump is let go of, as on Observe only

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
    """The advice of a cycle in which the heat pump heats on with no room calling.

    Held at its least speed it does so by design, until the rooms are warm enough to rest. "No room
    needs heat" next to it was taken for a fault three times on the test day of 2026-10-13: the reason
    says that it runs through instead.
    """
    return _instead_of_no_demand(advice, reason("heat_pump_runs_through", target_c=setpoint))


def _instead_of_no_demand(advice: Advice, said: Reason) -> Advice:
    """The advice with "no room needs heat" put the way the heat pump control saw it."""
    return replace(advice, reasons=tuple(said if item.code == "no_demand" else item for item in advice.reasons))


# hass.data key: (flow number, heating switch) -> (entry id, remover) of a hand-back that waits for the boiler's
# entities to come back. Keyed by the entities, not the entry: an entry deleted and added again has a new id, and the
# old wait set off by the new block's own write switched the heating on against it (second review 2026-11-08).
HAND_BACK_RETRIES = f"{DOMAIN}_hand_back_retries"


def cancel_hand_back_retry(hass: HomeAssistant, entry_id: str, entities: tuple[str, str] | None = None) -> None:
    """A run starts that commands these entities, or one of the entry starts again: the waiting for them ends.

    The run of the entry finds in its store what the entities hold; a run of another entry commands them itself, and
    is only given `entities` when it does: one that plans only or is off never writes them (verifier of 0.7.3b).
    """
    retries: dict[tuple[str, str], tuple[str, Callable[[], None]]] = hass.data.get(HAND_BACK_RETRIES, {})
    for key in [k for k, (owner, _) in retries.items() if owner == entry_id or (entities and set(k) & set(entities))]:
        retries.pop(key)[1]()


COARSE_THRESHOLD_K = 0.5  # a valve opening further below its setpoint than this gets a hint behind Better Thermostat


def _radiator_can_heat(res: RoomResult) -> bool:
    """Whether the boiler's heat reaches the room's radiator, as for the heating curve (boiler_load.counts): not with its
    thermostat or valve saying it does not heat, nor with a valve driven directly being exercised or on a flat battery.
    Review 2026-11-18: a room well below its target drifting 0.2 K in six hours by the sun, its valve shut, was learned
    as 0.1 K/h of the boiler."""
    return res.thermostat_heating is not False and res.valve_unable is None


def _burned_within(burns: Iterable[tuple[float, float]], now_ts: float, within_s: float = 3600.0) -> bool:
    """Whether the burner ran in the last `within_s`: only then does a room well below its target count as heating up."""
    return any(end >= now_ts - within_s for _, end in burns)


def _memory_after_part(
    before: valve_plan.ValveMemory, nxt: valve_plan.ValveMemory, sent: Iterable[valve_plan.Write]
) -> valve_plan.ValveMemory:
    """A valve's memory where its plan went out in part: as before, but the exercise as planned (it goes by the room's
    setpoint whatever became of the writes, review 2026-11-15) and what the writes sent stand for (`Write.memo`):
    the opening sent and the setpoint held back, the opening was taken for the person's next cycle (review 2026-11-18)."""
    kept = replace(before, exercise_step=nxt.exercise_step, exercise_ts=nxt.exercise_ts, opened_ts=nxt.opened_ts)
    fields = {name for write in sent for name in write.memo}
    return replace(kept, **{name: getattr(nxt, name) for name in fields}) if fields else kept


def _measures_the_room(issues: Iterable[str]) -> bool:
    """The room's temperature is what it is controlled by: its own sensor, or the valve in a room set up without one
    (2026-11-15). Not the valve standing in for a failed sensor."""
    return not any(
        i.startswith("using_trv_local_temperature") and i != "using_trv_local_temperature:not_configured" for i in issues
    )


def _exercise_hours(now: datetime) -> bool:
    """A valve exercise begins between 10 and 18 o'clock local time: its motor is heard (§5.6)."""
    return 10 <= dt_util.as_local(now).hour < 18


def _commanded_elsewhere(hass: HomeAssistant, entities: tuple[str, str]) -> bool:
    """A loaded entry commands one of these entities: what it writes is not ours to undo.

    Only one whose boiler control is active: an entry added again starts in Observe only with a fresh store and never
    hands back, and the block stayed on the switch for good (verifier of 0.7.3b, #R4, #R23).
    """
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        own = getattr(coordinator, "boiler_entities", None)
        if own and set(own) & set(entities) and getattr(coordinator, "boiler_control_mode", None) == CTRL_ACTIVE:
            return True
    return False


def retry_hand_back(hass: HomeAssistant, entry_id: str, entities: tuple[str, str], flow: float, heating_on: bool | None) -> None:
    """Hand the boiler back as soon as its entities come back, with no coordinator left to do it.

    Review 2026-11-08: disabled or deleted while an ESPHome gateway rebooted, the block stayed on
    the switch for good, and a deleted entry took the store with it that a reload makes it up from.
    Not tied to the entry: it ends when the hand-back went through or a run commands the entities again.
    """
    cancel_hand_back_retry(hass, entry_id, entities)
    trying = False

    def _mine() -> bool:
        held = hass.data.get(HAND_BACK_RETRIES, {}).get(entities)
        return held is not None and held[1] is remove

    async def _try() -> None:
        nonlocal trying
        if trying or not _mine():
            return
        if _commanded_elsewhere(hass, entities):
            cancel_hand_back_retry(hass, entry_id, entities)  # never over a running entry's command
            return
        trying = True
        try:
            sent = await executors.async_send_boiler_entities(hass, *entities, flow, heating_on, logging.DEBUG)
        finally:
            trying = False
        if sent and _mine():
            cancel_hand_back_retry(hass, entry_id, entities)
            issues.async_clear_hand_back(hass)
            _LOGGER.warning("thriftherm: boiler handed back after all (flow %.1f °C)", flow)

    @callback
    def _changed(_event: Event[EventStateChangedData]) -> None:
        hass.async_create_task(_try())

    remove = async_track_state_change_event(hass, list(entities), _changed)
    hass.data.setdefault(HAND_BACK_RETRIES, {})[entities] = (entry_id, remove)



def direct_margin(hass: HomeAssistant, cfg: RoomConfig, wants: bool, end_above_k: float, step: float) -> float:
    """What a room driven directly gets above its target (valve_plan.direct_on_top): by the coarsest of its valves (one
    left at the factory's 1 K opened below the call's start; review 2026-11-15, second round), in half degrees at least,
    as the setpoint goes out rounded to them (room_control.decide)."""
    threshold = max((valve_view(hass, valve).hysteresis_k for valve in cfg.valve_entities), default=0.0)
    return valve_plan.direct_on_top(wants, threshold, end_above_k, max(step, room_control.KNOB_STEP_K))

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
        # the people whose arrival home ends away mode: each one's last known place (engines/presence.py)
        self._whereabouts: dict[str, str | None] = {}
        # learning
        self._store: Store = persistence.ThrifthermStore(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}", minor_version=STORAGE_MINOR_VERSION
        )
        self.cop_map = CopMap()
        self.heat_rates = HeatRateStats()
        self.cool_rates = CoolRateStats()
        self.ac_cool_rates = AcCoolRateStats()  # how fast the unit cools each room it serves (docs/PHASE12_COOLING.md §20.3)
        self._last_store_save: float = 0.0
        self._store_dirty = False
        # heat pump control
        self.control_mode: str = CTRL_SHADOW
        self.control_memory = ControlMemory()
        self._control_memory_before = ControlMemory()  # as it was before this cycle's plan, see _forget
        self.boosts: dict[str, float] = {}
        # boiler control
        self.boiler_control_mode: str = CTRL_SHADOW
        self.boiler_memory = BoilerMemory()
        # what the boiler learning by load will be judged on: one compact row per cycle, half a day of them
        self.load_log: deque[list] = deque(maxlen=LOAD_LOG_ROWS)
        self.last_learning_reset: dict[str, Any] | None = None
        # gas for heating and gas for hot water, counted apart (engines/gas_split.py)
        self.gas_split = gas_split.GasSplit()
        # the boiler pump's electricity per kWh of boiler heat, while its power is set (engines/pump_share.py)
        self.pump_share = pump_share.PumpShare()
        self.boiler_fault = boiler_engine.BoilerFault()  # signs of a fault of the boiler (issue #5)
        self._pump_last: tuple[float, float] | None = None  # (when, gas for heating then) at the last cycle
        # room control via the room thermostats
        self.room_control_mode: str = CTRL_SHADOW
        self.room_ctrl_memory: dict[str, RoomCtrlMemory] = {}
        # valves driven directly (docs/PHASE13_VALVES.md): what each was given, kept over a restart; what a cycle plans
        # goes in force once it is sent (`_valve_memory_next`)
        self.valve_ctrl_memory: dict[str, valve_plan.ValveMemory] = {}
        self._valve_unanswered: dict[tuple[str, str], tuple[int, float]] = {}  # a valve's write it did not show yet
        self._own_targets: dict[str, float] = {}  # each room's own target of the last cycle, before heating along
        self._no_exercise: set[str] = set()  # rooms in which no valve exercise may begin this cycle
        self._valve_memory_next: dict[str, valve_plan.ValveMemory] = {}
        # the rooms heating along last cycle and their valves' opening: a trial of the heating curve is judged by the
        # burner only while these stay as they were (boiler_learning.review_trials)
        self._heating_along: tuple[tuple[str, float], ...] = ()
        # when each room's valve was last open in part by the flow: not kept over a restart, a call counts anew after it
        self._valve_limited_ts: dict[str, float] = {}
        # how far each room's valve opens on another room's flow, as learned (engines/flow_throttle.py; stored)
        self.flow_throttle: dict[str, flow_throttle.Throttle] = {}
        # the rooms heating up to a raised target they have not reached yet (boiler_load.heating_up): the heating curve
        # learns from holding a temperature, not from heating up to it (16.11.)
        self._heating_up: set[str] = set()
        self._valve_corrected: dict[str, valve_plan.ValveMemory] = {}  # way c learned for a first valve this cycle
        self._valve_steps: dict[str, str | None] = {}  # the step of its exercise this cycle
        self._exercise_begun = False  # an exercise began this cycle: the next valve waits a cycle
        # a valve whose write failed waits 2, 4, 8 ... minutes, an hour at most (review 2026-11-15: 30 s timeouts a cycle)
        self._valve_retry: dict[str, tuple[int, float]] = {}  # valve -> (failures in a row, next try)
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
        self._preheat_began: dict[str, float] = {}  # room -> when its preheat really began (engines/room.preheat_began)
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
        self._unloading = False  # the entry is being unloaded: a cycle under way sends nothing more
        self._boiler_lock = asyncio.Lock()  # one write to the boiler's entities at a time
        self._started_ts = time.time()
        self._heat_pump_gone_since: float | None = None  # when the heat pump last went away, while it is
        self._arrived: set[str] = set()  # the inputs that have reported since the start (engines/safety.arrivals)
        self._frost_rooms: tuple[str, ...] = ()
        self._last_room_plans: dict[str, tuple[str, float | None]] = {}
        self._last_targets: dict[str, float] = {}
        self._last_target_change_ts: float = 0.0
        self._last_target_change_rooms: tuple[str, ...] = ()  # whose setpoint started the learning pause
        self.room_temps: dict[str, dict[str, float]] = {}
        # cooling (docs/PHASE12_COOLING.md): the season chosen by hand, since when, the cooling overrides per room
        self.season: str = SEASON_HEATING  # the season in force
        self.season_since_ts: float | None = None  # its last real change, by hand or by the automatic
        # stage 2 (§20.2): the choice (heating, cooling or automatic), why the automatic chose, what it measures
        self.season_choice: str = SEASON_HEATING
        self.season_reason: str | None = None
        self.free_run = season_engine.FreeRunDays()
        self.outdoor_hours = season_engine.OutdoorHours()
        self.unit_modes_seen: tuple[str, ...] = ()  # the unit's modes as last seen: unreachable is no reason to heat
        self._season_evaluated_ts: float | None = None
        self.cool_overrides: dict[str, Override] = {}
        self._cool_precool_latch: dict[str, float] = {}  # room -> the block start or return a begun pre-cool is for
        self._failed_cycles = 0  # cycles in a row whose evaluation raised
        self._run_on_after_cooling = False  # the fan runs on after a cooling run: no heat, whatever the season says now

    @property
    def room_climate_rooms(self) -> tuple[str, ...]:
        """The rooms with a room climate of Thriftherm's own (climate.py): every room. First those the heat pump cools,
        then every room driven directly (steered by Siri, 2026-11-15), since 2026-11-17 every room: the living room
        with Better Thermostat had none, and a target set there would have gone past Thriftherm."""
        return tuple(r.key for r in self.builder.rooms)

    def own_target(self, room: str) -> float | None:
        """The room's own target of the last cycle, before heating along raised it for the boiler's sake."""
        if room in self._own_targets:
            return self._own_targets[room]
        res = (self.data or {}).get("rooms", {}).get(room)
        return None if res is None else res.target

    @property
    def cooling_allowed(self) -> bool:
        """The options allow cooling with the heat pump."""
        return const.COOLING_PREVIEW and self.has_heat_pump and bool(self.config.get(CONF_HEAT_PUMP_COOLING_ALLOWED, False))

    @property
    def unit_can_cool(self) -> bool:
        """The heat pump offers a cooling mode, as far as its entity tells, or told when it was last reachable."""
        modes = self._unit_modes_now()
        return "cool" in (self.unit_modes_seen if modes is None else modes)

    def _unit_modes_now(self) -> tuple[str, ...] | None:
        state = self.hass.states.get(self.config.get(CONF_HEAT_PUMP_CLIMATE) or "")
        modes = None if state is None else state.attributes.get("hvac_modes")
        return tuple(str(m) for m in modes) if modes else None

    @property
    def cooling_season(self) -> bool:
        """Thriftherm cools now: allowed, and the season chosen is cooling. Heating then holds the frost protection only."""
        return self.cooling_allowed and self.season == SEASON_COOLING

    @property
    def cool_floor(self) -> float:
        """The cooling floor of the options: no room is cooled below it."""
        return float(self.config.get(CONF_COOL_FLOOR_TEMP, DEFAULT_COOL_FLOOR_TEMP))

    @property
    def cheap_power(self) -> bool:
        """The user's own "power is cheap" entity is on (docs/PHASE12_COOLING.md §20.1); unreadable or missing is off."""
        entity_id = self.config.get(CONF_COOLING_CHEAP_POWER_ENTITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.state == "on"

    @property
    def _cheap_k(self) -> float:
        """How much lower the cooling targets are now: only in the cooling season, only with cheap power."""
        if not (self.cooling_season and self.cheap_power):
            return 0.0
        return float(self.config.get(CONF_COOLING_CHEAP_POWER_K, DEFAULT_COOLING_CHEAP_POWER_K))

    @property
    def _cool_shortfall(self) -> str:
        chosen = self.config.get(CONF_HEAT_PUMP_COOL_SHORTFALL)
        return chosen if chosen in COOL_SHORTFALL_CHOICES else COOL_SHORTFALL_LIFT

    @property
    def seasons(self) -> list[str]:
        """The choices: heating, cooling and automatic, where cooling is allowed and the unit can cool."""
        if self.cooling_allowed and self.unit_can_cool:
            return [SEASON_HEATING, SEASON_COOLING, SEASON_AUTO]
        return [SEASON_HEATING]

    async def async_set_season(self, season: str) -> None:
        """Heating, cooling or automatic. Refused where cooling is not allowed or the unit cannot cool.

        Automatic keeps the season in force; the 72 hours it holds count from its last real change (§20.2).
        """
        if season not in self.seasons:
            return
        if season == self.season_choice and (season == SEASON_AUTO or season == self.season):
            return
        self.season_choice = season
        self._store_dirty = True
        if season != SEASON_AUTO:
            self.season_reason = None  # chosen by hand: the automatic's reason no longer holds
        if season == SEASON_AUTO:
            self._season_evaluated_ts = None  # judged at the next cycle
            _LOGGER.info("thriftherm season chosen automatically, %s in force", self.season)
        elif season != self.season:
            self._set_season(season, None)
        await self._async_changed()

    def _set_season(self, season: str, reason: str | None) -> None:
        self.season = season
        self.season_since_ts = time.time()
        self.season_reason = reason
        self._store_dirty = True
        _LOGGER.info("thriftherm season set to %s%s", season, f" ({reason})" if reason else "")

    @property
    def season_verdicts(self) -> list[str | None]:
        """The automatic's verdicts of the last three days, yesterday first (None: the day did not count)."""
        return [v for _, v in self.free_run.verdicts(dt_util.now())]

    def cool_temperature(self, room: str, kind: str) -> float:
        """The cooling comfort (`cool_comfort`) or the target outside the cooling schedule (`cool_outside`) of a room."""
        stored = self.room_temps.get(room, {}).get(kind)
        if stored is not None:
            return stored
        return DEFAULT_COOL_COMFORT_TEMP if kind == "cool_comfort" else DEFAULT_COOL_OUTSIDE_TEMP

    async def async_set_cool_temperature(self, room: str, kind: str, value: float) -> None:
        """As for heating, the other way round: outside the schedule never cooler than the comfort; the other follows."""
        temps = self.room_temps.setdefault(room, {})
        temps[kind] = float(value)
        if kind == "cool_comfort" and self.cool_temperature(room, "cool_outside") < value:
            temps["cool_outside"] = float(value)
        if kind == "cool_outside" and self.cool_temperature(room, "cool_comfort") > value:
            temps["cool_comfort"] = float(value)
        _LOGGER.info("thriftherm %s: %s temperature set to %.1f °C", room, kind, value)
        await self._async_changed()

    async def async_set_cool_override(self, room: str, temperature: float, duration_min: int | None) -> None:
        minutes = duration_min or self._override_default_min
        now = time.time()
        self.cool_overrides[room] = Override(float(temperature), now + minutes * 60.0, now, hand.ACTION)
        _LOGGER.info("thriftherm cooling override: %s -> %.1f °C for %d min", room, temperature, minutes)
        await self._async_changed()

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

    def left_to_heat_pump(self, room_keys, cheaper: bool, heats: bool, frost: bool) -> frozenset[str]:
        """The rooms of "heat pump alone" while the unit heats them as the cheaper source: the boiler is not theirs then.

        A burner run for another room still warms them through a valve open below target, but they set no flow, count
        for no load, teach the curve nothing (their slow rise is what was chosen) and do not heat along. Dearer, blocked
        or in frost the boiler is theirs again.
        """
        chosen = self.config.get(CONF_HEAT_PUMP_SHORTFALL)
        return frozenset(room_keys) if chosen == SHORTFALL_ALONE and self.has_heat_source and cheaper and heats and not frost else frozenset()

    def shortfall(self, mode: str) -> tuple[bool, bool, bool]:
        """When a heat pump room lags or its preheat runs late: whether the boiler adds the peak, whether the
        unit may be set higher instead, and whether the boiler helps only at the comfort floor of "warm up slower"
        (the option "when the heat pump alone is not enough").

        Without a central heat source, or in the mode "heat pump only", there is no boiler to help; without one
        at all the unit is set higher whatever is chosen, else it would never catch up.
        """
        chosen = str(self.config.get(CONF_HEAT_PUMP_SHORTFALL) or DEFAULT_HEAT_PUMP_SHORTFALL)
        boiler_there = self.has_heat_source and mode != MODE_HEAT_PUMP_ONLY
        at_floor = boiler_there and chosen == SHORTFALL_SLOWER
        boiler_can_help = boiler_there and (chosen == SHORTFALL_BOILER_HELPS or at_floor)
        least_power_only = chosen in (SHORTFALL_SLOWER, SHORTFALL_ALONE)
        return boiler_can_help, not self.has_heat_source or not least_power_only, at_floor

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
    def celsius(self) -> bool:
        """Home Assistant shows temperatures in °C, the only unit this control reads and writes.

        The setup refuses any other; a unit system switched after it keeps the control hands-off.
        """
        return self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS

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
        # the release in the options is the one step: observe only goes active (forum, 26.11.2026)
        persistence.apply_release(self, data if isinstance(data, dict) else None)
        if self._bind_cop_map():
            _LOGGER.warning("thriftherm: fan stage or airflow curve changed, the COP table starts anew")
            self._forget({learning_reset.SCOPE_HEAT_PUMP}, None, learning_reset.REASON_CONFIGURATION)
        if not self.has_boiler:
            # no own boiler: never plan or send a SetMode, whatever was stored
            self.boiler_control_mode = CTRL_OFF
        # This entry's waiting ends: its store tells what the entities hold. Another's only when this run commands them.
        commands = self.boiler_entities if self.boiler_control_mode == CTRL_ACTIVE else None
        cancel_hand_back_retry(self.hass, self.entry.entry_id, commands)
        watched = self.builder.watched_entities()
        if watched:
            self.entry.async_on_unload(async_track_state_change_event(self.hass, list(watched), self._on_watched_change))
        self._track_setpoints()
        signs = [e for e in (self.config.get(CONF_BOILER_FLOW_TEMP), self.config.get(CONF_BOILER_STATE_NUMBER)) if e]
        if self.has_boiler and signs:
            self.entry.async_on_unload(async_track_state_change_event(self.hass, signs, self._on_boiler_sign))
        self.entry.async_on_unload(self._untrack_setpoints)
        self.entry.async_on_unload(self._cancel_knob_timer)
        self._track_settings()
        self._track_persons()

    def _track_persons(self) -> None:
        """Follow the people chosen for the return from away; where they are at the start is taken as it is."""
        persons = [str(p) for p in self.config.get(CONF_AWAY_RETURN_PERSONS) or []]
        if not persons:
            return
        for entity_id in persons:
            state = self.hass.states.get(entity_id)
            self._whereabouts[entity_id], _ = presence.follow(None, state.state if state is not None else None)
        self.entry.async_on_unload(async_track_state_change_event(self.hass, persons, self._on_person_change))

    @callback
    def _on_person_change(self, event: Event[EventStateChangedData]) -> None:
        """One of the chosen people came home while away: away ends as at a planned return, at once."""
        entity_id, new = event.data["entity_id"], event.data.get("new_state")
        # A person that just appeared (Home Assistant starting) has no place before it: no arrival.
        before = self._whereabouts.get(entity_id) if event.data.get("old_state") is not None else None
        self._whereabouts[entity_id], arrived = presence.follow(before, new.state if new is not None else None)
        if arrived and self.mode == MODE_AWAY:
            _LOGGER.info("thriftherm: %s came home, leaving away mode", entity_id)
            self._leave_away()
            self.hass.async_create_task(self.async_request_refresh())

    def _room_thermostats(self) -> frozenset[str]:
        return frozenset(room.climate_entity for room in self.builder.rooms if room.climate_entity)

    def _track_settings(self) -> None:
        """Record who changes the mode or the setpoint of what the control commands."""
        commanded = {room.climate_entity for room in self.builder.rooms if room.climate_entity}
        # the further valves of a room driven directly: a change asked meanwhile is not written over (review 2026-11-15)
        commanded.update(v for room in self.builder.rooms for v in room.valve_entities)
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
            if new.entity_id in self._room_thermostats():
                # A room thermostat or valve that comes back with another setpoint restarted (a valve after a change of
                # batteries comes with its factory 15 °C) or kept one of its own (Better Thermostat switched on again with
                # the 21 °C it had stored): neither is a hand (2026-11-13). The room gets ours again; a knob turned while
                # it was away is lost that way, turned again it counts (docs/PHASE13_VALVES.md §5.3).
                self._setting_changes.pop(new.entity_id, None)
                return None
            change = SettingChange(ts, hand.DEVICE, setpoint=before[1] != setting[1], mode=before[0] != setting[0])
            self._setting_changes[new.entity_id] = change
            return change
        mode, setpoint = new.state != old.state, setting[1] != old.attributes.get("temperature")
        if not mode and not setpoint:
            return None
        context = event.context
        origin = hand.origin(context.id, context.user_id, context.parent_id, self._own_contexts, self._asked_contexts)
        # A mode changed right behind a setpoint must not hide it: the cycle that judges them may come after both.
        previous = self._setting_changes.get(new.entity_id)
        if previous is not None and previous.setpoint and previous.origin != hand.OURS and ts - previous.ts <= SETTING_PAIR_S:
            setpoint = True
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

    def _bind_cop_map(self) -> bool:
        """Tell the COP table what it measures with. True: its data was measured with something else."""
        params = self.builder.params
        return self.cop_map.bind(params.heat_pump_fan_stage, params.airflow_curve, params.duct_factor)

    async def async_save_store(self, force: bool = False) -> None:
        now = time.time()
        if not force and not self._store_dirty and now - self._last_store_save < STORE_SAVE_INTERVAL_S:
            return
        self._last_store_save = now
        self._store_dirty = False
        await self._store.async_save(persistence.to_store(self))

    async def async_stop_cycles(self) -> None:
        """No new cycle, and the one under way finished, before the entry saves and lets go.

        Review 2026-11-08: a reload for an options change ran while a cycle was sending. It went on
        sending past the forced save and the release, the room setpoints with the old modes, even
        after active control had just been taken back. From here it sends nothing more; the call
        that is out already ends by itself, within its timeout.
        """
        self._unloading = True
        await self.async_shutdown()
        try:
            async with asyncio.timeout(UNLOAD_WAIT_S), self._debounced_refresh.async_lock():
                pass
        except TimeoutError:
            _LOGGER.warning("thriftherm: the cycle under way did not end within %.0f s, unloading all the same", UNLOAD_WAIT_S)

    def _boiler_held(self, now_ts: float) -> bool:
        """Whether signs of a fault keep the boiler from a release it would get: not on its knob, not with one in force."""
        return (
            self.has_boiler
            and self.boiler_control_mode == CTRL_ACTIVE
            and self.boiler_memory.state != BOILER_PLAN_HEAT
            and boiler_engine.no_new_release(self.boiler_fault, now_ts)
        )

    def _note_boiler_signs(self, flow_c: float | None, waits_for_water: bool, now_ts: float) -> None:
        """Put signs of a boiler fault on record; the store only for what is new, not for each cycle a sign lasts."""
        before = self.boiler_fault
        self.boiler_fault = boiler_engine.watch_fault(before, flow_c, waits_for_water, now_ts)
        if self.boiler_fault == before:
            return
        ticks = replace(self.boiler_fault, seen_ts=before.seen_ts, overheat_last_ts=before.overheat_last_ts) == before
        if not ticks or before.seen_ts is None or now_ts - before.seen_ts >= STORE_SAVE_INTERVAL_S:
            self._store_dirty = True

    @callback
    def _on_boiler_sign(self, event: Event[EventStateChangedData]) -> None:
        """A flow or a status code between two cycles: 112.94 °C on 01.11. came just after one and was gone by the next."""
        new = event.data.get("new_state")
        if new is None:
            return
        now_ts = new.last_updated.timestamp()
        if new.entity_id == self.config.get(CONF_BOILER_FLOW_TEMP):
            try:
                value = float(new.state)
            except ValueError:
                return
            if RANGE_FLOW_TEMP[0] <= value <= RANGE_FLOW_TEMP[1]:
                self._note_boiler_signs(value, False, now_ts)
            return
        if not self.boiler_entities and whole_number(new.state) in self.boiler_profile.water_wait_states:
            self._note_boiler_signs(None, True, now_ts)
        if self._burner_turned(event.data.get("old_state"), new):
            # Without a gas meter the status number is the burner's best sign: a run between two cycles (under a
            # minute) counted no start (decided 15.11.2026). A cycle where the burner goes on or off, not for every code
            # it ticks through (as the gas flow, measured 3-13 Nov 2026)
            self.hass.async_create_task(self.async_request_refresh())

    def _burner_turned(self, old: State | None, new: State) -> bool:
        """The status number says the heating burner went on or off; only where it is the sign the burns go by."""
        if old is None or self.boiler_entities or self.config.get(CONF_GAS_FLOW):
            return False
        heating = self.boiler_profile.reported_mode
        return (heating(whole_number(old.state)) == BOILER_REPORTS_HEATING) != (
            heating(whole_number(new.state)) == BOILER_REPORTS_HEATING
        )

    def _gas_burns(self, flow_m3h: float) -> bool:
        """A gas flow that counts as the burner running, by the measure of the boiler control."""
        prices = self.builder.prices
        power = economics.gas_power_w(flow_m3h, prices.gas_z_factor, prices.gas_calorific_kwh_m3)
        return power > boiler_control.HOT_WATER_GAS_W

    def _valve_turned(self, old: State, new: State) -> bool:
        """A room thermostat that starts or stops heating, or is blocked or released by Better Thermostat 2.0, is heard
        at once: the boiler need not wait for the next cycle (issue #8). Its other attribute updates wait."""
        if not any(room.climate_entity == new.entity_id for room in self.builder.rooms):
            return False
        return any(old.attributes.get(key) != new.attributes.get(key) for key in ("hvac_action", "heating_blocked_by"))

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
        if old is not None and new is not None and old.state == new.state and not by_others and not self._valve_turned(old, new):
            return
        if old is not None and new is not None and new.entity_id == self.config.get(CONF_GAS_FLOW):
            # A burner that starts or stops is seen at once. A flow that is merely another one waits for the regular
            # cycle: measured 3-13 Nov 2026, it changed every twelve seconds while burning, each change a cycle, and
            # the log of the boiler's load held two and a half hours of a heating day instead of twelve. Burning is
            # what the engines take for it (above 1 kW), not a flow above nought: a meter that ramps or rests a
            # little above 0 would have its start told by the wrong step (review 2026-11-13).
            before, now = as_float(old.state), as_float(new.state)
            if before is not None and now is not None and self._gas_burns(before) == self._gas_burns(now):
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
            was_active, left = self.control_mode == CTRL_ACTIVE, self.control_memory
            self.control_mode = mode
            # a planned run never touched the unit; carrying it over looked like a manual change
            self.control_memory = self.control_memory.fresh_run_state()
            if was_active:
                self.hass.async_create_task(self._async_let_go_after_the_cycle(left, self._control_memory_before))

    async def _async_let_go_after_the_cycle(self, mem: ControlMemory, before: ControlMemory) -> None:
        """Let go once the cycle under way has sent what it was sending.

        Review 2026-11-13: Observe only chosen while a quick heat-up's start went out found the unit not yet at 30 °C
        and left it; the cycle then sent the rest, and nobody set the unit down. `mem` is what that cycle planned,
        so the unit is judged against what it was really left with. `before` is the memory that plan began with: a
        cycle that had planned the end of a quick heat-up and could no longer send it left the unit as `before`
        says. Each let-go acts only on a unit that still stands as its memory left it, so at most one does.
        """
        async with self._debounced_refresh.async_lock():
            await self.async_let_go_of_heat_pump(mem)
            if before is not mem:
                await self.async_let_go_of_heat_pump(before)

    async def _async_let_go_of_cooling(self, mem: ControlMemory, entity_id: str) -> None:
        """A cooling run of ours is switched off as the control lets go: left as it is, it would cool on for good, turned
        up perhaps, and below the floor (docs/PHASE12_COOLING.md §4.1). One somebody set meanwhile stays."""
        if mem.manual_until_ts is not None and mem.manual_until_ts > time.time():
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "cool" or state.attributes.get("temperature") != mem.last_target:
            return
        # Switched off, not left resting in cool: once the control is back, perhaps in the heating season, a unit found
        # in cool would be taken for one cooled by hand and left so for good (review 2026-11-12).
        command = HeatPumpCommand("off", "stop", "off", None, None, None, "control_let_go")
        if await executors.async_execute_heat_pump(self.hass, entity_id, command, self._own_context()):
            _LOGGER.info("thriftherm heat pump: cooling at %s °C as the control lets go, switched off", mem.last_target)
            if mem is self.control_memory:
                self.control_memory = replace(mem, running_since_ts=None, last_hvac_mode="off", stopped_since_ts=time.time())

    async def _async_let_go_of_run_on(self, mem: ControlMemory, entity_id: str) -> None:
        """A fan run-on of ours is ended as the control lets go: its "off" is sent by an active control only, and the
        fan ran until the control was active again, for good after a disable or a delete (review 2026-11-13). A unit
        somebody set meanwhile stays."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "fan_only":
            return
        command = HeatPumpCommand("off", "stop", "off", None, None, None, "control_let_go")
        if await executors.async_execute_heat_pump(self.hass, entity_id, command, self._own_context()):
            _LOGGER.info("thriftherm heat pump: fan running on as the control lets go, switched off")
            if self.control_memory.fan_until_ts == mem.fan_until_ts:
                # also the memory a switch of the control began anew with: it kept the run-on on record
                self.control_memory = replace(self.control_memory, fan_until_ts=None, last_hvac_mode="off")

    async def async_hand_back_valves(
        self, only: set[str] | None = None, leaving: bool = False, sent: Mapping[str, float | None] | None = None
    ) -> None:
        """Give the valves driven directly back what they had (concept §7) as Thriftherm goes: disabled, deleted, Home
        Assistant stopping, active room control left (`leaving`), or (`only`) the valves a new configuration drives no
        more. Only where Thriftherm drove them, in active room control; never a valve Better or Versatile Thermostat drives
        again. A write that fails ends that valve's hand-back (it falls back by itself). `sent`: the setpoint last sent
        to each room's first valve, taken along where the room memory is cleared meanwhile (final review #4)."""
        if sent is None:
            sent = {key: mem.last_sent_target for key, mem in self.room_ctrl_memory.items()}
        if self.room_control_mode != CTRL_ACTIVE and not leaving:
            return
        for valve, mem in list(self.valve_ctrl_memory.items()):
            if (only is not None and valve not in only) or valve_driver(self.hass, valve) is not None:
                continue
            cfg = next((r for r in self.builder.rooms if valve in r.valve_entities), None)
            first = cfg is not None and cfg.valve_entities[0] == valve
            ours_c = (sent.get(cfg.key) if cfg is not None else None) if first else mem.setpoint_c
            target = self._own_targets.get(cfg.key) if cfg is not None else None
            for write in valve_plan.hand_back(valve_view(self.hass, valve), mem, target, ours_c):
                if not await executors.async_write_valve(self.hass, valve, write, self._own_context()):
                    break

    @property
    def direct_valves(self) -> set[str]:
        """The valves this configuration drives directly."""
        return {v for cfg in self.builder.rooms if cfg.direct for v in cfg.valve_entities}

    def _rooms_target(self) -> float | None:
        """The normal target of the rooms the unit serves: the highest of their own (heating along not counted)."""
        served = [r.key for r in self.builder.rooms if r.served_by_heat_pump]
        targets = [t for key in served if (t := self.own_target(key)) is not None]
        return max(targets) if targets else None

    async def async_let_go_of_heat_pump(self, mem: ControlMemory | None = None, to_rooms_target: bool = False) -> None:
        """Set a unit our last command left above its least speed down to its floor, as the control lets go of it.

        Review 2026-11-08: switched to Observe only or Off, or unloaded, mid-way through a quick heat-up (30 °C, the
        fan high) or a drying, the unit kept heating to that setpoint for good: their time limits run here only.
        As the boiler is handed back on the same transitions, the unit gets one last setpoint, the lowest, where
        it rests. A unit held at its least speed is left as it is, and so is one somebody set meanwhile.
        """
        mem = self.control_memory if mem is None else mem
        entity_id = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        boosted = mem.last_target is not None and mem.last_target >= heat_pump_control.BOOST_C
        if self.has_heat_pump and entity_id and mem.fan_until_ts is not None and mem.last_hvac_mode == "fan_only":
            await self._async_let_go_of_run_on(mem, entity_id)
            return
        if self.has_heat_pump and entity_id and mem.running_since_ts is not None and mem.last_hvac_mode == "cool":
            await self._async_let_go_of_cooling(mem, entity_id)
            return
        if not (self.has_heat_pump and entity_id and mem.running_since_ts is not None and mem.last_hvac_mode == "heat"):
            return
        if not (boosted or mem.drying_held) or (mem.manual_until_ts is not None and mem.manual_until_ts > time.time()):
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "heat" or state.attributes.get("temperature") != mem.last_target:
            return  # not as we left it: somebody's now
        step = state.attributes.get("target_temp_step")
        step = step if isinstance(step, (int, float)) else None
        floor = heat_pump_control.to_step(heat_pump_control.SETPOINT_MIN, step)
        # Home Assistant stopping (decided 19.11.): the rooms' normal target, not the floor, and remembered
        if to_rooms_target and (aim := self._rooms_target()) is not None:
            floor = max(heat_pump_control.to_step(aim, step), floor)
        command = HeatPumpCommand("off", "adjust", None, floor, None, None, "control_let_go")
        # ... and its fan back to where the quick heat-up or the drying turned it up from, while it still shows their
        # stage: left on it, that stage read as one chosen by hand once the control was back (review 2026-11-13).
        # After the setpoint and apart from it: a fan stage the unit refuses must not keep it at 30 °C.
        fan = mem.fan_turned_up_from if state.attributes.get("fan_mode") == mem.fan_turned_up_to else None
        if await executors.async_execute_heat_pump(self.hass, entity_id, command, self._own_context()):
            if fan:
                back = HeatPumpCommand("off", "adjust", None, None, fan, None, "control_let_go")
                await executors.async_execute_heat_pump(self.hass, entity_id, back, self._own_context())
            _LOGGER.info("thriftherm heat pump: left at %.1f °C as the control lets go, set down to %.1f °C", mem.last_target, floor)
            if mem is self.control_memory:
                # What the store keeps, and a new run finds: a drying still held at 17 °C stood on it until the unit
                # had stopped itself and stood five minutes, then started hard. Not held, it gets its setpoint at once.
                self.control_memory = replace(mem, last_target=floor, drying_held=False)

    def set_boiler_control_mode(self, mode: str) -> None:
        if self._switch_mode("boiler", self.boiler_control_mode, mode, self.boiler_control_modes):
            self.boiler_control_mode = mode
            if mode == CTRL_ACTIVE:
                cancel_hand_back_retry(self.hass, self.entry.entry_id, self.boiler_entities)  # this run commands them now
            # a fresh telegram right away when switching to active
            self.boiler_memory = replace(self.boiler_memory, last_payload=None, last_send_ts=None)

    def set_room_control_mode(self, mode: str) -> None:
        was_active = self.room_control_mode == CTRL_ACTIVE
        if was_active and mode != CTRL_ACTIVE and self.valve_ctrl_memory:
            # the valves driven directly get back what they had, with what was sent still known (§7, review #6)
            sent = {key: mem.last_sent_target for key, mem in self.room_ctrl_memory.items()}
            self.hass.async_create_task(self.async_hand_back_valves(leaving=True, sent=sent))
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
        if self.cooling_season:
            if not any(r.key == room and r.served_by_heat_pump for r in self.builder.rooms):
                # nothing heats in the cooling season, and the room has no air conditioner to cool it
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_override_in_cooling_season", translation_placeholders={"room": room}
                )
            await self.async_set_cool_override(room, temperature, duration_min)  # in the cooling season the cooling target
            return
        minutes = duration_min or self._override_default_min
        now = time.time()
        self.overrides[room] = Override(target=float(temperature), until_ts=now + minutes * 60.0, set_at_ts=now, by=hand.ACTION)
        _LOGGER.info("thriftherm override: %s -> %.1f °C for %d min", room, temperature, minutes)
        await self._async_changed()

    async def async_switch_room_off(self, room: str, cooling: bool = False) -> None:
        """"Off" on a room's climate: held until it is switched on again (decided 19.11.), at the frost limit, or not
        cooled. An override with no end shown (ROOM_OFF_UNTIL_TS)."""
        now = time.time()
        if cooling:
            self.cool_overrides[room] = Override(room_cooling.NO_COOLING_C, ROOM_OFF_UNTIL_TS, now, hand.ACTION)
        else:
            self.overrides[room] = Override(self.builder.params.frost_temp, ROOM_OFF_UNTIL_TS, now, hand.ACTION)
        _LOGGER.info("thriftherm room %s: switched off on its room climate", room)
        await self._async_changed()

    async def async_switch_room_on(self, room: str, cooling: bool = False) -> None:
        """Back on: its off ends, and only that (decided 19.11.: "heat" ended a quick heat-up and a drying as well)."""
        held = (self.cool_overrides if cooling else self.overrides).pop(room, None)
        if held is not None:
            _LOGGER.info("thriftherm room %s: switched on again on its room climate", room)
            await self._async_changed()

    async def async_clear_override(self, room: str | None) -> None:
        """End what was changed by hand: one room's override, or everything and the heat pump's too."""
        if room is None:
            self.overrides.clear()
            self.cool_overrides.clear()
            self.boosts.clear()
            if self.control_memory.manual_until_ts is not None:
                # back to automatic: a unit left heating is taken over by the next cycle
                self.control_memory = replace(self.control_memory, manual_until_ts=None, manual_by=None)
        else:
            self.overrides.pop(room, None)
            self.cool_overrides.pop(room, None)
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

    @callback
    def _leave_away(self) -> None:
        """Back to Auto from away mode, at the planned return or when somebody comes home, and shown at once."""
        self.mode = MODE_AUTO
        self.away_return_ts = None
        self.away_return_provisional = False
        self._store_dirty = True
        self._async_show_away()

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
        self._forget(chosen, room_keys, learning_reset.REASON_USER, boiler_rooms=room_keys)
        _LOGGER.warning("thriftherm: learning data reset by user: %s %s", list(chosen), room_keys or "")
        await self.async_save_store(force=True)
        await self.async_refresh()

    def _forget(
        self, scopes: Iterable[str], rooms: Iterable[str] | None, reason: str, boiler_rooms: Iterable[str] | None = None
    ) -> None:
        """`rooms` narrows the room area; `boiler_rooms` the boiler's curve to these rooms' lines (a reset by hand)."""
        # Only learned values go. Run state, lockout timers of the controllers and
        # manual-change holds stay, so a reset can never make a unit cycle.
        scopes = set(scopes)
        if learning_reset.SCOPE_BOILER in scopes:
            self.boiler_memory = boiler_learning.reset(self.boiler_memory, boiler_rooms)
            # what a room still shows of its last call is forgotten with it, or it would be taken in again
            gone = None if boiler_rooms is None else set(boiler_rooms)
            self.call_memory = {
                key: replace(mem, last=None) if gone is None or key in gone else mem for key, mem in self.call_memory.items()
            }
        if learning_reset.SCOPE_HEAT_PUMP in scopes:
            self.cop_map = CopMap()
            self._bind_cop_map()
            self.heat_pump_tracker.reset()
            forgotten = {"learning_bins": (), "learning_until_ts": None}
            self.control_memory = replace(self.control_memory, **forgotten)
            # A cycle that waits for the unit's answer puts the memory from before its plan back when
            # the command fails: the learned values must not come back with it.
            self._control_memory_before = replace(self._control_memory_before, **forgotten)
        if learning_reset.SCOPE_ROOMS in scopes:
            self.heat_rates.forget(None if rooms is None else list(rooms))
            self.cool_rates.forget(None if rooms is None else list(rooms))
            self.ac_cool_rates.forget(None if rooms is None else list(rooms))
            self.flow_throttle = {} if rooms is None else {k: t for k, t in self.flow_throttle.items() if k not in rooms}
        narrowed = {learning_reset.SCOPE_ROOMS, learning_reset.SCOPE_BOILER}  # the areas the rooms named narrow
        self.last_learning_reset = {
            "scopes": [s for s in learning_reset.SCOPES if s in scopes],
            "rooms": sorted(rooms) if rooms is not None and scopes & narrowed else None,
            "reason": reason,
            "at": dt_util.utcnow().isoformat(timespec="seconds"),
        }
        self._store_dirty = True

    # ------------------------------------------------------------------ cycle
    async def _async_update_data(self) -> dict[str, Any]:
        memory_before = self.control_memory
        try:
            now = time.time()
            self.overrides = {k: o for k, o in self.overrides.items() if o.until_ts > now}
            self.boosts = {k: t for k, t in self.boosts.items() if t > now}
            if self.mode == MODE_AWAY and self.away_return_ts is not None and self.away_return_ts <= now:
                _LOGGER.info("thriftherm: return time reached, leaving away mode")
                self._leave_away()  # before the slow part of this cycle
            snapshot = self.builder.build(
                self.mode,
                overrides=self.overrides,
                drying_states=self.drying_states,
                # the return the rooms pre-heat for: none while it is provisional
                away_return_ts=None if self.away_return_provisional else self.away_return_ts,
                boosts=self.boosts,
                room_temps=self.room_temps,
                setting_changes=self._setting_changes,
                settings_watched_since_ts=self._settings_watched_since_ts,
            )
            self._track_setpoints()  # the build looked the valves up anew
            self._control_memory_before = self.control_memory  # the heat pump decision moves it on as if its command went through
            result = self._evaluate(snapshot)
        except Exception as err:  # surface to HA as UpdateFailed
            if self.last_update_success:
                # Home Assistant logs the message only, the traceback on debug: once per series here
                _LOGGER.exception("thriftherm: evaluation failed")
            # what the heat pump decision planned did not go out
            self.control_memory = memory_before
            self._failed_cycles += 1
            # nothing below runs: a boiler addressed through entities must not keep a block meanwhile
            await self.async_release_boiler()
            if self._failed_cycles == EVALUATION_FAILURES_LET_GO and self.control_mode == CTRL_ACTIVE and not self._unloading:
                # The time limits of a quick heat-up and of a drying run in the evaluation only: a unit left in one
                # heated on at 30 °C for as long as it failed (review 2026-11-13). Not at the first failure, which
                # would end every quick heat-up for one bad cycle.
                await self.async_let_go_of_heat_pump()
            raise UpdateFailed(f"evaluation failed: {err}") from err
        self._failed_cycles = 0
        # The executors never raise and give up after a timeout, so every command below is tried and
        # the store and the repairs still follow. The boiler goes first: a heat pump that does not
        # answer must not delay its SetMode.
        boiler_cmd: BoilerCommand = result["boiler_command"]
        if self.has_boiler and self.boiler_entities:
            await self._async_command_boiler_entities(boiler_cmd)
        elif self.has_boiler and self.boiler_control_mode == CTRL_ACTIVE and boiler_cmd.send and boiler_cmd.payload:
            if self._unloading or not await executors.async_send_setmode(self.hass, self.boiler_setmode_topic, boiler_cmd.payload):
                # Not carried out, as on the entity path: due again next cycle, and no block counts as in force,
                # or a changed telegram waited for the keep-alive (review 2026-11-08).
                self.boiler_memory = replace(self.boiler_memory, last_payload=None, last_send_ts=None, block_since_ts=None)
        command: HeatPumpCommand = result["midea_command"]
        heat_pump_entity = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        cycle_ts = snapshot.now.timestamp()
        if self.control_mode == CTRL_ACTIVE and self.has_heat_pump and heat_pump_entity and command.action != "none":
            stop = command.action == "stop"
            if self._writes_held(command.target_temp) or self._asked_meanwhile(heat_pump_entity, cycle_ts):
                # planned without what somebody just asked for, or nothing may be written now: not sent,
                # and not on record as sent
                self.control_memory = self._control_memory_before
                self.plan_log.not_sent()
            elif not stop and cycle_ts < self._heat_pump_retry_ts:
                # a call failed lately: the next try waits, see below
                self.control_memory = replace(self._control_memory_before, last_hvac_mode=None)
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
                else:
                    self.plan_log.went_out(cycle_ts)
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
                self.control_memory = replace(self._control_memory_before, last_hvac_mode=None)
                self.plan_log.not_sent()
                wait = heat_pump_control.MANUAL_DETECT_GRACE_S * 2 ** min(self._heat_pump_call_failures, 8)
                self._heat_pump_retry_ts = cycle_ts + min(wait, heat_pump_control.RETRY_MAX_S)
                self._heat_pump_call_failures += 1
        if self.has_boiler and not self.boiler_entities and not self._unloading:
            await self._async_keep_boiler_readings_fresh(result["boiler"].gas_power_w, now)
        if self.room_control_mode == CTRL_ACTIVE:
            climate_of = {r.key: r.climate_entity for r in self.builder.rooms}
            for room_cmd in result["room_commands"].values():
                entity = climate_of.get(room_cmd.room)
                if not (room_cmd.send and room_cmd.target is not None and entity):
                    continue
                # asked anew for each room: an unload may begin while the calls before it are out
                if self._writes_held(room_cmd.target) or self._asked_meanwhile(entity, cycle_ts, setpoint_only=True):
                    # planned without what somebody just asked for, or nothing may be written now: not sent,
                    # and not on record as sent
                    if room_cmd.room in self._room_memory_before:
                        self.room_ctrl_memory[room_cmd.room] = self._room_memory_before[room_cmd.room]
                    continue
                await executors.async_set_thermostat(self.hass, entity, room_cmd, self._own_context())
            await self._async_send_valves(result.get("valve_plans", {}), cycle_ts)
        await self.async_save_store()
        await issues.async_sync(self.hass, self, result)
        return result

    def _writes_held(self, temperature: float | None) -> bool:
        """No command to the heat pump or the room thermostats now: the entry is being unloaded, or one with a
        temperature they would take our °C for another unit.

        A stop carries none: held back as well, a unit boosted to 30 °C heated on at full power for as long as
        Home Assistant showed °F (second review 2026-11-08).
        """
        return self._unloading or (temperature is not None and not self.celsius)

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
        if sent:
            issues.async_clear_hand_back(self.hass)  # the entities take our commands again

    async def _async_hand_back(self, flow: float, heating_on: bool | None = True) -> bool:
        entities = self.boiler_entities
        if entities is None:
            return True
        sent = await executors.async_send_boiler_entities(self.hass, *entities, flow, heating_on, self._boiler_log_level)
        self._boiler_sent(sent)
        if sent:
            if not self._boiler_entities_released:
                _LOGGER.warning("thriftherm: boiler handed back (heating on, flow %.1f °C)", flow)
            self._holds_boiler(False)
            if self._boiler_handed_back_flow != flow:
                self._boiler_handed_back_flow = flow
                self._store_dirty = True
        return sent

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
            heating_on: bool | None = True
            if self._boiler_entities_released is False:
                sent = await self._async_hand_back(flow_max if final else self._hand_back_flow())
            elif final and self._boiler_entities_released and self._boiler_handed_back_flow not in (None, flow_max):
                # Handed back earlier, it has followed the curve since, and nothing follows it once we stop.
                # The switch was the user's from the hand-back on and stays as it is.
                heating_on = None
                sent = await self._async_hand_back(flow_max, heating_on=None)
            else:
                sent = True  # never commanded: the entities are not ours to write
            if final and not sent:
                # Nothing of ours runs after this one: said loud, whatever the outage was logged at before,
                # put up as a repair, and made up as soon as the entities are back (review 2026-11-08).
                number, switch = self.boiler_entities
                _LOGGER.error(
                    "thriftherm: boiler not handed back, %s and %s did not take it; done as soon as they are back,"
                    " else switch %s on by hand",
                    number, switch, switch,
                )
                issues.async_raise_hand_back(self.hass, number, switch)
                retry_hand_back(self.hass, self.entry.entry_id, (number, switch), flow_max, heating_on)

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

    def _pump_power_w(self) -> float:
        try:
            return max(float(self.config.get(CONF_BOILER_PUMP_POWER_W) or 0.0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _count_pump(self, snapshot: HeatingSnapshot, now_ts: float) -> None:
        """Add the boiler pump's running time and the boiler heat since the last cycle, while its power is set."""
        power = self._pump_power_w()
        if not self.has_boiler or power <= 0.0:
            self._pump_last = None
            return
        heating_gas = self.gas_split.heating_kwh
        last, self._pump_last = self._pump_last, (now_ts, heating_gas)
        if last is None:
            return
        heat = max(heating_gas - last[1], 0.0) * snapshot.prices.boiler_efficiency
        running = boiler_engine.pump_heating(snapshot.boiler)
        self.pump_share = pump_share.count(self.pump_share, now_ts - last[0], running, power, heat)

    def _with_preheat_rate(self, state: RoomState, mode: str, heat_pump_heat_possible: bool, outdoor: float | None) -> RoomState:
        """The room with the heat-up rate its preheat is planned with: that of the source expected to heat it.

        That is the heat pump in a room it serves where it may heat now, and where it was the cheaper
        source last cycle or the only one; otherwise the boiler. A heat pump that only plans heats nothing.
        """
        by_heat_pump = (
            state.config.served_by_heat_pump
            and heat_pump_heat_possible
            and (mode == MODE_HEAT_PUMP_ONLY or not self.has_heat_source or self._last_cheaper == SOURCE_HEAT_PUMP)
        )
        key = state.config.key
        if by_heat_pump:
            rate = self.heat_rates.planning_rate(
                key, HEAT_BY_HEAT_PUMP, self.cool_rates.rate(key), state.temperature.value_or_none, outdoor
            )
        else:
            # Projected onto the weather only at the heat pump's fixed least speed: a boiler's heating curve raises
            # the flow in frost, and a leaky radiator room fell to the floor of 0.1 an hour (review 2026-10-30).
            # Few heat-ups mixed with the default (learning.HEAT_RATE_TRUSTED): one at a cycling boiler had the
            # bedroom preheat 5.6 hours (16.11.).
            rate = self.heat_rates.trusted_rate(key, HEAT_BY_BOILER, self.builder.params.default_heat_rate_radiator_k_h)
        return replace(state, heat_rate_k_h=rate, preheat_by_heat_pump=by_heat_pump)

    def _heated_by_hand(self, room: str) -> bool:
        """Somebody turned up the room's thermostat since the cooling season began (a change by hand taken over), not an
        override from the heating season or one asked of Thriftherm itself."""
        held = self.overrides.get(room)
        since = self.season_since_ts or 0.0
        return held is not None and held.by != hand.ACTION and held.set_at_ts >= since and held.until_ts > time.time()

    def _with_cooling_targets(
        self, snapshot: HeatingSnapshot, rooms: dict[str, RoomResult], mode: str, cooling: bool, now_ts: float
    ) -> dict[str, RoomResult]:
        """The served rooms with their cooling targets (engines/room_cooling.py), shown in the heating season too.

        A room counts for the air conditioner only in the cooling season, with a reading and its window shut, and not
        while somebody heats it at its thermostat (a heating override taken from a change by hand): that one is theirs.
        """
        self.cool_overrides = {k: o for k, o in self.cool_overrides.items() if o.until_ts > now_ts}
        floor = self.cool_floor
        cheap_k = self._cheap_k if cooling else 0.0
        latch: dict[str, float] = {}
        out = dict(rooms)
        for cfg in self.builder.rooms:
            res = rooms.get(cfg.key)
            if res is None or not cfg.served_by_heat_pump:
                continue
            aim = room_cooling.cooling_target(
                room_cooling.CoolTargetInputs(
                    now=snapshot.now, mode=mode, temperature=res.temperature,
                    comfort=self.cool_temperature(cfg.key, "cool_comfort"),
                    outside=self.cool_temperature(cfg.key, "cool_outside"),
                    away=float(self.config.get(CONF_COOL_AWAY_TEMP, DEFAULT_COOL_AWAY_TEMP)), floor=floor,
                    schedule=self.builder.cool_schedule(cfg), override=self.cool_overrides.get(cfg.key),
                    away_return_ts=self.away_return_ts, away_provisional=self.away_return_provisional,
                    latch_ts=self._cool_precool_latch.get(cfg.key), margin_s=snapshot.params.preheat_margin_s,
                    cheap_k=cheap_k, rate_k_h=self.ac_cool_rates.rate(cfg.key),
                )
            )
            if aim.precool_for_ts is not None:
                latch[cfg.key] = aim.precool_for_ts
            heated_by_hand = cooling and self._heated_by_hand(cfg.key)
            reason = heat_pump_cooling.HEATED_BY_HAND if heated_by_hand else aim.reason
            allowed = (
                cooling and aim.target is not None and res.temperature is not None and not res.window_open and not heated_by_hand
            )
            out[cfg.key] = replace(
                res, cool_target=aim.target, cool_target_reason=reason,
                cooling_allowed=allowed,
            )
        if latch != self._cool_precool_latch:
            self._cool_precool_latch = latch
            self._store_dirty = True
        return out

    def _learn_ac_cooling(
        self, snapshot: HeatingSnapshot, rooms: dict[str, RoomResult], command: Any, active: bool, outdoor: float | None,
        now_ts: float,
    ) -> None:
        """How fast the unit cools each room it serves at its least speed (docs/PHASE12_COOLING.md §20.3)."""
        if not self.cooling_allowed:
            return
        unit = snapshot.heat_pump
        mem = self.control_memory
        own_cool_run = self.cooling_season and active and command.plan == HEAT_PUMP_PLAN_COOL and unit.hvac_mode == "cool"
        ran = heat_pump_control.ran_s(unit, mem, now_ts)
        cheap = self._cheap_k > 0.0
        for cfg in self.builder.rooms:
            res = rooms.get(cfg.key)
            if res is None or not cfg.served_by_heat_pump:
                continue
            own = res.temperature is not None and _measures_the_room(res.issues)
            learnable = own and ac_cool_learnable(
                own_cool_run=own_cool_run, resting=mem.resting, lifted=mem.cool_lifted, compressor_ran_s=ran,
                override=cfg.key in self.cool_overrides, window_open=bool(res.window_open), cheap_power=cheap,
            )
            episode = self.ac_cool_rates.episodes.get(cfg.key)
            learned = self.ac_cool_rates.observe(cfg.key, now_ts, res.temperature, outdoor, learnable)
            if learned is not None and episode is not None:
                _LOGGER.info("thriftherm learned cooling rate %s: %.2f K/h", cfg.key, learned)
                self._store_dirty = True

    def _observe_season(
        self, snapshot: HeatingSnapshot, rooms: dict[str, RoomResult], boiler_released: bool, active: bool,
        outdoor: float | None, now_ts: float,
    ) -> None:
        """The automatic season (docs/PHASE12_COOLING.md §20.2): measure the rooms, judge once an hour, frost at once.

        Only where cooling is allowed and automatic is chosen: otherwise nothing is measured and nothing stored.
        """
        modes = self._unit_modes_now()
        if modes is not None and modes != self.unit_modes_seen:
            self.unit_modes_seen = modes
            self._store_dirty = True
        if not self.cooling_allowed or self.season_choice != SEASON_AUTO:
            return
        unit = snapshot.heat_pump
        sample_unit = season_engine.UnitSample(
            hvac_mode=unit.hvac_mode, compressor_hz=unit.compressor_hz, resting=active and self.control_memory.resting,
            run_on_after_cooling=self._run_on_after_cooling,
        )
        samples = []
        for cfg in self.builder.rooms:
            res = rooms.get(cfg.key)
            if res is None:
                continue
            served = cfg.served_by_heat_pump and self.has_heat_pump
            own = res.temperature is not None and _measures_the_room(res.issues)
            op = season_engine.room_operation(
                served=served, unit=sample_unit, thermostat_heating=res.thermostat_heating,
                calling=res.boiler_call == "calling", boiler_released=boiler_released, drying=res.drying.active,
                window_open=res.window_open, own_reading=own,
            )
            load = res.drying.active or (res.drying_reason or "") in _HUMIDITY_LOAD_REASONS
            samples.append(
                season_engine.RoomSample(
                    key=cfg.key, contact=bool(cfg.window_sensors), served=served, heats=op.heats, cools=op.cools,
                    free=op.free, temperature=res.temperature,
                    heat_comfort=self.room_temperature(cfg.key, "comfort") or snapshot.params.frost_temp,
                    cool_comfort=self.cool_temperature(cfg.key, "cool_comfort"), load=load, drying=res.drying.active,
                )
            )
        self.free_run.observe(snapshot.now, samples)
        self.outdoor_hours.observe(now_ts, outdoor)
        frost = bool(self._frost_rooms)
        due = self._season_evaluated_ts is None or now_ts - self._season_evaluated_ts >= season_engine.EVALUATE_EVERY_S
        if not due and not (frost and self.season == SEASON_COOLING):
            return
        self._season_evaluated_ts = now_ts
        self._store_dirty = True  # what was measured goes into the store with the next save
        decision = season_engine.decide(
            season_engine.SeasonInputs(
                now_ts=now_ts, season=self.season, since_ts=self.season_since_ts,
                verdicts=self.free_run.verdicts(snapshot.now), outdoor_mean=self.outdoor_hours.mean(now_ts), frost=frost,
                unit_can_cool=self.unit_can_cool,
                cooling_from=float(self.config.get(CONF_COOLING_SEASON_FROM_C, DEFAULT_COOLING_SEASON_FROM_C)),
                heating_from=float(self.config.get(CONF_HEATING_SEASON_FROM_C, DEFAULT_HEATING_SEASON_FROM_C)),
                veto=float(self.config.get(CONF_COOLING_SEASON_VETO_C, DEFAULT_COOLING_SEASON_VETO_C)),
            )
        )
        if decision.changed:
            self._set_season(decision.season, decision.reason)
        elif decision.reason is not None:
            self.season_reason = decision.reason

    def _evaluate(self, snapshot: HeatingSnapshot) -> dict[str, Any]:
        params = snapshot.params
        now_ts = snapshot.now.timestamp()
        # In the cooling season the heating side runs as in the mode "off": frost protection only (docs/PHASE12_COOLING.md
        # §2.3). The mode chosen stays as it is and decides the cooling.
        chosen_mode = snapshot.mode
        cooling = self.cooling_season
        if cooling:
            snapshot = replace(snapshot, mode=MODE_OFF)
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
        if self.room_control_mode == CTRL_ACTIVE:
            # a setpoint a thermostat has not answered makes it silent (room.thermostat_is_silent); in Plan
            # only nothing is sent, and what was pending before says nothing. Behind Better Thermostat the
            # setpoint is answered by its valve reporting after it, not by Better Thermostat taking it at once.
            pending: dict[str, float | None] = {}
            for key, state in snapshot.rooms.items():
                mem = self.room_ctrl_memory.get(key)
                if mem is None:
                    continue
                if state.thermostat_wrapped:
                    heard = room_control.valve_heard(mem, state.thermostat_silent_s, now_ts)
                    if heard is not mem:
                        self.room_ctrl_memory[key] = mem = heard
                        self._store_dirty = True
                pending[key] = room_control.pending_since(mem, state.thermostat_wrapped)
            snapshot = replace(
                snapshot,
                rooms={k: replace(s, thermostat_pending_since_ts=pending.get(k)) for k, s in snapshot.rooms.items()},
            )
        # a run found heating after a restart is no new start (review 2026-11-08)
        snapshot = replace(snapshot, heat_pump=self.heat_pump_tracker.taken_over(snapshot.heat_pump, params, now_ts))
        heat_pump_res: HeatPumpResult = heat_pump_engine.evaluate(snapshot.heat_pump, params)
        held = self.control_memory.manual_until_ts is not None and self.control_memory.manual_until_ts > now_ts
        if held and snapshot.heat_pump.hvac_mode != "heat":
            # During the time in hand nothing is sent: a unit somebody switched off heats no room.
            # A drying started on it would raise the target for the radiator (as on 2026-09-25).
            heat_pump_res = replace(heat_pump_res, heating_available=False)

        # --- defrost / icing -------------------------------------------------
        defrost_res, lockout_changed = self.heat_pump_tracker.defrost(
            snapshot, heat_pump_res.run_state, params, now_ts, boiler_held=self._boiler_held(now_ts)
        )
        if lockout_changed:
            self._store_dirty = True
        # a defrost shows as one, and the run goes on after it (review 2026-11-08)
        shown = self.heat_pump_tracker.run_state(snapshot.heat_pump, heat_pump_res.run_state, defrost_res, params, now_ts)
        heat_pump_res = replace(heat_pump_res, run_state=shown)

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
        snapshot = replace(
            snapshot,
            rooms={
                key: self._while_exercised(self._with_preheat_rate(state, snapshot.mode, heat_pump_heat_possible, outdoor))
                for key, state in snapshot.rooms.items()
            },
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
                cooling_season=cooling,
                frost=key in self._frost_rooms,  # last cycle's list: it is heated to where it leaves it
            )
            for key, state in snapshot.rooms.items()
        }
        rooms, self._preheat_began = room_engine.preheat_began(
            rooms, self._preheat_latch, self._preheat_began, now_ts,
            {key: state.temperature_history for key, state in snapshot.rooms.items()},
        )
        self._preheat_latch = {key: res.preheat_for_ts for key, res in rooms.items() if res.preheat_for_ts is not None}
        if self.cooling_allowed:
            rooms = self._with_cooling_targets(snapshot, rooms, chosen_mode, cooling, now_ts)
        for key, res in rooms.items():
            self.drying_states[key] = res.drying
            was = self._last_drying.get(key, False)
            if res.drying.active != was:
                _LOGGER.info("thriftherm bathroom drying %s: %s (%s)", key, "started" if res.drying.active else "ended", res.drying_reason)
                self._last_drying[key] = res.drying.active

        if self.has_boiler:
            waits_for_water = not self.boiler_entities and snapshot.boiler.state_number in self.boiler_profile.water_wait_states
            self._note_boiler_signs(snapshot.boiler.flow_temp.value_or_none, waits_for_water, now_ts)
        no_new_release = boiler_engine.no_new_release(self.boiler_fault, now_ts)
        boiler_held = self._boiler_held(now_ts)
        starting = now_ts - self._started_ts < ROOM_DATA_GRACE_S
        if starting:
            self._arrived |= safety.arrivals(snapshot, rooms)
        gone = snapshot.heat_pump.configured and not snapshot.heat_pump.available
        self._heat_pump_gone_since = (self._heat_pump_gone_since or now_ts) if gone else None
        heat_pump_gone_s = None if self._heat_pump_gone_since is None else now_ts - self._heat_pump_gone_since
        safety_res: SafetyResult = safety.evaluate(
            snapshot, rooms, self._frost_rooms, boiler_overheated=boiler_engine.overheated(self.boiler_fault, now_ts),
            celsius=self.celsius,
            # inputs that have not arrived after a start are no fault yet; the boiler waits for them as long
            arrived=frozenset(self._arrived) if starting else None,
            heat_pump_gone_s=heat_pump_gone_s,
        )
        self._frost_rooms = safety_res.frost_rooms
        boiler_res: BoilerResult = boiler_engine.evaluate(snapshot.boiler, snapshot.prices)

        # --- COP ---------------------------------------------------------------
        heat_pump_room_keys = [r.key for r in self.builder.rooms if r.served_by_heat_pump]
        served = tuple(rooms[k] for k in heat_pump_room_keys if k in rooms)
        cop_res = self.heat_pump_tracker.cop(
            snapshot,
            # in the cooling season, and in a fan run-on after cooling, the unit gives off no heat to measure
            replace(heat_pump_res, run_state=HEAT_PUMP_OFF) if cooling or self._run_on_after_cooling else heat_pump_res,
            defrost_res, params, now_ts,
            above_minimum=heat_pump_control.why_above_minimum(snapshot.heat_pump, served, self.control_memory),
        )

        # --- economics: settled COP while running, else learned / prior estimate --------
        if outdoor is not None:
            shipped = cop_profiles.profile(params.heat_pump_cop_profile)
            estimate, estimate_basis = self.cop_map.estimate(outdoor, params.duct_factor, shipped)
        else:
            estimate, estimate_basis = None, None
        # Never the COP of a whole cycle, which the sensor may show (see HeatPumpTracker.cop).
        settled_cop = cop_res.value if self.heat_pump_tracker.cop_settled else None
        decision_cop = settled_cop if settled_cop is not None else estimate
        decision_basis = "measured" if settled_cop is not None else estimate_basis
        try:
            prices = snapshot.prices
            pump_kwh = pump_share.kwh_per_kwh(self.pump_share) if self._pump_power_w() > 0.0 else None
            if pump_kwh is not None:
                prices = replace(prices, boiler_pump_kwh_per_kwh=pump_kwh)
            econ: EconomicsResult = economics.evaluate(
                prices, decision_cop, params.break_even_margin_on, params.break_even_margin_off, self._last_cheaper
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

        learning_until = self.control_memory.learning_until_ts
        learning_run = learning_until is not None and learning_until > now_ts
        self.heat_pump_tracker.track_efficiency(settled_cop, econ.break_even_cop, params, now_ts, learning=learning_run)

        # --- learning: COP table ---------------------------------------------------
        # Goes to the store with the next regular save: a restart loses ten minutes at most, of a sum of hours.
        interval = self.heat_pump_tracker.band_interval
        if outdoor is not None and interval is not None:
            self.cop_map.add_interval(outdoor, *interval, now_ts, recheck=learning_run)

        # --- boiler calls: hysteresis, stalled rooms, end of the comfort window ------------
        call_states: dict[str, str] = {}
        # A served room the boiler helps calls from two tenths below: the heat pump control's word of the last cycle,
        # a minute old at most. Called only at 0.3 K below, the boiler stood idle while it was said to help. A help
        # on record from before the option or the mode changed counts no more.
        boiler_can_help, may_lift, help_at_floor = self.shortfall(snapshot.mode)
        boiler_can_help = boiler_can_help and not boiler_held  # no peak to promise from a boiler held back
        helped = set(heat_pump_room_keys) if self.control_memory.boiler_helps and boiler_can_help else set()
        for key, res in rooms.items():
            before = self.call_memory.get(key, heat_call.CallMemory())
            up = boiler_load.heating_up(
                key in self._heating_up, before.target, res, params.call_off_k,
                steady_s=None if before.target_since_ts is None else now_ts - before.target_since_ts,
            )
            self._heating_up = (self._heating_up | {key}) if up else (self._heating_up - {key})
            state, memory = heat_call.update(
                res, self.call_memory.get(key, heat_call.CallMemory()), now_ts, res.comfort_end_ts, helping=key in helped,
                call_on_k=params.call_on_k, call_off_k=params.call_off_k,
            )
            if memory != self.call_memory.get(key):
                self._store_dirty = True
            self.call_memory[key] = memory
            call_states[key] = state
        rooms = {
            key: replace(
                res, boiler_call=call_states[key], stalled_target=self.call_memory[key].stalled_target,
                last_call=self.call_memory[key].last,
                calling_s=now_ts - since if (since := self.call_memory[key].since_ts) is not None else None,
                heating_up=key in self._heating_up,
                target_steady_s=None if (stood := self.call_memory[key].target_since_ts) is None else now_ts - stood,
            )
            for key, res in rooms.items()
        }
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
        blocked = advice.blocked.get("midea")
        if cooling:
            unit_room = self.config.get(CONF_HEAT_PUMP_ROOM)
            command, self.control_memory = heat_pump_cooling.decide(
                heat_pump_cooling.CoolInputs(
                    now_ts=now_ts, control_mode=heat_pump_mode, heat_pump=snapshot.heat_pump,
                    rooms=tuple(rooms[k] for k in heat_pump_room_keys if k in rooms), params=params,
                    floor=self.cool_floor,
                    shortfall=self._cool_shortfall, fan_stage=str(self.config.get(CONF_HEAT_PUMP_COOL_FAN_STAGE) or "auto"),
                    unit_window_open=bool(unit_room in rooms and rooms[unit_room].window_open),
                    safety_state=safety_res.state, frost=bool(safety_res.frost_rooms), started_ts=self._started_ts,
                    heat_pump_gone_s=heat_pump_gone_s, season_since_ts=self.season_since_ts,
                    safety_pending=starting and safety_res.state == SAFETY_FALLBACK and bool(rooms)
                    and all(f"room:{key}" not in self._arrived for key, res in rooms.items() if res.temperature is None),
                ),
                self.control_memory,
            )
        else:
            command, self.control_memory = heat_pump_control.decide(
                ControlInputs(
                    now_ts=now_ts,
                    control_mode=heat_pump_mode,
                    op_mode=snapshot.mode,
                    heat_pump=snapshot.heat_pump,
                    run_state=heat_pump_res.run_state,
                    rooms=tuple(rooms[k] for k in heat_pump_room_keys if k in rooms),
                    advice_source=advice.source,
                    blocked_reason=(
                        None if blocked in (None, heat_pump_res.run_state, "mode_boiler_only", "outdoor_too_cold") else blocked
                    ),
                    safety_state=safety_res.state,
                    outdoor_c=outdoor,
                    expected_cop=decision_cop,
                    break_even_cop=econ.break_even_cop,
                    outdoor_bin=None if outdoor is None else CopMap.bin_of(outdoor),
                    # known and run in within a week: a band that says gas is measured again now and then (review 2026-11-08)
                    bin_learned=outdoor is not None and self.cop_map.learned(outdoor, params.duct_factor, now_ts),
                    params=params,
                    cheaper=econ.cheaper_source == SOURCE_HEAT_PUMP,
                    started_ts=self._started_ts,
                    defrosting=defrost_res.suspected,
                    # the radiators add the peak for a lagging room; without them the unit itself is set higher, or not
                    boiler_can_help=boiler_can_help,
                    may_lift=may_lift,
                    boiler_held=boiler_held,
                    help_at_floor=help_at_floor,  # "warm up slower": the boiler helps from half a degree below only
                    # without a central heat source the unit holds frost and away protection itself (review 2026-11-08)
                    has_heat_source=self.has_heat_source,
                    frost_rooms=safety_res.frost_rooms,
                    heat_pump_gone_s=heat_pump_gone_s,  # a dropout of its bridge keeps the run, as the safety bridges it
                    # also where cooling was taken back in its season: that is a change of season too
                    season_since_ts=self.season_since_ts,
                    # every room unknown only because none has reported since the start: the unit is left as it is
                    safety_pending=starting
                    and safety_res.state == SAFETY_FALLBACK
                    and self.celsius
                    and bool(rooms)
                    and all(f"room:{key}" not in self._arrived for key, res in rooms.items() if res.temperature is None),
                ),
                self.control_memory,
            )
        if command.action == "stop" and (cooling or command.reason == "season_ended"):
            # also the run of the cooling season that the heating control ends once the season is heating
            self._run_on_after_cooling = True
        elif self.control_memory.fan_until_ts is None:
            self._run_on_after_cooling = False
        if self.plan_log.heat_pump(command, now_ts, self.control_mode == CTRL_ACTIVE):
            self._store_dirty = True
        if command.plan == HEAT_PUMP_PLAN_HEAT and heat_pump_acts and not self.control_memory.resting:
            # resting at the floor no room needs heat, as it says (review 2026-10-30: "runs through, setpoint 17 °C")
            advice = _runs_through(advice, command.target_temp)
        elif command.reason == "gas_cheaper" and heat_pump_acts:
            # Seen 2026-10-31: "no room needs heat" with the bathroom at 19.9/20, the unit stopped (now: resting) for gas cheaper
            advice = _instead_of_no_demand(advice, reason("heat_pump_gas_cheaper"))

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
        if cooling:
            learning_blocked.append("cooling_season")
        if outdoor is None and self.has_boiler:
            # no outdoor temperature, and the last known one has run out: a step of the curve would be put at a
            # temperature there never was (review of the curve learning 2026-11-12, finding 6)
            learning_blocked.append("outdoor_temperature_unknown")
        if any(r.boost_active for r in rooms.values()):
            learning_blocked.append("quick_heat_up")
        if any(r.drying.active for r in rooms.values()):
            learning_blocked.append("drying_mode")
        if self.boiler_control_mode != CTRL_ACTIVE:
            # while only planning, the front knob sets the flow: the offset changes nothing
            learning_blocked.append("boiler_control_not_active")
        reported = None if self.boiler_entities else self.boiler_profile.reported_mode(boiler_res.state_number)
        if no_new_release or reported == BOILER_REPORTS_FAULT:
            learning_blocked.append("boiler_fault")  # a flow of 100 °C and a room that stays cold say nothing about the curve
        learning_allowed = not learning_blocked
        # For an hour after hot water the circuit's spread says nothing, and the boiler control
        # holds the spread back itself for that long. What the rooms show stays evidence: with
        # the whole learning switched off for that hour, a combi boiler with its hourly keep-warm,
        # or a household that taps hot water more often than that, never learned.
        seen = self.boiler_memory.hot_water_seen_ts
        hot_water_recent = seen is not None and now_ts - seen < boiler_control.HOT_WATER_HOLDOFF_S

        # --- boiler control: observe only or active ----------------------------------------------
        # Right after a start, a heat pump whose run is kept while its entity is not loaded yet
        # counts as data still missing, like a room sensor that has not reported: the advice would
        # name the boiler for the rooms the unit serves, and the boiler would hold that for its
        # minimum time in state once the unit is back (review 2026-10-14). The same for a dropout of
        # its bridge at any time, whose run is kept as well (review 2026-11-08).
        heat_pump_pending = heat_pump_res.run_state == HEAT_PUMP_UNAVAILABLE and self.control_memory.running_since_ts is not None
        # After a failed call a start is held back for 2, 4, 8 ... minutes (see _async_update_data)
        # and planned again every cycle: counted as heating, it kept the boiler blocked for the
        # whole wait while nothing heated the rooms the unit serves. It counts once a call went through.
        start_failing = heat_pump_mode == CTRL_ACTIVE and command.action == "start" and self._heat_pump_call_failures > 0
        left_to_unit = self.left_to_heat_pump(
            heat_pump_room_keys,
            econ.cheaper_source == SOURCE_HEAT_PUMP,
            blocked is None and command.plan == HEAT_PUMP_PLAN_HEAT and heat_pump_acts and not start_failing,
            bool(safety_res.frost_rooms),
        )
        boiler_cmd, self.boiler_memory = boiler_control.decide(
            BoilerInputs(
                now_ts=now_ts,
                day_start_ts=dt_util.start_of_local_day(dt_util.as_local(snapshot.now)).timestamp(),
                control_mode=self.boiler_control_mode,
                op_mode=snapshot.mode,
                safety_state=safety_res.state,
                boiler_available=boiler_res.available,
                room_data_pending=(
                    now_ts - self._started_ts < ROOM_DATA_GRACE_S
                    and (heat_pump_pending or any(r.temperature is None for r in rooms.values()))
                )
                or (heat_pump_pending and heat_pump_gone_s is not None and heat_pump_gone_s <= HEAT_PUMP_DROPOUT_S),
                rooms=tuple(
                    replace(res, boiler_call=heat_call.CALL_IDLE) if key in left_to_unit
                    # limited by the flow a while ago: its slow rise says nothing about the flow yet (boiler_load.counts)
                    else replace(res, valve_limited_s=round(max(now_ts - self._valve_limited_ts[key], 0.0), 1))
                    if key in self._valve_limited_ts else res
                    for key, res in rooms.items()
                ),
                frost_rooms=safety_res.frost_rooms,
                advice_source=advisor.boiler_source(
                    advice.source,
                    command.plan,
                    heat_pump_mode,
                    self.boiler_control_mode,
                    command.delivers and not start_failing,
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
                no_new_release=no_new_release,
                heating_along=self._heating_along,
            ),
            self.boiler_memory,
        )
        if hot_water_recent and boiler_cmd.learning_phase == boiler_learning.PHASE_PAUSED:
            learning_blocked.append("hot_water_recent")  # shown as what the paused learning waits for
        self.plan_log.boiler(boiler_cmd, now_ts, self.boiler_control_mode == CTRL_ACTIVE)
        self._count_gas(snapshot, boiler_res, now_ts, boiler_cmd.warm_start)
        self._count_pump(snapshot, now_ts)
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
        self._own_targets = {key: res.target for key, res in rooms.items()}  # what a valve handed back gets (§7)
        sent_rooms = rooms
        if boiler_cmd.plan == BOILER_PLAN_HEAT:
            comfort = {
                key: state.config.comfort_temp if state.schedule_next_temp is None else state.schedule_next_temp
                for key, state in snapshot.rooms.items()
            }
            joining = {key: res for key, res in rooms.items() if key not in left_to_unit}
            sent_rooms = {**rooms, **heat_call.join(joining, call_states, comfort, now_ts)}

        # No valve exercise begins in a room whose window is open or that calls, nor while the boiler heats: its "shut"
        # would close a radiator the boiler heats for (concept §5.6; final review 2026-11-15 #6).
        heating = boiler_cmd.plan == BOILER_PLAN_HEAT
        self._no_exercise = {
            k for k, r in rooms.items() if heating or r.window_open or call_states.get(k) == heat_call.CALL_CALLING
        }

        # --- room control via the room thermostats ----------------------------------------------
        room_commands: dict[str, RoomCommand] = {}
        self._valve_corrected, self._valve_steps, self._exercise_begun = {}, {}, False
        for cfg in self.builder.rooms:
            state = snapshot.rooms.get(cfg.key)
            res = sent_rooms.get(cfg.key)
            if state is None or res is None:
                continue
            before = self._room_memory(cfg.key, cfg.climate_entity)
            self._room_memory_before[cfg.key] = before
            # the lead (heat_call.lead_k): while the boiler runs for the room, its thermostat should not throttle early
            own = rooms.get(cfg.key)
            ours = self.boiler_control_mode == CTRL_ACTIVE
            # the boiler waits for its data (restart, ebusd gap): the lead the thermostat has is held, not sent anew
            waiting = (
                ours and boiler_cmd.plan == BOILER_PLAN_HANDS_OFF
                and set(boiler_cmd.blockers) <= {"waiting_for_room_data", "boiler_data_unavailable"}
            )
            # a room on its own controller keeps the target at its valves: their own control is the net (§3.4)
            lead = 0.0 if own is None or cfg.valve_opening else heat_call.lead_k(
                call_states.get(cfg.key, heat_call.CALL_IDLE), res, own, cfg.lead_k,
                # Only while the boiler is ours to run: in "observe only" it goes by its own knob (review 2026-11-13)
                boiler_cmd.plan == BOILER_PLAN_HEAT and ours, cfg.key in left_to_unit,
                leading=before.sent_extra_k - before.sent_correction_k > 0.0,
                held_k=before.sent_extra_k - before.sent_correction_k if waiting else None,
            )
            if cfg.direct and not cfg.valve_opening:
                # one rule for a valve driven directly (review 2026-11-15 §3.4): wanting heat, safely above where it
                # shuts; else the target. The lead of a room thermostat is no number of its own here.
                # Only where a call starts a burner Thriftherm runs (decided 15.11.): with district heating, no central heat
                # source or a boiler only observed, the valve is the one thing that controls the room. It gets the target
                # and controls by the room's reading by itself, as behind Better Thermostat.
                wants = self.has_boiler and ours and (
                    call_states.get(cfg.key) == heat_call.CALL_CALLING or (own is not None and res.target > own.target)
                    # heating along in the setback up to its own target (decided 16.11.): open until the target
                    or res.target_reason in heat_call.JOINED_REASONS
                )
                # a room the boiler helps calls until the target (heat_call: HELP_ON_K .. 0.0)
                end_above = 0.0 if cfg.key in helped else -params.call_off_k
                lead = direct_margin(self.hass, cfg, wants, end_above, state.thermostat_step or 0.5)
            elif cfg.climate_entity and self.has_boiler and ours and call_states.get(cfg.key) == heat_call.CALL_CALLING:
                # A valve opens only its hysteresis below its setpoint: while the room calls, the larger of it and the lead
                # on top, not both (2026-11-15). 0 for Better or Versatile Thermostat, whose device has no such entity.
                lead = valve_plan.on_top_while_calling(lead, valve_view(self.hass, cfg.climate_entity).hysteresis_k)
            # not learned while the window is open: cold air at the room sensor (review 2026-11-15)
            correction = self._valve_correction(cfg, res.target, snapshot.now) if cfg.direct and not res.window_open else 0.0
            hold, hold_reason = None, "window_open_frost"
            # no Better Thermostat to shut its valves while the window is open (§5.1): off, or the frost limit
            window_off = cfg.direct and bool(res.window_open) and params.window_valve_off and self._valves_go_off(cfg)
            hvac_mode = state.trv_hvac_mode
            if cfg.direct and not window_off and hvac_mode == "off":
                first = self.valve_ctrl_memory.get(cfg.valve_entities[0])
                if first is not None and first.off_by_window:
                    hvac_mode = "heat"  # its own off of the window: switched on again this cycle (engines/valve_plan.py)
            if cfg.direct and res.window_open and not window_off:
                hold = params.frost_temp
            elif cfg.direct and not res.window_open and (step := self._valve_exercise(cfg, state, snapshot.now)) is not None:
                # moved, so that it does not stick (§5.6): the ends of its range
                low, high = state.thermostat_min_temp or 5.0, state.thermostat_max_temp or 30.0
                hold, hold_reason = (high if step == "open" else low), "valve_exercise"
            room_cmd, self.room_ctrl_memory[cfg.key] = room_control.decide(
                cfg.key,
                self.room_control_mode,
                res,
                bool(cfg.climate_entity),
                hvac_mode,
                state.trv_target_temp,
                now_ts,
                before,
                state.thermostat_own_target,
                min_temp=state.thermostat_min_temp,
                max_temp=state.thermostat_max_temp,
                step=state.thermostat_step,
                wrapped=state.thermostat_wrapped,
                held_by_hand=cooling and self._heated_by_hand(cfg.key),
                lead_k=lead,
                own_c=None if own is None else own.target,
                hold_c=hold,
                correction_k=correction,
                hold_reason=hold_reason,
            )
            if window_off:
                # the valves are switched off by their plans (engines/valve_plan.py): no setpoint meanwhile
                room_cmd = RoomCommand(cfg.key, ROOM_PLAN_HOLD, None, False, "window_open_off", state.trv_target_temp)
                self.room_ctrl_memory[cfg.key] = before
            after = self.room_ctrl_memory[cfg.key]
            if after.thermostat != cfg.climate_entity:
                self.room_ctrl_memory[cfg.key] = after = replace(after, thermostat=cfg.climate_entity)
            if (after.last_sent_target, after.sent_extra_k, after.sent_correction_k) != (
                before.last_sent_target, before.sent_extra_k, before.sent_correction_k
            ):
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

        # rooms that want heat now, for way d: they call, or heat along (heat_call.join)
        opening_rooms = {r.key for r in self.builder.rooms if r.valve_opening}
        wanting = {
            k for k, r in rooms.items()
            if call_states.get(k) == heat_call.CALL_CALLING or (k in sent_rooms and sent_rooms[k].target > r.target)
            or (k in sent_rooms and sent_rooms[k].target_reason in heat_call.JOINED_REASONS)
            # on its own controller a valve says idle because of our small opening (TRVZB: unreliable): no lock-out
            or (call_states.get(k) == heat_call.CALL_THERMOSTAT_IDLE and k in opening_rooms)
        }
        windows_open = {k for k, r in rooms.items() if r.window_open}
        # off where every valve of the room offers it; one that heats only gets the frost limit (final review #1)
        windows_off = {
            r.key for r in self.builder.rooms if r.key in windows_open and params.window_valve_off and self._valves_go_off(r)
        }
        # heating along, not calling: a valve with an opening limit opens in part (valve_plan.join_opening)
        joined = {
            k: to for k, r in sent_rooms.items()
            if k in rooms and call_states.get(k) != heat_call.CALL_CALLING
            and (to := heat_call.heats_along_to(r, rooms[k])) is not None
        }
        # calling, but another room sets a flow higher than this one needs: the same limit, by the share of its
        # radiator's output it needs (valve_plan.flow_share). Only by a flow Thriftherm sends itself.
        by_flow: dict[str, tuple[float, float]] = {}
        if boiler_cmd.plan == BOILER_PLAN_HEAT and self.boiler_control_mode == CTRL_ACTIVE and boiler_cmd.room_flows:
            by_flow = {
                k: (boiler_cmd.room_flows[k], boiler_cmd.flow_for_rooms) for k in boiler_cmd.throttle_rooms
                if k in boiler_cmd.room_flows and boiler_cmd.flow_for_rooms is not None and k not in joined
                and k not in left_to_unit and call_states.get(k) == heat_call.CALL_CALLING
            }
        valve_plans = self._plan_valves(
            snapshot, room_commands, {k: r.target for k, r in rooms.items()}, wanting, windows_off, windows_open,
            joined, by_flow,
        )
        self._heating_along = tuple(sorted((k, self._opening_along(k)) for k in joined))
        valves_by_flow = {k: pct for k in sorted(by_flow) if (pct := self._opening_along(k)) < 100.0}
        self._valve_limited_ts = {
            **{k: ts for k, ts in self._valve_limited_ts.items() if now_ts - ts < boiler_load.LAG_MIN_CALL_S},
            **dict.fromkeys(valves_by_flow, now_ts),
        }
        # what came of the opening teaches the next one: fallen behind its limited valve, more; gone on past its target
        # after a call that ended limited, less
        for cfg in self.builder.rooms:
            if not cfg.direct or cfg.valve_opening or (res := rooms.get(cfg.key)) is None:
                continue
            was = self.flow_throttle.get(cfg.key, flow_throttle.Throttle())
            sent = sent_rooms.get(cfg.key)
            now_is = flow_throttle.follow(
                was, calling=call_states.get(cfg.key) == heat_call.CALL_CALLING, limited=cfg.key in valves_by_flow,
                behind=cfg.key in boiler_cmd.throttle_behind,
                target=res.target, room_c=res.temperature, now_ts=now_ts,
                disturbed=cfg.key in joined or cfg.key in windows_open or (sent is not None and sent.target > res.target),
            )
            if now_is != was:
                self.flow_throttle[cfg.key] = now_is
                self._store_dirty = self._store_dirty or now_is.adjust_pct != was.adjust_pct

        # --- learning: heat-up rates (heating = a source that reaches the room is on) ----
        # Another room's call heats nothing here: a radiator gets the boiler's heat only where it may open,
        # and the unit reaches only the rooms it serves. The sun warming the living room while the unit
        # heated the bathroom and the boiler was blocked was learned as its heat-up rate, and its real
        # cool-downs were cut off. The boiler's plan already holds the mode and a unit that cannot deliver;
        # with the boiler on its own knob (control off, hands off) the advice is the best guess.
        if boiler_cmd.plan in (BOILER_PLAN_HEAT, BOILER_PLAN_BLOCK):
            boiler_heats = boiler_cmd.plan == BOILER_PLAN_HEAT
        else:
            boiler_heats = advice.source in (SOURCE_BOILER, SOURCE_BOTH)
        heat_pump_heats = advice.source in (SOURCE_HEAT_PUMP, SOURCE_BOTH)
        # Each source's rates apart: the heat pump's only at the least speed a preheat plans with, the
        # boiler's only where the unit's compressor does not run in the room as well (a unit that shows
        # no frequency counts as running in heat mode, unless it rests at the lowest setpoint).
        unit = snapshot.heat_pump
        # cool or dry, ours or by hand, and the fan running on after cooling: the unit takes heat out of its rooms
        # Also where cooling is not allowed: cooled by hand on a summer evening, a room's fall was learned as its heat
        # loss to outside, several times too large (review 2026-11-13, decided: wherever there is a heat pump).
        unit_cools = self.has_heat_pump and (
            unit.hvac_mode in ("cool", "dry") or ((cooling or self._run_on_after_cooling) and unit.hvac_mode == "fan_only")
        )
        compressor_heats = self.has_heat_pump and heat_pump_control.compressor_heats(
            unit, self.control_memory, heat_pump_mode == CTRL_ACTIVE
        )
        at_minimum = heat_pump_mode == CTRL_ACTIVE and heat_pump_control.heats_at_minimum(
            command, self.control_memory, unit, tuple(rooms[k] for k in heat_pump_room_keys if k in rooms), now_ts
        )
        for key, res in rooms.items():
            by_boiler = boiler_heats and res.heating_allowed
            reached = by_boiler or (heat_pump_heats and key in heat_pump_room_keys)
            # a shower warms the bathroom by itself within minutes; that is not a heat-up rate
            wants = (res.demand or 0.0) > 0.0 and not res.drying.active
            heating = reached and wants
            by_heat_pump = compressor_heats and key in heat_pump_room_keys
            source = None
            if wants and by_boiler and _radiator_can_heat(res):
                source = HEAT_BY_BOTH if by_heat_pump else HEAT_BY_BOILER
            elif wants and by_heat_pump and at_minimum:
                source = HEAT_BY_HEAT_PUMP
            # the valve's own reading stands in for a failed room sensor, but it sits at the radiator: rates learned
            # from it would not be the room's. A room set up without a sensor is controlled by it, and learns by it.
            measured = res.temperature if _measures_the_room(res.issues) else None
            episode = self.heat_rates.episodes.get(key)
            # behind its target, a room heats up only while something heats it: with the boiler, its burner ran lately
            fired = source != HEAT_BY_BOILER or _burned_within(self.boiler_memory.burns, now_ts)
            short = None if measured is None or not fired else round(res.target - measured, 2)
            learned = self.heat_rates.observe(
                key, now_ts, measured, source, bool(res.window_open), outdoor, short_k=short
            )
            if learned is not None and episode is not None:
                _LOGGER.info("thriftherm learned heat-up rate %s from the %s: %.2f K/h", key, episode.source, learned)
                self._store_dirty = True
            # how fast the room loses heat: the basis for a setback that really saves
            # Held at its least speed the compressor runs for hours with no room calling (2026-10-30): its rooms
            # drift slowly then, and that is no cool-down.
            # A room the air conditioner cools loses no heat to outside, whatever the gradient (docs/PHASE12_COOLING.md §7.1).
            cooled_by_unit = unit_cools and key in heat_pump_room_keys
            cooled = self.cool_rates.observe(
                key, now_ts, measured, outdoor, heating or by_heat_pump or cooled_by_unit, bool(res.window_open)
            )
            if cooled is not None:
                _LOGGER.info("thriftherm learned cool-down coefficient %s: %.4f per h", key, cooled)
                self._store_dirty = True

        self._observe_season(snapshot, rooms, boiler_heats, heat_pump_mode == CTRL_ACTIVE, outdoor, now_ts)
        self._learn_ac_cooling(snapshot, rooms, command, heat_pump_mode == CTRL_ACTIVE, outdoor, now_ts)

        self.plan_log.changes(advice, safety_res, defrost_res)
        room_names = {r.key: r.name for r in self.builder.rooms}
        reason_state, reason_texts = self.plan_log.reasons(advice, self.hass.config.language, room_names, now_ts)

        # how far each room's valves may open, for the card (0.8.2)
        openings = self._valve_openings()
        sent_rooms = {k: replace(r, valve_opening_pct=openings.get(k)) for k, r in sent_rooms.items()}

        return {
            "snapshot": snapshot,  # what the cycle read; nothing in the integration uses it, the tests look at it
            "mode": snapshot.mode,  # the operating mode the cycle ran in, the advice's own
            "rooms": sent_rooms,
            "safety": safety_res,
            "boiler": boiler_res,
            # the display code in plain words; a boiler addressed through entities has no code
            "boiler_state": None if self.boiler_entities else self.boiler_profile.shown_state(boiler_res.state_number),
            "gas_split": self.gas_split,
            "midea": heat_pump_res,
            "cop": cop_res,
            "econ": econ,
            "defrost": defrost_res,
            "advice": advice,
            "cop_map": self.cop_map.to_dict(params.duct_factor),
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
            "ac_cool_rates": self.ac_cool_rates.to_dict() if self.cooling_allowed else None,  # hidden with cooling
            "heating_power": {
                r.key: by_source
                for r in self.builder.rooms
                if (
                    by_source := {
                        source: value
                        for source in HEAT_SOURCES
                        if (value := self.heat_rates.heating_power_k_h(r.key, source, self.cool_rates.rate(r.key))) is not None
                    }
                )
            },
            "expected_cop_basis": estimate_basis,
            "decision_cop": decision_cop,
            "decision_cop_basis": decision_basis,
            "prior_calibration_factor": self.cop_map.calibration_factor(params.duct_factor),
            "midea_command": command,
            "control_mode": heat_pump_mode,
            # the hold at its least speed: whether it rests, the setpoint held
            "resting": self.control_memory.resting,
            "latched_setpoint_c": heat_pump_control.latched_setpoint(self.control_memory),
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
            "room_names": room_names,
            # the decision reason as the sensor shows it: changed only when what it says changes
            "reason_state": reason_state,
            "reason_texts": reason_texts,
            "room_commands": room_commands,
            "valve_plans": valve_plans,
            "valves_by_flow": valves_by_flow,
            "flow_adjust": {k: t.adjust_pct for k, t in sorted(self.flow_throttle.items()) if t.adjust_pct},
            "echoes_ignored": dict(self.echoes_ignored),
            "room_control_mode": self.room_control_mode,
            "season": self.season,
            "season_since_ts": self.season_since_ts,
            "season_choice": self.season_choice,
            "season_reason": self.season_reason,
            "cool_overrides": {k: {"target": o.target, "until_ts": o.until_ts} for k, o in self.cool_overrides.items()},
        }

    def _valves_go_off(self, cfg: RoomConfig) -> bool:
        """Every valve of the room offers the mode "off" (one that does not say counts as offering it)."""
        for valve in cfg.valve_entities:
            state = self.hass.states.get(valve)
            modes = state.attributes.get("hvac_modes") if state is not None else None
            if isinstance(modes, (list, tuple)) and modes and "off" not in modes:
                return False
        return True

    def _plan_valves(
        self, snapshot: HeatingSnapshot, room_commands: Mapping[str, RoomCommand], targets: Mapping[str, float],
        wanting: frozenset[str] | set[str] = frozenset(),
        windows_off: frozenset[str] | set[str] = frozenset(),
        windows_open: frozenset[str] | set[str] = frozenset(),
        joined: Mapping[str, float] | None = None,
        by_flow: Mapping[str, tuple[float, float]] | None = None,
    ) -> dict[str, valve_plan.ValvePlan]:
        """What the valves of the rooms driven directly get besides the first one's setpoint (engines/valve_plan.py).

        `joined`: the rooms heating along without a call of their own, with what they heat to (heat_call.heats_along_to);
        their valves open in part where they can, by how little the room lacks of it. `by_flow`: the calling rooms that
        need less flow than is sent, with the flow each would ask for itself and the flow sent: in part as well, by the
        share of the radiator's output the room needs (valve_plan.flow_share)."""
        joined, by_flow = joined or {}, by_flow or {}
        plans: dict[str, valve_plan.ValvePlan] = {}
        corrected, self._valve_corrected = self._valve_corrected, {}
        self._valve_memory_next = {}
        now_ts = snapshot.now.timestamp()
        for cfg in self.builder.rooms:
            if not cfg.direct:
                continue
            room_c, fresh = self.builder.valve_room_reading(cfg.temperature_sensor, snapshot.now)
            room_cmd = room_commands.get(cfg.key)
            setpoint = None if room_cmd is None or room_cmd.plan not in ("set", "hold") else room_cmd.target
            if room_cmd is not None and room_cmd.reason == "valve_exercise":
                # the first valve's exercise holds the room's ends: the further ones keep the target (final review #5)
                setpoint = targets.get(cfg.key, setpoint)
            # without a fresh room reading the valve is handed to its own control: fully open, its setpoint limits it
            # (review 2026-11-15: left at 3 % a room whose sensor died was never heated again)
            opening = 100.0 if cfg.valve_opening else None
            lacks = None
            if cfg.key in joined and not cfg.valve_opening and fresh and room_c is not None:
                lacks = round(joined[cfg.key] - room_c, 2)
            share = None
            if cfg.key in by_flow and not cfg.valve_opening and fresh and room_c is not None:
                share = valve_plan.flow_share(*by_flow[cfg.key], room_c)
                if share is not None:
                    share = flow_throttle.adjusted(share, self.flow_throttle.get(cfg.key))
            if cfg.valve_opening and fresh and room_c is not None and cfg.key in targets:
                opening = valve_tpi.opening(
                    targets[cfg.key], room_c, snapshot.outdoor_temp.value_or_none, cfg.key in wanting
                )
            for index, valve in enumerate(cfg.valve_entities):
                if (driver := valve_driver(self.hass, valve)) is not None:
                    # Better Thermostat switched on again after the form: two controllers at one valve (review 2026-11-15)
                    plans[valve] = valve_plan.ValvePlan(valve, "driven_elsewhere", f"driven_by:{driver}")
                    continue
                view = replace(valve_view(self.hass, valve), force=cfg.valve_force)
                mem = corrected.get(valve) or self.valve_ctrl_memory.get(valve, valve_plan.ValveMemory())
                if index and cfg.key not in windows_open and setpoint is not None:
                    # not while the window is open, nor for a room left alone (review 2026-11-15)
                    step, mem = self._exercise_step(valve, view, mem, snapshot.rooms.get(cfg.key), snapshot.now)
                elif index:
                    step = None
                else:
                    step = self._valve_steps.get(valve)
                plans[valve], self._valve_memory_next[valve] = valve_plan.plan(
                    view, mem, room_c, fresh, now_ts, setpoint if index else None,
                    target=targets.get(cfg.key) if index else None, opening=opening, exercise=step,
                    setpoint_limits=view.setpoint_limits, window_off=cfg.key in windows_off,
                    room_sensor=bool(cfg.temperature_sensor), window_open=cfg.key in windows_open, join_deficit_k=lacks,
                    flow_share=share,
                )
        return plans

    def _opening_along(self, room: str) -> float:
        """How far the valves of a room heating along open (valve_plan.join_opening), 100 where nothing limits them."""
        cfg = next((r for r in self.builder.rooms if r.key == room), None)
        limits = [
            mem.opening_pct for v in (cfg.valve_entities if cfg is not None and cfg.direct else ())
            if (mem := self._valve_memory_next.get(v)) is not None and mem.opening_ts is not None
            and mem.opening_pct is not None
        ]
        return min(limits) if limits else 100.0

    def _valve_openings(self) -> dict[str, float]:
        """How far each room's valves with an opening limit (Sonoff TRVZB/TRVZBT) may open, as they show it: the valves
        driven directly, those behind a Better or Versatile Thermostat, or the room's thermostat itself; the least of
        several. Rooms without such a valve are left out."""
        out: dict[str, float] = {}
        for cfg in self.builder.rooms:
            if cfg.direct:
                valves: tuple[str, ...] = tuple(cfg.valve_entities)
            elif cfg.climate_entity and self.builder.wraps_thermostats(cfg.climate_entity):
                valves = tuple(self.builder.valves_behind(cfg.climate_entity))
            else:
                valves = (cfg.climate_entity,) if cfg.climate_entity else ()
            shown = [
                view.opening_shown for v in valves if v.startswith("climate.")
                and (view := valve_view(self.hass, v)).caps.opening and view.quirks.opening_is_limit
                and view.opening_shown is not None
            ]
            if shown:
                out[cfg.key] = min(shown)
        return out

    def disabled_valve_entities(self, room: RoomConfig) -> tuple[str, ...]:
        """The sensor selections and inputs for the room temperature of the room's valves driven directly that are
        switched off in Home Assistant (issues.py; seen 2026-11-15)."""
        if not room.direct:
            return ()
        wanted = ("sensor_select", "sensor_switch", "external_temperature")
        return tuple(
            entity for valve in room.valve_entities
            for role, entity in valve_view(self.hass, valve).caps.disabled_roles if role in wanted
        )

    def coarse_valves(self, room: RoomConfig) -> tuple[tuple[str, float], ...]:
        """The valves behind the room's Better or Versatile Thermostat that open only more than 0.5 K below their
        setpoint, with that threshold (issues.py; seen 2026-11-15). Valves Thriftherm drives itself allow for it."""
        if room.direct or not room.climate_entity or not self.builder.wraps_thermostats(room.climate_entity):
            return ()
        found = []
        for valve in self.builder.valves_behind(room.climate_entity):
            if valve.startswith("climate.") and valve != room.climate_entity:
                k = valve_view(self.hass, valve).hysteresis_k
                if k > COARSE_THRESHOLD_K:
                    found.append((valve, k))
        return tuple(found)

    def _valve_exercise(self, cfg: RoomConfig, state: RoomState, now: datetime) -> str | None:
        """The step of the valve exercise for the room's first valve (engines/valve_plan.py `exercise`), None if none."""
        valve = cfg.valve_entities[0]
        if valve_driver(self.hass, valve) is not None:
            return None
        mem = self._valve_corrected.get(valve) or self.valve_ctrl_memory.get(valve, valve_plan.ValveMemory())
        step, self._valve_corrected[valve] = self._exercise_step(valve, valve_view(self.hass, valve), mem, state, now)
        self._valve_steps[valve] = step
        return step

    def _while_exercised(self, state: RoomState) -> RoomState:
        """A room driven directly whose valve is in its exercise: it cannot be open for the room (review item 4)."""
        cfg = state.config
        if not cfg.direct or state.valve_unable is not None:
            return state
        moving = any(self.valve_ctrl_memory.get(v, valve_plan.ValveMemory()).exercise_step for v in cfg.valve_entities)
        return replace(state, valve_unable="exercise") if moving else state

    def _exercise_step(
        self, valve: str, view: valve_plan.ValveView, mem: valve_plan.ValveMemory, state: RoomState | None,
        now: datetime,
    ) -> tuple[str | None, valve_plan.ValveMemory]:
        """One step of a valve's exercise. A new one begins (review 2026-11-15) in the day, not during an override or a
        quick heat-up, not while the valve's writes wait for a retry, and for one valve a cycle: rooms due together
        move one after the other."""
        now_ts = now.timestamp()
        quiet = state is not None and state.override is None and (state.boost_until_ts or 0.0) <= now_ts
        quiet = quiet and state is not None and state.config.key not in self._no_exercise
        retry = self._valve_retry.get(valve, (0, 0.0))[1] > now_ts
        may_start = _exercise_hours(now) and quiet and not retry and not self._exercise_begun
        step, after = valve_plan.exercise(view, mem, now_ts, may_start)
        if after.exercise_step == 1 and mem.exercise_step == 0:
            self._exercise_begun = True
        return step, after

    def _valve_correction(self, cfg: RoomConfig, target: float, now: datetime) -> float:
        """Way c for the room's first valve (engines/valve_plan.py `setpoint_correction`), its memory held for the plan."""
        valve = cfg.valve_entities[0]
        if valve_driver(self.hass, valve) is not None:
            return 0.0
        room_c, fresh = self.builder.valve_room_reading(cfg.temperature_sensor, now)
        mem = self.valve_ctrl_memory.get(valve, valve_plan.ValveMemory())
        correction, self._valve_corrected[valve] = valve_plan.setpoint_correction(
            replace(valve_view(self.hass, valve), force=cfg.valve_force), mem, room_c, fresh, target, now.timestamp()
        )
        return correction

    async def _async_send_valves(self, plans: Mapping[str, valve_plan.ValvePlan], cycle_ts: float) -> None:
        """Carry out the valve plans; what was sent goes into the memory, what was not stays planned anew.

        A write the valve does not show is planned again next cycle. A call over MQTT never fails, so the backoff below
        never held: a valve fallen back on its own sensor or out of reach got the same value every minute (final review
        2026-11-15 #2). The same value goes out once more at once, then after 5, 10, 20 ... minutes, an hour at most;
        once the plan no longer asks for it, the next asking starts afresh."""
        wanted = {(w.entity_id, str(w.value)) for plan in plans.values() for w in plan.writes}
        self._valve_unanswered = {k: v for k, v in self._valve_unanswered.items() if k in wanted}
        for valve, plan in plans.items():
            nxt = self._valve_memory_next.get(valve)
            if nxt is None:
                continue
            failures, retry_ts = self._valve_retry.get(valve, (0, 0.0))
            waiting = bool(plan.writes) and cycle_ts < retry_ts
            sent = not waiting
            went: list[valve_plan.Write] = []
            for write in () if waiting else plan.writes:
                # a temperature goes out in °C only (see _writes_held), the selection in any unit
                temperature = write.value if write.domain in ("number", "climate") else None
                asked = self._asked_meanwhile(write.entity_id, cycle_ts, setpoint_only=write.domain == "climate")
                if self._writes_held(temperature) or asked:
                    sent = False
                    break
                key = (write.entity_id, str(write.value))
                times, last_ts = self._valve_unanswered.get(key, (0, 0.0))
                if times and cycle_ts - last_ts < valve_plan.unanswered_wait_s(times):
                    sent = False
                    break
                self._valve_unanswered[key] = (times + 1, cycle_ts)
                if not await executors.async_write_valve(self.hass, valve, write, self._own_context()):
                    # stop at the first failure: the selection after a value that did not arrive put a valve on 0 °C
                    # (review 2026-11-15)
                    wait = min(60.0 * 2 ** min(failures + 1, 6), 3600.0)
                    self._valve_retry[valve] = (failures + 1, cycle_ts + wait)
                    sent = False
                    break
                went.append(write)
            else:
                if not waiting:
                    self._valve_retry.pop(valve, None)
            if not sent:
                nxt = _memory_after_part(self.valve_ctrl_memory.get(valve, valve_plan.ValveMemory()), nxt, went)
            if nxt != self.valve_ctrl_memory.get(valve):
                self._store_dirty = True
            self.valve_ctrl_memory[valve] = nxt

    def _room_memory(self, key: str, thermostat: str | None) -> RoomCtrlMemory:
        """What was sent to the room's thermostat; nothing where it was another one (review 2026-11-13: the memory stayed
        when the room got another thermostat in the form, and its value was weighed against the new one's)."""
        mem = self.room_ctrl_memory.get(key)
        if mem is None or (mem.thermostat is not None and mem.thermostat != thermostat):
            self.room_ctrl_memory.pop(key, None)
            return RoomCtrlMemory()
        return mem

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
                    # the room's wish: without the lead the thermostat had from us
                    value = room_control.without_extra(value, self.room_ctrl_memory.get(key, RoomCtrlMemory()))
                    minutes = self._override_default_min
                    override = Override(value, now_ts + minutes * 60.0, now_ts, hand.DEVICE)
                    self.overrides[key] = override
                    changed[key] = replace(state, override=override)
                    self._store_dirty = True
                    _LOGGER.info(
                        "thriftherm room %s: knob turned by %+.1f K -> override %.1f °C for %d min", key, turn[1], value, minutes
                    )
                    continue
            cfg = next((r for r in self.builder.rooms if r.key == key), None)
            if key not in self.room_ctrl_memory or cfg is None:
                continue
            mem = self._room_memory(key, cfg.climate_entity)
            if mem.last_sent_target is None:
                continue
            # Switched off (by us for a window, or by a person) a valve shows its own frost setpoint: no wish for the
            # room. A TRVZB reports it as a new state of its own, with no context of ours (27.11.2026: the window's off
            # read as a turn to 5 °C, the room held 7 °C for two hours after the window). A mode alone is no hand either.
            if state.trv_hvac_mode == "off":
                continue
            # The thermostat's answer to our own setpoint is nobody's hand, whatever value it shows.
            # A late echo of its valve is no answer: our setpoint is what makes Better Thermostat
            # rewrite the valve, so a stale reply it adopts within seconds still carries our context.
            answer = room_control.own_answer(state.trv_target_temp, mem, state.thermostat_change)
            if answer and not self._is_echo(key, state.trv_target_temp, now_ts):
                if mem.answered_with != state.trv_target_temp:
                    self.room_ctrl_memory[key] = replace(mem, answered_with=state.trv_target_temp, confirmed=True)
                    self._store_dirty = True
                continue
            # Asked for in Home Assistant by a person or an automation: a change by hand for certain.
            # Anything else is one unless the thermostat explains it itself (a rule of its own, an echo).
            asked = hand.deliberate_since(state.thermostat_change, mem.last_sent_ts, setpoint_only=True)
            if asked is None and state.thermostat_own_target:
                continue
            # A setpoint nobody was seen changing is no hand. Better Thermostat, restarted before it was back, returns
            # with its valves' setpoints (2026-11-02), and a change of mode alone says nothing about the setpoint.
            if asked is None and (state.thermostat_change is None or not state.thermostat_change.setpoint):
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
            # A turn of the knob at a thermostat that had the lead from us is a wish for the room: 22 turned to from
            # 21.5 is 21.5 for the room, and its thermostat shows 22 for as long as the boiler goes on running for it.
            # A value a person or an automation asks for is meant as it is (review 2026-11-13).
            wish = value if asked is not None else room_control.without_extra(value, mem)
            override = Override(wish, now_ts + minutes * 60.0, now_ts, asked or hand.DEVICE)
            self.overrides[key] = override
            # the extra of a turn holds the correction of way c as it did (review 2026-11-15): lost, it read as a lead
            self.room_ctrl_memory[key] = RoomCtrlMemory(
                last_sent_target=value, last_sent_ts=now_ts, confirmed=True, sent_extra_k=round(value - wish, 2),
                thermostat=mem.thermostat, sent_correction_k=mem.sent_correction_k if asked is None else 0.0,
            )
            changed[key] = replace(state, override=override)
            self._store_dirty = True
            _LOGGER.info(
                "thriftherm room %s: thermostat set to %.1f °C by hand (%s) -> override for %d min",
                key, value, override.by, minutes,
            )
        if not changed:
            return snapshot
        return replace(snapshot, rooms={**snapshot.rooms, **changed})
