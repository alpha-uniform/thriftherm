# Änderungen

[English](../en/CHANGELOG.md) | **Deutsch**

## 0.6.3 – 16.10.2026

Eine Raumkarte, „fast erreicht“ in den Karten und der Warmstart einer Kombitherme richtig gezählt. Die Konfiguration bleibt bei Version 3, alles Gelernte bleibt erhalten.

### Neu

- **Raumkarte** für die Seite eines einzelnen Raums: *Karte hinzufügen → Thriftherm room*. Ist, Soll und Grund, Soll von Hand, Komfort, Absenkung, Schnell aufheizen, Trocknen und Zeitplan; was erscheint, wählt man im Editor.
- Die Karten zeigen, wenn ein Raum als fast erreicht gilt: *Fast erreicht, heizt wieder unter …* statt nur des Zeitplans. Den Wert liefert der Sensor *Heizbedarf* als Attribut `calls_again_below`.
- Bilder der Karten in README und Einrichtung, mit Beispieldaten.

### Behoben

- Der Warmstart einer Kombitherme (sie hält ihr Warmwasser warm und meldet dabei „Heizen“) zählte als Brennerstart der Heizung und sein Gas als *Gas Heizung*. Brennt die Therme bei gesperrter Heizung mit stehender Heizungspumpe, zählt das jetzt als Warmwasser; läuft die Pumpe, heizt sie wirklich und wird als Heizung gezählt.

### Dokumentation

- Fehlersuche: Bleibt der letzte Heizkörper kalt, kann die Pumpe auf der kleinen Stufe laufen. Für Vaillant atmoTEC plus und turboTEC plus erklärt der Eintrag den Diagnosecode d.19, mit Warnung.
- Fehlersuche: Ein Raum, der ein paar Zehntel unter dem Soll bleibt, und eine fehlende Thriftherm-Karte nach dem Update.

## 0.6.2 – 16.10.2026

Eine eigene Dashboard-Karte kommt mit der Integration. Die Konfiguration bleibt bei Version 3, alles Gelernte bleibt erhalten.

### Neu

- **Eine eigene Dashboard-Karte.** *Karte hinzufügen → Thriftherm*: die Wärmequelle in einem Satz mit ihren Kosten (ein Tipp vergleicht Therme und Wärmepumpe), Hinweise nur, wenn etwas Aufmerksamkeit braucht, eine Zeile je Raum mit gemessener Temperatur, Soll und Grund (ein Tipp öffnet das Soll von Hand, Komfort, Absenkung, Schnell aufheizen, im Bad Jetzt trocknen und den Zeitplan des Raums; was davon erscheint, wählt man im Editor), und der Betriebsmodus. Sie kommt mit der Integration, findet die Räume selbst und spricht Deutsch oder Englisch. Siehe Schritt 9 der Einrichtung. Das Beispiel-Dashboard bleibt für alle, die Standard-Karten wollen.
- Der Sensor *Solltemperatur* eines Raums nennt, woher seine gemessene Temperatur kommt (`temperature_entity`: der Raumsensor oder das Ventil, das für ihn einspringt), den Raumschlüssel (`room_key`) und seinen Zeitplan-Helfer (`schedule_entity`) als Attribute.

### Geändert

- Die Dokumentation liegt jetzt in den Ordnern `de/` und `en/`, jeweils mit Einrichtung, Anleitung, Funktionsweise, Fehlersuche, ebusd und Änderungen. Im Hauptordner bleiben nur die READMEs. Alte Links auf Dateien im Hauptordner laufen ins Leere.
- Im Thermen-Formular erklären *Brenner an* und *Gasdurchfluss*, wann man sie braucht: Einen trägen Impulszähler lässt man beim Durchfluss besser leer, dann zählt der Statuscode der Therme.
- Das Beispiel-Dashboard sagt in Klartext, warum ein Raum gerade nicht heizt, zeigt die gemessene Temperatur, eine Übersteuerung mit Datum, und je Raum den Verlauf mit dem Soll von Thriftherm.
- Die README sagt ausdrücklich: Thriftherm läuft vollständig lokal und sendet keine Daten nach außen.

## 0.6.1 – 16.10.2026

### Neu

