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
| **Abwesend** | alle Räume auf Abwesenheitstemperatur; mit Rückkehrzeit wird rechtzeitig vorgeheizt, siehe unten |

### Abwesend mit und ohne Rückkehr

- **Einfach abwesend, ohne Ende:** Betriebsmodus *Abwesend* wählen, während ein anderer Modus an ist. *Geplante Rückkehr* bleibt leer, die Räume bleiben auf Abwesenheitstemperatur, bis du *Abwesend beenden* oder einen anderen Modus wählst. Ist *Abwesend* mit Rückkehr schon an, ändert ein erneutes *Abwesend* nichts: erst einen anderen Modus wählen und dann wieder *Abwesend*, oder `thriftherm.set_away` mit `clear_return_time: true` aufrufen.
- **Abwesend bis zu einem Zeitpunkt**, z. B. heute 22:00 oder 3. Oktober 12:00: *Geplante Rückkehr* einstellen, das schaltet auf Abwesend bis dahin. In den Feldern von Home Assistant (Dialog der Entität, Entitäten-Karte) erst das **Datum**, dann die **Uhrzeit** wählen – solange nichts geplant ist, nimmt das Uhrzeitfeld nichts an.
- **Nur ein Datum ist ein Platzhalter:** Solange keine Uhrzeit gewählt ist, steht die Rückkehr vorläufig auf 23:59 des gewählten Tages, heute wie an jedem späteren Tag; ein anderes Datum nimmt die vorläufige 23:59 mit. Eine vorläufige Rückkehr heizt nicht vor; erst die Uhrzeit, die du danach wählst, gilt, und für sie wird rechtzeitig vorgeheizt. Wählst du keine, endet Abwesend trotzdem um 23:59. Ob die Rückkehr vorläufig ist, zeigt das Attribut *Vorläufig (23:59 als Platzhalter)* (`provisional`) an *Geplante Rückkehr*: `true`, solange nur das Datum gewählt ist.
- **Datum ändern, wenn schon eine Uhrzeit geplant ist:** Das neue Datum behält die geplante Uhrzeit und gilt sofort. Wählst du heute und ist diese Uhrzeit schon vorbei, steht wieder vorläufig 23:59 da.
- **Was abgelehnt wird:** eine heute schon vergangene Uhrzeit (außer 00:00 in der Minute nach einer erreichten Rückkehr: die wird zum Platzhalter 23:59, solange 23:59 noch bevorsteht), ein vergangener Tag und, wenn es schon nach 23:59 ist, das heutige Datum allein – jeweils mit einer Meldung.
- **Wirklich um 23:59 zurück:** Steht dort die vorläufige 23:59, schickt das Uhrzeitfeld für 23:59 nichts, weil sich nichts ändert, und eine 23:59, die trotzdem ankommt (etwa per `datetime.set_value`), bleibt vorläufig. Erst eine andere Minute wählen, z. B. 23:58, und 23:59 erst, wenn sie angezeigt wird und das Attribut *Vorläufig* auf `false` steht – oder `thriftherm.set_away` mit `return_time` nehmen, das Datum und Uhrzeit in einem Schritt setzt.
- **Browser in einer anderen Zeitzone:** Beide Felder zeigen und rechnen in der Zeitzone des Browsers; Datum und Uhrzeit gelten also in Browser-Zeit. Beispiele mit Home Assistant in Berlin:
  - Ohne geplante Rückkehr schickt der Datumsschritt Mitternacht des Browsers, in Home Assistant eine andere Uhrzeit (London: 01:00; östlich von Home Assistant am Vortag, Helsinki 23:00, Tokio 17:00, im Winter 16:00). Das ist dann kein Platzhalter, sondern eine echte Zeit: Ist sie schon vorbei, wie für heute meist, wird sie abgelehnt; sonst gilt sie als Rückkehr zu dieser Stunde, mit Vorheizen.
  - Mit geplanter Rückkehr gilt eine im Uhrzeitfeld gewählte Uhrzeit in Browser-Zeit (in London gewählte 12:00 sind in Berlin 13:00), und auch der Tag kann sich verschieben: Östlich von Home Assistant erscheint die vorläufige 23:59 schon als nächster Tag (Helsinki: 00:59), eine dann gewählte Uhrzeit landet einen Tag später. Westlich kann das Rückkehrzeiten kurz nach Mitternacht treffen (3. Oktober 00:30, in London auf den 5. Oktober gelegt: 6. Oktober 00:30).
  - Stellen Browser und Home Assistant nicht am selben Tag auf Sommer- oder Winterzeit um – etwa ein Browser in den USA oder in einer Zone ohne Sommerzeit wie Dubai –, kann ein reiner Datumsschritt über eine Umstellung hinweg die Uhrzeit um eine Stunde verschieben. Eine vorläufige Rückkehr wird dann echt, und es wird vorgeheizt (vorläufig 26. Oktober 23:59, in New York auf den 3. November gelegt: 4. November 00:59).
  - Deshalb nach jeder Eingabe *Geplante Rückkehr* und das Attribut *Vorläufig* prüfen, den Browser auf die Zeitzone von Home Assistant stellen oder `thriftherm.set_away` nehmen.
