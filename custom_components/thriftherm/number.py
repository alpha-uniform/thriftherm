"""Comfort and setback temperature per room as adjustable numbers.

Keeping them here instead of in the config flow means you can change them from
a dashboard or from your phone, without the integration reloading.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .entity import ThrifthermEntity


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for room in coordinator.builder.rooms:
        entities.append(RoomTemperatureNumber(coordinator, room.key, "comfort"))
        entities.append(RoomTemperatureNumber(coordinator, room.key, "setback"))
    async_add_entities(entities)


class RoomTemperatureNumber(ThrifthermEntity, NumberEntity):
    """Comfort or setback temperature of one room."""

    _attr_native_min_value = 10.0  # same range the room form enforces
    _attr_native_max_value = 28.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ThrifthermCoordinator, room_key: str, kind: str) -> None:
        super().__init__(coordinator, f"{kind}_temp", room_key)
        self._kind = kind
        self._attr_translation_key = f"room_{kind}_temp"
        self.suggest_english_entity_id()
        self._attr_icon = "mdi:thermometer-high" if kind == "comfort" else "mdi:thermometer-low"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.room_temperature(self._room_key, self._kind)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_room_temperature(self._room_key, self._kind, value)
