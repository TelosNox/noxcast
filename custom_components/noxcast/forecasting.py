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
from . import curtailment as ct
from . import load as lf
from . import plan as pl
from .explain import hour_steps
from .const import MORNING_HOUR, TOTAL, Series
from .effects import day_effects
from .intraday import Partial, clearness_shift, expected_hours, held_forecast
from .learning import ArrayConfig, ConsumerConfig, highest, scaled_sum
from .installation import Installation
from .model import SUBSTEPS, LearnedArray, WeatherHour, clearsky_profile, clearsky_weather, forecast
from .solar import Site

RECENT_MIN_SLOTS_HOUR = 10       # 5-minute values needed to stand in for a just-ended hour
RECENT_MIN_SLOTS_RUNNING = 3     # 5-minute values (15 minutes) before the running hour counts


@dataclass
class NoxcastData:
    """Hourly Wh per array (subentry_id) and as total (key TOTAL)."""

    forecast: dict[str | None, Series] = field(default_factory=dict)   # weather forecast, 3 days
    morning: dict[str | None, Series] = field(default_factory=dict)    # today, fixed in the morning
    morning_steps: dict[str, dict[float, dict]] = field(default_factory=dict)  # how each morning hour came about
    expected: dict[str | None, Series] = field(default_factory=dict)   # today: actual + corrected rest
    clear: dict[str | None, Series] = field(default_factory=dict)      # clear-sky output over the horizon
    held: dict[str, Series] = field(default_factory=dict)  # today: latest forecast, held once the hour began
    correction: dict[str, float | None] = field(default_factory=dict)              # intraday clearness shift
    effects: dict[str, dict] = field(default_factory=dict)        # today per array: loss, gain (Wh/h), periods
    morning_fixed: datetime | None = None
    morning_late: dict[str, datetime] = field(default_factory=dict)       # arrays fixed later (added that day)
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
    limits: dict[str, float] = field(default_factory=dict)    # arrays with a fixed limit (W), capped last
    # forecast, morning and expected before the cap, by the same keys (arrays and TOTAL); empty without limits
    uncapped: dict[str, dict[str | None, Series]] = field(default_factory=dict)
    # per limited array: hour start -> clear-sky power at the sub-steps, the shape the cap follows
    profiles: dict[str, dict[float, list[float]]] = field(default_factory=dict, repr=False)

    @property
    def arrays(self) -> dict[str, Series]:
        return {k: v for k, v in self.forecast.items() if k is not TOTAL}

    @property
    def total(self) -> Series:
        return self.forecast.get(TOTAL, {})

    def fixed_at(self, sub_id: str | None) -> datetime | None:
        """When the morning values of an array were fixed; for the total the last of its arrays."""
        if self.morning_fixed is None:
            return None
        if sub_id is TOTAL:
            return max([self.morning_fixed, *self.morning_late.values()])
        return self.morning_late.get(sub_id, self.morning_fixed)


@dataclass
class Today:
    """Carried from one update to the next (and stored): per array Wh per hour start."""

    hours: dict[str, Series] = field(default_factory=dict)     # forecast since midnight, past hours kept
    morning: dict[str, Series] = field(default_factory=dict)   # today's forecast as fixed in the morning
    morning_fixed: datetime | None = None
    held: dict[str, Series] = field(default_factory=dict)      # see intraday.held_forecast
    morning_steps: dict[str, dict[float, dict]] = field(default_factory=dict)  # explain.hour_steps of the morning fix
    morning_late: dict[str, datetime] = field(default_factory=dict)   # arrays fixed after morning_fixed


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


def curtailed_hours(hourly: dict[str, dict[float, float | None]], recent: dict[str, dict[float, float | None]],
                    live: list[float], soc_full: float, now: float) -> set[float]:
    """Today's hour starts in which the PV was possibly curtailed: the highest state of charge reached soc_full.

    The same criterion as learning (model.usable_samples) and the archive's curtailed flag. hourly: hourly
    maxima since midnight; recent: 5-minute maxima since the previous hour, since hourly statistics come a
    few minutes after the hour; live: the current states, which count for the running hour.
    """
    hours = {t for t, v in highest(hourly).items() if v >= soc_full}
    hours |= {t - t % 3600 for t, v in highest(recent).items() if v >= soc_full}
    if live and max(live) >= soc_full:
        hours.add(now - now % 3600)
    return hours


