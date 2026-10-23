"""Room engine: target temperature, deviation, demand, trend, window logic,
overrides, away mode with return preheat, bathroom drying mode.

Target precedence (highest first):
    manual override → mode off (frost) → mode away (with preheat / humidity
    guard) → schedule (with preheat so the comfort temperature is reached when
    the comfort window starts). "Quick heat-up" raises the result to at least
    the comfort temperature. The bathroom drying mode raises the target and lets
    the Midea keep heating while the window is open; it only runs when the
    Midea can heat, otherwise the radiator follows the normal window logic.
    While the Midea cools, the rooms it serves stay at setback at most and take
    no heat at all.
"""

from __future__ import annotations

from dataclasses import replace
from statistics import median

from datetime import date, datetime, time, timedelta

from ..const import MODE_AWAY, MODE_OFF
from ..models import DryingState, Parameters, RoomConfig, RoomResult, RoomState, ScheduleState, TimeWindow
from . import psychrometrics as psy

_LAST_MINUTE = time(23, 59)  # how the form says "to the end of the day" (models.TimeWindow)
WINDOW_OPEN_STATES = ("on", "open", "true")
WINDOW_CLOSED_STATES = ("off", "closed", "false")

DRYING_BASELINE_WINDOW_S = 3 * 3600.0
DRYING_RH_TRIGGER_PCT = 70.0
DRYING_RH_RISE_WINDOW_S = 20 * 60.0
DRYING_END_DEWPOINT_MARGIN_K = 4.0
DRYING_END_ABS_MARGIN_G_M3 = 1.0
DRYING_WINDOW_NO_EFFECT_S = 20 * 60.0
# The humidity climbs for as long as the shower runs. Drying means the strongest fan and more
# heat: after the shower, not on wet skin. So the load only gives notice, and the drying starts
# once the climb is over: no new highest value for three minutes, or after twenty at the latest.
DRYING_AFTER_PEAK_S = 180.0
DRYING_PEAK_STEP_G_M3 = 0.2  # less than this above the highest value is no climb, the sensors flicker
DRYING_WAIT_MAX_S = 20 * 60.0
SCHEDULE_PREHEAT_HORIZON_S = 12 * 3600.0
# A valve that has not reported for this long takes no commands either: its room must
# not call the boiler. Valves here report at least every 15 minutes, but a Zigbee valve left
# at its default reports about hourly, and an unchanged value leaves no trace in Home Assistant:
# one hour took healthy valves for lost (reported 2026-10-10). One missed report must fit in.
THERMOSTAT_SILENT_S = 9000.0


def parse_schedule(text: str | None) -> tuple[TimeWindow, ...]:
    """Parse '05:15-07:30,17:30-22:00' into time windows. Empty -> no windows."""
    if not text or not text.strip():
        return ()
    windows: list[TimeWindow] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            start_s, end_s = part.split("-")
            sh, sm = (int(v) for v in start_s.strip().split(":"))
            eh, em = (int(v) for v in end_s.strip().split(":"))
            windows.append(TimeWindow(time(sh, sm), time(eh, em)))
        except (ValueError, TypeError) as err:
            raise ValueError(f"invalid schedule segment '{part}'") from err
    return tuple(windows)


def scheduled_target(cfg: RoomConfig, at: datetime, schedule: ScheduleState | None = None) -> tuple[float, str]:
    """Target from the schedule helper if one is assigned, else from the comfort windows."""
    if schedule is not None:
        if schedule.active:
            return (schedule.temperature if schedule.temperature is not None else cfg.comfort_temp), "schedule_comfort"
        return cfg.setback_temp, "schedule_setback"
    if _window_at(cfg, at) is not None:
        return cfg.comfort_temp, "schedule_comfort"
    return cfg.setback_temp, "schedule_setback"


def _windows_on(cfg: RoomConfig, day: date) -> tuple[TimeWindow, ...]:
    return cfg.schedule_weekend if day.weekday() >= 5 else cfg.schedule_weekday


