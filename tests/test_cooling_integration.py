"""Cooling in a real Home Assistant test instance (docs/PHASE12_COOLING.md): season, entities, wiring."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.thriftherm import const
from custom_components.thriftherm.const import (
    CONF_COOL_FLOOR_TEMP,
    CONF_HEAT_PUMP_COOLING_ALLOWED,
    DOMAIN,
    SEASON_COOLING,
    SEASON_HEATING,
)

from .test_integration import _setup

COOLING = {CONF_HEAT_PUMP_COOLING_ALLOWED: True}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def cooling_shown(monkeypatch):
    """Cooling itself is tested as it will be shown; test_cooling_preview.py covers it hidden."""
    monkeypatch.setattr(const, "COOLING_PREVIEW", True)


def _coordinator(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data


def _cooling_room(hass: HomeAssistant, temp: str = "26.0") -> None:
    hass.states.async_set("sensor.bath_temp", temp, {"unit_of_measurement": "°C"})


async def test_without_cooling_allowed_there_is_no_season_and_nothing_to_cool(hass: HomeAssistant) -> None:
    await _setup(hass)
    coord = _coordinator(hass)
    assert coord.season == SEASON_HEATING and not coord.cooling_allowed
    assert hass.states.get("select.thriftherm_season") is None
    assert hass.states.get("number.thriftherm_badezimmer_cooling_comfort_temperature") is None
    assert hass.states.get("climate.thriftherm_badezimmer_room_climate") is None


async def test_cooling_allowed_brings_the_season_the_numbers_and_the_room_climate(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    season = hass.states.get("select.thriftherm_season")
    assert season is not None and season.state == SEASON_HEATING
    assert season.attributes["options"] == [SEASON_HEATING, SEASON_COOLING, "auto"]
    assert float(hass.states.get("number.thriftherm_badezimmer_cooling_comfort_temperature").state) == 25.0
    assert float(hass.states.get("number.thriftherm_badezimmer_cooling_outside_schedule_temperature").state) == 28.0
    # only rooms the heat pump serves
    assert hass.states.get("number.thriftherm_wohnzimmer_cooling_comfort_temperature") is None
    climate = hass.states.get("climate.thriftherm_badezimmer_room_climate")
    assert climate is not None and climate.state == "heat"
    assert climate.attributes["hvac_modes"] == ["heat", "off"]


async def test_a_unit_without_cool_mode_offers_no_season(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    hass.states.async_set("climate.midea", "fan_only", {"hvac_modes": ["off", "heat", "fan_only"]})
    coord = _coordinator(hass)
    assert not coord.unit_can_cool
    await coord.async_set_season(SEASON_COOLING)
    assert coord.season == SEASON_HEATING


async def test_the_cooling_season_holds_the_radiators_at_frost_and_plans_cooling(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    _cooling_room(hass, "26.0")
    hass.states.async_set("climate.midea", "off", {"hvac_modes": ["off", "cool", "heat", "fan_only"], "indoor_temperature": 27})
    coord = _coordinator(hass)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_season", "option": SEASON_COOLING}, blocking=True
    )
    coord.season_since_ts -= 3600  # past the pause after the change of season
    await coord.async_refresh()
    assert coord.season == SEASON_COOLING
    rooms = coord.data["rooms"]
    bath = rooms["badezimmer"]
    assert bath.target == coord.builder.params.frost_temp
    assert bath.cool_target == 25.0 and bath.cooling_allowed is True
    assert rooms["wohnzimmer"].target == coord.builder.params.frost_temp
    command = coord.data["midea_command"]
    assert command.plan == "cool"
    climate = hass.states.get("climate.thriftherm_badezimmer_room_climate")
    assert climate.state == "cool" and climate.attributes["temperature"] == 25.0
    assert climate.attributes["hvac_modes"] == ["cool", "off"]


async def test_the_season_survives_a_restart(hass: HomeAssistant) -> None:
    entry = await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await coord.async_save_store(force=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _coordinator(hass).season == SEASON_COOLING


async def test_a_setpoint_set_on_the_room_climate_becomes_a_cooling_override(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    _cooling_room(hass)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.thriftherm_badezimmer_room_climate", "temperature": 23.0},
        blocking=True,
    )
    await coord.async_refresh()
    assert coord.cool_overrides["badezimmer"].target == 23.0
    assert "badezimmer" not in coord.overrides
    assert coord.data["rooms"]["badezimmer"].cool_target == 23.0


async def test_a_setpoint_set_on_the_room_climate_in_the_heating_season_is_a_heating_override(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.thriftherm_badezimmer_room_climate", "temperature": 22.0},
        blocking=True,
    )
    assert coord.overrides["badezimmer"].target == 22.0


async def test_the_room_climate_refuses_the_other_season(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    with pytest.raises(Exception):  # noqa: B017 - Home Assistant refuses a mode the entity does not offer
        await hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": "climate.thriftherm_badezimmer_room_climate", "hvac_mode": "cool"},
            blocking=True,
        )


async def test_the_floor_option_applies(hass: HomeAssistant) -> None:
    await _setup(hass, {**COOLING, CONF_COOL_FLOOR_TEMP: 24.0})
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await coord.async_set_cool_override("badezimmer", 22.0, 60)
    await coord.async_refresh()
    assert coord.data["rooms"]["badezimmer"].cool_target == 24.0


async def test_an_active_cooling_start_sends_cool(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.const import CONF_HEAT_PUMP_ALLOW_ACTIVE

    await _setup(hass, {**COOLING, CONF_HEAT_PUMP_ALLOW_ACTIVE: True})
    hass.states.async_set(
        "climate.midea", "off",
        {"hvac_modes": ["off", "cool", "heat", "fan_only"], "temperature": 26, "indoor_temperature": 27,
         "compressor_frequency": 0, "error_code": 0, "fan_modes": ["auto", "full"], "fan_mode": "auto"},
    )
    _cooling_room(hass, "26.5")
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_fan_mode")
    coord = _coordinator(hass)
    coord.set_control_mode("active")
    await coord.async_set_season(SEASON_COOLING)
    coord._started_ts -= 3600  # past the waiting for room data
    coord.season_since_ts -= 3600  # and past the pause after the change of season
    await coord.async_refresh()
    await hass.async_block_till_done()
    sent = [c.data.get("hvac_mode") for c in modes if c.data.get("entity_id") == "climate.midea"]
    assert "cool" in sent and "heat" not in sent


async def test_the_quick_heat_up_is_not_available_in_the_cooling_season(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await coord.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("button.thriftherm_badezimmer_quick_heat_up")
    assert state is not None and state.state == "unavailable"


async def test_the_diagnostics_show_the_season_and_the_cooling_targets(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["control"]["season"] == SEASON_COOLING and diag["installation"]["cooling_allowed"] is True
    assert diag["rooms"]["badezimmer"]["cool_target"] == 25.0


async def test_an_override_from_the_heating_season_is_no_heating_by_hand(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    _cooling_room(hass)
    coord = _coordinator(hass)
    await coord.async_set_override("badezimmer", 22.0, 600)  # heating season, through the card
    await coord.async_set_season(SEASON_COOLING)
    await coord.async_refresh()
    bath = coord.data["rooms"]["badezimmer"]
    assert bath.cooling_allowed and bath.cool_target_reason != "heating_by_hand_in_cooling"
    assert coord.data["room_commands"]["badezimmer"].reason != "heating_by_hand_in_cooling"


async def test_an_override_for_a_room_the_heat_pump_does_not_serve_is_refused_in_the_cooling_season(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    with pytest.raises(ServiceValidationError):
        await coord.async_set_override("wohnzimmer", 23.0, 60)


async def test_switching_the_room_climate_on_again_ends_the_override(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    entity = "climate.thriftherm_badezimmer_room_climate"
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": "off"}, blocking=True)
    assert "badezimmer" in coord.cool_overrides
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": "cool"}, blocking=True)
    assert "badezimmer" not in coord.cool_overrides and coord._store_dirty is False  # saved with the change


async def test_letting_go_of_a_cooling_run_switches_the_unit_off(hass: HomeAssistant) -> None:
    from dataclasses import replace as _replace

    await _setup(hass, COOLING)
    hass.states.async_set("climate.midea", "cool", {"hvac_modes": ["off", "cool", "heat", "fan_only"], "temperature": 24.0})
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "climate", "set_temperature")
    coord = _coordinator(hass)
    mem = _replace(coord.control_memory, running_since_ts=1.0, last_hvac_mode="cool", last_target=24.0)
    await coord.async_let_go_of_heat_pump(mem)
    assert [c.data["hvac_mode"] for c in modes] == ["off"]


# ---- review 2026-11-13 ----------------------------------------------------------------------------------
async def test_the_run_on_after_a_cooling_run_ended_by_the_change_to_heating_counts_as_one_after_cooling(
    hass: HomeAssistant,
) -> None:
    """K8: the heating control ends our cooling run ("season_ended"); its fan run-on gives off no heat to measure."""
    from dataclasses import replace as _replace

    from custom_components.thriftherm.const import CONF_HEAT_PUMP_ALLOW_ACTIVE

    await _setup(hass, {**COOLING, CONF_HEAT_PUMP_ALLOW_ACTIVE: True})
    hass.states.async_set(
        "climate.midea", "cool",
        {"hvac_modes": ["off", "cool", "heat", "fan_only"], "temperature": 26.0, "indoor_temperature": 25,
         "compressor_frequency": 30, "error_code": 0},
    )
    async_mock_service(hass, "climate", "set_hvac_mode")
    coord = _coordinator(hass)
    coord.set_control_mode("active")
    await coord.async_set_season(SEASON_COOLING)
    now = coord.season_since_ts
    coord.control_memory = _replace(
        coord.control_memory, running_since_ts=now - 3600, phase_since_ts=now - 3600, last_command_ts=now - 3600,
        last_hvac_mode="cool", last_target=26.0,
    )
    await coord.async_set_season(SEASON_HEATING)
    command = coord.data["midea_command"]
    assert (command.action, command.hvac_mode, command.reason) == ("stop", "fan_only", "season_ended")
    assert coord._run_on_after_cooling is True


async def test_letting_go_in_the_fan_run_on_switches_the_unit_off(hass: HomeAssistant) -> None:
    """K10: Plan only, Off, a disable or a delete in the run-on left the fan running until the control was active again."""
    import time
    from dataclasses import replace as _replace

    await _setup(hass, COOLING)
    hass.states.async_set("climate.midea", "fan_only", {"hvac_modes": ["off", "cool", "heat", "fan_only"]})
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    coord = _coordinator(hass)
    now = time.time()
    mem = _replace(coord.control_memory, last_hvac_mode="fan_only", fan_until_ts=now + 600, last_command_ts=now - 300)
    coord.control_memory = mem
    await coord.async_let_go_of_heat_pump()
    assert [c.data["hvac_mode"] for c in modes] == ["off"]
    assert coord.control_memory.fan_until_ts is None and coord.control_memory.last_hvac_mode == "off"
    # one somebody set meanwhile stays
    modes.clear()
    hass.states.async_set("climate.midea", "cool", {"hvac_modes": ["off", "cool", "heat", "fan_only"]})
    await coord.async_let_go_of_heat_pump(mem)
    assert modes == []


async def test_the_room_climate_takes_no_setpoint_below_the_cooling_floor(hass: HomeAssistant) -> None:
    """K11: its lowest setpoint in the cooling season is the cooling floor, not the unit's 16 °C: asked for 18 °C,
    the target showed 20."""
    await _setup(hass, {**COOLING, CONF_COOL_FLOOR_TEMP: 22.0})
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await hass.async_block_till_done()
    climate = hass.states.get("climate.thriftherm_badezimmer_room_climate")
    assert climate.attributes["min_temp"] == 22.0 and climate.attributes["max_temp"] == 32.0


async def test_the_diagnostics_of_an_entry_that_is_not_loaded_say_so(hass: HomeAssistant) -> None:
    """B15: without a coordinator the download raised AttributeError."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.diagnostics import async_get_config_entry_diagnostics

    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["loaded"] is False and diag["entry"]["version"] == entry.version


