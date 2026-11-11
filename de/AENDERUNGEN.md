# Änderungen

[English](../en/CHANGELOG.md) | **Deutsch**

Seit 0.7.0 ist die Konfiguration bei Version 4, von 0.5.0 bis 0.6.8 war sie bei Version 3.

## 0.7.4 – 2026-11-12

- Die Räume sind jetzt Untergeräte von „Thriftherm“ und heißen nur noch nach dem Raum („Wohnzimmer“ statt „Thriftherm Wohnzimmer“), nach der Namensregel von Home Assistant 2026.11. Geräte-IDs, Entity-IDs, eigene Namen und Bereiche bleiben.
- Behoben: Hieß ein Raumgerät nur „Thriftherm“, zeigte die Karte bei jedem Raum „Thriftherm“; sie nimmt dann den Bereich des Geräts oder den Raumnamen.
- Neu: Schalter *Raumfühler melden nur bei Änderung* (z. B. Shelly H&T): Raumtemperaturen gelten bis zum Max. Sensoralter statt höchstens 6 Stunden.
- Standard-COP-Werte aktualisiert.

## 0.7.3 – 2026-11-09

- Behoben: Weniger Fehlalarme „Heizkörperthermostat antwortet nicht“; gemeldet wird nur noch, wenn ein Ventil wirklich nicht reagiert.
- Neu: Die Wärmepumpe schützt ihre Räume vor Frost, ohne Zentralheizung sofort, sonst als Reserve nach 30 Minuten.
- Behoben: Eine Wärmepumpe im Schnell-Aufheizen oder Trocknen heizte weiter, wenn man die Steuerung abschaltete.
- Behoben: Eine Therme über Entitäten konnte nach dem Beenden gesperrt bleiben.
- Behoben: Die Wärmepumpe schaltete sich beim Durchlaufen immer wieder selbst ab.
- Die Wärmepumpe zeigt *Abtauen*, und ein eigener Modus wie *Heizen/Kühlen* bleibt dir überlassen.
- Der *Entscheidungsgrund* wechselt viel seltener, und nach einem Neustart gibt es keine Warnung mehr.
- Behoben: Ruhige Zigbee-Außenfühler, Stromzähler in kW und Thermostate in ganzen Grad.
- Sicherer: Personen nicht mehr in der Diagnose, mehr Prüfungen beim Einrichten, kleinere Fehler bei Lernen, Therme und Karte.
- Standard-COP-Werte aktualisiert.

## 0.7.2 – 2026-11-05

- Thriftherm ist keine Beta mehr; der Modus heißt nur noch *Nur planen*.
- Behoben: Lief die Wärmepumpe nach einer Ruhe nicht an, obwohl ein Raum unter Soll war, wartete sie bis zu einer Stunde; jetzt geht ihr Sollwert nach 5 Minuten auf 1,5 Grad über ihre Messung.
- Behoben: Nach dem Trocknen oder dem schnellen Aufheizen lief sie lange über ihrer kleinsten Leistung; 15 Minuten danach wird ihr Sollwert noch einmal gesetzt.
- Behoben: Stand der Ausblasfühler in einem ruhigen Lauf über eine Stunde still, war der COP unbekannt und das Kennfeld lernte nichts; jetzt zählt die Grenze erst, wenn auch sein Gerät schweigt.
- Standard-COP-Werte aktualisiert, neu auch für 0 bis 2 °C.

## 0.7.1 – 03.11.2026

