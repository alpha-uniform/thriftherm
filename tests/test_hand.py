"""Changes by hand: who changed a commanded setting, and what follows, for every device alike.

The Home Assistant tests use real climate entities, so Home Assistant itself gives the
state changes their context, as it does for a real thermostat.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockPlatform, mock_platform

from custom_components.thriftherm import persistence
from custom_components.thriftherm.const import CONF_ROOMS, DOMAIN
from custom_components.thriftherm.engines import hand
from custom_components.thriftherm.engines.heat_pump_control import (
    NOT_CARRIED_OUT,
    SETTLING,
    ControlMemory,
    changed_by,
    decide,
)
from custom_components.thriftherm.engines.room_control import RoomCtrlMemory, manual_change, own_answer
from custom_components.thriftherm.engines.room_control import decide as decide_room
from custom_components.thriftherm.models import SettingChange

from .conftest import PARAMS, heat_pump_state
from .test_heat_pump_control import NOW, RUNNING, inp, rr
from .test_integration import _entry_data, _set_states
from .test_room_control import rr as room_result


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# ------------------------------------------------------------------ who it was
def test_the_origin_is_read_from_the_context():
    own, asked = ["ours-1", "ours-2"], ["clock-automation"]
    assert hand.origin("ours-2", None, None, own, asked) == "ours"
    assert hand.origin("x", "somebody", None, own, asked) == "person"
    assert hand.origin("x", "somebody", "trigger", own, asked) == "person"  # a script somebody started
    assert hand.origin("x", None, "trigger", own, asked) == "automation"
    assert hand.origin("x", None, "ours-1", own, asked) == "automation"  # what reacts to our command is not us
    assert hand.origin("clock-automation", None, None, own, asked) == "automation"  # its call is all there is
    assert hand.origin("x", None, None, own, asked) == "device"
    assert hand.origin(None, None, None, own, asked) == "device"


def test_only_a_change_asked_for_after_our_command_is_deliberate():
    person = SettingChange(1000.0, "person")
    assert hand.deliberate_since(person, 900.0) == "person"
    assert hand.deliberate_since(person, 1000.0) is None  # not after it
    assert hand.deliberate_since(person, None) == "person"
    assert hand.deliberate_since(SettingChange(1000.0, "automation"), 900.0) == "automation"
    assert hand.deliberate_since(SettingChange(1000.0, "device"), 900.0) is None
    assert hand.deliberate_since(SettingChange(1000.0, "ours"), 900.0) is None
    assert hand.deliberate_since(None, 900.0) is None
    mode_only = SettingChange(1000.0, "person", setpoint=False, mode=True)
    assert hand.deliberate_since(mode_only, 900.0) == "person"
    assert hand.deliberate_since(mode_only, 900.0, setpoint_only=True) is None  # asked for no temperature


# ------------------------------------------------------------------ the heat pump: the verdict
SENT = replace(RUNNING, last_command_ts=NOW - 600, last_target=22.0)  # we sent "heat, 22 °C" ten minutes ago
JUST_SENT = replace(SENT, last_command_ts=NOW - 30)  # inside the time a unit may take to follow


def unit(target: float = 22.0, hvac: str = "heat", change: SettingChange | None = None, watched: float | None = NOW - 3600):
    state = heat_pump_state(hvac_mode=hvac)
    return replace(state, target_temp=target, setting_change=change, settings_watched_since_ts=watched)


def verdict(state, mem=SENT, now=NOW):
    return changed_by(inp([rr()], mode="active", heat_pump=state, now=now), mem)


def test_a_unit_as_we_left_it_was_changed_by_nobody():
    assert verdict(unit()) is None
    assert verdict(unit(change=SettingChange(NOW - 100, "person"))) is None  # asked for what it has anyway


def test_a_person_or_an_automation_counts_at_once():
    assert verdict(unit(24.0, change=SettingChange(NOW - 10, "person")), JUST_SENT) == "person"
    assert verdict(unit(24.0, change=SettingChange(NOW - 10, "automation")), JUST_SENT) == "automation"
    assert verdict(unit(hvac="off", change=SettingChange(NOW - 10, "person", setpoint=False, mode=True)), JUST_SENT) == "person"


def test_a_change_at_the_unit_counts_once_the_unit_has_had_time_to_follow():
    remote = SettingChange(NOW - 10, "device")
    assert verdict(unit(24.0, change=remote), JUST_SENT) == SETTLING
    assert verdict(unit(24.0, change=remote)) == "device"


def test_a_unit_nobody_touched_has_not_carried_the_command_out():
    # seen 2026-10-11: 18 °C sent, the unit stayed at 23 °C, and that was taken for a hand
    assert verdict(unit(23.0)) == NOT_CARRIED_OUT  # no change at all since our command
    assert verdict(unit(23.0, change=SettingChange(NOW - 7200, "person"))) == NOT_CARRIED_OUT  # an old one
    assert verdict(unit(23.0, change=SettingChange(NOW - 590, "ours"))) == NOT_CARRIED_OUT  # its answer to our own call
    assert verdict(unit(23.0), JUST_SENT) == SETTLING  # too early to say


def test_without_a_record_since_our_command_a_difference_is_a_hand_as_before():
    assert verdict(unit(23.0, watched=NOW - 60)) == "device"  # Home Assistant restarted after the command
    assert verdict(unit(23.0, watched=None)) == "device"


# ------------------------------------------------------------------ the heat pump: what follows
def step(state, mem, now=NOW, rooms=None, params=PARAMS):
    return decide(inp(rooms or [rr()], mode="active", heat_pump=state, now=now, params=params), mem)


def test_a_change_by_hand_is_respected_for_the_override_time_and_says_who_and_until_when():
    params = replace(PARAMS, manual_hold_s=45 * 60.0)
    state = unit(24.0, change=SettingChange(NOW - 10, "person"))
    cmd, mem = step(state, SENT, params=params)
    assert (cmd.plan, cmd.reason) == ("hands_off", "manual_takeover")
    assert (cmd.manual_until_ts, cmd.manual_by) == (NOW + 45 * 60.0, "person")
    later, mem = step(state, mem, NOW + 600, params=params)
    assert (later.plan, later.manual_until_ts, later.manual_by) == ("hands_off", NOW + 45 * 60.0, "person")
    assert ControlMemory.from_storage(mem.to_storage()).manual_by == "person"
    assert ControlMemory.from_storage({**mem.to_storage(), "manual_by": "nonsense"}).manual_by is None


def test_a_further_change_during_the_time_in_hand_renews_it_and_its_end_leaves_nothing_behind():
    state = unit(24.0, change=SettingChange(NOW - 10, "person"))
    _, mem = step(state, SENT)
    again = unit(25.0, change=SettingChange(NOW + 3600, "device"))  # an hour later, at the remote
    cmd, mem = step(again, mem, NOW + 3660)
    assert (cmd.plan, cmd.manual_until_ts) == ("hands_off", NOW + 3600 + 7200)
    assert cmd.manual_by == "person"  # still the person's time in hand: the unit reporting something does not rename it
    cmd, mem = step(again, mem, NOW + 3600 + 7200 + 60)  # over
    assert cmd.plan != "hands_off" and (cmd.manual_until_ts, cmd.manual_by) == (None, None)
    assert (mem.manual_until_ts, mem.manual_by) == (None, None)


def test_a_time_in_hand_that_ended_while_somebody_cooled_is_not_renewed_by_a_later_change():
    _, mem = step(unit(24.0, change=SettingChange(NOW - 10, "person")), SENT)
    day = 86400.0
    cooling = replace(unit(hvac="cool"), setting_change=SettingChange(NOW + 60, "person", setpoint=False, mode=True))
    cmd, mem = step(cooling, mem, NOW + day)  # a day in cool: the time in hand is long over
    assert cmd.reason == "manual_mode_cool" and (mem.manual_until_ts, mem.manual_by) == (None, None)
    off = unit(hvac="off", change=SettingChange(NOW + day + 60, "device", setpoint=False, mode=True))
    cmd, mem = step(off, mem, NOW + day + 120, rooms=[rr(temp=19.0)])
    assert cmd.plan != "hands_off" and mem.manual_until_ts is None  # nothing of ours was on record: no new hand


def test_a_unit_switched_off_by_hand_keeps_its_minimum_pause_after_back_to_automatic():
    off = unit(hvac="off", change=SettingChange(NOW - 10, "person", setpoint=False, mode=True))
    _, mem = step(off, SENT)
    assert mem.stopped_since_ts == NOW
    resumed = replace(mem, manual_until_ts=None, manual_by=None)  # what "back to automatic" does
    cmd, _ = step(off, resumed, NOW + 180, rooms=[rr(temp=19.0)])
    assert (cmd.plan, cmd.action, cmd.waiting) == ("off", "none", "min_off_time")
    cmd, _ = step(off, resumed, NOW + 660, rooms=[rr(temp=19.0)])
    assert cmd.action == "start"


def test_a_command_not_carried_out_is_sent_again_instead_of_handing_the_unit_over():
    cmd, mem = step(unit(23.0), SENT, rooms=[rr(temp=19.0)])
    assert (cmd.plan, cmd.action) == ("heat", "adjust") and cmd.target_temp is not None and cmd.target_temp != 23.0
    assert mem.manual_until_ts is None and mem.last_command_ts == NOW and mem.last_target == cmd.target_temp
    assert (cmd.not_carried_out, mem.not_carried_out) == (1, 1)


def test_a_unit_that_keeps_ignoring_a_command_gets_it_ever_more_rarely():
    mem, sent = SENT, []
    for minute in range(61):
        cmd, mem = step(unit(23.0), mem, NOW + minute * 60, rooms=[rr(temp=19.0)])
        if cmd.action != "none":
            sent.append(minute)
    assert sent == [0, 4, 12, 28, 60]  # after 2, 4, 8, 16, 32 minutes of being ignored
    assert mem.not_carried_out == 5 and mem.manual_until_ts is None
    cmd, followed = step(unit(mem.last_target), mem, NOW + 61 * 60, rooms=[rr(temp=19.0)])  # it follows at last
    assert followed.not_carried_out == 0 and cmd.not_carried_out == 0


def test_the_count_of_ignored_commands_ends_with_the_demand():
    # the start was ignored, the retry is due, and by then no room wants heat any more
    started = ControlMemory(running_since_ts=NOW - 600, last_command_ts=NOW - 600, last_hvac_mode="heat", last_target=22.0)
    cmd, mem = step(unit(hvac="off"), started, rooms=[rr(temp=22.0)])
    assert cmd.action == "none" and mem.not_carried_out == 1 and mem.last_command_ts is None
    cmd, mem = step(unit(hvac="off"), mem, NOW + 60, rooms=[rr(temp=22.0)])
    assert (cmd.not_carried_out, mem.not_carried_out) == (0, 0)  # nothing of ours on record: nothing ignored


def test_a_stop_not_carried_out_is_repeated_as_a_stop():
    stopped = ControlMemory(stopped_since_ts=NOW - 600, last_command_ts=NOW - 600, last_hvac_mode="off")
    cmd, mem = step(unit(), stopped, rooms=[rr(temp=22.0)])
    assert (cmd.plan, cmd.action, cmd.reason) == ("off", "stop", NOT_CARRIED_OUT)
    assert mem.manual_until_ts is None and mem.running_since_ts is None and mem.not_carried_out == 1


def test_nothing_is_sent_while_the_unit_differs_and_it_is_too_early_to_say_why():
    # we started it, somebody switched it off at the remote right away, and now a new setpoint is due
    off = unit(hvac="off", change=SettingChange(NOW - 20, "device", setpoint=False, mode=True))
    cmd, mem = step(off, JUST_SENT, rooms=[rr(temp=19.0, target=23.0)])
    assert (cmd.action, cmd.reason, cmd.waiting) == ("none", SETTLING, SETTLING) and mem == JUST_SENT
    cmd, mem = step(off, mem, NOW + 120, rooms=[rr(temp=19.0, target=23.0)])
    assert (cmd.plan, cmd.manual_by) == ("hands_off", "device")  # and then it is what it was: a hand at the unit


def test_planning_only_never_judges_a_change():
    state = unit(24.0, change=SettingChange(NOW - 10, "person"))
    cmd, mem = decide(inp([rr()], heat_pump=state), SENT)
    assert cmd.plan != "hands_off" and mem.manual_until_ts is None


# ------------------------------------------------------------------ the heat pump: the fan run-on
FAN = ControlMemory(stopped_since_ts=NOW - 60, last_command_ts=NOW - 60, last_hvac_mode="fan_only", fan_until_ts=NOW + 840)


def test_a_person_switching_to_heat_during_the_fan_run_on_keeps_the_unit():
    state = unit(22.0, change=SettingChange(NOW - 5, "person", setpoint=False, mode=True))
    cmd, mem = step(state, FAN, rooms=[rr(temp=22.0)])
    assert (cmd.plan, cmd.reason, mem.manual_by) == ("hands_off", "manual_takeover", "person") and mem.fan_until_ts is None


def test_switching_the_unit_off_during_the_fan_run_on_is_where_it_was_heading_anyway():
    running_on = replace(FAN, last_command_ts=NOW - 300, stopped_since_ts=NOW - 300, fan_until_ts=NOW + 600)
    off = unit(hvac="off", change=SettingChange(NOW - 5, "device", setpoint=False, mode=True))
    cmd, mem = step(off, running_on, rooms=[rr(temp=22.0)])
    assert cmd.plan == "off" and mem.manual_until_ts is None and mem.fan_until_ts is None and mem.last_hvac_mode == "off"
    # a person's "off" counts at once, a unit showing "off" on its way into fan only does not
    asked = unit(hvac="off", change=SettingChange(NOW - 5, "person", setpoint=False, mode=True))
    _, mem = step(asked, FAN, rooms=[rr(temp=22.0)])
    assert mem.fan_until_ts is None and mem.manual_until_ts is None
    cmd, mem = step(off, FAN, rooms=[rr(temp=22.0)])
    assert (cmd.waiting, mem.fan_until_ts) == ("fan_run_on", FAN.fan_until_ts)


def test_the_off_after_the_fan_run_on_is_repeated_when_the_unit_did_not_take_it():
    after = ControlMemory(stopped_since_ts=NOW - 1500, last_command_ts=NOW - 600, last_hvac_mode="off")
    fan_only = unit(hvac="fan_only")  # still ventilating, and nobody set that
    cmd, mem = step(fan_only, after, rooms=[rr(temp=19.0)])
    assert (cmd.plan, cmd.action, cmd.hvac_mode, cmd.reason) == ("off", "stop", "off", NOT_CARRIED_OUT)
    assert mem.last_command_ts == NOW and mem.not_carried_out == 1
    by_hand = unit(hvac="fan_only", change=SettingChange(NOW - 300, "person", setpoint=False, mode=True))
    cmd, _ = step(by_hand, after, rooms=[rr(temp=19.0)])
    assert (cmd.plan, cmd.reason) == ("hands_off", "manual_mode_fan_only")  # somebody ventilating: left alone


# ------------------------------------------------------------------ a room thermostat
def test_a_deliberate_change_on_a_thermostat_needs_no_confirmation():
    unconfirmed = RoomCtrlMemory(last_sent_target=21.0, last_sent_ts=NOW - 5, confirmed=False, thermostat_before_send=17.0)
    assert manual_change("active", 17.0, unconfirmed) is None  # still showing what it had: our value is on its way
    assert manual_change("active", 17.0, unconfirmed, deliberate=True) == 17.0  # somebody asked for exactly that
    assert manual_change("active", 21.0, unconfirmed, deliberate=True) is None  # asked for what we sent
    assert manual_change("shadow", 17.0, unconfirmed, deliberate=True) is None


def test_a_thermostat_answering_our_setpoint_in_its_own_step_is_in_sync_and_no_hand():
    sent = RoomCtrlMemory(last_sent_target=21.5, last_sent_ts=NOW - 60, confirmed=False, thermostat_before_send=17.0)
    ours = SettingChange(NOW - 60, "ours")
    assert own_answer(22.0, sent, ours) is True
    assert own_answer(22.0, sent, SettingChange(NOW - 60, "device")) is False  # a knob, not an answer
    assert own_answer(22.0, sent, SettingChange(NOW - 600, "ours")) is False  # to an earlier setpoint
    assert own_answer(21.5, sent, ours) is False  # it shows what was sent: nothing to note
    # Our context stays on the entity for seconds. Only a value next to what was sent is a rendering:
    assert own_answer(24.0, sent, ours) is False  # a knob turned right after our setpoint
    assert own_answer(17.0, replace(sent, last_sent_target=18.0), ours) is False  # fell back to what it had
    answered = replace(sent, answered_with=22.0, confirmed=True)
    assert manual_change("active", 22.0, answered) is None
    assert manual_change("active", 23.0, answered) == 23.0  # turned on from there: a hand
    cmd, after = decide_room("bad", "active", room_result(target=21.5), True, "heat", 22.0, NOW, answered)
    assert (cmd.plan, cmd.send, cmd.reason) == ("hold", False, "in_sync") and after == answered
    cmd, after = decide_room("bad", "active", room_result(target=20.0), True, "heat", 22.0, NOW, answered)
    assert cmd.send is True and after.answered_with is None  # a new setpoint: a new answer
    assert RoomCtrlMemory.from_storage(answered.to_storage()) == answered


# ------------------------------------------------------------------ through Home Assistant
class Climate(ClimateEntity):
    """A climate entity that writes its state as a real one does: Home Assistant gives it the context."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_fan_modes = ["auto", "high"]
    _attr_min_temp = 5
    _attr_max_temp = 30

    def __init__(self, object_id: str, mode: HVACMode, target: float, modes: list[HVACMode], extra: dict) -> None:
        self.entity_id = f"climate.{object_id}"
        self._attr_unique_id = object_id
        self._attr_hvac_mode = mode
        self._attr_hvac_modes = modes
        self._attr_target_temperature = target
        self._attr_fan_mode = "auto"
        self.extra = dict(extra)
        self.calls: list[tuple[str, object]] = []
        self.whole_degrees = False

    @property
    def extra_state_attributes(self):
        return self.extra

    async def async_set_temperature(self, **kwargs) -> None:
        value = kwargs["temperature"]
        self.calls.append(("set_temperature", value))
        self._attr_target_temperature = float(int(value + 0.5)) if self.whole_degrees else value
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        self.calls.append(("set_hvac_mode", str(hvac_mode)))
        self._attr_hvac_mode = HVACMode(hvac_mode)
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode) -> None:
        self.calls.append(("set_fan_mode", fan_mode))
        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()

    def at_the_device(self, *, mode: HVACMode | None = None, target: float | None = None, **extra) -> None:
        """A knob, a remote, a rule of the device's own: no service call, so nobody's context."""
        if mode is not None:
            self._attr_hvac_mode = mode
        if target is not None:
            self._attr_target_temperature = target
        self.extra.update(extra)
        self.async_write_ha_state()


