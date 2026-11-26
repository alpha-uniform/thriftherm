"""Rückfall-Prüfung 2026-11-15: gemeldete Fehler (Issues #2-#8, B17) gegen die neuen Wege dieses Branches.

Neu sind die direkte Ventilsteuerung, das Rufband, das Brennerzeichen aus der Statusnummer und das Zurückstellen der
Ventile. Jeder Test hier stellt einen gemeldeten Fall auf einem dieser Wege nach. Die beiden Rückfälle R1 und R2 sind
behoben (docs/auswertungen/2026-11-15-rueckfall-pruefung.md).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.thriftherm.adapters.inputs import SnapshotBuilder
from custom_components.thriftherm.const import CONF_ROOM_SENSORS_ON_CHANGE
from custom_components.thriftherm.engines import heat_call
from custom_components.thriftherm.engines import room as room_engine
from custom_components.thriftherm.engines.heat_call import CallMemory

from .conftest import PARAMS
from .test_heat_call import NOW, rr, step
from .test_integration import _entry_data, _set_states, _setup
from .test_snapshot_builder import _register_bath_sensor
from .test_zha_last_seen import START, FakeZha, _ago, _install_helpers, _register_thermometer

RELEASED = {"midea_allow_active_control": True, "boiler_allow_active_control": True, "room_allow_active_control": True}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def zha(hass: HomeAssistant, monkeypatch) -> FakeZha:
    fake = FakeZha()
    _install_helpers(monkeypatch, hass, async_get_zha_device_proxy=fake.async_get_zha_device_proxy)
    return fake


# ------------------------------------------------------------------ issue #2: ZHA, the value that goes to the valve
@pytest.mark.parametrize("hours", [1.0, 3.0, 5.9])
async def test_issue2_a_zha_thermometer_seen_recently_goes_to_a_direct_valve_while_its_value_holds(
    hass: HomeAssistant, freezer, zha: FakeZha, hours: float
) -> None:
    """The fix of 0.6.7 on the new way: ZHA's own last seen keeps the room value for the valve as well."""
    thermometer = _register_thermometer(hass, "zha")
    freezer.move_to(START)
    _set_states(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(hours=hours))
    zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    assert builder.valve_room_reading("sensor.bath_temp", dt_util.utcnow()) == (18.5, True)


async def test_issue2_a_zha_thermometer_unchanged_for_six_and_a_half_hours_still_goes_to_a_direct_valve(
    hass: HomeAssistant, freezer, zha: FakeZha
) -> None:
    """Issue #2, part 2, as reported: temperature and humidity in HA just over six hours old, ZHA saw the device 42 s
    ago. For the call the reading holds (24 h, test_zha_last_seen); for the valve it must hold too, or the valve falls
    back from the external thermometer to its own, the very switch the issue reports."""
    thermometer = _register_thermometer(hass, "zha")
    freezer.move_to(START)
    _set_states(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(hours=6.5))
    zha.devices[thermometer] = (_ago(timedelta(seconds=42)), True)
    now = dt_util.utcnow()
    assert builder.read_room_temperature("sensor.bath_temp", now).valid  # the call: held, the fix of 0.6.7
    assert builder.valve_room_reading("sensor.bath_temp", now) == (18.5, True)


# ------------------------------------------------------------------ issue #7: a sensor that reports only on a change
async def test_issue7_a_sensor_reporting_only_on_change_goes_on_to_a_direct_valve_while_it_holds_for_the_call(
    hass: HomeAssistant, freezer
) -> None:
    """Issue #7: a Shelly H&T reports at 1 K or 1 % only, its whole device silent for hours. With the option set its
    last value is the room's temperature (test_snapshot_builder). PHASE13_VALVES.md, Frische: with the option the value
    goes to the valve while it holds by today's rules. Else Thriftherm switches the valve to its own sensor at the
    radiator, which shuts early, and the room the call holds at its target stays below it."""
    _register_bath_sensor(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "19.65")
    hass.states.async_set("sensor.bath_rh", "55")
    builder = SnapshotBuilder(hass, {**_entry_data(), CONF_ROOM_SENSORS_ON_CHANGE: True})
    freezer.move_to(START + timedelta(hours=2))
    now = dt_util.utcnow()
    assert builder.read_room_temperature("sensor.bath_temp", now).valid
    assert builder.valve_room_reading("sensor.bath_temp", now) == (19.65, True)


