"""What Thriftherm keeps in memory is what something reads (measured 2026-10-23, roadmap "Arbeitsspeicher verschlanken")."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import CONF_ROOM_DRYING, CONF_ROOM_DRYING_AUTO, CONF_ROOM_HUMIDITY
from custom_components.thriftherm.coordinator import LOAD_LOG_ROWS
from custom_components.thriftherm.persistence import LOAD_LOG_STORED_ROWS

from .test_integration import _entry_data, _set_states


def test_the_boiler_load_log_keeps_no_more_rows_than_are_stored():
    """1440 rows were kept, 720 stored and 120 shown in the diagnostics; nothing read the rest (about 0.4 MB)."""
    assert LOAD_LOG_ROWS == LOAD_LOG_STORED_ROWS


def _humidity_kept(hass: HomeAssistant, **bath) -> dict[str, int]:
    """How many humidity samples each room holds after one build."""
    _set_states(hass)
    data = _entry_data()
    data["rooms"][0].update(bath)
    data["rooms"][1][CONF_ROOM_HUMIDITY] = "sensor.bath_rh"  # the living room has a humidity sensor too
    builder = SnapshotBuilder(hass, data)
    rooms = builder.build("auto", {}, {}, {}, None, {}, {}).rooms
    return {key: len(room.abs_humidity_history) for key, room in rooms.items()}


async def test_only_a_bathroom_that_dries_by_itself_keeps_its_humidity_history(hass: HomeAssistant) -> None:
    """The history is the baseline a shower is told by; without the automatic drying nobody reads it."""
    kept = _humidity_kept(hass, **{CONF_ROOM_DRYING: True, CONF_ROOM_DRYING_AUTO: True})
    assert kept == {"badezimmer": 1, "wohnzimmer": 0}


async def test_a_bathroom_that_dries_only_with_the_button_keeps_no_humidity_history(hass: HomeAssistant) -> None:
    assert _humidity_kept(hass, **{CONF_ROOM_DRYING: True, CONF_ROOM_DRYING_AUTO: False}) == {"badezimmer": 0, "wohnzimmer": 0}
    assert _humidity_kept(hass, **{CONF_ROOM_DRYING: False}) == {"badezimmer": 0, "wohnzimmer": 0}
