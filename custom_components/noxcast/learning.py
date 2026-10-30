"""Learning pipeline: PV arrays, the archive's actual and measured values, house load and consumers.

Pure functions, no Home Assistant imports. The coordinator fetches statistics and archives (async),
runs these in the executor on the fetched data and keeps the results. keep(hour_start) optionally
restricts learning to some hours, meant as whole days: a backtest learns on even days and tests on
odd ones. Without it everything available is used, as in production.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo

from . import archive as fc_archive
from . import arraycheck as ac
from . import events as ev
from . import load as lf
from . import plan as pl
from .model import HOUR, ArrayGeometry, LearnedArray, learn, measured_forecast, steady_hours, usable_samples
from .solar import Site
from .const import Series

_LOGGER = logging.getLogger(__name__)

Keep = Callable[[float], bool]
Series = Series


@dataclass(frozen=True)
class ArrayConfig:
    subentry_id: str
    name: str
    sensors: tuple[str, ...]
    geometry: ArrayGeometry
    valid_since: float | None
    temp_coeff: float | None = None          # per K, from the datasheet


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
    soc: Series                                          # highest state of charge of all batteries per hour (%)
    air_temp: Series | None = None                       # temperature archive (°C, middle of the hour)
    satellite_ghi: Series | None = None                  # satellite archive (W/m²)


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


def _only(series: Series, keep: Keep | None) -> Series:
    return series if keep is None else {t: v for t, v in series.items() if keep(t)}


def learn_array(site: Site, arr: ArrayConfig, history: PvHistory, now: float, soc_full: float,
                exclusions: list[tuple[float, float]], keep: Keep | None = None, period: float = HOUR) -> LearnedArray:
    """Learn one array from its usable hours, with the panel explanations (arraycheck.annotate).

    period: the length of history's periods in seconds; the panel explanations exist for hours only.
    """
    power = history.power
    missing = [s for s in arr.sensors if not power.get(s)]
    if missing:
        _LOGGER.warning("%s: no long-term statistics for %s (state_class measurement required)",
                        arr.name, ", ".join(missing))
    summed = scaled_sum(power, arr.sensors, history.factors)
    samples = usable_samples(summed, soc=history.soc, soc_full=soc_full, exclusions=exclusions,
                             valid_since=arr.valid_since)
    if keep is not None:
        samples = [s for s in samples if keep(s[0])]
    steady = steady_hours([(power.get(s, {}), history.power_min.get(s, {}), history.power_max.get(s, {}))
                           for s in arr.sensors])
    result = learn(site, arr.geometry, samples, now, air_temp=history.air_temp, satellite_ghi=history.satellite_ghi,
                   datasheet_temp_coeff=arr.temp_coeff, steady=steady, period=period)
    if result.samples and not 0.5 <= result.efficiency <= 1.2:
        _LOGGER.warning("%s: implausible system efficiency %.2f – check peak power and power sensor units",
                        arr.name, result.efficiency)
    if result.samples and period == HOUR:
        ac.annotate(site, arr.geometry, result, samples, history.air_temp, history.satellite_ghi,
                    datetime.fromtimestamp(now, timezone.utc).year, steady)
    _LOGGER.info("Learned %s: efficiency %.3f, temperature coefficient %.3f %%/K, %d shading cells, "
                 "coverage %.0f %%, %d samples, clear filter %s, steady filter %s, agreement %s",
                 arr.name, result.efficiency, result.temp_coeff * 100, len(result.cells),
                 result.coverage * 100, result.samples, result.clear_filter, result.steady_filter,
                 result.agreement)
    return result


def learn_arrays(site: Site, arrays: dict[str, ArrayConfig], history: PvHistory, now: float, soc_full: float,
                 exclusions: list[tuple[float, float]], keep: Keep | None = None,
                 period: float = HOUR) -> dict[str, LearnedArray]:
    """Learn every array (subentry id -> LearnedArray); now: UNIX seconds, the reference for ageing."""
    return {sid: learn_array(site, arr, history, now, soc_full, exclusions, keep, period) for sid, arr in arrays.items()}


def record_archive(archive: dict, site: Site, arrays: dict[str, ArrayConfig], learned: dict[str, LearnedArray],
                   history: PvHistory, now: float, soc_full: float) -> None:
    """Add the measured hours to the day-ahead archive, and the model on measured weather (satellite)."""
    full = {t for t, v in history.soc.items() if v >= soc_full}
    for sid, arr in arrays.items():
        fc_archive.add_actuals(archive, sid, scaled_sum(history.power, arr.sensors, history.factors), now, full)
        if history.satellite_ghi:
            def predict(hours, geo=arr.geometry, result=learned[sid]):
                return measured_forecast(site, geo, result, hours)
            fc_archive.add_measured(archive, sid, history.satellite_ghi, history.air_temp, predict, now)


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
    runs = {}
    for sid, c in consumers.items():
        if c.timing == lf.TIMING_FLEXIBLE and (run := pl.learn_run(c.name, consumer_series.get(sid, {}))):
            runs[sid] = {"power_w": run.power_w, "minutes": run.minutes}
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
