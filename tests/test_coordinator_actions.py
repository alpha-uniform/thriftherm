"""What the coordinator's user actions do: allowed modes, log lines, memory resets, saving."""

from __future__ import annotations

import asyncio
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
    assert override.until_ts - override.set_at_ts == 45 * 60.0 and override.by == "action"

    coordinator.overrides.clear()
    coordinator.room_control_mode = "active"
    coordinator.room_ctrl_memory["wohnzimmer"] = RoomCtrlMemory(last_sent_target=18.5, confirmed=True)
    snapshot = coordinator.data["snapshot"]
    room = replace(snapshot.rooms["wohnzimmer"], trv_target_temp=20.0)
    snapshot = replace(snapshot, rooms={**snapshot.rooms, "wohnzimmer": room})
    adopted = coordinator._adopt_manual_thermostat_changes(snapshot, 1000.0)
    assert coordinator.overrides["wohnzimmer"] == Override(20.0, 1000.0 + 45 * 60.0, 1000.0, "device")
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

    # the unit shows another setpoint than the one asked for: one it has already is not sent again
    unit = {"temperature": 20, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0}
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
    # sent again, not taken for a hand on the remote; a unit the first call did switch to heating
    # is taken over and only gets what is still missing
    assert coordinator.data["midea_command"].action == ("start" if failing == "set_hvac_mode" else "adjust")
    target = coordinator.data["midea_command"].target_temp
    assert ("set_temperature", {"entity_id": "climate.midea", "temperature": target}) in sent
    assert hass.states.get("climate.midea").state == "heat"
    assert coordinator.control_memory.running_since_ts is not None and coordinator.control_memory.last_hvac_mode == "heat"


async def test_the_heat_pump_setpoint_goes_last_and_an_unchanged_fan_mode_is_not_sent(hass: HomeAssistant) -> None:
    """Seen 2026-10-11: setpoint, then fan mode right after it, and the unit kept its old setpoint."""
    from custom_components.thriftherm import executors
    from custom_components.thriftherm.models import HeatPumpCommand

    sent: list[tuple[str, dict]] = []

    async def climate_service(call) -> None:
        sent.append((call.service, {k: v for k, v in call.data.items() if k != "entity_id"}))

    for service in ("set_hvac_mode", "set_temperature", "set_fan_mode"):
        hass.services.async_register("climate", service, climate_service)
    command = HeatPumpCommand(plan="heat", action="adjust", hvac_mode="heat", target_temp=18.0, fan_mode="auto", load="eco", reason="x")

    hass.states.async_set("climate.unit", "heat", {"temperature": 23, "fan_mode": "auto"})
    assert await executors.async_execute_heat_pump(hass, "climate.unit", command)
    assert sent == [("set_temperature", {"temperature": 18.0})]

    sent.clear()
    hass.states.async_set("climate.unit", "heat", {"temperature": 23, "fan_mode": "high"})
    assert await executors.async_execute_heat_pump(hass, "climate.unit", command)
    assert sent == [("set_fan_mode", {"fan_mode": "auto"}), ("set_temperature", {"temperature": 18.0})]


