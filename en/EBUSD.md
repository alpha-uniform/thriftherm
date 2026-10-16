# Poll fewer ebusd values

**English** | [Deutsch](../de/EBUSD.md)

Optional, for advanced users: this page shows how to trim the list ebusd polls in turn, so that flow and return are fresh more often. It belongs to the [setup guide, step 2.5](SETUP.md#25-poll-fewer-values-so-that-flow-and-return-are-fresher).

## Why the length of the list matters

ebusd reads the boiler's values one after another, one message every few seconds. The more messages are on the list, the more rarely each one gets its turn. For a Vaillant boiler some 75 to 80 are on it by default, and nobody needs most of them.

Thriftherm makes sure by itself that its five important messages (FlowTemp, ReturnTemp, Status01, WP, Statenumber) are polled with priority; there is nothing to configure for that. But between the priority rounds ebusd works through the whole rest of the list each time. Measured on the test installation (2026-10-12, one poll every 6 seconds): a priority value comes five times at 30-second spacing and then pauses for as long as the rest of the list takes.

| Other messages on the list | Pause for flow and return |
|---|---|
| about 75 (as delivered) | about 8 minutes |
| 35 (test installation until 2026-10-12) | 4 minutes |
| 13 (test installation after trimming by the list below) | 1 minute 48 seconds |

The first row is extrapolated from the measured pattern, the other two are measured. Every message less saves 6 seconds.

**For the control the pause does not matter.** Thriftherm reads its five values itself once a minute, however long the list is: with a gas meter as soon as it shows consumption, without one all the time (for then only these values tell that the burner has started). What remains is the pause with a gas meter while the boiler rests. So trimming pays off when you want to see the values promptly then too, in a dashboard for example, and it takes load off the bus.

## What Thriftherm needs

Do not exclude these messages:

| ebusd message | For |
|---|---|
| FlowTemp, ReturnTemp | flow and return: hot water detection, learning the heating curve |
| Status01 | field *pumpstate*: heating, overrun, hot water |
| WP | heating pump running |
| Statenumber | status number S.xx: heating, hot water or overrun |
| Status02 | field *hwcmode*: hot water active |
| SetMode | the command to the boiler – never exclude |
| PrEnergySumHc1 to 3, PrEnergySumHwc1 to 3 | only without a gas meter: Thriftherm estimates the gas for heating and for hot water from them (see [below](#what-nobody-needs-on-the-test-installation)) |

Plus *ebusd Signal* from the ebusd device itself; that is no read at the boiler.

## Worth keeping

Thriftherm does not read these messages, but they are useful all the same:

| ebusd message | For |
|---|---|
| HcHours, HcStarts, HwcHours, HwcStarts, PumpHours, HcPumpStarts | operating hours and starts of burner and pump, for a dashboard for example |
| ExtFlowTempDesiredMin (d.09) | shows which setpoint the boiler has accepted; 90 means: it is running by its own knob |
| FlowTempDesired | the setpoint in force |
| HcPumpMode (d.18) | proves the pump setting |
| StorageTemp | temperature on the hot water side |
| HoursTillService | hours until service |

## What nobody needs on the test installation

23 messages, read by nothing and shown nowhere:

- PrEnergyCountHc1 to 3, PrEnergyCountHwc1 to 3, PrEnergySumHc1 to 3, PrEnergySumHwc1 to 3 – twelve counters without a unit. **Without a gas meter, keep the six PrEnergySum messages:** Thriftherm estimates the gas for heating and for hot water from them ([setup, step 4.3](SETUP.md#43-gas-boiler-ebusd-and-gas-meter)). They add up the burner output in percent once a second; the six PrEnergyCount messages only count those seconds and can go. For that case the entry in the example below would be `^PrEnergyCount` instead of `^PrEnergy`
- CounterStartattempts1 and 2, DeactivationsTemplimiter, WaterpressureMeasureCounter, ValveStarts, StorageLoadPumpHours, StorageloadPumpStarts, HcUnderHundredStarts, HwcUnderHundredStarts – nine more counters
- PumpPower (always 0) and Templimiter

One exception in case you want to count burner starts exactly: HcStarts and HwcStarts count in steps of a hundred, HcUnderHundredStarts and HwcUnderHundredStarts deliver the last two digits. Thriftherm does not need that, it counts the burner runs itself.

## Already excluded on the test installation

Since 2026-09-13; the round became about half as long:

- HwcTemp, OutdoorstempSensor, Expertlevel_ReturnTemp – the boiler has no such sensors, the values were empty
- VortexFlowSensor, Status, Status16 – ebusd could not decode them for this unit
- PowerValue and the fixed settings (among them FlowTempMax, HwcTempDesired, HwcTempMax, PumpPowerDesired, StorageTempDesired, ExternalFlowTempDesired, TempDiffFailure, TempGradientFailure, TimerInputHc)

Which messages your boiler delivers depends on the unit. Look at the ebusd device in Home Assistant: what is always empty, 0 or unchanged and does not interest you can go. The test installation (atmoTEC) delivers no usable fault code or water pressure over ebusd; other boilers may, and then those values are worth keeping.

## How to exclude messages

1. Open `mqtt-hassio.cfg` as in [setup, step 2.4](SETUP.md#24-show-the-heating-pump-and-status-number).
2. Find or add the line `filter-non-name`. Its value is a list of names, each between `^` and `$`, separated by `|`. Case does not matter. Without the `$` at the end an entry matches everything that starts like it: `^PrEnergy` excludes all twelve PrEnergy counters. The filter can do no more than that – it does not understand brackets or `[12]` as other search patterns do, such entries simply match nothing. Example for the 23 messages above:

   ```
   filter-non-name = ^PrEnergy|^CounterStartAttempts1$|^CounterStartAttempts2$|^DeactivationsTemplimiter$|^WaterpressureMeasureCounter$|^ValveStarts$|^StorageLoadPumpHours$|^StorageLoadPumpStarts$|^HcUnderHundredStarts$|^HwcUnderHundredStarts$|^PumpPower$|^Templimiter$
   ```

   If the line already has a value, append with `|`.
3. Save and **restart the ebusd app**. It takes some minutes until the first fresh values are in Home Assistant; until then Home Assistant shows the old ones.

## Three common misunderstandings

- **Disabling an entity in Home Assistant saves nothing.** ebusd goes on polling the message. Only the filter takes it off the list.
- **What counts is the message, not the entity.** Status01 and Status02 each deliver several entities (temperatures, pump state). You can disable the surplus ones in Home Assistant; nothing gets faster by that.
- **Excluded entities stay in Home Assistant** until you delete them. If one cannot be deleted, its announcement is still in the MQTT broker: then publish an empty message with *Retain* to its topic `homeassistant/sensor/ebusd_bai_<name>_<field>/config` (Settings → Devices & services → MQTT → Configure → Publish a packet).

## Polling faster

`--pollinterval=2` in the command-line options of the ebusd app cuts all times to a half or a third. This has not been tried on the test installation. There about 9 % of the polls fail at the normal pace already and are repeated; whether a tighter pace makes that worse is open. If you try it, check the log of the ebusd app for lines with `ERR:` afterwards.
