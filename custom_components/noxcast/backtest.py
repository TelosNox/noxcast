"""Backtest of the PV model: would today's knowledge have done better, and does it hold on unlearned days?

Pure functions, no Home Assistant imports, nothing is written: the inputs stay as they are. Two questions,
each with a simple baseline (the same model without the learned shading map), as the load backtest has:

- Replay: the current learned model on the weather archived with each past day-ahead forecast, the
  same way the day view does (explain.replay), against what was forecast then and what happened. The
  weather error stays in; the difference between "then" and "now" is what the model has learned since.
- Hold-out: learn on even days and test on odd days, and vice versa (learning.learn_arrays with keep),
  on the measured satellite irradiance, so that the weather forecast error does not hide the model
  error. Every day is then evaluated twice by a model from the same amount of data: once unlearned
  (hold-out) and once learned (in-sample). The gap between the two is what the model memorises.

Hours are those learning uses (usable_samples: validity, full battery, exclusions); the archive's
curtailed flag applies on top for the replay. Only hours with light count. Results are small and
bounded: a few numbers per array and in total, no series.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, tzinfo

from . import archive as fc_archive
from . import explain
from . import learning as ln
from .model import LearnedArray, measured_forecast, usable_samples
from .solar import Site
from .const import Series

VERSION = 1
OK, NOT_ENOUGH = "ok", "not_enough_data"
NO_SATELLITE, FEW_DAYS = "no_satellite", "few_days"
MIN_REPLAY_DAYS = 7              # archived days an array needs for a replay result
MIN_FOLD_DAYS = 14               # test days each half of the hold-out needs ...
MIN_FOLD_HOURS = 60              # ... and hours with light
MIN_WH = 1.0                     # an hour counts when model or actual value exceed this (else: night)

Pairs = dict[float, tuple[float, float]]         # hour start -> (model Wh, actual Wh)


def score(pairs: Pairs, day_of: Callable[[float], date]) -> dict:
    """Errors relative to the actual energy, as archive.daily_error: |error| over days and hours, and the bias."""
    days: dict[date, list[float]] = {}
    for t, (f, a) in pairs.items():
        day = days.setdefault(day_of(t), [0.0, 0.0])
        day[0] += f
        day[1] += a
    actual = sum(a for _, a in pairs.values())
    absolute = sum(abs(f - a) for f, a in pairs.values())

    def share(v: float) -> float | None:
        return round(v / actual, 4) if actual > 0 else None
    return {"day_error": share(sum(abs(f - a) for f, a in days.values())), "hour_error": share(absolute),
            "bias": share(sum(f - a for f, a in pairs.values())),
            "hour_mae_wh": round(absolute / len(pairs), 1) if pairs else None,
            "days": len(days), "hours": len(pairs)}


def without_map(learned: LearnedArray) -> LearnedArray:
    """The same model without the learned shading map (cells and facade), for the baseline."""
    return LearnedArray(**{**learned.__dict__, "cells": {}, "facade": None})


def _not_enough(reason: str, days: int = 0, needed: int = 0) -> dict:
    return {"status": NOT_ENOUGH, "reason": reason, "days": days, "needed": needed}


def _usable(arr: ln.ArrayConfig, history: ln.PvHistory, soc_full: float, exclusions: list[tuple[float, float]]) -> Series:
    """The array's hours learning would use, with the mean power (W = Wh per hour)."""
    summed = ln.scaled_sum(history.power, arr.sensors, history.factors)
    return dict(usable_samples(summed, soc=history.soc, soc_full=soc_full, exclusions=exclusions,
                               valid_since=arr.valid_since))


def _lit(*values: float) -> bool:
    return max(values) > MIN_WH


def _summed(parts: list[Pairs]) -> Pairs:
    """All arrays per hour; an hour holds the arrays that have a value in it."""
    out: dict[float, list[float]] = {}
    for pairs in parts:
        for t, (f, a) in pairs.items():
            row = out.setdefault(t, [0.0, 0.0])
            row[0] += f
            row[1] += a
    return {t: (f, a) for t, (f, a) in out.items()}


def replay(site: Site, arrays: dict[str, ln.ArrayConfig], learned: dict[str, LearnedArray], history: ln.PvHistory,
           archive: dict, soc_full: float, exclusions: list[tuple[float, float]], day_of: Callable[[float], date]) -> dict:
    """{"arrays": {id: result}, "total": result}: result = {"status", "then", "now", "baseline"} or not enough data.

    Complete archived days only, as in archive.day_totals. All three on the same hours.
    """
    known = {sid: arr for sid, arr in arrays.items() if sid in learned}
    geometries = {sid: arr.geometry for sid, arr in known.items()}
    usable = {sid: _usable(arr, history, soc_full, exclusions) for sid, arr in known.items()}
    bare = {sid: without_map(learned[sid]) for sid in known}
    runs: dict[str, dict[str, Pairs]] = {sid: {"then": {}, "now": {}, "baseline": {}} for sid in known}
    for day in fc_archive.days(archive):
        if not day.complete:
            continue
        weather = explain.archived_weather(day)
        now, base = explain.replay(site, geometries, learned, weather), explain.replay(site, geometries, bare, weather)
        for h in day.hours:
            if h.curtailed:
                continue
            for sid, run in runs.items():
                if h.ts not in usable[sid] or sid not in h.forecast or sid not in h.actual:
                    continue
                then, actual = h.forecast[sid], h.actual[sid]
                fc = now[sid].get(h.ts, {}).get("result", 0.0)
                if _lit(then, fc, actual):
                    run["then"][h.ts] = (then, actual)
                    run["now"][h.ts] = (fc, actual)
                    run["baseline"][h.ts] = (base[sid].get(h.ts, {}).get("result", 0.0), actual)

    def result(parts: dict[str, Pairs]) -> dict:
        n = len({day_of(t) for t in parts["now"]})
        if n < MIN_REPLAY_DAYS:
            return _not_enough(FEW_DAYS, n, MIN_REPLAY_DAYS)
        return {"status": OK, **{name: score(pairs, day_of) for name, pairs in parts.items()}}

    out = {sid: result(run) for sid, run in runs.items()}
    good = [sid for sid, r in out.items() if r["status"] == OK]
    total = (result({name: _summed([runs[sid][name] for sid in good]) for name in ("then", "now", "baseline")})
             if good else _not_enough(FEW_DAYS, max((r["days"] for r in out.values()), default=0), MIN_REPLAY_DAYS))
    return {"arrays": out, "total": total}


