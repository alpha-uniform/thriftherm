"""ZHA's own "last seen" of a device counts as a report (issue #2, 2026-10-26).

ZHA leaves the timestamps of a device's entities alone while their values stay the same. Reported
with SONOFF valves and thermometers: a valve ZHA had seen two minutes before had entities 2.5 hours
old and counted as silent, a thermometer seen 42 seconds before had a temperature six hours old and
counted as stale. Zigbee2MQTT and every other integration are not asked about it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.room import THERMOSTAT_SILENT_S

from .conftest import PARAMS
from .test_integration import _entry_data, _set_states

ZHA_HELPERS = "homeassistant.components.zha.helpers"
START = datetime(2026, 10, 26, 6, 0, tzinfo=dt_util.UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeZha:
    """ZHA's helper as a test sees it: what ZHA knows of each device, by device registry id."""

    def __init__(self) -> None:
        self.devices: dict[str, tuple[Any, Any]] = {}  # device id -> (last_seen, available)
        self.calls = 0

    def async_get_zha_device_proxy(self, hass: HomeAssistant, device_id: str) -> SimpleNamespace:
        self.calls += 1
        last_seen, available = self.devices[device_id]  # a KeyError, as ZHA raises for a device it does not know
        return SimpleNamespace(device=SimpleNamespace(last_seen=last_seen, available=available))


def _install_helpers(monkeypatch, hass: HomeAssistant, **members: Any) -> None:
    module = ModuleType(ZHA_HELPERS)
    for name, value in members.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, ZHA_HELPERS, module)
    hass.config.components.add("zha")


@pytest.fixture
def zha(hass: HomeAssistant, monkeypatch) -> FakeZha:
    """ZHA loaded, with a helper that answers from `FakeZha.devices`."""
    fake = FakeZha()
    _install_helpers(monkeypatch, hass, async_get_zha_device_proxy=fake.async_get_zha_device_proxy)
    return fake


def _register_valve(hass: HomeAssistant, integration: str) -> str:
    """climate.bath_bt from Better Thermostat, driving climate.bath_trv of `integration`; the valve's device id."""
    registry = er.async_get(hass)
    radio = MockConfigEntry(domain=integration)
    radio.add_to_hass(hass)
    valve = dr.async_get(hass).async_get_or_create(
        config_entry_id=radio.entry_id, identifiers={(integration, "test-valve")}
    )
    for domain, uid, object_id in (("climate", "trv", "bath_trv"), ("sensor", "trv_battery", "bath_trv_battery")):
        registry.async_get_or_create(
            domain, integration, uid, suggested_object_id=object_id, device_id=valve.id, config_entry=radio
        )
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath_trv"]})
    bt.add_to_hass(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt", config_entry=bt)
    return valve.id


def _register_thermometer(hass: HomeAssistant, integration: str) -> str:
    """The bathroom's temperature and humidity as two sensors of one device of `integration`; its device id."""
    radio = MockConfigEntry(domain=integration)
    radio.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=radio.entry_id, identifiers={(integration, "test-thermometer")}
    )
    registry = er.async_get(hass)
    for uid, object_id in (("temp", "bath_temp"), ("rh", "bath_rh")):
        registry.async_get_or_create(
            "sensor", integration, uid, suggested_object_id=object_id, device_id=device.id, config_entry=radio
        )
    return device.id


def _builder(hass: HomeAssistant) -> SnapshotBuilder:
    """Every value set now, the bathroom driven by Better Thermostat."""
    _set_states(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "current_temperature": 19.0})
    hass.states.async_set("sensor.bath_trv_battery", "100")
    data = _entry_data()
    data["rooms"][0]["climate_entity"] = "climate.bath_bt"
    return SnapshotBuilder(hass, data)


def _bath(builder: SnapshotBuilder):
    return builder.build("auto", {}, {}, {}, None, {}, {}).rooms["badezimmer"]


def _ago(delta: timedelta) -> float:
    return (dt_util.utcnow() - delta).timestamp()


# ------------------------------------------------------------------ the valve
async def test_a_zha_valve_seen_two_minutes_ago_is_not_silent_although_its_values_have_not_changed_for_three_hours(
    hass: HomeAssistant, freezer, zha: FakeZha
) -> None:
    valve = _register_valve(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=3))
    zha.devices[valve] = (_ago(timedelta(minutes=2)), True)
    assert builder.thermostat_silent_s("climate.bath_bt", dt_util.utcnow()) == pytest.approx(120.0, abs=1)

    snap = builder.build("auto", {}, {}, {}, None, {}, {})
    bath = snap.rooms["badezimmer"]
    assert bath.thermostat_silent_s == pytest.approx(120.0, abs=1)
    result = room_engine.evaluate_room(bath, snap.now, snap.mode, PARAMS)
    assert result.heating_allowed and "thermostat_silent" not in result.issues