UNIT_READINGS = {
    "indoor_temperature": 21, "outdoor_temperature": 4.0, "compressor_frequency": 0, "indoor_fan_speed": 496,
    "realtime_power": 13.3, "error_code": 0,
}


async def _setup(hass: HomeAssistant, freezer, *, heat_pump_active: bool = False, **bath_extra):
    """Active room control over a real thermostat, and a real unit; optionally active heat pump control."""
    freezer.move_to(datetime(2026, 10, 12, 14, 30, tzinfo=dt_util.UTC))
    bath = Climate("bath", HVACMode.HEAT, 17.0, [HVACMode.HEAT, HVACMode.OFF], {"current_temperature": 18.5, **bath_extra})
    midea = Climate("midea", HVACMode.OFF, 22.0, [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY], UNIT_READINGS)

    async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
        async_add_entities([bath, midea])

    mock_platform(hass, "test.climate", MockPlatform(async_setup_platform=async_setup_platform))
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()
    _set_states(hass)  # the sensors; it also writes a made-up state for the unit, so the real one is written again
    midea.async_write_ha_state()
    data = _entry_data()
    data[CONF_ROOMS][0]["climate_entity"] = "climate.bath"
    released = {"room_allow_active_control": True, "midea_allow_active_control": True}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN, title="Thriftherm", options=released)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    if heat_pump_active:
        coordinator.set_mode("midea_only")
        coordinator.set_control_mode("active")
    coordinator.set_room_control_mode("active")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    return coordinator, bath, midea


