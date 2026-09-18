# Thriftherm – Anleitung

[English](GUIDE.md) | **Deutsch**

> **Du richtest Thriftherm zum ersten Mal ein?** Fang mit der [Einrichtungsanleitung](EINRICHTUNG.md) an.

Diese Anleitung erklärt den Alltag mit Thriftherm: was die Modi tun, welche Temperatur wann gilt, wie das Lernen funktioniert und was bei einer Reparaturmeldung zu tun ist. Idee und Hardware stehen im [README](README.md).

**Sprache.** Ist Home Assistant auf Deutsch eingestellt, spricht Thriftherm Deutsch, bei jeder anderen Sprache Englisch – Entitätsnamen, Zustände, Attribute, Entscheidungsgrund, Dialoge, Reparaturmeldungen und Fehlermeldungen. Die Entitäts-IDs sind immer englisch (`sensor.thriftherm_boiler_command`), damit Automationen und Dashboards unabhängig von der Sprache funktionieren.

## 1. Wie alles zusammenhängt

| Teil | Aufgabe | Braucht |
|---|---|---|
| Räume | Solltemperatur je Raum aus Zeitplan, Modus, Übersteuerung und Schutzregeln | Temperatursensor je Raum |
| Raumsteuerung | Sollwerte an die Raumthermostate geben | Better Thermostat je Raum |
| Thermensteuerung | Vorlauftemperatur vorgeben oder den Heizbetrieb sperren, wenn kein Raum Wärme braucht | eigene Therme über ebusd |
| Wärmepumpe (optional, Vorschau) | COP messen, Kosten vergleichen, langsam und effizient heizen | Climate-Entität und einige Sensoren |

Jede Steuerung hat drei Stufen, wählbar über ihre Auswahl-Entität:

| Stufe | Bedeutung |
|---|---|
| **Aus** | nichts wird geplant oder gesendet |
| **Nur planen (Beta)** | alles wird berechnet und angezeigt, nichts gesendet – Standard |
| **Aktiv** | Befehle werden gesendet. Erst wählbar, wenn in den Optionen freigegeben |

