# Fehlersuche

[English](TROUBLESHOOTING.md) | **Deutsch**

Wenn etwas nicht klappt oder anders aussieht als erwartet: Such hier nach dem, was du siehst. Die Einrichtung steht in der [Einrichtungsanleitung](EINRICHTUNG.md), der Alltag in der [Anleitung](ANLEITUNG.md).

**Bereiche:** [ebusd und Verbindung](#ebusd-und-verbindung) · [Therme](#therme) · [Räume und Thermostate](#räume-und-thermostate) · [Anzeigen](#anzeigen) · [Nichts davon passt](#nichts-davon-passt)

## ebusd und Verbindung

### Keine ebusd-Entitäten

ebusd ist nicht mit dem Adapter oder mit MQTT verbunden, oder der Namensfilter lässt nichts durch.

- Die Adapter-Einstellung und das Protokoll der ebusd-App prüfen ([Einrichtung, Schritt 2.3](EINRICHTUNG.md#23-ebusd-app-konfigurieren)).
- Prüfen, ob Mosquitto läuft und die MQTT-Integration eingerichtet ist ([Schritt 2.1](EINRICHTUNG.md#21-mqtt-broker-installieren)).
- Die Zeile `filter-name` in der `mqtt-hassio.cfg` prüfen ([Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen)).

### WP und Statenumber fehlen

Der Namensfilter von ebusd lässt sie nicht durch. Die Zeile `filter-name` in der `mqtt-hassio.cfg` um `|^wp$|statenumber` ergänzen und ebusd neu starten ([Schritt 2.4](EINRICHTUNG.md#24-heizungspumpe-und-status-nummer-sichtbar-machen)). Beide erscheinen oft erst nach ein paar Abfragen.

### Reparaturmeldung „Keine Daten von der Therme“

ebusd erreicht die Therme nicht. Die häufigste Ursache: Der eBUS-Adapter hat vom Router eine neue IP-Adresse bekommen. Oft merkst du es auch daran, dass die Therme heizt, obwohl Thriftherm sperrt.

So läuft ein Ausfall ab:

- Nach zwei Minuten ohne *ebusd Signal* sendet Thriftherm nichts mehr. *Therme-Steuerbefehl* zeigt *Drehknopf regelt*, und die Therme heizt nach ihrem Drehknopf.
- Nach zehn Minuten erscheint unter Einstellungen → Reparaturen *Keine Daten von der Therme*.
- Die eBUS-Werte in Home Assistant bleiben während des Ausfalls auf ihrem letzten Stand stehen. Ob ebusd die Therme erreicht, zeigt nur *ebusd Signal*.

Abhilfe:

1. Im Protokoll der ebusd-App nach „unable to open …“ suchen.
2. Die Adresse des Adapters in der ebusd-App korrigieren.
3. Im Router eine feste Adresse für den Adapter vergeben, eine DHCP-Reservierung ([Schritt 2.3](EINRICHTUNG.md#23-ebusd-app-konfigurieren)).

Für eine Push-Nachricht legst du eine Automation auf *ebusd Signal* an, zum Beispiel „5 Minuten aus“.

### Ein WLAN-Gerät verliert ständig die Verbindung

Zum Beispiel der Gaszähler. Er hat die alte Adresse des eBUS-Adapters bekommen, und ebusd klopft dort weiter an. Abhilfe wie oben: die Adresse in der ebusd-App korrigieren, feste Adressen vergeben und das Gerät neu starten.

## Therme

### Die Therme ignoriert den Sollwert

Dafür gibt es zwei Ursachen:

- **ebusd darf nicht schreiben.** `--accesslevel=*` in den Kommandozeilen-Optionen der ebusd-App ergänzen und die App neu starten ([Schritt 2.3](EINRICHTUNG.md#23-ebusd-app-konfigurieren)).
- **Der Drehknopf steht niedriger als der Sollwert.** Der Knopf ist die Obergrenze. Dreh ihn auf dein Maximum, zum Beispiel 55 °C ([Schritt 6](EINRICHTUNG.md#6-an-der-therme)).

### Die Therme heizt gar nicht

Der Drehknopf steht am linken Anschlag. Das ist Sommerbetrieb und sperrt die Heizung, egal was Thriftherm sendet. Dreh den Knopf auf.

### Die Therme heizt, obwohl Thriftherm sperrt

Meist erreicht ebusd die Therme nicht mehr, und sie heizt nach ihrem Drehknopf. Siehe [Keine Daten von der Therme](#reparaturmeldung-keine-daten-von-der-therme).

### Die Therme taktet, aber der letzte Heizkörper bleibt kalt

Dafür gibt es zwei Ursachen, oft zusammen:

- **Die Pumpe steht mit dem Brenner.** Den Pumpenmodus prüfen ([Schritt 6](EINRICHTUNG.md#6-an-der-therme)). Warum das zählt, steht in der [Anleitung](ANLEITUNG.md#warum-der-pumpenmodus-zählt).
- **Der Mindest-Vorlauf ist zu niedrig.** Bei einer Heizwerttherme die minimale Vorlauftemperatur auf etwa 45 °C stellen ([Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).

### Die Therme springt etwa einmal pro Stunde kurz an

Auch nachts und ohne Heizbedarf. Das ist die Warmstart-Funktion der Kombitherme: Sie hält den Warmwasser-Wärmetauscher warm, im Display steht ein **C**. Früher hat ein Raumregler am Bus sie unterdrückt.

So schaltest du sie ab:

- **An der Therme:** den Warmwasser-Drehknopf ganz nach links drehen, dann wieder auf den gewohnten Wert. Das C verschwindet (von einem Nutzer an einer Vaillant bestätigt).
- **In Thriftherm:** in den Optionen unter *Gastherme (ebusd) und Gaszähler* *Warmwasser bereithalten* ausschalten ([Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)). Das passt nur zu einer Kombitherme ohne Speicher und wirkt nur bei aktiver Thermensteuerung.

### Ändert Thriftherm meine Warmwassertemperatur?

Nein. Für den Warmwasser-Sollwert sendet Thriftherm „kein Wert“, der Drehknopf der Therme entscheidet. Die einzige Ausnahme wählst du selbst: Ist *Warmwasser bereithalten* aus, heizt die Therme nicht mehr für Warmwasser, solange kein Hahn offen ist.

### Home Assistant ist abgestürzt oder aus

Die Therme fällt innerhalb von 9 bis 16 Minuten auf ihren Drehknopf zurück. Die Thermostate behalten ihren letzten Sollwert.

Bei einer Therme über Entitäten (Vorschau) ist das anders: Stürzt Home Assistant ab oder fällt der Strom aus, bleibt der letzte Zustand stehen, auch eine gesperrte Heizung ([Schritt 4.3a](EINRICHTUNG.md#43a-therme-ohne-ebusd-über-entitäten-vorschau)).

## Räume und Thermostate

### Ein Raum ist kalt, aber die Therme ist gesperrt

Ein Fenster ist offen, oder das Thermostat des Raums ist ausgeschaltet. Schließ das Fenster oder stell das Thermostat wieder auf *Heizen*.

Hat sich ein Heizkörperthermostat seit zweieinhalb Stunden nicht gemeldet, fordert der Raum ebenfalls keine Wärme an. Dazu erscheint eine Reparaturmeldung.

### Ein Raum steht auf 15 °C

Er ist im Modus *Abwesend*, und 15 °C ist die Abwesenheitstemperatur. Der Frostschutz (7 °C) ist eine eigene, tiefere Grenze.

### Am Thermostat gedreht, und es stellt sich nicht zurück

Eine Änderung von Hand wird zur vorübergehenden Übersteuerung, standardmäßig für 120 Minuten. Du beendest sie mit dem Knopf *Zurück zur Automatik* oder der Aktion `thriftherm.clear_override`. Mehr dazu in der [Anleitung, Änderungen von Hand](ANLEITUNG.md#änderungen-von-hand).

### Übersteuerungen, obwohl niemand am Thermostat war

Better Thermostat schreibt den Sollwert des Heizkörperthermostats alle paar Sekunden neu. Kommt über Zigbee eine Antwort verspätet an, hält Better Thermostat den alten Wert für einen Dreh am Thermostat.

Das erkennt der **Echo-Filter** (Optionen → *Regelparameter*, Abschnitt *Freigaben*, standardmäßig an). Springt Better Thermostat auf einen Wert, den das Thermostat in der letzten Minute schon hatte und wieder verlassen hatte (zum Beispiel 16 → 16,5 → 16), wird das ignoriert und der geplante Sollwert neu gesendet. Ein echter Dreh bringt einen neuen Wert und wird übernommen.

Wann zuletzt ein Echo ignoriert wurde, zeigt der Sensor *Thermostat-Plan* des Raums.

### Ein Raum braucht nach dem Hochstellen sehr lange

Die Heizkurve ist dafür gemacht, Temperaturen zu halten, nicht für schnelles Aufheizen. Wie schnell es gehen soll, stellst du mit dem *Aufheiztempo* unter *Gastherme (ebusd) und Gaszähler* ein ([Anleitung, Aufheiztempo](ANLEITUNG.md#aufheiztempo)). Mehr Tempo kostet mehr Gas.

### Ein Thermostat nimmt keine Sollwerte an

Das Thermostat hängt. Nimm die Batterie heraus und setz sie wieder ein, und aktualisiere die Firmware.

## Anzeigen

### Warte auf Raumdaten nach einem Neustart

Direkt nach einem Neustart zeigt *Therme-Steuerbefehl* *Warte auf Raumdaten*. Thriftherm wartet auf die Raumfühler, und bis zu fünf Minuten ist das normal.

### Der Temperaturtrend ist nach einem Neustart leer

Der Trend braucht etwa 30 Minuten Messwerte. Bei einer neuen Installation ist der Sensor ausgeschaltet, bis du ihn einschaltest ([Anleitung, Wichtige Anzeigen](ANLEITUNG.md#8-wichtige-anzeigen)).

### Vorlauf und Rücklauf ändern sich nur alle paar Minuten

Das ist normal: ebusd fragt die Werte der Therme reihum ab. Für die Regelung holt Thriftherm sich seine Werte einmal pro Minute selbst, mit Gaszähler, sobald Gas brennt, ohne Gaszähler durchgehend. Willst du frischere Werte im Dashboard, kürzt du die Abfrageliste von ebusd: [Weniger ebusd-Werte abfragen](EBUSD.md).

## Nichts davon passt

- Was eine Reparaturmeldung bedeutet und was du tun kannst, steht in der [Anleitung, Reparaturmeldungen](ANLEITUNG.md#9-reparaturmeldungen).
- Der Sensor *Entscheidungsgrund* sagt in Worten, warum Thriftherm tut, was es tut.
- Für einen Fehlerbericht: Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → *Diagnose herunterladen*. Die Datei enthält Konfiguration, aktuellen Zustand und Lernwerte, es wird nichts daraus entfernt. Sieh sie vor dem Veröffentlichen durch und hänge sie an ein [Issue](https://github.com/alpha-uniform/thriftherm/issues) an.
