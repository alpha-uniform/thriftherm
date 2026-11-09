"""German and English wording: complete, and chosen by the Home Assistant language."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.thriftherm import texts
from custom_components.thriftherm.models import reason

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "thriftherm"


def test_german_only_for_german() -> None:
    assert texts.language_for("de") == "de"
    assert texts.language_for("de-CH") == "de"
    assert texts.language_for("en") == "en"
    assert texts.language_for("fr") == "en"
    assert texts.language_for(None) == "en"


def test_every_label_and_template_exists_in_both_languages() -> None:
    assert texts.LABELS["en"].keys() == texts.LABELS["de"].keys()
    assert texts.TEMPLATES["en"].keys() == texts.TEMPLATES["de"].keys()
    assert texts.DETECTION["en"].keys() == texts.DETECTION["de"].keys()


def test_every_advisor_reason_has_a_template() -> None:
    source = (COMPONENT / "engines" / "advisor.py").read_text()
    import re

    codes = set(re.findall(r'reason\("([a-z_]+)"', source))
    assert codes and codes <= texts.TEMPLATES["en"].keys()


def test_reason_rendering_per_language() -> None:
    names = {"badezimmer": "Badezimmer"}
    items = [
        reason("room_below_target", room="badezimmer", temp_c=20.84, target_c=21.0),
        reason("cop_above_break_even", basis="prior", cop=4.8, break_even=2.891),
        reason("heat_pump_cheaper", heat_pump_eur=0.06375, central_eur=0.1058, saving_pct=40.0),
        reason("cop_unknown", gates=("airflow_curve_not_calibrated",)),
    ]
    de = texts.render_reasons(items, "de", names)
    en = texts.render_reasons(items, "en-GB", names)
    assert de[0] == "Badezimmer 20,8 °C, Soll 21,0 °C"
    assert en[0] == "Badezimmer 20.8 °C, target 21.0 °C"
    assert de[1] == "erwarteter COP (Startkurve) 4,8 > Break-even 2,9"
    assert en[2] == "heat pump heat 0.06 €/kWh vs central heating 0.11 €/kWh (+40 %)"
    assert de[3] == "COP unbekannt (Luftmenge nicht kalibriert): vorerst Heizung"


def test_a_heat_pump_that_runs_through_is_named_with_its_setpoint() -> None:
    item = [reason("heat_pump_runs_through", target_c=22.0)]
    assert texts.render_reasons(item, "de", {}) == ["Wärmepumpe läuft durch, Sollwert 22,0 °C"]
    assert texts.render_reasons(item, "en", {}) == ["heat pump runs through, setpoint 22.0 °C"]


def test_unknown_code_does_not_break() -> None:
    assert texts.render_reasons([reason("something_new")], "de", {}) == ["something new"]


def test_translation_files_match_the_catalogue() -> None:
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    de = json.loads((COMPONENT / "translations" / "de.json").read_text())
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert en == strings
    target_en = en["entity"]["sensor"]["room_target"]["state_attributes"]["reason"]["state"]
    target_de = de["entity"]["sensor"]["room_target"]["state_attributes"]["reason"]["state"]
    assert target_en["away_preheat"] == texts.LABELS["en"]["away_preheat"]
    assert target_de["away_preheat"] == texts.LABELS["de"]["away_preheat"]
    assert en.get("exceptions", {}).keys() == de.get("exceptions", {}).keys() != set()


def test_the_shortfall_option_says_warming_up_slower_lets_the_boiler_help_until_every_room_is_there() -> None:
    # 2026-10-30: the text called "warm up slower" heat pump only, then had the boiler help "until the room is there",
    # while the help ends only once every served room is at its target.
    for language, every_room, heat_pump_only in (("en", "every room", "heat pump only"), ("de", "alle Räume", "nur die")):
        catalogue = json.loads((COMPONENT / "translations" / f"{language}.json").read_text())
        slower = catalogue["options"]["step"]["midea_tuning"]["data_description"]["heat_pump_shortfall"]
        slower = slower[slower.index(catalogue["selector"]["heat_pump_shortfall"]["options"]["slower"]):]
        assert every_room in slower
        assert heat_pump_only not in slower.lower()


def test_a_sensor_past_its_limit_is_named_in_words_in_the_reason_for_an_unknown_cop():
    gates = ("outlet_temp_invalid:stale", "intake_temp_invalid:stale")
    assert texts.render_reason(reason("cop_unknown", gates=gates), "de", {}) == (
        "COP unbekannt (Ausblasfühler zu lange still, Ansaugfühler zu lange still): vorerst Heizung"
    )
    assert "outlet sensor silent too long" in texts.render_reason(reason("cop_unknown", gates=gates), "en", {})


def test_the_shortfall_option_says_the_heat_pump_alone_leaves_the_other_rooms_to_the_boiler() -> None:
    for language, words in (("en", ("least power", "do not call the boiler", "other rooms", "district heating")), ("de", ("kleinster Leistung", "rufen ihre Räume die Therme nicht", "Die Therme heizt die anderen Räume", "Fernwärme"))):
        catalogue = json.loads((COMPONENT / "translations" / f"{language}.json").read_text())
        text = catalogue["options"]["step"]["midea_tuning"]["data_description"]["heat_pump_shortfall"]
        alone = text[text.index(catalogue["selector"]["heat_pump_shortfall"]["options"]["alone"]):]
        assert all(word in alone for word in words)


@pytest.mark.parametrize(
    "gate, german",
    [
        ("not_heating:heating_idle", "heizt gerade nicht"),
        ("insufficient_samples:3/12", "noch zu wenige Messwerte"),
        ("defrost_cycle", "Abtauen vermutet"),
        ("defrost_persistent", "Vereisung vermutet"),
        ("lift", "läuft über der kleinsten Leistung"),
        ("intake_temp_invalid:unavailable", "Ansaugfühler ohne gültigen Wert"),
        ("intake_rh_invalid:stale", "Ansaugfeuchte ohne gültigen Wert"),
        ("outlet_temp_invalid:out_of_range", "Ausblasfühler ohne gültigen Wert"),
    ],
)
def test_every_gate_of_an_unknown_cop_is_named_in_words(gate, german):
    """The reason read `not heating:heating idle` in a German sentence: only the plain gates had a label."""
    assert texts.render_reason(reason("cop_unknown", gates=(gate,)), "de", {}) == f"COP unbekannt ({german}): vorerst Heizung"
    english = texts.render_reason(reason("cop_unknown", gates=(gate,)), "en", {}).partition("(")[2].partition(")")[0]
    assert english not in ("", gate.partition(":")[0].replace("_", " ")) and ":" not in english and "_" not in english


def test_a_cop_from_the_shipped_profile_is_not_called_a_starting_curve():
    """The attribute said "measured on the same model" while the decision reason said "starting curve"."""
    item = reason("cop_above_break_even", basis="profile", cop=4.8, break_even=2.891)
    assert texts.render_reason(item, "de", {}) == "erwarteter COP (gemessen am gleichen Modell) 4,8 > Break-even 2,9"
    assert texts.render_reason(item, "en", {}).startswith("expected COP (measured on the same model) 4.8")
    calibrated = reason("cop_above_break_even", basis="prior_calibrated", cop=4.8, break_even=2.891)
    assert texts.render_reason(calibrated, "de", {}).startswith("erwarteter COP (Startkurve) ")


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_the_state_keys_are_what_hassfest_allows(name):
    """A gate code like outlet_temp_invalid:stale has a label, but it is no attribute state: hassfest fails on the colon
    in the public repo only, as it did over the manifest on 2026-10-26."""
    entities = json.loads((COMPONENT / name).read_text(encoding="utf-8"))["entity"]
    keys = set()
    for platform in entities.values():
        for entity in platform.values():
            keys |= set(entity.get("state", {}))
            for attribute in entity.get("state_attributes", {}).values():
                keys |= set(attribute.get("state", {}))
    allowed = re.compile(r"^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$")
    assert sorted(k for k in keys if not allowed.match(k)) == []


def test_a_cop_from_the_old_table_is_not_called_the_starting_curve():
    item = reason("cop_above_break_even", basis="legacy", cop=3.8, break_even=2.7)
    assert "erwarteter COP (alte Tabelle) 3,8" in texts.render_reason(item, "de", {})
    assert "expected COP (old table) 3.8" in texts.render_reason(item, "en", {})


# ---------------------------------------------------------------- the decision reason as a state (I3, 2026-11-08)
def test_the_cop_is_shown_to_a_tenth_and_the_prices_to_the_cent():
    """1941 changes in 7 days, 1297 texts: the COP and €/kWh moved in their second and third decimal."""
    cop = reason("cop_above_break_even", basis="measured", cop=3.534, break_even=2.731)
    assert texts.render_reason(cop, "de", {}) == "COP 3,5 > Break-even 2,7"
    eur = reason("heat_pump_cheaper", heat_pump_eur=0.0874, central_eur=0.1219, saving_pct=28.3)
    assert texts.render_reason(eur, "de", {}) == "Wärme aus der Wärmepumpe 0,09 €/kWh statt Heizung 0,12 €/kWh (+28 %)"


def test_the_state_never_exceeds_255_characters_and_ends_at_a_whole_reason():
    parts = [f"Raum {n}: 19,5 °C, Soll 21,0 °C" for n in range(20)]
    state = texts.fit_state(parts)
    assert len(state) <= 255 and state.endswith(" | …")
    assert state.removesuffix(" | …").split(" | ") == parts[: len(state.removesuffix(" | …").split(" | "))]
    assert texts.fit_state(parts[:2]) == " | ".join(parts[:2])
    assert len(texts.fit_state(["x" * 400])) == 255
    assert texts.fit_state([]) == "no data"


def _advice(cop: float, temp: float, *codes: str):
    from custom_components.thriftherm.models import Advice

    items = [reason("cop_above_break_even", basis="measured", cop=cop, break_even=2.73),
             reason("room_below_target", room="bad", temp_c=temp, target_c=20.0)]
    return Advice(source="midea", reasons=tuple(items + [reason(code) for code in codes]))


def test_the_decision_reason_holds_its_text_while_its_meaning_stays():
    from custom_components.thriftherm.plan_log import REASON_REFRESH_S, PlanLog

    log = PlanLog()
    names = {"bad": "Bad"}
    first = log.reasons(_advice(3.53, 19.2), "de", names, 0.0)
    assert first[0] == "COP 3,5 > Break-even 2,7 | Bad 19,2 °C, Soll 20,0 °C"
    assert log.reasons(_advice(3.71, 19.4), "de", names, 60.0) == first  # only the numbers moved
    changed = log.reasons(_advice(3.71, 19.4, "mode_away"), "de", names, 120.0)
    assert changed != first and changed[0].endswith("Abwesend: abgesenkte Sollwerte")  # what it says changed: at once
    assert log.reasons(_advice(3.9, 19.8, "mode_away"), "de", names, 120.0 + REASON_REFRESH_S - 1) == changed
    later = log.reasons(_advice(3.9, 19.8, "mode_away"), "de", names, 120.0 + REASON_REFRESH_S)
    assert later[0].startswith("COP 3,9 > ")  # the numbers catch up now and then
    assert later[1][0] == "COP 3,9 > Break-even 2,7"  # the attribute holds the same texts, in full


def test_a_steady_day_changes_the_decision_reason_far_less_than_fifty_times():
    import random

    from custom_components.thriftherm.plan_log import PlanLog

    log, rng, seen = PlanLog(), random.Random(7), []
    for minute in range(24 * 60):
        state = log.reasons(_advice(3.5 + rng.uniform(-0.3, 0.3), 19.0 + rng.uniform(-0.5, 0.5)), "de", {}, minute * 60.0)
        if not seen or seen[-1] != state:
            seen.append(state)
    assert len(seen) <= 25


def test_a_decided_setpoint_or_target_changes_the_decision_reason_at_once():
    """Second review 2026-11-08 (#R7, #R17): a new setpoint of the heat pump, or a room target set up by hand, kept the
    old number in the decision reason for up to an hour, against the command and the room target next to it."""
    from custom_components.thriftherm.models import Advice
    from custom_components.thriftherm.plan_log import PlanLog

    log = PlanLog()
    names = {"bad": "Bad"}

    def runs_through(setpoint: float, target: float, temp: float, cop: float) -> Advice:
        items = (
            reason("heat_pump_runs_through", target_c=setpoint),
            reason("room_below_target", room="bad", temp_c=temp, target_c=target),
            reason("cop_above_break_even", basis="measured", cop=cop, break_even=2.73),
        )
        return Advice(source="midea", reasons=items)

    first = log.reasons(runs_through(20.0, 20.0, 19.0, 3.5), "de", names, 0.0)
    assert first[0].startswith("Wärmepumpe läuft durch, Sollwert 20,0 °C")
    relatched = log.reasons(runs_through(21.0, 20.0, 19.0, 3.5), "de", names, 900.0)
    assert relatched[0].startswith("Wärmepumpe läuft durch, Sollwert 21,0 °C")
    raised = log.reasons(runs_through(21.0, 23.0, 19.1, 3.6), "de", names, 1000.0)
    assert "Bad 19,1 °C, Soll 23,0 °C" in raised[0]
    # what is measured still waits for the hour
    assert log.reasons(runs_through(21.0, 23.0, 19.4, 3.8), "de", names, 1100.0) == raised


@pytest.mark.parametrize(
    ("code", "cop", "break_even", "expected"),
    [
        ("cop_above_break_even", 3.04, 2.96, "COP 3,04 > Break-even 2,96"),
        ("cop_below_break_even", 2.96, 3.04, "COP 2,96 < Break-even 3,04: Heizung günstiger"),
        ("cop_above_break_even", 3.004, 2.996, "COP 3,004 > Break-even 2,996"),
        ("cop_above_break_even", 3.13, 2.96, "COP 3,1 > Break-even 3,0"),  # a tenth apart and more: as before
    ],
)
def test_a_cop_close_to_its_break_even_is_shown_with_as_many_decimals_as_tell_them_apart(code, cop, break_even, expected):
    """Second review 2026-11-08 (#R18): "erwarteter COP (gelernt) 3,0 > Break-even 3,0" contradicted itself near break-even."""
    assert texts.render_reason(reason(code, basis="measured", cop=cop, break_even=break_even), "de", {}) == expected
