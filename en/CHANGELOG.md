# Changelog

**English** | [Deutsch](../de/AENDERUNGEN.md)

Since 0.7.0 the configuration is at version 4; from 0.5.0 to 0.6.8 it was at version 3.

## 0.8.1 – 2026-11-25

- A room's thermostat and radiator valves share one field; Thriftherm sorts them by their integration (Better, Versatile, Generic Thermostat are room thermostats, anything else is a valve). A radiator valve entered itself as a thermostat so far is driven directly, window included, once the room is saved next; without saving nothing changes.
- The room form is shorter: what every room needs stands at the top; heat pump and bathroom drying, comfort windows without a schedule helper, priority, internal heat sources, lead and valve control lie in folded sections. It is stored as before.
- Fixed: Thriftherm took the boiler's hot water mode (ebusd Status02 hwcmode “on”) for hot water running; with a cylinder boiler every hour counted as hot water and learning stood still. The field now takes a binary sensor only, and a stored Status02 value is no longer read.
- Fixed: for a room that counts as reached and is cooling again the card said “Nearly there”; now “Counts as reached, heats again below …”.
- Fixed: under *Observe only* the card said “Boiler is heating” with flames although nothing went to the boiler; now “Boiler would heat (observing only)”.

## 0.8.0 – 2026-11-20

**Coming from 0.7.5:**

- Every room gets a new thermostat `climate.thriftherm_<room>_room_climate`. Rooms with Better Thermostat then show twice in Apple Home: leave one of the two out of the HomeKit Bridge, best Better Thermostat's.
- Going back to 0.7.5 loses the new options (tolerance below/above the target, the window with valves driven directly, the lead at the thermostat, valves driven directly); rooms driven directly then need Better Thermostat again.

- Fixed: a room preheating as planned or heating up to a raised target counted as lagging for the heating curve and raised it; now only once it rises slower than planned, and such steps go back after the heat-up, at most 2 degrees above their start; a room standing still for an hour holds.
- Fixed: a trial of the heating curve was taken back when the burner ran longer because other rooms heated along; then it now says nothing.
- Fixed: a room with only one learned heat-up preheated for hours or too late; until three are learned the median with the default counts, a single heat-up moves nothing.
- Fixed: with the boiler a room preheats 4 hours at most by default, as with the heat pump (a slow rate made it preheat for many hours otherwise); a *Longest preheat* set still counts, and before a return from *Away* it preheats as long as it takes.
- Fixed: a room began its preheat too late: warmth from elsewhere (a shower, say) counted as a quick heat-up, and slow heat-ups well below the target were never learned; both count right now, with sensors in 0.5 or 1 degree steps too, slow ones only where the radiator can heat, and nothing is planned below 0.2 degrees an hour.
- New: every room has a room climate of its own for Apple Home and Siri, rooms with Better Thermostat too; *off* stays off until you switch it on again; leave one of the two out of the HomeKit Bridge, best Better Thermostat.
- New: heating along in the setback too, up to the setback target, never past it; a valve driven directly with an opening limit (Sonoff TRVZB/TRVZBT) opens only in part then and gets your value back afterwards, also where a radio command is lost.
- New: lead at the thermostat. While the boiler runs for a room, its thermostat gets 0.5 degrees more so that it does not throttle early (per room, 0 = off).
- Fixed: a thermostat that came back with another setpoint after a restart or a change of batteries (or Better Thermostat switched on again) counted as turned by hand; it now gets Thriftherm's setpoint again.
- Fixed: after a restart of MQTT or Home Assistant a silent valve was noticed only hours later.
- New (preview): drive radiator valves directly, without Better Thermostat. The valve gets the target and the room temperature, an offset or a corrected setpoint; the child lock and mode at the valve stay untouched. With the window open the valves are off (or at the frost limit, if you like), and every 7 days they are moved.
- New: *tolerance below the target* and *tolerance above the target* (expert) set when a room calls for the boiler (default as before, 0.3 below to 0.1 below); the learning goes by the band set.
- Fixed: a valve driven directly could shut before its room stopped calling; it now stays open until the end. With district heating or without a boiler it gets the target.
- While a valve is exercised or its battery is nearly flat, the heating curve learns nothing from its room.
- Fixed: with a cycling boiler a step up was taken back as no help even when the burner ran no longer after it; a room heating alone stayed just below its target for days.
- Fixed: a valve on a corrected setpoint could shut while the room was still below its target; there the correction is now rounded up to the valve's next step.
- The mode *Plan only* is now called *Observe only*; in automations the value stays `shadow`.
- The heating curve no longer has "found" values that only yielded to lasting evidence; the rule never acted in weeks. The learning state no longer shows it.
- *Boiler heating starts today* says what it counts from and whether short burner runs may be missed (without a gas meter).
- Fixed: without a gas meter, burner runs under a minute were not counted; now every one the status number shows counts, for cycling and learning too.
- *Boiler heating active* shows the burner by the same sign as the heating starts.
- Fixed: a valve driven directly fell back on its own sensor although the room sensor still held (a sensor whose value stood longer than 30 minutes, a ZHA sensor unchanged for over 6 hours, sensors that report only on a change).
- Fixed: a valve without an "off" went on heating with the window open; it now gets the frost limit.
- Fixed: a value a valve did not take went out again every minute; now further and further apart.
- Fixed: the valve exercise never ran on a Sonoff TRVZB.
- For a test the offset or the corrected setpoint can be forced per room.
- When Thriftherm goes (room back on Better Thermostat, disabled, deleted, Home Assistant stopping), the valves get their own sensor and old offset back.
- New: the room temperature sensor is optional; without one the thermostat or the valve measures.
- Fixed: moving a room from Better Thermostat to its valves (or choosing another thermostat) forgot the room's learned heat-up rates; they now stay.
- New: a notice when a valve behind Better Thermostat opens only more than 0.5 degrees below its setpoint, with the advice to set it to −0.4.
- Fixed: a valve that opens only 1 degree below its setpoint (Sonoff TRVZB) kept its room just below the target; Thriftherm now allows for the threshold.
- Fixed: a room sensor next to an open window called the boiler when it froze outside; a room with its window open now counts as no frost room for 30 minutes, after that it does again.
- New: when the boiler cycles on its least power far below the flow sent, Thriftherm sends more step by step (2 degrees once the last step has shown after 45 minutes) while a room does not get there (up to 55 °C for a condensing boiler, new option *Condensing boiler*) instead of lifting the heating curve; lifts made so go back. Once the rooms rise with room to spare it gives a step back; what was enough is remembered per room and outdoor temperature, and the next heating begins with it; another room calling along keeps it; a heating within two hours of the last begins a step below what that one ended with.
- Fixed: a room whose temperature did not change counted as a silent sensor after 90 minutes and taught nothing, though its device went on reporting (Zigbee2MQTT writes no unchanged value anew); now what counts is when the device last reported.
- Fixed: Home Assistant stopping during a quick heat-up or a drying left the heat pump at 30 °C; it now gets the rooms' target.