async def test_a_room_without_a_cooling_target_shows_off_on_its_room_climate(hass: HomeAssistant) -> None:
    """Not cooled outside its schedule (32 °C), or in the mode Off: "cool" with no target left Apple Home showing the
    last one, "cooling to 25°"."""
    await _setup(hass, COOLING)
    _cooling_room(hass)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    coord.set_mode("off")
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert coord.data["rooms"]["badezimmer"].cool_target is None
    assert hass.states.get("climate.thriftherm_badezimmer_room_climate").state == "off"


# --- stage 2: cheap power (docs/PHASE12_COOLING.md §20.1) ---

CHEAP = {**COOLING, "cooling_cheap_power_entity": "binary_sensor.pv_surplus"}


async def _cooling_now(hass: HomeAssistant, options: dict):
    await _setup(hass, options)
    _cooling_room(hass, "26.0")
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    return coord


async def test_cheap_power_lowers_the_cooling_targets(hass: HomeAssistant) -> None:
    coord = await _cooling_now(hass, CHEAP)
    hass.states.async_set("binary_sensor.pv_surplus", "on")
    await coord.async_refresh()
    bath = coord.data["rooms"]["badezimmer"]
    assert (bath.cool_target, bath.cool_target_reason) == (24.0, "cool_cheap_power")
    hass.states.async_set("binary_sensor.pv_surplus", "off")
    await coord.async_refresh()
    assert coord.data["rooms"]["badezimmer"].cool_target == 25.0


