# Fehlersuche

[English](../en/TROUBLESHOOTING.md) | **Deutsch**

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

**So kann das aussehen:** Der Router vergibt die Adressen nachts neu, der eBUS-Adapter bekommt eine andere IP, und der WLAN-Gaszähler bekommt die alte Adresse des Adapters.

- ebusd findet den Adapter nicht mehr („unable to open …“ im Log). Thriftherm gibt die Therme nach wenigen Minuten ab, und sie heizt nach ihrem Drehknopf.
- ebusd klopft alle paar Sekunden beim Gaszähler an. Der verliert dadurch immer wieder seine MQTT-Verbindung, und seine Werte stehen in Home Assistant auf „nicht verfügbar“.
- Home Assistant zeigt die ganze Zeit die letzten gespeicherten eBUS-Werte weiter an, weil ebusd seine Werte im Broker hinterlegt, auch wenn der tatsächliche Vorlauf längst anders ist.

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

Dafür gibt es drei Ursachen, oft zusammen:

- **Die Pumpe steht mit dem Brenner.** Den Pumpenmodus prüfen ([Schritt 6](EINRICHTUNG.md#6-an-der-therme)). Warum das zählt, steht in der [Anleitung](ANLEITUNG.md#warum-der-pumpenmodus-zählt).
- **Der Mindest-Vorlauf ist zu niedrig.** Bei einer Heizwerttherme die minimale Vorlauftemperatur auf etwa 45 °C stellen ([Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)).
- **Die Pumpe läuft auf der kleinen Stufe.** Dann fließt viel Wasser über den Bypass der Therme statt durch den letzten Heizkörper. Bei Vaillant atmoTEC plus und turboTEC plus regelt das der Diagnosecode d.19 (zweite Diagnoseebene, Code 17): Werkseinstellung 2 heißt, beim Heizen hängt die Stufe von der Teillast d.00 ab, unter 60 % läuft die kleine; 3 heißt immer die große Stufe. **Warnung:** d.19 ist ein Wert für Fachhandwerker. Ändere ihn nur, wenn du weißt, was du tust, schreib den alten Wert auf, und lies die Installationsanleitung deiner Therme. Die große Stufe braucht etwas mehr Strom und kann an fast geschlossenen Ventilen rauschen. Thriftherm schreibt diesen Wert nie.

### Die Therme springt etwa einmal pro Stunde kurz an

Auch nachts und ohne Heizbedarf. Das ist die Warmstart-Funktion der Kombitherme: Sie hält den Warmwasser-Wärmetauscher warm, im Display steht ein **C**. Früher hat ein Raumregler am Bus sie unterdrückt. Thriftherm zählt dieses Gas als Warmwasser und nicht als Brennerstart der Heizung.

So schaltest du sie ab:

- **An der Therme:** den Warmwasser-Drehknopf ganz nach links drehen, dann wieder auf den gewohnten Wert. Das C verschwindet (von einem Nutzer an einer Vaillant bestätigt).
- **In Thriftherm:** in den Optionen unter *Gastherme (ebusd) und Gaszähler* *Warmwasser bereithalten* ausschalten ([Schritt 4.3](EINRICHTUNG.md#43-gastherme-ebusd-und-gaszähler)). Das passt nur zu einer Kombitherme ohne Speicher und wirkt nur bei aktiver Thermensteuerung.

### Reparaturmeldung „Vorlauf der Therme über 100 °C“

Eine Therme darf diesen Wert nie erreichen, egal ob er stimmt oder der Fühler defekt ist. Thriftherm kann das nicht anfordern und nicht stoppen: Gesendet werden höchstens 75 °C, und in einem gemeldeten Fall stieg der Vorlauf trotz gesendeter Sperre. Lass die Therme bald vom Fachbetrieb prüfen (Pumpe, Luft, Druck, Bypass, Vorlauf- und Rücklauffühler, Fehlerspeicher, d.60). Passiert es wieder, schalte sie aus und lass sie aus, bis sie geprüft ist.

Ab 90 °C und bei den Codes S.53 und S.54 (zu wenig Wasser) gibt Thriftherm eine halbe Stunde lang keine neue Heizung frei, höchstens vier Stunden am Stück, damit ein hängender Fühler nicht für immer sperrt. Eine laufende Freigabe bleibt, Frostschutz geht vor, und bei aktiver Thermensteuerung heizt die Wärmepumpe ihre Räume in der Zeit auch dann, wenn die Heizung günstiger wäre, auch über eine Sperre wegen teurer Wärme hinweg. Die Meldung endet einen Tag nach dem letzten Wert über 100 °C, solange steht *Sicherheitsstatus* auf eingeschränkt, und die Heizkurve lernt nicht.

### Ändert Thriftherm meine Warmwassertemperatur?

Nein. Für den Warmwasser-Sollwert sendet Thriftherm „kein Wert“, der Drehknopf der Therme entscheidet. Die einzige Ausnahme wählst du selbst: Ist *Warmwasser bereithalten* aus, heizt die Therme nicht mehr für Warmwasser, solange kein Hahn offen ist.

### Home Assistant ist abgestürzt oder aus

Die Therme fällt innerhalb von 9 bis 16 Minuten auf ihren Drehknopf zurück. Die Thermostate behalten ihren letzten Sollwert.

Bei einer Therme über Entitäten (Vorschau) ist das anders: Stürzt Home Assistant ab oder fällt der Strom aus, bleibt der letzte Zustand stehen, auch eine gesperrte Heizung ([Schritt 4.3a](EINRICHTUNG.md#43a-therme-ohne-ebusd-über-entitäten-vorschau)).

## Räume und Thermostate

### Ein Raum ist kalt, aber die Therme ist gesperrt

Ein Fenster ist offen, oder das Thermostat des Raums ist ausgeschaltet. Schließ das Fenster oder stell das Thermostat wieder auf *Heizen*.

Auch ein [stummes Heizkörperthermostat](ANLEITUNG.md#stumme-thermostate) (Sollwert eine halbe Stunde nicht beantwortet, eine halbe Stunde nicht verfügbar oder sechs Stunden ohne Meldung) fordert keine Wärme an. Dazu erscheint eine Reparaturmeldung. Sieh in Zigbee2MQTT oder ZHA nach seinem *Zuletzt gesehen*; mit ZHA zählt es auch als Meldung.

### Ein Raum steht auf 15 °C

Er ist im Modus *Abwesend*, und 15 °C ist die Abwesenheitstemperatur. Der Frostschutz (7 °C) ist eine eigene, tiefere Grenze.

### Am Thermostat gedreht, und es stellt sich nicht zurück

Eine Änderung von Hand wird zur vorübergehenden Übersteuerung, standardmäßig für 120 Minuten. Du beendest sie mit dem Knopf *Zurück zur Automatik* oder der Aktion `thriftherm.clear_override`. Mehr dazu in der [Anleitung, Änderungen von Hand](ANLEITUNG.md#änderungen-von-hand).

### Übersteuerungen, obwohl niemand am Thermostat war

Meist ein verspätetes Funk-Echo: Better Thermostat schreibt den Sollwert alle paar Sekunden neu, und eine verspätete Antwort über Zigbee hält es für einen Dreh am Thermostat. Das fängt der **Echo-Filter** ab (Optionen → *Regelparameter*, Abschnitt *Freigaben*, standardmäßig an; [so arbeitet er](FUNKTIONSWEISE.md#echo-filter)). Prüfe, ob er eingeschaltet ist. Wann zuletzt ein Echo ignoriert wurde, zeigt der Sensor *Thermostat-Plan* des Raums.

### Ein Raum braucht nach dem Hochstellen sehr lange

Die Heizkurve ist dafür gemacht, Temperaturen zu halten, nicht für schnelles Aufheizen. Wie schnell es gehen soll, stellst du mit dem *Aufheiztempo* unter *Gastherme (ebusd) und Gaszähler* ein ([Anleitung, Aufheiztempo](ANLEITUNG.md#aufheiztempo)). Mehr Tempo kostet mehr Gas.

### Ein Raum bleibt ein paar Zehntel unter dem Soll

Die Karte zeigt dann *Gilt als erreicht, heizt wieder unter …*. Hat ein Raum eine Stunde geheizt, liegt höchstens 0,3 Grad unter dem Soll und steigt kaum noch, gilt er als erreicht: Die Therme würde sonst stundenlang für die letzten Zehntel takten. Er ruft wieder, sobald er 0,5 Grad unter dem Soll liegt oder sich das Soll ändert. *Schnell aufheizen* ist von der Regel ausgenommen.

Kommt ein Raum öfter nicht ganz hin, ist meist der Vorlauf zu niedrig. Stell ein *Aufheiztempo* ein (z. B. 1 Grad pro Stunde): Dann hebt Thriftherm den Vorlauf an, solange der Raum zu langsam steigt, und merkt sich, was er braucht. Das kostet etwas mehr Gas.

### Ein direkt gesteuertes Ventil nimmt die Raumtemperatur nicht

Beim Sonoff TRVZB und anderen ist die *Fühlerauswahl* (`temperature_sensor_select`) in Home Assistant oft deaktiviert, ohne dass es jemand bewusst so eingestellt hat. Dann kann Thriftherm sie nicht auf den Raum stellen, und das Ventil regelt nach seinem eigenen Fühler mit Offset. Thriftherm meldet das mit dem Hinweis *Entitäten am Ventil ausgeschaltet*. Aktiviere die Entität unter Einstellungen → Geräte → das Ventil → Entitäten; der Hinweis verschwindet dann von selbst.

### Ein Raum bleibt knapp unter dem Ziel, das Ventil bleibt zu

Viele Ventile öffnen erst ein Stück unter ihrem Sollwert. Beim Sonoff TRVZB heißt die Einstellung *Temperaturgenauigkeit* (`temperature_accuracy`, ab Werk oft −1): Das Ventil öffnet erst 1 Grad unter seinem Soll und meldet bis dahin „ruht“. Ein Raum für 20 °C pendelt dann zwischen 19 und 20 °C.

- **Ventile direkt oder ein Ventil als Thermostat des Raums:** Thriftherm erkennt die Einstellung (`temperature_accuracy`, `hysteresis`), legt beim Anfordern der Therme den größeren Wert von Vorhalt und Schwelle auf den Sollwert und lässt sich vom „ruht“ des Ventils nicht abhalten.
- **Feiner stellen hilft immer:** Stell die Genauigkeit am Ventil auf −0,4 oder −0,2 (beim TRVZB in Schritten von 0,2). Das Ventil öffnet dann früher, auch hinter Better Thermostat.
- **Hinter Better Thermostat** gleicht BT den Sollwert selbst nach; dort hilft vor allem das feinere Stellen am Ventil. Thriftherm zeigt dafür den Hinweis *Ventil öffnet spät*, der von selbst verschwindet, sobald die Schwelle 0,5 Grad oder feiner ist.

### Ein Raum kommt nicht ans Soll, obwohl die Korrektur an der Grenze steht

Der *Lernstatus* zeigt dann *Heizkurven-Korrektur an ihrer Grenze*, und im Attribut `curve_by_load` steht die Linie dieses Raums bei rund 10 Grad. Thriftherm hat den Vorlauf für ihn also so weit angehoben, wie es darf. Meist ist der Heizkörper zu klein für den Raum, etwa ein schmaler Handtuchheizkörper in einem Bad mit Außenwand. Mehr Vorlauf bringt so einem Raum nur noch wenig.

Prüf zuerst, ob das Ventil ganz öffnet und der Heizkörper entlüftet ist. Bleibt es dabei, stell das Soll des Raums etwas niedriger oder ersetz den Heizkörper durch einen größeren. Über die 10 Grad geht die Korrektur nie hinaus, und der höhere Vorlauf, solange der Raum ruft, kostet nur wenig Gas.

### Der Lernstatus zeigt „Wartet auf Heizlauf“

Das ist kein Fehler. Gelernt wird nur, während die Therme für die Heizung brennt oder taktet und die Thermensteuerung *Aktiv* ist; ohne Außentemperatur lernt die Heizkurve nichts. Den genauen Grund nennt das Attribut *Pausiert weil*. Wie lange das Lernen insgesamt dauert: [Anleitung, Wie lange das Lernen dauert](ANLEITUNG.md#wie-lange-das-lernen-dauert).

### Die Aufheizrate eines Raums bleibt beim Startwert

Ein Raum lernt seine Aufheizrate aus Phasen, in denen er mindestens 30 Minuten lang um mindestens 0,5 Grad steigt, bei geschlossenem Fenster. Ein Raum ohne Absenkung pendelt nur um ein, zwei Zehntel und lernt sie deshalb nie; er bleibt bei der *Start-Aufheizrate*. Das schadet nicht, es betrifft nur, wie früh er vorheizt.

### Ein Thermostat nimmt keine Sollwerte an

Das Thermostat hängt. Nimm die Batterie heraus und setz sie wieder ein, und aktualisiere die Firmware.

## Anzeigen

### Warte auf Raumdaten nach einem Neustart

Direkt nach einem Neustart zeigt *Therme-Steuerbefehl* *Warte auf Raumdaten*. Thriftherm wartet auf die Raumfühler, und bis zu fünf Minuten ist das normal.

### Der Temperaturtrend ist nach einem Neustart leer

Der Trend braucht etwa 30 Minuten Messwerte. Bei einer neuen Installation ist der Sensor ausgeschaltet, bis du ihn einschaltest ([Anleitung, Wichtige Anzeigen](ANLEITUNG.md#wichtige-anzeigen)).

### Vorlauf und Rücklauf ändern sich nur alle paar Minuten

Das ist normal: ebusd fragt die Werte der Therme reihum ab. Für die Regelung holt Thriftherm sich seine Werte einmal pro Minute selbst, mit Gaszähler, sobald Gas brennt, ohne Gaszähler durchgehend. Willst du frischere Werte im Dashboard, kürzt du die Abfrageliste von ebusd: [Weniger ebusd-Werte abfragen](EBUSD.md).

### Die Thriftherm-Karte fehlt („Custom element doesn't exist“)

Das Dashboard zeigt *Custom element not found: thriftherm-card* oder *Custom element doesn't exist*. Thriftherm trägt die Karte beim Start selbst als Dashboard-Ressource ein: *Einstellungen → Dashboards → ⋮ → Ressourcen* zeigt `/thriftherm/thriftherm-card.js?v=…` als JavaScript-Modul. So lädt jedes Dashboard sie, bevor es seine Karten zeichnet. Steht dort ein Eintrag für dieselbe Datei, den du von Hand angelegt hast, übernimmt Thriftherm ihn, statt einen zweiten anzulegen. Wird Thriftherm entfernt, verschwindet der Eintrag wieder.

Hält deine Installation die Ressourcen in YAML (`lovelace: resource_mode: yaml`), kann Thriftherm dort nichts eintragen und lädt die Karte wie bisher auf jeder Seite mit. Das klappt nicht immer zuverlässig, deshalb die Ressource dann selbst eintragen:

```yaml
lovelace:
  resources:
    - url: /thriftherm/thriftherm-card.js
      type: module
```

Fehlt die Karte trotzdem, zeigt der Browser meist noch eine Startseite von vor der Installation oder dem Update. Home Assistant hält diese Seite im Browser vor, deshalb hilft einfaches Neuladen oft nicht.

1. Die Seite hart neu laden: Cmd+Shift+R (Mac) bzw. Strg+Shift+R.
2. Hilft das nicht: im Browser die Websitedaten deiner Home-Assistant-Adresse löschen und neu anmelden. In der App: *Einstellungen → Companion App → Frontend-Cache zurücksetzen*.
3. Hilft auch das nicht: Thriftherm neu laden (*Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → Neu laden*) oder Home Assistant neu starten. Dabei meldet Thriftherm die Karte wieder an.

## Nichts davon passt

- Was eine Reparaturmeldung bedeutet und was du tun kannst, steht in der [Anleitung, Reparaturmeldungen](ANLEITUNG.md#reparaturmeldungen).
- Der Sensor *Entscheidungsgrund* sagt in Worten, warum Thriftherm tut, was es tut.
- Für einen Fehlerbericht: Einstellungen → Geräte & Dienste → Thriftherm → ⋮ → *Diagnose herunterladen*. Die Datei enthält Konfiguration, aktuellen Zustand und Lernwerte; nur die Personen, deren Ankunft *Abwesend* beendet, fehlen darin. Sieh sie vor dem Veröffentlichen durch und hänge sie an ein [Issue](https://github.com/alpha-uniform/thriftherm/issues) an.