- Neu je Raum: *Längstes Vorheizen* begrenzt, wie früh ein Raum beginnt, und er ruft die Therme dann in seinem Block bis zum Ende ([#6](https://github.com/alpha-uniform/thriftherm/issues/6)).
- Behoben: Ein Vorlauf über 100 °C galt als „Daten fehlen“, Störcodes der Therme als Warmwasser; jetzt warnt eine Reparaturmeldung, und ab 90 °C sowie bei S.53/S.54 gibt es vorerst keine neue Freigabe, die Wärmepumpe heizt ihre Räume dann auch, wenn sie teurer ist ([#5](https://github.com/alpha-uniform/thriftherm/issues/5)).
- Behoben: Stand die Wärmepumpe länger als eine halbe Stunde, galt das immer wieder als neuer Stopp, und ihr Sollwert sank; läuft sie bei einem Raum unter Soll nicht an, geht er nun alle halbe Stunde ein halbes Grad höher.
- Neue Wahl *Wärmepumpe allein* bei *Wenn die Wärmepumpe allein nicht reicht*: kleinste Leistung, ihre Räume rufen die Therme nicht, solange die Wärmepumpe günstiger ist, und die Therme heizt die anderen Räume weiter.
- Behoben: Kam ein Raumthermostat nach einem Neustart mit einem anderen Sollwert zurück, galt das zwei Stunden lang als Änderung von Hand; jetzt gilt wieder der Plan.
- Behoben: Das COP-Kennfeld kannte je Stufe nur die letzten 45 Minuten Lauf und lag zu hoch, in der Stufe des Starts bis 27 %; jetzt summiert es Wärme und Strom über die ganze Heizzeit samt Start und Nachlauf, Altes verblasst mit 100 Laufstunden Halbwertszeit.
- Behoben: Der COP eines kurzen Laufs wählt nicht mehr die Heizung und stoppt damit die Wärmepumpe; der Sensor zeigt ihn weiter, entschieden wird nach dem eingeschwungenen Wert oder dem Kennfeld.
- Behoben: Stoppte ein zu niedriger eingeschwungener COP die Wärmepumpe und startete das Kennfeld sie wieder, wechselten Laufen und Ruhen endlos; jetzt sperren zwei solche Werte mit mindestens 30 Minuten Abstand sie auch über eine Pause von bis zu einer Stunde (*Sperre nach Vereisung / Ineffizienz*, Standard 120 Minuten).
- Neue Lernphase: Das alte Kennfeld ließ sich nicht umrechnen. Bis eine Stufe 3 Stunden Laufzeit hat, gilt sein Wert schwach gewichtet weiter (Grundlage *Alte Tabelle*), und Lernläufe sind dort wieder möglich.
- Ein neuer Schlauchfaktor rechnet das Kennfeld nur um; eine andere Lüfterstufe im Normalbetrieb lässt es neu beginnen, wie schon eine andere Luftmengen-Kennlinie.
- Behoben: Der COP war oft „unbekannt“, weil ein ruhiger Fühler als veraltet galt; jetzt zählt die letzte Meldung am Ausblas 60 Minuten; an der Ansaugung 3 Stunden, sobald auch ihr Gerät schweigt, sonst bis zu einem Tag (einstellbar unter *COP-Messung*).
- Behoben: *Jetzt trocknen* ließ den Verdichter stehen; jetzt läuft die Wärmepumpe beim Trocknen etwas stärker.
- Die Wärmepumpe folgt ihrer steigenden Messung und schaltet sich seltener selbst ab.
- Weniger harte Starts der Wärmepumpe: Ist die Heizung günstiger, aber ein Raum noch gewünscht, bleibt sie im Heizbetrieb und ruht, statt aus- und wieder einzuschalten; der *Entscheidungsgrund* sagt dann „Heizung günstiger“.
- Behoben: Zur Rückkehr aus Abwesend wird auf das Soll geheizt, das dann laut Plan gilt; kommst du am Ende der Komfortzeit zurück, ist das die Absenkung, kurz vor einer Komfortzeit wird für diese vorgeheizt.
- Behoben: Vor der Rückkehr aus Abwesend wird kein Raum kälter gestellt als die Abwesenheitstemperatur.
- Behoben: Beim Abtauen wird die Wärmepumpe nicht mehr ausgeschaltet, wenn die Räume warm sind.
- Neue Therme in der Liste: Vaillant atmoTEC pro VUW PL 240/3-3M ([#4](https://github.com/alpha-uniform/thriftherm/issues/4)).
- Standard-COP-Werte aktualisiert.

## 0.7.0 – 30.10.2026

Im Normalbetrieb läuft die Wärmepumpe nur noch mit kleinster Leistung, dort ist ihr COP am besten. Konfiguration Version 4, die ältere Versionen nicht laden: Vor dem Update ein Backup machen, falls du zurückwillst.

- Ein halbes Grad über dem Soll des kältesten Raums ruht die Wärmepumpe, sie piept seltener; kommt ein Raum nicht hinterher, hilft die Therme oder Fernwärme. Ihr COP wird nur noch bei kleinster Leistung gemessen. Die Optionen *Sollwert folgt den Räumen* und *Sollwert-Abstand* entfallen.
- Neue Optionen *Wenn die Wärmepumpe allein nicht reicht* (Therme oder Fernwärme hilft, Wärmepumpe dreht höher, langsamer warm werden) und *Lüfterstufe im Normalbetrieb* (Standard Auto).
- Aufheizraten getrennt für Therme und Wärmepumpe, in Wärmepumpen-Räumen ab 0,5 Grad je Stunde neu gelernt; mit der Wärmepumpe beginnt das Vorheizen höchstens 4 Stunden vorher.
- Ein Raum ruft die Therme nur, wenn auch sein Thermostat heizen will ([#3](https://github.com/alpha-uniform/thriftherm/issues/3)).
- Abwesend endet auf Wunsch, sobald eine gewählte Person heimkommt (Regelparameter).
- Optional der Strom der Heizungspumpe im Kostenvergleich (Leistung in den Thermen-Optionen).
- Trocknen im Bad mit Fensterkontakt nur bei offenem Fenster: Hinaus geht die Feuchte nur dort.
- Standard-COP-Werte aktualisiert.
- Behoben: Stand die Wärmepumpe in einem wärmeren Raum, blieb ihr Verdichter stehen; das Trocknen blies dann nur Luft.

## 0.6.8 – 27.10.2026

- Etwas weniger Arbeitsspeicher (rund 0,5 MB).

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
