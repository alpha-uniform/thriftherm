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
| Home Assistant 2026.9 oder neuer | ja | *Einstellungen → Über* zeigt deine Version |
| MQTT-Broker | bei eigener Therme | die App **Mosquitto broker** |
| App **ebusd** | bei eigener Therme | liest die Therme und schickt ihr den Sollwert |
| Ein Temperaturfühler je Raum | ja | jeder Sensor, der in Home Assistant die Raumtemperatur zeigt |
| Fensterkontakte | optional | solange ein Fenster offen ist, pausiert das Heizen in diesem Raum |
| Feuchtesensoren | optional | Schutz gegen Feuchte, während du weg bist |
| Heizkörperthermostate mit **Better Thermostat** | optional | nötig, wenn Thriftherm die Raumtemperaturen vorgeben soll; ohne sie funktioniert die Thermensteuerung trotzdem |
| Gaszähler-Werte | optional | Gesamtvolumen (m³) und Momentanfluss (m³/h), falls dein Gaszähler in Home Assistant ist |
| Außentemperaturfühler | optional | sonst dient eine Wetter-Entität |

**Ein zentraler Raumregler an der eBUS-Klemme muss weichen.** Hängt an der eBUS-Klemme deiner Therme schon ein Raumregler, klemmst du ihn ab; an dieselbe Klemme kommt der eBUS-Adapter. Thriftherm übernimmt die Aufgabe des Reglers, und zwei Regler am eBUS würden sich gegenseitig die Vorgaben überschreiben.

Die **Wärmepumpen**-Erweiterung ist eine Vorschau und noch nicht mit einer echten Wärmepumpe getestet. Du kannst sie ganz weglassen. Willst du sie ausprobieren, braucht sie die Climate-Entität der Wärmepumpe, einen Zwischenstecker, der ihre Leistung misst, Temperatur und Feuchte ihrer Ansaugluft und die Temperatur ihrer Ausblasluft.

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
| Kommandozeilen-Optionen | `--accesslevel=*` ergänzen – nötig, damit Thriftherm der Therme ihren Sollwert schicken kann |
| MQTT | die App findet den Mosquitto broker automatisch. Wenn nicht, Host, Benutzer und Passwort eintragen |
| `mqtt-hassio.cfg` kopieren (`seed_mqtt_cfg`) | an – damit liegt eine bearbeitbare Kopie der MQTT-Konfiguration im Ordner der App |

Speichern und die App starten.

### 2.4 Heizungspumpe und Status-Nummer sichtbar machen

Von sich aus schickt ebusd die Heizungspumpe (`WP`) und die Status-Nummer der Therme (`Statenumber`) nicht an Home Assistant. Eine Zeile ändert das:

1. `mqtt-hassio.cfg` im Ordner der ebusd-App öffnen (unter `addon_configs`, der Ordner, der auf `_ebusd` endet), zum Beispiel mit der App *File editor* oder *Studio Code Server*.
2. Die Zeile suchen, die mit `filter-name` beginnt, und `|^wp$|statenumber` hinten an ihren Wert anhängen (der Filter ignoriert Groß- und Kleinschreibung). Beispiel:

   ```
   filter-name = ...bisheriger Wert...|^wp$|statenumber
   ```

3. Speichern und die **ebusd-App neu starten**.

**Prüfen:** Nach ein paar Minuten zeigt *Einstellungen → Geräte & Dienste → MQTT* ein ebusd-Gerät für deine Therme (zum Beispiel *ebusd bai*) mit Entitäten wie **FlowTemp temp**, **ReturnTemp temp**, **WP** und **Statenumber**. `WP` und `Statenumber` erscheinen oft erst nach ein paar Abfragen.

Ist Thriftherm schon eingerichtet? Dann *Thriftherm → Konfigurieren → Gastherme und Gaszähler* öffnen: Die leeren Felder *Heizungspumpe läuft (ebusd WP, on/off)* und *Status-Nummer der Therme (ebusd Statenumber, S.xx)* sind mit den gefundenen Entitäten ausgefüllt. Prüfen und absenden.

