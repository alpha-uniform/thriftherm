"""The dashboard card: served by the integration, loaded with its version, worded from texts.py."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
    stalled = replace(rr(temp=20.8, target=21.0), boiler_call=heat_call.CALL_STALLED, stalled_target=21.0)
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


async def test_a_heat_pump_the_mode_does_not_use_is_no_problem_until_a_mode_uses_it(hass: HomeAssistant) -> None:
    """Unplugged in "boiler only" (2026-10-21) its problem sensor stayed on; "off" uses no heat pump either.

    Switched to a mode that uses it, the sensor is on again with the same switch, no cycle later.
    The reason stays all along: it says what keeps the heat pump from heating, only the problem follows the mode."""
    await _setup(hass)
    hass.states.async_set("climate.midea", "unavailable")  # the unit unplugged
    seen = []
    for mode in ("boiler_only", "off", "auto", "midea_only"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": "select.thriftherm_operating_mode", "option": mode}, blocking=True
        )
        blocked = hass.states.get("binary_sensor.thriftherm_heat_pump_blocked_for_heating")
        seen.append((mode, blocked.state, blocked.attributes["reason"]))

    assert seen == [
        ("boiler_only", "off", "unavailable"),
        ("off", "off", "unavailable"),
        ("auto", "on", "unavailable"),
        ("midea_only", "on", "unavailable"),
    ]


# Issue #1 (2026-10-18): loaded with `add_extra_js_url` only, the card was missing now and then in Firefox
# and the companion app ("Custom element not found"); a dashboard resource for it, added by hand, cured it.


async def _with_dashboards(hass: HomeAssistant, lovelace: dict | None = None) -> None:
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {"lovelace": lovelace or {}})
    hass.config.components.add("frontend")  # its own setup needs the frontend package, which tests lack


def _card_resources(hass: HomeAssistant) -> list[dict]:
    return [r for r in hass.data["lovelace"].resources.async_items() if r["url"].split("?")[0] == card.CARD_URL]


async def test_the_card_is_a_dashboard_resource_with_the_version_in_its_address(hass: HomeAssistant) -> None:
    await _with_dashboards(hass)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await _setup(hass)

    assert [(r["type"], r["url"]) for r in _card_resources(hass)] == [("module", card.card_url(VERSION, card.card_digest()))]
    add_js.assert_not_called()  # loaded once: as a resource, not a second time on every page


async def test_a_resource_for_the_card_added_by_hand_is_brought_to_the_current_address(hass: HomeAssistant) -> None:
    await _with_dashboards(hass)
    await hass.data["lovelace"].resources.async_create_item({"res_type": "module", "url": card.CARD_URL})
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        await _setup(hass)

    assert [r["url"] for r in _card_resources(hass)] == [card.card_url(VERSION, card.card_digest())]  # no second one


async def test_with_resources_in_yaml_the_card_is_loaded_on_every_page_as_before(hass: HomeAssistant) -> None:
    await _with_dashboards(hass, {"resource_mode": "yaml", "resources": []})
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await _setup(hass)

    add_js.assert_called_once_with(hass, card.card_url(VERSION, card.card_digest()))


async def test_removing_thriftherm_removes_its_dashboard_resource(hass: HomeAssistant) -> None:
    await _with_dashboards(hass)
    await hass.data["lovelace"].resources.async_create_item({"res_type": "module", "url": "/local/other-card.js"})
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        entry = await _setup(hass)
    assert _card_resources(hass)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _card_resources(hass) == []
    assert [r["url"] for r in hass.data["lovelace"].resources.async_items()] == ["/local/other-card.js"]


async def test_thriftherm_added_again_after_its_removal_is_a_dashboard_resource_again(hass: HomeAssistant) -> None:
    await _with_dashboards(hass)
    with patch("homeassistant.components.frontend.add_extra_js_url"):
        entry = await _setup(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _card_resources(hass) == []

    # added again in the same run: Home Assistant does not set up the component a second time, only the entry
    again = MockConfigEntry(domain="thriftherm", data=_entry_data(), unique_id="thriftherm", title="Thriftherm")
    again.add_to_hass(hass)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        assert await hass.config_entries.async_setup(again.entry_id)
        await hass.async_block_till_done()

    assert [r["url"] for r in _card_resources(hass)] == [card.card_url(VERSION, card.card_digest())]
    add_js.assert_not_called()


async def test_a_failing_resource_store_leaves_the_card_on_every_page(hass: HomeAssistant) -> None:
    await _with_dashboards(hass)
    broken = patch.object(hass.data["lovelace"].resources, "async_create_item", side_effect=RuntimeError("store"))
    with broken, patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await _setup(hass)

    add_js.assert_called_once_with(hass, card.card_url(VERSION, card.card_digest()))
    assert hass.states.get("sensor.thriftherm_badezimmer_target_temperature") is not None  # started all the same


# The card itself, run in node: a browser and a Home Assistant just large enough for it, then the file as it is served.

_DOM = """
process.on("unhandledRejection", () => {});
const defined = new Map();
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = { innerHTML: "", activeElement: null, addEventListener() {} }; return this.shadowRoot; }
  dispatchEvent() { return true; }
};
globalThis.customElements = { get: (n) => defined.get(n), define: (n, c) => defined.set(n, c) };
globalThis.document = { createElement: (n) => new (defined.get(n))() };
globalThis.window = globalThis;
globalThis.CustomEvent = class { constructor(type, init) { Object.assign(this, { type }, init); } };
"""
_HASS = """
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const calls = [];
function makeHass(refuse = () => false) {
  const entities = {}, states = {};
  const add = (entity_id, tk, device_id, state, attributes = {}) => {
    entities[entity_id] = { entity_id, platform: "thriftherm", translation_key: tk, device_id };
    states[entity_id] = { entity_id, state, attributes, last_updated: "t0" };
  };
  for (const key of ["badezimmer", "wohnzimmer"]) {
    add(`sensor.thriftherm_${key}_target_temperature`, "room_target", key, "21.0",
      { room_key: key, temperature_entity: `sensor.${key}_temp`, reason: "schedule_comfort" });
    add(`binary_sensor.thriftherm_${key}_window_open`, "room_window_open", key, "off");
    add(`binary_sensor.thriftherm_${key}_heating_allowed`, "room_heating_allowed", key, "on");
    add(`number.thriftherm_${key}_comfort_temperature`, "room_comfort_temp", key, "18.5", { min: 10, max: 28, step: 0.5 });
    add(`number.thriftherm_${key}_setback_temperature`, "room_setback_temp", key, "18.5", { min: 5, max: 25, step: 0.5 });
    states[`sensor.${key}_temp`] = { entity_id: `sensor.${key}_temp`, state: "18.0", attributes: {}, last_updated: "t0" };
  }
  return {
    language: "en", locale: { language: "en", number_format: "language" }, config: { time_zone: "Europe/Berlin" },
    entities, states, devices: { badezimmer: { name: "Thriftherm Badezimmer" }, wohnzimmer: { name: "Thriftherm Wohnzimmer" } },
    callWS: async (msg) => (msg.type.endsWith("card_texts") ? TEXTS : { codes: [], texts: [] }),
    callService: (...a) => { calls.push(a); return refuse(a) ? Promise.reject(new Error("refused")) : Promise.resolve(); },
    formatEntityState: (s, v) => v ?? s.state,
    formatEntityAttributeValue: (s, a) => s.attributes[a],
  };
}
async function shown(card, hass) { card.hass = hass; await tick(20); card.hass = { ...hass }; await tick(20); }
"""


def _run_card(script: str) -> dict:
    """The card file run in node after a minimal browser and Home Assistant; the script prints one JSON line."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    prelude = _DOM + f"const TEXTS = {json.dumps(texts.card_texts('en'))};\n" + _HASS
    code = prelude + card.CARD_FILE.read_text() + "\n(async () => {\n" + script + "\n})();"
    done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_setback_home_assistant_refused_is_not_shown_as_set() -> None:
    """Thriftherm refuses a setback above comfort: the number keeps its state and its time, the card its old value."""
    result = _run_card("""
      const room = new (customElements.get("thriftherm-room-card"))();
      room.setConfig({ room: "wohnzimmer" });
      const hass = makeHass((a) => a[0] === "number");
      await shown(room, hass);
      room._step("number.thriftherm_wohnzimmer_setback_temperature", 1);
      await tick(700);
      room.hass = { ...hass, states: { ...hass.states } };
      const steppers = [...room.shadowRoot.innerHTML.matchAll(/step-label">([^<]*)<[\\s\\S]*?step-value">([^<]*)</g)];
      console.log(JSON.stringify({ sent: calls.length, setback: steppers.find((m) => m[1] === "Setback")[2] }));
    """)

    assert result["sent"] == 1
    assert result["setback"].startswith("18.5")


