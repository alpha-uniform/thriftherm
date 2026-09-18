# Changelog

## 0.4.0 — 2026-09-18

First release that controls a real boiler day and night. Everything below was found or confirmed on a Vaillant atmoTEC plus VCW 194/4-5.

### Boiler
- **ebusd auto-detection:** the boiler step finds the ebusd entities of a known boiler from its profile (first profile: Vaillant `bai`), plus the eBUS signal and the gas meter, and says what it found, e.g. "Found: Vaillant (bai), 6 of 6 values — please check."
- **Heat reaches the last radiator:** the setup guide shows how to check the pump mode, the user guide explains why. With the factory "post-run" mode (Vaillant d.18 = 0) the pump stops with the burner and distant radiators stay cold; "permanent" (d.18 = 1) keeps it running while heating is released and stops it when heating is blocked. Thriftherm never writes installer parameters.
- **Heating pump and status code:** ebusd `WP` (the pump itself) and `Statenumber` (S.xx) are polled with priority and can be selected in the options. `Status01` is kept for hot water detection; its "pump state" follows the heating demand, not the pump.
- **Hot water is never touched:** its setpoint is always sent as "no value". Hot water is recognised within a minute and kept out of the learning.
- **Short cycling is a learning signal:** three short burner runs within an hour lower the heating curve by one step, never below the minimum flow.
- **No burner run is missed:** the gas meter and the pump wake the controller instead of waiting for the next minute.
- **Fresh readings:** flow, return and status keep a high ebusd poll priority, renewed every 10 minutes.
- **One-second eBUS dropouts are ignored;** only a signal gone for two minutes hands the boiler back to its knob.
- **Timers survive a restart,** and after a restart the controller waits up to five minutes for the room sensors instead of blocking on missing data.

### Rooms
- A thermostat switched off by hand no longer calls the boiler; an unavailable one still does.
- Cool-down coefficient and heating power are learned per room, and every heat-up rate keeps its temperature gradient.
- Away mode keeps a planned return, which is now an entity you can set; setting a return switches to away.
- Schedules and room temperatures are configured in the UI; schedule helpers refresh immediately.
- Bathroom drying runs on the heat pump, so the room form offers it only once a heat pump is set up.

### General
- Learned values can be forgotten per area, by hand or automatically after a relevant setting changes.
- German for a German Home Assistant, English for every other language; entity ids are always English.
- Step-by-step setup guide and user guide in English and German; the German README is the default, the English one is README.en.md.
- Diagnostics, repairs for silent degradation, logo and brand images.
- Installations without an own boiler are supported: district heating, or a heat pump alone (preview).
- The heat pump add-on ships as a preview: not yet tested with a real heat pump, so do not rely on it.
- Removed five settings that no code used: the boiler flow setpoint and storage temperature entities, the heat pump outlet humidity and energy entities, and the fallback room temperature. Stored values are ignored.
- Many fixes from a code review of the control logic, the COP arithmetic and the Home Assistant integration.

## 0.3.0

Renamed to Thriftherm (domain `thriftherm`).
