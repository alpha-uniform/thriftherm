"""Home Assistant service calls that carry out a plan.

Nothing here decides anything: the engines plan, the coordinator decides whether a
plan is sent, and these helpers only perform the call and log a failure without
letting it break the update cycle.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from homeassistant.core import Context, HomeAssistant

from .models import HeatPumpCommand, RoomCommand

_LOGGER = logging.getLogger(__name__)

CALL_TIMEOUT_S = 30.0  # a unit that does not answer must not hold up the rest of the cycle


async def _call(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any], what: str, level: int = logging.WARNING,
    context: Context | None = None,
) -> bool:
    """Perform one service call; False when it failed or timed out, which is logged, never raised.

    The services belong to other integrations (Better Thermostat, Midea AC LAN, MQTT), so
    any exception is caught on purpose: one failed room or unit must not keep the other
    commands, the store and the repairs of this cycle from happening.
    """
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            await hass.services.async_call(domain, service, data, blocking=True, context=context)
    except Exception as err:  # noqa: BLE001 - foreign code, see above
        _LOGGER.log(level, "thriftherm %s failed: %s", what, str(err) or type(err).__name__)  # a timeout has no text
        return False
    return True


async def async_set_thermostat(
    hass: HomeAssistant, climate_entity: str, room_cmd: RoomCommand, context: Context | None = None
) -> None:
    """Write a room setpoint to its thermostat. `context` marks the change it causes as ours."""
    data = {"entity_id": climate_entity, "temperature": room_cmd.target}
    what = f"room {room_cmd.room}: setpoint {room_cmd.target:.1f}"
    if await _call(hass, "climate", "set_temperature", data, what, context=context):
        _LOGGER.info("thriftherm room %s: thermostat set to %.1f °C (%s)", room_cmd.room, room_cmd.target, room_cmd.reason)


async def async_send_setmode(hass: HomeAssistant, topic: str, text: str) -> None:
    """Publish a SetMode telegram to ebusd. Never retained: a stale retained telegram would outlive us."""
    if await _call(hass, "mqtt", "publish", {"topic": topic, "payload": text, "qos": 0, "retain": False}, "boiler SetMode"):
        _LOGGER.debug("thriftherm boiler SetMode sent: %s", text)


def _number_value(state, flow_c: float) -> float:
    """The flow as the number entity takes it: within its limits, on its step."""
    attrs = state.attributes
    value = flow_c
    try:
        step = float(attrs.get("step") or 0.0)
        if step > 0:
            value = math.floor(value / step + 0.5) * step  # halves up, not to the even neighbour
        value = min(max(value, float(attrs.get("min", value))), float(attrs.get("max", value)))
    except (TypeError, ValueError):
        pass
    return round(value, 2)


async def async_send_boiler_entities(
    hass: HomeAssistant, setpoint_entity: str, enable_entity: str, flow_c: float, heating_on: bool
) -> bool:
    """Command a boiler through a number (flow setpoint) and a switch (central heating on).

    Nothing is written that the entity already has: many gateways store what they are sent.
    Releasing the heating sends the setpoint first, blocking it the switch first, so the boiler
    never heats to a setpoint that is about to change. False when an entity is missing or a
    call failed: the command then counts as not carried out and is tried again.
    """
    number, switch = hass.states.get(setpoint_entity), hass.states.get(enable_entity)
    if number is None or switch is None or "unavailable" in (number.state, switch.state):
        _LOGGER.warning("thriftherm boiler command not sent: %s or %s is not available", setpoint_entity, enable_entity)
        return False
    value = _number_value(number, flow_c)
    try:
        has_value = abs(float(number.state) - value) < 0.01
    except (TypeError, ValueError):
        has_value = False
    calls: list[tuple[str, str, dict[str, Any]]] = []
    if not has_value:
        calls.append((setpoint_entity.split(".", 1)[0], "set_value", {"entity_id": setpoint_entity, "value": value}))
    if switch.state != ("on" if heating_on else "off"):
        calls.append(("homeassistant", "turn_on" if heating_on else "turn_off", {"entity_id": enable_entity}))
    if not heating_on:
        calls.reverse()
    for domain, service, data in calls:
        if not await _call(hass, domain, service, data, f"boiler command {service} {data}"):
            return False
    if calls:
        _LOGGER.debug("thriftherm boiler command sent: flow %.1f °C, heating %s", value, "on" if heating_on else "off")
    return True


async def async_request_ebus_read(hass: HomeAssistant, topic: str, payload: str = "") -> bool:
    """Ask ebusd for a read: empty payload reads now, "?1" raises the poll priority.

    Reads only – nothing is written to the boiler. False when MQTT is not ready yet.
    """
    data = {"topic": topic, "payload": payload, "qos": 0, "retain": False}
    return await _call(hass, "mqtt", "publish", data, f"ebusd read request {topic}", logging.DEBUG)


async def async_execute_heat_pump(
    hass: HomeAssistant, entity_id: str, command: HeatPumpCommand, context: Context | None = None
) -> bool:
    """Send a heat pump command as climate service calls; stop at the first failure.

    False when a call failed: the command then counts as not carried out.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    if command.action == "stop":
        # "fan_only" first when the fan is to run on; the "off" follows as a stop of its own
        calls.append(("set_hvac_mode", {"hvac_mode": command.hvac_mode or "off"}))
    else:
        # Seen 2026-10-11 with a Midea over LAN: the setpoint went out, the fan mode right after
        # it, and the unit kept its old setpoint - the second call carried the state from before
        # the first. So nothing is sent that the unit already has, and the setpoint goes last.
        state = hass.states.get(entity_id)
        has = state.attributes if state is not None else {}
        if command.action == "start":
            calls.append(("set_hvac_mode", {"hvac_mode": "heat"}))
        if command.fan_mode and has.get("fan_mode") != command.fan_mode:
            calls.append(("set_fan_mode", {"fan_mode": command.fan_mode}))
        if command.target_temp is not None:
            calls.append(("set_temperature", {"temperature": command.target_temp}))
    for service, data in calls:
        what = f"heat pump command {service} {data}"
        if not await _call(hass, "climate", service, {"entity_id": entity_id, **data}, what, context=context):
            return False
    _LOGGER.info("thriftherm heat pump command sent: %s", ", ".join(f"{s} {d}" for s, d in calls))
    return True
