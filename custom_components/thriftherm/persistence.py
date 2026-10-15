"""Loading and saving the coordinator's state.

Everything the user set (modes, overrides, boosts, room temperatures) and
everything that was learned (COP map, heat-up rates, controller memories)
survives a restart. Stored values may be damaged or from an older version, so
every field is read defensively: a bad value is dropped, never fatal.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from . import learning_reset
from .engines import hand
from .engines.boiler_memory import BoilerMemory
from .engines.gas_split import GasSplit
from .engines.common import as_dict, as_float
from .engines.heat_call import CallMemory
from .engines.heat_pump_control import ControlMemory
from .engines.room_control import RoomCtrlMemory
from .engines.learning import CoolRateStats, CopMap, HeatRateStats
from .const import SOURCE_BOILER, SOURCE_HEAT_PUMP
from .models import DryingState, Override

if TYPE_CHECKING:
    from .coordinator import ThrifthermCoordinator

LOAD_LOG_COLUMNS = 10
# Half a day of cycles survives a restart (about 50 kB); the store is written every ten minutes
# and on every user action, a full day in it would be rewritten for nothing.
LOAD_LOG_STORED_ROWS = 720


def restore(coord: ThrifthermCoordinator, data: dict[str, Any]) -> None:
    # only rooms still configured: a room created again under a deleted one's name starts fresh
    rooms = {r.key for r in coord.builder.rooms}
    coord.cop_map = CopMap.from_storage(data.get("cop_map"))
    coord.heat_rates = HeatRateStats.from_storage(data.get("heat_rates"))
    coord.heat_rates.forget([r for r in {*coord.heat_rates.rates, *coord.heat_rates.gradients} if r not in rooms])
    coord.cool_rates = CoolRateStats.from_storage(data.get("cool_rates"))
    coord.cool_rates.forget([r for r in coord.cool_rates.rates if r not in rooms])
    block = data.get("midea_block_until")
    coord.heat_pump_tracker.block_until = as_float(block) if block else None
    cause = data.get("midea_block_reason")
    coord.heat_pump_tracker.block_reason = cause if isinstance(cause, str) else None
    now = time.time()
    for key, raw in as_dict(data.get("overrides")).items():
        try:
            by = raw.get("by") if isinstance(raw, dict) else None
            who = by if by in hand.SHOWN else None
            ov = Override(float(raw["target"]), float(raw["until_ts"]), float(raw.get("set_at_ts", now)), who)
        except (KeyError, TypeError, ValueError):
            continue
        if ov.until_ts > now and key in rooms:
            coord.overrides[key] = ov
    rt = data.get("away_return_ts")
    coord.away_return_ts = as_float(rt) if rt else None
    coord.away_return_provisional = coord.away_return_ts is not None and data.get("away_return_provisional") is True
    # a mode that is not offered (any more) keeps the default, e.g. "heat pump only" after the heat pump was removed
    for key, allowed in (
        ("mode", coord.modes),
        ("control_mode", coord.control_modes),
        ("boiler_control_mode", coord.boiler_control_modes),
        ("room_control_mode", coord.room_control_modes),
    ):
        if data.get(key) in allowed:
            setattr(coord, key, data[key])
    coord.control_memory = ControlMemory.from_storage(data.get("control_memory"))
    coord.boiler_memory = BoilerMemory.from_storage(data.get("boiler_memory"))
    coord.gas_split = GasSplit.from_storage(data.get("gas_split"))
    if data.get("boiler_entities_held") is True:
        coord._boiler_entities_released = False  # handed back by the first cycle that does not command
    # a boiler handed back before the restart keeps following the curve
    coord._boiler_handed_back_flow = as_float(data.get("boiler_handed_back_flow"))
    stored_log = data.get("boiler_load_log")
    if isinstance(stored_log, list):
        coord.load_log.extend(row for row in stored_log if isinstance(row, list) and len(row) == LOAD_LOG_COLUMNS)
    stored = data.get("room_temps")
    if isinstance(stored, dict):
        coord.room_temps = {
            str(room): {k: v for k, raw in values.items() if k in ("comfort", "setback") and (v := as_float(raw)) is not None}
            for room, values in stored.items()
            if isinstance(values, dict) and room in rooms
        }
    coord.boosts = {
        k: v for k, raw in as_dict(data.get("boosts")).items() if (v := as_float(raw)) is not None and v > now and k in rooms
    }
    stored_rooms = data.get("room_ctrl_memory")
    if isinstance(stored_rooms, dict):
        coord.room_ctrl_memory = {room: RoomCtrlMemory.from_storage(mem) for room, mem in stored_rooms.items() if room in rooms}
    stored_calls = data.get("call_memory")
    if isinstance(stored_calls, dict):
        coord.call_memory = {room: CallMemory.from_storage(mem) for room, mem in stored_calls.items() if room in rooms}
    # What a decision leans on from one cycle to the next. Without it a restart flipped the advice
    # (the hysteresis around the break-even started from nothing, so a heat pump just above it
    # read as dearer and was stopped) and dropped a pre-heating that had begun.
    cheaper = data.get("last_cheaper")
    coord._last_cheaper = cheaper if cheaper in (SOURCE_HEAT_PUMP, SOURCE_BOILER) else None
    coord._preheat_latch = {
        k: v
        for k, raw in as_dict(data.get("preheat_latch")).items()
        if k in rooms and (v := as_float(raw)) is not None and v > now
    }
    # A drying under way keeps its start (and so its time limit), a finished one its guard time,
    # a shower noticed its wait: a restart used to forget all three.
    coord.drying_states = {
        room: state
        for room, raw in as_dict(data.get("drying_states")).items()
        if room in rooms and (state := _drying(raw)) is not None
    }
    coord._stored_heat_pump_watch = as_dict(data.get("heat_pump_watch"))  # taken up when the listeners start
    last_reset = data.get("last_learning_reset")
    coord.last_learning_reset = last_reset if isinstance(last_reset, dict) else None


def _drying(raw: Any) -> DryingState | None:
    """A stored drying state; None when it holds nothing worth keeping or cannot be read."""
    stored = as_dict(raw)
    numbers = ("started_ts", "baseline_abs_humidity", "ended_ts", "wet_since_ts", "peak_abs_humidity", "peak_ts")
    times = {key: as_float(stored.get(key)) for key in numbers}
    active = stored.get("active") is True and times["started_ts"] is not None
    # a start time without "active": waiting for the heat pump, see engines/room.evaluate_drying
    manual = times["started_ts"] is not None and stored.get("manual") is True
    state = DryingState(active=active, manual=manual, **times)
    return None if state == DryingState() else state


def to_store(coord: ThrifthermCoordinator) -> dict[str, Any]:
    return {
        "cop_map": coord.cop_map.to_storage(),
        "heat_rates": coord.heat_rates.to_storage(),
        "cool_rates": coord.cool_rates.to_storage(),
        "midea_block_until": coord.heat_pump_tracker.block_until,
        "midea_block_reason": coord.heat_pump_tracker.block_reason,  # a lockout for dear heat alone ends in "heat pump only"
        "overrides": {
            k: {"target": o.target, "until_ts": o.until_ts, "set_at_ts": o.set_at_ts, "by": o.by} for k, o in coord.overrides.items()
        },
        "away_return_ts": coord.away_return_ts,
        "away_return_provisional": coord.away_return_provisional,
        "mode": coord.mode,
        "control_mode": coord.control_mode,
        # a boiler addressed through entities still holds what the control sent (see the coordinator)
        "boiler_entities_held": coord._boiler_entities_released is False,
        "boiler_handed_back_flow": coord._boiler_handed_back_flow,
        "control_memory": coord.control_memory.to_storage(),
        "boosts": coord.boosts,
        "boiler_control_mode": coord.boiler_control_mode,
        "boiler_memory": coord.boiler_memory.to_storage(),
        "gas_split": coord.gas_split.to_storage(),
        "boiler_load_log": list(coord.load_log)[-LOAD_LOG_STORED_ROWS:],
        "room_control_mode": coord.room_control_mode,
        "room_temps": coord.room_temps,
        "room_ctrl_memory": {room: mem.to_storage() for room, mem in coord.room_ctrl_memory.items()},
        "call_memory": {room: mem.to_storage() for room, mem in coord.call_memory.items()},
        "last_cheaper": coord._last_cheaper,
        "heat_pump_watch": coord.heat_pump_watch(),
        "drying_states": {room: asdict(state) for room, state in coord.drying_states.items() if state != DryingState()},
        "preheat_latch": dict(coord._preheat_latch),
        "learning_basis": learning_reset.basis(coord.config),
        "last_learning_reset": coord.last_learning_reset,
    }