## 3. Thriftherm installieren

**Mit HACS (empfohlen):**

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
| Therme-Status aus Status01 (off/on/overrun/hwc) | ebusd **Status01** |
| Heizungspumpe läuft (ebusd WP, on/off) | ebusd **WP** |
| Status-Nummer der Therme (ebusd Statenumber, S.xx) | ebusd **Statenumber** |
| Warmwasser-Modus | ebusd **Status02 hwcmode** |
| eBUS-Signal (Binärsensor) | der *signal*-Verbindungssensor von ebusd oder deinem Adapter, falls vorhanden; sonst leer lassen |
| Nenn-Umlaufwassermenge der Pumpe | den Standardwert lassen, außer die Anleitung deiner Therme sagt etwas anderes |
| Gaszähler Volumen (m³, Gesamt), Gaszähler Momentanfluss (m³/h) | deine Gaszähler-Entitäten, falls vorhanden; sonst leer lassen |
| ebusd-Kreis der Therme (meist bai) | `bai` lassen, außer dein ebusd-Gerät nutzt einen anderen Kreisnamen |
| Heizkurve: Vorlauf bei -10 °C außen | mit dem Standardwert 55 °C beginnen |
| Heizkurve: Vorlauf bei +15 °C außen | mit dem Standardwert 30 °C beginnen |
| Minimale Vorlauftemperatur | **etwa 45 °C bei einer Heizwerttherme, etwa 30 °C bei einem Brennwertgerät** |
| Maximale Vorlauftemperatur | deine Drehknopf-Stellung aus Schritt 6 (zum Beispiel 55 °C) oder etwas darunter; höhere Werte wirken nicht, denn der Knopf ist die Obergrenze |
| Aktive Thermensteuerung freigeben (sonst nur planen) | **vorerst aus lassen** – das schaltest du in Schritt 7 ein |

