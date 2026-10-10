"""What happens when sensors fail: measured on the live system, pinned down here.

The three days of active operation delivered most of these for real (a room sensor
dropping out, the eBUS signal disappearing for 22 hours, Zigbee restarting). These
tests keep the reactions from drifting.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant

from custom_components.thriftherm.adapters.inputs import ROOM_TEMP_MAX_AGE_S
from custom_components.thriftherm.engines import boiler_control, room as room_engine, safety
from custom_components.thriftherm.engines.boiler_control import curve_flow

from .conftest import PARAMS, room_config, room_state, snapshot
from .test_integration import _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ------------------------------------------------------------------ pure engines
def test_without_an_outdoor_temperature_the_curve_uses_its_middle():
    # the cold point (55 °C) would heat as in deep winter just because a sensor died
    assert curve_flow(None, PARAMS) == pytest.approx((PARAMS.boiler_curve_flow_cold + PARAMS.boiler_curve_flow_warm) / 2)
    assert curve_flow(-10.0, PARAMS) == PARAMS.boiler_curve_flow_cold


def test_one_room_sensor_missing_degrades_but_keeps_control(two_rooms):
    two_rooms["badezimmer"] = room_state(two_rooms["badezimmer"].config, temp=None, trv_local=19.5)
    snap = snapshot(two_rooms)
    rooms = {k: room_engine.evaluate_room(s, snap.now, snap.mode, PARAMS) for k, s in snap.rooms.items()}
    res = safety.evaluate(snap, rooms)
    assert res.state == "degraded"
    assert "room_using_trv_fallback:badezimmer" in res.issues
    assert rooms["badezimmer"].temperature == 19.5  # the thermostat's own reading stands in


def test_every_room_sensor_missing_is_a_fallback_and_stops_commands(two_rooms):
    for key, state in two_rooms.items():
        two_rooms[key] = room_state(state.config, temp=None)
    snap = snapshot(two_rooms)
    rooms = {k: room_engine.evaluate_room(s, snap.now, snap.mode, PARAMS) for k, s in snap.rooms.items()}
    res = safety.evaluate(snap, rooms)
    assert res.state == "fallback"

    cmd, _ = boiler_control.decide(
        boiler_control.BoilerInputs(
            now_ts=1_000_000.0, control_mode="active", op_mode="auto", safety_state=res.state, boiler_available=True,
            rooms=tuple(rooms.values()), frost_rooms=(), advice_source="boiler", outdoor_c=5.0, params=PARAMS,
        ),
        boiler_control.BoilerMemory(),
    )
    assert cmd.plan == "hands_off" and cmd.payload is None
    assert "safety_fallback" in cmd.blockers


def test_a_room_sensor_that_went_quiet_counts_as_missing():
    assert ROOM_TEMP_MAX_AGE_S == 6 * 3600.0
    cfg = room_config("badezimmer")
    fresh = room_engine.evaluate_room(room_state(cfg, temp=20.0), snapshot({}).now, "auto", PARAMS)
    assert fresh.temperature == 20.0


# ------------------------------------------------------------------ live integration
async def test_outdoor_sensor_falls_back_to_the_weather_service(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "cloudy", {"temperature": 7.5, "humidity": 80})
    entry = await _setup(hass, options={"weather_entity": "weather.home"})
    hass.states.async_remove("sensor.outdoor")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    outdoor = hass.states.get("sensor.thriftherm_outdoor_temperature_selected_source")
    assert float(outdoor.state) == 7.5
    assert outdoor.attributes["source"] == "weather:weather.home"


async def test_the_last_known_outdoor_temperature_stands_in(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)  # sensor.outdoor is 4 °C
    coordinator = entry.runtime_data
    assert coordinator._last_outdoor is not None and coordinator._last_outdoor[0] == 4.0

    hass.states.async_remove("sensor.outdoor")  # no sensor, no weather entity configured
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.thriftherm_boiler_flow_setpoint_planned").state) == pytest.approx(
        curve_flow(4.0, coordinator.data["snapshot"].params), abs=8.1  # curve plus demand boost
    )
    assert hass.states.get("sensor.thriftherm_safety_state").state == "degraded"


async def test_a_missing_gas_meter_does_not_stop_the_control(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_remove("sensor.gas_flow")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.thriftherm_gas_power").state in ("unknown", "unavailable")
    assert hass.states.get("sensor.thriftherm_boiler_command").state in ("heat", "block")
    assert hass.states.get("sensor.thriftherm_safety_state").state == "ok"


@pytest.mark.parametrize(
    "options",
    [
        {"gas_price": 0.0},
        {"system_type": "district", "heat_price": 0.0},
        {"system_type": "district", "heat_consumption_share_pct": 0.0, "heat_ownership_share_pct": 0.0},
        {"system_type": "none", "gas_price": 0.0},
    ],
)
async def test_a_stored_price_of_0_does_not_stop_the_control(hass: HomeAssistant, options: dict) -> None:
    """Prices from before the form refused 0 only lose the cost comparison, never the control."""
    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import async_mock_service

    publish = async_mock_service(hass, "mqtt", "publish")
    entry = await _setup(hass, options={**options, "boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    assert entry.state is ConfigEntryState.LOADED
    assert coordinator.last_update_success
    if options.get("system_type", "gas") != "none":
        assert coordinator.data["econ"].break_even_cop is None
        assert coordinator.data["econ"].cheaper_source == "unknown"
    if coordinator.has_boiler:
        coordinator.set_boiler_control_mode("active")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.last_update_success
        assert [c for c in publish if c.data["topic"].endswith("/SetMode/set")]  # the boiler still gets its command


async def test_a_thermostat_that_raises_does_not_stop_the_other_rooms(hass: HomeAssistant, caplog) -> None:
    """Better Thermostat is foreign code: any exception from it used to end the cycle half way."""
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN

    from .test_integration import _entry_data, _set_states

    written: list[dict] = []

    async def set_temperature(call) -> None:
        if call.data["entity_id"] == "climate.bath_bt":
            raise TypeError("'NoneType' object is not subscriptable")
        written.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", set_temperature)
    _set_states(hass)
    data = _entry_data()
    for room, entity in zip(data[CONF_ROOMS], ("climate.bath_bt", "climate.living_bt"), strict=True):
        room["climate_entity"] = entity
        hass.states.async_set(entity, "heat", {"temperature": 17.0, "current_temperature": 18.0})
    entry = MockConfigEntry(
        domain=DOMAIN, data=data, options={"room_allow_active_control": True}, unique_id=DOMAIN, title="Thriftherm"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.set_room_control_mode("active")
    coordinator._store.async_save = AsyncMock()

    with patch("custom_components.thriftherm.issues.async_sync", AsyncMock()) as sync:
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert written == [{"entity_id": "climate.living_bt", "temperature": 18.5}]  # the bathroom came first and failed
    coordinator._store.async_save.assert_awaited_once()
    sync.assert_awaited_once()
    assert ("custom_components.thriftherm.executors", logging.WARNING,
            "thriftherm room badezimmer: setpoint 21.0 failed: 'NoneType' object is not subscriptable") in caplog.record_tuples


async def test_the_boiler_gets_its_setmode_before_a_silent_heat_pump(hass: HomeAssistant, monkeypatch, caplog) -> None:
    """Every call gives up after a timeout, and a hanging heat pump no longer holds up the boiler."""
    import asyncio

    from custom_components.thriftherm import executors

    monkeypatch.setattr(executors, "CALL_TIMEOUT_S", 0.05)
    order: list[str] = []

    async def publish(call) -> None:
        if call.data["topic"].endswith("/SetMode/set"):
            order.append("SetMode")

    async def no_answer(call) -> None:
        order.append(call.service)
        await asyncio.sleep(3600)

    hass.services.async_register("mqtt", "publish", publish)
    for service in ("set_hvac_mode", "set_temperature", "set_fan_mode"):
        hass.services.async_register("climate", service, no_answer)
    released = {"boiler_allow_active_control": True, "midea_allow_active_control": True}
    coordinator = (await _setup(hass, options=released)).runtime_data
    hass.states.async_set(
        "climate.midea", "off",
        {"temperature": 22, "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "error_code": 0},
    )
    coordinator.set_boiler_control_mode("active")
    coordinator.set_control_mode("active")
    order.clear()

    async with asyncio.timeout(10):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data["midea_command"].action == "start"
    assert order == ["SetMode", "set_hvac_mode"]  # the heat pump gave up at its first call
    assert coordinator.control_memory.running_since_ts is None
    assert ("custom_components.thriftherm.executors", logging.WARNING,
            "thriftherm heat pump command set_hvac_mode {'hvac_mode': 'heat'} failed: TimeoutError") in caplog.record_tuples


async def test_the_settings_stay_usable_while_a_cycle_fails(hass: HomeAssistant, monkeypatch) -> None:
    """Code audit 2026-09-28 (H3): a failed cycle made the selects, numbers, the return and the button
    unavailable, and Home Assistant skips unavailable entities in service calls."""
    from unittest.mock import Mock

    coordinator = (await _setup(hass)).runtime_data
    monkeypatch.setattr(coordinator, "_evaluate", Mock(side_effect=RuntimeError("broken")))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.thriftherm_recommended_heat_source").state == "unavailable"  # a result of the cycle
    for entity_id in (
        "select.thriftherm_operating_mode",
        "select.thriftherm_room_control",
        "select.thriftherm_boiler_control",
        "select.thriftherm_heat_pump_control",
        "number.thriftherm_badezimmer_comfort_temperature",
        "datetime.thriftherm_planned_return",
        "button.thriftherm_badezimmer_quick_heat_up",
    ):
        assert hass.states.get(entity_id).state != "unavailable", entity_id

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_operating_mode", "option": "off"}, blocking=True
    )
    assert coordinator.mode == "off"


async def test_a_failing_cycle_logs_its_traceback_once(hass: HomeAssistant, monkeypatch, caplog) -> None:
    """Code audit 2026-09-28 (H4): Home Assistant logs a failed cycle without its traceback (debug only)."""
    from unittest.mock import Mock

    coordinator = (await _setup(hass)).runtime_data
    evaluate = coordinator._evaluate

    def tracebacks() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info and r.exc_info[0] is RuntimeError]

    monkeypatch.setattr(coordinator, "_evaluate", Mock(side_effect=RuntimeError("broken")))
    for _ in range(3):
        await coordinator.async_refresh()
    assert len(tracebacks()) == 1  # the first of the series, not every cycle

    monkeypatch.setattr(coordinator, "_evaluate", evaluate)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    monkeypatch.setattr(coordinator, "_evaluate", Mock(side_effect=RuntimeError("broken again")))
    await coordinator.async_refresh()
    assert len(tracebacks()) == 2 and str(tracebacks()[-1].exc_info[1]) == "broken again"  # a new series


# ------------------------------------------------------------------ restart
def test_boiler_timers_survive_a_restart():
    mem = boiler_control.BoilerMemory(
        offset_k=1.5, state="heat", state_since_ts=1_000_000.0, last_flow=35.0, last_flow_ts=1_000_050.0,
        ramp_from=30.0, ramp_since_ts=999_900.0, hot_water_seen_ts=999_000.0, last_send_ts=1_000_060.0,
        samples=12, spread_ema=9.0,
    )
    back = boiler_control.BoilerMemory.from_storage(mem.to_storage())
    assert back.state == "heat" and back.state_since_ts == 1_000_000.0
    assert back.ramp_from == 30.0 and back.ramp_since_ts == 999_900.0
    assert back.last_flow == 35.0 and back.hot_water_seen_ts == 999_000.0
    assert back.offset_k == 1.5
    assert back.last_send_ts is None  # a SetMode goes out right after the restart
    assert back.samples == 0 and back.spread_ema is None  # a learning run starts fresh

    assert boiler_control.BoilerMemory.from_storage(None) == boiler_control.BoilerMemory()
    assert boiler_control.BoilerMemory.from_storage({"state": "nonsense", "state_since_ts": "x"}).state is None


def test_stored_memories_keep_their_format_and_drop_what_is_not_a_number():
    from custom_components.thriftherm.engines.heat_pump_control import ControlMemory
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    boiler = boiler_control.BoilerMemory.from_storage(
        {
            "offset_k": "warm", "adjustments": [1.0, "x", None, *range(2, 14)], "last_adjust_reason": 5, "state": "block",
            "last_flow": "35", "ramp_from": [], "burns": [[1, 61], ["a", 5], [9, 3], "x", [100, 160, 7]], "slope_k": "steep",
            "burn_starts": [1, 2], "burn_durations": [60],  # written by 0.5.4 and earlier: ignored
        }
    )
    assert boiler.offset_k == 0.0 and boiler.last_adjust_reason is None and boiler.state == "block"
    assert boiler.adjustments == (1.0, *map(float, range(2, 14)))  # all of them, even beyond ten
    assert boiler.last_flow == 35.0 and boiler.ramp_from is None
    assert boiler.burns == ((1.0, 61.0),) and boiler.slope_k == 0.0  # only a whole (start, end) pair counts
    assert boiler_control.BoilerMemory.from_storage({"offset_k": None}).offset_k == 0.0
    assert list(boiler.to_storage()) == [
        "offset_k", "slope_k", "adjustments", "last_adjust_reason", "state", "state_since_ts", "last_flow", "last_flow_ts",
        "ramp_from", "ramp_since_ts", "hot_water_seen_ts", "burn_since_ts", "burns",
    ]

    control = ControlMemory(offset_k=1.5, learning_bins=((2, 10.0),), running_since_ts=5.0, last_hvac_mode="heat", last_target=21.0)
    assert control.to_storage() == {
        "offset_k": 1.5, "learning_bins": [[2, 10.0]], "manual_until_ts": None, "running_since_ts": 5.0, "stopped_since_ts": None,
        "last_command_ts": None, "last_hvac_mode": "heat", "last_target": 21.0, "learning_until_ts": None, "last_offset_review_ts": None,
    }
    assert list(control.to_storage()) == [
        "offset_k", "learning_bins", "manual_until_ts", "running_since_ts", "stopped_since_ts",
        "last_command_ts", "last_hvac_mode", "last_target", "learning_until_ts", "last_offset_review_ts",
    ]
    assert ControlMemory.from_storage({"last_target": "21.5", "learning_bins": [[1], ["x", 2], [3, "4"]]}) == ControlMemory(
        last_target=21.5, learning_bins=((3, 4.0),)
    )

    room = RoomCtrlMemory.from_storage({"last_sent_target": "21", "last_sent_ts": "x", "confirmed": 1, "thermostat_before_send": {}})
    assert room == RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=None, confirmed=True, thermostat_before_send=None)
    assert RoomCtrlMemory.from_storage("nonsense") == RoomCtrlMemory()
    assert list(room.to_storage()) == ["last_sent_target", "last_sent_ts", "confirmed", "thermostat_before_send"]


def test_the_minimum_state_time_still_holds_after_a_restart():
    from .test_boiler_control import inp, rr

    restarted = boiler_control.BoilerMemory.from_storage(
        boiler_control.BoilerMemory(state="heat", state_since_ts=2_000_000.0, last_flow=35.0).to_storage()
    )
    # no room needs heat any more, but the boiler switched to "heat" one minute ago
    cmd, _ = boiler_control.decide(inp([rr(demand=0.0, temp=22.0)], mode="active", advice="none", now=2_000_060.0), restarted)
    assert cmd.plan == "heat" and cmd.waiting == "min_state_time"


async def test_room_send_limit_and_manual_detection_survive_a_restart(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.engines.room_control import RoomCtrlMemory

    entry = await _setup(hass, options={"room_allow_active_control": True})
    coordinator = entry.runtime_data
    coordinator.room_ctrl_memory["badezimmer"] = RoomCtrlMemory(
        last_sent_target=21.0, last_sent_ts=1_000_000.0, confirmed=True, thermostat_before_send=19.0
    )
    await coordinator.async_save_store(force=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    restored = entry.runtime_data.room_ctrl_memory.get("badezimmer")
    assert restored is not None
    assert restored.last_sent_target == 21.0 and restored.last_sent_ts == 1_000_000.0
    assert restored.confirmed is True and restored.thermostat_before_send == 19.0


async def test_a_damaged_store_still_loads(hass: HomeAssistant, hass_storage) -> None:
    """A stored value that is not a number is dropped; the integration must still start."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

    from .test_integration import _entry_data, _set_states

    hass_storage[f"{STORAGE_KEY}.damaged"] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": f"{STORAGE_KEY}.damaged",
        "data": {"away_return_ts": "garbage", "midea_block_until": "garbage", "mode": "away", "boosts": {"badezimmer": "x"}},
    }
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), entry_id="damaged", unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.away_return_ts is None
    assert coordinator.heat_pump_tracker.block_until is None
    assert coordinator.mode == "away"  # the good values around it survive
    assert coordinator.boosts == {}