Mit *Nur planen* anfangen, einige Tage die Pläne ansehen, dann auf *Aktiv* – Schritt für Schritt in der [Einrichtungsanleitung, Schritt 7](EINRICHTUNG.md#7-erst-nur-planen-dann-aktiv).

## 2. Betriebsmodi

Auswahl **Betriebsmodus**:

| Modus | Was passiert |
|---|---|
| **Automatik** | Räume folgen ihren Zeitplänen; die günstigere Wärmequelle heizt |
| **Nur Therme** | Wärmepumpe wird nicht genutzt |
| **Nur Wärmepumpe** | nur Wärmepumpen-Räume werden geheizt; die Therme nur für Frostschutz. Vorschau, siehe Abschnitt 7 |
| **Sommerbetrieb** | keine Raumheizung; Warmwasser funktioniert; Frostschutz bleibt |
| **Abwesend** | alle Räume auf Abwesenheitstemperatur; mit Rückkehrzeit wird rechtzeitig vorgeheizt. Du stellst *Geplante Rückkehr* ein, damit ist Abwesend bis dahin an; aus Automationen geht dasselbe mit `thriftherm.set_away`, und die Aktion *Abwesend beenden* (`thriftherm.clear_away`) beendet beides |

## 3. Welche Temperatur gilt

Für jeden Raum gilt die erste zutreffende Regel:

1. **Übersteuerung** (Dienst `set_override` oder Änderung am Thermostat) – bis sie abläuft.
2. **Sommerbetrieb** – nur Frostschutztemperatur.
3. **Abwesend** – Abwesenheitstemperatur (Standard 15 °C). Vor der Rückkehr wird auf Komfort vorgeheizt. Kommt die Luft ihrem Taupunkt zu nahe (Standard 3 K), wird der Sollwert gegen Feuchte angehoben.
4. **Zeitplan** – Komforttemperatur in einem Zeitplan-Block, sonst Absenktemperatur. Vor Blockbeginn wird so vorgeheizt, dass es *zu Beginn* warm ist.

Zwei Schutzregeln gelten immer zusätzlich:

| Schutz | Standard | Verhalten |
|---|---|---|
| Frostschutz | 7 °C | Fällt ein Raum darunter, heizt die Therme sofort – auch im Sommerbetrieb – bis der Raum 1 K darüber liegt. |
| Fenster offen | 90 s Karenz | Solange ein Fensterkontakt offen ist, pausiert das Heizen in diesem Raum. |

Komfort- und Absenktemperatur sind Zahlen-Entitäten je Raum (*Komforttemperatur*, *Absenktemperatur*) und lassen sich im Dashboard ändern. Die Absenktemperatur kann nie über der Komforttemperatur liegen.

### Zeitpläne

Jeder Raum kann einen **Zeitplan-Helfer** von Home Assistant nutzen (Einstellungen → Geräte & Dienste → Helfer → Zeitplan). Die Blöcke des Helfers sind die Komfortzeiten. Ein Block kann ein Attribut `temperature` tragen, um eine andere Temperatur als die Komforttemperatur zu verwenden. Räume ohne Helfer nutzen die Zeitfenster aus den Raum-Optionen.

### Vorheizen

Beginn = Blockbeginn − (Soll − Ist) / Aufheizrate − Sicherheitszuschlag. Die Aufheizrate lernt Thriftherm je Raum; bis dahin gelten 0,8 K/h für Heizkörperräume und 1,2 K/h für Wärmepumpen-Räume, mit 20 Minuten Zuschlag. Der Solltemperatur-Sensor zeigt den geplanten Beginn unter *Vorheizen beginnt*.

## 4. Dienste

| Dienst | Felder | Wofür |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature`, `duration_min` (Standard 120) | vorübergehend anderer Sollwert für einen Raum |
| `thriftherm.clear_override` | `room` (optional) | eine oder alle Übersteuerungen beenden |
| `thriftherm.set_away` | `return_time` (optional) | Abwesenheit, mit Vorheizen zur Rückkehr |
| `thriftherm.clear_away` | – | zurück auf Automatik |
| `thriftherm.boost` | `room`, `duration_min` (Standard 45) | einen Raum schnell aufheizen |
| `thriftherm.reset_learning` | `scope` (`boiler`, `heat_pump`, `rooms`), `rooms` | Gelerntes vergessen, siehe Abschnitt 6 |

`room` ist der Raum-Schlüssel aus den Optionen (z. B. `badezimmer`).

Beispiel – abwesend bis Freitag 16:00:

```yaml
action: thriftherm.set_away
data:
  return_time: "2026-12-18 16:00:00"
```

## 5. Thermensteuerung (eigene Therme über ebusd)

- **Vorlauftemperatur** aus einer Zwei-Punkt-Heizkurve (Standard 55 °C bei −10 °C, 30 °C bei +15 °C), bei großem Wärmebedarf der Räume bis 8 K höher, dazu eine gelernte Korrektur. Sie bleibt immer zwischen eingestelltem Minimum und Maximum.
  **Warum das Minimum vom Gerätetyp abhängt.** Ein Brennwertgerät profitiert von niedrigem Vorlauf: Liegt der Rücklauf unter etwa 56 °C, kondensiert das Abgas und bringt bis zu 11 % mehr, etwa 30 °C sind also ein gutes Minimum. Ein Heizwertgerät kondensiert nie, dort spart ein sehr niedriger Vorlauf wenig – und bei kurzen Brennerläufen kommt die Wärme womöglich gar nicht bis zu den fernen Heizkörpern. Gemessen an der getesteten Heizwerttherme: Mit 30 °C blieb der letzte Heizkörper kalt, 45 °C funktioniert, dort also mit etwa 45 °C beginnen. Die Einstellung heißt *Minimale Vorlauftemperatur* ([Einrichtungsanleitung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).
- **Heizbetrieb gesperrt** (`disablehc`), wenn kein Raum Wärme braucht. Warmwasser wird nie angefasst: Der Warmwasser-Sollwert wird immer als „kein Wert“ gesendet, die Therme folgt weiter ihrem Drehknopf.
- **Ein von Hand ausgeschaltetes Thermostat fordert keine Therme an:** Sein Ventil ist zu, der Raum zählt beim Bedarf nicht mit. Ein nur nicht erreichbares Thermostat zählt weiter, denn sein Ventil regelt selbst weiter.
- **Nach einem Neustart** wartet Thriftherm bis zu fünf Minuten auf die Raumfühler, bevor es den Heizbetrieb sperrt. Bis dahin geht nichts raus, und die Therme behält ihren letzten Befehl.
- **Anstieg begrenzt** auf 5 K je 10 Minuten, damit die Therme nicht von 30 auf 60 °C springt. Frostschutz ist davon ausgenommen.
- **Kurze Aussetzer werden ignoriert:** Adapter verlieren das eBUS-Signal immer wieder für eine Sekunde. Erst wenn es zwei Minuten lang wegbleibt, gilt das als Datenausfall und die Therme läuft wieder über ihren Drehknopf.
- **Wiederholung** alle 2 Minuten. Fällt Home Assistant aus, gilt nach 9–16 Minuten wieder der Drehknopf, und der Knopf bleibt immer die Obergrenze – deshalb steht er auf der höchsten Vorlauftemperatur, die du zulassen willst ([Einrichtungsanleitung, Schritt 6](EINRICHTUNG.md#6-an-der-therme)).
- **Warmwasser-Erkennung:** ebusd meldet Warmwasser nicht bei jeder Therme zuverlässig, deshalb erkennt Thriftherm es selbst: Gas trotz gesperrtem Heizbetrieb, Vorlauf weit über dem gesendeten Heizungs-Soll, Rücklauf über Vorlauf oder Vorlauf über dem Heizmaximum. Die ersten beiden greifen innerhalb einer Minute, die Temperaturen kommen nur alle paar Minuten. *Therme Warmwasser aktiv* zeigt das Ergebnis, und das Lernen pausiert eine Stunde, damit Duschwasser nicht die Heizkurve verstellt.
- **Frische Messwerte:** ebusd liest die Werte der Therme reihum, Vorlauf und Rücklauf wären sonst minutenalt. Thriftherm setzt die Abfrage-Priorität von Vorlauf, Rücklauf, Therme-Status, Heizungspumpe und Status-Nummer in ebusd hoch (alle 10 Minuten erneuert, damit ein ebusd-Neustart kaum auffällt) und fordert sie bei brennendem Gas einmal pro Minute direkt an. Das sind nur Leseanfragen, an die Therme wird nichts geschrieben.

Der Sensor *Therme-Steuerbefehl* zeigt den Plan (`Heizen`, `Heizbetrieb gesperrt`, …), den genauen `SetMode`-Befehl, den Grund, worauf gewartet wird, ob die Heizungspumpe läuft und die Status-Nummer der Therme (`S.xx`, wie auf ihrem Display).

### Warum der Pumpenmodus zählt

Im Werkszustand läuft die Pumpe nur, solange der Brenner brennt, plus kurzem Nachlauf (Vaillant: d.18 = 0, „Nachlauf“). Nehmen die Räume wenig Wärme ab, erreicht die Therme ihren Sollwert in Sekunden, schaltet ab, und das Wasser hat nur eine Runde über den Bypass der Therme gedreht: **Die Heizkörper weit hinten im Kreis bleiben kalt**, obwohl die Therme den ganzen Tag taktet.

An der getesteten Therme war genau das der Grund für ein kaltes Bad. Mit Pumpenmodus **durchlaufend** (d.18 = 1) läuft die Pumpe, solange der Heizbetrieb freigegeben ist, und **steht, wenn Thriftherm sperrt** — etwa 33 W, nur während geheizt wird. Die Temperatur im Bad stieg innerhalb einer Stunde von 20,8 auf 21,5 °C, nachdem sie sich den ganzen Nachmittag nicht bewegt hatte.

Wie du ihn prüfst und änderst, ist ein einmaliger Schritt in der [Einrichtungsanleitung, Schritt 6](EINRICHTUNG.md#6-an-der-therme).

### ebusd: Pumpe und Statuswerte

Das letzte Feld der ebusd-Nachricht `Status01` heißt Pumpenstatus, folgt aber dem Heizbedarf: Es meldete „aus“, während die Pumpe hörbar lief. Die Pumpe selbst ist **`WP`** (d.10), der Display-Code ist **`Statenumber`** (8 = S.8 Brennersperrzeit, 7 = S.7 Pumpennachlauf, 31 = S.31 keine Heizanforderung). `Status01` erkennt weiterhin Warmwasser und bleibt dafür im Einsatz.

Beide kommen nur in Home Assistant an, wenn sie durch den Namensfilter der ebusd-MQTT-Integration passen; wie du sie durchlässt: [Einrichtungsanleitung, Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen). Ohne sie greift Thriftherm auf `Status01` zurück.

### Ohne smarte Thermostate

Jeder Raum benötigt einen Temperaturfühler, kein smartes Thermostat. Alles oben funktioniert weiter: Bedarf je Raum, Heizkurve, Sperren wenn kein Raum Wärme braucht, Lernen und Frostschutz. Der *Thermostat-Plan* des Raums nennt dann den Grund *Kein Thermostat*, und an ein Ventil wird nichts gesendet: Die Handventile bleiben weit genug offen, und die Vorlauftemperatur regelt ([Einrichtungsanleitung, Schritt 5](EINRICHTUNG.md#5-better-thermostat-je-raum-optional)). Smarte Thermostate lassen sich später Raum für Raum ergänzen, gemischt geht auch.

## 6. Lernen

**Beta.** Die Steuerung selbst – Sperren, Vorlauf, Sicherheit – ist an der Therme des Entwicklers geprüft. Die gelernten Korrekturen kennen bisher nur mildes Wetter; in den ersten kalten Wochen lohnt ein Blick darauf.

| Bereich | Was gelernt wird | Woraus |
|---|---|---|
| Therme | Heizkurven-Korrektur (±10 K) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum (K/h) | echte Heizphasen bei geschlossenem Fenster |
| Räume | Auskühlkoeffizient je Raum (1/h) | Phasen ohne Heizen, Fenster zu, drinnen mindestens 5 K wärmer als draußen |
| Räume | Heizleistung je Raum (K/h ohne Verluste) | Aufheizrate plus die Verluste während der Episode; anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe (Vorschau) | COP-Kennfeld über der Außentemperatur, Sollwert-Korrektur | gemessene COP-Läufe |

**Taktbetrieb zählt ebenfalls:** Brennt die Therme eine Minute und wartet dann eine Viertelstunde, liefert sie weit mehr, als die Räume abnehmen — der Vorlauf ist zu heiß. Drei solche Läufe innerhalb einer Stunde senken die Kurve um eine Stufe. Die Spreizung käme in so kurzen Läufen nie zur Ruhe, deshalb ist das im Übergangswetter das einzige brauchbare Signal. Unter den Mindest-Vorlauf geht sie dabei nie.

Schutz gegen falsches Lernen: Die Therme lernt nur im Modus *Aktiv*, frühestens 10 Minuten nach Brennerstart, aus mindestens 10 Messwerten, höchstens eine Änderung je Stunde und vier je Tag, nie während Warmwasser, einer Sollwertänderung in den letzten 20 Minuten, Schnell-Aufheizen, Trocknung oder wenn der Sicherheitsstatus nicht OK ist. Sie beginnt im **Einlernen** (1-K-Schritte) und wechselt nach sechs Änderungen oder einem ruhigen Tag ins **Verfeinern** (0,5 K).

Der Sensor *Lernstatus* zeigt Phase, aktuelle Korrektur, Pausengrund und das letzte Zurücksetzen.

### Gelerntes vergessen

Nach einer Änderung an der Anlage passen alte Werte nicht mehr.

- **Von Hand:** Optionen → *Lernen zurücksetzen*, Bereiche (und Räume) ankreuzen, oder Dienst `thriftherm.reset_learning`.
- **Automatisch:** Ändert sich eine Einstellung, von der ein Bereich abhängt, wird nur dieser Bereich vergessen:

| Änderung | Vergessen wird |
|---|---|
| Heizkurve, minimaler oder maximaler Vorlauf | Thermen-Korrektur |
| Wärmepumpen-Entität, Ansaug-/Ausblassensor, Luftmengen-Kennlinie, Schlauchfaktor, Aufstellraum, beheizte Räume | COP-Kennfeld und Wärmepumpen-Korrektur |
| Temperatursensor oder Thermostat eines Raums | Aufheizrate dieses Raums |

Laufzustand, Sperrzeiten und Handbedienung werden nie zurückgesetzt – ein Zurücksetzen kann also kein Takten auslösen.

## 7. Wärmepumpe (optional)

> **Vorschau – folgt bald.** Noch nicht mit einer echten Wärmepumpe getestet; nicht darauf verlassen. Bisher lief sie nur im Modus *Nur planen* und hat noch nie eine Wärmepumpe gesteuert. Die Punkte unten beschreiben, was sie können soll.

- Der COP wird aus der Luftmenge (Lüfterdrehzahl → m³/h-Kennlinie) sowie Temperatur und Feuchte von Ansaug- und Ausblasluft gemessen, erst wenn das Gerät eingeschwungen ist.
- Verglichen wird mit dem Preis der zentralen Wärme. Bei Fernwärme zählt der **Heizkostenschlüssel**: Nur der Verbrauchsanteil folgt dem eigenen Zähler, eine gesparte kWh spart also weniger als ihr Preis, und die Wärmepumpe braucht einen höheren COP, damit sie sich lohnt.
- Die Wärmepumpe läuft langsam mit hohem COP. Gesperrt ist sie unter ihrer Mindest-Außentemperatur (Standard −10 °C), bei erkannter Vereisung oder wenn jemand damit kühlt. Kühlen und Heizen überschneiden sich nie.
- **Der Aufstellort zählt.** Ohne Schläuche bleibt die warme Luft im Aufstellraum. Aufstellraum und Schlauchfaktor (1,0 = keine Schläuche) eintragen; nicht erreichbare Räume erzeugen eine Reparaturmeldung.
- **Bad-Trocknung** braucht die Wärmepumpe: nur in einem Raum, den die Wärmepumpe beheizt und der einen Feuchtesensor hat; das Raumformular bietet sie erst an, wenn eine Wärmepumpe eingerichtet ist. Nach dem Duschen trocknet die Wärmepumpe den Raum – nur solange sie heizen und effizient laufen kann, höchstens 60 Minuten und nie über Soll + 1,5 K. Sonst folgt der Heizkörper der normalen Fensterlogik.
- **Schnell aufheizen** (Button je Raum oder `thriftherm.boost`) lässt sie für begrenzte Zeit mit voller Leistung laufen.

## 8. Wichtige Anzeigen

| Entität | Zeigt |
|---|---|
| *Entscheidungsgrund* | in Worten, warum das System tut, was es tut |
| *Empfohlene Wärmequelle* | Therme, Wärmepumpe, beide oder keine |
| *Sicherheitsstatus* | OK, eingeschränkt (ein Sensor fehlt), Fallback (nichts Verlässliches, nichts wird gesendet) |
| *Solltemperatur* je Raum | den Sollwert und unter *Grund*, woher er kommt |
| *Thermostat-Plan* je Raum | was an das Thermostat geht |
| *Therme-Steuerbefehl* | was die Therme bekommt; *Drehknopf regelt* heißt, es wird nichts gesendet und die Therme läuft über ihren Knopf. Zeigt auch Heizungspumpe und Status S.xx |
| *Lernstatus* | was gelernt wurde und warum das Lernen pausiert |
| *Geplante Rückkehr* | wann du zurück bist; einstellbar, leer wenn nichts geplant ist |

## 9. Reparaturmeldungen

| Meldung | Bedeutung und Abhilfe |
|---|---|
| *Raum*: kein Heizfenster hinterlegt | weder Zeitplan-Helfer noch Zeitfenster – der Raum bleibt auf Absenkung. In den Raum-Optionen einen Zeitplan wählen. |
| *Raum*: Zeitplan-Helfer fehlt | der gewählte Zeitplan wurde gelöscht. Neu anlegen oder einen anderen wählen. |
| Keine Daten von der Therme | ebusd liefert nichts Brauchbares; nichts wird gesendet, die Therme läuft über ihren Drehknopf. ebusd-App und Adapter prüfen. |
| Luftmenge der Wärmepumpe nicht kalibriert | keine Luftmengen-Kennlinie, daher kein COP; bis dahin gilt das Datenblatt. |
| Wärmepumpe versorgt Räume ohne Schläuche | keine Schläuche eingetragen, die warme Luft bleibt im Aufstellraum. Schläuche montieren und Schlauchfaktor setzen oder die Räume abwählen. |

Manches zeigt sich ohne Meldung: Ein Raumfühler, der seit sechs Stunden still ist, zählt nicht mehr, die Temperatur des Thermostats springt ein, und der *Sicherheitsstatus* geht auf *Eingeschränkt*.

Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → *Diagnose herunterladen* liefert eine Datei mit Konfiguration, aktuellem Zustand und Lernwerten für Fehlerberichte. Es wird nichts daraus entfernt – vor dem Veröffentlichen durchsehen.

## 10. Häufige Fragen

**Warum steht ein Raum auf 15 °C?** Er ist im Modus Abwesend. Der Frostschutz (7 °C) ist eine eigene, tiefere Grenze.

**Ich habe am Thermostat gedreht – warum stellt es sich nicht zurück?** Eine Handänderung wird zur vorübergehenden Übersteuerung (Standard 120 Minuten). Mit `clear_override` beenden.

**Verändert Thriftherm meine Warmwassertemperatur?** Nein. Für den Warmwasser-Sollwert wird „kein Wert“ gesendet; der Drehknopf der Therme entscheidet.

**Was passiert, wenn Home Assistant abstürzt?** Die Therme fällt innerhalb von 9–16 Minuten auf ihren Drehknopf zurück; die Thermostate behalten ihren letzten Sollwert.

**Warum ist der *Temperaturtrend* nach einem Neustart leer?** Der Trend braucht etwa 30 Minuten Messwerte.
