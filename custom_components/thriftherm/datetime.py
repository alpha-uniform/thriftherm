"""The planned return from an absence, editable straight from a dashboard.

Away mode holds every room at the away temperature and pre-heats them so the
comfort temperature is reached when you get back. That only works if the
integration knows when "back" is, so the time is an entity of its own rather
than a service argument.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ThrifthermConfigEntry, ThrifthermCoordinator
from .entity import ThrifthermEntity


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([AwayReturnDateTime(entry.runtime_data)])


class AwayReturnDateTime(ThrifthermEntity, DateTimeEntity):
    """When you expect to be back. Empty while no return is planned."""

    _attr_translation_key = "away_return"
    _attr_icon = "mdi:home-clock"

    def __init__(self, coordinator: ThrifthermCoordinator) -> None:
        super().__init__(coordinator, "away_return")
        self.suggest_english_entity_id()

    @property
    def native_value(self) -> datetime | None:
        ts = self.coordinator.away_return_ts
        return None if ts is None else dt_util.utc_from_timestamp(ts)

    async def async_set_value(self, value: datetime) -> None:
        # Planning a return means being away until then, so this switches the mode too.
        # "Back home" (clear_away) ends both the absence and the plan.
        await self.coordinator.async_set_away(value.timestamp())
