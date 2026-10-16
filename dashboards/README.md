# Beispiel-Dashboard / Example dashboard

## Deutsch

Einfacher geht es mit der Thriftherm-Karte, die mit der Integration kommt: *Karte hinzufügen → Thriftherm* ([Einrichtung, Schritt 9](../de/EINRICHTUNG.md#9-dashboard)). Dieses Dashboard ist für alle, die nur Standard-Karten wollen.

[`thriftherm.yaml`](thriftherm.yaml) ist ein fertiges Dashboard für Thriftherm, nur mit Standard-Karten von Home Assistant.
Ansichten: Übersicht (was heizt gerade und warum), Räume, Therme, Wärmepumpe, Verlauf.

1. Einstellungen > Dashboards > *Dashboard hinzufügen* > *Neues Dashboard von Grund auf*.
2. Dashboard öffnen, Stift oben rechts, dann ⋮ > *Raw-Konfigurationseditor*.
3. Alles durch den Inhalt der Datei ersetzen und speichern.
4. Raumblöcke anpassen: Die Beispielräume heißen `badezimmer` und `wohnzimmer`. Für jeden eigenen Raum einen Block kopieren und den Raumschlüssel ersetzen (Beispiel: `sensor.thriftherm_kuche_target_temperature`). `climate.badezimmer` und `climate.wohnzimmer` durch das Thermostat des Raums ersetzen; es liefert die gemessene Temperatur.
5. Ohne Wärmepumpe die Ansicht *Wärmepumpe* löschen, ohne Gastherme die Ansicht *Therme*.

Mehr dazu steht oben in der Datei.

## English

The Thriftherm card that comes with the integration is simpler: *Add card → Thriftherm* ([setup, step 9](../en/SETUP.md#9-dashboard)). This dashboard is for anyone who wants standard cards only.

[`thriftherm.en.yaml`](thriftherm.en.yaml) is the same dashboard with English labels. Paste it the same way: Settings > Dashboards > *Add dashboard* > *New dashboard from scratch*, open it, pencil, ⋮ > *Raw configuration editor*, replace everything, save. Then copy one room block per room and replace the room key (`badezimmer`, `wohnzimmer`) with your own. Replace `climate.badezimmer` and `climate.wohnzimmer` with the room's thermostat; it supplies the measured temperature. Delete the *Heat pump* view if you have no heat pump, the *Boiler* view if you have no gas boiler. Details are at the top of the file.
