"""What a valve can do, told by the entities of its device (docs/PHASE13_VALVES.md §3.2).

The names come from the device definitions of Zigbee2MQTT (zigbee-herdsman-converters, MIT), the ZHA quirks and ZHA
(Apache-2.0), Home Assistant's Shelly integration (Apache-2.0) and, for a Sonoff TRVZBT, from one installation
(read 2026-11-13). Quirks from Better Thermostat (AGPL-3.0) are facts written anew, no code of it.
"""

from __future__ import annotations

import pytest

from custom_components.thriftherm.engines import valve_caps
from custom_components.thriftherm.engines.valve_caps import Entity, ValveCaps, caps_of, quirks_of


def e(entity_id, key=None, unit=None, options=(), writable=True, enabled=True):  # noqa: PLR0913
    domain = entity_id.split(".", 1)[0]
    return Entity(entity_id, domain, key or entity_id.split(".", 1)[1], unit, tuple(options), writable, enabled)


BATH_TRVZBT = (  # a bathroom valve, Zigbee2MQTT (a part of its 44 entities)
    e("climate.heizkorper_badezimmer", "0xa4c138fcc3bce63e_climate_zigbee2mqtt"),
    e("number.heizkorper_badezimmer_external_temperature_input", "0xa4c1_external_temperature_input_zigbee2mqtt", "°C"),
    e("select.heizkorper_badezimmer_temperature_sensor_select", "0xa4c1_temperature_sensor_select_zigbee2mqtt",
      options=("local_temperature", "remote_temperature", "remote_source_offline")),
    e("number.heizkorper_badezimmer_local_temperature_calibration", "0xa4c1_local_temperature_calibration_zigbee2mqtt", "°C"),
    e("number.heizkorper_badezimmer_heating_valve_position", "0xa4c1_heating_valve_position_zigbee2mqtt", "%"),
    e("number.heizkorper_badezimmer_idle_valve_position", "0xa4c1_idle_valve_position_zigbee2mqtt", "%"),
    e("switch.heizkorper_badezimmer_child_lock", "0xa4c1_child_lock_zigbee2mqtt"),
    e("switch.heizkorper_badezimmer_open_window", "0xa4c1_open_window_zigbee2mqtt"),
    e("sensor.heizkorper_badezimmer_battery", "0xa4c1_battery_zigbee2mqtt", "%"),
    e("number.heizkorper_badezimmer_frost_protection_temperature", "0xa4c1_frost_protection_temperature_zigbee2mqtt", "°C"),
)


def test_a_trvzbt_can_take_the_room_temperature_an_offset_and_an_opening():
    caps = caps_of(BATH_TRVZBT)
    assert caps.external_temperature == "number.heizkorper_badezimmer_external_temperature_input"
    assert caps.sensor_select == "select.heizkorper_badezimmer_temperature_sensor_select"
    assert (caps.remote_option, caps.local_option) == ("remote_temperature", "local_temperature")
    assert caps.offset == "number.heizkorper_badezimmer_local_temperature_calibration"
    assert caps.opening == "number.heizkorper_badezimmer_heating_valve_position"
    assert caps.closing == "number.heizkorper_badezimmer_idle_valve_position"
    assert caps.child_lock == "switch.heizkorper_badezimmer_child_lock"
    assert caps.window_detection == "switch.heizkorper_badezimmer_open_window"
    assert caps.battery == "sensor.heizkorper_badezimmer_battery"
    assert caps.ways() == ("external", "offset", "setpoint")  # the opening is a choice of the room (§3.1)


def test_an_older_zigbee2mqtt_names_the_opening_and_the_selection_otherwise():
    caps = caps_of((
        e("climate.trv"),
        e("number.trv_valve_opening_degree", "x_valve_opening_degree", "%"),
        e("number.trv_valve_closing_degree", "x_valve_closing_degree", "%"),
        e("select.trv_temperature_sensor_select", "x_temperature_sensor_select", options=("internal", "external")),
        e("number.trv_external_temperature_input", "x_external_temperature_input", "°C"),
    ))
    assert (caps.opening, caps.closing) == ("number.trv_valve_opening_degree", "number.trv_valve_closing_degree")
    assert (caps.remote_option, caps.local_option) == ("external", "internal")


def test_zha_tells_by_translation_key_and_a_disabled_entity_is_not_there():
    caps = caps_of((
        e("climate.bad"),
        e("number.bad_external_temperature", "external_temperature_sensor_value", "°C"),
        e("switch.bad_external_temperature", "external_temperature_sensor"),
        e("number.bad_calibration", "local_temperature_calibration", "°C"),
        e("number.bad_valve_opening", "valve_opening_degree", "%", enabled=False),
        e("switch.bad_child_lock", "child_lock"),
    ))
    assert caps.external_temperature == "number.bad_external_temperature"
    assert caps.sensor_switch == "switch.bad_external_temperature" and caps.sensor_select is None
    assert caps.opening is None and caps.disabled == ("number.bad_valve_opening",)


