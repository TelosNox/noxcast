"""Learning and forecasting for one PV array (pure Python, no Home Assistant imports).

Model:  P = peak_w · efficiency · (beam_poa · T(sun_azimuth, sun_elevation) + diffuse_poa) / 1000

- efficiency: system efficiency, learned from hours with strong expected output.
- T: beam transmission (1 = unshaded) per sun-position cell, learned from hours where direct
  light is geometrically possible. The 90th percentile per cell represents clear conditions;
  clouds and occasional curtailment fall below it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .solar import Site, erbs, extra_radiation, plane_of_array, sun_position, clearsky_simplified_solis

AZ_STEP = 10.0
EL_STEP = 5.0
SUBSTEPS = 6                     # 10-minute sub-steps per hour
MIN_BEAM_W = 60.0                # expected beam power (W) needed to observe shading
MIN_CELL_SAMPLES = 4
RECENT_DAYS = 60                 # window for change detection per cell
CHANGE_THRESHOLD = 0.25
DEFAULT_EFFICIENCY = 0.9
HALF_LIFE_DAYS = 365.0


@dataclass(frozen=True)
class ArrayGeometry:
    tilt: float
    azimuth: float
    peak_w: float


@dataclass(frozen=True)
class WeatherHour:
    start: float                 # UNIX seconds, start of the hour
    ghi: float                   # mean global horizontal irradiance over the hour, W/m²
    dni: float | None = None     # optional; split from GHI (Erbs) when missing
    dhi: float | None = None


@dataclass
class LearnedArray:
    efficiency: float = DEFAULT_EFFICIENCY
    cells: dict[str, float] = field(default_factory=dict)      # "az_idx,el_idx" -> transmission
    coverage: float = 0.0                                      # share of reachable cells learned
    samples: int = 0
    first_sample: float | None = None
    learned_at: float | None = None

    def as_dict(self) -> dict:
        return {"efficiency": self.efficiency, "cells": self.cells, "coverage": self.coverage,
                "samples": self.samples, "first_sample": self.first_sample, "learned_at": self.learned_at}

    @classmethod
    def from_dict(cls, data: dict) -> LearnedArray:
        return cls(**{k: data[k] for k in ("efficiency", "cells", "coverage", "samples", "first_sample", "learned_at")
                      if k in data})


# ---------------------------------------------------------------------------- helpers

def _cell(azimuth: float, elevation: float) -> tuple[int, int]:
    return int(azimuth // AZ_STEP), int(elevation // EL_STEP)


def _key(az_idx: int, el_idx: int) -> str:
    return f"{az_idx},{el_idx}"


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def clearsky_hour(site: Site, geo: ArrayGeometry, start: float) -> tuple[float, float, float, float]:
    """Hourly means of clear-sky (beam, diffuse) on the plane plus mean sun (azimuth, elevation).

    The sun position is averaged over the sub-steps where the sun is up, so an hourly mean value
    belongs to the sun path within that hour, not to the hour start.
    """
    beam = diff = 0.0
    sx = sy = el_sum = 0.0
    up = 0
    pressure = site.pressure
    dni_extra = extra_radiation(start + 1800)
    for i in range(SUBSTEPS):
        sun = sun_position(site, start + (i + 0.5) * 3600 / SUBSTEPS)
        if sun.elevation <= 0:
            continue
        ghi, dni, dhi = clearsky_simplified_solis(sun.elevation, pressure, dni_extra)
        b, d = plane_of_array(geo.tilt, geo.azimuth, sun, dni, dhi, ghi)
        beam += b
        diff += d
        sx += math.sin(math.radians(sun.azimuth))
        sy += math.cos(math.radians(sun.azimuth))
        el_sum += sun.elevation
        up += 1
    if not up:
        return 0.0, 0.0, 0.0, -1.0
    az = math.degrees(math.atan2(sx, sy)) % 360.0
    return beam / SUBSTEPS, diff / SUBSTEPS, az, el_sum / up


# ---------------------------------------------------------------------------- learning

def usable_samples(power: dict[float, float], *, soc: dict[float, float] | None = None, soc_full: float = 95.0,
                   exclusions: list[tuple[float, float]] = (), valid_since: float | None = None) -> list[tuple[float, float]]:
    """Filter hourly mean power (W) for learning: commissioning, validity, curtailment, exclusions."""
    producing = [t for t, p in power.items() if p is not None and p > 5]
    if not producing:
        return []
    start = max(min(producing), valid_since or 0)
    out = []
    for t, p in sorted(power.items()):
        if p is None or t < start or p < 0:
            continue
        if soc and soc.get(t) is not None and soc[t] >= soc_full:
            continue
        if any(a <= t < b for a, b in exclusions):
            continue
        out.append((t, p))
    return out


def learn(site: Site, geo: ArrayGeometry, samples: list[tuple[float, float]], now: float,
          half_life_days: float = HALF_LIFE_DAYS) -> LearnedArray:
    """Learn efficiency and the shading map from (hour_start, mean power W) samples."""
    rows = []
    for t, p in samples:
        beam, diff, az, el = clearsky_hour(site, geo, t)
        if el <= 0:
            continue
        age = max(now - t, 0) / 86400.0
        rows.append((t, p, beam, diff, az, el, 0.5 ** (age / half_life_days), age))
    if not rows:
        return LearnedArray(learned_at=now)

    # efficiency from strong expected hours
    exp = [geo.peak_w * (r[2] + r[3]) / 1000 for r in rows]
    limit = 0.4 * max(exp)
    # ratios above 1.5 are physically impossible (sensor spikes) and must not inflate the efficiency
    ratios = [(r[1] / e, r[6]) for r, e in zip(rows, exp) if e > limit and e > 0 and r[1] / e <= 1.5]
    efficiency = weighted_quantile([v for v, _ in ratios], [w for _, w in ratios], 0.95) if ratios else DEFAULT_EFFICIENCY

    # beam transmission per sun-position cell
    obs: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for t, p, beam, diff, az, el, weight, age in rows:
        beam_w = geo.peak_w * beam / 1000
        if beam_w <= MIN_BEAM_W:
            continue
        tr = (p / efficiency - geo.peak_w * diff / 1000) / beam_w
        obs.setdefault(_cell(az, el), []).append((min(max(tr, -0.2), 1.3), weight, age))

    cells = {}
    for (ai, ei), items in obs.items():
        if len(items) < MIN_CELL_SAMPLES:
            continue
        value = weighted_quantile([v for v, _, _ in items], [w for _, w, _ in items], 0.9)
        recent = [v for v, _, a in items if a <= RECENT_DAYS]
        if len(recent) >= 3:
            recent_value = weighted_quantile(recent, [1.0] * len(recent), 0.9)
            if abs(recent_value - value) > CHANGE_THRESHOLD:     # e.g. a tree was cut or a building added
                value = recent_value
        cells[_key(ai, ei)] = round(min(max(value, 0.0), 1.0), 3)

    reachable = reachable_cells(site, geo)
    coverage = len(reachable & {tuple(map(int, k.split(","))) for k in cells}) / len(reachable) if reachable else 0.0
    return LearnedArray(efficiency=round(efficiency, 4), cells=cells, coverage=round(coverage, 3),
                        samples=len(rows), first_sample=rows[0][0], learned_at=now)


def reachable_cells(site: Site, geo: ArrayGeometry, year_start: float = 1767225600.0) -> set[tuple[int, int]]:
    """Cells that hourly samples can fill within a year (same criteria as learning)."""
    visits: dict[tuple[int, int], int] = {}
    for k in range(365 * 24):
        beam, _, az, el = clearsky_hour(site, geo, year_start + k * 3600)
        if el > 0 and geo.peak_w * beam / 1000 > MIN_BEAM_W:
            c = _cell(az, el)
            visits[c] = visits.get(c, 0) + 1
    return {c for c, n in visits.items() if n >= MIN_CELL_SAMPLES}


# ---------------------------------------------------------------------------- forecasting

def transmission(learned: LearnedArray, azimuth: float, elevation: float) -> float:
    """Bilinear interpolation between cell centres; missing cells fall back to neighbours, then 1."""
    fa = azimuth / AZ_STEP - 0.5
    fe = elevation / EL_STEP - 0.5
    a0, e0 = math.floor(fa), math.floor(fe)
    da, de = fa - a0, fe - e0
    num = den = 0.0
    for ai, wa in ((a0, 1 - da), (a0 + 1, da)):
        for ei, we in ((e0, 1 - de), (e0 + 1, de)):
            v = learned.cells.get(_key(ai % 36, ei))
            if v is not None and wa * we > 0:
                num += v * wa * we
                den += wa * we
    if den > 0:
        return num / den
    ai, ei = _cell(azimuth, elevation)
    near = [learned.cells[k] for k in (_key((ai + x) % 36, ei + y) for x in (-1, 0, 1) for y in (-1, 0, 1))
            if k in learned.cells]
    return sum(near) / len(near) if near else 1.0


def forecast(site: Site, geo: ArrayGeometry, learned: LearnedArray, weather: list[WeatherHour]) -> dict[float, float]:
    """Expected energy per hour (Wh), keyed by hour start (UNIX seconds)."""
    out = {}
    for wh in weather:
        dni_extra = extra_radiation(wh.start + 1800)
        total = 0.0
        for i in range(SUBSTEPS):
            sun = sun_position(site, wh.start + (i + 0.5) * 3600 / SUBSTEPS)
            if sun.elevation <= 0 or wh.ghi <= 0:
                continue
            if wh.dni is None or wh.dhi is None:
                dni, dhi = erbs(wh.ghi, sun.zenith, dni_extra)
            else:
                dni, dhi = wh.dni, wh.dhi
            beam, diff = plane_of_array(geo.tilt, geo.azimuth, sun, dni, dhi, wh.ghi)
            total += geo.peak_w * learned.efficiency * (beam * transmission(learned, sun.azimuth, sun.elevation) + diff) / 1000
        out[wh.start] = total / SUBSTEPS
    return out
