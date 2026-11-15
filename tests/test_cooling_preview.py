"""Cooling ships hidden until it is measured on a real unit (COOLING_PREVIEW): no option, no entity, no effect."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.thriftherm import config_schema, const
from custom_components.thriftherm.const import CONF_HEAT_PUMP_COOLING_ALLOWED, DOMAIN, SEASON_HEATING

from .test_integration import _setup


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def test_cooling_is_hidden_by_default() -> None:
    assert const.COOLING_PREVIEW is False


def test_the_heat_pump_tuning_offers_no_cooling_while_hidden() -> None:
    keys = {str(m) for m in config_schema.schema_heat_pump_tuning({}).schema}
    assert config_schema.SECTION_COOLING not in keys


def test_the_room_form_offers_no_cooling_schedule_while_hidden() -> None:
    keys = {str(m) for m in config_schema.schema_room({}, allow_add_another=False, heat_pump=True).schema}
    assert const.CONF_ROOM_COOLING_SCHEDULE_ENTITY not in keys


def test_both_appear_once_cooling_is_shown(monkeypatch) -> None:
    monkeypatch.setattr(const, "COOLING_PREVIEW", True)
    assert config_schema.SECTION_COOLING in {str(m) for m in config_schema.schema_heat_pump_tuning({}).schema}
    room = {str(m) for m in config_schema.schema_room({}, allow_add_another=False, heat_pump=True).schema}
    assert const.CONF_ROOM_COOLING_SCHEDULE_ENTITY in room


async def test_a_stored_cooling_option_does_nothing_while_hidden(hass: HomeAssistant) -> None:
    await _setup(hass, {CONF_HEAT_PUMP_COOLING_ALLOWED: True})
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert not coord.cooling_allowed and coord.season == SEASON_HEATING
    assert hass.states.get("select.thriftherm_season") is None
    assert hass.states.get("climate.thriftherm_badezimmer_room_climate") is None


def test_stored_cooling_options_are_kept_while_hidden() -> None:
    """A hidden option is no leftover: what was stored stays for the day cooling is shown."""
    stored = {CONF_HEAT_PUMP_COOLING_ALLOWED: True, const.CONF_COOL_FLOOR_TEMP: 21.0}
    assert config_schema.known_keys(stored) == stored


STAGE_2_OPTIONS = (
    "cooling_cheap_power_entity", "cooling_cheap_power_k",
    "cooling_season_from_c", "heating_season_from_c", "cooling_season_veto_c",
)


def test_the_stage_2_options_are_hidden_with_cooling() -> None:
    assert not set(STAGE_2_OPTIONS) & set(config_schema.keys_of(config_schema.schema_heat_pump_tuning))


def test_the_stage_2_options_appear_once_cooling_is_shown(monkeypatch) -> None:
    monkeypatch.setattr(const, "COOLING_PREVIEW", True)
    assert set(STAGE_2_OPTIONS) <= set(config_schema.keys_of(config_schema.schema_heat_pump_tuning))


async def test_cheap_power_does_nothing_while_hidden(hass: HomeAssistant) -> None:
    await _setup(hass, {CONF_HEAT_PUMP_COOLING_ALLOWED: True, "cooling_cheap_power_entity": "binary_sensor.pv_surplus"})
    hass.states.async_set("binary_sensor.pv_surplus", "on")
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    coord.season = const.SEASON_COOLING  # as a store from a summer with cooling shown would leave it
    await coord.async_refresh()
    assert not coord.cooling_season and coord._cheap_k == 0.0
    assert coord.data["rooms"]["badezimmer"].cool_target_reason != "cool_cheap_power"


@pytest.mark.parametrize(
    ("cooling_from", "heating_from", "veto", "error"),
    [
        (15.0, 14.0, 12.0, "season_means_too_close"),
        (18.0, 14.0, 15.0, "season_veto_above_heating"),
        (16.0, 14.0, 14.0, None),
    ],
    ids=["too_close", "veto_above_heating", "fine"],
)
def test_the_automatic_seasons_means_are_checked(cooling_from, heating_from, veto, error) -> None:
    answer = {"cooling_season_from_c": cooling_from, "heating_season_from_c": heating_from, "cooling_season_veto_c": veto}
    assert config_schema.validate_heat_pump_tuning(answer) == ({} if error is None else {"base": error})
    assert config_schema.validate_heat_pump_tuning({}) == {}  # hidden: nothing to check


async def test_the_stage_2_options_survive_loading_while_hidden(hass: HomeAssistant) -> None:
    """Loading drops unknown option keys (main 1cbb2fb); the hidden stage-2 keys must be known ones."""
    stored = {
        CONF_HEAT_PUMP_COOLING_ALLOWED: True, "cooling_cheap_power_entity": "binary_sensor.pv_surplus",
        "cooling_cheap_power_k": 1.5, "cooling_season_from_c": 19.0, "heating_season_from_c": 13.0,
        "cooling_season_veto_c": 11.0,
    }
    assert set(STAGE_2_OPTIONS) <= set(config_schema.CONFIG_KEYS)
    entry = await _setup(hass, stored)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {k: entry.options.get(k) for k in stored} == stored