async def test_the_setpoint_waits_until_the_unit_shows_the_fan_stage(hass: HomeAssistant, monkeypatch) -> None:
    """Seen 2026-10-13: a drying asked for "full" and 22 °C in one go, and the unit stayed on "auto".

    The Midea sends its whole state with every call, as it knew it before the call: the setpoint
    right after the fan stage took the old fan stage along. A setpoint the unit has is not sent.
    """
    from custom_components.thriftherm import executors
    from custom_components.thriftherm.models import HeatPumpCommand

    monkeypatch.setattr(executors, "SHOWN_WAIT_S", 5.0)
    unit = "climate.unit"
    shown_at_setpoint: list[str] = []

    async def set_fan_mode(call) -> None:
        async def answer() -> None:  # the unit shows it a moment later
            await asyncio.sleep(0.3)
            hass.states.async_set(unit, "heat", {**hass.states.get(unit).attributes, "fan_mode": call.data["fan_mode"]})

        hass.async_create_task(answer())

    async def set_temperature(call) -> None:
        shown_at_setpoint.append(hass.states.get(unit).attributes["fan_mode"])

    hass.services.async_register("climate", "set_fan_mode", set_fan_mode)
    hass.services.async_register("climate", "set_temperature", set_temperature)
    heat = {"plan": "heat", "hvac_mode": "heat", "load": "eco", "reason": "x"}
    command = HeatPumpCommand(action="adjust", target_temp=22.0, fan_mode="full", **heat)

    hass.states.async_set(unit, "heat", {"temperature": 18.5, "fan_mode": "auto", "fan_modes": ["auto", "full"]})
    assert await executors.async_execute_heat_pump(hass, unit, command)
    assert shown_at_setpoint == ["full"]

    shown_at_setpoint.clear()
    hass.states.async_set(unit, "heat", {"temperature": 22.0, "fan_mode": "auto", "fan_modes": ["auto", "full"]})
    assert await executors.async_execute_heat_pump(hass, unit, command)
    assert shown_at_setpoint == []  # the fan stage alone
    await hass.async_block_till_done()
    assert hass.states.get(unit).attributes["fan_mode"] == "full"


async def test_a_unit_gets_only_modes_and_fan_stages_it_offers(hass: HomeAssistant) -> None:
    """Home Assistant refuses the others, and a refused call ended the whole command."""
    from custom_components.thriftherm import executors
    from custom_components.thriftherm.models import HeatPumpCommand

    sent = {name: async_mock_service(hass, "climate", name) for name in ("set_hvac_mode", "set_fan_mode", "set_temperature")}
    unit = "climate.other_unit"
    hass.states.async_set(unit, "off", {"hvac_modes": ["off", "heat", "cool"], "fan_modes": ["1", "2", "3"], "fan_mode": "2"})
    heat = {"plan": "heat", "hvac_mode": "heat", "load": "eco", "reason": "x"}
    start = HeatPumpCommand(action="start", target_temp=21.0, fan_mode="auto", **heat)
    assert await executors.async_execute_heat_pump(hass, unit, start)
    assert sent["set_fan_mode"] == [] and sent["set_temperature"][-1].data["temperature"] == 21.0  # no "auto" there
    off = {"plan": "off", "target_temp": None, "fan_mode": None, "load": None, "reason": "x"}
    stop = HeatPumpCommand(action="stop", hvac_mode="fan_only", **off)
    assert await executors.async_execute_heat_pump(hass, unit, stop)
    assert sent["set_hvac_mode"][-1].data["hvac_mode"] == "off"  # no fan-only there: switched off at once

    midea = "climate.midea_like"
    offers = {"hvac_modes": ["off", "heat", "fan_only"], "fan_modes": ["auto", "high", "full"], "fan_mode": "auto"}
    hass.states.async_set(midea, "heat", offers)
    assert await executors.async_execute_heat_pump(hass, midea, stop)
    assert sent["set_hvac_mode"][-1].data == {"entity_id": midea, "hvac_mode": "fan_only"}
    adjust = HeatPumpCommand(action="adjust", target_temp=22.0, fan_mode="full", **heat)
    assert await executors.async_execute_heat_pump(hass, midea, adjust)
    assert sent["set_fan_mode"][-1].data == {"entity_id": midea, "fan_mode": "full"}


UNIT = {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0,
        "hvac_modes": ["off", "heat", "fan_only"], "fan_modes": ["auto", "high", "full"], "fan_mode": "auto"}