def pv(site: Site, arrays: dict[str, ArrayConfig], learned: dict[str, LearnedArray], weather: list[WeatherHour],
       models: dict[str, list[WeatherHour]], before: Today, actual: dict[str, Series],
       running: dict[str, tuple[float, float, float]], now: datetime, local_now: datetime, day_start: float,
       day_end: float, curtailed: set[float] | frozenset[float] = frozenset(), inst: Installation | None = None,
       limited: dict[str, set[float]] | None = None) -> tuple[NoxcastData, Today, dict[str, dict[str, Series]]]:
    """PV forecast per array and in total: weather forecast, morning fix, clear sky, today's expected hours.

    weather: hours from local midnight to the horizon; models: each weather model on its own (for the
    archive); now: UTC, local_now: the same in the local time zone; curtailed: today's hour starts with possibly
    curtailed PV (curtailed_hours), which the intraday correction does not read as clouds; inst: the
    installation, whose fixed limits cap the forecast last; limited: per array today's hour starts at its fixed
    limit, which the intraday correction treats like curtailed hours. Returns (data, the new Today, the
    forecast per model and array).

    Everything up to the published values works on the uncapped forecast, so the intraday correction and the
    band vary the clouds; the cap comes last (curtailment.cap) and the uncapped values stay in data.uncapped.
    """
    ts_now = now.timestamp()
    result = NoxcastData(day_start=day_start)
    today = Today(dict(before.hours), dict(before.morning), before.morning_fixed, dict(before.held),
                  dict(before.morning_steps), dict(before.morning_late))
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
        today.morning_steps = {}
        today.morning_late = {}
    if today.morning_fixed is None and local_now.hour >= MORNING_HOUR:
        today.morning_fixed = now
    if today.morning_fixed is not None:
        # an array added after the fix gets fixed at its first update instead of following the weather all day
        new = [sid for sid in arrays if sid not in today.morning]
        # with the same weather and model as the fixed values, so the day view explains exactly them
        day_weather = [w for w in weather if w.start < day_end]
        for sid in new:
            today.morning[sid] = {t: v for t, v in result.forecast[sid].items() if t < day_end}
            today.morning_steps[sid] = hour_steps(site, arrays[sid].geometry, learned.get(sid, LearnedArray()),
                                                  day_weather)
            if today.morning_fixed != now:
                today.morning_late[sid] = now
    result.morning_fixed, result.morning_steps = today.morning_fixed, today.morning_steps
    result.morning_late = today.morning_late

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
        cut = curtailed | (limited or {}).get(sub_id, set())
        shift = clearness_shift(day, actual.get(sub_id, {}), clear, ts_now, arr.geometry.peak_w, partial, cut)
        result.correction[sub_id] = None if shift is None else round(shift, 3)
        result.expected[sub_id] = expected_hours(day, actual.get(sub_id, {}), clear, ts_now, shift)
        corrected = {t: v for t, v in result.expected[sub_id].items() if t > ts_now}
        today.held[sub_id] = held_forecast(before.held.get(sub_id) or result.morning[sub_id], corrected, ts_now,
                                           day_start)
        result.held[sub_id] = today.held[sub_id]
        result.effects[sub_id] = day_effects(site, arr.geometry, learned.get(sub_id), weather, day,
                                             result.expected[sub_id], day_start, day_end, clear_weather,
                                             today.held[sub_id])
    limits = {sid: w for sid, w in (inst.limited_arrays() if inst else {}).items() if sid in arrays}
    if limits:
        _cap(result, alt, site, arrays, learned, limits, temps, actual, ts_now)
    for series in (result.forecast, result.morning, result.expected, result.clear, *result.uncapped.values()):
        series[TOTAL] = _total(series)
    return result, today, alt


def _total(series: dict[str | None, Series]) -> Series:
    total: Series = {}
    for sid, hours in series.items():
        if sid is TOTAL:
            continue
        for t, wh in hours.items():
            total[t] = total.get(t, 0.0) + wh
    return dict(sorted(total.items()))


def _cap(result: NoxcastData, alt: dict[str, dict[str, Series]], site: Site, arrays: dict[str, ArrayConfig],
         learned: dict[str, LearnedArray], limits: dict[str, float], temps: dict[float, float | None],
         actual: dict[str, Series], now: float) -> None:
    """Cap the published values of arrays with a fixed limit, keeping the uncapped ones (see pv).

    Measured hours stay as measured. Held values are capped too: they are forecasts of hours that have begun.
    """
    result.limits = dict(limits)
    result.uncapped = {name: {sid: dict(getattr(result, name)[sid]) for sid in arrays}
                       for name in ("forecast", "morning", "expected")}
    for sid, limit in limits.items():
        geo, la = arrays[sid].geometry, learned.get(sid, LearnedArray())
        profiles = result.profiles.setdefault(sid, {})

        def profile(t: float, geo=geo, la=la, profiles=profiles) -> list[float]:
            if t not in profiles:
                profiles[t] = clearsky_profile(site, geo, la, t, temps.get(t), SUBSTEPS)
            return profiles[t]

        result.forecast[sid] = ct.cap(result.forecast[sid], limit, profile)
        result.morning[sid] = ct.cap(result.morning[sid], limit, profile)
        result.held[sid] = ct.cap(result.held[sid], limit, profile)
        measured = actual.get(sid, {})
        result.expected[sid] = {t: v if t + 3600 <= now and t in measured else ct.cap_hour(v, limit, profile(t))
                                for t, v in result.expected[sid].items()}
        for hours in alt.values():
            if sid in hours:
                hours[sid] = ct.cap(hours[sid], limit, profile)


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
    if result.limits:
        # the band varies the clouds of the uncapped forecast; the limits cap it afterwards
        raw = {**result.uncapped["forecast"][TOTAL], **result.uncapped["expected"][TOTAL]}
        result.band = capped_band(bd.forecast_band(band, site, result.weather, raw, result.uncapped["expected"][TOTAL],
                                                   result.clear.get(TOTAL, {}), now, day_end), result, now, day_end)
    else:
        result.band = bd.forecast_band(band, site, result.weather, total, result.expected.get(TOTAL, {}),
                                       result.clear.get(TOTAL, {}), now, day_end)
    load_hours = result.load.hours if result.load is not None else None
    arrays = {sid: {**hours, **result.expected.get(sid, {})} for sid, hours in result.arrays.items()}
    result.battery = bt.day_simulation(batteries, dict(total), load_hours, (result.band or {}).get("hours"), now, day_end,
                                       arrays)
    if result.load is None or not runs:
        result.plan = {}
        return
    house_pv, lost = usable(total, result.battery, now)
    result.plan = pl.day_plan(runs, house_pv, load_hours, lost, now, day_end)