@pytest.mark.parametrize(
    ("entities", "ways"),
    [
        # Danfoss Ally: its offset moves the setpoint, Thriftherm treats it as the setpoint way
        ((e("number.ally_external_measured_room_sensor", "x_external_measured_room_sensor", "°C"),
          e("number.ally_regulation_setpoint_offset", "x_regulation_setpoint_offset", "°C")), ("external", "setpoint")),
        # Tuya: an offset only, its position read only
        ((e("number.tuya_local_temperature_calibration", unit="°C"), e("sensor.tuya_position", unit="%", writable=False)),
         ("offset", "setpoint")),
        # Shelly BLU TRV
        ((e("number.blu_external_temperature", "external_temperature", "°C"),), ("external", "setpoint")),
        # Homematic IP: nothing but the climate entity
        ((), ("setpoint",)),
    ],
)
def test_other_valves(entities, ways):
    assert caps_of((e("climate.v"), *entities)).ways() == ways


def test_a_number_that_is_no_temperature_or_not_writable_is_no_input():
    caps = caps_of((
        e("climate.v"),
        e("number.v_external_temperature_input", unit="%"),
        e("sensor.v_local_temperature_calibration", unit="°C", writable=False),
    ))
    assert caps.ways() == ("setpoint",)


def test_a_valve_whose_climate_entity_shows_no_temperature_gets_a_plain_setpoint():
    assert ValveCaps(climate="climate.v", measures=False).ways() == ("plain",)


@pytest.mark.parametrize(
    ("manufacturer", "model", "attr", "value"),
    [
        ("SONOFF", "TRVZB", "unreliable_hvac_action", True),
        ("SONOFF", "TRVZB", "min_opening_pct", 3.0),
        ("SONOFF", "Zigbee thermostatic radiator valve", "unreliable_hvac_action", True),  # how a TRVZBT in use shows
        ("SONOFF", "TRVZB", "external_max_age_s", 2 * 3600.0),  # measured 2026-11-14 (Phase B): 2 h to the minute
        ("SONOFF", "Zigbee thermostatic radiator valve", "external_max_age_s", 2 * 3600.0),
        ("Avatto", "ME167", "offset_inverted", True),
        ("Shelly", "SHTRV-01", "min_setpoint", 4.5),
        ("Danfoss", "eTRV0100", "external_max_age_s", 3 * 3600.0),
        ("Bosch", "BTH-RA", "external_max_age_s", 1800.0),
        ("Acme", "TRV 1", "unreliable_hvac_action", False),
    ],
)
def test_the_quirks_of_a_model(manufacturer, model, attr, value):
    assert getattr(quirks_of(manufacturer, model), attr) == value


def test_the_external_temperature_is_refreshed_within_the_shortest_known_limit():
    assert min(q.external_max_age_s for q in valve_caps.QUIRKS.values() if q.external_max_age_s) > valve_caps.EXTERNAL_REFRESH_S


def test_the_hysteresis_of_a_valve_is_found():
    """Seen 2026-11-15 at 03:59: the TRVZB's temperature_accuracy −1 kept it shut down to 1 K below its setpoint."""
    caps = caps_of((
        e("climate.v"),
        e("number.v_temperature_accuracy", "x_temperature_accuracy_zigbee2mqtt", "°C"),
    ))
    assert caps.hysteresis == "number.v_temperature_accuracy"


def test_a_hysteresis_by_its_plain_name_is_found_too():
    """Moes TRV801Z and others name it so (Zigbee2MQTT device pages)."""
    assert caps_of((e("climate.v"), e("number.v_hysteresis", unit="°C"))).hysteresis == "number.v_hysteresis"


def test_a_valve_whose_sensor_selection_is_switched_off_in_home_assistant_takes_no_room_temperature():
    """Seen 2026-11-15: three TRVZBT had their selection disabled and stood on their own sensor. The value written to
    the input went unheeded, the valve went by its own warm reading. Without a selection to set: the offset."""
    caps = caps_of((
        e("climate.v"),
        e("number.v_external_temperature_input", "x_external_temperature_input", "°C"),
        e("select.v_temperature_sensor_select", "x_temperature_sensor_select",
          options=("local_temperature", "remote_temperature"), enabled=False),
        e("number.v_local_temperature_calibration", "x_local_temperature_calibration", "°C"),
    ))
    assert caps.ways() == ("offset", "setpoint")
    assert caps.disabled == ("select.v_temperature_sensor_select",)


def test_the_role_of_an_entity_switched_off_is_kept():
    caps = caps_of((
        e("climate.v"),
        e("number.v_external_temperature_input", "x_external_temperature_input", "°C", enabled=False),
        e("select.v_temperature_sensor_select", "x_temperature_sensor_select", options=("local_temperature",), enabled=False),
    ))
    assert dict(caps.disabled_roles) == {
        "external_temperature": "number.v_external_temperature_input",
        "sensor_select": "select.v_temperature_sensor_select",
    }
