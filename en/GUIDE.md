# Thriftherm user guide

**English** | [Deutsch](../de/ANLEITUNG.md)

> **Setting up for the first time?** Start with the [setup guide](SETUP.md).

This guide explains how to live with Thriftherm day to day: what the modes do, which temperatures apply when, what Thriftherm learns and what to do when Home Assistant reports a problem. The [README](../README.en.md) explains the idea and the hardware. How Thriftherm works inside, with every threshold and time, is in [How Thriftherm works](HOW_IT_WORKS.md). When something goes wrong, see [Troubleshooting](TROUBLESHOOTING.md).

**Language.** Thriftherm speaks German when Home Assistant is set to German and English for every other language – entity names, states, attributes, the decision reason, dialogs, repairs and error messages. Entity IDs are always English (`sensor.thriftherm_boiler_command`), so automations and dashboards keep working whatever the language.

## 1. How it fits together

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
| **Plan only (beta)** | everything is calculated and shown, nothing is sent – the default |
| **Active** | commands are sent. Only offered after you release it in the options |

Start with *Plan only*, watch the plans for a few days, then switch to *Active* – step by step in the [setup guide, step 7](SETUP.md#7-plan-only-first-then-active).

## 2. Operating modes

Select **Operating mode**:

| Mode | What happens |
|---|---|
| **Auto** | rooms follow their schedules; the cheaper heat source is used |
| **Central heating only** | the heat pump is not used; the boiler or the district heating heats |
| **Heat pump only** | only heat pump rooms are heated; the boiler only for frost protection. See section 7 |
| **Summer (heating off)** | no space heating; hot water keeps working; frost protection stays |
| **Away** | all rooms are held at the away temperature; with a return time they are pre-heated in time, see below |

### Away with and without a return

- **Away with no end:** choose *Away* as the operating mode. *Planned return* stays empty. The rooms stay at the away temperature until you choose *Clear away* or another mode. If *Away* with a return is already on, choosing *Away* again changes nothing; call `thriftherm.set_away` with `clear_return_time: true` then.
- **Away until a point in time**, e.g. today 22:00 or 3 October 12:00: set *Planned return*. That switches to away until then, and the rooms are pre-heated in time for the return. In Home Assistant's fields (the entity's dialog, the entities card) pick the **date** first, then the **time**.
- **A date alone is a placeholder:** without a time the return stands at 23:59 of that day for the moment. Away then ends at 23:59, but nothing is pre-heated yet. The attribute *Provisional (23:59 placeholder)* (`provisional`) of *Planned return* is `true` for as long.
- **Back home:** the action *Clear away* (`thriftherm.clear_away`) ends the absence and the return. Once the return time is reached, Thriftherm goes back to Auto by itself.
- **From automations:** use `thriftherm.set_away`, with `return_time` or with `clear_return_time: true` for no end. Unlike `datetime.set_value` it reports an error when something fails.

