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
| **Away** | all rooms are held at the away temperature; with a return time they are pre-heated in time. Setting *Planned return* (a date and time entity) switches to away until then; `thriftherm.set_away` does the same from an automation, and the action *Clear away* (`thriftherm.clear_away`) ends both |

## 3. Which temperature applies

For every room Thriftherm picks the first rule that applies:

1. **Manual override** (service `set_override`, or a change on the thermostat) – until it expires.
2. **Summer mode** – frost protection temperature only.
3. **Away** – away temperature (default 15 °C). Before the return time the room is pre-heated to its comfort temperature. If the air gets too close to its dew point (default margin 3 K) the target is raised against damp.
4. **Schedule** – comfort temperature inside a schedule block, setback temperature outside. Before a block starts the room is pre-heated so it is warm *when* the block begins.

Two protections apply on top, always:

| Protection | Default | Behaviour |
|---|---|---|
| Frost protection | 7 °C | If a room falls below it, the boiler heats immediately – also in summer mode – until the room is 1 K above. |
| Window open | 90 s grace | Heating in that room pauses while a window contact is open. |

Comfort and setback temperatures are number entities per room (*Comfort temperature*, *Setback temperature*); you can change them on the dashboard. The setback temperature can never be higher than comfort.

### Schedules

Each room can use a Home Assistant **schedule helper** (Settings → Devices & services → Helpers → Schedule). Blocks of the helper are the comfort periods. A block may carry a `temperature` attribute to use a different target than the room's comfort temperature. Rooms without a helper use the time ranges from the room options.

### Pre-heating

Pre-heating starts at `start = block start − (target − current) / heat-up rate − margin`. The heat-up rate is learned per room; until then 0.8 K/h is assumed for radiator rooms and 1.2 K/h for heat pump rooms, with a 20-minute margin. The room target sensor shows the planned start in *Pre-heating starts*.

## 4. Services

| Service | Fields | Use |
|---|---|---|
| `thriftherm.set_override` | `room`, `temperature`, `duration_min` (default 120) | temporarily different target for one room |
| `thriftherm.clear_override` | `room` (optional) | end one or all overrides |
| `thriftherm.set_away` | `return_time` (optional) | away mode, with pre-heating for the return |
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
- **Heating is blocked** (`disablehc`) when no room needs heat. Hot water is never touched: the hot water setpoint is always sent as “no value”, so the boiler keeps following its own knob.
- **A thermostat switched off by hand does not call the boiler**: its valve is shut, so the room is left out of the demand. A thermostat that is merely unavailable still counts, because its valve keeps regulating on its own.
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
| heating curve, minimum or maximum flow | boiler correction |
| heat pump entity, intake/outlet sensor, airflow curve, duct factor, installation room, served rooms | COP map and heat pump correction |
| a room's temperature sensor or thermostat | that room's heat-up rate |

Run state, lockout times and manual holds are never reset, so a reset cannot make a unit cycle.

## 7. Heat pump (optional)

> **Preview – coming soon.** Not yet tested with a real heat pump – do not rely on it. It has only ever run in *Plan only* mode and has never controlled a heat pump. The points below describe what it is built to do.

- The COP is measured from the airflow (fan speed → m³/h curve) and the temperature and humidity of intake and outlet air, only once the unit has settled.
- It is compared with the price of central heat. For district heating the **allocation key** matters: only the consumption share follows your meter, so a saved kWh saves less than its price and the heat pump must reach a higher COP to pay off.
- The heat pump runs slowly at a high COP. It is blocked below its minimum outdoor temperature (default −10 °C), when icing is detected, or when the user cools with it. Cooling and heating never overlap.
- **Where it stands matters.** Without ducts the warm air stays in the room the unit is in. Configure the installation room and the duct factor (1.0 = no ducts); rooms it cannot reach get a repair notice.
- **Bathroom drying** needs the heat pump: only in a room the heat pump heats and that has a humidity sensor; the room form offers it only once a heat pump is set up. After a shower the heat pump dries the room – only while it can heat and run efficiently, at most 60 minutes, and never above target + 1.5 K. Otherwise the radiator follows the normal window logic.
- **Quick heat-up** (button per room or `thriftherm.boost`) runs it at full power for a limited time.

## 8. Dashboard sensors worth knowing

| Entity | Tells you |
|---|---|
| *Decision reason* | in plain words why the system does what it does |
| *Recommended heat source* | boiler, heat pump, both or none |
| *Safety state* | OK, degraded (a sensor is missing), fallback (nothing trustworthy, nothing is sent) |
| *Target temperature* per room | the target, and in *Reason* where it comes from |
| *Thermostat plan* per room | what is handed to the thermostat |
| *Boiler command* | what the boiler gets; *Knob in charge* means nothing is sent and the boiler runs on its own knob. Also shows the heating pump and the status code S.xx |
| *Learning state* | what has been learned and why learning pauses |
| *Planned return* | when you expect to be back; editable, empty when nothing is planned |

## 9. Repairs

| Notice | Meaning and fix |
|---|---|
| *Room*: no heating window configured | neither schedule helper nor time range – the room stays at setback. Select a schedule in the room options. |
| *Room*: schedule helper missing | the selected schedule was deleted. Recreate it or pick another. |
| No data from the boiler | ebusd delivers nothing usable; nothing is sent, the boiler runs on its knob. Check the ebusd app and adapter. |
| Heat pump airflow not calibrated | no airflow curve, so no COP; the data sheet is used meanwhile. |
| Heat pump assigned to rooms it cannot reach | no ducts configured, so the warm air stays in its own room. Fit ducts and set the duct factor, or untick those rooms. |

Some problems show up without a notice: a room sensor quiet for six hours no longer counts, the thermostat's own temperature stands in, and *Safety state* goes to *Degraded*.

Settings → Devices & services → Thriftherm → ⋮ → *Download diagnostics* gives a file with configuration, current state and learned values for bug reports. Nothing is removed from it, so look through it before posting it publicly.

## 10. Questions

**Why is a room at 15 °C?** It is in away mode. Frost protection (7 °C) is a separate lower limit.

**I turned the thermostat by hand – why does it not change back?** A manual change becomes a temporary override (default 120 minutes). End it with `clear_override`.

**Does Thriftherm change my hot water temperature?** No. It sends “no value” for the hot water setpoint; the boiler's knob decides.

**What if Home Assistant crashes?** The boiler falls back to its knob within 9–16 minutes; thermostats keep their last target.

**Why is *Temperature trend* empty after a restart?** The trend needs about 30 minutes of readings.
