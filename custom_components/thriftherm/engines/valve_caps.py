"""What a radiator valve can do, told by the entities of its device (docs/PHASE13_VALVES.md §3.2, §3.5).

Pure logic, no Home Assistant import: the adapter hands over the entities of the valve's device.

No profile per model is needed to drive a valve: the entities say what it can. An entity is told by its key (the
translation key where the integration names one, else the end of its unique id) or the end of its entity id. The names
come from the device definitions of Zigbee2MQTT (zigbee-herdsman-converters, MIT, (c) 2018 Koen Kanters), the ZHA quirks
and ZHA (Apache-2.0), Home Assistant's Shelly integration (Apache-2.0), and a Sonoff TRVZBT in use (Zigbee2MQTT, read
2026-11-13). Only what deviates from that goes into QUIRKS, per model; facts from Better Thermostat (AGPL-3.0) are
written anew there, none of its code.

Child lock, the valve's own window detection, its schedule and its mode are found to be read, never written: what
somebody set at the valve is the truth (decided 2026-11-13).
"""

from __future__ import annotations

from dataclasses import dataclass

# Thriftherm sends the room temperature to a valve on every change of 0.1 K, and refreshes it at the latest after this:
# below the shortest limit known (Bosch BTH-RA 30 min, Danfoss Ally with its radiator covered 35 min) and well below the
# two hours after which the Sonoff TRVZB falls back on its own sensor (measured 2026-11-14 on a TRVZBT, to the
# minute; it takes the external value again 2 s after the next one is written).
EXTERNAL_REFRESH_S = 25 * 60.0
TEMPERATURE_UNITS = ("°C", "°F", "K")

WAY_EXTERNAL = "external"  # the room temperature into the valve's input (§3.3)
WAY_OFFSET = "offset"  # its calibration offset
WAY_SETPOINT = "setpoint"  # a corrected setpoint, for every valve that measures
WAY_PLAIN = "plain"  # the room's target as it is, for one that does not


@dataclass(frozen=True)
class Entity:
    """An entity of the valve's device as the adapter reads it."""

    entity_id: str
    domain: str
    key: str  # translation key, else unique id
    unit: str | None = None
    options: tuple[str, ...] = ()
    writable: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class ValveCaps:
    climate: str
    measures: bool = True  # its climate entity shows a current temperature
    external_temperature: str | None = None
    sensor_select: str | None = None
    remote_option: str | None = None
    local_option: str | None = None
    sensor_switch: str | None = None  # ZHA: a switch "use the external sensor"
    offset: str | None = None
    setpoint_offset: str | None = None  # Danfoss: moves the setpoint, no calibration of the sensor
    opening: str | None = None
    closing: str | None = None
    child_lock: str | None = None
    window_detection: str | None = None
    battery: str | None = None
    last_seen: str | None = None
    hysteresis: str | None = None  # how far below its setpoint it opens (TRVZB: temperature_accuracy), read only
    disabled: tuple[str, ...] = ()  # found, but switched off in Home Assistant
    disabled_roles: tuple[tuple[str, str], ...] = ()  # (role, entity) of each found but switched off
    selection_disabled: bool = False  # it has a sensor selection, switched off in Home Assistant: no way a (2026-11-15)

    def ways(self) -> tuple[str, ...]:
        """The ways the valve can be driven, the most exact first (§3.1); the opening is a choice of the room."""
        if not self.measures:
            return (WAY_PLAIN,)
        ways = []
        if self.external_temperature and not self.selection_disabled:
            ways.append(WAY_EXTERNAL)
        if self.offset:
            ways.append(WAY_OFFSET)
        ways.append(WAY_SETPOINT)
        return tuple(ways)


def _is(entity: Entity, *names: str) -> bool:
    """The key or the entity id ends in one of `names` (Zigbee2MQTT appends "_zigbee2mqtt" to its unique ids)."""
    key = entity.key.lower().removesuffix("_zigbee2mqtt")
    object_id = entity.entity_id.split(".", 1)[-1].lower()
    return any(key == name or key.endswith("_" + name) or object_id.endswith("_" + name) for name in names)


def _temperature(entity: Entity) -> bool:
    return entity.domain == "number" and entity.writable and entity.unit in TEMPERATURE_UNITS


def _percent(entity: Entity) -> bool:
    return entity.domain == "number" and entity.writable and entity.unit in ("%", None)


def _first(options: tuple[str, ...], *wanted: str) -> str | None:
    return next((o for o in wanted if o in options), None)


