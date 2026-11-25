"""Review 2026-11-13 (config flow, adapters, entities, card): one failing test per finding worth one.

Each test asserts the behaviour that is right and failed on the code as it was, until the finding was fixed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thriftherm import const
from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import DOMAIN

from .test_config_flow import _open, _prefilled
from .test_integration import _answer, _entry_data, _set_states, _setup

START = datetime(2026, 1, 15, 12, 0, tzinfo=dt_util.UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _build(builder: SnapshotBuilder):
    return builder.build("auto", overrides={}, drying_states={}, away_return_ts=None, boosts={}, room_temps={})


async def _save_bathroom_unchanged_but_priority(hass: HomeAssistant, entry) -> dict:
    """Open the bathroom in the options, change only its priority, save; the room as stored afterwards."""
    result = await _open(hass, entry, "rooms")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room": "badezimmer", "action": "edit"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], _answer(result, {**_prefilled(result), "priority": 2}))
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    return next(r for r in entry.options["rooms"] if r["key"] == "badezimmer")


async def test_saving_the_room_form_keeps_the_cooling_schedule_it_does_not_show(hass: HomeAssistant) -> None:
    """Finding 1a: `room_from_input` (config_schema.py:749) builds the room from the answer alone.

    With COOLING_PREVIEW off the room form has no cooling schedule field, so saving a room for any
    other reason (here: its priority) drops a stored `cooling_schedule_entity` for good. The heat pump
    tuning form leaves its hidden cooling keys alone (keys_of); the room form does not.
    """
    assert const.COOLING_PREVIEW is False
    _set_states(hass)
    data = _entry_data()
    data["rooms"][0][const.CONF_ROOM_COOLING_SCHEDULE_ENTITY] = "schedule.bath_cool"
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    room = await _save_bathroom_unchanged_but_priority(hass, entry)

    assert room["priority"] == 2
    assert room.get(const.CONF_ROOM_COOLING_SCHEDULE_ENTITY) == "schedule.bath_cool"


async def test_saving_the_room_form_without_a_heat_pump_keeps_the_rooms_heat_pump_settings(hass: HomeAssistant) -> None:
    """Finding 1b: the same loss for "served by the heat pump" and the drying ticks.

    Without a heat pump configured the room form hides them (schema_room, heat_pump=False). A room saved
    in that time loses them: after the heat pump is entered again (another integration, another entity)
    it serves no room, and nothing says why.
    """
    entry = await _setup(hass)
    result = await _open(hass, entry, "midea")
    without_unit = {k: v for k, v in _prefilled(result).items() if k != "midea_climate"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], without_unit)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert not entry.options.get("midea_climate")

    room = await _save_bathroom_unchanged_but_priority(hass, entry)

    assert room.get(const.CONF_ROOM_HEAT_PUMP) is True


@pytest.mark.parametrize(
    ("value", "unit", "cubic_metres"),
    [
        pytest.param("52000", "kWh", None, id="an energy"),
        pytest.param("1234500", "L", 1234.5, id="litres"),
    ],
)
async def test_a_gas_meter_in_another_unit_is_not_read_as_cubic_metres(hass: HomeAssistant, value, unit, cubic_metres) -> None:
    """Finding 2: the gas meter's unit is never looked at (inputs.py:888-889).

    A reading in kWh is taken as m³ and multiplied by the calorific value once more (gas energy and the
    heating/hot water split about ten times too high), litres a thousand times. The power sensors got this
    check on 2026-11-08 (`read_power`); the gas meter did not. Right is the value in m³, or no reading.
    """
    _set_states(hass)
    hass.states.async_set("sensor.gas_volume", value, {"unit_of_measurement": unit, "state_class": "total_increasing"})
    builder = SnapshotBuilder(hass, {**_entry_data(), "gas_volume": "sensor.gas_volume"})

    reading = _build(builder).boiler.gas_volume_m3

    if cubic_metres is None:
        assert not reading.valid
    else:
        assert not reading.valid or reading.value == pytest.approx(cubic_metres)


async def test_a_power_sensor_in_another_unit_stays_refused_when_it_goes_quiet(hass: HomeAssistant, freezer) -> None:
    """Finding 3: `read_power` marks "wrong_unit" only on a reading that is otherwise valid (inputs.py:363-364).

    Six minutes after its last report the same sensor is "stale" instead, and `_heat_pump_state`
    (inputs.py:819-820) turns a stale value of 50 or less back into a valid one while the compressor
    stands: 8 VA count as 8 W from the plug, and the warning that names the unit is gone until the
    sensor reports again.
    """
    freezer.move_to(START)
    _set_states(hass)  # the unit stands: fan_only, compressor at 0
    hass.states.async_set("sensor.midea_power", "8.0", {"unit_of_measurement": "VA"})
    builder = SnapshotBuilder(hass, _entry_data())
    plug = _build(builder).heat_pump.plug_power
    assert (plug.valid, plug.reason, plug.unit) == (False, "wrong_unit", "VA")

    freezer.move_to(START + timedelta(minutes=10))
    plug = _build(builder).heat_pump.plug_power

    assert (plug.valid, plug.reason, plug.unit) == (False, "wrong_unit", "VA")
