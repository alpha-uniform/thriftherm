# Thriftherm setup guide

**English** | [Deutsch](EINRICHTUNG.md)

This guide takes you from nothing to a working Thriftherm installation, one step at a time. You do not need to know ebusd or Home Assistant internals.

**Nothing is sent to your boiler or thermostats until you switch to *Active* yourself in step 7.** Until then Thriftherm only calculates and shows what it would do, so you can try every step safely.

| Step | What you do | Where |
|---|---|---|
| [1](#1-what-you-need) | Check you have everything | – |
| [2](#2-set-up-ebusd) | Connect Home Assistant to the boiler | ebusd app |
| [3](#3-install-thriftherm) | Install Thriftherm | HACS |
| [4](#4-add-the-integration) | Walk through the setup dialog | Home Assistant |
| [5](#5-better-thermostat-per-room-optional) | One Better Thermostat per room | Better Thermostat |
| [6](#6-at-the-boiler) | Set the knob, check the pump mode | at the boiler |
| [7](#7-plan-only-first-then-active) | Watch for a few days, then go active | Home Assistant |
| [8](#8-troubleshooting) | If something does not work | – |

Have district heating or no own boiler? Skip steps 2 and 6; the setup dialog leaves out the boiler for you.

## 1. What you need

| Item | Needed? | Notes |
|---|---|---|
| Boiler with eBUS | with an own boiler | for boiler control |
| eBUS adapter | with an own boiler | for example an ebusd adapter shield, connected to the boiler's eBUS terminals |
| Home Assistant 2026.9 or newer | yes | *Settings → About* shows your version |
| MQTT broker | with an own boiler | the **Mosquitto broker** app |
| **ebusd** app | with an own boiler | reads the boiler and sends it the setpoint |
| One temperature sensor per room | yes | any sensor that shows the room temperature in Home Assistant |
| Window contacts | optional | heating in a room pauses while its window is open |
| Humidity sensors | optional | protection against damp while you are away |
| Radiator thermostats with **Better Thermostat** | optional | needed if Thriftherm should set the room temperatures; without them the boiler control still works |
| Gas meter reading | optional | total volume (m³) and current flow (m³/h), if your gas meter is in Home Assistant |
| Outdoor temperature sensor | optional | otherwise a weather entity is used |

**A central room controller on the eBUS terminal has to go.** If a room controller is already connected to your boiler's eBUS terminal, disconnect it; the eBUS adapter goes on the same terminal. Thriftherm takes over the controller's job, and two controllers on the eBUS would keep overwriting each other's setpoints.

The **heat pump** add-on is a preview and not yet tested with a real heat pump. You can skip it completely. If you want to try it, it needs the heat pump's climate entity, a smart plug measuring its power, the temperature and humidity of its intake air and the temperature of its outlet air.

## 2. Set up ebusd

### 2.1 Install the MQTT broker

1. Open *Settings → Apps* and install **Mosquitto broker**. Start it.
2. Home Assistant then offers the **MQTT** integration under *Settings → Devices & services*. Click *Configure* and confirm.

**Check:** *Settings → Devices & services* lists **MQTT**.

### 2.2 Install the ebusd app

1. In *Settings → Apps*, open the app store, then ⋮ → *Repositories* and add `https://github.com/LukasGrebe/ha-addons`.
2. Install **ebusd**. Do not start it yet.

### 2.3 Configure the ebusd app

Open the **Configuration** tab of the ebusd app:

| Setting | What to enter |
|---|---|
| Adapter / device | your eBUS adapter: the USB device, or for a network adapter its address, for example `ens:192.0.2.10:9999` |
| Command-line options | add `--accesslevel=*` – needed so Thriftherm can send the boiler its setpoint |
| MQTT | the app finds the Mosquitto broker automatically. If not, enter its host, user and password |
| Copy `mqtt-hassio.cfg` (`seed_mqtt_cfg`) | on – this puts an editable copy of the MQTT configuration into the app's folder |

Save and start the app.

### 2.4 Show the heating pump and status number

By default ebusd does not send the heating pump (`WP`) and the boiler's status number (`Statenumber`) to Home Assistant. One line changes that:

1. Open `mqtt-hassio.cfg` in the ebusd app's folder (under `addon_configs`, the folder ending in `_ebusd`), for example with the *File editor* or *Studio Code Server* app.
2. Find the line starting with `filter-name` and add `|^wp$|statenumber` to the end of its value (the filter ignores case). Example:

   ```
   filter-name = ...existing value...|^wp$|statenumber
   ```

3. Save and **restart the ebusd app**.

**Check:** after a few minutes *Settings → Devices & services → MQTT* shows an ebusd device for your boiler (for example *ebusd bai*) with entities such as **FlowTemp temp**, **ReturnTemp temp**, **WP** and **Statenumber**. `WP` and `Statenumber` can take a few reads to appear.

Is Thriftherm already set up? Then open *Thriftherm → Configure → Gas boiler and gas meter*: the empty fields *Heating pump running (ebusd WP, on/off)* and *Boiler status number (ebusd Statenumber, S.xx)* are filled in with the entities found. Check them and submit.

## 3. Install Thriftherm

**With HACS (recommended):**

1. Open **HACS**, click ⋮ (top right) → *Custom repositories*.
2. Repository: `https://github.com/alpha-uniform/thriftherm`, type: **Integration**. Click *Add*.
3. Search for **Thriftherm** in HACS, open it and click *Download*.
4. Restart Home Assistant (*Settings → System → ⋮ → Restart Home Assistant*).

**By hand:** copy the folder `custom_components/thriftherm` from the repository into `config/custom_components/` of your Home Assistant, then restart.

**Check:** *Settings → Devices & services → Add integration* finds **Thriftherm**.

## 4. Add the integration

Open *Settings → Devices & services → Add integration* and pick **Thriftherm**. The dialog has up to six steps. Everything can be changed later under *Thriftherm → Configure* (the options).

### 4.1 What kind of heating do you have?

| Field | What to choose |
|---|---|
| Kind of heating | **Own gas or oil boiler** for boiler control over ebusd. With *Central or district heating, or heat supplied by the landlord* or *None – heat pump and room control only* the boiler step is left out |

### 4.2 Prices

Take the values from your bills. The pre-filled numbers are only examples.

| Field | Where to find it |
|---|---|
| Electricity price | electricity bill, €/kWh |
| Gas price | gas bill, €/kWh |
| Effective boiler efficiency (Hs) | leave the default if unsure |
| Calorific value Hs | gas bill, kWh/m³ |
| State number (Z) | gas bill |

With district heating you see *Heat price from your bill*, *Share billed by consumption* and *Your ownership share of the building* instead.

### 4.3 Gas boiler (ebusd) and gas meter

The step *Gas boiler (ebusd) and gas meter* looks for your ebusd boiler and fills in what it finds: the boiler's values, its ebusd circuit, the eBUS signal and, if exactly one fits, the gas meter. The step's description says what was found, for example *Found: Vaillant (bai), 6 of 6 values — please check.* Check the fields and continue. If nothing was found, it says *No ebusd boiler found — select the entities yourself.*

| Field | What to select or enter |
|---|---|
| Flow temperature | ebusd **FlowTemp temp** |
| Return temperature | ebusd **ReturnTemp temp** |
| Boiler state from Status01 (off/on/overrun/hwc) | ebusd **Status01** |
| Heating pump running (ebusd WP, on/off) | ebusd **WP** |
| Boiler status number (ebusd Statenumber, S.xx) | ebusd **Statenumber** |
| Domestic hot water mode | ebusd **Status02 hwcmode** |
| eBUS signal (binary sensor) | the *signal* connectivity sensor of ebusd or your adapter, if you have one; otherwise leave empty |
| Nominal circulation flow of the pump | keep the default unless your boiler's manual says otherwise |
| Gas meter volume (m³, total), Gas meter instantaneous flow (m³/h) | your gas meter entities, if you have them; otherwise leave empty |
| ebusd circuit of the boiler (usually bai) | keep `bai` unless your ebusd device uses another circuit name |
| Heating curve: flow temperature at -10 °C outdoor | start with the default 55 °C |
| Heating curve: flow temperature at +15 °C outdoor | start with the default 30 °C |
| Minimum flow temperature | **about 45 °C for a non-condensing boiler, about 30 °C for a condensing boiler** |
| Maximum flow temperature | your knob setting from step 6 (for example 55 °C) or a bit lower; higher values have no effect because the knob is the upper limit |
| Release active boiler control (otherwise plan only) | **leave off for now** – you switch it on in step 7 |

**Why the minimum depends on the boiler type:** a condensing boiler gains from a low flow, but a non-condensing boiler hardly saves anything there, and with short burner runs the heat may not reach distant radiators – see the [user guide, section 5](GUIDE.md#5-boiler-control-own-boiler-on-ebusd).

The heating curve does not have to be perfect: Thriftherm learns a correction of up to ±10 K.

### 4.4 Heat pump (optional add-on)

This add-on is a preview and not yet tested with a real heat pump. **Leave every field empty and click *Submit*.** You can add a heat pump later in the options.

### 4.5 Outdoor conditions

| Field | What to select |
|---|---|
| Outdoor temperature sensors (priority order) | your outdoor sensor(s), best first; leave empty if you have none |
| Outdoor humidity sensor | optional |
| Weather entity (fallback) | your weather entity, for example the one Home Assistant created at installation (often *Forecast Home*) |

Select at least one outdoor sensor or the weather entity – the heating curve needs the outdoor temperature.

### 4.6 Rooms

You add one room per page. Tick **Add another room** to get the next page; submit the last room with the tick off to finish.

| Field | What to enter |
|---|---|
| Room name | for example `Living room` |
| Priority (1 = highest) | rooms that matter most get a lower number; 5 is fine to start |
| Room temperature sensor | the room's temperature sensor (required) |
| Room humidity sensor | optional |
| Window / door contacts | optional, all contacts of the room |
| Thermostat (TRV or Better Thermostat) | the room's **Better Thermostat** entity. No Better Thermostat yet? Leave it empty and add it later (options → *Rooms* → room → *Edit*) |
| Power sensors of internal heat sources | optional, leave empty |
| Weekly schedule helper (optional; replaces the comfort windows below) | a Home Assistant schedule helper whose blocks are the comfort times |
| Comfort temperature | temperature while the room is in use, for example 20 °C |
| Setback temperature | temperature outside the comfort times, for example 17 °C; never above comfort |
| Comfort windows Mon–Fri / Sat–Sun | times as `HH:MM-HH:MM`, several separated by commas, for example `06:00-08:00,17:00-22:00` |

The room is warm *when* a comfort time starts: Thriftherm starts heating early enough.

Only with a heat pump set up (preview) does the form also show *Heated by the heat pump* and *Bathroom drying with the heat pump (keep heating while airing after showers)*. Bathroom drying runs on the heat pump, so without one neither field appears.

**Check:** after the last room *Settings → Devices & services* lists **Thriftherm**. Its device shows *Boiler control* and *Room control* set to **Plan only (beta)**.

## 5. Better Thermostat per room (optional)

Skip this if you have no smart radiator thermostats. The boiler control works without them; open the manual valves far enough and let the flow temperature do the work.

For each room:

1. Install **Better Thermostat** from HACS, if not done yet.
2. Add one Better Thermostat for the room: select the room's radiator thermostat(s), the **same room temperature sensor** you gave Thriftherm, and the room's **window contacts**.
3. If you give it an outdoor temperature sensor, set the outdoor temperature at which it switches off **high enough, for example 25 °C** – or leave the outdoor sensor empty. Otherwise Better Thermostat blocks heating that Thriftherm asks for.
4. In Thriftherm, select this Better Thermostat as the room's thermostat (options → *Rooms* → room → *Edit*), if you left it empty in step 4.6.

A thermostat you switch **off** by hand is left alone by Thriftherm, and that room no longer calls the boiler.

## 6. At the boiler

1. **Turn the front knob to the highest flow temperature you want to allow**, for example 55 °C. The knob stays the upper limit, and it is what the boiler falls back to if Home Assistant stops. Do not turn it to the left stop: on the tested boiler that is summer mode and blocks heating whatever Thriftherm sends.
2. **Check the pump mode once.** With the factory setting the pump may stop together with the burner: the boiler cycles all day, but the radiators far down the circuit stay cold. Why this happens: [user guide, section 5](GUIDE.md#why-the-pump-mode-matters).
   - Vaillant: d.18 = 0 "post-run" is the factory setting – the pump runs only with the burner, plus a short overrun. d.18 = 1 "permanent" – the pump runs while heating is released and stops when Thriftherm blocks heating.
   - Change it in the boiler's service menu, or once via ebusd (message `HcPumpMode`, values `post_run`, `permanent`, `winter`).
   - Check your boiler's manual first; other makes use other codes.

   Thriftherm never writes installer parameters: changing the pump mode is your decision.

## 7. Plan only first, then Active

### 7.1 A few days in "Plan only"

Leave everything in *Plan only* and look at these entities once or twice a day:

| Entity | What to look for |
|---|---|
| *Boiler command* | does *Heat* / *Heating blocked* match what the rooms need? Is the flow temperature plausible? |
| *Thermostat plan* (per room) | the targets it would give each thermostat |
| *Decision reason* | in plain words why Thriftherm does what it does |

If something looks wrong, adjust it in the options before going active.

### 7.2 Going active

1. Switch the Better Thermostat entities to **heat**.
2. Thriftherm → *Configure* → **Gas boiler and gas meter**: switch on *Release active boiler control (otherwise plan only)*. Submit.
3. Thriftherm → *Configure* → **Control parameters**: switch on *Release active room control (write setpoints to the thermostats)*. Submit.
4. On the Thriftherm device, set **Boiler control** and **Room control** to **Active**.

**Check after a few minutes:**

| Entity | Expected |
|---|---|
| *Boiler command* | attribute *Commands sent* is on |
| Better Thermostat entities | show the new targets from *Thermostat plan* |
| *Safety state* | OK |

Everything about daily use – modes, schedules, away mode, learning – is in the [user guide](GUIDE.md).

## 8. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| No ebusd entities at all | ebusd not connected to the adapter or to MQTT, or the name filter lets nothing through | check the adapter setting and the ebusd app's log; check that Mosquitto runs and the MQTT integration is set up; check the `filter-name` line in `mqtt-hassio.cfg` |
| *WP* and *Statenumber* missing | the ebusd name filter does not let them through | extend `filter-name` in `mqtt-hassio.cfg` by `\|^wp$\|statenumber` and restart ebusd ([step 2.4](#24-show-the-heating-pump-and-status-number)) |
| Boiler ignores the setpoint | ebusd is not allowed to write | add `--accesslevel=*` to the ebusd command-line options and restart the app |
| Boiler ignores the setpoint | knob set lower than the setpoint | the knob is the upper limit – turn it up to your maximum (for example 55 °C) |
| Boiler does not heat at all | knob at the left stop = summer mode | turn the knob up |
| Boiler cycles, but the farthest radiator stays cold | pump stops with the burner; minimum flow too low | check the pump mode ([step 6](#6-at-the-boiler)); on a non-condensing boiler set the minimum flow to about 45 °C ([step 4.3](#43-gas-boiler-ebusd-and-gas-meter)) |
| A thermostat does not accept setpoints | the thermostat hangs | take the battery out and put it back; update its firmware |
| Right after a restart *Boiler command* shows *Waiting for room data* | Thriftherm waits for the room sensors | normal for up to five minutes |
| A room is cold, but the boiler is blocked | window open, or the room's thermostat switched off | close the window; switch the thermostat back to heat |
