"""Which weather models suit this installation? A day-ahead hindcast of every model and pair of models.

Pure functions, no Home Assistant imports, nothing is written. The coordinator fetches what each model
forecast for every past hour one day ahead (Open-Meteo's previous-runs archive, "previous_day1") and the
measured output; this runs today's learned model on each model's weather and on the mean of every pair,
the same way the live forecast averages several models (model.mean_weather), and scores the results
against what the arrays produced.

Fair by construction: every combination is scored on the same hours, those learning uses (validity,
full battery, exclusions) in which every compared model has a forecast. A model that lacks too many of
them (regional models outside their area, models with a short archive) is listed as skipped with the
reason instead of shrinking the comparison for all.

With dozens of combinations one is always best, if only by chance. A change is recommended only when the
combination picked as best on one half of the days (even or odd) beats the current choice on the other half in
at least RECOMMEND_SHARE of the bootstrap resamples of those days, in both directions; otherwise the current
choice stays. Days are classed by what the arrays delivered against their clear-sky output (measured, the same
for every combination), not by a forecast.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date, tzinfo
from itertools import combinations

from . import backtest as bt
from . import learning as ln
from .const import Series
from .model import LearnedArray, WeatherHour, clearsky_weather, forecast, mean_weather, sun_paths
from .solar import Site

VERSION = 1
OK, NOT_ENOUGH, NOT_LEARNED = "ok", "not_enough_data", "not_learned"
CHANGE, KEEP, CURRENT_BEST = "change", "keep", "current_best"
NO_DATA, FEW_HOURS = "no_data", "coverage"
MIN_DAYS = 60                    # days with usable hours needed for a result
MIN_COVERAGE = 0.9               # share of the usable hours a model must have a forecast for
RECOMMEND_SHARE = 0.95           # bootstrap resamples in which the best must beat the current choice
RESAMPLES = 2000
CLASSES = (("clear", 0.7, float("inf")), ("mixed", 0.35, 0.7), ("overcast", float("-inf"), 0.35))

Days = dict[date, tuple[float, float]]       # day -> (forecast Wh, actual Wh), all arrays


def _days(pairs: bt.Pairs, day_of: Callable[[float], date]) -> Days:
    out: dict[date, list[float]] = {}
    for t, (f, a) in pairs.items():
        row = out.setdefault(day_of(t), [0.0, 0.0])
        row[0] += f
        row[1] += a
    return {d: (f, a) for d, (f, a) in out.items()}


def _error(days: Days, keep: Callable[[date], bool] = lambda d: True) -> tuple[float | None, float | None, int]:
    """(daily error, bias, days) over the days kept, relative to their actual energy as backtest.score."""
    sel = [v for d, v in days.items() if keep(d)]
    actual = sum(a for _, a in sel)
    if actual <= 0:
        return None, None, len(sel)
    return (round(sum(abs(f - a) for f, a in sel) / actual, 4), round(sum(f - a for f, a in sel) / actual, 4), len(sel))


def _part(days: Days, keep: Callable[[date], bool]) -> dict:
    error, bias, n = _error(days, keep)
    return {"day_error": error, "bias": bias, "days": n}


def _beats(best: Days, current: Days, rng: random.Random) -> float:
    """Share of bootstrap resamples of the days in which best has the smaller daily error."""
    keys = sorted(best)
    diff = [abs(best[d][0] - best[d][1]) - abs(current[d][0] - current[d][1]) for d in keys]
    wins = 0
    for _ in range(RESAMPLES):
        if sum(diff[rng.randrange(len(diff))] for _ in diff) < 0:
            wins += 1
    return round(wins / RESAMPLES, 3)


def run(site: Site, arrays: dict[str, ln.ArrayConfig], learned: dict[str, LearnedArray], history: ln.PvHistory,
        weather: dict[str, list[WeatherHour]], current: list[str], soc_full: float,
        exclusions: list[tuple[float, float]], tz: tzinfo, now: float) -> dict:
    """The comparison; weather: per model its day-ahead forecast by hour. Reads its inputs, changes nothing.

    {"version", "computed_at", "status", "current", "first", "last", "days", "hours", "combos": [...], "skipped":
    [...], "recommendation"}; combos sorted by daily error, each {"models", "day_error", "hour_error", "bias",
    "hour_mae_wh", "days", "hours"}; the best and the current choice also "classes" and "months". The current
    choice is named in the order of the compared models, so a pair matches whatever order it was configured in.
    """
    day_of = bt.local_day(tz)
    head = {"version": VERSION, "computed_at": now, "current": list(current)}
    known = {sid: arr for sid, arr in arrays.items() if sid in learned}
    if not known:
        return {**head, "status": NOT_LEARNED, "combos": [], "skipped": [], "recommendation": None}

    # usable hours with light within the fetched period: learning's hours where the clear sky or the
    # actual output exceeds a trace
    starts = [w.start for hours in weather.values() for w in hours]
    lo, hi = (min(starts), max(starts)) if starts else (0.0, -1.0)
    actual: dict[str, Series] = {}
    clear: dict[str, Series] = {}
    for sid, arr in known.items():
        hours = {t: p for t, p in bt.usable(arr, history, soc_full, exclusions).items() if lo <= t <= hi}
        sky = forecast(site, arr.geometry, learned[sid], [clearsky_weather(site, t) for t in sorted(hours)])
        actual[sid] = {t: p for t, p in hours.items() if max(p, sky.get(t, 0.0)) > bt.MIN_WH}
        clear[sid] = {t: sky.get(t, 0.0) for t in actual[sid]}
    wanted = set().union(*actual.values())

    by_model = {m: {w.start: w for w in hours if w.ghi is not None} for m, hours in weather.items()}
    skipped, valid = [], []
    for m in weather:
        share = len(wanted & set(by_model[m])) / len(wanted) if wanted else 0.0
        if share >= MIN_COVERAGE:
            valid.append(m)
        else:
            skipped.append({"model": m, "reason": NO_DATA if share == 0 else FEW_HOURS, "coverage": round(share, 3)})
    common = set(wanted)
    for m in valid:
        common &= set(by_model[m])
    first_last = sorted(common)
    span = {"first": day_of(first_last[0]).isoformat() if first_last else None,
            "last": day_of(first_last[-1]).isoformat() if first_last else None}
    n_days = len({day_of(t) for t in common})
    if not valid or n_days < MIN_DAYS:
        return {**head, **span, "status": NOT_ENOUGH, "days": n_days, "needed": MIN_DAYS, "combos": [],
                "skipped": skipped, "recommendation": None}

    # weather class of a day from what the arrays delivered against their clear-sky output
    sky_day: dict[date, list[float]] = {}
    for sid in known:
        for t in common & set(actual[sid]):
            row = sky_day.setdefault(day_of(t), [0.0, 0.0])
            row[0] += actual[sid][t]
            row[1] += clear[sid][t]
    clearness = {d: a / c for d, (a, c) in sky_day.items() if c > 0}

    combos: list[tuple[str, ...]] = [(m,) for m in valid] + list(combinations(valid, 2))
    if current and all(m in valid for m in current):
        current = [m for m in valid if m in current]          # the mean does not depend on the order
        head["current"] = current
        if tuple(current) not in combos:
            combos.append(tuple(current))
    ordered = sorted(common)
    paths = sun_paths(site, ordered)                  # the same for every combination: computed once
    results, per_day = [], {}
    for combo in combos:
        hours = mean_weather([[by_model[m][t] for t in ordered] for m in combo])
        parts = []
        for sid, arr in known.items():
            fc = forecast(site, arr.geometry, learned[sid], [w for w in hours if w.start in actual[sid]], paths=paths)
            parts.append({t: (fc.get(t, 0.0), a) for t, a in actual[sid].items() if t in common})
        pairs = bt.summed(parts)
        days = _days(pairs, day_of)
        per_day[combo] = days
        months = sorted({d.strftime("%Y-%m") for d in days})
        results.append({
            "models": list(combo), **bt.score(pairs, day_of),
            "classes": {name: _part(days, lambda d, lo=lo, hi=hi: d in clearness and lo <= clearness[d] < hi)
                        for name, lo, hi in CLASSES},
            "months": {mo: _part(days, lambda d, mo=mo: d.strftime("%Y-%m") == mo) for mo in months},
        })
    results.sort(key=lambda r: (r["day_error"] is None, r["day_error"] or 0.0, len(r["models"])))
    for r in results[1:]:                    # where they differ is shown for the best and the current choice only
        if r["models"] != current:
            del r["classes"], r["months"]
    return {**head, **span, "status": OK, "days": n_days, "hours": len(common), "combos": results,
            "skipped": skipped, "recommendation": _recommend(results, per_day, current)}


def _recommend(results: list[dict], per_day: dict[tuple[str, ...], Days], current: list[str]) -> dict | None:
    """Change only when a choice made without a half of the days wins on that half, in both directions.

    For each half (even, odd days) the best combination is picked on the other half and compared with the
    current choice on the held-out half: it must have the smaller daily error in RECOMMEND_SHARE of the
    bootstrap resamples. Picking the best of many on the same days it is judged on would always find one.
    """
    best = results[0]
    now = next((r for r in results if r["models"] == list(current)), None)
    if now is None:
        return None                                   # the current choice could not be scored (skipped model)
    rank = results.index(now) + 1
    if best is now:
        return {"verdict": CURRENT_BEST, "models": now["models"], "current_rank": rank, "gain": 0.0, "checks": []}
    rng = random.Random(0)
    checks = []
    for test in (0, 1):
        def on(d: date, half: int) -> bool:
            return d.toordinal() % 2 == half

        pick = min(results, key=lambda r: _error(per_day[tuple(r["models"])], lambda d: on(d, 1 - test))[0] or float("inf"))
        share = 0.0
        if pick is not now:
            held = {k: {d: v for d, v in per_day[k].items() if on(d, test)} for k in (tuple(pick["models"]), tuple(current))}
            share = _beats(held[tuple(pick["models"])], held[tuple(current)], rng)
        checks.append({"models": pick["models"], "share": share})
    change = all(c["share"] >= RECOMMEND_SHARE for c in checks)
    return {"verdict": CHANGE if change else KEEP, "models": best["models"], "current_rank": rank,
            "gain": round(now["day_error"] - best["day_error"], 4), "checks": checks}


def valid(result: dict | None) -> dict | None:
    """A stored result if it has the current form; an older one is dropped until the next run."""
    return result if isinstance(result, dict) and result.get("version") == VERSION else None
