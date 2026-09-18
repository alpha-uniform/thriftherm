"""Safety engine: validity of inputs, frost protection, fallback states.

Rules (see architecture §16):
- A single failing sensor never disables heating permanently.
- If every room temperature is missing, the system is in `fallback`.
- Any missing or stale critical input degrades the state and is reported.
"""

from __future__ import annotations

from ..const import SAFETY_DEGRADED, SAFETY_FALLBACK, SAFETY_OK
from ..models import HeatingSnapshot, RoomResult, SafetyResult

# A room hovering at the frost limit would otherwise toggle the boiler between
# heat and block every few minutes; it stays a frost room until 1 K above.
FROST_HYSTERESIS_K = 1.0


def evaluate(snapshot: HeatingSnapshot, rooms: dict[str, RoomResult], previous_frost_rooms: tuple[str, ...] = ()) -> SafetyResult:
    issues: list[str] = []
    frost_rooms: list[str] = []

    if not snapshot.rooms:
        issues.append("no_rooms_configured")

    invalid_rooms = [key for key, r in rooms.items() if r.temperature is None]
    for key in invalid_rooms:
        issues.append(f"room_temperature_missing:{key}")
    for key, r in rooms.items():
        if r.window_unknown:
            issues.append(f"window_state_unknown:{key}")
        for issue in r.issues:
            if issue.startswith("using_trv_local_temperature"):
                issues.append(f"room_using_trv_fallback:{key}")
        if r.temperature is not None:
            limit = snapshot.params.frost_temp + (FROST_HYSTERESIS_K if key in previous_frost_rooms else 0.0)
            if r.temperature < limit:
                frost_rooms.append(key)

    if not snapshot.outdoor_temp.valid:
        issues.append(f"outdoor_temperature_missing:{snapshot.outdoor_temp.reason}")

    boiler = snapshot.boiler
    if boiler.signal_ok is False:
        issues.append("ebusd_signal_lost")
    elif boiler.flow_temp.entity_id and not boiler.flow_temp.valid:
        issues.append(f"boiler_flow_temp_invalid:{boiler.flow_temp.reason}")

    heat_pump = snapshot.heat_pump
    if heat_pump.configured and heat_pump.plug_power.entity_id and not heat_pump.available:
        issues.append("midea_unavailable")
    if heat_pump.configured and heat_pump.error_code not in (None, 0):
        issues.append(f"midea_error_code:{heat_pump.error_code}")
    if heat_pump.configured and heat_pump.plug_power.entity_id and not heat_pump.plug_power.valid:
        issues.append(f"midea_power_sensor_invalid:{heat_pump.plug_power.reason}")

    if rooms and len(invalid_rooms) == len(rooms):
        state = SAFETY_FALLBACK
        issues.append("all_room_temperatures_missing")
    elif issues:
        state = SAFETY_DEGRADED
    else:
        state = SAFETY_OK

    return SafetyResult(state=state, issues=tuple(issues), frost_rooms=tuple(frost_rooms))
