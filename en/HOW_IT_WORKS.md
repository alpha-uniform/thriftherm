# How Thriftherm works

**English** | [Deutsch](../de/FUNKTIONSWEISE.md)

For day-to-day use the [user guide](GUIDE.md) is enough. This page shows how Thriftherm decides internally.

The numbers come from the code. "By default" means you can change it in the options. All other values are fixed.

**Contents**

- [Away and planned return](#away-and-planned-return)
- [Changes by hand](#changes-by-hand)
- [Pre-heating](#pre-heating)
- [Boiler control](#boiler-control)
- [Heat-up speed](#heat-up-speed)
- [Learning](#learning)
- [Heat pump](#heat-pump)

## Away and planned return

How to use it is in the [user guide, section 2](GUIDE.md#away-with-and-without-a-return). Here are the rules by which *Planned return* reads what you enter.

- ***Away* again:** if *Away* with a return is already on, choosing *Away* again changes nothing. For away with no end, choose another mode first and then *Away* again, or call `thriftherm.set_away` with `clear_return_time: true`.
- **The date first:** while nothing is planned, the time field takes nothing.
- **A date alone is a placeholder:** until a time is picked, the return stands at 23:59 of the chosen day for the moment, today as on any later day. Another date takes the provisional 23:59 along. A provisional return does not pre-heat. Only the time you pick next counts, and the rooms are pre-heated for that. If you pick none, away still ends at 23:59.
- **Changing the date when a time is already planned:** the new date keeps the planned time and counts at once. If you pick today and that time has already passed, it shows the provisional 23:59 again.
- **What is refused**, each with a message: a time of today that has already passed, a past day and, once it is past 23:59, today's date alone. The exception: 00:00 in the minute after a return is reached becomes the 23:59 placeholder while 23:59 is still ahead.
- **Really back at 23:59:** while the provisional 23:59 is shown, the time field sends nothing for 23:59 because nothing changes. A 23:59 that arrives anyway (say through `datetime.set_value`) stays provisional. So pick another minute first, e.g. 23:58. Pick 23:59 only once 23:58 shows and the attribute *Provisional* is `false`. Or use `thriftherm.set_away` with `return_time`, which sets date and time in one step.

### Browser in another time zone

Both fields show and work in the browser's time zone, so date and time count in browser time. Examples with Home Assistant in Berlin:

- **While nothing is planned,** the date step sends the browser's midnight. In Home Assistant that is another time of day: London 01:00; east of Home Assistant on the day before, Helsinki 23:00, Tokyo 17:00, 16:00 in winter. That is no placeholder then but a real time. If it has passed, as it mostly has for today, it is refused. Otherwise it counts as a return at that hour, with pre-heating.
- **With a return planned,** a time picked in the time field counts in browser time: 12:00 picked in London is 13:00 in Berlin. The day can shift too. East of Home Assistant the provisional 23:59 already shows as the next day (Helsinki: 00:59), so a time picked then lands a day later. West of it this can hit returns shortly after midnight (3 October 00:30, moved to 5 October in London: 6 October 00:30).
- **Summer and winter time:** if the browser and Home Assistant do not change on the same day, say a browser in the US or in a zone without summer time such as Dubai, a date step alone across a change can move the time by an hour. A provisional return then becomes real, and the rooms pre-heat for it (provisional 26 October 23:59, moved to 3 November in New York: 4 November 00:59).

So after each entry check *Planned return* and its attribute *Provisional*, set the browser to Home Assistant's time zone, or use `thriftherm.set_away`.

### From automations

`thriftherm.set_away` with `return_time`, or with `clear_return_time: true` for no end. There every time in the past is an error, including one of today. Every time counts as it comes, midnight too.

`datetime.set_value` on *Planned return* reads values the way the fields send them instead: midnight while nothing is planned becomes the 23:59 placeholder, on a later day too. And while *Planned return* is unavailable (Thriftherm not loaded), Home Assistant drops `datetime.set_value` without an error. `set_away` reports one. So automations should use `set_away`.

### Damp guard

If the air in a room gets closer to its dew point than 3 degrees while you are away (default, *Away: dew-point margin for humidity guard*), Thriftherm raises the target. It then stands 1 degree above the room temperature, at most at the comfort temperature. Reason: *Away, raised against damp*. The upper limit keeps a humidity sensor stuck near 100 % from pulling the target up degree by degree.

## Changes by hand

What a change by hand does and where you see it is in the [user guide, section 3](GUIDE.md#changes-by-hand). Here is how Thriftherm recognises one.

Thriftherm writes setpoints, so it has to know who changed one. Home Assistant says so itself: every change carries where it comes from.

| From | Example | A change by hand? |
|---|---|---|
| Thriftherm itself | its own command and the device's answer to it | never |
| Somebody in Home Assistant | dashboard, app, voice assistant | always, at once |
| An automation or a script | "23 °C before the shower", also one run by the clock | always, at once |
| At the device | the knob on a valve, the heat pump's remote, the maker's app; also what changed while the device could not be reached | yes, unless the device explains the change itself (a rule of Versatile Thermostat's own, a late radio echo with Better Thermostat). On the heat pump only once it has had two minutes to follow a command |

A setpoint somebody else sets on a thermostat or on the heat pump starts a cycle at once, without waiting for the next one. An automation of your own to speed the display up is therefore not needed.

**Echo filter:** Better Thermostat rewrites the valve setpoint every few seconds. When a Zigbee reply arrives late, Better Thermostat takes the old value for a turn of the knob. The echo filter (Options → Control parameters, on by default) recognises this: when Better Thermostat jumps to a value the valve already showed within the last minute and left again (e.g. 16 → 16.5 → 16), the jump is ignored and the planned setpoint is sent again. A real turn of the knob brings a new value and is taken over. The room's *Thermostat plan* sensor shows when an echo was last ignored.

### Turning the knob of a valve behind a room thermostat

The room thermostat is the first to judge a turn of the knob. With Better Thermostat that depends on its *Child lock* switch for the room.

- **Child lock off:** Better Thermostat takes the number the valve shows after the turn as the target for the room. Thriftherm keeps that as a change at the device. Mind that this is the number on the valve, not the difference: with the valve showing 16 °C for a room at 18.5 °C because of Better Thermostat's correction, three clicks down make it 14.5 °C for the room.
- **Child lock on:** Better Thermostat sets the valve back and keeps its target. Measured 2026-10-12: from 16 to 14.5 °C, back at 16 °C 0.9 seconds later. Thriftherm keeps to that too: the turn does not count.
- **The lock on the valve itself** is a separate thing. Lifting it at the valve does not switch off Better Thermostat's child lock.
- **Versatile Thermostat without *follow underlying temperature change*** does nothing at all about the turn. Then Thriftherm takes it over itself after five seconds, as a difference: three clicks down are 1.5 degrees less for the room.

### What is no change by hand

- **The command did not arrive.** If the heat pump shows something else after a command and nobody changed it, the command is sent again: after 2, 4, 8 … minutes, hourly at most, for as long as the unit does not carry it out. The command shows how many in a row (*Commands in a row the unit did not carry out*). Only when Home Assistant was restarted between the command and the difference can the two no longer be told apart. The difference then counts as a change at the unit.
- **The thermostat rounds.** A thermostat in whole degrees that answers 21.5 °C with 22 °C shows our setpoint as it can hold it. That is no override, and nothing is written against it.
- **Switching a thermostat off** asks for no temperature. That makes no override.

This has one limit: when a device writes its state within a few seconds of a command, Home Assistant passes that change on as the command's, whatever changed. A press on the remote in exactly those seconds therefore counts as whoever commanded last.

## Pre-heating

Thriftherm starts early enough for the room to be warm when a comfort block begins:

start = block start − (target − current) / heat-up rate − margin

- The heat-up rate is learned per room ([What is learned](#what-is-learned)). Until then 0.8 degrees an hour is assumed for radiator rooms and 1.2 degrees an hour for heat pump rooms (default, *Default heat-up rate*).
- The margin is 20 minutes (default).
- Rooms are pre-heated for blocks that begin within the next 12 hours.
- Before a return from *Away* the same sum applies. The aim is the temperature that holds at the return. With a schedule helper that is the comfort temperature, because a weekly schedule helper cannot be evaluated for a later time. With time ranges from the room options it is the temperature the time range has then.

## Boiler control

How to use it is in the [user guide, section 5](GUIDE.md#5-boiler-control-own-boiler-on-ebusd).

### Flow

- **Heating curve:** a straight line through two points, by default 55 °C at −10 °C and 30 °C at +15 °C. Without an outdoor temperature Thriftherm takes the middle of the two points.
- **On top comes what the room that needs the most asks for.** The other rooms' valves take less ([Load classes and values per room](#load-classes-and-values-per-room)). The room asks for:
  - its learned correction;
  - while it calls, a demand extra of 8 degrees times its heat demand. 100 % heat demand means: the room is 2 degrees below target (default, *Deviation for 100 % demand*), looked ahead by its trend for half an hour (default, *Trend look-ahead*);
  - while it heats up on an extra, its extra for the [heat-up speed](#heat-up-speed).
- **Limits:** only the sum is limited to the minimum and maximum.
- **Rises are limited:** at most 5 degrees per 10 minutes, counted from where the rise began, and never more than 5 degrees above the value last sent, also after a pause without a setpoint. So the boiler does not jump from 30 to 60 °C. Frost protection ignores the limit.
- **A rise waits in the lockout:** while the boiler reports its overrun or burner lockout after a heating run (S.5 to S.8), a higher setpoint goes out only once it may light again. A rise in that time made it light minutes early, one start more each time. Frost protection and *Quick heat-up* do not wait, and after 30 minutes at most the rise goes out anyway. While it waits, *Boiler command* shows *Boiler in its lockout: the rise waits* under *Waiting for*. Boilers without a status code are not affected.
- **Heating and blocking** change at most every 5 minutes. Frost protection is exempt.
- **Frost protection** heats until the room is 1 degree above the frost temperature. So a room right at the limit does not switch the boiler on and off every few minutes.

### When a room calls the boiler

- A room calls from 0.3 degrees below target and stops once it is less than 0.1 degrees below.
- **Stall:** if the call has run for an hour, the room is at most 0.3 degrees below target and rises more slowly than 0.15 degrees an hour, it counts as reached. It calls again only when it falls 0.5 degrees below target or its target changes. An unknown trend, say right after a restart, is no stall.
- **When the target goes down** (an override ends, say), a running call is judged again by the start threshold.
- **End of comfort:** no room calls in the last half hour of a comfort period. The heat would arrive too late. This also holds just after the end while the schedule has not switched yet (up to 2 minutes).
- *Quick heat-up* is exempt from the stall and end-of-comfort rules.

### Heating along

- While the boiler runs anyway, other rooms in their comfort period that are below target get 0.5 degrees more (reason *Heating along*).
- A room whose pre-heat would start within the hour starts now (reason *Pre-heating early*).
- Why: one radiator takes about 1 kW, while the boiler burns at 8 kW or more. More open radiators mean longer burner runs and fewer starts.
- Rooms that heat along never call by themselves and cannot keep the boiler running.

### Outages and resending

- **Resend:** Thriftherm sends its command every 2 minutes. Without one the boiler returns to its knob after 9–16 minutes (measured). A lost telegram is harmless that way, and a Home Assistant that stops hands the boiler back to its knob.
- **Short dropouts:** adapters lose the eBUS signal for a second now and then. Only a signal that stays gone for two minutes counts as a data outage.
- **After a restart** Thriftherm waits up to five minutes for the room sensors before it blocks heating. Until then it sends nothing, and the boiler keeps its last command.
- **Silent radiator thermostat:** the limit is two and a half hours. It leaves room for thermostats that report only about hourly (the Zigbee default), and for one missed report. A limit of one hour took healthy valves for lost (reported 2026-10-10). On the test installation the thermostats report at least every 15 minutes. Any change of one of the thermostat's entities counts as a sign of life; a report with unchanged values leaves no trace in Home Assistant. With ZHA, ZHA's own *Last seen* counts as well, unless ZHA marks the device unavailable.

### Hot water detection

ebusd does not report hot water reliably on every boiler, so Thriftherm recognises it itself. Where the boiler reports a status code, that counts. Otherwise it is hot water when:

- gas burns (above 1000 W on the gas meter) although heating has been blocked for at least two minutes;
- the flow is more than 15 degrees above the heating flow that was sent;
- the return is more than 1 degree above the flow (only without a status code);
- the flow is more than 2 degrees above the heating maximum, while that maximum is in force, i.e. while Thriftherm sends a flow or a block.

The first two react within a minute; the temperatures only arrive every few minutes. *Boiler hot water active* stays on for 10 minutes after the last sign, 2 minutes with a status code. Learning pauses for an hour so shower water does not teach the heating curve.

**Warm start.** A combi boiler keeps its hot water exchanger warm and burns briefly about once an hour for it, also with heating blocked. It then reports the same status code as when heating (S.4). If heating has been blocked for at least two minutes, the boiler still reports heating and its heating pump stands, that is this warm start: it does not count as a burner start of the heating, and its gas counts as hot water, also what the gas meter reports shortly after. If the pump runs while it burns, the boiler really heats, for example because the block did not reach it; without a pump sign nothing counts as a warm start. Learning does not pause for a warm start, the heating circuit stayed cold, and the hot exchanger afterwards is no sign of hot water for 20 minutes (measured 2026-10-16: one minute at S.4, heating pump off, flow from 21 to 67 °C).

### Fresh readings

ebusd reads the boiler's values one after another, so flow and return would otherwise be minutes old.

- Thriftherm raises the poll priority of flow, return, the boiler state, the heating pump and the status code in ebusd.
- This is renewed every 10 minutes, so an ebusd restart hardly shows.
- On top, Thriftherm requests them directly once a minute: with a gas meter while gas is burning, without one all the time.

These are read requests only; nothing is written to the boiler.

### ebusd: pump and status values

The last field of the ebusd message `Status01` is called the pump state, but it follows the heating demand: it read "off" while the pump was audibly running. So Thriftherm reads:

- the pump itself from **`WP`** (d.10);
- the display code from **`Statenumber`**: 8 = S.8 burner anti-cycling, 7 = S.7 pump overrun, 31 = S.31 no heat demand.

`Status01` still tells hot water apart and stays in use for that. Without `WP` and `Statenumber` Thriftherm falls back to `Status01`.

**Pump mode, measured:** on the tested boiler the pump mode *post-run* was the cause of a cold bathroom. With pump mode *permanent* the bathroom warmed from 20.8 to 21.5 °C within an hour, where it had not moved all afternoon before.

## Heat-up speed

The setting is in the [user guide, heat-up speed](GUIDE.md#heat-up-speed).

**Why:** reported by a user on 2026-10-14: the living room raised from 20.5 to 23.5 °C, the flow stayed at 45 °C for 40 minutes, the boiler cycled with the return at 44 °C, and two hours later the room was at 22.2 °C.

**Start and end:** a heat-up starts when a room that calls is 1 degree or more below its target. It ends once the room is 0.2 degrees below, or no longer wants heat.

**Measuring:**

- The first verdict comes 20 minutes after the start. The flow rises by at most 5 degrees per 10 minutes, and the radiator has to warm through first.
- After that Thriftherm measures every 20 minutes how fast the room itself has risen since the last verdict.
- A stretch only counts if the boiler heated in it, Thriftherm controlled it actively all the time, and the room's valve was open all the time (the thermostat did not report "not heating").
- The room must still be at least 0.5 degrees below target. Closer, the valve throttles, and the rise says nothing any more.
- The measure is the rise in the room, not the spread: a small radiator and a room that loses much heat look alike at the boiler.

**Verdict** after each stretch that counts:

| Rise in the room | Extra |
|---|---|
| slower than half the speed | 4 degrees more |
| slower than four fifths of the speed | 2 degrees more |
| more than one and a half times the speed | 2 degrees less |
| in between | stays |

- The extra goes up to 16 degrees, and the flow never above its maximum.
- If the flow stood at its maximum in the stretch, was rising as fast as it may, or what the room asks for still lay below the flow minimum, the extra does not grow: more would not arrive. It may still go down. In the test of 2026-10-15 it otherwise grew to 14 degrees at 22 °C outside without moving the flow.
- A heat-up that was done much faster than asked (on average since its start more than one and a half times the speed, after at least 10 minutes) takes 2 degrees off.

**Effect:** the extra is multiplied by the room's heat demand (0 to 100 %). The closer the room gets to its target, the smaller it is.

**Remembering:** the extra at the end of a heat-up is where that room's next heat-up starts. It survives a restart. With the heat-up speed at 0 no heat-up runs; the remembered values are kept for later.

**Learning:** while a room heats up on an extra, the heating curve learning pauses (reason *a room heats up on an extra*). A boiler cycling on the higher flow would otherwise teach the curve to go down.

## Learning

What is learned and where you see it is in the [user guide, section 6](GUIDE.md#6-learning).

### What is learned

| Area | What is learned | From |
|---|---|---|
| Boiler | heating curve correction per load class and room, in two parts, ±10 degrees each: **level** (at every outdoor temperature) and **slope** (in full at −10 °C, not at all at +15 °C) | flow/return spread, how fast rooms warm up, and short cycling |
| Rooms | heat-up rate per room (degrees an hour) | heating periods with the window closed, in which a heat source reaches the room, at least 30 minutes long and at least 0.5 degrees of rise. The median of the last 10 counts |
| Rooms | cool-down coefficient per room (1/h): drop per hour = coefficient × (inside − outside) | stretches without heating, window closed, at least 5 degrees warmer inside than out, at least an hour long and at least 0.3 degrees of drop. The median of the last 10 counts |
| Rooms | heating power per room (degrees an hour without losses) | heat-up rate plus the losses during that period. Unlike the raw rate it holds in winter too |
| Heat pump | COP map over outdoor temperature, setpoint correction | measured COP runs, see [Heat pump](#heat-pump) |

**At the flow minimum the curve does not learn upwards.** On a mild day the curve lies below the minimum, and the minimum is sent. A room that does not get there then needs more than the minimum; that says nothing about the curve on a cold day. A step used to raise the value by the whole gap up to the minimum: in the test of 2026-10-15 one step at 16 °C outside moved a value by 4.4 degrees, and after three steps the bedroom stood at 9.3 degrees more. Now the learning holds still there, and the learning state shows *A room does not get there, but the flow stands at its minimum*. If that lasts, the minimum is too low for this room, and you raise it in the boiler options.

**At the limit of the correction** (±10 degrees for level or slope) a step goes only up to the limit, or it is dropped. The learning state then shows *Heating curve correction at its limit (level or slope, ±10 degrees each)* until no step has met the limit for an hour and 20 minutes. Which line it is shows in the attribute `curve_by_load`. If a single room stays there, the cause is usually not the curve but that room's radiator or hydraulics.

### Level and slope

A learning step is shared between level and slope by where the outdoor temperature lies between the two points of the curve. The flow at the current outdoor temperature moves by the full step.

A step on a mild day thus mostly moves the level, one in the cold mostly the slope. That is how a heating curve is set by hand: too cold only in winter means steeper, too cold in between means higher. What is learned in autumn therefore hardly touches the winter.

### Telling the burner

Thriftherm tells the burner by the best sign the installation has:

1. a gas meter: above 1000 W counts as burning;
2. else the boiler's status code;
3. else a flame sign of the boiler's own, or its pump state.

A gas meter is not needed. Burner runs for hot water never count.

### Spread or the rooms' progress

While the boiler runs through, Thriftherm judges the curve after each review window of 20 minutes. The spread is smoothed for that.

**How many rooms call:** one open radiator returns the water almost as warm as it went out. The spread therefore judges the curve only while enough rooms call:

- Enough means: at least the *Share of rooms …* (boiler options, 0.5 by default) of the rooms that can take heat right now, rounded up and at least one.
- A room can take heat if its temperature is known and heating is allowed in it. An open window, or a thermostat switched off or silent, leaves it out.
- Rooms that merely heat along are not counted as calling.
- The spread must have been measured with enough rooms for a whole review window. What was collected with too few rooms is dropped.
- Below that, only the progress of the calling rooms counts.

**Rules:**

| Finding | Curve |
|---|---|
| spread below 5 degrees, and no room lags or makes slow progress | down |
| spread above 15 degrees, and a room has more than 30 % heat demand | up |
| a room with more than 30 % heat demand rises more slowly than 0.2 degrees an hour | up |
| every calling room has 10 % heat demand or less, and no room lags | down |

A room whose progress says nothing about the flow (see [Guards against learning nonsense](#guards-against-learning-nonsense)) never raises the curve.

### Cycling

A cycling boiler never gives a settled spread. Then the rooms decide.

- **Cycling** means: at least 3 burner starts in the last hour, and the finished burner runs last 5 minutes or less (median).
- **Up by one step** when a called room lags and heating has been released for at least 30 minutes (reason *Boiler cycles and a room does not reach its target*). Lagging means: at least 0.2 degrees below target and rising more slowly than 0.2 degrees an hour. A room the stall rule has given up on counts too.
- **Down** only when every called room warms up briskly: 0.5 degrees an hour or more.

Measured 2026-10-10: at a 35 °C flow minimum instead of 45 °C the burner ran for only 41 s every 15 minutes instead of 62 s. The bathroom stayed 0.2 to 0.3 degrees below target for 14 hours. Less flow meant less heat there, not fewer starts.

### Load classes and values per room

The flow a flat needs depends on the outdoor temperature, on how many radiators take heat, and on each radiator: a small radiator in a cold room needs more than a large one.

Thriftherm therefore keeps three load classes: one room calls, a few, the majority. Within each class every room has a correction of its own, with level and slope. Six numbers per room say which flow suits which outdoor temperature and number of rooms.

**The majority** is more than the *Share of rooms …* of the rooms that can take heat right now (by default more than half). With four rooms, two are a few and three the majority; with three rooms, two are the majority already.

- **The room that needs the most sets the flow.** There is one flow for all radiators. Of the rooms that want heat, the one whose value and extras ask for the most sets it. The others' valves take less. Which room that is depends on the radiator, the walls and the windows, not on the room's size: on the test day of 2026-10-13 the small kitchen stopped rising while the living room went on.
- **A room that does not get there raises only its own value.** It starts from the flow that was actually sent: a room that got more than its own value and still does not get there needs more than that. The kitchen not getting there says nothing about the living room.
- **Steps down and steps for the spread** move the value of the room that set the flow. After a step down no room that wants heat stays above the new value. Otherwise the next of them would set the flow at once, and it would never come down.
- **No room has to heat on its own for this.** Every time a room calls along with others, its own progress is judged, and it gathers its own hours of operation.
- **A learning step only moves the class it was taken in.** Measured 2026-10-11: the bathroom alone drove the common correction to 4 degrees more, and three rooms together then started from there.
- **A new class only counts after ten minutes.** With a third room calling on and off, the flow would otherwise change by the minute (seen 2026-10-11: 4 degrees every minute). When the class changes, the flow moves by the difference between the two curves; the flow limits always hold. While no room calls, the class stays until Thriftherm blocks heating.

**Starting without a value of its own:**

- A class without a value of its own starts from the nearest class. Of two equally near, it takes the one with more rooms. If there is none, it starts from the common correction of before.
- A room without a value of its own starts from the value its class had learned before there were values per room. So an update does not change the flow. It also takes over whether that value was found already and how long it has served.
- If there is no such value, the room starts from its own value in the nearest class, for it is the same radiator.
- Otherwise class and room take over the value only. They gather their hours and steps themselves.

### Found values

Without this rule the learning answers every small deviation and the flow keeps going up and down.

- Once a value has served for three hours of operation without a lagging room and without a learning step, it counts as found and stays put.
- Only time in which a room called and heating had been released for half an hour counts towards the three hours. A room whose progress says nothing about the flow ([guards against learning nonsense](#guards-against-learning-nonsense)) gathers no time meanwhile. Gaps of more than 5 minutes between two cycles (restart, outage) do not count as operation.
- A found value is raised again only after a room has missed its target for 45 minutes. It is lowered only after the rooms have allowed it for two hours.
- Either may pause for up to 45 minutes without starting over. For a room 0.2 degrees below target may read only 0.1 degrees below for the next half hour (measured 2026-10-10 in the bathroom), and a cycling boiler's calls end again and again.
- After a learning step the value has to prove itself again.

### Guards against learning nonsense

**When the boiler learns:**

- only in *Active* mode;
- at the earliest 10 minutes after the burner starts, and from at least 10 samples;
- never while hot water is made, a setpoint changed in the last 20 minutes, a quick heat-up, a drying or a heat-up on an extra ([heat-up speed](#heat-up-speed)) runs, or the safety state is not OK.

After each such pause the learning waits a full review window of 20 minutes and counts only the burner runs of that time. What came before belonged to another setpoint, a drying or the boiler's own knob.

**Waiting time and daily allowances** are shared by all classes and rooms:

- at least 45 minutes since the last step before a step up, an hour before a step down;
- per day at most 10 degrees up and 4 degrees down.

**Rooms that do not count as "not getting there":**

- **A room in a quick heat-up:** a wish for a while says nothing about the heating curve, which is to hold the temperatures planned.
- **A room with a change by hand while it is still heating up to it.** Once it is within 1 degree and holds there, it counts like any room; otherwise a household that heats by hand only would never teach anything. Seen 2026-10-13: four rooms set 3 degrees above their temperature for a test drove the correction of most rooms from 1 to 8.5 degrees in three hours.
- **A room whose thermostat says it is not heating right now** (Better Thermostat and Versatile Thermostat say so). Its valve is shut although the room is below target, and a hotter flow would only shut it sooner.

### Step sizes

Steps grow with certainty: coarse first, then fine.

- **A step up after a step up** doubles: 1, 2, 4 degrees, no more.
- **A step against the one before** halves, down to 0.5 degrees.
- **A step down after a step down** is the first step again. Steps down never grow: comfort comes before saving.
- Within a load class the last step of that class counts. A step the bathroom alone took says nothing about the next one for the whole flat.
- No step goes further than the flow can follow within its limits, nor further than the daily allowance still lets it. If less than 0.5 degrees is left, there is no step.
- After three hours without a step a run starts at the first step again: 1 degree while **Learning**, 0.5 degrees while **Refining**. *Refining* applies after six changes or after a day without a change.

Measured 2026-10-11: with fixed steps of 1 degree, four a day, the correction stood at 4 degrees more after eight hours, and the bathroom was still 0.6 degrees below target. The flow minimum there was 10 degrees too low for the bathroom alone.

## Heat pump

How to use it is in the [user guide, section 7](GUIDE.md#7-heat-pump-optional).

### Measuring the COP

The COP is the heat the heat pump puts into the air, divided by the electricity it uses.

- **Heat:** from the airflow and the difference in heat content between intake and outlet air. Temperature and humidity of the intake air and the temperature of the outlet air count for that.
- **Airflow:** from the fan speed through the curve (speed → m³/h) from the setup, times the duct factor. Just outside the curve (up to 20 % above or below) Thriftherm goes on with the fan speed. With the compressor standing between two starts, further below too: otherwise the heat of the pause would be missing and the whole cycle would be dropped.
- **Duct factor:** it corrects the airflow, not the heat. Against the resistance of the ducts the fan moves less air at the same speed. Heat the ducts give off on the way stays in the home and is no loss.

A reading only counts once the unit has settled:

- the compressor has run for at least 10 minutes (default);
- the power is at least 150 W (default);
- the outlet air is at least 3 degrees warmer than the intake air (default) and changes by at most 0.5 degrees a minute (default);
- the value is not above 60 % of what physics allows (Carnot, from the temperatures of the two coils).

The COP shown needs at least 12 valid readings (default), cleaned of outliers and smoothed. Otherwise it reads "unknown" instead of an invented number.

**COP over the cycle:** a heat pump that cycles because its setpoint is reached never gives a settled value. The whole cycle counts then: heat and electricity are added up from one compressor start to the next, the off time with its standby power included. This COP (attribute *COP over the last on/off cycle*) is lower than in a continuous run, but it is the one that decides the cost.

**COP map:** Thriftherm keeps the COP in steps of 2 degrees of outdoor temperature. From 5 readings a step counts as known. For the others it uses a curve after typical data sheets and fits it with a factor from the known steps; the factor starts at 80 %. With a model picked in the heat pump form under *COP values of this model*, steps without a measurement of their own take that model's shipped values, and outside them the curve fitted to those values (basis *Measured on the same model*). What your own unit measures always comes first. Shipped so far: the Midea PortaSplit, measured on one unit in the first week of heating at 2 to 16 °C outside.

**Learning runs:** if an outdoor temperature step has fewer than 5 readings yet and the estimated COP is at least 85 % of the break-even COP, the heat pump may run there for an hour to measure, even if the central heating would be cheaper right now. That happens only when a room needs heat or is being dried, and at most once a day per step. Switch it off with *Allow learning runs* in the heat pump options.

### Lockouts

- **Too dear:** if the measured COP stays below the *Break-even COP* for 30 minutes, the heat pump heats dearer than the central heating and is locked out (parameter *Lockout after icing / inefficiency*, 120 minutes by default). In the mode *Heat pump only* there is no such lockout, and one under way ends at once when you switch to *Heat pump only*.
- **Icing or frequent defrosts:** this lockout protects the unit and always runs to its end.

### Icing and defrosting

The Midea reports no defrost. Thriftherm infers it from patterns: in the temperatures at the indoor and outdoor coils, the air and the power.

| Sign | Meaning |
|---|---|
| outdoor coil rises by more than 5 degrees (default) within 2 minutes, the compressor runs, and the indoor coil falls by more than 1 degree | circuit reversed |
| indoor coil more than 5 degrees colder than the intake air twice in a row (without an intake reading: below 5 °C), compressor running | circuit runs backwards |
| outlet air less than 1 degree warmer than the intake air for 3 minutes, while heating has settled | cold outlet |
| power falls by more than 40 % within 2 minutes (from above 300 W), compressor running | power drop |
| outdoor coil more than 8 degrees (default) below the outdoor air for 10 minutes, compressor running | iced outdoor coil |
| below 5 °C outside and above 80 % humidity | icing weather, shown only |

- **A defrost cycle** is: circuit reversed, circuit runs backwards, or a cold outlet together with a power drop. It is counted once, however long it lasts. Its COP readings are dropped.
- **Locked out** after more than 3 defrost cycles in 90 minutes (default), or with an iced outdoor coil together with a cold outlet. An outdoor coil 8 to 12 degrees below the air alone is normal at full load in frost, and so is a power drop with it: that is the compressor slowing down (seen 2026-10-20: from 630 to 348 W, and the heat pump went on heating).
- Circuit and power are read while heating only. A compressor that starts to cool or to dry shows the same temperatures.

### Bathroom drying

- **Trigger:** the absolute humidity is more than 2.5 g/m³ above its baseline (default, *Trigger: rise in absolute humidity*). The baseline is the median of the last three hours without the last 20 minutes. With the relative humidity above 70 % already, 60 % of that rise is enough.
- **Start after the shower:** the rise only gives notice. The drying starts once the humidity has reached no new high (more than 0.2 g/m³ above) for three minutes, 20 minutes after the rise at the latest.
- **While drying** the heat pump blows with its strongest fan stage and the drying target temperature (22 °C by default), never above target plus 1.5 degrees. With its setpoint following the rooms, the unit gets its own reading plus 1 degree then, if that is more than the rooms' setpoint; this lead reaches at most 2 degrees above the drying target. With the rooms' setpoint alone it would stop, fan and all, once its own spot is that warm already (seen 2026-10-18). If it cannot heat right now, the drying waits with the time it has left.
- **End:** once the dew point is at least 4 degrees below the room temperature again and the absolute humidity at most 1 g/m³ above the baseline; at the latest after the longest duration (60 minutes by default). With a window open the heat pump keeps blowing. If the window stays open for more than 20 minutes and the humidity is then still more than 1 g/m³ above the baseline, the drying ends as well.
- **Pause afterwards:** if a drying ended because of its longest duration or a window open without effect, none starts by itself for three hours. The baseline still holds the air from before the shower in that time (measured 2026-09-25).
- **The button *Dry now*** starts at once and runs the full time, whatever the humidity and the window.

### Setpoint follows the rooms

With the switch the heat pump gets the highest target of the rooms it serves plus the setpoint offset (1 degree by default).

**Raising:** the unit regulates on the air of the room it stands in. If that room is warmer than a served one, the unit throttles before the served room is warm. Seen 2026-10-13: set to the bathroom's 21 °C, 22 °C at its own intake, the bathroom at 19.8 °C, and the boiler was called for it.

- A served room lags when it is below its target and rises more slowly than 0.3 degrees an hour. An unknown trend does not count, nor does a room being dried.
- After it has lagged for 20 minutes, Thriftherm raises the setpoint by half a degree (or the unit's coarser step), 2 degrees above the setpoint offset at most (attribute `eco_offset_k`).
- That happens only while the unit throttles, i.e. while its own reading is within 1 degree of its setpoint (without an own reading: the compressor stands, last the intake air). If it works flat out already, or the raising is used up, the boiler helps (attribute `boiler_helps`) until the rooms are warm.

**Bringing it back:** with all served rooms warm, the setpoint comes back in the same steps, two hours after the last step at the earliest. With every served room at least 0.5 degrees above its target, every 20 minutes. Every step makes the unit beep, so there are few. Seen 2026-10-13: after a restart the two hours began anew, and the unit ran for 1.7 hours at the raised setpoint although every room was 1 to 2 degrees above its target.

### Without "setpoint follows the rooms"

Thriftherm then uses the setpoint as a power lever: intake air plus a learned offset. A small offset means a low compressor load and a high COP; 30 °C means full power (only for *Quick heat-up*).

- The heat pump starts when a served room is 0.3 degrees below its target, and stops once no room is below its target plus 0.2 degrees.
- It runs for at least 20 minutes and pauses for at least 10 minutes (default, *Minimum run time*, *Minimum pause*). A lockout ends a run at once.
- Every 30 minutes of a run Thriftherm looks at the slowest room: below 0.3 degrees an hour the offset grows by 0.5 degrees, above 1.5 degrees an hour it shrinks by 0.5 degrees, between 0.5 and 5 degrees.
