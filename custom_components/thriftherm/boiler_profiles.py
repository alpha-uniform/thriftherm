"""Boiler families Thriftherm can read and address over ebusd.

A profile names the ebusd messages behind each boiler value, the messages
worth polling fast, and the message that takes the heating command. Another
boiler family is one more row in `PROFILES`.

No Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .const import (
    BOILER_REPORTS_HEATING,
    BOILER_REPORTS_HEATING_AFTER,
    BOILER_REPORTS_HOT_WATER,
    CONF_BOILER_FLOW_TEMP,
    CONF_BOILER_HWC_MODE,
    CONF_BOILER_PUMP_RUNNING,
    CONF_BOILER_PUMP_STATE,
    CONF_BOILER_RETURN_TEMP,
    CONF_BOILER_STATE_NUMBER,
)


@dataclass(frozen=True)
class BoilerProfile:
    key: str
    name: str  # shown to the user, e.g. in the setup summary
    circuit: str  # ebusd circuit the boiler usually appears on
    roles: Mapping[str, tuple[str, str]]  # config key -> (ebusd message, field)
    fast_messages: tuple[str, ...]  # polled with high priority, read directly while gas burns
    setmode_message: str  # takes the flow temperature and heating block
    heating_states: frozenset[int] = frozenset()  # status codes while the burner serves the heating circuit
    hot_water_states: frozenset[int] = frozenset()  # status codes while it makes hot water
    heating_after_states: frozenset[int] = frozenset()  # overrun and lockout after a heating run

    def reported_mode(self, state_number: int | None) -> str | None:
        """What the boiler itself says it burns for, None when the code does not tell."""
        if state_number in self.hot_water_states:
            return BOILER_REPORTS_HOT_WATER
        if state_number in self.heating_states:
            return BOILER_REPORTS_HEATING
        if state_number in self.heating_after_states:
            return BOILER_REPORTS_HEATING_AFTER
        return None


PROFILES: dict[str, BoilerProfile] = {
    p.key: p
    for p in (
        BoilerProfile(
            key="vaillant_bai",
            name="Vaillant",
            circuit="bai",
            roles={
                CONF_BOILER_FLOW_TEMP: ("FlowTemp", "temp"),
                CONF_BOILER_RETURN_TEMP: ("ReturnTemp", "temp"),
                CONF_BOILER_PUMP_STATE: ("Status01", "pumpstate"),
                CONF_BOILER_PUMP_RUNNING: ("WP", "value"),  # the heating pump itself (d.10)
                CONF_BOILER_STATE_NUMBER: ("Statenumber", "value"),  # the S.xx display code
                CONF_BOILER_HWC_MODE: ("Status02", "hwcmode"),
            },
            # ebusd reads its ~80 boiler values one after another, every 5 s, so flow and
            # return arrive only every seven minutes. These drive hot-water detection and learning.
            fast_messages=("FlowTemp", "ReturnTemp", "Status01", "WP", "Statenumber"),
            setmode_message="SetMode",
            # S.1–S.4 fan start, ignition, burner on for heating; S.10–S.17 tapping, S.20–S.27
            # cylinder loading. S.5–S.8 are overrun and lockout after a heating run: no gas, but
            # the return is still warmer than the flow (measured 2026-09-24..26: 19 of 20 "hot
            # water" alarms fell into S.7/S.8). S.0 and S.30+ say nothing.
            heating_states=frozenset(range(1, 5)),
            hot_water_states=frozenset({*range(11, 15), *range(21, 25)}),
            heating_after_states=frozenset(range(5, 9)),
        ),
    )
}
DEFAULT_PROFILE = "vaillant_bai"  # what every configuration stored before profiles existed means


def profile(key: object) -> BoilerProfile:
    """The profile stored under `key`; a missing or unknown key means the Vaillant bai profile."""
    found = PROFILES.get(key) if isinstance(key, str) else None
    return found or PROFILES[DEFAULT_PROFILE]
