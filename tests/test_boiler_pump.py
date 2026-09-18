"""The heating pump and the boiler status: measured on the atmoTEC plus on 2026-09-18."""

from dataclasses import replace

from custom_components.thriftherm.adapters.inputs import on_off, whole_number
from custom_components.thriftherm.engines import boiler as boiler_engine

from .conftest import PRICES, boiler_state


def test_status01_off_does_not_mean_the_pump_stands_still():
    # Status01 said "off" while the pump ran audibly and drew 33 W; WP said "on"
    state = replace(boiler_state(flow=45.0, ret=38.0, pump="off"), pump_running=True)
    res = boiler_engine.evaluate(state, PRICES)
    assert res.thermal_power_estimate_w is not None and res.thermal_power_estimate_w > 0
    assert res.pump_running is True


def test_a_stopped_pump_moves_no_heat_whatever_status01_says():
    state = replace(boiler_state(flow=45.0, ret=38.0, pump="on"), pump_running=False)
    assert boiler_engine.evaluate(state, PRICES).thermal_power_estimate_w is None


def test_without_the_pump_entity_status01_still_decides():
    res = boiler_engine.evaluate(boiler_state(flow=45.0, ret=38.0, pump="on"), PRICES)
    assert res.thermal_power_estimate_w is not None
    assert res.pump_running is None


def test_hot_water_detection_keeps_using_status01():
    state = replace(boiler_state(pump="hwc"), pump_running=True)
    assert boiler_engine.evaluate(state, PRICES).hwc_active is True


def test_the_status_number_is_passed_on():
    state = replace(boiler_state(), state_number=8)
    assert boiler_engine.evaluate(state, PRICES).state_number == 8


def test_parsing_on_off_and_numbers():
    assert on_off("on") is True and on_off("OFF") is False and on_off("unknown") is None and on_off(None) is None
    assert whole_number("8") == 8 and whole_number("8.0") == 8 and whole_number("S.8") is None and whole_number(None) is None
