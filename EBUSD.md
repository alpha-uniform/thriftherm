# Weniger ebusd-Werte abfragen

[English](EBUSD.en.md) | **Deutsch**

Optional, für Fortgeschrittene: Hier kürzt du die Liste, die ebusd reihum abfragt, damit Vorlauf und Rücklauf öfter frisch sind. Die Seite gehört zur [Einrichtungsanleitung, Schritt 2.5](EINRICHTUNG.md#25-weniger-werte-abfragen-damit-vorlauf-und-rücklauf-frischer-sind).

## Warum die Länge der Liste zählt

ebusd fragt die Werte der Therme der Reihe nach ab, eine Nachricht alle paar Sekunden. Je mehr Nachrichten auf der Liste stehen, desto seltener kommt jede einzelne dran. Von sich aus stehen bei einer Vaillant-Therme rund 75 bis 80 darauf, und die meisten braucht niemand.

Thriftherm sorgt selbst dafür, dass seine fünf wichtigen Nachrichten (FlowTemp, ReturnTemp, Status01, WP, Statenumber) bevorzugt abgefragt werden; dafür musst du nichts einstellen. Zwischen den bevorzugten Runden arbeitet ebusd aber jedes Mal die ganze übrige Liste ab. Auf der Testanlage gemessen (12.10.2026, eine Abfrage alle 6 Sekunden): Ein bevorzugter Wert kommt fünfmal im Abstand von 30 Sekunden und pausiert dann so lange, wie die übrige Liste braucht.

| Übrige Nachrichten auf der Liste | Pause für Vorlauf und Rücklauf |
|---|---|
| etwa 75 (Auslieferungszustand) | etwa 8 Minuten |
| 35 (Testanlage bis 12.10.2026) | 4 Minuten |
| 13 (Testanlage nach dem Kürzen um die Liste unten) | 1 Minute 48 Sekunden |

Die erste Zeile ist aus dem gemessenen Muster hochgerechnet, die beiden anderen sind gemessen. Jede Nachricht weniger spart 6 Sekunden.

**Für die Regelung spielt die Pause keine Rolle.** Thriftherm liest seine fünf Werte einmal pro Minute selbst aus, egal wie lang die Liste ist: mit Gaszähler, sobald der Verbrauch zeigt, ohne Gaszähler durchgehend (denn dann verraten nur diese Werte, dass der Brenner angesprungen ist). Übrig bleibt die Pause mit Gaszähler bei ruhender Therme. Kürzen lohnt sich also, wenn du die Werte auch dann zeitnah sehen willst, etwa in einem Dashboard, und es entlastet den Bus.

## Was Thriftherm braucht

Diese Nachrichten nicht ausschließen:

| ebusd-Nachricht | Wofür |
|---|---|
| FlowTemp, ReturnTemp | Vorlauf und Rücklauf: Warmwasser-Erkennung, Lernen der Heizkurve |
| Status01 | Feld *pumpstate*: Heizbetrieb, Nachlauf, Warmwasser |
| WP | Heizungspumpe läuft |
| Statenumber | Status-Nummer S.xx: Heizung, Warmwasser oder Nachlauf |
| Status02 | Feld *hwcmode*: Warmwasser aktiv |
| SetMode | der Befehl an die Therme – nie ausschließen |
| PrEnergySumHc1 bis 3, PrEnergySumHwc1 bis 3 | nur ohne Gaszähler: Daraus schätzt Thriftherm das Gas für Heizung und für Warmwasser (siehe [unten](#was-auf-der-testanlage-niemand-braucht)) |

Dazu kommt *ebusd Signal* vom ebusd-Gerät selbst; das ist keine Abfrage an der Therme.

## Was sich zu behalten lohnt

Thriftherm liest diese Nachrichten nicht, nützlich sind sie trotzdem:

| ebusd-Nachricht | Wofür |
|---|---|
| HcHours, HcStarts, HwcHours, HwcStarts, PumpHours, HcPumpStarts | Betriebsstunden und Starts von Brenner und Pumpe, etwa für ein Dashboard |
| ExtFlowTempDesiredMin (d.09) | zeigt, welchen Sollwert die Therme angenommen hat; 90 heißt: sie regelt gerade nach ihrem Drehknopf |
| FlowTempDesired | der Sollwert, der gerade gilt |
| HcPumpMode (d.18) | belegt die Einstellung der Pumpe |
| StorageTemp | Temperatur auf der Warmwasser-Seite |
| HoursTillService | Stunden bis zur Wartung |

## Was auf der Testanlage niemand braucht

23 Nachrichten, von nichts gelesen und nirgends angezeigt:

- PrEnergyCountHc1 bis 3, PrEnergyCountHwc1 bis 3, PrEnergySumHc1 bis 3, PrEnergySumHwc1 bis 3 – zwölf Zähler ohne Einheit. **Hast du keinen Gaszähler, behalte die sechs PrEnergySum-Nachrichten:** Thriftherm schätzt daraus das Gas für Heizung und für Warmwasser ([Einrichtung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)). Sie summieren jede Sekunde die Brennerleistung in Prozent; die sechs PrEnergyCount-Nachrichten zählen nur diese Sekunden und können weg. Für diesen Fall hieße der Eintrag im Beispiel unten `^PrEnergyCount` statt `^PrEnergy`
- CounterStartattempts1 und 2, DeactivationsTemplimiter, WaterpressureMeasureCounter, ValveStarts, StorageLoadPumpHours, StorageloadPumpStarts, HcUnderHundredStarts, HwcUnderHundredStarts – neun weitere Zähler
- PumpPower (dauernd 0) und Templimiter

Eine Ausnahme, falls du Brennerstarts genau zählen willst: HcStarts und HwcStarts zählen in Hunderterschritten, HcUnderHundredStarts und HwcUnderHundredStarts liefern die letzten beiden Stellen dazu. Thriftherm braucht das nicht, es zählt die Brennerläufe selbst.

## Was auf der Testanlage schon ausgeschlossen ist

Seit dem 13.09.2026; die Runde wurde damit etwa halb so lang:

- HwcTemp, OutdoorstempSensor, Expertlevel_ReturnTemp – diese Fühler hat die Therme nicht, die Werte waren leer
- VortexFlowSensor, Status, Status16 – ebusd konnte sie für dieses Gerät nicht entschlüsseln
- PowerValue und die festen Einstellwerte (unter anderem FlowTempMax, HwcTempDesired, HwcTempMax, PumpPowerDesired, StorageTempDesired, ExternalFlowTempDesired, TempDiffFailure, TempGradientFailure, TimerInputHc)

Welche Nachrichten deine Therme liefert, hängt vom Gerät ab. Schau in Home Assistant auf das ebusd-Gerät: Was dauernd leer, 0 oder unverändert ist und dich nicht interessiert, kann weg. Einen Fehlercode oder den Wasserdruck liefert die Testanlage (atmoTEC) über ebusd nicht brauchbar; bei anderen Thermen kann das anders sein, dann lohnt es sich, diese Werte zu behalten.

## So schließt du Nachrichten aus

1. `mqtt-hassio.cfg` öffnen wie in [Einrichtung, Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen).
2. Die Zeile `filter-non-name` suchen oder anlegen. Ihr Wert ist eine Liste von Namen, jeder zwischen `^` und `$`, getrennt durch `|`. Groß- und Kleinschreibung spielt keine Rolle. Ohne das `$` am Ende trifft ein Eintrag alles, was so anfängt: `^PrEnergy` schließt alle zwölf PrEnergy-Zähler aus. Mehr kann der Filter nicht – Klammern oder `[12]` wie in anderen Suchmustern versteht er nicht, solche Einträge treffen einfach nichts. Beispiel für die 23 Nachrichten oben:

   ```
   filter-non-name = ^PrEnergy|^CounterStartAttempts1$|^CounterStartAttempts2$|^DeactivationsTemplimiter$|^WaterpressureMeasureCounter$|^ValveStarts$|^StorageLoadPumpHours$|^StorageLoadPumpStarts$|^HcUnderHundredStarts$|^HwcUnderHundredStarts$|^PumpPower$|^Templimiter$
   ```

   Steht in der Zeile schon etwas, hänge mit `|` an.
3. Speichern und die **ebusd-App neu starten**. Bis die ersten frischen Werte in Home Assistant stehen, vergehen einige Minuten; so lange zeigt Home Assistant die alten.

## Drei häufige Missverständnisse

- **Eine Entität in Home Assistant zu deaktivieren spart nichts.** ebusd fragt die Nachricht weiter ab. Nur der Filter nimmt sie von der Liste.
- **Gezählt wird je Nachricht, nicht je Entität.** Status01 und Status02 liefern jeweils mehrere Entitäten (Temperaturen, Pumpenzustand). Die überzähligen kannst du in Home Assistant deaktivieren, schneller wird dadurch nichts.
- **Ausgeschlossene Entitäten bleiben in Home Assistant stehen**, bis du sie löschst. Lässt sich eine nicht löschen, liegt ihre Ankündigung noch im MQTT-Broker: Sende dann eine leere Nachricht mit *Retain* an ihr Thema `homeassistant/sensor/ebusd_bai_<Name>_<Feld>/config` (Einstellungen → Geräte & Dienste → MQTT → Konfigurieren → Paket veröffentlichen).

## Schneller abfragen

`--pollinterval=2` in den Kommandozeilen-Optionen der ebusd-App halbiert bis drittelt alle Zeiten. Das ist auf der Testanlage nicht ausprobiert. Dort schlagen schon beim normalen Takt etwa 9 % der Abfragen fehl und werden wiederholt; ob ein dichterer Takt das verschlimmert, ist offen. Wer es versucht, schaut danach ins Protokoll der ebusd-App nach Zeilen mit `ERR:`.
