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

**Bedarfsgerecht heizen mit Home Assistant.** Thriftherm heizt jeden Raum nach seinem Zeitplan und gibt deiner Gastherme eine Regelung, die ihr bisher fehlte. Mit Fernwärme geht es auch, und eine Wärmepumpe kann dazukommen.

## Was Thriftherm bei dir tut

**Eigene Gastherme über ebusd: der Kern.** Thriftherm wird zum Regler deiner Therme und spricht über [ebusd](https://ebusd.eu) mit ihr. Es hält den Vorlauf so niedrig wie möglich, sperrt den Heizbetrieb, wenn kein Raum Wärme braucht, und lernt für jeden Raum, wie viel Vorlauf er braucht.

**Dazu eine Wärmepumpe.** Thriftherm misst ihren COP und lässt sie heizen, wenn ihre Wärme gerade günstiger ist als die der Therme. Ein Bad, das sie beheizt, trocknet sie nach dem Duschen.

**Fernwärme, Zentralheizung oder keine eigene Therme.** Thriftherm heizt die Räume über [Better Thermostat](https://github.com/KartoffelToby/better_thermostat) nach Zeitplan, mit Vorheizen und Abwesenheit; an die Wärmeversorgung geht nie etwas. Richtig lohnt sich das mit einer Wärmepumpe: Dann vergleicht Thriftherm den Strom mit dem Grenzpreis der Fernwärme. Ohne Therme kann die Wärmepumpe auch allein heizen.

Ein Temperaturfühler je Raum reicht, smarte Thermostate lassen sich später ergänzen. Was genau mit welcher Ausstattung geht, zeigt die [Übersicht in der Einrichtungsanleitung](EINRICHTUNG.md#was-mit-deiner-ausstattung-geht).

## Was sich an der Therme ändert

Viele Thermen laufen ohne echten Regler. Dann hält die Therme ihren Heizkreis den ganzen Winter auf der Temperatur ihres Drehknopfs, auch wenn alle Thermostatventile zu sind. Sie zündet für ein paar Sekunden mit Mindestlast, schießt über das Ziel hinaus und taktet. In der getesteten Installation hing ein zentraler Raumregler am eBUS: Er maß einen einzigen Raum, entschied damit für die ganze Wohnung und war nicht mit dem Internet verbunden. Thriftherm übernimmt die Rolle des Reglers:

- **Vorlauf so niedrig wie möglich:** aus Heizkurve, Wärmebedarf der Räume und der Spreizung zwischen Vorlauf und Rücklauf. Der Raum, der am meisten braucht, bestimmt den Vorlauf.
- **Heizbetrieb gesperrt, wenn kein Raum Wärme braucht.** Warmwasser bleibt unberührt und folgt weiter dem Drehknopf.
- **Aufheiztempo nach Wunsch** in Grad pro Stunde. Mehr Tempo kostet mehr Gas.
- **Warmwasser erkannt:** Nach dem Duschen pausiert das Lernen eine Stunde, damit Duschwasser die Heizkurve nicht verstellt.
- **Sommerbetrieb über eBUS:** Die Heizung ist aus, Warmwasser läuft weiter, und der Drehknopf kann aufgedreht bleiben.
- **Sicherer Rückfall:** Fällt Home Assistant aus, regelt die Therme nach 9 bis 16 Minuten wieder nach ihrem Drehknopf. Der Knopf bleibt immer die Obergrenze.

Jede Entscheidung hat einen Grund, den du in Home Assistant nachlesen kannst. Fehlen Daten der Therme, sendet Thriftherm nichts, und die Therme läuft über ihren Drehknopf.

## Schnellstart

An Therme und Thermostate werden keine Befehle gesendet, bis du selbst auf *Aktiv* stellst.

1. **ebusd:** den eBUS-Adapter an die eBUS-Klemme der Therme (ein vorhandener Raumregler dort wird abgeklemmt), dann die ebusd-App mit MQTT einrichten, mit `--accesslevel=*` und der Ergänzung von `filter-name` ([Schritt 2](EINRICHTUNG.md#2-ebusd-einrichten)); ohne eigene Therme überspringen.
2. **Installieren:** in HACS das benutzerdefinierte Repository `https://github.com/alpha-uniform/thriftherm` (Kategorie *Integration*) hinzufügen, **Thriftherm** herunterladen und Home Assistant neu starten. Schneller geht es mit diesem Knopf, er öffnet das Repository direkt in HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alpha-uniform&repository=thriftherm&category=integration)
3. **Integration hinzufügen:** *Einstellungen → Geräte & Dienste → Integration hinzufügen → Thriftherm*. Findet Thriftherm ebusd-Entitäten, ist der Thermen-Schritt schon ausgefüllt – prüfen und weiter.
4. **Einige Tage *Nur planen*:** *Therme-Steuerbefehl*, *Thermostat-Plan* und *Entscheidungsgrund* mit dem vergleichen, was du selbst getan hättest.
5. **Auf *Aktiv* schalten:** die aktive Steuerung in den Optionen freigeben, dann *Thermensteuerung* und *Raumsteuerung* auf *Aktiv* stellen.

## Dokumentation

- [Einrichtung](EINRICHTUNG.md): Schritt für Schritt von null bis *Aktiv*
- [Anleitung](ANLEITUNG.md): der Alltag mit Modi, Temperaturen, Diensten, Lernen und Reparaturmeldungen
- [Funktionsweise](FUNKTIONSWEISE.md): wie Thriftherm entscheidet, mit Schwellen, Lernschritten und Regeln
- [Fehlersuche](FEHLERSUCHE.md): wenn etwas nicht klappt, geordnet nach dem, was du siehst
- [Weniger ebusd-Werte abfragen](EBUSD.md): optional, für frischere Werte von Vorlauf und Rücklauf
- [Änderungen](CHANGELOG.md): was jede Version bringt (auf Englisch)

**Sprachen:** Thriftherm spricht Deutsch oder Englisch, je nach Sprache von Home Assistant. Mehr dazu in der [Anleitung](ANLEITUNG.md).

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
| Ohne Regler, Ventile zu | Brennerläufe von 30 s, 14 Grad Überschwingen, dann Taktsperre – genau die Verschwendung, die diese Integration beseitigt |
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

Jede Therme kommt als Profil dazu: welche ebusd-Nachrichten ihre Werte liefern und welche Nachricht den Sollwert annimmt. Ich kann nur die Therme bei mir zu Hause testen. Jede weitere Therme kommt über Daten dazu, die ihre Besitzer teilen – **deine Therme kann die nächste sein**: Öffne ein [Issue mit Thermendaten](https://github.com/alpha-uniform/thriftherm/issues/new?template=boiler_data.yml) und nenne Modell, ebusd-Definition und was dir aufgefallen ist. Wer Daten beisteuert, wird auf Wunsch in der Tabelle oben genannt; ohne Angabe bleibt der Eintrag anonym.

## Voraussetzungen

- Home Assistant 2026.9 oder neuer
- Mit eigener Therme: eBUS-Adapter und die ebusd-App mit MQTT
- Ein Temperaturfühler je Raum; Better Thermostat, wenn Thriftherm die Heizkörperthermostate stellen soll
- Optional: eine Wärmepumpe mit ein paar Sensoren

Die vollständige Liste steht in der [Einrichtungsanleitung, Schritt 1](EINRICHTUNG.md#1-was-du-brauchst).

## Status und Verantwortung

**Das Schreiben an eine Gastherme geschieht auf deine Verantwortung.** Über ebusd wird nur die Nachricht `SetMode` genutzt, Installateur-Parameter werden nie geschrieben; bei einer Therme über Entitäten (Vorschau) nur die zwei Entitäten, die du auswählst. Den Pumpenmodus zu ändern ist eine einmalige Entscheidung, die du selbst triffst.

**Status: Beta.** Alle Steuerungen starten in *Nur planen*: Sie berechnen und zeigen an, was sie tun würden, und schreiben nichts, bis du die aktive Steuerung freigibst. Sperren, Vorlaufregelung und die Sicherheits-Fallbacks sind an der Therme des Entwicklers geprüft. Die gelernten Korrekturen kennen bisher Außentemperaturen bis hinunter zu 2 °C, noch keinen Frost. Die Wärmepumpen-Erweiterung steuert seit dem 11.10.2026 beim Entwickler täglich eine Midea PortaSplit, auch bei 2 °C draußen, als sie vereiste und abtauen musste. Andere Geräte sind noch nicht erprobt.

## Entwicklung

```bash
python3 -m venv .venv
./.venv/bin/pip install pytest pytest-asyncio pytest-homeassistant-custom-component
./.venv/bin/pytest -q
```

Die Engines unter `custom_components/thriftherm/engines/` sind reines Python ohne Home-Assistant-Importe und durch Unit-Tests abgedeckt (etwa 90 % Zeilen- und Zweigabdeckung).

## ☕ Unterstützen

Thriftherm entsteht in meiner Freizeit. Wenn es dir Energie oder Geld spart, kannst du mir [einen Kaffee spendieren](https://buymeacoffee.com/alphauniform) – so bekomme ich ein Stück der Zeit zurück, die in neue Funktionen und neue Thermen fließt.

<a href="https://buymeacoffee.com/alphauniform"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-green.png" alt="Buy me a coffee" width="240"></a>

Die **Heating Heroes** stehen hier:

- **David** – der erste Kaffee, und zwei Ideen, die jetzt drin sind: das Gas getrennt nach Heizung und Warmwasser zu zählen, auch ohne Gaszähler, und die Abfrageliste von ebusd zu kürzen, damit die wichtigen Werte öfter kommen. Danke!

## Lizenz

MIT