async def test_issue7_without_the_option_a_silent_sensor_is_no_value_for_the_valve(hass: HomeAssistant, freezer) -> None:
    """The other side, as intended: without the option, a sensor whose device is silent past 2 h sends nothing."""
    _register_bath_sensor(hass)
    freezer.move_to(START)
    _set_states(hass)
    builder = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(hours=2, minutes=5))
    assert builder.valve_room_reading("sensor.bath_temp", dt_util.utcnow())[1] is False


async def test_a_room_sensor_unchanged_but_alive_is_heard_and_a_silent_device_is_not(hass: HomeAssistant, freezer) -> None:
    """Seen 19.11.2026: the bathroom stood at 19.7 °C from 05:45, its Zigbee2MQTT sensor sending the humidity every few
    minutes but no unchanged temperature; after 90 minutes it counted as silent (issue #7's rule), and the boiler's
    limit added nothing more while the room did not rise. What counts is when the device was last heard: an unchanged
    value of a device alive is a room standing still. A Shelly H&T silent as a whole stays silent (issue #7)."""
    _register_bath_sensor(hass)
    freezer.move_to(START)
    _set_states(hass)
    hass.states.async_set("sensor.bath_temp", "19.7")
    hass.states.async_set("sensor.bath_rh", "45")
    builder = SnapshotBuilder(hass, _entry_data())
    freezer.move_to(START + timedelta(hours=2, minutes=30))
    hass.states.async_set("sensor.bath_rh", "44.8")
    freezer.move_to(START + timedelta(hours=2, minutes=31))
    reading = builder.read_room_temperature("sensor.bath_temp", dt_util.utcnow())
    assert reading.valid and reading.age_s == pytest.approx(9060.0) and reading.heard_s == pytest.approx(60.0)
    # with sensors that report only on a change the reading holds, but the device silent as a whole is not heard
    silent = SnapshotBuilder(hass, {**_entry_data(), CONF_ROOM_SENSORS_ON_CHANGE: True})
    freezer.move_to(START + timedelta(hours=5))
    reading = silent.read_room_temperature("sensor.bath_temp", dt_util.utcnow())
    assert reading.valid and reading.heard_s == pytest.approx(9000.0)  # the whole device silent 2.5 h: not heard


# ------------------------------------------------------------------ issues #3 and #8: Better Thermostat's word stays
def _bt_driving_a_trvzb_with_hysteresis(hass: HomeAssistant) -> SnapshotBuilder:
    """The bathroom on Better Thermostat (own device), which drives a TRVZB opening 1 K below its setpoint."""
    registry, devices = er.async_get(hass), dr.async_get(hass)
    radio = MockConfigEntry(domain="mqtt")
    radio.add_to_hass(hass)
    trv = devices.async_get_or_create(
        config_entry_id=radio.entry_id, identifiers={("mqtt", "trv_bath")}, manufacturer="SONOFF", model="TRVZB"
    )
    for domain, uid, object_id in (("climate", "trv_bath", "bath_trv"),
                                   ("number", "trv_bath_temperature_accuracy", "bath_trv_temperature_accuracy")):
        registry.async_get_or_create(domain, "mqtt", uid, suggested_object_id=object_id, device_id=trv.id,
                                     config_entry=radio)
    bt = MockConfigEntry(domain="better_thermostat", options={"thermostat": ["climate.bath_trv"]})
    bt.add_to_hass(hass)
    bt_device = devices.async_get_or_create(config_entry_id=bt.entry_id, identifiers={("better_thermostat", "bath")})
    registry.async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bath_bt",
                                 device_id=bt_device.id, config_entry=bt)
    _set_states(hass)
    hass.states.async_set("number.bath_trv_temperature_accuracy", "-1.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.bath_trv", "heat", {"temperature": 21.0, "current_temperature": 19.0,
                                                       "hvac_action": "idle"})
    data = _entry_data()
    data["rooms"][0]["climate_entity"] = "climate.bath_bt"
    return SnapshotBuilder(hass, data)