async def test_the_amount_of_cheap_power_is_an_option(hass: HomeAssistant) -> None:
    coord = await _cooling_now(hass, {**CHEAP, "cooling_cheap_power_k": 2.0})
    hass.states.async_set("binary_sensor.pv_surplus", "on")
    await coord.async_refresh()
    assert coord.data["rooms"]["badezimmer"].cool_target == 23.0


@pytest.mark.parametrize("state", ["unavailable", "unknown", None], ids=["unavailable", "unknown", "missing"])
async def test_cheap_power_that_cannot_be_read_counts_as_off(hass: HomeAssistant, state: str | None) -> None:
    coord = await _cooling_now(hass, CHEAP)
    if state is not None:
        hass.states.async_set("binary_sensor.pv_surplus", state)
    await coord.async_refresh()
    assert coord.data["rooms"]["badezimmer"].cool_target == 25.0


async def test_cheap_power_does_nothing_in_the_heating_season(hass: HomeAssistant) -> None:
    await _setup(hass, CHEAP)
    _cooling_room(hass, "26.0")
    coord = _coordinator(hass)
    hass.states.async_set("binary_sensor.pv_surplus", "on")
    await coord.async_refresh()
    bath = coord.data["rooms"]["badezimmer"]
    assert (bath.cool_target, bath.cool_target_reason) == (25.0, "cool_schedule_comfort")
    assert bath.target == coord.data["rooms"]["badezimmer"].target  # heating untouched