async def test_a_deleted_room_leaves_nothing_behind(hass: HomeAssistant, hass_storage) -> None:
    """Audit 2026-09-28: a room created again under a deleted one's name inherited its learned and set values."""
    import time

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

    from .test_integration import _entry_data, _set_states

    later = time.time() + 3600
    hass_storage[f"{STORAGE_KEY}.deleted"] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": f"{STORAGE_KEY}.deleted",
        "data": {
            "room_temps": {"badezimmer": {"comfort": 22.0}, "keller": {"comfort": 25.0}},
            "room_ctrl_memory": {"badezimmer": {"last_sent_target": 21.0}, "keller": {"last_sent_target": 25.0}},
            "call_memory": {"badezimmer": {"calling": True, "since_ts": 1.0}, "keller": {"calling": True, "since_ts": 1.0}},
            "heat_rates": {"rates": {"badezimmer": [1.5], "keller": [3.0]}, "gradients": {"keller": [12.0]}},
            "cool_rates": {"rates": {"badezimmer": [0.05], "keller": [0.2]}},
            "overrides": {"keller": {"target": 25.0, "until_ts": later}},
            "boosts": {"keller": later},
        },
    }
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), entry_id="deleted", unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.room_temps == {"badezimmer": {"comfort": 22.0}}
    assert "keller" not in coordinator.room_ctrl_memory
    assert "keller" not in coordinator.call_memory and coordinator.call_memory["badezimmer"].since_ts == 1.0
    assert coordinator.heat_rates.rates == {"badezimmer": [1.5]} and "keller" not in coordinator.heat_rates.gradients
    assert coordinator.cool_rates.rates == {"badezimmer": [0.05]}
    assert coordinator.overrides == {} and coordinator.boosts == {}