@pytest.mark.parametrize(
    ("seen_ago", "available"),
    [
        pytest.param(timedelta(minutes=2), False, id="zha calls it unavailable"),
        pytest.param(timedelta(hours=3), True, id="seen 3 h ago"),
    ],
)
async def test_a_zha_valve_that_zha_calls_unavailable_or_last_saw_three_hours_ago_is_silent_as_before(
    hass: HomeAssistant, freezer, zha: FakeZha, seen_ago: timedelta, available: bool
) -> None:
    valve = _register_valve(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=3))
    zha.devices[valve] = (_ago(seen_ago), available)
    bath = _bath(builder)
    assert bath.thermostat_silent_s == pytest.approx(10800.0, abs=1) and bath.thermostat_silent_s > THERMOSTAT_SILENT_S


async def test_a_zha_valve_that_zha_stops_seeing_goes_silent_again_in_the_next_build(
    hass: HomeAssistant, freezer, zha: FakeZha
) -> None:
    valve = _register_valve(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=3))
    zha.devices[valve] = (_ago(timedelta(minutes=2)), True)
    assert _bath(builder).thermostat_silent_s == pytest.approx(120.0, abs=1)

    freezer.move_to(START + timedelta(hours=6))  # ZHA has not seen it since
    silent = _bath(builder).thermostat_silent_s
    assert silent == pytest.approx(3 * 3600.0 + 120.0, abs=1) and silent > THERMOSTAT_SILENT_S


# ------------------------------------------------------------------ the room thermometer
async def test_a_zha_thermometer_seen_recently_stays_valid_after_six_and_a_half_hours_without_a_change(
    hass: HomeAssistant, freezer, zha: FakeZha
) -> None:
    thermometer = _register_thermometer(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=6, minutes=30))
    zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    reading = _bath(builder).temperature
    assert reading.valid and reading.value == 18.5 and reading.reason is None
    assert reading.age_s == pytest.approx(6.5 * 3600.0, abs=1)  # the measurement keeps its own age


@pytest.mark.parametrize(
    ("seen_ago", "available"),
    [
        pytest.param(timedelta(seconds=42), False, id="zha calls it unavailable"),
        pytest.param(timedelta(hours=7), True, id="seen 7 h ago"),
    ],
)
async def test_a_zha_thermometer_that_zha_calls_unavailable_or_last_saw_seven_hours_ago_is_stale_as_before(
    hass: HomeAssistant, freezer, zha: FakeZha, seen_ago: timedelta, available: bool
) -> None:
    thermometer = _register_thermometer(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=6, minutes=30))
    zha.devices[thermometer] = (_ago(seen_ago), available)
    reading = _bath(builder).temperature
    assert not reading.valid and reading.reason == "stale"


async def test_a_zha_thermometer_unchanged_for_more_than_a_day_is_stale_however_recently_zha_saw_it(
    hass: HomeAssistant, freezer, zha: FakeZha
) -> None:
    thermometer = _register_thermometer(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=25))
    zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    reading = _bath(builder).temperature
    assert not reading.valid and reading.reason == "stale"


# ------------------------------------------------------------------ everything else as before
async def test_the_devices_of_another_integration_are_never_looked_up_in_zha(hass: HomeAssistant, freezer, zha: FakeZha) -> None:
    valve = _register_valve(hass, "mqtt")
    thermometer = _register_thermometer(hass, "mqtt")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=6, minutes=30))
    zha.devices[valve] = zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    bath = _bath(builder)
    assert bath.thermostat_silent_s == pytest.approx(6.5 * 3600.0, abs=1)
    assert not bath.temperature.valid and bath.temperature.reason == "stale"
    assert zha.calls == 0


