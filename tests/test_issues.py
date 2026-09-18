"""Repair issues must not fire on a restart's first, not-yet-populated evaluation."""

from types import SimpleNamespace

from custom_components.thriftherm import issues


class _States:
    def get(self, entity_id):
        return None


def _coordinator(available: bool):
    return SimpleNamespace(
        data={"boiler": SimpleNamespace(available=available)},
        builder=SimpleNamespace(rooms=(), params=SimpleNamespace(airflow_curve=((1, 1), (2, 2)), duct_factor=0.75)),
        has_heat_pump=False,
        has_boiler=True,
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
