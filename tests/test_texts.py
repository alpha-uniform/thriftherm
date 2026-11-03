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
    assert de[1] == "erwarteter COP (Startkurve) 4,80 > Break-even 2,89"
    assert en[2] == "heat pump heat 0.064 €/kWh vs central heating 0.106 €/kWh (+40 %)"
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
    assert texts.render_reason(item, "de", {}) == "erwarteter COP (gemessen am gleichen Modell) 4,80 > Break-even 2,89"
    assert texts.render_reason(item, "en", {}).startswith("expected COP (measured on the same model) 4.80")
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
    assert "erwarteter COP (alte Tabelle) 3,80" in texts.render_reason(item, "de", {})
    assert "expected COP (old table) 3.80" in texts.render_reason(item, "en", {})
