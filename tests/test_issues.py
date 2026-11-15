"""Repair issues must not fire on a restart's first, not-yet-populated evaluation."""

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.thriftherm import issues
from custom_components.thriftherm.const import DOMAIN


class _States:
    def get(self, entity_id):
        return None


def _coordinator(available: bool):
    return SimpleNamespace(
        data={"boiler": SimpleNamespace(available=available)},
        builder=SimpleNamespace(rooms=(), params=SimpleNamespace(airflow_curve=((1, 1), (2, 2)), duct_factor=0.75)),
        has_heat_pump=False,
        has_boiler=True,
        issue_since={},
        config={},
    )


def test_boiler_data_gap_after_restart_is_not_a_repair(monkeypatch):
    hass = SimpleNamespace(states=_States())
    coord = _coordinator(available=False)
    clock = [1_000_000.0]
    monkeypatch.setattr(issues.time, "time", lambda: clock[0])

    assert "boiler_data_unavailable" not in issues.detect(hass, coord)  # just restarted
    clock[0] += issues.BOILER_UNAVAILABLE_GRACE_S - 1
    assert "boiler_data_unavailable" not in issues.detect(hass, coord)
    clock[0] += 2
    assert "boiler_data_unavailable" in issues.detect(hass, coord)  # a real, lasting gap

    coord.data["boiler"].available = True
    assert "boiler_data_unavailable" not in issues.detect(hass, coord)
    coord.data["boiler"].available = False
    assert "boiler_data_unavailable" not in issues.detect(hass, coord)  # the grace starts over


def test_the_result_of_the_cycle_that_just_ran_is_what_counts(monkeypatch):
    """The coordinator publishes its result only after the repairs are synced."""
    hass = SimpleNamespace(states=_States())
    coord = _coordinator(available=True)  # what the last cycle saw
    clock = [1_000_000.0]
    monkeypatch.setattr(issues.time, "time", lambda: clock[0])
    fresh = {"boiler": SimpleNamespace(available=False)}
    pending: set[str] = set()
    assert issues.detect(hass, coord, fresh, pending) == {} and pending == {"boiler_data_unavailable"}  # undecided
    clock[0] += issues.BOILER_UNAVAILABLE_GRACE_S + 1
    pending.clear()
    assert "boiler_data_unavailable" in issues.detect(hass, coord, fresh, pending) and not pending


def _heat_pump_rooms(installed_in: str | None, duct_factor: float):
    rooms = (
        SimpleNamespace(key="wohnzimmer", name="Wohnzimmer", served_by_heat_pump=True, schedule_entity=None,
                        schedule_weekday="06:00-22:00", schedule_weekend="08:00-23:00"),
    )
    coord = _coordinator(available=True)
    coord.builder = SimpleNamespace(rooms=rooms, params=SimpleNamespace(airflow_curve=((1, 1), (2, 2)), duct_factor=duct_factor))
    coord.has_heat_pump = True
    coord.config = {"midea_room": installed_in}
    return coord


def test_no_repair_about_ducts_before_the_units_room_is_known():
    """The setup asks for the heat pump before any room exists: a fresh installation has no room for it yet."""
    hass = SimpleNamespace(states=_States())
    assert "heat_stays_in_the_room" not in issues.detect(hass, _heat_pump_rooms(None, 1.0))
    assert "heat_stays_in_the_room" in issues.detect(hass, _heat_pump_rooms("badezimmer", 1.0))  # stands elsewhere, no ducts
    assert "heat_stays_in_the_room" not in issues.detect(hass, _heat_pump_rooms("wohnzimmer", 1.0))
    assert "heat_stays_in_the_room" not in issues.detect(hass, _heat_pump_rooms("badezimmer", 0.9))  # ducts fitted


def test_a_curve_of_one_point_is_no_calibration():
    hass = SimpleNamespace(states=_States())
    coord = _heat_pump_rooms("wohnzimmer", 0.9)
    coord.builder.params.airflow_curve = ((600.0, 450.0),)
    assert "airflow_not_calibrated" in issues.detect(hass, coord)


