"""Diagnostics download for a Thriftherm config entry.

What a useful bug report needs is the shape of the installation and what the
engines made of it: which subsystems exist, what was planned and why, and what
has been learned so far. Nothing here is a credential, so nothing is redacted —
entity ids in particular have to stay readable or the report is worthless.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import ThrifthermConfigEntry


def _command(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ThrifthermConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    rooms = data.get("rooms") or {}
    return {
        "entry": {
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "installation": {
            "system_type": coordinator.system_type,
            "has_boiler": coordinator.has_boiler,
            "has_heat_source": coordinator.has_heat_source,
            "has_midea": coordinator.has_heat_pump,
            "rooms": [r.key for r in coordinator.builder.rooms],
        },
        "control": {
            "mode": coordinator.mode,
            "midea_control": coordinator.control_mode,
            "boiler_control": coordinator.boiler_control_mode,
            "room_control": coordinator.room_control_mode,
            "setmode_topic": coordinator.boiler_setmode_topic if coordinator.has_boiler else None,
        },
        "plans": {
            "boiler": _command(data.get("boiler_command")),
            "midea": _command(data.get("midea_command")),
            "rooms": {key: _command(cmd) for key, cmd in (data.get("room_commands") or {}).items()},
        },
        "rooms": {
            key: {
                "target": getattr(result, "target", None),
                "target_reason": getattr(result, "target_reason", None),
                "demand": getattr(result, "demand", None),
                "heating_allowed": getattr(result, "heating_allowed", None),
                "issues": list(getattr(result, "issues", ()) or ()),
            }
            for key, result in rooms.items()
        },
        "learning": {
            "cop_map": coordinator.cop_map.to_storage(),
            "heat_rates": coordinator.heat_rates.to_dict(),
            "cool_rates": coordinator.cool_rates.to_dict(),
            "boiler_memory": coordinator.boiler_memory.to_storage(),
        },
        "safety": _command(data.get("safety")),
    }
