# Thriftherm – Anleitung

[English](../en/GUIDE.md) | **Deutsch**

> **Du richtest Thriftherm zum ersten Mal ein?** Fang mit der [Einrichtungsanleitung](EINRICHTUNG.md) an.

Diese Anleitung erklärt den Alltag mit Thriftherm: was die Modi tun, welche Temperatur wann gilt, was Thriftherm lernt und was bei einer Reparaturmeldung zu tun ist. Idee und Hardware stehen im [README](../README.md). Wie Thriftherm intern rechnet, mit allen Schwellen und Zeiten, steht in [Wie Thriftherm rechnet](FUNKTIONSWEISE.md). Bei Problemen hilft die [Fehlersuche](FEHLERSUCHE.md).

**Sprache.** Ist Home Assistant auf Deutsch eingestellt, spricht Thriftherm Deutsch, bei jeder anderen Sprache Englisch – Entitätsnamen, Zustände, Attribute, Entscheidungsgrund, Dialoge, Reparaturmeldungen und Fehlermeldungen. Die Entitäts-IDs sind immer englisch (`sensor.thriftherm_boiler_command`), damit Automationen und Dashboards unabhängig von der Sprache funktionieren.

## 1. Wie alles zusammenhängt

| Teil | Aufgabe | Braucht |
|---|---|---|
| Räume | Solltemperatur je Raum aus Zeitplan, Modus, Übersteuerung und Schutzregeln | Temperatursensor je Raum |
| Raumsteuerung | Sollwerte an die Raumthermostate geben | Better Thermostat je Raum |
| Thermensteuerung | Vorlauftemperatur vorgeben oder den Heizbetrieb sperren, wenn kein Raum Wärme braucht | eigene Therme über ebusd |
| Wärmepumpe (optional) | COP messen, Kosten vergleichen, langsam und effizient heizen | Climate-Entität und einige Sensoren |

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
| **Nur Heizung** | Wärmepumpe wird nicht genutzt; es heizt die Therme oder die Fernwärme |
| **Nur Wärmepumpe** | nur Wärmepumpen-Räume werden geheizt; die Therme nur für Frostschutz. Siehe Abschnitt 7 |
| **Sommerbetrieb** | keine Raumheizung; Warmwasser funktioniert; Frostschutz bleibt |
| **Abwesend** | alle Räume auf Abwesenheitstemperatur; mit Rückkehrzeit wird rechtzeitig vorgeheizt, siehe unten |

### Abwesend mit und ohne Rückkehr

