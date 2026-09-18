"""Repair issues: make silent degradation visible.

Several conditions do not break the integration but quietly reduce it to less
than it promises — a room with no heating window never leaves setback, an
uncalibrated airflow curve means no COP is ever measured, a lost eBUS signal
means the boiler is back on its own knob. Each of those deserves a line in
Settings → Repairs instead of a value that silently stays empty.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import CONF_HEAT_PUMP_ROOM, DOMAIN

_ISSUE_PREFIX = "thriftherm_"
# After a restart the first evaluation runs before ebusd has republished its
# values; flagging that would put a repair entry up on every restart.
BOILER_UNAVAILABLE_GRACE_S = 600.0


SCHEDULE_MISSING_GRACE_S = 120.0  # the schedule integration may load after us


def _sustained(coordinator: Any, key: str, active: bool, grace_s: float) -> bool:
    """True once a condition has held for `grace_s`; restarting the clock when it clears."""
    since_map = getattr(coordinator, "issue_since", None)
    if since_map is None:
        since_map = {}
        coordinator.issue_since = since_map
    if not active:
        since_map.pop(key, None)
        return False
    now = time.time()
    since = since_map.setdefault(key, now)
    return now - since >= grace_s


def detect(hass: HomeAssistant, coordinator: Any) -> dict[str, dict[str, str]]:
    """Return the issues that currently apply, keyed by issue id.

    Reads state but touches no registry, so it stays straightforward to test.
    """
    found: dict[str, dict[str, str]] = {}
    data = coordinator.data or {}

    for room in coordinator.builder.rooms:
        missing = bool(room.schedule_entity) and hass.states.get(room.schedule_entity) is None
        if _sustained(coordinator, f"schedule_missing_{room.key}", missing, SCHEDULE_MISSING_GRACE_S):
            # the helper was deleted or renamed: the room silently falls back to
            # the text windows, which are usually empty once a helper was chosen
            found[f"schedule_missing_{room.key}"] = {
                "translation_key": "schedule_missing",
                "room": room.name,
                "entity_id": room.schedule_entity,
            }
        elif not missing and not room.schedule_entity and not room.schedule_weekday and not room.schedule_weekend:
            found[f"no_schedule_{room.key}"] = {"translation_key": "no_schedule", "room": room.name}

    if coordinator.has_heat_pump and not coordinator.builder.params.airflow_curve:
        found["airflow_not_calibrated"] = {"translation_key": "airflow_not_calibrated"}

    if coordinator.has_heat_pump:
        served = [r for r in coordinator.builder.rooms if r.served_by_heat_pump]
        # read from the configuration: on the first cycle after a start there is no data yet
        installed_in = coordinator.config.get(CONF_HEAT_PUMP_ROOM)
        elsewhere = [r for r in served if r.key != installed_in]
        # a duct factor at or above 1 means no ducts are fitted: the warm air
        # stays in the room the unit stands in, whatever it is meant to serve
        if elsewhere and coordinator.builder.params.duct_factor >= 0.95:
            found["heat_stays_in_the_room"] = {
                "translation_key": "heat_stays_in_the_room",
                "rooms": ", ".join(r.name for r in elsewhere),
            }

    if coordinator.has_boiler:
        boiler = data.get("boiler")
        unavailable = boiler is not None and getattr(boiler, "available", True) is False
        if _sustained(coordinator, "boiler_data_unavailable", unavailable, BOILER_UNAVAILABLE_GRACE_S):
            found["boiler_data_unavailable"] = {"translation_key": "boiler_data_unavailable"}

    return found


async def async_sync(hass: HomeAssistant, coordinator: Any) -> None:
    """Create the issues that apply now and clear the ones that no longer do."""
    current = detect(hass, coordinator)
    registry = ir.async_get(hass)
    existing = {
        issue_id
        for (domain, issue_id) in registry.issues
        if domain == DOMAIN and issue_id.startswith(_ISSUE_PREFIX)
    }
    for issue_id, info in current.items():
        full_id = _ISSUE_PREFIX + issue_id
        placeholders = {k: v for k, v in info.items() if k != "translation_key"}
        ir.async_create_issue(
            hass,
            DOMAIN,
            full_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=info["translation_key"],
            translation_placeholders=placeholders or None,
        )
        existing.discard(full_id)
    for stale in existing:
        ir.async_delete_issue(hass, DOMAIN, stale)