def _window_at(cfg: RoomConfig, at: datetime) -> tuple[TimeWindow, date] | None:
    """The comfort window running at `at` and the day it began on, None outside of one.

    The hours after midnight of a window across midnight belong to the day it began on:
    Friday's "22:00-02:00" still runs on Saturday at 00:30, whatever the weekend's windows say,
    and Sunday's windows decide over Monday at 00:30.
    """
    clock = at.time()
    for w in _windows_on(cfg, at.date()):
        if (clock >= w.start) if w.across_midnight else w.contains(clock):
            return w, at.date()
    yesterday = at.date() - timedelta(days=1)
    for w in _windows_on(cfg, yesterday):
        if w.across_midnight and clock < w.end:
            return w, yesterday
    return None


def next_comfort_start(cfg: RoomConfig, now: datetime, schedule: ScheduleState | None = None) -> datetime | None:
    """Start of the next comfort block (schedule helper) or comfort window.

    A window begins on its own day only: the hours after midnight of a window across midnight
    are no start, they continue the evening before (see `_window_at`).
    """
    if schedule is not None:
        return None if schedule.active else schedule.next_start
    for ahead in range(3):
        day = (now + timedelta(days=ahead)).date()
        for w in sorted(_windows_on(cfg, day), key=lambda w: w.start):
            start = datetime.combine(day, w.start, tzinfo=now.tzinfo)
            if start > now:
                return start
    return None


def comfort_end(cfg: RoomConfig, now: datetime, schedule: ScheduleState | None = None) -> datetime | None:
    """End of the comfort block or window running now, None outside of one."""
    if schedule is not None:
        return schedule.next_end if schedule.active else None

    at, end = now, None
    for _ in range(8):  # windows that follow each other (e.g. "00:00-23:59" every day) are one
        running = _window_at(cfg, at)
        if running is None:
            return end
        w, began = running
        if w.end == _LAST_MINUTE:
            # "to 23:59" is to the end of the day. Taken as 23:59 plus a day whenever it had passed,
            # the end lay a day ahead during that last minute: the room called the boiler for one
            # cycle every night at 23:59.
            end = datetime.combine(began + timedelta(days=1), time(), tzinfo=at.tzinfo)
        else:
            end = datetime.combine(began + timedelta(days=1 if w.across_midnight else 0), w.end, tzinfo=at.tzinfo)
        at = end + timedelta(minutes=1)
    return None  # comfort all week


def _default_rate(state: RoomState, params: Parameters) -> float:
    return state.heat_rate_k_h or (
        params.default_heat_rate_heat_pump_k_h if state.config.served_by_heat_pump else params.default_heat_rate_radiator_k_h
    )


def _latched(state: RoomState, for_ts: float) -> bool:
    """Preheating already began for this comfort start and must not be undone.

    The start time is recomputed from the current temperature every cycle, so a
    0.1 K sensor tick moves it by minutes. Without a latch the target flipped
    between comfort and setback each minute (measured: 21/17/21/17 at 14:10-14:16).
    """
    return state.preheat_latch_ts is not None and abs(state.preheat_latch_ts - for_ts) < 1.0


def preheat_start(
    return_ts: float,
    target_at_return: float,
    current_temp: float | None,
    heat_rate_k_h: float,
    margin_s: float,
) -> float:
    """Timestamp at which heating must start to reach the target at return time."""
    if current_temp is None or heat_rate_k_h <= 0:
        return return_ts - margin_s
    deficit = max(target_at_return - current_temp, 0.0)
    return return_ts - deficit / heat_rate_k_h * 3600.0 - margin_s