def test_a_heat_pump_that_cannot_heat_is_one_warning_not_two() -> None:
    """In fan only, switched off or out of reach the heat pump is blocked: the lock line says so, the reason not again."""
    from custom_components.thriftherm.models import reason

    unavailable = [reason("heat_pump_unavailable", state="fan_only"), reason("no_demand")]
    decision = {"codes": [r.code for r in unavailable], "texts": texts.render_reasons(unavailable, "en", {})}
    codes = json.dumps(decision["codes"])
    result = _run_card(f"""
      const main = new (customElements.get("thriftherm-card"))();
      main.setConfig({{}});
      const hass = makeHass();
      const system = (entity_id, tk, state, attributes) => {{
        hass.entities[entity_id] = {{ entity_id, platform: "thriftherm", translation_key: tk }};
        hass.states[entity_id] = {{ entity_id, state, attributes, last_updated: "t0" }};
      }};
      system("binary_sensor.thriftherm_heat_pump_blocked_for_heating", "midea_blocked", "on", {{ reason: "fan_only" }});
      system("sensor.thriftherm_decision_reason", "decision_reason", "no demand", {{ reason_codes: {codes} }});
      const ask = hass.callWS;
      hass.callWS = async (msg) => (msg.type.endsWith("decision_reasons") ? {json.dumps(decision)} : ask(msg));
      await shown(main, hass);
      main.hass = {{ ...hass, states: {{ ...hass.states }} }};
      const warnings = [...main.shadowRoot.innerHTML.matchAll(/warn-text">([^<]*)</g)].map((m) => m[1]);
      console.log(JSON.stringify({{ warnings }}));
    """)

    assert result["warnings"] == ["Heat pump blocked: Fan only"]


