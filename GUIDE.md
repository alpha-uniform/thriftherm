# Thriftherm user guide

**English** | [Deutsch](ANLEITUNG.md)

> **Setting up for the first time?** Start with the [setup guide](SETUP.md).

This guide explains how to live with Thriftherm day to day: what the modes do, which temperatures apply when, how learning works and what to do when Home Assistant reports a problem. The [README](README.en.md) explains the idea and the hardware.

**Language.** Thriftherm speaks German when Home Assistant is set to German and English for every other language – entity names, states, attributes, the decision reason, dialogs, repairs and error messages. Entity IDs are always English (`sensor.thriftherm_boiler_command`), so automations and dashboards keep working whatever the language.

## 1. How it fits together

| Part | Job | Needs |
|---|---|---|
| Rooms | Work out a target temperature per room from schedule, mode, overrides and protection rules | a temperature sensor per room |
| Room control | Hand those targets to the room thermostats | Better Thermostat per room |
| Boiler control | Give the boiler a flow temperature, or block space heating when no room needs heat | own boiler on ebusd |
| Heat pump (optional, preview) | Measure its COP, compare costs, run it slowly and efficiently | a climate entity and a few sensors |

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
| **Boiler only** | the heat pump is not used |
| **Heat pump only** | only heat pump rooms are heated; the boiler only for frost protection. Preview, see section 7 |
| **Summer (heating off)** | no space heating; hot water keeps working; frost protection stays |
| **Away** | all rooms are held at the away temperature; with a return time they are pre-heated in time, see below |

### Away with and without a return

- **Just away, no end:** choose *Away* as the operating mode while another mode is on. *Planned return* stays empty, and the rooms stay at the away temperature until you choose *Clear away* or another mode. If *Away* with a return is already on, choosing *Away* again changes nothing: choose another mode first and then *Away* again, or call `thriftherm.set_away` with `clear_return_time: true`.
- **Away until a point in time**, e.g. today 22:00 or 3 October 12:00: set *Planned return*, which switches to away until then. In Home Assistant's fields (the entity's dialog, the entities card) pick the **date** first, then the **time** – while nothing is planned, the time field takes nothing.
- **A date alone is a placeholder:** until a time is picked, the return stands at 23:59 of the chosen day for the moment, today as on any later day; another date takes the provisional 23:59 along. A provisional return does not pre-heat; only the time you pick next counts, and the rooms are pre-heated for that. If you pick none, away still ends at 23:59. The attribute *Provisional (23:59 placeholder)* (`provisional`) of *Planned return* shows which it is: `true` while only the date is picked.
- **Changing the date when a time is already planned:** the new date keeps the planned time and counts at once. If you pick today and that time has already passed, it shows the provisional 23:59 again.
- **What is refused:** a time of today that has already passed (except 00:00 in the minute after a return is reached, which becomes the 23:59 placeholder while 23:59 is still ahead), a past day and, once it is past 23:59, today's date alone – each with a message.
- **Really back at 23:59:** while the provisional 23:59 is shown, the time field sends nothing for 23:59 because nothing changes, and a 23:59 that arrives anyway (say through `datetime.set_value`) stays provisional. Pick another minute first, e.g. 23:58, and 23:59 only once that minute shows and the attribute *Provisional* is `false` – or use `thriftherm.set_away` with `return_time`, which sets date and time in one step.
- **Browser in another time zone:** both fields show and work in the browser's time zone, so date and time count in browser time. Examples with Home Assistant in Berlin:
  - While nothing is planned, the date step sends the browser's midnight, which is another time of day in Home Assistant (London: 01:00; east of Home Assistant on the day before, Helsinki 23:00, Tokyo 17:00, 16:00 in winter). That is no placeholder then but a real time: if it has passed, as it mostly has for today, it is refused; otherwise it counts as a return at that hour, with pre-heating.
  - With a return planned, a time picked in the time field counts in browser time (12:00 picked in London is 13:00 in Berlin), and the day can shift too: east of Home Assistant the provisional 23:59 already shows as the next day (Helsinki: 00:59), so a time picked then lands a day later. West of it this can hit returns shortly after midnight (3 October 00:30, moved to 5 October in London: 6 October 00:30).
  - If the browser and Home Assistant do not change to or from summer time on the same day – say a browser in the US, or in a zone without summer time such as Dubai – a date step alone across a change can move the time by an hour. A provisional return then becomes real, and the rooms pre-heat for it (provisional 26 October 23:59, moved to 3 November in New York: 4 November 00:59).
  - So after each entry check *Planned return* and its attribute *Provisional*, set the browser to Home Assistant's time zone, or use `thriftherm.set_away`.
