"""Plan and command history, and log lines only when something changes.

Kept apart from the coordinator: it holds its own "last seen" state so a plan
that repeats every minute is logged once, not sixty times an hour.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from . import texts
from .models import Advice, BoilerCommand, DefrostResult, HeatPumpCommand, SafetyResult

_LOGGER = logging.getLogger(__name__)

HISTORY_LENGTH = 20
STARTUP_GRACE_S = 180.0  # right after start most source entities are not loaded yet


class PlanLog:
    def __init__(self) -> None:
        self.command_log: deque[dict[str, Any]] = deque(maxlen=HISTORY_LENGTH)
        self.boiler_log: deque[dict[str, Any]] = deque(maxlen=HISTORY_LENGTH)
        self._last_plan: tuple[str, str] | None = None
        self._last_boiler_plan: tuple[str, str] | None = None
        self._last_source: str | None = None
        self._last_safety: str | None = None
        self._last_defrost_kind = "none"
        self._started_ts = time.time()

    def boiler(self, command: BoilerCommand, now_ts: float, active: bool) -> None:
        plan = (command.plan, command.payload or "")
        if plan == self._last_boiler_plan:
            return
        self.boiler_log.append({"ts": round(now_ts), "plan": command.plan, "payload": command.payload, "reason": command.reason, "sent": active and command.send})
        _LOGGER.info("thriftherm boiler %s: %s %s (%s)", "command" if active else "plan (not sent)", command.plan, command.payload, command.reason)
        self._last_boiler_plan = plan

    def heat_pump(self, command: HeatPumpCommand, now_ts: float, active: bool) -> bool:
        """Record a heat pump command. True when the history changed and should be saved."""
        plan = (command.plan, command.reason)
        recorded = False
        if command.action != "none":
            self.command_log.append(
                {"ts": round(now_ts), "action": command.action, "target": command.target_temp, "fan": command.fan_mode,
                 "load": command.load, "reason": command.reason, "sent": active}
            )
            recorded = True
            _LOGGER.info(
                "thriftherm heat pump %s: %s target=%s fan=%s load=%s (%s)",
                "command" if active else "plan (not sent)", command.action, command.target_temp, command.fan_mode, command.load, command.reason,
            )
        elif plan != self._last_plan:
            _LOGGER.debug("thriftherm heat pump plan: %s (%s) blockers=%s", command.plan, command.reason, command.blockers)
        self._last_plan = plan
        return recorded

    def changes(self, advice: Advice, safety_res: SafetyResult, defrost_res: DefrostResult) -> None:
        if advice.source != self._last_source:
            _LOGGER.info("thriftherm advice: %s -> %s | %s", self._last_source, advice.source, "; ".join(texts.render_reasons(advice.reasons, "en", {})))
            self._last_source = advice.source
        if safety_res.state != self._last_safety:
            starting = time.time() - self._started_ts < STARTUP_GRACE_S
            (_LOGGER.debug if starting else _LOGGER.warning)(
                "thriftherm safety state %s -> %s: %s", self._last_safety, safety_res.state, ", ".join(safety_res.issues)
            )
            self._last_safety = safety_res.state
        if defrost_res.kind != self._last_defrost_kind:
            level = _LOGGER.warning if defrost_res.kind == "persistent" else _LOGGER.info
            level(
                "thriftherm defrost: %s -> %s (%s) cycles/90min=%d block=%s",
                self._last_defrost_kind, defrost_res.kind, ", ".join(defrost_res.indicators) or "-", defrost_res.cycles_last_90min, defrost_res.block_reason,
            )
            self._last_defrost_kind = defrost_res.kind