def target_temperature(
    state: RoomState,
    now: datetime,
    mode: str,
    params: Parameters,
    temperature: float | None,
    dew_point: float | None,
    away_return_ts: float | None,
) -> tuple[float, str, float | None]:
    """Return (target, reason, preheat_start_ts)."""
    cfg = state.config
    now_ts = now.timestamp()

    if state.override is not None and state.override.until_ts > now_ts:
        return state.override.target, "override", None

    if mode == MODE_OFF:
        return params.frost_temp, "mode_off_frost_protection", None

    if mode == MODE_AWAY:
        target, reason = params.away_temp, "mode_away"
        preheat_ts = None
        if away_return_ts is not None and away_return_ts > now_ts:
            # from the timestamp, not by adding seconds to the clock: across a clock change that is an hour off
            return_dt = datetime.fromtimestamp(away_return_ts, now.tzinfo)
            if state.schedule is not None:
                # a weekly schedule helper cannot be evaluated for a future time: assume comfort on return
                target_at_return = cfg.comfort_temp
            else:
                target_at_return, _ = scheduled_target(cfg, return_dt)
            rate = _default_rate(state, params)
            preheat_ts = preheat_start(away_return_ts, target_at_return, temperature, rate, params.preheat_margin_s)
            if now_ts >= preheat_ts or _latched(state, away_return_ts):
                return target_at_return, "away_preheat", preheat_ts
        if temperature is not None and dew_point is not None and temperature - dew_point < params.away_dewpoint_margin_k:
            # One degree above the room, but never above its comfort temperature: a humidity sensor
            # stuck near 100 % would otherwise pull the target up with the room, degree by degree.
            return max(target, min(temperature + 1.0, cfg.comfort_temp)), "away_humidity_guard", preheat_ts
        return target, reason, preheat_ts

    target, reason = scheduled_target(cfg, now, state.schedule)
    if reason == "schedule_setback":
        # start early and slowly instead of late and fast: reach comfort at the window start
        start = next_comfort_start(cfg, now, state.schedule)
        if start is not None and start.timestamp() - now_ts <= SCHEDULE_PREHEAT_HORIZON_S:
            ahead = cfg.comfort_temp
            if state.schedule_next_temp is not None:
                # the coming block of a schedule helper names its own temperature
                ahead = max(state.schedule_next_temp, target)
            if temperature is None:
                # No reading, as in the first minute after a restart: a preheating that began goes on
                # (it fell back to the setback for that minute, and the heat pump was set down with it),
                # none begins.
                if _latched(state, start.timestamp()):
                    return ahead, "schedule_preheat", None
                return target, reason, None
            rate = _default_rate(state, params)
            preheat_ts = preheat_start(start.timestamp(), ahead, temperature, rate, params.preheat_margin_s)
            if now_ts >= preheat_ts or _latched(state, start.timestamp()):
                return ahead, "schedule_preheat", preheat_ts
            return target, reason, preheat_ts
    return target, reason, None


def trend_k_per_h(
    history: tuple[tuple[float, float], ...],
    now_ts: float,
    window_s: float = 5400.0,
    min_span_s: float = 1800.0,
) -> float | None:
    """Least-squares slope of temperature over the recent window, in K/h.

    The window has to be long compared to the sensor's resolution. A typical
    Zigbee sensor reports in 0.1 K steps and only on change, so over a short
    window every sample carries the same value and the slope comes out as
    exactly zero — until one step enters the window, produces a spike, and
    leaves again. That sawtooth is quantisation, not temperature, and it would
    make the predicted demand jump from minute to minute.

    Ninety minutes resolves a drift of 0.2 K/h through a 0.1 K grid: the step
    spans a small fraction of the window instead of dominating it. A flat window
    still yields 0.0, which is then the honest answer.
    """
    pts = [(ts, t) for ts, t in history if now_ts - ts <= window_s]
    if len(pts) < 3:
        return None
    span = pts[-1][0] - pts[0][0]
    if span < min_span_s:
        return None
    n = len(pts)
    mean_x = sum(p[0] for p in pts) / n
    mean_y = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mean_x) ** 2 for p in pts)
    if sxx == 0:
        return None
    sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in pts)
    return sxy / sxx * 3600.0


def window_status(
    readings: tuple[tuple[str, str | None, float | None], ...],
    grace_s: float,
) -> tuple[bool | None, bool, float | None]:
    """Consolidate window contacts.

    Returns (open, unknown, open_since_s). `open` is True once any contact has
    been open for at least `grace_s`; None when no contact delivers a state.
    `unknown` is True when any contact delivers none: a window with a dead contact
    may stand open unseen, even while the others report.
    """
    if not readings:
        return None, False, None
    known = 0
    longest_open: float | None = None
    for _entity, state, open_since in readings:
        s = "" if state is None else state.lower()
        if s in WINDOW_OPEN_STATES:
            known += 1
            if open_since is None or open_since >= grace_s:
                longest_open = max(longest_open or 0.0, open_since or grace_s)
        elif s in WINDOW_CLOSED_STATES:
            known += 1
    unknown = known < len(readings)
    if not known:
        return None, True, None
    if longest_open is not None:
        return True, unknown, longest_open
    return False, unknown, None