- **From automations:** `thriftherm.set_away` with `return_time`, or with `clear_return_time: true` for no end. There every time in the past is an error, including one of today, and every time counts as it comes, midnight too. `datetime.set_value` on *Planned return* reads values the way the fields send them instead: midnight while nothing is planned becomes the 23:59 placeholder, on a later day too. And while *Planned return* is unavailable (Thriftherm not loaded), Home Assistant drops `datetime.set_value` without an error, where `set_away` reports one. So automations should use `set_away`.
- **Back home:** the action *Clear away* (`thriftherm.clear_away`) ends the absence and the return. Once the return time is reached, Thriftherm goes back to Auto by itself.

## 3. Which temperature applies

For every room Thriftherm picks the first rule that applies:

1. **Manual override** (service `set_override`, or a change on the thermostat) – until it expires.
2. **Summer mode** – frost protection temperature only.
3. **Away** – away temperature (default 15 °C). Before a return with a chosen time the room is pre-heated to its comfort temperature (not for the provisional 23:59, see section 2). If the air gets too close to its dew point (default margin 3 K) the target is raised against damp.
4. **Schedule** – comfort temperature inside a schedule block, setback temperature outside. Before a block starts the room is pre-heated so it is warm *when* the block begins.

Two protections apply on top, always:

| Protection | Default | Behaviour |
|---|---|---|
| Frost protection | 7 °C | If a room falls below it, the boiler heats immediately – also in summer mode – until the room is 1 K above. |
| Window open | 90 s grace | Heating in that room pauses while a window contact is open. |

Comfort and setback temperatures are number entities per room (*Comfort temperature*, *Setback temperature*); you can change them on the dashboard. The setback temperature can never be higher than comfort. If you later change either of them in the room options, both values from the options apply again.

### Schedules

Each room can use a Home Assistant **schedule helper** (Settings → Devices & services → Helpers → Schedule). Blocks of the helper are the comfort periods. A block may carry a `temperature` attribute to use a different target than the room's comfort temperature. Rooms without a helper use the time ranges from the room options.

### Pre-heating

Pre-heating starts at `start = block start − (target − current) / heat-up rate − margin`. The heat-up rate is learned per room; until then 0.8 K/h is assumed for radiator rooms and 1.2 K/h for heat pump rooms, with a 20-minute margin. The room target sensor shows the planned start in *Pre-heating starts*.

## 4. Services