async def test_an_ignored_repair_survives_the_first_cycle_after_a_restart(hass: HomeAssistant, monkeypatch) -> None:
    clock = [1_000_000.0]
    monkeypatch.setattr(issues.time, "time", lambda: clock[0])
    issue_id = "thriftherm_boiler_data_unavailable"
    registry = ir.async_get(hass)
    coord = _coordinator(available=False)
    await issues.async_sync(hass, coord)
    assert registry.async_get_issue(DOMAIN, issue_id) is None  # inside the grace time
    clock[0] += issues.BOILER_UNAVAILABLE_GRACE_S + 1
    await issues.async_sync(hass, coord)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version is not None

    restarted = _coordinator(available=False)  # its clock for the condition starts over
    await issues.async_sync(hass, restarted)
    assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version is not None  # undecided: left as it is

    restarted.data["boiler"].available = True
    await issues.async_sync(hass, restarted)
    assert registry.async_get_issue(DOMAIN, issue_id) is None  # decided: the boiler is back



def test_frost_in_the_cooling_season_is_a_repair():
    """The heat pump never heats in the cooling season (docs/PHASE12_COOLING.md §8): frost there needs a person."""
    hass = SimpleNamespace(states=_States())
    coord = _heat_pump_rooms("wohnzimmer", 0.9)
    coord.cooling_season = True
    coord.data = {"boiler": SimpleNamespace(available=True), "safety": SimpleNamespace(issues=(), frost_rooms=("wohnzimmer",))}
    found = issues.detect(hass, coord)
    assert found["frost_in_cooling_season"] == {"translation_key": "frost_in_cooling_season", "rooms": "Wohnzimmer"}
    coord.cooling_season = False
    assert "frost_in_cooling_season" not in issues.detect(hass, coord)


def test_a_missing_cooling_schedule_is_a_repair(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(issues.time, "time", lambda: clock[0])
    hass = SimpleNamespace(states=_States())
    coord = _heat_pump_rooms("wohnzimmer", 0.9)
    coord.builder.rooms[0].cooling_schedule_entity = "schedule.kuehlplan"
    coord.cooling_allowed = False
    issues.detect(hass, coord)
    clock[0] += issues.SCHEDULE_MISSING_GRACE_S + 1
    assert "cooling_schedule_missing_wohnzimmer" not in issues.detect(hass, coord)  # cooling not allowed: nothing to miss
    coord.cooling_allowed = True
    issues.detect(hass, coord)
    clock[0] += issues.SCHEDULE_MISSING_GRACE_S + 1
    found = issues.detect(hass, coord)
    assert found["cooling_schedule_missing_wohnzimmer"]["entity_id"] == "schedule.kuehlplan"


def test_two_rooms_whose_entities_collide_are_a_repair():
    """Review 2026-11-13 (K13): the cooling comfort of "Bad" and the heating comfort of "Bad cool" get one unique id,
    and Home Assistant drops one of the two entities. The form refuses such a name since; rooms that have it already
    are told, where the other room exists. A room named so on its own collides with nothing and is left alone."""
    hass = SimpleNamespace(states=_States())
    coord = _coordinator(available=True)

    def room(key: str, name: str):
        plan = {"schedule_entity": None, "schedule_weekday": "06:00-22:00", "schedule_weekend": ""}
        return SimpleNamespace(key=key, name=name, cooling_schedule_entity=None, served_by_heat_pump=key == "bad", **plan)

    coord.builder.rooms = (
        room("bad", "Bad"), room("bad_cool", "Bad cool"), room("keller_cool", "Keller cool"),
        room("flur", "Flur"), room("flur_cool", "Flur cool"),
    )
    # Only where it really happens: the other room has cooling numbers once cooling is allowed and the heat pump
    # serves it. Told without that, the repair asked to delete a room, and what it had learned, for nothing.
    coord.cooling_allowed = False
    assert not [key for key in issues.detect(hass, coord) if key.startswith("room_names_collide")]
    coord.cooling_allowed = True
    found = issues.detect(hass, coord)
    assert found["room_names_collide_bad_cool"] == {"translation_key": "room_names_collide", "room": "Bad cool", "other": "Bad"}
    assert "room_names_collide_keller_cool" not in found and "room_names_collide_flur_cool" not in found
