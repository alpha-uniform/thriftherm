"""Binary sensor entities for Thriftherm."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ThrifthermConfigEntry
from .entity import DescribedEntity, is_wanted


@dataclass(frozen=True, kw_only=True)
class ThrifthermBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool | None]
    attr_fn: Callable[[Any], dict[str, Any]] | None = None
    heat_pump_addon: bool = False  # only created when a Midea heat pump is configured
    boiler_only: bool = False  # needs an own boiler reachable over ebusd


def _desc(key: str, **kwargs: Any) -> ThrifthermBinaryDescription:
    return ThrifthermBinaryDescription(key=key, translation_key=key, **kwargs)


def _room_desc(key: str, **kwargs: Any) -> ThrifthermBinaryDescription:
    return ThrifthermBinaryDescription(key=key, translation_key=f"room_{key}", **kwargs)


SYSTEM_BINARY: tuple[ThrifthermBinaryDescription, ...] = (
    _desc(
        "boiler_heating_active",
        boiler_only=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d["boiler"].hc_active,
    ),
    _desc(
        "boiler_hwc_active",
        boiler_only=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:water-boiler",
        # ebusd's own flag is unreliable on some boilers; the controller's detection covers it
        value_fn=lambda d: True if d["boiler_command"].hot_water else d["boiler"].hwc_active,
        attr_fn=lambda d: {"last_detected_ts": d["boiler_command"].hot_water_seen_ts},
    ),
    _desc(
        "boiler_available",
        boiler_only=True,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: d["boiler"].available,
        attr_fn=lambda d: {"issues": list(d["boiler"].issues)},
    ),
    _desc(
        "midea_blocked",
        heat_pump_addon=True,
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: "midea" in d["advice"].blocked,
        attr_fn=lambda d: {"reason": d["advice"].blocked.get("midea")},
    ),
    _desc(
        "defrost_suspected",
        heat_pump_addon=True,
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d["defrost"].suspected,
        attr_fn=lambda d: {
            "kind": d["defrost"].kind,
            "indicators": list(d["defrost"].indicators),
            "cycles_last_90min": d["defrost"].cycles_last_90min,
            "block_reason": d["defrost"].block_reason,
        },
    ),
    _desc(
        "override_active",
        icon="mdi:account-clock",
        value_fn=lambda d: bool(d.get("overrides")),
        attr_fn=lambda d: {"overrides": d.get("overrides"), "away_return_ts": d.get("away_return_ts")},
    ),
    _desc(
        "midea_heating_available",
        heat_pump_addon=True,
        icon="mdi:heat-pump-outline",
        value_fn=lambda d: d["midea"].heating_available,
    ),
)

ROOM_BINARY: tuple[ThrifthermBinaryDescription, ...] = (
    _room_desc(
        "window_open",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda r: r.window_open,
        attr_fn=lambda r: {"unknown": r.window_unknown},
    ),
    _room_desc(
        "heating_allowed",
        icon="mdi:radiator",
        value_fn=lambda r: r.heating_allowed,
        attr_fn=lambda r: {"midea_allowed": r.heat_pump_allowed},
    ),
    _room_desc(
        "drying_mode",
        heat_pump_addon=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda r: r.drying.active,
        attr_fn=lambda r: {
            "reason": r.drying_reason,
            "started_ts": r.drying.started_ts,
            "baseline_abs_humidity_g_m3": r.drying.baseline_abs_humidity,
        },
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ThrifthermConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [DescribedBinary(coordinator, d) for d in SYSTEM_BINARY if is_wanted(coordinator, d)]
    for room in coordinator.builder.rooms:
        entities.extend(DescribedBinary(coordinator, d, room.key) for d in ROOM_BINARY if is_wanted(coordinator, d))
    async_add_entities(entities)


class DescribedBinary(DescribedEntity, BinarySensorEntity):
    entity_description: ThrifthermBinaryDescription

    @property
    def is_on(self) -> bool | None:
        return self._value()
