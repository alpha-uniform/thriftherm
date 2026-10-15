# Beispiel-Dashboard / Example dashboard

## Deutsch

[`thriftherm.yaml`](thriftherm.yaml) ist ein fertiges Dashboard für Thriftherm, nur mit Standard-Karten von Home Assistant.
Ansichten: Übersicht (was heizt gerade und warum), Räume, Therme, Wärmepumpe, Verlauf.

1. Einstellungen > Dashboards > *Dashboard hinzufügen* > *Neues Dashboard von Grund auf*.
2. Dashboard öffnen, Stift oben rechts, dann ⋮ > *Raw-Konfigurationseditor*.
3. Alles durch den Inhalt der Datei ersetzen und speichern.
4. Raumblöcke anpassen: Die Beispielräume heißen `badezimmer` und `wohnzimmer`. Für jeden eigenen Raum einen Block kopieren und den Raumschlüssel ersetzen (Beispiel: `sensor.thriftherm_kuche_target_temperature`).
5. Ohne Wärmepumpe die Ansicht *Wärmepumpe* löschen, ohne Gastherme die Ansicht *Therme*.

Mehr dazu steht oben in der Datei.

## English

[`thriftherm.en.yaml`](thriftherm.en.yaml) is the same dashboard with English labels. Paste it the same way: Settings > Dashboards > *Add dashboard* > *New dashboard from scratch*, open it, pencil, ⋮ > *Raw configuration editor*, replace everything, save. Then copy one room block per room and replace the room key (`badezimmer`, `wohnzimmer`) with your own. Delete the *Heat pump* view if you have no heat pump, the *Boiler* view if you have no gas boiler. Details are at the top of the file.