def caps_of(entities: tuple[Entity, ...], climate: str | None = None, measures: bool = True) -> ValveCaps:
    """What the valve whose device has `entities` can do."""
    found: dict[str, str | None] = {}
    disabled: list[str] = []
    climate = climate or next((x.entity_id for x in entities if x.domain == "climate"), "")

    roles: list[tuple[str, str]] = []

    def take(role: str, entity: Entity) -> None:
        if not entity.enabled:
            disabled.append(entity.entity_id)
            roles.append((role, entity.entity_id))
        elif found.get(role) is None:
            found[role] = entity.entity_id

    remote = local = None
    for x in entities:
        if x.domain == "number" and _is(x, "regulation_setpoint_offset"):
            if _temperature(x):
                take("setpoint_offset", x)
        elif x.domain == "number" and _is(
            x, "external_temperature_input", "external_temperature_sensor_value", "external_temperature",
            "external_measured_room_sensor", "remote_temperature",
        ):
            if _temperature(x):
                take("external_temperature", x)
        elif x.domain == "number" and _is(x, "local_temperature_calibration", "calibration"):
            if _temperature(x):
                take("offset", x)
        elif x.domain == "number" and _is(x, "valve_closing_degree", "idle_valve_position"):
            if _percent(x):
                take("closing", x)
        elif x.domain == "number" and _is(x, "valve_opening_degree", "heating_valve_position", "valve_position"):
            if _percent(x):
                take("opening", x)
        elif x.domain == "select" and (_is(x, "temperature_sensor_select") or (_is(x, "sensor") and "external" in x.options)):
            take("sensor_select", x)
            if not x.enabled:
                found["_selection_disabled"] = x.entity_id
            if x.enabled:
                remote = _first(x.options, "remote_temperature", "external")
                local = _first(x.options, "local_temperature", "internal")
        elif x.domain == "switch" and _is(x, "external_temperature_sensor", "prioritize_external_temperature_sensor"):
            take("sensor_switch", x)
        elif x.domain in ("switch", "lock", "select") and _is(x, "child_lock", "keypad_lockout"):
            take("child_lock", x)
        elif x.domain == "switch" and _is(x, "open_window", "window_detection", "window_open_feature"):
            take("window_detection", x)
        elif x.domain == "number" and _is(x, "temperature_accuracy", "hysteresis"):
            take("hysteresis", x)
        elif x.domain == "sensor" and _is(x, "battery"):
            take("battery", x)
        elif x.domain == "sensor" and _is(x, "last_seen"):
            take("last_seen", x)
    selection_disabled = found.pop("_selection_disabled", None) is not None and found.get("sensor_select") is None
    return ValveCaps(
        climate=climate, measures=measures, remote_option=remote, local_option=local, disabled=tuple(disabled),
        selection_disabled=selection_disabled, disabled_roles=tuple(roles), **found,
    )


@dataclass(frozen=True)
class ValveQuirks:
    """What a model does otherwise than its entities tell (§3.5)."""

    unreliable_hvac_action: bool = False  # reports "idle" though open (Versatile Thermostat's documentation)
    min_opening_pct: float = 0.0  # it is never set below this opening: it loses its calibration (Versatile Thermostat)
    offset_inverted: bool = False  # takes the calibration offset the other way round
    min_setpoint: float | None = None  # a lower one reads as "off"
    external_max_age_s: float | None = None  # it takes its own sensor again when no external value came for this long
    exercise_s: float = 7 * 86400.0  # a valve that did not open for this long is moved (§5.6)
    # its opening number is a limit the valve controls under, not its position: only then is it set in part while the
    # room heats along and the user's value given back (review 2026-11-18: a Shelly's valve_position is its position)
    opening_is_limit: bool = False


# (manufacturer, model) as lower-case parts of what the device registry names. Sources in the docstring of each.
QUIRKS: dict[tuple[str, str], ValveQuirks] = {
    # Sonoff TRVZB: hvac_action, the 3 % (Versatile Thermostat, documentation/en/self-regulation.md, over-climate.md).
    # A TRVZBT in use shows as "Zigbee thermostatic radiator valve"; whether all of it holds there is measured (§15).
    # The 2 h without an external value: measured on the TRVZBT 2026-11-14 (docs/auswertungen, Phase B).
    # Moved every 84 h: a fact of Better Thermostat's model fixes (AGPL-3.0), no code of it.
    ("sonoff", "trvzb"): ValveQuirks(
        unreliable_hvac_action=True, min_opening_pct=3.0, external_max_age_s=2 * 3600.0, exercise_s=84 * 3600.0,
        opening_is_limit=True,
    ),
    ("sonoff", "thermostatic radiator valve"): ValveQuirks(
        unreliable_hvac_action=True, min_opening_pct=3.0, external_max_age_s=2 * 3600.0, exercise_s=84 * 3600.0,
        opening_is_limit=True,
    ),
    # Avatto ME167: the sign of the offset (a fact Better Thermostat records)
    ("avatto", "me167"): ValveQuirks(offset_inverted=True),
    # Shelly TRV Gen1: 4 °C reads as off in Home Assistant (a fact Better Thermostat records)
    ("shelly", "shtrv-01"): ValveQuirks(min_setpoint=4.5),
    # Danfoss Ally: external value at least every 3 h (Zigbee2MQTT device page 014G2461); covered radiator 35 min
    ("danfoss", "etrv0100"): ValveQuirks(external_max_age_s=3 * 3600.0),
    ("danfoss", "014g2461"): ValveQuirks(external_max_age_s=3 * 3600.0),
    # Bosch BTH-RA: external value at least every 30 min (Zigbee2MQTT device page BTH-RA)
    ("bosch", "bth-ra"): ValveQuirks(external_max_age_s=1800.0),
}


def quirks_of(manufacturer: str | None, model: str | None) -> ValveQuirks:
    """The quirks of a model, none for one not listed."""
    maker, name = (manufacturer or "").lower(), (model or "").lower()
    return next((q for (m, n), q in QUIRKS.items() if m in maker and n in name), ValveQuirks())
