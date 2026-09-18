"""Shared fixtures: synthetic snapshots for the pure engines."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from custom_components.thriftherm.models import (
    BoilerState,
    HeatingSnapshot,
    HeatPumpState,
    Parameters,
    Prices,
    RoomConfig,
    RoomState,
    SensorReading,
    TimeWindow,
)

pytest_plugins = "pytest_homeassistant_custom_component"


def reading(value: float | None, valid: bool = True, entity_id: str = "sensor.x", age_s: float = 10.0, reason=None) -> SensorReading:
    if value is None:
        return SensorReading(entity_id, None, age_s, False, reason or "unavailable")
    return SensorReading(entity_id, value, age_s, valid, None if valid else (reason or "stale"))


MISSING = SensorReading.missing()


def room_config(key: str, heat_pump: bool = False, priority: int = 5, comfort: float = 21.0, setback: float = 17.0) -> RoomConfig:
    return RoomConfig(
        key=key,
        name=key.title(),
        priority=priority,
        temperature_sensor=f"sensor.{key}_temp",
        humidity_sensor=f"sensor.{key}_rh",
        window_sensors=(f"binary_sensor.{key}_window",),
        climate_entity=f"climate.{key}",
        served_by_heat_pump=heat_pump,
        comfort_temp=comfort,
        setback_temp=setback,
        schedule_weekday=(TimeWindow(time(5, 15), time(7, 30)), TimeWindow(time(17, 30), time(22, 0))),
        schedule_weekend=(TimeWindow(time(9, 0), time(22, 0)),),
    )


def room_state(
    cfg: RoomConfig,
    temp: float | None = 20.0,
    rh: float | None = 50.0,
    window: str | None = "off",
    window_open_since: float | None = None,
    trv_local: float | None = None,
    gain_w: float | None = None,
    history=(),
) -> RoomState:
    return RoomState(
        config=cfg,
        temperature=reading(temp) if temp is not None else reading(None),
        humidity=reading(rh) if rh is not None else MISSING,
        window_readings=((cfg.window_sensors[0], window, window_open_since),),
        trv_local_temp=trv_local,
        trv_target_temp=None,
        internal_gain_w=gain_w,
        temperature_history=tuple(history),
    )


def heat_pump_state(
    available: bool = True,
    hvac_mode: str = "heat",
    compressor_hz: float | None = 40.0,
    running_s: float | None = 900.0,
    plug_power: float | None = 800.0,
    intake: tuple[float, float] | None = (20.0, 45.0),
    outlet: tuple[float, float | None] | None = (38.0, 20.0),  # (temp, rh); the rh is ignored, see test_cop
    fan_rpm: float | None = 600.0,
    error_code: int | None = 0,
    indoor_coil: float | None = 45.0,
    outdoor_coil: float | None = 0.0,
    realtime_power: float | None = None,
) -> HeatPumpState:
    return HeatPumpState(
        available=available,
        hvac_mode=hvac_mode if available else None,
        hvac_action=None,
        target_temp=22.0,
        indoor_temp=21.0,
        outdoor_temp=5.0,
        indoor_coil_temp=indoor_coil,
        outdoor_coil_temp=outdoor_coil,
        compressor_hz=compressor_hz,
        fan_rpm=fan_rpm,
        realtime_power_w=realtime_power,
        error_code=error_code,
        plug_power=reading(plug_power) if plug_power is not None else MISSING,
        intake_temp=reading(intake[0]) if intake else MISSING,
        intake_rh=reading(intake[1]) if intake else MISSING,
        outlet_temp=reading(outlet[0]) if outlet else MISSING,
        compressor_running_s=running_s,
    )


def boiler_state(
    signal: bool | None = True,
    flow: float | None = 45.0,
    ret: float | None = 38.0,
    pump: str | None = "on",
    hwc_mode: str | None = "off",
    gas_flow: float | None = 1.2,
    gas_volume: float | None = 100.0,
) -> BoilerState:
    return BoilerState(
        signal_ok=signal,
        flow_temp=reading(flow, entity_id="sensor.flow") if flow is not None else reading(None, entity_id="sensor.flow"),
        return_temp=reading(ret) if ret is not None else reading(None),
        pump_state=pump,
        hwc_mode=hwc_mode,
        gas_volume_m3=reading(gas_volume) if gas_volume is not None else MISSING,
        gas_flow_m3h=reading(gas_flow) if gas_flow is not None else MISSING,
        circulation_l_h=860.0,
    )


PRICES = Prices(electricity_eur_kwh=0.306, gas_eur_kwh=0.0889, boiler_efficiency=0.84, gas_calorific_kwh_m3=11.45, gas_z_factor=0.9381)
PARAMS = Parameters(airflow_curve=((300.0, 200.0), (600.0, 450.0), (900.0, 700.0)))


def snapshot(
    rooms: dict[str, RoomState],
    heat_pump: HeatPumpState | None = None,
    boiler: BoilerState | None = None,
    mode: str = "auto",
    outdoor: float | None = 5.0,
    now: datetime | None = None,
    params: Parameters = PARAMS,
) -> HeatingSnapshot:
    return HeatingSnapshot(
        now=now or datetime(2026, 1, 12, 18, 0),  # Monday evening inside comfort window
        mode=mode,
        rooms=rooms,
        heat_pump=heat_pump or heat_pump_state(),
        boiler=boiler or boiler_state(),
        outdoor_temp=reading(outdoor) if outdoor is not None else MISSING,
        outdoor_temp_source="sensor.outdoor",
        outdoor_rh=reading(80.0),
        prices=PRICES,
        params=params,
    )


@pytest.fixture
def two_rooms() -> dict[str, RoomState]:
    bath = room_config("badezimmer", heat_pump=True, priority=1, comfort=21.0)
    living = room_config("wohnzimmer", heat_pump=False, priority=3, comfort=18.5)
    return {"badezimmer": room_state(bath, temp=18.5), "wohnzimmer": room_state(living, temp=17.0)}
