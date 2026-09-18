"""Home Assistant service calls that carry out a plan.

Nothing here decides anything: the engines plan, the coordinator decides whether a
plan is sent, and these helpers only perform the call and log a failure without
letting it break the update cycle.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .models import HeatPumpCommand, RoomCommand

_LOGGER = logging.getLogger(__name__)

# a schema error from a stricter climate entity must not fail the whole update
_CALL_ERRORS = (HomeAssistantError, vol.Invalid)


async def async_set_thermostat(hass: HomeAssistant, climate_entity: str, room_cmd: RoomCommand) -> None:
    """Write a room setpoint to its thermostat."""
    try:
        await hass.services.async_call("climate", "set_temperature", {"entity_id": climate_entity, "temperature": room_cmd.target}, blocking=True)
    except _CALL_ERRORS as err:
        _LOGGER.warning("thriftherm room %s: setpoint %.1f failed: %s", room_cmd.room, room_cmd.target, err)
        return
    _LOGGER.info("thriftherm room %s: thermostat set to %.1f °C (%s)", room_cmd.room, room_cmd.target, room_cmd.reason)


async def async_send_setmode(hass: HomeAssistant, topic: str, text: str) -> None:
    """Publish a SetMode telegram to ebusd. Never retained: a stale retained telegram would outlive us."""
    if not hass.services.has_service("mqtt", "publish"):
        _LOGGER.warning("thriftherm boiler: mqtt.publish not available, SetMode not sent")
        return
    try:
        await hass.services.async_call("mqtt", "publish", {"topic": topic, "payload": text, "qos": 0, "retain": False}, blocking=True)
    except _CALL_ERRORS as err:
        _LOGGER.warning("thriftherm boiler SetMode failed: %s", err)
        return
    _LOGGER.debug("thriftherm boiler SetMode sent: %s", text)


async def async_request_ebus_read(hass: HomeAssistant, topic: str, payload: str = "") -> bool:
    """Ask ebusd for a read: empty payload reads now, "?1" raises the poll priority.

    Reads only – nothing is written to the boiler. False when MQTT is not ready yet.
    """
    if not hass.services.has_service("mqtt", "publish"):
        return False
    try:
        await hass.services.async_call("mqtt", "publish", {"topic": topic, "payload": payload, "qos": 0, "retain": False}, blocking=True)
    except _CALL_ERRORS as err:
        _LOGGER.debug("thriftherm ebusd read request %s failed: %s", topic, err)
        return False
    return True


async def async_execute_heat_pump(hass: HomeAssistant, entity_id: str, command: HeatPumpCommand) -> None:
    """Send a heat pump command as climate service calls; stop at the first failure."""
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
        try:
            await hass.services.async_call("climate", service, {"entity_id": entity_id, **data}, blocking=True)
        except _CALL_ERRORS as err:
            _LOGGER.warning("thriftherm heat pump command %s %s failed: %s", service, data, err)
            return
    _LOGGER.info("thriftherm heat pump command sent: %s", ", ".join(f"{s} {d}" for s, d in calls))
