"""Boiler profiles: the table stays consistent and the Vaillant row reproduces what was hard-coded."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm import boiler_profiles, config_schema
from custom_components.thriftherm.boiler_profiles import DEFAULT_PROFILE, PROFILES, profile

from .test_integration import _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.mark.parametrize("key", list(PROFILES))
def test_profile_rows_are_consistent(key: str) -> None:
    p = PROFILES[key]
    assert p.key == key and p.name and p.circuit and p.setmode_message
    assert set(p.roles) <= set(config_schema.BOILER_KEYS)  # every role is a field of the boiler step
    assert "boiler_flow_temp" in p.roles and "boiler_return_temp" in p.roles  # detection needs both
    messages = {message for message, _field in p.roles.values()}
    assert set(p.fast_messages) <= messages and len(set(p.fast_messages)) == len(p.fast_messages)
    assert all(message and field for message, field in p.roles.values())


def test_vaillant_profile_is_what_was_hard_coded() -> None:
    p = PROFILES["vaillant_bai"]
    assert DEFAULT_PROFILE == "vaillant_bai" and p.circuit == "bai"
    assert p.fast_messages == ("FlowTemp", "ReturnTemp", "Status01", "WP", "Statenumber")
    assert p.setmode_message == "SetMode"
    assert dict(p.roles) == {
        "boiler_flow_temp": ("FlowTemp", "temp"),
        "boiler_return_temp": ("ReturnTemp", "temp"),
        "boiler_pump_state": ("Status01", "pumpstate"),
        "boiler_pump_running": ("WP", "value"),
        "boiler_state_number": ("Statenumber", "value"),
        "boiler_hwc_mode": ("Status02", "hwcmode"),
    }


@pytest.mark.parametrize("stored", [None, "", "no_such_boiler", ["vaillant_bai"], 3])
def test_missing_or_unknown_profile_means_vaillant(stored) -> None:
    assert profile(stored) is PROFILES["vaillant_bai"]


async def test_unknown_stored_profile_keeps_the_topics(hass: HomeAssistant) -> None:
    publish = async_mock_service(hass, "mqtt", "publish")
    coordinator = (await _setup(hass, options={"boiler_profile": "no_such_boiler"})).runtime_data
    assert coordinator.boiler_profile is boiler_profiles.PROFILES["vaillant_bai"]
    assert coordinator.boiler_setmode_topic == "ebusd/bai/SetMode/set"
    publish.clear()
    coordinator._ebus_priority_ts = None
    await coordinator._async_keep_boiler_readings_fresh(0.0, 1_000_000.0)
    assert [c.data["topic"] for c in publish] == [
        "ebusd/bai/FlowTemp/get", "ebusd/bai/ReturnTemp/get", "ebusd/bai/Status01/get", "ebusd/bai/WP/get", "ebusd/bai/Statenumber/get",
    ]
