"""What the coordinator's user actions do: allowed modes, log lines, memory resets, saving."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm.engines.boiler_control import BoilerMemory
from custom_components.thriftherm.engines.heat_pump_control import ControlMemory
from custom_components.thriftherm.engines.room_control import RoomCtrlMemory
from custom_components.thriftherm.models import Override

from .test_integration import _setup

RELEASED = {"midea_allow_active_control": True, "boiler_allow_active_control": True, "room_allow_active_control": True}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_allowed_control_modes_follow_the_release(hass: HomeAssistant) -> None:
    coordinator = (await _setup(hass)).runtime_data
    assert coordinator.control_modes == ["off", "shadow"]
    assert coordinator.boiler_control_modes == ["off", "shadow"]
    assert coordinator.room_control_modes == ["off", "shadow"]
    coordinator.config.update(RELEASED)
    assert coordinator.control_modes == ["off", "shadow", "active"]
    assert coordinator.boiler_control_modes == ["off", "shadow", "active"]
    assert coordinator.room_control_modes == ["off", "shadow", "active"]
    assert coordinator.control_modes is not coordinator.control_modes  # a fresh list every time


@pytest.mark.parametrize(
    ("options", "missing"), [({"midea_climate": None}, "midea_only"), ({"system_type": "none"}, "boiler_only")]
)
async def test_operating_modes_follow_the_installation(hass: HomeAssistant, hass_storage, options: dict, missing: str) -> None:
    """A mode without its heat source (heat pump only without a heat pump, boiler only without one) heats nothing."""
    from homeassistant.exceptions import ServiceValidationError
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

    from .test_integration import _entry_data, _set_states

    # stored while the installation still had that source
    key = f"{STORAGE_KEY}.modes"
    hass_storage[key] = {"version": STORAGE_VERSION, "minor_version": 1, "key": key, "data": {"mode": missing}}
    _set_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=_entry_data(), options=options, entry_id="modes", unique_id=DOMAIN, title="Thriftherm"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    assert coordinator.mode == "auto"
    assert missing not in coordinator.modes and {"auto", "off", "away"} <= set(coordinator.modes)
    assert hass.states.get("select.thriftherm_operating_mode").attributes["options"] == coordinator.modes
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": "select.thriftherm_operating_mode", "option": missing}, blocking=True
        )
    with pytest.raises(ValueError, match=f"^mode {missing} not offered$"):
        coordinator.set_mode(missing)
    assert coordinator.mode == "auto"


@pytest.mark.parametrize(
    ("setter", "label"),
    [("set_control_mode", "midea"), ("set_boiler_control_mode", "boiler"), ("set_room_control_mode", "room")],
)
async def test_a_mode_that_is_not_released_is_refused(hass: HomeAssistant, setter: str, label: str) -> None:
    coordinator = (await _setup(hass)).runtime_data
    with pytest.raises(ValueError, match=f"^{label} control mode active not allowed$"):
        getattr(coordinator, setter)("active")


async def test_switching_control_modes_logs_resets_and_marks_the_store(hass: HomeAssistant, caplog) -> None:
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    coordinator.control_memory = ControlMemory(running_since_ts=1.0, last_hvac_mode="heat", offset_k=2.5)
    coordinator.boiler_memory = BoilerMemory(last_payload="auto;x", last_send_ts=5.0, offset_k=1.5)
    coordinator.room_ctrl_memory["badezimmer"] = RoomCtrlMemory(last_sent_target=21.0)
    coordinator._store_dirty = False
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="custom_components.thriftherm.coordinator"):
        coordinator.set_control_mode("shadow")  # unchanged: nothing happens
        coordinator.set_boiler_control_mode("shadow")
        coordinator.set_room_control_mode("shadow")
        assert caplog.messages == [] and coordinator._store_dirty is False
        assert coordinator.room_ctrl_memory

        coordinator.set_control_mode("active")
        coordinator.set_boiler_control_mode("off")
        coordinator.set_room_control_mode("active")

    assert caplog.messages == [
        "thriftherm: midea control shadow -> active",
        "thriftherm: boiler control shadow -> off",
        "thriftherm: room control shadow -> active",
    ]
    assert (coordinator.control_mode, coordinator.boiler_control_mode, coordinator.room_control_mode) == ("active", "off", "active")
    assert coordinator.control_memory.running_since_ts is None and coordinator.control_memory.offset_k == 2.5
    assert coordinator.boiler_memory.last_payload is None and coordinator.boiler_memory.last_send_ts is None
    assert coordinator.boiler_memory.offset_k == 1.5
    assert coordinator.room_ctrl_memory == {}
    assert coordinator._store_dirty is True


async def test_every_user_action_saves_and_refreshes(hass: HomeAssistant) -> None:
    coordinator = (await _setup(hass)).runtime_data
    coordinator._store.async_save = AsyncMock()
    coordinator.async_refresh = AsyncMock(wraps=coordinator.async_refresh)
    actions = [
        lambda: coordinator.async_set_room_temperature("badezimmer", "comfort", 21.5),
        lambda: coordinator.async_boost("badezimmer", 10),
        lambda: coordinator.async_set_override("badezimmer", 22.0, 30),
        lambda: coordinator.async_clear_override("badezimmer"),
        lambda: coordinator.async_set_away(None),
        lambda: coordinator.async_clear_away(),
    ]
    for action in actions:
        coordinator._store.async_save.reset_mock()
        coordinator.async_refresh.reset_mock()
        await action()
        coordinator.async_refresh.assert_awaited_once()
        coordinator._store.async_save.assert_awaited_once()  # the action marked the store dirty
        assert coordinator._store_dirty is False


async def test_override_and_manual_changes_use_the_default_duration(hass: HomeAssistant) -> None:
    coordinator = (await _setup(hass, options={"override_default_min": 45, **RELEASED})).runtime_data
    await coordinator.async_set_override("badezimmer", 22.0, None)
    override = coordinator.overrides["badezimmer"]
    assert override.until_ts - override.set_at_ts == 45 * 60.0

    coordinator.overrides.clear()
    coordinator.room_control_mode = "active"
    coordinator.room_ctrl_memory["wohnzimmer"] = RoomCtrlMemory(last_sent_target=18.5, confirmed=True)
    snapshot = coordinator.data["snapshot"]
    room = replace(snapshot.rooms["wohnzimmer"], trv_target_temp=20.0)
    snapshot = replace(snapshot, rooms={**snapshot.rooms, "wohnzimmer": room})
    adopted = coordinator._adopt_manual_thermostat_changes(snapshot, 1000.0)
    assert coordinator.overrides["wohnzimmer"] == Override(20.0, 1000.0 + 45 * 60.0, 1000.0)
    assert adopted.rooms["wohnzimmer"].override == coordinator.overrides["wohnzimmer"]


async def test_ebusd_topics(hass: HomeAssistant) -> None:
    publish = async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options={"boiler_ebus_circuit": " bai2 "})).runtime_data
    assert coordinator.boiler_setmode_topic == "ebusd/bai2/SetMode/set"
    publish.clear()
    coordinator._ebus_priority_ts = None
    await coordinator._async_keep_boiler_readings_fresh(0.0, 1_000_000.0)
    assert [(c.data["topic"], c.data["payload"]) for c in publish] == [
        ("ebusd/bai2/FlowTemp/get", "?1"),
        ("ebusd/bai2/ReturnTemp/get", "?1"),
        ("ebusd/bai2/Status01/get", "?1"),
        ("ebusd/bai2/WP/get", "?1"),
        ("ebusd/bai2/Statenumber/get", "?1"),
    ]


@pytest.mark.parametrize("failing", ["set_hvac_mode", "set_temperature"])
async def test_a_failed_heat_pump_command_does_not_count_as_carried_out(hass: HomeAssistant, freezer, failing: str) -> None:
    """Kept as sent, the start would read as a change by hand two minutes later: 3 h hands off."""
    import time
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError

    unit = {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0}
    answering = {"ok": False}
    sent: list[tuple[str, dict]] = []

    async def climate_service(call) -> None:
        if call.service == failing and not answering["ok"]:
            raise HomeAssistantError("the unit did not answer")
        sent.append((call.service, dict(call.data)))
        if call.service == "set_hvac_mode":
            hass.states.async_set("climate.midea", call.data["hvac_mode"], unit)

    for service in ("set_hvac_mode", "set_temperature", "set_fan_mode"):
        hass.services.async_register("climate", service, climate_service)
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("climate.midea", "off", unit)
    coordinator.set_control_mode("active")
    now = time.time()
    coordinator.control_memory = ControlMemory(last_hvac_mode="off", last_command_ts=now - 7200, stopped_since_ts=now - 7200)

    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].action == "start"
    assert coordinator.control_memory.running_since_ts is None  # the start did not happen
    assert coordinator.control_memory.last_command_ts == now - 7200

    answering["ok"] = True
    sent.clear()
    freezer.tick(timedelta(seconds=180))
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].action == "start"  # sent again, not taken for a hand on the remote
    assert ("set_hvac_mode", {"entity_id": "climate.midea", "hvac_mode": "heat"}) in sent
    assert coordinator.control_memory.running_since_ts is not None and coordinator.control_memory.last_hvac_mode == "heat"