@pytest.mark.parametrize(
    ("attrs", "heating", "call"),
    [
        pytest.param({"hvac_action": "idle"}, False, heat_call.CALL_THERMOSTAT_IDLE, id="#3 BT idle"),
        pytest.param({"hvac_action": "heating", "heating_blocked_by": "valve_maintenance"}, False,
                     heat_call.CALL_THERMOSTAT_IDLE, id="#8 BT blocked"),
        pytest.param({"hvac_action": "heating", "heating_blocked_by": None}, True, heat_call.CALL_CALLING, id="BT heats"),
    ],
)
async def test_issue3_and_8_better_thermostat_keeps_its_say_although_its_valve_has_a_hysteresis(
    hass: HomeAssistant, attrs: dict, heating: bool, call: str
) -> None:
    """The branch sets `thermostat_heating=None` for direct rooms and for a room thermostat with a hysteresis. A room on
    Better Thermostat is neither, even when the valve behind it has one: BT's idle and its block still keep the room
    from calling the boiler (issue #3, #8)."""
    builder = _bt_driving_a_trvzb_with_hysteresis(hass)
    hass.states.async_set("climate.bath_bt", "heat", {"temperature": 21.0, "current_temperature": 18.5, **attrs})
    snap = builder.build("auto", {}, {}, None, {}, {})
    bath = snap.rooms["badezimmer"]
    assert bath.thermostat_heating is heating
    result = room_engine.evaluate_room(bath, snap.now, snap.mode, PARAMS)
    state, _ = heat_call.update(result, CallMemory(), snap.now.timestamp())
    assert state == call


@pytest.mark.parametrize(("on_k", "off_k"), [(0.3, 0.1), (0.5, 0.1), (0.3, -0.2)])
def test_issue3_the_band_of_the_call_keeps_an_idle_thermostat_from_calling(on_k: float, off_k: float) -> None:
    """Whatever band is set, a thermostat that reports its valve shut makes no boiler call (issue #3)."""
    shut = replace(rr(temp=20.0), thermostat_heating=False)
    state, mem = step(shut, CallMemory(), call_on_k=on_k, call_off_k=off_k)
    assert state == heat_call.CALL_THERMOSTAT_IDLE
    state, _ = step(replace(shut, thermostat_heating=True), mem, NOW + 60, call_on_k=on_k, call_off_k=off_k)
    assert state == heat_call.CALL_CALLING


# ------------------------------------------------------------------ issue #6: the bathroom's short block
@pytest.mark.parametrize(("on_k", "off_k"), [(0.3, 0.1), (0.5, 0.1), (0.3, -0.2)])
def test_issue6_a_limited_preheat_calls_to_the_end_of_its_block_with_any_band(on_k: float, off_k: float) -> None:
    """Issue #6: a block of 30 minutes with its preheat cut short must call within the block, whatever the band."""
    short = replace(rr(temp=20.0), short_preheat=True)
    state, _ = step(short, CallMemory(), NOW, NOW + 1200.0, call_on_k=on_k, call_off_k=off_k)
    assert state == heat_call.CALL_CALLING


