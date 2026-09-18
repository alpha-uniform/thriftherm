"""Room engine: target temperature, deviation, demand, trend, window logic,
overrides, away mode with return preheat, bathroom drying mode.

Target precedence (highest first):
    manual override → mode off (frost) → mode away (with preheat / humidity
    guard) → schedule (with preheat so the comfort temperature is reached when
    the comfort window starts). "Quick heat-up" raises the result to at least
    the comfort temperature. The bathroom drying mode raises the target and lets
    the Midea keep heating while the window is open; it only runs when the
    Midea can heat, otherwise the radiator follows the normal window logic.
"""

from __future__ import annotations

from statistics import median

from datetime import datetime, time, timedelta

from ..const import MODE_AWAY, MODE_OFF
from ..models import DryingState, Parameters, RoomConfig, RoomResult, RoomState, ScheduleState, TimeWindow
from . import psychrometrics as psy

WINDOW_OPEN_STATES = ("on", "open", "true")
WINDOW_CLOSED_STATES = ("off", "closed", "false")

DRYING_BASELINE_WINDOW_S = 3 * 3600.0
DRYING_RH_TRIGGER_PCT = 70.0
DRYING_RH_RISE_WINDOW_S = 20 * 60.0
DRYING_END_DEWPOINT_MARGIN_K = 4.0
DRYING_END_ABS_MARGIN_G_M3 = 1.0
DRYING_WINDOW_NO_EFFECT_S = 20 * 60.0
SCHEDULE_PREHEAT_HORIZON_S = 12 * 3600.0


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
    windows = cfg.schedule_weekend if at.weekday() >= 5 else cfg.schedule_weekday
    if any(w.contains(at.time()) for w in windows):
        return cfg.comfort_temp, "schedule_comfort"
    return cfg.setback_temp, "schedule_setback"


def next_comfort_start(cfg: RoomConfig, now: datetime, schedule: ScheduleState | None = None) -> datetime | None:
    """Start of the next comfort block (schedule helper) or comfort window."""
    if schedule is not None:
        return None if schedule.active else schedule.next_start
    for day in range(3):
        date = (now + timedelta(days=day)).date()
        windows = cfg.schedule_weekend if date.weekday() >= 5 else cfg.schedule_weekday
        for w in sorted(windows, key=lambda w: w.start):
            start = datetime.combine(date, w.start, tzinfo=now.tzinfo)
            if start > now:
                return start
    return None


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
            return_dt = now + timedelta(seconds=away_return_ts - now_ts)
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
            return max(target, temperature + 1.0), "away_humidity_guard", preheat_ts
        return target, reason, preheat_ts

    target, reason = scheduled_target(cfg, now, state.schedule)
    if reason == "schedule_setback" and temperature is not None:
        # start early and slowly instead of late and fast: reach comfort at the window start
        start = next_comfort_start(cfg, now, state.schedule)
        if start is not None and start.timestamp() - now_ts <= SCHEDULE_PREHEAT_HORIZON_S:
            preheat_ts = preheat_start(start.timestamp(), cfg.comfort_temp, temperature, _default_rate(state, params), params.preheat_margin_s)
            if now_ts >= preheat_ts or _latched(state, start.timestamp()):
                return cfg.comfort_temp, "schedule_preheat", preheat_ts
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
    """
    if not readings:
        return None, False, None
    any_known = False
    longest_open: float | None = None
    for _entity, state, open_since in readings:
        if state is None:
            continue
        s = state.lower()
        if s in WINDOW_OPEN_STATES:
            any_known = True
            if open_since is None or open_since >= grace_s:
                longest_open = max(longest_open or 0.0, open_since or grace_s)
        elif s in WINDOW_CLOSED_STATES:
            any_known = True
    if not any_known:
        return None, True, None
    if longest_open is not None:
        return True, False, longest_open
    return False, False, None


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
    mode, lockout, too cold outside) the mode does not start, and an active
    one ends, so the radiator follows the normal window logic.
    """
    cfg = state.config
    prev = state.drying
    if not cfg.bathroom_drying or not cfg.served_by_heat_pump or temperature is None or rh is None or abs_humidity is None:
        return DryingState(), None
    if not heat_pump_heat_possible:
        return DryingState(), ("drying_ended_midea_unavailable" if prev.active else None)

    baseline = humidity_baseline(state.abs_humidity_history, now_ts)

    if prev.active and prev.started_ts is not None:
        base = prev.baseline_abs_humidity if prev.baseline_abs_humidity is not None else baseline
        elapsed = now_ts - prev.started_ts
        if elapsed > params.drying_max_s:
            return DryingState(), "drying_ended_timeout"
        if window_open_since_s is not None and window_open_since_s > DRYING_WINDOW_NO_EFFECT_S and base is not None and abs_humidity > base + DRYING_END_ABS_MARGIN_G_M3:
            return DryingState(), "drying_ended_window_ineffective"
        dew_margin_ok = dew_point is not None and temperature - dew_point >= DRYING_END_DEWPOINT_MARGIN_K
        abs_ok = base is None or abs_humidity <= base + DRYING_END_ABS_MARGIN_G_M3
        if dew_margin_ok and abs_ok:
            return DryingState(), "drying_ended_humidity_normal"
        return prev, "drying_active"

    # not active: check triggers
    rise_abs = None if baseline is None else abs_humidity - baseline
    trig_abs = rise_abs is not None and rise_abs > params.drying_abs_humidity_rise_g_m3
    # relative-humidity based trigger uses the abs history timestamps with rh re-derived is not available;
    # the caller passes rh directly, so use a coarse rule: high RH and a large recent absolute rise
    trig_rh = rh > DRYING_RH_TRIGGER_PCT and rise_abs is not None and rise_abs > params.drying_abs_humidity_rise_g_m3 * 0.6
    if trig_abs or trig_rh:
        return DryingState(True, now_ts, baseline), "drying_started_humidity_load"
    return DryingState(), None


