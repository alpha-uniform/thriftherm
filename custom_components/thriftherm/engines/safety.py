"""Safety engine: validity of inputs, frost protection, fallback states.

Rules:
- A single failing sensor never disables heating permanently.
- If every room temperature is missing, the system is in `fallback`.
- Any missing or stale critical input degrades the state and is reported.
"""

from __future__ import annotations

from ..const import SAFETY_DEGRADED, SAFETY_FALLBACK, SAFETY_OK, SYSTEM_NONE
from ..models import HeatingSnapshot, RoomResult, SafetyResult

# Issues of the heat pump add-on start with this; they never concern the boiler.
HEAT_PUMP_ISSUE_PREFIX = "midea_"

# A room hovering at the frost limit would otherwise toggle the boiler between
# heat and block every few minutes; it stays a frost room until 1 K above.
FROST_HYSTERESIS_K = 1.0

# What a reading says that has not arrived yet: its entity is not loaded, or has no state so far.
NOT_ARRIVED = frozenset({"unavailable", "unknown", "empty", "none", "None", "entity_not_found"})
# A heat pump away no longer than this is a dropout of its bridge, not a fault (seen with Midea AC LAN).
HEAT_PUMP_DROPOUT_S = 10.0


def arrivals(snapshot: HeatingSnapshot, rooms: dict[str, RoomResult]) -> set[str]:
    """The inputs that report now, by the names `evaluate` asks `arrived` for."""
    found = {f"room:{key}" for key, state in snapshot.rooms.items() if state.temperature.valid}
    found |= {f"window:{key}" for key, r in rooms.items() if not r.window_unknown}
    if snapshot.outdoor_temp.valid:
        found.add("outdoor")
    if snapshot.boiler.flow_temp.valid:
        found.add("boiler_flow")
    if snapshot.heat_pump.available:
        found.add("heat_pump")
    if snapshot.heat_pump.plug_power.valid:
        found.add("heat_pump_power")
    return found


def evaluate(
    snapshot: HeatingSnapshot,
    rooms: dict[str, RoomResult],
    previous_frost_rooms: tuple[str, ...] = (),
    boiler_overheated: bool = False,
    celsius: bool = True,
    arrived: frozenset[str] | None = None,
    heat_pump_gone_s: float | None = None,
) -> SafetyResult:
    """`celsius` False: Home Assistant shows temperatures in another unit, which every limit here would misread.

    `arrived`: within the start's grace period, the inputs (see `arrivals`) that have reported since. One
    that has not yet is neither listed nor a degradation: after every restart the state was "degraded" for
    50-60 s with 9-10 issues, and a warning went to the log (2026-11-08). One that is there but wrong, or
    went away again, counts as ever, and no room temperature at all is still hands off. None: the start is
    over. `heat_pump_gone_s`: how long the heat pump has been away.
    """
    issues: list[str] = []
    frost_rooms: list[str] = []
    pending: set[str] = set()  # not arrived yet since the start

    def not_arrived(name: str, reading) -> bool:
        if arrived is None or name in arrived:
            return False
        return reading is None or (not reading.valid and reading.reason in NOT_ARRIVED)

    if not snapshot.rooms:
        issues.append("no_rooms_configured")

    invalid_rooms = [key for key, r in rooms.items() if r.temperature is None]
    for key in invalid_rooms:
        issues.append(f"room_temperature_missing:{key}")
    for key, r in rooms.items():
        state = snapshot.rooms.get(key)
        if state is not None and not_arrived(f"room:{key}", state.temperature):
            pending.update({f"room_temperature_missing:{key}", f"room_using_trv_fallback:{key}"})
        if r.window_unknown:
            issues.append(f"window_state_unknown:{key}")
            if not_arrived(f"window:{key}", None):  # a contact tells open or closed only once it has reported
                pending.add(issues[-1])
        for issue in r.issues:
            if issue.startswith("using_trv_local_temperature"):
                issues.append(f"room_using_trv_fallback:{key}")
            elif issue == "thermostat_silent":
                issues.append(f"room_thermostat_silent:{key}")
        if r.temperature is not None:
            limit = snapshot.params.frost_temp + (FROST_HYSTERESIS_K if key in previous_frost_rooms else 0.0)
            if r.temperature < limit:
                frost_rooms.append(key)

    if not snapshot.outdoor_temp.valid:
        issues.append(f"outdoor_temperature_missing:{snapshot.outdoor_temp.reason}")
        if not_arrived("outdoor", snapshot.outdoor_temp):
            pending.add(issues[-1])

    boiler = snapshot.boiler
    if boiler.signal_ok is False:
        issues.append("ebusd_signal_lost")
    elif boiler.flow_temp.entity_id and not boiler.flow_temp.valid:
        issues.append(f"boiler_flow_temp_invalid:{boiler.flow_temp.reason}")
        if not_arrived("boiler_flow", boiler.flow_temp):
            pending.add(issues[-1])
    if boiler_overheated:
        issues.append("boiler_overheated")

    heat_pump = snapshot.heat_pump
    if heat_pump.configured and not heat_pump.available:
        issues.append("midea_unavailable")  # with or without a plug that measures its power
        if not_arrived("heat_pump", None) or (heat_pump_gone_s is not None and heat_pump_gone_s <= HEAT_PUMP_DROPOUT_S):
            pending.add(issues[-1])
    if heat_pump.configured and heat_pump.error_code not in (None, 0):
        issues.append(f"midea_error_code:{heat_pump.error_code}")
    if heat_pump.configured and heat_pump.plug_power.entity_id and not heat_pump.plug_power.valid:
        # refused for its unit, the unit is named: "power reading missing" did not say why (review 2026-11-08)
        unit = heat_pump.plug_power.unit
        issues.append(f"midea_power_sensor_invalid:{heat_pump.plug_power.reason}" + ("" if unit is None else f":{unit}"))
        if not_arrived("heat_pump_power", heat_pump.plug_power):
            pending.add(issues[-1])

    # Heat pump faults are listed but degrade the state only where the heat pump is the only
    # heat source: a switched-off add-on otherwise kept "degraded" on for days and hid the
    # faults that matter (measured 2026-09-26 to 2026-10-03).
    counted = [
        i for i in issues
        if i not in pending and (not i.startswith(HEAT_PUMP_ISSUE_PREFIX) or snapshot.prices.system_type == SYSTEM_NONE)
    ]
    if not celsius:
        # Switched to US customary after the setup: 44 °F (6.7 °C) passed as 44 °C and hid a cold room,
        # and a setpoint of 20 went out as 20 °F (review 2026-11-08). Hands off until it is °C again.
        issues.append("temperature_unit_not_celsius")
        state = SAFETY_FALLBACK
    elif not rooms or len(invalid_rooms) == len(rooms):
        # nothing to judge the heating by: hands off, the boiler runs on its own controls
        state = SAFETY_FALLBACK
        if rooms:
            issues.append("all_room_temperatures_missing")
    elif counted:
        state = SAFETY_DEGRADED
    else:
        state = SAFETY_OK
    if state != SAFETY_FALLBACK:
        issues = [i for i in issues if i not in pending]  # a hands-off keeps all it rests on

    return SafetyResult(state=state, issues=tuple(issues), frost_rooms=tuple(frost_rooms))