| Service | Fields | Use |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature`, `duration_min` (default 120) | temporarily different target for one room |
| `thriftherm.clear_override` | `room` (optional) | end one or all overrides |
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

- **Flow temperature** comes from a two-point heating curve (default 55 °C at −10 °C, 30 °C at +15 °C), raised by up to 8 K when rooms are far below target, and corrected by a learned offset. It never leaves the configured minimum and maximum flow temperature.
  **Why the minimum depends on the boiler type.** A condensing boiler gains from a low flow: below a return of about 56 °C its flue gas condenses and yields up to 11 % more, so about 30 °C is a good minimum. A non-condensing boiler never condenses, so a very low flow saves little there – and with short burner runs the heat may not even reach distant radiators. Measured on the tested non-condensing boiler: 30 °C left the last radiator cold, 45 °C works, so start there with about 45 °C. The setting is *Minimum flow temperature* ([setup guide, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)).
- **When a room calls the boiler:** from 0.3 K below target until it is less than 0.1 K below. A room that hangs just below target after an hour of heating (at most 0.3 K) and barely rises any more (below 0.15 K/h) counts as reached, until it falls 0.5 K below target or its target changes. When the target goes down (an override ends, say), a running call is judged again by the start threshold. No room calls in the last half hour of a comfort period, the heat would arrive too late. *Quick heat-up* is exempt from both rules. The attribute *Boiler call* on the room's *Heat demand* sensor shows the state.
- **Heating along:** while the boiler runs anyway, other rooms in their comfort period that are below target get 0.5 K more, and a room whose preheat would start within the hour starts now (reason *Heating along* / *Pre-heating early*). One radiator takes about 1 kW while the boiler burns at 8 kW or more; more open radiators mean longer burner runs and fewer starts. Rooms that heat along never call by themselves and cannot keep the boiler running.
- **Heating is blocked** (`disablehc`) when no room needs heat. Hot water is never touched: the hot water setpoint is always sent as “no value”, so the boiler keeps following its own knob.
- **A thermostat switched off by hand does not call the boiler**: its valve is shut, so the room is left out of the demand. A thermostat that is merely unavailable still counts, because its valve keeps regulating on its own.
- **A radiator thermostat silent for over an hour does not call the boiler either.** It keeps its last setpoint and takes no new ones. With Better Thermostat the real thermostat behind it is watched, not BT itself. A repair entry appears in HA. Healthy thermostats here report at least every 15 minutes.
- **After a restart** Thriftherm waits up to five minutes for the room sensors before it blocks heating. Until then it sends nothing and the boiler keeps its last command.
- **Rises are limited** to 5 K per 10 minutes, so the boiler does not jump from 30 to 60 °C. Frost protection ignores the limit.
- **Short dropouts are tolerated**: adapters lose the eBUS signal for a second now and then. Only a signal that stays gone for two minutes counts as a data outage and hands the boiler back to its knob.
- **Resend** every 2 minutes. If Home Assistant stops, the boiler returns to its knob after 9–16 minutes, and the knob always stays the upper limit – which is why it is set to the highest flow temperature you want to allow ([setup guide, step 6](SETUP.md#6-at-the-boiler)).
- **Hot water detection**: ebusd does not report hot water reliably on every boiler, so Thriftherm recognises it itself: gas burning although heating is blocked, a flow far above the heating flow that was sent, a return above the flow, or a flow above the heating maximum. The first two react within a minute; the temperatures only arrive every few minutes. *Boiler hot water active* shows the result, and learning pauses for an hour so shower water does not teach the heating curve.
- **Fresh readings**: ebusd reads the boiler's values one after another, so flow and return would otherwise be minutes old. Thriftherm raises the poll priority of flow, return, the boiler state, the heating pump and the status code in ebusd (renewed every 10 minutes, so an ebusd restart hardly shows) and requests them directly once a minute while gas is burning. These are read requests only; nothing is written to the boiler.

Sensor *Boiler command* shows the plan (`Heat`, `Heating blocked`, …), the exact `SetMode` payload, the reason, what it is waiting for, whether the heating pump runs and the boiler's status code (`S.xx`, as on its display).

### Why the pump mode matters

With the factory pump mode the pump runs only while the burner fires, plus a short overrun (Vaillant: d.18 = 0, "post-run"). When the rooms take little heat, the boiler reaches its setpoint within seconds, stops, and the water has only circled through the boiler's bypass: **the radiators far down the circuit stay cold**, although the boiler cycles all day.

On the tested boiler this was the cause of a cold bathroom. With pump mode **permanent** (d.18 = 1) the pump runs whenever heating is released and **stops when Thriftherm blocks heating** — about 33 W, only while heating. The bathroom warmed from 20.8 to 21.5 °C within an hour, where it had not moved all afternoon before.

How to check and change it is a one-time step in the [setup guide, step 6](SETUP.md#6-at-the-boiler).

### ebusd: pump and status values

The last field of the ebusd message `Status01` is called the pump state, but it follows the heating demand: it read "off" while the pump was audibly running. The pump itself is **`WP`** (d.10), the display code is **`Statenumber`** (8 = S.8 burner anti-cycling, 7 = S.7 pump overrun, 31 = S.31 no heat demand). `Status01` still tells hot water apart and stays in use for that.

Both only reach Home Assistant if they pass the name filter of the ebusd MQTT integration; how to let them through: [setup guide, step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number). Without them Thriftherm falls back to `Status01`.

### Without smart thermostats

A room needs a temperature sensor, not a smart thermostat. Everything above still works: demand per room, heating curve, blocking when no room needs heat, learning and frost protection. The room's *Thermostat plan* then gives the reason *No thermostat* and nothing is written to a valve: the manual valves stay open far enough and the flow temperature regulates ([setup guide, step 5](SETUP.md#5-better-thermostat-per-room-optional)). You can add smart thermostats later, room by room; mixing both is fine.

## 6. Learning

**Beta.** The control itself – blocking, flow, safety – is verified on the maintainer's boiler. The learned corrections have so far only seen mild weather; watch them in your first cold weeks.

| Area | What is learned | From |
|---|---|---|
| Boiler | heating curve correction (±10 K) | flow/return spread, how fast rooms warm up, and short cycling |
| Rooms | heat-up rate per room (K/h) | real heating periods with the window closed |
| Rooms | cool-down coefficient per room (1/h) | stretches without heating, window closed, at least 5 K warmer inside than out |
| Rooms | heating power per room (K/h without losses) | heat-up rate plus the losses during that episode; unlike the raw rate it holds in winter too |
| Heat pump (preview) | COP map over outdoor temperature, setpoint correction | measured COP runs |

**Short cycling counts too:** a burner that runs for a minute and then waits a quarter of an hour delivers far more than the rooms take, so the flow is too hot. Three such runs within an hour lower the curve by one step — the spread would never settle in such a run, so this is the only signal available in mild weather. It never goes below the minimum flow temperature.

Guards against learning nonsense: the boiler learns only in *Active* mode, 10 minutes after the burner starts, from at least 10 samples, at most one change per hour and four per day, never while hot water is made, a setpoint changed in the last 20 minutes, a boost or drying runs, or the safety state is not OK. It starts in **Learning** (1 K steps) and moves to **Refining** (0.5 K) after six changes or a quiet day.

Sensor *Learning state* shows the phase, the current correction, why it is paused and the last reset.

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

> **Preview – coming soon.** Not yet tested with a real heat pump – do not rely on it. It has only ever run in *Plan only* mode and has never controlled a heat pump. The points below describe what it is built to do.

- The COP is measured from the airflow (fan speed → m³/h curve) and the temperature and humidity of intake and outlet air, only once the unit has settled.
- It is compared with the price of central heat. For district heating the **allocation key** matters: only the consumption share follows your meter, so a saved kWh saves less than its price and the heat pump must reach a higher COP to pay off.
- The heat pump runs slowly at a high COP. It is blocked below its minimum outdoor temperature (default −10 °C), when icing is detected, or when the user cools with it. Cooling and heating never overlap: while it cools, the rooms it serves keep their radiators at the setback temperature and call no boiler heat. Frost protection still applies.
- **Where it stands matters.** Without ducts the warm air stays in the room the unit is in. Configure the installation room and the duct factor (1.0 = no ducts); rooms it cannot reach get a repair notice.
- **Bathroom drying** needs the heat pump: only in a room the heat pump heats and that has a humidity sensor; the room form offers it only once a heat pump is set up. After a shower the heat pump dries the room – only while it can heat and run efficiently, at most 60 minutes, and never above target + 1.5 K. Otherwise the radiator follows the normal window logic.
- **Quick heat-up** (button per room or `thriftherm.boost`) runs it at full power for a limited time.

## 8. Dashboard sensors worth knowing

| Entity | Tells you |
|---|---|
| *Decision reason* | in plain words why the system does what it does |
| *Recommended heat source* | boiler, heat pump, both or none |
| *Safety state* | OK, degraded (a sensor is missing), fallback (no room or no trustworthy room temperature, nothing is sent). Heat pump faults are listed in the attributes, but degrade the state only where the heat pump is the only heat source. |
| *Target temperature* per room | the target, and in *Reason* where it comes from |
| *Thermostat plan* per room | what is handed to the thermostat |
| *Boiler command* | what the boiler gets; *Knob in charge* means nothing is sent and the boiler runs on its own knob. Also shows the heating pump and the status code S.xx |
| *Learning state* | what has been learned and why learning pauses |
| *Planned return* | when you expect to be back; editable (date first, then time), empty when nothing is planned. *Provisional (23:59 placeholder)* (`provisional`) is `true` while only a date is picked: away then ends at 23:59, nothing is pre-heated yet |

**Detail sensors start disabled.** Temperature deviation, temperature trend, dew point and absolute humidity per room, the flow/return spread, the boiler's thermal power estimate and the heat pump's airflow estimate change with almost every cycle and serve the analysis, not the control. Each change is a row in Home Assistant's database, so on a new installation they are disabled; enable the ones you want under *Settings → Devices & services → Entities*. Thriftherm computes them either way.

## 9. Repairs

| Notice | Meaning and fix |
|---|---|
| *Room*: no heating window configured | neither schedule helper nor time range – the room stays at setback. Select a schedule in the room options. |
| *Room*: schedule helper missing | the selected schedule was deleted. Recreate it or pick another. |
| No data from the boiler | ebusd delivers nothing usable; nothing is sent, the boiler runs on its knob. Check the ebusd app and adapter. |
| Heat pump airflow not calibrated | no airflow curve, so no COP; the data sheet is used meanwhile. |
| Heat pump assigned to rooms it cannot reach | no ducts configured, so the warm air stays in its own room. Fit ducts and set the duct factor, or untick those rooms. |

Some problems show up without a notice: a room sensor quiet for six hours no longer counts, the valve's own temperature stands in (behind Better Thermostat the valve itself, not Better Thermostat, which repeats the room sensor), and *Safety state* goes to *Degraded*.

Settings → Devices & services → Thriftherm → ⋮ → *Download diagnostics* gives a file with configuration, current state and learned values for bug reports. Nothing is removed from it, so look through it before posting it publicly.

## 10. Questions

**Why is a room at 15 °C?** It is in away mode. Frost protection (7 °C) is a separate lower limit.

**I turned the thermostat by hand – why does it not change back?** A manual change becomes a temporary override (default 120 minutes). End it with `clear_override`.

**Overrides although nobody touched the thermostat?** Better Thermostat rewrites the valve setpoint every few seconds. When a Zigbee reply arrives late, BT takes the old value for a turn of the knob. The **echo filter** (Options → Control parameters, on by default) recognises this: when BT jumps to a value the valve already showed within the last minute and left again (e.g. 16 → 16.5 → 16), the jump is ignored and the planned setpoint is sent again. A real turn of the knob brings a new value and is taken over. The room's *Thermostat plan* sensor shows when an echo was last ignored.

**Does Thriftherm change my hot water temperature?** No. It sends “no value” for the hot water setpoint; the boiler's knob decides.

**What if Home Assistant crashes?** The boiler falls back to its knob within 9–16 minutes; thermostats keep their last target.

**What if ebusd cannot reach the boiler?** After two minutes without *ebusd signal* Thriftherm stops sending (*Boiler command*: *No setpoint sent*), and the boiler heats by its knob. After ten minutes the repair entry *No data from the boiler* appears. The most common cause is a new IP address of the eBUS adapter, so give it a fixed address in the router ([setup, step 2.3](SETUP.md#23-configure-the-ebusd-app)). Note: during an outage the eBUS values in Home Assistant stay at their last reading; only *ebusd signal* counts.

**Why is *Temperature trend* empty after a restart?** The trend needs about 30 minutes of readings. On a new installation the sensor is disabled until you enable it (section 8).
