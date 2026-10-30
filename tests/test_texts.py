"""German and English wording: complete, and chosen by the Home Assistant language."""

from __future__ import annotations

import json
from pathlib import Path

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