- **COP-Werte eines Modells für Geräte ohne Sensoren.** Ohne Zwischenstecker und Fühler an Ansaugung und Ausblas konnte Thriftherm nur nach einer allgemeinen Kurve entscheiden. Im Wärmepumpen-Formular bietet *COP-Werte dieses Modells* jetzt Werte an, die am gleichen Modell gemessen wurden; bisher die Midea PortaSplit (ein Gerät, 2 bis 16 °C draußen). Was dein eigenes Gerät misst, geht immer vor.
- **Ein Beispiel-Dashboard** zum Einfügen in Home Assistant, auf Deutsch und Englisch: was gerade heizt und warum, die Räume, die Therme, die Wärmepumpe und der Verlauf. Siehe Schritt 9 der Einrichtung.

## 0.6.0 – 15.10.2026

Der Vorlauf der Therme wird je Raum gelernt, ein Aufheiztempo nach Wunsch bringt langsame Räume schneller auf Temperatur, und das Gas wird getrennt nach Heizung und Warmwasser gezählt. Die Wärmepumpen-Erweiterung ist keine Vorschau mehr. Wer von 0.5.7 kommt: Die Konfiguration bleibt bei Version 3, alles Gelernte bleibt erhalten.

### Neu

- **Vorlauf je Raum gelernt.** In jedem Lastfall hat jeder Raum seine eigene Korrektur der Heizkurve. Der Raum, der am meisten braucht, bestimmt den Vorlauf; bei den anderen nehmen die Ventile weniger. Ein Raum, der nicht hinkommt, hebt nur seinen eigenen Wert an, und dafür muss kein Raum allein heizen. Der Lernstatus zeigt den *Raum, der den Vorlauf bestimmt*. Die Thermen-Option *Eigene Heizkurve je Raum* schaltet zurück auf eine gemeinsame Korrektur.
- **Aufheiztempo** (Thermen-Optionen, Grad pro Stunde, Standard 0 = aus). Thriftherm misst, wie schnell ein Raum wirklich steigt, und gibt ihm mehr Vorlauf, alle 20 Minuten 2 Grad, bis er mitkommt. Was ein Raum gebraucht hat, merkt es sich fürs nächste Aufheizen. Mehr Tempo kostet mehr Gas.
- **Gas für Heizung und Gas für Warmwasser** als zwei Sensoren fürs Energie-Dashboard. Mit Gaszähler danach, was die Therme gerade tut; ohne Gaszähler bei Vaillant-Thermen aus deren eigenen Zählern (neue Thermen-Option: Nennwärmebelastung vom Typenschild).
- **Trocknen nach dem Duschen**, mit der stärksten Lüfterstufe: Es beginnt, sobald die Feuchte nicht mehr steigt. Die neue Taste *Jetzt trocknen* startet es sofort; den Start von selbst kann man je Raum abschalten.
- **Änderungen von Hand folgen auf jedem Gerät einer Regel und zeigen sich sofort.** Eine Person oder eine Automation in Home Assistant zählt sofort, die eigenen Befehle von Thriftherm nie. Die Änderung gilt für die Dauer der Übersteuerung, auch an der Wärmepumpe, und zeigt, wer sie gemacht hat und bis wann. Die neue Taste *Zurück zur Automatik* beendet sie; die Aktion `clear_override` ohne Raum beendet auch die an der Wärmepumpe.
- **Ein Dreh am Ventil zählt als Änderung von Hand.** Bei Better Thermostat muss dessen *Kindersicherung* aus sein: Ist sie an, setzt Better Thermostat das Ventil zurück, und der Dreh zählt nicht.
- **Die Wärmepumpe ist keine Vorschau mehr.** Bevor die Therme einem Raum der Wärmepumpe hilft, wird der Sollwert der Wärmepumpe in Schritten von 0,5 Grad angehoben, um höchstens 2 Grad.
- **Versatile Thermostat als Thermostat eines Raums (Vorschau).** Was es nach eigener Regel tut (Fenster, Lastabwurf, Sicherheitsmodus), gilt nicht als Änderung von Hand; noch nicht an einer echten Installation getestet.
- **Therme ohne eBUS über Entitäten (Vorschau).** Im Thermen-Schritt eine Zahl-Entität für den Vorlauf-Sollwert und einen Schalter für die Heizung wählen, etwa die eines OpenTherm-Adapters am ESP; noch an keiner echten Therme gelaufen.
- **Geordnete Formulare:** Die Regelparameter beginnen mit den Werten für den Alltag; die Wärmepumpe hat ein eigenes Formular, *Wärmepumpe: Feineinstellung*.
- **Dokumentation neu geordnet:** kurze README, Einrichtung, Anleitung, *Wie Thriftherm rechnet*, Fehlersuche und eine Seite dazu, wie man weniger Werte von ebusd abfragt.