async def test_a_start_that_keeps_failing_is_not_tried_again_every_cycle(hass: HomeAssistant, freezer) -> None:
    """Sent again each cycle, every try held the cycle up for its timeout; and each was on record as sent."""
    import time
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError

    tries: list[str] = []

    async def no_answer(call) -> None:
        tries.append(call.service)
        raise HomeAssistantError("the unit did not answer")

    for service in ("set_hvac_mode", "set_temperature", "set_fan_mode"):
        hass.services.async_register("climate", service, no_answer)
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("climate.midea", "off", UNIT)
    coordinator.set_control_mode("active")
    now = time.time()
    coordinator.control_memory = ControlMemory(last_hvac_mode="off", last_command_ts=now - 7200, stopped_since_ts=now - 7200)
    tries.clear()
    for _ in range(4):  # four cycles, a minute apart
        await coordinator.async_refresh()
        freezer.tick(timedelta(seconds=60))
    assert len(tries) == 2  # at once, and again after two minutes: not four times
    assert coordinator.control_memory.running_since_ts is None  # and the unit does not count as running
    log = [entry for entry in coordinator.plan_log.command_log if entry["action"] == "start"]
    assert len(log) == 1 and log[0]["sent"] is False  # on record once, and not as sent
    for _ in range(4):  # the next try comes after four more minutes
        await coordinator.async_refresh()
        freezer.tick(timedelta(seconds=60))
    assert len(tries) == 3


async def test_a_stop_that_went_through_although_it_reported_a_failure_ends_switched_off(hass: HomeAssistant, freezer) -> None:
    """Rolled back, the unit stayed in fan only for good: its own run-on was taken for somebody's mode."""
    import time
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError

    lost = {"answers": 1}

    async def set_mode(call) -> None:
        hass.states.async_set("climate.midea", call.data["hvac_mode"], UNIT, context=call.context)  # the unit does switch
        if lost["answers"]:
            lost["answers"] -= 1
            raise HomeAssistantError("no answer from the unit")

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    coordinator = (await _setup(hass, options={**RELEASED, "midea_fan_run_on_min": 15})).runtime_data
    hass.states.async_set("climate.midea", "heat", UNIT)
    coordinator.set_control_mode("active")
    now = time.time()
    long_ago = now - 7200
    heating = {"last_hvac_mode": "heat", "last_target": 22.0}
    coordinator.control_memory = ControlMemory(running_since_ts=long_ago, last_command_ts=long_ago, **heating)
    hass.states.async_set("sensor.bath_temp", "24.0")  # warm enough: the unit is to stop
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].action == "stop"
    assert hass.states.get("climate.midea").state == "fan_only" and coordinator.control_memory.fan_until_ts is not None

    freezer.tick(timedelta(minutes=16))
    await coordinator.async_refresh()
    assert hass.states.get("climate.midea").state == "off"  # the run-on was ours, and it is finished
    assert coordinator.data["midea_command"].reason != "manual_mode_fan_only"


async def test_a_unit_switched_off_by_hand_dries_no_room_while_it_is_left_alone(hass: HomeAssistant) -> None:
    """During the time in hand nothing is sent to the unit: a drying would raise the target for the radiator."""
    import time

    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("climate.midea", "off", UNIT)
    coordinator.set_control_mode("active")
    await coordinator.async_refresh()
    assert coordinator.data["midea"].heating_available is True
    coordinator.control_memory = replace(coordinator.control_memory, manual_until_ts=time.time() + 3600, manual_by="person")
    await coordinator.async_refresh()
    assert coordinator.data["midea"].heating_available is False
    hass.states.async_set("climate.midea", "heat", UNIT)  # left heating by hand: it can dry
    await coordinator.async_refresh()
    assert coordinator.data["midea"].heating_available is True


