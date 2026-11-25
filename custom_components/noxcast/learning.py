"""Learning pipeline: PV arrays, the archive's actual and measured values, house load and consumers.

Pure functions, no Home Assistant imports. The coordinator fetches statistics and archives (async),
runs these in the executor on the fetched data and keeps the results. keep(hour_start) optionally
restricts learning to some hours, meant as whole days: a backtest learns on even days and tests on
odd ones. Without it everything available is used, as in production.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo

from . import archive as fc_archive
from . import arraycheck as ac
from . import events as ev
from . import curtailment as ct
from . import fine as fn
from . import load as lf
from . import plan as pl
from .model import (
    AZ_STEP, CLEAR_KT, EFFICIENCY_PLAUSIBLE, EL_STEP, HOUR, MIN_UNSHADED_HOURS, SUBSTEPS, ArrayGeometry, LearnedArray, WeatherHour,
    clearsky_hour, clearsky_profile, clearsky_weather, forecast, known_free, learn, measured_forecast, sample_reasons,
    learn_snow, snow_scan, solar_date, solar_day, steady_hours, sun_path_cache, usable_samples, weighted_quantile,
)
from .model import dead_days as model_dead_days
from .installation import Installation
from .solar import Site, sun_position
from .const import Series

_LOGGER = logging.getLogger(__name__)

Keep = Callable[[float], bool]
Series = Series


@dataclass(frozen=True)
class Segment:
    """An array before a system swap (swap.py, docs/concept.md System swap): its sensors and limits then. Its hours
    end at until; the hours from until to resume (the rest of the swap day, a part on each device) belong to no one."""

    until: float
    resume: float
    sensors: tuple[str, ...]
    limits: tuple[tuple[str, float], ...] = ()
    caps: tuple[tuple, ...] = ()               # as ArrayConfig.caps
    device: bool = True                  # a new device converts the array's power after it: a new efficiency


@dataclass(frozen=True)
class ArrayConfig:
    subentry_id: str
    name: str
    sensors: tuple[str, ...]
    geometry: ArrayGeometry
    valid_since: float | None
    temp_coeff: float | None = None          # per K, from the datasheet
    limits: tuple[tuple[str, float], ...] = ()   # (sensor, fixed limit in W): periods at a limit are lower bounds
    # (sensors, fixed limit in W): output limits of the systems the array belongs to, with every sensor of the
    # arrays that share them; periods with their sum at the limit are lower bounds (installation.Cap). With a third
    # element, each sensor's share of that sum (a three-phase device under a limit per phase: curtailment.shares)
    caps: tuple[tuple, ...] = ()
    segments: tuple[Segment, ...] = ()       # the array before its system swaps, oldest first

    @property
    def limit_w(self) -> float | None:
        """The array's fixed limit: the sum of its sensors' limits, if every sensor has one, or the output limit of
        a system that holds only this array, whichever is lower (installation.array_limit)."""
        limited = dict(self.limits)
        found = [sum(limited.values())] if self.sensors and all(s in limited for s in self.sensors) else []
        # alone under a limit of which it takes a part, the array reaches it later
        found += [cap[1] / min(ct.shares(cap).values(), default=1.0) for cap in self.caps
                  if set(cap[0]) == set(self.sensors)]
        return min(found) if found else None


@dataclass(frozen=True)
class ConsumerConfig:
    subentry_id: str
    name: str
    sensor: str | None                   # power (W)
    timing: str
    energy: str | None = None            # energy (kWh); history from its hourly change when there is no power sensor
    remaining: str | None = None         # minutes a running program still needs
    elapsed: str | None = None           # minutes it has run
    run_power: float | None = None       # flexible: a run's power and duration, None = learned
    run_minutes: float | None = None
    kind: str = "profile"                # fixed: profile, program or session
    start_share: float | None = None     # flexible: PV share (%) from which "start now" turns on, None = default

    @property
    def series_id(self) -> str:
        return self.sensor or self.energy


@dataclass(frozen=True)
class PvHistory:
    """Fetched history for the PV arrays; statistics per sensor in the sensor's unit (None = no value)."""

    power: dict[str, dict[float, float | None]]          # hourly mean
    power_min: dict[str, dict[float, float | None]]      # hourly minimum and maximum: steady hours
    power_max: dict[str, dict[float, float | None]]
    factors: dict[str, float]                            # sensor unit -> W
    air_temp: Series | None = None                       # temperature archive (°C, middle of the hour)
    satellite_ghi: Series | None = None                  # satellite archive (W/m²)
    # per array the hour starts in which no battery that takes its PV took more and one of them was full
    # (installation.battery_hours)
    full: dict[str, frozenset[float]] = field(default_factory=dict)
    # per array the hour starts in which the batteries that take its PV charged at their maximum or were full
    # (installation.battery_hours)
    charge: dict[str, frozenset[float]] = field(default_factory=dict)
    # per array the hour starts in which its system may have held PV back for a low load or at the household's
    # feed-in limit (installation.held_hours)
    held: dict[str, frozenset[float]] = field(default_factory=dict)
    # per array the hour starts known only by an hourly mean in which the batteries that take its PV may have been
    # at their maximum for a part of the hour: left out of learning like a full battery (installation.battery_hours)
    unsure: dict[str, frozenset[float]] = field(default_factory=dict)
    snowfall: Series | None = None                       # snowfall archive (cm per hour at the site, hour start)
    # per array the hour starts in which an output limit with a battery below it was reached: the PV less the charge
    # came to the limit (installation.output_hours); a lower bound like a fixed limit
    output: dict[str, frozenset[float]] = field(default_factory=dict)
    # left_out per (array, period), computed once: learning, backtest and hold-out of a night ask for it again
    memo: dict = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class LoadHistory:
    """Fetched history for the load models, in W (energy sensors already as hourly change in Wh = mean W)."""

    series: dict[str, Series]               # house load and consumers, PROFILE_DAYS + BACKTEST_DAYS + 1 days
    floor: Series                           # hourly minimum of the house load
    year: dict[str, Series] | None = None   # a year of the same as series, for regulated consumers
    fine: dict[str, Series] | None = None   # 5-minute means for the event suggestions; None = unavailable


