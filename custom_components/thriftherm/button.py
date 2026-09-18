"""Quick heat-up button per room.

The boost raises the room to its comfort temperature ahead of schedule. That
works through Better Thermostat just as well as through the heat pump, so the
button belongs to every room we can actually influence — not only to the ones
the Midea serves.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .entity import ThrifthermEntity


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        BoostButton(coordinator, room.key)
        for room in coordinator.builder.rooms
        if room.climate_entity or (coordinator.has_heat_pump and room.served_by_heat_pump)
    )


class BoostButton(ThrifthermEntity, ButtonEntity):
    """Heat the room to its comfort temperature ahead of schedule.

    With a heat pump serving the room it also runs it at full power.
    """

    _attr_translation_key = "room_boost"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: ThrifthermCoordinator, room_key: str) -> None:
        super().__init__(coordinator, "boost", room_key)
        self.suggest_english_entity_id()

    async def async_press(self) -> None:
        await self.coordinator.async_boost(self._room_key, None)
