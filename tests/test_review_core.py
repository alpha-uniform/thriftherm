"""Findings of the code review of the core (coordinator, executors, lifecycle), each as a test that failed until it
was fixed (2026-11-13).

Every test asserts the behaviour that would be right; none of them is expected to pass on the code as reviewed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm.engines.heat_pump_control import ControlMemory

from .conftest import climate_loaded
from .test_integration import _boosting, _setup

RELEASED = {"midea_allow_active_control": True, "boiler_allow_active_control": True, "room_allow_active_control": True}
UNIT = {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0,
        "hvac_modes": ["off", "heat", "fan_only"]}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_a_unit_left_in_a_quick_heat_up_is_set_down_when_the_evaluation_keeps_failing(
    hass: HomeAssistant, monkeypatch, freezer
) -> None:
    """Finding 1 (coordinator.py:1223-1229): a cycle whose evaluation raises hands the boiler back and sends nothing else.

    A heat pump our last command left in a quick heat-up (30 °C, the fan up) or a drying keeps that setpoint for as
    long as the evaluation fails: their time limits run in the evaluation only. The boiler is let go of on this path,
    the unit is not, though `async_let_go_of_heat_pump` exists for exactly that.
    """
    _, coordinator, set_temperature = await _boosting(hass)

    def broken(_snapshot):
        raise RuntimeError("an engine raised")

    monkeypatch.setattr(coordinator, "_evaluate", broken)
    for _ in range(3):
        freezer.tick(timedelta(seconds=60))
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False  # the cycles did fail
    assert [c.data["temperature"] for c in set_temperature] == [17.0]


async def test_plan_only_chosen_while_a_quick_heat_up_start_goes_out_does_not_leave_the_unit_at_30(hass: HomeAssistant) -> None:
    """Finding 2 (coordinator.py:978-985, 1244-1257; executors.py:186-191): a command under way is finished after the
    control was taken back.

    `set_control_mode` lets go at once, in a task of its own: it finds the unit not yet as the command will leave it
    and sends nothing. The cycle then sends the rest of its start (the fan stage, 30 °C) with the control in Observe only,
    and nothing sets the unit down afterwards.
    """
    entered, release = asyncio.Event(), asyncio.Event()

    async def climate_service(call) -> None:
        if call.service == "set_hvac_mode":
            entered.set()
            await release.wait()  # the unit takes its time, as a Midea over LAN does
        shown = hass.states.get("climate.midea")
        attrs = {**shown.attributes, **({"temperature": call.data["temperature"]} if call.service == "set_temperature" else {})}
        hass.states.async_set("climate.midea", call.data.get("hvac_mode", shown.state), attrs)

    for service in ("set_hvac_mode", "set_temperature", "set_fan_mode"):
        await climate_loaded(hass)
        hass.services.async_register("climate", service, climate_service)
    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    hass.states.async_set("climate.midea", "off", UNIT)
    coordinator.set_control_mode("active")
    ago = time.time() - 7200
    coordinator.control_memory = ControlMemory(last_hvac_mode="off", last_command_ts=ago, stopped_since_ts=ago)
    coordinator.boosts["badezimmer"] = time.time() + 1800

    cycle = hass.async_create_task(coordinator.async_refresh())
    await asyncio.wait_for(entered.wait(), 5)  # the start of the quick heat-up is on its way
    coordinator.set_control_mode("shadow")  # Observe only, chosen in the dashboard at that moment
    await asyncio.sleep(0)
    await asyncio.sleep(0)  # the let-go task has looked at the unit
    release.set()
    await cycle
    await hass.async_block_till_done()

    unit = hass.states.get("climate.midea")
    assert coordinator.control_mode == "shadow"
    assert (unit.state, unit.attributes["temperature"]) != ("heat", 30.0), "left heating at 30 °C with nobody in control"


async def test_letting_go_of_a_boosted_unit_puts_its_fan_back(hass: HomeAssistant) -> None:
    """Finding 3 (coordinator.py:1026): the let-go sets the setpoint down to the floor and leaves the fan turned up.

    A quick heat-up or a drying turns the fan up and keeps on record what to put back (`fan_turned_up_from`). Plan
    only, Off, a disable or a delete mid-way send 17 °C alone: the fan stays on the stage of the quick heat-up, and
    once the control is back that stage reads as one chosen by hand and stays.
    """
    _, coordinator, set_temperature = await _boosting(hass)
    await climate_loaded(hass)
    set_fan_mode = async_mock_service(hass, "climate", "set_fan_mode")
    shown = {**hass.states.get("climate.midea").attributes, "fan_modes": ["auto", "low", "high", "full"], "fan_mode": "high"}
    hass.states.async_set("climate.midea", "heat", shown)
    await hass.async_block_till_done()
    coordinator.control_memory = replace(
        coordinator.control_memory, fan_turned_up_from="auto", fan_turned_up_to="high", fan_normal="auto"
    )
    set_temperature.clear()

    coordinator.set_control_mode("shadow")
    await hass.async_block_till_done()

    assert [c.data["temperature"] for c in set_temperature] == [17.0]
    assert [c.data["fan_mode"] for c in set_fan_mode] == ["auto"]


# ---- the review of these fixes (2026-11-13) --------------------------------------------------------------------
async def test_letting_go_sets_the_setpoint_down_although_the_unit_refuses_the_fan_stage(hass: HomeAssistant) -> None:
    """The fan went out before the setpoint, and a refused call ended the command: a unit that did not take the fan
    stage back stayed at 30 °C, where it had been set down before the fan was added to the let-go."""
    _, coordinator, _ = await _boosting(hass)
    sent: list[float] = []

    async def refuse(call) -> None:
        raise HomeAssistantError("no such fan stage")

    async def take(call) -> None:
        sent.append(call.data["temperature"])

    await climate_loaded(hass)
    hass.services.async_register("climate", "set_fan_mode", refuse)
    hass.services.async_register("climate", "set_temperature", take)
    shown = {**hass.states.get("climate.midea").attributes, "fan_modes": ["auto", "low", "high", "full"], "fan_mode": "high"}
    hass.states.async_set("climate.midea", "heat", shown)
    await hass.async_block_till_done()
    coordinator.control_memory = replace(coordinator.control_memory, fan_turned_up_from="auto", fan_turned_up_to="high")
    sent.clear()

    coordinator.set_control_mode("shadow")
    await hass.async_block_till_done()

    assert sent == [17.0]


async def test_plan_only_lets_go_of_a_quick_heat_up_whose_end_was_planned_but_not_sent(hass: HomeAssistant) -> None:
    """The cycle under way had planned the end of the quick heat-up; Observe only came before its command went out, so
    nothing was sent, and the memory of that plan no longer said 30 °C: the unit was not let go of."""
    _, coordinator, set_temperature = await _boosting(hass)
    left_at_30 = coordinator.control_memory
    coordinator._control_memory_before = left_at_30  # as the cycle found it
    coordinator.control_memory = replace(left_at_30, last_target=21.0)  # as the cycle planned it, unsent

    coordinator.set_control_mode("shadow")
    await hass.async_block_till_done()

    assert [c.data["temperature"] for c in set_temperature] == [17.0]


async def test_a_setup_that_fails_after_a_restart_lets_go_of_a_unit_left_in_a_quick_heat_up(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Three failing cycles in a row let go, but a first refresh that fails ends the setup, and every new try begins
    with a new coordinator: after a restart of Home Assistant the unit stayed at 30 °C for as long as it failed."""
    from custom_components.thriftherm import PENDING_LET_GO
    from custom_components.thriftherm.coordinator import ThrifthermCoordinator

    entry, coordinator, set_temperature = await _boosting(hass)
    await coordinator.async_save_store(force=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.get(PENDING_LET_GO, {}).clear()  # Home Assistant restarted: nothing is parked
    set_temperature.clear()

    def broken(self, _snapshot):
        raise RuntimeError("an engine raised")

    monkeypatch.setattr(ThrifthermCoordinator, "_evaluate", broken)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert [c.data["temperature"] for c in set_temperature] == [17.0]


async def test_a_gas_flow_that_only_changes_its_value_while_burning_asks_for_no_cycle(hass: HomeAssistant) -> None:
    """Finding B17, measured on the test installation (3-13 Nov 2026): the gas flow changed 334 times in 70 minutes of
    burning, a cycle every twelve seconds instead of every sixty. The log of the boiler's load (720 rows, one per
    cycle) then holds two and a half hours of a heating day instead of twelve. A burner that starts or stops is
    still seen at once; a flow that is merely another one waits for the regular cycle."""
    coordinator = (await _setup(hass)).runtime_data
    asked: list[int] = []

    async def count() -> None:
        asked.append(1)

    coordinator.async_request_refresh = count
    for flow, cycles in (("1.3", 0), ("1.753", 0), ("0.0", 1), ("1.052", 2), ("unavailable", 3), ("1.2", 4)):
        hass.states.async_set("sensor.gas_flow", flow)
        await hass.async_block_till_done()
        assert len(asked) == cycles, f"gas flow {flow}"
    # A meter that ramps, or rests a little above nought: the burner starts where the gas passes what counts as
    # burning (1 kW), not where the flow leaves 0. Told by 0 alone, a burn of under a minute was not seen at all.
    asked.clear()
    for flow, cycles in (("0.001", 1), ("0.02", 1), ("0.4", 2), ("1.3", 2), ("0.03", 3), ("0.001", 3)):
        hass.states.async_set("sensor.gas_flow", flow)
        await hass.async_block_till_done()
        assert len(asked) == cycles, f"gas flow {flow}"


async def test_a_room_thermostat_that_blocks_or_starts_heating_asks_for_a_cycle(hass: HomeAssistant) -> None:
    """Issue #8: Better Thermostat 2.0 may publish a block without a control cycle; the boiler hears it at once. Other
    attribute updates of the room thermostat still wait for the regular cycle."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN

    from .test_integration import _entry_data, _set_states

    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "hvac_action": "idle", "heating_blocked_by": None})
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath_bt"
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    asked: list[int] = []

    async def count() -> None:
        asked.append(1)

    coordinator.async_request_refresh = count
    steps = (
        ({"hvac_action": "idle", "heating_blocked_by": None, "current_temperature": 19.0}, 0),
        ({"hvac_action": "heating", "heating_blocked_by": None, "current_temperature": 19.0}, 1),
        ({"hvac_action": "heating", "heating_blocked_by": "valve_maintenance", "current_temperature": 19.0}, 2),
        ({"hvac_action": "heating", "heating_blocked_by": "valve_maintenance", "current_temperature": 19.1}, 2),
        ({"hvac_action": "heating", "heating_blocked_by": None, "current_temperature": 19.1}, 3),
    )
    for attrs, cycles in steps:
        hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, **attrs})
        await hass.async_block_till_done()
        assert len(asked) == cycles, attrs