# ---------------------------------------------------------------------------
def evaluate_room(
    state: RoomState,
    now: datetime,
    mode: str,
    params: Parameters,
    away_return_ts: float | None = None,
    heat_pump_heat_possible: bool = True,
) -> RoomResult:
    cfg = state.config
    issues: list[str] = []
    now_ts = now.timestamp()

    temp = state.temperature.value_or_none
    if temp is None and state.trv_local_temp is not None:
        temp = state.trv_local_temp
        issues.append(f"using_trv_local_temperature:{state.temperature.reason}")
    if temp is None:
        issues.append(f"temperature_invalid:{state.temperature.reason}")

    dew_point = None
    abs_hum = None
    rh = state.humidity.value_or_none
    if temp is not None and rh is not None:
        dew_point = psy.dew_point_c(temp, rh)
        abs_hum = psy.absolute_humidity_g_m3(temp, rh)

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

    # radiator / Better Thermostat: never with an open window, and not while the thermostat
    # is switched off by hand - its valve is shut, so the boiler would only heat the pipes.
    # Midea: independent of the valve, also while drying.
    thermostat_off = state.trv_hvac_mode == "off"
    heating_allowed = not bool(window_open) and not thermostat_off
    heat_pump_allowed = not bool(window_open) or drying.active

    trend = trend_k_per_h(state.temperature_history, now_ts)
    deviation = None if temp is None else round(temp - target, 2)
    dem = demand(target, temp, trend, state.internal_gain_w, params)
    if not heat_pump_allowed and dem is not None:
        dem = 0.0

    override_until = state.override.until_ts if state.override is not None and state.override.until_ts > now_ts else None

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
        drying=drying,
        drying_reason=drying_reason,
        preheat_start_ts=preheat_ts,
        heat_pump_allowed=heat_pump_allowed,
        boost_active=boost_active,
        preheat_for_ts=preheat_for_ts,
    )