### Behoben

Therme und Heizkurve:

- Das Vorlauf-Minimum ist eine Untergrenze, kein Sockel: Gelernte Korrektur und Bedarfszuschlag kommen nicht mehr obendrauf.
- Die Kurve lernt nicht mehr von einem Raum, der zu einem Wunsch von Hand aufheizt, und nicht von einem Raum, dessen Ventil zu ist. Unter dem Vorlauf-Minimum macht sie keinen Schritt nach oben.
- „Die Mehrheit“ der Räume heißt mehr als die Hälfte (bei vier Räumen: einer, zwei, drei oder vier).
- Ein Zeitplan-Block bis 23:59 mit einem Block ab 00:00 danach und Blöcke, die direkt aufeinander folgen, beenden den Komfort nicht mehr.
- Warmwasser wird nur angezeigt, solange es läuft; eine Therme am eigenen Drehknopf über dem Vorlauf-Maximum gilt nicht mehr als Warmwasser; die Stunde nach dem Warmwasser hält nicht mehr das ganze Lernen an.
- Nach einem Neustart heizt die Therme nicht mehr für einen Raum, den die Wärmepumpe versorgt.
- Eine Therme über Entitäten bekommt beim Stoppen von Home Assistant das Vorlauf-Maximum zurück.

Wärmepumpe:

- Eine Lüfterstufe von Hand bleibt, auch beim Trocknen und beim Schnellaufheizen.
- Ein Neustart schickt dem laufenden Gerät nichts; *Letzte Befehle* zeigt nur Befehle, die angekommen sind.
- Mit *Sollwert folgt den Räumen* sagt der Entscheidungsgrund „Wärmepumpe läuft durch“ statt „Kein Raum braucht Wärme“.
- *Nur Wärmepumpe* beendet eine Sperre wegen teurer Wärme sofort; eine Vereisungssperre läuft bis zum Ende und wird nicht mehr mit einer Sperre wegen teurer Wärme verwechselt.
- Abtauen wird auch am Innenregister erkannt und je Abtauvorgang einmal gezählt; Kühlen und Entfeuchten zählen nicht mehr als Abtauen.
- Ein Gerät bekommt nur Modi, Lüfterstufen und Sollwert-Schritte, die es kennt; einen Befehl, den es nicht ausgeführt hat, bekommt es nach 2, 4, 8 … Minuten noch einmal, statt dass er als Änderung von Hand gilt; ein gescheitertes Ausschalten und das „Aus“ nach dem Lüfternachlauf lassen das Gerät nicht mehr im Lüfterbetrieb.

Räume:

- Ein beim Start offenes Fenster gilt sofort als offen.
- Ein Sollwert wird an die Grenzen des Thermostats angepasst; ein getauschtes Ventil wird ohne Neuladen gefunden.
- Das Trocknen startet und stoppt bei offenem Fenster nicht mehr jede Minute und übersteht einen Neustart.
- Das Raum-Formular zeigt die Temperaturen, die gerade gelten.
- Ein Thermostat, das in seinem eigenen Raster antwortet (21,5 °C gesendet, 22 °C gezeigt), gilt nicht mehr als übersteuert; eine Änderung während eines Durchlaufs oder während das Gerät nicht erreichbar war, wird nicht mehr zurückgestellt.
- Ein Raum bekommt keine Entitäten für etwas, das er nicht hat; solche Entitäten verschwinden mit dem Update.

Home Assistant:

- Das Gerät Thriftherm zeigt die installierte Version.
- Reparaturmeldungen verschwinden nicht mehr nach jedem Neustart und kommen wieder; die Gasenergie springt nicht mehr, wenn sich der Brennwert ändert; Fernwärme heißt nicht mehr „Gastherme“.

## 0.5.7 – 11.10.2026

### Behoben

- Ein neuer Sollwert der Wärmepumpe geht nicht mehr verloren, wenn mit ihm eine Lüfterstufe gesendet wird.

## 0.5.6 – 11.10.2026

Das Lernen der Therme weiß jetzt, wie viele Räume Wärme anfordern, und die Wärmepumpen-Erweiterung hat zum ersten Mal ein echtes Gerät gesteuert; sie bleibt eine Vorschau. Die Konfiguration bleibt bei Version 3. Alles Gelernte bleibt erhalten, und jeder Lastfall startet mit der bisher gelernten gemeinsamen Korrektur.

