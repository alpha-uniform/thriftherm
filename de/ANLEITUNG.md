# Thriftherm – Anleitung

[English](../en/GUIDE.md) | **Deutsch**

> **Du richtest Thriftherm zum ersten Mal ein?** Fang mit der [Einrichtungsanleitung](EINRICHTUNG.md) an.

Diese Anleitung erklärt den Alltag: was die Modi tun, welche Temperatur wann gilt, was Thriftherm lernt und wie lange das dauert, und was bei einer Reparaturmeldung zu tun ist. Idee und Hardware stehen im [README](../README.md), alle Schwellen und Zeiten in [Wie Thriftherm rechnet](FUNKTIONSWEISE.md), Hilfe bei Problemen in der [Fehlersuche](FEHLERSUCHE.md). Fachbegriffe wie *Vorlauf* oder *COP* erklärt der Abschnitt [Begriffe](#begriffe).

**Inhalt:** [Wie alles zusammenhängt](#wie-alles-zusammenhängt) · [Betriebsmodi](#betriebsmodi) · [Welche Temperatur gilt](#welche-temperatur-gilt) · [Änderungen von Hand](#änderungen-von-hand) · [Raumthermostate](#raumthermostate) · [Thermensteuerung](#thermensteuerung) · [Lernen](#lernen) · [Wärmepumpe](#wärmepumpe-optional) · [Dienste](#dienste) · [Wichtige Anzeigen](#wichtige-anzeigen) · [Reparaturmeldungen](#reparaturmeldungen) · [Begriffe](#begriffe)

**Sprache.** Ist Home Assistant auf Deutsch eingestellt, spricht Thriftherm Deutsch, bei jeder anderen Sprache Englisch: Entitätsnamen, Zustände, Attribute, Entscheidungsgrund, Dialoge, Reparatur- und Fehlermeldungen. Die Entitäts-IDs sind immer englisch (`sensor.thriftherm_boiler_command`), damit Automationen und Dashboards in jeder Sprache funktionieren.

## Wie alles zusammenhängt

| Teil | Aufgabe | Braucht |
|---|---|---|
| Räume | Solltemperatur je Raum aus Zeitplan, Modus, Übersteuerung und Schutzregeln | Temperaturfühler je Raum |
| Raumsteuerung | Sollwerte an die Raumthermostate geben | Better Thermostat je Raum |
| Thermensteuerung | Vorlauftemperatur vorgeben oder den Heizbetrieb sperren, wenn kein Raum Wärme braucht | eigene Therme über ebusd |
| Wärmepumpe (optional) | COP messen, Kosten vergleichen, langsam und effizient heizen | Climate-Entität und einige Sensoren |

Jede Steuerung hat drei Stufen, wählbar über ihre Auswahl-Entität:

| Stufe | Bedeutung |
|---|---|
| **Aus** | nichts wird geplant oder gesendet |
| **Nur beobachten** | Thriftherm rechnet und zeigt, was es tun würde, steuert aber nichts – Standard (früher *Nur planen*) |
| **Aktiv** | Befehle werden gesendet. Erst wählbar, wenn in den Optionen freigegeben |

Mit *Nur beobachten* anfangen, einige Tage die Pläne ansehen, dann auf *Aktiv* – Schritt für Schritt in der [Einrichtungsanleitung, Schritt 7](EINRICHTUNG.md#7-erst-nur-beobachten-dann-aktiv).

## Betriebsmodi

Auswahl **Betriebsmodus**:

| Modus | Was passiert |
|---|---|
| **Automatik** | Räume folgen ihren Zeitplänen; die günstigere Wärmequelle heizt |
| **Nur Heizung** | die Wärmepumpe wird nicht genutzt; es heizt die Therme oder die Fernwärme |
| **Nur Wärmepumpe** | nur Wärmepumpen-Räume werden geheizt, die Therme nur für Frostschutz ([Wärmepumpe](#wärmepumpe-optional)) |
| **Aus (nur Frostschutz)** | keine Raumheizung; Warmwasser funktioniert; Frostschutz bleibt |
| **Abwesend** | alle Räume auf Abwesenheitstemperatur; mit Rückkehrzeit wird rechtzeitig vorgeheizt |

### Abwesend mit und ohne Rückkehr

- **Ohne Ende:** Betriebsmodus *Abwesend* wählen, *Geplante Rückkehr* bleibt leer. Die Räume bleiben auf Abwesenheitstemperatur, bis du *Abwesend beenden* oder einen anderen Modus wählst.
- **Bis zu einem Zeitpunkt**, z. B. heute 22:00 oder 3. Oktober 12:00: *Geplante Rückkehr* einstellen, erst das **Datum**, dann die **Uhrzeit** (im Dialog der Entität oder in einer Entitäten-Karte). Das schaltet auf Abwesend bis dahin, und zur Rückkehr wird rechtzeitig vorgeheizt.
- **Nur ein Datum ist ein Platzhalter:** Ohne Uhrzeit steht die Rückkehr vorläufig auf 23:59 des Tages. Abwesend endet dann um 23:59, vorgeheizt wird aber nicht. So lange steht das Attribut *Vorläufig (23:59 als Platzhalter)* (`provisional`) an *Geplante Rückkehr* auf `true`.
- **Erneut *Abwesend*:** Läuft schon *Abwesend* mit Rückkehr, ändert ein erneutes *Abwesend* nichts. Für Abwesend ohne Ende erst einen anderen Modus wählen und dann wieder *Abwesend*, oder `thriftherm.set_away` mit `clear_return_time: true` aufrufen.
- **Wieder da:** *Abwesend beenden* (`thriftherm.clear_away`) beendet Abwesenheit und Rückkehr. Ist die Rückkehrzeit erreicht, geht es von selbst zurück auf Automatik.
- **Rückkehr über Anwesenheit:** In den Optionen unter *Regelparameter* eine oder mehrere Personen wählen (*Abwesend endet, wenn eine dieser Personen heimkommt*). Kommt eine davon heim, endet Abwesend wie zur geplanten Rückkehr: zurück auf Automatik, die Räume heizen auf. Wer beim Einschalten von *Abwesend* noch zu Hause ist, beendet es erst, wenn er geht und wiederkommt. Weggehen schaltet nie auf Abwesend ([Regeln](FUNKTIONSWEISE.md#rückkehr-über-anwesenheit)).
- **Aus Automationen:** `thriftherm.set_away` mit `return_time`, oder mit `clear_return_time: true` ohne Ende. Anders als `datetime.set_value` meldet es einen Fehler, wenn es nicht klappt.

Nach jeder Eingabe *Geplante Rückkehr* und *Vorläufig* prüfen, besonders wenn dein Browser in einer anderen Zeitzone läuft als Home Assistant: Die Felder rechnen in Browser-Zeit. Was abgelehnt wird, wie du wirklich um 23:59 zurückkommst und was bei anderen Zeitzonen passiert: [Abwesend und geplante Rückkehr](FUNKTIONSWEISE.md#abwesend-und-geplante-rückkehr).

## Welche Temperatur gilt

Für jeden Raum gilt die erste zutreffende Regel:

1. **Übersteuerung** (Aktion `set_override` oder eine [Änderung von Hand](#änderungen-von-hand)) – bis sie abläuft.
2. **Aus (nur Frostschutz)** – nur Frostschutztemperatur.
3. **Abwesend** – Abwesenheitstemperatur (Standard 15 °C). Vor einer Rückkehr mit Uhrzeit wird rechtzeitig vorgeheizt (nicht bei der vorläufigen 23:59). Kommt die Luft ihrem Taupunkt zu nahe (Standard 3 Grad), wird der Sollwert gegen Feuchte angehoben ([Feuchteschutz](FUNKTIONSWEISE.md#feuchteschutz)).
4. **Zeitplan** – Komforttemperatur in einem Zeitplan-Block, sonst Absenktemperatur. Vor Blockbeginn wird so vorgeheizt, dass es *zu Beginn* warm ist.

Zwei Schutzregeln gelten immer zusätzlich:

| Schutz | Standard | Verhalten |
|---|---|---|
| Frostschutz | 7 °C | Fällt ein Raum darunter, heizt die Therme sofort – auch im Modus *Aus* –, bis der Raum 1 Grad darüber liegt; sein Thermostat bekommt so lange ein Ziel ein halbes Grad darüber. Kein Raumziel liegt unter dieser Temperatur, auch keine Absenkung, Abwesenheitstemperatur oder Übersteuerung. In einem Wärmepumpen-Raum ohne zentrale Heizung heizt die Wärmepumpe; mit zentraler Heizung springt sie ein, wenn der Raum nach 30 Minuten noch darunter liegt ([Wärmepumpe](#wärmepumpe-optional)). |
| Fenster offen | 90 s Karenz | Solange ein Fensterkontakt offen ist, pausiert das Heizen in diesem Raum. Ein Kontakt, der beim Start von Thriftherm schon offen ist, zählt sofort. |

**Komfort- und Absenktemperatur** sind Zahlen-Entitäten je Raum (*Komforttemperatur*, *Absenktemperatur*), im Dashboard änderbar. Die Absenkung liegt nie über Komfort: Drehst du Komfort darunter, geht die Absenkung mit. Die Raum-Optionen zeigen die Werte, die gerade gelten; änderst du dort einen, gelten beide Werte aus dem Formular.

### Zeitpläne

Jeder Raum kann einen **Zeitplan-Helfer** von Home Assistant nutzen (*Einstellungen → Geräte & Dienste → Helfer → Zeitplan*). Seine Blöcke sind die Komfortzeiten. Ein Block mit dem Attribut `temperature` nutzt diese statt der Komforttemperatur. Räume ohne Helfer nutzen die Zeitfenster aus den Raum-Optionen.

### Ohne festen Zeitplan

Wer lieber von Hand regelt als nach Uhrzeit:

- **Rund um die Uhr Komfort:** Zeitfenster `00:00-23:59` an Werktagen und am Wochenende, oder ein Zeitplan-Helfer mit Blöcken von 00:00 bis 24:00 (oder bis 23:59). Beides gilt als durchgehend, auch über Mitternacht.
- **Temperatur verstellen:** die Zahl *Komforttemperatur* des Raums ändern, etwa über eine Thermostat-Karte oder ein Template-Thermostat. Die Absenkung geht mit, wenn du darunter drehst.
- **Abwesend/Zuhause:** `thriftherm.set_away` (mit `clear_return_time: true` ohne Ende, mit `return_time` samt Vorheizen) und `thriftherm.clear_away`. Die Komforttemperaturen bleiben dabei stehen.

Für das Lernen der Heizkurve ist das sogar günstig: Sie lernt aus langen, gleichmäßigen Heizläufen. Eine Verstellung pausiert das Lernen 20 Minuten; ein Raum, dessen Soll sich geändert hat, zählt 90 Minuten lang nicht mit ([Lernen](#lernen)). Auch ein Soll von Hand lehrt die Kurve, sobald der Raum bis auf 1 Grad heran ist und dort hält.

### Vorheizen

Thriftherm beginnt so früh, dass der Raum zu Beginn eines Komfort-Blocks warm ist. Dafür nimmt es den Abstand zum Soll, die gelernte Aufheizrate des Raums für die Wärmequelle, die heizen wird, und einen Sicherheitszuschlag (Standard 20 Minuten). Den geplanten Beginn zeigt der Sensor *Solltemperatur* unter *Vorheizen beginnt*. Formel und Startwerte: [Vorheizen](FUNKTIONSWEISE.md#vorheizen).

**Kurze Blöcke:** Braucht ein Raum nur kurz Wärme, etwa ein Bad von 06:00 bis 06:30, begrenzt *Längstes Vorheizen* in den Raum-Optionen, wie früh er beginnt (leer: höchstens 4 Stunden, 0: gar nicht; sinnvoll sind 45 bis 60 Minuten; mit der Therme auch länger als 4 Stunden). Vor der Rückkehr aus *Abwesend* gilt die Grenze von 4 Stunden nicht. Ist es gesetzt, ruft der Raum die Therme auch in seinem Block bis zum Ende, sonst bliebe ein kurzer Block kalt (eine neue Anforderung beginnt bis 5 Minuten vor dem Ende). Solange er Wärme will, lernt die Heizkurve nichts.

## Änderungen von Hand

Ändert jemand anderes als Thriftherm einen Sollwert, ist das eine Änderung von Hand: in Home Assistant (Dashboard, App, Sprachassistent), durch eine Automation oder ein Skript, oder am Gerät (Drehknopf am Ventil, Fernbedienung der Klimaanlage, App des Herstellers). Wer es war, sagt Home Assistant selbst ([wie Thriftherm das liest](FUNKTIONSWEISE.md#änderungen-von-hand)).

- **Sie gilt eine feste Zeit** (*Wie lange eine Änderung von Hand gilt*, Standard 120 Minuten). Am Raumthermostat wird sie zur Übersteuerung des Raums; an der Wärmepumpe lässt Thriftherm das Gerät so lange in Ruhe. Eine weitere Änderung in dieser Zeit verlängert sie.
- **Sie wird sofort angezeigt:** am Sensor *Solltemperatur* des Raums (*Übersteuert bis*, *Übersteuert durch*) und am Steuerbefehl der Wärmepumpe (*Von Hand geändert, gilt bis*, *Von Hand geändert durch*).
- **Sie endet von selbst** oder mit dem Knopf *Zurück zur Automatik* (Aktion `thriftherm.clear_override` ohne Raum). Das beendet alle Übersteuerungen, jedes Schnell-Aufheizen und die Ruhezeit der Wärmepumpe auf einmal.
- **Keine Änderung von Hand** sind ein Befehl, den die Wärmepumpe nicht ausgeführt hat (er wird wiederholt), ein Thermostat, das auf ganze Grad rundet, und ein Thermostat, das jemand nur ausschaltet.
- **Am Drehknopf eines Ventils** hängt es am Raumthermostat davor, ob der Dreh zählt ([Raumthermostate](#raumthermostate)).

Kühlt, entfeuchtet oder lüftet jemand mit der Wärmepumpe oder stellt einen anderen Modus als Heizen oder Aus ein (etwa *Auto* oder *Heizen/Kühlen*), bleibt Thriftherm draußen, solange dieser Modus an ist. Eine Therme über Entitäten ([Einrichtung, Schritt 4.3a](EINRICHTUNG.md#43a-therme-ohne-ebusd-über-entitäten-vorschau)) kennt keine Änderung von Hand: Solange die Steuerung aktiv ist, gehören Zahl und Schalter Thriftherm; wer selbst stellen will, schaltet auf *Nur beobachten*.

## Raumthermostate

Thriftherm gibt jedem Raum einen Sollwert. An die Ventile kommt er auf einem von drei Wegen: direkt an die Ventile (Vorschau), über [Better Thermostat](https://github.com/KartoffelToby/better_thermostat) oder über Versatile Thermostat (Vorschau). Eingetragen werden alle im Feld *Thermostat oder Heizkörperventile* der Raum-Optionen. Einrichten: [Einrichtung, Schritt 5](EINRICHTUNG.md#5-ventile-oder-thermostate-je-raum-optional).

Für jedes Raumthermostat gilt: Ein Thermostat, das du von Hand **ausschaltest**, lässt Thriftherm in Ruhe; sein Ventil ist zu, und der Raum fordert die Therme nicht an. Ein kurz nicht erreichbares zählt weiter, denn sein Ventil regelt selbst.

### Better Thermostat

- **Heizt nicht:** Meldet das Thermostat, dass es gerade nicht heizt (Ventil zu), ruft der Raum die Therme nicht, auch wenn Thriftherm ihn anfordern würde ([Einzelheiten](FUNKTIONSWEISE.md#wann-ein-raum-die-therme-anfordert)).
- **Drehknopf am Ventil:** Ob ein Dreh zählt, entscheidet der Schalter *Kindersicherung* des Raums in Better Thermostat. Ist er an, setzt Better Thermostat das Ventil zurück, und der Dreh zählt nicht. Ist er aus, wird die Zahl, die das Ventil nach dem Dreh zeigt, zum Sollwert des Raums. Wegen der Korrektur von Better Thermostat kann das ein anderer Wert sein, als du denkst ([Beispiel](FUNKTIONSWEISE.md#dreh-am-ventil-hinter-einem-raumthermostat)).
- **Übersteuerungen, obwohl niemand gedreht hat:** Ein verspätetes Funk-Echo fängt der *Echo-Filter* ab (Optionen → *Regelparameter*, standardmäßig an; [Einzelheiten](FUNKTIONSWEISE.md#echo-filter)).

### Ventile direkt, ohne Raumthermostat (Vorschau)

> **Vorschau.** Mit Sonoff TRVZB/TRVZBT und Zigbee2MQTT erprobt; andere Ventile (ZHA, Shelly und weitere) sind bisher nur nachgestellt getestet.

Statt eines Raumthermostats kannst du dem Raum seine Heizkörperventile geben (Raum-Optionen → *Thermostat oder Heizkörperventile*). Better Thermostat brauchst du dann nicht; ein Ventil, das ein aktiver Better-Thermostat-Eintrag noch steuert, lehnt das Formular ab. Thriftherm sortiert nach der Integration: Better, Versatile und Generic Thermostat sind Raumthermostate, alles andere (Zigbee2MQTT, ZHA, Z-Wave, Homematic IP …) sind Ventile. Der Raum hat entweder ein Raumthermostat oder Ventile, nicht beides. Ein Heizkörperventil, das früher selbst als Thermostat eingetragen war, wird beim nächsten Speichern der Raum-Optionen zum direkt gesteuerten Ventil; erst dann übernimmt Thriftherm auch das Fenster.

- **Was das Ventil bekommt:** das Ziel des Raums, solange der Raum Wärme will (er fordert die Therme an oder heizt mit) so viel darüber, dass es bis zum Ende der Anforderung sicher offen ist: das Größere aus seiner Öffnungsschwelle und „Toleranz über dem Ziel“ + 0,2 Grad. Bei Fernwärme, ohne Therme oder wenn Thriftherm die Therme nur beobachtet, bekommt es das Ziel ohne Aufschlag und regelt selbst nach dem Raum, wie hinter Better Thermostat. Der [Vorhalt](#vorhalt-am-thermostat) gilt hier nicht, er ist für Thermostate mit eigener Regelung. Das offene Ventil ist gewollt: Die Vorlauftemperatur regelt die Wärme, und die Therme läuft gleichmäßiger, statt gegen ein geschlossenes Ventil zu takten ([warum](#vorhalt-am-thermostat)). Dazu,  je nachdem was es kann, die Temperatur des Raumfühlers in seinen Fremdtemperatur-Eingang (Sonoff TRVZB/TRVZBT, Danfoss Ally, Bosch, Aqara E1) oder einen Kalibrier-Offset (die meisten anderen Zigbee-Ventile). Alle anderen mit Ist-Temperatur (Homematic IP, Z-Wave, Matter und weitere) bekommen einen korrigierten Sollwert: Misst das Ventil 1 Grad mehr als der Raum, bekommt es 1 Grad mehr. Was das Ventil kann, erkennt Thriftherm an den Entitäten seines Geräts; ein Modellprofil ist nicht nötig.
- **Fenster offen:** Nach der *Karenzzeit Fenster offen* (Standard 90 Sekunden) schaltet Thriftherm die Ventile aus, nach dem Schließen wieder an und auf das Ziel; so machen es auch Better und Versatile Thermostat. Wer das nicht will, stellt unter *Regelparameter → Fenster und Vorheizen* auf *Auf der Frostgrenze*. Ein Ventil, das du selbst ausschaltest, bleibt aus. **Achtung:** Ob ein Ventil im Zustand „aus“ seinen eigenen Frostschutz behält, hängt vom Modell ab. Das Sonoff TRVZB/TRVZBT hält dann seine *Frostschutztemperatur*. Bei Tuya- und Avatto-Ventilen ist der Frostschutz ein eigener Schalter, der an sein muss. Bei anderen Modellen ist es nicht belegt; im Zweifel *Auf der Frostgrenze* wählen.
- **Raumfühler meldet nicht mehr:** Das Ventil regelt nach seinem eigenen Fühler, bis der Raumfühler wieder meldet.
- **Was am Ventil eingestellt ist, bleibt:** Kindersicherung, die eigene Fenstererkennung des Ventils, sein Wochenplan und sein Modus werden nie geschrieben. Stellst du die Fühlerauswahl in Home Assistant auf den eigenen Fühler, bleibt sie so; das Ventil bekommt dann einen Offset.
- **Drehknopf:** Ein Dreh am ersten Ventil des Raums ist eine Änderung von Hand wie am Raumthermostat, und zwar als Unterschied zum Ziel. Weitere Ventile des Raums bekommen denselben Sollwert; ein Dreh an ihnen wird noch nicht zur Übersteuerung des Raums und nach fünf Minuten überschrieben.
- **Ventilschutz:** Ein Ventil, das eine Woche nicht geöffnet hat (Sonoff TRVZB/TRVZBT: 84 Stunden), fährt zweimal ganz auf und zu, je etwa zwei Minuten, damit es nicht festsitzt; das beginnt nur zwischen 10 und 18 Uhr, nicht während die Therme heizt, der Raum anfordert oder sein Fenster offen ist.
- **Apple Home und Siri:** Ohne Better Thermostat fehlt dem Raum sein Thermostat in Apple Home. Thriftherm bringt dafür, wie für jeden Raum, ein eigenes *Raumklima* mit (siehe *Raumklima für Apple Home und Siri*) (`climate.thriftherm_<raum>_room_climate`): Heizen oder Aus, Sollwert ist das Ziel des Raums, Ist der Raumfühler. Ein Sollwert, den du dort (oder per Siri) stellst, gilt als Änderung von Hand; *Aus* hält den Raum auf der Frostgrenze, bis du wieder *Heizen* wählst. In der HomeKit-Bridge dieses Raumklima aufnehmen und die Ventile selbst ausgeschlossen lassen.
- **Öffnungsschwelle klein stellen:** Viele Ventile öffnen erst eine Spanne unter ihrem Sollwert (Sonoff TRVZB/TRVZBT: *Temperaturgenauigkeit*, ab Werk −1). Thriftherm rechnet sie ein. Je kleiner sie ist, desto weniger schwankt der Raum; −0,4 hat sich bewährt.
- **Nach dem Wechsel von Better Thermostat:** Das Gelernte des Raums bleibt. Weil das Ventil jetzt anders öffnet als unter Better Thermostat, passen sich die gelernten Linien der Heizkurve in den ersten Tagen an; das ist kein Fehler.
- **Weg zum Testen erzwingen:** *Wie die Ventile geregelt werden* steht auf *Automatisch* (der genaueste Weg, den das Ventil kann). Zum Vergleich lässt sich je Raum der Offset oder der korrigierte Sollwert erzwingen; das Ventil geht dann auf seinen eigenen Fühler. Zurück auf *Automatisch* bekommt es wieder die Raumtemperatur.
- **Wenn Thriftherm geht:** Stellst du den Raum wieder auf Better Thermostat, nimmst ein Ventil heraus, schaltest die Raumsteuerung von *Aktiv* weg, deaktivierst oder löschst Thriftherm oder stoppt Home Assistant, bekommt jedes Ventil zurück, was es hatte: seinen eigenen Fühler, seinen alten Offset und die Öffnung, die es vorher hatte. Hält es noch einen Sollwert von Thriftherm (Aufschlag, Frostgrenze, Ventilschutz), bekommt es das Ziel des Raums; hat nur das Fenster es ausgeschaltet, wird es wieder eingeschaltet. Einen Sollwert oder ein „aus“ von dir lässt Thriftherm stehen, die Kindersicherung nie angefasst. Vor einem Wechsel auf eine ältere Version die Räume erst auf Better Thermostat zurückstellen; eine ältere Version kann das nicht.

Regeln und Zeiten: [Ventile direkt](FUNKTIONSWEISE.md#ventile-direkt).

### Versatile Thermostat statt Better Thermostat (Vorschau)

> **Vorschau.** Nach der Dokumentation von Versatile Thermostat gebaut und mit nachgestellten Zuständen getestet, noch nicht mit einer echten Installation.

Als Thermostat eines Raums kannst du auch die Klima-Entität von [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat) wählen. Thriftherm schreibt ihr nur die Solltemperatur.

- **Eigene Regeln bleiben bei Versatile Thermostat.** Senkt es den Sollwert selbst ab, weil ein Fenster offen ist, es Last abwirft oder im Sicherheitsmodus ist, hält Thriftherm das nicht für eine Änderung von Hand und schreibt nicht dagegen; Versatile Thermostat stellt den angeforderten Wert danach selbst wieder her. Der Raum zeigt so lange *Das Thermostat wendet eine eigene Regel an*.
- **Schaltet es sich bei offenem Fenster aus,** gilt das wie ein ausgeschaltetes Thermostat: Der Raum fordert die Therme nicht an.
- **Die Funktion „Zentralheizung“ von Versatile Thermostat ausgeschaltet lassen,** wenn Thriftherm die Therme steuert. Sonst schalten zwei an derselben Therme.
- **Voreinstellungen (Komfort, Eco, Boost) und Anwesenheit** in Versatile Thermostat verstellen den Sollwert als Anforderung. Thriftherm liest das als Änderung von Hand und macht daraus eine Übersteuerung. Den Zeitplan führst du deshalb in Thriftherm.
- **Drehknopf am Ventil:** Ohne *Änderungen am Gerät folgen* übernimmt Thriftherm einen Dreh nach fünf Sekunden selbst ([Einzelheiten](FUNKTIONSWEISE.md#dreh-am-ventil-hinter-einem-raumthermostat)).
- Die Ventile hinter Versatile Thermostat findet Thriftherm selbst und meldet, wenn eines verstummt.

### Raumklima für Apple Home und Siri

Jeder Raum hat ein eigenes Thermostat von Thriftherm, `climate.thriftherm_<raum>_room_climate`. Sollwert ist das Ziel des Raums (ohne Vorhalt und ohne Mitheizen), Ist der Raumfühler, Modus *Heizen* oder *Aus*.

- Ein Sollwert dort gilt wie ein Dreh am Thermostat des Raums: als Änderung von Hand für die Übersteuerungsdauer. Thriftherm gibt ihn an das Thermostat weiter (etwa Better Thermostat), dessen Antwort zählt nicht als zweite Änderung. Drehst du danach am Thermostat selbst, gilt das Neuere.
- *Aus* hält den Raum auf der Frostgrenze, bis du ihn wieder auf *Heizen* stellst; *Heizen* bringt ihn zurück in den Zeitplan.
- Weitergegeben wird nur, wenn Thriftherm die Räume aktiv steuert; in *Nur beobachten* ändert der Sollwert nur Thriftherms Planung.
- **HomeKit-Bridge:** Seit 0.8 hat jeder Raum dieses Raumklima. Hat ein Raum zusätzlich Better Thermostat (oder gibt die Bridge die direkt gesteuerten Ventile frei), steht er in Apple Home doppelt. Nimm eines davon aus der Bridge, am besten das von Better Thermostat bzw. die Ventile: Dann stellt Siri Thriftherms Ziele, und Apple Home zeigt das Ziel des Raums ohne Vorhalt.

### Vorhalt am Thermostat

Ein Thermostat mit eigener Regelung (Better Thermostat, Versatile Thermostat, ein Ventil allein) drosselt sein Ventil schon kurz vor dem Sollwert. Thriftherm ruft die Therme aber erst unter dem Ziel und lässt sie kurz davor wieder aus (Standard 0,3 Grad darunter bis 0,1 Grad darunter; einstellbar unter Regelparameter → Experte: *Toleranz unter dem Ziel*, *Toleranz über dem Ziel*). Damit das Ventil in dieser Zeit offen ist, bekommt das Thermostat **solange die Therme für den Raum läuft** das Ziel plus den Vorhalt (Standard 0,5 Grad), danach wieder das Ziel. Es schließt also weiter am Ziel, der Raum wird nicht wärmer.

**Warum das Ventil offen sein soll:** Mit Thriftherm regelt die Vorlauftemperatur die Wärme, nicht das Ventil. Ist das Ventil offen, solange die Therme für den Raum läuft, kommt die Wärme im Raum an, und die Therme läuft länger und gleichmäßiger mit niedrigerem Vorlauf; ein Ventil, das dabei drosselt, lässt sie gegen ein geschlossenes Ventil heizen, und sie taktet. Gegen eine Therme, die viel mehr leistet, als die Räume brauchen, hilft das allein nicht. Bei Fernwärme und in *Nur beobachten* regelt das Ventil selbst.

- Gilt auch, während ein Raum mitheizt; dann zählt das Höhere von beidem, nicht beides.
- Nicht bei Fernwärme, bei gesperrter Therme, bei offenem Fenster oder wenn das Thermostat meldet, dass es nicht heizt.
- Das Raumziel in Thriftherm und in der Karte bleibt das eigentliche Ziel. Am Thermostat (und in Apple Home, wenn dort das Thermostat selbst statt Thriftherms Raumklima steht) siehst du während des Rufs den höheren Wert.
- Drehst du in dieser Zeit am Drehknopf des Ventils oder Thermostats, gilt dein Dreh für den Raum: von 21,5 auf 22 gedreht heißt 21,5 für den Raum. Ein Wert, den du in Home Assistant oder Apple Home einstellst (oder eine Automation), gilt so, wie er ist.
- Nur, wenn Thriftherm die Therme aktiv steuert, nicht in *Nur beobachten*. Ein Thermostat in ganzen Grad bekommt den Vorhalt nur in ganzen Grad (0,5 wirkt dort nicht, 1 schon).
- Ändern unter Optionen → *Räume* → Raum → *Bearbeiten*: 0, 0,5 oder 1 Grad. Für Räume, deren Ventile Thriftherm direkt steuert, gilt er nicht.

### Stumme Thermostate

Als stumm gilt ein Heizkörperthermostat, wenn es einen Sollwert eine halbe Stunde lang nicht beantwortet (hinter Better Thermostat: bis sich ein Ventil dahinter gemeldet hat), eine halbe Stunde lang nicht verfügbar ist oder sich sechs Stunden lang gar nicht meldet. Bei Better Thermostat wird das echte Thermostat dahinter überwacht. Werte, die nach einem Neustart von MQTT oder Home Assistant nur wiederholt werden, zählen nicht als Meldung; kommt ein Thermostat nach einem Neustart oder Batteriewechsel mit anderem Sollwert zurück, gilt das nicht als Dreh von Hand, es bekommt wieder Thriftherms Sollwert. Ein stummes Thermostat fordert keine Therme an und nimmt keine neuen Sollwerte an; dazu erscheint eine [Reparaturmeldung](#reparaturmeldungen). Hält Thriftherm ein gesundes Thermostat für stumm, in Zigbee2MQTT dessen Entität *Last seen* einschalten; mit ZHA ist nichts zu tun ([warum](FUNKTIONSWEISE.md#ausfälle-und-wiederholung)).

### Ohne smarte Thermostate

Ein Raum braucht nur etwas, das ihn misst: einen Temperaturfühler, ein Ventil oder ein Thermostat. Ohne smarte Ventile genügt der Fühler. Alles andere funktioniert weiter: Bedarf je Raum, Heizkurve, Sperren, wenn kein Raum Wärme braucht, Lernen und Frostschutz. Der *Thermostat-Plan* des Raums nennt dann den Grund *Kein Thermostat*, und an kein Ventil geht etwas: Die Handventile bleiben weit genug offen, und die Vorlauftemperatur regelt. Smarte Thermostate lassen sich später Raum für Raum ergänzen, gemischt geht auch.

## Thermensteuerung

Gilt für eine eigene Therme über ebusd. Eine Therme ohne eBUS lässt sich als Vorschau über eine Zahl-Entität (Vorlauf-Sollwert) und einen Schalter (Heizung an/aus) steuern ([Einrichtung, Schritt 4.3a](EINRICHTUNG.md#43a-therme-ohne-ebusd-über-entitäten-vorschau)). Alles Folgende gilt für beide Wege; wo von `SetMode` die Rede ist, schreibt Thriftherm dort Zahl und Schalter.

- **Vorlauftemperatur:** aus einer Zwei-Punkt-Heizkurve (Standard 55 °C bei −10 °C, 30 °C bei +15 °C). Dazu kommt, was der Raum verlangt, der am meisten braucht: seine gelernte Korrektur ([Lernen](#lernen)), bis zu 8 Grad mehr, solange er weit unter Soll ist, und sein Zuschlag fürs [Aufheiztempo](#aufheiztempo). Der Vorlauf bleibt immer zwischen eingestelltem Minimum und Maximum und steigt nicht sprunghaft ([Vorlauf im Detail](FUNKTIONSWEISE.md#vorlauf)).
- **Grenze der Therme:** Taktet die Therme auf kleinster Leistung weit unter dem gesendeten Vorlauf, hebt Thriftherm nicht die Heizkurve an, sondern schickt schrittweise mehr, solange ein Raum nicht hinkommt; bei einer *Brennwerttherme* bis 55 °C. Was gereicht hat, merkt es sich. Den Zuschlag zeigt der *Lernstatus* im Attribut `boiler_limit_k` ([Einzelheiten](FUNKTIONSWEISE.md#grenze-der-therme)).
- **Anforderung:** Ein Raum fordert die Therme an, wenn er spürbar unter Soll liegt, bis er fast dort ist. In der letzten halben Stunde seiner Komfortzeit nicht mehr, außer die Therme hilft der Wärmepumpe oder der Raum hat ein *Längstes Vorheizen* (dann bis 5 Minuten vor dem Ende). Den Zustand zeigt das Attribut *Anforderung an die Therme* am Sensor *Heizbedarf* des Raums ([Schwellen](FUNKTIONSWEISE.md#wann-ein-raum-die-therme-anfordert)).
- **Mitheizen:** Läuft die Therme ohnehin, heizen andere Räume mit (Grund *Heizt mit* bzw. *Heizt früher vor*), in der Absenkung bis zu ihrem Absenkungsziel, nie darüber (*Heizt bis zur Absenkung mit*). Das gibt längere Brennerläufe und weniger Starts. Ein direkt gesteuertes Sonoff TRVZB/TRVZBT öffnet dabei nur anteilig (30 % oder 60 %, je nachdem, wie wenig dem Raum fehlt) und bekommt danach den Ventilöffnungsgrad zurück, den du eingestellt hast ([Einzelheiten](FUNKTIONSWEISE.md#mitheizen)).
- **Heizbetrieb gesperrt** (`disablehc`), wenn kein Raum Wärme braucht. Warmwasser wird nicht angefasst: Der Warmwasser-Sollwert geht immer als „kein Wert“ hinaus, die Therme folgt dafür weiter ihrem Drehknopf. Die einzige Ausnahme wählst du selbst: Ist *Warmwasser bereithalten* in den Thermen-Optionen aus, heizt die Therme nicht mehr für Warmwasser, solange kein Hahn offen ist (das stündliche Warmhalten einer Kombitherme). Mit Warmwasserspeicher eingeschaltet lassen.
- **Ausfälle:** Nach einem Neustart wartet Thriftherm bis zu fünf Minuten auf die Raumfühler. Bleibt das eBUS-Signal zwei Minuten weg, sendet Thriftherm nichts mehr. Fällt Home Assistant aus, läuft die Therme nach 9–16 Minuten wieder über ihren Drehknopf. Der Knopf bleibt immer die Obergrenze; stell ihn deshalb auf die höchste Vorlauftemperatur, die du zulassen willst ([Einrichtung, Schritt 6](EINRICHTUNG.md#6-an-der-therme)).
- **Warmwasser:** Thriftherm erkennt selbst, wenn die Therme Warmwasser macht (*Therme Warmwasser aktiv*), und pausiert das Lernen dann eine Stunde ([woran](FUNKTIONSWEISE.md#warmwasser-erkennung)).
- **Nur Leseanfragen:** Damit Vorlauf und Rücklauf frisch sind, lässt Thriftherm ebusd sie öfter lesen. An die Therme wird dabei nichts geschrieben ([Einzelheiten](FUNKTIONSWEISE.md#frische-messwerte)).

Der Sensor *Therme-Steuerbefehl* zeigt den Plan (`Heizen`, `Heizbetrieb gesperrt`, …), den genauen `SetMode`-Befehl, den Grund, worauf gewartet wird, ob die Heizungspumpe läuft und die Status-Nummer der Therme (`S.xx`, wie auf ihrem Display).

**Warum das Minimum vom Gerätetyp abhängt.** Ein Brennwertgerät profitiert von niedrigem Vorlauf: Unter etwa 56 °C Rücklauf kondensiert das Abgas und bringt bis zu 11 % mehr, etwa 30 °C sind ein gutes Minimum. Ein Heizwertgerät kondensiert nie und spart dort wenig; bei kurzen Brennerläufen kommt die Wärme womöglich nicht bis zu den fernen Heizkörpern. Bei einer Heizwerttherme kann mit 30 °C der letzte Heizkörper kalt bleiben; dort mit etwa 45 °C beginnen. Die Einstellung heißt *Minimale Vorlauftemperatur* ([Einrichtung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).

### Aufheiztempo

Die Heizkurve ist dafür da, eine Temperatur zu halten. Wird ein Raum hochgestellt, kann das Aufheizen allein mit ihr lange dauern. Wie schnell ein Raum warm werden soll, stellst du in den Thermen-Optionen ein: *Aufheiztempo (Grad pro Stunde, 0 = aus)*, Standard 0.

- Liegt ein Raum, der anfordert, 1 Grad oder mehr unter Soll (nach dem Hochstellen, nach der Absenkung oder einem offenen Fenster), bekommt er beim Aufheizen einen eigenen Zuschlag auf den Vorlauf. Das wirkt nur bei aktiver Thermensteuerung.
- Alle 20 Minuten misst Thriftherm, wie schnell der Raum steigt. Zu langsam: Der Zuschlag wächst. Deutlich zu schnell: Er sinkt. Nahe am Soll wird er kleiner.
- Was ein Raum gebraucht hat, ist der Start fürs nächste Aufheizen dieses Raums.
- Solange ein Raum mit Zuschlag aufheizt, pausiert das Lernen der Heizkurve (Grund *ein Raum heizt mit Zuschlag auf*).

Mehr Tempo kostet mehr Gas. Welcher Wert passt:

| Wert | Wirkung |
|---|---|
| **0** (Standard) | aus, der Vorlauf wie bisher: Heizkurve, gelernte Korrektur und bis zu 8 Grad mehr, solange ein Raum weit unter Soll ist. Reicht, wenn die Räume nach Zeitplan vorheizen oder es nicht eilt |
| **0,5** | sanft: greift nur, wenn ein Raum beim Aufheizen fast stehen bleibt (weniger als 0,4 Grad je Stunde). Kostet am wenigsten |
| **1** | zügig: etwa ein Grad je Stunde. Ein guter Anfang, wenn dir Räume nach dem Hochstellen zu lange brauchen |
| **2** | schnell: zwei Grad je Stunde. Braucht viel Vorlauf und am meisten Gas; mit kleinen Heizkörpern oder niedrigem Maximum reicht es nicht immer |

Der *Lernstatus* zeigt *Aufheiz-Zuschlag je Raum* (beim Aufheizen der Zuschlag, sonst der Startwert fürs nächste) und *Räume, die mit Zuschlag aufheizen*. Schritte, Grenzen und wann gemessen wird: [Aufheiztempo](FUNKTIONSWEISE.md#aufheiztempo).

### Warum der Pumpenmodus zählt

Im Werkszustand läuft die Pumpe nur, solange der Brenner brennt, plus kurzem Nachlauf (Vaillant: d.18 = 0, „Nachlauf“). Nehmen die Räume wenig Wärme ab, schaltet die Therme nach Sekunden wieder ab. Das Wasser hat dann nur eine Runde über den Bypass der Therme gedreht: **Die Heizkörper weit hinten im Kreis bleiben kalt**, obwohl die Therme den ganzen Tag taktet.

Mit Pumpenmodus **durchlaufend** (d.18 = 1) läuft die Pumpe, solange der Heizbetrieb freigegeben ist, und **steht, wenn Thriftherm sperrt**. Das kostet etwa 33 W, nur während geheizt wird. Mit dem Modus *Nachlauf* kann ein Heizkörper fern der Therme kalt bleiben ([Hintergrund](FUNKTIONSWEISE.md#ebusd-pumpe-und-statuswerte)). Prüfen und ändern: [Einrichtung, Schritt 6](EINRICHTUNG.md#6-an-der-therme).

### ebusd: Pumpe und Statuswerte

Ob die Heizungspumpe läuft und welche Status-Nummer (S.xx) die Therme zeigt, liest Thriftherm aus den ebusd-Werten `WP` und `Statenumber`. Sie kommen nur in Home Assistant an, wenn sie durch den Namensfilter der ebusd-MQTT-Integration passen ([Einrichtung, Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen)). Ohne sie greift Thriftherm auf `Status01` zurück ([warum das nicht reicht](FUNKTIONSWEISE.md#ebusd-pumpe-und-statuswerte)).

## Lernen

Thriftherm lernt laufend aus dem Alltag. Dafür musst du nichts tun; anfangs gelten Startwerte, die schon brauchbar heizen.

| Bereich | Was gelernt wird | Woraus |
|---|---|---|
| Therme | Korrektur der Heizkurve, je Raum und je Lastfall (ein Raum, wenige oder die Mehrheit fordern an) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum und Wärmequelle (Grad je Stunde) | echte Heizphasen bei geschlossenem Fenster |
| Räume | Auskühlkoeffizient je Raum (1/h) | Phasen ohne Heizen bei geschlossenem Fenster |
| Räume | Heizleistung je Raum (Grad je Stunde ohne Verluste) | Aufheizrate plus die Verluste in der Phase; anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe | COP über der Außentemperatur (COP-Kennfeld) | Wärme und Strom der ganzen Heizzeit |

Einzelheiten zu allen Bereichen: [Lernen](FUNKTIONSWEISE.md#lernen).

### Wie lange das Lernen dauert

Gelernt wird nur, während wirklich geheizt wird. Bis etwas gelernt ist, heizt Thriftherm mit Startwerten: der eingestellten Heizkurve, 0,8 Grad je Stunde Aufheizrate (Wärmepumpe 0,5) und einer COP-Kurve nach Datenblatt. Die Zeiten unten sind aus den Wartezeiten und Schwellen abgeschätzt und gelten für eine Heizsaison mit üblichem Wetter; über eine ganze Saison gemessen sind sie noch nicht.

| Was | Erster Wert | Stabil | Gut zu wissen |
|---|---|---|---|
| Heizkurve: Höhe | am ersten Heiztag, frühestens 1,5 bis 2 Stunden nach der letzten Sollwertänderung | nach etwa 1 bis 2 Wochen Heizsaison | nur mit *Aktiv*. Eine zu niedrige Kurve steigt am ersten Tag um bis zu 10 Grad, eine zu hohe sinkt höchstens 4 Grad am Tag und braucht einige Heiztage |
| Heizkurve: Steigung | an den ersten Tagen deutlich unter 0 °C | nach dem ersten richtigen Frost; in einem milden Winter erst in der nächsten Saison | bis dahin trägt die Höhe |
| Heizkurve je Lastfall | der häufigste Lastfall zuerst, oft „ein Raum“ | seltene Lastfälle wie „die Mehrheit“ nach Wochen bis Monaten | bis dahin nimmt ein Raum seinen Wert aus dem Lastfall mit mehr Räumen oder die gemeinsame Korrektur |
| Aufheizrate je Raum | mit der Therme ab der dritten Aufheizphase, also nach 2 bis 4 Tagen mit Absenkung; vorher zählt der Startwert mit | nach 1 bis 2 Wochen | braucht Aufheizphasen von mindestens 30 Minuten und 0,5 Grad, oder 2 Stunden und 0,2 Grad. Wärme von woanders (Dusche, Sonne) zählt nicht. Ein Raum ohne Absenkung behält den Startwert |
| Auskühlkoeffizient je Raum | nach der ersten Nacht mit Absenkung | nach etwa einer Woche | nur, wenn es drinnen mindestens 5 Grad wärmer ist als draußen |
| COP-Kennfeld | eine Außentemperatur-Stufe nach 3 Stunden Laufzeit darin, bei passendem Wetter nach 1 bis 3 Tagen | die häufigen Stufen (etwa 0 bis 12 °C) nach 2 bis 4 Wochen; Froststufen erst bei Frost | Stufen, in denen die Heizung günstiger ist, lernen nur über *Lernläufe* (eine Stunde am Tag), das dauert 1 bis 3 Wochen. Ohne Lernläufe bleiben sie bei der Schätzung |

Wie schnell eine taktende Therme ihre Kurve senkt, hängt davon ab, ob ihre Räume das Soll nach einem kurzen Ruf zügig wieder erreichen; diese Regel ist noch jung. Wie die Zeiten zustande kommen: [Wie lange das Lernen dauert](FUNKTIONSWEISE.md#wie-lange-das-lernen-dauert).

### Wie die Heizkurve lernt

- **Höhe und Steigung:** Die Korrektur der Heizkurve hat zwei Teile. Die **Höhe** gilt bei jeder Außentemperatur, die **Steigung** vor allem bei Kälte, so wie man eine Heizkurve von Hand einstellt. Was im Herbst gelernt wird, verstellt deshalb den Winter kaum.
- **Je Raum und Lastfall:** Ein kleiner Heizkörper in einem kalten Raum braucht mehr Vorlauf als ein großer, wenige offene Heizkörper einen anderen als viele. Deshalb hat jeder Raum eine eigene Korrektur, getrennt für drei Lastfälle: ein Raum fordert an, wenige, die Mehrheit. Der Raum, der am meisten braucht, bestimmt den Vorlauf ([Lastfälle und Werte je Raum](FUNKTIONSWEISE.md#lastfälle-und-werte-je-raum)).
- **Vorsichtig:** Die Therme lernt nur im Modus *Aktiv*, in kleinen Schritten mit Wartezeiten und Tagesgrenzen. Pause ist bei Warmwasser, nach einer Sollwertänderung (20 Minuten), bei Schnell-Aufheizen, Trocknung, einem Aufheizen mit Zuschlag, einem Vorheizen mit *Längstes Vorheizen*, einer Störung der Therme, ohne Außentemperatur und wenn der Sicherheitsstatus nicht OK ist ([Schutz gegen falsches Lernen](FUNKTIONSWEISE.md#schutz-gegen-falsches-lernen)).
- **Taktet die Therme,** zählt allein, ob die Räume vorankommen; ein Schritt, der nichts bringt, wird zurückgenommen ([Taktbetrieb](FUNKTIONSWEISE.md#taktbetrieb)).
- **Ein Gaszähler ist nicht nötig.** Den Brenner erkennt Thriftherm auch am Status der Therme ([Brenner erkennen](FUNKTIONSWEISE.md#brenner-erkennen)).

**Was erprobt ist.** Die Steuerung selbst – Sperren, Vorlauf, Sicherheit – ist an der Therme des Entwicklers geprüft. Die gelernten Korrekturen kennen bisher Außentemperaturen bis hinunter zu 2 °C, noch keinen Frost; in den ersten Frostwochen lohnt ein Blick darauf.

**Was du einstellen kannst** (Thermen-Optionen): *Anteil der Räume …* – die Spreizung bewertet die Kurve nur, wenn mindestens dieser Anteil der Räume anfordert, die gerade Wärme abnehmen können (Standard die Hälfte). Sonst zählt allein, ob die anfordernden Räume vorankommen.

### Wo du es siehst

Der Sensor *Lernstatus* zeigt

- die Phase (*Einlernen*, *Verfeinern* oder *Wartet auf Heizlauf*), die aktuelle Korrektur mit Höhe und Steigung, den Pausengrund und das letzte Zurücksetzen. Gelernt wird nur, während die Therme heizt; sonst steht dort *Wartet auf Heizlauf*, das ist kein Fehler. Wartet das Lernen wegen einer Sollwertänderung, steht dort auch, bis wann und in welchem Raum;
- *Anfordernde Räume* und ob die Spreizung gerade bewertet;
- den *Lastfall jetzt* und den *Raum, der den Vorlauf bestimmt*;
- unter *Heizkurven-Korrektur je Lastfall und Raum* für jeden Fall und Raum (Einträge wie `few:badezimmer`) Korrektur, Höhe, Steigung, die Betriebsstunden und wie oft dabei ein Raum hing.

Der Sensor *Therme Brenneranteil* zeigt, welchen Anteil der letzten Stunde der Brenner für die Heizung lief, und die Zahl der Starts; er schreibt vorerst nur mit. *Therme Heizstarts heute* zählt die Brennerstarts für die Heizung seit Mitternacht. Ein Start zählt, sobald sein Brennerlauf endet, denn erst dann steht fest, dass er nicht für Warmwasser war. Die Statistik von Home Assistant zeigt damit die Starts jedes Tages. Gezählt wird nach dem besten Zeichen, das da ist: Gaszähler, sonst die Statusnummer der Therme (eBUS), sonst Flammen- oder Pumpensignal. Das Attribut *Gezählt nach* nennt es. Mit der Statusnummer zählt jeder Brennerlauf, den ebusd sieht: Thriftherm rechnet beim Wechsel Brenner an/aus sofort, nicht erst im nächsten Minutentakt. Ohne Gaszähler können trotzdem Läufe fehlen, die kürzer sind als die Abfrage von ebusd (Attribut *Kurze Brennerläufe können fehlen*). *Therme Heizbetrieb aktiv* zeigt den Brenner nach demselben Zeichen.

### Gelerntes vergessen

Nach einer Änderung an der Anlage passen alte Werte nicht mehr.

- **Von Hand:** Optionen → *Lernen zurücksetzen*, Bereiche (und Räume) ankreuzen, oder Dienst `thriftherm.reset_learning` (als Administrator). Mit Räumen gehen bei der Therme nur die Heizkurven-Werte dieser Räume weg, etwa `scope: ["boiler"]`, `rooms: ["badezimmer"]`.
- **Automatisch:** Ändert sich eine Einstellung, von der ein Bereich abhängt, wird nur dieser Bereich vergessen:

| Änderung | Vergessen wird |
|---|---|
| Heizkurve | Thermen-Korrektur |
| Wärmepumpen-Entität, Ansaug-/Ausblassensor, Luftmengen-Kennlinie, Lüfterstufe im Normalbetrieb, Aufstellraum, beheizte Räume | COP-Kennfeld und Lernläufe (der Schlauchfaktor rechnet das Kennfeld nur um) |
| Temperatursensor oder Thermostat eines Raums | Aufheizrate dieses Raums |

Laufzustand, Sperrzeiten und Handbedienung werden nie zurückgesetzt – ein Zurücksetzen kann also kein Takten auslösen.

## Wärmepumpe (optional)

Erprobt ist sie an einer Midea PortaSplit, auch mit Vereisung und Abtauen; andere Geräte noch nicht.

- **COP:** Thriftherm misst ihn aus der Luft, die die Wärmepumpe ansaugt und ausbläst, und aus ihrem Strom. Taktet sie, zählt der ganze Takt samt Pause (Attribut *COP über den letzten Takt*); dieser Wert wird nur angezeigt. Die Kosten bestimmt der eingeschwungene COP oder das COP-Kennfeld ([COP messen](FUNKTIONSWEISE.md#cop-messen)).
- **Kosten:** Die Wärmepumpe läuft langsam mit hohem COP. Verglichen wird mit dem Preis der zentralen Wärme: Zur Wärmepumpe wechselt Thriftherm, wenn ihr COP 10 % über dem *Break-even-COP* liegt, zurück, wenn er 5 % darunter fällt (Feineinstellung, *Wann sie läuft*). Der Abstand verhindert ständiges Hin und Her. Ist die *Leistung der Heizungspumpe* eingetragen, zählt Thriftherm die Laufzeit der Pumpe und die Wärme der Therme und rechnet den Pumpenstrom je kWh Wärme zum Thermenpreis hinzu (höchstens 0,06 kWh), sodass eine Wärmepumpe, die übernimmt, auch ihn spart. Bei Fernwärme zählt der **Heizkostenschlüssel**: Nur der Verbrauchsanteil folgt dem eigenen Zähler. Eine gesparte kWh spart also weniger als ihr Preis, und die Wärmepumpe braucht einen höheren COP, damit sie sich lohnt ([Bei Fernwärme](EINRICHTUNG.md#bei-fernwärme)).
- **Aufstellort und Schläuche:** Ohne Schläuche bleibt die warme Luft im Aufstellraum. Aufstellraum und Schlauchfaktor (1,0 = keine Schläuche) eintragen; nicht erreichbare Räume erzeugen eine Reparaturmeldung. Der Schlauchfaktor korrigiert die Luftmenge, nicht die Wärme: Was die Schläuche unterwegs abgeben, bleibt in der Wohnung.
- **Gesperrt** ist die Wärmepumpe unter ihrer Mindest-Außentemperatur (*Minimale Außentemperatur für das Heizen (Datenblatt)*, Standard −10 °C), bei Vereisung oder häufigem Abtauen, wenn jemand mit ihr kühlt, und wenn ihr eingeschwungener COP länger unter dem *Break-even-COP* liegt, sie also teurer heizt als die Heizung. Wie lange die Sperren wegen Vereisung und Ineffizienz dauern, stellt *Sperre nach Vereisung / Ineffizienz* ein (Standard zwei Stunden). Im Modus *Nur Wärmepumpe* und solange eine Störung die Therme zurückhält, gibt es die Sperre wegen Ineffizienz nicht ([Sperren](FUNKTIONSWEISE.md#sperren)).
- **Kühlen und Heizen überschneiden sich nie:** Solange sie kühlt, bleiben die Heizkörper der Räume, die sie bedient, auf Absenktemperatur und fordern keine Wärme von der Therme an. Der Frostschutz gilt weiter.
- **Frostschutz:** Ohne zentrale Heizung hält die Wärmepumpe im Modus *Abwesend* die Abwesenheitstemperatur und im Modus *Aus* die Frostschutztemperatur, und einen Raum unter der Frostgrenze heizt sie in jedem Modus, auch bei offenem Fenster und Frost draußen, bis er 1 Grad darüber liegt. Mit Therme oder Fernwärme ist sie die Reserve: Liegt einer ihrer Räume nach 30 Minuten noch unter der Grenze, heizt sie ihn ebenso, auch wenn das teurer ist. Vereisung, Fehlercodes und der Sicherheitsstatus *Fallback* halten sie weiter an, und in *Nur beobachten* wird nichts gesendet.
- **Schnell aufheizen** (Knopf je Raum oder `thriftherm.boost`) lässt sie für begrenzte Zeit mit voller Leistung laufen.
- **Lüfter:** Thriftherm stellt ihn nur beim Einschalten, für eine Trocknung oder ein Schnell-Aufheizen und danach zurück, auf die *Lüfterstufe im Normalbetrieb* aus der Feineinstellung (Standard *Auto*). Eine höhere Stufe bringt mehr Wärme in die Räume, ist aber lauter. Eine neu gewählte Stufe bekommt die Wärmepumpe auch mitten im Lauf, sofern sie noch die alte hat; eine Stufe, die du von Hand wählst, bleibt. Vom Heizen geht es nie direkt auf Aus: Erst läuft 15 Minuten nur der Lüfter (Parameter, 0 = sofort aus). Stellt in dieser Zeit jemand von Hand einen anderen Modus ein, endet der Nachlauf.
- **Loslassen:** Geht ihre Steuerung auf *Nur beobachten* oder *Aus*, oder wird Thriftherm deaktiviert oder gelöscht, wird eine Wärmepumpe, die noch auf den 30 °C des Schnell-Aufheizens oder dem Sollwert einer Trocknung steht, auf ihren kleinsten Sollwert gestellt und ruht dort. Stoppt Home Assistant, bekommt sie in diesem Fall das Ziel ihres Raums, denn danach regelt sie allein weiter. Eine auf kleinster Leistung oder eine, die inzwischen jemand gestellt hat, bleibt, wie sie ist.

### Am Minimum: wie die Wärmepumpe heizt

Die Wärmepumpe läuft mit kleinster Leistung, dort ist ihr COP am besten. Ihr Sollwert liegt ein Grad unter ihrer eigenen Messung und wird nur selten neu gesetzt, damit sie wenig piept. Der kälteste versorgte Raum entscheidet: Ein halbes Grad über Soll ruht der Verdichter (Sollwert 17 °C, das Gerät bleibt im Heizbetrieb), am Soll heizt sie wieder. Mehr Leistung gibt es mit *Schnell aufheizen*.

Was geschieht, wenn ein Raum nicht hinterherkommt, stellst du in der Feineinstellung unter *Wenn die Wärmepumpe allein nicht reicht* ein:

| Wahl | Was passiert |
|---|---|
| *Therme oder Fernwärme hilft* (Standard) | die Heizung hilft (Attribut `boiler_helps`); die Wärmepumpe bleibt am Minimum. Am schnellsten warm, ein paar Cent teurer |
| *Wärmepumpe dreht höher* | ohne Therme: ihr Sollwert geht langsam höher, ihr COP sinkt |
| *Langsamer warm werden* | nur kleinste Leistung; die Heizung hilft erst, wenn ein Raum ein halbes Grad unter Soll liegt |
| *Wärmepumpe allein* | nur kleinste Leistung; solange sie die günstigere Quelle ist, rufen ihre Räume die Therme nicht. Die anderen Räume heizt die Therme weiter, und alle, wenn die Wärmepumpe teurer oder gesperrt ist |

Ohne zentrale Heizung dreht sie immer höher, was auch gewählt ist; im Modus *Nur Wärmepumpe* ebenso, außer bei *Langsamer warm werden* oder *Wärmepumpe allein*. Läuft sie, ohne dass ein Raum gerade Wärme braucht, steht im *Entscheidungsgrund* „Wärmepumpe läuft durch“ und nicht „Kein Raum braucht Wärme“; ruht sie, weil die Heizung günstiger ist, steht dort „Heizung günstiger, die Wärmepumpe pausiert“ ([Einzelheiten](FUNKTIONSWEISE.md#am-minimum-halten)).

### Bad-Trocknung

Die Bad-Trocknung braucht die Wärmepumpe: nur in einem Raum, den sie beheizt und der einen Feuchtesensor hat. Das Raumformular bietet sie erst an, wenn eine Wärmepumpe eingerichtet ist.

- Getrocknet wird **nach** dem Duschen: Steigt die Feuchte, wartet Thriftherm, bis sie nicht mehr steigt. Dann bläst die Wärmepumpe mit höchster Lüfterstufe und der Trocknungs-Solltemperatur, ihr Sollwert 3 Grad über ihrer eigenen Messung im Stand, höchstens 60 Minuten (Standard) und nur, solange sie aktiv gesteuert wird, heizen und effizient laufen kann. Danach bekommt der Lüfter seine Stufe von vorher zurück, und sie läuft wieder mit kleinster Leistung.
- Der Knopf *Jetzt trocknen* startet sofort und für die volle Dauer, etwa für Wäsche oder einen nassen Boden; *Zurück zur Automatik* beendet die Trocknung. Wer nur den Knopf will, schaltet im Raumformular *Trocknung nach dem Duschen von selbst starten* aus.
- Hinaus geht die Feuchte nur durchs Fenster. Hat das Bad einen Fensterkontakt, bläst die Wärmepumpe deshalb nur, solange das Fenster offen ist: Fenster nach dem Duschen weit öffnen, dann trocknet die warme Luft Wände und Spiegel, und die Feuchte zieht ab. Bei geschlossenem Fenster wartet die Trocknung (*Wartet: Fenster öffnen*). Ohne Fensterkontakt bläst sie auch bei geschlossenem Fenster. Der Heizkörper folgt immer der normalen Fensterlogik.

Wann sie startet und endet: [Bad-Trocknung](FUNKTIONSWEISE.md#bad-trocknung).

## Dienste

| Dienst | Felder | Wofür |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature` (5–30 °C), `duration_min` (5–1440, Standard 120) | vorübergehend anderer Sollwert für einen Raum |
| `thriftherm.clear_override` | `room` (optional) | zurück zur Automatik: eine Übersteuerung beenden, ohne Raum alles von Hand Geänderte |
| `thriftherm.set_away` | `return_time` oder `clear_return_time` (beide optional) | Abwesenheit, mit Vorheizen zur Rückkehr oder ohne Ende |
| `thriftherm.clear_away` | – | zurück auf Automatik |
| `thriftherm.boost` | `room`, `duration_min` (5–240, Standard aus der Option *Dauer „Schnell aufheizen“*, 45) | einen Raum schnell aufheizen |
| `thriftherm.reset_learning` | `scope` (`boiler`, `heat_pump`, `rooms`), `rooms` | Gelerntes vergessen ([Gelerntes vergessen](#gelerntes-vergessen)); nur für Administratoren |

`room` ist der Raum-Schlüssel aus den Optionen (z. B. `badezimmer`).

Beispiel – abwesend bis Freitag 16:00:

```yaml
action: thriftherm.set_away
data:
  return_time: "2026-12-18 16:00:00"
```

## Wichtige Anzeigen

| Entität | Zeigt |
|---|---|
| *Entscheidungsgrund* | in Worten, warum das System tut, was es tut |
| *Empfohlene Wärmequelle* | Heizung (Therme oder Fernwärme), Wärmepumpe, beide oder keine |
| *Sicherheitsstatus* | *OK*; *Eingeschränkt*: ein Sensor fehlt oder die Therme hat eine Störung; *Fallback*: kein Raum oder keine verlässliche Raumtemperatur, nichts wird gesendet. Störungen der Wärmepumpe stehen in den Attributen, schränken den Status aber nur ein, wo die Wärmepumpe die einzige Wärmequelle ist |
| *Solltemperatur* je Raum | den Sollwert und unter *Grund*, woher er kommt |
| *Thermostat-Plan* je Raum | was an das Thermostat geht |
| *Therme-Steuerbefehl* | was die Therme bekommt; *Drehknopf regelt* heißt, es wird nichts gesendet und die Therme läuft über ihren Knopf. Zeigt auch Heizungspumpe und Status S.xx |
| *Lernstatus* | was gelernt wurde und warum das Lernen wartet ([Wo du es siehst](#wo-du-es-siehst)) |
| *Gas für Heizung*, *Gas für Warmwasser* | wie viel Gas (kWh) seit der Einrichtung in die Heizung und ins Warmwasser ging. Mit Gaszähler gemessen: Jeder Schritt des Zählers zählt für das, was die Therme in dem Moment tut; was verbrannt wurde, während Home Assistant nicht lief, steht unter *Nicht zugeordnet*. Ohne Gaszähler bei Vaillant-Thermen aus deren eigenen Zählern geschätzt ([Einrichtung, Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)). Beide eignen sich für das Energie-Dashboard |
| *Geplante Rückkehr* | wann du zurück bist; einstellbar (erst Datum, dann Uhrzeit), leer, wenn nichts geplant ist. *Vorläufig (23:59 als Platzhalter)* (`provisional`) ist `true`, solange nur ein Datum gewählt ist: Abwesend endet dann um 23:59, vorgeheizt wird noch nicht |

**Detail-Sensoren sind anfangs ausgeschaltet.** Abweichung vom Soll, Temperaturtrend, Taupunkt und absolute Feuchte je Raum, die Spreizung Vorlauf/Rücklauf, die geschätzte Wärmeleistung der Therme und die geschätzte Luftmenge der Wärmepumpe ändern sich fast bei jedem Zyklus und dienen der Auswertung, nicht der Regelung. Jede Änderung ist eine Zeile in der Datenbank von Home Assistant, deshalb sind sie bei einer neuen Installation ausgeschaltet; einschalten unter *Einstellungen → Geräte & Dienste → Entitäten*. Berechnet werden sie so oder so.

## Reparaturmeldungen

| Meldung | Bedeutung und Abhilfe |
|---|---|
| *Raum*: kein Heizfenster hinterlegt | weder Zeitplan-Helfer noch Zeitfenster – der Raum bleibt auf Absenkung. In den Raum-Optionen einen Zeitplan wählen. |
| *Raum*: Zeitplan-Helfer fehlt | der gewählte Zeitplan wurde gelöscht. Neu anlegen oder einen anderen wählen. |
| *Raum*: Heizkörperthermostat antwortet nicht | ein Ventil ist [stumm](#stumme-thermostate). Meist hängt es am Funk oder an der Batterie; der Raum fordert die Therme so lange nicht an. Batterie prüfen, Ventil neu anlernen oder aus- und einschalten. |
| Vorlauf der Therme über 100 °C | die Therme vom Fachbetrieb prüfen lassen ([Einzelheiten](FEHLERSUCHE.md#reparaturmeldung-vorlauf-der-therme-über-100-c)). |
| Keine Daten von der Therme | ebusd liefert nichts Brauchbares; nichts wird gesendet, die Therme läuft über ihren Drehknopf. ebusd-App und Adapter prüfen ([Einzelheiten](FEHLERSUCHE.md#reparaturmeldung-keine-daten-von-der-therme)). |
| Luftmenge der Wärmepumpe nicht kalibriert | keine Luftmengen-Kennlinie, daher kein COP; bis dahin gilt das Datenblatt oder, falls gewählt, die *COP-Werte dieses Modells*. |
| Heizung der Therme womöglich noch gesperrt | Thriftherm wurde beendet, während Zahl oder Schalter der Therme nicht erreichbar waren (Therme über Entitäten). Es gibt die Therme zurück, sobald beide wieder da sind; bis dahin die Heizung von Hand einschalten. |
| Home Assistant zeigt Temperaturen nicht mehr in °C | Thriftherm rechnet nur in °C und sendet keine Sollwerte (eine laufende Wärmepumpe wird gestoppt, eine Therme über Entitäten zurückgegeben), bis Home Assistant wieder das metrische Einheitensystem nutzt (*Einstellungen → System → Allgemein*). |
| Wärmepumpe versorgt Räume ohne Schläuche | keine Schläuche eingetragen, die warme Luft bleibt im Aufstellraum. Schläuche montieren und Schlauchfaktor setzen oder die Räume abwählen. |

Manches zeigt sich ohne Meldung: Ein Raumfühler, der seit sechs Stunden still ist, zählt nicht mehr (meldet sein Gerät noch anderes, etwa die Feuchte, erst nach einem Tag; mit *Raumfühler melden nur bei Änderung* erst nach dem *Max. Sensoralter*, Standard 720 Minuten), die eigene Temperatur des Ventils springt ein (hinter Better Thermostat das Ventil selbst, nicht Better Thermostat, das den Raumfühler nur wiederholt), und der *Sicherheitsstatus* geht auf *Eingeschränkt*.

*Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → Diagnose herunterladen* liefert eine Datei mit Konfiguration, aktuellem Zustand und Lernwerten für Fehlerberichte. Nur die Personen, deren Ankunft *Abwesend* beendet, fehlen darin – vor dem Veröffentlichen durchsehen.

Antworten auf häufige Fragen und was bei Problemen zu tun ist: [Fehlersuche und häufige Fragen](FEHLERSUCHE.md).

## Begriffe

| Begriff | Bedeutung |
|---|---|
| **Nur beobachten** | Thriftherm rechnet und zeigt, was es tun würde, steuert aber nichts. In Automationen heißt der Wert weiter `shadow` |
| **Vorlauf** | das Heizwasser, das von der Therme zu den Heizkörpern fließt, und seine Temperatur |
| **Rücklauf** | das abgekühlte Wasser, das von den Heizkörpern zurückkommt |
| **Spreizung** | Vorlauf minus Rücklauf. Groß heißt: Die Heizkörper nehmen viel Wärme ab; klein heißt: Das Wasser kommt fast so warm zurück, wie es ging |
| **Heizkurve** | welcher Vorlauf zu welcher Außentemperatur gehört: je kälter draußen, desto wärmer der Vorlauf. Bei Thriftherm eine Gerade durch zwei Punkte |
| **Korrektur, Höhe, Steigung** | was Thriftherm zur Heizkurve dazulernt: die Höhe verschiebt die ganze Kurve, die Steigung vor allem die kalten Tage |
| **Lastfall** | wie viele Räume gerade Wärme wollen: einer, wenige oder die Mehrheit |
| **Anfordern, Ruf** | ein Raum unter Soll ruft die Therme; ein *Halte-Ruf* bringt einen Raum, der sein Soll hält, von knapp darunter wieder hin |
| **Takten** | die Therme schaltet in kurzen Abständen an und aus, weil die Räume weniger Wärme abnehmen, als ihr kleinster Brenner liefert |
| **Brennwert / Heizwert** | ein Brennwertgerät gewinnt bei niedrigem Rücklauf zusätzlich Wärme aus dem Abgas, ein Heizwertgerät nicht |
| **eBUS, ebusd** | der Datenbus vieler Thermen und das Programm, das ihn mit Home Assistant verbindet. `SetMode` ist die einzige Nachricht, die Thriftherm dort schreibt |
| **Drehknopf** | der Vorlauf-Regler an der Therme; er bleibt die Obergrenze und regelt, wenn Thriftherm nichts sendet |
| **Better Thermostat** | eine Integration, die Heizkörperthermostate (Ventile) mit einem Raumfühler zu einem Raumthermostat verbindet |
| **COP** | Leistungszahl der Wärmepumpe: Wärme geteilt durch Strom. 3 heißt: aus 1 kWh Strom werden 3 kWh Wärme |
| **COP-Kennfeld** | der gelernte COP je Außentemperatur-Stufe von 2 Grad |
| **Break-even-COP** | der COP, ab dem Wärme aus der Wärmepumpe billiger ist als aus der Heizung |
| **Grenzpreis** | was eine eingesparte kWh Fernwärme wirklich spart; wegen des Heizkostenschlüssels weniger als der Preis auf der Rechnung |
| **Schlauchfaktor** | wie viel Luft die Wärmepumpe mit Schläuchen noch fördert, 1,0 = keine Schläuche |
| **Übersteuerung** | ein Sollwert, der für eine begrenzte Zeit statt des Zeitplans gilt |
| **Komfort / Absenkung** | die Temperatur in den Zeitplan-Blöcken und die niedrigere außerhalb davon |
| **Vorhalt** | was das Thermostat eines Raums über dessen Ziel bekommt, solange die Therme für den Raum läuft, damit es nicht vorher drosselt |
