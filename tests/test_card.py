"""The dashboard card: served by the integration, loaded with its version, worded from texts.py."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm import card, texts
from custom_components.thriftherm.const import CONF_ROOMS

from .test_integration import _entry_data, _set_states, _setup
from .test_thermostat_silent import _register_bt_and_valve

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "thriftherm"
VERSION = json.loads((COMPONENT / "manifest.json").read_text())["version"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_the_card_is_served(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "http", {})
    await _setup(hass)

    response = await (await hass_client()).get(card.CARD_URL)

    assert response.status == 200
    body = await response.text()
    assert 'customElements.define("thriftherm-card"' in body
    assert body == card.CARD_FILE.read_text()


async def test_dashboards_load_the_card_with_the_version_in_its_address(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")  # its own setup needs the frontend package, which tests lack
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await _setup(hass)

    add_js.assert_called_once_with(hass, f"/thriftherm/thriftherm-card.js?v={VERSION}-{card.card_digest()}")


async def test_without_a_web_server_the_integration_still_starts(hass: HomeAssistant) -> None:
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await _setup(hass)

    add_js.assert_not_called()
    assert hass.states.get("sensor.thriftherm_badezimmer_target_temperature") is not None


async def test_the_card_gets_its_wording_in_the_language_of_the_viewer(hass: HomeAssistant, hass_ws_client) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "thriftherm/card_texts", "language": "de-CH"})
    german = await client.receive_json()
    await client.send_json({"id": 2, "type": "thriftherm/card_texts", "language": "fr"})
    english = await client.receive_json()

    assert german["success"] and german["result"]["card"]["comfort"] == "Komfort"
    assert german["result"]["paused_because"]["no_heating_run"] == "die Therme gerade nicht heizt"
    assert english["result"]["language"] == "en" and english["result"]["card"]["comfort"] == "Comfort"


async def test_a_room_tells_where_its_temperature_comes_from_and_its_key(hass: HomeAssistant) -> None:
    await _setup(hass)

    bath = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    living = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")

    assert bath.attributes["temperature_entity"] == "sensor.bath_temp"
    assert bath.attributes["room_key"] == "badezimmer"
    assert living.attributes["temperature_entity"] == "sensor.living_temp"
    assert "current_temperature" not in bath.attributes  # read at its source, not copied into every row


async def _refresh(hass: HomeAssistant) -> None:
    await hass.config_entries.async_entries("thriftherm")[0].runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_the_temperature_entity_moves_to_the_valve_while_it_stands_in(hass: HomeAssistant) -> None:
    _register_bt_and_valve(hass)
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    _set_states(hass)
    # Better Thermostat keeps showing the dead room sensor; the valve behind it measures the room
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "current_temperature": 20.0})
    entry = MockConfigEntry(domain="thriftherm", data=data, unique_id="thriftherm", title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    target = "sensor.thriftherm_badezimmer_target_temperature"
    assert hass.states.get(target).attributes["temperature_entity"] == "sensor.bath_temp"

    hass.states.async_set("sensor.bath_temp", "unavailable")
    await _refresh(hass)
    assert hass.states.get(target).attributes["temperature_entity"] == "climate.bath_trv"

    hass.states.async_set("sensor.bath_temp", "19.0")
    await _refresh(hass)
    assert hass.states.get(target).attributes["temperature_entity"] == "sensor.bath_temp"


async def test_a_room_without_a_temperature_names_no_source(hass: HomeAssistant) -> None:
    await _setup(hass)
    hass.states.async_set("sensor.living_temp", "unavailable")
    await _refresh(hass)

    living = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")
    assert living.attributes["temperature_entity"] is None


def test_card_wording_exists_in_both_languages() -> None:
    assert texts.CARD["en"].keys() == texts.CARD["de"].keys()
    assert texts.PAUSED_BECAUSE["en"].keys() == texts.PAUSED_BECAUSE["de"].keys()
    assert texts.SAFETY_ISSUES["en"].keys() == texts.SAFETY_ISSUES["de"].keys()


def test_every_word_the_card_asks_for_exists() -> None:
    source = card.CARD_FILE.read_text()
    # every quoted key in the first argument of this._t(...), also where an expression picks it
    # (a string compared with === is a value, not a key)
    asked = {key for arg in re.findall(r"this\._t\(([^,)]*)", source) for key in re.findall(r'(?<!== )"([a-z_]+)"', arg)}
    asked |= {"editor_title", "editor_rooms", "editor_up", "editor_down"}  # read through a local t() in the editor
    assert {"gas", "district", "heat_pump_heats", "comfort"} <= asked
    assert asked <= texts.CARD["en"].keys()
    assert "type: `${DOMAIN}/card_texts`" in source and card.WS_TEXTS == "thriftherm/card_texts"
    assert "type: `${DOMAIN}/decision_reasons`" in source and card.WS_REASONS == "thriftherm/decision_reasons"


async def test_the_card_gets_the_decision_reasons_in_the_language_of_the_viewer(hass: HomeAssistant, hass_ws_client) -> None:
    hass.config.language = "en"
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "thriftherm/decision_reasons", "language": "de"})
    german = (await client.receive_json())["result"]
    sensor = hass.states.get("sensor.thriftherm_decision_reason")

    assert german["codes"] == sensor.attributes["reason_codes"]
    assert "heat_pump_unavailable" in german["codes"]
    index = german["codes"].index("heat_pump_unavailable")
    assert german["texts"][index].startswith("Wärmepumpe nicht heizbereit")
    assert sensor.attributes["reasons"][index].startswith("heat pump not ready")


def test_every_safety_issue_has_wording() -> None:
    source = (COMPONENT / "engines" / "safety.py").read_text()
    codes = set(re.findall(r'issues\.append\(f?"([a-z_]+)', source))
    assert codes and codes <= texts.SAFETY_ISSUES["en"].keys()


def test_every_reason_the_learning_pauses_for_has_wording() -> None:
    source = (COMPONENT / "coordinator.py").read_text() + (COMPONENT / "sensor.py").read_text()
    codes = set(re.findall(r'learning_blocked\.append\("([a-z_]+)"\)', source)) | {"no_heating_run", "rooms_heating_up"}
    assert codes and codes <= texts.PAUSED_BECAUSE["en"].keys()


async def test_a_room_names_its_schedule_helper(hass: HomeAssistant) -> None:
    data = _entry_data()
    data[CONF_ROOMS][0]["schedule_entity"] = "schedule.bath"
    _set_states(hass)
    entry = MockConfigEntry(domain="thriftherm", data=data, unique_id="thriftherm", title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    bath = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    living = hass.states.get("sensor.thriftherm_wohnzimmer_target_temperature")
    assert bath.attributes["schedule_entity"] == "schedule.bath"
    assert living.attributes["schedule_entity"] is None


def test_a_stalled_room_names_the_temperature_it_calls_again_at() -> None:
    from dataclasses import replace

    from custom_components.thriftherm.engines import heat_call
    from custom_components.thriftherm.sensor import ROOM_SENSORS

    from .test_heat_call import rr

    demand = next(d for d in ROOM_SENSORS if d.key == "demand")
    stalled = replace(rr(temp=20.8, target=21.0), boiler_call=heat_call.CALL_STALLED)
    assert demand.attr_fn(stalled)["calls_again_below"] == round(21.0 - heat_call.STALL_RELEASE_K, 1)
    assert demand.attr_fn(replace(stalled, boiler_call=heat_call.CALL_CALLING))["calls_again_below"] is None


def test_a_drying_started_by_hand_says_so_and_back_to_automatic_ends_it() -> None:
    """The cards offer "back to automatic" for it: the action ends a drying asked for with the button, not one after a shower."""
    from dataclasses import replace

    from custom_components.thriftherm.binary_sensor import ROOM_BINARY
    from custom_components.thriftherm.models import DryingState

    from .test_heat_call import rr

    drying = next(d for d in ROOM_BINARY if d.key == "drying_mode")
    by_button = replace(rr(temp=20.8, target=22.0), drying=DryingState(True, 0.0, None, manual=True))
    assert drying.attr_fn(by_button)["by_hand"] is True
    assert drying.attr_fn(replace(by_button, drying=DryingState(True, 0.0, None)))["by_hand"] is False
    card_js = card.CARD_FILE.read_text()
    assert '"room_drying_mode"' in card_js and "attributes.by_hand === true" in card_js