### Neu

- **Eine Heizkurve je Lastfall.** Ein Raum fordert an, wenige oder die Mehrheit: Jeder Fall hat seine eigene Korrektur, zu sehen im Lernstatus, damit der Vorlauf zur Zahl der anfordernden Räume passt. Eine Thermen-Option schaltet zurück auf eine gemeinsame Korrektur.
- **Wärmepumpen-Option *Sollwert folgt den Räumen*.** Das Gerät bekommt die höchste Solltemperatur der Räume, die es versorgt, plus den Sollwert-Abstand (Standard 1 Grad), regelt selbst und bleibt an, solange die Wärmepumpe die günstigere Quelle ist.
- **Der Lüfter der Wärmepumpe läuft nach, bevor das Gerät ausgeschaltet wird:** 15 Minuten Lüfterbetrieb (neuer Parameter, 0 = sofort aus).

### Geändert

- **Die Spreizung Vorlauf/Rücklauf bewertet die Kurve nur, solange genug Räume anfordern** (neue Thermen-Option, Standard: die Hälfte der Räume). Ein einzelner offener Heizkörper schickt das Wasser fast so warm zurück, wie es kam; das hat die Kurve früher fälschlich gesenkt.
- **Lernschritte nach oben werden größer** (1, 2, dann 4 Grad), solange die Kurve weiter steigt; ein Schritt in die andere Richtung halbiert die Größe.
- **Eine Korrektur, die passt, bleibt.** Nach drei Stunden in einem Lastfall, ohne dass ein Raum hinterherhinkt, folgt sie nicht mehr jeder kleinen Abweichung. So geht der Vorlauf nicht mehr ständig hoch und runter.

### Behoben

- Ein Neustart von Home Assistant stoppt eine laufende Wärmepumpe nicht mehr; sie läuft weiter, bis die Temperaturen der versorgten Räume wieder bekannt sind.
- Die aktive Steuerung übernimmt ein Gerät, das schon heizt, wenn sie es vorfindet; ein Gerät, das von Hand eingeschaltet wurde, bevor die Steuerung aktiv wurde, wurde nie gestoppt.
- Eine Wärmepumpe, die ihren Kompressor selbst ein- und ausschaltet, bekommt jetzt einen COP, gemessen über ganze Takte.
- Der COP wird nicht mehr unbekannt, solange der Feuchtesensor der Ansaugluft, der nur bei Änderung meldet, auf einem Wert stehen bleibt.

## 0.5.5 – 11.10.2026

Das Lernen der Therme ist neu gebaut. Die Konfiguration bleibt bei Version 3. Was bisher gelernt wurde, bleibt als Höhe der Kurve erhalten.

### Geändert

- **Die Heizkurve wird in Höhe und Steigung korrigiert,** beides zu sehen im Lernstatus. Ein Schritt an einem milden Tag verschiebt vor allem die Höhe, einer bei Kälte vor allem die Steigung. So stellt ein milder Oktober nicht mehr die Kurve für den Januar ein.
- **Brennerläufe werden am besten Zeichen erkannt, das die Anlage hat:** am Gaszähler, sonst am Statuscode der Therme, sonst am Zustand der Pumpe. Brennerläufe für Warmwasser zählen nie.

### Behoben

- Ein Brennerlauf, der gerade läuft, übersteht einen Neustart als ein einziger Lauf.
- Der Lernstatus sagt nicht mehr „Pausiert“, während er von einer taktenden Therme lernt.

## 0.5.4 – 11.10.2026

Das Lernen kommt mit einer Therme zurecht, die taktet; das tun die meisten Thermen bei mildem Wetter. Die Konfiguration bleibt bei Version 3.

### Neu

- **Sensor *Therme Brenneranteil*:** der Anteil der letzten Stunde, in dem der Brenner für die Heizung lief.
- **Anleitung:** Ist in Zigbee2MQTT die Entität *Last seen* eines Ventils eingeschaltet, wird ein gesundes Thermostat nicht als stumm gemeldet.

### Geändert

- **Eine taktende Therme lernt aus den Räumen.** Ein anfordernder Raum, der unter Soll bleibt und nur langsam warm wird, hebt die Kurve an; Takten senkt sie nur, solange jeder anfordernde Raum zügig warm wird.