async def test_zha_devices_are_not_asked_about_while_zha_is_not_loaded(hass: HomeAssistant, freezer, zha: FakeZha) -> None:
    valve = _register_valve(hass, "zha")
    thermometer = _register_thermometer(hass, "zha")
    hass.config.components.remove("zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=6, minutes=30))
    zha.devices[valve] = zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    bath = _bath(builder)
    assert bath.thermostat_silent_s == pytest.approx(6.5 * 3600.0, abs=1)
    assert not bath.temperature.valid and zha.calls == 0


async def test_a_zha_device_is_looked_up_once_per_build(hass: HomeAssistant, freezer, zha: FakeZha) -> None:
    # every cycle, every room: the valve's two entities share one device, asked once per build
    valve = _register_valve(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=3))
    zha.devices[valve] = (_ago(timedelta(minutes=2)), True)
    _bath(builder)
    assert zha.calls == 1
    _bath(builder)
    assert zha.calls == 2


def _raising(error: BaseException):
    def helper(hass: HomeAssistant, device_id: str) -> Any:
        raise error

    return helper


def _answering(proxy: Any):
    def helper(hass: HomeAssistant, device_id: str) -> Any:
        return proxy

    return helper


def _seen(last_seen: Any, available: Any = True) -> SimpleNamespace:
    return SimpleNamespace(device=SimpleNamespace(last_seen=last_seen, available=available))


_NOW = (START + timedelta(hours=6, minutes=30)).timestamp()
BROKEN_HELPERS = [
    pytest.param(None, id="the import fails"),
    pytest.param({}, id="the helper is gone"),
    pytest.param({"async_get_zha_device_proxy": _raising(KeyError("unknown device"))}, id="a device zha does not know"),
    pytest.param({"async_get_zha_device_proxy": _raising(ValueError("No gateway object exists"))}, id="no gateway"),
    pytest.param({"async_get_zha_device_proxy": _raising(StopIteration())}, id="no zha identifier"),
    pytest.param({"async_get_zha_device_proxy": _raising(AttributeError("device"))}, id="another shape"),
    pytest.param({"async_get_zha_device_proxy": _raising(TypeError("takes 1 argument"))}, id="another signature"),
    pytest.param({"async_get_zha_device_proxy": _answering(None)}, id="no proxy"),
    pytest.param({"async_get_zha_device_proxy": _answering(SimpleNamespace(device=None))}, id="no device"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(None))}, id="never seen"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(float("nan")))}, id="not a number"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(float("inf")))}, id="infinite"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(_NOW + 3600.0))}, id="in the future"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(_NOW * 1000.0))}, id="in milliseconds"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen("2026-10-26T12:29:18"))}, id="a text"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(True))}, id="a bool"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(_NOW - 42.0, None))}, id="available unknown"),
    pytest.param({"async_get_zha_device_proxy": _answering(_seen(_NOW - 42.0, "yes"))}, id="available a text"),
]


@pytest.mark.parametrize("helpers", BROKEN_HELPERS)
async def test_a_broken_or_missing_zha_helper_changes_nothing_and_raises_nothing(
    hass: HomeAssistant, freezer, monkeypatch, helpers: dict[str, Any] | None
) -> None:
    _register_valve(hass, "zha")
    _register_thermometer(hass, "zha")
    if helpers is None:
        hass.config.components.add("zha")
        monkeypatch.setitem(sys.modules, ZHA_HELPERS, None)  # what Python does for a module that cannot be imported
    else:
        _install_helpers(monkeypatch, hass, **helpers)
    freezer.move_to(START)
    builder = _builder(hass)
    freezer.move_to(START + timedelta(hours=6, minutes=30))
    bath = _bath(builder)
    assert bath.thermostat_silent_s == pytest.approx(6.5 * 3600.0, abs=1)
    assert not bath.temperature.valid and bath.temperature.reason == "stale"


async def test_an_odd_identifier_in_the_device_registry_is_no_zha_device_and_raises_nothing(
    hass: HomeAssistant, freezer, zha: FakeZha, monkeypatch
) -> None:
    """Review 2026-10-26: an empty identifier tuple raised IndexError out of the build, every cycle."""
    valve = _register_valve(hass, "zha")
    freezer.move_to(START)
    builder = _builder(hass)
    registry = dr.async_get(hass)
    real_get = registry.async_get
    odd = SimpleNamespace(id=valve, identifiers={()})
    monkeypatch.setattr(registry, "async_get", lambda device_id: odd if device_id == valve else real_get(device_id))
    freezer.move_to(START + timedelta(hours=3))
    zha.devices[valve] = (_ago(timedelta(minutes=2)), True)
    assert _bath(builder).thermostat_silent_s == pytest.approx(3 * 3600.0, abs=1)
    assert zha.calls == 0
