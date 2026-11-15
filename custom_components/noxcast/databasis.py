"""What learning saw: every daylight hour of an array with its status, its reasons and the model split into steps.

Pure functions, no Home Assistant imports. The table goes into the diagnostics download and, as CSV, into the export
of the panel, so that a user (or their own tools) can check whether the filters fit the installation: does "battery
full" start too early, does a snow window end when the modules are free again.

Status: "used" (a sample of learning), "lower_bound" (a sample, but the real output was at least as high: a power
limit, a battery charging at its maximum, a system holding PV back) or "left_out" with every reason. The model is the
learned model on the satellite irradiance of that hour (as in the hold-out test), split as in explain.hour_steps.
Sun positions are left out: with the time they would give the location.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

from . import arraycheck as ac
from . import explain
from . import learning as ln
from .model import LearnedArray, WeatherHour, clearsky_hour, clearsky_weather, forecast, solar_day
from .solar import Site

VERSION = 2
COLUMNS = ["start", "status", "reasons", "power_w", "model_w", "bound_w", "clear_w", "clouds_w", "shade_w", "reflection_w",
           "direction_w", "heat_w", "kt", "air_temp", "snowfall_cm", "soc_pct", "steady", "efficiency", "check"]
USED, LOWER_BOUND, LEFT_OUT = "used", "lower_bound", "left_out"
_EPOCH = date(1970, 1, 1)                 # solar day numbers count from here (model.solar_day)
_STEP_COLUMNS = ("clear", "clouds", "shade", "reflection", "direction", "heat")


def hour_table(site: Site, arr: ln.ArrayConfig, learned: LearnedArray, history: ln.PvHistory, soc_full: float,
               exclusions: list[tuple[float, float]]) -> dict:
    """{"version", "columns", "rows"}: every hour of the array's history with the sun up, oldest first.

    reasons: the names joined by a space (learning.hour_reasons). bound_w: the learned model under a clear sky without
    the shading map, the most the array should give (the design's upper bound). clear_w exists for every row (the learned model
    under a clear sky, to weigh an hour by what it could have given); the other model columns are empty without a
    satellite value for the hour (the satellite archive is an option). model_w is the model without
    the output limits, so a lower_bound hour at a limit shows more than the limit. soc_pct is the household's state of
    charge (main entry); battery_full also comes from the batteries of the array's systems. Only hours with a
    measurement are listed. efficiency and check only exist for samples (arraycheck.hour_table).
    """
    geo = arr.geometry
    reasons = ln.hour_reasons(site, arr, history, soc_full, exclusions)
    power = ln.scaled_sum(history.power, arr.sensors, history.factors)
    samples, steady, floors = ln.array_samples(arr, history, soc_full, exclusions,
                                               skip=ln.left_out(site, arr, history).hours)
    checked = {row[0]: (row[7], row[8]) for row in
               ac.hour_table(site, geo, learned, samples, history.satellite_ghi, history.air_temp, steady,
                             floors)["rows"]}
    sat, air = history.satellite_ghi or {}, history.air_temp or {}
    snow, soc = history.snowfall or {}, history.soc or {}
    sky: dict[float, tuple[float, float]] = {}       # hour start -> (elevation, clear-sky GHI)
    for t in reasons:
        _, _, _, el, cs_ghi = clearsky_hour(site, geo, t, learned.sky_view)
        if el > 0:
            sky[t] = (el, cs_ghi)
    steps = explain.hour_steps(site, geo, learned, [WeatherHour(t, max(0.0, sat[t]), temp=air.get(t))
                                                    for t in sky if sat.get(t) is not None])
    # without a satellite value only the clear-sky start: what the hour could have given, to weigh it by
    clear_only = explain.hour_steps(site, geo, learned, [clearsky_weather(site, t, air.get(t))
                                                         for t in sky if t not in steps])
    steps.update({t: {"clear": step["clear"]} for t, step in clear_only.items()})
    # the upper bound of the design: clear sky with the learned model (reflection, direction, heat), shade aside
    unshaded = LearnedArray(**{**learned.__dict__, "cells": {}, "facade": None})
    bound = forecast(site, geo, unshaded, [clearsky_weather(site, t, air.get(t)) for t in sky])
    rows = []
    for t, (why, low) in reasons.items():
        if t not in sky:
            continue
        status = LEFT_OUT if why else LOWER_BOUND if low else USED
        step = steps.get(t, {})
        g, cs_ghi = sat.get(t), sky[t][1]
        kt = round(min(max(0.0, g) / cs_ghi, 1.2), 3) if g is not None and cs_ghi > 50 else None
        iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
        eff, check = checked.get(iso, (None, None))
        temp = air.get(t)
        rows.append([iso, status, " ".join(why or low), round(power[t], 1), step.get("result"),
                     round(bound[t], 1) if t in bound else None,
                     *(step.get(name) for name in _STEP_COLUMNS), kt,
                     round(temp, 1) if temp is not None else None, snow.get(t), soc.get(t),
                     t in steady if status != LEFT_OUT else None, eff, check if status != LEFT_OUT else None])
    return {"version": VERSION, "columns": COLUMNS, "rows": rows}


def to_csv(table: dict) -> str:
    """The table as CSV: one header line, an empty field for no value, 1 and 0 for yes and no."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(table["columns"])
    for row in table["rows"]:
        writer.writerow(["" if v is None else int(v) if isinstance(v, bool) else v for v in row])
    return out.getvalue()