## 0.5.3 – 10.10.2026

Nur Dokumentation; der Code ist der von 0.5.2.

### Neu

- **Der stündliche Brennerstart in der Nacht** (die Warmstart-Funktion einer Kombitherme) und wie du sie an der Therme abschaltest, stehen in der Tabelle der Fehlersuche.
- **Ein Knopf öffnet das Repository in HACS.**

## 0.5.2 – 10.10.2026

Die Konfiguration bleibt bei Version 3.

### Neu

- **Thermen-Option *Warmwasser bereithalten*,** wie bisher standardmäßig an. Ausgeschaltet heizt eine Kombitherme ihr Warmwasser nicht mehr, solange kein Hahn offen ist; mit einem Warmwasserspeicher eingeschaltet lassen.

### Behoben

- Ein Heizkörperthermostat gilt nach zweieinhalb Stunden als stumm, nicht schon nach einer.
- Eine Komfortzeit bis 23:59 schließt die letzte Minute des Tages ein.

## 0.5.1 – 09.10.2026

Die Konfiguration bleibt bei Version 3; zurück auf 0.5.0 geht es ohne Backup. Ohne Gaszähler fallen die Sensoren *Gasleistung* und *Gasenergie* weg, die nur „unbekannt“ anzeigen konnten: Entferne jede Dashboard-Karte, die sie zeigt.

### Neu

- **Abschnitt „Ohne festen Zeitplan“ in der Anleitung.**
- **Der Lernstatus sagt, welche Änderung ihn pausiert hat,** und bis wann.

### Geändert

- **Stellst du den Komfort unter die Absenkung, sinkt die Absenkung mit,** statt dass die Änderung abgelehnt wird.

### Behoben

- Ein Zeitplan-Block bis 24:00 läuft über Mitternacht hinweg.

## 0.5.0 – 04.10.2026

Drei weitere Wochen mit einer echten Therme, Tag und Nacht gesteuert, die ersten kalten Abende der Saison eingeschlossen: weniger Brennerstarts, Warmwasser am eigenen Statuscode der Therme erkannt, und eine Steuerung, die weiterläuft, wenn ein Sensor, ein Ventil oder ein Befehl ausfällt. Die Konfiguration wechselt auf Version 3, die ein älteres Thriftherm nicht lädt: Mach vorher ein Backup von Home Assistant, falls du vielleicht zurückwillst.

### Neu

- **Mitheizen.** Solange die Therme läuft, bekommen Räume in ihrer Komfortzeit, die unter Soll liegen, 0,5 Grad mehr, und ein Vorheizen, das innerhalb der nächsten Stunde fällig ist, beginnt sofort: längere Brennerläufe, weniger Starts.
- **Echo-Filter für Better Thermostat** (Option, standardmäßig an). Späte Funk-Echos gelten nicht mehr als Dreh am Thermostat und werden nicht mehr zu zweistündigen Übersteuerungen.
- **Vorläufige Rückkehr.** Solange keine Rückkehr geplant ist, wird ein für *Geplante Rückkehr* gewähltes Datum zum Platzhalter um 23:59 dieses Tages, der nicht vorheizt; erst die danach gewählte Uhrzeit setzt die echte Rückkehr.
- **Der Sensor *Heizbedarf* jedes Raums zeigt, ob der Raum die Therme anfordert.**
- **Einrichtungsanleitung:** eine DHCP-Reservierung für den eBUS-Adapter und jedes andere Netzwerkgerät, von dem Thriftherm Werte liest, und das eigene Signal von ebusd als eBUS-Signal, nicht der Verbindungssensor des Adapters.
- **Anleitung:** Abwesend mit und ohne geplante Rückkehr, und was ein Browser in einer anderen Zeitzone bewirkt.

### Geändert

