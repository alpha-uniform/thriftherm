"""Who counts as coming home: the return from away by presence.

Away mode ends at a planned return, or when one of the people chosen in the options comes
home. Only an arrival counts, a change of a person to "home" from a known place elsewhere
("not_home" or a zone). Being home is not enough: people switch away mode on as they leave
(the idea of 2026-10-14), so whoever is still home then must not end it at once.

An unknown state is no news. "unknown" and "unavailable" keep the last known place, so
somebody away, unavailable for a while and then home has arrived. Nothing known before
(a start, a person who just appeared) is no arrival either: whether somebody came or was
there all along cannot be told.

Leaving never starts away mode, and nothing is pre-heated on the way home: the heat pump
at its least speed makes about half a degree an hour, a short way home brings nothing.

Pure logic, no Home Assistant import.
"""

from __future__ import annotations

HOME = "home"
_UNKNOWN = ("unknown", "unavailable")


def follow(last_known: str | None, state: str | None) -> tuple[str | None, bool]:
    """The person's last known place after this state, and whether the state is an arrival home."""
    if state is None or state in _UNKNOWN:
        return last_known, False
    return state, state == HOME and last_known is not None and last_known != HOME
