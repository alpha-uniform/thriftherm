# Troubleshooting

**English** | [Deutsch](FEHLERSUCHE.md)

When something does not work or looks different from what you expected: look here for what you see. Setting up is in the [setup guide](SETUP.md), daily use in the [user guide](GUIDE.md).

**Sections:** [ebusd and connection](#ebusd-and-connection) · [Boiler](#boiler) · [Rooms and thermostats](#rooms-and-thermostats) · [Sensors](#sensors) · [None of this fits](#none-of-this-fits)

## ebusd and connection

### No ebusd entities

ebusd is not connected to the adapter or to MQTT, or the name filter lets nothing through.

- Check the adapter setting and the ebusd app's log ([setup, step 2.3](SETUP.md#23-configure-the-ebusd-app)).
- Check that Mosquitto runs and the MQTT integration is set up ([step 2.1](SETUP.md#21-install-the-mqtt-broker)).
- Check the `filter-name` line in `mqtt-hassio.cfg` ([step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number)).

### WP and Statenumber missing

The ebusd name filter does not let them through. Extend the `filter-name` line in `mqtt-hassio.cfg` by `|^wp$|statenumber` and restart ebusd ([step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number)). Both can take a few reads to appear.

### Repair notice “No data from the boiler”

ebusd does not reach the boiler. The most common cause: the eBUS adapter got a new IP address from the router. You often notice it as well because the boiler heats although Thriftherm blocks it.

How an outage goes:

- After two minutes without *ebusd signal* Thriftherm stops sending. *Boiler command* shows *Knob in charge*, and the boiler heats by its knob.
- After ten minutes *No data from the boiler* appears under Settings → Repairs.
- During the outage the eBUS values in Home Assistant stay at their last reading. Only *ebusd signal* tells whether ebusd reaches the boiler.

Fix:

1. Look for "unable to open …" in the ebusd app's log.
2. Correct the adapter's address in the ebusd app.
3. Give the adapter a fixed address in the router, a DHCP reservation ([step 2.3](SETUP.md#23-configure-the-ebusd-app)).

For a push notification, add an automation on *ebusd signal*, for example "off for 5 minutes".

### A Wi-Fi device keeps dropping its connection

The gas meter, for example. It got the eBUS adapter's old address, and ebusd keeps knocking there. Fix as above: correct the address in the ebusd app, give fixed addresses and restart the device.

## Boiler

### The boiler ignores the setpoint

There are two causes:

- **ebusd is not allowed to write.** Add `--accesslevel=*` to the command-line options of the ebusd app and restart the app ([step 2.3](SETUP.md#23-configure-the-ebusd-app)).
- **The knob is set lower than the setpoint.** The knob is the upper limit. Turn it up to your maximum, for example 55 °C ([step 6](SETUP.md#6-at-the-boiler)).

### The boiler does not heat at all

The knob is at the left stop. That is summer mode and blocks heating whatever Thriftherm sends. Turn the knob up.

### The boiler heats although Thriftherm blocks it

Mostly ebusd no longer reaches the boiler, and it heats by its knob. See [No data from the boiler](#repair-notice-no-data-from-the-boiler).

### The boiler cycles, but the farthest radiator stays cold

There are two causes, often together:

- **The pump stops with the burner.** Check the pump mode ([step 6](SETUP.md#6-at-the-boiler)). Why it matters is in the [user guide](GUIDE.md#why-the-pump-mode-matters).
- **The minimum flow is too low.** On a non-condensing boiler set the minimum flow temperature to about 45 °C ([step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)).

### The boiler fires briefly about once an hour

At night too, and with no heating demand. That is the warm start function of the combi boiler: it keeps the hot water exchanger warm, and the display shows a **C**. A room controller on the bus used to suppress it.

How to switch it off:

- **At the boiler:** turn the hot water knob fully to the left, then back to your usual value. The C disappears (confirmed by a user on a Vaillant).
- **In Thriftherm:** switch off *Keep hot water ready* in the options under *Gas boiler (ebusd) and gas meter* ([step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)). This suits only a combi boiler without a cylinder, and it only acts with active boiler control.

### Does Thriftherm change my hot water temperature?

No. Thriftherm sends "no value" for the hot water setpoint, and the boiler's knob decides. The only exception is your own choice: with *Keep hot water ready* off, the boiler no longer heats for hot water while no tap is open.

### Home Assistant crashed or is off

The boiler falls back to its knob within 9 to 16 minutes. The thermostats keep their last target.

A boiler through entities (preview) is different: if Home Assistant crashes or loses power, the last state stays, a blocked heating included ([step 4.3a](SETUP.md#43a-a-boiler-without-ebusd-through-entities-preview)).

## Rooms and thermostats

### A room is cold, but the boiler is blocked

A window is open, or the room's thermostat is switched off. Close the window or switch the thermostat back to *heat*.

If a radiator thermostat has not reported for two and a half hours, the room does not call for heat either. A repair notice appears for it.

### A room is at 15 °C

It is in away mode, and 15 °C is the away temperature. Frost protection (7 °C) is a separate, lower limit.

### I turned the thermostat, and it does not change back

A change by hand becomes a temporary override, for 120 minutes by default. End it with the button *Back to automatic* or the action `thriftherm.clear_override`. More in the [user guide, changes by hand](GUIDE.md#changes-by-hand).

### Overrides although nobody touched the thermostat

Better Thermostat rewrites the valve setpoint every few seconds. When a Zigbee reply arrives late, Better Thermostat takes the old value for a turn of the knob.

The **echo filter** recognises this (Options → *Control parameters*, section *Releases*, on by default). When Better Thermostat jumps to a value the valve already showed within the last minute and left again (for example 16 → 16.5 → 16), the jump is ignored and the planned setpoint is sent again. A real turn of the knob brings a new value and is taken over.

The room's *Thermostat plan* sensor shows when an echo was last ignored.

### A room takes very long after you turn it up

The heating curve is made to hold temperatures, not to heat up fast. How fast it is to go is set with the *heat-up speed* under *Gas boiler (ebusd) and gas meter* ([user guide, heat-up speed](GUIDE.md#heat-up-speed)). More speed burns more gas.

### A thermostat does not accept setpoints

The thermostat hangs. Take the battery out and put it back, and update its firmware.

## Sensors

### Waiting for room data after a restart

Right after a restart *Boiler command* shows *Waiting for room data*. Thriftherm waits for the room sensors, and up to five minutes is normal.

### Temperature trend is empty after a restart

The trend needs about 30 minutes of readings. On a new installation the sensor is disabled until you enable it ([user guide, dashboard sensors](GUIDE.md#8-dashboard-sensors-worth-knowing)).

### Flow and return change only every few minutes

That is normal: ebusd polls the boiler's values in turn. For the control Thriftherm fetches its values itself once a minute, with a gas meter while gas burns, without one all the time. If you want fresher values in a dashboard, trim ebusd's poll list: [Poll fewer ebusd values](EBUSD.en.md).

## None of this fits

- What a repair notice means and what to do is in the [user guide, repairs](GUIDE.md#9-repairs).
- The *Decision reason* sensor says in plain words why Thriftherm does what it does.
- For a bug report: Settings → Devices & services → Thriftherm → ⋮ → *Download diagnostics*. The file holds configuration, current state and learned values, and nothing is removed from it. Look through it before posting it and attach it to an [issue](https://github.com/alpha-uniform/thriftherm/issues).