## 0.7.5 – 2026-11-15

- The operating mode *Summer* is now called *Off (frost protection only)*.
- The docs are reordered, explain the terms and say how long learning takes.
- Fixed: the boiler's heating curve went up and down because it was lowered in the middle of a heat-up; it now learns only from rooms whose target has stood for 90 minutes.
- Fixed: steps up came faster than their effect could be measured; now 90 minutes apart and 2 degrees at most.
- Fixed: with a cycling boiler a room's value kept rising although more flow did not help; a step without effect is now taken back after 90 minutes, and the learning state says so.
- Fixed: what a room had learned alone also applied with several rooms calling and stayed there for good.
- Fixed: room sensors that report only on larger changes (a Shelly H&T, say) raised the heating curve because their flat line read as "room does not rise" ([#7](https://github.com/alpha-uniform/thriftherm/issues/7)).
- Fixed: with Better Thermostat 2.0 a room it blocks (valve maintenance, devices released, startup) could still call the boiler; it no longer does, and a change is heard at once ([#8](https://github.com/alpha-uniform/thriftherm/issues/8)).

- The learning state *Paused* is now called *Waiting for a heating run*; without an outdoor temperature the heating curve learns nothing.
- Fixed: a room below the frost limit kept the boiler released for good; it is now heated to 1 degree above the limit, and no room target is below the frost limit any more.
- Fixed: a heat pump in a quick heat-up or a drying heated on when the control was set to *Plan only* just while a command went out, or when the evaluation failed.
- Fixed: switching the control off left the heat pump's fan turned up, and a fan run-on kept running.
- Fixed: with room targets below 14 °C the heat pump was sent setpoints below 17 °C.
- Fixed: cooling by hand in summer spoiled the learned heat loss of the rooms.
- Fixed: gas meters that report only every few minutes had half of their gas put to "unassigned"; meters in litres are converted, other units refused.
- Fixed: the room form dropped "served by the heat pump" while no heat pump was entered.
- Fixed: a return at exactly 00:00, set in the card, became "23:59, provisional".
- A gas meter with a flow sensor no longer starts a cycle of its own every few seconds while the burner runs.
- A new room must not end in "cool"; stored learning values that cannot be right are dropped at the start.
- A cycling boiler's heating curve now comes down when a room reaches its target briskly three times in a row; a step down that leaves the room lagging is taken back.
- When more flow did not help a room, Thriftherm tries less: a value learned too high comes down again by itself.
- *Reset learning* applies to single rooms for the boiler too (`scope: ["boiler"]`, `rooms: […]`); a reset of boiler and rooms with a list of rooms now resets only these rooms for the boiler.
- After a restart or saving the options the heating curve goes on learning at once instead of after 90 minutes.
- Diagnostics: each room's last ten calls with duration, rise and verdict.
- The option *A heating curve of its own per room* is gone, every room always has a value of its own; whoever had switched it off starts from the common correction learned so far, and older values per room are forgotten once.

- Default COP values updated.

## 0.7.4 – 2026-11-12

- Rooms are now child devices of "Thriftherm" and are named after the room alone ("Living room" instead of "Thriftherm Living room"), following the naming of Home Assistant 2026.11. Device ids, entity ids, your own names and areas stay.
- Fixed: a room device renamed to just "Thriftherm" showed "Thriftherm" for every room in the card; it now takes the device's area or the room name.
- New: switch *Room sensors report only on a change* (e.g. Shelly H&T): room temperatures hold up to the max. sensor age instead of 6 hours at most.
- Default COP values updated.

## 0.7.3 – 2026-11-09

- Fixed: fewer false alarms "radiator thermostat not responding"; it is reported only when a valve really does not react.
- New: the heat pump protects its rooms from frost, at once without central heating, otherwise as a reserve after 30 minutes.
- Fixed: a heat pump in a quick heat-up or a drying went on heating when its control was switched off.
- Fixed: a boiler through entities could stay blocked after Thriftherm stopped.
- Fixed: the heat pump kept stopping itself while it ran through.
- The heat pump shows *Defrosting*, and a mode of its own such as *Heat/Cool* is left to you.
- The *Decision reason* changes far less often, and a restart no longer brings a warning.
- Fixed: quiet Zigbee outdoor sensors, power meters in kW and thermostats in whole degrees.
- Safer: no person in the diagnostics, more checks during setup, smaller fixes in learning, boiler and card.
- Default COP values updated.

## 0.7.2 – 2026-11-05

- Thriftherm is no longer a beta; the mode is now just called *Plan only*.
- Fixed: when the heat pump did not start after a rest with a room below its target, it could take up to an hour; its setpoint now goes to 1.5 degrees above its reading after 5 minutes.
- Fixed: after a drying or a quick heat-up it ran above its least speed for a long time; its setpoint is set once more 15 minutes later.
- Fixed: when the outlet sensor stayed quiet for over an hour in a steady run, the COP was unknown and the map learned nothing; the limit now counts only once its device is silent too.
- Default COP values updated, now also for 0 to 2 °C.

## 0.7.1 – 2026-11-03

- New per room: *Longest preheat* limits how early a room begins, and it then calls the boiler in its block up to its end ([#6](https://github.com/alpha-uniform/thriftherm/issues/6)).
- Fixed: a flow above 100 °C counted as "data missing" and boiler fault codes as hot water; a repair notice now warns, and from 90 °C and with S.53/S.54 there is no new release for now, while the heat pump heats its rooms even if it is dearer ([#5](https://github.com/alpha-uniform/thriftherm/issues/5)).
- Fixed: a heat pump standing for over half an hour counted as stopping anew again and again, and its setpoint sank; if it does not start with a room below target, the setpoint now goes half a degree higher every half hour.
- New choice *Heat pump alone* under *When the heat pump alone is not enough*: least power, its rooms do not call the boiler while the heat pump is cheaper, and the boiler goes on heating the others.
- Fixed: a room thermostat back after a restart with another setpoint counted as a change by hand for two hours; the plan now applies again.
- Fixed: the COP map knew only the last 45 minutes of running per step and read too high, by up to 27 % in the step of a start; it now adds up heat and electricity over all of the heating time with start and run-on, and old data fades with a half-life of 100 run hours.
- Fixed: the COP of a short run no longer chooses the central heating and so stops the heat pump; the sensor still shows it, the choice goes by the settled value or the map.
- Fixed: when a settled COP too low stopped the heat pump and the map started it again, running and resting went on with no end; two such values at least 30 minutes apart now lock it out, also across a rest of up to an hour (*Lockout after icing / inefficiency*, 120 minutes by default).
- New learning phase: the old COP map could not be converted. Until a step has 3 hours of run time its value still counts, weakly (basis *Old table*), and learning runs are possible there again.
- A new duct factor only rescales the COP map; another fan stage in normal operation starts it anew, as another airflow curve already did.
- Fixed: the COP was often "unknown" because a quiet sensor counted as out of date; the last report now counts for 60 minutes at the outlet; at the intake for 3 hours once its device is silent too, otherwise up to a day (adjustable under *COP measurement*).
- Fixed: *Dry now* left the compressor standing; the heat pump now runs a little stronger while drying.
- The heat pump follows its rising reading and stops itself less often.
- Fewer hard starts of the heat pump: with the central heating cheaper but a room still wanted, it stays in heating and rests instead of switching off and on; the *Decision reason* then says "central heating cheaper".
- Fixed: returning from Away heats to the target the schedule gives at that time; back at the end of the comfort time, that is the setback, and shortly before a comfort time the room is pre-heated for it.
- Fixed: before the return from Away no room is set lower than the away temperature.
- Fixed: the heat pump is no longer switched off during a defrost when the rooms are warm.
- New boiler in the list: Vaillant atmoTEC pro VUW PL 240/3-3M ([#4](https://github.com/alpha-uniform/thriftherm/issues/4)).
- Default COP values updated.

## 0.7.0 — 2026-10-30

In normal operation the heat pump now runs at its least power only, where its COP is best. Configuration version 4, which older versions do not load: make a backup before the update in case you want to go back.

- Half a degree above the coldest room's target the heat pump rests, and it beeps less; if a room lags, the boiler or district heating helps. Its COP is measured at its least power only. The options *Setpoint follows the rooms* and *setpoint offset* are gone.
- New options *When the heat pump alone is not enough* (boiler or district heating helps, heat pump runs higher, warm up slower) and *Fan stage in normal operation* (default Auto).
- Heat-up rates kept apart for boiler and heat pump, learned anew in heat pump rooms from 0.5 degrees an hour; with the heat pump, pre-heating starts at most 4 hours ahead.
- A room calls the boiler only when its thermostat wants to heat too ([#3](https://github.com/alpha-uniform/thriftherm/issues/3)).
- Away can end as soon as a chosen person comes home (control parameters).
- Optionally the heating pump's electricity in the cost comparison (its power in the boiler options).
- Bathroom drying with a window contact only while the window is open: that is the only way the moisture leaves.
- Default COP values updated.
- Fixed: standing in a warmer room, the heat pump's compressor stood still; a drying then only blew air.

## 0.6.8 — 2026-10-27

- A little less memory (about 0.5 MB).

## 0.6.7 — 2026-10-26

- Fixed: with ZHA, thermostats and room sensors counted as silent when their values did not change for a long time ([#2](https://github.com/alpha-uniform/thriftherm/issues/2)).

## 0.6.6 — 2026-10-26

- Troubleshooting: a room does not reach its target although the correction is at its limit (radiator too small).
- Fixed: the sensor *Boiler heating starts today* sometimes took a start back, and Home Assistant then warned in its log.

## 0.6.5 — 2026-10-23

- Fewer needless burner starts: a rise of the flow waits while the boiler waits out its lockout (S.5–S.8).
- A comfort period that has just ended no longer calls the boiler.
- After a pause without a setpoint the flow no longer jumps up at once.
- New sensor *Boiler heating starts today*.
- The card adds itself as a dashboard resource ([#1](https://github.com/alpha-uniform/thriftherm/issues/1)); with resources in YAML see [Troubleshooting](TROUBLESHOOTING.md#the-thriftherm-card-is-missing-custom-element-doesnt-exist).
- No heat pump warning in *Central heating only* any more.
- Drying with *Setpoint follows the rooms*: the unit no longer stops.
- The learning state shows when the heating curve correction is at its limit.
- Thriftherm is in the HACS default list.
- Default COP values updated.
- Fixed: in *Heat pump only* the boiler also heated while the heat pump was run by hand, waited or did not deliver.
- Fixed: ordinary inverter slow-downs in frost locked the heat pump out for two hours as iced.
- Fixed: the heat pump COP was missing in many cycles when the fan turned slowly between starts.
- Fixed: a heat pump without fan stages did not start; after a failed start the boiler now takes over at once.
- Fixed: a time window such as `00:00-00:00` was empty instead of a whole day.
- Fixed: a window contact that dropped out and came back open did not count as open at once.

## 0.6.4 — 2026-10-18

- The heat pump is set higher first, before the boiler helps; whether it throttles is told by its own reading.
- Info line at a room: the schedule's next change and the trend (hide it with `hidden_controls: [info]`).
- Room card without its head (`header: false`).
- *Back to automatic* also after *Quick heat-up* and *Dry now*.
- Default COP values updated.

## 0.6.3 — 2026-10-16

- New room card for the page of a single room.
- The cards show *Nearly there*.
- A combi boiler's warm start counts as hot water, not as a heating start.
- Troubleshooting: a cold last radiator (pump stage d.19), a room just below target, a missing card.

## 0.6.2 — 2026-10-16

- A dashboard card of its own, coming with the integration (*Add card → Thriftherm*).
- The docs now live in `de/` and `en/`; old links into the main folder lead nowhere.
- Example dashboard easier to read. The README says: everything local, no data sent out.

## 0.6.1 — 2026-10-16

- COP values of a model for units without sensors of their own (so far the Midea PortaSplit).
- An example dashboard to paste in.

## 0.6.0 — 2026-10-15

- Flow learned per room: the room that needs the most sets it.
- A heat-up pace of your choice (off by default).
- Gas split into heating and hot water.
- Drying after a shower, button *Dry now*.
- Changes by hand follow one rule everywhere, with *Back to automatic*. With Better Thermostat its child lock must be off for a turn of the valve to count.
- The heat pump is no preview any more.
- Preview: Versatile Thermostat and a boiler through entities (OpenTherm, say).
- Docs and forms reordered.
- Fixed: the flow minimum was a base, with the learned correction and the demand boost on top.
- Fixed: a schedule block until 23:59 followed by one from 00:00 ended the comfort.
- Fixed: after a restart the boiler heated for a room the heat pump serves.
- Fixed: a thermostat answering in its own steps (21.5 °C sent, 22 °C shown) counted as overridden.
- Fixed: repair notices vanished at every restart and came back.

## 0.5.7 — 2026-10-11

- A heat pump setpoint sent with a fan stage is no longer lost.

## 0.5.6 — 2026-10-11

- A heating curve per load class: one room, a few, the majority.
- Heat pump option *Setpoint follows the rooms* and fan run-on.
- Learning steps grow (1, 2, 4 degrees); a correction that fits stays.
- COP also for a cycling heat pump.
- Fixed: a restart of Home Assistant stopped a running heat pump.

## 0.5.5 — 2026-10-11

- Heating curve learned in level and slope.
- Burner runs recognised by the best sign the system has.

## 0.5.4 — 2026-10-11

- A cycling boiler learns from the rooms.
- Sensor *Boiler burner share*.

## 0.5.3 — 2026-10-10

- Docs only: a combi boiler's warm start, a button for HACS.

## 0.5.2 — 2026-10-10

- Option *Keep hot water ready* (leave it on with a hot water tank).
- Fixed: a thermostat counted as silent after one hour already, now after two and a half.
- Fixed: a comfort period until 23:59 left out the last minute of the day.

## 0.5.1 — 2026-10-09

- Without a gas meter *Gas power* and *Gas energy* are gone: remove dashboard cards that show them.
- The learning state names what paused it.
- Comfort below setback takes the setback down with it.
- Fixed: a schedule block until 24:00 did not run past midnight.

## 0.5.0 — 2026-10-04

Configuration version 3, which older versions do not load: make a backup before the update in case you want to go back.

- Heating along: longer burner runs, fewer starts.
- Rooms call with hysteresis (from 0.3 degrees below target, until 0.1), not in the last half hour of a comfort period.
- Hot water recognised by the boiler's status code.
- Echo filter for Better Thermostat.
- The control keeps going when a sensor, a valve or a command fails.
- Fewer rows in Home Assistant's database.
- Fixed: a cold boiler lost its data after six quiet hours, and the boiler fell back to its knob.
- Fixed: a failed command to one device held up the others.
- Fixed: a price of 0 or a value such as "nan" from another integration stopped the control.

## 0.4.0 — 2026-09-18

- First version that controls a real boiler day and night: discovery through ebusd, district heating or a heat pump alone, a planned return, German and English.

## 0.3.0

- Renamed to Thriftherm (domain `thriftherm`).
