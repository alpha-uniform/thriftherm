# Änderungen

[English](../en/CHANGELOG.md) | **Deutsch**

Seit 0.5.0 bleibt die Konfiguration bei Version 3: Jedes Update seitdem behält alles Gelernte.

## 0.6.7 – 26.10.2026

- Behoben: Mit ZHA galten Thermostate und Raumfühler als stumm, wenn sich ihre Werte lange nicht änderten ([#2](https://github.com/alpha-uniform/thriftherm/issues/2)).

## 0.6.6 – 26.10.2026

- Fehlersuche: Ein Raum kommt nicht ans Soll, obwohl die Korrektur an der Grenze steht (Heizkörper zu klein).
- Behoben: Der Sensor *Therme Heizstarts heute* zählte manchmal einen Start wieder ab; Home Assistant warnte dann im Protokoll.

## 0.6.5 – 23.10.2026

- Weniger unnötige Brennerstarts: Ein Anstieg des Vorlaufs wartet, solange die Therme ihre Sperrzeit abwartet (S.5–S.8).
- Ein gerade beendetes Komfortfenster ruft die Therme nicht mehr.
- Nach einer Pause ohne Vorgabe springt der Vorlauf nicht mehr auf einmal hoch.
- Neuer Sensor *Therme Heizstarts heute*.
- Die Karte trägt sich selbst als Dashboard-Ressource ein ([#1](https://github.com/alpha-uniform/thriftherm/issues/1)); mit Ressourcen in YAML siehe [Fehlersuche](FEHLERSUCHE.md#die-thriftherm-karte-fehlt-custom-element-doesnt-exist).
- Keine Wärmepumpen-Warnung mehr bei *Nur Heizung*.
- Trocknen mit *Sollwert folgt den Räumen*: Das Gerät bleibt nicht mehr stehen.
- Der Lernstatus zeigt, wenn die Heizkurven-Korrektur an ihrer Grenze steht.
- Thriftherm steht in der Standardliste von HACS.
- Standard-COP-Werte aktualisiert.
- Behoben: Bei *Nur Wärmepumpe* heizte die Therme auch, wenn die Wärmepumpe von Hand lief, wartete oder nicht lieferte.
- Behoben: Normales Herunterregeln des Verdichters bei Frost sperrte die Wärmepumpe zwei Stunden als vereist.
- Behoben: Der COP der Wärmepumpe fehlte in vielen Takten, wenn der Lüfter in den Pausen langsam drehte.
- Behoben: Eine Wärmepumpe ohne Lüfterstufen startete nicht; nach einem gescheiterten Start übernimmt die Therme sofort.
- Behoben: Ein Zeitfenster wie `00:00-00:00` galt als leer statt als ganzer Tag.
- Behoben: Ein Fensterkontakt, der kurz ausfällt und offen zurückkommt, galt nicht sofort als offen.

## 0.6.4 – 18.10.2026

- Die Wärmepumpe wird zuerst höher gestellt, bevor die Therme hilft; ob sie drosselt, sagt ihr eigener Messwert.
- Infozeile am Raum: nächster Wechsel laut Zeitplan und Trend (ausblenden mit `hidden_controls: [info]`).
- Raumkarte ohne Kopfzeile (`header: false`).
- *Zurück zur Automatik* auch nach *Schnell aufheizen* und *Jetzt trocknen*.
- Standard-COP-Werte aktualisiert.

## 0.6.3 – 16.10.2026

- Neue Raumkarte für die Seite eines einzelnen Raums.
- Die Karten zeigen *Fast erreicht*.
- Der Warmstart einer Kombitherme zählt als Warmwasser, nicht als Heizstart.
- Fehlersuche: kalter letzter Heizkörper (Pumpenstufe d.19), Raum knapp unter Soll, fehlende Karte.

## 0.6.2 – 16.10.2026

- Eigene Dashboard-Karte, kommt mit der Integration (*Karte hinzufügen → Thriftherm*).
- Die Doku liegt jetzt in `de/` und `en/`; alte Links in den Hauptordner laufen ins Leere.
- Beispiel-Dashboard verständlicher. Die README sagt: alles lokal, keine Daten nach außen.

## 0.6.1 – 16.10.2026

- COP-Werte eines Modells für Geräte ohne eigene Sensoren (bisher Midea PortaSplit).
- Beispiel-Dashboard zum Einfügen.

## 0.6.0 – 15.10.2026

- Vorlauf je Raum gelernt: Der Raum, der am meisten braucht, bestimmt ihn.
- Aufheiztempo nach Wunsch (Standard aus).
- Gas getrennt nach Heizung und Warmwasser.
- Trocknen nach dem Duschen, Taste *Jetzt trocknen*.
- Änderungen von Hand gelten überall gleich, mit *Zurück zur Automatik*. Bei Better Thermostat muss dessen Kindersicherung aus sein, damit ein Dreh am Ventil zählt.
- Die Wärmepumpe ist keine Vorschau mehr.
- Vorschau: Versatile Thermostat und eine Therme über Entitäten (etwa OpenTherm).
- Doku und Formulare neu geordnet.
- Behoben: Das Vorlauf-Minimum war ein Sockel, gelernte Korrektur und Bedarfszuschlag kamen obendrauf.
- Behoben: Ein Zeitplan-Block bis 23:59 mit einem Block ab 00:00 danach beendete den Komfort.
- Behoben: Nach einem Neustart heizte die Therme für einen Raum, den die Wärmepumpe versorgt.
- Behoben: Ein Thermostat, das in seinem eigenen Raster antwortet (21,5 °C gesendet, 22 °C gezeigt), galt als übersteuert.
- Behoben: Reparaturmeldungen verschwanden nach jedem Neustart und kamen wieder.

## 0.5.7 – 11.10.2026

- Ein Sollwert der Wärmepumpe mit Lüfterstufe geht nicht mehr verloren.

## 0.5.6 – 11.10.2026

- Eine Heizkurve je Lastfall: ein Raum, wenige, die Mehrheit.
- Wärmepumpen-Option *Sollwert folgt den Räumen* und Lüfternachlauf.
- Lernschritte wachsen (1, 2, 4 Grad); eine passende Korrektur bleibt.
- COP auch bei einer taktenden Wärmepumpe.
- Behoben: Ein Neustart von Home Assistant stoppte eine laufende Wärmepumpe.

## 0.5.5 – 11.10.2026

- Heizkurve in Höhe und Steigung gelernt.
- Brennerläufe am besten vorhandenen Zeichen erkannt.

## 0.5.4 – 11.10.2026

- Eine taktende Therme lernt aus den Räumen.
- Sensor *Therme Brenneranteil*.

## 0.5.3 – 10.10.2026

- Nur Doku: Warmstart einer Kombitherme, Knopf für HACS.

## 0.5.2 – 10.10.2026

- Option *Warmwasser bereithalten* (mit Warmwasserspeicher an lassen).
- Behoben: Ein Thermostat galt schon nach einer Stunde als stumm, jetzt nach zweieinhalb.
- Behoben: Eine Komfortzeit bis 23:59 schloss die letzte Minute des Tages aus.

## 0.5.1 – 09.10.2026

- Ohne Gaszähler fallen *Gasleistung* und *Gasenergie* weg: Dashboard-Karten, die sie zeigen, entfernen.
- Der Lernstatus nennt, was ihn pausiert hat.
- Komfort unter der Absenkung zieht die Absenkung mit.
- Behoben: Ein Zeitplan-Block bis 24:00 lief nicht über Mitternacht.

## 0.5.0 – 04.10.2026

Konfiguration Version 3, die ältere Versionen nicht laden: Vor dem Update ein Backup machen, falls du zurückwillst.

- Mitheizen: längere Brennerläufe, weniger Starts.
- Räume rufen mit Hysterese (ab 0,3 Grad unter Soll, bis 0,1 Grad), nicht in der letzten halben Stunde der Komfortzeit.
- Warmwasser am Statuscode der Therme erkannt.
- Echo-Filter für Better Thermostat.
- Die Steuerung läuft weiter, wenn ein Sensor, ein Ventil oder ein Befehl ausfällt.
- Weniger Zeilen in der Datenbank von Home Assistant.
- Behoben: Eine kalte Therme verlor nach sechs ruhigen Stunden ihre Daten, und die Therme fiel auf ihren Drehknopf zurück.
- Behoben: Ein gescheiterter Befehl an ein Gerät hielt die übrigen auf.
- Behoben: Ein Preis von 0 oder ein Wert wie „nan“ aus einer anderen Integration stoppte die Steuerung.

## 0.4.0 – 18.09.2026

- Erste Version, die eine echte Therme Tag und Nacht steuert: Erkennung über ebusd, Fernwärme oder Wärmepumpe allein, geplante Rückkehr, Deutsch und Englisch.

## 0.3.0

- Umbenannt in Thriftherm (Domain `thriftherm`).
