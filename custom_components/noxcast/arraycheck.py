"""Checks of a learned array for the panel: monthly clear-sky balance, efficiency by month and by sun
direction, geometry fit, and hints at a configuration that does not fit the measurements.

Pure functions, no Home Assistant imports. Nothing here changes the forecast; the results explain it
and point at things worth checking (soiling, ageing, a tilt or azimuth that does not fit the data).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from .model import (
    CLEAR_KT, SUBSTEPS, ArrayGeometry, LearnedArray, _quantile, censored_quantile, clearsky_heating, clearsky_hour,
    clearsky_profile, known_free, temperature_factor, transmission,
)
from .solar import Site, clearsky_simplified_solis, extra_radiation, plane_of_array, sun_position
from .const import Series

_LOGGER = logging.getLogger(__name__)
H = 3600.0
MONTH_MIN_HOURS = 20                 # hours a month needs for its efficiency
GEOMETRY_MIN_HOURS = 60              # clear unshaded hours the geometry check needs
GEOMETRY_SIDE_MIN_HOURS = 15         # of them on each side of the plane's direction (sun left and right of it)
GEOMETRY_MAX_ROWS = 600             # rows evaluated per candidate (evenly thinned)
MIN_BEAM_SHARE = 0.4                 # hours whose clear-sky plane irradiance is at least this share of the best
DIRECTION_MIN_HOURS = 10             # hours a sun sector needs for its efficiency
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]   # sun azimuth in 45° sectors, centred on the name
EFFICIENCY_HIGH = 1.05               # above: more than the configured modules can give
DIRECTION_SWING = 0.12               # morning and afternoon this far apart (relative): the azimuth
SEASON_SWING = 0.15                  # months this far apart (relative): the tilt, or model errors at low sun


def _month(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def month_balance(site: Site, geo: ArrayGeometry, learned: LearnedArray, year: int,
                  air_temp: Series | None = None) -> list[dict]:
    """Clear-sky day in the middle of each month: energy and what shade, reflection, direction and heat change.

    Each effect is the difference between the full model and the model without that effect, relative
    to the full model without it. Temperature uses the mean daytime air temperature of that calendar
    month from the history, when there is one.
    """
    temps: dict[int, list[float]] = {}
    for t, v in (air_temp or {}).items():
        if v is not None:
            temps.setdefault(datetime.fromtimestamp(t, timezone.utc).month, []).append(v)
    out = []
    for m in range(1, 13):
        day = datetime(year, m, 15, tzinfo=timezone.utc).timestamp()
        temp = sum(temps[m]) / len(temps[m]) if temps.get(m) else None

        def energy(la: LearnedArray, t: float | None) -> float:
            return sum(sum(clearsky_profile(site, geo, la, day + h * H, t, steps=4)) / 4 for h in range(24))

        cool = LearnedArray(**{**learned.__dict__, "temp_coeff": 0.0})
        full = energy(cool, None)
        no_shade = energy(LearnedArray(**{**cool.__dict__, "cells": {}, "facade": None}), None)
        no_refl = energy(LearnedArray(**{**cool.__dict__, "reflection": {}}), None)
        no_dir = energy(LearnedArray(**{**cool.__dict__, "direction": {}}), None)
        heat = energy(learned, temp) if temp is not None and learned.temp_coeff else None

        def rel(a: float, b: float) -> float | None:
            return round((a - b) / b, 4) if b > 0 else None

        out.append({"month": m, "clear_wh": round(heat if heat is not None else full),
                    "shade": rel(full, no_shade), "reflection": rel(full, no_refl), "direction": rel(full, no_dir),
                    "temperature": rel(heat, full) if heat is not None else None,
                    "air_temp": round(temp, 1) if temp is not None else None})
    return out


def _rows(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
          satellite_ghi: Series | None, air_temp: Series | None,
          steady: set[float] | None = None, floors: set[float] | None = None) -> list[dict]:
    """Hours that show the plane itself: clear (by satellite, if there is one), unshaded, well lit.

    With steady hours (see model.steady_hours) only those, when there are enough of them. floors: hours at a
    fixed power limit, whose power is a lower bound (row "floor").
    """
    rows = []
    for t, p in samples:
        beam, diff, az, el, cs_ghi = clearsky_hour(site, geo, t, learned.sky_view)
        if el <= 5 or p <= 0:
            continue
        kt = None
        if satellite_ghi is not None:
            g = satellite_ghi.get(t)
            if g is None or cs_ghi <= 50 or g / cs_ghi < CLEAR_KT:
                continue
            kt = min(g / cs_ghi, 1.2)
        if not known_free(learned, az, el, margin=True):
            continue
        temp = (air_temp or {}).get(t)
        rows.append({"t": t, "p": p, "poa": beam + diff, "kt": kt, "temp": temp, "az": az, "floor": t in (floors or ()),
                     "heating": clearsky_heating(site, geo, t, learned.sky_view) if temp is not None else geo.heating})
    if steady is not None:
        calm = [r for r in rows if r["t"] in steady]
        if len(calm) >= GEOMETRY_MIN_HOURS:
            rows = calm
    if not rows:
        return []
    best = max(r["poa"] for r in rows)
    return [r for r in rows if r["poa"] >= MIN_BEAM_SHARE * best]


def efficiency_by_month(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                        satellite_ghi: Series | None = None,
                        air_temp: Series | None = None,
                        steady: set[float] | None = None, floors: set[float] | None = None) -> dict[str, list]:
    """Efficiency at 25 °C per calendar month: {"YYYY-MM": [efficiency, hours]}.

    With the satellite filter the median of clear hours, otherwise the 95th percentile of all strong
    hours, as in the learning itself. A slow decline points at ageing, a dip at soiling or snow; a
    seasonal swing rather at model errors at low sun.
    """
    return _efficiency_by(lambda r: _month(r["t"]), MONTH_MIN_HOURS,
                          _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady, floors), geo, learned,
                          satellite_ghi is not None)


def efficiency_by_direction(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                            satellite_ghi: Series | None = None,
                            air_temp: Series | None = None,
                            steady: set[float] | None = None, floors: set[float] | None = None) -> dict[str, list]:
    """Efficiency at 25 °C per sun direction (45° sectors, see SECTORS): {"SE": [efficiency, hours]}.

    The same hours as by month. With the right azimuth the morning and the afternoon agree; a wrong
    one makes one side too high and the other too low. Works for several planes too, where the
    geometry check does not.
    """
    def sector(r) -> str:
        return SECTORS[int(((r["az"] + 22.5) % 360) // 45)]

    out = _efficiency_by(sector, DIRECTION_MIN_HOURS,
                         _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady, floors),
                         geo, learned, satellite_ghi is not None)
    return {s: out[s] for s in SECTORS if s in out}


def _efficiency_by(key, min_hours: int, rows: list[dict], geo: ArrayGeometry, learned: LearnedArray,
                   satellite: bool) -> dict[str, list]:
    """A group whose quantile the hours at a power limit hide is left out, as cells are in the learning."""
    groups: dict[str, list[tuple[float, bool]]] = {}
    for r in rows:
        exp = geo.peak_w * r["poa"] / 1000 * (r["kt"] or 1.0)
        f = temperature_factor(learned.temp_coeff, r["temp"], r["poa"], r["heating"]) if r["temp"] is not None else 1.0
        ratio = r["p"] / (exp * f)
        if ratio <= 1.5:
            groups.setdefault(key(r), []).append((ratio, r["floor"]))
    q = 0.5 if satellite else 0.95
    out = {}
    for k, v in sorted(groups.items()):
        if len(v) >= min_hours and (value := censored_quantile([x for x, _ in v], [1.0] * len(v),
                                                               [f for _, f in v], q)) is not None:
            out[k] = [round(value, 4), len(v)]
    return out


def hints(geo: ArrayGeometry, learned: LearnedArray) -> list[str]:
    """What does not fit the configuration, most certain first.

    peak: an hourly mean above the configured peak power, which no module gives (peak power or sensor);
    high: an efficiency above EFFICIENCY_HIGH (peak power, sensor or a geometry that gets too little light);
    direction: morning and afternoon disagree (azimuth); season: the months disagree (tilt).
    Shade and little data only lower the efficiency, so a low one alone is no hint.
    """
    out = []
    if learned.max_power_w is not None and learned.max_power_w > geo.peak_w:
        out.append("peak")
    if learned.samples and learned.efficiency > EFFICIENCY_HIGH:
        out.append("high")
    d = learned.efficiency_directions or {}
    morning = [d[s][0] for s in ("NE", "E", "SE") if s in d]
    afternoon = [d[s][0] for s in ("SW", "W", "NW") if s in d]
    if morning and afternoon and learned.efficiency > 0:
        if abs(sum(morning) / len(morning) - sum(afternoon) / len(afternoon)) / learned.efficiency > DIRECTION_SWING:
            out.append("direction")
    months = [e for e, _ in (learned.efficiency_months or {}).values()]
    if len(months) >= 3 and learned.efficiency > 0 and (max(months) - min(months)) / learned.efficiency > SEASON_SWING:
        out.append("season")
    return out


HOUR_COLUMNS = ["start", "power_w", "clear_w", "kt", "air_temp", "steady", "shade", "efficiency", "check", "at_limit"]


def hour_table(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
               satellite_ghi: Series | None = None, air_temp: Series | None = None,
               steady: set[float] | None = None, floors: set[float] | None = None) -> dict:
    """Every learning hour with the sun up, for the diagnostics download: {"columns": [...], "rows": [[...]]}.

    clear_w: clear-sky power of the configured planes at an efficiency of 100 %; kt: the satellite's
    clearness; shade: the learned transmission at the sun position (1 = free); efficiency: what this
    hour alone says, measured over clear_w · kt · temperature factor (only with a satellite value);
    check: one of the clear, unshaded, well lit hours behind the efficiency by month and the geometry
    check. An efficiency far above 1 in these hours, all day long, points at the peak power or the
    sensor; only in the morning or the afternoon, at the azimuth; rising towards winter, at the tilt.
    at_limit: a sensor was at its fixed power limit, so power and efficiency of the hour are lower bounds.
    Sun positions are left out: with the time they would give the location.
    """
    check = {r["t"] for r in _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady, floors)}
    rows = []
    for t, p in samples:
        beam, diff, az, el, cs_ghi = clearsky_hour(site, geo, t, learned.sky_view)
        if el <= 0:
            continue
        poa = beam + diff
        clear_w = geo.peak_w * poa / 1000
        g = satellite_ghi.get(t) if satellite_ghi is not None else None
        kt = min(g / cs_ghi, 1.2) if g is not None and cs_ghi > 50 else None
        temp = (air_temp or {}).get(t)
        eff = None
        if kt and clear_w > 0:
            f = temperature_factor(learned.temp_coeff, temp, poa,
                                   clearsky_heating(site, geo, t, learned.sky_view)) if temp is not None else 1.0
            eff = round(p / (clear_w * kt * f), 3)
        rows.append([datetime.fromtimestamp(t, timezone.utc).isoformat(), round(p, 1), round(clear_w, 1),
                     round(kt, 3) if kt is not None else None, temp, t in steady if steady is not None else None,
                     round(transmission(learned, az, el), 3), eff, t in check, t in (floors or ())])
    return {"columns": HOUR_COLUMNS, "rows": rows}


def _spread(ratios: list[float], floors: list[bool] | None = None) -> float:
    """Interquartile range over the median; floors: ratios that are lower bounds (an hour at a power limit)."""
    if not floors or not any(floors):
        mid = _quantile(ratios, 0.5)
        return (_quantile(ratios, 0.75) - _quantile(ratios, 0.25)) / mid if mid > 0 else math.inf
    low, mid, high = (censored_quantile(ratios, [1.0] * len(ratios), floors, q) for q in (0.25, 0.5, 0.75))
    return (high - low) / mid if None not in (low, mid, high) and mid > 0 else math.inf


def check_geometry(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                   satellite_ghi: Series | None = None,
                   air_temp: Series | None = None, steady: set[float] | None = None,
                   floors: set[float] | None = None) -> dict | None:
    """Tilt and azimuth under which clear unshaded hours fit one efficiency best.

    With the right geometry the ratio of measured to modelled power stays the same over the day and the
    year; a wrong azimuth shows as mornings too high and afternoons too low. The spread (interquartile
    range over the median) is minimised on a coarse and then a fine grid. Only a diagnostic: the
    shading map was learned with the configured geometry, so nothing is applied automatically.
    Needs the satellite filter, since cloudy hours would hide the signal. Not done for an array with
    several orientations: one best plane does not describe it, and moving one of its planes while
    the others stay would mostly fit the learned shade and direction factor, so any advice would mislead.
    """
    if satellite_ghi is None or geo.extra:
        return None
    rows = _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady, floors)
    if len(rows) < GEOMETRY_MIN_HOURS:
        return None
    # An azimuth error shows as one side of the plane's direction against the other. With free hours on one
    # side only (a tree takes the other), the fit would turn the plane to explain shade it does not know.
    left = sum(1 for r in rows if (r["az"] - geo.azimuth) % 360 > 180)
    if min(left, len(rows) - left) < GEOMETRY_SIDE_MIN_HOURS:
        return None
    if len(rows) > GEOMETRY_MAX_ROWS:
        step = len(rows) / GEOMETRY_MAX_ROWS
        rows = [rows[int(i * step)] for i in range(GEOMETRY_MAX_ROWS)]
    # sun and clear-sky irradiance per sub-step do not depend on the geometry: computed once
    prepared = []
    for r in rows:
        dni_extra = extra_radiation(r["t"] + 1800)
        subs = []
        for i in range(SUBSTEPS):
            sun = sun_position(site, r["t"] + (i + 0.5) * H / SUBSTEPS)
            if sun.elevation > 0:
                subs.append((sun, *clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra), dni_extra))
        prepared.append((r, subs))

    def spread(tilt: float, azimuth: float) -> float:
        ratios, bounds = [], []
        for r, subs in prepared:
            poa = 0.0
            for sun, ghi, dni, dhi, dni_extra in subs:
                b, d = plane_of_array(tilt, azimuth, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view)
                poa += b + d
            poa /= SUBSTEPS
            if poa <= 0:
                continue
            f = temperature_factor(learned.temp_coeff, r["temp"], poa, geo.heating) if r["temp"] is not None else 1.0
            ratios.append(r["p"] / (geo.peak_w * poa / 1000 * r["kt"] * f))
            bounds.append(r["floor"])
        return _spread(ratios, bounds) if len(ratios) >= GEOMETRY_MIN_HOURS else math.inf

    def search(tilt0: float, az0: float, d_tilt: float, d_az: float, n: int) -> tuple[float, float, float]:
        best = (math.inf, tilt0, az0)
        for i in range(-n, n + 1):
            tilt = min(max(tilt0 + i * d_tilt, 0.0), 90.0)
            for j in range(-n, n + 1):
                az = (az0 + j * d_az) % 360
                s = spread(tilt, az)
                if s < best[0]:
                    best = (s, tilt, az)
        return best

    configured = spread(geo.tilt, geo.azimuth)
    s, tilt, az = search(geo.tilt, geo.azimuth, 5.0, 10.0, 3)
    s, tilt, az = search(tilt, az, 2.0, 4.0, 2)
    improvement = 1 - s / configured if configured > 0 and math.isfinite(configured) else 0.0
    d_az = (az - geo.azimuth + 180) % 360 - 180
    off = abs(tilt - geo.tilt) >= 5 or abs(d_az) >= 10
    confidence = "high" if improvement >= 0.2 and len(rows) >= 200 else "medium" if improvement >= 0.1 else "low"
    return {"tilt": round(tilt, 1), "azimuth": round(az, 1), "spread": round(s, 4), "spread_configured": round(configured, 4),
            "improvement": round(improvement, 3), "hours": len(rows), "deviates": off and confidence != "low",
            "confidence": confidence}


def annotate(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
             air_temp: Series | None, satellite_ghi: Series | None, year: int,
             steady: set[float] | None = None, floors: set[float] | None = None) -> None:
    """Set the panel explanations on a freshly learned array; a failure never stops the learning."""
    try:
        learned.months = month_balance(site, geo, learned, year, air_temp)
        learned.efficiency_months = efficiency_by_month(site, geo, learned, samples, satellite_ghi, air_temp, steady,
                                                        floors)
        learned.geometry_check = check_geometry(site, geo, learned, samples, satellite_ghi, air_temp, steady, floors)
        learned.efficiency_directions = efficiency_by_direction(site, geo, learned, samples, satellite_ghi, air_temp,
                                                                steady, floors)
        if samples:
            learned.max_power_t, learned.max_power_w = max(samples, key=lambda s: s[1])
            learned.max_power_w = round(learned.max_power_w, 1)
        learned.hints = hints(geo, learned)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Array check failed")
