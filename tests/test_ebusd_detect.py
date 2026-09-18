"""Finding the boiler's ebusd entities, the eBUS signal and the gas meter in the entity registry.

The unique_ids are those ebusd's MQTT discovery really publishes; entity ids
are the user's to rename, so several tests rename them.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.thriftherm.adapters.ebusd_detect import Detection, detect, prefill

BAI = {
    "boiler_flow_temp": ("sensor", "FlowTemp_temp"),
    "boiler_return_temp": ("sensor", "ReturnTemp_temp"),
    "boiler_pump_state": ("sensor", "Status01_pumpstate"),
    "boiler_pump_running": ("sensor", "WP_value"),
    "boiler_state_number": ("sensor", "Statenumber_value"),
    "boiler_hwc_mode": ("sensor", "Status02_hwcmode"),
}


def _add(hass: HomeAssistant, domain: str, unique_id: str, platform: str = "mqtt", **kwargs) -> str:
    object_id = kwargs.pop("suggested_object_id", f"heating_{unique_id.lower()}")
    return er.async_get(hass).async_get_or_create(domain, platform, unique_id, suggested_object_id=object_id, **kwargs).entity_id


def _boiler(hass: HomeAssistant, circuit: str = "bai", roles=tuple(BAI)) -> dict[str, str]:
    """Register the boiler messages of one circuit; returns config key -> entity_id."""
    return {key: _add(hass, BAI[key][0], f"ebusd_{circuit}_{BAI[key][1]}") for key in roles}


def _rename(hass: HomeAssistant, entity_id: str, new_entity_id: str) -> str:
    return er.async_get(hass).async_update_entity(entity_id, new_entity_id=new_entity_id).entity_id


# ------------------------------------------------------------------ the boiler
async def test_finds_the_vaillant_boiler_by_unique_id(hass: HomeAssistant) -> None:
    expected = _boiler(hass)
    assert expected["boiler_flow_temp"] == "sensor.heating_ebusd_bai_flowtemp_temp"
    expected["boiler_return_temp"] = _rename(hass, expected["boiler_return_temp"], "sensor.ruecklauf")
    # neighbours whose names contain the role's name must not be taken for it
    _add(hass, "sensor", "ebusd_bai_Expertlevel_ReturnTemp_temp")
    _add(hass, "sensor", "ebusd_bai_FlowTempDesired_temp")
    _add(hass, "sensor", "ebusd_bai_FlowTemp_temp", platform="template")  # not from MQTT
    _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    found = detect(hass)
    assert found == Detection(
        "vaillant_bai", "bai", {**expected, "boiler_signal": "binary_sensor.heating_eas_signal"}, found=6, total=6
    )


async def test_prefers_the_circuit_with_the_most_values(hass: HomeAssistant) -> None:
    _boiler(hass, "bai", roles=("boiler_flow_temp", "boiler_return_temp"))
    expected = _boiler(hass, "bai2")
    _add(hass, "sensor", "ebusd_700_Hc1FlowTemp_value")  # the controller: no boiler values
    _add(hass, "sensor", "ebusd_global_Uptime_value")
    found = detect(hass)
    assert (found.profile, found.circuit, found.found, found.total) == ("vaillant_bai", "bai2", 6, 6)
    assert found.entities == expected


async def test_equal_circuits_prefer_the_usual_one(hass: HomeAssistant) -> None:
    _boiler(hass, "abc")
    expected = _boiler(hass, "bai")
    _boiler(hass, "zzz")
    found = detect(hass)
    assert found.circuit == "bai" and found.entities == expected


async def test_missing_optional_values_are_counted(hass: HomeAssistant) -> None:
    expected = _boiler(hass, roles=("boiler_flow_temp", "boiler_return_temp"))
    # ebusd may publish the pump as a binary sensor; the pump field accepts both
    expected["boiler_pump_running"] = _add(hass, "binary_sensor", "ebusd_bai_WP_value")
    _add(hass, "binary_sensor", "ebusd_bai_Statenumber_value")  # the status field takes sensors only
    found = detect(hass)
    assert found == Detection("vaillant_bai", "bai", expected, found=3, total=6)


async def test_flow_and_return_are_required(hass: HomeAssistant) -> None:
    _boiler(hass, roles=tuple(k for k in BAI if k != "boiler_return_temp"))
    assert detect(hass) == Detection(None, None, {})


async def test_disabled_entities_are_ignored(hass: HomeAssistant) -> None:
    _boiler(hass, roles=("boiler_flow_temp",))
    _add(hass, "sensor", "ebusd_bai_ReturnTemp_temp", disabled_by=er.RegistryEntryDisabler.USER)
    assert detect(hass).profile is None


async def test_nothing_from_ebusd(hass: HomeAssistant) -> None:
    _add(hass, "sensor", "0x1234_temperature", platform="zha")
    _add(hass, "sensor", "shelly_flow_temp", platform="shelly")
    hass.states.async_set("sensor.outdoor", "4.0", {"unit_of_measurement": "°C"})
    assert detect(hass) == Detection(None, None, {}, found=0, total=0)


# ------------------------------------------------------------------ eBUS signal
async def test_signal_single_candidate_from_any_mqtt_device(hass: HomeAssistant) -> None:
    signal = _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    _add(hass, "binary_sensor", "door_signal", original_device_class="door")  # not connectivity
    _add(hass, "binary_sensor", "router_signal", platform="fritz", original_device_class="connectivity")
    assert detect(hass).entities == {"boiler_signal": signal}


async def test_signal_several_candidates_prefer_ebusd(hass: HomeAssistant) -> None:
    _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    signal = _add(hass, "binary_sensor", "ebusd_global_signal", original_device_class="connectivity")
    assert detect(hass).entities == {"boiler_signal": signal}


async def test_signal_ambiguous_is_left_empty(hass: HomeAssistant) -> None:
    _add(hass, "binary_sensor", "eas_signal", original_device_class="connectivity")
    _add(hass, "binary_sensor", "other_adapter_signal", original_device_class="connectivity")
    assert "boiler_signal" not in detect(hass).entities

    _add(hass, "binary_sensor", "ebusd_global_signal", original_device_class="connectivity")
    _add(hass, "binary_sensor", "ebusd2_global_signal", original_device_class="connectivity")
    assert "boiler_signal" not in detect(hass).entities


async def test_signal_device_class_set_by_the_user_counts(hass: HomeAssistant) -> None:
    signal = _add(hass, "binary_sensor", "eas_signal")
    er.async_get(hass).async_update_entity(signal, device_class="connectivity")
    assert detect(hass).entities == {"boiler_signal": signal}


# ------------------------------------------------------------------ gas meter
def _gas_volume(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(entity_id, "1234.5", {"device_class": "gas", "unit_of_measurement": "m³", "state_class": "total_increasing"})


def _gas_flow(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(entity_id, "0.0", {"unit_of_measurement": "m³/h", "state_class": "measurement"})


async def test_gas_meter_single_candidates(hass: HomeAssistant) -> None:
    _gas_volume(hass, "sensor.gas_meter_volume")
    _gas_flow(hass, "sensor.gas_meter_flow")
    hass.states.async_set("sensor.water_meter", "88.1", {"device_class": "water", "unit_of_measurement": "m³", "state_class": "total_increasing"})
    hass.states.async_set("sensor.gas_price", "0.09", {"unit_of_measurement": "€/m³"})
    # Thriftherm's own airflow estimate is in m³/h as well
    own = _add(hass, "sensor", "thriftherm_midea_airflow_estimate", platform="thriftherm")
    hass.states.async_set(own, "300", {"unit_of_measurement": "m³/h"})
    assert detect(hass).entities == {"gas_volume": "sensor.gas_meter_volume", "gas_flow": "sensor.gas_meter_flow"}


async def test_gas_meter_ambiguous_is_left_empty(hass: HomeAssistant) -> None:
    _gas_volume(hass, "sensor.gas_meter_volume")
    _gas_volume(hass, "sensor.gas_meter_volume_2")
    _gas_flow(hass, "sensor.gas_meter_flow")
    hass.states.async_set("sensor.gas_meter_volume_raw", "1234.5", {"device_class": "gas", "unit_of_measurement": "m³"})  # no state class
    assert detect(hass).entities == {"gas_flow": "sensor.gas_meter_flow"}

    _gas_flow(hass, "sensor.ventilation_flow")
    assert detect(hass).entities == {}


# ------------------------------------------------------------------ pre-filling a form
FOUND = Detection(
    "vaillant_bai",
    "bai2",
    {
        "boiler_flow_temp": "sensor.flow", "boiler_return_temp": "sensor.return", "boiler_pump_running": "sensor.wp",
        "boiler_signal": "binary_sensor.signal", "gas_volume": "sensor.gas",
    },
    found=3,
    total=6,
)


def test_prefill_an_empty_configuration_takes_everything() -> None:
    assert prefill({}, FOUND) == {**FOUND.entities, "boiler_ebus_circuit": "bai2", "boiler_profile": "vaillant_bai"}
    assert prefill({"boiler_ebus_circuit": "bai", "boiler_flow_temp": None, "gas_flow": None}, FOUND) == prefill({}, FOUND)


def test_prefill_never_replaces_a_configured_entity() -> None:
    current = {"boiler_ebus_circuit": "bai2", "boiler_flow_temp": "sensor.my_flow", "gas_volume": "sensor.my_gas"}
    assert prefill(current, FOUND) == {
        "boiler_return_temp": "sensor.return", "boiler_pump_running": "sensor.wp", "boiler_signal": "binary_sensor.signal",
    }


def test_prefill_does_not_mix_in_another_boiler() -> None:
    # configured for circuit bai (the default), the boiler found is on bai2: only signal and meter are offered
    assert prefill({"boiler_flow_temp": "sensor.my_flow"}, FOUND) == {"boiler_signal": "binary_sensor.signal", "gas_volume": "sensor.gas"}


def test_prefill_without_a_boiler_offers_signal_and_meter() -> None:
    found = Detection(None, None, {"boiler_signal": "binary_sensor.signal"})
    assert prefill({}, found) == {"boiler_signal": "binary_sensor.signal"}