async def test_our_own_fan_only_is_still_ours_after_a_restart(hass: HomeAssistant) -> None:
    """The "off" after the fan run-on, not taken by the unit, and a restart more than two minutes later:
    nothing told any more that nobody had touched it, and the unit stayed in fan only as somebody's mode."""
    import time

    from custom_components.thriftherm import persistence

    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("climate.midea", "fan_only", UNIT)
    coordinator.set_control_mode("active")
    now = time.time()
    off_sent = ControlMemory(stopped_since_ts=now - 1500, last_command_ts=now - 600, last_hvac_mode="off")
    stored = {"entity": "climate.midea", "since": now - 3600, "last": ["fan_only", 22], "change": None}

    coordinator.control_memory = off_sent  # as today after the restart: watched since the start only
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].reason == "manual_mode_fan_only"

    coordinator.control_memory = off_sent  # with what the store kept: the unit stands as last seen, nobody touched it
    coordinator._stored_heat_pump_watch = dict(stored)
    coordinator._resume_heat_pump_watch()
    await coordinator.async_refresh()
    command = coordinator.data["midea_command"]
    assert (command.action, command.hvac_mode, command.reason) == ("stop", "off", "not_carried_out")
    assert persistence.to_store(coordinator)["heat_pump_watch"]["since"] == now - 3600  # and the watch goes on

    coordinator.control_memory = off_sent  # the unit was off when last seen: somebody chose fan only meanwhile
    coordinator._stored_heat_pump_watch = {**stored, "last": ["off", 22]}
    coordinator._resume_heat_pump_watch()
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].reason == "manual_mode_fan_only"


# ---- what the test day of 2026-10-13 showed --------------------------------------------------------
FOLLOWING = {**RELEASED, "midea_follow_rooms": True}  # the bathroom's 21 °C plus one degree: the unit gets 22 °C


def _climate_calls(hass: HomeAssistant) -> dict[str, list]:
    return {name: async_mock_service(hass, "climate", name) for name in ("set_hvac_mode", "set_fan_mode", "set_temperature")}


async def test_a_fan_stage_chosen_by_hand_stays_when_a_setpoint_goes_out(hass: HomeAssistant) -> None:
    """Seen 2026-10-13: "full" chosen by hand stayed for 45 minutes, until the next setpoint of ours put "auto" back."""
    import time

    sent = _climate_calls(hass)
    coordinator = (await _setup(hass, options=FOLLOWING)).runtime_data
    hass.states.async_set("climate.midea", "heat", {**UNIT, "temperature": 20, "fan_mode": "full"})
    coordinator.set_control_mode("active")
    long_ago = time.time() - 7200
    coordinator.control_memory = ControlMemory(
        running_since_ts=long_ago, last_command_ts=long_ago, last_hvac_mode="heat", last_target=20.0
    )
    await coordinator.async_refresh()
    command = coordinator.data["midea_command"]
    assert (command.action, command.target_temp, command.fan_mode) == ("adjust", 22.0, None)
    assert [call.data["temperature"] for call in sent["set_temperature"]] == [22.0] and sent["set_fan_mode"] == []
    assert coordinator.data["command_log"][-1]["fan"] is None and coordinator.control_memory.manual_until_ts is None


async def test_a_unit_not_loaded_yet_after_a_restart_keeps_its_run_and_gets_nothing_sent(hass: HomeAssistant) -> None:
    """Seen 2026-10-13 after three restarts: taken over anew, an "adjust" to what it had already, the lift clock reset."""
    import time

    sent = _climate_calls(hass)
    coordinator = (await _setup(hass, options=FOLLOWING)).runtime_data
    coordinator.set_control_mode("active")
    long_ago = time.time() - 7200
    running = ControlMemory(
        running_since_ts=long_ago, last_command_ts=long_ago, last_offset_review_ts=long_ago,  # the lift clock: its last step
        last_hvac_mode="heat", last_target=22.0,
    )
    coordinator.control_memory = running
    hass.states.async_set("climate.midea", "unavailable")  # its entity is not loaded yet
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].reason == "midea_unavailable" and coordinator.control_memory == running
    hass.states.async_set("climate.midea", "heat", UNIT)  # back as it was left: 22 °C
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].action == "none"
    memory = coordinator.control_memory
    assert (memory.running_since_ts, memory.last_offset_review_ts) == (long_ago, long_ago)  # the same run, the same clock
    assert not any(entry["sent"] for entry in coordinator.data["command_log"]) and not any(sent.values())