# ---------------------------------------------------------------------------- summary for the panel

SUMMARY_VERSION = 3
CATEGORIES = ("used", "lower_bound", "battery", "snow", "no_output", "excluded", "other")
# a day under snow that delivered nothing is snow first: it is the textbook snow case, not an outage
_LEFT = (("excluded", {"excluded"}), ("snow", {"snow"}), ("no_output", {"no_output"}),
         ("battery", {"soc_full", "battery_full"}))
LIT_SHARE = 0.1             # an hour counts for a rule from this share of the peak power on (model or bound)
MATCH = 0.05                # "battery full" although the power is within this of the model
MATCH_DAYS = 3              # ... on at least this many days (two such hours each), and
MATCH_SHARE = 0.3           # ... for at least this share of the "battery full" hours with a model,
MATCH_OVER_USED = 1.5       # ... and this many times the share of used hours that match as closely
ABOVE_BOUND = 1.15          # power above this many times the clear-sky bound of the learned model
ABOVE_HOURS = 5             # ... in at least this many hours and
ABOVE_SHARE = 0.01          # ... this share of the lit counted hours: a rare hour of cloud enhancement is no sign
BRIGHT_KT = 0.5             # a day without output (and no snow) under a sky this clear: an outage
DAY_RATIO = 0.7             # a used day at less than this share of the model, which no filter explains
DAY_MIN_H = 2.0             # ... with a model day of at least this many hours at peak power: bright days only, on
                            # dull days the satellite itself is often off by a third
CALENDAR_DAYS = 365         # the calendar shows the year before the last day; older days stay in the CSV export
DAY_LEFT = 0.5              # a day counts as left out when more than this share of its clear-sky energy was
CALENDAR_MIN_H = 0.2        # a day's deviation needs a model day of this many hours at peak power: below it the
                            # ratio of two small numbers says nothing


def category(status: str, reasons: str) -> str | None:
    """The one category a row counts in: its status, or for a left-out row its main reason; None before the array
    was in use (commissioning is not data quality)."""
    if status != LEFT_OUT:
        return status
    names = set(reasons.split())
    if "before_use" in names:
        return None
    return next((cat for cat, keys in _LEFT if names & keys), "other")


def summary(site: Site, table: dict, peak_w: float) -> dict:
    """What learning saw, for the panel: shares of the clear-sky energy per category (whole history and per month,
    UTC) and the anomalies, each with a count, a value and its most telling day (UTC time of solar noon).

    Weighted by clear_w, not by hours: a June noon left out costs learning far more than a December edge hour.
    """
    cols = table["columns"]
    rows = [dict(zip(cols, r)) for r in table["rows"]]
    energy = dict.fromkeys(CATEGORIES, 0.0)
    months: dict[str, dict[str, float]] = {}
    days: dict[int, list[dict]] = {}
    for r in rows:
        cat = category(r["status"], r["reasons"])
        if cat is None:
            continue
        r["cat"] = cat
        clear = r["clear_w"] or 0.0
        energy[cat] += clear
        month = months.setdefault(r["start"][:7], dict.fromkeys(CATEGORIES, 0.0))
        month[cat] += clear
        days.setdefault(_solar_day(site, r["start"]), []).append(r)

    def shares(e: dict[str, float]) -> dict[str, float]:
        total = sum(e.values())
        return {k: round(v / total, 3) for k, v in e.items() if v > 0} if total > 0 else {}

    return {"version": SUMMARY_VERSION, "hours": sum(len(v) for v in days.values()), "shares": shares(energy),
            "months": {m: shares(e) for m, e in sorted(months.items())},
            "anomalies": _anomalies(site, days, peak_w) if peak_w > 0 else [],
            "days": _calendar(site, days, peak_w)}