async def _cycle(hass: HomeAssistant, coordinator, freezer, seconds: float = 60) -> None:
    freezer.tick(timedelta(seconds=seconds))
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def _set(hass: HomeAssistant, entity: str, temperature: float, context: Context) -> None:
    data = {"entity_id": entity, "temperature": temperature}
    await hass.services.async_call("climate", "set_temperature", data, blocking=True, context=context)


def _command(hass: HomeAssistant) -> tuple:
    state = hass.states.get("sensor.thriftherm_heat_pump_command")
    return state.state, state.attributes["reason"], state.attributes["manual_by"]


async def test_home_assistant_says_who_changed_the_thermostat(hass: HomeAssistant, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    record = lambda: coordinator._setting_changes["climate.bath"]  # noqa: E731
    assert bath.target_temperature == 21.0 and record().origin == "ours"  # our own command
    freezer.tick(timedelta(minutes=5))
    await _set(hass, "climate.bath", 23.0, Context(user_id=hass_admin_user.id))
    assert record().origin == "person"
    freezer.tick(timedelta(minutes=5))
    await _set(hass, "climate.bath", 22.0, Context(parent_id="a-trigger"))  # an automation with a trigger
    assert record().origin == "automation"
    freezer.tick(timedelta(minutes=5))
    await _set(hass, "climate.bath", 22.5, Context())  # one run by the clock: neither a person nor a trigger
    assert record().origin == "automation"
    freezer.tick(timedelta(minutes=5))
    bath.at_the_device(target=20.0)
    assert record().origin == "device" and (record().setpoint, record().mode) == (True, False)
    freezer.tick(timedelta(minutes=5))
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.bath", "hvac_mode": "off"}, blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert (record().origin, record().setpoint, record().mode) == ("person", False, True)


async def test_gone_and_back_is_nobodys_change_unless_the_setting_is_another(hass, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    freezer.tick(timedelta(minutes=5))
    await _set(hass, "climate.bath", 23.0, Context(user_id=hass_admin_user.id))
    by_person = coordinator._setting_changes["climate.bath"]

    freezer.tick(timedelta(minutes=5))
    bath._attr_available = False
    bath.async_write_ha_state()
    freezer.tick(timedelta(minutes=5))
    bath._attr_available = True
    bath.at_the_device(current_temperature=19.0)  # back as it left, and a new reading
    assert coordinator._setting_changes["climate.bath"] == by_person  # still the person's, from ten minutes ago

    freezer.tick(timedelta(minutes=5))
    bath._attr_available = False
    bath.async_write_ha_state()
    bath._attr_available = True
    bath.at_the_device(target=19.0)  # back with another setpoint: somebody at the device meanwhile
    back = coordinator._setting_changes["climate.bath"]
    assert (back.origin, back.setpoint, back.mode) == ("device", True, False) and back.ts > by_person.ts


async def test_a_person_overrides_a_rule_of_the_thermostats_own_and_it_shows_who(hass, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)  # our 21 is confirmed
    own_rule = {"window_manager": {"window_state": "on", "is_window_bypass": False}}
    bath.at_the_device(target=17.0, **own_rule)  # its window rule
    await _cycle(hass, coordinator, freezer)
    assert "badezimmer" not in coordinator.overrides
    await _set(hass, "climate.bath", 19.0, Context(user_id=hass_admin_user.id))  # and somebody asks for 19 all the same
    await _cycle(hass, coordinator, freezer)
    override = coordinator.overrides["badezimmer"]
    assert (override.target, override.by) == (19.0, "person")
    shown = hass.states.get("sensor.thriftherm_badezimmer_target_temperature").attributes
    assert shown["reason"] == "override" and shown["override_by"] == "person"


async def test_a_person_switching_the_thermostat_off_asks_for_no_temperature(hass, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    bath.at_the_device(target=17.0, window_manager={"window_state": "on", "is_window_bypass": False})
    await _cycle(hass, coordinator, freezer)
    off = {"entity_id": "climate.bath", "hvac_mode": "off"}
    person = Context(user_id=hass_admin_user.id)
    await hass.services.async_call("climate", "set_hvac_mode", off, blocking=True, context=person)
    await _cycle(hass, coordinator, freezer)
    assert "badezimmer" not in coordinator.overrides  # the 17 is still the thermostat's own, whoever switched it off


async def test_a_clock_automation_is_a_change_by_hand_at_once(hass: HomeAssistant, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    assert coordinator.room_ctrl_memory["badezimmer"].confirmed is False  # our 21 just went out
    freezer.tick(timedelta(seconds=10))
    await _set(hass, "climate.bath", 23.0, Context())  # "23 °C before the shower", run by the clock
    await _cycle(hass, coordinator, freezer, 5)  # no waiting for a confirmation
    override = coordinator.overrides["badezimmer"]
    assert (override.target, override.by) == (23.0, "automation")


async def test_a_call_by_area_or_for_all_counts_and_a_wrappers_call_on_its_valve_does_not(hass, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    everything = Context()
    for_all = {"entity_id": "all", "temperature": 23.0}
    await hass.services.async_call("climate", "set_temperature", for_all, blocking=True, context=everything)
    assert coordinator._setting_changes["climate.bath"].origin == "automation"

    def call(domain: str, service: str, **data) -> Context:
        context = Context()
        hass.bus.async_fire("call_service", {"domain": domain, "service": service, "service_data": data}, context=context)
        return context

    by_area = call("climate", "set_temperature", area_id="bad")
    valve = call("climate", "set_temperature", entity_id="climate.bath_trv")  # a thermostat wrapper to the valve behind it
    light = call("light", "turn_on", entity_id="all")
    assert everything.id in coordinator._asked_contexts and by_area.id in coordinator._asked_contexts
    assert valve.id not in coordinator._asked_contexts and light.id not in coordinator._asked_contexts
    assert not set(coordinator._own_contexts) & set(coordinator._asked_contexts)  # our own calls are not "asked"


async def test_a_knob_turned_seconds_after_our_setpoint_is_still_a_hand(hass: HomeAssistant, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)  # our 21 just went out: our context is still on the entity
    freezer.tick(timedelta(seconds=3))
    bath.at_the_device(target=24.0)
    assert coordinator._setting_changes["climate.bath"].origin == "ours"  # what Home Assistant makes of it
    await _cycle(hass, coordinator, freezer)
    override = coordinator.overrides["badezimmer"]
    assert (override.target, override.by) == (24.0, "device")


async def test_a_thermostat_in_whole_degrees_is_not_overridden_by_its_own_answer(hass: HomeAssistant, freezer) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    bath.whole_degrees = True
    coordinator.room_temps["badezimmer"] = {"comfort": 21.5}
    await _cycle(hass, coordinator, freezer, 120)
    assert bath.calls[-1] == ("set_temperature", 21.5) and bath.target_temperature == 22.0
    sent = len(bath.calls)
    for _ in range(10):
        await _cycle(hass, coordinator, freezer)
    assert coordinator.overrides == {} and len(bath.calls) == sent  # no override, and nothing sent against it
    assert coordinator.data["room_commands"]["badezimmer"].reason == "in_sync"


async def test_a_change_made_while_the_cycle_runs_is_not_overwritten(hass: HomeAssistant, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    coordinator.room_temps["badezimmer"] = {"comfort": 22.0}  # 22 is due in the next cycle
    evaluate = coordinator._evaluate

    def evaluate_then_a_person_acts(snapshot):
        result = evaluate(snapshot)  # the states are read, the commands decided ...
        bath._attr_target_temperature = 24.0  # ... and before they go out somebody sets 24
        bath.async_set_context(Context(user_id=hass_admin_user.id))
        bath.async_write_ha_state()
        return result

    coordinator._evaluate = evaluate_then_a_person_acts
    await _cycle(hass, coordinator, freezer)
    coordinator._evaluate = evaluate
    assert bath.target_temperature == 24.0 and ("set_temperature", 22.0) not in bath.calls
    await _cycle(hass, coordinator, freezer)
    override = coordinator.overrides["badezimmer"]
    assert (override.target, override.by) == (24.0, "person")


async def test_our_start_is_ours_and_a_person_at_the_heat_pump_is_respected_and_shown(hass, freezer, hass_admin_user) -> None:
    coordinator, _, midea = await _setup(hass, freezer, heat_pump_active=True)
    assert midea.hvac_mode == HVACMode.HEAT and coordinator._setting_changes["climate.midea"].origin == "ours"
    await _cycle(hass, coordinator, freezer)
    assert _command(hass) == ("heat", "mode_midea_only", None)
    await _set(hass, "climate.midea", 26.0, Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()  # no waiting for a person, not even for the next cycle
    assert _command(hass) == ("hands_off", "manual_takeover", "person")
    until = hass.states.get("sensor.thriftherm_heat_pump_command").attributes["manual_until_ts"]
    assert until == pytest.approx(dt_util.utcnow().timestamp() + 7200, abs=1)
    calls = len(midea.calls)
    await _cycle(hass, coordinator, freezer, 600)
    assert len(midea.calls) == calls and midea.target_temperature == 26.0  # left alone


async def test_a_change_at_the_heat_pump_while_the_cycle_runs_is_not_overwritten(hass, freezer, hass_admin_user) -> None:
    coordinator, _, midea = await _setup(hass, freezer, heat_pump_active=True)
    await _cycle(hass, coordinator, freezer, 600)
    coordinator.boosts["badezimmer"] = dt_util.utcnow().timestamp() + 3600  # quick heat-up: a fan command is due
    evaluate = coordinator._evaluate

    def evaluate_then_a_person_acts(snapshot):
        result = evaluate(snapshot)
        midea._attr_target_temperature = 26.0
        midea.async_set_context(Context(user_id=hass_admin_user.id))
        midea.async_write_ha_state()
        return result

    coordinator._evaluate = evaluate_then_a_person_acts
    calls = len(midea.calls)
    await _cycle(hass, coordinator, freezer)
    coordinator._evaluate = evaluate
    assert len(midea.calls) == calls and midea.target_temperature == 26.0  # the planned command stayed unsent
    await _cycle(hass, coordinator, freezer)
    assert _command(hass) == ("hands_off", "manual_takeover", "person")


async def test_a_unit_switched_off_while_unreachable_is_not_switched_on_again(hass: HomeAssistant, freezer) -> None:
    coordinator, _, midea = await _setup(hass, freezer, heat_pump_active=True)
    await _cycle(hass, coordinator, freezer, 600)
    calls = len(midea.calls)
    midea._attr_available = False
    midea.async_write_ha_state()
    await _cycle(hass, coordinator, freezer)
    midea._attr_available = True
    midea.at_the_device(mode=HVACMode.OFF)  # back, and switched off at the unit meanwhile
    await hass.async_block_till_done()  # the cycle this change starts by itself
    await _cycle(hass, coordinator, freezer)
    assert midea.hvac_mode == HVACMode.OFF and len(midea.calls) == calls
    assert _command(hass) == ("hands_off", "manual_takeover", "device")


async def test_back_to_automatic_ends_every_override_and_the_heat_pumps_time_in_hand(hass, freezer, hass_admin_user) -> None:
    coordinator, _, midea = await _setup(hass, freezer, heat_pump_active=True)
    await _cycle(hass, coordinator, freezer, 600)
    await _set(hass, "climate.bath", 23.0, Context(user_id=hass_admin_user.id))
    await _set(hass, "climate.midea", 26.0, Context(user_id=hass_admin_user.id))
    await _cycle(hass, coordinator, freezer)
    assert coordinator.overrides["badezimmer"].by == "person" and coordinator.control_memory.manual_by == "person"

    button = "button.thriftherm_back_to_automatic"
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.overrides == {}
    assert (coordinator.control_memory.manual_until_ts, coordinator.control_memory.manual_by) == (None, None)
    await _cycle(hass, coordinator, freezer)
    assert _command(hass)[0] == "heat" and midea.target_temperature != 26.0  # ours again, with our setpoint


async def test_who_asked_for_an_override_survives_a_restart_and_nonsense_does_not(hass: HomeAssistant, freezer) -> None:
    coordinator, _, _ = await _setup(hass, freezer)
    now = dt_util.utcnow().timestamp()
    stored = persistence.to_store(coordinator)
    stored["overrides"] = {
        "badezimmer": {"target": 22.0, "until_ts": now + 3600, "set_at_ts": now, "by": "person"},
        "wohnzimmer": {"target": 20.0, "until_ts": now + 3600, "set_at_ts": now, "by": "nonsense"},
    }
    coordinator.overrides.clear()
    persistence.restore(coordinator, stored)
    assert {key: override.by for key, override in coordinator.overrides.items()} == {"badezimmer": "person", "wohnzimmer": None}
    del stored["overrides"]["badezimmer"]["by"]  # stored before it was kept
    coordinator.overrides.clear()
    persistence.restore(coordinator, stored)
    assert coordinator.overrides["badezimmer"].by is None


async def test_a_setpoint_changed_by_hand_shows_as_an_override_at_once(hass, freezer, hass_admin_user) -> None:
    coordinator, bath, _ = await _setup(hass, freezer)
    await _cycle(hass, coordinator, freezer, 600)
    freezer.tick(timedelta(seconds=5))
    await _set(hass, "climate.bath", 23.0, Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()  # no cycle of ours in between: the change itself starts one
    assert coordinator.overrides["badezimmer"].by == "person"
    shown = hass.states.get("sensor.thriftherm_badezimmer_target_temperature")
    assert float(shown.state) == 23.0 and shown.attributes["reason"] == "override"
    assert shown.attributes["override_until_ts"] == pytest.approx(dt_util.utcnow().timestamp() + 7200, abs=2)

    freezer.tick(timedelta(minutes=10))
    cycles = []
    coordinator.async_add_listener(lambda: cycles.append(1))
    bath.at_the_device(current_temperature=19.2)  # a reading alone starts nothing
    await hass.async_block_till_done()
    assert cycles == []
    bath.at_the_device(target=24.0)  # the knob does
    await hass.async_block_till_done()
    assert cycles and coordinator.overrides["badezimmer"].target == 24.0