- **Räume fordern die Therme mit Hysterese an:** ab 0,3 Grad unter Soll, bis sie weniger als 0,1 Grad darunter liegen. So springt die Therme für die letzten Zehntel nicht mehr nur eine Minute lang an.
- **Ein Raum, der knapp unter Soll hängt, gilt als erreicht,** sobald er eine Stunde geheizt hat; er fordert wieder an, wenn er 0,5 Grad unter Soll liegt oder ein neues Soll bekommt.
- **Keine Anforderung in der letzten halben Stunde einer Komfortzeit;** *Schnell aufheizen* ist ausgenommen.
- **Nur anfordernde Räume heben den Vorlauf an.** Ein Raum, der als erreicht gilt oder am Ende seiner Komfortzeit ist, trägt nichts mehr dazu bei.
- **Die Bad-Trocknung läuft nur mit aktiver Wärmepumpen-Steuerung.** Das Raum-Formular lehnt sie ohne *Wird von der Wärmepumpe beheizt* oder ohne Feuchtesensor ab, und nach ihrem Zeitlimit wartet sie drei Stunden.
- **Solange die Wärmepumpe kühlt, bleiben die Räume, die sie versorgt, höchstens auf Absenkung,** auch gegen einen Dreh am Thermostat, und fordern keine Wärme von der Therme an. Die Solltemperatur zeigt *Wärmepumpe kühlt: Heizkörper auf Absenkung*.
- **Der Betriebsmodus bietet nur an, womit die Anlage heizen kann:** *Nur Wärmepumpe* braucht eine Wärmepumpe, *Nur Heizung* eine zentrale Wärmequelle.
- **Viel weniger Zeilen in der Datenbank von Home Assistant.** Detail-Sensoren ändern sich in gröberen Schritten und sind bei einer neuen Installation anfangs ausgeschaltet (bestehende bleiben, wie sie sind), und lange Attributlisten werden nicht mehr aufgezeichnet.
- **Die Formulare lehnen Einstellungen ab, die nie funktionieren können:** eine Heizkurve, die bei −10 °C niedriger liegt als bei +15 °C, eine minimale Vorlauftemperatur über der maximalen, einen Raum, der den Namen eines anderen Raums bekommen soll.
- **Die Einrichtung bricht bei einer Installation in °F ab,** mit einer Meldung; Thriftherm arbeitet nur in °C.

### Behoben

Therme und Heizkurve:

- Warmwasser wird am eigenen Statuscode der Therme erkannt: Der Pumpennachlauf nach einem Heizlauf und ein Heizlauf nach einem eBUS-Ausfall gelten nicht mehr als Duschen, und eine Vermutung wird zurückgenommen, wenn die Therme meldet, dass sie geheizt hat.
- Eine kalte Therme verliert nach sechs ruhigen Stunden nicht mehr ihre Daten. Dadurch hatte Thriftherm aufgehört zu senden, und die Therme fiel auf ihren Drehknopf zurück.
- Ein Gaszähler, der wegfällt, während Gas brennt, lässt die Therme nicht mehr bis zu seinem nächsten Wert „brennen“.
- Eine ausgeschaltete oder fehlende Wärmepumpe pausiert das Lernen der Therme nicht mehr.
- Eine Änderung der minimalen oder maximalen Vorlauftemperatur behält die gelernte Korrektur der Heizkurve; nur die zwei Punkte der Kurve setzen sie zurück.
- Der Vorlauf-Sollwert steigt höchstens 5 Grad je 10 Minuten, auch wenn ein Soll hoch- und wieder runtergeklickt wird.
- Der Lernstatus sagt, warum er wartet, etwa „Therme heizt gerade nicht“ oder Takten am Vorlauf-Minimum.

Räume:

- Ein ausgefallener Raumsensor wird durch das ersetzt, was das Ventil selbst misst, auch hinter Better Thermostat, das weiter den veralteten Wert des Sensors anzeigte.
- Ein Heizkörperthermostat, das eine Stunde lang stumm war, fordert keine Therme mehr an, und eine Reparaturmeldung sagt das; das gilt auch, wenn die Zigbee-Bridge das Ventil als nicht verfügbar markiert.
- Ein Raumsensor, der nur bei Änderung meldet, gilt nicht mehr als tot, solange ein anderer Sensor desselben Geräts weiter meldet.
- Nach einem Neustart gilt ein Raum knapp unter Soll nicht mehr als stehengeblieben.
- Eine in den Raum-Optionen geänderte Temperatur gilt wieder, auch nachdem Komfort oder Absenkung im Dashboard gesetzt wurden.
- Ein gelöschter Raum hinterlässt nichts; ein Raum, der unter demselben Namen neu angelegt wird, übernimmt nicht mehr dessen gelernte Werte und Übersteuerungen.

Abwesend:

