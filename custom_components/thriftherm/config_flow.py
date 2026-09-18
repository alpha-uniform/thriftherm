"""Config and options flow for Thriftherm."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from . import boiler_profiles, learning_reset, texts
from .adapters import ebusd_detect
from .adapters.config import system_type_from_config
from .const import CONFIG_VERSION, CONF_BOILER_PROFILE, CONF_ROOM_KEY, CONF_ROOMS, SYSTEM_GAS, DOMAIN

from .config_schema import (
    BOILER_KEYS,
    HEAT_PUMP_KEYS,
    OUTDOOR_KEYS,
    PARAMETER_KEYS,
    PRICE_KEYS,
    ROOM_ACTION_ADD,
    SYSTEM_KEYS,
    has_heat_pump,
    merge_present,
    merge_step,
    room_from_input,
    room_options,
    schema_boiler,
    schema_heat_pump,
    schema_outdoor,
    schema_parameters,
    schema_prices,
    schema_room,
    schema_system,
    validate_heat_pump,
    validate_room,
)


# ---------------------------------------------------------------------------
# Boiler step (shared by config and options flow)
# ---------------------------------------------------------------------------
def _boiler_form(hass: HomeAssistant, current: Mapping[str, Any]) -> tuple[dict[str, Any], vol.Schema, dict[str, str]]:
    """Search the ebusd entities; returns what was pre-filled, the form, and the sentence saying what was found."""
    detection = ebusd_detect.detect(hass)
    fill = ebusd_detect.prefill(current, detection)
    name = boiler_profiles.profile(detection.profile).name if detection.profile else None
    summary = texts.detection_summary(name, detection.circuit, detection.found, detection.total, hass.config.language)
    return fill, schema_boiler({**current, **fill}), {"detected": summary}


def _merge_boiler(target: dict[str, Any], user_input: Mapping[str, Any], fill: Mapping[str, Any]) -> None:
    """Apply the boiler step; the profile is stored only when the form took over a boiler found."""
    merge_step(target, BOILER_KEYS, user_input)
    if CONF_BOILER_PROFILE in fill:
        target[CONF_BOILER_PROFILE] = fill[CONF_BOILER_PROFILE]


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------
class ThrifthermConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = CONFIG_VERSION

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._rooms: list[dict[str, Any]] = []
        self._boiler_fill: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ThrifthermOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            merge_present(self._data, SYSTEM_KEYS, user_input)
            return await self.async_step_prices()
        return self.async_show_form(step_id="user", data_schema=schema_system(self._data))

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            merge_present(self._data, PRICE_KEYS, user_input)
            return await self.async_step_boiler()
        return self.async_show_form(step_id="prices", data_schema=schema_prices(self._data))

    async def async_step_boiler(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if system_type_from_config(self._data) != SYSTEM_GAS:
            return await self.async_step_midea()
        if user_input is not None:
            _merge_boiler(self._data, user_input, self._boiler_fill)
            return await self.async_step_midea()
        self._boiler_fill, schema, placeholders = _boiler_form(self.hass, self._data)
        return self.async_show_form(step_id="boiler", data_schema=schema, description_placeholders=placeholders)

    async def async_step_midea(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_heat_pump(user_input)
            if not errors:
                merge_step(self._data, HEAT_PUMP_KEYS, user_input)
                return await self.async_step_outdoor()
        return self.async_show_form(step_id="midea", data_schema=schema_heat_pump({**self._data, **(user_input or {})}), errors=errors)

    async def async_step_outdoor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            merge_step(self._data, OUTDOOR_KEYS, user_input)
            return await self.async_step_room()
        return self.async_show_form(step_id="outdoor", data_schema=schema_outdoor(self._data))

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_room(user_input, self._rooms)
            if not errors:
                self._rooms.append(room_from_input(user_input))
                if user_input.get("add_another"):
                    return await self.async_step_room()
                self._data[CONF_ROOMS] = self._rooms
                return self.async_create_entry(title="Thriftherm", data=self._data)
        return self.async_show_form(
            step_id="room",
            data_schema=schema_room(user_input or {}, allow_add_another=True, heat_pump=has_heat_pump(self._data)),
            errors=errors,
            description_placeholders={"count": str(len(self._rooms))},
        )


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------
class ThrifthermOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._room_key: str | None = None
        self._boiler_fill: dict[str, Any] = {}

    @property
    def _config(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        options = {**self._config, **changes}
        return self.async_create_entry(title="", data=options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["system", "prices"]
        if system_type_from_config(self._config) == SYSTEM_GAS:
            options.append("boiler")
        options += ["midea", "outdoor", "parameters", "rooms", "reset_learning"]
        return self.async_show_menu(step_id="init", menu_options=options)

    def _simple_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        schema_fn: Callable[[Mapping[str, Any]], vol.Schema],
        keys: tuple[str, ...],
        merge: Callable[[dict[str, Any], tuple[str, ...], Mapping[str, Any]], None] = merge_step,
        validate: Callable[[Mapping[str, Any]], dict[str, str]] | None = None,
    ) -> ConfigFlowResult:
        """Show one group of fields, or merge the answer into the options and save."""
        errors: dict[str, str] | None = {} if validate else None
        if user_input is not None:
            if validate:
                errors = validate(user_input)
            if not errors:
                changes: dict[str, Any] = {}
                merge(changes, keys, user_input)
                return self._save(changes)
        return self.async_show_form(step_id=step_id, data_schema=schema_fn({**self._config, **(user_input or {})}), errors=errors)

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._simple_step("system", user_input, schema_system, SYSTEM_KEYS)

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._simple_step("prices", user_input, schema_prices, PRICE_KEYS, merge=merge_present)

    async def async_step_boiler(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Like a simple step, but fields still empty are pre-filled with the ebusd entities found."""
        if user_input is not None:
            changes: dict[str, Any] = {}
            _merge_boiler(changes, user_input, self._boiler_fill)
            return self._save(changes)
        self._boiler_fill, schema, placeholders = _boiler_form(self.hass, self._config)
        return self.async_show_form(step_id="boiler", data_schema=schema, description_placeholders=placeholders)

    async def async_step_midea(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._simple_step("midea", user_input, schema_heat_pump, HEAT_PUMP_KEYS, validate=validate_heat_pump)

    async def async_step_outdoor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._simple_step("outdoor", user_input, schema_outdoor, OUTDOOR_KEYS)

    async def async_step_parameters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._simple_step("parameters", user_input, schema_parameters, PARAMETER_KEYS)

    async def async_step_reset_learning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Forget learned values after a change to the installation (nothing else is touched)."""
        config = self._config
        scopes = [
            s
            for s in learning_reset.SCOPES
            if (s != learning_reset.SCOPE_BOILER or system_type_from_config(config) == SYSTEM_GAS)
            and (s != learning_reset.SCOPE_HEAT_PUMP or has_heat_pump(config))
        ]
        rooms = room_options(config.get(CONF_ROOMS, []))
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get("scope") or []
            coordinator = getattr(self.config_entry, "runtime_data", None)
            if not chosen:
                errors["scope"] = "no_scope"
            elif coordinator is None:
                return self.async_abort(reason="not_loaded")
            else:
                await coordinator.async_reset_learning(chosen, user_input.get("rooms") or None)
                return self.async_abort(reason="learning_reset")
        schema = vol.Schema(
            {
                vol.Required("scope", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=scopes, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="learning_scope")
                ),
                vol.Optional("rooms"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=rooms, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
            }
        )
        return self.async_show_form(step_id="reset_learning", data_schema=schema, errors=errors)

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        rooms: list[dict[str, Any]] = list(self._config.get(CONF_ROOMS, []))
        if user_input is not None:
            choice = user_input["room"]
            action = user_input["action"]
            if choice == ROOM_ACTION_ADD:
                self._room_key = None
                return await self.async_step_room()
            if action == "delete":
                remaining = [r for r in rooms if r[CONF_ROOM_KEY] != choice]
                return self._save({CONF_ROOMS: remaining})
            self._room_key = choice
            return await self.async_step_room()
        options = [*room_options(rooms), selector.SelectOptionDict(value=ROOM_ACTION_ADD, label="+")]
        schema = vol.Schema(
            {
                vol.Required("room", default=ROOM_ACTION_ADD): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN, translation_key="room_choice")
                ),
                vol.Required("action", default="edit"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=["edit", "delete"], mode=selector.SelectSelectorMode.LIST, translation_key="room_action")
                ),
            }
        )
        return self.async_show_form(step_id="rooms", data_schema=schema)

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        rooms: list[dict[str, Any]] = list(self._config.get(CONF_ROOMS, []))
        current = next((r for r in rooms if r[CONF_ROOM_KEY] == self._room_key), {}) if self._room_key else {}
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_room(user_input, rooms if self._room_key is None else ())
            if not errors:
                room = room_from_input(user_input, key=self._room_key)
                if self._room_key is None:
                    rooms.append(room)
                else:
                    rooms = [room if r[CONF_ROOM_KEY] == self._room_key else r for r in rooms]
                return self._save({CONF_ROOMS: rooms})
        return self.async_show_form(
            step_id="room",
            data_schema=schema_room(user_input or current, allow_add_another=False, heat_pump=has_heat_pump(self._config)),
            errors=errors,
            description_placeholders={"count": str(len(rooms))},
        )