- **Aus Automationen:** `thriftherm.set_away` mit `return_time`, oder mit `clear_return_time: true` ohne Ende. Dort ist jede Zeit in der Vergangenheit ein Fehler, auch eine von heute, und jede Zeit gilt so, wie sie kommt, auch Mitternacht. `datetime.set_value` auf *Geplante Rückkehr* liest Werte dagegen wie die Felder: Mitternacht ohne geplante Rückkehr wird zum Platzhalter 23:59, auch an einem späteren Tag. Und solange *Geplante Rückkehr* nicht verfügbar ist (Thriftherm nicht geladen), verwirft Home Assistant `datetime.set_value` ohne Fehlermeldung, `set_away` meldet dagegen einen Fehler. Automationen nehmen deshalb `set_away`.
- **Wieder da:** Die Aktion *Abwesend beenden* (`thriftherm.clear_away`) beendet Abwesenheit und Rückkehr. Ist die Rückkehrzeit erreicht, geht es von selbst zurück auf Automatik.

## 3. Welche Temperatur gilt

Für jeden Raum gilt die erste zutreffende Regel:

1. **Übersteuerung** (Aktion `set_override` oder eine Änderung von Hand am Thermostat, siehe unten) – bis sie abläuft.
2. **Sommerbetrieb** – nur Frostschutztemperatur.
3. **Abwesend** – Abwesenheitstemperatur (Standard 15 °C). Vor einer Rückkehr mit gewählter Uhrzeit wird auf Komfort vorgeheizt (nicht bei der vorläufigen 23:59, siehe Abschnitt 2). Kommt die Luft ihrem Taupunkt zu nahe (Standard 3 K), wird der Sollwert gegen Feuchte angehoben.
4. **Zeitplan** – Komforttemperatur in einem Zeitplan-Block, sonst Absenktemperatur. Vor Blockbeginn wird so vorgeheizt, dass es *zu Beginn* warm ist.

### Änderungen von Hand

Thriftherm schreibt Sollwerte und muss deshalb wissen, wer einen Sollwert geändert hat. Das sagt Home Assistant selbst: Jede Änderung trägt mit, woher sie kommt.

| Woher | Beispiel | Gilt als Änderung von Hand? |
|---|---|---|
| Thriftherm selbst | der eigene Befehl und die Antwort des Geräts darauf | nie |
| Jemand in Home Assistant | Dashboard, App, Sprachassistent | immer, sofort |
| Eine Automation oder ein Skript | „vor dem Duschen 23 °C“, auch nach der Uhr | immer, sofort |
| Am Gerät | Drehknopf am Ventil, Fernbedienung der Klimaanlage, App des Herstellers; auch was sich änderte, während das Gerät nicht erreichbar war | ja – außer das Gerät erklärt die Änderung selbst (eigene Regel von Versatile Thermostat, verspätetes Funk-Echo bei Better Thermostat). An der Wärmepumpe erst, wenn sie zwei Minuten Zeit hatte, einem Befehl zu folgen |

Was daraus folgt, ist für alle Geräte gleich:

- **Sie gilt eine feste Zeit** (Parameter *Wie lange eine Änderung von Hand gilt*, Standard 120 Minuten). Am Raumthermostat wird sie zur Übersteuerung des Raums; an der Wärmepumpe lässt Thriftherm das Gerät so lange in Ruhe. Eine weitere Änderung in dieser Zeit verlängert sie.
- **Sie wird angezeigt:** am Sollwert-Sensor des Raums (*Übersteuert bis*, *Übersteuert durch*) und am Steuerbefehl der Wärmepumpe (*Von Hand geändert, gilt bis*, *Von Hand geändert durch*).
- **Sie endet von selbst** oder mit dem Knopf *Zurück zur Automatik* (Aktion `thriftherm.clear_override` ohne Raum): Das beendet alle Übersteuerungen, jedes Schnell-Aufheizen und die Ruhezeit der Wärmepumpe auf einmal.

Was keine Änderung von Hand ist:

