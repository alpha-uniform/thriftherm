# Thriftherm einrichten

[English](SETUP.md) | **Deutsch**

Diese Anleitung bringt dich Schritt für Schritt von null zu einer laufenden Thriftherm-Installation. Du musst dich weder mit ebusd noch mit den Innereien von Home Assistant auskennen.

**An Therme und Thermostate geht nichts, bis du in Schritt 7 selbst auf *Aktiv* stellst.** Bis dahin rechnet Thriftherm nur und zeigt an, was es tun würde – du kannst also jeden Schritt gefahrlos ausprobieren.

| Schritt | Was du tust | Wo |
|---|---|---|
| [1](#1-was-du-brauchst) | Prüfen, ob alles da ist | – |
| [2](#2-ebusd-einrichten) | Home Assistant mit der Therme verbinden | ebusd-App |
| [3](#3-thriftherm-installieren) | Thriftherm installieren | HACS |
| [4](#4-integration-hinzufügen) | Den Einrichtungsdialog durchgehen | Home Assistant |
| [5](#5-better-thermostat-je-raum-optional) | Ein Better Thermostat je Raum | Better Thermostat |
| [6](#6-an-der-therme) | Drehknopf stellen, Pumpenmodus prüfen | an der Therme |
| [7](#7-erst-nur-planen-dann-aktiv) | Ein paar Tage beobachten, dann aktiv schalten | Home Assistant |
| [8](#8-wenn-etwas-nicht-klappt) | Wenn etwas nicht klappt | – |

Fernwärme oder keine eigene Therme? Dann überspringst du die Schritte 2 und 6; der Einrichtungsdialog lässt die Therme von selbst weg.

## 1. Was du brauchst

| Was | Nötig? | Hinweis |
|---|---|---|
| Therme mit eBUS | bei eigener Therme | für die Thermensteuerung |
| eBUS-Adapter | bei eigener Therme | zum Beispiel ein ebusd Adapter Shield, an die eBUS-Klemmen der Therme angeschlossen |
| Home Assistant 2026.9 oder neuer | ja | *Einstellungen → Über* zeigt deine Version. Temperaturen in °C (metrisches Einheitensystem): Thriftherm rechnet nur in °C und bricht die Einrichtung sonst ab |
| MQTT-Broker | bei eigener Therme | die App **Mosquitto broker** |
| App **ebusd** | bei eigener Therme | liest die Therme und schickt ihr den Sollwert |
| Ein Temperaturfühler je Raum | ja | jeder Sensor, der in Home Assistant die Raumtemperatur zeigt |
| Fensterkontakte | optional | solange ein Fenster offen ist, pausiert das Heizen in diesem Raum |
| Feuchtesensoren | optional | Schutz gegen Feuchte, während du weg bist |
| Heizkörperthermostate mit **Better Thermostat** | optional | nötig, wenn Thriftherm die Raumtemperaturen vorgeben soll; ohne sie funktioniert die Thermensteuerung trotzdem |
| Gaszähler-Werte | optional | Gesamtvolumen (m³) und Momentanfluss (m³/h), falls dein Gaszähler in Home Assistant ist |
| Außentemperaturfühler | optional | sonst dient eine Wetter-Entität |

**Ein zentraler Raumregler an der eBUS-Klemme muss weichen.** Hängt an der eBUS-Klemme deiner Therme schon ein Raumregler, klemmst du ihn ab; an dieselbe Klemme kommt der eBUS-Adapter. Thriftherm übernimmt die Aufgabe des Reglers, und zwei Regler am eBUS würden sich gegenseitig die Vorgaben überschreiben.

Die **Wärmepumpen**-Erweiterung steuert eine Klimaanlage im Heizbetrieb, erprobt an einer Midea PortaSplit. Du kannst sie ganz weglassen. Willst du sie nutzen, braucht sie die Climate-Entität der Wärmepumpe, einen Zwischenstecker, der ihre Leistung misst, Temperatur und Feuchte ihrer Ansaugluft und die Temperatur ihrer Ausblasluft.

### Was mit deiner Ausstattung geht

Im ersten Einrichtungsschritt wählst du, welche Heizung du hast. Schritte, die nicht passen, lässt der Dialog weg. Die Tabelle zeigt, was Thriftherm dann für dich tut.

| Funktion | Eigene Therme über ebusd (Kern) | + Better Thermostat | + Wärmepumpe | Nur Wärmepumpe, keine Therme | Zentral- oder Fernwärme¹ |
|---|:-:|:-:|:-:|:-:|:-:|
| **Räume** | | | | | |
| Solltemperaturen aus Zeitplänen (Komfort, Absenkung, Zeitplan-Helfer) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Vorheizen, damit der Raum zu Beginn des Zeitplans warm ist | ✓ | ✓ | ✓ | ✓ | ✓ |
| Abwesenheit mit Vorheizen zur Rückkehr | ✓ | ✓ | ✓ | ✓ | ✓ |
| Solltemperatur eines Raums vorübergehend übersteuern | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fenster offen: Der Raum fordert keine Wärme mehr an | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gelernte Aufheiz- und Abkühlrate je Raum | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sollwerte an die Thermostate; Drehen von Hand wird zur vorübergehenden Übersteuerung | – | ✓ | ✓ | – | ✓ |
| Knopf „Schnell aufheizen“ je Raum | – | ✓ | ✓ | ✓ | ✓ |
| Frostschutz, auch im Sommerbetrieb | ✓ | ✓ | ✓ | – | ✓ |
| **Therme** | | | | | |
| Vorlauf aus Heizkurve, Wärmebedarf der Räume und Spreizung Vorlauf/Rücklauf | ✓ | ✓ | ✓ | – | – |
| Heizbetrieb gesperrt, wenn kein Raum Wärme braucht; Warmwasser unberührt | ✓ | ✓ | ✓ | – | – |
| Gelernte Korrektur der Heizkurve je Raum: Der Raum, der am meisten braucht, bestimmt den Vorlauf | ✓ | ✓ | ✓ | – | – |
| Aufheiztempo nach Wunsch: mehr Vorlauf für einen Raum, der zu langsam warm wird, gelernt je Raum | ✓ | ✓ | ✓ | – | – |
| Warmwasser-Erkennung: Das Lernen pausiert nach dem Duschen | ✓ | ✓ | ✓ | – | – |
| Sommerbetrieb über eBUS: Heizung aus, Warmwasser läuft weiter | ✓ | ✓ | ✓ | – | – |
| Rückfall auf den Drehknopf der Therme, wenn Home Assistant ausfällt | ✓ | ✓ | ✓ | – | – |
| **Wärmepumpe** | | | | | |
| Steuerung der Wärmepumpe: langsam mit hohem COP, volle Leistung bei „Schnell aufheizen“ | – | – | ✓ | ✓ | ✓² |
| COP aus Luftmenge und Enthalpie der Luft; Erkennung von Abtauen und Vereisung | – | – | ✓ | ✓ | ✓² |
| Kostenvergleich mit der zentralen Wärmequelle | – | – | ✓ | – | ✓² |
| Bad-Trocknung | – | – | ✓ | ✓ | ✓² |
| **Immer** | | | | | |
| Erst *Nur planen*, *Aktiv* erst nach deiner Freigabe; ein Grund für jede Entscheidung; Reparaturmeldungen | ✓ | ✓ | ✓ | ✓ | ✓ |

✓ geht · – nicht verfügbar. Jede „+“-Spalte enthält die Spalten links davon. In der Spalte „Nur Wärmepumpe“ gelten die Raum-Zeilen für die Räume, die die Wärmepumpe erreicht.

¹ Zentralheizung, Fernwärme oder Wärme vom Vermieter: keine eigene Therme. Die Räume steuert Thriftherm über Better Thermostat, an die Wärmeversorgung wird nie etwas geschrieben.

² Mit der Wärmepumpen-Erweiterung. Der Kostenvergleich rechnet dann mit dem Grenzpreis der Fernwärme, siehe [Bei Fernwärme](#bei-fernwärme).

**Gut zu wissen:**

- **Eine Wärmepumpe ohne Therme funktioniert.** Im ersten Einrichtungsschritt *Keine – nur Wärmepumpe und Raumsteuerung* wählen.
- **Bad-Trocknung braucht die Wärmepumpe.** Sie trocknet mit der warmen Luft der Wärmepumpe, nur in einem Raum, den die Wärmepumpe beheizt. Einzelheiten stehen in der [Anleitung, Abschnitt 7](ANLEITUNG.md#7-wärmepumpe-optional).
- **Alles in den Therme-Zeilen braucht eine eigene Therme über ebusd**, oder als Vorschau eine Therme über zwei Entitäten wie bei OpenTherm ([Schritt 4.3a](#43a-therme-ohne-ebusd-über-entitäten-vorschau)). Das betrifft Vorlauf, Heizsperre, gelernte Heizkurve, Warmwasser-Erkennung, Sommerbetrieb über eBUS und den Rückfall auf den Drehknopf. Bei Fernwärme oder ohne Therme wird nie etwas über eBUS gesendet.

### Bei Fernwärme

Bei Fernwärme bringt Thriftherm vor allem mit einer Wärmepumpe etwas. Dann entscheidet es laufend, ob Wärme aus Strom oder aus der Fernwärme gerade günstiger ist, und lässt die Wärmepumpe nur heizen, wenn sie sich rechnet. Ausgenommen sind „Schnell aufheizen“, Bad-Trocknung und Lernläufe. Ohne Wärmepumpe bleibt nur die Raumsteuerung. Die Ventile regelt dabei ohnehin Better Thermostat, Thriftherm ergänzt Zeitpläne mit Vorheizen und die Abwesenheit. Viel mehr, als Better Thermostat mit einem Zeitplan schon kann, ist das nicht.

Der Kostenvergleich rechnet mit dem **Grenzpreis** der Wärme, nicht mit dem Preis, der auf der Rechnung steht. Nach der Heizkostenverordnung folgt nur der Verbrauchsanteil der Gebäudeabrechnung deinem eigenen Zähler, und vom Flächenanteil trägt deine Wohnung ihren Miteigentumsanteil. Eine gesparte kWh spart deshalb weniger, als der Preis je kWh vermuten lässt. Das *erhöht* den COP, den die Wärmepumpe erreichen muss. Beide Anteile stellst du bei den Preisen ein ([Schritt 4.2](#42-preise)); wer sie ignoriert, rechnet die Wärmepumpe schön.

## 2. ebusd einrichten

### 2.1 MQTT-Broker installieren

1. *Einstellungen → Apps* öffnen und **Mosquitto broker** installieren. Starten.
2. Home Assistant bietet danach unter *Einstellungen → Geräte & Dienste* die Integration **MQTT** an. Auf *Konfigurieren* klicken und bestätigen.

**Prüfen:** *Einstellungen → Geräte & Dienste* zeigt **MQTT**.

### 2.2 ebusd-App installieren

1. In *Einstellungen → Apps* den App Store öffnen, dann ⋮ → *Repositories* und `https://github.com/LukasGrebe/ha-addons` hinzufügen.
2. **ebusd** installieren. Noch nicht starten.

### 2.3 ebusd-App konfigurieren

Den Reiter **Konfiguration** der ebusd-App öffnen:

| Einstellung | Was du einträgst |
|---|---|
| Adapter / Gerät | deinen eBUS-Adapter: das USB-Gerät, oder bei einem Netzwerk-Adapter seine Adresse, zum Beispiel `ens:192.0.2.10:9999` |
| Kommandozeilen-Optionen | `--accesslevel=*` ergänzen – nötig, damit Thriftherm der Therme ihren Sollwert schicken kann. Die Testanlage hat zusätzlich `--mqttretain`: Dann bleiben alle Werte im Broker liegen und stehen nach einem Neustart von Home Assistant sofort wieder da – auch veraltete, siehe den Hinweis unten |
| MQTT | nichts einzutragen: Die App findet den Mosquitto broker von Home Assistant selbst. Einen anderen Broker gibst du in den Kommandozeilen-Optionen an (`--mqtthost=…`, `--mqttport=…`, `--mqttuser=…`, `--mqttpass=…`) |
| `mqtt-hassio.cfg` kopieren (`seed_mqtt_cfg`) | an – damit liegt eine bearbeitbare Kopie der MQTT-Konfiguration im Ordner der App |

Speichern und die App starten.

> **Feste Adressen vergeben.** Ein Netzwerk-Adapter braucht eine feste IP-Adresse. Lege im Router eine **DHCP-Reservierung** für den eBUS-Adapter an, ebenso für jedes andere Netzwerkgerät, von dem Thriftherm Werte bekommt, etwa einen WLAN-Gaszähler.
> Warum das wichtig ist, zeigt eine Nacht auf der Testanlage: Der Router vergab die Adressen neu, der eBUS-Adapter bekam eine andere IP, und der WLAN-Gaszähler bekam die alte Adresse des Adapters.
> - ebusd fand den Adapter nicht mehr („unable to open …“ im Log). Thriftherm gab die Therme nach wenigen Minuten ab, und sie heizte eine gute Stunde lang nach ihrem Drehknopf.
> - ebusd klopfte alle 5 Sekunden beim Gaszähler an. Der verlor dadurch immer wieder seine MQTT-Verbindung, und seine Werte standen in Home Assistant auf „nicht verfügbar“.
> - Home Assistant zeigte die ganze Zeit die letzten gespeicherten eBUS-Werte weiter an (Vorlauf 22 °C, Status 31), weil ebusd seine Werte im Broker hinterlegt. Tatsächlich lag der Vorlauf bei fast 50 °C.
>
> Ob ebusd die Therme wirklich erreicht, zeigt nur *ebusd Signal* (`binary_sensor.…_global_signal`). Fällt es länger als zehn Minuten aus, meldet Thriftherm das unter Einstellungen → Reparaturen (*Keine Daten von der Therme*). Wer eine Push-Nachricht möchte, legt dafür eine Automation auf dieses Signal an, zum Beispiel „5 Minuten aus“.

### 2.4 Heizungspumpe und Status-Nummer sichtbar machen

Von sich aus schickt ebusd die Heizungspumpe (`WP`) und die Status-Nummer der Therme (`Statenumber`) nicht an Home Assistant. Eine Zeile ändert das:

1. `mqtt-hassio.cfg` im Ordner der ebusd-App öffnen (unter `addon_configs`, der Ordner, der auf `_ebusd` endet), zum Beispiel mit der App *File editor* oder *Studio Code Server*.
2. Die Zeile suchen, die mit `filter-name` beginnt, und `|^wp$|statenumber` hinten an ihren Wert anhängen (der Filter ignoriert Groß- und Kleinschreibung). Beispiel:

   ```
   filter-name = ...bisheriger Wert...|^wp$|statenumber
   ```

3. Speichern und die **ebusd-App neu starten**.

**Prüfen:** Nach bis zu zehn Minuten zeigt *Einstellungen → Geräte & Dienste → MQTT* ein ebusd-Gerät für deine Therme (zum Beispiel *ebusd bai*) mit Entitäten wie **FlowTemp temp**, **ReturnTemp temp**, **WP** und **Statenumber**. `WP` und `Statenumber` erscheinen oft erst nach ein paar Abfragen.

Ist Thriftherm schon eingerichtet? Dann *Thriftherm → Konfigurieren → Gastherme und Gaszähler* öffnen: Die leeren Felder *Heizungspumpe läuft (ebusd WP, on/off)* und *Status-Nummer der Therme (ebusd Statenumber, S.xx)* sind mit den gefundenen Entitäten ausgefüllt. Prüfen und absenden.

### 2.5 Weniger Werte abfragen, damit Vorlauf und Rücklauf frischer sind

Optional, für Fortgeschrittene. ebusd fragt die Werte der Therme reihum ab; je kürzer seine Liste, desto öfter kommen Vorlauf und Rücklauf (auf der Testanlage pausieren sie nach dem Kürzen nur noch 1 Minute 48 Sekunden statt 4 Minuten). Für die Regelung brauchst du das nicht, Thriftherm holt sich seine Werte selbst; es hilft, wenn du sie zeitnah im Dashboard sehen willst. Was weg kann, was bleiben muss und wie du kürzt: [Weniger ebusd-Werte abfragen](EBUSD.md).

## 3. Thriftherm installieren

**Mit HACS (empfohlen):**

Am schnellsten mit diesem Knopf, er öffnet das Repository direkt in HACS; dann *Herunterladen* klicken und bei Schritt 4 unten weitermachen:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alpha-uniform&repository=thriftherm&category=integration)

Oder von Hand:

1. **HACS** öffnen, oben rechts ⋮ → *Benutzerdefinierte Repositories*.
2. Repository: `https://github.com/alpha-uniform/thriftherm`, Typ: **Integration**. *Hinzufügen* klicken.
3. In HACS nach **Thriftherm** suchen, öffnen und *Herunterladen* klicken.
4. Home Assistant neu starten (*Einstellungen → System → ⋮ → Home Assistant neu starten*).

**Von Hand:** den Ordner `custom_components/thriftherm` aus dem Repository nach `config/custom_components/` deiner Home-Assistant-Installation kopieren, dann neu starten.

**Prüfen:** *Einstellungen → Geräte & Dienste → Integration hinzufügen* findet **Thriftherm**.

## 4. Integration hinzufügen

*Einstellungen → Geräte & Dienste → Integration hinzufügen* öffnen und **Thriftherm** wählen. Der Dialog hat bis zu sechs Schritte. Alles lässt sich später unter *Thriftherm → Konfigurieren* (den Optionen) ändern.

### 4.1 Was für eine Heizung hast du?

| Feld | Was du wählst |
|---|---|
| Art der Heizung | **Eigene Gas- oder Ölheizung** für die Thermensteuerung über ebusd. Bei *Zentralheizung, Fernwärme oder Wärme vom Vermieter* oder *Keine – nur Wärmepumpe und Raumsteuerung* entfällt der Thermen-Schritt |

Änderst du die Art später in den Optionen, folgen die Preise für die neue Art und bei eigener Therme der Thermen-Schritt; gespeichert wird erst nach dem letzten Schritt.

### 4.2 Preise

Die Werte stehen auf deinen Abrechnungen. Die vorausgefüllten Zahlen sind nur Beispiele.

| Feld | Wo du es findest |
|---|---|
| Strompreis | Stromrechnung, €/kWh |
| Gaspreis | Gasrechnung, €/kWh |
| Effektiver Wirkungsgrad der Therme (Hs) | im Zweifel den Standardwert lassen |
| Brennwert Hs | Gasrechnung, kWh/m³ |
| Zustandszahl (Z) | Gasrechnung |

Bei Fernwärme erscheinen stattdessen *Wärmepreis laut Abrechnung*, *Davon nach Verbrauch abgerechnet* und *Dein Miteigentumsanteil (MEA) am Haus*.

### 4.3 Gastherme (ebusd) und Gaszähler

Der Schritt *Gastherme (ebusd) und Gaszähler* sucht nach deiner ebusd-Therme und trägt ein, was er findet: die Werte der Therme, ihren ebusd-Kreis, das eBUS-Signal und, wenn genau einer passt, den Gaszähler. Die Beschreibung des Schritts sagt, was gefunden wurde, zum Beispiel *Gefunden: Vaillant (bai), 6 von 6 Werten – bitte prüfen.* Prüfe die Felder und mach weiter. Wurde nichts gefunden, steht dort *Keine Therme über ebusd gefunden – bitte wähle die Entitäten selbst aus.*

| Feld | Was du wählst oder einträgst |
|---|---|
| Vorlauftemperatur | ebusd **FlowTemp temp** |
| Rücklauftemperatur | ebusd **ReturnTemp temp** |
| Therme-Status aus Status01 (off/on/overrun/hwc) | ebusd **Status01 pumpstate** (Status01 liefert mehrere Entitäten; die anderen sind Temperaturen) |
| Heizungspumpe läuft (ebusd WP, on/off) | ebusd **WP** |
| Status-Nummer der Therme (ebusd Statenumber, S.xx) | ebusd **Statenumber** |
| Warmwasser aktiv (ebusd hwcmode oder ein Binärsensor) | ebusd **Status02 hwcmode** |
| eBUS-Signal (Binärsensor) | **das Signal von ebusd selbst** (`binary_sensor.…_global_signal`), nicht den Verbindungssensor des Adapters: Fällt der Adapter aus dem WLAN, bleibt sein eigener Sensor auf seinem letzten Wert „an“ stehen (gemessen) |
| Nenn-Umlaufwassermenge der Pumpe | den Standardwert lassen, außer die Anleitung deiner Therme sagt etwas anderes |
| Gaszähler Volumen (m³, Gesamt), Gaszähler Momentanfluss (m³/h) | deine Gaszähler-Entitäten, falls vorhanden; sonst leer lassen. Die Steuerung braucht keinen Gaszähler: Brennerlauf und Warmwasser erkennt sie an Pumpe und Statuscode. Ohne Zähler gibt es die Sensoren *Gasleistung* und *Gasenergie* nicht |
| Ohne Gaszähler: Nennwärmebelastung der Therme (Typenschild, kW) | nur ohne Gaszähler und nur bei einer Vaillant-Therme: der Wert **Q** oder **Qn** vom Typenschild, bei einem Bereich der größere (atmoTEC plus VCW 194/4-5: 22,2). Damit schätzt Thriftherm *Gas für Heizung* und *Gas für Warmwasser* aus den Zählern der Therme. Dafür müssen die sechs ebusd-Nachrichten PrEnergySumHc1 bis 3 und PrEnergySumHwc1 bis 3 in Home Assistant ankommen, schließ sie also nicht aus ([Weniger ebusd-Werte abfragen](EBUSD.md#was-thriftherm-braucht)); Thriftherm findet sie selbst. Auf der Testanlage lag die Schätzung in 32 Tagen (95,8 kWh am Gaszähler) insgesamt 1 % über dem Zähler und an keinem Tag mehr als 0,3 kWh daneben. Mit Gaszähler leer lassen |
| Warmwasser bereithalten (Warmhalten der Kombitherme, Speicherladung) | eingeschaltet lassen. Ausschalten nur bei einer Kombitherme ohne Speicher, die dich nachts mit ihrem Warmhalten stört (etwa ein Brennerstart pro Stunde ohne Heizbedarf); wirkt nur bei aktiver Thermensteuerung |
| ebusd-Kreis der Therme (meist bai) | `bai` lassen, außer dein ebusd-Gerät nutzt einen anderen Kreisnamen |
| Heizkurve: Vorlauf bei -10 °C außen | mit dem Standardwert 55 °C beginnen |
| Heizkurve: Vorlauf bei +15 °C außen | mit dem Standardwert 30 °C beginnen |
| Minimale Vorlauftemperatur | **etwa 45 °C bei einer Heizwerttherme, etwa 30 °C bei einem Brennwertgerät** |
| Maximale Vorlauftemperatur | deine Drehknopf-Stellung aus Schritt 6 (zum Beispiel 55 °C) oder etwas darunter; höhere Werte wirken nicht, denn der Knopf ist die Obergrenze |
| Aufheiztempo (Grad pro Stunde, 0 = aus) | mit 0 beginnen. Wird dir ein Raum nach dem Hochstellen zu langsam warm, 0,5 bis 2 einstellen; mehr Tempo kostet mehr Gas ([Anleitung, Aufheiztempo](ANLEITUNG.md#aufheiztempo)) |
| Anteil der Räume, die anfordern müssen, bevor die Spreizung die Kurve bewertet | den Standardwert 0,5 lassen |
| Eigene Heizkurve je Raum, für einen Raum, wenige Räume und die Mehrheit | eingeschaltet lassen |
| Aktive Thermensteuerung freigeben (sonst nur planen) | **vorerst aus lassen** – das schaltest du in Schritt 7 ein |

**Warum das Minimum vom Gerätetyp abhängt:** Ein Brennwertgerät profitiert von niedrigem Vorlauf, ein Heizwertgerät spart dort kaum etwas, und bei kurzen Brennerläufen kommt die Wärme womöglich nicht bis zu den fernen Heizkörpern – siehe [Anleitung, Abschnitt 5](ANLEITUNG.md#5-thermensteuerung-eigene-therme-über-ebusd).

Die Heizkurve muss nicht perfekt sein: Thriftherm lernt eine Korrektur dazu, für Höhe und Steigung je bis zu ±10 Grad. Das Formular lehnt nur eine Kurve ab, die bei -10 °C niedriger liegt als bei +15 °C, und ein Minimum über dem Maximum.

### 4.3a Therme ohne ebusd: über Entitäten (Vorschau)

> **Vorschau.** Noch an keiner echten Therme gelaufen; nicht darauf verlassen.

Hat deine Therme kein eBUS, lässt sie sich über zwei Entitäten steuern, die schon in Home Assistant sein müssen – etwa von einem OpenTherm-Adapter mit ESPHome:

| Feld | Was du wählst |
|---|---|
| Ohne ebusd: Vorlauf-Sollwert der Therme | eine **Zahl-Entität**, die den Vorlauf vorgibt |
| Ohne ebusd: Heizung an/aus | einen **Schalter**, der die Heizung freigibt und sperrt (Warmwasser bleibt davon unberührt) |
| Brenner an | optional: Binärsensor „Flamme“ oder ein Sensor „Brennerleistung“ in Prozent |
| Warmwasser aktiv | optional: Binärsensor „Warmwasser aktiv“ |
| Heizungspumpe läuft | optional: Binärsensor „Heizung aktiv“ |
| Vorlauftemperatur | der Temperatursensor des Vorlaufs (nötig) |

Sind Zahl und Schalter gewählt, geht nichts mehr an ebusd; der ebusd-Kreis und *Warmwasser bereithalten* haben dann keine Wirkung. Es braucht immer beide.

Ein Unterschied zu ebusd: Ein ebusd-Befehl läuft nach einigen Minuten ab, und die Therme heizt wieder nach ihrem Drehknopf. Eine Entität behält, was ihr gesendet wurde. Thriftherm gibt die Therme deshalb selbst zurück (Heizung an), sobald es nicht mehr steuert: beim Umschalten auf *Nur planen* und ohne Thermendaten mit dem Vorlauf der Heizkurve, der dann weiter der Außentemperatur folgt; beim Beenden von Home Assistant mit dem maximalen Vorlauf, so wie ein Drehknopf stünde. Entitäten, die Thriftherm nie gesteuert hat, fasst es nicht an. Stürzt Home Assistant ab oder fällt der Strom aus, bleibt der letzte Zustand stehen – auch eine gesperrte Heizung. Prüfe, ob dein Adapter für diesen Fall eine eigene Zeitüberwachung hat.

### 4.4 Wärmepumpe (optionales Zusatzmodul)

Erprobt ist diese Erweiterung an einer Midea PortaSplit. Ohne Wärmepumpe: **Alle Felder leer lassen und auf *Absenden* klicken.** Eine Wärmepumpe lässt sich später in den Optionen ergänzen.

Ist eine Wärmepumpe eingerichtet, gibt es unter *Konfigurieren* den zusätzlichen Punkt **Wärmepumpe: Feineinstellung**. Dort stehen die Freigabe der aktiven Steuerung, der Sollwert-Abstand, Mindestlaufzeit und Mindestpause und, in eingeklappten Abschnitten, Bad-Trocknung, COP-Messung und Abtauen. Ohne Wärmepumpe erscheint der Punkt nicht.

### 4.5 Außenbedingungen

| Feld | Was du wählst |
|---|---|
| Außentemperatursensoren (Priorität) | deine Außenfühler, den am besten geeigneten zuerst; leer lassen, wenn du keinen hast |
| Außenfeuchtesensor | optional |
| Wetter-Entität (Fallback) | deine Wetter-Entität, zum Beispiel die, die Home Assistant bei der Installation angelegt hat (oft *Forecast Home*) |

Wähle mindestens einen Außenfühler oder die Wetter-Entität – die Heizkurve braucht die Außentemperatur.

### 4.6 Räume

Du legst einen Raum pro Seite an. Mit dem Haken **Weiteren Raum hinzufügen** kommt die nächste Seite; den letzten Raum ohne Haken absenden, dann ist die Einrichtung fertig.

| Feld | Was du einträgst |
|---|---|
| Raumname | zum Beispiel `Wohnzimmer` |
| Priorität (1 = höchste) | wichtigere Räume bekommen eine kleinere Zahl; 5 passt für den Anfang |
| Raumtemperatursensor | der Temperaturfühler des Raums (Pflicht) |
| Raumfeuchtesensor | optional |
| Fenster-/Türkontakte | optional, alle Kontakte des Raums |
| Thermostat (TRV oder Better Thermostat) | die **Better-Thermostat**-Entität des Raums. Noch kein Better Thermostat? Leer lassen und später ergänzen (Optionen → *Räume* → Raum → *Bearbeiten*) |
| Leistungssensoren interner Wärmequellen | optional, leer lassen |
| Zeitplan-Helfer (optional; ersetzt die Komfortfenster unten) | ein Zeitplan-Helfer von Home Assistant, dessen Blöcke die Komfortzeiten sind |
| Komforttemperatur | Temperatur, wenn der Raum genutzt wird, zum Beispiel 20 °C |
| Absenktemperatur | Temperatur außerhalb der Komfortzeiten, zum Beispiel 17 °C; nie über der Komforttemperatur |
| Komfortzeiten Mo–Fr / Sa–So | Zeiten als `HH:MM-HH:MM`, mehrere mit Komma getrennt, zum Beispiel `06:00-08:00,17:00-22:00` |

Der Raum ist warm, *wenn* eine Komfortzeit beginnt: Thriftherm fängt rechtzeitig vorher an zu heizen.

Nur wenn eine Wärmepumpe eingerichtet ist, zeigt das Formular zusätzlich *Wird von der Wärmepumpe beheizt*, *Bad-Trocknung mit der Wärmepumpe* und *Trocknung nach dem Duschen von selbst starten*. Die Bad-Trocknung läuft über die Wärmepumpe, ohne sie erscheint keins der Felder. Mit ihr bekommt der Raum den Knopf *Jetzt trocknen*; ist der dritte Schalter aus, trocknet nur der Knopf. Sie braucht außerdem *Wird von der Wärmepumpe beheizt* und den Raumfeuchtesensor, sonst lehnt das Formular sie ab.

**Prüfen:** Nach dem letzten Raum zeigt *Einstellungen → Geräte & Dienste* **Thriftherm**. Sein Gerät zeigt *Thermensteuerung* und *Raumsteuerung* auf **Nur planen (Beta)**.

## 5. Better Thermostat je Raum (optional)

Überspringen, wenn du keine smarten Heizkörperthermostate hast. Die Thermensteuerung funktioniert auch ohne; dann die Handventile weit genug öffnen und die Vorlauftemperatur die Arbeit machen lassen.

Für jeden Raum:

1. **Better Thermostat** aus HACS installieren, falls noch nicht geschehen.
2. Ein Better Thermostat für den Raum anlegen: die Heizkörperthermostate des Raums wählen, **denselben Raumtemperaturfühler**, den auch Thriftherm bekommen hat, und die **Fensterkontakte** des Raums.
3. Gibst du ihm einen Außentemperaturfühler, die Außentemperatur, bei der es abschaltet, **hoch genug einstellen, zum Beispiel 25 °C** – oder den Außenfühler leer lassen. Sonst sperrt Better Thermostat Heizen, das Thriftherm anfordert.
4. In Thriftherm dieses Better Thermostat als Thermostat des Raums wählen (Optionen → *Räume* → Raum → *Bearbeiten*), falls du es in Schritt 4.6 leer gelassen hast.
5. Soll ein Dreh am Drehknopf des Ventils als Handänderung zählen, in Better Thermostat den Schalter **Kindersicherung** des Raums **ausschalten**. Ist er an, setzt Better Thermostat das Ventil zurück, und der Dreh zählt nicht.

Ein Thermostat, das du von Hand **ausschaltest**, lässt Thriftherm in Ruhe, und dieser Raum fordert die Therme nicht mehr an.

### Versatile Thermostat statt Better Thermostat (Vorschau)

> **Vorschau.** Nach der Dokumentation von Versatile Thermostat gebaut und mit nachgestellten Zuständen getestet, noch nicht mit einer echten Installation.

Als Thermostat eines Raums kannst du auch die Klima-Entität von [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat) wählen. Thriftherm schreibt ihr nur die Solltemperatur.

- **Eigene Regeln bleiben bei Versatile Thermostat.** Senkt es den Sollwert selbst ab, weil ein Fenster offen ist, es Last abwirft oder im Sicherheitsmodus ist, hält Thriftherm das nicht für eine Änderung von Hand und schreibt auch nicht dagegen; Versatile Thermostat stellt den angeforderten Wert danach selbst wieder her. Der Raum zeigt solange *Das Thermostat wendet eine eigene Regel an*.
- **Schaltet es sich bei offenem Fenster aus,** gilt das wie ein ausgeschaltetes Thermostat: Der Raum fordert die Therme nicht an.
- **Die Funktion „Zentralheizung“ von Versatile Thermostat ausgeschaltet lassen,** wenn Thriftherm die Therme steuert. Sonst schalten zwei an derselben Therme.
- **Voreinstellungen (Komfort, Eco, Boost) und Anwesenheit** in Versatile Thermostat verstellen den Sollwert als Anforderung. Das liest Thriftherm wie eine Änderung von Hand und macht daraus eine Übersteuerung. Den Zeitplan führst du deshalb in Thriftherm, nicht dort.
- Die Ventile hinter Versatile Thermostat findet Thriftherm selbst und meldet, wenn eines verstummt.

## 6. An der Therme

1. **Den Drehknopf auf die höchste Vorlauftemperatur stellen, die du zulassen willst**, zum Beispiel 55 °C. Der Knopf bleibt die Obergrenze und ist das, worauf die Therme zurückfällt, wenn Home Assistant ausfällt. Nicht an den linken Anschlag drehen: An der getesteten Therme ist das Sommerbetrieb und sperrt die Heizung, egal was Thriftherm sendet.
2. **Einmal den Pumpenmodus prüfen.** Mit der Werkseinstellung bleibt die Pumpe womöglich zusammen mit dem Brenner stehen: Die Therme taktet den ganzen Tag, aber die Heizkörper weit hinten im Kreis bleiben kalt. Warum das passiert: [Anleitung, Abschnitt 5](ANLEITUNG.md#warum-der-pumpenmodus-zählt).
   - Vaillant: d.18 = 0 „Nachlauf“ ist die Werkseinstellung – die Pumpe läuft nur mit dem Brenner, plus kurzem Nachlauf. d.18 = 1 „durchlaufend“ – die Pumpe läuft, solange der Heizbetrieb freigegeben ist, und steht, wenn Thriftherm den Heizbetrieb sperrt.
   - Ändern im Servicemenü der Therme oder einmalig über ebusd (Nachricht `HcPumpMode`, Werte `post_run`, `permanent`, `winter`).
   - Vorher in die Anleitung der Therme schauen; andere Hersteller nutzen andere Codes.

   Thriftherm schreibt nie Installateur-Parameter: Den Pumpenmodus zu ändern ist deine Entscheidung.

## 7. Erst nur planen, dann aktiv

### 7.1 Ein paar Tage „Nur planen“

Alles auf *Nur planen* lassen und ein- bis zweimal am Tag auf diese Entitäten schauen:

| Entität | Worauf du achtest |
|---|---|
| *Therme-Steuerbefehl* | passt *Heizen* / *Heizbetrieb gesperrt* zu dem, was die Räume brauchen? Ist die Vorlauftemperatur plausibel? |
| *Thermostat-Plan* (je Raum) | die Sollwerte, die es jedem Thermostat geben würde |
| *Entscheidungsgrund* | in Worten, warum Thriftherm tut, was es tut |

Sieht etwas falsch aus, in den Optionen anpassen, bevor du aktiv schaltest.

### 7.2 Aktiv schalten

1. Die Better-Thermostat-Entitäten auf **Heizen** schalten.
2. Thriftherm → *Konfigurieren* → **Gastherme und Gaszähler**: *Aktive Thermensteuerung freigeben (sonst nur planen)* einschalten. Absenden.
3. Thriftherm → *Konfigurieren* → **Regelparameter**, Abschnitt *Freigaben*: *Aktive Raumsteuerung freigeben (Sollwerte an die Thermostate schreiben)* einschalten. Absenden.
4. Am Thriftherm-Gerät **Thermensteuerung** und **Raumsteuerung** auf **Aktiv** stellen.

**Nach ein paar Minuten prüfen:**

| Entität | Erwartet |
|---|---|
| *Therme-Steuerbefehl* | Attribut *Befehle werden gesendet* ist an |
| Better-Thermostat-Entitäten | zeigen die neuen Sollwerte aus dem *Thermostat-Plan* |
| *Sicherheitsstatus* | OK |

Alles zum Alltag – Modi, Zeitpläne, Abwesenheit, Lernen – steht in der [Anleitung](ANLEITUNG.md).

## 8. Wenn etwas nicht klappt

Keine ebusd-Werte, eine Therme, die den Sollwert nicht annimmt, ein Thermostat, das hängt: Was dann zu tun ist, steht in der [Fehlersuche](FEHLERSUCHE.md). Sie ist nach dem geordnet, was du siehst, und beantwortet auch häufige Fragen aus dem Alltag.