@dataclass
class LoadLearned:
    profile: lf.Profile                     # base load
    floor: lf.Profile                       # base load from the hourly minimum, for sustained loads
    consumers: dict[str, lf.Profile]
    models: dict[str, dict]                 # per consumer: program, session and regulated model (or None)
    backtest: dict
    runs: dict[str, dict]                   # learned run of flexible consumers: power_w, minutes
    events: dict | None = None              # recurring device runs in the house load (suggestions)


def highest(series: dict[str, dict[float, float | None]]) -> Series:
    """Highest value per hour over several sensors (state of charge of several batteries)."""
    out: Series = {}
    for values in series.values():
        for ts, value in values.items():
            if value is not None:
                out[ts] = max(value, out.get(ts, value))
    return out


def sum_series(series: list[Series]) -> Series:
    """Sum several sensors per hour; hours where any sensor is missing are dropped."""
    if not series or not all(series):
        return {}
    common = set(series[0])
    for s in series[1:]:
        common &= set(s)
    return {t: sum(s[t] for s in series) for t in common}


def scaled_sum(stats: dict[str, dict[float, float | None]], sensors: tuple[str, ...] | list[str],
               factors: dict[str, float]) -> Series:
    """An array's power in W: its sensors converted and summed per period."""
    return sum_series([{t: v * factors.get(s, 1.0) for t, v in stats.get(s, {}).items() if v is not None}
                       for s in sensors])


def spans(arr: ArrayConfig) -> list[tuple[ArrayConfig, float | None, float | None]]:
    """The array as it was in each segment and as it is since the last swap: (the array with that span's sensors and
    limits, start, end), None = open. Without a swap the array itself, unbounded."""
    if not arr.segments:
        return [(arr, None, None)]
    out: list[tuple[ArrayConfig, float | None, float | None]] = []
    start = None
    for seg in arr.segments:
        out.append((replace(arr, sensors=seg.sensors, limits=seg.limits, caps=seg.caps, segments=()), start, seg.until))
        start = seg.resume
    out.append((replace(arr, segments=()), start, None))
    return out


def _within(t: float, start: float | None, end: float | None) -> bool:
    return (start is None or t >= start) and (end is None or t < end)


def array_power(arr: ArrayConfig, stats: dict[str, dict[float, float | None]], factors: dict[str, float]) -> Series:
    """An array's power in W across its swaps: in each span the sum of that span's sensors."""
    if not arr.segments:
        return scaled_sum(stats, arr.sensors, factors)
    out: Series = {}
    for view, start, end in spans(arr):
        out.update({t: v for t, v in scaled_sum(stats, view.sensors, factors).items() if _within(t, start, end)})
    return out