# ------------------------------------------------------------------ issue #5: hot water and overheating
async def test_issue5_a_hot_water_run_by_the_status_number_counts_no_start_and_is_no_heating(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The new burner sign without a gas meter: hot water (Vaillant S.11-S.14) is never a heating start, and *Therme
    Heizbetrieb aktiv* stays off while the boiler makes hot water, also straight out of a heating run (a combi's
    priority)."""
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set("sensor.statenumber", "0")
    entry = await _setup(hass, options={"boiler_state_number": "sensor.statenumber", "gas_flow": None})
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    before = coordinator.data["boiler_command"].starts_today
    cycles: list[str] = []

    async def counted() -> None:
        cycles.append(hass.states.get("sensor.statenumber").state)
        await coordinator.async_refresh()

    monkeypatch.setattr(coordinator, "async_request_refresh", counted)
    for code in ("11", "13", "14"):
        hass.states.async_set("sensor.statenumber", code)
        await hass.async_block_till_done()
    assert cycles == []  # no burner for the heating went on or off
    await coordinator.async_refresh()
    assert hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active").state == "off"
    assert hass.states.get("binary_sensor.thriftherm_boiler_hot_water_active").state == "on"
    hass.states.async_set("sensor.statenumber", "0")
    await hass.async_block_till_done()
    assert coordinator.data["boiler_command"].starts_today == before
    # a heating run handing over to hot water (a combi's priority): heating ends with S.14, no second start
    for code in ("4", "14", "0"):
        hass.states.async_set("sensor.statenumber", code)
        await hass.async_block_till_done()
        if code == "14":
            assert hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active").state == "off"
    assert cycles == ["4", "14"]
    # within seconds the heating burn is taken for the start of the hot water and dropped (drop_burn_in_progress,
    # older than this branch): at most one start, never two
    assert coordinator.data["boiler_command"].starts_today <= before + 1


async def test_issue5_an_overheating_between_two_cycles_is_seen_without_a_gas_meter(hass: HomeAssistant) -> None:
    """01.11.: 112.94 °C between two cycles. With the status number as burner sign (no gas meter) the flow is still
    watched between the cycles, and S.53 is still a wait for water, not hot water nor a heating start."""
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set("sensor.statenumber", "4")
    options = {**RELEASED, "boiler_state_number": "sensor.statenumber", "gas_flow": None}
    coordinator = (await _setup(hass, options=options)).runtime_data
    await coordinator.async_refresh()
    for value in ("96.25", "112.94", "93.75"):
        hass.states.async_set("sensor.flow", value, {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
    assert coordinator.boiler_fault.overheat_c == 112.94
    hass.states.async_set("sensor.statenumber", "53")
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    data = coordinator.data
    assert "boiler_overheated" in data["safety"].issues
    assert not data["boiler_command"].hot_water
    assert hass.states.get("binary_sensor.thriftherm_boiler_space_heating_active").state == "off"


# ------------------------------------------------------------------ B17: no flood of cycles
async def test_b17_with_a_gas_meter_the_status_number_asks_for_no_cycle(hass: HomeAssistant, monkeypatch) -> None:
    """With a gas meter the gas decides the burner (B17 debounced it); the status number ticking through a run asks for
    no cycle of its own, or a run would be two cycles more on top of the gas meter's."""
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set("sensor.statenumber", "0")
    entry = await _setup(hass, options={"boiler_state_number": "sensor.statenumber"})
    coordinator = entry.runtime_data
    asked: list[int] = []

    async def count() -> None:
        asked.append(1)

    monkeypatch.setattr(coordinator, "async_request_refresh", count)
    for code in ("1", "2", "3", "4", "7", "8", "0", "13", "14", "0"):
        hass.states.async_set("sensor.statenumber", code)
        await hass.async_block_till_done()
    assert asked == []


async def test_b17_a_status_number_ticking_through_many_runs_asks_for_two_cycles_a_run(hass: HomeAssistant, monkeypatch) -> None:
    """Without a gas meter: ten short runs, each through S.1-S.4, S.7, S.8 and back to S.0, are twenty cycles, one at the
    burner's start and one at its end, not one for every code (the flood of B17 was one every twelve seconds)."""
    async_mock_service(hass, "mqtt", "publish")
    hass.states.async_set("sensor.statenumber", "0")
    entry = await _setup(hass, options={"boiler_state_number": "sensor.statenumber", "gas_flow": None})
    coordinator = entry.runtime_data
    asked: list[int] = []

    async def count() -> None:
        asked.append(1)

    monkeypatch.setattr(coordinator, "async_request_refresh", count)
    for _ in range(10):
        for code in ("1", "2", "3", "4", "4", "7", "8", "0"):
            hass.states.async_set("sensor.statenumber", code)
            await hass.async_block_till_done()
    assert len(asked) == 20


# ------------------------------------------------------------------ 0.7.5: a thermostat back after a restart is no hand
async def test_a_valve_handed_back_at_a_stop_is_no_hand_after_the_restart(hass: HomeAssistant, freezer) -> None:
    """The hand-back of this branch and the fix of 0.7.5 together: Home Assistant stops, the calling valve gets the
    room's target back instead of Thriftherm's margin. After the restart that target is no turn of the knob: no
    override, and the valve has Thriftherm's setpoint again."""
    from custom_components.thriftherm import PENDING_LET_GO

    from .test_hand import _cycle
    from .test_valve_direct import _calling_with_accuracy

    coordinator, bath = await _calling_with_accuracy(hass, freezer)
    assert bath.target_temperature == 21.5
    entry = coordinator.config_entry
    for job in list(hass._shutdown_jobs):  # stage 1 of a stop, as in test_valve_direct._given_back
        if (task := hass.async_run_hass_job(job.job, *job.args)) is not None:
            await task
    await hass.async_block_till_done()
    assert bath.target_temperature == 21.0  # handed back: the target, not the margin
    await coordinator.async_save_store(force=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.get(PENDING_LET_GO, {}).clear()  # Home Assistant restarted: nothing is parked
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 70)
    assert "badezimmer" not in coordinator.overrides
    assert bath.target_temperature == 21.5


async def test_issue3_and_8_a_room_thermostat_of_better_thermostat_never_counts_a_hysteresis(hass: HomeAssistant) -> None:
    """Check of 2026-11-15, risk without a test: a hysteresis found on the device of Better or Versatile Thermostat
    would set the thermostat's word aside (issue #3) and with it the block of BT 2.0 (issue #8). Theirs is their own
    decision; only a valve's own threshold is Thriftherm's to allow for."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.thriftherm.adapters.inputs import valve_view

    owner = MockConfigEntry(domain="better_thermostat")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=owner.entry_id, identifiers={("better_thermostat", "x")})
    registry = er.async_get(hass)
    registry.async_get_or_create("climate", "better_thermostat", "bt_bath", suggested_object_id="bt_bath",
                                 device_id=device.id, config_entry=owner)
    registry.async_get_or_create("number", "better_thermostat", "bt_bath_temperature_accuracy",
                                 suggested_object_id="bt_bath_temperature_accuracy", device_id=device.id, config_entry=owner)
    hass.states.async_set("climate.bt_bath", "heat", {"current_temperature": 20.0, "temperature": 21.0})
    hass.states.async_set("number.bt_bath_temperature_accuracy", "-1.0", {"unit_of_measurement": "°C"})
    assert valve_view(hass, "climate.bt_bath").hysteresis_k == 0.0


# ------------------------------------------------------------------ heating along, the valve open in part (decided 16.11.)
async def test_a_valve_heating_along_opens_in_part_a_call_opens_it_fully_and_observe_only_gives_it_back(
    hass: HomeAssistant, freezer
) -> None:
    """The one rule holds: a room that calls has its valve open as the person set it, never limited (no call without an
    open valve). Heating along it opens in part; once Thriftherm only observes, the valve gets its opening back and
    nothing more is written."""
    from .test_hand import _cycle
    from .test_valve_direct import _direct

    coordinator, _, calls = await _direct(hass, freezer, room={"served_by_midea": False})
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    # the valve shows the room's temperature as its source, as it does once it took it
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature"]})

    def openings() -> list[float]:
        return [c.data["value"] for c in calls["number"] if c.data["entity_id"] == "number.bath_heating_valve_position"]

    hass.states.async_set("sensor.living_temp", "17.0")  # the living room calls: the boiler runs
    hass.states.async_set("sensor.bath_temp", "20.9")  # the bathroom 0.1 below its 21: no call of its own
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 60)
    assert coordinator.data["rooms"]["badezimmer"].target_reason == heat_call.JOINED_COMFORT
    assert openings()[-1] == 30.0
    hass.states.async_set("number.bath_heating_valve_position", "30", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bath_temp", "20.5")  # now it calls itself: fully open, as the person set it
    await _cycle(hass, coordinator, freezer, 60)
    assert coordinator.data["rooms"]["badezimmer"].boiler_call == heat_call.CALL_CALLING
    assert openings()[-1] == 100.0
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bath_temp", "20.9")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 60)
    assert openings()[-1] == 30.0
    hass.states.async_set("number.bath_heating_valve_position", "30", {"unit_of_measurement": "%"})
    coordinator.set_room_control_mode("shadow")  # observe only: given back, then nothing more
    await hass.async_block_till_done()
    assert openings()[-1] == 100.0
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    written = len(calls["number"])
    for _ in range(3):
        await _cycle(hass, coordinator, freezer, 60)
    assert len(calls["number"]) == written
    # the child lock and the mode were never touched
    assert not calls["switch_on"] and not calls["switch_off"] and not calls["lock"]


# ------------------------------------------------------------------ 0.8.2: in part by the flow
async def test_a_calling_room_that_does_not_set_the_flow_opens_in_part_and_fully_once_it_falls_behind(
    hass: HomeAssistant, freezer, monkeypatch
) -> None:
    """The living room far below its target sets the flow. The bathroom calls 0.3 below its own and would need far
    less: its valve opens in part, and fully as soon as it lies half a degree below. A boiler only observed heats by its
    knob: nothing is limited by a flow Thriftherm does not send."""
    from .test_hand import _cycle
    from .test_valve_direct import _direct

    coordinator, _, calls = await _direct(hass, freezer, room={"served_by_midea": False})
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature"]})

    def openings() -> list[float]:
        return [c.data["value"] for c in calls["number"] if c.data["entity_id"] == "number.bath_heating_valve_position"]

    from custom_components.thriftherm.engines import boiler_control

    seen: list[dict] = []
    decide = boiler_control.decide

    def noting(inp, mem):
        seen.append({r.key: r.valve_limited_s for r in inp.rooms if r.valve_limited_s is not None})
        return decide(inp, mem)

    monkeypatch.setattr(boiler_control, "decide", noting)
    hass.states.async_set("sensor.living_temp", "15.0")  # far enough below that the bathroom needs 80 % at most
    hass.states.async_set("sensor.bath_temp", "20.7")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 60)
    data = coordinator.data
    assert data["rooms"]["badezimmer"].boiler_call == heat_call.CALL_CALLING
    assert data["boiler_command"].flow_set_by == "wohnzimmer" and data["boiler_command"].throttle_rooms == ("badezimmer",)
    assert 20.0 <= openings()[-1] <= 70.0 and openings()[-1] % 10 == 0 and data["valves_by_flow"] == {"badezimmer": openings()[-1]}
    hass.states.async_set("number.bath_heating_valve_position", str(openings()[-1]), {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bath_temp", "20.5")  # half a degree below: fully open, as the person set it
    await _cycle(hass, coordinator, freezer, 60)
    assert openings()[-1] == 100.0 and coordinator.data["valves_by_flow"] == {}
    # fallen behind its limited valve: the next time it gets ten points more, and that is stored, shown and forgotten
    from custom_components.thriftherm import learning_reset, persistence

    assert coordinator.flow_throttle["badezimmer"].adjust_pct == 10.0
    assert coordinator.data["flow_adjust"] == {"badezimmer": 10.0}
    assert persistence.to_store(coordinator)["flow_throttle"] == {"badezimmer": {"adjust_pct": 10.0}}
    first = openings()[-2]
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bath_temp", "20.7")
    for _ in range(12):  # ten minutes on a smaller opening may come
        await _cycle(hass, coordinator, freezer, 60)
    assert openings()[-1] == first + 10.0
    hass.states.async_set("number.bath_heating_valve_position", str(openings()[-1]), {"unit_of_measurement": "%"})
    await coordinator.async_reset_learning({learning_reset.SCOPE_ROOMS})
    assert coordinator.flow_throttle["badezimmer"].adjust_pct == 0.0 and coordinator.data["flow_adjust"] == {}
    hass.states.async_set("sensor.bath_temp", "20.5")
    await _cycle(hass, coordinator, freezer, 60)
    assert openings()[-1] == 100.0
    # what the boiler is told a cycle later: limited a minute ago, so its slow rise is no sign of the flow yet
    assert seen[0] == {} and seen[-1] == {"badezimmer": 60.0}
    hass.states.async_set("number.bath_heating_valve_position", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bath_temp", "20.7")
    coordinator.set_boiler_control_mode("shadow")
    await hass.async_block_till_done()
    written = len(openings())
    for _ in range(12):
        await _cycle(hass, coordinator, freezer, 60)
    assert len(openings()) == written
    assert not calls["switch_on"] and not calls["switch_off"] and not calls["lock"]


async def test_the_opening_of_a_valve_heating_along_survives_a_restart_to_be_given_back(hass: HomeAssistant, freezer) -> None:
    from custom_components.thriftherm import persistence
    from custom_components.thriftherm.engines.valve_plan import ValveMemory

    from .test_hand import _cycle
    from .test_valve_direct import _direct

    coordinator, _, _ = await _direct(hass, freezer, room={"served_by_midea": False})
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "70", {"unit_of_measurement": "%"})  # the person's 70
    hass.states.async_set("select.bath_temperature_sensor_select", "remote_temperature",
                          {"options": ["local_temperature", "remote_temperature"]})
    hass.states.async_set("sensor.living_temp", "17.0")
    hass.states.async_set("sensor.bath_temp", "20.9")
    await _cycle(hass, coordinator, freezer, 60)
    await _cycle(hass, coordinator, freezer, 60)
    stored = persistence.to_store(coordinator)["valve_ctrl_memory"]["climate.bath"]
    back = ValveMemory.from_storage(stored)
    assert (back.opening_pct, back.opening_before) == (30.0, 70.0)


def test_a_bedroom_with_one_slow_heat_up_preheats_no_five_and_a_half_hours():
    """16.11. 11:12: the bedroom heated early from about 10:55 for 17:30 (20.0 to 21.0 °C) on 0.196 K/h from one heat-up
    at a cycling boiler. With the default in the places of the two heat-ups missing the preheat is planned 1.25 hours
    ahead, not 5.1."""
    from custom_components.thriftherm.engines.learning import HeatRateStats

    rates = HeatRateStats(rates={"schlafzimmer": {"boiler": [0.196]}})
    rate = rates.trusted_rate("schlafzimmer", "boiler", 0.8)
    comfort = 17.5 * 3600.0
    start = room_engine.preheat_start(comfort, 21.0, 20.0, rate, margin_s=0.0)
    assert (comfort - start) / 3600.0 == pytest.approx(1.0 / 0.8, abs=0.05)
    alone = room_engine.preheat_start(comfort, 21.0, 20.0, rates.rate("schlafzimmer", "boiler"), margin_s=0.0)
    assert (comfort - alone) / 3600.0 > 5.0  # as it was planned


async def test_a_room_heating_up_to_a_raised_target_is_remembered_through_a_restart(hass: HomeAssistant) -> None:
    """Heating up is no holding for the heating curve (16.11.): a restart in the middle must not make it holding."""
    from custom_components.thriftherm import persistence

    coordinator = (await _setup(hass, options=RELEASED)).runtime_data
    coordinator._heating_up = {"badezimmer"}
    stored = persistence.to_store(coordinator)
    assert stored["heating_up"] == ["badezimmer"]
    coordinator._heating_up = set()
    persistence.restore(coordinator, stored)
    assert coordinator._heating_up == {"badezimmer"}


def test_a_preheating_room_on_its_plan_gets_no_step_of_the_curve_one_behind_it_does():
    """16.11. 11:50: the study, preheating at 0.15 K/h on a plan of 0.117, got a step and a lift of +4.6 K. On its plan
    it now counts as holding nothing: no step. Behind its plan (the mixed rate of 0.57) a step may come, on trial."""
    from custom_components.thriftherm.engines import boiler_load as load

    from .test_boiler_control import rr as boiler_room

    study = replace(boiler_room(key="arbeitszimmer", temp=19.4, target=20.0, demand=0.3, trend=0.15),
                    boiler_call="calling", calling_s=7200.0, target_steady_s=7200.0, preheat_for_ts=1.0,
                    preheat_pace_k_per_h=0.15, heat_rate_k_h=0.117)
    assert load.lagging_rooms((study,)) == []
    assert load.lagging_rooms((replace(study, heat_rate_k_h=0.57),)) != []


def test_a_bathroom_at_a_cycling_boiler_preheats_four_hours_at_most():
    """17.11.: the bathroom learned 0.1 K/h from a night it stood still at a cycling boiler; with a second such night it
    would have planned on 0.1 K/h and preheated ten hours for one degree. A longer preheat gains nothing there, it costs
    gas: four hours at most, as with the heat pump, unless the room is given a longer one of its own."""
    from datetime import datetime

    from custom_components.thriftherm.engines.learning import HeatRateStats

    from .conftest import PARAMS, room_config, room_state

    rates = HeatRateStats(rates={"badezimmer": {"boiler": [1.987, 0.101, 0.1]}})
    rate = rates.trusted_rate("badezimmer", "boiler", 0.8)
    assert rate == pytest.approx(0.101, abs=0.001)
    cfg = room_config("badezimmer", comfort=21.0, setback=20.0)  # the boiler's: no heat pump
    st = room_state(cfg, temp=19.4)
    st = st.__class__(**{**st.__dict__, "heat_rate_k_h": rate})
    comfort = datetime(2026, 1, 12, 5, 15)  # a weekday's comfort
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 0, 0), "auto", PARAMS)
    assert res.preheat_start_ts == pytest.approx(comfort.timestamp() - room_engine.PREHEAT_LEAD_MAX_S, abs=1)
    own = st.__class__(**{**st.__dict__, "config": replace(cfg, preheat_max_s=6 * 3600.0)})
    longer = room_engine.evaluate_room(own, datetime(2026, 1, 11, 22, 0), "auto", PARAMS)
    assert longer.preheat_start_ts == pytest.approx(comfort.timestamp() - 6 * 3600.0, abs=1)  # its own counts beyond


def test_a_learned_rate_at_the_floor_is_planned_with_no_less_than_the_lagging_rate():
    """Review 2026-11-18: the bathroom learned 0.1 K/h at a cycling boiler. Planned with that, every rise from 0.1 an
    hour was "on plan" while heating up, so the curve never learned; a holding room lags under 0.2 already. A plan is
    never below 0.2 K/h (boiler_load.LAG_RATE_K_H): heating up at 0.12 the bathroom counts as lagging."""
    from datetime import datetime

    from custom_components.thriftherm.engines import boiler_load as load

    from .conftest import PARAMS, room_config, room_state
    from .test_boiler_control import rr as boiler_room

    st = room_state(room_config("badezimmer", comfort=21.0, setback=20.0), temp=19.4)
    st = st.__class__(**{**st.__dict__, "heat_rate_k_h": 0.1})
    res = room_engine.evaluate_room(st, datetime(2026, 1, 12, 0, 0), "auto", PARAMS)
    assert res.heat_rate_k_h == room_engine.PLAN_RATE_MIN_K_H == load.LAG_RATE_K_H
    bath = replace(boiler_room(key="badezimmer", temp=19.6, target=21.0, demand=0.6, trend=0.12),
                   boiler_call="calling", calling_s=7200.0, target_steady_s=7200.0, preheat_for_ts=1.0,
                   preheat_pace_k_per_h=0.12, heat_rate_k_h=res.heat_rate_k_h)
    assert load.lagging_rooms((bath,)) != []


async def test_the_heat_demand_shows_how_far_a_valve_with_an_opening_limit_may_open(hass: HomeAssistant, freezer) -> None:
    """For the card (0.8.2): what the valve shows, the person's limit as well as Thriftherm's; nothing without one."""
    from .test_hand import _cycle
    from .test_valve_direct import _direct

    coordinator, _, _ = await _direct(hass, freezer, room={"served_by_midea": False})
    await _cycle(hass, coordinator, freezer, 60)
    demand = "sensor.thriftherm_badezimmer_heat_demand"
    assert hass.states.get(demand).attributes["valve_opening_pct"] is None  # no opening entity: nothing to show
    registry = er.async_get(hass)
    device = registry.async_get("climate.bath").device_id
    registry.async_get_or_create("number", "mqtt", "trv_bath_heating_valve_position",
                                 suggested_object_id="bath_heating_valve_position", device_id=device)
    hass.states.async_set("number.bath_heating_valve_position", "60", {"unit_of_measurement": "%"})
    await _cycle(hass, coordinator, freezer, 60)
    assert hass.states.get(demand).attributes["valve_opening_pct"] == 60.0
