"""Home Assistant service calls that carry out a plan.

Nothing here decides anything: the engines plan, the coordinator decides whether a
plan is sent, and these helpers only perform the call and log a failure without
letting it break the update cycle.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .models import HeatPumpCommand, RoomCommand

_LOGGER = logging.getLogger(__name__)

CALL_TIMEOUT_S = 30.0  # a unit that does not answer must not hold up the rest of the cycle


async def _call(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any], what: str, level: int = logging.WARNING
) -> bool:
    """Perform one service call; False when it failed or timed out, which is logged, never raised.

    The services belong to other integrations (Better Thermostat, Midea AC LAN, MQTT), so
    any exception is caught on purpose: one failed room or unit must not keep the other
    commands, the store and the repairs of this cycle from happening.
    """
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            await hass.services.async_call(domain, service, data, blocking=True)
    except Exception as err:  # noqa: BLE001 - foreign code, see above
        _LOGGER.log(level, "thriftherm %s failed: %s", what, str(err) or type(err).__name__)  # a timeout has no text
        return False
    return True


async def async_set_thermostat(hass: HomeAssistant, climate_entity: str, room_cmd: RoomCommand) -> None:
    """Write a room setpoint to its thermostat."""
    data = {"entity_id": climate_entity, "temperature": room_cmd.target}
    if await _call(hass, "climate", "set_temperature", data, f"room {room_cmd.room}: setpoint {room_cmd.target:.1f}"):
        _LOGGER.info("thriftherm room %s: thermostat set to %.1f °C (%s)", room_cmd.room, room_cmd.target, room_cmd.reason)


async def async_send_setmode(hass: HomeAssistant, topic: str, text: str) -> None:
    """Publish a SetMode telegram to ebusd. Never retained: a stale retained telegram would outlive us."""
    if await _call(hass, "mqtt", "publish", {"topic": topic, "payload": text, "qos": 0, "retain": False}, "boiler SetMode"):
        _LOGGER.debug("thriftherm boiler SetMode sent: %s", text)


async def async_request_ebus_read(hass: HomeAssistant, topic: str, payload: str = "") -> bool:
    """Ask ebusd for a read: empty payload reads now, "?1" raises the poll priority.

    Reads only – nothing is written to the boiler. False when MQTT is not ready yet.
    """
    data = {"topic": topic, "payload": payload, "qos": 0, "retain": False}
    return await _call(hass, "mqtt", "publish", data, f"ebusd read request {topic}", logging.DEBUG)


async def async_execute_heat_pump(hass: HomeAssistant, entity_id: str, command: HeatPumpCommand) -> bool:
    """Send a heat pump command as climate service calls; stop at the first failure.

    False when a call failed: the command then counts as not carried out.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    if command.action == "stop":
        calls.append(("set_hvac_mode", {"hvac_mode": "off"}))
    else:
        if command.action == "start":
            calls.append(("set_hvac_mode", {"hvac_mode": "heat"}))
        if command.target_temp is not None:
            calls.append(("set_temperature", {"temperature": command.target_temp}))
        if command.fan_mode:
            calls.append(("set_fan_mode", {"fan_mode": command.fan_mode}))
    for service, data in calls:
        if not await _call(hass, "climate", service, {"entity_id": entity_id, **data}, f"heat pump command {service} {data}"):
            return False
    _LOGGER.info("thriftherm heat pump command sent: %s", ", ".join(f"{s} {d}" for s, d in calls))
    return True