def array_steady(arr: ArrayConfig, history: PvHistory) -> set[float]:
    """The array's steady hours (model.steady_hours) across its swaps, each span from its own sensors."""
    return {t for view, start, end in spans(arr)
            for t in steady_hours([(history.power.get(s, {}), history.power_min.get(s, {}),
                                    history.power_max.get(s, {})) for s in view.sensors])
            if _within(t, start, end)}


def devices(arr: ArrayConfig) -> list[tuple[float | None, float | None]]:
    """The time ranges in which one device converted the array's power, oldest first, (start, end), None = open: a
    swap that keeps the device (a battery swap) does not split them."""
    out: list[tuple[float | None, float | None]] = []
    start = None
    for seg in arr.segments:
        if seg.device:
            out.append((start, seg.until))
            start = seg.resume
    out.append((start, None))
    return out


def device_factor(arr: ArrayConfig, ratios: list[dict], t: float) -> float:
    """What the newest device's model is multiplied by for period t: 1 / the ratio of the device that ran then
    (LearnedArray.segment_ratios), 1 for the newest device and without a swap."""
    if not ratios:
        return 1.0
    for (start, end), r in zip(devices(arr), ratios):
        if _within(t, start, end) and r["until"] == end and r["ratio"] > 0:
            return 1.0 / r["ratio"]
    return 1.0


def _as_newest(arr: ArrayConfig, samples: list[tuple[float, float]], ratios: list[dict]) -> list[tuple[float, float]]:
    """Samples of older devices scaled to the newest one: what the modules gave, measured with today's device."""
    return [(t, p / device_factor(arr, ratios, t)) for t, p in samples]


# A ratio outside this range is no inverter's step but a unit or a configuration error: it is held at the bound.
RATIO_LIMITS = (0.75, 1.33)
RATIO_ROUNDS = 4                # learning rounds at most until the ratio settles ...
RATIO_SETTLED = 0.002           # ... to within this
RATIO_FREE_W = 0.05             # a period counts for the ratio from this share of the peak power in the model


