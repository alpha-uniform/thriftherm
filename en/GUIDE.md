# Thriftherm user guide

**English** | [Deutsch](../de/ANLEITUNG.md)

> **Setting up for the first time?** Start with the [setup guide](SETUP.md).

This guide explains how to live with Thriftherm day to day: what the modes do, which temperatures apply when, what Thriftherm learns and how long that takes, and what to do when Home Assistant reports a problem. The [README](../README.en.md) explains the idea and the hardware. Every threshold and time is in [How Thriftherm works](HOW_IT_WORKS.md). When something goes wrong, see [Troubleshooting](TROUBLESHOOTING.md). Terms such as *flow* or *COP* are explained in the section [Terms](#terms).

**Contents:** [How it fits together](#how-it-fits-together) · [Operating modes](#operating-modes) · [Which temperature applies](#which-temperature-applies) · [Changes by hand](#changes-by-hand) · [Room thermostats](#room-thermostats) · [Boiler control](#boiler-control) · [Learning](#learning) · [Heat pump](#heat-pump-optional) · [Services](#services) · [Dashboard sensors](#dashboard-sensors-worth-knowing) · [Repairs](#repairs) · [Terms](#terms)

**Language.** Thriftherm speaks German when Home Assistant is set to German and English for every other language – entity names, states, attributes, the decision reason, dialogs, repairs and error messages. Entity IDs are always English (`sensor.thriftherm_boiler_command`), so automations and dashboards keep working whatever the language.

## How it fits together

| Part | Job | Needs |
|---|---|---|
| Rooms | Work out a target temperature per room from schedule, mode, overrides and protection rules | a temperature sensor per room |
| Room control | Hand those targets to the room thermostats | Better Thermostat per room |
| Boiler control | Give the boiler a flow temperature, or block space heating when no room needs heat | own boiler on ebusd |
| Heat pump (optional) | Measure its COP, compare costs, run it slowly and efficiently | a climate entity and a few sensors |

Every controller has three settings, chosen with its select entity:

| Setting | Meaning |
|---|---|
| **Off** | nothing is planned or sent |
| **Observe only** | Thriftherm works out and shows what it would do, but controls nothing – the default (formerly *Plan only*) |
| **Active** | commands are sent. Only offered after you release it in the options |

Start with *Observe only*, watch the plans for a few days, then switch to *Active* – step by step in the [setup guide, step 7](SETUP.md#7-observe-only-first-then-active).

## Operating modes

Select **Operating mode**:

| Mode | What happens |
|---|---|
| **Auto** | rooms follow their schedules; the cheaper heat source is used |
| **Central heating only** | the heat pump is not used; the boiler or the district heating heats |
| **Heat pump only** | only heat pump rooms are heated; the boiler only for frost protection ([Heat pump](#heat-pump-optional)) |
| **Off (frost protection only)** | no space heating; hot water keeps working; frost protection stays |
| **Away** | all rooms are held at the away temperature; with a return time they are pre-heated in time |

### Away with and without a return

- **With no end:** choose *Away* as the operating mode. *Planned return* stays empty. The rooms stay at the away temperature until you choose *Clear away* or another mode.
- **Until a point in time**, e.g. today 22:00 or 3 October 12:00: set *Planned return*, first the **date**, then the **time** (in the entity's dialog or in an entities card). That switches to away until then, and the rooms are pre-heated in time for the return.
- **A date alone is a placeholder:** without a time the return stands at 23:59 of that day for the moment. Away then ends at 23:59, but nothing is pre-heated. The attribute *Provisional (23:59 placeholder)* (`provisional`) of *Planned return* is `true` for as long.
- **Choosing *Away* again:** if *Away* with a return is already on, choosing *Away* again changes nothing. For away with no end, first choose another mode and then *Away* again, or call `thriftherm.set_away` with `clear_return_time: true`.
- **Back home:** *Clear away* (`thriftherm.clear_away`) ends the absence and the return. Once the return time is reached, Thriftherm goes back to Auto by itself.
- **Return by presence:** in the options under *Control parameters* choose one or more people (*Away ends when one of these people comes home*). When one of them comes home, away ends as at a planned return: back to Auto, the rooms heat up. Whoever is still home when *Away* is switched on ends it only after leaving and coming back. Leaving never switches to away ([rules](HOW_IT_WORKS.md#return-by-presence)).
- **From automations:** `thriftherm.set_away` with `return_time`, or with `clear_return_time: true` for no end. Unlike `datetime.set_value` it reports an error when something fails.

After each entry check *Planned return* and *Provisional*, above all when your browser runs in another time zone than Home Assistant: the fields work in browser time. What is refused, how to be really back at 23:59 and what happens with other time zones: [Away and planned return](HOW_IT_WORKS.md#away-and-planned-return).

## Which temperature applies

For every room the first rule that applies wins:

1. **Override** (action `set_override` or a [change by hand](#changes-by-hand)) – until it expires.
2. **Off (frost protection only)** – frost protection temperature only.
3. **Away** – away temperature (default 15 °C). Before a return with a time the room is pre-heated in time (not for the provisional 23:59). If the air gets too close to its dew point (default 3 degrees) the target is raised against damp ([damp guard](HOW_IT_WORKS.md#damp-guard)).
4. **Schedule** – comfort temperature inside a schedule block, setback temperature outside. Before a block starts the room is pre-heated so it is warm *when* the block begins.

Two protections apply on top, always:

| Protection | Default | Behaviour |
|---|---|---|
| Frost protection | 7 °C | If a room falls below it, the boiler heats immediately – also in the mode *Off* – until the room is 1 degree above; its thermostat is given a target half a degree above that meanwhile. No room target is below this temperature, be it a setback, the away temperature or an override. In a heat pump room without central heating the heat pump does that; with central heating it steps in if the room is still below after 30 minutes ([Heat pump](#heat-pump-optional)). |
| Window open | 90 s grace | Heating in that room pauses while a window contact is open. A contact already open when Thriftherm starts counts at once. |

**Comfort and setback temperatures** are number entities per room (*Comfort temperature*, *Setback temperature*); you can change them on the dashboard. The setback is never higher than comfort: turn comfort below it, and the setback follows. The room options show the values in force; if you change either of them there, both values from the form apply.

### Schedules

Each room can use a Home Assistant **schedule helper** (*Settings → Devices & services → Helpers → Schedule*). Its blocks are the comfort periods. A block with a `temperature` attribute uses that instead of the comfort temperature. Rooms without a helper use the time ranges from the room options.

### Without a fixed schedule

If you would rather adjust by hand than by the clock:

- **Comfort around the clock:** time range `00:00-23:59` on weekdays and weekends, or a schedule helper with blocks from 00:00 to 24:00 (or to 23:59). Both count as continuous, across midnight as well.
- **Changing the temperature:** change the room's *Comfort temperature* number, for example from a thermostat card or a template thermostat. The setback follows when you go below it.
- **Away/home:** `thriftherm.set_away` (with `clear_return_time: true` open-ended, with `return_time` including pre-heating) and `thriftherm.clear_away`. The comfort temperatures stay as they are.

This even suits the heating curve learning, which learns from long, even heating runs. A change pauses the learning for 20 minutes; a room whose target changed does not count for 90 minutes ([Learning](#learning)). A target set by hand teaches the curve too, once the room is within 1 degree of it and holds there.

### Pre-heating

Thriftherm starts early enough for the room to be warm when a comfort block begins. For that it takes the gap to the target, the room's learned heat-up rate for the heat source that will heat it, and a margin (20 minutes by default). The room's *Target temperature* sensor shows the planned start in *Pre-heating starts*. Formula and starting values: [Pre-heating](HOW_IT_WORKS.md#pre-heating).

**Short blocks:** if a room needs heat only briefly, say a bathroom from 06:00 to 06:30, *Longest preheat* in the room options limits how early it begins (empty: at most 4 hours, 0: not at all; 45 to 60 minutes make sense; with the boiler also longer than 4 hours). Before the return from *Away* the 4-hour limit does not apply. Set, the room also calls the boiler in its block up to its end, or a short block would stay cold (a new call begins up to 5 minutes before the end). While it wants heat, the heating curve learns nothing.

## Changes by hand

If anyone but Thriftherm changes a setpoint, that is a change by hand: in Home Assistant (dashboard, app, voice assistant), by an automation or a script, or at the device (the knob on a valve, the heat pump's remote, the maker's app). Home Assistant itself says who it was ([how Thriftherm reads that](HOW_IT_WORKS.md#changes-by-hand)).

- **It holds for a fixed time** (*How long a change by hand is respected*, 120 minutes by default). On a room thermostat it becomes the room's override; on the heat pump Thriftherm leaves the unit alone for that long. A further change during that time renews it.
- **It is shown at once:** on the room's *Target temperature* sensor (*Override until*, *Override by*) and on the heat pump's command (*Changed by hand, respected until*, *Changed by hand by*).
- **It ends by itself** or with the button *Back to automatic* (action `thriftherm.clear_override` without a room). That ends every override, every quick heat-up and the heat pump's time in hand at once.
- **No change by hand** are a command the heat pump did not carry out (it is sent again), a thermostat that rounds to whole degrees, and a thermostat that somebody merely switches off.
- **The knob on a valve:** whether a turn counts depends on the room thermostat in front of it ([Room thermostats](#room-thermostats)).

While somebody cools, dries or ventilates with the heat pump, or runs it in any mode other than heating or off (such as *Auto* or *Heat/Cool*), Thriftherm stays out for as long as that mode is on. A boiler through entities ([setup guide, step 4.3a](SETUP.md#43a-a-boiler-without-ebusd-through-entities-preview)) has no change by hand: while the control is active the number and the switch belong to Thriftherm; switch to *Observe only* to set them yourself.

## Room thermostats

Thriftherm gives every room a target. It reaches the valves in one of three ways: directly to the valves (preview), through [Better Thermostat](https://github.com/KartoffelToby/better_thermostat) or through Versatile Thermostat (preview). Setting it up: [setup guide, step 5](SETUP.md#5-valves-or-thermostats-per-room-optional).

For every room thermostat: one you switch **off** by hand is left alone by Thriftherm; its valve is shut, and the room does not call the boiler. One that cannot be reached for a short while still counts, because its valve keeps regulating on its own.

### Better Thermostat

- **Not heating:** when the thermostat reports that it is not heating right now (valve shut), the room does not call the boiler, even if Thriftherm would ([details](HOW_IT_WORKS.md#when-a-room-calls-the-boiler)).
- **The knob on a valve:** whether a turn counts is up to the room's *Child lock* switch in Better Thermostat. With it on, Better Thermostat sets the valve back, and the turn does not count. With it off, the number the valve shows after the turn becomes the room's target. Because of Better Thermostat's correction that may be another value than you think ([example](HOW_IT_WORKS.md#turning-the-knob-of-a-valve-behind-a-room-thermostat)).
- **Overrides although nobody turned anything:** a late radio echo is caught by the *Echo filter* (Options → *Control parameters*, on by default; [details](HOW_IT_WORKS.md#echo-filter)).

### Valves driven directly, without a room thermostat (preview)

> **Preview.** In use on Sonoff TRVZB/TRVZBT with Zigbee2MQTT since 2026-11-15; other valves (ZHA, Shelly and more) have only been tested with simulated ones so far.

Instead of a room thermostat you can give the room its radiator valves (room options → *Radiator valves, driven directly*). You then need no Better Thermostat; the form refuses a valve an active Better Thermostat entry still drives. A room has either a *Thermostat* or valves, not both.

- **What the valve gets:** the room's target, and while the room wants heat (it calls for the boiler or heats along) enough above it to stay open safely until the call ends: the larger of its opening threshold and "tolerance above the target" + 0.2 degrees. With district heating, without a boiler or while Thriftherm only observes the boiler, it gets the target without a margin and controls by the room by itself, as behind Better Thermostat. The [lead](#lead-at-the-thermostat) does not apply here, it is for thermostats with a control of their own. The open valve is on purpose: the flow temperature controls the heat, and the boiler runs more evenly instead of cycling against a shut valve ([why](#lead-at-the-thermostat)). Besides,  as it can take it, the room sensor's temperature in its external temperature input (Sonoff TRVZB/TRVZBT, Danfoss Ally, Bosch, Aqara E1) or a calibration offset (most other Zigbee valves). All others with a current temperature (Homematic IP, Z-Wave, Matter and more) get a corrected setpoint: a valve reading 1 degree more than the room gets 1 degree more. Thriftherm tells what a valve can do by the entities of its device; no model profile is needed.
- **Window open:** after the *window open grace period* (90 seconds by default) Thriftherm switches the valves off and, once the window is shut, on again and to the target; Better and Versatile Thermostat do the same. If you do not want that, set *Control parameters → Windows and preheating* to *At the frost limit*. A valve you switch off yourself stays off. **Mind:** whether a valve keeps its own frost protection while off depends on the model. The Sonoff TRVZB/TRVZBT then holds its *frost protection temperature*. On Tuya and Avatto valves frost protection is a switch of its own that has to be on. For other models it is not documented; when in doubt choose *At the frost limit*.
- **Room sensor stops reporting:** the valve goes by its own sensor until the room sensor reports again.
- **What is set at the valve stays:** its child lock, its own window detection, its weekly schedule and its mode are never written. If you set its sensor selection to its own sensor in Home Assistant, it stays so; the valve then gets an offset.
- **Knob:** a turn at the room's first valve is a change by hand as at a room thermostat, taken as the difference to the target. Further valves of the room get the same setpoint; a turn at them does not become the room's override yet and is overwritten after five minutes.
- **Valve exercise:** a valve that has not opened for a week (Sonoff TRVZB/TRVZBT: 84 hours) is opened and shut twice, about two minutes each, so that it does not stick; that begins between 10 and 18 o'clock only, and not while the boiler heats, the room calls or its window is open.
- **Apple Home and Siri:** without Better Thermostat the room has no thermostat in Apple Home. Thriftherm brings a *room climate* of its own, as for every room (see *Room climate for Apple Home and Siri*) (`climate.thriftherm_<room>_room_climate`): heat or off, the setpoint is the room's target, the current temperature the room sensor. A setpoint you set there (or by Siri) counts as a change by hand; *off* holds the room at the frost limit until you choose *heat* again. Add this room climate to the HomeKit Bridge and leave the valves themselves excluded.
- **Set the opening threshold small:** many valves open only some way below their setpoint (Sonoff TRVZB/TRVZBT: *temperature accuracy*, −1 out of the box). Thriftherm allows for it. The smaller it is, the less the room swings; −0.4 has worked well.
- **After switching from Better Thermostat:** what the room has learned stays. As the valve now opens differently than under Better Thermostat, the learned lines of the heating curve adjust in the first days; that is no fault.
- **Forcing a way for a test:** *How the valves are controlled* is set to *Automatic* (the most exact way the valve can do). For a comparison the offset or the corrected setpoint can be forced per room; the valve then goes on its own sensor. Back on *Automatic* it gets the room temperature again.
- **When Thriftherm goes:** when you put the room back on Better Thermostat, take a valve out, switch room control away from *Active*, disable or delete Thriftherm or Home Assistant stops, every valve gets back what it had: its own sensor, its old offset and the opening it had before. Where it still holds a setpoint of Thriftherm's (a margin, the frost limit, the exercise), it gets the room's target; switched off by the window only, it is switched on again. A setpoint or an off of yours stays, the child lock is never touched. Before going back to an older version, put the rooms back on Better Thermostat first; an older version cannot do this.

Rules and times: [Valves driven directly](HOW_IT_WORKS.md#valves-driven-directly).

### Versatile Thermostat instead of Better Thermostat (preview)

> **Preview.** Built from Versatile Thermostat's documentation and tested with imitated states, not yet with a real installation.

A room's thermostat can also be the climate entity of [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat). Thriftherm only writes its target temperature.

- **Its own rules stay with Versatile Thermostat.** When it lowers the target by itself because a window is open, it sheds load or is in safety mode, Thriftherm does not take that for a change by hand and does not write against it; Versatile Thermostat restores the requested value afterwards. The room shows *The thermostat applies a rule of its own* meanwhile.
- **If it switches itself off with a window open,** that counts like a thermostat switched off: the room does not call the boiler.
- **Leave Versatile Thermostat's "central boiler" feature off** while Thriftherm controls the boiler. Otherwise two controllers switch the same boiler.
- **Presets (comfort, eco, boost) and presence** in Versatile Thermostat change the target as a request. Thriftherm reads that as a change by hand and turns it into an override. So keep the schedule in Thriftherm.
- **The knob on a valve:** without *follow underlying temperature change*, Thriftherm takes a turn over itself after five seconds ([details](HOW_IT_WORKS.md#turning-the-knob-of-a-valve-behind-a-room-thermostat)).
- Thriftherm finds the valves behind Versatile Thermostat by itself and reports one that goes silent.

### Room climate for Apple Home and Siri

Every room has a thermostat of Thriftherm's own, `climate.thriftherm_<room>_room_climate`. Its setpoint is the room's target (without the lead and without heating along), its current temperature the room sensor, its mode *heat* or *off*.

- A setpoint set there counts as a turn on the room's thermostat: a change by hand for the override time. Thriftherm passes it on to the thermostat (Better Thermostat, say), and its answer is no second change. Turn the thermostat itself afterwards, and the newer one counts.
- *Off* holds the room at the frost limit until you set it to *heat* again; *heat* brings it back to its schedule.
- It is passed on only while Thriftherm controls the rooms actively; in *Observe only* the setpoint changes Thriftherm's planning only.
- **HomeKit Bridge:** since 0.8 every room has this room climate. If a room also has Better Thermostat (or the bridge exposes the valves driven directly), it shows twice in Apple Home. Leave one of them out of the bridge, best Better Thermostat's or the valves: then Siri sets Thriftherm's targets, and Apple Home shows the room's target without the lead.

### Lead at the thermostat

A thermostat with a control of its own (Better Thermostat, Versatile Thermostat, a valve on its own) throttles its valve shortly before its setpoint. Thriftherm, though, calls the boiler only below the target and lets it go shortly before (by default from 0.3 degrees below to 0.1 degrees below; set under control parameters → expert: *tolerance below the target*, *tolerance above the target*). So that the valve is open meanwhile, the thermostat gets the target plus the lead (0.5 degrees by default) **for as long as the boiler runs for the room**, then the target again. It still shuts at the target; the room gets no warmer.

**Why the valve should be open:** with Thriftherm the flow temperature controls the heat, not the valve. With the valve open while the boiler runs for the room, the heat reaches the room, and the boiler runs longer and more evenly at a lower flow; a valve throttling meanwhile has it heat against a shut valve, and it cycles. Against a boiler that delivers far more than the rooms need, this alone does not help. With district heating and in *Observe only* the valve controls by itself.

- It also applies while a room heats along; then the higher of the two counts, not both.
- Not with district heating, with the boiler blocked, with a window open or when the thermostat reports it does not heat.
- The room's target in Thriftherm and on the card stays the real target. At the thermostat (and in Apple Home, where the thermostat itself stands there instead of Thriftherm's room climate) you see the higher value while the room calls.
- Turning the knob of the valve or thermostat meanwhile counts for the room: turned from 21.5 to 22 means 21.5 for the room. A value you set in Home Assistant or Apple Home (or an automation does) counts as it is.
- Only while Thriftherm runs the boiler actively, not in *Observe only*. A thermostat in whole degrees gets the lead in whole degrees only (0.5 has no effect there, 1 does).
- Change it under options → *Rooms* → room → *Edit*: 0, 0.5 or 1 degree. It does not apply to rooms whose valves Thriftherm drives directly.

### Silent thermostats

A radiator thermostat counts as silent when it has not answered a setpoint within half an hour (behind Better Thermostat: until a valve behind it has reported), when it has been unavailable for half an hour, or after six hours without any report. With Better Thermostat the real thermostat behind it is watched. Values merely repeated after a restart of MQTT or Home Assistant are no report; a thermostat that comes back with another setpoint after a restart or a change of batteries does not count as turned by hand and gets Thriftherm's setpoint again. A silent thermostat does not call the boiler and takes no new setpoints; a [repair](#repairs) appears. If Thriftherm takes a healthy thermostat for silent, enable its *Last seen* entity in Zigbee2MQTT; with ZHA there is nothing to do ([why](HOW_IT_WORKS.md#outages-and-resending)).

### Without smart thermostats

A room only needs something that measures it: a temperature sensor, a valve or a thermostat. Without smart valves the sensor is enough. Everything else still works: demand per room, heating curve, blocking when no room needs heat, learning and frost protection. The room's *Thermostat plan* then gives the reason *No thermostat*, and nothing is written to a valve: the manual valves stay open far enough and the flow temperature regulates. You can add smart thermostats later, room by room; mixing both is fine.

## Boiler control

For an own boiler on ebusd. A boiler without eBUS can be controlled, as a preview, through a number entity (flow setpoint) and a switch (central heating on/off) ([setup guide, step 4.3a](SETUP.md#43a-a-boiler-without-ebusd-through-entities-preview)). Everything below holds for both ways; where `SetMode` is mentioned, Thriftherm writes the number and the switch there.

- **Flow temperature:** from a two-point heating curve (default 55 °C at −10 °C, 30 °C at +15 °C). On top comes what the room that needs the most asks for: its learned correction ([Learning](#learning)), up to 8 degrees more while it is far below target, and its extra for the [heat-up speed](#heat-up-speed). The flow never leaves the configured minimum and maximum, and it does not jump ([flow in detail](HOW_IT_WORKS.md#flow)).
- **The boiler's own limit:** if the boiler cycles on its least power far below the flow sent, Thriftherm does not raise the heating curve but sends more step by step while a room does not get there; up to 55 °C for a *Condensing boiler*. What was enough is remembered. The *Learning state* shows the extra in the attribute `boiler_limit_k` ([details](HOW_IT_WORKS.md#the-boilers-own-limit)).
- **Calling the boiler:** a room calls the boiler when it is noticeably below target, until it is almost there. Not in the last half hour of its comfort period, unless the boiler helps the heat pump or the room has a *Longest preheat* (then up to 5 minutes before the end). The attribute *Boiler call* on the room's *Heat demand* sensor shows the state ([thresholds](HOW_IT_WORKS.md#when-a-room-calls-the-boiler)).
- **Heating along:** while the boiler runs anyway, other rooms heat along (reason *Heating along* / *Pre-heating early*), in the setback up to their setback target, never past it (*Heating along up to its setback*). That means longer burner runs and fewer starts. A Sonoff TRVZB/TRVZBT driven directly opens only in part then (30 % or 60 %, by how little the room lacks) and gets back the valve opening degree you set afterwards ([details](HOW_IT_WORKS.md#heating-along)).
- **Heating is blocked** (`disablehc`) when no room needs heat. Hot water is not touched: the hot water setpoint always goes out as "no value", so the boiler keeps following its own knob. The one exception is yours to choose: with *Keep hot water ready* switched off in the boiler options, the boiler no longer heats for hot water without a tap open (the hourly keep-warm of a combi boiler). Leave it on with a hot water cylinder.
- **Outages:** after a restart Thriftherm waits up to five minutes for the room sensors. If the eBUS signal stays gone for two minutes, Thriftherm stops sending. If Home Assistant stops, the boiler is back on its knob after 9–16 minutes. The knob always stays the upper limit, so set it to the highest flow temperature you want to allow ([setup guide, step 6](SETUP.md#6-at-the-boiler)).
- **Hot water:** Thriftherm recognises by itself when the boiler makes hot water (*Boiler hot water active*), and learning pauses for an hour then ([how](HOW_IT_WORKS.md#hot-water-detection)).
- **Read requests only:** to keep flow and return fresh, Thriftherm has ebusd read them more often. Nothing is written to the boiler for that ([details](HOW_IT_WORKS.md#fresh-readings)).

Sensor *Boiler command* shows the plan (`Heat`, `Heating blocked`, …), the exact `SetMode` payload, the reason, what it is waiting for, whether the heating pump runs and the boiler's status code (`S.xx`, as on its display).

**Why the minimum depends on the boiler type.** A condensing boiler gains from a low flow: below a return of about 56 °C its flue gas condenses and yields up to 11 % more, so about 30 °C is a good minimum. A non-condensing boiler never condenses and saves little there; with short burner runs the heat may not even reach distant radiators. On the tested non-condensing boiler 30 °C left the last radiator cold, 45 °C works: start there with about 45 °C. The setting is *Minimum flow temperature* ([setup guide, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)).

### Heat-up speed

The heating curve is there to hold a temperature. When a room is turned up, heating up on the curve alone can take long. How fast a room is to warm up is set in the boiler options: *Heat-up speed (°C per hour, 0 = off)*, 0 by default.

- A room that calls 1 degree or more below its target (after a raise, after the setback or an open window) gets an extra of its own on the flow while it heats up. This only acts with active boiler control.
- Every 20 minutes Thriftherm measures how fast the room rises. Too slow: the extra grows. Much too fast: it shrinks. Close to the target it gets smaller.
- What a room needed is where that room's next heat-up starts.
- While a room heats up on an extra, the heating curve learning pauses (reason *a room heats up on an extra*).

More speed burns more gas. Which value suits:

| Value | Effect |
|---|---|
| **0** (default) | off, the flow as before: heating curve, learned correction and up to 8 degrees more while a room is far below target. Enough when the rooms pre-heat by schedule or nothing is in a hurry |
| **0.5** | gentle: only steps in when a room almost stalls while heating up (less than 0.4 degrees an hour). Costs least |
| **1** | brisk: about a degree an hour. A good start when rooms take too long after being turned up |
| **2** | fast: two degrees an hour. Needs a high flow and the most gas; with small radiators or a low maximum it is not always reached |

The *Learning state* shows *Heat-up extra per room* (the extra while a room heats up, else where its next heat-up starts) and *Rooms heating up on an extra*. Steps, limits and when it measures: [Heat-up speed](HOW_IT_WORKS.md#heat-up-speed).

### Why the pump mode matters

With the factory pump mode the pump runs only while the burner fires, plus a short overrun (Vaillant: d.18 = 0, "post-run"). When the rooms take little heat, the boiler stops again within seconds. The water has then only circled through the boiler's bypass: **the radiators far down the circuit stay cold**, although the boiler cycles all day.

With pump mode **permanent** (d.18 = 1) the pump runs whenever heating is released and **stops when Thriftherm blocks heating**. That costs about 33 W, only while heating. On the tested boiler this was the cause of a cold bathroom ([measured](HOW_IT_WORKS.md#ebusd-pump-and-status-values)). To check and change it: [setup guide, step 6](SETUP.md#6-at-the-boiler).

### ebusd: pump and status values

Whether the heating pump runs and which status code (S.xx) the boiler shows, Thriftherm reads from the ebusd values `WP` and `Statenumber`. They only reach Home Assistant if they pass the name filter of the ebusd MQTT integration ([setup guide, step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number)). Without them Thriftherm falls back to `Status01` ([why that is not enough](HOW_IT_WORKS.md#ebusd-pump-and-status-values)).

## Learning

Thriftherm keeps learning from everyday use. You do not have to do anything; at first it uses starting values that already heat well enough.

| Area | What is learned | From |
|---|---|---|
| Boiler | heating curve correction, per room and per load class (one room, a few or the majority call) | flow/return spread, how fast rooms warm up, and short cycling |
| Rooms | heat-up rate per room and heat source (degrees an hour) | real heating periods with the window closed |
| Rooms | cool-down coefficient per room (1/h) | stretches without heating with the window closed |
| Rooms | heating power per room (degrees an hour without losses) | heat-up rate plus the losses during that period; unlike the raw rate it holds in winter too |
| Heat pump | COP over outdoor temperature (COP map) | heat and electricity of all of the heating time |

Details on every area: [Learning](HOW_IT_WORKS.md#learning).

### How long learning takes

Thriftherm only learns while it really heats. Until something is learned, it heats with starting values: the heating curve you set, a heat-up rate of 0.8 degrees an hour (heat pump 0.5) and a COP curve from the data sheet. The times below are estimated from the waiting times and thresholds and hold for a heating season with ordinary weather; they have not yet been measured over a whole season.

| What | First value | Stable | Good to know |
|---|---|---|---|
| Heating curve: level | on the first heating day, at the earliest 1.5 to 2 hours after the last setpoint change | after about 1 to 2 weeks of heating season | only with *Active*. A curve too low rises by up to 10 degrees on the first day; one too high sinks at most 4 degrees a day and takes a few heating days |
| Heating curve: slope | on the first days well below 0 °C | after the first real frost; in a mild winter only in the next season | until then the level carries it |
| Heating curve per load class | the most frequent load class first, often "one room" | rare load classes such as "the majority" after weeks to months | until then a room takes its value from the load class with more rooms or from the shared correction |
| Heat-up rate per room | with the boiler from the third heat-up period, so after 2 to 4 days with a setback; before that the starting value counts along | after 1 to 2 weeks | needs heat-up periods of at least 30 minutes and 0.5 degrees, or 2 hours and 0.2 degrees. Heat from elsewhere (shower, sun) does not count. A room without a setback keeps the starting value |
| Cool-down coefficient per room | after the first night with a setback | after about a week | only when it is at least 5 degrees warmer inside than outside |
| COP map | an outdoor temperature band after 3 hours of running in it, with suitable weather after 1 to 3 days | the frequent bands (about 0 to 12 °C) after 2 to 4 weeks; frost bands only in frost | bands in which the central heating is cheaper learn only through *learning runs* (one hour a day), which takes 1 to 3 weeks. Without learning runs they stay at the estimate |

How fast a cycling boiler lowers its curve depends on whether its rooms reach their target again soon after a short call; this rule is still young. How the times come about: [How long learning takes](HOW_IT_WORKS.md#how-long-learning-takes).

### How the heating curve learns

- **Level and slope:** the heating curve correction has two parts. The **level** holds at every outdoor temperature, the **slope** mostly in the cold, as a heating curve is set by hand. What is learned in autumn therefore hardly touches the winter.
- **Per room and load class:** a small radiator in a cold room needs a hotter flow than a large one, and a few open radiators need another flow than many. So every room has a correction of its own, kept apart for three load classes: one room calls, a few, the majority. The room that needs the most sets the flow ([load classes and values per room](HOW_IT_WORKS.md#load-classes-and-values-per-room)).
- **Careful:** the boiler learns only in *Active* mode, in small steps with waiting times and daily allowances. Learning pauses for hot water, after a setpoint change (20 minutes), for a quick heat-up, a drying, a heat-up on an extra, a preheat with *Longest preheat*, a boiler fault, without an outdoor temperature and while the safety state is not OK ([guards against learning nonsense](HOW_IT_WORKS.md#guards-against-learning-nonsense)).
- **While the boiler cycles,** only the rooms' progress counts; a step that brings nothing is taken back ([cycling](HOW_IT_WORKS.md#cycling)).
- **A gas meter is not needed.** Thriftherm also tells the burner by the boiler's state ([telling the burner](HOW_IT_WORKS.md#telling-the-burner)).

**What has been tried.** The control itself – blocking, flow, safety – is verified on the maintainer's boiler. The learned corrections have so far seen outdoor temperatures down to 2 °C, no frost yet; watch them in your first frosty weeks.

**What you can set** (boiler options): *Share of rooms …* – the spread judges the curve only while at least this share of the rooms that can take heat right now calls (half by default). Otherwise only the progress of the calling rooms counts.

### Where you see it

The sensor *Learning state* shows

- the phase (*Learning*, *Refining* or *Waiting for a heating run*), the current correction with level and slope, why it waits and the last reset. It learns only while the boiler heats; otherwise it says *Waiting for a heating run*, which is no fault. When a setpoint change paused it, it also shows until when and in which room;
- *Rooms calling for heat* and whether the spread judges right now;
- the *Load class now* and the *Room that sets the flow*;
- under *Heating curve correction per load class and room*, for each class and room (entries such as `few:bathroom`), the correction, level, slope, the hours of operation and how often a room lagged in them.

The sensor *Boiler burner share* shows the share of the last hour the burner ran for the heating, and the number of starts; for now it only records. *Boiler heating starts today* counts the burner's starts for the heating since midnight. A start counts once its burn has ended, as only then is it certain that it was not for hot water. Home Assistant's statistics then show the starts of every day. They are counted from the best sign there is: the gas meter, else the boiler's status number (eBUS), else a flame or pump signal; the attribute *Counted from* names it. With the status number every burn ebusd sees counts: Thriftherm works out the change as the burner goes on or off, not with the next cycle a minute later. Without a gas meter, runs shorter than ebusd's polling may still be missed (attribute *Short burner runs may be missed*). *Boiler heating active* shows the burner by the same sign.

### Forgetting what was learned

After a change to the installation old values no longer fit.

- **By hand:** Options → *Reset learning*, tick the areas (and rooms), or call `thriftherm.reset_learning` (as an administrator). With rooms, only these rooms' heating curve values of the boiler go, for example `scope: ["boiler"]`, `rooms: ["bathroom"]`.
- **Automatically:** when you change a setting a learned area depends on, only that area is forgotten:

| Change | Forgotten |
|---|---|
| heating curve | boiler correction |
| heat pump entity, intake/outlet sensor, airflow curve, fan stage in normal operation, installation room, served rooms | COP map and learning runs (the duct factor only rescales the map) |
| a room's temperature sensor or thermostat | that room's heat-up rate |

Run state, lockout times and manual holds are never reset, so a reset cannot make a unit cycle.

## Heat pump (optional)

It has been tried on a Midea PortaSplit, icing and defrosting included; other units not yet.

- **COP:** Thriftherm measures it from the air the heat pump takes in and blows out, and from its electricity. When it cycles, the whole cycle counts, off time included (attribute *COP over the last on/off cycle*); that value is only shown. The cost goes by the settled COP or the COP map ([measuring the COP](HOW_IT_WORKS.md#measuring-the-cop)).
- **Cost:** the heat pump runs slowly at a high COP. It is compared with the price of central heat: Thriftherm switches to the heat pump when its COP is 10 % above the *Break-even COP*, and back when it falls 5 % below (fine tuning, *When it runs*). The gap prevents constant switching back and forth. If the *Power of the heating pump* is set, Thriftherm counts the pump's running time and the boiler's heat and adds the pump's electricity per kWh of heat to the boiler price (at most 0.06 kWh), so that a heat pump that takes over saves that too. For district heating the **allocation key** matters: only the consumption share follows your meter. A saved kWh therefore saves less than its price, and the heat pump must reach a higher COP to pay off ([With district heating](SETUP.md#with-district-heating)).
- **Location and ducts:** without ducts the warm air stays in the room the unit is in. Configure the installation room and the duct factor (1.0 = no ducts); rooms it cannot reach get a repair notice. The duct factor corrects the airflow, not the heat: what the ducts give off on the way stays in the home.
- **Locked out** is the heat pump below its minimum outdoor temperature (*Minimum outdoor temperature for heating (data sheet)*, default −10 °C), for icing or frequent defrosts, when somebody cools with it, and when its settled COP stays below the *Break-even COP* for a while, so that it heats dearer than the central heating. How long the lockouts for icing and inefficiency last is set with *Lockout after icing / inefficiency* (two hours by default). In the mode *Heat pump only*, and while a boiler fault holds the boiler back, there is no lockout for inefficiency ([lockouts](HOW_IT_WORKS.md#lockouts)).
- **Cooling and heating never overlap:** while it cools, the rooms it serves keep their radiators at the setback temperature and call no boiler heat. Frost protection still applies.
- **Frost protection:** without central heating the heat pump holds the away temperature in the mode *Away* and the frost protection temperature in the mode *Off*, and heats a room below the frost limit in every mode, window open and frost outside included, until it is 1 degree above. With a boiler or district heating it is the reserve: a room of its own still below the limit after 30 minutes is heated by it the same way, even if that is dearer. Icing, error codes and the safety state *Fallback* still stop it, and in *Observe only* nothing is sent.
- **Quick heat-up** (button per room or `thriftherm.boost`) runs it at full power for a limited time.
- **Fan:** Thriftherm sets it only on a start, for a drying or a quick heat-up, and back afterwards, to the *Fan stage in normal operation* from the fine tuning (default *Auto*). A higher stage carries more heat into the rooms but is louder. A newly chosen stage reaches the heat pump also in the middle of a run, if it still has the old one; a stage you choose by hand stays. From heating it never goes straight to off: the fan runs alone for 15 minutes first (parameter, 0 = off at once). A mode set by hand during that time ends the run-on.
- **Letting go:** when its control goes to *Observe only* or *Off*, or Thriftherm is disabled or deleted, a heat pump left at a quick heat-up's 30 °C or a drying's setpoint is set to its lowest setpoint and rests there. If Home Assistant stops, it gets its room's target in this case, as it then goes on regulating alone. One at its least power, or set by somebody meanwhile, stays as it is.

### At its minimum: how the heat pump heats

The heat pump runs at its least power, where its COP is best. Its setpoint sits a degree below its own reading and is set anew only rarely, so it beeps little. The coldest served room decides: half a degree above its target the compressor rests (setpoint 17 °C, the unit stays in heating), at its target it heats again. *Quick heat-up* gives more power.

What happens when a room lags is set in the fine tuning under *When the heat pump alone is not enough*:

| Choice | What happens |
|---|---|
| *Boiler or district heating helps* (default) | the central heating helps (attribute `boiler_helps`); the heat pump stays at its minimum. Warm fastest, a few cents dearer |
| *Heat pump runs higher* | no boiler: its setpoint goes up slowly, its COP drops |
| *Warm up slower* | least power only; the central heating helps only once a room is half a degree below its target |
| *Heat pump alone* | least power only; while it is the cheaper source its rooms do not call the boiler. The boiler goes on heating the other rooms, and all of them if the heat pump is dearer or blocked |

Without central heating it always runs higher, whatever is chosen; in the mode *Heat pump only* as well, except with *Warm up slower* or *Heat pump alone*. While it runs with no room needing heat right now, the *Decision reason* says "heat pump runs through" rather than "no room needs heat"; resting because the central heating is cheaper, it says "central heating cheaper, the heat pump pauses" ([details](HOW_IT_WORKS.md#held-at-its-minimum)).

### Bathroom drying

Bathroom drying needs the heat pump: only in a room it heats and that has a humidity sensor. The room form offers it only once a heat pump is set up.

- Drying comes **after** the shower: when the humidity rises, Thriftherm waits until it stops rising. Then the heat pump blows with its strongest fan stage and the drying target temperature, its setpoint 3 degrees above its own reading at a standstill, at most 60 minutes (default) and only while it is actively controlled and can heat and run efficiently. Afterwards the fan gets back the stage it had before, and it runs at its least power again.
- The button *Dry now* starts at once and for the full time, for laundry or a wet floor; *Back to automatic* ends the drying. If you want the button only, switch off *Start the drying by itself after a shower* in the room form.
- The moisture only leaves through the window. With a window contact in the bathroom the heat pump therefore blows only while the window is open: open it wide after the shower, and the warm air dries walls and mirror while the moisture leaves. With the window shut the drying waits (*Waiting: open the window*). Without a window contact it blows also with the window shut. The radiator always follows the normal window logic.

When it starts and ends: [bathroom drying](HOW_IT_WORKS.md#bathroom-drying).

## Services

| Service | Fields | Use |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature` (5–30 °C), `duration_min` (5–1440, default 120) | temporarily different target for one room |
| `thriftherm.clear_override` | `room` (optional) | back to automatic: end one override, without a room everything changed by hand |
| `thriftherm.set_away` | `return_time` or `clear_return_time` (both optional) | away mode, with pre-heating for the return or with no end |
| `thriftherm.clear_away` | – | back to Auto |
| `thriftherm.boost` | `room`, `duration_min` (5–240, default from the option *Quick heat-up duration*, 45) | quick heat-up of one room |
| `thriftherm.reset_learning` | `scope` (`boiler`, `heat_pump`, `rooms`), `rooms` | forget learned values ([Forgetting what was learned](#forgetting-what-was-learned)); administrators only |

`room` is the room key from the options (for example `bathroom`).

Example – away until Friday 16:00:

```yaml
action: thriftherm.set_away
data:
  return_time: "2026-12-18 16:00:00"
```

## Dashboard sensors worth knowing

| Entity | Tells you |
|---|---|
| *Decision reason* | in plain words why the system does what it does |
| *Recommended heat source* | central heating (boiler or district heating), heat pump, both or none |
| *Safety state* | *OK*; *Degraded*: a sensor is missing or the boiler has a fault; *Fallback*: no room or no trustworthy room temperature, nothing is sent. Heat pump faults are listed in the attributes, but degrade the state only where the heat pump is the only heat source |
| *Target temperature* per room | the target, and in *Reason* where it comes from |
| *Thermostat plan* per room | what is handed to the thermostat |
| *Boiler command* | what the boiler gets; *Knob in charge* means nothing is sent and the boiler runs on its own knob. Also shows the heating pump and the status code S.xx |
| *Learning state* | what has been learned and why learning waits ([Where you see it](#where-you-see-it)) |
| *Gas for heating*, *Gas for hot water* | how much gas (kWh) went into the heating and how much into hot water since setup. Measured with a gas meter: each step of the meter counts for what the boiler is doing at that moment; what was burnt while Home Assistant was not running is under *Not assigned*. Without a gas meter, estimated for Vaillant boilers from their own counters ([setup guide, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)); on the test installation the estimate was 1 % above the meter over 32 days (95.8 kWh at the gas meter) and never more than 0.3 kWh off on any day. Both fit the energy dashboard |
| *Planned return* | when you expect to be back; editable (date first, then time), empty when nothing is planned. *Provisional (23:59 placeholder)* (`provisional`) is `true` while only a date is picked: away then ends at 23:59, nothing is pre-heated yet |

**Detail sensors start disabled.** Deviation from target, temperature trend, dew point and absolute humidity per room, the flow/return spread, the boiler's thermal power estimate and the heat pump's airflow estimate change with almost every cycle and serve the analysis, not the control. Each change is a row in Home Assistant's database, so on a new installation they are disabled; enable the ones you want under *Settings → Devices & services → Entities*. Thriftherm computes them either way.

## Repairs

| Notice | Meaning and fix |
|---|---|
| *Room*: no heating window configured | neither schedule helper nor time range – the room stays at setback. Select a schedule in the room options. |
| *Room*: schedule helper missing | the selected schedule was deleted. Recreate it or pick another. |
| *Room*: radiator thermostat not responding | a valve is [silent](#silent-thermostats). Usually the radio link or the battery; the room does not call the boiler meanwhile. Check the battery, pair the valve again or switch it off and on. |
| Boiler flow above 100 °C | have the boiler checked by a heating engineer ([details](TROUBLESHOOTING.md#repair-notice-boiler-flow-above-100-c)). |
| No data from the boiler | ebusd delivers nothing usable; nothing is sent, the boiler runs on its knob. Check the ebusd app and adapter ([details](TROUBLESHOOTING.md#repair-notice-no-data-from-the-boiler)). |
| Heat pump airflow not calibrated | no airflow curve, so no COP; the data sheet is used meanwhile, or the *COP values of this model* if picked. |
| Boiler heating may still be blocked | Thriftherm stopped while the boiler's number or switch could not be reached (boiler through entities). It hands the boiler back as soon as both are back; until then switch the heating on by hand. |
| Home Assistant no longer shows temperatures in °C | Thriftherm works in °C only and sends no setpoints (a running heat pump is stopped, a boiler through entities handed back) until Home Assistant is back on the metric unit system (*Settings → System → General*). |
| Heat pump assigned to rooms it cannot reach | no ducts configured, so the warm air stays in its own room. Fit ducts and set the duct factor, or untick those rooms. |

Some problems show up without a notice: a room sensor quiet for six hours no longer counts (if its device still reports something else, the humidity say, only after a day; with *Room sensors report only on a change* only after the *Max. sensor age*, 720 minutes by default), the valve's own temperature stands in (behind Better Thermostat the valve itself, not Better Thermostat, which repeats the room sensor), and *Safety state* goes to *Degraded*.

*Settings → Devices & services → Thriftherm → ⋮ → Download diagnostics* gives a file with configuration, current state and learned values for bug reports. Only the people whose arrival ends away mode are left out, so look through it before posting it publicly.

Answers to common questions and what to do when something goes wrong: [Troubleshooting and FAQ](TROUBLESHOOTING.md).

## Terms

| Term | Meaning |
|---|---|
| **Observe only** | Thriftherm works out and shows what it would do, but controls nothing. In automations the value is still `shadow` |
| **Flow** | the heating water that flows from the boiler to the radiators, and its temperature |
| **Return** | the cooled water that comes back from the radiators |
| **Spread** | flow minus return. Large means the radiators take a lot of heat; small means the water comes back almost as warm as it left |
| **Heating curve** | which flow belongs to which outdoor temperature: the colder outside, the warmer the flow. In Thriftherm a straight line through two points |
| **Correction, level, slope** | what Thriftherm learns on top of the heating curve: the level shifts the whole curve, the slope mostly the cold days |
| **Load class** | how many rooms want heat right now: one, a few or the majority |
| **Calling, call** | a room below its target calls the boiler; a *holding call* brings a room that holds its target back up from just below it |
| **Cycling** | the boiler switches on and off at short intervals, because the rooms take less heat than its smallest burner gives |
| **Condensing / non-condensing** | a condensing boiler gains extra heat from its flue gas at a low return, a non-condensing one does not |
| **eBUS, ebusd** | the data bus of many boilers and the program that connects it to Home Assistant. `SetMode` is the only message Thriftherm writes there |
| **Knob** | the flow control on the boiler; it stays the upper limit and regulates when Thriftherm sends nothing |
| **Better Thermostat** | an integration that combines radiator thermostats (valves) with a room sensor into one room thermostat |
| **COP** | coefficient of performance of the heat pump: heat divided by electricity. 3 means 1 kWh of electricity gives 3 kWh of heat |
| **COP map** | the learned COP per outdoor temperature band of 2 degrees |
| **Break-even COP** | the COP from which heat from the heat pump is cheaper than from the central heating |
| **Marginal price** | what a saved kWh of district heating really saves; because of the allocation key less than the price on the bill |
| **Duct factor** | how much air the heat pump still moves with ducts, 1.0 = no ducts |
| **Override** | a target that applies for a limited time instead of the schedule |
| **Comfort / setback** | the temperature in the schedule blocks and the lower one outside them |
| **Lead** | what a room's thermostat gets above the room's target while the boiler runs for the room, so that it does not throttle early |
