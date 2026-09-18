"""Find the boiler's ebusd entities, the eBUS signal and the gas meter.

ebusd announces every message over MQTT discovery with the registry unique_id
`ebusd_<circuit>_<message>_<field>`. The entity ids are the user's to rename,
the unique_ids are not, so the search goes by unique_id only. The circuit
device carries no boiler model, so the profile is chosen by which of its
messages exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .. import boiler_profiles
from ..const import (
    CONF_BOILER_EBUS_CIRCUIT,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_PROFILE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_SIGNAL,
    CONF_GAS_FLOW,
    CONF_GAS_VOLUME,
    DEFAULT_BOILER_EBUS_CIRCUIT,
    DOMAIN,
)

EBUSD_PREFIX = "ebusd_"
MQTT = "mqtt"
# the domains the boiler form accepts for a value; everything else is a sensor
_ROLE_DOMAINS: dict[str, tuple[str, ...]] = {CONF_BOILER_PUMP_RUNNING: ("sensor", "binary_sensor")}
_REQUIRED_ROLES = (CONF_BOILER_FLOW_TEMP, CONF_BOILER_RETURN_TEMP)  # without these nothing can be derived
# every value a profile can fill: a configuration holding any of them already points at a boiler
_ROLE_KEYS = frozenset(key for p in boiler_profiles.PROFILES.values() for key in p.roles)
_OPTIONAL_KEYS = (CONF_BOILER_SIGNAL, CONF_GAS_VOLUME, CONF_GAS_FLOW)


@dataclass(frozen=True)
class Detection:
    profile: str | None  # None: no ebusd boiler found
    circuit: str | None
    entities: dict[str, str] = field(default_factory=dict)  # config key -> entity_id, signal and gas meter included
    found: int = 0  # boiler values found ...
    total: int = 0  # ... of those the profile knows


def detect(hass: HomeAssistant) -> Detection:
    registry = er.async_get(hass)
    mqtt = {(e.domain, e.unique_id): e.entity_id for e in registry.entities.values() if e.platform == MQTT and not e.disabled_by}
    best: tuple[boiler_profiles.BoilerProfile, str, dict[str, str]] | None = None
    for profile in boiler_profiles.PROFILES.values():
        for circuit in _circuits(mqtt):
            found = _roles(mqtt, profile, circuit)
            if not all(key in found for key in _REQUIRED_ROLES):
                continue
            # most values wins; on a tie the circuit the family usually uses, then the first
            if best is None or (len(found), circuit == profile.circuit) > (len(best[2]), best[1] == best[0].circuit):
                best = (profile, circuit, found)
    extras = _gas_meter(hass, registry)  # not part of the boiler: found with or without one
    if (signal := _signal(registry)) is not None:
        extras[CONF_BOILER_SIGNAL] = signal
    if best is None:
        return Detection(None, None, extras)
    profile, circuit, found = best
    return Detection(profile.key, circuit, {**found, **extras}, len(found), len(profile.roles))


def prefill(current: Mapping[str, Any], detection: Detection) -> dict[str, Any]:
    """What the boiler form should suggest: found entities for fields that are still empty.

    Boiler values come only from the boiler the configuration already points at
    (same profile and circuit), or, while no boiler value is configured at all,
    from the boiler found, together with its circuit and profile. A configured
    entity is never replaced. The eBUS signal and the gas meter fill any empty field.
    """
    fill: dict[str, Any] = {}
    if detection.profile is not None:
        configured = any(current.get(key) for key in _ROLE_KEYS)
        circuit = str(current.get(CONF_BOILER_EBUS_CIRCUIT) or DEFAULT_BOILER_EBUS_CIRCUIT).strip()
        same = boiler_profiles.profile(current.get(CONF_BOILER_PROFILE)).key == detection.profile and circuit == detection.circuit
        if not configured:
            fill[CONF_BOILER_EBUS_CIRCUIT] = detection.circuit
            fill[CONF_BOILER_PROFILE] = detection.profile
        if not configured or same:
            roles = boiler_profiles.profile(detection.profile).roles
            fill.update({k: v for k, v in detection.entities.items() if k in roles and not current.get(k)})
    fill.update({k: detection.entities[k] for k in _OPTIONAL_KEYS if k in detection.entities and not current.get(k)})
    return fill


def _circuits(mqtt: Mapping[tuple[str, str], str]) -> list[str]:
    """Circuit candidates. Message names may contain underscores, so only the circuit is split off."""
    return sorted({uid.split("_", 2)[1] for _domain, uid in mqtt if uid.startswith(EBUSD_PREFIX) and uid.count("_") >= 2})


def _roles(mqtt: Mapping[tuple[str, str], str], profile: boiler_profiles.BoilerProfile, circuit: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, (message, value_field) in profile.roles.items():
        uid = f"{EBUSD_PREFIX}{circuit}_{message}_{value_field}"
        entity_id = next((mqtt[(d, uid)] for d in _ROLE_DOMAINS.get(key, ("sensor",)) if (d, uid) in mqtt), None)
        if entity_id is not None:
            found[key] = entity_id
    return found


def _signal(registry: er.EntityRegistry) -> str | None:
    """The eBUS connectivity sensor, from the adapter or from ebusd itself; several and none from ebusd: no guess."""
    candidates = [
        e
        for e in registry.entities.values()
        if e.domain == "binary_sensor"
        and e.platform == MQTT
        and not e.disabled_by
        and e.unique_id.endswith("_signal")
        and (e.device_class or e.original_device_class) == "connectivity"
    ]
    if len(candidates) > 1:
        candidates = [e for e in candidates if e.unique_id.startswith("ebusd")]
    return candidates[0].entity_id if len(candidates) == 1 else None


def _gas_meter(hass: HomeAssistant, registry: er.EntityRegistry) -> dict[str, str]:
    """The gas meter's total volume and flow, each only when exactly one sensor fits."""
    volume: list[str] = []
    flow: list[str] = []
    for state in hass.states.async_all("sensor"):
        entry = registry.async_get(state.entity_id)
        if entry is not None and (entry.platform == DOMAIN or entry.disabled_by):
            continue  # our own sensors report m³/h too
        attrs = state.attributes
        unit, device_class = attrs.get("unit_of_measurement"), attrs.get("device_class")
        if device_class == "gas" and unit == "m³" and attrs.get("state_class") in ("total_increasing", "total"):
            volume.append(state.entity_id)
        elif unit == "m³/h" and device_class in (None, "gas", "volume_flow_rate"):
            flow.append(state.entity_id)
    found: dict[str, str] = {}
    if len(volume) == 1:
        found[CONF_GAS_VOLUME] = volume[0]
    if len(flow) == 1:
        found[CONF_GAS_FLOW] = flow[0]
    return found