# --- stage 2: the automatic season (docs/PHASE12_COOLING.md §20.2) ---

from datetime import timedelta  # noqa: E402

from homeassistant.util import dt as dt_util  # noqa: E402

from custom_components.thriftherm.const import SEASON_AUTO  # noqa: E402
from custom_components.thriftherm.engines import season as sn  # noqa: E402

H = 3600.0


def _days(*verdicts: str | None) -> sn.FreeRunDays:
    """Stored days for the bathroom (it has a window contact), yesterday first, each giving the verdict asked for."""
    rooms = {
        sn.VERDICT_COOLING: sn.RoomDay(free_s=8 * H, warm_s=3 * H),
        sn.VERDICT_HEATING: sn.RoomDay(free_s=8 * H, cool_s=3 * H),
        sn.VERDICT_NEUTRAL: sn.RoomDay(free_s=8 * H),
        None: sn.RoomDay(free_s=H),
    }
    midnight = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
    days = sn.FreeRunDays()
    for back, v in enumerate(verdicts, start=1):
        start = midnight - timedelta(days=back)
        days.days[start.date().isoformat()] = sn.Day(start.timestamp(), {"badezimmer": rooms[v]})
    return days


async def _automatic(hass: HomeAssistant, options: dict | None = None, days_ago: float = 5.0):
    await _setup(hass, {**COOLING, **(options or {})})
    coord = _coordinator(hass)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.thriftherm_season", "option": SEASON_AUTO}, blocking=True
    )
    coord.season_since_ts = dt_util.now().timestamp() - days_ago * 24 * H
    coord._season_evaluated_ts = None  # judged at the next cycle, as after a restart
    return coord