def test_restore_keeps_only_allowed_modes_and_valid_values():
    import time
    from types import SimpleNamespace

    from custom_components.thriftherm import persistence

    def coord():
        return SimpleNamespace(
            builder=SimpleNamespace(rooms=[SimpleNamespace(key=k) for k in "abcd"]),
            heat_pump_tracker=SimpleNamespace(block_until=None), overrides={}, boosts={}, room_temps={}, room_ctrl_memory={},
            mode="auto", control_mode="shadow", boiler_control_mode="shadow", room_control_mode="shadow",
            control_modes=["off", "shadow"], boiler_control_modes=["off", "shadow", "active"], room_control_modes=["off", "shadow"],
            modes=["auto", "boiler_only", "off", "away"],  # no heat pump
        )

    future = time.time() + 3600
    good = coord()
    persistence.restore(
        good,
        {
            "mode": "away", "control_mode": "off", "boiler_control_mode": "active", "room_control_mode": "off",
            "midea_block_until": "123.5", "away_return_ts": 0, "away_return_provisional": True,
            "overrides": {"a": {"target": "20", "until_ts": future}, "b": {"target": 20, "until_ts": 1.0}, "c": {"until_ts": future}, "d": "x"},
            "boosts": {"a": future, "b": 1.0, "c": "x"},
            "room_temps": {"a": {"comfort": "21", "setback": "x", "other": 1}, "b": "x"},
            "room_ctrl_memory": {"a": {"last_sent_target": 21}},
            "last_learning_reset": "x",
        },
    )
    assert (good.mode, good.control_mode, good.boiler_control_mode, good.room_control_mode) == ("away", "off", "active", "off")
    assert good.heat_pump_tracker.block_until == 123.5 and good.away_return_ts is None
    assert good.away_return_provisional is False  # provisional without a return means nothing
    assert list(good.overrides) == ["a"] and good.overrides["a"].target == 20.0
    assert good.boosts == {"a": future}
    assert good.room_temps == {"a": {"comfort": 21.0}}
    assert good.room_ctrl_memory["a"].last_sent_target == 21.0
    assert good.last_learning_reset is None

    # a mode that is not offered (any more) keeps the default
    bad = coord()
    persistence.restore(bad, {"mode": "party", "control_mode": "active", "boiler_control_mode": None, "room_control_mode": "active"})
    assert (bad.mode, bad.control_mode, bad.boiler_control_mode, bad.room_control_mode) == ("auto", "shadow", "shadow", "shadow")
    no_heat_pump = coord()
    persistence.restore(no_heat_pump, {"mode": "midea_only"})
    assert no_heat_pump.mode == "auto"


