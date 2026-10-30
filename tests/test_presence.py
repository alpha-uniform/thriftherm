"""Who counts as coming home: only a change to "home" from a known place elsewhere."""

from __future__ import annotations

import pytest

from custom_components.thriftherm.engines import presence


@pytest.mark.parametrize("before", ["not_home", "Arbeit"])
def test_a_person_from_elsewhere_who_is_home_now_has_arrived(before: str) -> None:
    assert presence.follow(before, "home") == ("home", True)


def test_a_person_who_was_home_already_has_not_arrived() -> None:
    assert presence.follow("home", "home") == ("home", False)


def test_leaving_is_no_arrival_and_is_remembered() -> None:
    assert presence.follow("home", "not_home") == ("not_home", False)


@pytest.mark.parametrize("gone", ["unknown", "unavailable", None])
def test_an_unknown_state_changes_nothing(gone: str | None) -> None:
    assert presence.follow("not_home", gone) == ("not_home", False)
    assert presence.follow("home", gone) == ("home", False)


def test_the_last_known_place_counts_across_an_unknown_state() -> None:
    known, arrived = presence.follow("not_home", "unavailable")
    assert presence.follow(known, "home") == ("home", True)


def test_home_with_nothing_known_before_is_no_arrival() -> None:
    """At a start, and after a state that was never known: whether somebody came, or was there all along, is not to tell."""
    assert presence.follow(None, "home") == ("home", False)
