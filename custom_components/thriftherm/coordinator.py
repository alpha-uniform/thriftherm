"""Coordinator: one snapshot per cycle, all engines, one result set."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import boiler_profiles, executors, issues, learning_reset, persistence
from .adapters.config import system_type_from_config
from .adapters.inputs import SnapshotBuilder
from .const import (
    CONF_BOILER_ALLOW_ACTIVE,
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_PROFILE,
    CONF_BOOST_DURATION_MIN,
    CONF_HEAT_PUMP_ALLOW_ACTIVE,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_ROOM,
    CONF_OVERRIDE_DEFAULT_MIN,
    CONF_ROOM_ALLOW_ACTIVE,
    CTRL_ACTIVE,
    CTRL_OFF,
    CTRL_SHADOW,
    DEFAULT_BOILER_ALLOW_ACTIVE,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DEFAULT_BOOST_DURATION_MIN,
    DEFAULT_HEAT_PUMP_ALLOW_ACTIVE,
    DEFAULT_OVERRIDE_DEFAULT_MIN,
    DEFAULT_ROOM_ALLOW_ACTIVE,
    DOMAIN,
    MODES,
    MODE_AUTO,
    MODE_AWAY,
    MODE_BOILER_ONLY,
    MODE_OFF,
    SAFETY_OK,
    SOURCE_NONE,
    STORAGE_KEY,
    STORAGE_VERSION,
    SYSTEM_GAS,
    SYSTEM_NONE,
    UPDATE_INTERVAL_S,
)
from .engines import (
    advisor,
    boiler as boiler_engine,
    boiler_control,
    economics,
    heat_pump as heat_pump_engine,
    heat_pump_control,
    room as room_engine,
    room_control,
    safety,
)
from .engines.heat_pump_tracking import HeatPumpTracker
from .engines.learning import CoolRateStats, CopMap, HeatRateStats
from .engines.boiler_control import BoilerInputs, BoilerMemory
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
)
from .plan_log import PlanLog

_LOGGER = logging.getLogger(__name__)

EBUS_PRIORITY_REFRESH_S = 600.0  # a restart of ebusd forgets the priority; renew it so a gap lasts minutes, not an hour
EBUS_READ_WHILE_BURNING_S = 60.0  # fresh values once a minute while gas is burning
OUTDOOR_FALLBACK_MAX_AGE_S = 6 * 3600.0  # how long the last known outdoor temperature stands in
# After a restart the Zigbee room sensors come back one by one (measured 2026-09-18: the bathroom
# after 38 s). Until they have, the boiler gets no command instead of a block decided on gaps.
ROOM_DATA_GRACE_S = 300.0


STORE_SAVE_INTERVAL_S = 600.0
SETPOINT_SETTLE_S = 1200.0  # after a setpoint change the rooms and Better Thermostat settle first

type ThrifthermConfigEntry = ConfigEntry["ThrifthermCoordinator"]


def _modes(allow_active: bool) -> list[str]:
    """Control modes on offer: "active" only once released in the options."""
    return [CTRL_OFF, CTRL_SHADOW, CTRL_ACTIVE] if allow_active else [CTRL_OFF, CTRL_SHADOW]


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
        self._last_drying: dict[str, bool] = {}
        # room engine state
        self.overrides: dict[str, Override] = {}
        self.drying_states: dict[str, DryingState] = {}
        self.away_return_ts: float | None = None
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
        self.last_learning_reset: dict[str, Any] | None = None
        # room control via Better Thermostat
        self.room_control_mode: str = CTRL_SHADOW
        self.room_ctrl_memory: dict[str, RoomCtrlMemory] = {}
        self._preheat_latch: dict[str, float] = {}
        self.expected_unique_ids: set[str] = set()  # filled by the entities; the rest is stale
        self.issue_since: dict[str, float] = {}
        self._ebus_priority_ts: float | None = None
        self._ebus_signal_lost_since: float | None = None
        self._last_outdoor: tuple[float, float] | None = None  # (°C, timestamp) for sensor outages
        self._ebus_read_ts = 0.0
        self._started_ts = time.time()
        self._frost_rooms: tuple[str, ...] = ()
        self._last_room_plans: dict[str, tuple[str, float | None]] = {}
        self._last_targets: dict[str, float] = {}
        self._last_target_change_ts: float = 0.0
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
        """True only for an own boiler we can read and address over ebusd.

        District heating and heat-pump-only installations have no boiler of
        their own, so no SetMode command may ever be built or sent for them.
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
    def boiler_setmode_topic(self) -> str:
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
        # Attribute-only updates (e.g. the Midea climate refreshing every second) must not
        # trigger a cycle; only a changed state (window opened, hvac mode switched) does.
        if old is not None and new is not None and old.state == new.state:
            return
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ user actions
    async def _async_changed(self) -> None:
        """A user action changed stored state: keep it and show its effect at once."""
        self._store_dirty = True
        await self.async_refresh()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        if mode != self.mode:
            _LOGGER.info("thriftherm: operating mode changed %s -> %s (observation only)", self.mode, mode)
            self.mode = mode
            if mode != MODE_AWAY:
                self.away_return_ts = None
            self._store_dirty = True

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
        if other is not None and ((kind == "setback" and value > other) or (kind == "comfort" and value < other)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="setback_above_comfort", translation_placeholders={"room": room}
            )
        self.room_temps.setdefault(room, {})[kind] = float(value)
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
        self.overrides[room] = Override(target=float(temperature), until_ts=now + minutes * 60.0, set_at_ts=now)
        _LOGGER.info("thriftherm override: %s -> %.1f °C for %d min", room, temperature, minutes)
        await self._async_changed()

    async def async_clear_override(self, room: str | None) -> None:
        if room is None:
            self.overrides.clear()
            self.boosts.clear()
        else:
            self.overrides.pop(room, None)
            self.boosts.pop(room, None)
        _LOGGER.info("thriftherm override cleared: %s", room or "all rooms")
        await self._async_changed()

    async def async_set_away(self, return_ts: float | None, clear_return: bool = False) -> None:  # noqa: D417
        """Switch to away mode. Without a return time a planned one is kept.

        The dashboard button calls this with no arguments; discarding a return
        time that was set for a trip would silently drop the pre-heating for it.
        """
        if return_ts is not None and return_ts <= time.time():
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="return_time_in_past")
        self.mode = MODE_AWAY
        if return_ts is not None:
            self.away_return_ts = return_ts
        elif clear_return or (self.away_return_ts is not None and self.away_return_ts <= time.time()):
            self.away_return_ts = None
        _LOGGER.info("thriftherm away mode set, return=%s", self.away_return_ts)
        await self._async_changed()

    async def async_clear_away(self) -> None:
        if self.mode == MODE_AWAY:
            self.mode = MODE_AUTO
        self.away_return_ts = None
        _LOGGER.info("thriftherm away mode cleared")
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
            self.boiler_memory = replace(
                self.boiler_memory, offset_k=0.0, adjustments=(), last_adjust_reason=None, spread_ema=None, samples=0, last_review_ts=None
            )
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
                self._store_dirty = True
            snapshot = self.builder.build(
                self.mode,
                overrides=self.overrides,
                drying_states=self.drying_states,
                heat_rates={r.key: v for r in self.builder.rooms if (v := self.heat_rates.rate(r.key)) is not None},
                away_return_ts=self.away_return_ts,
                boosts=self.boosts,
                room_temps=self.room_temps,
            )
            result = self._evaluate(snapshot)
        except Exception as err:  # noqa: BLE001 - surface to HA as UpdateFailed
            raise UpdateFailed(f"evaluation failed: {err}") from err
        command: HeatPumpCommand = result["midea_command"]
        heat_pump_entity = self.config.get(CONF_HEAT_PUMP_CLIMATE)
        if self.control_mode == CTRL_ACTIVE and self.has_heat_pump and heat_pump_entity and command.action != "none":
            await executors.async_execute_heat_pump(self.hass, heat_pump_entity, command)
        boiler_cmd: BoilerCommand = result["boiler_command"]
        if self.has_boiler and self.boiler_control_mode == CTRL_ACTIVE and boiler_cmd.send and boiler_cmd.payload:
            await executors.async_send_setmode(self.hass, self.boiler_setmode_topic, boiler_cmd.payload)
        if self.has_boiler:
            await self._async_keep_boiler_readings_fresh(result["boiler"].gas_power_w, now)
        if self.room_control_mode == CTRL_ACTIVE:
            climate_of = {r.key: r.climate_entity for r in self.builder.rooms}
            for room_cmd in result["room_commands"].values():
                entity = climate_of.get(room_cmd.room)
                if room_cmd.send and room_cmd.target is not None and entity:
                    await executors.async_set_thermostat(self.hass, entity, room_cmd)
        await self.async_save_store()
        await issues.async_sync(self.hass, self)
        return result

    async def _async_keep_boiler_readings_fresh(self, gas_power_w: float | None, now: float) -> None:
        """Keep flow and return current: high poll priority always, direct reads while gas burns."""
        topics = [self._ebus_topic(name, "get") for name in self.boiler_profile.fast_messages]
        if self._ebus_priority_ts is None or now - self._ebus_priority_ts >= EBUS_PRIORITY_REFRESH_S:
            if all([await executors.async_request_ebus_read(self.hass, topic, "?1") for topic in topics]):
                self._ebus_priority_ts = now
        burning = gas_power_w is not None and gas_power_w > boiler_control.HOT_WATER_GAS_W
        if burning and now - self._ebus_read_ts >= EBUS_READ_WHILE_BURNING_S:
            for topic in topics:
                await executors.async_request_ebus_read(self.hass, topic)
            self._ebus_read_ts = now

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
            and self.control_mode != CTRL_OFF
            and outdoor_for_limit is not None
            and outdoor_for_limit >= params.heat_pump_min_outdoor_temp
        )
        rooms: dict[str, RoomResult] = {
            key: room_engine.evaluate_room(
                state, snapshot.now, snapshot.mode, params, snapshot.away_return_ts, heat_pump_heat_possible=heat_pump_heat_possible
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
            estimate, estimate_basis = self.cop_map.estimate(outdoor)
        else:
            estimate, estimate_basis = None, None
        decision_cop = cop_res.value if cop_res.value is not None else estimate
        decision_basis = "measured" if cop_res.value is not None else estimate_basis
        econ: EconomicsResult = economics.evaluate(
            snapshot.prices, decision_cop, params.break_even_margin_on, params.break_even_margin_off, self._last_cheaper
        )
        if econ.cheaper_source != "unknown":
            self._last_cheaper = econ.cheaper_source

        self.heat_pump_tracker.track_efficiency(cop_res.value, econ.break_even_cop, params.break_even_margin_off, now_ts)

        # --- learning: COP map ---------------------------------------------------
        if outdoor is not None and self.heat_pump_tracker.map_entry_due(cop_res, now_ts):
            self.cop_map.add(outdoor, cop_res.value)
            self._store_dirty = True

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
        )

        # --- midea control (phase 7): shadow or active --------------------------------------
        heat_pump_room_keys = [r.key for r in self.builder.rooms if r.served_by_heat_pump]
        heat_pump_mode = self.control_mode if self.has_heat_pump else CTRL_OFF
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
            ),
            self.control_memory,
        )
        if self.plan_log.heat_pump(command, now_ts, self.control_mode == CTRL_ACTIVE):
            self._store_dirty = True

        # --- learning guard: never learn while setpoints move or special modes run ---------------
        for key, res in rooms.items():
            if self._last_targets.get(key) != res.target:
                self._last_targets[key] = res.target
                self._last_target_change_ts = now_ts
        learning_blocked: list[str] = []
        if safety_res.state != SAFETY_OK:
            learning_blocked.append(f"safety_{safety_res.state}")
        if now_ts - self._last_target_change_ts < SETPOINT_SETTLE_S:
            learning_blocked.append("setpoint_recently_changed")
        if any(r.boost_active for r in rooms.values()):
            learning_blocked.append("quick_heat_up")
        if any(r.drying.active for r in rooms.values()):
            learning_blocked.append("drying_mode")
        if self.boiler_control_mode != CTRL_ACTIVE:
            # while only planning, the front knob sets the flow: the offset changes nothing
            learning_blocked.append("boiler_control_not_active")
        seen = self.boiler_memory.hot_water_seen_ts
        if seen is not None and now_ts - seen < boiler_control.HOT_WATER_HOLDOFF_S:
            learning_blocked.append("hot_water_recent")

        # --- boiler control: plan only or active ----------------------------------------------
        boiler_cmd, self.boiler_memory = boiler_control.decide(
            BoilerInputs(
                now_ts=now_ts,
                control_mode=self.boiler_control_mode,
                op_mode=snapshot.mode,
                safety_state=safety_res.state,
                boiler_available=boiler_res.available,
                room_data_pending=now_ts - self._started_ts < ROOM_DATA_GRACE_S
                and any(r.temperature is None for r in rooms.values()),
                rooms=tuple(rooms.values()),
                frost_rooms=safety_res.frost_rooms,
                advice_source=advisor.boiler_source(
                    advice.source,
                    command.plan,
                    heat_pump_mode,
                    self.boiler_control_mode,
                ),
                outdoor_c=outdoor,
                params=params,
                flow_c=snapshot.boiler.flow_temp.value_or_none,
                return_c=snapshot.boiler.return_temp.value_or_none,
                gas_power_w=boiler_res.gas_power_w,
                # The pump state alone misses a cycling boiler: measured 2026-09-17, the burner
                # ran for a minute every 15 and ebusd polled the pump state in between, so the
                # learning never saw a heating run. Burning gas is the faster evidence.
                burner_heating=(
                    snapshot.boiler.pump_state == "on"
                    or (boiler_res.gas_power_w or 0.0) > boiler_control.HOT_WATER_GAS_W
                )
                and not bool(boiler_res.hwc_active),
                learning_allowed=not learning_blocked,
            ),
            self.boiler_memory,
        )
        self.plan_log.boiler(boiler_cmd, now_ts, self.boiler_control_mode == CTRL_ACTIVE)

        # --- room control via Better Thermostat ------------------------------------------------
        room_commands: dict[str, RoomCommand] = {}
        for cfg in self.builder.rooms:
            state = snapshot.rooms.get(cfg.key)
            res = rooms.get(cfg.key)
            if state is None or res is None:
                continue
            room_cmd, self.room_ctrl_memory[cfg.key] = room_control.decide(
                cfg.key,
                self.room_control_mode,
                res,
                bool(cfg.climate_entity),
                state.trv_hvac_mode,
                state.trv_target_temp,
                now_ts,
                self.room_ctrl_memory.get(cfg.key, RoomCtrlMemory()),
            )
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
            learned = self.heat_rates.observe(key, now_ts, res.temperature, heating, bool(res.window_open), outdoor)
            if learned is not None:
                _LOGGER.info("thriftherm learned heat-up rate %s: %.2f K/h", key, learned)
                self._store_dirty = True
            # how fast the room loses heat: the basis for a setback that really saves
            cooled = self.cool_rates.observe(key, now_ts, res.temperature, outdoor, heating, bool(res.window_open))
            if cooled is not None:
                _LOGGER.info("thriftherm learned cool-down coefficient %s: %.4f per h", key, cooled)
                self._store_dirty = True

        self.plan_log.changes(advice, safety_res, defrost_res)

        return {
            "snapshot": snapshot,
            "rooms": rooms,
            "safety": safety_res,
            "boiler": boiler_res,
            "midea": heat_pump_res,
            "cop": cop_res,
            "econ": econ,
            "defrost": defrost_res,
            "advice": advice,
            "cop_map": self.cop_map.to_dict(),
            "expected_cop": estimate,
            "duct_factor": params.duct_factor,
            "midea_rooms": heat_pump_room_keys,
            "midea_room": self.config.get(CONF_HEAT_PUMP_ROOM),
            "overrides": {k: {"target": o.target, "until_ts": o.until_ts} for k, o in self.overrides.items()},
            "away_return_ts": self.away_return_ts,
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
            "eco_offset_k": self.control_memory.offset_k if self.control_memory.offset_k is not None else params.heat_pump_setpoint_offset_k,
            "command_log": list(self.plan_log.command_log),
            "boosts": dict(self.boosts),
            "boiler_command": boiler_cmd,
            "boiler_control_mode": self.boiler_control_mode,
            "boiler_log": list(self.plan_log.boiler_log),
            "boiler_setmode_topic": self.boiler_setmode_topic,
            "learning_blocked_by": learning_blocked,
            "learning_reset": self.last_learning_reset,
            "language": self.hass.config.language,
            "room_names": {r.key: r.name for r in self.builder.rooms},
            "room_commands": room_commands,
            "room_control_mode": self.room_control_mode,
        }

    def _adopt_manual_thermostat_changes(self, snapshot: HeatingSnapshot, now_ts: float) -> HeatingSnapshot:
        """Turn a manual change on a Better Thermostat into an override (active room control only)."""
        if self.room_control_mode != CTRL_ACTIVE:
            return snapshot
        changed = {}
        for key, state in snapshot.rooms.items():
            mem = self.room_ctrl_memory.get(key)
            if mem is None:
                continue
            value = room_control.manual_change(self.room_control_mode, state.trv_target_temp, mem)
            if value is None:
                continue
            minutes = self._override_default_min
            override = Override(value, now_ts + minutes * 60.0, now_ts)
            self.overrides[key] = override
            self.room_ctrl_memory[key] = RoomCtrlMemory(last_sent_target=value, last_sent_ts=now_ts, confirmed=True)
            changed[key] = replace(state, override=override)
            self._store_dirty = True
            _LOGGER.info("thriftherm room %s: manual thermostat change to %.1f °C -> override for %d min", key, value, minutes)
        if not changed:
            return snapshot
        return replace(snapshot, rooms={**snapshot.rooms, **changed})