async def test_away_without_a_return_time_keeps_a_planned_one(hass: HomeAssistant) -> None:
    """The dashboard button calls set_away with no arguments; a trip's return must survive that."""
    from custom_components.thriftherm.const import DOMAIN

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    friday = 4_000_000_000.0  # far in the future
    await coordinator.async_set_away(friday)
    assert coordinator.away_return_ts == friday

    await hass.services.async_call(DOMAIN, "set_away", {}, blocking=True)
    assert coordinator.mode == "away"
    assert coordinator.away_return_ts == friday  # kept

    await hass.services.async_call(DOMAIN, "set_away", {"clear_return_time": True}, blocking=True)
    assert coordinator.away_return_ts is None  # explicitly open-ended


async def test_the_planned_return_can_be_set_from_the_dashboard(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.util import dt as dt_util

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    assert hass.states.get("datetime.thriftherm_planned_return").state == "unknown"

    back = dt_util.utcnow().replace(microsecond=0) + timedelta(days=2)
    await hass.services.async_call(
        "datetime", "set_value", {"entity_id": "datetime.thriftherm_planned_return", "datetime": back.isoformat()}, blocking=True
    )
    assert coordinator.away_return_ts == pytest.approx(back.timestamp())
    assert coordinator.mode == "away"  # planning a return means being away until then
    assert hass.states.get("datetime.thriftherm_planned_return").state == back.isoformat()

    with pytest.raises(ServiceValidationError):  # a return in the past would pre-heat for nothing
        await coordinator.async_set_away(dt_util.utcnow().timestamp() - 60)

    await coordinator.async_clear_away()  # "back home" ends the absence and the plan
    assert coordinator.mode == "auto" and coordinator.away_return_ts is None
    assert hass.states.get("datetime.thriftherm_planned_return").state == "unknown"


async def test_a_cycling_boiler_still_counts_as_heating(hass: HomeAssistant) -> None:
    """One minute of burner every fifteen: the pump state is polled too slowly to catch it."""
    entry = await _setup(hass, options={"boiler_allow_active_control": True})
    coordinator = entry.runtime_data
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_boiler_control", "option": "active"}, blocking=True
    )
    coordinator._last_target_change_ts = 0.0  # the room setpoints have settled
    hass.states.async_set("sensor.pump", "off")  # ebusd last polled the pump between two bursts
    hass.states.async_set("sensor.gas_flow", "1.2")  # but gas is flowing right now
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    command = hass.states.get("sensor.thriftherm_boiler_command")
    assert command.state == "heat"
    assert command.attributes["learning_phase"] != "paused"  # samples are being collected

    hass.states.async_set("sensor.gas_flow", "0.0")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_boiler_command").attributes["learning_phase"] == "paused"