def capped_band(band: dict | None, result: NoxcastData, now: float, day_end: float) -> dict | None:
    """The band of the uncapped total (band.forecast_band) under the fixed limits of the arrays.

    A band value is the uncapped total with its clearness shifted by s. The same shift is applied to each array
    (bounded as in band.py), each limited array is capped like its forecast, and the arrays are summed again.
    """
    if not band:
        return band
    arrays = [sid for sid in result.uncapped["forecast"] if sid is not TOTAL]
    raw = {sid: {**result.uncapped["forecast"][sid], **result.uncapped["expected"][sid]} for sid in arrays}
    total_raw = {**result.uncapped["forecast"][TOTAL], **result.uncapped["expected"][TOTAL]}
    sky = result.clear.get(TOTAL, {})
    expected = result.expected.get(TOTAL, {})

    def at(t: float, s: float) -> float:
        out = 0.0
        for sid in arrays:
            f = raw[sid].get(t)
            if f is None:
                continue
            c = result.clear.get(sid, {}).get(t, f)
            v = min(max(f + s * c, 0.0), max(c * bd.CLEARNESS_MAX, f))
            limit = result.limits.get(sid)
            out += ct.cap_hour(v, limit, result.profiles.get(sid, {}).get(t)) if limit is not None else v
        return out

    def shift(value_wh: float, ts: list[float], weight=lambda t: 1.0) -> float:
        f = sum(total_raw.get(t, 0.0) * weight(t) for t in ts)
        c = sum(sky.get(t, total_raw.get(t, 0.0)) * weight(t) for t in ts)
        return (value_wh - f) / c if c > 0 else 0.0

    hours = {t: tuple(at(t, shift(v, [t])) for v in pair) for t, pair in band["hours"].items()}
    days = {}
    for offset, pair in band["days"].items():
        if offset == 0:
            continue
        a, b = day_end + (offset - 1) * 86400, day_end + offset * 86400
        ts = [t for t in total_raw if a <= t < b]
        days[offset] = [round(sum(at(t, shift(v * 1000, ts)) for t in ts) / 1000, 3) for v in pair]
    rest = None
    if band.get("rest") is not None:
        ts = [t for t in expected if t + 3600 > now and t < day_end]

        def share(t: float) -> float:
            return min(1.0, (t + 3600 - now) / 3600)
        rest = [round(sum(at(t, shift(v * 1000, ts, share)) * share(t) for t in ts) / 1000, 3) for v in band["rest"]]
        done = sum(v for t, v in expected.items() if t < day_end) - sum(expected[t] * share(t) for t in ts)
        days[0] = [round(done / 1000 + r, 3) for r in rest]
    return {"hours": hours, "days": dict(sorted(days.items())), "rest": rest}


def usable(total: Series, battery: dict | None, now: float) -> tuple[Series, Series]:
    """PV that can reach the house, and per hour the surplus the battery would lose, for planning consumers.

    PV above an inverter's output cap only charges the battery behind it; what it cannot take is curtailed.
    Neither can run a consumer in the house.
    """
    if not battery:
        return dict(total), {}
    capped = battery["limits"].capped if battery.get("limits") else {}
    house = {t: max(v - capped.get(t, 0.0), 0.0) for t, v in total.items()}

    def curtailed(t: float, h: dict) -> float:
        # the battery takes capped PV first, since nothing else can; with the house drawing at the same time
        # its net flow is smaller than what it took, and then all that is lost is capped PV anyway
        # the running hour is simulated from now on, so only its rest of the capped PV counts
        share = min(max((t + 3600 - max(t, now)) / 3600, 0.0), 1.0)
        return min(h["lost"], max(capped.get(t, 0.0) * share - max(h["battery"], 0.0), 0.0))

    return house, {t: max(h["lost"] - curtailed(t, h), 0.0) for t, h in battery["sim"].hours.items()}
