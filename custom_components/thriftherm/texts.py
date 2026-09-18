"""User-facing wording in German and English.

Home Assistant translates entity names, states and dialogs from the JSON files
in `translations/`. Two things it cannot translate on its own are composed
here: the decision reason, which is built from live values, and the labels for
codes that appear in attributes (the JSON files are generated from this module
by `scripts/build_translations.py` and kept in sync by a test).

Rule: a German Home Assistant gets German, every other language English.
No Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import Reason

LANGUAGES = ("en", "de")


def language_for(ha_language: str | None) -> str:
    return "de" if (ha_language or "").lower().startswith("de") else "en"


# ---------------------------------------------------------------- code labels
# Every code the engines put into an attribute or a reason, with its wording.
LABELS: dict[str, dict[str, str]] = {
    "en": {
        # control modes
        "off": "Off", "shadow": "Plan only", "active": "Active",
        # where a target temperature comes from
        "override": "Manual override", "mode_off_frost_protection": "Summer mode (frost protection only)",
        "mode_away": "Away", "away_preheat": "Pre-heating for your return", "away_humidity_guard": "Away, raised against damp",
        "schedule_comfort": "Schedule: comfort", "schedule_setback": "Schedule: setback", "schedule_preheat": "Pre-heating for the schedule",
        "bathroom_drying": "Drying the bathroom",
        # drying mode
        "drying_active": "Drying", "drying_started_humidity_load": "Started: humidity after a shower",
        "drying_ended_humidity_normal": "Ended: humidity back to normal", "drying_ended_timeout": "Ended: time limit",
        "drying_ended_window_ineffective": "Ended: window open without effect", "drying_ended_midea_unavailable": "Ended: heat pump not available",
        # boiler
        "room_demand": "Rooms need heat", "no_room_needs_boiler": "No room needs the boiler", "frost_protection": "Frost protection",
        "summer_mode": "Summer mode", "safety_fallback": "Safety fallback", "boiler_data_unavailable": "Boiler data unavailable", "waiting_for_room_data": "Waiting for room data",
        "hands_off": "No setpoint sent", "control_off": "Control off", "not_configured": "Not configured",
        "min_state_time": "Minimum time in state", "ramp_limit": "Rising slowly (ramp limit)",
        "spread_small": "Flow/return spread small", "spread_large": "Flow/return spread large",
        "short_cycling": "Boiler kept switching on and off",
        "rooms_slow": "Rooms heating up slowly", "rooms_satisfied": "Rooms warm without demand",
        "learning": "Learning", "refining": "Refining", "paused": "Paused",
        # heat pump
        "cheaper_than_gas": "Cheaper than central heating", "gas_cheaper": "Central heating cheaper", "learning_run": "Learning run",
        "quick_heat_up": "Quick heat-up", "mode_midea_only": "Mode: heat pump only", "mode_boiler_only": "Mode: boiler only",
        "manual_takeover": "Changed by hand", "midea_unavailable": "Heat pump unavailable", "target_reached": "Target reached",
        "no_demand": "No demand", "blocked": "Blocked", "intake_temperature_unknown": "Intake temperature unknown",
        "outdoor_temperature_unknown": "Outdoor temperature unknown", "outdoor_too_cold": "Too cold outside",
        "min_run_time": "Minimum run time", "min_off_time": "Minimum off time", "rate_limit": "Waiting between commands",
        "none": "None", "start": "Start", "stop": "Stop", "adjust": "Adjust",
        "unavailable": "Unavailable", "cooling_user": "Cooling (user)", "fan_only": "Fan only", "auto": "Auto",
        "heating_idle": "Heating idle", "heating_warming_up": "Heating warming up", "heating_stable": "Heating",
        # COP basis and power source
        "measured": "Measured", "learned": "Learned", "prior": "Starting curve", "prior_calibrated": "Starting curve (calibrated)",
        "plug": "Smart plug", "midea_internal": "Heat pump's own value", "nominal_circulation_estimate": "Estimate from nominal circulation (±30 %)",
        # icing
        "persistent": "Icing", "cycle": "Defrost cycle", "frequent_defrost_cycles": "Frequent defrost cycles",
        "icing_lockout_active": "Icing lockout active", "outdoor_coil_iced": "Outdoor coil iced", "sustained_inefficiency": "Heat output collapsed",
        # thermostats
        "in_sync": "In sync", "waiting_for_thermostat": "Waiting for the thermostat", "no_thermostat": "No thermostat",
        "thermostat_off": "Thermostat off", "thermostat_unavailable": "Thermostat unavailable",
        # COP gates
        "airflow_curve_not_calibrated": "airflow not calibrated", "warming_up": "warming up", "electrical_power_missing": "no power reading",
        "electrical_power_below_minimum": "power too low", "delta_t_below_minimum": "temperature rise too small",
        "outlet_temp_not_settled": "outlet not settled", "fan_rpm_outside_curve": "fan speed outside the curve",
        "raw_cop_out_of_range": "COP implausible", "exceeds_carnot_limit": "above the physical limit",
        "setpoint_recently_changed": "setpoint changed recently", "boiler_control_not_active": "boiler control not active",
        "hot_water_recent": "hot water recently",
    },
    "de": {
        "off": "Aus", "shadow": "Nur planen", "active": "Aktiv",
        "override": "Übersteuerung von Hand", "mode_off_frost_protection": "Sommerbetrieb (nur Frostschutz)",
        "mode_away": "Abwesend", "away_preheat": "Vorheizen zur Rückkehr", "away_humidity_guard": "Abwesend, gegen Feuchte angehoben",
        "schedule_comfort": "Zeitplan: Komfort", "schedule_setback": "Zeitplan: Absenkung", "schedule_preheat": "Vorheizen für den Zeitplan",
        "bathroom_drying": "Bad trocknen",
        "drying_active": "Trocknet", "drying_started_humidity_load": "Gestartet: Feuchte nach dem Duschen",
        "drying_ended_humidity_normal": "Beendet: Feuchte wieder normal", "drying_ended_timeout": "Beendet: Zeitlimit",
        "drying_ended_window_ineffective": "Beendet: Fenster offen ohne Wirkung", "drying_ended_midea_unavailable": "Beendet: Wärmepumpe nicht verfügbar",
        "room_demand": "Räume brauchen Wärme", "no_room_needs_boiler": "Kein Raum braucht die Therme", "frost_protection": "Frostschutz",
        "summer_mode": "Sommerbetrieb", "safety_fallback": "Sicherheits-Fallback", "boiler_data_unavailable": "Thermendaten fehlen", "waiting_for_room_data": "Warte auf Raumdaten",
        "hands_off": "Keine Vorgabe", "control_off": "Steuerung aus", "not_configured": "Nicht eingerichtet",
        "min_state_time": "Mindestzeit im Zustand", "ramp_limit": "Steigt langsam (Anstiegsbegrenzung)",
        "spread_small": "Spreizung klein", "spread_large": "Spreizung groß",
        "short_cycling": "Therme taktet",
        "rooms_slow": "Räume heizen langsam auf", "rooms_satisfied": "Räume warm ohne Bedarf",
        "learning": "Einlernen", "refining": "Verfeinern", "paused": "Pausiert",
        "cheaper_than_gas": "Günstiger als die Heizung", "gas_cheaper": "Heizung günstiger", "learning_run": "Lernlauf",
        "quick_heat_up": "Schnell aufheizen", "mode_midea_only": "Modus: nur Wärmepumpe", "mode_boiler_only": "Modus: nur Therme",
        "manual_takeover": "Von Hand geändert", "midea_unavailable": "Wärmepumpe nicht verfügbar", "target_reached": "Ziel erreicht",
        "no_demand": "Kein Bedarf", "blocked": "Gesperrt", "intake_temperature_unknown": "Ansaugtemperatur unbekannt",
        "outdoor_temperature_unknown": "Außentemperatur unbekannt", "outdoor_too_cold": "Draußen zu kalt",
        "min_run_time": "Mindestlaufzeit", "min_off_time": "Mindeststillstand", "rate_limit": "Wartezeit zwischen Befehlen",
        "none": "Keine", "start": "Start", "stop": "Stopp", "adjust": "Anpassen",
        "unavailable": "Nicht verfügbar", "cooling_user": "Kühlen (Nutzer)", "fan_only": "Nur Lüfter", "auto": "Auto",
        "heating_idle": "Heizen bereit", "heating_warming_up": "Heizen (Warmlauf)", "heating_stable": "Heizen",
        "measured": "Gemessen", "learned": "Gelernt", "prior": "Startkurve", "prior_calibrated": "Startkurve (kalibriert)",
        "plug": "Zwischenstecker", "midea_internal": "Eigener Wert der Wärmepumpe", "nominal_circulation_estimate": "Schätzung aus Nenn-Umlaufmenge (±30 %)",
        "persistent": "Vereisung", "cycle": "Abtauzyklus", "frequent_defrost_cycles": "Häufige Abtauzyklen",
        "icing_lockout_active": "Vereisungssperre aktiv", "outdoor_coil_iced": "Außenregister vereist", "sustained_inefficiency": "Wärmeabgabe eingebrochen",
        "in_sync": "Stimmt überein", "waiting_for_thermostat": "Wartet auf das Thermostat", "no_thermostat": "Kein Thermostat",
        "thermostat_off": "Thermostat aus", "thermostat_unavailable": "Thermostat nicht verfügbar",
        "airflow_curve_not_calibrated": "Luftmenge nicht kalibriert", "warming_up": "Warmlauf", "electrical_power_missing": "keine Leistungsmessung",
        "electrical_power_below_minimum": "Leistung zu gering", "delta_t_below_minimum": "Temperaturhub zu klein",
        "outlet_temp_not_settled": "Ausblas nicht eingeschwungen", "fan_rpm_outside_curve": "Lüfterdrehzahl außerhalb der Kennlinie",
        "raw_cop_out_of_range": "COP unplausibel", "exceeds_carnot_limit": "über der physikalischen Grenze",
        "setpoint_recently_changed": "Sollwert kürzlich geändert", "boiler_control_not_active": "Thermensteuerung nicht aktiv",
        "hot_water_recent": "Warmwasser kürzlich",
    },
}

# the code families shown for each attribute that carries a code
CONTROL_MODES = ("off", "shadow", "active")
LEARNING_PHASES = ("learning", "refining", "paused")
ACTIONS = ("none", "start", "stop", "adjust")
DEFROST_KINDS = ("none", "persistent", "cycle")
COP_BASES = ("measured", "learned", "prior", "prior_calibrated")
POWER_SOURCES = ("plug", "midea_internal")
REASON_CODES = tuple(k for k in LABELS["en"] if k not in CONTROL_MODES + ACTIONS + COP_BASES + POWER_SOURCES + ("persistent", "cycle"))


def label(code: str | None, lang: str) -> str:
    if code is None:
        return "–"
    return LABELS[lang].get(code) or code.replace("_", " ")


# ---------------------------------------------------------------- decision reasons
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "heat_pump_blocked": "heat pump blocked: {reason}",
        "safety_fallback": "safety fallback: room temperatures unavailable",
        "frost_protection": "frost protection active: {rooms}",
        "heat_pump_unavailable": "heat pump not ready to heat ({state})",
        "heat_pump_outdoor_too_cold": "heat pump blocked: outdoor {outdoor_c} °C below {limit_c} °C",
        "hot_water_priority": "boiler is making hot water (it keeps priority)",
        "mode_off": "summer mode: no space heating",
        "mode_boiler_only": "mode boiler only",
        "mode_heat_pump_only": "mode heat pump only",
        "cop_above_break_even": "{cop_label} {cop} > break-even {break_even}",
        "heat_pump_cheaper": "heat pump heat {heat_pump_eur} €/kWh vs central heating {central_eur} €/kWh{saving}",
        "cop_below_break_even": "{cop_label} {cop} < break-even {break_even}: central heating cheaper",
        "cop_unknown": "COP unknown ({gates}): central heating for now",
        "heat_pump_rooms_blocked": "heat pump rooms need heat but the heat pump is blocked",
        "room_below_target": "{room} {temp_c} °C, target {target_c} °C",
        "room_window_open": "{room}: window open, heating paused",
        "mode_away": "away: reduced targets",
        "boiler_data_unavailable": "boiler data unavailable (ebusd), boiler state unknown",
        "no_demand": "no room needs heat",
    },
    "de": {
        "heat_pump_blocked": "Wärmepumpe gesperrt: {reason}",
        "safety_fallback": "Sicherheits-Fallback: keine Raumtemperaturen",
        "frost_protection": "Frostschutz aktiv: {rooms}",
        "heat_pump_unavailable": "Wärmepumpe nicht heizbereit ({state})",
        "heat_pump_outdoor_too_cold": "Wärmepumpe gesperrt: außen {outdoor_c} °C unter {limit_c} °C",
        "hot_water_priority": "Therme bereitet Warmwasser (hat Vorrang)",
        "mode_off": "Sommerbetrieb: keine Raumheizung",
        "mode_boiler_only": "Modus nur Therme",
        "mode_heat_pump_only": "Modus nur Wärmepumpe",
        "cop_above_break_even": "{cop_label} {cop} > Break-even {break_even}",
        "heat_pump_cheaper": "Wärme aus der Wärmepumpe {heat_pump_eur} €/kWh statt Heizung {central_eur} €/kWh{saving}",
        "cop_below_break_even": "{cop_label} {cop} < Break-even {break_even}: Heizung günstiger",
        "cop_unknown": "COP unbekannt ({gates}): vorerst Heizung",
        "heat_pump_rooms_blocked": "Wärmepumpen-Räume brauchen Wärme, Wärmepumpe gesperrt",
        "room_below_target": "{room} {temp_c} °C, Soll {target_c} °C",
        "room_window_open": "{room}: Fenster offen, Heizen pausiert",
        "mode_away": "Abwesend: abgesenkte Sollwerte",
        "boiler_data_unavailable": "Thermendaten fehlen (ebusd), Zustand der Therme unbekannt",
        "no_demand": "Kein Raum braucht Wärme",
    },
}
_COP_LABEL = {
    "en": {"measured": "COP", "learned": "expected COP (learned)"},
    "de": {"measured": "COP", "learned": "erwarteter COP (gelernt)"},
}
_COP_LABEL_PRIOR = {"en": "expected COP (starting curve)", "de": "erwarteter COP (Startkurve)"}
_DIGITS = {"cop": 2, "break_even": 2, "heat_pump_eur": 3, "central_eur": 3}


def _number(value: Any, digits: int, lang: str) -> str:
    if value is None:
        return "–"
    text = f"{float(value):.{digits}f}"
    return text.replace(".", ",") if lang == "de" else text


def render_reason(item: Reason, lang: str, room_names: Mapping[str, str]) -> str:
    lang = lang if lang in LANGUAGES else "en"
    template = TEMPLATES[lang].get(item.code)
    if template is None:
        return item.code.replace("_", " ")
    values: dict[str, str] = {}
    for key, value in item.params:
        if key == "room":
            values[key] = room_names.get(value, value)
        elif key == "rooms":
            values[key] = ", ".join(room_names.get(r, r) for r in value)
        elif key == "gates":
            values[key] = ", ".join(label(g, lang) for g in value)
        elif key in ("reason", "state"):
            values[key] = label(value, lang)
        elif key == "basis":
            values["cop_label"] = _COP_LABEL[lang].get(value, _COP_LABEL_PRIOR[lang])
        elif key == "saving_pct":
            values["saving"] = "" if value is None else f" ({float(value):+.0f} %)"
        else:
            values[key] = _number(value, _DIGITS.get(key, 1), lang)
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return item.code.replace("_", " ")


def render_reasons(reasons: Iterable[Reason], ha_language: str | None, room_names: Mapping[str, str]) -> list[str]:
    lang = language_for(ha_language)
    return [render_reason(r, lang, room_names) for r in reasons]


# ---------------------------------------------------------------- setup
# The boiler step says what the search for ebusd entities found. Home Assistant
# inserts a description placeholder as it is, so the sentence is composed here.
DETECTION: dict[str, dict[str, str]] = {
    "en": {
        "found": "Found: {name} ({circuit}), {found} of {total} values — please check.",
        "none": "No ebusd boiler found — select the entities yourself.",
    },
    "de": {
        "found": "Gefunden: {name} ({circuit}), {found} von {total} Werten – bitte prüfen.",
        "none": "Keine Therme über ebusd gefunden – bitte wähle die Entitäten selbst aus.",
    },
}


def detection_summary(name: str | None, circuit: str | None, found: int, total: int, ha_language: str | None) -> str:
    """One sentence on the boiler found; `name` None means none was found."""
    wording = DETECTION[language_for(ha_language)]
    if name is None:
        return wording["none"]
    return wording["found"].format(name=name, circuit=circuit, found=found, total=total)
