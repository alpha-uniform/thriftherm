"""Shared entity base classes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThrifthermCoordinator
from .entity_ids import OBJECT_IDS


_PLATFORMS = (
    (BinarySensorEntity, "binary_sensor"),
    (ButtonEntity, "button"),
    (DateTimeEntity, "datetime"),
    (NumberEntity, "number"),
    (SelectEntity, "select"),
    (SensorEntity, "sensor"),
)


def _platform_of(entity: object) -> str | None:
    for cls, domain in _PLATFORMS:
        if isinstance(entity, cls):
            return domain
    return None


class ThrifthermEntity(CoordinatorEntity[ThrifthermCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ThrifthermCoordinator, key: str, room_key: str | None = None) -> None:
        super().__init__(coordinator)
        self._entry_id = coordinator.entry.entry_id
        self._room_key = room_key
        if room_key is None:
            self._attr_unique_id = f"{self._entry_id}_{key}"
            self._room_name = None
        else:
            self._attr_unique_id = f"{self._entry_id}_{room_key}_{key}"
            self._room_name = next((r.name for r in coordinator.builder.rooms if r.key == room_key), room_key)
        coordinator.expected_unique_ids.add(self._attr_unique_id)

    def suggest_english_entity_id(self) -> None:
        """Propose a language-independent entity id.

        Only a proposal: an entity already in the registry keeps the id it has.
        Display names are untouched and still follow the user's language.
        """
        domain = _platform_of(self)
        translation_key = getattr(self, "_attr_translation_key", None)
        description = getattr(self, "entity_description", None)
        if translation_key is None and description is not None:
            translation_key = description.translation_key
        object_id = OBJECT_IDS.get(domain or "", {}).get(translation_key or "")
        if domain is None or object_id is None:
            return
        prefix = f"{DOMAIN}_{self._room_key}_" if self._room_key else f"{DOMAIN}_"
        self.entity_id = f"{domain}.{prefix}{object_id}"

    @property
    def device_info(self) -> DeviceInfo:
        if self._room_key is None:
            return DeviceInfo(
                identifiers={(DOMAIN, self._entry_id)},
                name="Thriftherm",
                manufacturer="Thriftherm",
                model="Heating control",
            )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_room_{self._room_key}")},
            name=f"Thriftherm {self._room_name}",
            manufacturer="Thriftherm",
            model="Room",
        )
        if self.hass is not None:
            # the hub device is created in async_setup_entry before any platform loads
            hub = dr.async_get(self.hass).async_get_device_by_identifier((DOMAIN, self._entry_id), self._entry_id)
            if hub is not None:
                info["via_device_id"] = hub.id
        return info

    @property
    def room(self):
        if self._room_key is None or not self.coordinator.data:
            return None
        return self.coordinator.data["rooms"].get(self._room_key)


def is_wanted(coordinator: ThrifthermCoordinator, description: Any) -> bool:
    """Leave out what needs a heat pump, an own boiler or a heat source the installation lacks."""
    return (
        (coordinator.has_heat_pump or not getattr(description, "heat_pump_addon", False))
        and (coordinator.has_boiler or not getattr(description, "boiler_only", False))
        and (coordinator.has_heat_source or not getattr(description, "heat_source", False))
    )


class DescribedEntity(ThrifthermEntity):
    """Value and attributes come from the description: of the whole system, or of one room."""

    def __init__(self, coordinator: ThrifthermCoordinator, description: Any, room_key: str | None = None) -> None:
        super().__init__(coordinator, description.key, room_key)
        self.entity_description = description
        self.suggest_english_entity_id()

    def _source(self) -> Any:
        if self._room_key is None:
            return self.coordinator.data or None
        return self.room

    def _value(self) -> Any:
        source = self._source()
        return None if source is None else self.entity_description.value_fn(source)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        source = self._source()
        if source is None or self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(source)