- **Abwesend ohne Ende:** Betriebsmodus *Abwesend* wählen. *Geplante Rückkehr* bleibt leer. Die Räume bleiben auf Abwesenheitstemperatur, bis du *Abwesend beenden* oder einen anderen Modus wählst. Läuft schon *Abwesend* mit Rückkehr, ändert ein erneutes *Abwesend* nichts; dann `thriftherm.set_away` mit `clear_return_time: true` aufrufen.
- **Abwesend bis zu einem Zeitpunkt**, z. B. heute 22:00 oder 3. Oktober 12:00: *Geplante Rückkehr* einstellen. Das schaltet auf Abwesend bis dahin, und zur Rückkehr wird rechtzeitig vorgeheizt. In den Feldern von Home Assistant (Dialog der Entität, Entitäten-Karte) erst das **Datum**, dann die **Uhrzeit** wählen.
- **Nur ein Datum ist ein Platzhalter:** Ohne Uhrzeit steht die Rückkehr vorläufig auf 23:59 des Tages. Abwesend endet dann um 23:59, vorgeheizt wird aber noch nicht. Das Attribut *Vorläufig (23:59 als Platzhalter)* (`provisional`) an *Geplante Rückkehr* steht so lange auf `true`.
- **Wieder da:** Die Aktion *Abwesend beenden* (`thriftherm.clear_away`) beendet Abwesenheit und Rückkehr. Ist die Rückkehrzeit erreicht, geht es von selbst zurück auf Automatik.
- **Rückkehr über Anwesenheit:** In den Optionen unter *Regelparameter* eine oder mehrere Personen wählen (*Abwesend endet, wenn eine dieser Personen heimkommt*). Kommt eine davon heim, endet Abwesend wie zur geplanten Rückkehr: zurück auf Automatik, die Räume heizen auf. Wer beim Einschalten von *Abwesend* noch zu Hause ist, beendet es nicht; erst wenn er geht und wiederkommt. Weggehen schaltet nie auf Abwesend. Regeln: [Rückkehr über Anwesenheit](FUNKTIONSWEISE.md#rückkehr-über-anwesenheit).
- **Aus Automationen:** `thriftherm.set_away` nehmen, mit `return_time` oder mit `clear_return_time: true` ohne Ende. Anders als `datetime.set_value` meldet es einen Fehler, wenn es nicht klappt.

Nach jeder Eingabe *Geplante Rückkehr* und *Vorläufig* prüfen, besonders wenn dein Browser in einer anderen Zeitzone läuft als Home Assistant: Die Felder rechnen in Browser-Zeit. Was abgelehnt wird, wie du wirklich um 23:59 zurückkommst und was bei anderen Zeitzonen passiert: [Wie Thriftherm rechnet: Abwesend und geplante Rückkehr](FUNKTIONSWEISE.md#abwesend-und-geplante-rückkehr).

## 3. Welche Temperatur gilt

Für jeden Raum gilt die erste zutreffende Regel:

1. **Übersteuerung** (Aktion `set_override` oder eine Änderung von Hand am Thermostat, siehe unten) – bis sie abläuft.
2. **Sommerbetrieb** – nur Frostschutztemperatur.
3. **Abwesend** – Abwesenheitstemperatur (Standard 15 °C). Vor einer Rückkehr mit gewählter Uhrzeit wird rechtzeitig vorgeheizt (nicht bei der vorläufigen 23:59, siehe Abschnitt 2). Kommt die Luft ihrem Taupunkt zu nahe (Standard 3 Grad), wird der Sollwert gegen Feuchte angehoben ([Feuchteschutz](FUNKTIONSWEISE.md#feuchteschutz)).
4. **Zeitplan** – Komforttemperatur in einem Zeitplan-Block, sonst Absenktemperatur. Vor Blockbeginn wird so vorgeheizt, dass es *zu Beginn* warm ist.

### Änderungen von Hand

Ändert jemand anderes als Thriftherm einen Sollwert, ist das eine Änderung von Hand: in Home Assistant (Dashboard, App, Sprachassistent), durch eine Automation oder ein Skript, oder am Gerät (Drehknopf am Ventil, Fernbedienung der Klimaanlage, App des Herstellers). Wer es war, sagt Home Assistant selbst. Wie Thriftherm das liest: [Wie Thriftherm rechnet: Änderungen von Hand](FUNKTIONSWEISE.md#änderungen-von-hand).

- **Sie gilt eine feste Zeit** (Parameter *Wie lange eine Änderung von Hand gilt*, Standard 120 Minuten). Am Raumthermostat wird sie zur Übersteuerung des Raums; an der Wärmepumpe lässt Thriftherm das Gerät so lange in Ruhe. Eine weitere Änderung in dieser Zeit verlängert sie.
- **Sie wird sofort angezeigt:** am Sollwert-Sensor des Raums (*Übersteuert bis*, *Übersteuert durch*) und am Steuerbefehl der Wärmepumpe (*Von Hand geändert, gilt bis*, *Von Hand geändert durch*).
- **Sie endet von selbst** oder mit dem Knopf *Zurück zur Automatik* (Aktion `thriftherm.clear_override` ohne Raum). Das beendet alle Übersteuerungen, jedes Schnell-Aufheizen und die Ruhezeit der Wärmepumpe auf einmal.
- **Drehknopf am Ventil hinter Better Thermostat:** Ob ein Dreh zählt, entscheidet dort der Schalter *Kindersicherung*. Ist er an, zählt der Dreh nicht. Ist er aus, wird die Zahl, die das Ventil nach dem Dreh zeigt, zum Sollwert des Raums. Wegen der Korrektur von Better Thermostat kann das ein anderer Wert sein, als du denkst ([Beispiel](FUNKTIONSWEISE.md#dreh-am-ventil-hinter-einem-raumthermostat)).
- **Keine Änderung von Hand** sind ein Befehl, den die Wärmepumpe nicht ausgeführt hat (er wird wiederholt), ein Thermostat, das auf ganze Grad rundet, und ein Thermostat, das jemand nur ausschaltet.

Kühlt, entfeuchtet oder lüftet jemand mit der Wärmepumpe, bleibt Thriftherm draußen, solange dieser Modus an ist. Eine Therme über Entitäten (Abschnitt 5) hat keine Handänderung: Solange die Steuerung aktiv ist, gehören Zahl und Schalter ihr; wer selbst stellen will, schaltet auf *Nur planen*.

Zwei Schutzregeln gelten immer zusätzlich:

| Schutz | Standard | Verhalten |
|---|---|---|
| Frostschutz | 7 °C | Fällt ein Raum darunter, heizt die Therme sofort – auch im Sommerbetrieb – bis der Raum 1 Grad darüber liegt. |
| Fenster offen | 90 s Karenz | Solange ein Fensterkontakt offen ist, pausiert das Heizen in diesem Raum. Ein Kontakt, der beim Start von Thriftherm schon offen ist, zählt sofort. |

Komfort- und Absenktemperatur sind Zahlen-Entitäten je Raum (*Komforttemperatur*, *Absenktemperatur*) und lassen sich im Dashboard ändern. Die Absenktemperatur kann nie über der Komforttemperatur liegen: Drehst du Komfort darunter, geht die Absenkung mit. Die Raum-Optionen zeigen die Werte, die gerade gelten; änderst du dort eine der beiden, gelten beide Werte aus dem Formular.

### Zeitpläne

Jeder Raum kann einen **Zeitplan-Helfer** von Home Assistant nutzen (Einstellungen → Geräte & Dienste → Helfer → Zeitplan). Die Blöcke des Helfers sind die Komfortzeiten. Ein Block kann ein Attribut `temperature` tragen, um eine andere Temperatur als die Komforttemperatur zu verwenden. Räume ohne Helfer nutzen die Zeitfenster aus den Raum-Optionen.

### Ohne festen Zeitplan

Wer lieber von Hand regelt als nach Uhrzeit:

- **Rund um die Uhr Komfort:** Zeitfenster `00:00-23:59` an Werktagen und am Wochenende, oder ein Zeitplan-Helfer mit Blöcken von 00:00 bis 24:00 (oder bis 23:59). Beides gilt als durchgehend, auch über Mitternacht.
- **Temperatur verstellen:** die Zahl *Komforttemperatur* des Raums ändern, etwa über eine Thermostat-Karte oder ein Template-Thermostat. Die Absenkung geht mit, wenn du darunter drehst.
- **Abwesend/Zuhause:** `thriftherm.set_away` (mit `clear_return_time: true` ohne Ende, mit `return_time` samt Vorheizen) und `thriftherm.clear_away`. Die Komforttemperaturen bleiben dabei stehen.

Für das Lernen der Heizkurve ist das sogar günstig, sie lernt aus langen, gleichmäßigen Heizläufen. Jede Verstellung pausiert das Lernen nur 20 Minuten ([Abschnitt 6](#6-lernen)). Auch ein Soll von Hand am Thermostat lehrt die Kurve, sobald der Raum bis auf 1 Grad heran ist und dort hält.

### Vorheizen

Thriftherm beginnt so früh, dass der Raum zu Beginn eines Komfort-Blocks warm ist. Dafür nimmt es den Abstand zum Soll, die gelernte Aufheizrate des Raums für die Wärmequelle, die heizen wird, und einen Sicherheitszuschlag (Standard 20 Minuten). Den geplanten Beginn zeigt der Solltemperatur-Sensor unter *Vorheizen beginnt*. Formel und Startwerte: [Wie Thriftherm rechnet: Vorheizen](FUNKTIONSWEISE.md#vorheizen).

## 4. Dienste

| Dienst | Felder | Wofür |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature`, `duration_min` (Standard 120) | vorübergehend anderer Sollwert für einen Raum |
| `thriftherm.clear_override` | `room` (optional) | zurück zur Automatik: eine Übersteuerung beenden, ohne Raum alles von Hand Geänderte |
| `thriftherm.set_away` | `return_time` oder `clear_return_time` (beide optional) | Abwesenheit, mit Vorheizen zur Rückkehr oder ohne Ende |
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

Eine Therme ohne eBUS lässt sich als Vorschau über eine Zahl-Entität (Vorlauf-Sollwert) und einen Schalter (Heizung an/aus) steuern, siehe [EINRICHTUNG.md](EINRICHTUNG.md) Abschnitt 4.3a. Alles Folgende gilt für beide Wege; wo von SetMode die Rede ist, schreibt Thriftherm dort Zahl und Schalter.

- **Vorlauftemperatur:** aus einer Zwei-Punkt-Heizkurve (Standard 55 °C bei −10 °C, 30 °C bei +15 °C). Dazu kommt, was der Raum verlangt, der am meisten braucht: seine gelernte Korrektur ([Abschnitt 6](#6-lernen)), bis zu 8 Grad mehr, solange er weit unter Soll ist, und sein Zuschlag fürs [Aufheiztempo](#aufheiztempo). Der Vorlauf bleibt immer zwischen eingestelltem Minimum und Maximum und steigt nicht sprunghaft ([Vorlauf im Detail](FUNKTIONSWEISE.md#vorlauf)).
- **Anforderung:** Ein Raum fordert die Therme an, wenn er spürbar unter Soll liegt, bis er fast dort ist; in der letzten halben Stunde seiner Komfortzeit nicht mehr, außer die Therme hilft der Wärmepumpe. Den Zustand zeigt das Attribut *Anforderung an die Therme* am Sensor *Heizbedarf* des Raums ([Schwellen](FUNKTIONSWEISE.md#wann-ein-raum-die-therme-anfordert)).
- **Mitheizen:** Läuft die Therme ohnehin, heizen andere Räume mit (Grund *Heizt mit* bzw. *Heizt früher vor*). Das gibt längere Brennerläufe und weniger Starts ([Einzelheiten](FUNKTIONSWEISE.md#mitheizen)).
- **Heizbetrieb gesperrt** (`disablehc`), wenn kein Raum Wärme braucht. Warmwasser wird nicht angefasst: Der Warmwasser-Sollwert geht immer als „kein Wert“ hinaus, die Therme folgt weiter ihrem Drehknopf. Die einzige Ausnahme wählst du selbst: Ist *Warmwasser bereithalten* in den Thermen-Optionen aus, heizt die Therme nicht mehr für Warmwasser, solange kein Hahn offen ist (das stündliche Warmhalten einer Kombitherme). Mit Warmwasserspeicher eingeschaltet lassen.
- **Thermostat aus oder stumm:** Ein von Hand ausgeschaltetes Thermostat fordert keine Therme an, sein Ventil ist zu. Ein nur nicht erreichbares zählt weiter, denn sein Ventil regelt selbst. Ein Heizkörperthermostat, das über zweieinhalb Stunden schweigt, fordert keine Therme an und nimmt keine neuen Sollwerte an; dazu erscheint eine Reparaturmeldung. Bei Better Thermostat wird das echte Thermostat dahinter überwacht. Hält Thriftherm ein gesundes Thermostat für stumm, in Zigbee2MQTT dessen Entität *Last seen* einschalten; mit ZHA ist nichts zu tun ([warum](FUNKTIONSWEISE.md#ausfälle-und-wiederholung)).
- **Ausfälle:** Nach einem Neustart wartet Thriftherm bis zu fünf Minuten auf die Raumfühler. Bleibt das eBUS-Signal zwei Minuten weg, sendet Thriftherm nichts mehr. Fällt Home Assistant aus, läuft die Therme nach 9–16 Minuten wieder über ihren Drehknopf. Der Knopf bleibt immer die Obergrenze; stell ihn deshalb auf die höchste Vorlauftemperatur, die du zulassen willst ([Einrichtungsanleitung, Schritt 6](EINRICHTUNG.md#6-an-der-therme)).
- **Warmwasser:** Thriftherm erkennt selbst, wenn die Therme Warmwasser macht (*Therme Warmwasser aktiv*), und pausiert das Lernen dann eine Stunde ([woran](FUNKTIONSWEISE.md#warmwasser-erkennung)).
- **Nur Leseanfragen:** Damit Vorlauf und Rücklauf frisch sind, lässt Thriftherm ebusd sie öfter lesen. An die Therme wird dabei nichts geschrieben ([Einzelheiten](FUNKTIONSWEISE.md#frische-messwerte)).

Der Sensor *Therme-Steuerbefehl* zeigt den Plan (`Heizen`, `Heizbetrieb gesperrt`, …), den genauen `SetMode`-Befehl, den Grund, worauf gewartet wird, ob die Heizungspumpe läuft und die Status-Nummer der Therme (`S.xx`, wie auf ihrem Display).

**Warum das Minimum vom Gerätetyp abhängt.** Ein Brennwertgerät profitiert von niedrigem Vorlauf: Unter etwa 56 °C Rücklauf kondensiert das Abgas und bringt bis zu 11 % mehr, etwa 30 °C sind ein gutes Minimum. Ein Heizwertgerät kondensiert nie und spart dort wenig; bei kurzen Brennerläufen kommt die Wärme womöglich nicht bis zu den fernen Heizkörpern. An der getesteten Heizwerttherme blieb mit 30 °C der letzte Heizkörper kalt, 45 °C funktioniert: dort mit etwa 45 °C beginnen. Die Einstellung heißt *Minimale Vorlauftemperatur* ([Einrichtungsanleitung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).

### Aufheiztempo

Die Heizkurve ist dafür da, eine Temperatur zu halten. Wird ein Raum hochgestellt, kann das Aufheizen allein mit ihr lange dauern. Wie schnell ein Raum warm werden soll, stellst du in den Thermen-Optionen ein: *Aufheiztempo (Grad pro Stunde, 0 = aus)*, Standard 0.

- Liegt ein Raum, der anfordert, 1 Grad oder mehr unter Soll, bekommt er beim Aufheizen einen eigenen Zuschlag auf den Vorlauf.
- Alle 20 Minuten misst Thriftherm, wie schnell der Raum steigt. Zu langsam: Der Zuschlag wächst. Deutlich zu schnell: Er sinkt. Nahe am Soll wird er kleiner.
- Was ein Raum gebraucht hat, ist der Start fürs nächste Aufheizen dieses Raums.
- Solange ein Raum mit Zuschlag aufheizt, pausiert das Lernen der Heizkurve (Grund *ein Raum heizt mit Zuschlag auf*).

Schritte, Grenzen und wann gemessen wird: [Wie Thriftherm rechnet: Aufheiztempo](FUNKTIONSWEISE.md#aufheiztempo).

Mehr Tempo kostet mehr Gas. Welcher Wert passt:

| Wert | Wirkung |
|---|---|
| **0** (Standard) | aus, der Vorlauf wie bisher: Heizkurve, gelernte Korrektur und bis zu 8 Grad mehr, solange ein Raum weit unter Soll ist. Reicht, wenn die Räume nach Zeitplan vorheizen oder es nicht eilt |
| **0,5** | sanft: greift nur, wenn ein Raum beim Aufheizen fast stehen bleibt (weniger als 0,4 Grad je Stunde). Kostet am wenigsten |
| **1** | zügig: etwa ein Grad je Stunde. Ein guter Anfang, wenn dir Räume nach dem Hochstellen zu lange brauchen |
| **2** | schnell: zwei Grad je Stunde. Braucht viel Vorlauf und am meisten Gas; mit kleinen Heizkörpern oder niedrigem Maximum reicht es nicht immer |

Der Lernstatus zeigt *Aufheiz-Zuschlag je Raum* (beim Aufheizen der Zuschlag, sonst der Startwert fürs nächste) und *Räume, die mit Zuschlag aufheizen*.

### Warum der Pumpenmodus zählt

Im Werkszustand läuft die Pumpe nur, solange der Brenner brennt, plus kurzem Nachlauf (Vaillant: d.18 = 0, „Nachlauf“). Nehmen die Räume wenig Wärme ab, schaltet die Therme nach Sekunden wieder ab. Das Wasser hat dann nur eine Runde über den Bypass der Therme gedreht: **Die Heizkörper weit hinten im Kreis bleiben kalt**, obwohl die Therme den ganzen Tag taktet.

Mit Pumpenmodus **durchlaufend** (d.18 = 1) läuft die Pumpe, solange der Heizbetrieb freigegeben ist, und **steht, wenn Thriftherm sperrt**. Das kostet etwa 33 W, nur während geheizt wird. An der getesteten Therme war genau das der Grund für ein kaltes Bad ([gemessen](FUNKTIONSWEISE.md#ebusd-pumpe-und-statuswerte)). Prüfen und ändern: [Einrichtungsanleitung, Schritt 6](EINRICHTUNG.md#6-an-der-therme).

### ebusd: Pumpe und Statuswerte

Ob die Heizungspumpe läuft und welche Status-Nummer (S.xx) die Therme zeigt, liest Thriftherm aus den ebusd-Werten `WP` und `Statenumber`. Sie kommen nur in Home Assistant an, wenn sie durch den Namensfilter der ebusd-MQTT-Integration passen: [Einrichtungsanleitung, Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen). Ohne sie greift Thriftherm auf `Status01` zurück ([warum das nicht reicht](FUNKTIONSWEISE.md#ebusd-pumpe-und-statuswerte)).

### Ohne smarte Thermostate

Jeder Raum benötigt einen Temperaturfühler, kein smartes Thermostat. Alles oben funktioniert weiter: Bedarf je Raum, Heizkurve, Sperren wenn kein Raum Wärme braucht, Lernen und Frostschutz. Der *Thermostat-Plan* des Raums nennt dann den Grund *Kein Thermostat*, und an ein Ventil wird nichts gesendet: Die Handventile bleiben weit genug offen, und die Vorlauftemperatur regelt ([Einrichtungsanleitung, Schritt 5](EINRICHTUNG.md#5-better-thermostat-je-raum-optional)). Smarte Thermostate lassen sich später Raum für Raum ergänzen, gemischt geht auch.

## 6. Lernen

**Beta.** Die Steuerung selbst – Sperren, Vorlauf, Sicherheit – ist an der Therme des Entwicklers geprüft. Die gelernten Korrekturen kennen bisher Außentemperaturen bis hinunter zu 2 °C, noch keinen Frost; in den ersten Frostwochen lohnt ein Blick darauf.

| Bereich | Was gelernt wird | Woraus |
|---|---|---|
| Therme | Korrektur der Heizkurve, je Raum und je Lastfall (ein Raum, wenige oder die Mehrheit fordern an) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum und Wärmequelle (Grad je Stunde) | echte Heizphasen bei geschlossenem Fenster |
| Räume | Auskühlkoeffizient je Raum (1/h) | Phasen ohne Heizen bei geschlossenem Fenster |
| Räume | Heizleistung je Raum (Grad je Stunde ohne Verluste) | Aufheizrate plus die Verluste in der Phase; anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe | COP über der Außentemperatur | gemessene COP-Läufe |

Einzelheiten zu allen Bereichen: [Wie Thriftherm rechnet: Lernen](FUNKTIONSWEISE.md#lernen).

- **Höhe und Steigung:** Die Korrektur der Heizkurve hat zwei Teile. Die **Höhe** gilt bei jeder Außentemperatur, die **Steigung** vor allem bei Kälte, so wie man eine Heizkurve von Hand einstellt. Was im Herbst gelernt wird, verstellt deshalb den Winter kaum.
- **Je Raum und Lastfall:** Ein kleiner Heizkörper in einem kalten Raum braucht mehr Vorlauf als ein großer, wenige offene Heizkörper einen anderen als viele. Deshalb hat jeder Raum eine eigene Korrektur, getrennt für drei Lastfälle: ein Raum fordert an, wenige, die Mehrheit. Der Raum, der am meisten braucht, bestimmt den Vorlauf ([Lastfälle und Werte je Raum](FUNKTIONSWEISE.md#lastfälle-und-werte-je-raum)).
- **Vorsichtig:** Die Therme lernt nur im Modus *Aktiv*, in kleinen Schritten mit Wartezeiten und Tagesgrenzen. Ein Wert, der sich einige Stunden bewährt hat, bleibt stehen. Pause ist bei Warmwasser, nach einer Sollwertänderung (20 Minuten), bei Schnell-Aufheizen, Trocknung, einem Aufheizen mit Zuschlag und wenn der Sicherheitsstatus nicht OK ist ([Schutz gegen falsches Lernen](FUNKTIONSWEISE.md#schutz-gegen-falsches-lernen)).
- **Taktet die Therme,** zählt allein, ob die Räume vorankommen ([Taktbetrieb](FUNKTIONSWEISE.md#taktbetrieb)).
- **Ein Gaszähler ist nicht nötig.** Den Brenner erkennt Thriftherm auch am Status der Therme ([Brenner erkennen](FUNKTIONSWEISE.md#brenner-erkennen)).

**Wo du es siehst:** Der Sensor *Lernstatus* zeigt

- Phase (*Einlernen*, *Verfeinern* oder pausiert), aktuelle Korrektur mit Höhe und Steigung, Pausengrund und das letzte Zurücksetzen. Pausiert er wegen einer Sollwertänderung, steht dort auch, bis wann und in welchem Raum;
- *Anfordernde Räume* und ob die Spreizung gerade bewertet;
- den *Lastfall jetzt* und den *Raum, der den Vorlauf bestimmt*;
- unter *Heizkurven-Korrektur je Lastfall und Raum* für jeden Fall und Raum (Einträge wie `few:badezimmer`) Korrektur, Höhe, Steigung, ob der Wert gefunden ist, die Betriebsstunden und wie oft dabei ein Raum hing.

Der Sensor *Therme Brenneranteil* zeigt, welchen Anteil der letzten Stunde der Brenner für die Heizung lief, und die Zahl der Starts. Er schreibt vorerst nur mit. Der Sensor *Therme Heizstarts heute* zählt die Brennerstarts für die Heizung seit Mitternacht. Ein Start zählt, sobald sein Brennerlauf endet, denn erst dann steht fest, dass er nicht für Warmwasser war. Die Statistik von Home Assistant zeigt damit die Starts jedes Tages.

**Was du einstellen kannst** (Thermen-Optionen):

- *Anteil der Räume …*: Die Spreizung bewertet die Kurve nur, wenn mindestens dieser Anteil der Räume anfordert, die gerade Wärme abnehmen können (Standard die Hälfte). Sonst zählt allein, ob die anfordernden Räume vorankommen.
- *Eigene Heizkurve je Raum, für einen Raum, wenige Räume und die Mehrheit*: Ausgeschaltet gilt eine gemeinsame Korrektur für alle Räume und Fälle; die Werte je Lastfall und Raum bleiben erhalten.

### Gelerntes vergessen

Nach einer Änderung an der Anlage passen alte Werte nicht mehr.

- **Von Hand:** Optionen → *Lernen zurücksetzen*, Bereiche (und Räume) ankreuzen, oder Dienst `thriftherm.reset_learning`.
- **Automatisch:** Ändert sich eine Einstellung, von der ein Bereich abhängt, wird nur dieser Bereich vergessen:

| Änderung | Vergessen wird |
|---|---|
| Heizkurve | Thermen-Korrektur |
| Wärmepumpen-Entität, Ansaug-/Ausblassensor, Luftmengen-Kennlinie, Schlauchfaktor, Aufstellraum, beheizte Räume | COP-Kennfeld und Lernläufe |
| Temperatursensor oder Thermostat eines Raums | Aufheizrate dieses Raums |

Laufzustand, Sperrzeiten und Handbedienung werden nie zurückgesetzt – ein Zurücksetzen kann also kein Takten auslösen.

## 7. Wärmepumpe (optional)

Erprobt ist sie an einer Midea PortaSplit, auch mit Vereisung und Abtauen; andere Geräte noch nicht.

- **COP:** Thriftherm misst ihn aus der Luft, die die Wärmepumpe ansaugt und ausbläst, und aus ihrem Strom. Taktet sie, zählt der ganze Takt samt Pause (Attribut *COP über den letzten Takt*); dieser Wert bestimmt die Kosten ([COP messen](FUNKTIONSWEISE.md#cop-messen)).
- **Kosten:** Die Wärmepumpe läuft langsam mit hohem COP. Verglichen wird mit dem Preis der zentralen Wärme. Bei Fernwärme zählt der **Heizkostenschlüssel**: Nur der Verbrauchsanteil folgt dem eigenen Zähler. Eine gesparte kWh spart also weniger als ihr Preis, und die Wärmepumpe braucht einen höheren COP, damit sie sich lohnt.
- **Aufstellort und Schläuche:** Ohne Schläuche bleibt die warme Luft im Aufstellraum. Aufstellraum und Schlauchfaktor (1,0 = keine Schläuche) eintragen; nicht erreichbare Räume erzeugen eine Reparaturmeldung. Der Schlauchfaktor korrigiert die Luftmenge, nicht die Wärme: Was die Schläuche unterwegs abgeben, bleibt in der Wohnung.
- **Gesperrt** ist die Wärmepumpe unter ihrer Mindest-Außentemperatur (Standard −10 °C), bei Vereisung oder häufigem Abtauen, wenn jemand mit ihr kühlt, und wenn ihr gemessener COP länger unter dem *Break-even-COP* liegt, sie also teurer heizt als die Heizung. Wie lange die Sperren wegen Vereisung und Ineffizienz dauern, stellt *Sperre nach Vereisung / Ineffizienz* ein (Standard zwei Stunden). Im Modus *Nur Wärmepumpe* gibt es die Sperre wegen Ineffizienz nicht ([Sperren](FUNKTIONSWEISE.md#sperren)).
- **Kühlen und Heizen überschneiden sich nie:** Solange sie kühlt, bleiben die Heizkörper der Räume, die sie bedient, auf Absenktemperatur und fordern keine Wärme von der Therme an. Der Frostschutz gilt weiter.
- **Schnell aufheizen** (Button je Raum oder `thriftherm.boost`) lässt sie für begrenzte Zeit mit voller Leistung laufen.
- **Lüfter:** Thriftherm stellt ihn nur beim Einschalten, für eine Trocknung oder ein Schnell-Aufheizen und danach zurück, auf die *Lüfterstufe im Normalbetrieb* aus der Feineinstellung (Standard *Auto*). Eine höhere Stufe bringt mehr Wärme in die Räume, ist aber lauter. Eine neu gewählte Stufe bekommt die Wärmepumpe auch mitten im Lauf, sofern sie noch die alte hat. Eine Stufe, die du von Hand wählst, bleibt. Vom Heizen geht es nie direkt auf Aus: Erst läuft 15 Minuten nur der Lüfter (Parameter, 0 = sofort aus). Stellt in dieser Zeit jemand von Hand einen anderen Modus ein, endet der Nachlauf.

**Bad-Trocknung** braucht die Wärmepumpe: nur in einem Raum, den sie beheizt und der einen Feuchtesensor hat. Das Raumformular bietet sie erst an, wenn eine Wärmepumpe eingerichtet ist.

- Getrocknet wird **nach** dem Duschen: Steigt die Feuchte, wartet Thriftherm, bis sie nicht mehr steigt. Dann bläst die Wärmepumpe mit höchster Lüfterstufe und der Trocknungs-Solltemperatur, höchstens 60 Minuten (Standard) und nur, solange sie aktiv gesteuert wird, heizen und effizient laufen kann. Danach bekommt der Lüfter seine Stufe von vorher zurück.
- Der Knopf *Jetzt trocknen* startet sofort und für die volle Dauer, etwa für Wäsche oder einen nassen Boden; *Zurück zur Automatik* beendet die Trocknung. Wer nur den Knopf will, schaltet im Raumformular *Trocknung nach dem Duschen von selbst starten* aus.
- Hinaus geht die Feuchte nur durchs Fenster. Hat das Bad einen Fensterkontakt, bläst die Wärmepumpe deshalb nur, solange das Fenster offen ist: Fenster nach dem Duschen weit öffnen, dann trocknet die warme Luft Wände und Spiegel, und die Feuchte zieht ab. Bei geschlossenem Fenster wartet die Trocknung (*Wartet: Fenster öffnen*). Ohne Fensterkontakt bläst sie wie bisher auch bei geschlossenem Fenster. Der Heizkörper folgt immer der normalen Fensterlogik.

Wann sie startet und endet: [Bad-Trocknung](FUNKTIONSWEISE.md#bad-trocknung).

**Sollwert:** Die Wärmepumpe läuft mit kleinster Leistung, dort ist ihr COP am besten. Ihr Sollwert liegt ein Grad unter ihrer eigenen Messung und wird nur selten neu gesetzt, damit sie wenig piept. Der kälteste versorgte Raum entscheidet: Ein halbes Grad über Soll ruht der Verdichter (Sollwert 17 °C, das Gerät bleibt im Heizbetrieb), am Soll heizt sie wieder. Kommt ein Raum nicht hinterher, hilft die Heizung, also Therme oder Fernwärme (Attribut `boiler_helps`); ohne zentrale Heizung oder im Modus *Nur Wärmepumpe* geht ihr Sollwert langsam höher. Das lässt sich unter *Wenn die Wärmepumpe allein nicht reicht* in der Feineinstellung ändern: *Wärmepumpe dreht höher* (ohne Therme, ihr COP sinkt) oder *Langsamer warm werden* (nur kleinste Leistung; die Heizung hilft erst, wenn ein Raum ein halbes Grad unter Soll liegt). Ohne zentrale Heizung dreht sie immer höher, was auch gewählt ist. Mehr Leistung gibt es mit *Schnell aufheizen*. Läuft sie, ohne dass ein Raum gerade Wärme braucht, steht im *Entscheidungsgrund* „Wärmepumpe läuft durch“ und nicht „Kein Raum braucht Wärme“ ([Einzelheiten](FUNKTIONSWEISE.md#am-minimum-halten)).

## 8. Wichtige Anzeigen

| Entität | Zeigt |
|---|---|
| *Entscheidungsgrund* | in Worten, warum das System tut, was es tut |
| *Empfohlene Wärmequelle* | Heizung (Therme oder Fernwärme), Wärmepumpe, beide oder keine |
| *Sicherheitsstatus* | OK, eingeschränkt (ein Sensor fehlt), Fallback (kein Raum oder keine verlässliche Raumtemperatur, nichts wird gesendet). Störungen der Wärmepumpe stehen in den Attributen, schränken den Status aber nur ein, wo die Wärmepumpe die einzige Wärmequelle ist. |
| *Solltemperatur* je Raum | den Sollwert und unter *Grund*, woher er kommt |
| *Thermostat-Plan* je Raum | was an das Thermostat geht |
| *Therme-Steuerbefehl* | was die Therme bekommt; *Drehknopf regelt* heißt, es wird nichts gesendet und die Therme läuft über ihren Knopf. Zeigt auch Heizungspumpe und Status S.xx |
| *Lernstatus* | was gelernt wurde und warum das Lernen pausiert |
| *Gas für Heizung*, *Gas für Warmwasser* | wie viel Gas (kWh) seit der Einrichtung in die Heizung und wie viel ins Warmwasser ging. Mit Gaszähler gemessen: Jeder Schritt des Zählers zählt für das, was die Therme in dem Moment tut; was verbrannt wurde, während Home Assistant nicht lief, steht unter *Nicht zugeordnet*. Ohne Gaszähler bei Vaillant-Thermen aus deren eigenen Zählern geschätzt ([Einrichtung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)). Beide eignen sich für das Energie-Dashboard |
| *Geplante Rückkehr* | wann du zurück bist; einstellbar (erst Datum, dann Uhrzeit), leer wenn nichts geplant ist. *Vorläufig (23:59 als Platzhalter)* (`provisional`) ist `true`, solange nur ein Datum gewählt ist: Abwesend endet dann um 23:59, vorgeheizt wird noch nicht |

**Detail-Sensoren sind anfangs ausgeschaltet.** Abweichung vom Soll, Temperaturtrend, Taupunkt und absolute Feuchte je Raum, die Spreizung Vorlauf/Rücklauf, die geschätzte Wärmeleistung der Therme und die geschätzte Luftmenge der Wärmepumpe ändern sich fast bei jedem Zyklus und dienen der Auswertung, nicht der Regelung. Jede Änderung ist eine Zeile in der Datenbank von Home Assistant, deshalb sind sie bei einer neuen Installation ausgeschaltet; einschalten unter *Einstellungen → Geräte & Dienste → Entitäten*. Berechnet werden sie so oder so.

## 9. Reparaturmeldungen

| Meldung | Bedeutung und Abhilfe |
|---|---|
| *Raum*: kein Heizfenster hinterlegt | weder Zeitplan-Helfer noch Zeitfenster – der Raum bleibt auf Absenkung. In den Raum-Optionen einen Zeitplan wählen. |
| *Raum*: Zeitplan-Helfer fehlt | der gewählte Zeitplan wurde gelöscht. Neu anlegen oder einen anderen wählen. |
| *Raum*: Heizkörperthermostat antwortet nicht | ein Ventil hat sich seit zweieinhalb Stunden nicht gemeldet, obwohl es Befehle bekam. Es hängt meist am Funk oder an der Batterie; der Raum fordert die Therme so lange nicht an. Batterie prüfen, Ventil neu anlernen oder aus- und einschalten. |
| Keine Daten von der Therme | ebusd liefert nichts Brauchbares; nichts wird gesendet, die Therme läuft über ihren Drehknopf. ebusd-App und Adapter prüfen. |
| Luftmenge der Wärmepumpe nicht kalibriert | keine Luftmengen-Kennlinie, daher kein COP; bis dahin gilt das Datenblatt oder, falls gewählt, die *COP-Werte dieses Modells*. |
| Wärmepumpe versorgt Räume ohne Schläuche | keine Schläuche eingetragen, die warme Luft bleibt im Aufstellraum. Schläuche montieren und Schlauchfaktor setzen oder die Räume abwählen. |

Manches zeigt sich ohne Meldung: Ein Raumfühler, der seit sechs Stunden still ist, zählt nicht mehr, die eigene Temperatur des Ventils springt ein (hinter Better Thermostat das Ventil selbst, nicht Better Thermostat, das den Raumfühler nur wiederholt), und der *Sicherheitsstatus* geht auf *Eingeschränkt*.

Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → *Diagnose herunterladen* liefert eine Datei mit Konfiguration, aktuellem Zustand und Lernwerten für Fehlerberichte. Es wird nichts daraus entfernt – vor dem Veröffentlichen durchsehen.

## 10. Häufige Fragen

Antworten auf häufige Fragen und was bei Problemen zu tun ist, stehen auf einer eigenen Seite: [Fehlersuche und häufige Fragen](FEHLERSUCHE.md).
