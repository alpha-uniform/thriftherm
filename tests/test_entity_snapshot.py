"""Every entity as Home Assistant sees it, pinned against a recorded snapshot.

Guards refactorings of the entity platforms and the coordinator: ids, unique ids,
translation keys, names, device classes, units, options, states and attributes
must stay exactly as they were. After an intended change, record it again with
THRIFTHERM_UPDATE_SNAPSHOT=1 and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.const import CONF_HEAT_PUMP_CLIMATE, CONF_HEAT_PUMP_POWER, CONF_ROOMS, DOMAIN

from .test_integration import _entry_data, _set_states

SNAPSHOT = Path(__file__).parent / "snapshots" / "entities.json"
FROZEN_AT = "2026-01-14 10:30:00+00:00"  # a Wednesday morning in winter


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _with_thermostat() -> dict:
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    return data


def _scenarios() -> dict[str, tuple[dict, dict]]:
    released = {"midea_allow_active_control": True, "boiler_allow_active_control": True, "room_allow_active_control": True}
    return {
        "gas_heat_pump": (_with_thermostat(), {}),
        "gas_heat_pump_released": (_with_thermostat(), released),
        "gas_only": ({k: v for k, v in _entry_data().items() if k not in (CONF_HEAT_PUMP_CLIMATE, CONF_HEAT_PUMP_POWER)}, {}),
        "district": (
            {**_entry_data(), "system_type": "district", "heat_price": 0.12, "heat_consumption_share_pct": 70.0,
             "heat_ownership_share_pct": 5.0},
            {},
        ),
        "heat_pump_only": ({**_entry_data(), "system_type": "none"}, {}),
    }


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _collect(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)

    def device(device_id: str | None) -> dict[str, Any] | None:
        dev = dev_reg.async_get(device_id) if device_id else None
        if dev is None:
            return None
        via = dev_reg.async_get(dev.via_device_id) if dev.via_device_id else None
        return {
            "identifiers": sorted("/".join(i).replace(entry.entry_id, "ENTRY") for i in dev.identifiers),
            "name": dev.name,
            "manufacturer": dev.manufacturer,
            "model": dev.model,
            "via": None if via is None else sorted("/".join(i).replace(entry.entry_id, "ENTRY") for i in via.identifiers),
        }

    out: dict[str, Any] = {}
    for reg in sorted(er.async_entries_for_config_entry(ent_reg, entry.entry_id), key=lambda e: e.entity_id):
        state = hass.states.get(reg.entity_id)
        out[reg.entity_id] = {
            "unique_id": reg.unique_id.replace(entry.entry_id, "ENTRY"),
            "translation_key": reg.translation_key,
            "original_device_class": reg.original_device_class,
            "original_icon": reg.original_icon,
            "unit_of_measurement": reg.unit_of_measurement,
            "capabilities": reg.capabilities,
            "options": {k: dict(v) for k, v in reg.options.items()},
            "device": device(reg.device_id),
            "state": None if state is None else state.state,
            "attributes": None if state is None else dict(state.attributes),
        }
    return _plain(out)


async def _snapshot_of(hass: HomeAssistant, data: dict, options: dict) -> dict[str, Any]:
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 17.0, "current_temperature": 18.5})
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=options, unique_id=DOMAIN, title="Thriftherm", version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return _collect(hass, entry)


@pytest.mark.parametrize("scenario", list(_scenarios()))
async def test_entities_match_the_recorded_snapshot(hass: HomeAssistant, freezer, scenario: str) -> None:
    freezer.move_to(FROZEN_AT)
    data, options = _scenarios()[scenario]
    current = await _snapshot_of(hass, data, options)

    recorded = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
    if os.environ.get("THRIFTHERM_UPDATE_SNAPSHOT"):
        recorded[scenario] = current
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(recorded, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    assert scenario in recorded, "no snapshot recorded yet"
    assert current == recorded[scenario]
