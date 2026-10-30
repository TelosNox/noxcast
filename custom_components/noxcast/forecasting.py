"""Hourly forecast pipeline: PV per array and in total, intraday correction, load, band, battery and plan.

Pure functions, no Home Assistant imports. The coordinator fetches the weather, today's measurements and
the recent load, reads the current states (batteries, running consumers), calls these and publishes the
result. What one update hands to the next (today's hours, the morning fix, held values) is a `Today`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from . import band as bd
from . import battery as bt
from . import load as lf
from . import plan as pl
from .const import MORNING_HOUR, TOTAL, Series
from .effects import day_effects
from .intraday import Partial, clearness_shift, expected_hours, held_forecast
from .learning import ArrayConfig, ConsumerConfig, scaled_sum
from .model import LearnedArray, WeatherHour, clearsky_profile, clearsky_weather, forecast
from .solar import Site

RECENT_MIN_SLOTS_HOUR = 10       # 5-minute values needed to stand in for a just-ended hour
RECENT_MIN_SLOTS_RUNNING = 3     # 5-minute values (15 minutes) before the running hour counts


@dataclass
class NoxcastData:
    """Hourly Wh per array (subentry_id) and as total (key TOTAL)."""

    forecast: dict[str | None, Series] = field(default_factory=dict)   # weather forecast, 3 days
    morning: dict[str | None, Series] = field(default_factory=dict)    # today, fixed in the morning
    expected: dict[str | None, Series] = field(default_factory=dict)   # today: actual + corrected rest
    clear: dict[str | None, Series] = field(default_factory=dict)      # clear-sky output over the horizon
    held: dict[str, Series] = field(default_factory=dict)  # today: latest forecast, held once the hour began
    correction: dict[str, float | None] = field(default_factory=dict)              # intraday clearness shift
    effects: dict[str, dict] = field(default_factory=dict)        # today per array: loss, gain (Wh/h), periods
    morning_fixed: datetime | None = None
    updated: datetime | None = None
    day_start: float | None = None            # local midnight of the day these values were computed for
    weather_fetched: datetime | None = None                                # last successful Open-Meteo answer
    load: lf.LoadForecast | None = None       # house load from the running hour until the end of tomorrow
    battery: dict | None = None               # simulation until tomorrow's charge start (see battery.day_simulation)
    plan: dict[str, dict] = field(default_factory=dict)   # flexible consumer -> windows today and tomorrow
    band: dict | None = None                  # P10 / P90 of the total: hours, days, rest of today (see band.forecast_band)
    actual: dict[str, Series] = field(default_factory=dict)   # today's measured hours per array
    weather: list[WeatherHour] = field(default_factory=list)              # weather hours from today on
    outlook: list[WeatherHour] = field(default_factory=list)              # the same for a week: the weather entity

    @property
    def arrays(self) -> dict[str, Series]:
        return {k: v for k, v in self.forecast.items() if k is not TOTAL}

    @property
    def total(self) -> Series:
        return self.forecast.get(TOTAL, {})


@dataclass
class Today:
    """Carried from one update to the next (and stored): per array Wh per hour start."""

    hours: dict[str, Series] = field(default_factory=dict)     # forecast since midnight, past hours kept
    morning: dict[str, Series] = field(default_factory=dict)   # today's forecast as fixed in the morning
    morning_fixed: datetime | None = None
    held: dict[str, Series] = field(default_factory=dict)      # see intraday.held_forecast


def today_actual(arrays: dict[str, ArrayConfig], hourly: dict[str, dict[float, float | None]],
                 recent: dict[str, dict[float, float | None]], factors: dict[str, float], day_start: float,
                 now: float) -> tuple[dict[str, Series], dict[str, tuple[float, float, float]]]:
    """Measured mean power (W = Wh) of today's hours per array, and the running hour.

    hourly: hourly means since midnight; recent: 5-minute means since the start of the previous hour.
    Hourly statistics are compiled a few minutes after each hour; the hour just ended and the running
    hour therefore come from 5-minute statistics. Returns (hours, running) with
    running = (hour start, mean power of the measured part, measured share).
    """
    hour_now = now - now % 3600
    hours: dict[str, Series] = {}
    running: dict[str, tuple[float, float, float]] = {}
    for sub_id, arr in arrays.items():
        measured = scaled_sum(hourly, arr.sensors, factors)
        slots = scaled_sum(recent, arr.sensors, factors)
        prev = hour_now - 3600
        done = [v for t, v in slots.items() if prev <= t < hour_now]
        if prev >= day_start and prev not in measured and len(done) >= RECENT_MIN_SLOTS_HOUR:
            measured[prev] = sum(done) / len(done)
        part = [v for t, v in slots.items() if hour_now <= t < now]
        if len(part) >= RECENT_MIN_SLOTS_RUNNING:
            running[sub_id] = (hour_now, sum(part) / len(part), len(part) / 12)
        hours[sub_id] = measured
    return hours, running


def pv(site: Site, arrays: dict[str, ArrayConfig], learned: dict[str, LearnedArray], weather: list[WeatherHour],
       models: dict[str, list[WeatherHour]], before: Today, actual: dict[str, Series],
       running: dict[str, tuple[float, float, float]], now: datetime, local_now: datetime, day_start: float,
       day_end: float) -> tuple[NoxcastData, Today, dict[str, dict[str, Series]]]:
    """PV forecast per array and in total: weather forecast, morning fix, clear sky, today's expected hours.

    weather: hours from local midnight to the horizon; models: each weather model on its own (for the
    archive); now: UTC, local_now: the same in the local time zone. Returns (data, the new Today, the
    forecast per model and array).
    """
    ts_now = now.timestamp()
    result = NoxcastData(day_start=day_start)
    today = Today(dict(before.hours), dict(before.morning), before.morning_fixed, dict(before.held))
    alt: dict[str, dict[str, Series]] = {m: {} for m in models}
    for sub_id, arr in arrays.items():
        la = learned.get(sub_id, LearnedArray())
        fresh = forecast(site, arr.geometry, la, weather)
        # keep earlier values for past hours the source no longer reports
        merged = {t: v for t, v in before.hours.get(sub_id, {}).items() if t >= day_start}
        merged.update(fresh)
        today.hours[sub_id] = merged
        result.forecast[sub_id] = dict(sorted(merged.items()))
        for m, hours in models.items():
            alt[m][sub_id] = forecast(site, arr.geometry, la, hours)

    # today's forecast is fixed in the morning; before that it follows the weather source
    if today.morning_fixed is None or today.morning_fixed.astimezone(local_now.tzinfo).date() != local_now.date():
        today.morning_fixed = None
        today.morning = {}
    if today.morning_fixed is None and local_now.hour >= MORNING_HOUR:
        today.morning_fixed = now
        today.morning = {sid: {t: v for t, v in h.items() if t < day_end} for sid, h in result.arrays.items()}
    result.morning_fixed = today.morning_fixed

    result.actual, result.weather = actual, weather
    temps = {w.start: w.temp for w in weather}
    clear_weather = [clearsky_weather(site, t, temps.get(t))
                     for t in sorted(result.forecast.get(next(iter(arrays), None), {}))]
    for sub_id, arr in arrays.items():
        la = learned.get(sub_id, LearnedArray())
        day = {t: v for t, v in result.forecast[sub_id].items() if t < day_end}
        result.morning[sub_id] = today.morning.get(sub_id) or day
        result.clear[sub_id] = forecast(site, arr.geometry, la, clear_weather)
        clear = {t: v for t, v in result.clear[sub_id].items() if t < day_end}
        partial = None
        if sub_id in running:
            start, power, share = running[sub_id]
            profile = clearsky_profile(site, arr.geometry, la, start, temps.get(start))
            measured = profile[:round(share * len(profile))]
            if measured and sum(profile) > 0:
                partial = Partial(start, power, share, (sum(measured) / len(measured)) / (sum(profile) / len(profile)))
        shift = clearness_shift(day, actual.get(sub_id, {}), clear, ts_now, arr.geometry.peak_w, partial)
        result.correction[sub_id] = None if shift is None else round(shift, 3)
        result.expected[sub_id] = expected_hours(day, actual.get(sub_id, {}), clear, ts_now, shift)
        corrected = {t: v for t, v in result.expected[sub_id].items() if t > ts_now}
        today.held[sub_id] = held_forecast(before.held.get(sub_id) or result.morning[sub_id], corrected, ts_now,
                                           day_start)
        result.held[sub_id] = today.held[sub_id]
        result.effects[sub_id] = day_effects(site, arr.geometry, learned.get(sub_id), weather, day,
                                             result.expected[sub_id], day_start, day_end)
    for series in (result.forecast, result.morning, result.expected, result.clear):
        total: Series = {}
        for hours in list(series.values()):
            for t, wh in hours.items():
                total[t] = total.get(t, 0.0) + wh
        series[TOTAL] = dict(sorted(total.items()))
    return result, today, alt


def load(house_load: str, consumers: dict[str, ConsumerConfig], profile: lf.Profile, floor_profile: lf.Profile | None,
         profiles: dict[str, lf.Profile], models: dict[str, dict], series: dict[str, Series],
         floor: Series | None, running: dict[str, lf.Run], zone: tzinfo, tz: tzinfo, now: float,
         end: float, day_start: float, temps: dict[float, float | None] | None = None) -> lf.LoadForecast:
    """Load forecast with the correction from the last hours and the consumers running now.

    series: recent load and consumer power (W) since at most midnight; floor: recent hourly minimum of
    the house load (None = unavailable); zone: local time zone with day types, tz: the plain one.
    """
    base = lf.base_series(series.get(house_load, {}), [series.get(c.series_id, {}) for c in consumers.values()])
    sustained = (0.0, None)
    if floor_profile is not None and floor is not None:
        floor_base = lf.base_series(floor, [series.get(c.series_id, {}) for c in consumers.values()])
        sustained = lf.sustained(floor_base, floor_profile, zone, now)
    # the correction factor follows the events on top of a sustained load
    k = lf.correction({t: max(0.0, v - sustained[0]) for t, v in base.items()}, profile, zone, now)
    # mean forecast temperature per local day, for regulated consumers (partial days count with what they have)
    by_day: dict = {}
    for t, v in (temps or {}).items():
        if v is not None:
            by_day.setdefault(datetime.fromtimestamp(t, tz).date(), []).append(v)
    day_temp = {d: sum(v) / len(v) for d, v in by_day.items()}
    out = []
    for sid, c in consumers.items():
        m = models.get(sid, {})
        out.append(lf.Consumer(sid, c.timing, profiles.get(sid, lf.Profile()), c.kind, m.get("program"),
                               m.get("session"), lf.runs_since(series.get(c.series_id, {}), day_start, now, sid in running),
                               m.get("regulated"), day_temp))
    return lf.forecast(profile, out, zone, now, end, k, running, sustained)


def surplus(result: NoxcastData, site: Site, band: dict | None, batteries: list[bt.Battery],
            runs: list[pl.RunProfile], now: float, day_end: float) -> None:
    """Band, battery simulation and plan of flexible consumers, set on result (needs its PV and load)."""
    total = dict(result.forecast.get(TOTAL, {}))
    total.update(result.expected.get(TOTAL, {}))          # today: actual so far and the corrected rest
    result.band = bd.forecast_band(band, site, result.weather, total, result.expected.get(TOTAL, {}),
                                   result.clear.get(TOTAL, {}), now, day_end)
    load_hours = result.load.hours if result.load is not None else None
    result.battery = bt.day_simulation(batteries, dict(total), load_hours, (result.band or {}).get("hours"), now, day_end)
    if result.load is None or not runs:
        result.plan = {}
        return
    lost = {t: h["lost"] for t, h in result.battery["sim"].hours.items()} if result.battery else {}
    result.plan = pl.day_plan(runs, dict(total), load_hours, lost, now, day_end)
