<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alpha-uniform/thriftherm/main/custom_components/thriftherm/brand/dark_logo@2x.png">
    <img alt="Thriftherm" width="450" src="https://raw.githubusercontent.com/alpha-uniform/thriftherm/main/custom_components/thriftherm/brand/logo@2x.png">
  </picture>
</p>

# Thriftherm for Home Assistant

**English** | [Deutsch](README.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz/docs/faq/custom_repositories)
![Status: beta](https://img.shields.io/badge/status-beta-orange)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/alphauniform)

**Demand-based heating for Home Assistant.** Thriftherm heats every room to its schedule and gives your gas boiler the controller it previously lacked. It also works with district heating, and a heat pump add-on is in preview.

## What works with your equipment

The first setup step asks which heating you have and leaves out the steps that do not apply.

| Feature | Own boiler on ebusd (core) | + Better Thermostat | + heat pump (preview) | Heat pump only, no boiler (preview) | Central or district heating¹ |
|---|:-:|:-:|:-:|:-:|:-:|
| **Rooms** | | | | | |
| Room targets from schedules (comfort, setback, schedule helper) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pre-heating, so the room is warm when the schedule starts | ✓ | ✓ | ✓ | ✓ | ✓ |
| Away mode with pre-heating for your return | ✓ | ✓ | ✓ | ✓ | ✓ |
| Temporary override of a room's target | ✓ | ✓ | ✓ | ✓ | ✓ |
| Open window: the room stops calling for heat | ✓ | ✓ | ✓ | ✓ | ✓ |
| Learned heat-up and cool-down rate per room | ✓ | ✓ | ✓ | ✓ | ✓ |
| Targets written to the thermostats; turning one by hand becomes a temporary override | – | ✓ | ✓ | – | ✓ |
| Quick heat-up button per room | – | ✓ | ✓ | ✓ | ✓ |
| Frost protection, also in summer mode | ✓ | ✓ | ✓ | – | ✓ |
| **Boiler** | | | | | |
| Flow temperature from heating curve, room demand and flow/return spread | ✓ | ✓ | ✓ | – | – |
| Heating blocked when no room needs heat; hot water untouched | ✓ | ✓ | ✓ | – | – |
| Learned heating curve correction | ✓ | ✓ | ✓ | – | – |
| Hot water recognition (learning pauses after a shower) | ✓ | ✓ | ✓ | – | – |
| Summer mode over eBUS: heating off, hot water keeps working | ✓ | ✓ | ✓ | – | – |
| Fallback to the boiler's knob if Home Assistant stops | ✓ | ✓ | ✓ | – | – |
| **Heat pump** | | | | | |
| Heat pump control: slow at a high COP, full power for quick heat-up | – | – | ✓ | ✓ | ✓² |
| COP measured from airflow and air enthalpy; defrost and icing detection | – | – | ✓ | ✓ | ✓² |
| Cost comparison with the central heat source | – | – | ✓ | – | ✓² |
| Bathroom drying | – | – | ✓ | ✓ | ✓² |
| **Always** | | | | | |
| *Plan only* first, *Active* only after you release it; a reason for every decision; repair notices | ✓ | ✓ | ✓ | ✓ | ✓ |

✓ works · – not available · preview = not yet tested with a real heat pump. Each “+” column includes the columns to its left. In the heat-pump-only column the room rows apply to the rooms the heat pump reaches.

¹ Central or district heating, or heat supplied by the landlord: no own boiler. Rooms are controlled through Better Thermostat, and nothing is ever written to the heat supply.

² With the heat pump add-on (preview). The cost comparison then uses the marginal price of district heat (see [below](#add-on-heat-pump-preview)).

- **A heat pump without a boiler works**: choose *None – heat pump and room control only* in the first setup step. Like the whole add-on, this is a preview.
- **Bathroom drying needs the heat pump.** It dries with the heat pump's warm air, only in a room the heat pump heats – details in the [user guide, section 7](GUIDE.md#7-heat-pump-optional).
- **Everything in the boiler rows needs an own boiler on ebusd** – flow temperature, heating block, learned heating curve, hot water recognition, summer mode over eBUS and the knob fallback. With district heating or without a boiler, nothing is ever sent over eBUS.
- **With district heating, Thriftherm pays off mainly with a heat pump.** It then keeps deciding whether heat from electricity or from the district heating is cheaper right now, and lets the heat pump heat only when it pays – except for quick heat-up, bathroom drying and learning runs. Without a heat pump, only room control is left. Better Thermostat already regulates the valves, and Thriftherm adds schedules with pre-heating and away mode – not much beyond what Better Thermostat with a schedule can already do.

## Quick start

Nothing is written to the boiler or the thermostats until you switch to *Active* yourself.

1. **ebusd:** connect the eBUS adapter to the boiler's eBUS terminal (a room controller already there is disconnected), then set up the ebusd app with MQTT, with `--accesslevel=*` and the `filter-name` addition ([step 2](SETUP.md#2-set-up-ebusd)); skip this without an own boiler.
2. **Install:** in HACS add the custom repository `https://github.com/alpha-uniform/thriftherm` (category *Integration*), download **Thriftherm** and restart Home Assistant.
3. **Add the integration:** *Settings → Devices & services → Add integration → Thriftherm*. When ebusd entities are found, the boiler step is filled in for you – check it and continue.
4. **Plan only for a few days:** compare *Boiler command*, *Thermostat plan* and *Decision reason* with what you would have done.
5. **Switch to *Active*:** release active control in the options, then set *Boiler control* and *Room control* to *Active*.

**Step by step:** [setup guide](SETUP.md)

**Day to day** – modes, temperatures, services, learning, repairs: [user guide](GUIDE.md)

**Languages:** German or English, following Home Assistant's language – more in the [user guide](GUIDE.md).

## What it does

Thriftherm is a Home Assistant custom integration that heats rooms to a schedule instead of a thermostat's guess. With an own gas boiler it becomes the controller the boiler previously lacked, talking to it through [ebusd](https://ebusd.eu). With district heating it leaves the heat supply alone and works through the room thermostats. Where radiators have smart thermostats, [Better Thermostat](https://github.com/KartoffelToby/better_thermostat) does the local valve work.

### Core: smart boiler control (eBUS)

At the tested installation the boiler was run by a central room controller on its eBUS terminal. It measured a single room and decided for the whole flat from that one sensor, and it was not connected to the internet: no remote control, no data, nothing to learn from.

The other common case is a boiler without any controller. It keeps its heating circuit at the knob temperature all winter – even when every thermostatic valve is closed – then fires for seconds at minimum load, overshoots and cycles. This integration acts as the boiler's controller instead:

- **Flow temperature as low as possible**, from three signals:
  - a two-point heating curve over the outdoor temperature,
  - the heat demand of the rooms,
  - the **flow/return spread** while the boiler heats: a small spread means the radiators take little heat, so the flow is lowered; a large spread with rooms still in demand raises it.
  A learned offset keeps adapting; ramps are limited.
- **Heating blocked when no room needs heat** (`disablehc` via the ebusd `SetMode` message). Hot water is never touched: its setpoint is sent as “no value”, so the boiler keeps following its knob.
- **Hot water recognised**: ebusd does not report hot water reliably on every boiler. Thriftherm recognises it from gas use and temperatures and pauses learning for an hour, so shower water does not teach the heating curve.
- **Summer mode by eBUS**: the operating mode “Summer” keeps space heating blocked while hot water keeps working – the knob can stay open.
- **Safe fallback**: the setpoint is re-sent every 2 minutes. If Home Assistant stops, the boiler returns to its own front knob after 9–16 minutes (measured), and the knob always remains the upper limit.

### Works without smart thermostats

One temperature sensor per room is enough. The boiler control – heating curve, demand, blocking, learning, frost protection – needs nothing else, and the room control simply reports *No thermostat* and writes nowhere. Smart thermostats can be added later, one room at a time; a mixed setup is fine.

### Rooms: Better Thermostat

- Per-room targets from schedules, **pre-heating so the comfort temperature is reached when the schedule starts**, overrides, “quick heat-up”, away mode with return pre-heat, frost and humidity protection.
- Targets are handed to Better Thermostat, which uses the room sensor and window contacts. A manual change on a thermostat becomes a temporary override instead of being fought. A thermostat that is switched off is left alone.

### Add-on: heat pump (preview)

> **Preview – coming soon.** Not yet tested with a real heat pump – do not rely on it. It was developed with a Midea PortaSplit in mind but has only ever run in *Plan only* mode and has never controlled a heat pump. What follows describes what it is built to do.

If a heat pump is configured, the integration measures its COP from airflow and air enthalpy, compares heat cost against the central source using real prices and a learned COP map, detects defrost/icing, and runs the heat pump slowly at a high COP. It can also dry a bathroom it heats after a shower. Without a heat pump, all of this simply disappears; without a boiler, the heat pump is the only heat source and there is nothing to compare.

For district heating the comparison uses the **marginal** price of heat, not the headline one: under the German Heizkostenverordnung only the consumption share of the building's bill follows your own meter, and of the floor-area share your flat carries its ownership fraction. Saving a kWh therefore saves less than the price per kWh suggests, which *raises* the COP the heat pump has to reach. Both shares are configurable; ignoring them would flatter the heat pump.

### Explainable and safe

Every decision comes with a reason attribute. Sensor failures degrade gracefully; missing boiler data or a safety fallback means *nothing is sent* and the boiler runs on its knob.

## Verified on real hardware

Tested on a Vaillant atmoTEC plus VCW 194/4-5 (non-condensing combi boiler, ebusd circuit `bai`, definition `bai.308523`) with an eBUS adapter C6:

| Finding | Result |
|---------|--------|
| `SetMode` from ebusd (address 31) accepted | yes, external setpoint d.09 follows the value |
| Effective flow setpoint | `min(front knob, eBUS setpoint)` – the knob is the upper limit |
| Heating block `disablehc=1` | space heating blocked, **hot water unaffected** |
| Hot water setpoint sent as `-` (no value) | accepted; the heating setpoint still applies and hot water keeps following the knob |
| Fallback without new `SetMode` | back to the knob after 9–16 min |
| Knob at the left stop | summer mode (d.23 off), blocks heating regardless of eBUS |
| Without a controller, valves closed | 30 s burner runs, 14 K overshoot, then anti-cycling – the waste this integration removes |
| Pump mode "post-run" (d.18 = 0, factory) | pump runs only with the burner; the water circles through the bypass and **the last radiator stays cold** |
| Pump mode "permanent" (d.18 = 1) | pump runs while heating is released and **stops when `disablehc=1`**; ~33 W; the cold radiator warmed within an hour |
| Minimum flow on this non-condensing boiler | 30 °C left the last radiator cold, 45 °C works |
| ebusd `Status01` "pump state" | follows the heating demand, not the pump; the pump itself is `WP`, the display code `Statenumber` |

## Hardware used

This is what the tested installation runs on. Other devices work the same way as long as they show up in Home Assistant.

| Job | Device |
|---|---|
| Boiler | Vaillant atmoTEC plus VCW 194/4-5 (non-condensing) |
| eBUS adapter | [eBUS Adapter Shield C6](https://adapter.ebusd.eu/v5-c6/index.en.html), stick version, by ebusd.eu – sold in the [Elecrow shop](https://www.elecrow.com/store/ebusd) |
| Gas meter reading | [WiFi ACM-ESP](https://www.seegel-systeme.de/produkt/wlan-acm-esp-kommunikationsmodul-fuer-elster-gaszaehler/) by Seegel Systeme, for Elster/Honeywell BK-G4A(T) |
| Radiator thermostats | [SONOFF TRV Gen2 (TRV-ZBT)](https://sonoff.tech/en-us/products/sonoff-trv-gen2-zigbee-thermostatic-radiator-valve-trv-zbt), Zigbee, with Better Thermostat |

## Supported boilers

| Boiler | ebusd circuit / definition | Status | Data from |
|--------|----------------------------|--------|-----------|
| Vaillant atmoTEC plus VCW 194/4-5 | `bai` / `bai.308523` | verified, write test passed | maintainer |

Each boiler is added as a profile: which ebusd messages carry its values and which message takes the setpoint. I can only test the boiler in my own home. Every other boiler is added from data its owners share, so **your boiler can be next**: open a [boiler data issue](https://github.com/alpha-uniform/thriftherm/issues/new?template=boiler_data.yml) with the model, the ebusd definition and what you observed. Everyone who contributes data is credited in the table above.

## Requirements

- Home Assistant 2026.9 or newer
- With an own boiler: an eBUS adapter and the ebusd app with MQTT
- One temperature sensor per room; Better Thermostat if Thriftherm should set the radiator thermostats
- Optional, preview only: a heat pump with a few sensors

The full list is in the [setup guide, step 1](SETUP.md#1-what-you-need).

## Status and responsibility

**Writing to a gas boiler is your responsibility.** Only the `SetMode` message is used; no installer parameters are written. Changing the pump mode is a one-time decision you make yourself.

**Status: beta.** All controllers start in *Plan only*: they compute and publish what they would do and write nothing until you release active control. Blocking, flow control and the safety fallbacks are verified on the maintainer's boiler. The learned corrections have so far only seen mild weather. The heat pump add-on is a preview – coming soon, not yet tested with a real heat pump; do not rely on it.

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install pytest pytest-asyncio pytest-homeassistant-custom-component
./.venv/bin/pytest -q
```

The engines under `custom_components/thriftherm/engines/` are pure Python without Home Assistant imports and unit-tested (about 90 % line and branch coverage).

## Sponsors

Thriftherm is built in my spare time. If it saves you energy or money, you can [buy me a coffee](https://buymeacoffee.com/alphauniform) – it gives me back a bit of the time that goes into new features and new boilers.

**Heating Heroes** are listed here:

*Be the first.*

## License

MIT
