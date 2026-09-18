<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alpha-uniform/thriftherm/main/custom_components/thriftherm/brand/dark_logo@2x.png">
    <img alt="Thriftherm" width="450" src="https://raw.githubusercontent.com/alpha-uniform/thriftherm/main/custom_components/thriftherm/brand/logo@2x.png">
  </picture>
</p>

# Thriftherm für Home Assistant

[English](README.en.md) | **Deutsch**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz/docs/faq/custom_repositories)
![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/alphauniform)

**Bedarfsgerecht heizen mit Home Assistant.** Thriftherm heizt jeden Raum nach seinem Zeitplan und gibt deiner Gastherme eine Regelung, die ihr bisher fehlte. Mit Fernwärme geht es auch, eine Wärmepumpen-Erweiterung ist in der Vorschau.

## Was mit deiner Ausstattung geht

Im ersten Einrichtungsschritt wählst du, welche Heizung du hast; Schritte, die nicht passen, lässt der Dialog weg.

| Funktion | Eigene Therme über ebusd (Kern) | + Better Thermostat | + Wärmepumpe (Vorschau) | Nur Wärmepumpe, keine Therme (Vorschau) | Zentral- oder Fernwärme¹ |
|---|:-:|:-:|:-:|:-:|:-:|
| **Räume** | | | | | |
| Solltemperaturen aus Zeitplänen (Komfort, Absenkung, Zeitplan-Helfer) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Vorheizen, damit der Raum zu Beginn des Zeitplans warm ist | ✓ | ✓ | ✓ | ✓ | ✓ |
| Abwesenheit mit Vorheizen zur Rückkehr | ✓ | ✓ | ✓ | ✓ | ✓ |
| Vorübergehende Übersteuerung der Solltemperatur eines Raums | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fenster offen: Der Raum fordert keine Wärme mehr an | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gelernte Aufheiz- und Abkühlrate je Raum | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sollwerte an die Thermostate; Drehen von Hand wird zur vorübergehenden Übersteuerung | – | ✓ | ✓ | – | ✓ |
| Button „Schnell aufheizen“ je Raum | – | ✓ | ✓ | ✓ | ✓ |
| Frostschutz, auch im Sommerbetrieb | ✓ | ✓ | ✓ | – | ✓ |
| **Therme** | | | | | |
| Vorlauf aus Heizkurve, Wärmebedarf der Räume und Spreizung Vorlauf/Rücklauf | ✓ | ✓ | ✓ | – | – |
| Heizbetrieb gesperrt, wenn kein Raum Wärme braucht; Warmwasser unberührt | ✓ | ✓ | ✓ | – | – |
| Gelernte Korrektur der Heizkurve | ✓ | ✓ | ✓ | – | – |
| Warmwasser-Erkennung (Lernen pausiert nach dem Duschen) | ✓ | ✓ | ✓ | – | – |
| Sommerbetrieb über eBUS: Heizung aus, Warmwasser läuft weiter | ✓ | ✓ | ✓ | – | – |
| Rückfall auf den Drehknopf der Therme, wenn Home Assistant ausfällt | ✓ | ✓ | ✓ | – | – |
| **Wärmepumpe** | | | | | |
| Wärmepumpen-Steuerung: langsam mit hohem COP, volle Leistung bei „Schnell aufheizen“ | – | – | ✓ | ✓ | ✓² |
| COP aus Luftmenge und Enthalpie der Luft; Erkennung von Abtauen und Vereisung | – | – | ✓ | ✓ | ✓² |
| Kostenvergleich mit der zentralen Wärmequelle | – | – | ✓ | – | ✓² |
| Bad-Trocknung | – | – | ✓ | ✓ | ✓² |
| **Immer** | | | | | |
| Erst *Nur planen*, *Aktiv* erst nach deiner Freigabe; ein Grund für jede Entscheidung; Reparaturmeldungen | ✓ | ✓ | ✓ | ✓ | ✓ |

✓ geht · – nicht verfügbar · Vorschau = noch nicht mit einer echten Wärmepumpe getestet. Jede „+“-Spalte enthält die Spalten links davon. In der Spalte „Nur Wärmepumpe“ gelten die Raum-Zeilen für die Räume, die die Wärmepumpe erreicht.