- **Der Befehl kam nicht an.** Zeigt die Wärmepumpe nach einem Befehl etwas anderes, ohne dass es jemand geändert hat, wird der Befehl wiederholt: nach 2, 4, 8 … Minuten, höchstens stündlich, solange das Gerät ihn nicht ausführt. Der Steuerbefehl zeigt, wie oft in Folge (*Befehle in Folge, die das Gerät nicht ausgeführt hat*). Nur wenn Home Assistant zwischen Befehl und Abweichung neu gestartet wurde, lässt sich das nicht mehr unterscheiden; dann gilt die Abweichung wie bisher als Änderung am Gerät.
- **Das Thermostat rundet.** Antwortet ein Thermostat in ganzen Grad auf 21,5 °C mit 22 °C, ist das unser Sollwert, so wie es ihn halten kann – keine Übersteuerung, und es wird nicht dagegen geschrieben.
- **Wer ein Thermostat nur ausschaltet,** verlangt keine Temperatur: Daraus wird keine Übersteuerung.

Eine Grenze hat das: Schreibt ein Gerät seinen Zustand wenige Sekunden nach einem Befehl, gibt Home Assistant diese Änderung als die des Befehls aus, was auch immer sich geändert hat. Ein Druck auf die Fernbedienung in genau diesen Sekunden zählt deshalb zu dem, der zuletzt befohlen hat.

Kühlt, entfeuchtet oder lüftet jemand mit der Wärmepumpe, bleibt Thriftherm draußen, solange dieser Modus an ist. Eine Therme über Entitäten (Abschnitt 5) hat keine Handänderung: Solange die Steuerung aktiv ist, gehören Zahl und Schalter ihr; wer selbst stellen will, schaltet auf *Nur planen*.

Zwei Schutzregeln gelten immer zusätzlich:

| Schutz | Standard | Verhalten |
|---|---|---|
| Frostschutz | 7 °C | Fällt ein Raum darunter, heizt die Therme sofort – auch im Sommerbetrieb – bis der Raum 1 K darüber liegt. |
| Fenster offen | 90 s Karenz | Solange ein Fensterkontakt offen ist, pausiert das Heizen in diesem Raum. |

Komfort- und Absenktemperatur sind Zahlen-Entitäten je Raum (*Komforttemperatur*, *Absenktemperatur*) und lassen sich im Dashboard ändern. Die Absenktemperatur kann nie über der Komforttemperatur liegen: Drehst du Komfort darunter, geht die Absenkung mit. Änderst du später eine der beiden in den Raum-Optionen, gelten wieder beide Werte aus den Optionen.

### Zeitpläne

Jeder Raum kann einen **Zeitplan-Helfer** von Home Assistant nutzen (Einstellungen → Geräte & Dienste → Helfer → Zeitplan). Die Blöcke des Helfers sind die Komfortzeiten. Ein Block kann ein Attribut `temperature` tragen, um eine andere Temperatur als die Komforttemperatur zu verwenden. Räume ohne Helfer nutzen die Zeitfenster aus den Raum-Optionen.

### Ohne festen Zeitplan

Wer lieber von Hand regelt als nach Uhrzeit:

- **Rund um die Uhr Komfort:** Zeitfenster `00:00-23:59` an Werktagen und am Wochenende, oder ein Zeitplan-Helfer mit Blöcken von 00:00 bis 24:00. Beides gilt als durchgehend, auch über Mitternacht.
- **Temperatur verstellen:** die Zahl *Komforttemperatur* des Raums ändern, etwa über eine Thermostat-Karte oder ein Template-Thermostat. Die Absenkung geht mit, wenn du darunter drehst.
- **Abwesend/Zuhause:** `thriftherm.set_away` (mit `clear_return_time: true` ohne Ende, mit `return_time` samt Vorheizen) und `thriftherm.clear_away`. Die Komforttemperaturen bleiben dabei stehen.

