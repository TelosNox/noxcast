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
from datetime import datetime, timezone

from . import arraycheck as ac
from . import explain
from . import learning as ln
from .model import LearnedArray, WeatherHour, clearsky_hour, clearsky_weather
from .solar import Site

VERSION = 1
COLUMNS = ["start", "status", "reasons", "power_w", "model_w", "clear_w", "clouds_w", "shade_w", "reflection_w",
           "direction_w", "heat_w", "kt", "air_temp", "snowfall_cm", "soc_pct", "steady", "efficiency", "check"]
USED, LOWER_BOUND, LEFT_OUT = "used", "lower_bound", "left_out"
_STEP_COLUMNS = ("clear", "clouds", "shade", "reflection", "direction", "heat")


def hour_table(site: Site, arr: ln.ArrayConfig, learned: LearnedArray, history: ln.PvHistory, soc_full: float,
               exclusions: list[tuple[float, float]]) -> dict:
    """{"version", "columns", "rows"}: every hour of the array's history with the sun up, oldest first.

    reasons: the names joined by a space (learning.hour_reasons). clear_w exists for every row (the learned model
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
