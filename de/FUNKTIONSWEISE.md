# Wie Thriftherm rechnet

[English](../en/HOW_IT_WORKS.md) | **Deutsch**

Für die Bedienung reicht die [Anleitung](ANLEITUNG.md). Hier steht, wie Thriftherm intern entscheidet.

Die Zahlen stammen aus dem Code. „Standard“ heißt: in den Optionen einstellbar. Alle anderen Werte sind fest.

**Inhalt**

- [Abwesend und geplante Rückkehr](#abwesend-und-geplante-rückkehr)
- [Änderungen von Hand](#änderungen-von-hand)
- [Vorheizen](#vorheizen)
- [Thermensteuerung](#thermensteuerung)
- [Aufheiztempo](#aufheiztempo)
- [Lernen](#lernen)
- [Wärmepumpe](#wärmepumpe)

## Abwesend und geplante Rückkehr

Die Bedienung steht in der [Anleitung, Abschnitt 2](ANLEITUNG.md#abwesend-mit-und-ohne-rückkehr). Hier die Regeln, nach denen *Geplante Rückkehr* deine Eingaben liest.

- **Erneut *Abwesend*:** Ist *Abwesend* mit Rückkehr schon an, ändert ein erneutes *Abwesend* nichts. Für Abwesend ohne Ende erst einen anderen Modus wählen und dann wieder *Abwesend*, oder `thriftherm.set_away` mit `clear_return_time: true` aufrufen.
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

### Feuchteschutz

Kommt die Luft im Raum während *Abwesend* ihrem Taupunkt näher als 3 Grad (Standard, *Abwesend: Taupunktabstand für Feuchteschutz*), hebt Thriftherm den Sollwert an. Er steht dann 1 Grad über der Raumtemperatur, höchstens auf der Komforttemperatur. Grund: *Abwesend, gegen Feuchte angehoben*. Die Obergrenze verhindert, dass ein Feuchtesensor, der bei fast 100 % hängt, den Sollwert Grad für Grad mitzieht.

## Änderungen von Hand

Was eine Änderung von Hand bewirkt und wo du sie siehst, steht in der [Anleitung, Abschnitt 3](ANLEITUNG.md#änderungen-von-hand). Hier steht, woran Thriftherm sie erkennt.

Thriftherm schreibt Sollwerte und muss deshalb wissen, wer einen Sollwert geändert hat. Das sagt Home Assistant selbst: Jede Änderung trägt mit, woher sie kommt.

| Woher | Beispiel | Gilt als Änderung von Hand? |
|---|---|---|
| Thriftherm selbst | der eigene Befehl und die Antwort des Geräts darauf | nie |
| Jemand in Home Assistant | Dashboard, App, Sprachassistent | immer, sofort |
| Eine Automation oder ein Skript | „vor dem Duschen 23 °C“, auch nach der Uhr | immer, sofort |
| Am Gerät | Drehknopf am Ventil, Fernbedienung der Klimaanlage, App des Herstellers; auch was sich änderte, während das Gerät nicht erreichbar war | ja, außer das Gerät erklärt die Änderung selbst (eigene Regel von Versatile Thermostat, verspätetes Funk-Echo bei Better Thermostat). An der Wärmepumpe erst, wenn sie zwei Minuten Zeit hatte, einem Befehl zu folgen |

Ein fremder Sollwert an einem Thermostat oder an der Wärmepumpe startet sofort einen Zyklus, ohne auf den nächsten zu warten. Eine eigene Automation, die die Anzeige beschleunigt, ist deshalb nicht nötig.

**Echo-Filter:** Better Thermostat schreibt den Sollwert des Heizkörperthermostats alle paar Sekunden neu. Kommt über Zigbee eine Antwort verspätet an, hält Better Thermostat den alten Wert für einen Dreh am Thermostat. Der Echo-Filter (Optionen → Regelparameter, standardmäßig an) erkennt das: Springt Better Thermostat auf einen Wert, den das Thermostat in der letzten Minute schon hatte und wieder verlassen hatte (z. B. 16 → 16,5 → 16), wird das ignoriert und der geplante Sollwert neu gesendet. Ein echter Dreh bringt einen neuen Wert und wird übernommen. Wann zuletzt ein Echo ignoriert wurde, zeigt der Sensor *Thermostat-Plan* des Raums.

### Dreh am Ventil hinter einem Raumthermostat

Über einen Dreh am Ventil entscheidet zuerst das Raumthermostat. Bei Better Thermostat hängt das an seinem Schalter *Kindersicherung* für den Raum.

- **Kindersicherung aus:** Better Thermostat übernimmt die Zahl, die das Ventil nach dem Dreh zeigt, als Sollwert für den Raum. Thriftherm führt das als Änderung am Gerät. Achtung: Das ist die Zahl am Ventil, nicht der Unterschied. Zeigt das Ventil wegen der Korrektur von Better Thermostat 16 °C bei 18,5 °C für den Raum, machen drei Rasten nach unten daraus 14,5 °C für den Raum.
- **Kindersicherung an:** Better Thermostat stellt das Ventil zurück und behält seinen Sollwert. Am 12.10.2026 gemessen: von 16 auf 14,5 °C, 0,9 Sekunden später wieder 16 °C. Daran hält sich auch Thriftherm: Der Dreh zählt nicht.
- **Die Sperre am Ventil selbst** ist davon unabhängig. Sie am Ventil aufzuheben, schaltet die Kindersicherung von Better Thermostat nicht aus.
- **Versatile Thermostat ohne *Änderungen am Gerät folgen*** macht mit dem Dreh gar nichts. Dann übernimmt Thriftherm ihn nach fünf Sekunden selbst, und zwar als Unterschied: Drei Rasten nach unten sind 1,5 Grad weniger für den Raum.

### Was keine Änderung von Hand ist

- **Der Befehl kam nicht an.** Zeigt die Wärmepumpe nach einem Befehl etwas anderes, ohne dass es jemand geändert hat, wird der Befehl wiederholt: nach 2, 4, 8 … Minuten, höchstens stündlich, solange das Gerät ihn nicht ausführt. Der Steuerbefehl zeigt, wie oft in Folge (*Befehle in Folge, die das Gerät nicht ausgeführt hat*). Nur wenn Home Assistant zwischen Befehl und Abweichung neu gestartet wurde, lässt sich das nicht mehr unterscheiden. Dann gilt die Abweichung als Änderung am Gerät.
- **Das Thermostat rundet.** Antwortet ein Thermostat in ganzen Grad auf 21,5 °C mit 22 °C, ist das unser Sollwert, so wie es ihn halten kann. Das ist keine Übersteuerung, und es wird nicht dagegen geschrieben.
- **Wer ein Thermostat nur ausschaltet,** verlangt keine Temperatur. Daraus wird keine Übersteuerung.

Eine Grenze hat das: Schreibt ein Gerät seinen Zustand wenige Sekunden nach einem Befehl, gibt Home Assistant diese Änderung als die des Befehls aus, was auch immer sich geändert hat. Ein Druck auf die Fernbedienung in genau diesen Sekunden zählt deshalb zu dem, der zuletzt befohlen hat.

## Vorheizen

Thriftherm beginnt so früh, dass der Raum zu Beginn eines Komfort-Blocks warm ist:

Beginn = Blockbeginn − (Soll − Ist) / Aufheizrate − Sicherheitszuschlag

- Die Aufheizrate lernt Thriftherm je Raum ([Was gelernt wird](#was-gelernt-wird)). Bis dahin gelten 0,8 Grad je Stunde für Heizkörperräume und 1,2 Grad je Stunde für Wärmepumpen-Räume (Standard, *Start-Aufheizrate*).
- Der Sicherheitszuschlag ist 20 Minuten (Standard).
- Vorgeheizt wird für Blöcke, die in den nächsten 12 Stunden beginnen.
- Vor einer Rückkehr aus *Abwesend* gilt dieselbe Rechnung. Ziel ist die Temperatur, die zur Rückkehr gilt. Mit Zeitplan-Helfer ist das die Komforttemperatur, denn ein Wochenplan-Helfer lässt sich für eine spätere Zeit nicht auswerten. Mit Zeitfenstern aus den Raum-Optionen ist es die Temperatur, die das Zeitfenster dann vorsieht.

## Thermensteuerung

Die Bedienung steht in der [Anleitung, Abschnitt 5](ANLEITUNG.md#5-thermensteuerung-eigene-therme-über-ebusd).

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
- **Frostschutz** heizt, bis der Raum 1 Grad über der Frostschutztemperatur liegt. So schaltet ein Raum knapp an der Grenze die Therme nicht alle paar Minuten an und aus.

### Wann ein Raum die Therme anfordert

- Ein Raum fordert ab 0,3 Grad unter Soll an und hört auf, wenn er weniger als 0,1 Grad darunter liegt.
- **Stillstand:** Läuft die Anforderung seit einer Stunde, liegt der Raum höchstens 0,3 Grad unter Soll und steigt langsamer als 0,15 Grad je Stunde, gilt er als erreicht. Er fordert erst wieder an, wenn er 0,5 Grad unter Soll fällt oder sich der Sollwert ändert. Ein unbekannter Trend, etwa kurz nach einem Neustart, gilt nicht als Stillstand.
- **Sinkt der Sollwert** (z. B. eine Übersteuerung endet), wird eine laufende Anforderung neu mit der Startschwelle bewertet.
- **Ende der Komfortzeit:** In der letzten halben Stunde einer Komfortzeit fordert kein Raum mehr an. Die Wärme käme zu spät. Das gilt auch kurz nach dem Ende, solange der Zeitplan noch nicht umgeschaltet hat (bis zu 2 Minuten).
- *Schnell aufheizen* ist von Stillstand und Komfortende ausgenommen.

### Mitheizen

- Läuft die Therme ohnehin, bekommen andere Räume in ihrer Komfortzeit, die unter Soll liegen, 0,5 Grad mehr (Grund *Heizt mit*).
- Ein Raum, dessen Vorheizen in der nächsten Stunde begänne, fängt jetzt an (Grund *Heizt früher vor*).
- Warum: Ein einzelner Heizkörper nimmt nur etwa 1 kW ab, die Therme brennt aber mit mindestens 8 kW. Mehr offene Heizkörper bedeuten längere Brennerläufe und weniger Starts.
- Mitheizende Räume fordern selbst nichts an und halten die Therme nicht am Laufen.

### Ausfälle und Wiederholung

- **Wiederholung:** Thriftherm sendet seinen Befehl alle 2 Minuten. Ohne Befehl fällt die Therme nach 9–16 Minuten (gemessen) auf ihren Drehknopf zurück. Ein verlorenes Telegramm schadet so nicht, und ein ausgefallenes Home Assistant gibt die Therme an ihren Knopf zurück.
- **Kurze Aussetzer:** Adapter verlieren das eBUS-Signal immer wieder für eine Sekunde. Erst wenn es zwei Minuten lang wegbleibt, gilt das als Datenausfall.
- **Nach einem Neustart** wartet Thriftherm bis zu fünf Minuten auf die Raumfühler, bevor es den Heizbetrieb sperrt. Bis dahin geht nichts hinaus, und die Therme behält ihren letzten Befehl.
- **Stummes Heizkörperthermostat:** Die Frist ist zweieinhalb Stunden. Sie lässt Platz für Thermostate, die sich nur etwa stündlich melden (Zigbee-Standard), und für eine verpasste Meldung. Eine Frist von einer Stunde hielt gesunde Ventile für verloren (gemeldet am 10.10.2026). An der Testanlage melden sich die Thermostate mindestens alle 15 Minuten. Als Lebenszeichen zählt jede Änderung an einer Entität des Thermostats; eine Meldung mit unveränderten Werten hinterlässt in Home Assistant keine Spur. Mit ZHA zählt auch ZHAs eigenes *Zuletzt gesehen*, außer ZHA führt das Gerät als nicht verfügbar.

### Warmwasser-Erkennung

ebusd meldet Warmwasser nicht bei jeder Therme zuverlässig. Deshalb erkennt Thriftherm es selbst. Meldet die Therme einen Statuscode, gilt der. Sonst gilt als Warmwasser:

- Gas brennt (über 1000 W am Gaszähler), obwohl der Heizbetrieb seit mindestens zwei Minuten gesperrt ist;
- der Vorlauf liegt mehr als 15 Grad über dem gesendeten Heizungs-Soll;
- der Rücklauf liegt mehr als 1 Grad über dem Vorlauf (nur ohne Statuscode);
- der Vorlauf liegt mehr als 2 Grad über dem Heizmaximum, solange dieses Maximum gilt, also Thriftherm einen Vorlauf oder eine Sperre sendet.

Die ersten beiden greifen innerhalb einer Minute, die Temperaturen kommen nur alle paar Minuten. *Therme Warmwasser aktiv* bleibt nach dem letzten Zeichen noch 10 Minuten an, mit Statuscode 2 Minuten. Das Lernen pausiert eine Stunde, damit Duschwasser nicht die Heizkurve verstellt.

**Warmstart.** Eine Kombitherme hält ihren Warmwasser-Wärmetauscher warm und brennt dafür etwa einmal pro Stunde kurz, auch wenn die Heizung gesperrt ist. Dabei meldet sie denselben Statuscode wie beim Heizen (S.4). Ist der Heizbetrieb seit mindestens zwei Minuten gesperrt, meldet sie trotzdem „Heizen“ und steht dabei die Heizungspumpe, ist das dieser Warmstart: Er zählt nicht als Brennerstart der Heizung, und sein Gas zählt als Warmwasser, auch was der Gaszähler kurz danach noch meldet. Läuft die Pumpe beim Brennen, heizt die Therme wirklich, etwa weil die Sperre sie nicht erreicht hat; ohne Angabe zur Pumpe gilt nichts als Warmstart. Das Lernen pausiert für einen Warmstart nicht, der Heizkreis blieb kalt, und der heiße Wärmetauscher danach zählt 20 Minuten lang nicht als Zeichen für Warmwasser (gemessen am 16.10.2026: eine Minute bei S.4, Heizungspumpe aus, Vorlauf von 21 auf 67 °C).

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

**Pumpenmodus, gemessen:** An der getesteten Therme war der Pumpenmodus *Nachlauf* der Grund für ein kaltes Bad. Mit Pumpenmodus *durchlaufend* stieg die Temperatur im Bad innerhalb einer Stunde von 20,8 auf 21,5 °C, nachdem sie sich den ganzen Nachmittag nicht bewegt hatte.

## Aufheiztempo

Die Einstellung steht in der [Anleitung, Aufheiztempo](ANLEITUNG.md#aufheiztempo).

**Anlass:** Von einem Nutzer am 14.10.2026 gemeldet: Wohnzimmer von 20,5 auf 23,5 °C gestellt, der Vorlauf blieb 40 Minuten bei 45 °C, die Therme taktete mit 44 °C Rücklauf, und zwei Stunden später stand der Raum bei 22,2 °C.

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
- Stand der Vorlauf im Abschnitt schon am Maximum, stieg er gerade so schnell, wie er darf, oder lag das, was der Raum verlangt, noch unter dem Vorlauf-Minimum, gibt es keinen weiteren Zuschlag: Mehr käme nicht an. Weniger geht trotzdem. Im Test vom 15.10.2026 wuchs der Zuschlag bei 22 °C draußen sonst auf 14 Grad, ohne den Vorlauf zu bewegen.
- War ein Aufheizen deutlich schneller fertig als gewünscht (im Schnitt seit Beginn mehr als anderthalbmal so schnell, nach mindestens 10 Minuten), nimmt Thriftherm 2 Grad weg.

**Wirkung:** Der Zuschlag wird mit dem Heizbedarf des Raums malgenommen (0 bis 100 %). Je näher der Raum seinem Soll kommt, desto kleiner wird er.

**Merken:** Der Zuschlag am Ende eines Aufheizens ist der Start fürs nächste Aufheizen dieses Raums. Er bleibt über einen Neustart erhalten. Steht das Aufheiztempo auf 0, läuft kein Aufheizen; die gemerkten Werte bleiben für später.

**Lernen:** Solange ein Raum mit Zuschlag aufheizt, pausiert das Lernen der Heizkurve (Grund *ein Raum heizt mit Zuschlag auf*). Eine Therme, die auf dem höheren Vorlauf taktet, würde ihr sonst beibringen, nach unten zu gehen.

## Lernen

Was gelernt wird und wo du es siehst, steht in der [Anleitung, Abschnitt 6](ANLEITUNG.md#6-lernen).

### Was gelernt wird

| Bereich | Was gelernt wird | Woraus |
|---|---|---|
| Therme | Heizkurven-Korrektur je Lastfall und Raum, in zwei Teilen, je ±10 Grad: **Höhe** (gilt bei jeder Außentemperatur) und **Steigung** (voll bei −10 °C, gar nicht bei +15 °C) | Spreizung Vorlauf/Rücklauf, wie schnell die Räume warm werden, und Taktbetrieb |
| Räume | Aufheizrate je Raum (Grad je Stunde) | Heizphasen bei geschlossenem Fenster, in denen eine Wärmequelle den Raum erreicht, mindestens 30 Minuten lang und mindestens 0,5 Grad Anstieg. Es gilt der Median der letzten 10 |
| Räume | Auskühlkoeffizient je Raum (1/h): Temperaturabfall je Stunde = Koeffizient × (drinnen − draußen) | Phasen ohne Heizen, Fenster zu, drinnen mindestens 5 Grad wärmer als draußen, mindestens eine Stunde lang und mindestens 0,3 Grad Abfall. Es gilt der Median der letzten 10 |
| Räume | Heizleistung je Raum (Grad je Stunde ohne Verluste) | Aufheizrate plus die Verluste während der Phase. Anders als die reine Rate gilt sie auch im Winter |
| Wärmepumpe | COP-Kennfeld über der Außentemperatur, Sollwert-Korrektur | gemessene COP-Läufe, siehe [Wärmepumpe](#wärmepumpe) |

**Am Vorlauf-Minimum lernt die Kurve nicht nach oben.** An einem milden Tag liegt die Kurve unter dem Minimum, und gesendet wird das Minimum. Kommt ein Raum dann nicht hin, braucht er mehr als das Minimum; über die Kurve an einem kalten Tag sagt das nichts. Früher hob ein Schritt den Wert um die ganze Lücke bis zum Minimum: Im Test vom 15.10.2026 stieg bei 16 °C draußen ein Wert mit einem Schritt um 4,4 Grad, nach drei Schritten stand das Schlafzimmer bei 9,3 Grad mehr. Jetzt bleibt das Lernen dort stehen, und der Lernstatus zeigt *Ein Raum kommt nicht hin, der Vorlauf steht aber am Minimum*. Hält das an, ist das Minimum für diesen Raum zu niedrig, und du hebst es in den Thermen-Optionen an.

**An der Grenze der Korrektur** (±10 Grad für Höhe oder Steigung) geht ein Schritt nur noch bis zur Grenze, oder er entfällt. Dann zeigt der Lernstatus *Heizkurven-Korrektur an ihrer Grenze (Höhe oder Steigung, je ±10 Grad)*, bis eine Stunde und 20 Minuten lang kein Schritt mehr an die Grenze stößt. Welche Linie es ist, steht im Attribut `curve_by_load`. Steht ein einzelner Raum dauerhaft dort, liegt es meist nicht an der Kurve, sondern am Heizkörper oder an der Hydraulik dieses Raums.

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
- **Rauf um eine Stufe**, wenn ein angeforderter Raum hängt und der Heizbetrieb seit mindestens 30 Minuten frei ist (Grund *Therme taktet und ein Raum erreicht sein Soll nicht*). Hängen heißt: mindestens 0,2 Grad unter Soll und langsamer als 0,2 Grad je Stunde. Ein Raum, den die Stillstand-Regel aufgegeben hat, zählt mit.
- **Runter** nur, wenn alle angeforderten Räume zügig warm werden: ab 0,5 Grad je Stunde.

Gemessen am 10.10.2026: Mit 35 °C Mindest-Vorlauf statt 45 °C lief der Brenner nur noch 41 s alle 15 min statt 62 s. Das Bad blieb 14 Stunden 0,2 bis 0,3 Grad unter Soll. Weniger Vorlauf hieß dort weniger Wärme, nicht weniger Starts.

### Lastfälle und Werte je Raum

Welchen Vorlauf die Wohnung braucht, hängt von der Außentemperatur ab, davon, wie viele Heizkörper Wärme abnehmen, und von jedem Heizkörper: Ein kleiner Heizkörper in einem kalten Raum braucht mehr als ein großer.

Thriftherm führt deshalb drei Lastfälle: ein Raum fordert an, wenige, die Mehrheit. In jedem Lastfall hat jeder Raum eine eigene Korrektur mit Höhe und Steigung. Sechs Zahlen je Raum sagen also, welcher Vorlauf zu welcher Außentemperatur und Raumzahl passt.

**Die Mehrheit** sind mehr als der *Anteil der Räume …* der Räume, die gerade Wärme abnehmen können (Standard: mehr als die Hälfte). Bei vier Räumen sind zwei „wenige“ und drei die Mehrheit, bei drei Räumen sind zwei schon die Mehrheit.

- **Der Raum, der am meisten braucht, bestimmt den Vorlauf.** Es gibt nur einen Vorlauf für alle Heizkörper. Von den Räumen, die Wärme wollen, gibt der den Vorlauf vor, der mit seinem Wert und seinen Zuschlägen am meisten verlangt. Bei den anderen nehmen die Ventile weniger. Welcher das ist, hängt von Heizkörper, Wänden und Fenstern ab, nicht von der Raumgröße: Am Testtag (13.10.2026) blieb die kleine Küche stehen, während das Wohnzimmer weiter stieg.
- **Kommt ein Raum nicht hin, hebt er nur seinen eigenen Wert an.** Er beginnt dabei bei dem Vorlauf, der tatsächlich gesendet wurde: Wer schon mehr bekam als seinen eigenen Wert und trotzdem nicht hinkommt, braucht mehr als das. Die Küche, die nicht hinkommt, sagt nichts über das Wohnzimmer.
- **Schritte nach unten und Schritte wegen der Spreizung** bewegen den Wert des Raums, der den Vorlauf bestimmt hat. Nach einem Schritt nach unten liegt kein Raum, der Wärme will, mehr über dem neuen Wert. Sonst bestimmte gleich der nächste den Vorlauf, und er käme nie herunter.
- **Dafür muss kein Raum allein heizen.** Jedes Mal, wenn ein Raum zusammen mit anderen anfordert, wird sein eigenes Vorankommen bewertet, und er sammelt seine eigenen Betriebsstunden.
- **Ein Lernschritt verändert nur den Lastfall, in dem er gemacht wurde.** Gemessen am 11.10.2026: Das Bad allein trieb die gemeinsame Korrektur auf 4 Grad mehr, und drei Räume zusammen starteten danach von dort.
- **Ein neuer Lastfall zählt erst nach zehn Minuten.** Fordert ein dritter Raum mal an, mal nicht, würde der Vorlauf sonst im Minutentakt wechseln (gesehen am 11.10.2026: jede Minute 4 Grad). Wechselt der Lastfall, ändert sich der Vorlauf um den Unterschied der beiden Kurven; die Vorlaufgrenzen gelten immer. Fordert gerade kein Raum an, bleibt der Lastfall, bis Thriftherm den Heizbetrieb sperrt.

**Start ohne eigenen Wert:**

- Ein Lastfall ohne eigenen Wert startet mit dem des nächstgelegenen Lastfalls. Sind zwei gleich nah, nimmt er den mit mehr Räumen. Gibt es keinen, startet er mit der bisherigen gemeinsamen Korrektur.
- Ein Raum ohne eigenen Wert startet mit dem Wert, den sein Lastfall vor den Werten je Raum gelernt hatte. So ändert ein Update den Vorlauf nicht. Er übernimmt auch, ob dieser Wert schon gefunden war und wie lange er sich schon bewährt hat.
- Gibt es den nicht, startet der Raum mit seinem eigenen Wert aus dem nächstgelegenen Lastfall, denn es ist derselbe Heizkörper.
- Sonst übernehmen Lastfall und Raum nur den Wert. Betriebsstunden und Schritte sammeln sie selbst.

### Gefundene Werte

Ohne diese Regel beantwortet das Lernen jede kleine Abweichung, und der Vorlauf geht dauernd hoch und runter.

- Hat ein Wert drei Betriebsstunden ohne hängenden Raum und ohne Lernschritt hinter sich, gilt er als gefunden und bleibt stehen.
- Zu den drei Stunden zählt nur Zeit, in der ein Raum angefordert hat und der Heizbetrieb schon eine halbe Stunde frei war. Ein Raum, dessen Vorankommen nichts über den Vorlauf sagt ([Schutz gegen falsches Lernen](#schutz-gegen-falsches-lernen)), sammelt dabei keine Zeit. Lücken über 5 Minuten zwischen zwei Zyklen (Neustart, Ausfall) zählen nicht als Betrieb.
- Angehoben wird ein gefundener Wert erst wieder, wenn ein Raum 45 Minuten lang sein Soll nicht erreicht. Gesenkt wird er erst, wenn die Räume es zwei Stunden lang zulassen.
- Das darf jeweils bis zu 45 Minuten aussetzen, ohne von vorn zu beginnen. Denn ein Raum, der 0,2 Grad unter Soll liegt, zeigt die nächste halbe Stunde vielleicht nur 0,1 Grad darunter (gemessen am 10.10.2026 im Bad), und eine taktende Therme beendet ihre Anforderungen immer wieder.
- Nach einem Lernschritt muss sich der Wert neu bewähren.

### Schutz gegen falsches Lernen

**Wann gelernt wird:**

- nur im Modus *Aktiv*;
- frühestens 10 Minuten nach Brennerstart und aus mindestens 10 Messwerten;
- nie während Warmwasser, einer Sollwertänderung in den letzten 20 Minuten, Schnell-Aufheizen, Trocknung, einem Aufheizen mit Zuschlag ([Aufheiztempo](#aufheiztempo)) oder wenn der Sicherheitsstatus nicht OK ist.

Nach jeder solchen Pause wartet das Lernen ein volles Prüffenster von 20 Minuten und zählt nur Brennerläufe aus dieser Zeit. Was davor war, gehörte zu einem anderen Sollwert, einer Trocknung oder dem Drehknopf der Therme.

**Wartezeit und Tagesgrenzen** gelten für alle Lastfälle und Räume gemeinsam:

- vor einem Schritt nach oben mindestens 45 Minuten seit dem letzten Schritt, vor einem nach unten eine Stunde;
- je Tag höchstens 10 Grad nach oben und 4 Grad nach unten.

**Welche Räume nicht als „kommt nicht hin“ zählen:**

- **Ein Raum mit Schnell-Aufheizen:** Ein Wunsch auf Zeit sagt nichts über die Heizkurve, die die geplanten Temperaturen halten soll.
- **Ein Raum mit Handänderung, solange er noch dorthin aufheizt.** Ist er bis auf 1 Grad heran und hält dort, zählt er wie jeder Raum. Sonst lernte ein Haushalt, der nur von Hand heizt, nie etwas. Gesehen am 13.10.2026: Vier Räume, für einen Test 3 Grad über ihre Temperatur gestellt, trieben die Korrektur der meisten Räume in drei Stunden von 1 auf 8,5 Grad.
- **Ein Raum, dessen Thermostat meldet, dass es gerade nicht heizt** (das sagen Better Thermostat und Versatile Thermostat). Sein Ventil ist zu, auch wenn der Raum unter Soll liegt, und ein heißerer Vorlauf schlösse es nur früher.

### Schrittgrößen

Die Schritte wachsen mit der Gewissheit: erst grob, dann fein.

- **Ein Schritt nach oben nach einem nach oben** verdoppelt sich: 1, 2, 4 Grad, mehr nicht.
- **Ein Schritt gegen den vorigen** halbiert sich, bis auf 0,5 Grad.
- **Ein Schritt nach unten nach einem nach unten** ist wieder der erste Schritt. Schritte nach unten wachsen nie: Komfort geht vor Sparen.
- In einem Lastfall zählt der letzte Schritt dieses Lastfalls. Ein Schritt, den das Bad allein machte, sagt nichts über den nächsten für die ganze Wohnung.
- Kein Schritt geht weiter, als der Vorlauf wegen seiner Grenzen folgen kann, und keiner weiter, als die Tagesgrenze noch zulässt. Bliebe weniger als 0,5 Grad, entfällt der Schritt.
- Nach drei Stunden ohne Schritt beginnt eine Folge wieder beim ersten Schritt: 1 Grad im **Einlernen**, 0,5 Grad im **Verfeinern**. *Verfeinern* gilt nach sechs Änderungen oder nach einem Tag ohne Änderung.

Gemessen am 11.10.2026: Mit festen Schritten von 1 Grad, vier am Tag, stand die Korrektur nach acht Stunden bei 4 Grad mehr, und das Bad lag noch 0,6 Grad unter Soll. Das Vorlauf-Minimum war dort für das Bad allein 10 Grad zu niedrig.

## Wärmepumpe

Die Bedienung steht in der [Anleitung, Abschnitt 7](ANLEITUNG.md#7-wärmepumpe-optional).

### COP messen

Der COP ist die Wärme, die die Wärmepumpe in die Luft bringt, geteilt durch den Strom, den sie braucht.

- **Wärme:** aus der Luftmenge und dem Unterschied im Wärmeinhalt zwischen Ansaug- und Ausblasluft. Dafür zählen Temperatur und Feuchte der Ansaugluft und die Temperatur der Ausblasluft.
- **Luftmenge:** aus der Lüfterdrehzahl über die Kennlinie (Drehzahl → m³/h) aus der Einrichtung, mal Schlauchfaktor. Knapp außerhalb der Kennlinie (bis 20 % darüber oder darunter) rechnet Thriftherm mit der Drehzahl weiter. Steht der Verdichter zwischen zwei Starts, auch weiter darunter: Sonst fehlte die Wärme der Pause, und der ganze Takt fiele weg.
- **Schlauchfaktor:** Er korrigiert die Luftmenge, nicht die Wärme. Gegen den Widerstand der Schläuche fördert der Lüfter bei gleicher Drehzahl weniger Luft. Was die Schläuche unterwegs an Wärme abgeben, bleibt in der Wohnung und ist kein Verlust.

Ein Messwert zählt erst, wenn das Gerät eingeschwungen ist:

- der Verdichter läuft seit mindestens 10 Minuten (Standard);
- die Leistung liegt bei mindestens 150 W (Standard);
- die Ausblasluft ist mindestens 3 Grad wärmer als die Ansaugluft (Standard) und ändert sich um höchstens 0,5 Grad je Minute (Standard);
- der Wert liegt nicht über 60 % des physikalisch Möglichen (Carnot, aus den Temperaturen der beiden Wärmetauscher).

Angezeigt wird der COP aus mindestens 12 gültigen Messwerten (Standard), von Ausreißern bereinigt und geglättet. Sonst steht dort „unbekannt“ statt einer erfundenen Zahl.

**COP über den Takt:** Taktet die Wärmepumpe, weil ihr Sollwert erreicht ist, gibt es keinen eingeschwungenen Wert. Dann zählt der ganze Takt: Wärme und Strom werden von einem Verdichterstart bis zum nächsten aufsummiert, die Pause mit ihrem Standby-Strom eingeschlossen. Dieser COP (Attribut *COP über den letzten Takt*) ist niedriger als der im Dauerlauf, aber er ist der, der die Kosten bestimmt.

**COP-Kennfeld:** Thriftherm merkt sich den COP in Stufen von 2 Grad Außentemperatur. Ab 5 Messwerten gilt eine Stufe als bekannt. Für die übrigen nimmt es eine Kurve nach typischen Datenblättern und passt sie mit einem Faktor aus den bekannten Stufen an; der Faktor beginnt bei 80 %. Ist im Wärmepumpen-Formular unter *COP-Werte dieses Modells* ein Modell gewählt, gelten für Stufen ohne eigene Messung dessen mitgelieferte Werte und außerhalb davon die Kurve, angepasst an diese Werte (Grundlage *Gemessen am gleichen Modell*). Was das eigene Gerät misst, geht immer vor. Mitgeliefert ist bisher die Midea PortaSplit: an einem Gerät gemessen, in der ersten Heizwoche, bei 2 bis 16 °C draußen.

**Lernläufe:** Hat eine Außentemperatur-Stufe noch keine 5 Messwerte und liegt der geschätzte COP bei mindestens 85 % des Break-even-COP, darf die Wärmepumpe dort eine Stunde laufen, um zu messen, auch wenn die Heizung gerade günstiger wäre. Das geschieht nur, wenn ein Raum Wärme braucht oder getrocknet wird, und je Stufe höchstens einmal am Tag. Abschalten mit *Lernläufe erlauben* in den Wärmepumpen-Optionen.

### Sperren

- **Zu teuer:** Liegt der gemessene COP 30 Minuten lang unter dem *Break-even-COP*, heizt die Wärmepumpe teurer als die Heizung und wird gesperrt (Parameter *Sperre nach Vereisung / Ineffizienz*, Standard 120 Minuten). Im Modus *Nur Wärmepumpe* gibt es diese Sperre nicht, und eine laufende endet sofort, wenn du auf *Nur Wärmepumpe* stellst.
- **Vereisung oder häufiges Abtauen:** Diese Sperre schützt das Gerät und läuft immer bis zu ihrem Ende.

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
- **Gesperrt** wird bei mehr als 3 Abtauzyklen in 90 Minuten (Standard) oder bei vereistem Außen-Coil zusammen mit kalter Ausblasluft. Ein Außen-Coil 8 bis 12 Grad unter der Luft ist bei Volllast im Frost allein noch normal, ein Leistungsabfall dabei auch: So drosselt der Verdichter (gesehen am 20.10.2026: von 630 auf 348 W, und die Wärmepumpe heizte weiter).
- Kreislauf und Leistung werden nur im Heizbetrieb gelesen. Ein Verdichter, der zum Kühlen oder Entfeuchten anläuft, zeigt dieselben Temperaturen.

### Bad-Trocknung

- **Auslöser:** Die absolute Feuchte liegt mehr als 2,5 g/m³ über ihrem Grundwert (Standard, *Auslöser: Anstieg der absoluten Feuchte*). Der Grundwert ist der Median der letzten drei Stunden ohne die letzten 20 Minuten. Liegt die relative Feuchte schon über 70 %, reichen 60 % dieses Anstiegs.
- **Start nach dem Duschen:** Der Anstieg merkt die Dusche nur vor. Die Trocknung beginnt, sobald die Feuchte drei Minuten lang keinen neuen Höchstwert erreicht (mehr als 0,2 g/m³ darüber), spätestens 20 Minuten nach dem Anstieg.
- **Während der Trocknung** bläst die Wärmepumpe mit ihrer höchsten Lüfterstufe und der Trocknungs-Solltemperatur (Standard 22 °C), nie über Soll plus 1,5 Grad. Folgt ihr Sollwert den Räumen, bekommt sie dabei ihren eigenen Messwert plus 1 Grad, wenn das mehr ist als der Sollwert der Räume; dieser Vorsprung reicht höchstens bis 2 Grad über das Trocknungsziel. Mit dem Sollwert der Räume allein bliebe sie samt Lüfter stehen, wenn es an ihrem Platz schon so warm ist (gesehen am 18.10.2026). Kann sie gerade nicht heizen, wartet die Trocknung mit ihrer restlichen Zeit.
- **Ende:** wenn der Taupunkt wieder mindestens 4 Grad unter der Raumtemperatur liegt und die absolute Feuchte höchstens 1 g/m³ über dem Grundwert; spätestens nach der längsten Dauer (Standard 60 Minuten). Bei offenem Fenster bläst die Wärmepumpe weiter. Bleibt es länger als 20 Minuten offen und liegt die Feuchte dann noch mehr als 1 g/m³ über dem Grundwert, endet die Trocknung ebenfalls.
- **Pause danach:** Endete eine Trocknung wegen ihrer längsten Dauer oder wegen eines wirkungslos offenen Fensters, startet drei Stunden lang keine neue von selbst. Der Grundwert enthält in dieser Zeit noch die Luft von vor dem Duschen (gemessen am 25.09.2026).
- **Der Knopf *Jetzt trocknen*** startet sofort und läuft die volle Dauer, egal wie Feuchte und Fenster stehen.

### Sollwert folgt den Räumen

Mit dem Schalter bekommt die Wärmepumpe die höchste Solltemperatur der versorgten Räume plus den Sollwert-Abstand (Standard 1 Grad).

**Anheben:** Das Gerät regelt auf die Luft in dem Raum, in dem es steht. Ist der wärmer als ein versorgter Raum, drosselt es, bevor dieser warm ist. Gesehen am 13.10.2026: auf die 21 °C des Bads gestellt, 22 °C an der eigenen Ansaugung, das Bad bei 19,8 °C, und die Therme wurde dafür angefordert.

- Ein versorgter Raum hängt, wenn er unter Soll liegt und langsamer als 0,3 Grad je Stunde steigt. Ein unbekannter Trend zählt nicht, ein Raum in der Trocknung auch nicht.
- Hängt er 20 Minuten, hebt Thriftherm den Sollwert um ein halbes Grad (oder die gröbere Stufe des Geräts), höchstens 2 Grad über den Sollwert-Abstand (Attribut `eco_offset_k`).
- Das geschieht nur, solange das Gerät drosselt, also sein eigener Messwert bis auf 1 Grad an seinem Sollwert liegt (ohne eigenen Messwert: der Verdichter steht, zuletzt die Ansaugluft). Läuft es schon mit voller Kraft oder ist das Anheben ausgeschöpft, hilft die Therme (Attribut `boiler_helps`), bis die Räume warm sind.

**Zurücknehmen:** Sind alle versorgten Räume warm, geht der Sollwert in denselben Schritten zurück, frühestens zwei Stunden nach dem letzten Schritt. Liegen alle mindestens 0,5 Grad über Soll, alle 20 Minuten. Jeder Schritt lässt das Gerät piepen, deshalb gibt es wenige. Gesehen am 13.10.2026: Nach einem Neustart begannen die zwei Stunden neu, und das Gerät lief 1,7 Stunden mit angehobenem Sollwert, obwohl alle Räume 1 bis 2 Grad über Soll lagen.

### Ohne „Sollwert folgt den Räumen“

Dann nutzt Thriftherm den Sollwert als Leistungshebel: Ansaugluft plus ein gelernter Abstand. Ein kleiner Abstand heißt wenig Verdichterlast und hoher COP, 30 °C heißt volle Leistung (nur für *Schnell aufheizen*).

- Die Wärmepumpe startet, wenn ein versorgter Raum 0,3 Grad unter Soll liegt, und hört auf, wenn keiner mehr unter Soll plus 0,2 Grad liegt.
- Sie läuft mindestens 20 Minuten und pausiert mindestens 10 Minuten (Standard, *Mindestlaufzeit*, *Mindestpause*). Eine Sperre beendet einen Lauf sofort.
- Alle 30 Minuten eines Laufs prüft Thriftherm den langsamsten Raum: unter 0,3 Grad je Stunde wird der Abstand um 0,5 Grad größer, über 1,5 Grad je Stunde um 0,5 Grad kleiner, zwischen 0,5 und 5 Grad.
