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
        ),
    )
}
DEFAULT_PROFILE = "vaillant_bai"  # what every configuration stored before profiles existed means


def profile(key: object) -> BoilerProfile:
    """The profile stored under `key`; a missing or unknown key means the Vaillant bai profile."""
    found = PROFILES.get(key) if isinstance(key, str) else None
    return found or PROFILES[DEFAULT_PROFILE]