- Eine Rückkehr lässt sich in den Feldern für Datum und Uhrzeit von *Geplante Rückkehr* setzen, auch für heute: erst das Datum, dann die Uhrzeit; Automationen sollten `thriftherm.set_away` nutzen, das Mitternacht so nimmt, wie sie ist.
- Änderungen der Rückkehr oder von Abwesend zeigen sich sofort in *Geplante Rückkehr* und im Betriebsmodus.
- Die Solltemperatur bei der Rückkehr stimmt auch über eine Zeitumstellung hinweg.

Wärmepumpe:

- Eine Störung der Wärmepumpe allein schränkt den Sicherheitsstatus nicht mehr ein, wo eine Therme oder Fernwärme heizt; sie steht weiter in den Attributen.
- Ein gescheiterter Befehl an die Wärmepumpe wird im nächsten Zyklus erneut gesendet, statt als gesendet und danach als Änderung von Hand zu gelten.
- Wird das Gelernte der Wärmepumpe vergessen, bleibt eine Vereisungssperre bestehen.

Home Assistant:

- Ein gescheiterter Befehl an Better Thermostat, die Wärmepumpe oder ebusd hält die anderen nicht mehr auf; er wird als Warnung protokolliert, und die anderen Räume bekommen trotzdem ihre Sollwerte.
- Ein Preis von 0 stoppt nicht mehr die ganze Steuerung; das Preis-Formular verlangt mindestens 0,001 €/kWh und einen Anteil nach Verbrauch von mindestens 1 %.
- Ohne einen einzigen Raum hält sich Thriftherm heraus (Sicherheitsstatus *Fallback*), statt die Therme alle zwei Minuten zu sperren.
- Ein Wert wie „nan“ oder „inf“ aus einer anderen Integration stoppt den Zyklus nicht mehr.
- Der Betriebsmodus, die Auswahlfelder der Steuerung, Komfort und Absenkung, *Geplante Rückkehr* und *Schnell aufheizen* bleiben bedienbar, wenn ein Zyklus scheitert.
- Die Steuerung läuft weiter, auch wenn jede Entität von Thriftherm deaktiviert ist, und der Zustand wird gespeichert, wenn Home Assistant stoppt.
- *Außentemperatur (gewählte Quelle)* zeigt den letzten bekannten Wert, der einspringt, wenn jede Außenquelle ausfällt; *Therme Heizbetrieb aktiv* ist nicht mehr gleichzeitig mit *Therme Warmwasser aktiv* an.
- Eine Änderung der Heizungsart in den Optionen fragt nach deren Preisen, bevor etwas gespeichert wird.
- Das Deaktivieren von Thriftherm entfernt seine Reparaturmeldungen, und das Entfernen löscht das gespeicherte Gelernte.

## 0.4.0 – 18.09.2026

Erste Version, die eine echte Therme Tag und Nacht steuert.

### Neu

- **Automatische Erkennung über ebusd** einer bekannten Therme (erstes Profil: Vaillant `bai`), des eBUS-Signals und des Gaszählers.
- **Anlagen ohne eigene Therme:** Fernwärme oder eine Wärmepumpe allein. Die Wärmepumpen-Erweiterung ist eine Vorschau, noch nicht mit einer echten Wärmepumpe getestet.
- **Eine geplante Rückkehr, die du einstellen kannst;** setzt du eine, schaltet Thriftherm auf Abwesend.
- **Zeitpläne und Raumtemperaturen in der Oberfläche.**
- **Takten senkt die Heizkurve,** nie unter die minimale Vorlauftemperatur.
- **Gelerntes lässt sich vergessen,** je Bereich.
- **Deutsch und Englisch,** mit Einrichtungsanleitung und Anleitung in beiden Sprachen; Diagnose und Reparaturmeldungen.

### Geändert

- **Warmwasser wird nie angefasst** und bleibt aus dem Lernen heraus.
- **Die Bad-Trocknung läuft über die Wärmepumpe.**

### Behoben

- Kein Brennerlauf wird verpasst, eBUS-Aussetzer von einer Sekunde werden ignoriert, und Timer überstehen einen Neustart.
- Ein von Hand ausgeschaltetes Thermostat fordert keine Therme mehr an.

### Entfernt

- **Fünf Einstellungen, die kein Code nutzte:** die Entitäten für Vorlauf-Sollwert und Speichertemperatur der Therme, die Entitäten für Feuchte der Ausblasluft und Energie der Wärmepumpe und die Ersatz-Raumtemperatur.

## 0.3.0

Umbenannt in Thriftherm (Domain `thriftherm`).
