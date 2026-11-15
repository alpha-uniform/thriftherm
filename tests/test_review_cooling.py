"""Findings of the code review of 2026-11-13 on cooling (docs/auswertungen/2026-11-13-code-review.md).

Every test here shows one finding; each failed until its finding was fixed (2026-11-13, once approved).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.thriftherm import const
from custom_components.thriftherm.const import CONF_HEAT_PUMP_COOLING_ALLOWED, DOMAIN, SEASON_COOLING, SEASON_HEATING
from custom_components.thriftherm.engines.heat_pump_control import ADJUST_MIN_INTERVAL_S, ControlMemory
from custom_components.thriftherm.engines.heat_pump_cooling import decide

from .test_heat_pump_cooling import NOW, RUN, RUNNING_UNIT, cin, room, unit
from .test_integration import _setup

COOLING = {CONF_HEAT_PUMP_COOLING_ALLOWED: True}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def cooling_shown(monkeypatch):
    monkeypatch.setattr(const, "COOLING_PREVIEW", True)


def _coordinator(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data


async def test_K1_cooling_allowed_again_does_not_bring_back_last_summers_cooling_season(hass: HomeAssistant) -> None:
    """K1: the season "cooling" stays in the store while cooling is not allowed, and is in force again the moment it is.

    Summer: season "cooling". Autumn: "Allow cooling" switched off instead of the season (heating works, §2.1). The
    next time the option is switched on (or a release turns COOLING_PREVIEW on), the cooling season is in force at once,
    without anybody choosing it: every radiator at frost protection, the boiler blocked, no pause after the change.
    """
    entry = await _setup(hass, COOLING)
    await _coordinator(hass).async_set_season(SEASON_COOLING)
    await _coordinator(hass).async_save_store(force=True)
    # the options hold the whole configuration since the migration to version 4
    options = dict(entry.options)
    hass.config_entries.async_update_entry(entry, options={**options, CONF_HEAT_PUMP_COOLING_ALLOWED: False})  # taken back
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not _coordinator(hass).cooling_season  # as the concept says: heating at once
    hass.config_entries.async_update_entry(entry, options=options)  # months later: allowed again
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coord = _coordinator(hass)
    assert coord.cooling_allowed
    assert coord.season == SEASON_HEATING, "the cooling season of last summer is in force again, unasked"
    assert coord.data["rooms"]["wohnzimmer"].target_reason != "cooling_season"


def test_K4_turned_up_fully_the_setpoint_does_not_follow_the_units_reading_down_at_once():
    """K4: a room two degrees too warm turns the unit up "at once" again every time its own reading falls half a degree.

    The reading at the indoor unit falls fast at full power. Each half degree is a new setpoint (three below the
    reading), sent past the ten minutes between adjustments: eight commands and beeps in half an hour in the
    review's run, the setpoint ratcheting down to 16 °C. §8 promises steps every ten minutes when turning up.
    """
    warm = room(temp=27.5, target=25.0, trend=-1.0)
    cmd, mem = decide(cin([warm], heat_pump=RUNNING_UNIT), RUN)
    assert (cmd.action, cmd.target_temp, cmd.reason) == ("adjust", 22.0, "cool_lift")  # 25 - 3: at once, as wanted
    later = NOW + 120
    fallen = unit("cool", 60.0, reading=24.5, setpoint=22.0, running_s=3720.0)
    cmd, mem = decide(cin([warm], heat_pump=fallen, now=later), mem)
    assert later - NOW < ADJUST_MIN_INTERVAL_S
    assert cmd.action == "none", f"a second setpoint ({cmd.target_temp} °C) two minutes after the first"


def test_K6_a_step_up_that_could_not_go_out_is_not_on_record_as_made():
    """K6: a step of "turn up" held back by the ten minutes between adjustments is remembered as carried out.

    Nothing was sent, yet the memory says `cool_lifted` and restarts the ten minutes of the lag: the card shows
    "turned up" at the unchanged setpoint, the step comes up to ten minutes late, and the hold at its least speed
    (reading rose, reading fell, stopped itself) is skipped meanwhile.
    """
    lagging = room(temp=25.5, target=25.0, trend=-0.1)
    mem = replace(RUN, last_command_ts=NOW - 300, cool_warm_since_ts=NOW - 700)
    cmd, after = decide(cin([lagging], heat_pump=RUNNING_UNIT), mem)
    assert cmd.action == "none" and cmd.target_temp == 26.0  # nothing went out
    assert after.cool_lifted is False
    assert cmd.reason != "cool_lift"


async def test_K2_a_unit_cooling_by_hand_teaches_no_cool_down_coefficient_where_cooling_is_hidden(
    hass: HomeAssistant, freezer, monkeypatch
) -> None:
    """K2: the guard of §7.1 ("any unit in cool, ours or by hand, starts no episode") works only where cooling is allowed.

    As shipped (COOLING_PREVIEW off) nobody can allow it. A summer evening, 26 °C inside, 18 °C outside, the air
    conditioner cooling by hand: the bathroom falls 1.2 K in two hours and that is learned as its heat loss to
    outside, a coefficient several times too large. It feeds the planning rate of heat pump preheats.
    """
    from datetime import timedelta

    monkeypatch.setattr(const, "COOLING_PREVIEW", False)  # as shipped
    await _setup(hass)
    coord = _coordinator(hass)
    hass.states.async_set("sensor.outdoor", "18.0")
    hass.states.async_set("sensor.living_temp", "26.0")
    hass.states.async_set(
        "climate.midea", "cool",
        {"hvac_modes": ["off", "cool", "heat", "fan_only"], "temperature": 22, "indoor_temperature": 24,
         "compressor_frequency": 40, "error_code": 0},
    )
    for step in range(13):  # two hours, ten minutes apart
        hass.states.async_set("sensor.bath_temp", f"{26.0 - step * 0.1:.1f}")
        await coord.async_refresh()
        freezer.tick(timedelta(minutes=10))
    hass.states.async_set("sensor.bath_temp", "25.5")  # warming again closes the stretch
    await coord.async_refresh()
    assert coord.cool_rates.rate("badezimmer") is None, "the air conditioner's cooling was learned as heat loss"


def test_K7_a_room_switched_off_on_its_room_climate_is_not_cooled_however_warm_it_is():
    """K7: "off" on the room climate, and 32 °C outside the cooling schedule ("do not cool", §3.1), are a target of 32.

    The room counts as any other (§4.2), so a room at 33 °C (under a roof, in a heat wave) starts the unit although
    it was switched off, and a room at 31.6 °C keeps a running unit from resting.
    """
    from custom_components.thriftherm.climate import COOL_OFF_TARGET
    from custom_components.thriftherm.engines import room_cooling as rc
    from custom_components.thriftherm.models import Override

    from .test_room_cooling import MON_15, inputs

    now = MON_15.timestamp()
    off = Override(COOL_OFF_TARGET, now + 3600.0, now, "action")  # what RoomClimate.async_set_hvac_mode(off) sets
    target = rc.cooling_target(inputs(temperature=33.0, override=off)).target
    cmd, _ = decide(cin([room(temp=33.0, target=target)]), ControlMemory())
    assert cmd.action == "none", "the unit starts for a room that was switched off"


async def test_K9_taking_cooling_back_in_the_cooling_season_is_a_change_of_season(hass: HomeAssistant) -> None:
    """K9: "Allow cooling" taken back while the season is cooling is a change to heating, with its pause (§2.1).

    The heating control was given no time of the change then, and heated after the minimum pause alone.
    """
    import time

    entry = await _setup(hass, COOLING)
    await _coordinator(hass).async_set_season(SEASON_COOLING)
    _coordinator(hass).season_since_ts = time.time() - 30 * 86400.0  # cooling since a month
    await _coordinator(hass).async_save_store(force=True)
    taken_back = time.time()
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_HEAT_PUMP_COOLING_ALLOWED: False})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coord = _coordinator(hass)
    assert coord.season == SEASON_HEATING and coord.season_since_ts >= taken_back
    seen = {}
    from custom_components.thriftherm.engines import heat_pump_control

    real = heat_pump_control.decide

    def spy(inputs, memory):
        seen["since"] = inputs.season_since_ts
        return real(inputs, memory)

    heat_pump_control.decide, restore = spy, real
    try:
        await coord.async_refresh()
    finally:
        heat_pump_control.decide = restore
    assert seen["since"] == coord.season_since_ts  # the heating control waits the pause after the change
