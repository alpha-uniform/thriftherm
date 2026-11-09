# Changelog

**English** | [Deutsch](../de/AENDERUNGEN.md)

Since 0.7.0 the configuration is at version 4; from 0.5.0 to 0.6.8 it was at version 3.

## 0.7.3 – 2026-11-09

- Fixed: false alarms for silent radiator thermostats (18 in a week, a bathroom left cold for hours); one now counts as silent only when it does not answer a setpoint within 30 minutes (behind Better Thermostat: until a valve behind it reports), is unavailable for 30 minutes, or has not reported for 6 hours.
- Fixed: without central heating the heat pump was off in *Away* and *Summer*, and a room could fall to 4 °C; it now holds the away and frost protection temperatures and heats a room below the frost limit in every mode.
- New: with a boiler or district heating the heat pump is the frost reserve and heats a room of its own that is still below the frost limit after 30 minutes.
- Fixed: a heat pump left at a quick heat-up's 30 °C or a drying's setpoint heated on when its control went to *Plan only* or *Off* or the entry was disabled or deleted; it now goes to its lowest setpoint.
- Fixed: a boiler through entities could stay blocked when its hand-back failed as Thriftherm stopped (gateway away); the hand-back is made up once the entities are back, and a repair notice says so.
- A heat pump in *Heat/Cool* (heat_cool) or another mode of its own is left to you, as in *Auto*.
- The heat pump's run state shows *Defrosting*; after a defrost or a restart the run goes on without a new warm-up.
- Fixed: a heat pump running through with no room short stopped itself again and again as its reading rose; its setpoint now follows the rising reading, a degree below it, as while a room is short.
- Fixed: a thermostat in whole degrees already showing 22 °C got 21.5 °C sent every 5 minutes; it is sent once more and then taken as its answer.
- The *Decision reason* changes far less often (its numbers once an hour at most) and is cut at a whole reason within 255 characters.
- Fixed: after a restart the safety state no longer shows *Degraded* for a minute.
- Fixed: outdoor sensors on ZHA or Zigbee2MQTT that stay quiet on a still night no longer count as missing.
- Fixed: a heat pump plug reporting in kW gave no COP; power is now read in W, kW or mW.
- Diagnostics leave out the person entities (they carry first names); *Reset learning* is for administrators only.
- Checks: Home Assistant switched to °F after the setup stops the control with a repair notice; the eBUS circuit is checked; a room thermostat that is the heat pump or already another room's is refused.
- Learning: heat-up rates are no longer learned too low, a COP step that has not run for a week is checked again, a learning run no longer ends in a lockout, and a jittering gas meter no longer counts gas twice.
- Boiler: a raised minimum flow holds at once, and a failed eBUS command is sent again in the next cycle.
- Card: rooms and warnings stay current while the mode list is open, and it says when Thriftherm is not loaded.
- Default COP values updated, now also for −2 to 0 °C.
- Releases now carry thriftherm.zip, so that the Marketplace built into Home Assistant since 2026.11 can count downloads.

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
