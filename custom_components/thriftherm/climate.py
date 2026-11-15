"""A room thermostat of Thriftherm's own for every room the heat pump serves (docs/PHASE12_COOLING.md §3.6, variant A4).

Made for Apple Home and Siri through the HomeKit Bridge: one thermostat per room that heats in the heating season and
cools in the cooling season. Its setpoint is the target of the season; a setpoint set on it becomes an override, of the
heating target or of the cooling target. A cooling target never reaches Better Thermostat: there a setpoint is a heating
command, and a radiator would heat against the air conditioner.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HEAT_PUMP_PLAN_COOL
from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .engines.heat_pump_cooling import COOL_SETPOINT_MIN
from .engines.room_cooling import NO_COOLING_C
from .entity import ThrifthermEntity

# "Off" on the room thermostat: the room rests for the time of an override, heated to the frost limit only, or not
# cooled (the cooling target that means "do not cool").
COOL_OFF_TARGET = NO_COOLING_C
HEAT_RANGE = (5.0, 30.0)
COOL_RANGE = (COOL_SETPOINT_MIN, COOL_OFF_TARGET)


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if not coordinator.cooling_allowed:
        return
    async_add_entities(RoomClimate(coordinator, room.key) for room in coordinator.builder.rooms if room.served_by_heat_pump)


class RoomClimate(ThrifthermEntity, ClimateEntity):
    """Heat or cool, by the season; the setpoint is the room's target, a change of it an override."""

    _attr_translation_key = "room_climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: ThrifthermCoordinator, room_key: str) -> None:
        super().__init__(coordinator, "room_climate", room_key)
        self.suggest_english_entity_id()

    @property
    def available(self) -> bool:
        return self.room is not None

    @property
    def _cooling(self) -> bool:
        return self.coordinator.cooling_season

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [HVACMode.COOL, HVACMode.OFF] if self._cooling else [HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode:
        if self._cooling:
            # No cooling target (switched off here, 32 °C outside its schedule or away, the operating mode): off.
            # Shown as "cool" without a target, Apple Home kept the last one, "cooling to 25°" (review 2026-11-13).
            room = self.room
            held = self.coordinator.cool_overrides.get(self._room_key)
            off = (held is not None and held.target >= COOL_OFF_TARGET) or (room is not None and room.cool_target is None)
            return HVACMode.OFF if off else HVACMode.COOL
        held = self.coordinator.overrides.get(self._room_key)
        frost = self.coordinator.builder.params.frost_temp
        return HVACMode.OFF if held is not None and held.target <= frost else HVACMode.HEAT

    @property
    def min_temp(self) -> float:
        # in the cooling season the cooling floor: a setpoint below it would only be raised to it
        return max(COOL_RANGE[0], self.coordinator.cool_floor) if self._cooling else HEAT_RANGE[0]

    @property
    def max_temp(self) -> float:
        return (COOL_RANGE if self._cooling else HEAT_RANGE)[1]

    @property
    def current_temperature(self) -> float | None:
        room = self.room
        return None if room is None else room.temperature

    @property
    def target_temperature(self) -> float | None:
        room = self.room
        if room is None:
            return None
        return room.cool_target if self._cooling else room.target

    @property
    def hvac_action(self) -> HVACAction | None:
        room = self.room
        if room is None:
            return None
        if self._cooling:
            command = (self.coordinator.data or {}).get("midea_command")
            runs = command is not None and command.plan == HEAT_PUMP_PLAN_COOL and not self.coordinator.control_memory.resting
            return HVACAction.COOLING if runs and room.cooling_allowed else HVACAction.IDLE
        return HVACAction.HEATING if room.thermostat_heating or (room.demand or 0.0) > 0.0 else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self.room
        return {"reason": None if room is None else (room.cool_target_reason if self._cooling else room.target_reason)}

    async def async_set_temperature(self, **kwargs: Any) -> None:
        value = kwargs.get(ATTR_TEMPERATURE)
        if value is None:
            return
        if self._cooling:
            await self.coordinator.async_set_cool_override(self._room_key, float(value), None)
        else:
            await self.coordinator.async_set_override(self._room_key, float(value), None)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="season_mode_not_offered")
        if hvac_mode == HVACMode.OFF:
            if self._cooling:
                await self.coordinator.async_set_cool_override(self._room_key, COOL_OFF_TARGET, None)
            else:
                await self.coordinator.async_set_override(self._room_key, self.coordinator.builder.params.frost_temp, None)
            return
        # back on: the room follows its plan again, as with "back to automatic" for this room
        overrides = self.coordinator.cool_overrides if self._cooling else self.coordinator.overrides
        if self._room_key in overrides:
            await self.coordinator.async_clear_override(self._room_key)
