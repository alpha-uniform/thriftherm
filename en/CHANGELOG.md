# Changelog

**English** | [Deutsch](../de/AENDERUNGEN.md)

## 0.6.5 — 2026-10-23

Fewer needless burner starts: a rise of the flow waits while the boiler waits out its lockout, and a comfort period that has just ended no longer calls the boiler for nothing. Also a sensor for the heating starts of the day, the card as a dashboard resource (issue #1), no heat pump warning in *Central heating only*, and 25 defects from a test-driven review. The configuration stays at version 3; everything learned is kept.

### New

- **Sensor *Boiler heating starts today*:** counts the burner's starts for the heating since midnight (hot water does not count) and starts at nought each day. Home Assistant's statistics then show the starts of every day, for instance in a statistics graph with *change* per day. It is kept through a restart.

### Changed

- COP values of the Midea PortaSplit brought up to date (2026-10-11 to 10-21), a little lower than before: at 4 to 6 °C outside 3.30 from 41 points, at 6 to 8 °C 3.63 from 59, at 8 to 10 °C 3.55 from 64, at 10 to 12 °C 3.91 from 49, at 12 to 14 °C 4.13 from 37.
- **Learning state at the limit of the heating curve correction:** when a learning step meets the limit of ±10 degrees for level or slope, the learning state now shows *Heating curve correction at its limit (level or slope, ±10 degrees each)*. Before, it only said that a room does not get there, and the step was dropped silently.
- **HACS:** Thriftherm has been in the HACS default list since 2026-10-21. The setup now describes searching for it in HACS instead of adding a custom repository.

### Fixed

- **Card in *Central heating only*:** with the heat pump unplugged and the mode *Central heating only* or *Summer (heating off)*, the card shows no warning any more and the problem sensor *Heat pump blocked for heating* stays off. In a mode that uses it (*Auto*, *Heat pump only*, *Away*) the warning is back as soon as you switch, and as one line instead of two.
- **Fewer needless burner starts:**
  - When the flow setpoint rises while the boiler runs its pump on after a heating run or waits out its burner lockout (S.5–S.8), the rise now waits until the burner may light again. Before, it lit minutes early, one start more each time (seen 2026-10-22). Frost protection, *Quick heat-up* and falling setpoints do not wait, and after 30 minutes at most the rise goes out anyway. While it waits, *Boiler command* shows *Boiler in its lockout: the rise waits* under *Waiting for*. Boilers without a status code behave as before.
  - When two rooms' comfort ended in the same minute and one schedule switched milliseconds before the other, the second room called the boiler once more for nothing. A comfort end that passed up to two minutes ago now still counts as the end.
- After hours without a setpoint sent to the boiler (boiler data missing, control off, Home Assistant down), the flow could jump from 35 to 60 °C in one step. It now rises at most 5 degrees above the last value sent, then 5 degrees per 10 minutes as usual.
- **Review, second round (test-driven):** 7 more defects fixed, each with a test that failed before:
  - *Heat pump COP:* when the fan turns slower than the lowest point of the airflow curve between two starts, the airflow follows the fan law instead of the whole cycle being thrown away. On the PortaSplit the COP had a value for only about 35 % of the running time.
  - *Defrost detection:* ordinary inverter slow-downs in frost no longer lock the heat pump out for two hours as iced. A compressor that has just stopped by itself no longer counts as a power drop or an iced coil.
  - Learning the heat-up rates counts a room as heated only while a heat source really reaches it.
  - A late valve echo that Better Thermostat adopts right after our setpoint is recognised as an echo instead of being held as the thermostat's answer.
  - When the heat pump fails to start, the boiler takes over the served rooms at once, not only after the wait for the next try.
  - A reset of the heat pump learning made while a command fails is kept.
- **Whole-program review (test-driven):** 18 defects fixed, each with a test that failed before:
  - *Heat pump only:* the boiler heats for frost alone, no longer when the heat pump is run by hand, waits or does not deliver.
  - The flow boost after a heat-up goes back also when the room stops calling a tenth short of its target.
  - Heating-curve learning without the load model counts the demand boost of the shared line: no step at a flow already at its maximum, and a slow room raises the curve.
  - A shower noticed before a restart is still dried after it.
  - A drying no longer makes the heat pump look flat out, which would call the boiler for another room.
  - *Nearly there* names the temperature of the room's own target while it heats along.
  - A heat pump without fan stages gets its setpoint at a start; before, the start ended at the refused fan stage. A unit without *auto* gets back its own stage after a drying or quick heat-up.
  - A window contact that drops out and comes back open counts as open again at once.
  - After the boiler control is switched on again, a block counts only once it has stood a while.
  - A cycle without a room temperature (right after a restart, say) no longer forgets whether the room calls or counts as nearly there.
  - A time window that ends where it starts (e.g. `00:00-00:00`) runs a whole day instead of being an empty window.
  - The boiler's data is read every minute again, not every second minute when a cycle comes milliseconds early.
  - *Back to automatic* for an unknown room is refused instead of silently doing nothing.
  - A heat pump command that went out on a second try is on record as sent.
  - Card: when Home Assistant refuses a stepped value, the card shows the old one again. A heat pump that cannot heat is one warning, not two. After Thriftherm is removed and added again, the card is among the resources again.
- **Card sometimes not found** ([#1](https://github.com/alpha-uniform/thriftherm/issues/1)): *Custom element not found: thriftherm-card*, mostly in Firefox and the app. Thriftherm now adds the card as a dashboard resource itself (*Settings → Dashboards → Resources*), with the version in its address; an entry added by hand for the same file is taken over. If you keep resources in YAML, add it yourself ([Troubleshooting](TROUBLESHOOTING.md#the-thriftherm-card-is-missing-custom-element-doesnt-exist)).
- **Drying with the heat pump under "setpoint follows the rooms":** the unit now gets at least its own reading plus 1 degree, at most 2 degrees above the drying target. Before, it got the drying target only; reading more than that where it stands, it stood, fan and all, and no air reached the bathroom.

## 0.6.4 — 2026-10-18

The heat pump is set higher first, before the boiler helps, also when its intake sensor reads colder than the unit itself. Also an info line at a room, the room card without its head, and *Back to automatic* after *Quick heat-up* and *Dry now*. The configuration stays at version 3; everything learned is kept.

### New

- **Info line at a room** (big card and room card): the schedule's next change with the target after it, e.g. *From 22:00: 17.0 °C*, and the trend once the *Temperature trend* sensor is enabled. The *Target temperature* sensor has the attributes `next_change_ts` and `next_target` for it; hide it with `hidden_controls: [info]`.
- **Room card without its head:** `header: false` (the box *Head* in the editor) shows only the buttons, for instance under a room card of your own with temperature and target.

### Changed

- COP values of the Midea PortaSplit brought up to date (2026-10-11 to 18): at 6 to 8 °C outside 3.86 from 29 readings, at 8 to 10 °C 3.99 from 29, at 10 to 12 °C 4.30 from 33.

### Fixed

- **Heat pump with "setpoint follows the rooms":** whether the unit throttles is now judged by its own reading, no longer by the intake sensor; without its own reading by whether its compressor stands. A unit reading 22 °C at a setpoint of 22 °C and standing, with the intake sensor beside it at 20.3 °C, was taken for working flat out: its setpoint was not raised and the boiler was called at once. A unit found standing while the boiler helps is now set higher as well, and the boiler stops. A unit that stands although its own reading is far below its setpoint (after a protection stop, say) is not set higher: the boiler helps at once.
- **Cards:** *Back to automatic* now appears at a room during *Quick heat-up* and after *Dry now* too, not only after a change by hand. The *Drying mode* sensor shows the attribute `by_hand` for it.

## 0.6.3 — 2026-10-16

A room card, "nearly there" in the cards, and a combi boiler's warm start counted right. The configuration stays at version 3; everything learned is kept.

### New

- **Room card** for the page of a single room: *Add card → Thriftherm room*. Current, target and reason, the target by hand, comfort, setback, quick heat-up, drying and the schedule; the editor chooses what appears.
- The cards show when a room counts as nearly there: *Nearly there, heats again below …* instead of only the schedule. The value comes from the *Heat demand* sensor as the attribute `calls_again_below`.
- Pictures of the cards in the README and the setup, with sample data.

### Fixed

- A combi boiler's warm start (it keeps its hot water warm and reports "heating" meanwhile) counted as a burner start of the heating and its gas as *Gas for heating*. A burn while Thriftherm blocks the heating and the heating pump stands now counts as hot water; with the pump running it really heats and counts as heating.

### Documentation

- Troubleshooting: if the farthest radiator stays cold, the pump may run on its low speed. For Vaillant atmoTEC plus and turboTEC plus the entry explains diagnosis code d.19, with a warning.
- Troubleshooting: a room that stays a few tenths below its target, and a missing Thriftherm card after an update.

## 0.6.2 — 2026-10-16

A dashboard card of its own comes with the integration. The configuration stays at version 3; everything learned is kept.

### New

- **A dashboard card of its own.** *Add card → Thriftherm*: the heat source in one sentence with its cost (a tap compares boiler and heat pump), notes only when something needs attention, one row per room with measured temperature, target and reason (a tap opens the target by hand, comfort, setback, quick heat-up, in the bathroom dry now, and the room's schedule; the editor chooses which of these appear), and the operating mode. It comes with the integration, finds the rooms by itself, and speaks German or English. See setup step 9. The example dashboard stays for anyone who prefers standard cards.
- The room's target temperature sensor names where its measured temperature comes from (`temperature_entity`: the room sensor, or the valve standing in for it), the room key (`room_key`) and its schedule helper (`schedule_entity`) as attributes.

### Changed

- The documentation now lives in the folders `de/` and `en/`, each with setup, guide, how it works, troubleshooting, ebusd and changelog. Only the READMEs stay at the top. Old links to files at the top level no longer work.
- In the boiler form, *Burner on* and *Gas flow* explain when they are needed: a slow pulse meter is better left out of the gas flow, then the boiler's status code counts.
- The example dashboard says in plain words why a room is not heating, shows the measured temperature, an override with its date, and per room the history with Thriftherm's target.
- The README states it plainly: Thriftherm runs entirely locally and sends no data anywhere.

## 0.6.1 — 2026-10-16

### New

- **COP values of a model for units without sensors.** Without a smart plug and sensors at the intake and outlet, Thriftherm could only decide on a general curve. In the heat pump form, *COP values of this model* now offers values measured on the same model; so far the Midea PortaSplit (one unit, 2 to 16 °C outside). What your own unit measures always comes first.
- **An example dashboard** to paste into Home Assistant, in German and English: what heats now and why, the rooms, the boiler, the heat pump and the history. See setup step 9.

## 0.6.0 — 2026-10-15

The boiler's flow is learned per room, a heat-up speed of your choice speeds up slow rooms, and gas is counted apart for heating and for hot water. The heat pump add-on is no longer a preview. Coming from 0.5.7: the configuration stays at version 3, and everything learned so far is kept.

### New

- **Flow learned per room.** In each load class every room has its own correction of the heating curve. The room that needs the most sets the flow; the others' valves take less. A room that does not get there raises only its own value, and no room has to heat on its own for this. The learning state shows the *Room that sets the flow*. The boiler option *A heating curve of its own per room* switches back to one common correction.
- **Heat-up speed** (boiler options, °C per hour, default 0 = off). Thriftherm measures how fast a room really rises and gives it more flow, 2 °C at a time every 20 minutes, until it keeps up. What a room needed is remembered for its next heat-up. More speed burns more gas.
- **Gas for heating and gas for hot water** as two sensors for the energy dashboard. With a gas meter by what the boiler is doing at the time; without one, Vaillant boilers from their own counters (new boiler option: nominal heat input from the type plate).
- **Drying after the shower**, with the strongest fan: it starts once the humidity stops rising. The new button *Dry now* starts it at once; the start by itself can be switched off per room.
- **Changes by hand follow one rule on every device and show at once.** A person or an automation in Home Assistant counts at once, Thriftherm's own commands never do. The change holds for the override duration, on the heat pump too, and shows who made it and until when. The new button *Back to automatic* ends it, and the action `clear_override` without a room ends the heat pump's as well.
- **Turning the knob on a valve counts as a change by hand.** With Better Thermostat its *Child lock* must be off: with it on, Better Thermostat sets the valve back, and the turn does not count.
- **The heat pump is no longer a preview.** Before the boiler helps a room the heat pump serves, the heat pump's setpoint is raised in steps of 0.5 °C, up to 2 °C.
- **Versatile Thermostat can be a room's thermostat (preview).** What it does by its own rule (window, load shedding, safety mode) is not taken for a change by hand; not yet tested with a real installation.
- **A boiler without eBUS through entities (preview).** Choose a number entity for the flow setpoint and a switch for the central heating in the boiler step, for example those of an OpenTherm adapter on an ESP; not yet run on a real boiler.
- **Sorted forms:** the control parameters start with the values of everyday use; the heat pump has a form of its own, *Heat pump: fine tuning*.
- **Documentation sorted anew:** a short README, setup, guide, *How Thriftherm works*, troubleshooting, and a page on reading fewer values from ebusd.

### Fixed

Boiler and heating curve:

- The flow minimum is a floor, not a base: the learned correction and the demand boost no longer come on top of it.
- The curve no longer learns from a room heating up to a wish by hand, nor from a room whose valve is shut. Below the flow minimum it takes no step up.
- "The majority" of the rooms means more than half (with four rooms: one, two, three or four).
- A schedule block to 23:59 followed by one at 00:00, and blocks that follow each other, no longer end the comfort.
- Hot water is shown only while it runs; a boiler on its own knob above the flow maximum is no longer taken for hot water; the hour after hot water no longer stops all learning.
- After a restart the boiler no longer heats a room the heat pump is handling.
- A boiler controlled through entities is handed back with the flow maximum when Home Assistant stops.

Heat pump:

- A fan stage chosen by hand stays, also through a drying or a quick heat-up.
- A restart sends the running unit nothing; *Recent commands* lists only commands that reached it.
- With *Setpoint follows the rooms* the decision reason says "Heat pump runs through" instead of "No room needs heat".
- *Heat pump only* ends a lockout for dear heat at once; an icing lockout runs its full time and is no longer confused with one for dear heat.
- Defrosts are recognised by the indoor coil as well and counted once each; cooling and drying no longer count as defrosts.
- A unit is only sent modes, fan stages and setpoint steps it offers; a command it did not carry out is sent again after 2, 4, 8 … minutes instead of being taken for a change by hand; a failed stop and the "off" after the fan run-on no longer leave the unit in fan only.

Rooms:

- A window found open at the start counts as open at once.
- A setpoint is fitted to the thermostat's own limits; a replaced valve is found without a reload.
- A drying no longer starts and stops every minute with the window open, and it survives a restart.
- The room form shows the temperatures in force.
- A thermostat that answers in its own step (21.5 °C sent, 22 °C shown) no longer counts as overridden; a change made during a cycle, or while the device could not be reached, is no longer undone.
- A room gets no entities for what it does not have; such entities are removed with the update.

Home Assistant:

- The Thriftherm device shows the installed version.
- Repairs no longer vanish and come back after each restart; the gas energy no longer jumps when the calorific value changes; district heating is no longer called "Gas boiler".

## 0.5.7 — 2026-10-11

### Fixed

- A new heat pump setpoint is no longer lost when a fan mode is sent with it.

## 0.5.6 — 2026-10-11

The boiler learning now knows how many rooms call for heat, and the heat pump add-on controlled a real unit for the first time; it remains a preview. The configuration stays at version 3; everything learned is kept, and each load class starts from the common correction learned so far.

### New

- **A heating curve per load class.** One room calling, a few, or the majority: each has its own correction, shown in the learning state, so the flow suits the number of rooms calling. A boiler option goes back to one common correction.
- **Heat pump option *Setpoint follows the rooms*.** The unit gets the highest target of the rooms it serves plus the setpoint offset (1 °C by default), regulates by itself and stays on while the heat pump is the cheaper source.
- **The heat pump's fan runs on before the unit is switched off:** fan only for 15 minutes (new parameter, 0 = off at once).

### Changed

- **The flow/return spread judges the curve only while enough rooms call** (new boiler option, default: half of them). One open radiator returns the water almost as warm as it went out, which used to lower the curve wrongly.
- **Learning steps up grow** (1, 2, then 4 degrees) while the curve keeps going up; a step the other way halves the size.
- **A correction that fits is kept.** After three hours in a load class without a room lagging, it no longer follows every small deviation, so the flow stops going up and down.

### Fixed

- A restart of Home Assistant no longer stops a running heat pump; it keeps running until the served rooms' temperatures are known again.
- Active control takes over a unit it finds heating; one switched on by hand before the control became active was never stopped.
- A heat pump that switches its compressor on and off by itself now gets a COP, measured over whole cycles.
- The COP no longer turns unknown while the intake humidity sensor, which reports on change only, stays at one value.

## 0.5.5 — 2026-10-11

The boiler learning was rebuilt. The configuration stays at version 3, and what was learned so far is kept as the curve's level.

### Changed

- **The heating curve is corrected in level and slope,** both shown in the learning state. A step on a mild day mostly moves the level, one in the cold mostly the slope, so a mild October no longer sets the curve for January.
- **Burner runs are read from the best sign the installation has:** the gas meter, else the boiler's status code, else the pump state. Burns for hot water never count.

### Fixed

- A burner run in progress survives a restart as one run.
- The learning state no longer says "paused" while it learns from a cycling boiler.

## 0.5.4 — 2026-10-11

The learning meets a boiler that cycles, which is what most boilers do in mild weather. The configuration stays at version 3.

### New

- **Sensor *Boiler burner share*:** the share of the last hour the burner ran for the heating.
- **Guide:** enabling a valve's *Last seen* entity in Zigbee2MQTT keeps a healthy thermostat from being reported as silent.

### Changed

- **A cycling boiler learns from the rooms.** A calling room that stays below target and warms slowly raises the curve; cycling lowers it only while every calling room warms up briskly.

## 0.5.3 — 2026-10-10

Documentation only; the code is that of 0.5.2.

### New

- **The hourly burner start at night** (the warm start function of a combi boiler) and how to switch it off at the boiler are in the troubleshooting table.
- **A button opens the repository in HACS.**

## 0.5.2 — 2026-10-10

The configuration stays at version 3.

### New

- **Boiler option *Keep hot water ready*,** on by default as before. Switched off, a combi boiler no longer heats its hot water without a tap open; leave it on with a hot water cylinder.

### Fixed

- A radiator thermostat counts as silent after two and a half hours, not one.
- A comfort window to 23:59 includes the last minute of the day.

## 0.5.1 — 2026-10-09

The configuration stays at version 3; going back to 0.5.0 needs no backup. Without a gas meter the *Gas power* and *Gas energy* sensors, which could only say "unknown", are gone: remove any dashboard card that shows them.

### New

- **Guide section "Without a fixed schedule".**
- **The learning state says which change paused it** and until when.

### Changed

- **Turning comfort below the setback lowers the setback too,** instead of refusing the change.

### Fixed

- A schedule block to 24:00 runs through midnight.

## 0.5.0 — 2026-10-04

Three more weeks of a real boiler controlled day and night, the first cold evenings of the season included: fewer burner starts, hot water told apart by the boiler's own status code, and a control that keeps going when a sensor, a valve or a command fails. The configuration moves to version 3, which an older Thriftherm refuses to load: make a Home Assistant backup first if you might want to go back.

### New

- **Heating along.** While the boiler runs, rooms in their comfort period that are below target get 0.5 °C more, and a preheat due within the hour starts now: longer burner runs, fewer starts.
- **Echo filter for Better Thermostat** (option, on by default). Late radio echoes are no longer taken for a turn of the knob and no longer become two-hour overrides.
- **Provisional return.** While no return is planned, a date picked for *Planned return* becomes a placeholder at 23:59 of that day, which does not pre-heat; the time picked next sets the real return.
- **Each room's heat demand sensor shows whether the room calls the boiler.**
- **Setup guide:** a DHCP reservation for the eBUS adapter and every other network device Thriftherm reads from, and ebusd's own signal as the eBUS signal, not the adapter's connectivity sensor.
- **User guide:** away with and without a planned return, and what a browser in another time zone does.

### Changed

- **Rooms call the boiler with hysteresis:** from 0.3 °C below target until less than 0.1 °C below, so the boiler no longer fires one-minute bursts for the last tenths.
- **A room that hangs just below target counts as reached** after an hour of heating; it calls again 0.5 °C below target or with a new target.
- **No call in the last half hour of a comfort period;** quick heat-up is exempt.
- **Only calling rooms raise the flow.** A room that counts as reached or is at the end of its comfort period no longer adds to it.
- **Bathroom drying runs only with active heat pump control.** The room form refuses it without *Heated by the heat pump* or a humidity sensor, and after its time limit it waits three hours.
- **While the heat pump cools, the rooms it serves stay at setback at most,** also against a turn of the knob, and call no boiler heat. The target shows *Heat pump cooling: radiator at setback*.
- **The operating mode offers only what the installation can heat with:** *Heat pump only* needs a heat pump, *Boiler only* a central heat source.
- **Far fewer rows in the Home Assistant database.** Detail sensors change in coarser steps and start disabled on a new installation (existing ones stay as they are), and long attribute lists are no longer recorded.
- **The forms refuse settings that can never work:** a heating curve lower at −10 °C than at +15 °C, a minimum flow above the maximum, a room renamed to another room's name.
- **The setup stops on a °F installation** with a message; Thriftherm works in °C only.

### Fixed

Boiler and heating curve:

- Hot water is told apart by the boiler's own status code: the pump's overrun after a heating run and a heating run after an eBUS outage are no longer taken for a shower, and a guess is taken back when the boiler says it heated.
- A cold boiler no longer loses its data after six quiet hours, which made Thriftherm stop sending and the boiler fall back to its knob.
- A gas meter that drops off while gas burns no longer keeps the boiler "burning" until its next value.
- A switched-off or missing heat pump no longer pauses the boiler learning.
- Changing the minimum or maximum flow keeps the learned correction of the heating curve; only the curve's two points reset it.
- The flow setpoint rises at most 5 °C per 10 minutes, also when a target is clicked up and down.
- The learning state says why it waits, for example "boiler not heating right now" or short cycling at the flow minimum.

Rooms:

- A failed room sensor is replaced by what the valve itself measures, also behind Better Thermostat, which kept showing the sensor's stale value.
- A radiator thermostat that went silent for an hour no longer calls the boiler, and a repair entry says so; this holds also when the Zigbee bridge marks the valve unavailable.
- A room sensor that reports only on change no longer counts as dead while another sensor of the same device keeps reporting.
- After a restart a room just below target is no longer taken for stalled.
- A temperature changed in the room options applies again, also after comfort or setback was set on the dashboard.
- A deleted room leaves nothing behind; a room created again under the same name no longer takes over its learned values and overrides.

Away:

- A return can be set in the date and time fields of *Planned return*, today too: the date first, then the time; automations should use `thriftherm.set_away`, which takes midnight as it is.
- Changes of the return or of away mode show at once on *Planned return* and the operating mode.
- The target at the return is right across a clock change.

Heat pump:

- A heat pump fault alone no longer degrades the safety state where a boiler or district heating does the heating; it stays listed in the attributes.
- A heat pump command that failed is sent again in the next cycle instead of counting as sent and then as a change by hand.
- Forgetting the heat pump learning keeps an icing lockout.

Home Assistant:

- One failed command to Better Thermostat, the heat pump or ebusd no longer stops the others; it is logged as a warning, and the other rooms still get their setpoints.
- A price of 0 no longer stops the whole control; the price form asks for at least 0.001 €/kWh and a consumption share of at least 1 %.
- Without any room Thriftherm keeps its hands off (safety state *Fallback*) instead of blocking the boiler every two minutes.
- A value like "nan" or "inf" from another integration no longer stops the cycle.
- The operating mode, the control selects, comfort and setback, *Planned return* and *Quick heat-up* stay usable when a cycle fails.
- The control keeps running with every Thriftherm entity disabled, and state is saved when Home Assistant stops.
- *Outdoor temperature (selected source)* shows the last known value that stands in when every outdoor source fails; *Boiler space heating active* is no longer on together with *Boiler hot water active*.
- Changing the kind of heating in the options asks for its prices before anything is saved.
- Disabling Thriftherm removes its repair entries, and removing it deletes its stored learning.

## 0.4.0 — 2026-09-18

First release that controls a real boiler day and night.

### New

- **ebusd auto-detection** of a known boiler (first profile: Vaillant `bai`), the eBUS signal and the gas meter.
- **Installations without an own boiler:** district heating, or a heat pump alone. The heat pump add-on is a preview, not yet tested with a real heat pump.
- **A planned return you can set;** setting one switches to away.
- **Schedules and room temperatures in the UI.**
- **Short cycling lowers the heating curve,** never below the minimum flow.
- **Learned values can be forgotten** per area.
- **German and English,** with a setup guide and a user guide in both; diagnostics and repairs.

### Changed

- **Hot water is never touched** and is kept out of the learning.
- **Bathroom drying runs on the heat pump.**

### Fixed

- No burner run is missed, one-second eBUS dropouts are ignored, and timers survive a restart.
- A thermostat switched off by hand no longer calls the boiler.

### Removed

- **Five settings no code used:** the boiler flow setpoint and storage temperature entities, the heat pump outlet humidity and energy entities, and the fallback room temperature.

## 0.3.0

Renamed to Thriftherm (domain `thriftherm`).
