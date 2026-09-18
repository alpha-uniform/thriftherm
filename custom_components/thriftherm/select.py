"""Operating mode and the three control-mode selects (off, plan only, active)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODES
from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .entity import ThrifthermEntity


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [ModeSelect(coordinator), ControlSelect(coordinator, "room_control", "mdi:thermostat", "room_")]
    if coordinator.has_boiler:
        entities.append(ControlSelect(coordinator, "boiler_control", "mdi:water-boiler", "boiler_"))
    if coordinator.has_heat_pump:
        entities.append(ControlSelect(coordinator, "midea_control", "mdi:heat-pump-outline", ""))
    async_add_entities(entities)


class ModeSelect(ThrifthermEntity, SelectEntity):
    """Operating mode. Persisted by the coordinator's store, which is loaded before entities exist."""

    _attr_translation_key = "mode"
    _attr_icon = "mdi:tune"
    _attr_options = MODES

    def __init__(self, coordinator: ThrifthermCoordinator) -> None:
        super().__init__(coordinator, "mode")
        self.suggest_english_entity_id()

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    @property
    def extra_state_attributes(self) -> dict:
        return {"away_return_ts": self.coordinator.away_return_ts, "heat_rates": self.coordinator.heat_rates.to_dict()}

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_mode(option)
        self.async_write_ha_state()
        # immediate (non-debounced) refresh so the new mode is reflected at once
        await self.coordinator.async_refresh()


class ControlSelect(ThrifthermEntity, SelectEntity):
    """Off / plan only (shadow) / active. "Active" appears only when released in the options.

    The heat pump, the boiler and the room thermostats each have one; they differ in
    which coordinator attributes they use: prefix "" for the heat pump, "boiler_", "room_".
    """

    def __init__(self, coordinator: ThrifthermCoordinator, key: str, icon: str, prefix: str) -> None:
        self._attr_translation_key = key
        self._attr_icon = icon
        self._prefix = prefix
        super().__init__(coordinator, key)
        self.suggest_english_entity_id()

    @property
    def options(self) -> list[str]:
        return getattr(self.coordinator, f"{self._prefix}control_modes")

    @property
    def current_option(self) -> str:
        return getattr(self.coordinator, f"{self._prefix}control_mode")

    @property
    def extra_state_attributes(self) -> dict:
        return {"active_control_released": getattr(self.coordinator, f"{self._prefix}allow_active")}

    async def async_select_option(self, option: str) -> None:
        getattr(self.coordinator, f"set_{self._prefix}control_mode")(option)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