async def test_the_boiler_does_not_take_over_a_room_while_the_running_unit_is_not_loaded_yet(hass: HomeAssistant) -> None:
    """Review 2026-10-14: the unit's run was kept, but the boiler was switched to heat for the bathroom it serves,
    and held there for its minimum time in state after the unit was back."""
    import time

    _climate_calls(hass)
    async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options=FOLLOWING)).runtime_data
    coordinator.set_control_mode("active")
    coordinator.set_boiler_control_mode("active")
    long_ago = time.time() - 7200
    coordinator.control_memory = ControlMemory(
        running_since_ts=long_ago, last_command_ts=long_ago, last_offset_review_ts=long_ago,
        last_hvac_mode="heat", last_target=22.0,
    )
    coordinator.boiler_memory = replace(coordinator.boiler_memory, state="block", state_since_ts=0.0)
    hass.states.async_set("sensor.bath_temp", "20.4")  # 0.6 K below its 21 °C: it calls
    hass.states.async_set("sensor.living_temp", "19.0")
    hass.states.async_set("climate.midea", "unavailable")  # the rooms are known, the unit's entity is not loaded yet
    await coordinator.async_refresh()
    boiler = coordinator.data["boiler_command"]
    assert (boiler.plan, boiler.blockers) == ("hands_off", ("waiting_for_room_data",))
    assert coordinator.boiler_memory.state == "block"  # what was in force before the restart stays

    hass.states.async_set("climate.midea", "heat", UNIT)  # back, heating as before
    await coordinator.async_refresh()
    assert coordinator.data["midea_command"].plan == "heat" and coordinator.data["boiler_command"].plan == "block"


async def test_a_command_the_unit_has_all_of_already_is_not_on_record_as_sent(hass: HomeAssistant) -> None:
    """The record of commands lists what went out: an "adjust" without a call stood there as sent."""
    import time

    sent = _climate_calls(hass)
    coordinator = (await _setup(hass, options=FOLLOWING)).runtime_data
    hass.states.async_set("climate.midea", "heat", UNIT)  # 22 °C, what the bathroom asks for
    coordinator.set_control_mode("active")
    # running, and what was sent is not known (so a command the unit had not carried out is forgotten)
    coordinator.control_memory = ControlMemory(running_since_ts=time.time() - 7200, last_hvac_mode="heat")
    await coordinator.async_refresh()
    assert (coordinator.data["midea_command"].action, coordinator.data["midea_command"].target_temp) == ("adjust", 22.0)
    assert not any(sent.values()) and [entry for entry in coordinator.data["command_log"] if entry["action"] == "adjust"] == []
    assert coordinator.control_memory.last_target == 22.0  # carried out all the same: the unit shows it


async def test_a_heat_pump_that_runs_through_is_named_instead_of_no_room_needing_heat(hass: HomeAssistant) -> None:
    """Read as a fault three times on 2026-10-13: "no room needs heat" while the unit heated, as it is to."""
    import time

    _climate_calls(hass)
    coordinator = (await _setup(hass, options=FOLLOWING)).runtime_data
    hass.states.async_set("climate.midea", "heat", UNIT)
    hass.states.async_set("sensor.bath_temp", "21.5")  # every room warm: none calls
    hass.states.async_set("sensor.living_temp", "19.0")
    coordinator.set_control_mode("active")
    long_ago = time.time() - 7200
    coordinator.control_memory = ControlMemory(
        running_since_ts=long_ago, last_command_ts=long_ago, last_hvac_mode="heat", last_target=22.0
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["midea_command"].plan == "heat"
    shown = hass.states.get("sensor.thriftherm_decision_reason").attributes
    assert "heat_pump_runs_through" in shown["reason_codes"] and "no_demand" not in shown["reason_codes"]
    assert "heat pump runs through, setpoint 22.0 °C" in shown["reasons"]

    coordinator.set_control_mode("shadow")  # it only plans while the boiler really acts: no room needs heat, as before
    coordinator.set_boiler_control_mode("active")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "no_demand" in hass.states.get("sensor.thriftherm_decision_reason").attributes["reason_codes"]