def demand(
    target: float,
    temperature: float | None,
    trend: float | None,
    internal_gain_w: float | None,
    params: Parameters,
) -> float | None:
    if temperature is None:
        return None
    predicted = temperature + (trend or 0.0) * params.trend_weight_h
    raw = (target - predicted) / params.demand_full_delta_k
    if internal_gain_w is not None and internal_gain_w > params.gain_threshold_w:
        raw -= (internal_gain_w - params.gain_threshold_w) * params.gain_factor_per_w
    return min(max(raw, 0.0), 1.0)


# ---------------------------------------------------------------------------
# Bathroom drying mode
# ---------------------------------------------------------------------------
def humidity_baseline(history: tuple[tuple[float, float], ...], now_ts: float) -> float | None:
    """Median absolute humidity over the last 3 h, excluding the last 20 min."""
    pts = [v for ts, v in history if DRYING_RH_RISE_WINDOW_S <= now_ts - ts <= DRYING_BASELINE_WINDOW_S]
    if len(pts) < 3:
        return None
    return median(pts)





def evaluate_drying(
    state: RoomState,
    now_ts: float,
    temperature: float | None,
    rh: float | None,
    dew_point: float | None,
    abs_humidity: float | None,
    window_open_since_s: float | None,
    params: Parameters,
    heat_pump_heat_possible: bool = True,
) -> tuple[DryingState, str | None]:
    """Return the new drying state and a reason string.

    Drying is done with Midea air only. When the Midea cannot heat (manual
    mode, lockout, too cold outside) the mode does not start, and one under
    way waits without effect, so the radiator follows the normal window logic.
    """
    cfg = state.config
    prev = state.drying
    if not cfg.bathroom_drying or not cfg.served_by_heat_pump:
        return DryingState(), None
    under_way = prev.started_ts is not None  # drying, or waiting for the heat pump with its start time kept
    if under_way and now_ts - prev.started_ts > params.drying_max_s:
        # One started by hand leaves no guard time: it says nothing about the air a shower brings later.
        return DryingState(ended_ts=prev.ended_ts if prev.manual else now_ts), "drying_ended_timeout"
    if not heat_pump_heat_possible:
        if under_way:
            # Not ended: seen 2026-10-13, the unit was not there yet in the first cycle after a restart,
            # and a drying asked for with the button was gone. It goes on when the heat pump is back,
            # with the time it has left.
            return replace(prev, active=False), "drying_waiting_for_the_heat_pump"
        # Whatever interrupts here or below, the guard time of the last drying stays: dropped for one
        # cycle (heat pump unavailable, a reading missing), the same air started another hour.
        return DryingState(ended_ts=prev.ended_ts), None
    if under_way and not prev.active:
        prev = replace(prev, active=True)  # the heat pump is back
    running = under_way
    if running and prev.manual:
        return prev, "drying_active"  # asked for: it runs its time, whatever the humidity and the window
    if not cfg.drying_automatic:
        # Only with the button: no shower starts a drying, and one that was started by a shower
        # before the automatic was switched off ends here. The guard time of the last one stays.
        return DryingState(ended_ts=prev.ended_ts), None
    if temperature is None or rh is None or abs_humidity is None:
        # A missing reading says nothing about the air. A running drying goes on with its start
        # time: ended and started anew with the next reading, its time limit never applied.
        return (prev, "drying_active") if running else (DryingState(ended_ts=prev.ended_ts), None)

    baseline = humidity_baseline(state.abs_humidity_history, now_ts)
    window_open_long = window_open_since_s is not None and window_open_since_s > DRYING_WINDOW_NO_EFFECT_S

    if running:
        base = prev.baseline_abs_humidity if prev.baseline_abs_humidity is not None else baseline
        if window_open_long and base is not None and abs_humidity > base + DRYING_END_ABS_MARGIN_G_M3:
            # with the guard time of a drying that ran out: the air that ended it would start it again
            return DryingState(ended_ts=now_ts), "drying_ended_window_ineffective"
        dew_margin_ok = dew_point is not None and temperature - dew_point >= DRYING_END_DEWPOINT_MARGIN_K
        abs_ok = base is None or abs_humidity <= base + DRYING_END_ABS_MARGIN_G_M3
        if dew_margin_ok and abs_ok:
            return DryingState(), "drying_ended_humidity_normal"
        return prev, "drying_active"

    if prev.ended_ts is not None and now_ts - prev.ended_ts < DRYING_BASELINE_WINDOW_S:
        # the baseline still holds the air from before the shower: the same humidity would
        # start the drying again a minute after its time limit (measured 2026-09-25)
        return DryingState(ended_ts=prev.ended_ts), None
    if window_open_long:
        # No start that the next cycle ends as "window ineffective": that went on and off every
        # cycle. The drying may start once the window is shut.
        return DryingState(), None

    # not active: check triggers
    if baseline is None and prev.wet_since_ts is not None:
        # The humidity history lives in memory only and starts empty after a restart, while the store
        # keeps the wait for a shower: it measures the rise against the air it kept from before it.
        baseline = prev.baseline_abs_humidity
    rise_abs = None if baseline is None else abs_humidity - baseline
    trig_abs = rise_abs is not None and rise_abs > params.drying_abs_humidity_rise_g_m3
    # second trigger: air that is damp already (above 70 % relative humidity) needs a smaller rise
    # over the baseline, 60 % of the configured one
    trig_rh = rh > DRYING_RH_TRIGGER_PCT and rise_abs is not None and rise_abs > params.drying_abs_humidity_rise_g_m3 * 0.6
    if not (trig_abs or trig_rh):
        return DryingState(), None  # also when a load had been noticed: the air cleared by itself
    wet_since = prev.wet_since_ts if prev.wet_since_ts is not None else now_ts
    before = baseline if prev.wet_since_ts is None else prev.baseline_abs_humidity  # the air from before the shower
    climbing = prev.peak_abs_humidity is None or abs_humidity > prev.peak_abs_humidity + DRYING_PEAK_STEP_G_M3
    peak, peak_ts = (abs_humidity, now_ts) if climbing or prev.peak_ts is None else (prev.peak_abs_humidity, prev.peak_ts)
    if now_ts - peak_ts >= DRYING_AFTER_PEAK_S or now_ts - wet_since >= DRYING_WAIT_MAX_S:
        return DryingState(True, now_ts, before), "drying_started_humidity_load"
    waiting = DryingState(baseline_abs_humidity=before, wet_since_ts=wet_since, peak_abs_humidity=peak, peak_ts=peak_ts)
    return waiting, "drying_waiting_for_the_shower_to_end"


