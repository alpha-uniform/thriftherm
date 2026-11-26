# Wie Thriftherm rechnet

[English](../en/HOW_IT_WORKS.md) | **Deutsch**

Für die Bedienung reicht die [Anleitung](ANLEITUNG.md). Hier steht, wie Thriftherm intern entscheidet. Fachbegriffe erklärt die [Anleitung, Begriffe](ANLEITUNG.md#begriffe).

Die Zahlen stammen aus dem Code. „Standard“ heißt: in den Optionen einstellbar. Alle anderen Werte sind fest.

**Inhalt**

- [Abwesend und geplante Rückkehr](#abwesend-und-geplante-rückkehr)
- [Änderungen von Hand](#änderungen-von-hand)
- [Vorheizen](#vorheizen)
- [Thermensteuerung](#thermensteuerung)
- [Aufheiztempo](#aufheiztempo)
- [Lernen](#lernen), mit [Wie lange das Lernen dauert](#wie-lange-das-lernen-dauert)
- [Wärmepumpe](#wärmepumpe)

## Abwesend und geplante Rückkehr

Die Bedienung, auch ein erneutes *Abwesend*, steht in der [Anleitung](ANLEITUNG.md#abwesend-mit-und-ohne-rückkehr). Hier die Regeln, nach denen *Geplante Rückkehr* deine Eingaben liest.

- **Erst das Datum:** Solange nichts geplant ist, nimmt das Uhrzeitfeld nichts an.
- **Nur ein Datum ist ein Platzhalter:** Solange keine Uhrzeit gewählt ist, steht die Rückkehr vorläufig auf 23:59 des gewählten Tages, heute wie an jedem späteren Tag. Ein anderes Datum nimmt die vorläufige 23:59 mit. Eine vorläufige Rückkehr heizt nicht vor. Erst die Uhrzeit, die du danach wählst, gilt, und für sie wird rechtzeitig vorgeheizt. Wählst du keine, endet Abwesend trotzdem um 23:59.
- **Datum ändern, wenn schon eine Uhrzeit geplant ist:** Das neue Datum behält die geplante Uhrzeit und gilt sofort. Wählst du heute und ist diese Uhrzeit schon vorbei, steht wieder vorläufig 23:59 da.
- **Was abgelehnt wird**, jeweils mit einer Meldung: eine heute schon vergangene Uhrzeit, ein vergangener Tag und, wenn es schon nach 23:59 ist, das heutige Datum allein. Ausnahme: 00:00 in der Minute nach einer erreichten Rückkehr wird zum Platzhalter 23:59, solange 23:59 noch bevorsteht.
- **Wirklich um 23:59 zurück:** Steht dort die vorläufige 23:59, schickt das Uhrzeitfeld für 23:59 nichts, weil sich nichts ändert. Eine 23:59, die trotzdem ankommt (etwa per `datetime.set_value`), bleibt vorläufig. Deshalb erst eine andere Minute wählen, z. B. 23:58. 23:59 erst wählen, wenn 23:58 angezeigt wird und das Attribut *Vorläufig* auf `false` steht. Oder `thriftherm.set_away` mit `return_time` nehmen; das setzt Datum und Uhrzeit in einem Schritt.

### Browser in einer anderen Zeitzone

Beide Felder zeigen und rechnen in der Zeitzone des Browsers. Datum und Uhrzeit gelten also in Browser-Zeit. Beispiele mit Home Assistant in Berlin:

- **Ohne geplante Rückkehr** schickt der Datumsschritt Mitternacht des Browsers. In Home Assistant ist das eine andere Uhrzeit: London 01:00; östlich von Home Assistant am Vortag, Helsinki 23:00, Tokio 17:00, im Winter 16:00. Das ist dann kein Platzhalter, sondern eine echte Zeit. Ist sie schon vorbei, wie für heute meist, wird sie abgelehnt. Sonst gilt sie als Rückkehr zu dieser Stunde, mit Vorheizen.
- **Mit geplanter Rückkehr** gilt eine im Uhrzeitfeld gewählte Uhrzeit in Browser-Zeit: In London gewählte 12:00 sind in Berlin 13:00. Auch der Tag kann sich verschieben. Östlich von Home Assistant erscheint die vorläufige 23:59 schon als nächster Tag (Helsinki: 00:59), eine dann gewählte Uhrzeit landet einen Tag später. Westlich kann das Rückkehrzeiten kurz nach Mitternacht treffen (3. Oktober 00:30, in London auf den 5. Oktober gelegt: 6. Oktober 00:30).
- **Sommer- und Winterzeit:** Stellen Browser und Home Assistant nicht am selben Tag um, etwa ein Browser in den USA oder in einer Zone ohne Sommerzeit wie Dubai, kann ein reiner Datumsschritt über eine Umstellung hinweg die Uhrzeit um eine Stunde verschieben. Eine vorläufige Rückkehr wird dann echt, und es wird vorgeheizt (vorläufig 26. Oktober 23:59, in New York auf den 3. November gelegt: 4. November 00:59).

Deshalb nach jeder Eingabe *Geplante Rückkehr* und das Attribut *Vorläufig* prüfen, den Browser auf die Zeitzone von Home Assistant stellen oder `thriftherm.set_away` nehmen.

### Aus Automationen

`thriftherm.set_away` mit `return_time`, oder mit `clear_return_time: true` ohne Ende. Dort ist jede Zeit in der Vergangenheit ein Fehler, auch eine von heute. Jede Zeit gilt so, wie sie kommt, auch Mitternacht.

`datetime.set_value` auf *Geplante Rückkehr* liest Werte dagegen wie die Felder: Mitternacht ohne geplante Rückkehr wird zum Platzhalter 23:59, auch an einem späteren Tag. Und solange *Geplante Rückkehr* nicht verfügbar ist (Thriftherm nicht geladen), verwirft Home Assistant `datetime.set_value` ohne Fehlermeldung. `set_away` meldet dagegen einen Fehler. Automationen nehmen deshalb `set_away`.

### Rückkehr über Anwesenheit

Sind in den Optionen Personen gewählt, beendet Thriftherm *Abwesend*, sobald eine davon heimkommt. Das geht denselben Weg wie eine erreichte geplante Rückkehr: zurück auf Automatik, die Rückkehr wird gelöscht.

- **Nur Heimkommen zählt:** ein Wechsel der Person auf *Zuhause* (`home`) von einem bekannten Ort, also *Abwesend* (`not_home`) oder einer Zone. Wer beim Einschalten von *Abwesend* zu Hause ist, beendet es nicht, denn man schaltet *Abwesend* meist beim Gehen ein. Eine Person reicht.
- **Unbekannt ist keine Nachricht:** `unknown` und `unavailable` ändern nichts, es zählt der letzte bekannte Ort. Wer erst unterwegs, dann eine Weile nicht verfügbar und dann zu Hause ist, ist heimgekommen.
- **Nach einem Neustart** gilt der Ort, den eine Person dann hat, so wie er ist. Eine Person, die erst danach erscheint, gilt nicht als heimgekommen: Ob sie gerade kam oder schon da war, lässt sich nicht sagen.
- **Weggehen** schaltet nie auf *Abwesend*. Die geplante Rückkehr gilt daneben weiter, und gelesen wird nur der Zustand der Personen in Home Assistant.

### Feuchteschutz

Kommt die Luft im Raum während *Abwesend* ihrem Taupunkt näher als 3 Grad (Standard, *Abwesend: Taupunktabstand für Feuchteschutz*), hebt Thriftherm den Sollwert an. Er steht dann 1 Grad über der Raumtemperatur, höchstens auf der Komforttemperatur. Grund: *Abwesend, gegen Feuchte angehoben*. Die Obergrenze verhindert, dass ein Feuchtesensor, der bei fast 100 % hängt, den Sollwert Grad für Grad mitzieht.

## Änderungen von Hand

Was eine Änderung von Hand bewirkt und wo du sie siehst, steht in der [Anleitung](ANLEITUNG.md#änderungen-von-hand). Hier steht, woran Thriftherm sie erkennt.

Thriftherm schreibt Sollwerte und muss deshalb wissen, wer einen Sollwert geändert hat. Das sagt Home Assistant selbst: Jede Änderung trägt mit, woher sie kommt.

| Woher | Beispiel | Gilt als Änderung von Hand? |
|---|---|---|
| Thriftherm selbst | der eigene Befehl und die Antwort des Geräts darauf | nie |
| Jemand in Home Assistant | Dashboard, App, Sprachassistent | immer, sofort |
| Eine Automation oder ein Skript | „vor dem Duschen 23 °C“, auch nach der Uhr | immer, sofort |
| Am Gerät | Drehknopf am Ventil, Fernbedienung der Klimaanlage, App des Herstellers; auch was sich änderte, während das Gerät nicht erreichbar war (ein Raumthermostat nur, solange Home Assistant läuft: Kommt es nach einem Neustart mit einem anderen Wert zurück, gilt wieder der Plan) | ja, außer das Gerät erklärt die Änderung selbst (eigene Regel von Versatile Thermostat, verspätetes Funk-Echo bei Better Thermostat). An der Wärmepumpe erst, wenn sie zwei Minuten Zeit hatte, einem Befehl zu folgen |

Ein fremder Sollwert an einem Thermostat oder an der Wärmepumpe startet sofort einen Zyklus, ohne auf den nächsten zu warten. Eine eigene Automation, die die Anzeige beschleunigt, ist deshalb nicht nötig.

### Echo-Filter

Better Thermostat schreibt den Sollwert des Heizkörperthermostats alle paar Sekunden neu. Kommt über Zigbee eine Antwort verspätet an, hält Better Thermostat den alten Wert für einen Dreh am Thermostat. Der Echo-Filter (Optionen → Regelparameter, standardmäßig an) erkennt das: Springt Better Thermostat auf einen Wert, den das Thermostat in der letzten Minute schon hatte und wieder verlassen hatte (z. B. 16 → 16,5 → 16), wird das ignoriert und der geplante Sollwert neu gesendet. Ein echter Dreh bringt einen neuen Wert und wird übernommen. Wann zuletzt ein Echo ignoriert wurde, zeigt der Sensor *Thermostat-Plan* des Raums.

### Dreh am Ventil hinter einem Raumthermostat

Über einen Dreh am Ventil entscheidet zuerst das Raumthermostat. Bei Better Thermostat hängt das an seinem Schalter *Kindersicherung* für den Raum.

- **Kindersicherung aus:** Better Thermostat übernimmt die Zahl, die das Ventil nach dem Dreh zeigt, als Sollwert für den Raum. Thriftherm führt das als Änderung am Gerät. Achtung: Das ist die Zahl am Ventil, nicht der Unterschied. Zeigt das Ventil wegen der Korrektur von Better Thermostat 16 °C bei 18,5 °C für den Raum, machen drei Rasten nach unten daraus 14,5 °C für den Raum.
- **Kindersicherung an:** Better Thermostat stellt das Ventil zurück und behält seinen Sollwert. Das Ventil springt kurz auf den gedrehten Wert und gleich wieder zurück. Daran hält sich auch Thriftherm: Der Dreh zählt nicht.
- **Die Sperre am Ventil selbst** ist davon unabhängig. Sie am Ventil aufzuheben, schaltet die Kindersicherung von Better Thermostat nicht aus.
- **Versatile Thermostat ohne *Änderungen am Gerät folgen*** macht mit dem Dreh gar nichts. Dann übernimmt Thriftherm ihn nach fünf Sekunden selbst, und zwar als Unterschied: Drei Rasten nach unten sind 1,5 Grad weniger für den Raum.

### Ventile direkt

Was ein direkt gesteuertes Ventil bekommt, steht in der [Anleitung](ANLEITUNG.md#ventile-direkt-ohne-raumthermostat-vorschau). Hier die Regeln.

| Was | Regel |
|---|---|
| Weg | Fremdtemperatur, wenn das Ventil einen Eingang hat (und seine Fühlerauswahl in HA nicht deaktiviert ist); sonst Kalibrier-Offset; sonst Sollwert-Korrektur; ohne Ist-Temperatur nur der Sollwert |
| Sollwert | will der Raum Wärme (er fordert die Therme an oder heizt mit): Ziel + das Größte aus der Öffnungsschwelle des Ventils (TRVZB/TRVZBT: *Temperaturgenauigkeit*) und „Toleranz über dem Ziel“ + 0,2, in den Schritten des Ventils aufgerundet; sonst das Ziel. So ist das Ventil offen, solange die Therme für den Raum läuft, und schließt danach am Ziel. Nur mit eigener Therme, die Thriftherm aktiv steuert; bei Fernwärme, ohne Therme oder in *Nur beobachten* immer das Ziel: Dort ist das Ventil das Einzige, was den Raum regelt, und kein Ruf startet einen Brenner. Der Vorhalt gilt nur für Thermostate mit eigener Regelung. Das „ruht“ des Ventils hält den Raum nicht vom Anfordern ab |
| Sollwert-Korrektur | Ziel plus eigene Messung des Ventils minus Raum, über 30 Minuten gedämpft, −2 bis +3 Grad, in den Schritten des Ventils (unter dem Ziel aufgerundet, zurück erst anderthalb Schritte darunter), höchstens alle 10 Minuten geändert; nach oben weggenommen, sobald der Raum sein Ziel erreicht, und erst 0,2 Grad darunter wieder gegeben; ein Dreh zählt ohne sie |
| Fremdtemperatur | Raumfühler auf 0,1 Grad, bei einer Änderung ab 0,1 Grad (frühestens nach 60 Sekunden), sonst alle 25 Minuten neu; sofort, wenn das Ventil 0 zeigt (nach einem Neustart) oder von selbst auf den eigenen Fühler gegangen ist |
| Raumfühler gilt als frisch | höchstens 2 Stunden alt (so lange hält auch das TRVZB/TRVZBT einen Wert), oder sein Gerät meldet in der Zeit andere Werte bzw. ZHA hat es gesehen (Home Assistant schreibt einen unveränderten Wert nicht neu), dann bis 24 Stunden; mit *Raumfühler melden nur bei Änderung* wie für den Ruf |
| Fühlerauswahl | auf den Raum, solange der Raumfühler frisch ist, sonst auf den eigenen Fühler; nie gegen eine Wahl von Hand. „Remote source offline“ (TRVZB/TRVZBT nach 2 Stunden ohne Wert) bekommt nur den neuen Wert |
| Offset | Raum minus eigene Messung des Ventils, über 30 Minuten gedämpft, in den Schritten des Ventils, höchstens alle 10 Minuten geändert |
| Ventilschutz | ein Ventil, das 7 Tage (TRVZB/TRVZBT 84 Stunden) nicht offen war, bekommt zweimal das obere und das untere Ende seines Sollwerts, je mindestens 90 Sekunden (bei Zyklen von einer Minute etwa zwei); offen heißt: es meldet „heizt“, beim TRVZB/TRVZBT auch ein Sollwert 0,5 Grad über seiner Messung, oder eine Öffnung, die Thriftherm gesetzt hat (die Öffnung des TRVZB/TRVZBT ist sonst seine Grenze, ab Werk 100 %). Beginnt nur 10–18 Uhr, ein Ventil je Zyklus, nicht während die Therme heizt, der Raum anfordert oder sein Fenster offen ist, nicht bei ausgeschaltetem oder nicht erreichbarem Ventil |
| Fenster | nach der Fenster-Wartezeit Ventile aus (Standard) oder Frostgrenze als Sollwert ohne Vorhalt (Option); nach dem Schließen wieder an und auf das Ziel. Ein „aus“ wegen des Fensters ist keine Änderung von Hand; ein „aus“ einer Person bleibt |
| Weitere Ventile | Sollwert des Raums, ein neuer sofort, derselbe erneut höchstens alle 5 Minuten, solange eines etwas anderes zeigt |
| Ventil aus | bekommt nichts, der Modus gehört dir |
| Schreiben schlägt fehl | Rest des Zyklus entfällt (keine Auswahl ohne Wert), nächster Versuch nach 2, 4, 8 … Minuten, höchstens stündlich |
| Better Thermostat steuert das Ventil wieder | das Ventil bekommt nichts mehr von Thriftherm |
| Nie geschrieben | Kindersicherung, Fenstererkennung des Ventils, Wochenplan, Modus |

### Was keine Änderung von Hand ist

- **Der Befehl kam nicht an.** Zeigt die Wärmepumpe nach einem Befehl etwas anderes, ohne dass es jemand geändert hat, wird der Befehl wiederholt: nach 2, 4, 8 … Minuten, höchstens stündlich, solange das Gerät ihn nicht ausführt. Der Steuerbefehl zeigt, wie oft in Folge (*Befehle in Folge, die das Gerät nicht ausgeführt hat*). Nur wenn Home Assistant zwischen Befehl und Abweichung neu gestartet wurde, lässt sich das nicht mehr unterscheiden. Dann gilt die Abweichung als Änderung am Gerät.
- **Das Thermostat rundet.** Antwortet ein Thermostat in ganzen Grad auf 21,5 °C mit 22 °C, ist das unser Sollwert, so wie es ihn halten kann. Das ist keine Übersteuerung, und es wird nicht dagegen geschrieben. Zeigte es schon 22 °C, sodass keine Antwort kommt, geht 21,5 °C noch einmal hinaus und gilt dann als seine Antwort.
- **Wer ein Thermostat nur ausschaltet,** verlangt keine Temperatur. Daraus wird keine Übersteuerung.

Eine Grenze hat das: Schreibt ein Gerät seinen Zustand wenige Sekunden nach einem Befehl, gibt Home Assistant diese Änderung als die des Befehls aus, was auch immer sich geändert hat. Ein Druck auf die Fernbedienung in genau diesen Sekunden zählt deshalb zu dem, der zuletzt befohlen hat.

## Vorheizen

Thriftherm beginnt so früh, dass der Raum zu Beginn eines Komfort-Blocks warm ist:

Beginn = Blockbeginn − (Soll − Ist) / Aufheizrate − Sicherheitszuschlag

- Die Aufheizrate lernt Thriftherm je Raum, getrennt für Therme und Wärmepumpe ([Was gelernt wird](#was-gelernt-wird)). Gerechnet wird mit der Quelle, die den Raum heizen wird: In einem Raum der Wärmepumpe ist das die Wärmepumpe, wenn sie heizen darf und zuletzt günstiger war, oder im Modus *Nur Wärmepumpe*. Sonst ist es die Therme.
- Mit der Therme zählt eine gelernte Rate erst ab drei Aufheizphasen allein. Vorher vertritt die Start-Aufheizrate die fehlenden Phasen, und es zählt der mittlere Wert der Reihe (Median): Eine einzelne Phase verschiebt nichts, zwei, die übereinstimmen, entscheiden. Eine Phase an taktender Therme misst eher die Sperrzeit als den Raum.
- Liegt ein Raum mindestens 0,5 Grad unter seinem Ziel und heizt seine Quelle, heizt er auf, auch wenn er eine Stunde lang nicht weiter steigt: Bis zur letzten Messung dort zählt die Zeit mit. Über mindestens zwei Stunden genügen 0,2 Grad Anstieg, langsamer als 0,1 Grad je Stunde wird als 0,1 gelernt. So lernt ein Bad an taktender Therme seine 0,1–0,3 Grad je Stunde statt nur schneller Phasen. Knapp unter dem Ziel endet die Phase weiter an ihrem höchsten Punkt.
- Steigt ein Raum binnen zehn Minuten um ein halbes Grad, kommt die Wärme von woanders (Dusche, Sonne, Gerät): Die Phase bis dahin zählt nicht, eine neue beginnt.
- Bis etwas gelernt ist, gelten 0,8 Grad je Stunde für die Therme und 0,5 Grad je Stunde für die Wärmepumpe (Standard, *Start-Aufheizrate*): So viel schafft die Wärmepumpe bei kleinster Leistung.
- Ist auch der Auskühlkoeffizient des Raums bekannt, gilt die Rate fürs Wetter von jetzt: die Heizleistung, abzüglich dessen, was der Raum beim jetzigen Abstand nach draußen verliert, mindestens 0,1 Grad je Stunde. Bei 3 °C draußen hält die Wärmepumpe am Minimum einen Raum gerade, bei 8 °C wärmt sie ihn um rund ein halbes Grad je Stunde.
- *Längstes Vorheizen* in den Raumeinstellungen begrenzt den Vorlauf bei beiden Quellen (leer: höchstens 4 Stunden, 0: kein Vorheizen; mit der Therme auch länger als 4 Stunden, mit der Wärmepumpe nie), und der Raum fordert dann in seinem Block an; solange er Wärme will, lernt die Heizkurve nichts.
- Mit der Therme beginnt das Vorheizen ab Werk höchstens 4 Stunden vorher: Eine taktende Therme bringt bei längerem Vorheizen nicht mehr Wärme in den Raum, es kostet nur Gas. Vor der Rückkehr aus *Abwesend* gilt diese Grenze nicht: Nach Tagen auf Abwesenheitstemperatur fehlen oft mehrere Grad.
- Mit der Wärmepumpe beginnt das Vorheizen höchstens 4 Stunden vorher, auch wenn keine Therme hilft. Wird es so nicht rechtzeitig fertig, hilft die Therme oder die Wärmepumpe dreht höher, je nach Option; bei *Langsamer warm werden* hilft die Therme erst, wenn voraussichtlich ein halbes Grad fehlt, bei *Wärmepumpe allein* gar nicht ([Am Minimum halten](#am-minimum-halten)). Früher anfangen bringt wenig: In der Simulation lief sie mit 12 Stunden die ganze Nacht am Minimum, die Räume blieben trotzdem unter 21 °C, und die Absenkung war weg.
- Der Sicherheitszuschlag ist 20 Minuten (Standard).
- Vorgeheizt wird für Blöcke, die in den nächsten 12 Stunden beginnen.
- Vor einer Rückkehr aus *Abwesend* gilt dieselbe Rechnung. Ziel ist die Temperatur, die zur Rückkehr laut Plan gilt: aus den Zeitfenstern der Raum-Optionen oder aus dem Wochenplan des Zeitplan-Helfers. Fällt die Rückkehr in die letzte halbe Stunde einer Komfortzeit oder auf ihr Ende (ein Raum mit *Längstes Vorheizen*: nur auf ihr Ende), ist das Ziel, was danach kommt, meist die Absenkung: Für die paar Minuten Komfort wird nicht vorgeheizt. Muss das Vorheizen für die nächste Komfortzeit nach der Rückkehr schon vorher beginnen, wird für sie vorgeheizt, wie ohne Abwesend. Liegt die Absenkung unter der Abwesenheitstemperatur, bleibt der Raum bis zur Rückkehr auf der Abwesenheitstemperatur. Nennt ein Zeitplan-Helfer seine Blöcke nicht, kennt Thriftherm nur seinen nächsten Wechsel und nimmt an, dass bis zur Rückkehr höchstens einer liegt.

## Thermensteuerung

Die Bedienung steht in der [Anleitung](ANLEITUNG.md#thermensteuerung).

### Vorlauf

- **Heizkurve:** eine Gerade durch zwei Punkte, Standard 55 °C bei −10 °C und 30 °C bei +15 °C. Fehlt die Außentemperatur, nimmt Thriftherm die Mitte der beiden Punkte.
- **Dazu kommt, was der Raum verlangt, der am meisten braucht.** Bei den anderen Räumen nehmen die Ventile weniger ([Lastfälle und Werte je Raum](#lastfälle-und-werte-je-raum)). Der Raum verlangt:
  - seine gelernte Korrektur;
  - solange er anfordert, ein Bedarfszuschlag von 8 Grad mal seinem Heizbedarf. 100 % Heizbedarf heißt: Der Raum liegt 2 Grad unter Soll (Standard, *Abweichung für 100 % Heizbedarf*), mit seinem Trend eine halbe Stunde vorausgerechnet (Standard, *Trend-Vorausschau*);
  - solange er mit Zuschlag aufheizt, sein Zuschlag fürs [Aufheiztempo](#aufheiztempo).
- **Grenzen:** Erst die Summe wird auf Minimum und Maximum begrenzt.
- **Anstieg begrenzt:** höchstens 5 Grad je 10 Minuten, gerechnet ab dem Beginn des Anstiegs, und nie mehr als 5 Grad über dem zuletzt gesendeten Wert, auch nach einer Pause ohne Vorgabe. So springt die Therme nicht von 30 auf 60 °C. Frostschutz ist davon ausgenommen.
- **Anstieg wartet in der Sperrzeit:** Meldet die Therme nach einem Heizlauf Nachlauf oder Brennersperre (S.5 bis S.8), geht ein höherer Sollwert erst hinaus, wenn sie wieder zünden darf. Ein Anstieg in dieser Zeit ließ sie sonst Minuten zu früh zünden, jedes Mal ein Start mehr. Frostschutz und *Schnell aufheizen* warten nicht, und nach spätestens 30 Minuten geht der Anstieg auch so hinaus. Solange er wartet, zeigt *Therme-Steuerbefehl* unter *Wartet auf*: *Therme in Sperrzeit: Anstieg wartet*. Thermen ohne Statuscode sind nicht betroffen.
- **Heizen und Sperren** wechseln höchstens alle 5 Minuten. Frostschutz ist davon ausgenommen.
- **Frostschutz** heizt, bis der Raum 1 Grad über der Frostschutztemperatur liegt. So schaltet ein Raum knapp an der Grenze die Therme nicht alle paar Minuten an und aus. Ein Raum mit offenem Fenster zählt die ersten 30 Minuten nicht als Frostraum (die Kälte am Fühler kommt dann vom Fenster), danach wieder (ein vergessenes Fenster).

### Grenze der Therme

Liegt die kleinste Leistung der Therme weit über dem, was die Heizkörper abnehmen, erreicht sie den gesendeten Vorlauf in ein, zwei Minuten, läuft nach und wartet ihre Sperre ab. Im Mittel kommt dann viel weniger an als gesendet. Dann ist nicht die Heizkurve zu niedrig.

- **Erkannt** nach einer halben Stunde Heizen: mindestens zwei kurze Brennerläufe in der Stunde (im Median höchstens 5 Minuten), und der gemessene Vorlauf liegt im Mittel über eine halbe Stunde 8 Grad oder mehr unter dem gesendeten. Den Mittelwert zeigt der *Lernstatus* im Attribut `flow_mean_c`.
- **Die Heizkurve wird dann nicht angehoben** (Lernstatus: Grund `boiler_limit`). Versuche nach oben gehen ungewertet zurück.
- **Stattdessen geht mehr hinaus** (Attribut `boiler_limit_k`), solange ein rufender Raum langsamer steigt als geplant (mindestens 0,2 Grad je Stunde): 2 Grad, sobald der letzte Schritt 45 Minuten stand und bewertet ist. Steigen alle Räume eine halbe Stunde lang mit Reserve, geht es 1 Grad zurück, aber nie unter einen Wert, bei dem ein Raum nach einem Schritt zurück wieder zu langsam stieg.
- **Obergrenze:** 55 °C bei einer *Brennwerttherme* (Option, Standard an: Ihr Wirkungsgrad sinkt mit heißem Rücklauf), sonst die maximale Vorlauftemperatur. Begrenzter Anstieg und Warten in der Sperrzeit gelten weiter.
- **Gelernt** wird je Raum, der den Vorlauf bestimmt, je Lastfall und je Außentemperatur-Bereich von 5 Grad (Attribut `boiler_limit_learned_k`): womit die Räume in einer Heizphase zuverlässig stiegen. Eine einzelne Phase verschiebt nichts; zwei, die mehr als 1 Grad in dieselbe Richtung abweichen, verschieben um höchstens 2 Grad. Das nächste Heizen beginnt mit dem Gelernten. Ruft ein weiterer Raum mit, bleibt es beim Zuschlag; mehr nur, wenn dessen Linie mehr gelernt hat.
- **Nach einer kurzen Pause:** Heizt die Therme binnen zwei Stunden im selben Außentemperatur-Bereich wieder, beginnt sie einen Schritt unter dem, womit das letzte Heizen endete. Sonst fiele der Zuschlag nach einer kurzen Sperre auf 0 zurück, und es dauerte Stunden, bis es wieder reichte. Frostschutz allein beginnt ohne Zuschlag.

### Wann ein Raum die Therme anfordert

- Ein Raum fordert ab 0,3 Grad unter Soll an und hört auf, wenn er weniger als 0,1 Grad darunter liegt. Beides ist einstellbar (Regelparameter → Experte: *Toleranz unter dem Ziel* 0,2 bis 0,5, *Toleranz über dem Ziel* −0,1 bis 0,5); ein breiteres Band lässt die Therme seltener starten, der Raum schwankt dafür stärker. Die Lernregeln rechnen mit dem eingestellten Band: Ein Ruf gilt als Halten, wenn er höchstens 0,2 Grad tiefer als der Beginn startet (mindestens bis 0,5 unter Soll), und „Raum kommt nicht hin“ beginnt 0,1 Grad vor dem Beginn (mindestens 0,2). Hilft die Therme der Wärmepumpe ([Am Minimum halten](#am-minimum-halten)), fordern deren Räume ab 0,2 Grad unter Soll an, bis sie am Soll sind.
- **Das Thermostat hat ein Wort mit:** Meldet es, dass es nicht heizt (`hvac_action` *idle* oder *off*, sein Ventil zu), ruft der Raum die Therme nicht, auch wenn Thriftherm ihn anfordern würde (*Thermostat heizt nicht: kein Ruf an die Therme*). Wärme für ein geschlossenes Ventil hilft keinem Raum. Die Hysterese läuft dabei weiter, ein Thermostat, das heizt, ruft nie von sich aus, und eines ohne diese Angabe ändert nichts ([#3](https://github.com/alpha-uniform/thriftherm/issues/3)).
- **Stillstand:** Läuft die Anforderung seit einer Stunde, liegt der Raum höchstens 0,3 Grad unter Soll und steigt langsamer als 0,15 Grad je Stunde, gilt er als erreicht. Er fordert erst wieder an, wenn er 0,5 Grad unter Soll fällt oder sich der Sollwert ändert. Ein unbekannter Trend, etwa kurz nach einem Neustart, gilt nicht als Stillstand.
- **Sinkt der Sollwert** (z. B. eine Übersteuerung endet), wird eine laufende Anforderung neu mit der Startschwelle bewertet.
- **Ende der Komfortzeit:** In der letzten halben Stunde einer Komfortzeit fordert kein Raum mehr an, außer einer mit *Längstes Vorheizen*, dort beginnt eine neue Anforderung bis 5 Minuten vor dem Ende. Die Wärme käme zu spät. Das gilt auch kurz nach dem Ende, solange der Zeitplan noch nicht umgeschaltet hat (bis zu 2 Minuten).
- *Schnell aufheizen* ist von Stillstand und Komfortende ausgenommen. Ein Raum, dem die Therme für die Wärmepumpe hilft, fordert auch in der letzten halben Stunde an: Die Wärmepumpe bleibt für ihn am Minimum, sonst gäbe niemand die Spitze dazu. Als Stillstand gilt bei ihm erst, wenn er zwei Stunden lang höchstens 0,2 Grad unter Soll hängt und kaum steigt; er fordert dann wieder an, sobald er 0,3 Grad unter Soll fällt. Sonst würde ein Raum, dessen Fühler nie ganz das Soll erreicht, die Therme die ganze Komfortzeit für einen Heizkörper takten lassen.

### Mitheizen

- Läuft die Therme ohnehin, bekommen andere Räume in ihrer Komfortzeit, die unter Soll liegen, 0,5 Grad mehr (Grund *Heizt mit*).
- Ein Raum, dessen Vorheizen in der nächsten Stunde begänne, fängt jetzt an (Grund *Heizt früher vor*), außer er hat ein *Längstes Vorheizen*.
- Warum: Ein einzelner Heizkörper nimmt nur etwa 1 kW ab, die Therme brennt aber mit mindestens 8 kW. Mehr offene Heizkörper bedeuten längere Brennerläufe und weniger Starts.
- In der Absenkung heizt ein Raum unter seinem Absenkungsziel bis zu diesem Ziel mit, nie darüber (Grund *Heizt bis zur Absenkung mit*). Die Wärme fehlt ihm ohnehin; jetzt mitgeheizt, braucht er später keinen eigenen Start. Ein Thermostat mit eigener Regelung (Better Thermostat) bekommt dabei nur sein Ziel und öffnet selbst anteilig.
- Ein direkt gesteuertes Ventil mit Öffnungsgrenze (Sonoff TRVZB/TRVZBT: *Ventilöffnungsgrad*) öffnet beim Mitheizen nur anteilig, nach dem, was dem Raum zu seinem Ziel fehlt, in 10-%-Schritten ab 20 %: bei 0,1 Grad 30 %, bei 0,2 Grad 50 %, bei 0,3 Grad 70 %, ab 0,45 Grad so weit, wie du es eingestellt hast. So nimmt der Raum die Wärme über den ganzen Brennerlauf ab, statt sein Ziel schnell zu erreichen und zu schließen. Sie ändert sich erst, wenn 10 Prozentpunkte mehr oder weniger gefragt sind: größer dann sofort, kleiner frühestens 10 Minuten nach der letzten Änderung. Dein eingestellter Wert ist die Obergrenze; Thriftherm merkt ihn sich (auch über einen Neustart) und stellt ihn zurück, sobald der Raum selbst anfordert, nicht mehr mitheizt, sein Fenster öffnet, das Ventil bewegt wird oder Thriftherm geht. In *Nur beobachten* schreibt Thriftherm nichts.
- Genauso öffnet ein Raum, der selbst anfordert, aber den Vorlauf nicht bestimmt. Es gibt einen Vorlauf für alle Heizkörper, und der Raum mit dem höchsten Bedarf gibt ihn vor (oft ein kleiner Heizkörper im Bad). Ein großer Heizkörper gibt an diesem Vorlauf mehr Wärme ab, als sein Raum braucht: Voll geöffnet schwingt der Raum über, das Ventil schließt, und die Therme, die nicht unter ihre kleinste Leistung kommt, taktet. Thriftherm kennt den Vorlauf, den jeder Raum selbst bräuchte (Heizkurve, seine gelernte Linie, sein Bedarf), und rechnet daraus den Anteil der Heizkörperleistung, den der Raum braucht: ((eigener Vorlauf − Raumtemperatur) / (gesendeter Vorlauf − Raumtemperatur)) hoch 1,3. Auf diesen Anteil öffnet das Ventil, in 10-%-Schritten und nie unter 20 %. Gedrosselt wird nur ein Raum, der höchstens 80 % braucht; darüber bleibt das Ventil so weit offen, wie du es eingestellt hast, denn zwischen 90 % Öffnung und ganz offen ändert sich am Durchfluss kaum etwas, und so wenig Überschuss bringt keinen Raum über sein Ziel. Ein gedrosseltes Ventil geht wieder ganz auf, sobald der Raum 90 % braucht. Geändert wird erst bei 10 Prozentpunkten mehr oder weniger, größer sofort, kleiner frühestens nach 10 Minuten. Beispiel: Das Bad gibt 55 Grad vor, das Wohnzimmer bräuchte 45 Grad bei 21 Grad im Raum; es braucht 64 % der Leistung, sein Ventil öffnet auf 60 %.
- Ganz offen (so weit, wie du es eingestellt hast) bleibt das Ventil des Raums, der den Vorlauf bestimmt, und jedes Raums, der zurückfällt: 0,5 Grad oder mehr unter seinem Ziel, seit einer Viertelstunde angefordert und langsamer als 0,2 Grad je Stunde steigend, oder langsamer, als sein Aufheizen geplant ist. Ebenso bei Frostschutz, Schnell-Aufheizen und Bad-Trocknung, und solange Thriftherm die Therme nur beobachtet. Was die Therme an ihrer eigenen Grenze mehr sendet, zählt dabei nicht als Vorlauf. Ein Raum, dessen Ventil so begrenzt war, sagt der Heizkurve und der Thermen-Grenze erst wieder etwas, wenn sein Ventil eine halbe Stunde lang ganz offen war: Sein langsames Steigen lag am Ventil, nicht am Vorlauf. Auch auf seine eigene Linie wird diese Zeit nicht gebucht. Ein Ventil ohne Öffnungsgrenze bleibt, wie es war. Im Sensor *Lernstatus* stehen der Vorlauf je Raum, die so begrenzten Ventile und die gelernte Korrektur als Attribute.
- Wie weit das Ventil öffnet, lernt Thriftherm je Raum, höchstens einen Schritt von 10 Prozentpunkten je Ruf: Muss das begrenzte Ventil wieder ganz geöffnet werden, weil der Raum zurückfällt (innerhalb einer halben Stunde nach der Begrenzung, bei unverändertem Ziel), öffnet es beim nächsten Mal 10 Punkte weiter. Endet der Ruf mit begrenztem Ventil und steigt der Raum in den folgenden 45 Minuten 0,3 Grad über sein Ziel, öffnet es beim nächsten Mal 10 Punkte weniger; das zählt nicht, solange der Raum mitheizt oder sein Fenster offen ist. Die Korrektur bleibt zwischen −30 und +50 Punkten, übersteht einen Neustart und geht mit *Lernen zurücksetzen* (Räume). Grund: Der eigene Vorlauf eines Raums, der fast nie den Vorlauf bestimmt, wird kaum gelernt, und ein halb offenes Ventil lässt mehr als die halbe Wärme durch.
- Mitheizende Räume fordern selbst nichts an und halten die Therme nicht am Laufen.
- Ein Raum, dessen Thermostat nicht heizen will, heizt nicht mit: Mit 0,5 Grad mehr öffnete es sein Ventil und riefe beim nächsten Mal selbst die Therme.

### Ausfälle und Wiederholung

- **Wiederholung:** Thriftherm sendet seinen Befehl alle 2 Minuten. Ohne Befehl fällt die Therme nach 9–16 Minuten auf ihren Drehknopf zurück. Ein verlorenes Telegramm schadet so nicht, und ein ausgefallenes Home Assistant gibt die Therme an ihren Knopf zurück.
- **Kurze Aussetzer:** Adapter verlieren das eBUS-Signal immer wieder für eine Sekunde. Erst wenn es zwei Minuten lang wegbleibt, gilt das als Datenausfall.
- **Nach einem Neustart** wartet Thriftherm bis zu fünf Minuten auf die Raumfühler, bevor es den Heizbetrieb sperrt. Bis dahin geht nichts hinaus, und die Therme behält ihren letzten Befehl.
- **Stummes Heizkörperthermostat:** Als stumm gilt ein Thermostat, wenn es einen Sollwert eine halbe Stunde lang nicht beantwortet hat und von seinem Ventil seither nichts kam, wenn alle seine Entitäten eine halbe Stunde lang nicht verfügbar sind oder nach sechs Stunden ganz ohne Meldung. Hinter Better Thermostat (oder Versatile Thermostat über ein Klima) gilt ein Sollwert erst als beantwortet, wenn sich ein Ventil dahinter gemeldet hat, denn die Hülle bestätigt sofort. Eine feste Frist von zweieinhalb Stunden löste Fehlalarme aus: Zigbee-Ventile, die denselben Wert noch einmal melden, hinterlassen in Home Assistant keine Spur. Als Lebenszeichen zählt jede Änderung an einer Entität des Thermostats; eine Meldung mit unveränderten Werten hinterlässt in Home Assistant keine Spur. Mit ZHA zählt auch ZHAs eigenes *Zuletzt gesehen*, außer ZHA führt das Gerät als nicht verfügbar.

### Warmwasser-Erkennung

ebusd meldet Warmwasser nicht bei jeder Therme zuverlässig. Deshalb erkennt Thriftherm es selbst. Meldet die Therme einen Statuscode, gilt der. Sonst gilt als Warmwasser:

- Gas brennt (über 1000 W am Gaszähler), obwohl der Heizbetrieb seit mindestens zwei Minuten gesperrt ist;
- der Vorlauf liegt mehr als 15 Grad über dem gesendeten Heizungs-Soll;
- der Rücklauf liegt mehr als 1 Grad über dem Vorlauf (nur ohne Statuscode);
- der Vorlauf liegt mehr als 2 Grad über dem Heizmaximum, solange dieses Maximum gilt, also Thriftherm einen Vorlauf oder eine Sperre sendet.

Die ersten beiden greifen innerhalb einer Minute, die Temperaturen kommen nur alle paar Minuten. *Therme Warmwasser aktiv* bleibt nach dem letzten Zeichen noch 10 Minuten an, mit Statuscode 2 Minuten. Das Lernen pausiert eine Stunde, damit Duschwasser nicht die Heizkurve verstellt.

**Warmstart.** Eine Kombitherme hält ihren Warmwasser-Wärmetauscher warm und brennt dafür etwa einmal pro Stunde kurz, auch wenn die Heizung gesperrt ist. Dabei meldet sie denselben Statuscode wie beim Heizen (S.4). Ist der Heizbetrieb seit mindestens zwei Minuten gesperrt, meldet sie trotzdem „Heizen“ und steht dabei die Heizungspumpe, ist das dieser Warmstart: Er zählt nicht als Brennerstart der Heizung, und sein Gas zählt als Warmwasser, auch was der Gaszähler kurz danach noch meldet. Läuft die Pumpe beim Brennen, heizt die Therme wirklich, etwa weil die Sperre sie nicht erreicht hat; ohne Angabe zur Pumpe gilt nichts als Warmstart. Das Lernen pausiert für einen Warmstart nicht, der Heizkreis blieb kalt, und der heiße Wärmetauscher danach zählt 20 Minuten lang nicht als Zeichen für Warmwasser.

### Frische Messwerte

ebusd liest die Werte der Therme reihum; Vorlauf und Rücklauf wären sonst minutenalt.

- Thriftherm setzt in ebusd die Abfrage-Priorität von Vorlauf, Rücklauf, Therme-Status, Heizungspumpe und Status-Nummer hoch.
- Das wird alle 10 Minuten erneuert, damit ein ebusd-Neustart kaum auffällt.
- Dazu fordert Thriftherm die Werte einmal pro Minute direkt an: mit Gaszähler, solange Gas brennt, ohne Gaszähler durchgehend.

Das sind nur Leseanfragen, an die Therme wird nichts geschrieben.

### ebusd: Pumpe und Statuswerte

Das letzte Feld der ebusd-Nachricht `Status01` heißt Pumpenstatus, folgt aber dem Heizbedarf: Es meldete „aus“, während die Pumpe hörbar lief. Deshalb liest Thriftherm:

- die Pumpe selbst aus **`WP`** (d.10);
- den Display-Code aus **`Statenumber`**: 8 = S.8 Brennersperrzeit, 7 = S.7 Pumpennachlauf, 31 = S.31 keine Heizanforderung.

`Status01` erkennt weiterhin Warmwasser und bleibt dafür im Einsatz. Fehlen `WP` und `Statenumber`, greift Thriftherm auf `Status01` zurück.

**Pumpenmodus:** Bei Pumpenmodus *Nachlauf* kreist das Wasser über den Bypass, und der letzte Heizkörper bleibt kalt. Mit *durchlaufend* kommt auch dort Wärme an.

## Aufheiztempo

Die Einstellung steht in der [Anleitung](ANLEITUNG.md#aufheiztempo).

**Anlass:** Wird ein Raum deutlich höher gestellt, bleibt der Vorlauf unter Umständen lange bei seinem Wert, die Therme taktet mit hohem Rücklauf, und der Raum kommt nur langsam hin.

**Beginn und Ende:** Ein Aufheizen beginnt, wenn ein Raum, der anfordert, 1 Grad oder mehr unter Soll liegt. Es endet, wenn er 0,2 Grad unter Soll ist oder keine Wärme mehr will.

**Messen:**

- Die erste Bewertung kommt 20 Minuten nach Beginn. Der Vorlauf steigt höchstens 5 Grad je 10 Minuten, und der Heizkörper muss erst warm werden.
- Danach misst Thriftherm alle 20 Minuten, wie schnell der Raum selbst seit der letzten Bewertung gestiegen ist.
- Gewertet wird ein Abschnitt nur, wenn die Therme darin geheizt hat, Thriftherm sie die ganze Zeit aktiv gesteuert hat und das Ventil des Raums die ganze Zeit offen war (das Thermostat meldete nicht „heizt nicht“).
- Der Raum muss dabei noch mindestens 0,5 Grad unter Soll sein. Näher dran drosselt das Ventil, und der Anstieg sagt nichts mehr.
- Maß ist der Anstieg im Raum, nicht die Spreizung: Ein kleiner Heizkörper und ein Raum, der viel Wärme verliert, sehen an der Therme gleich aus.

**Urteil** nach jedem gewerteten Abschnitt:

| Anstieg im Raum | Zuschlag |
|---|---|
| langsamer als die Hälfte des Tempos | 4 Grad mehr |
| langsamer als vier Fünftel des Tempos | 2 Grad mehr |
| mehr als anderthalbmal so schnell | 2 Grad weniger |
| dazwischen | bleibt |

- Der Zuschlag geht bis 16 Grad, der Vorlauf nie über sein Maximum.
- Stand der Vorlauf im Abschnitt schon am Maximum, stieg er gerade so schnell, wie er darf, oder lag das, was der Raum verlangt, noch unter dem Vorlauf-Minimum, gibt es keinen weiteren Zuschlag: Mehr käme nicht an. Weniger geht trotzdem. Sonst wüchse der Zuschlag, ohne den Vorlauf zu bewegen.
- War ein Aufheizen deutlich schneller fertig als gewünscht (im Schnitt seit Beginn mehr als anderthalbmal so schnell, nach mindestens 10 Minuten), nimmt Thriftherm 2 Grad weg, aber nur, wenn der Raum das angepeilte Soll erreicht hat, der Vorlauf die ganze Zeit von Thriftherm kam und die Therme brannte.

**Wirkung:** Der Zuschlag wird mit dem Heizbedarf des Raums malgenommen (0 bis 100 %). Je näher der Raum seinem Soll kommt, desto kleiner wird er.

**Merken:** Der Zuschlag am Ende eines Aufheizens ist der Start fürs nächste Aufheizen dieses Raums. Er bleibt über einen Neustart erhalten. Steht das Aufheiztempo auf 0, läuft kein Aufheizen; die gemerkten Werte bleiben für später.

**Lernen:** Solange ein Raum mit Zuschlag aufheizt, pausiert das Lernen der Heizkurve (Grund *ein Raum heizt mit Zuschlag auf*). Eine Therme, die auf dem höheren Vorlauf taktet, würde ihr sonst beibringen, nach unten zu gehen.

## Lernen

Was gelernt wird und wo du es siehst, steht in der [Anleitung](ANLEITUNG.md#lernen).

### Was gelernt wird

| Bereich | Was gelernt wird | Woraus |
|---|---|---|
| Therme | Heizkurven-Korrektur je Lastfall und Raum, in zwei Teilen, je ±10 Grad: **Höhe** (gilt bei jeder Außentemperatur) und **Steigung** (voll bei −10 °C, gar nicht bei +15 °C) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum und Wärmequelle (Grad je Stunde) | Heizphasen bei geschlossenem Fenster, in denen genau eine Wärmequelle den Raum erreicht, mindestens 30 Minuten lang und mindestens 0,5 Grad Anstieg, oder mindestens 2 Stunden und 0,2 Grad (Einzelheiten unter [Vorheizen](#vorheizen)). Mit der Therme zählt sie erst ab drei Phasen allein, vorher mit dem Startwert gemischt. Die Wärmepumpe zählt nur bei kleinster Leistung: ab 10 Minuten nach dem Start des Verdichters, nicht beim Ruhen, Schnell-Aufheizen, Trocknen oder höher gedreht. Wechselt die Quelle, zählt die Phase bis dahin nicht. Eine Phase endet an ihrem höchsten Punkt: nach einer Stunde ohne neuen Höchstwert (nicht, solange der Raum 0,5 Grad oder mehr unter seinem Ziel liegt) oder sobald der Raum 0,3 Grad darunter liegt. Wärme von woanders beendet sie ohne Wert. Es gilt der Median der letzten 10 |
| Räume | Auskühlkoeffizient je Raum (1/h): Temperaturabfall je Stunde = Koeffizient × (drinnen − draußen) | Phasen ohne Heizen, Fenster zu, drinnen mindestens 5 Grad wärmer als draußen, mindestens eine Stunde lang und mindestens 0,3 Grad Abfall. Es gilt der Median der letzten 10 |
| Räume | Heizleistung je Raum und Wärmequelle (Grad je Stunde ohne Verluste) | Aufheizrate plus die Verluste während der Phase. Anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe | COP-Kennfeld über der Außentemperatur | Wärme und Strom der Heizzeit, siehe [Wärmepumpe](#wärmepumpe) |

**Am Vorlauf-Minimum lernt die Kurve nicht nach oben.** An einem milden Tag liegt die Kurve unter dem Minimum, und gesendet wird das Minimum. Kommt ein Raum dann nicht hin, braucht er mehr als das Minimum; über die Kurve an einem kalten Tag sagt das nichts. Früher hob ein Schritt den Wert um die ganze Lücke bis zum Minimum, und der Wert wuchs ohne Wirkung. Jetzt bleibt das Lernen dort stehen, und der Lernstatus zeigt *Ein Raum kommt nicht hin, der Vorlauf steht aber am Minimum*. Hält das an, ist das Minimum für diesen Raum zu niedrig, und du hebst es in den Thermen-Optionen an.

**An der Grenze der Korrektur** (±10 Grad für Höhe oder Steigung) geht ein Schritt nur noch bis zur Grenze, oder er entfällt. Dann zeigt der Lernstatus *Heizkurven-Korrektur an ihrer Grenze (Höhe oder Steigung, je ±10 Grad)*, bis zwei Stunden und 10 Minuten lang kein Schritt mehr an die Grenze stößt. Welche Linie es ist, steht im Attribut `curve_by_load`. Steht ein einzelner Raum dauerhaft dort, liegt es meist nicht an der Kurve, sondern am Heizkörper oder an der Hydraulik dieses Raums.

### Höhe und Steigung

Ein Lernschritt wird auf Höhe und Steigung verteilt, je nachdem, wo die Außentemperatur zwischen den beiden Kurvenpunkten liegt. Der Vorlauf bei der aktuellen Außentemperatur bewegt sich dabei um den ganzen Schritt.

Ein Schritt an einem milden Tag verschiebt so vor allem die Höhe, einer bei Kälte vor allem die Steigung. Das ist wie beim Einstellen von Hand: nur im Winter zu kalt heißt steiler, in der Übergangszeit zu kalt heißt höher. Was im Herbst gelernt wird, verstellt deshalb den Winter kaum.

### Brenner erkennen

Thriftherm erkennt den Brenner am besten Zeichen, das die Anlage hat:

1. Gaszähler: über 1000 W gilt als brennend;
2. sonst der Statuscode der Therme;
3. sonst ein eigenes Flammensignal der Therme oder ihr Pumpenstatus.

Ein Gaszähler ist nicht nötig. Brennerläufe für Warmwasser zählen nie mit.

### Spreizung oder Fortschritt der Räume

Läuft die Therme durch, bewertet Thriftherm die Kurve nach jedem Prüffenster von 20 Minuten. Die Spreizung wird dafür geglättet.

**Wie viele Räume anfordern:** Ein einzelner offener Heizkörper schickt das Wasser fast so warm zurück, wie es kam. Die Spreizung bewertet die Kurve deshalb nur, wenn genug Räume anfordern:

- Genug heißt: mindestens der *Anteil der Räume …* (Thermen-Optionen, Standard 0,5) der Räume, die gerade Wärme abnehmen können, aufgerundet und mindestens einer.
- Wärme abnehmen kann ein Raum mit bekannter Temperatur, in dem Heizen erlaubt ist. Ein offenes Fenster oder ein ausgeschaltetes oder stummes Thermostat schließt ihn aus.
- Räume, die nur mitheizen, zählen nicht als anfordernd.
- Die Spreizung muss ein ganzes Prüffenster lang mit genug Räumen gemessen sein. Was mit zu wenigen Räumen gesammelt wurde, wird verworfen.
- Darunter zählt allein, ob die anfordernden Räume vorankommen.

**Regeln:**

| Befund | Kurve |
|---|---|
| Spreizung unter 5 Grad, und kein Raum hängt oder kommt langsam voran | runter |
| Spreizung über 15 Grad, und ein Raum hat über 30 % Heizbedarf | rauf |
| ein Raum mit über 30 % Heizbedarf steigt langsamer als 0,2 Grad je Stunde | rauf |
| alle anfordernden Räume haben höchstens 10 % Heizbedarf, und kein Raum hängt | runter |

Ein Raum, der nichts über den Vorlauf sagt (siehe [Schutz gegen falsches Lernen](#schutz-gegen-falsches-lernen)), hebt die Kurve nie an.

### Taktbetrieb

Eine taktende Therme liefert nie eine ruhige Spreizung. Dann entscheiden die Räume.

- **Taktbetrieb** heißt: mindestens 3 Brennerstarts in der letzten Stunde, und die fertigen Brennerläufe dauern im Median höchstens 5 Minuten.
- **Rauf um eine Stufe**, wenn ein angeforderter Raum hängt, sein eigener Ruf seit mindestens 30 Minuten läuft und der Heizbetrieb ebenso lange frei ist (Grund *Therme taktet und ein Raum erreicht sein Soll nicht*). Hängen heißt: mindestens 0,2 Grad unter Soll und langsamer als 0,2 Grad je Stunde. Ein Raum, den die Stillstand-Regel aufgegeben hat, zählt mit.
- **Aufheizen ist kein Halten.** Ein Raum, der vorheizt, schnell aufheizt oder auf ein angehobenes Ziel aufheizt, zählt erst als nicht hinterherkommend, wenn er langsamer steigt als für sein Aufheizen geplant. Ein Schritt in dieser Zeit gilt nie als „geholfen“: Nach dem Aufheizen oder nach drei Stunden geht die Linie ohne Sperre an ihren Anfang zurück.
- **Ein solcher Schritt ist ein Versuch.** Thriftherm merkt sich, wie schnell der Raum beim Schritt stieg. Hängt der Raum 90 Minuten später immer noch und steigt nicht um mindestens 0,1 Grad je Stunde schneller, zählt, ob der Brenner seither länger lief als in der Stunde davor. Haben seither andere Räume angefangen oder aufgehört mitzuheizen oder ihre Ventile anders geöffnet, lief der Brenner auch für sie: Dann sagt der Versuch ebenfalls nichts. Lief er nicht länger, sagt der Versuch nichts: Der Schritt bleibt, der nächste wird nicht größer und setzt den Versuch fort, höchstens bis 2 Grad über seinem Anfang. Lief er länger, oder sind die 2 Grad erreicht, wird bis zum Anfang zurückgenommen, und für diesen Raum gibt es in diesem Lastfall einen Tag lang keinen weiteren (Lernstatus: *Mehr Vorlauf hat diesem Raum nicht geholfen*). Dann bremst der Heizkörper, das Ventil oder der kurze Brennerlauf, nicht die Kurve. Steigt er schneller oder hängt er nicht mehr, bleibt der Schritt. Hängen mehrere Räume, kommt der nächste an die Reihe.
- **Hat der Schritt nach oben nicht geholfen, wird weniger probiert.** Dann ändert der Vorlauf an dieser Stelle der Kurve für den Raum nichts. Thriftherm nimmt deshalb gleich einen Schritt nach unten, wieder als Versuch, aber nie unter die gemeinsame Korrektur. Steigt der Raum danach nicht um 0,1 Grad je Stunde langsamer, bleibt er; sonst wird er zurückgenommen (Lernstatus: *Weniger Vorlauf hat diesem Raum geschadet*), und der Wert wird einen Tag lang nicht mehr gesenkt. Ein Wert, der nur hoch ist, weil mehr nichts half, kommt so einen Schritt am Tag herunter.
- **Runter über die Rufe.** Ein Raum, der sein Soll hält, pendelt um ein, zwei Zehntel; sein Trend über 90 Minuten zeigt nichts. Was der Vorlauf für ihn tut, zeigt sich darin, wie schnell ein **Halte-Ruf** ihn von 0,3 Grad unter Soll ans Soll bringt. Drei solche Rufe in Folge mit mindestens 0,6 Grad je Stunde senken den Wert des Raums um einen Schritt (Grund *Therme taktet*). Dafür muss die Therme im ganzen Ruf frei gewesen sein und getaktet haben, und kein anderer Raum darf den Vorlauf bestimmt haben, weder mit seinem Wert noch mit seinem Bedarfszuschlag. Jeder langsamere Ruf und jedes Hängen setzt die Zählung zurück, und nach einem Tag ohne zügigen Ruf beginnt sie neu.
- **Was kein Halte-Ruf ist:** ein Ruf, der mit einem neuen Soll oder mehr als 0,5 Grad unter Soll begann (das ist Aufheizen), einer mit Schnell-Aufheizen, zugedrehtem Ventil oder Hilfe der Wärmepumpe, einer, der durch Stillstand, Fenster oder Komfortende endete, der erste nach dem Lüften, und einer, in dem der Fühler nur einen einzigen Schritt gemeldet hat. Er zählt weder für noch gegen.
- **Auch dieser Schritt ist ein Versuch.** Hängt der Raum in den drei Stunden danach, wird er zurückgenommen, statt mit einem neuen Schritt nach oben beantwortet zu werden, und der Wert wird einen Tag lang nicht mehr gesenkt.
- **Rufprotokoll:** Die letzten zehn Rufe je Raum stehen in der Diagnose (`boiler_memory.calls`: Raum, Ende, Minuten, Anstieg, Tempo, gesendeter Vorlauf, Brennerstarts der letzten Stunde, Urteil). Sie bleiben lokal.

Ein niedrigerer Mindest-Vorlauf heißt bei einer Heizwerttherme weniger Wärme, nicht weniger Starts.

### Lastfälle und Werte je Raum

Welchen Vorlauf die Wohnung braucht, hängt von der Außentemperatur ab, davon, wie viele Heizkörper Wärme abnehmen, und von jedem Heizkörper: Ein kleiner Heizkörper in einem kalten Raum braucht mehr als ein großer.

Thriftherm führt deshalb drei Lastfälle: ein Raum fordert an, wenige, die Mehrheit. In jedem Lastfall hat jeder Raum eine eigene Korrektur mit Höhe und Steigung. Sechs Zahlen je Raum sagen also, welcher Vorlauf zu welcher Außentemperatur und Raumzahl passt.

**Die Mehrheit** sind mehr als der *Anteil der Räume …* der Räume, die gerade Wärme abnehmen können (Standard: mehr als die Hälfte). Bei vier Räumen sind zwei „wenige“ und drei die Mehrheit, bei drei Räumen sind zwei schon die Mehrheit.

- **Der Raum, der am meisten braucht, bestimmt den Vorlauf.** Es gibt nur einen Vorlauf für alle Heizkörper. Von den Räumen, die Wärme wollen, gibt der den Vorlauf vor, der mit seinem Wert und seinen Zuschlägen am meisten verlangt. Bei den anderen nehmen die Ventile weniger. Welcher das ist, hängt von Heizkörper, Wänden und Fenstern ab, nicht von der Raumgröße.
- **Kommt ein Raum nicht hin, hebt er nur seinen eigenen Wert an.** Er beginnt dabei bei dem Vorlauf, der tatsächlich gesendet wurde: Wer schon mehr bekam als seinen eigenen Wert und trotzdem nicht hinkommt, braucht mehr als das. Ein Raum, der nicht hinkommt, sagt nichts über die anderen.
- **Schritte nach unten und Schritte wegen der Spreizung** bewegen den Wert des Raums, der den Vorlauf bestimmt hat. Nach einem Schritt nach unten liegt kein Raum, der Wärme will, mehr über dem neuen Wert. Sonst bestimmte gleich der nächste den Vorlauf, und er käme nie herunter.
- **Dafür muss kein Raum allein heizen.** Jedes Mal, wenn ein Raum zusammen mit anderen anfordert, wird sein eigenes Vorankommen bewertet, und er sammelt seine eigenen Betriebsstunden.
- **Ein Lernschritt verändert nur den Lastfall, in dem er gemacht wurde.** Treibt ein Raum allein die gemeinsame Korrektur nach oben, starten mehrere Räume zusammen nicht von dort.
- **Ein neuer Lastfall zählt erst nach zehn Minuten.** Fordert ein dritter Raum mal an, mal nicht, würde der Vorlauf sonst im Minutentakt wechseln. Wechselt der Lastfall, ändert sich der Vorlauf um den Unterschied der beiden Kurven; die Vorlaufgrenzen gelten immer. Fordert gerade kein Raum an, bleibt der Lastfall, bis Thriftherm den Heizbetrieb sperrt.

**Start ohne eigenen Wert:**

- Ein Raum ohne eigenen Wert nimmt seinen eigenen Wert aus dem nächsten Lastfall mit **mehr** Räumen, denn es ist derselbe Heizkörper. Umgekehrt nicht: Ruft ein Raum allein, taktet die Therme, und ihr Vorlauf liegt im Mittel weit unter dem Sollwert. Was der Raum dabei gelernt hat, wäre mit mehreren Räumen zu viel.
- Gibt es den nicht, gilt die gemeinsame Korrektur. Sie gilt auch, solange seit der Freigabe noch kein Raum angefordert hat. Wer die Werte je Raum früher abgeschaltet hatte (die Option gibt es seit 13.11.2026 nicht mehr), hat dort seine gelernte Korrektur: Jeder Raum beginnt bei ihr und lernt von da an seinen eigenen Wert. Werte je Raum aus der Zeit davor werden beim ersten Start einmal verworfen, damit der Vorlauf bleibt, wie er war.
- Der Wert bleibt geliehen, bis der Raum seinen ersten eigenen Schritt macht: Ändert sich die Herkunft oder wird sie zurückgesetzt, folgt er. Seine Betriebsstunden sammelt jeder Raum selbst.

### Schutz gegen falsches Lernen

**Wann gelernt wird:**

- nur im Modus *Aktiv*;
- frühestens 10 Minuten nach Brennerstart und aus mindestens 10 Messwerten;
- nie während Warmwasser, einer Sollwertänderung in den letzten 20 Minuten, Schnell-Aufheizen, Trocknung, einem Aufheizen mit Zuschlag ([Aufheiztempo](#aufheiztempo)) oder einem Vorheizen mit *Längstes Vorheizen*, bei einer Störung der Therme, ohne Außentemperatur oder wenn der Sicherheitsstatus nicht OK ist.

Nach jeder solchen Pause wartet das Lernen ein volles Prüffenster von 20 Minuten und zählt nur Brennerläufe aus dieser Zeit. Was davor war, gehörte zu einem anderen Sollwert, einer Trocknung oder dem Drehknopf der Therme.

**Wartezeit und Tagesgrenzen** gelten für alle Lastfälle und Räume gemeinsam:

- vor einem Schritt nach oben mindestens 90 Minuten seit dem letzten Schritt, vor einem nach unten eine Stunde. So weit schaut der Trend der Räume zurück: Früher zeigt er noch nicht, was der letzte Schritt gebracht hat;
- je Tag höchstens 10 Grad nach oben und 4 Grad nach unten.

**Welche Räume mit ihrem Vorankommen nichts über den Vorlauf sagen**, weder für mehr noch für weniger (die Regeln zur Spreizung gelten unabhängig davon):

- **Ein Raum, dessen Soll sich in den letzten 90 Minuten geändert hat.** Sein Trend enthält noch die Zeit davor, und er heizt auf, statt zu halten. Die Heizkurve ist fürs Halten da; fürs Aufheizen gibt es den Bedarfszuschlag und das [Aufheiztempo](#aufheiztempo). Die Zeit übersteht einen Neustart und das Speichern der Optionen; nach einem offenen Fenster beginnt sie neu. Ein Komfortblock, der samt Vorheizen kürzer als 90 Minuten ist, hebt die Kurve deshalb nie an; senken können seine Halte-Rufe sie trotzdem.
- **Ein Raum, dessen Fühler seit 90 Minuten nichts gemeldet hat.** Ein Fühler, der nur bei einer Änderung um etwa ein Grad meldet (etwa Shelly H&T), zeigt stundenlang eine flache Linie und springt dann. Die flache Linie ist Schweigen, kein Stillstand ([#7](https://github.com/alpha-uniform/thriftherm/issues/7)). Als Meldung zählt auch alles andere vom selben Gerät, etwa die Feuchte, und mit ZHA dessen *Zuletzt gesehen*. Zigbee2MQTT schreibt einen gleichen Wert nicht neu: Ein Raum mit gleichbleibender Temperatur galt sonst als stumm.
- **Ein Raum mit Schnell-Aufheizen:** Ein Wunsch auf Zeit sagt nichts über die Heizkurve, die die geplanten Temperaturen halten soll.
- **Ein Raum, der nach Plan aufheizt** (Vorheizen oder ein angehobenes Ziel, das er noch nicht erreicht hat), solange er mindestens so schnell steigt wie geplant. Hinter dem Plan zählt er. Sonst zählte er als zurückliegend, und ein Schritt hob seine Linie ohne Grund.
- **Ein Raum mit Handänderung, solange er noch dorthin aufheizt.** Ist er bis auf 1 Grad heran und hält dort, zählt er wie jeder Raum. Sonst lernte ein Haushalt, der nur von Hand heizt, nie etwas.
- **Ein Raum, dessen Thermostat meldet, dass es gerade nicht heizt** (das sagen Better Thermostat und Versatile Thermostat). Sein Ventil ist zu, auch wenn der Raum unter Soll liegt, und ein heißerer Vorlauf schlösse es nur früher.
- **Ein direkt gesteuertes Ventil, das gerade nicht offen sein kann:** während des Ventilschutzes oder bei höchstens 10 % Batterie. Ein ausgeschaltetes oder stummes Ventil nimmt dem Raum ohnehin das Heizen.

### Schrittgrößen

Die Schritte wachsen mit der Gewissheit: erst grob, dann fein.

- **Ein Schritt nach oben nach einem nach oben** verdoppelt sich: 1, 2 Grad, mehr nicht.
- **Ein Schritt gegen den vorigen** halbiert sich, bis auf 0,5 Grad.
- **Ein Schritt nach unten nach einem nach unten** ist wieder der erste Schritt. Schritte nach unten wachsen nie: Komfort geht vor Sparen.
- In einem Lastfall zählt der letzte Schritt dieses Lastfalls. Ein Schritt, den das Bad allein machte, sagt nichts über den nächsten für die ganze Wohnung.
- Kein Schritt geht weiter, als der Vorlauf wegen seiner Grenzen folgen kann, und keiner weiter, als die Tagesgrenze noch zulässt. Bliebe weniger als 0,5 Grad, entfällt der Schritt.
- Nach drei Stunden ohne Schritt beginnt eine Folge wieder beim ersten Schritt: 1 Grad im **Einlernen**, 0,5 Grad im **Verfeinern**. *Verfeinern* gilt nach sechs Änderungen oder nach einem Tag ohne Änderung.

Mit festen Schritten von 1 Grad, vier am Tag, wäre die Korrektur nach acht Stunden erst bei 4 Grad mehr; ein Raum, dessen Vorlauf-Minimum weit zu niedrig ist, bliebe lange unter Soll.

### Wie lange das Lernen dauert

Die Übersicht steht in der [Anleitung](ANLEITUNG.md#wie-lange-das-lernen-dauert). Hier die Rechnung dahinter. Sie folgt aus den Regeln oben, nicht aus einer ganzen gemessenen Saison.

**Heizkurve, nach oben.** Ein Raum, der hängt, bekommt frühestens 30 Minuten nach Beginn seines Rufs einen Schritt, und sein Soll muss seit 90 Minuten stehen. Die Schritte wachsen 1, 2, 2, 2 Grad mit je 90 Minuten Abstand: Eine um 7 Grad zu niedrige Kurve ist so in rund 6 Stunden eingeholt, mehr als 10 Grad am Tag gibt es nicht.

**Heizkurve, nach unten.** Höchstens 4 Grad am Tag, mit einer Stunde Abstand. Läuft die Therme durch, entscheidet die Spreizung nach je 20 Minuten. Taktet sie, braucht jeder Schritt drei zügige Halte-Rufe in Folge; ein Ruf dauert 1 bis 1,5 Stunden, rund fünf kommen in zehn Stunden. Das sind ein bis zwei Schritte am Tag, eine um 4 bis 6 Grad zu hohe Kurve braucht also 2 bis 6 Heiztage. Ob Halte-Rufe die nötigen 0,6 Grad je Stunde überhaupt erreichen, ist die größte Unsicherheit: Wenn nicht, sinkt die Kurve einer taktenden Therme kaum.

**Seltene Lastfälle** sammeln ihre Betriebsstunden langsam.

**Steigung.** Ein Schritt geht an milden Tagen fast ganz in die Höhe ([Höhe und Steigung](#höhe-und-steigung)). Die Steigung lernt erst an Tagen deutlich unter 0 °C, in einem milden Winter erst in der nächsten Saison.

**Aufheizrate und Auskühlkoeffizient.** Es zählt der Median der letzten zehn Werte. Der Auskühlkoeffizient gilt ab dem ersten Wert; die Aufheizrate mit der Therme erst ab drei Phasen, bis dahin füllt die Start-Aufheizrate die fehlenden Plätze ([Vorheizen](#vorheizen)). Mit einer Absenkung gibt es ein bis zwei Aufheizphasen und eine Auskühlphase am Tag: ein erster Auskühlwert nach einem Tag, eine eigene Aufheizrate mit drei Phasen nach zwei bis vier Tagen, die volle Reihe nach ein bis zwei Wochen. Ein Raum mit festem Soll pendelt nur 0,2 bis 0,4 Grad knapp unter dem Ziel; das reicht für keine Phase, er behält die Start-Aufheizrate. Ausgekühlt wird nur gelernt, wenn es drinnen mindestens 5 Grad wärmer ist als draußen, also meist nicht über etwa 16 °C draußen.

**COP-Kennfeld.** Eine Stufe von 2 Grad Außentemperatur ist nach 3 Stunden Laufzeit bei kleinster Leistung bekannt. Liegt das Wetter 4 bis 8 Stunden am Tag in einer Stufe und läuft die Wärmepumpe die Hälfte davon, sind das 1 bis 3 Tage. Eine Stufe, in der die Heizung günstiger ist, lernt nur über Lernläufe von einer Stunde, höchstens einer am Tag: mindestens 3 Tage mit passendem Wetter, realistisch 1 bis 3 Wochen. Die häufigen Stufen (etwa 0 bis 12 °C) sind so nach 2 bis 4 Wochen bekannt, Froststufen erst bei Frost. Bis dahin gilt die Nachbarstufe oder die angepasste Startkurve; angepasst wird sie schon ab der ersten bekannten Stufe.

## Wärmepumpe

Die Bedienung steht in der [Anleitung](ANLEITUNG.md#wärmepumpe-optional).

### COP messen

Der COP ist die Wärme, die die Wärmepumpe in die Luft bringt, geteilt durch den Strom, den sie braucht.

- **Wärme:** aus der Luftmenge und dem Unterschied im Wärmeinhalt zwischen Ansaug- und Ausblasluft. Dafür zählen Temperatur und Feuchte der Ansaugluft und die Temperatur der Ausblasluft.
- **Luftmenge:** aus der Lüfterdrehzahl über die Kennlinie (Drehzahl → m³/h) aus der Einrichtung, mal Schlauchfaktor. Knapp außerhalb der Kennlinie (bis 20 % darüber oder darunter) rechnet Thriftherm mit der Drehzahl weiter. Steht der Verdichter zwischen zwei Starts, auch weiter darunter: Sonst fehlte die Wärme der Pause, und der ganze Takt fiele weg.
- **Schlauchfaktor:** Er korrigiert die Luftmenge, nicht die Wärme. Gegen den Widerstand der Schläuche fördert der Lüfter bei gleicher Drehzahl weniger Luft. Was die Schläuche unterwegs an Wärme abgeben, bleibt in der Wohnung und ist kein Verlust.

Ein Messwert zählt erst, wenn das Gerät eingeschwungen ist:

- der Verdichter läuft seit mindestens 10 Minuten, auch bei kürzer eingestellter Warmlaufzeit: Nach dem Start dreht er kurz hoch;
- kein Schnellaufheizen und kein Trocknen: Der COP gilt für die kleinste Leistung, mit der die Wärmepumpe läuft;
- die Leistung liegt bei mindestens 150 W (Standard);
- die Ausblasluft ist mindestens 3 Grad wärmer als die Ansaugluft (Standard) und ändert sich um höchstens 0,5 Grad je Minute (Standard);
- beide Temperaturfühler sind verfügbar, die letzte Meldung am Ausblas ist höchstens 60 Minuten alt und an der Ansaugung höchstens 3 Stunden (Standard, *COP-Messung* in der Feineinstellung). Solche Fühler melden nur, wenn sich der Wert ändert: Ein ruhiger Fühler stimmt also noch; die Grenze zählt erst, wenn auch die anderen Sensoren seines Geräts schweigen, am Ausblas längstens 6 Stunden, an der Ansaugung einen Tag. Ein Ausblaswert von vor dem Verdichterstart zählt nicht; Einbrüche und Abtauen fallen über den Betriebszustand und die Grenzen dieser Liste heraus, nicht über das Alter;
- der Wert liegt nicht über 60 % des physikalisch Möglichen (Carnot, aus den Temperaturen der beiden Wärmetauscher).

Angezeigt wird der COP aus mindestens 12 gültigen Messwerten (Standard), von Ausreißern bereinigt und geglättet. Sonst steht dort „unbekannt“ statt einer erfundenen Zahl.

**COP über den Takt:** Taktet die Wärmepumpe, weil ihr Sollwert erreicht ist, gibt es keinen eingeschwungenen Wert. Dann zählt der ganze Takt: Wärme und Strom werden von einem Verdichterstart bis zum nächsten aufsummiert, die Pause mit ihrem Standby-Strom eingeschlossen. Dieser COP (Attribut *COP über den letzten Takt*) ist niedriger als der im Dauerlauf. Der Sensor zeigt ihn, er entscheidet aber nichts: Ein kurzer Lauf mit 2,3 würde sonst die Heizung wählen, das stoppt die Wärmepumpe, und das macht den nächsten kurzen Lauf. Was kurze Läufe wirklich kosten, steckt im COP-Kennfeld. Steht der Verdichter und zeigt ein Ausblasfühler jenseits seiner Grenze keine Wärme, zählt das als keine Wärme: In einer langen Pause gibt es für ihn nichts zu melden.

**COP-Kennfeld:** Über Wärmepumpe oder Heizung entscheidet der eingeschwungene COP, solange es einen gibt, sonst das Kennfeld. Thriftherm summiert darin Wärme und Strom in Stufen von 2 Grad Außentemperatur: über die ganze Heizzeit, mit Start, Abtauen und Nachlauf des Lüfters. Betrieb über der kleinsten Drehzahl (schnelles Aufheizen, Trocknen, Anhebung) zählt samt seinem Nachlauf nicht mit, eine Ruhepause mit stehendem Verdichter und ohne Wärme auch nicht. Auch nicht, wenn der Ausblasfühler hängt: Bei eingeschwungenem Verdichter und drehendem Lüfter zeigt er weniger als die *Minimale Lufterwärmung* oder mehr Wärme als ein COP von 7, oder er hat seit dem Verdichterstart nichts gemeldet. Darum zählt ein Start erst, sobald der Fühler reagiert hat (gemessen: binnen einer Minute), dann aber ganz; bleibt er 10 Minuten oder bis zum Stopp stumm, fällt der Lauf samt Nachlauf heraus. Beim Abtauen gilt das nicht. Der COP einer Stufe ist Wärme mal Schlauchfaktor durch Strom. Ein neuer Schlauchfaktor rechnet das Kennfeld also nur um; eine andere Lüfterstufe im Normalbetrieb oder eine andere Luftmengen-Kennlinie lässt es neu beginnen, weil die alte Wärme dafür nicht nachzurechnen ist. Altes verblasst mit der Laufzeit in der Stufe (Halbwertszeit 100 Stunden), eine Stufe, die einen Sommer lang nicht läuft, behält ihre Werte.

Ab 3 Stunden Laufzeit gilt eine Stufe als bekannt. Bis dahin gilt, was daneben bekannt ist, dann der Wert aus der Tabelle vor dieser Umstellung (Grundlage *Alte Tabelle*: Sie ließ sich nicht umrechnen, ihr Wert zählt nur wie eine Stunde Laufzeit und entfällt, sobald die Stufe bekannt ist). Für die übrigen nimmt Thriftherm eine Kurve nach typischen Datenblättern und passt sie mit einem Faktor aus den bekannten Stufen an, nach Laufzeit gewichtet; der Faktor beginnt bei 80 %. Ist im Wärmepumpen-Formular unter *COP-Werte dieses Modells* ein Modell gewählt, gelten für Stufen ohne eigene Messung dessen mitgelieferte Werte und außerhalb davon die Kurve, angepasst an diese Werte (Grundlage *Gemessen am gleichen Modell*). Was das eigene Gerät misst, geht immer vor. Mitgeliefert ist bisher die Midea PortaSplit: an einem Gerät gemessen, bei 2 bis 16 °C draußen.

**Lernläufe:** Ist eine Außentemperatur-Stufe noch nicht bekannt oder ist sie bekannt, lief aber eine Woche lang nicht, und liegt der geschätzte COP bei mindestens 85 % des Break-even-COP, darf die Wärmepumpe dort eine Stunde laufen, um zu messen, auch wenn die Heizung gerade günstiger wäre. Das geschieht nur, wenn ein Raum Wärme braucht oder getrocknet wird, und je Stufe höchstens einmal am Tag. Abschalten mit *Lernläufe erlauben* in den Wärmepumpen-Optionen.

### Sperren

- **Zu teuer:** Liegt der eingeschwungene COP 30 Minuten lang unter dem *Break-even-COP*, heizt die Wärmepumpe teurer als die Heizung und wird gesperrt (Parameter *Sperre nach Vereisung / Ineffizienz*, Standard 120 Minuten). Der COP eines kurzen Laufs zählt dafür nicht. Eine Pause von bis zu einer Stunde dazwischen unterbricht die Zeit nicht, solange der nächste Lauf wieder einen solchen Wert gibt; so endet auch ein Wechsel aus Laufen und Ruhen, wenn das Kennfeld hoch steht. Im Modus *Nur Wärmepumpe* gibt es diese Sperre nicht, und eine laufende endet sofort, wenn du auf *Nur Wärmepumpe* stellst.
- **Vereisung oder häufiges Abtauen:** Diese Sperre schützt das Gerät und läuft immer bis zu ihrem Ende.

### Frostschutz

Ohne zentrale Heizung (eingerichtet mit *Keine – nur Wärmepumpe und Raumsteuerung*) ist die Wärmepumpe die einzige Wärme, die Thriftherm steuert. Sie hält dann im Modus *Abwesend* die Abwesenheitstemperatur und im Modus *Aus (nur Frostschutz)* die Frostschutztemperatur, und einen versorgten Raum unter der Frostschutztemperatur heizt sie in jedem Modus, bei offenem Fenster nach 30 Minuten, bei unbekannter oder zu tiefer Außentemperatur und auch bei teurer Wärme, bis er 1 Grad darüber liegt.

Mit Therme oder Fernwärme ist sie die Reserve: Einen versorgten Raum, der 30 Minuten ohne Unterbrechung unter der Frostgrenze liegt, heizt sie ebenso (*Frostschutz: Zentralheizung reicht nicht*). Eine Heizung, die für den Sommer aus ist, auf ihrem Drehknopf steht oder ausfällt, ließe den Raum sonst weiter auskühlen. Ein Neustart lässt die 30 Minuten nicht von vorn beginnen. Vereisung, Fehlercodes und der Sicherheitsstatus *Fallback* halten sie weiter an, und in *Nur beobachten* wird nichts gesendet.

### Vereisung und Abtauen

Die Midea meldet kein Abtauen. Thriftherm schließt es aus Mustern: aus den Temperaturen an den Wärmetauschern innen und außen (Innen- und Außen-Coil), der Luft und der Leistung.

| Zeichen | Bedeutung |
|---|---|
| Außen-Coil steigt innerhalb von 2 Minuten um mehr als 5 Grad (Standard), der Verdichter läuft, und der Innen-Coil fällt um mehr als 1 Grad | Kreislauf umgekehrt |
| Innen-Coil zweimal in Folge mehr als 5 Grad kälter als die Ansaugluft (ohne Ansaugwert: unter 5 °C), Verdichter läuft | Kreislauf läuft rückwärts |
| Ausblasluft über 3 Minuten weniger als 1 Grad wärmer als die Ansaugluft, im eingeschwungenen Heizen | kalte Ausblasluft |
| Leistung fällt innerhalb von 2 Minuten um mehr als 40 % (von über 300 W), Verdichter läuft | Leistungsabfall |
| Außen-Coil über 10 Minuten mehr als 8 Grad (Standard) unter der Außenluft, Verdichter läuft | vereister Außen-Coil |
| unter 5 °C draußen und über 80 % Luftfeuchte | Vereisungswetter, nur angezeigt |

- **Ein Abtauzyklus** ist: Kreislauf umgekehrt, Kreislauf läuft rückwärts, oder kalte Ausblasluft zusammen mit Leistungsabfall. Er wird einmal gezählt, wie lange er auch dauert. Seine COP-Werte werden verworfen.
- **Gesperrt** wird bei mehr als 3 Abtauzyklen in 90 Minuten (Standard) oder bei vereistem Außen-Coil zusammen mit kalter Ausblasluft. Ein Außen-Coil 8 bis 12 Grad unter der Luft ist bei Volllast im Frost allein noch normal, ein Leistungsabfall dabei auch: So drosselt der Verdichter.
- Beim Abtauen und im kurzen Halt danach zeigt der Laufzustand *Abtauen*. Der Lauf geht danach ohne neues Aufwärmen weiter, auch nach einem Neustart von Home Assistant.
- Kreislauf und Leistung werden nur im Heizbetrieb gelesen. Ein Verdichter, der zum Kühlen oder Entfeuchten anläuft, zeigt dieselben Temperaturen.

### Bad-Trocknung

- **Auslöser:** Die absolute Feuchte liegt mehr als 2,5 g/m³ über ihrem Grundwert (Standard, *Auslöser: Anstieg der absoluten Feuchte*). Der Grundwert ist der Median der letzten drei Stunden ohne die letzten 20 Minuten. Liegt die relative Feuchte schon über 70 %, reichen 60 % dieses Anstiegs.
- **Start nach dem Duschen:** Der Anstieg merkt die Dusche nur vor. Die Trocknung beginnt, sobald die Feuchte drei Minuten lang keinen neuen Höchstwert erreicht (mehr als 0,2 g/m³ darüber), spätestens 20 Minuten nach dem Anstieg.
- **Während der Trocknung** bläst die Wärmepumpe mit ihrer höchsten Lüfterstufe und der Trocknungs-Solltemperatur (Standard 22 °C), nie über Soll plus 1,5 Grad. Ihr Sollwert geht dabei sofort auf 3 Grad über ihre eigene Messung, auch wenn der Verdichter gerade steht; läuft er, werden vorher 1,5 Grad abgezogen, weil ihre Messung dann so viel mehr zeigt. Dort bleibt er; neu gesetzt wird er nur, wenn sie sich selbst abschaltet. So läuft sie etwas stärker als bei kleinster Leistung, und sie ruht nicht. Am Ende der Trocknung oder an der Grenze von Soll plus 1,5 Grad geht der Sollwert sofort zurück auf ein Grad unter ihre Messung; wieder stärker heizt sie erst ein halbes Grad darunter, aus dem Ruhen nach der Mindestpause. Ohne das stünde der Verdichter bei *Jetzt trocknen* oft. Kann sie gerade nicht heizen, wartet die Trocknung mit ihrer restlichen Zeit.
- **Ende:** wenn der Taupunkt wieder mindestens 4 Grad unter der Raumtemperatur liegt und die absolute Feuchte höchstens 1 g/m³ über dem Grundwert; spätestens nach der längsten Dauer (Standard 60 Minuten). Mit Fensterkontakt bläst die Wärmepumpe nur bei offenem Fenster, bei geschlossenem wartet die Trocknung, und ihre Zeit läuft weiter. Ohne Fensterkontakt bläst sie auch bei geschlossenem Fenster.
- **Pause danach:** Endete eine Trocknung wegen ihrer längsten Dauer, startet drei Stunden lang keine neue von selbst. Der Grundwert enthält in dieser Zeit noch die Luft von vor dem Duschen.
- **Der Knopf *Jetzt trocknen*** startet sofort und läuft die volle Dauer, egal wie Feuchte und Fenster stehen.

### Am Minimum halten

Die Wärmepumpe regelt auf die Luft an ihrem Platz, nicht auf die versorgten Räume. Thriftherm nutzt ihren Sollwert deshalb nur für eines: Er hält den Verdichter bei kleinster Leistung, denn dort ist ihr COP am besten. Bei einer PortaSplit gilt:

- Ein Grad unter ihrer eigenen Messung läuft sie mit 24 Hz und rund 340 W, bei einem COP von 3,4 bis 3,6. Bei 8 °C draußen wärmt das die Räume um rund ein halbes Grad je Stunde, bei 3 °C hält es sie gerade.
- Ein Grad darüber regelt sie sich selbst auf 600 bis 700 W hoch, und die Räume werden um ein Grad je Stunde wärmer. So lief es bis 0.6.8: Die Räume wurden zu warm.

So läuft es:

- **Start:** Ist der kälteste versorgte Raum an seinem Soll, geht der Sollwert ein halbes Grad über die eigene Messung des Geräts. So läuft es sanft an; weiter darüber startet die PortaSplit gleich mit 60 bis 79 Hz. War das Gerät lange aus, steht seine eigene Messung still, dann zählt der Ansaugfühler, solange seine letzte Meldung innerhalb seiner Grenze liegt (Standard 3 Stunden).
- **Halten:** Läuft der Verdichter, steigt die eigene Messung um rund 1,5 Grad, und der Sollwert liegt dann etwa ein Grad darunter. Dort bleibt er: Ein neues Soll der Räume schickt nichts, das Gerät piept selten. Neu gesetzt wird er nur, wenn das Gerät selbst abschaltet, obwohl ein Raum unter Soll ist, wenn seine Messung 10 Minuten lang mehr als ein halbes Grad unter dem Sollwert liegt, oder einmal 15 Minuten nach einer höheren Leistung, dann nur nach unten. Steigt die Messung 5 Minuten lang auf 1,5 Grad über den Sollwert, ob ein Raum unter Soll ist oder nicht (bei 2 Grad ohne die 5 Minuten abzuwarten, frühestens 10 Minuten nach dem letzten Sollwert), geht er auf ein Grad unter die Messung, nicht während die Wärmepumpe höher dreht: Rund 1,75 Grad über dem Sollwert schaltet das Gerät sich sonst selbst ab. Beim Abtauen und 15 Minuten danach nie.
- **Steht sie weiter:** Liegt ein Raum 0,2 Grad unter Soll und läuft ihr Verdichter 5 Minuten nach dem letzten Sollwert noch immer nicht, geht der Sollwert auf 1,5 Grad über ihre eigene Messung, frühestens nach der Mindestpause; hilft schon die Heizung, erst nach einer halben Stunde, bei *Langsamer warm werden* erst 0,5 Grad unter Soll. 15 Minuten nach dem Start wird er wieder ein Grad unter ihre Messung gesetzt. Das kann ihren Start für ein bis drei Minuten härter machen (28 bis 67 Hz). Nach einer längeren Ruhe startet ein halbes Grad darüber sie oft nicht: Sie bleibt dann oft länger stehen.
- **Ruhen:** Ist der kälteste versorgte Raum ein halbes Grad über Soll, geht der Sollwert auf 17 °C (*Räume warm: Verdichter ruht*), frühestens nach der Mindestlaufzeit. Das Gerät bleibt im Heizbetrieb, nur der Lüfter läuft: Aus dem Heizbetrieb läuft es sanft wieder an, aus Aus oder Lüften startet es mit 60 bis 79 Hz. Am Soll heizt es wieder, frühestens nach der Mindestpause. Misst das Gerät bei laufendem Verdichter weniger als 19 °C (stehend 1,5 Grad weniger), oder läuft der Verdichter 15 Minuten nach dem Ruhe-Sollwert noch, wird es ausgeschaltet.
- **Kommt ein Raum nicht hinterher** (10 Minuten lang 0,2 Grad unter Soll und langsamer als 0,3 Grad je Stunde, oder sein Vorheizen wird nicht rechtzeitig fertig; ein vorheizender Raum wird erst eine halbe Stunde nach dessen Beginn geprüft, mit dem Tempo seither), entscheidet die Option *Wenn die Wärmepumpe allein nicht reicht*. Dabei zählen nur Räume, deren Heizkörper heizen darf, also nicht einer mit von Hand ausgeschaltetem Thermostat; der bekommt keine Hilfe und ist später am Soll. Ein Raum knapp unter Soll, der nicht hinterherhängt, löst nichts aus.
  - *Therme oder Fernwärme hilft* (Standard): Die Heizung hilft, bis alle Räume am Soll sind (Attribut `boiler_helps`). Die Wärmepumpen-Räume rufen sie dann ab 0,2 Grad unter Soll. Die Wärmepumpe bleibt am Minimum, denn darüber kostet ihre Wärme mehr als die der Therme.
  - *Wärmepumpe dreht höher*, ebenso im Modus *Nur Wärmepumpe* (außer mit *Langsamer warm werden* oder *Wärmepumpe allein*): Ihr Sollwert geht alle 10 Minuten um ein halbes Grad hoch, höchstens 1,5 Grad über ihre eigene Messung, und bleibt dort, bis alle Räume am Soll sind; ihr COP sinkt dabei. Dann geht der Sollwert gleich zurück auf ein Grad unter ihre Messung.
  - *Langsamer warm werden*: Sie bleibt am Minimum, dreht nie höher, und die Räume brauchen länger. Die Heizung hilft erst, wenn ein Raum ein halbes Grad unter Soll liegt (auch in der Absenkung) oder ein Vorheizen voraussichtlich ein halbes Grad zu kurz kommt, und dann, bis alle Räume am Soll sind. In der Simulation über zehn aufgezeichnete Tage lagen die Räume so im Mittel 0,13 bis 0,18 Grad unter Soll statt 0,3 bis 0,6 ohne diese Hilfe (bei Frost bis 1,7), für rund 7 Cent am Tag weniger als mit *Therme oder Fernwärme hilft*, mit 2 bis 4 Thermenläufen von rund 40 Minuten am Tag statt 5 bis 9 kurzen. Im Modus *Nur Wärmepumpe* hilft dann nichts.
  - *Wärmepumpe allein*: Sie bleibt ebenso am Minimum, und solange sie die günstigere Quelle ist, rufen ihre Räume die Therme nicht: Sie heizen nicht mit, bestimmen den Vorlauf nicht und lehren die Heizkurve nichts. Die Therme heizt die anderen Räume weiter; läuft sie für einen davon, bekommt ein Raum unter Soll über sein offenes Ventil etwas ab. Ist die Wärmepumpe teurer oder gesperrt, heizt die Therme alle Räume. Bei Fernwärme heißt das nur kleinste Leistung, denn dort gibt es nichts zu sperren.
  - Ohne zentrale Heizung (eingerichtet mit *Keine – nur Wärmepumpe und Raumsteuerung*) dreht sie immer höher, was auch eingestellt ist.
- **Loslassen:** Geht ihre Steuerung auf *Nur beobachten* oder *Aus*, oder wird Thriftherm deaktiviert oder gelöscht, wird ein Gerät, das noch auf 30 °C oder dem Sollwert einer Trocknung steht, auf seinen kleinsten Sollwert gestellt und ruht dort; stoppt Home Assistant, bekommt es stattdessen das Ziel des Raums, denn danach regelt es allein weiter; eines auf kleinster Leistung oder eines, das inzwischen jemand gestellt hat, bleibt, wie es ist. Ein Neuladen, nach dem es aktiv weitergeht, ändert nichts.
- **Mehr Leistung** gibt es nur mit *Schnell aufheizen* (30 °C, Lüfter hoch), etwas mehr beim Trocknen (siehe oben). Danach fällt ihre Messung mit der Leistung: 15 Minuten später wird der Sollwert noch einmal aus ihr gesetzt, nur nach unten.
- **Lüfter:** Sonst läuft er auf der *Lüfterstufe im Normalbetrieb* (Standard *Auto*; *Voll* ist die stärkste Stufe des Geräts, eine Stufe, die es nicht hat, gilt als *Auto*). *Voll* bringt bei gleichem Strom rund 7 % mehr Wärme als *Auto*, und *Auto* dreht bei wenig Last langsamer als *Silent*; mehr Luft trägt auch mehr Wärme durch die Schläuche in die Räume. Lauter ist es dafür. Trocknen und Schnell aufheizen geben die Stufe danach zurück. Hat das Gerät noch die zuletzt gegebene Stufe, bekommt es eine neu gewählte einmal; eine Stufe von Hand bleibt.
- **Heizung günstiger:** Will noch jemand einen versorgten Raum warm haben, ruht sie wie oben bei 17 °C im Heizbetrieb (*Heizung günstiger*), denn er wird bald wieder gebraucht. Ist die Wärmepumpe wieder günstiger, läuft sie sanft an. Bleibt die Heizung eine Stunde lang günstiger, wird sie ohne Lüfternachlauf ausgeschaltet. Beim Abtauen und 15 Minuten danach behält sie ihren Sollwert, ob sie ruht oder aus geht, entscheidet sich danach; das gilt auch, wenn die Räume warm sind. Ein Halt wegen *Heizung günstiger* endet oft in einem harten Start mit rund 1 kW. Ruhen kostet 9 W mehr als Aus, ein harter Start so viel wie 7 bis 12 Stunden Ruhen.
- **Ausgeschaltet** wird sie, wenn niemand einen versorgten Raum warm haben will (abwesend, Modus aus, Frostschutz); ohne zentrale Heizung hält sie stattdessen Abwesenheits- und Frostschutztemperatur ([Frostschutz](#frostschutz)). Nach langem Ruhen geschieht das ohne Lüfternachlauf, der Lüfter lief ja die ganze Zeit.
- Sie läuft mindestens 20 Minuten und pausiert mindestens 10 Minuten (Standard, *Mindestlaufzeit*, *Mindestpause*). Eine Sperre beendet einen Lauf sofort.
- **Geräte ohne Verdichterfrequenz** (eine Klima-Entität ohne das Attribut `compressor_frequency`) gelten im Heizbetrieb als laufend. Ob sie selbst abgeschaltet haben, sieht Thriftherm dann nicht; ihr eigenes Thermostat startet sie wieder.

Die Attribute `latched_setpoint_c` (der gehaltene Sollwert) und `resting` (Verdichter ruht) zeigen, wo das Gerät gerade steht.
