"""What the controller has learned, and when that knowledge no longer applies.

Learned values describe the installation as it was: a heating curve correction
fits the radiators and the hydraulic balance, a COP map fits the unit with or
without ducts, a heat-up rate fits a room's sensor and thermostat. When the user
changes exactly those settings, the old values would steer the new setup, so
the affected area is forgotten. Anything else is kept.

No Home Assistant imports: the comparison is plain data and unit-tested.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from .const import (
    CONF_BOILER_CURVE_COLD,
    CONF_BOILER_CURVE_WARM,
    CONF_BOILER_FLOW_MAX,
    CONF_BOILER_FLOW_MIN,
    CONF_HEAT_PUMP_AIRFLOW_CURVE,
    CONF_HEAT_PUMP_CLIMATE,
    CONF_HEAT_PUMP_DUCT_FACTOR,
    CONF_HEAT_PUMP_INTAKE_TEMP,
    CONF_HEAT_PUMP_OUTLET_TEMP,
    CONF_HEAT_PUMP_ROOM,
    CONF_ROOM_CLIMATE,
    CONF_ROOM_HEAT_PUMP,
    CONF_ROOM_KEY,
    CONF_ROOM_TEMP,
    CONF_ROOMS,
    DEFAULT_BOILER_CURVE_COLD,
    DEFAULT_BOILER_CURVE_WARM,
    DEFAULT_BOILER_FLOW_MAX,
    DEFAULT_BOILER_FLOW_MIN,
    DEFAULT_HEAT_PUMP_DUCT_FACTOR,
)

SCOPE_BOILER = "boiler"
SCOPE_HEAT_PUMP = "heat_pump"
SCOPE_ROOMS = "rooms"
SCOPES = (SCOPE_BOILER, SCOPE_HEAT_PUMP, SCOPE_ROOMS)

REASON_USER = "user"
REASON_CONFIGURATION = "configuration_changed"

_ROOM_PREFIX = "room:"


def _fingerprint(values: Mapping[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, default=str)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def basis(config: Mapping[str, Any]) -> dict[str, str]:
    """Fingerprint of the settings each learned area depends on."""
    rooms = [r for r in (config.get(CONF_ROOMS) or []) if r.get(CONF_ROOM_KEY)]
    result = {
        SCOPE_BOILER: _fingerprint(
            {
                "curve_cold": _as_float(config.get(CONF_BOILER_CURVE_COLD), DEFAULT_BOILER_CURVE_COLD),
                "curve_warm": _as_float(config.get(CONF_BOILER_CURVE_WARM), DEFAULT_BOILER_CURVE_WARM),
                "flow_min": _as_float(config.get(CONF_BOILER_FLOW_MIN), DEFAULT_BOILER_FLOW_MIN),
                "flow_max": _as_float(config.get(CONF_BOILER_FLOW_MAX), DEFAULT_BOILER_FLOW_MAX),
            }
        ),
        SCOPE_HEAT_PUMP: _fingerprint(
            {
                "climate": config.get(CONF_HEAT_PUMP_CLIMATE),
                "intake": config.get(CONF_HEAT_PUMP_INTAKE_TEMP),
                "outlet": config.get(CONF_HEAT_PUMP_OUTLET_TEMP),
                "airflow_curve": (config.get(CONF_HEAT_PUMP_AIRFLOW_CURVE) or "").replace(" ", ""),
                "duct_factor": _as_float(config.get(CONF_HEAT_PUMP_DUCT_FACTOR), DEFAULT_HEAT_PUMP_DUCT_FACTOR),
                "installed_in": config.get(CONF_HEAT_PUMP_ROOM),
                "serves": sorted(str(r[CONF_ROOM_KEY]) for r in rooms if r.get(CONF_ROOM_HEAT_PUMP)),
            }
        ),
    }
    for room in rooms:
        result[_ROOM_PREFIX + str(room[CONF_ROOM_KEY])] = _fingerprint(
            {"temperature": room.get(CONF_ROOM_TEMP), "thermostat": room.get(CONF_ROOM_CLIMATE)}
        )
    return result


def invalidated(stored: Any, current: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """Scopes and rooms whose settings changed since the learned values were stored.

    Without a stored basis (first start of this version) nothing is invalidated:
    the values were learned on the setup that is configured now. A room that is
    new or was removed has nothing learned worth resetting.
    """
    if not isinstance(stored, Mapping):
        return set(), set()
    scopes = {s for s in (SCOPE_BOILER, SCOPE_HEAT_PUMP) if s in stored and stored[s] != current.get(s)}
    rooms = {
        key[len(_ROOM_PREFIX):]
        for key, value in current.items()
        if key.startswith(_ROOM_PREFIX) and key in stored and stored[key] != value
    }
    if rooms:
        scopes.add(SCOPE_ROOMS)
    return scopes, rooms


def normalise_scopes(scopes: Iterable[str] | None) -> tuple[str, ...]:
    chosen = set(SCOPES if scopes is None else scopes)
    return tuple(s for s in SCOPES if s in chosen)