¹ Zentralheizung, Fernwärme oder Wärme vom Vermieter: keine eigene Therme. Die Räume steuert Thriftherm über Better Thermostat, an die Wärmeversorgung wird nie etwas geschrieben.

² Mit der Wärmepumpen-Erweiterung (Vorschau). Der Kostenvergleich rechnet dann mit dem Grenzpreis der Fernwärme (siehe [unten](#erweiterung-wärmepumpe-vorschau)).

- **Eine Wärmepumpe ohne Therme funktioniert**: Im ersten Einrichtungsschritt *Keine – nur Wärmepumpe und Raumsteuerung* wählen. Wie die ganze Erweiterung ist das eine Vorschau.
- **Bad-Trocknung braucht die Wärmepumpe.** Sie trocknet mit der warmen Luft der Wärmepumpe, nur in einem Raum, den die Wärmepumpe beheizt – Einzelheiten in der [Anleitung, Abschnitt 7](ANLEITUNG.md#7-wärmepumpe-optional).
- **Alles in den Therme-Zeilen braucht eine eigene Therme über ebusd** – Vorlauf, Heizsperre, gelernte Heizkurve, Warmwasser-Erkennung, Sommerbetrieb über eBUS und der Rückfall auf den Drehknopf. Bei Fernwärme oder ohne Therme wird nie etwas über eBUS gesendet.
- **Bei Fernwärme bringt Thriftherm vor allem mit einer Wärmepumpe etwas.** Dann entscheidet es laufend, ob Wärme aus Strom oder aus der Fernwärme gerade günstiger ist, und lässt die Wärmepumpe nur dann heizen, wenn sie sich rechnet – außer bei „Schnell aufheizen“, Bad-Trocknung und Lernläufen. Ohne Wärmepumpe bleibt nur die Raumsteuerung. Die Ventile regelt dabei ohnehin Better Thermostat, Thriftherm ergänzt Zeitpläne mit Vorheizen und die Abwesenheit – viel mehr, als Better Thermostat mit einem Zeitplan schon kann, ist das nicht.

## Schnellstart

An Therme und Thermostate werden keine Befehle gesendet, bis du selbst auf *Aktiv* stellst.

1. **ebusd:** den eBUS-Adapter an die eBUS-Klemme der Therme (ein vorhandener Raumregler dort wird abgeklemmt), dann die ebusd-App mit MQTT einrichten, mit `--accesslevel=*` und der Ergänzung von `filter-name` ([Schritt 2](EINRICHTUNG.md#2-ebusd-einrichten)); ohne eigene Therme überspringen.
2. **Installieren:** in HACS das benutzerdefinierte Repository `https://github.com/alpha-uniform/thriftherm` (Kategorie *Integration*) hinzufügen, **Thriftherm** herunterladen und Home Assistant neu starten.
3. **Integration hinzufügen:** *Einstellungen → Geräte & Dienste → Integration hinzufügen → Thriftherm*. Findet Thriftherm ebusd-Entitäten, ist der Thermen-Schritt schon ausgefüllt – prüfen und weiter.
4. **Einige Tage *Nur planen*:** *Therme-Steuerbefehl*, *Thermostat-Plan* und *Entscheidungsgrund* mit dem vergleichen, was du selbst getan hättest.
5. **Auf *Aktiv* schalten:** die aktive Steuerung in den Optionen freigeben, dann *Thermensteuerung* und *Raumsteuerung* auf *Aktiv* stellen.

**Schritt für Schritt:** [Einrichtungsanleitung](EINRICHTUNG.md)

**Im Alltag** – Modi, Temperaturen, Dienste, Lernen, Reparaturmeldungen: [Anleitung](ANLEITUNG.md)

**Sprachen:** Deutsch oder Englisch, je nach Sprache von Home Assistant – mehr dazu in der [Anleitung](ANLEITUNG.md).

## Was Thriftherm tut

Thriftherm ist eine Custom Integration für Home Assistant, die Räume nach Zeitplan heizt, statt sich auf die Schätzung eines Thermostats zu verlassen. Mit eigener Gastherme wird sie zu der Regelung, die der Therme bisher fehlte, und spricht über [ebusd](https://ebusd.eu) mit ihr. Bei Fernwärme lässt sie die Wärmeversorgung in Ruhe und arbeitet über die Raumthermostate. Wo Heizkörper smarte Thermostate haben, übernimmt [Better Thermostat](https://github.com/KartoffelToby/better_thermostat) vor Ort die Ventile.

### Kern: intelligente Thermensteuerung (eBUS)

In der getesteten Installation regelte ein zentraler Raumregler an der eBUS-Klemme die Therme. Er maß einen einzigen Raum und entschied anhand dieses einen Fühlers für die ganze Wohnung. Mit dem Internet war er nicht verbunden: keine Fernbedienung, keine Daten, nichts, woraus sich lernen ließe.

Der andere häufige Fall ist eine Therme ganz ohne Regler. Sie hält ihren Heizkreis den ganzen Winter auf der Temperatur, die am Drehknopf eingestellt ist – auch wenn alle Thermostatventile zu sind –, zündet dann für ein paar Sekunden mit Mindestlast, schießt über das Ziel hinaus und taktet. Diese Integration übernimmt stattdessen die Rolle des Reglers:

- **Vorlauftemperatur so niedrig wie möglich**, abgeleitet aus drei Signalen:
  - einer Zwei-Punkt-Heizkurve über der Außentemperatur,
  - dem Wärmebedarf der Räume,
  - der **Spreizung zwischen Vorlauf und Rücklauf**, während die Therme heizt: Eine kleine Spreizung heißt, dass die Heizkörper wenig Wärme abnehmen, also wird der Vorlauf gesenkt; eine große Spreizung, während die Räume noch Wärme brauchen, hebt ihn an.
  Eine gelernte Korrektur passt sich laufend an; der Anstieg ist begrenzt.
- **Heizbetrieb gesperrt, wenn kein Raum Wärme braucht** (`disablehc` über die ebusd-Nachricht `SetMode`). Warmwasser wird nie angefasst: Sein Sollwert wird als „kein Wert“ gesendet, die Therme folgt dafür weiter ihrem Drehknopf.
- **Warmwasser erkannt**: ebusd meldet Warmwasser nicht an jeder Therme zuverlässig. Thriftherm erkennt es selbst an Gasverbrauch und Temperaturen und pausiert das Lernen für eine Stunde, damit Duschwasser der Heizkurve nichts Falsches beibringt.
- **Sommerbetrieb über eBUS**: Die Betriebsart „Sommer“ hält die Raumheizung gesperrt, Warmwasser funktioniert weiter – der Drehknopf kann aufgedreht bleiben.
- **Sicherer Rückfall**: Der Sollwert wird alle 2 Minuten neu gesendet. Fällt Home Assistant aus, regelt die Therme nach 9–16 Minuten (gemessen) wieder über ihren eigenen Drehknopf, und der Knopf bleibt immer die Obergrenze.

### Funktioniert ohne smarte Thermostate

Ein Temperaturfühler je Raum reicht. Die Thermensteuerung – Heizkurve, Bedarf, Sperren, Lernen, Frostschutz – braucht nichts weiter; die Raumsteuerung meldet dann einfach *Kein Thermostat* und schreibt nirgendwohin. Smarte Thermostate lassen sich später Raum für Raum ergänzen, gemischt geht auch.

### Räume: Better Thermostat

- Solltemperaturen je Raum aus Zeitplänen, **Vorheizen, damit die Komforttemperatur schon zu Beginn des Zeitplans erreicht ist**, Übersteuerungen, „Schnell aufheizen“, Abwesenheit mit Vorheizen zur Rückkehr, Frost- und Feuchteschutz.
- Die Sollwerte gehen an Better Thermostat, das Raumfühler und Fensterkontakte berücksichtigt. Drehst du von Hand am Thermostat, wird das zur vorübergehenden Übersteuerung, statt dagegen anzuregeln. Ein ausgeschaltetes Thermostat lässt Thriftherm in Ruhe.

### Erweiterung: Wärmepumpe (Vorschau)

> **Vorschau – folgt bald.** Noch nicht mit einer echten Wärmepumpe getestet; nicht darauf verlassen. Entwickelt wurde sie mit Blick auf eine Midea PortaSplit, lief bisher aber nur im Modus *Nur planen* und hat noch nie eine Wärmepumpe gesteuert. Die folgende Beschreibung zeigt, was sie können soll.

Ist eine Wärmepumpe eingerichtet, misst die Integration ihren COP aus Luftmenge und Enthalpie der Luft, vergleicht die Wärmekosten mit der zentralen Wärmequelle anhand echter Preise und eines gelernten COP-Kennfelds, erkennt Abtauen und Vereisung und lässt die Wärmepumpe langsam mit hohem COP laufen. Ein Bad, das sie beheizt, kann sie nach dem Duschen trocknen. Ohne Wärmepumpe fällt all das einfach weg; ohne Therme ist die Wärmepumpe die einzige Wärmequelle, und es gibt nichts zu vergleichen.

Bei Fernwärme rechnet der Vergleich mit dem **Grenzpreis** der Wärme, nicht mit dem Preis, der auf der Rechnung steht: Nach der Heizkostenverordnung folgt nur der Verbrauchsanteil der Gebäudeabrechnung deinem eigenen Zähler, und vom Flächenanteil trägt deine Wohnung ihren Miteigentumsanteil. Eine gesparte kWh spart deshalb weniger, als der Preis je kWh vermuten lässt – und das *erhöht* den COP, den die Wärmepumpe erreichen muss. Beide Anteile sind einstellbar; wer sie ignoriert, rechnet die Wärmepumpe schön.

### Nachvollziehbar und sicher

Jede Entscheidung hat ein Attribut mit ihrem Grund. Fällt ein Sensor aus, arbeitet Thriftherm eingeschränkt weiter; fehlen Daten der Therme oder steht der Sicherheitsstatus auf *Fallback*, wird *nichts gesendet*, und die Therme läuft über ihren Drehknopf.

## An echter Hardware geprüft

Getestet an einer Vaillant atmoTEC plus VCW 194/4-5 (Heizwert-Kombitherme, ebusd-Circuit `bai`, Definition `bai.308523`) mit einem eBUS-Adapter C6:

| Befund | Ergebnis |
|--------|----------|
| `SetMode` von ebusd (Adresse 31) angenommen | ja, der externe Sollwert d.09 folgt dem Wert |
| Wirksamer Vorlauf-Sollwert | `min(Drehknopf, eBUS-Sollwert)` – der Knopf ist die Obergrenze |
| Heizsperre `disablehc=1` | Raumheizung gesperrt, **Warmwasser unberührt** |
| Warmwasser-Sollwert als `-` (kein Wert) gesendet | angenommen; der Heizungs-Sollwert gilt weiter, und Warmwasser folgt weiter dem Drehknopf |
| Rückfall ohne neues `SetMode` | nach 9–16 min wieder der Drehknopf |
| Drehknopf am linken Anschlag | Sommerbetrieb (d.23 aus), sperrt die Heizung unabhängig von eBUS |
| Ohne Regler, Ventile zu | Brennerläufe von 30 s, 14 K Überschwingen, dann Taktsperre – genau die Verschwendung, die diese Integration beseitigt |
| Pumpenmodus „Nachlauf“ (d.18 = 0, Werkseinstellung) | Pumpe läuft nur mit dem Brenner; das Wasser kreist über den Bypass, und **der letzte Heizkörper bleibt kalt** |
| Pumpenmodus „durchlaufend“ (d.18 = 1) | Pumpe läuft, solange der Heizbetrieb freigegeben ist, und **steht bei `disablehc=1`**; etwa 33 W; der kalte Heizkörper war innerhalb einer Stunde warm |
| Mindest-Vorlauf an dieser Heizwerttherme | mit 30 °C blieb der letzte Heizkörper kalt, 45 °C funktioniert |
| ebusd `Status01` „Pumpenstatus“ | folgt dem Heizbedarf, nicht der Pumpe; die Pumpe selbst ist `WP`, der Display-Code `Statenumber` |

## Verwendete Hardware

Damit lief die getestete Installation. Andere Geräte gehen genauso, solange sie in Home Assistant auftauchen.

| Aufgabe | Gerät |
|---|---|
| Therme | Vaillant atmoTEC plus VCW 194/4-5 (Heizwertgerät) |
| eBUS-Adapter | [eBUS Adapter Shield C6](https://adapter.ebusd.eu/v5-c6/index.en.html), Stick-Ausführung, von ebusd.eu – erhältlich im [Elecrow-Shop](https://www.elecrow.com/store/ebusd) |
| Gaszähler auslesen | [WiFi ACM-ESP](https://www.seegel-systeme.de/produkt/wlan-acm-esp-kommunikationsmodul-fuer-elster-gaszaehler/) von Seegel Systeme, für Elster/Honeywell BK-G4A(T) |
| Heizkörperthermostate | [SONOFF TRV Gen2 (TRV-ZBT)](https://sonoff.tech/en-us/products/sonoff-trv-gen2-zigbee-thermostatic-radiator-valve-trv-zbt), Zigbee, mit Better Thermostat |

## Unterstützte Thermen

| Therme | ebusd-Circuit / Definition | Status | Daten von |
|--------|----------------------------|--------|-----------|
| Vaillant atmoTEC plus VCW 194/4-5 | `bai` / `bai.308523` | geprüft, Schreibtest bestanden | Entwickler |

Jede Therme kommt als Profil dazu: welche ebusd-Nachrichten ihre Werte liefern und welche Nachricht den Sollwert annimmt. Ich kann nur die Therme bei mir zu Hause testen. Jede weitere Therme kommt über Daten dazu, die ihre Besitzer teilen – **deine Therme kann die nächste sein**: Öffne ein [Issue mit Thermendaten](https://github.com/alpha-uniform/thriftherm/issues/new?template=boiler_data.yml) und nenne Modell, ebusd-Definition und was dir aufgefallen ist. Wer Daten beisteuert, wird in der Tabelle oben genannt.

## Voraussetzungen

- Home Assistant 2026.9 oder neuer
- Mit eigener Therme: eBUS-Adapter und die ebusd-App mit MQTT
- Ein Temperaturfühler je Raum; Better Thermostat, wenn Thriftherm die Heizkörperthermostate stellen soll
- Optional, nur Vorschau: eine Wärmepumpe mit ein paar Sensoren

Die vollständige Liste steht in der [Einrichtungsanleitung, Schritt 1](EINRICHTUNG.md#1-was-du-brauchst).

## Status und Verantwortung

**Das Schreiben an eine Gastherme geschieht auf deine Verantwortung.** Genutzt wird nur die Nachricht `SetMode`; Installateur-Parameter werden nie geschrieben. Den Pumpenmodus zu ändern ist eine einmalige Entscheidung, die du selbst triffst.

**Status: Beta.** Alle Steuerungen starten in *Nur planen*: Sie berechnen und zeigen an, was sie tun würden, und schreiben nichts, bis du die aktive Steuerung freigibst. Sperren, Vorlaufregelung und die Sicherheits-Fallbacks sind an der Therme des Entwicklers geprüft. Die gelernten Korrekturen kennen bisher nur mildes Wetter. Die Wärmepumpen-Erweiterung ist eine Vorschau – folgt bald, noch nicht mit einer echten Wärmepumpe getestet; nicht darauf verlassen.

## Entwicklung

```bash
python3 -m venv .venv
./.venv/bin/pip install pytest pytest-asyncio pytest-homeassistant-custom-component
./.venv/bin/pytest -q
```

Die Engines unter `custom_components/thriftherm/engines/` sind reines Python ohne Home-Assistant-Importe und durch Unit-Tests abgedeckt (etwa 90 % Zeilen- und Zweigabdeckung).

## Sponsoren

Thriftherm entsteht in meiner Freizeit. Wenn es dir Energie oder Geld spart, kannst du mir [einen Kaffee spendieren](https://buymeacoffee.com/alphauniform) – so bekomme ich ein Stück der Zeit zurück, die in neue Funktionen und neue Thermen fließt.

Die **Heating Heroes** stehen hier:

*Noch ist der Platz frei.*

## Lizenz

MIT