def _decision(*items) -> dict:
    return {"codes": [r.code for r in items], "texts": texts.render_reasons(items, "en", {})}


def _warnings_through(stages: list) -> list[list[str]]:
    """The card's warnings at each (operating mode, heat pump blocked, decision) in turn."""
    result = _run_card(f"""
      const main = new (customElements.get("thriftherm-card"))();
      main.setConfig({{}});
      const hass = makeHass();
      let decision, stamp;
      const system = (entity_id, tk, state, attributes) => {{
        hass.entities[entity_id] = {{ entity_id, platform: "thriftherm", translation_key: tk }};
        hass.states[entity_id] = {{ entity_id, state, attributes, last_updated: stamp }};
      }};
      const ask = hass.callWS;
      hass.callWS = async (msg) => (msg.type.endsWith("decision_reasons") ? decision : ask(msg));
      const seen = [];
      for (const [i, [mode, blocked, d]] of {json.dumps(stages)}.entries()) {{
        [decision, stamp] = [d, `t${{i}}`];
        system("select.thriftherm_operating_mode", "mode", mode, {{ options: ["auto", "boiler_only", "midea_only", "off"] }});
        system("binary_sensor.thriftherm_heat_pump_blocked_for_heating", "midea_blocked", blocked, {{ reason: "unavailable" }});
        system("sensor.thriftherm_decision_reason", "decision_reason", "-", {{ reason_codes: d.codes }});
        await shown(main, hass);
        main.hass = {{ ...hass, states: {{ ...hass.states }} }};
        seen.push([...main.shadowRoot.innerHTML.matchAll(/warn-text">([^<]*)</g)].map((m) => m[1]));
      }}
      console.log(JSON.stringify({{ seen }}));
    """)
    return result["seen"]


def test_a_heat_pump_the_mode_does_not_use_is_no_warning_until_a_mode_uses_it() -> None:
    """Unplugged since 2026-10-21 in "boiler only", the card warned about the heat pump all along.

    In boiler only and off nothing that keeps the heat pump from heating is a warning: not its block,
    not the reason behind it. Switched to a mode that uses it, the warning is there with the new mode."""
    from custom_components.thriftherm.models import reason

    unreachable = reason("heat_pump_unavailable", state="unavailable")
    stages = [
        ("boiler_only", "on", _decision(unreachable, reason("mode_boiler_only"))),  # the sensor as it was, or a moment old
        ("boiler_only", "off", _decision(unreachable, reason("mode_boiler_only"))),
        ("off", "off", _decision(unreachable, reason("mode_off"))),
        ("auto", "on", _decision(unreachable, reason("no_demand"))),
    ]
    assert _warnings_through(stages) == [[], [], [], ["Heat pump blocked: Unavailable"]]


def test_heat_pump_rooms_waiting_for_a_blocked_heat_pump_are_said_by_the_block_alone() -> None:
    """Review 2026-10-23: in auto with the unit unplugged, the block and "heat pump rooms need heat" were two lines,
    and in off with a room below frost protection the second line stayed although the heat pump does not heat there."""
    from custom_components.thriftherm.models import reason

    unreachable = reason("heat_pump_unavailable", state="unavailable")
    stages = [
        ("auto", "on", _decision(unreachable, reason("heat_pump_rooms_blocked"))),
        ("off", "off", _decision(unreachable, reason("heat_pump_rooms_blocked"))),
    ]
    assert _warnings_through(stages) == [["Heat pump blocked: Unavailable"], []]