async def test_the_season_select_offers_automatic(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    state = hass.states.get("select.thriftherm_season")
    assert state.attributes["options"] == [SEASON_HEATING, SEASON_COOLING, SEASON_AUTO]


async def test_choosing_automatic_keeps_the_season_in_force(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    assert (coord.season_choice, coord.season) == (SEASON_AUTO, SEASON_HEATING)
    state = hass.states.get("select.thriftherm_season")
    assert state.state == SEASON_AUTO and state.attributes["season"] == SEASON_HEATING


async def test_two_cooling_days_switch_the_automatic_season_to_cooling(hass: HomeAssistant, caplog) -> None:
    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, sn.VERDICT_NEUTRAL)
    await coord.async_refresh()
    assert (coord.season, coord.season_reason) == (SEASON_COOLING, sn.REASON_ROOMS_WARM_UP)
    assert "season" in caplog.text and sn.REASON_ROOMS_WARM_UP in caplog.text
    state = hass.states.get("select.thriftherm_season")
    assert state.attributes["season"] == SEASON_COOLING and state.attributes["reason"] == sn.REASON_ROOMS_WARM_UP
    assert state.attributes["verdicts"] == [sn.VERDICT_COOLING, sn.VERDICT_COOLING, sn.VERDICT_NEUTRAL]


async def test_a_season_chosen_by_hand_is_not_switched(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    await coord.async_set_season(SEASON_HEATING)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, sn.VERDICT_COOLING)
    await coord.async_refresh()
    assert (coord.season_choice, coord.season) == (SEASON_HEATING, SEASON_HEATING)


async def test_back_to_automatic_counts_the_72_hours_from_the_last_real_change(hass: HomeAssistant) -> None:
    coord = await _automatic(hass, days_ago=2.0)
    await coord.async_set_season(SEASON_HEATING)  # no change: heating was in force already
    await coord.async_set_season(SEASON_AUTO)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, None)
    await coord.async_refresh()
    assert coord.season == SEASON_HEATING  # 48 h since the last real change
    coord.season_since_ts -= 25 * H
    coord._season_evaluated_ts = None
    await coord.async_refresh()
    assert coord.season == SEASON_COOLING


async def test_the_automatic_season_is_judged_once_an_hour(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    await coord.async_refresh()
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, None)
    coord._season_evaluated_ts = dt_util.now().timestamp() - 59 * 60
    await coord.async_refresh()
    assert coord.season == SEASON_HEATING
    coord._season_evaluated_ts = dt_util.now().timestamp() - 60 * 60
    await coord.async_refresh()
    assert coord.season == SEASON_COOLING


async def test_frost_in_the_automatic_cooling_season_switches_to_heating_at_once(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, None)
    await coord.async_refresh()
    assert coord.season == SEASON_COOLING
    coord._season_evaluated_ts = dt_util.now().timestamp()  # judged just now: frost does not wait for the hour
    hass.states.async_set("sensor.bath_temp", "4.0", {"unit_of_measurement": "°C"})
    await coord.async_refresh()
    await coord.async_refresh()  # the frost list of one cycle is read by the next
    assert (coord.season, coord.season_reason) == (SEASON_HEATING, sn.REASON_FROST)


async def test_nothing_is_measured_without_automatic(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_refresh()
    await coord.async_refresh()
    assert coord.free_run.days == {} and coord.free_run.last_ts is None


async def test_the_automatic_season_measures_the_rooms(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    await coord.async_refresh()
    coord.free_run.last_ts -= 300  # five minutes since the last cycle
    await coord.async_refresh()
    today = coord.free_run.day_of(dt_util.now())
    assert "badezimmer" in today.rooms and "wohnzimmer" not in today.rooms  # only rooms with a window contact
    bath = today.rooms["badezimmer"]
    # five minutes, free or heated (the bathroom at 18.5 °C calls for heat in the test house), never both
    assert bath.free_s + bath.heat_s == pytest.approx(300.0, abs=5.0)


async def test_the_automatic_season_survives_a_restart(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, sn.VERDICT_NEUTRAL)
    await coord.async_refresh()
    before = (coord.season, coord.season_since_ts, coord.free_run.verdicts(dt_util.now()), coord.season_reason)
    await coord.async_save_store(force=True)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    again = _coordinator(hass)
    assert again.season_choice == SEASON_AUTO
    assert (again.season, again.season_since_ts, again.free_run.verdicts(dt_util.now()), again.season_reason) == before


async def test_an_unreachable_unit_last_seen_with_cool_still_counts_as_able_to_cool(hass: HomeAssistant) -> None:
    entry = await _setup(hass, COOLING)
    coord = _coordinator(hass)
    await coord.async_set_season(SEASON_COOLING)
    await coord.async_refresh()
    await coord.async_save_store(force=True)
    hass.states.async_set("climate.midea", "unavailable", {})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    again = _coordinator(hass)
    assert again.unit_can_cool and again.season == SEASON_COOLING
    assert SEASON_COOLING in hass.states.get("select.thriftherm_season").attributes["options"]


async def test_automatic_without_cooling_allowed_is_heating_measures_nothing_and_stays_chosen(hass: HomeAssistant) -> None:
    """K1 for the automatic: the cooling season of the store does not come back unasked."""
    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, None)
    await coord.async_refresh()
    assert coord.season == SEASON_COOLING
    await coord.async_save_store(force=True)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_HEAT_PUMP_COOLING_ALLOWED: False})
    await hass.async_block_till_done()
    again = _coordinator(hass)
    assert (again.season, again.season_choice) == (SEASON_HEATING, SEASON_AUTO)
    await again.async_refresh()
    assert again.free_run.days == {}