**Warum das Minimum vom Gerätetyp abhängt:** Ein Brennwertgerät profitiert von niedrigem Vorlauf, ein Heizwertgerät spart dort kaum etwas, und bei kurzen Brennerläufen kommt die Wärme womöglich nicht bis zu den fernen Heizkörpern – siehe [Anleitung, Abschnitt 5](ANLEITUNG.md#5-thermensteuerung-eigene-therme-über-ebusd).

Die Heizkurve muss nicht perfekt sein: Thriftherm lernt eine Korrektur von bis zu ±10 K.

### 4.4 Wärmepumpe (optionales Zusatzmodul)

Diese Erweiterung ist eine Vorschau und noch nicht mit einer echten Wärmepumpe getestet. **Alle Felder leer lassen und auf *Absenden* klicken.** Eine Wärmepumpe lässt sich später in den Optionen ergänzen.

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

Nur wenn eine Wärmepumpe eingerichtet ist (Vorschau), zeigt das Formular zusätzlich *Wird von der Wärmepumpe beheizt* und *Bad-Trocknung mit der Wärmepumpe (nach dem Duschen trotz Lüften weiterheizen)*. Die Bad-Trocknung läuft über die Wärmepumpe, ohne sie erscheint keins der beiden Felder.

**Prüfen:** Nach dem letzten Raum zeigt *Einstellungen → Geräte & Dienste* **Thriftherm**. Sein Gerät zeigt *Thermensteuerung* und *Raumsteuerung* auf **Nur planen (Beta)**.

## 5. Better Thermostat je Raum (optional)

Überspringen, wenn du keine smarten Heizkörperthermostate hast. Die Thermensteuerung funktioniert auch ohne; dann die Handventile weit genug öffnen und die Vorlauftemperatur die Arbeit machen lassen.

Für jeden Raum:

1. **Better Thermostat** aus HACS installieren, falls noch nicht geschehen.
2. Ein Better Thermostat für den Raum anlegen: die Heizkörperthermostate des Raums wählen, **denselben Raumtemperaturfühler**, den auch Thriftherm bekommen hat, und die **Fensterkontakte** des Raums.
3. Gibst du ihm einen Außentemperaturfühler, die Außentemperatur, bei der es abschaltet, **hoch genug einstellen, zum Beispiel 25 °C** – oder den Außenfühler leer lassen. Sonst sperrt Better Thermostat Heizen, das Thriftherm anfordert.
4. In Thriftherm dieses Better Thermostat als Thermostat des Raums wählen (Optionen → *Räume* → Raum → *Bearbeiten*), falls du es in Schritt 4.6 leer gelassen hast.

Ein Thermostat, das du von Hand **ausschaltest**, lässt Thriftherm in Ruhe, und dieser Raum fordert die Therme nicht mehr an.

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
3. Thriftherm → *Konfigurieren* → **Regelparameter**: *Aktive Raumsteuerung freigeben (Sollwerte an die Thermostate schreiben)* einschalten. Absenden.
4. Am Thriftherm-Gerät **Thermensteuerung** und **Raumsteuerung** auf **Aktiv** stellen.

**Nach ein paar Minuten prüfen:**

| Entität | Erwartet |
|---|---|
| *Therme-Steuerbefehl* | Attribut *Befehle werden gesendet* ist an |
| Better-Thermostat-Entitäten | zeigen die neuen Sollwerte aus dem *Thermostat-Plan* |
| *Sicherheitsstatus* | OK |

Alles zum Alltag – Modi, Zeitpläne, Abwesenheit, Lernen – steht in der [Anleitung](ANLEITUNG.md).

## 8. Wenn etwas nicht klappt

| Problem | Ursache | Abhilfe |
|---|---|---|
| Gar keine ebusd-Entitäten | ebusd nicht mit Adapter oder MQTT verbunden, oder der Namensfilter lässt nichts durch | Adapter-Einstellung und Log der ebusd-App prüfen; prüfen, ob Mosquitto läuft und die MQTT-Integration eingerichtet ist; die Zeile `filter-name` in der `mqtt-hassio.cfg` prüfen |
| *WP* und *Statenumber* fehlen | der ebusd-Namensfilter lässt sie nicht durch | `filter-name` in der `mqtt-hassio.cfg` um `\|^wp$\|statenumber` ergänzen und ebusd neu starten ([Schritt 2.4](#24-heizungspumpe-und-status-nummer-sichtbar-machen)) |
| Therme ignoriert den Sollwert | ebusd darf nicht schreiben | `--accesslevel=*` in den Kommandozeilen-Optionen von ebusd ergänzen und die App neu starten |
| Therme ignoriert den Sollwert | Drehknopf steht niedriger als der Sollwert | der Knopf ist die Obergrenze – auf dein Maximum aufdrehen (zum Beispiel 55 °C) |
| Therme heizt gar nicht | Drehknopf am linken Anschlag = Sommerbetrieb | Knopf aufdrehen |
| Therme taktet, aber der letzte Heizkörper bleibt kalt | Pumpe steht mit dem Brenner; Mindest-Vorlauf zu niedrig | Pumpenmodus prüfen ([Schritt 6](#6-an-der-therme)); bei einer Heizwerttherme den Mindest-Vorlauf auf etwa 45 °C stellen ([Schritt 4.3](#43-gastherme-ebusd-und-gaszähler)) |
| Ein Thermostat nimmt keine Sollwerte an | das Thermostat hängt | Batterie raus und wieder rein; Firmware aktualisieren |
| Direkt nach einem Neustart zeigt *Therme-Steuerbefehl* *Warte auf Raumdaten* | Thriftherm wartet auf die Raumfühler | bis zu fünf Minuten normal |
| Ein Raum ist kalt, aber die Therme ist gesperrt | Fenster offen oder das Thermostat des Raums ausgeschaltet | Fenster schließen; das Thermostat wieder auf Heizen stellen |