After each entry check *Planned return* and *Provisional*, above all when your browser runs in another time zone than Home Assistant: the fields work in browser time. What is refused, how to be really back at 23:59 and what happens with other time zones: [How Thriftherm works: away and planned return](HOW_IT_WORKS.md#away-and-planned-return).

## 3. Which temperature applies

For every room Thriftherm picks the first rule that applies:

1. **Manual override** (service `set_override`, or a change on the thermostat) – until it expires.
2. **Summer mode** – frost protection temperature only.
3. **Away** – away temperature (default 15 °C). Before a return with a chosen time the room is pre-heated in time (not for the provisional 23:59, see section 2). If the air gets too close to its dew point (default margin 3 degrees) the target is raised against damp ([damp guard](HOW_IT_WORKS.md#damp-guard)).
4. **Schedule** – comfort temperature inside a schedule block, setback temperature outside. Before a block starts the room is pre-heated so it is warm *when* the block begins.

### Changes by hand

If anyone but Thriftherm changes a setpoint, that is a change by hand: in Home Assistant (dashboard, app, voice assistant), by an automation or a script, or at the device (the knob on a valve, the heat pump's remote, the maker's app). Home Assistant itself says who it was. How Thriftherm reads that: [How Thriftherm works: changes by hand](HOW_IT_WORKS.md#changes-by-hand).

- **It holds for a fixed time** (parameter *How long a change by hand is respected*, 120 minutes by default). On a room thermostat it becomes the room's override; on the heat pump Thriftherm leaves the unit alone for that long. A further change during that time renews it.
- **It is shown at once:** on the room's target sensor (*Override until*, *Override by*) and on the heat pump's command (*Changed by hand, respected until*, *Changed by hand by*).
- **It ends by itself** or with the button *Back to automatic* (action `thriftherm.clear_override` without a room). That ends every override, every quick heat-up and the heat pump's time in hand at once.
- **The knob on a valve behind Better Thermostat:** whether a turn counts is up to its *Child lock* switch there. With it on, the turn does not count. With it off, the number the valve shows after the turn becomes the room's target. Because of Better Thermostat's correction that may be another value than you think ([example](HOW_IT_WORKS.md#turning-the-knob-of-a-valve-behind-a-room-thermostat)).
- **No change by hand** are a command the heat pump did not carry out (it is sent again), a thermostat that rounds to whole degrees, and a thermostat that somebody merely switches off.

While somebody cools, dries or ventilates with the heat pump, Thriftherm stays out for as long as that mode is on. A boiler through entities (section 5) has no change by hand: while the control is active the number and the switch are its own; switch to *Plan only* to set them yourself.

Two protections apply on top, always:

| Protection | Default | Behaviour |
|---|---|---|
| Frost protection | 7 °C | If a room falls below it, the boiler heats immediately – also in summer mode – until the room is 1 degree above. |
| Window open | 90 s grace | Heating in that room pauses while a window contact is open. A contact already open when Thriftherm starts counts at once. |

Comfort and setback temperatures are number entities per room (*Comfort temperature*, *Setback temperature*); you can change them on the dashboard. The setback temperature can never be higher than comfort: turn comfort below it, and the setback follows. The room options show the values in force; if you change either of them there, both values from the form apply.

### Schedules

Each room can use a Home Assistant **schedule helper** (Settings → Devices & services → Helpers → Schedule). Blocks of the helper are the comfort periods. A block may carry a `temperature` attribute to use a different target than the room's comfort temperature. Rooms without a helper use the time ranges from the room options.

### Without a fixed schedule

If you would rather adjust by hand than by the clock:

- **Comfort around the clock:** time range `00:00-23:59` on weekdays and weekends, or a schedule helper with blocks from 00:00 to 24:00 (or to 23:59). Both count as continuous, across midnight as well.
- **Changing the temperature:** change the room's *Comfort temperature* number, for example from a thermostat card or a template thermostat. The setback follows when you go below it.
- **Away/home:** `thriftherm.set_away` (with `clear_return_time: true` open-ended, with `return_time` including pre-heating) and `thriftherm.clear_away`. The comfort temperatures stay as they are.

This even suits the heating curve learning, which learns from long, even heating runs. Each change pauses the learning for only 20 minutes ([section 6](#6-learning)). A target set by hand on the thermostat teaches the curve too, once the room is within 1 degree of it and holds there.

### Pre-heating

Thriftherm starts early enough for the room to be warm when a comfort block begins. For that it takes the gap to the target, the room's learned heat-up rate and a margin (20 minutes by default). The room target sensor shows the planned start in *Pre-heating starts*. Formula and starting values: [How Thriftherm works: pre-heating](HOW_IT_WORKS.md#pre-heating).

## 4. Services

| Service | Fields | Use |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature`, `duration_min` (default 120) | temporarily different target for one room |
| `thriftherm.clear_override` | `room` (optional) | back to automatic: end one override, without a room everything changed by hand |
| `thriftherm.set_away` | `return_time` or `clear_return_time` (both optional) | away mode, with pre-heating for the return or with no end |
| `thriftherm.clear_away` | – | back to Auto |
| `thriftherm.boost` | `room`, `duration_min` (default 45) | quick heat-up of one room |
| `thriftherm.reset_learning` | `scope` (`boiler`, `heat_pump`, `rooms`), `rooms` | forget learned values, see section 6 |

`room` is the room key from the options (for example `bathroom`).

Example – away until Friday 16:00:

```yaml
action: thriftherm.set_away
data:
  return_time: "2026-12-18 16:00:00"
```

## 5. Boiler control (own boiler on ebusd)

A boiler without eBUS can be controlled, as a preview, through a number entity (flow setpoint) and a switch (central heating on/off), see [SETUP.md](SETUP.md) section 4.3a. Everything below holds for both ways; where SetMode is mentioned, Thriftherm writes the number and the switch there.

- **Flow temperature:** from a two-point heating curve (default 55 °C at −10 °C, 30 °C at +15 °C). On top comes what the room that needs the most asks for: its learned correction ([section 6](#6-learning)), up to 8 degrees more while it is far below target, and its extra for the [heat-up speed](#heat-up-speed). The flow never leaves the configured minimum and maximum, and it does not jump ([flow in detail](HOW_IT_WORKS.md#flow)).
- **Calling the boiler:** a room calls the boiler when it is noticeably below target, until it is almost there; not in the last half hour of its comfort period. The attribute *Boiler call* on the room's *Heat demand* sensor shows the state ([thresholds](HOW_IT_WORKS.md#when-a-room-calls-the-boiler)).
- **Heating along:** while the boiler runs anyway, other rooms heat along (reason *Heating along* / *Pre-heating early*). That means longer burner runs and fewer starts ([details](HOW_IT_WORKS.md#heating-along)).
- **Heating is blocked** (`disablehc`) when no room needs heat. Hot water is not touched: the hot water setpoint always goes out as "no value", so the boiler keeps following its own knob. The one exception is yours to choose: with *Keep hot water ready* switched off in the boiler options, the boiler no longer heats for hot water without a tap open (the hourly keep-warm of a combi boiler). Leave it on with a hot water cylinder.
- **Thermostat off or silent:** a thermostat switched off by hand does not call the boiler; its valve is shut. One that is merely unavailable still counts, because its valve keeps regulating on its own. A radiator thermostat silent for over two and a half hours does not call the boiler and takes no new setpoints; a repair entry appears. With Better Thermostat the real thermostat behind it is watched. If Thriftherm takes a healthy thermostat for silent, enable its *Last seen* entity in Zigbee2MQTT ([why](HOW_IT_WORKS.md#outages-and-resending)).
- **Outages:** after a restart Thriftherm waits up to five minutes for the room sensors. If the eBUS signal stays gone for two minutes, Thriftherm stops sending. If Home Assistant stops, the boiler is back on its knob after 9–16 minutes. The knob always stays the upper limit, so set it to the highest flow temperature you want to allow ([setup guide, step 6](SETUP.md#6-at-the-boiler)).
- **Hot water:** Thriftherm recognises by itself when the boiler makes hot water (*Boiler hot water active*), and learning pauses for an hour then ([how](HOW_IT_WORKS.md#hot-water-detection)).
- **Read requests only:** to keep flow and return fresh, Thriftherm has ebusd read them more often. Nothing is written to the boiler for that ([details](HOW_IT_WORKS.md#fresh-readings)).

Sensor *Boiler command* shows the plan (`Heat`, `Heating blocked`, …), the exact `SetMode` payload, the reason, what it is waiting for, whether the heating pump runs and the boiler's status code (`S.xx`, as on its display).

**Why the minimum depends on the boiler type.** A condensing boiler gains from a low flow: below a return of about 56 °C its flue gas condenses and yields up to 11 % more, so about 30 °C is a good minimum. A non-condensing boiler never condenses and saves little there; with short burner runs the heat may not even reach distant radiators. On the tested non-condensing boiler 30 °C left the last radiator cold, 45 °C works: start there with about 45 °C. The setting is *Minimum flow temperature* ([setup guide, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)).

### Heat-up speed

The heating curve is there to hold a temperature. When a room is turned up, heating up on the curve alone can take long. How fast a room is to warm up is set in the boiler options: *Heat-up speed (°C per hour, 0 = off)*, 0 by default.

- A room that calls 1 degree or more below its target gets an extra of its own on the flow while it heats up.
- Every 20 minutes Thriftherm measures how fast the room rises. Too slow: the extra grows. Much too fast: it shrinks. Close to the target it gets smaller.
- What a room needed is where that room's next heat-up starts.
- While a room heats up on an extra, the heating curve learning pauses (reason *a room heats up on an extra*).

Steps, limits and when it measures: [How Thriftherm works: heat-up speed](HOW_IT_WORKS.md#heat-up-speed).

More speed burns more gas. Which value suits:

| Value | Effect |
|---|---|
| **0** (default) | off, the flow as before: heating curve, learned correction and up to 8 degrees more while a room is far below target. Enough when the rooms pre-heat by schedule or nothing is in a hurry |
| **0.5** | gentle: only steps in when a room almost stalls while heating up (less than 0.4 degrees an hour). Costs least |
| **1** | brisk: about a degree an hour. A good start when rooms take too long after being turned up |
| **2** | fast: two degrees an hour. Needs a high flow and the most gas; with small radiators or a low maximum it is not always reached |

The learning state shows *Heat-up extra per room* (the extra while a room heats up, else where its next heat-up starts) and *Rooms heating up on an extra*.

### Why the pump mode matters

With the factory pump mode the pump runs only while the burner fires, plus a short overrun (Vaillant: d.18 = 0, "post-run"). When the rooms take little heat, the boiler stops again within seconds. The water has then only circled through the boiler's bypass: **the radiators far down the circuit stay cold**, although the boiler cycles all day.

With pump mode **permanent** (d.18 = 1) the pump runs whenever heating is released and **stops when Thriftherm blocks heating**. That costs about 33 W, only while heating. On the tested boiler this was the cause of a cold bathroom ([measured](HOW_IT_WORKS.md#ebusd-pump-and-status-values)). To check and change it: [setup guide, step 6](SETUP.md#6-at-the-boiler).

### ebusd: pump and status values

Whether the heating pump runs and which status code (S.xx) the boiler shows, Thriftherm reads from the ebusd values `WP` and `Statenumber`. They only reach Home Assistant if they pass the name filter of the ebusd MQTT integration: [setup guide, step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number). Without them Thriftherm falls back to `Status01` ([why that is not enough](HOW_IT_WORKS.md#ebusd-pump-and-status-values)).

### Without smart thermostats

A room needs a temperature sensor, not a smart thermostat. Everything above still works: demand per room, heating curve, blocking when no room needs heat, learning and frost protection. The room's *Thermostat plan* then gives the reason *No thermostat* and nothing is written to a valve: the manual valves stay open far enough and the flow temperature regulates ([setup guide, step 5](SETUP.md#5-better-thermostat-per-room-optional)). You can add smart thermostats later, room by room; mixing both is fine.

## 6. Learning

**Beta.** The control itself – blocking, flow, safety – is verified on the maintainer's boiler. The learned corrections have so far seen outdoor temperatures down to 2 °C, no frost yet; watch them in your first frosty weeks.

| Area | What is learned | From |
|---|---|---|
| Boiler | heating curve correction, per room and per load class (one room, a few or the majority call) | flow/return spread, how fast rooms warm up, and short cycling |
| Rooms | heat-up rate per room (degrees an hour) | real heating periods with the window closed |
| Rooms | cool-down coefficient per room (1/h) | stretches without heating with the window closed |
| Rooms | heating power per room (degrees an hour without losses) | heat-up rate plus the losses during that period; unlike the raw rate it holds in winter too |
| Heat pump | COP over outdoor temperature, setpoint correction | measured COP runs |

Details on every area: [How Thriftherm works: learning](HOW_IT_WORKS.md#learning).

- **Level and slope:** the heating curve correction has two parts. The **level** holds at every outdoor temperature, the **slope** mostly in the cold, as a heating curve is set by hand. What is learned in autumn therefore hardly touches the winter.
- **Per room and load class:** a small radiator in a cold room needs a hotter flow than a large one, and a few open radiators need another flow than many. So every room has a correction of its own, kept apart for three load classes: one room calls, a few, the majority. The room that needs the most sets the flow ([load classes and values per room](HOW_IT_WORKS.md#load-classes-and-values-per-room)).
- **Careful:** the boiler learns only in *Active* mode, in small steps with waiting times and daily allowances. A value that has served for some hours stays put. Learning pauses for hot water, after a setpoint change (20 minutes), for a quick heat-up, a drying, a heat-up on an extra and while the safety state is not OK ([guards against learning nonsense](HOW_IT_WORKS.md#guards-against-learning-nonsense)).
- **While the boiler cycles,** only the rooms' progress counts ([cycling](HOW_IT_WORKS.md#cycling)).
- **A gas meter is not needed.** Thriftherm also tells the burner by the boiler's state ([telling the burner](HOW_IT_WORKS.md#telling-the-burner)).

**Where you see it:** sensor *Learning state* shows

- the phase (*Learning*, *Refining* or paused), the current correction with level and slope, why it is paused and the last reset. When a setpoint change paused it, it also shows until when and in which room;
- *Rooms calling for heat* and whether the spread judges right now;
- the *Load class now* and the *Room that sets the flow*;
- under *Heating curve correction per load class and room*, for each class and room (entries such as `few:bathroom`), the correction, level, slope, whether the value is found, the hours of operation and how often a room lagged in them.

The sensor *Boiler burner share* shows the share of the last hour the burner ran for the heating, and the number of starts. For now it only records. The sensor *Boiler heating starts today* counts the burner's starts for the heating since midnight; Home Assistant's statistics then show the starts of every day.

**What you can set** (boiler options):

- *Share of rooms …*: the spread judges the curve only while at least this share of the rooms that can take heat right now calls (half by default). Otherwise only the progress of the calling rooms counts.
- *A heating curve of its own per room, for one room, a few rooms and the majority calling*: switched off, one common correction holds for all rooms and classes; the values per class and room are kept.

### Forgetting what was learned

After a change to the installation old values no longer fit.

- **By hand:** Options → *Reset learning*, tick the areas (and rooms), or call `thriftherm.reset_learning`.
- **Automatically:** when you change a setting a learned area depends on, only that area is forgotten:

| Change | Forgotten |
|---|---|
| heating curve | boiler correction |
| heat pump entity, intake/outlet sensor, airflow curve, duct factor, installation room, served rooms | COP map and heat pump correction |
| a room's temperature sensor or thermostat | that room's heat-up rate |

Run state, lockout times and manual holds are never reset, so a reset cannot make a unit cycle.

## 7. Heat pump (optional)

It has been tried on a Midea PortaSplit, icing and defrosting included; other units not yet.

- **COP:** Thriftherm measures it from the air the heat pump takes in and blows out, and from its electricity. When it cycles, the whole cycle counts, off time included (attribute *COP over the last on/off cycle*); that value decides the cost ([measuring the COP](HOW_IT_WORKS.md#measuring-the-cop)).
- **Cost:** the heat pump runs slowly at a high COP. It is compared with the price of central heat. For district heating the **allocation key** matters: only the consumption share follows your meter. A saved kWh therefore saves less than its price, and the heat pump must reach a higher COP to pay off.
- **Location and ducts:** without ducts the warm air stays in the room the unit is in. Configure the installation room and the duct factor (1.0 = no ducts); rooms it cannot reach get a repair notice. The duct factor corrects the airflow, not the heat: what the ducts give off on the way stays in the home.
- **Locked out** is the heat pump below its minimum outdoor temperature (default −10 °C), for icing or frequent defrosts, when somebody cools with it, and when its measured COP stays below the *Break-even COP* for a while, so that it heats dearer than the central heating. How long the lockouts for icing and inefficiency last is set with *Lockout after icing / inefficiency* (two hours by default). In the mode *Heat pump only* there is no lockout for inefficiency ([lockouts](HOW_IT_WORKS.md#lockouts)).
- **Cooling and heating never overlap:** while it cools, the rooms it serves keep their radiators at the setback temperature and call no boiler heat. Frost protection still applies.
- **Quick heat-up** (button per room or `thriftherm.boost`) runs it at full power for a limited time.
- **Fan:** Thriftherm sets it only on a start (to *auto*), for a drying or a quick heat-up, and back afterwards. A stage you choose by hand otherwise stays. From heating it never goes straight to off: the fan runs alone for 15 minutes first (parameter, 0 = off at once). A mode set by hand during that time ends the run-on.

**Bathroom drying** needs the heat pump: only in a room it heats and that has a humidity sensor. The room form offers it only once a heat pump is set up.

- Drying comes **after** the shower: when the humidity rises, Thriftherm waits until it stops rising. Then the heat pump blows with its strongest fan stage and the drying target temperature, at most 60 minutes (default) and only while it is actively controlled and can heat and run efficiently. Afterwards the fan gets back the stage it had before.
- The button *Dry now* starts at once and for the full time, for laundry or a wet floor; *Back to automatic* ends the drying. If you want the button only, switch off *Start the drying by itself after a shower* in the room form.
- The moisture does not leave the home that way: the warm air dries walls and mirror and spreads the moisture; it only leaves when you air. The quickest way is a window opened briefly after the shower. If it stays open long without the humidity falling, the drying ends. The radiator always follows the normal window logic.

When it starts and ends: [bathroom drying](HOW_IT_WORKS.md#bathroom-drying).

**Setpoint follows the rooms** (switch in the heat pump form): the heat pump gets the highest target of the rooms it serves plus the setpoint offset (1 degree by default) and regulates by itself. Comfort 21 °C gives 22 °C on the unit, the night setback of 17 °C gives 18 °C.

- It stays on, day and night, for as long as it is the cheaper source, and goes off only while nobody wants the rooms it serves warm (away, off, a window open in each of them). So while no room needs heat, the *Decision reason* reads "heat pump runs through, setpoint 22.0 °C" and not "no room needs heat".
- *Quick heat-up* then only raises the fan.
- While drying, the unit is set at least 1 degree above its own reading, at most 2 degrees above the drying target, so that it runs, fan and all ([details](HOW_IT_WORKS.md#bathroom-drying)).
- When a served room falls behind, Thriftherm sets the unit higher in small steps (attribute `eco_offset_k`) and back again later. The boiler helps only once that is not enough (attribute `boiler_helps`), and only until the room is warm ([details](HOW_IT_WORKS.md#setpoint-follows-the-rooms)).

Without the switch Thriftherm uses the setpoint as a power lever (intake air plus a learned offset) and switches by the rooms' demand ([details](HOW_IT_WORKS.md#without-setpoint-follows-the-rooms)).

## 8. Dashboard sensors worth knowing

| Entity | Tells you |
|---|---|
| *Decision reason* | in plain words why the system does what it does |
| *Recommended heat source* | central heating (boiler or district heating), heat pump, both or none |
| *Safety state* | OK, degraded (a sensor is missing), fallback (no room or no trustworthy room temperature, nothing is sent). Heat pump faults are listed in the attributes, but degrade the state only where the heat pump is the only heat source. |
| *Target temperature* per room | the target, and in *Reason* where it comes from |
| *Thermostat plan* per room | what is handed to the thermostat |
| *Boiler command* | what the boiler gets; *Knob in charge* means nothing is sent and the boiler runs on its own knob. Also shows the heating pump and the status code S.xx |
| *Learning state* | what has been learned and why learning pauses |
| *Gas for heating*, *Gas for hot water* | how much gas (kWh) went into the heating and how much into hot water since setup. Measured with a gas meter: each step of the meter counts for what the boiler is doing at that moment; what was burnt while Home Assistant was not running is under *Not assigned*. Without a gas meter, estimated for Vaillant boilers from their own counters ([setup, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)). Both fit the energy dashboard |
| *Planned return* | when you expect to be back; editable (date first, then time), empty when nothing is planned. *Provisional (23:59 placeholder)* (`provisional`) is `true` while only a date is picked: away then ends at 23:59, nothing is pre-heated yet |

**Detail sensors start disabled.** Temperature deviation, temperature trend, dew point and absolute humidity per room, the flow/return spread, the boiler's thermal power estimate and the heat pump's airflow estimate change with almost every cycle and serve the analysis, not the control. Each change is a row in Home Assistant's database, so on a new installation they are disabled; enable the ones you want under *Settings → Devices & services → Entities*. Thriftherm computes them either way.

## 9. Repairs

| Notice | Meaning and fix |
|---|---|
| *Room*: no heating window configured | neither schedule helper nor time range – the room stays at setback. Select a schedule in the room options. |
| *Room*: schedule helper missing | the selected schedule was deleted. Recreate it or pick another. |
| *Room*: radiator thermostat not responding | a valve has not reported for two and a half hours although it was sent commands. Usually the radio link or the battery; the room does not call the boiler meanwhile. Check the battery, pair the valve again or switch it off and on. |
| No data from the boiler | ebusd delivers nothing usable; nothing is sent, the boiler runs on its knob. Check the ebusd app and adapter. |
| Heat pump airflow not calibrated | no airflow curve, so no COP; the data sheet is used meanwhile, or the *COP values of this model* if picked. |
| Heat pump assigned to rooms it cannot reach | no ducts configured, so the warm air stays in its own room. Fit ducts and set the duct factor, or untick those rooms. |

Some problems show up without a notice: a room sensor quiet for six hours no longer counts, the valve's own temperature stands in (behind Better Thermostat the valve itself, not Better Thermostat, which repeats the room sensor), and *Safety state* goes to *Degraded*.

Settings → Devices & services → Thriftherm → ⋮ → *Download diagnostics* gives a file with configuration, current state and learned values for bug reports. Nothing is removed from it, so look through it before posting it publicly.

## 10. Questions

Answers to common questions and what to do when something goes wrong are on a page of their own: [Troubleshooting and FAQ](TROUBLESHOOTING.md).