async def test_the_diagnostics_show_what_the_automatic_measured(hass: HomeAssistant) -> None:
    from custom_components.thriftherm.diagnostics import async_get_config_entry_diagnostics

    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, None, None)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    control = diag["control"]
    assert control["season_choice"] == SEASON_AUTO
    assert control["season_verdicts"] == [sn.VERDICT_COOLING, None, None]
    assert "badezimmer" in str(control["season_free_run"])


# --- stage 2: the cooling rate the unit reaches in a room (docs/PHASE12_COOLING.md §20.3) ---


async def test_the_learned_cooling_rates_show_in_the_learning_state_and_survive_a_restart(hass: HomeAssistant) -> None:
    entry = await _setup(hass, COOLING)
    coord = _coordinator(hass)
    coord.ac_cool_rates.rates["badezimmer"] = [0.8, 1.0, 1.2]
    coord.ac_cool_rates.gradients["badezimmer"] = [-5.0, -6.0, None]
    await coord.async_refresh()
    learning = hass.states.get("sensor.thriftherm_learning_state")
    assert learning.attributes["room_ac_cool_rates"] == {"badezimmer": {"k_per_h": 1.0, "episodes": 3}}
    await coord.async_save_store(force=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _coordinator(hass).ac_cool_rates.rate("badezimmer") == 1.0


async def test_resetting_the_rooms_forgets_the_learned_cooling_rates(hass: HomeAssistant) -> None:
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    coord.ac_cool_rates.rates.update({"badezimmer": [1.0], "wohnzimmer": [0.9]})
    await coord.async_reset_learning(["rooms"], ["badezimmer"])
    assert "badezimmer" not in coord.ac_cool_rates.rates and "wohnzimmer" in coord.ac_cool_rates.rates
    await coord.async_reset_learning(["rooms"])
    assert coord.ac_cool_rates.rates == {}


async def test_a_changed_room_sensor_forgets_its_learned_cooling_rate(hass: HomeAssistant) -> None:
    entry = await _setup(hass, COOLING)
    coord = _coordinator(hass)
    coord.ac_cool_rates.rates["badezimmer"] = [1.0, 1.0, 1.0]
    await coord.async_save_store(force=True)
    rooms = [dict(r) for r in entry.options[const.CONF_ROOMS]]
    for r in rooms:
        if r[const.CONF_ROOM_KEY] == "badezimmer":
            r[const.CONF_ROOM_TEMP] = "sensor.bath_temp_new"
    hass.config_entries.async_update_entry(entry, options={**entry.options, const.CONF_ROOMS: rooms})
    await hass.async_block_till_done()
    assert _coordinator(hass).ac_cool_rates.rate("badezimmer") is None


async def test_the_pre_cooling_plans_with_the_learned_rate(hass: HomeAssistant, monkeypatch) -> None:
    from custom_components.thriftherm.engines import room_cooling

    seen = []
    original = room_cooling.cooling_target
    monkeypatch.setattr(room_cooling, "cooling_target", lambda inp: seen.append(inp) or original(inp))
    await _setup(hass, COOLING)
    coord = _coordinator(hass)
    coord.ac_cool_rates.rates["badezimmer"] = [0.8, 1.0, 1.2]
    await coord.async_refresh()
    assert {i.rate_k_h for i in seen[-1:]} == {1.0}


async def _cooling_actively(hass: HomeAssistant, monkeypatch, ran: float | None, options: dict | None = None):
    from custom_components.thriftherm.const import CONF_HEAT_PUMP_ALLOW_ACTIVE
    from custom_components.thriftherm.engines import heat_pump_control

    monkeypatch.setattr(heat_pump_control, "ran_s", lambda unit, mem, now: ran)
    await _setup(hass, {**COOLING, CONF_HEAT_PUMP_ALLOW_ACTIVE: True, **(options or {})})
    hass.states.async_set(
        "climate.midea", "cool",
        {"hvac_modes": ["off", "cool", "heat", "fan_only"], "temperature": 26, "indoor_temperature": 27,
         "compressor_frequency": 24, "error_code": 0, "fan_modes": ["auto", "full"], "fan_mode": "auto"},
    )
    _cooling_room(hass, "26.5")
    async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_fan_mode")
    coord = _coordinator(hass)
    coord.set_control_mode("active")
    await coord.async_set_season(SEASON_COOLING)
    coord._started_ts -= 3600
    coord.season_since_ts -= 3600
    await coord.async_refresh()
    await coord.async_refresh()
    return coord


async def test_an_own_cooling_run_at_the_least_speed_begins_an_episode(hass: HomeAssistant, monkeypatch) -> None:
    coord = await _cooling_actively(hass, monkeypatch, ran=900.0)
    assert coord.data["midea_command"].plan == "cool"
    assert "badezimmer" in coord.ac_cool_rates.episodes


async def test_a_compressor_just_started_teaches_nothing(hass: HomeAssistant, monkeypatch) -> None:
    coord = await _cooling_actively(hass, monkeypatch, ran=300.0)
    assert "badezimmer" not in coord.ac_cool_rates.episodes


async def test_cheap_power_teaches_no_cooling_rate(hass: HomeAssistant, monkeypatch) -> None:
    hass.states.async_set("binary_sensor.pv_surplus", "on")
    cheap = {"cooling_cheap_power_entity": "binary_sensor.pv_surplus"}
    coord = await _cooling_actively(hass, monkeypatch, ran=900.0, options=cheap)
    assert "badezimmer" not in coord.ac_cool_rates.episodes


async def test_choosing_a_season_by_hand_drops_the_automatics_reason(hass: HomeAssistant) -> None:
    coord = await _automatic(hass)
    coord.free_run = _days(sn.VERDICT_COOLING, sn.VERDICT_COOLING, None)
    await coord.async_refresh()
    assert coord.season_reason == sn.REASON_ROOMS_WARM_UP
    await coord.async_set_season(SEASON_COOLING)
    assert (coord.season_choice, coord.season_reason) == (SEASON_COOLING, None)


@pytest.mark.parametrize(
    ("stored", "season", "choice", "reason"),
    [
        ({"season": "cooling", "season_choice": "foo"}, SEASON_COOLING, SEASON_COOLING, None),
        (
            {"season": "cooling", "season_choice": "auto", "season_reason": "season_rooms_warm_up"},
            SEASON_HEATING, SEASON_AUTO, None,
        ),
    ],
    ids=["damaged_choice", "k1_drops_the_reason"],
)
def test_the_season_choice_is_restored_safely(stored, season, choice, reason) -> None:
    from types import SimpleNamespace

    from custom_components.thriftherm import persistence

    allowed = stored["season_choice"] != "auto"
    coord = SimpleNamespace(cooling_allowed=allowed, season=SEASON_HEATING, season_since_ts=None, season_choice=SEASON_HEATING,
                            _store_dirty=False)
    persistence._restore_season(coord, stored, now=1000.0)
    assert (coord.season, coord.season_choice, coord.season_reason) == (season, choice, reason)


async def test_a_served_room_without_a_contact_learns_its_cooling_rate(hass: HomeAssistant, monkeypatch) -> None:
    """Review: its window counts as shut for the cooling, so it does for the learning too."""
    from .test_integration import _entry_data

    rooms = [{k: v for k, v in r.items() if k != const.CONF_ROOM_WINDOWS} for r in _entry_data()[const.CONF_ROOMS]]
    coord = await _cooling_actively(hass, monkeypatch, ran=900.0, options={const.CONF_ROOMS: rooms})
    bath = coord.data["rooms"]["badezimmer"]
    assert bath.window_open is None and bath.cooling_allowed  # cooled as shut
    assert "badezimmer" in coord.ac_cool_rates.episodes