def _even(t: float, day_of: Callable[[float], date]) -> bool:
    return day_of(t).toordinal() % 2 == 0


def holdout(site: Site, arrays: dict[str, ln.ArrayConfig], history: ln.PvHistory, now: float, soc_full: float,
            exclusions: list[tuple[float, float]], day_of: Callable[[float], date]) -> dict:
    """{"arrays": {id: result}, "total": result}: result = {"status", "holdout", "in_sample", "baseline"} or not enough data.

    holdout: every day by the model learned on the other days; in_sample: by the model learned on its
    own half; baseline: the hold-out model without the shading map. All three on the same hours: those
    learning uses, with measured irradiance. Without satellite irradiance there is no such test.
    """
    sat = history.satellite_ghi
    if not sat:
        reason = _not_enough(NO_SATELLITE)
        return {"arrays": {sid: reason for sid in arrays}, "total": reason}
    air = history.air_temp or {}
    tests: dict[str, Series] = {}
    out: dict[str, dict] = {}
    for sid, arr in arrays.items():
        tests[sid] = {t: p for t, p in _usable(arr, history, soc_full, exclusions).items()
                      if t in sat and _lit(p, sat[t])}
        folds = [[t for t in tests[sid] if _even(t, day_of) == even] for even in (True, False)]
        days = [len({day_of(t) for t in fold}) for fold in folds]
        if min(days) < MIN_FOLD_DAYS or min(map(len, folds)) < MIN_FOLD_HOURS:
            out[sid] = _not_enough(FEW_DAYS, sum(days), 2 * MIN_FOLD_DAYS)
    good = {sid: arr for sid, arr in arrays.items() if sid not in out}
    if not good:
        total = _not_enough(FEW_DAYS, max((r["days"] for r in out.values()), default=0), 2 * MIN_FOLD_DAYS)
        return {"arrays": out, "total": total}

    runs: dict[str, dict[str, Pairs]] = {sid: {"holdout": {}, "in_sample": {}, "baseline": {}} for sid in good}
    for even in (True, False):
        models = ln.learn_arrays(site, good, history, now, soc_full, exclusions,
                                 keep=lambda t, even=even: _even(t, day_of) == even)
        for sid, arr in good.items():
            hours = [(t, max(0.0, sat[t]), air.get(t)) for t in tests[sid]]
            fc = measured_forecast(site, arr.geometry, models[sid], hours)
            base = measured_forecast(site, arr.geometry, without_map(models[sid]), hours)
            for t, p in tests[sid].items():
                if _even(t, day_of) == even:
                    runs[sid]["in_sample"][t] = (fc[t], p)
                else:
                    runs[sid]["holdout"][t] = (fc[t], p)
                    runs[sid]["baseline"][t] = (base[t], p)

    def result(parts: dict[str, Pairs]) -> dict:
        return {"status": OK, **{name: score(pairs, day_of) for name, pairs in parts.items()}}

    out.update({sid: result(run) for sid, run in runs.items()})
    return {"arrays": out,
            "total": result({name: _summed([run[name] for run in runs.values()])
                             for name in ("holdout", "in_sample", "baseline")})}


def run(site: Site, arrays: dict[str, ln.ArrayConfig], learned: dict[str, LearnedArray], history: ln.PvHistory,
        archive: dict, now: float, soc_full: float, exclusions: list[tuple[float, float]], tz: tzinfo) -> dict:
    """The whole backtest; tz: the local time zone, days are local days. Reads its inputs, changes nothing."""
    cache: dict[int, date] = {}

    def day_of(t: float) -> date:
        hour = int(t // 3600)                     # time zones with half hours: hour starts stay in their day
        if hour not in cache:
            cache[hour] = datetime.fromtimestamp(t, tz).date()
        return cache[hour]

    return {"version": VERSION, "computed_at": now,
            "replay": replay(site, arrays, learned, history, archive, soc_full, exclusions, day_of),
            "holdout": holdout(site, arrays, history, now, soc_full, exclusions, day_of)}


def valid(result: dict | None) -> dict | None:
    """A stored result if it has the current form; an older one is dropped until the next run."""
    return result if isinstance(result, dict) and result.get("version") == VERSION else None