def learn_ratios(site: Site, arr: ArrayConfig, learned: LearnedArray, samples: list[tuple[float, float]],
                 history: PvHistory, steady: set[float], floors: set[float], period: float = HOUR) -> list[dict]:
    """Per older device the newest device's efficiency over its own (LearnedArray.segment_ratios).

    Measured over model on measured weather, in clear periods with the sun in a cell the map knows as free, per cell
    where both devices have such periods: the median of the newest device's over that of the older one, then the
    median over the cells weighted by the fewer side. Shared cells keep the model's errors at a low sun, which
    differ by season, out of the ratio. Until each side has MIN_UNSHADED_HOURS such periods the ratio is 1."""
    ranges = devices(arr)
    if len(ranges) < 2:
        return []
    sat, air = history.satellite_ghi or {}, history.air_temp or {}
    clear = []
    for t, p in samples:
        if t in floors or (period == HOUR and steady and t not in steady) or (ghi := sat.get(t)) is None:
            continue
        cs = clearsky_hour(site, arr.geometry, t, learned.sky_view, period)[4]
        if cs > 50 and ghi / cs >= CLEAR_KT:
            clear.append((t, p, ghi))
    modeled = measured_forecast(site, arr.geometry, learned, [(t, ghi, air.get(t)) for t, _, ghi in clear], period)
    cells: list[dict[tuple[int, int], list[float]]] = [{} for _ in ranges]
    for t, p, _ in clear:
        model_w = modeled.get(t, 0.0) * HOUR / period
        if model_w < RATIO_FREE_W * arr.geometry.peak_w:
            continue
        sun = sun_position(site, t + period / 2)
        if not known_free(learned, sun.azimuth, sun.elevation):
            continue
        i = next(i for i, (start, end) in enumerate(ranges) if _within(t, start, end))
        cells[i].setdefault((int(sun.azimuth // AZ_STEP), int(sun.elevation // EL_STEP)), []).append(p / model_w)
    newest, need = cells[-1], MIN_UNSHADED_HOURS * HOUR / period
    out = []
    for (_, end), own in zip(ranges[:-1], cells[:-1]):
        shared = set(own) & set(newest)
        old_n, new_n = sum(len(own[c]) for c in shared), sum(len(newest[c]) for c in shared)
        ratio, enough = 1.0, old_n >= need and new_n >= need
        if enough:
            per_cell = [(_median(newest[c]) / m, min(len(own[c]), len(newest[c]))) for c in shared
                        if (m := _median(own[c])) > 0]
            ratio = weighted_quantile([r for r, _ in per_cell], [w for _, w in per_cell], 0.5) if per_cell else 1.0
            if not RATIO_LIMITS[0] <= ratio <= RATIO_LIMITS[1]:
                _LOGGER.warning("%s: the new device measures %.2f times the old one; held at %s. Check the unit and "
                                "the sensors of the swap", arr.name, ratio, RATIO_LIMITS)
            ratio = min(max(ratio, RATIO_LIMITS[0]), RATIO_LIMITS[1])
        out.append({"until": end, "ratio": round(ratio, 4), "learned": enough,
                    "hours": [round(old_n * period / HOUR), round(new_n * period / HOUR)]})
    return out


def _median(values: list[float]) -> float:
    return weighted_quantile(values, [1.0] * len(values), 0.5)


def _only(series: Series, keep: Keep | None) -> Series:
    return series if keep is None else {t: v for t, v in series.items() if keep(t)}


def fine_history(site: Site, history: PvHistory, store: dict) -> PvHistory:
    """The history at the 5-minute archive's periods (fine.py), for learning with period=fine.STEP.

    The archive holds power in W. Temperature keeps the value of its hour. The
    satellite's hour keeps its clearness, so that its mean stays the same and a low sun does not look
    brighter than the clear sky. Hours without a satellite value stay without one.
    """
    sensors = {s for day in store.values() for s in day}
    power = {s: fn.series(store, s) for s in sensors}
    bands = {s: fn.steady_band(series) for s, series in power.items()}
    hours = {t - t % HOUR for series in power.values() for t in series}
    steps = [k * fn.STEP for k in range(int(HOUR // fn.STEP))]
    air: Series = {}
    satellite: Series = {}
    for hour in hours:
        for step in steps:
            if history.air_temp and hour in history.air_temp:
                air[hour + step] = history.air_temp[hour]
        if history.satellite_ghi and hour in history.satellite_ghi:
            clear = [clearsky_weather(site, hour + step, period=fn.STEP).ghi for step in steps]
            mean = sum(clear) / len(clear)
            for step, ghi in zip(steps, clear):
                satellite[hour + step] = history.satellite_ghi[hour] * ghi / mean if mean > 0 else 0.0
    return PvHistory(power, {s: b[0] for s, b in bands.items()}, {s: b[1] for s, b in bands.items()}, {},
                     air or None, satellite or None, history.full, history.charge, history.held, history.unsure,
                     history.snowfall, output=history.output)


# An hour whose maximum touched a limit, but whose mean reached this share of what free modules give under the
# satellite sky (geometry, default efficiency, no learned shade): the limit can have cut little, since what it cut
# is missing from the mean. Typical of cloud edges, whose light peaks for a minute above the limit. Below the share,
# a cut and shade look the same, and the hour stays a lower bound, which is true either way.
LIMIT_FREE_SHARE = 0.9
# ... and whose mean at the limit stayed below this share of it: a mean near the limit is an hour spent at it, cut
# a little every clear noon, which taken as measured would bend the efficiency down
LIMIT_MEAN_SHARE = 0.8


def floor_hours(arr: ArrayConfig, history: PvHistory, site: Site | None = None) -> set[float]:
    """Periods at a fixed limit (_floor_hours), across swaps each span with its own sensors and limits."""
    if not arr.segments:
        return _floor_hours(arr, history, site)
    return {t for view, start, end in spans(arr) for t in _floor_hours(view, history, site) if _within(t, start, end)}


def _floor_hours(arr: ArrayConfig, history: PvHistory, site: Site | None = None) -> set[float]:
    """Periods in which a sensor of the array reached its fixed limit, or the arrays of a system without a battery
    reached its output limit together: the array's power then is only a lower bound (curtailment.at_limit).

    A period goes by its maximum, the mean where there is none. With site, an hour whose mean stayed below
    LIMIT_MEAN_SHARE of the limit and reached LIMIT_FREE_SHARE of free modules under the satellite sky counts as
    measured although its maximum touched the limit: a peak of a minute cuts little. The 5-minute means are no
    better judge: passing clouds cut within each 5 minutes without bringing their mean to the limit. The sum of period maxima is at least
    the maximum of the sum, so a period a little below a shared limit may count as at it: it becomes a lower bound
    where it was exact."""
    def peaks(sensors: tuple[str, ...], shares: dict[str, float]) -> dict[float, tuple[float, float]]:
        """Period start -> (maximum, mean) of the sensors' sum, each with its share, in W."""
        series = []
        for sensor in sensors:
            factor = history.factors.get(sensor, 1.0) * shares.get(sensor, 1.0)
            high = history.power_max.get(sensor, {})
            series.append({t: (peak * factor, mean * factor) for t, mean in history.power.get(sensor, {}).items()
                           if mean is not None and (peak := high.get(t) if high.get(t) is not None else mean) is not None})
        common = set(series[0]).intersection(*series[1:]) if series else set()
        return {t: (sum(p[t][0] for p in series), sum(p[t][1] for p in series)) for t in common}

    free_ok: dict[float, bool] = {}
    summed: Series = {}

    def near_free(t: float) -> bool:
        if site is None or t % HOUR:
            return False
        if t not in free_ok:
            if not summed:
                summed.update(scaled_sum(history.power, arr.sensors, history.factors))
            free_ok[t] = _near_free(site, arr, history, t, summed.get(t))
        return free_ok[t]

    out: set[float] = set()
    for cap in [((s,), w) for s, w in arr.limits] + list(arr.caps):
        limit = cap[1]
        for t, (peak, mean) in peaks(cap[0], ct.shares(cap)).items():
            if ct.at_limit(peak, limit) and (mean >= LIMIT_MEAN_SHARE * limit or not near_free(t)):
                out.add(t)
    return out


def _near_free(site: Site, arr: ArrayConfig, history: PvHistory, t: float, measured: float | None) -> bool:
    """Whether the array's mean in hour t (measured, W) reached LIMIT_FREE_SHARE of free modules under the
    satellite sky."""
    ghi = (history.satellite_ghi or {}).get(t)
    if ghi is None:
        return False
    free = forecast(site, arr.geometry, LearnedArray(), [WeatherHour(t, max(0.0, ghi), temp=(history.air_temp or {}).get(t))])
    return measured is not None and free.get(t, 0.0) > 0 and measured >= LIMIT_FREE_SHARE * free[t]


@dataclass(frozen=True)
class LeftOut:
    """What learning leaves out of an array because it says nothing about shade or efficiency."""

    dead: list[int]              # solar days with almost no output (model.dead_days)
    snow: set[float]             # hour starts that snow may have covered (model.snow_windows)
    hours: frozenset[float]      # the hour starts of both
    snow_capped: tuple[float, ...] = ()  # ends of the snow windows that closed only at model.SNOW_MAX_DAYS
    snow_cleared: tuple[dict[float, float], ...] = ()  # how snow lay where the array cleared (model.snow_scan)


def left_out(site: Site, arr: ArrayConfig, history: PvHistory, period: float = HOUR) -> LeftOut:
    """Days without output and hours under snow of one array, from its whole history."""
    key = (arr.subentry_id, arr.sensors, arr.segments, arr.geometry, arr.valid_since, period)
    if key not in history.memo:
        history.memo[key] = _left_out(site, arr, history, period)
    return history.memo[key]


def _left_out(site: Site, arr: ArrayConfig, history: PvHistory, period: float) -> LeftOut:
    summed = array_power(arr, history.power, history.factors)
    dead = model_dead_days(site, arr.geometry, summed, valid_since=arr.valid_since, period=period)
    snow, capped, cleared = snow_scan(site, arr.geometry, summed, history.snowfall, history.air_temp,
                                      history.satellite_ghi, arr.valid_since)
    wanted = set(dead)
    hours = {t - t % HOUR for t in summed if solar_day(site, t) in wanted} if dead else set()
    return LeftOut(dead, snow, frozenset(hours | snow), tuple(capped), tuple(cleared))


def solar_dates(days: Collection[int]) -> list[str]:
    """Solar days as ISO dates, oldest first: the days the panel names."""
    return [solar_date(d) for d in sorted(days)]


def array_samples(arr: ArrayConfig, history: PvHistory, exclusions: list[tuple[float, float]],
                  keep: Keep | None = None, skip: frozenset[float] = frozenset(), site: Site | None = None
                  ) -> tuple[list[tuple[float, float]], set[float], set[float]]:
    """An array's usable (period start, mean W) samples, its steady hours and its hours at a fixed limit: what
    learning sees. skip: hour starts left out besides (left_out). site: for hourly periods, to keep the hours that
    only a short peak brought to a limit (floor_hours)."""
    power = history.power
    summed = array_power(arr, power, history.factors)
    samples = usable_samples(summed, exclusions=exclusions,
                             valid_since=arr.valid_since,
                             full=history.full.get(arr.subentry_id, frozenset()) | history.unsure.get(arr.subentry_id, frozenset())
                             | skip)
    if keep is not None:
        samples = [s for s in samples if keep(s[0])]
    steady = array_steady(arr, history)
    # an hour charging at the maximum, at an output limit with a battery below it, or one in which a system
    # following the load held PV back: every period in it delivered at least what was measured
    charge = (history.charge.get(arr.subentry_id, frozenset()) | history.held.get(arr.subentry_id, frozenset())
              | history.output.get(arr.subentry_id, frozenset()))
    floors = floor_hours(arr, history, site) | {t for t, _ in samples if t - t % HOUR in charge}
    return samples, steady, floors


def hour_reasons(site: Site, arr: ArrayConfig, history: PvHistory,
                 exclusions: list[tuple[float, float]]) -> dict[float, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Per period with a value: (why learning leaves it out, why it counts only as a lower bound).

    The same sets as array_samples and learn_array, by name: an hour with no reason to leave it out is a sample of
    learning (test_databasis checks that both agree).
    """
    sid = arr.subentry_id
    out = left_out(site, arr, history)
    dead = set(out.dead)
    left = {"battery_full": history.full.get(sid, frozenset()), "maybe_charging": history.unsure.get(sid, frozenset()),
            "no_output": {t for t in out.hours if solar_day(site, t) in dead}, "snow": out.snow}
    reasons = sample_reasons(array_power(arr, history.power, history.factors),
                             exclusions=exclusions, valid_since=arr.valid_since,
                             left={k: v for k, v in left.items() if v})
    output = history.output.get(sid, frozenset())
    limit = floor_hours(arr, history, site) | {t for t in reasons if t - t % HOUR in output}
    bounds = {"at_limit": None, "charging_max": history.charge.get(sid, frozenset()),
              "held_back": history.held.get(sid, frozenset())}
    result = {}
    for t, why in reasons.items():
        hour = t - t % HOUR
        low = tuple(name for name, hours in bounds.items() if (t in limit if hours is None else hour in hours))
        result[t] = (why, () if why else low)
    return result


def learn_array(site: Site, arr: ArrayConfig, history: PvHistory, now: float,
                exclusions: list[tuple[float, float]], keep: Keep | None = None, period: float = HOUR) -> LearnedArray:
    """Learn one array from its usable hours, with the panel explanations (arraycheck.annotate).

    period: the length of history's periods in seconds; the panel explanations exist for hours only.
    """
    missing = [s for s in arr.sensors if not history.power.get(s)]
    if missing:
        _LOGGER.warning("%s: no long-term statistics for %s (state_class measurement required)",
                        arr.name, ", ".join(missing))
    out = left_out(site, arr, history, period)
    samples, steady, floors = array_samples(arr, history, exclusions, keep, out.hours,
                                            site if period == HOUR else None)
    args = dict(air_temp=history.air_temp, satellite_ghi=history.satellite_ghi, datasheet_temp_coeff=arr.temp_coeff,
                steady=steady, period=period, floors=floors, limit_w=arr.limit_w)
    result = learn(site, arr.geometry, samples, now, **args)
    ratios = learn_ratios(site, arr, result, samples, history, steady, floors, period)
    measured = samples
    for _ in range(RATIO_ROUNDS):
        if all(r["ratio"] == 1.0 for r in ratios):
            break
        # again on one device's scale: else a better inverter would read as less shade in every old cell. The map
        # the ratio was measured on still held the step at the edges of its cells, so it is measured again on the
        # new map, until it settles
        samples = _as_newest(arr, measured, ratios)
        result = learn(site, arr.geometry, samples, now, **args)
        step = learn_ratios(site, arr, result, samples, history, steady, floors, period)
        ratios = [{**r, "ratio": round(min(max(r["ratio"] * s["ratio"], RATIO_LIMITS[0]), RATIO_LIMITS[1]), 4)}
                  for r, s in zip(ratios, step)]
        if all(abs(s["ratio"] - 1.0) < RATIO_SETTLED for s in step):
            break
    result.segment_ratios = ratios
    if result.samples and not EFFICIENCY_PLAUSIBLE[0] <= result.efficiency <= EFFICIENCY_PLAUSIBLE[1]:
        _LOGGER.warning("%s: implausible system efficiency %.2f – check peak power and power sensor units",
                        arr.name, result.efficiency)
    result.dead_days = solar_dates(out.dead)
    result.snow_days = solar_dates({solar_day(site, t) for t in out.snow})
    result.snow = learn_snow(site, arr.geometry, result, list(out.snow_cleared), history.satellite_ghi,
                             history.air_temp)
    if out.dead or out.snow:
        _LOGGER.info("%s: left out of learning: %d days with almost no output (outage, snow or too dark), %d hours "
                     "that snow may have covered", arr.name, len(out.dead), len(out.snow))
    if result.efficiency_source == "typical":
        _LOGGER.warning("%s: the hours below the power limit do not show the efficiency, the typical %.0f %% is "
                        "used – if this stays, check peak power and power sensor units", arr.name,
                        result.efficiency * 100)
    if result.samples and period == HOUR:
        ac.annotate(site, arr.geometry, result, samples, history.air_temp, history.satellite_ghi,
                    datetime.fromtimestamp(now, timezone.utc).year, steady, floors)
    _LOGGER.info("Learned %s: efficiency %.3f, temperature coefficient %.3f %%/K, %d shading cells, "
                 "coverage %.0f %%, %d samples, clear filter %s, steady filter %s, agreement %s",
                 arr.name, result.efficiency, result.temp_coeff * 100, len(result.cells),
                 result.coverage * 100, result.samples, result.clear_filter, result.steady_filter,
                 result.agreement)
    return result


@sun_path_cache
def learn_arrays(site: Site, arrays: dict[str, ArrayConfig], history: PvHistory, now: float,
                 exclusions: list[tuple[float, float]], keep: Keep | None = None,
                 period: float = HOUR) -> dict[str, LearnedArray]:
    """Learn every array (subentry id -> LearnedArray); now: UNIX seconds, the reference for ageing."""
    return {sid: learn_array(site, arr, history, now, exclusions, keep, period) for sid, arr in arrays.items()}


def record_archive(archive: dict, site: Site, arrays: dict[str, ArrayConfig], learned: dict[str, LearnedArray],
                   history: PvHistory, now: float, inst: Installation | None = None) -> None:
    """Add the measured hours to the day-ahead archive, and the model on measured weather (satellite).

    inst: the installation; hours in which an array was at a fixed limit are flagged, and the model on measured
    weather is capped like the forecast, so the split into weather and model error stays fair.
    """
    peaks = {s: {t: v * history.factors.get(s, 1.0) for t, v in values.items() if v is not None}
             for s, values in history.power_max.items()} if inst and (inst.limited_arrays() or inst.caps) else {}
    for sid, arr in arrays.items():
        limited = ct.limited_hours(inst, sid, peaks) if peaks else set()
        # an hour at a battery's charge ceiling shows nothing about the sun either, and its forecast is not capped
        fc_archive.add_actuals(archive, sid, array_power(arr, history.power, history.factors), now,
                               history.full.get(sid, frozenset()) | history.charge.get(sid, frozenset())
                               | history.held.get(sid, frozenset()) | history.unsure.get(sid, frozenset())
                               | history.output.get(sid, frozenset()),
                               limited)
        if history.satellite_ghi:
            fc_archive.add_measured(archive, sid, history.satellite_ghi, history.air_temp,
                                    _capped_measured(site, arrays, learned, inst, sid), now)


def _capped_measured(site: Site, arrays: dict[str, ArrayConfig], learned: dict[str, LearnedArray],
                     inst: Installation | None, sid: str) -> Callable[[list[tuple[float, float, float | None]]], Series]:
    """The model on measured weather for one array, capped like the forecast (forecasting._cap): by its own limit
    and by a limit it shares with other arrays, whose model on the same weather counts in the sum."""
    own = {a: w for a in arrays if inst and (w := inst.array_limit(a)) is not None}
    caps = [c for cap in (inst.forecast_caps() if inst else [])
            if sid in cap[0] and (c := ct.within(cap, lambda a: a in arrays and a in learned))]
    group = {sid} | {a for cap in caps for a in cap[0]}

    def predict(hours: list[tuple[float, float, float | None]]) -> Series:
        # an hour of a device before a swap: each array's model at the efficiency of the device that ran then
        raw = {a: {t: v * device_factor(arrays[a], learned[a].segment_ratios, t)
                   for t, v in measured_forecast(site, arrays[a].geometry, learned[a], hours).items()} for a in group}
        if sid not in own and not caps:
            return raw[sid]
        temps = {t: temp for t, _, temp in hours}

        def profile(a: str, t: float) -> list[float]:
            return clearsky_profile(site, arrays[a].geometry, learned[a], t, temps.get(t), SUBSTEPS)
        return {t: ct.cap_group({a: raw[a].get(t, 0.0) for a in group}, lambda a, t=t: profile(a, t),
                                {a: w for a, w in own.items() if a in group}, caps)[sid]
                for t in raw[sid]}
    return predict


def learn_runs(consumers: dict[str, ConsumerConfig], consumer_series: dict[str, Series]) -> dict[str, dict]:
    """The typical run of each flexible consumer from its own power ({sid: {"power_w", "minutes"}})."""
    runs = {}
    for sid, c in consumers.items():
        if c.timing == lf.TIMING_FLEXIBLE and (run := pl.learn_run(c.name, consumer_series.get(sid, {}))):
            runs[sid] = {"power_w": run.power_w, "minutes": run.minutes}
    return runs


def learn_load(house_load: str, consumers: dict[str, ConsumerConfig], history: LoadHistory, zone: tzinfo,
               tz: tzinfo, until: float, now: float, air_temp: Series | None = None,
               keep: Keep | None = None) -> LoadLearned | None:
    """Base-load, floor and consumer profiles and models from the recent weeks, plus a replay of the last days.

    zone: the local time zone with the known day types (load.DayTypeZone) or the plain one; tz: the plain
    local time zone (event suggestions); until: local midnight of today. None without house-load history.
    """
    series = {sid: _only(s, keep) for sid, s in history.series.items()}
    load = series.get(house_load, {})
    if not load:
        return None
    consumer_series = {sid: series.get(c.series_id, {}) for sid, c in consumers.items()}
    base = lf.base_series(load, list(consumer_series.values()))
    profile = lf.learn_profile(base, zone, until)
    floor = lf.base_series(_only(history.floor, keep), [series.get(c.series_id, {}) for c in consumers.values()])
    floor_profile = lf.learn_profile(floor, zone, until)
    profiles = {sid: lf.learn_profile(s, zone, until) for sid, s in consumer_series.items()}
    models = {sid: {"program": lf.learn_program(s, zone, until), "session": lf.learn_session(s, zone, until)}
              for sid, s in consumer_series.items()}
    # regulated consumers: a year of history, so the relation to the temperature spans the seasons
    regulated = [sid for sid, c in consumers.items() if c.kind == lf.KIND_REGULATED and c.timing == lf.TIMING_FIXED]
    if regulated and air_temp and history.year is not None:
        for sid in regulated:
            models[sid]["regulated"] = lf.learn_regulated(_only(history.year.get(consumers[sid].series_id, {}), keep),
                                                          air_temp, zone, until)
    backtest = lf.backtest(load, consumer_series, {sid: c.timing for sid, c in consumers.items()}, zone, now,
                           lf.BACKTEST_DAYS, {sid: c.kind for sid, c in consumers.items()}, air_temp)
    runs = learn_runs(consumers, consumer_series)
    _LOGGER.info("Learned load profile from %d days; backtest %s", profile.days, backtest)
    return LoadLearned(profile, floor_profile, profiles, models, backtest, runs,
                       learn_events(house_load, consumers, history.fine, tz, keep) if history.fine is not None else None)


def learn_events(house_load: str, consumers: dict[str, ConsumerConfig], fine: dict[str, Series], tz: tzinfo,
                 keep: Keep | None = None) -> dict | None:
    """Recurring device runs in the 5-minute house load (the recorder keeps about ten days)."""
    load = _only(fine.get(house_load, {}), keep)
    # energy sensors give Wh per 5 minutes, not mean W
    others = [{t: v * (3600 / ev.SLOT if not c.sensor else 1.0) for t, v in _only(fine.get(c.series_id, {}), keep).items()}
              for c in consumers.values()]
    return ev.suggestions(load, others, tz) if load else None
