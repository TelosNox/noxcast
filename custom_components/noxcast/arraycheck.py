"""Checks of a learned array for the panel: monthly clear-sky balance, efficiency by month, geometry fit.

Pure functions, no Home Assistant imports. Nothing here changes the forecast; the results explain it
and point at things worth checking (soiling, ageing, a tilt or azimuth that does not fit the data).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .model import (
    CLEAR_KT, SUBSTEPS, ArrayGeometry, LearnedArray, _quantile, clearsky_heating, clearsky_hour, clearsky_profile,
    temperature_factor, transmission,
)
from .solar import Site, clearsky_simplified_solis, extra_radiation, plane_of_array, sun_position

H = 3600.0
MONTH_MIN_HOURS = 20                 # hours a month needs for its efficiency
GEOMETRY_MIN_HOURS = 60              # clear unshaded hours the geometry check needs
GEOMETRY_MAX_ROWS = 600              # rows evaluated per candidate (evenly thinned)
MIN_BEAM_SHARE = 0.4                 # hours whose clear-sky plane irradiance is at least this share of the best


def _month(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def month_balance(site: Site, geo: ArrayGeometry, learned: LearnedArray, year: int,
                  air_temp: dict[float, float] | None = None) -> list[dict]:
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
          satellite_ghi: dict[float, float] | None, air_temp: dict[float, float] | None,
          steady: set[float] | None = None) -> list[dict]:
    """Hours that show the plane itself: clear (by satellite, if there is one), unshaded, well lit.

    With steady hours (see model.steady_hours) only those, when there are enough of them.
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
        if transmission(learned, az, el) < 0.95:
            continue
        temp = (air_temp or {}).get(t)
        rows.append({"t": t, "p": p, "poa": beam + diff, "kt": kt, "temp": temp,
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
                        satellite_ghi: dict[float, float] | None = None,
                        air_temp: dict[float, float] | None = None,
                        steady: set[float] | None = None) -> dict[str, list]:
    """Efficiency at 25 °C per calendar month: {"YYYY-MM": [efficiency, hours]}.

    With the satellite filter the median of clear hours, otherwise the 95th percentile of all strong
    hours, as in the learning itself. A slow decline points at ageing, a dip at soiling or snow; a
    seasonal swing rather at model errors at low sun.
    """
    groups: dict[str, list[float]] = {}
    for r in _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady):
        exp = geo.peak_w * r["poa"] / 1000 * (r["kt"] or 1.0)
        f = temperature_factor(learned.temp_coeff, r["temp"], r["poa"], r["heating"]) if r["temp"] is not None else 1.0
        ratio = r["p"] / (exp * f)
        if ratio <= 1.5:
            groups.setdefault(_month(r["t"]), []).append(ratio)
    q = 0.5 if satellite_ghi is not None else 0.95
    return {m: [round(_quantile(v, q), 4), len(v)] for m, v in sorted(groups.items()) if len(v) >= MONTH_MIN_HOURS}


def _spread(ratios: list[float]) -> float:
    mid = _quantile(ratios, 0.5)
    return (_quantile(ratios, 0.75) - _quantile(ratios, 0.25)) / mid if mid > 0 else math.inf


def check_geometry(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                   satellite_ghi: dict[float, float] | None = None,
                   air_temp: dict[float, float] | None = None, steady: set[float] | None = None) -> dict | None:
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
    rows = _rows(site, geo, learned, samples, satellite_ghi, air_temp, steady)
    if len(rows) < GEOMETRY_MIN_HOURS:
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
        ratios = []
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
        return _spread(ratios) if len(ratios) >= GEOMETRY_MIN_HOURS else math.inf

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