# ---------------------------------------------------------------------------
def next_schedule_change(state: RoomState, now: datetime, mode: str) -> tuple[float | None, float | None]:
    """When the schedule switches next, and the target it switches to: for a dashboard to show.

    The comfort windows: the end of the one running now (to the setback), else the start of the next
    (to the comfort). A schedule helper: the change its blocks make next (adapters/inputs.py). What the
    schedule does, whatever a change by hand or a quick heat-up does meanwhile; nothing in the modes
    that leave the schedule aside.
    """
    if mode in (MODE_AWAY, MODE_OFF):
        return None, None
    cfg = state.config
    if state.schedule is not None:
        # a schedule helper: its own next change, which also sees a block at another temperature
        change, target = state.schedule.next_change, state.schedule.next_change_target
        return (None, None) if change is None or target is None else (change.timestamp(), target)
    _, reason = scheduled_target(cfg, now)
    if reason == "schedule_comfort":
        end = comfort_end(cfg, now)
        return (None, None) if end is None else (end.timestamp(), cfg.setback_temp)
    start = next_comfort_start(cfg, now)
    return (None, None) if start is None else (start.timestamp(), cfg.comfort_temp)


def evaluate_room(
    state: RoomState,
    now: datetime,
    mode: str,
    params: Parameters,
    away_return_ts: float | None = None,
    heat_pump_heat_possible: bool = True,
    heat_pump_cooling: bool = False,
) -> RoomResult:
    cfg = state.config
    issues: list[str] = []
    now_ts = now.timestamp()

    temp = state.temperature.value_or_none
    temp_entity = state.temperature.entity_id if temp is not None else None
    if temp is None and state.trv_local_temp is not None:
        temp = state.trv_local_temp
        temp_entity = state.trv_local_entity
        issues.append(f"using_trv_local_temperature:{state.temperature.reason}")
    if temp is None:
        issues.append(f"temperature_invalid:{state.temperature.reason}")

    dew_point = None
    abs_hum = None
    rh = state.humidity.value_or_none
    # Relative humidity belongs to the temperature it was measured at. The valve's reading stands
    # in for a failed room sensor, but it sits at the radiator: paired with the room's humidity
    # the absolute humidity jumps, and a drying starts.
    own_temp = state.temperature.value_or_none
    if own_temp is not None and rh is not None:
        dew_point = psy.dew_point_c(own_temp, rh)
        abs_hum = psy.absolute_humidity_g_m3(own_temp, rh)

    target, target_reason, preheat_ts = target_temperature(state, now, mode, params, temp, dew_point, away_return_ts)
    preheat_for_ts: float | None = None
    if target_reason == "schedule_preheat":
        start = next_comfort_start(cfg, now, state.schedule)
        preheat_for_ts = None if start is None else start.timestamp()
    elif target_reason == "away_preheat":
        preheat_for_ts = away_return_ts
    boost_active = state.boost_until_ts is not None and state.boost_until_ts > now_ts
    if boost_active and target < cfg.comfort_temp:
        target, target_reason = cfg.comfort_temp, "boost"
    elif boost_active:
        target_reason = f"{target_reason}+boost"

    window_open, window_unknown, open_since = window_status(state.window_readings, params.window_grace_s)
    if window_unknown:
        issues.append("window_contacts_unavailable")

    drying, drying_reason = evaluate_drying(state, now_ts, temp, rh, dew_point, abs_hum, open_since, params, heat_pump_heat_possible)
    if drying.active:
        target = max(target, params.drying_target_temp)
        target_reason = "bathroom_drying"

    # Cooling never collides with heating: while the heat pump cools, the rooms it serves keep
    # their radiator at setback and call no boiler heat. Frost protection goes by temperature
    # (engines/safety.py) and still applies.
    cooled = heat_pump_cooling and cfg.served_by_heat_pump
    if cooled:
        target, target_reason = min(target, cfg.setback_temp), "heat_pump_cooling"

    # radiator / Better Thermostat: never with an open window, and not while the thermostat
    # is switched off by hand - its valve is shut, so the boiler would only heat the pipes.
    # Midea: independent of the valve, also while drying.
    thermostat_off = state.trv_hvac_mode == "off"
    thermostat_silent = state.thermostat_silent_s is not None and state.thermostat_silent_s > THERMOSTAT_SILENT_S
    if thermostat_silent:
        issues.append("thermostat_silent")
    heating_allowed = not bool(window_open) and not thermostat_off and not thermostat_silent and not cooled
    heat_pump_allowed = not bool(window_open) or drying.active

    trend = trend_k_per_h(state.temperature_history, now_ts)
    deviation = None if temp is None else round(temp - target, 2)
    dem = demand(target, temp, trend, state.internal_gain_w, params)
    if (not heat_pump_allowed or cooled) and dem is not None:
        dem = 0.0

    override_until = state.override.until_ts if state.override is not None and state.override.until_ts > now_ts else None
    end = comfort_end(cfg, now, state.schedule) if target_reason == "schedule_comfort" else None
    next_ts, next_target = next_schedule_change(state, now, mode)

    return RoomResult(
        key=cfg.key,
        target=target,
        target_reason=target_reason,
        temperature=temp,
        deviation_k=deviation,
        demand=dem,
        trend_k_per_h=None if trend is None else round(trend, 3),
        dew_point_c=None if dew_point is None else round(dew_point, 2),
        abs_humidity_g_m3=None if abs_hum is None else round(abs_hum, 2),
        window_open=window_open,
        window_unknown=window_unknown,
        heating_allowed=heating_allowed,
        internal_gain_w=state.internal_gain_w,
        issues=tuple(issues),
        override_until_ts=override_until,
        override_by=state.override.by if override_until is not None and state.override is not None else None,
        drying=drying,
        drying_reason=drying_reason,
        preheat_start_ts=preheat_ts,
        heat_pump_allowed=heat_pump_allowed,
        boost_active=boost_active,
        preheat_for_ts=preheat_for_ts,
        comfort_end_ts=None if end is None else end.timestamp(),
        thermostat_heating=state.thermostat_heating,
        temperature_entity=temp_entity,
        schedule_entity=cfg.schedule_entity,
        next_change_ts=next_ts,
        next_target=next_target,
    )