Für das Lernen der Heizkurve ist das sogar günstig: Sie lernt aus langen, gleichmäßigen Heizläufen. Jede Verstellung pausiert das Lernen 20 Minuten (siehe [Abschnitt 6](#6-lernen)), gelernt bleibt alles.

### Vorheizen

Beginn = Blockbeginn − (Soll − Ist) / Aufheizrate − Sicherheitszuschlag. Die Aufheizrate lernt Thriftherm je Raum; bis dahin gelten 0,8 K/h für Heizkörperräume und 1,2 K/h für Wärmepumpen-Räume, mit 20 Minuten Zuschlag. Der Solltemperatur-Sensor zeigt den geplanten Beginn unter *Vorheizen beginnt*.

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

- **Vorlauftemperatur** aus einer Zwei-Punkt-Heizkurve (Standard 55 °C bei −10 °C, 30 °C bei +15 °C), bei großem Wärmebedarf der Räume bis 8 K höher, dazu eine gelernte Korrektur. Sie bleibt immer zwischen eingestelltem Minimum und Maximum.
  **Warum das Minimum vom Gerätetyp abhängt.** Ein Brennwertgerät profitiert von niedrigem Vorlauf: Liegt der Rücklauf unter etwa 56 °C, kondensiert das Abgas und bringt bis zu 11 % mehr, etwa 30 °C sind also ein gutes Minimum. Ein Heizwertgerät kondensiert nie, dort spart ein sehr niedriger Vorlauf wenig – und bei kurzen Brennerläufen kommt die Wärme womöglich gar nicht bis zu den fernen Heizkörpern. Gemessen an der getesteten Heizwerttherme: Mit 30 °C blieb der letzte Heizkörper kalt, 45 °C funktioniert, dort also mit etwa 45 °C beginnen. Die Einstellung heißt *Minimale Vorlauftemperatur* ([Einrichtungsanleitung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).
- **Wann ein Raum die Therme anfordert:** ab 0,3 K unter Soll, bis er weniger als 0,1 K darunter liegt. Hängt ein Raum nach einer Stunde Heizen knapp unter Soll (höchstens 0,3 K) und steigt kaum noch (unter 0,15 K/h), gilt er als erreicht, bis er 0,5 K unter Soll fällt oder sich der Sollwert ändert. Sinkt der Sollwert (z. B. eine Übersteuerung endet), wird eine laufende Anforderung neu mit der Startschwelle bewertet. In der letzten halben Stunde einer Komfortzeit fordert kein Raum mehr an, die Wärme käme zu spät. *Schnell aufheizen* ist von beiden Regeln ausgenommen. Den Zustand zeigt das Attribut *Anforderung an die Therme* am Sensor *Wärmebedarf* des Raums.
- **Mitheizen:** Läuft die Therme ohnehin, bekommen andere Räume in ihrer Komfortzeit, die unter Soll liegen, 0,5 K mehr, und ein Raum, dessen Vorheizen in der nächsten Stunde begänne, fängt jetzt an (Grund *Heizt mit* bzw. *Heizt früher vor*). Ein einzelner Heizkörper nimmt nur etwa 1 kW ab, die Therme brennt aber mit mindestens 8 kW; mehr offene Heizkörper bedeuten längere Brennerläufe und weniger Starts. Mitheizende Räume fordern selbst nichts an und halten die Therme nicht am Laufen.
- **Heizbetrieb gesperrt** (`disablehc`), wenn kein Raum Wärme braucht. Warmwasser wird nicht angefasst: Der Warmwasser-Sollwert wird immer als „kein Wert“ gesendet, die Therme folgt weiter ihrem Drehknopf. Die einzige Ausnahme wählst du selbst: Ist *Warmwasser bereithalten* in den Thermen-Optionen ausgeschaltet, heizt die Therme nicht mehr für Warmwasser, solange kein Hahn offen ist (das stündliche Warmhalten einer Kombitherme); mit Warmwasserspeicher eingeschaltet lassen.
- **Ein von Hand ausgeschaltetes Thermostat fordert keine Therme an:** Sein Ventil ist zu, der Raum zählt beim Bedarf nicht mit. Ein nur nicht erreichbares Thermostat zählt weiter, denn sein Ventil regelt selbst weiter.
- **Ein Heizkörperthermostat, das über zweieinhalb Stunden schweigt, fordert ebenfalls keine Therme an.** Es behält seinen letzten Sollwert und nimmt keine neuen an. Bei Better Thermostat wird das echte Thermostat dahinter überwacht, nicht BT selbst. Dazu erscheint eine Reparaturmeldung in HA. Die Frist lässt Platz für Thermostate, die sich nur etwa stündlich melden (Zigbee-Standard); an der Testanlage melden sie sich mindestens alle 15 Minuten. Als Lebenszeichen zählt jede Änderung an einer Entität des Thermostats; eine Meldung mit unveränderten Werten hinterlässt in Home Assistant keine Spur. Meldet Thriftherm ein gesundes Thermostat als stumm, in Zigbee2MQTT dessen Entität *Last seen* einschalten: Sie ändert sich mit jeder Meldung.
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
| Therme | Heizkurven-Korrektur in zwei Teilen, je ±10 K: **Höhe** (gilt bei jeder Außentemperatur) und **Steigung** (voll bei −10 °C, gar nicht bei +15 °C) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum (K/h) | echte Heizphasen bei geschlossenem Fenster |
| Räume | Auskühlkoeffizient je Raum (1/h) | Phasen ohne Heizen, Fenster zu, drinnen mindestens 5 K wärmer als draußen |
| Räume | Heizleistung je Raum (K/h ohne Verluste) | Aufheizrate plus die Verluste während der Episode; anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe (Vorschau) | COP-Kennfeld über der Außentemperatur, Sollwert-Korrektur | gemessene COP-Läufe |

**Höhe und Steigung:** Ein Lernschritt an einem milden Tag verschiebt vor allem die Höhe der Kurve, einer bei Kälte vor allem die Steigung – so, wie man eine Heizkurve von Hand einstellt (nur im Winter zu kalt: steiler; in der Übergangszeit zu kalt: höher). Was im Herbst gelernt wird, verstellt deshalb den Winter kaum. Der Lernstatus zeigt beide Teile und die Summe bei der aktuellen Außentemperatur.

**Wie viele Räume anfordern:** Ein einzelner offener Heizkörper schickt das Wasser fast so warm zurück, wie es kam. Die Spreizung bewertet die Kurve deshalb nur, wenn mindestens die Hälfte der Räume, die gerade Wärme abnehmen können, anfordert (einstellbar, *Anteil der Räume …* in den Thermen-Optionen); darunter zählt allein, ob die anfordernden Räume vorankommen. Räume, die nur mitheizen, zählen nicht als anfordernd. Der Lernstatus zeigt *Anfordernde Räume* und ob die Spreizung gerade bewertet.

**Eine Heizkurve je Lastfall (Kennfeld):** Welchen Vorlauf die Wohnung braucht, hängt von der Außentemperatur ab und davon, wie viele Heizkörper Wärme abnehmen. Thriftherm führt deshalb für drei Lastfälle – ein Raum fordert an, wenige, die Mehrheit – je eine eigene Korrektur der Heizkurve mit Höhe und Steigung. Sechs Zahlen sagen also, welcher Vorlauf zu welcher Außentemperatur und Raumzahl passt.

- **Ein Lernschritt verändert nur den Lastfall, in dem er gemacht wurde.** Gemessen am 11.10.2026: Das Bad allein trieb die gemeinsame Korrektur auf +4 K, und drei Räume zusammen starteten danach von dort.
- **Ein Lastfall ohne eigenen Wert** startet mit dem des nächstgelegenen, sonst mit der bisherigen gemeinsamen Korrektur.
- **Ein Wert darf gefunden sein:** Hat ein Lastfall drei Betriebsstunden ohne hängenden Raum und ohne Lernschritt hinter sich, bleibt sein Wert stehen. Angehoben wird er erst wieder, wenn ein Raum 45 Minuten lang sein Soll nicht erreicht, gesenkt erst, wenn die Räume es zwei Stunden lang zulassen; das darf jeweils bis zu 45 Minuten aussetzen, ohne von vorn zu beginnen. Zu den drei Stunden zählt nur Zeit, in der ein Raum angefordert hat und schon eine halbe Stunde Zeit hatte. Wartezeit und Tagesgrenzen der Lernschritte gelten für alle Lastfälle gemeinsam. Ohne das beantwortet das Lernen jede kleine Abweichung, und der Vorlauf geht dauernd hoch und runter.
- **Ein neuer Lastfall zählt erst nach zehn Minuten.** Fordert ein dritter Raum mal an, mal nicht, würde der Vorlauf sonst im Minutentakt wechseln. Wechselt der Lastfall, ändert sich der Vorlauf um den Unterschied der beiden Kurven; die Vorlaufgrenzen gelten immer.
- Der Lernstatus zeigt den *Lastfall jetzt* und unter *Heizkurven-Korrektur je Lastfall* für jeden Fall Korrektur, Höhe, Steigung, ob er gefunden ist, seine Betriebsstunden und wie oft dabei ein Raum hing.
- Der Schalter *Eigene Heizkurve für einen Raum, wenige Räume und die Mehrheit* in den Thermen-Optionen stellt auf eine gemeinsame Korrektur für alle zurück; die Kurven der Lastfälle bleiben dabei erhalten.

**Woran der Brenner erkannt wird:** am besten Zeichen, das die Anlage hat – Gaszähler, sonst Statuscode der Therme, sonst ihr Pumpenstatus. Ein Gaszähler ist nicht nötig. Brennerläufe für Warmwasser zählen nie mit.

**Im Taktbetrieb entscheiden die Räume:** Eine taktende Therme liefert nie eine ruhige Spreizung. Hängt dabei ein angeforderter Raum mindestens 0,2 K unter Soll und steigt langsamer als 0,2 K/h, wird die Kurve um eine Stufe angehoben (Grund *Therme taktet und ein Raum erreicht sein Soll nicht*). Gesenkt wird im Taktbetrieb nur, wenn alle angeforderten Räume zügig warm werden (ab 0,5 K/h). Gemessen am 10.10.2026: Mit 35 °C Mindest-Vorlauf lief der Brenner nur noch 41 s alle 15 min, das Bad blieb 14 Stunden knapp unter Soll.

Der Sensor *Therme Brenneranteil* zeigt, welchen Anteil der letzten Stunde der Brenner für die Heizung lief, dazu die Zahl der Starts. Er schreibt vorerst nur mit.

**Taktbetrieb zählt ebenfalls:** Brennt die Therme eine Minute und wartet dann eine Viertelstunde, liefert sie weit mehr, als die Räume abnehmen — der Vorlauf ist zu heiß. Drei solche Läufe innerhalb einer Stunde senken die Kurve um eine Stufe. Die Spreizung käme in so kurzen Läufen nie zur Ruhe, deshalb ist das im Übergangswetter das einzige brauchbare Signal. Unter den Mindest-Vorlauf geht sie dabei nie.

Schutz gegen falsches Lernen: Die Therme lernt nur im Modus *Aktiv*, frühestens 10 Minuten nach Brennerstart, aus mindestens 10 Messwerten, vor einem Schritt nach oben mindestens 45 Minuten und vor einem nach unten eine Stunde seit dem letzten, je Tag höchstens 10 K nach oben und 4 K nach unten, nie während Warmwasser, einer Sollwertänderung in den letzten 20 Minuten, Schnell-Aufheizen, Trocknung oder wenn der Sicherheitsstatus nicht OK ist. **Die Schritte wachsen mit der Gewissheit:** Folgt ein Schritt nach oben auf einen nach oben, verdoppelt er sich (1, 2, 4 K, mehr nicht); geht ein Schritt gegen den vorigen, halbiert er sich bis auf 0,5 K – erst grob, dann fein. Schritte nach unten wachsen nie: Komfort geht vor Sparen. Kein Schritt geht weiter, als der Vorlauf wegen seiner Grenzen folgen kann. Nach drei Stunden ohne Schritt beginnt eine Folge wieder beim ersten Schritt: 1 K im **Einlernen**, 0,5 K im **Verfeinern** (nach sechs Änderungen oder einem ruhigen Tag). Gemessen am 11.10.2026: Mit festen 1-K-Schritten, vier am Tag, stand die Korrektur nach acht Stunden bei +4 K und das Bad noch 0,6 K unter Soll.

Der Sensor *Lernstatus* zeigt Phase, aktuelle Korrektur, Pausengrund und das letzte Zurücksetzen. Pausiert er wegen einer Sollwertänderung, steht dort auch, bis wann und in welchem Raum.

### Gelerntes vergessen

Nach einer Änderung an der Anlage passen alte Werte nicht mehr.

- **Von Hand:** Optionen → *Lernen zurücksetzen*, Bereiche (und Räume) ankreuzen, oder Dienst `thriftherm.reset_learning`.
- **Automatisch:** Ändert sich eine Einstellung, von der ein Bereich abhängt, wird nur dieser Bereich vergessen:

| Änderung | Vergessen wird |
|---|---|
| Heizkurve | Thermen-Korrektur |
| Wärmepumpen-Entität, Ansaug-/Ausblassensor, Luftmengen-Kennlinie, Schlauchfaktor, Aufstellraum, beheizte Räume | COP-Kennfeld und Wärmepumpen-Korrektur |
| Temperatursensor oder Thermostat eines Raums | Aufheizrate dieses Raums |

Laufzustand, Sperrzeiten und Handbedienung werden nie zurückgesetzt – ein Zurücksetzen kann also kein Takten auslösen.

## 7. Wärmepumpe (optional)

> **Vorschau.** Seit dem 11.10.2026 steuert sie eine Midea PortaSplit aktiv, bisher aber erst wenige Stunden bei mildem Wetter; noch nicht darauf verlassen. Die Punkte unten beschreiben, was sie können soll.

- Der COP wird aus der Luftmenge (Lüfterdrehzahl → m³/h-Kennlinie) sowie Temperatur und Feuchte von Ansaug- und Ausblasluft gemessen, erst wenn das Gerät eingeschwungen ist.
- Taktet die Wärmepumpe, weil ihr Sollwert erreicht ist, gibt es keinen eingeschwungenen Wert. Dann zählt der ganze Takt: Wärme und Strom werden von einem Verdichterstart bis zum nächsten aufsummiert, die Pause mit ihrem Standby-Strom eingeschlossen. Dieser COP (Attribut *COP über den letzten Takt*) ist niedriger als der im Dauerlauf, aber er ist der, der die Kosten bestimmt.
- Der **Schlauchfaktor** korrigiert die Luftmenge, nicht die Wärme: Gegen den Widerstand der Schläuche fördert der Lüfter bei gleicher Drehzahl weniger Luft. Was die Schläuche unterwegs an Wärme abgeben, bleibt in der Wohnung und ist kein Verlust.
- Verglichen wird mit dem Preis der zentralen Wärme. Bei Fernwärme zählt der **Heizkostenschlüssel**: Nur der Verbrauchsanteil folgt dem eigenen Zähler, eine gesparte kWh spart also weniger als ihr Preis, und die Wärmepumpe braucht einen höheren COP, damit sie sich lohnt.
- Die Wärmepumpe läuft langsam mit hohem COP. Gesperrt ist sie unter ihrer Mindest-Außentemperatur (Standard −10 °C), bei erkannter Vereisung oder wenn jemand damit kühlt. Kühlen und Heizen überschneiden sich nie: Solange sie kühlt, bleiben die Heizkörper der Räume, die sie bedient, auf Absenktemperatur und fordern keine Wärme von der Therme an. Der Frostschutz gilt weiter.
- **Der Aufstellort zählt.** Ohne Schläuche bleibt die warme Luft im Aufstellraum. Aufstellraum und Schlauchfaktor (1,0 = keine Schläuche) eintragen; nicht erreichbare Räume erzeugen eine Reparaturmeldung.
- **Bad-Trocknung** braucht die Wärmepumpe: nur in einem Raum, den die Wärmepumpe beheizt und der einen Feuchtesensor hat; das Raumformular bietet sie erst an, wenn eine Wärmepumpe eingerichtet ist. Nach dem Duschen trocknet die Wärmepumpe den Raum – nur solange sie heizen und effizient laufen kann, höchstens 60 Minuten und nie über Soll + 1,5 K. Sonst folgt der Heizkörper der normalen Fensterlogik.
- **Schnell aufheizen** (Button je Raum oder `thriftherm.boost`) lässt sie für begrenzte Zeit mit voller Leistung laufen.
- **Lüfter-Nachlauf:** Thriftherm schaltet die Wärmepumpe nie direkt vom Heizen auf Aus. Sie läuft erst 15 Minuten nur mit dem Lüfter (Parameter, 0 = sofort aus) und wird danach ausgeschaltet. Stellt in dieser Zeit jemand von Hand einen anderen Modus ein, endet der Nachlauf.
- **Sollwert folgt den Räumen** (Schalter im Wärmepumpen-Formular): Die Wärmepumpe bekommt die höchste Solltemperatur der von ihr versorgten Räume plus den Sollwert-Abstand (Standard 1 K) und regelt selbst. Komfort 21 °C ergibt 22 °C am Gerät, die Nachtabsenkung auf 17 °C ergibt 18 °C. Sie bleibt Tag und Nacht an, solange sie die günstigere Wärmequelle ist, und wird nur ausgeschaltet, solange niemand die versorgten Räume warm haben will (abwesend, aus, in jedem ein Fenster offen). *Schnell aufheizen* stellt dann nur den Lüfter hoch. Hat die Ansaugluft den Sollwert erreicht, heizt das Gerät von sich aus nicht mehr; fehlt einem versorgten Raum dann noch Wärme, bringt sie die Therme. Ohne den Schalter nutzt Thriftherm den Sollwert als Leistungshebel (Ansaugluft plus gelernter Abstand) und schaltet nach dem Bedarf der Räume.

## 8. Wichtige Anzeigen

| Entität | Zeigt |
|---|---|
| *Entscheidungsgrund* | in Worten, warum das System tut, was es tut |
| *Empfohlene Wärmequelle* | Therme, Wärmepumpe, beide oder keine |
| *Sicherheitsstatus* | OK, eingeschränkt (ein Sensor fehlt), Fallback (kein Raum oder keine verlässliche Raumtemperatur, nichts wird gesendet). Störungen der Wärmepumpe stehen in den Attributen, schränken den Status aber nur ein, wo die Wärmepumpe die einzige Wärmequelle ist. |
| *Solltemperatur* je Raum | den Sollwert und unter *Grund*, woher er kommt |
| *Thermostat-Plan* je Raum | was an das Thermostat geht |
| *Therme-Steuerbefehl* | was die Therme bekommt; *Drehknopf regelt* heißt, es wird nichts gesendet und die Therme läuft über ihren Knopf. Zeigt auch Heizungspumpe und Status S.xx |
| *Lernstatus* | was gelernt wurde und warum das Lernen pausiert |
| *Geplante Rückkehr* | wann du zurück bist; einstellbar (erst Datum, dann Uhrzeit), leer wenn nichts geplant ist. *Vorläufig (23:59 als Platzhalter)* (`provisional`) ist `true`, solange nur ein Datum gewählt ist: Abwesend endet dann um 23:59, vorgeheizt wird noch nicht |

**Detail-Sensoren sind anfangs ausgeschaltet.** Abweichung vom Soll, Temperaturtrend, Taupunkt und absolute Feuchte je Raum, die Spreizung Vorlauf/Rücklauf, die geschätzte Wärmeleistung der Therme und die geschätzte Luftmenge der Wärmepumpe ändern sich fast bei jedem Zyklus und dienen der Auswertung, nicht der Regelung. Jede Änderung ist eine Zeile in der Datenbank von Home Assistant, deshalb sind sie bei einer neuen Installation ausgeschaltet; einschalten unter *Einstellungen → Geräte & Dienste → Entitäten*. Berechnet werden sie so oder so.

## 9. Reparaturmeldungen

| Meldung | Bedeutung und Abhilfe |
|---|---|
| *Raum*: kein Heizfenster hinterlegt | weder Zeitplan-Helfer noch Zeitfenster – der Raum bleibt auf Absenkung. In den Raum-Optionen einen Zeitplan wählen. |
| *Raum*: Zeitplan-Helfer fehlt | der gewählte Zeitplan wurde gelöscht. Neu anlegen oder einen anderen wählen. |
| Keine Daten von der Therme | ebusd liefert nichts Brauchbares; nichts wird gesendet, die Therme läuft über ihren Drehknopf. ebusd-App und Adapter prüfen. |
| Luftmenge der Wärmepumpe nicht kalibriert | keine Luftmengen-Kennlinie, daher kein COP; bis dahin gilt das Datenblatt. |
| Wärmepumpe versorgt Räume ohne Schläuche | keine Schläuche eingetragen, die warme Luft bleibt im Aufstellraum. Schläuche montieren und Schlauchfaktor setzen oder die Räume abwählen. |

Manches zeigt sich ohne Meldung: Ein Raumfühler, der seit sechs Stunden still ist, zählt nicht mehr, die eigene Temperatur des Ventils springt ein (hinter Better Thermostat das Ventil selbst, nicht Better Thermostat, das den Raumfühler nur wiederholt), und der *Sicherheitsstatus* geht auf *Eingeschränkt*.

Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → *Diagnose herunterladen* liefert eine Datei mit Konfiguration, aktuellem Zustand und Lernwerten für Fehlerberichte. Es wird nichts daraus entfernt – vor dem Veröffentlichen durchsehen.

## 10. Häufige Fragen

**Warum steht ein Raum auf 15 °C?** Er ist im Modus Abwesend. Der Frostschutz (7 °C) ist eine eigene, tiefere Grenze.

**Ich habe am Thermostat gedreht – warum stellt es sich nicht zurück?** Eine Handänderung wird zur vorübergehenden Übersteuerung (Standard 120 Minuten). Mit dem Knopf *Zurück zur Automatik* oder `clear_override` beenden; mehr unter *Änderungen von Hand* in Abschnitt 3.

**Übersteuerungen, obwohl niemand am Thermostat war?** Better Thermostat schreibt den Sollwert des Heizkörperthermostats alle paar Sekunden neu. Kommt über Zigbee eine Antwort verspätet an, hält BT den alten Wert für einen Dreh am Thermostat. Der **Echo-Filter** (Optionen → Regelparameter, standardmäßig an) erkennt das: Springt BT auf einen Wert, den das Thermostat in der letzten Minute schon hatte und wieder verlassen hatte (z. B. 16 → 16,5 → 16), wird das ignoriert und der geplante Sollwert neu gesendet. Ein echter Dreh bringt einen neuen Wert und wird übernommen. Wann zuletzt ein Echo ignoriert wurde, zeigt der Sensor *Thermostat-Plan* des Raums.

**Verändert Thriftherm meine Warmwassertemperatur?** Nein. Für den Warmwasser-Sollwert wird „kein Wert“ gesendet; der Drehknopf der Therme entscheidet.

**Was passiert, wenn Home Assistant abstürzt?** Die Therme fällt innerhalb von 9–16 Minuten auf ihren Drehknopf zurück; die Thermostate behalten ihren letzten Sollwert.

**Was passiert, wenn ebusd die Therme nicht erreicht?** Nach zwei Minuten ohne *ebusd Signal* sendet Thriftherm nichts mehr (*Therme-Steuerbefehl*: *Keine Vorgabe*), die Therme heizt nach ihrem Drehknopf. Nach zehn Minuten erscheint die Reparaturmeldung *Keine Daten von der Therme*. Häufigste Ursache ist eine neue IP-Adresse des eBUS-Adapters, deshalb im Router eine feste Adresse vergeben ([Einrichtung, Schritt 2.3](EINRICHTUNG.md#23-ebusd-app-konfigurieren)). Achtung: Die eBUS-Werte in Home Assistant bleiben während eines Ausfalls auf ihrem letzten Stand stehen, maßgeblich ist nur *ebusd Signal*.

**Warum ist der *Temperaturtrend* nach einem Neustart leer?** Der Trend braucht etwa 30 Minuten Messwerte. Bei einer neuen Installation ist der Sensor ausgeschaltet, bis du ihn einschaltest (Abschnitt 8).
