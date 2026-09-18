"""Loading and saving the coordinator's state.

Everything the user set (modes, overrides, boosts, room temperatures) and
everything that was learned (COP map, heat-up rates, controller memories)
survives a restart. Stored values may be damaged or from an older version, so
every field is read defensively: a bad value is dropped, never fatal.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from . import learning_reset
from .const import MODES
from .engines.boiler_control import BoilerMemory
from .engines.common import as_dict, as_float
from .engines.heat_pump_control import ControlMemory
from .engines.room_control import RoomCtrlMemory
from .engines.learning import CoolRateStats, CopMap, HeatRateStats
from .models import Override

if TYPE_CHECKING:
    from .coordinator import ThrifthermCoordinator


def restore(coord: ThrifthermCoordinator, data: dict[str, Any]) -> None:
    coord.cop_map = CopMap.from_storage(data.get("cop_map"))
    coord.heat_rates = HeatRateStats.from_storage(data.get("heat_rates"))
    coord.cool_rates = CoolRateStats.from_storage(data.get("cool_rates"))
    block = data.get("midea_block_until")
    coord.heat_pump_tracker.block_until = as_float(block) if block else None
    now = time.time()
    for key, raw in as_dict(data.get("overrides")).items():
        try:
            ov = Override(float(raw["target"]), float(raw["until_ts"]), float(raw.get("set_at_ts", now)))
        except (KeyError, TypeError, ValueError):
            continue
        if ov.until_ts > now:
            coord.overrides[key] = ov
    rt = data.get("away_return_ts")
    coord.away_return_ts = as_float(rt) if rt else None
    # a mode that is not offered (any more) keeps the default
    for key, allowed in (
        ("mode", MODES),
        ("control_mode", coord.control_modes),
        ("boiler_control_mode", coord.boiler_control_modes),
        ("room_control_mode", coord.room_control_modes),
    ):
        if data.get(key) in allowed:
            setattr(coord, key, data[key])
    coord.control_memory = ControlMemory.from_storage(data.get("control_memory"))
    coord.boiler_memory = BoilerMemory.from_storage(data.get("boiler_memory"))
    stored = data.get("room_temps")
    if isinstance(stored, dict):
        coord.room_temps = {
            str(room): {k: v for k, raw in values.items() if k in ("comfort", "setback") and (v := as_float(raw)) is not None}
            for room, values in stored.items()
            if isinstance(values, dict)
        }
    coord.boosts = {k: v for k, raw in as_dict(data.get("boosts")).items() if (v := as_float(raw)) is not None and v > now}
    stored_rooms = data.get("room_ctrl_memory")
    if isinstance(stored_rooms, dict):
        coord.room_ctrl_memory = {str(room): RoomCtrlMemory.from_storage(mem) for room, mem in stored_rooms.items()}
    last_reset = data.get("last_learning_reset")
    coord.last_learning_reset = last_reset if isinstance(last_reset, dict) else None


def to_store(coord: ThrifthermCoordinator) -> dict[str, Any]:
    return {
        "cop_map": coord.cop_map.to_storage(),
        "heat_rates": coord.heat_rates.to_storage(),
        "cool_rates": coord.cool_rates.to_storage(),
        "midea_block_until": coord.heat_pump_tracker.block_until,
        "overrides": {k: {"target": o.target, "until_ts": o.until_ts, "set_at_ts": o.set_at_ts} for k, o in coord.overrides.items()},
        "away_return_ts": coord.away_return_ts,
        "mode": coord.mode,
        "control_mode": coord.control_mode,
        "control_memory": coord.control_memory.to_storage(),
        "boosts": coord.boosts,
        "boiler_control_mode": coord.boiler_control_mode,
        "boiler_memory": coord.boiler_memory.to_storage(),
        "room_control_mode": coord.room_control_mode,
        "room_temps": coord.room_temps,
        "room_ctrl_memory": {room: mem.to_storage() for room, mem in coord.room_ctrl_memory.items()},
        "learning_basis": learning_reset.basis(coord.config),
        "last_learning_reset": coord.last_learning_reset,
    }