def _calendar(site: Site, days: dict[int, list[dict]], peak_w: float) -> dict[str, list]:
    """The days with data in the CALENDAR_DAYS before the last one, oldest first, as columns:

    day: the solar day as ISO date (a date, not its noon: the time of solar noon would give the longitude). cat:
    "used" when learning kept most of the day's clear-sky energy (as sample or lower bound), else the category that
    took most of it. ratio: actual against model over the used hours with a satellite value, None for a left-out
    day, a dull one (CALENDAR_MIN_H) or one mostly at a lower bound. battery: lit hours left out as "battery full",
    so a day that learning used can still show that its noon was missing.
    """
    lit = LIT_SHARE * peak_w
    out: dict[str, list] = {"day": [], "cat": [], "ratio": [], "battery": []}
    last = max(days, default=0)
    for d in sorted(d for d in days if d > last - CALENDAR_DAYS):
        rs = days[d]
        energy: dict[str, float] = {}
        for r in rs:
            energy[r["cat"]] = energy.get(r["cat"], 0.0) + (r["clear_w"] or 0.0)
        total = sum(energy.values())
        kept = energy.get("used", 0.0) + energy.get("lower_bound", 0.0)
        cat = "used" if total <= 0 or kept >= (1 - DAY_LEFT) * total else max(
            (k for k in energy if k not in ("used", "lower_bound")), key=lambda k: energy[k])
        ratio = None
        if cat == "used":
            used = [r for r in rs if r["cat"] == "used" and r["model_w"] is not None]
            model = sum(r["model_w"] for r in used)
            if peak_w > 0 and model >= CALENDAR_MIN_H * peak_w:
                ratio = round(sum(r["power_w"] for r in used) / model, 2)
        battery = sum(1 for r in rs if r["cat"] == "battery" and (r["clear_w"] or 0) >= lit)
        for key, value in (("day", _EPOCH.fromordinal(_EPOCH.toordinal() + d).isoformat()), ("cat", cat),
                           ("ratio", ratio), ("battery", battery)):
            out[key].append(value)
    return out


def day_rows(site: Site, table: dict, day: date) -> dict:
    """The rows of the table on a solar day of the calendar: the day view of the panel."""
    d = day.toordinal() - _EPOCH.toordinal()
    return {"version": table["version"], "columns": table["columns"],
            "rows": [r for r in table["rows"] if _solar_day(site, r[0]) == d]}


def _solar_day(site: Site, iso: str) -> int:
    return solar_day(site, datetime.fromisoformat(iso).timestamp())


def _anomalies(site: Site, days: dict[int, list[dict]], peak_w: float) -> list[dict]:
    lit = LIT_SHARE * peak_w
    noon = lambda d: ln.noons(site, [d])[0]                       # noqa: E731
    close = lambda r: abs(r["power_w"] / r["model_w"] - 1) <= MATCH       # noqa: E731
    out = []

    # "battery full", but the array delivered what the model says, clearly more often than in hours learning used:
    # the battery did not hold it back, and learning lost these hours for nothing
    used = [r for rs in days.values() for r in rs if r["cat"] == "used" and (r["model_w"] or 0) >= lit]
    used_share = sum(map(close, used)) / len(used) if used else 0.0
    with_model = matched = 0
    per_day: dict[int, int] = {}
    for d, rs in days.items():
        for r in rs:
            if r["cat"] == "battery" and (r["model_w"] or 0) >= lit:
                with_model += 1
                if close(r):
                    matched += 1
                    per_day[d] = per_day.get(d, 0) + 1
    good = {d: n for d, n in per_day.items() if n >= 2}
    share = matched / with_model if with_model else 0.0
    if len(good) >= MATCH_DAYS and share >= max(MATCH_SHARE, MATCH_OVER_USED * used_share):
        out.append({"key": "battery_matches", "count": len(good), "value": round(share, 2),
                    "used": round(used_share, 2), "day": noon(max(good, key=lambda d: (good[d], d)))})

    counted = [(r, d) for d, rs in days.items() for r in rs
               if r["cat"] in ("used", "lower_bound") and (r["bound_w"] or 0) >= lit]
    above = [(r["power_w"] / r["bound_w"], d) for r, d in counted if r["power_w"] > ABOVE_BOUND * r["bound_w"]]
    if len(above) >= max(ABOVE_HOURS, ABOVE_SHARE * len(counted)):
        ratio, d = max(above)
        out.append({"key": "above_clear", "count": len(above), "value": round(ratio, 2), "day": noon(d)})

    bright = []
    for d, rs in days.items():
        kts = [r["kt"] for r in rs if r["cat"] == "no_output" and r["kt"] is not None and (r["clear_w"] or 0) >= lit]
        if kts and sum(kts) / len(kts) >= BRIGHT_KT:
            bright.append((sum(kts) / len(kts), d))
    if bright:
        kt, d = max(bright)
        out.append({"key": "bright_outage", "count": len(bright), "value": round(kt, 2), "day": noon(d)})

    worst = []
    for d, rs in days.items():
        hours = [r for r in rs if r["cat"] == "used" and r["model_w"] is not None]
        model = sum(r["model_w"] for r in hours)
        # only whole days: a day that is mostly left out says nothing about the model
        if len(hours) < 0.8 * len(rs) or model < DAY_MIN_H * peak_w:
            continue
        ratio = sum(r["power_w"] for r in hours) / model
        if ratio < DAY_RATIO:
            worst.append((ratio, d))
    if worst:
        ratio, d = min(worst)
        out.append({"key": "unexplained", "count": len(worst), "value": round(ratio, 2), "day": noon(d)})
    return out