STORED_KEYS = (
    "cop_map", "heat_rates", "cool_rates", "midea_block_until", "overrides", "away_return_ts", "away_return_provisional", "mode",
    "control_mode", "control_memory", "boosts", "boiler_control_mode", "boiler_memory", "room_control_mode",
    "room_temps", "room_ctrl_memory", "learning_basis", "last_learning_reset",
)
NESTED_GARBAGE = {
    "cop_map": {"bins": 5},
    "heat_rates": {"badezimmer": "x"},
    "cool_rates": {"badezimmer": [1, "x"]},
    "overrides": {"badezimmer": 5},
    "control_memory": {"learning_bins": 3, "running_since_ts": "x"},
    "boiler_memory": {"adjustments": 5, "burn_starts": "x", "burn_durations": 7, "offset_k": "y"},
    "room_temps": {"badezimmer": [21]},
    "room_ctrl_memory": {"badezimmer": [1]},
}


@pytest.mark.parametrize("garbage", [[1, 2], 42, "text", True, "nested"])
async def test_no_stored_value_can_stop_the_integration_from_loading(hass: HomeAssistant, hass_storage, garbage) -> None:
    """Whatever an old version or a broken disk left in the store, Thriftherm must still start."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.const import DOMAIN, STORAGE_KEY, STORAGE_VERSION

    from .test_integration import _entry_data, _set_states

    data = dict(NESTED_GARBAGE) if garbage == "nested" else dict.fromkeys(STORED_KEYS, garbage)
    hass_storage[f"{STORAGE_KEY}.garbage"] = {"version": STORAGE_VERSION, "minor_version": 1, "key": f"{STORAGE_KEY}.garbage", "data": data}
    _set_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), entry_id="garbage", unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thriftherm_boiler_command") is not None


# ------------------------------------------------------------------ bathroom drying only with a heat pump that acts
async def test_bathroom_drying_needs_an_active_heat_pump(hass: HomeAssistant, monkeypatch) -> None:
    """Measured 2026-09-25: while the heat pump only planned, drying raised the bathroom to 22 °C on gas."""
    from custom_components.thriftherm import coordinator as coordinator_module
    from custom_components.thriftherm.const import CTRL_ACTIVE, CTRL_SHADOW

    seen: list[bool] = []
    evaluate = coordinator_module.room_engine.evaluate_room

    def spy(*args, heat_pump_heat_possible=True, **kwargs):
        seen.append(heat_pump_heat_possible)
        return evaluate(*args, heat_pump_heat_possible=heat_pump_heat_possible, **kwargs)

    monkeypatch.setattr(coordinator_module.room_engine, "evaluate_room", spy)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    hass.states.async_set("climate.midea", "off", {**hass.states.get("climate.midea").attributes})  # ready to heat
    for mode, expected in ((CTRL_SHADOW, False), (CTRL_ACTIVE, True)):
        coordinator.control_mode = mode
        seen.clear()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert seen and all(value is expected for value in seen), mode
