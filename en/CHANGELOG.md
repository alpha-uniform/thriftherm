# Changelog

**English** | [Deutsch](../de/AENDERUNGEN.md)

Since 0.5.0 the configuration stays at version 3: every update since keeps everything learned.

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
