"""Learning and forecasting for one PV array (pure Python, no Home Assistant imports).

Model:  P = peak_w · efficiency₂₅ · f_temp · (beam_poa · T(sun_azimuth, sun_elevation) + diffuse_poa) / 1000

- efficiency₂₅: system efficiency at 25 °C cell temperature, learned from hours with strong
  expected output.
- f_temp = 1 + temp_coeff · (T_cell − 25 °C), T_cell = T_air + HEATING_K · POA / 1000 W/m².
  The temperature coefficient comes from the datasheet. Without it, it is learned from the upper
  envelope of actual/expected per cell-temperature bin, only from satellite-confirmed clear hours,
  shrunk towards a typical value and rejected when implausible; otherwise the typical value is used.
  Heating by irradiance and cooling by wind are not identifiable from hourly data and use a
  fixed typical heating.
- T: beam transmission (1 = unshaded) per sun-position cell, learned from hours where direct
  light is geometrically possible. The 90th percentile per cell represents clear conditions;
  clouds and occasional curtailment fall below it. Optionally, satellite irradiance pre-selects
  clear hours.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .solar import Site, clearsky_simplified_solis, erbs, extra_radiation, plane_of_array, sun_position

AZ_STEP = 10.0
EL_STEP = 5.0
SUBSTEPS = 6                     # 10-minute sub-steps per hour
MIN_BEAM_W = 60.0                # expected beam power (W) needed to observe shading
MIN_CELL_SAMPLES = 4
RECENT_DAYS = 60                 # window for change detection per cell
CHANGE_THRESHOLD = 0.25
DEFAULT_EFFICIENCY = 0.9
HALF_LIFE_DAYS = 365.0

TEMP_REF = 25.0
HEATING_K = 30.0                 # cell heating in K at 1000 W/m² on the plane
DEFAULT_TEMP_COEFF = -0.0035     # per K, typical crystalline module
TEMP_COEFF_PLAUSIBLE = (-0.005, -0.002)   # learned values outside are rejected
TEMP_BIN_K = 5.0
TEMP_MIN_BIN_SAMPLES = 15
TEMP_FULL_TRUST_BINS = 8         # bins needed to trust the learned coefficient completely
FALLBACK_AIR_TEMP = 15.0         # when a forecast has no temperature
CLEAR_KT = 0.8                   # satellite clearness index for a clear hour
MIN_CLEAR_COVERAGE = 0.3         # use the satellite filter only if it covers this share of samples


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
    temp: float | None = None    # air temperature °C


@dataclass
class LearnedArray:
    efficiency: float = DEFAULT_EFFICIENCY                     # at 25 °C cell temperature
    temp_coeff: float = DEFAULT_TEMP_COEFF                     # per K
    temp_learned: bool = False
    temp_source: str = "default"                               # datasheet | learned | default | none
    cells: dict[str, float] = field(default_factory=dict)      # "az_idx,el_idx" -> transmission
    coverage: float = 0.0                                      # share of reachable cells learned
    samples: int = 0
    clear_filter: bool = False                                 # satellite pre-filter was applied
    agreement: float | None = None                             # correlation satellite vs. installation
    first_sample: float | None = None
    learned_at: float | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> LearnedArray:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


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


def _quantile(values: list[float], q: float) -> float:
    return weighted_quantile(values, [1.0] * len(values), q)


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 20:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def temperature_factor(temp_coeff: float, air_temp: float | None, poa_w: float) -> float:
    """Output factor relative to 25 °C cell temperature."""
    t_air = FALLBACK_AIR_TEMP if air_temp is None else air_temp
    t_cell = t_air + HEATING_K * poa_w / 1000.0
    return max(1.0 + temp_coeff * (t_cell - TEMP_REF), 0.5)


def clearsky_hour(site: Site, geo: ArrayGeometry, start: float) -> tuple[float, float, float, float, float]:
    """Hourly means of clear-sky (beam, diffuse) on the plane, mean sun (azimuth, elevation) and GHI.

    The sun position is averaged over the sub-steps where the sun is up, so an hourly mean value
    belongs to the sun path within that hour, not to the hour start.
    """
    beam = diff = ghi_sum = 0.0
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
        ghi_sum += ghi
        sx += math.sin(math.radians(sun.azimuth))
        sy += math.cos(math.radians(sun.azimuth))
        el_sum += sun.elevation
        up += 1
    if not up:
        return 0.0, 0.0, 0.0, -1.0, 0.0
    az = math.degrees(math.atan2(sx, sy)) % 360.0
    return beam / SUBSTEPS, diff / SUBSTEPS, az, el_sum / up, ghi_sum / SUBSTEPS


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


def _learn_temp_coeff(rows: list[tuple[float, float, float]]) -> tuple[float, bool]:
    """(cell temperature, ratio, _) rows -> temperature coefficient from the upper envelope per bin."""
    bins: dict[int, list[float]] = {}
    for t_cell, ratio, _ in rows:
        bins.setdefault(int(t_cell // TEMP_BIN_K), []).append(ratio)
    env = [((b + 0.5) * TEMP_BIN_K, _quantile(v, 0.9)) for b, v in sorted(bins.items()) if len(v) >= TEMP_MIN_BIN_SAMPLES]
    if len(env) < 4 or env[-1][0] - env[0][0] < 15:
        return DEFAULT_TEMP_COEFF, False
    xs, ys = [x for x, _ in env], [y for _, y in env]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    coeff = slope / max(ys)
    trust = min(1.0, len(env) / TEMP_FULL_TRUST_BINS)
    coeff = trust * coeff + (1 - trust) * DEFAULT_TEMP_COEFF
    if not TEMP_COEFF_PLAUSIBLE[0] <= coeff <= TEMP_COEFF_PLAUSIBLE[1]:
        return DEFAULT_TEMP_COEFF, False                 # irradiance-dependent model errors, not temperature
    return coeff, True


def learn(site: Site, geo: ArrayGeometry, samples: list[tuple[float, float]], now: float, *,
          air_temp: dict[float, float] | None = None, satellite_ghi: dict[float, float] | None = None,
          datasheet_temp_coeff: float | None = None, half_life_days: float = HALF_LIFE_DAYS) -> LearnedArray:
    """Learn efficiency, temperature coefficient and shading map from (hour_start, mean power W) samples.

    air_temp: air temperature (°C) at the middle of each hour.
    satellite_ghi: measured hourly mean GHI (W/m²) used to pre-select clear hours (optional).
    datasheet_temp_coeff: Pmax temperature coefficient per K; used as fixed value instead of learning it.
      Learning over cell-temperature bins also picks up irradiance-dependent model errors, so a known
      datasheet value is more robust.
    """
    rows = []
    for t, p in samples:
        beam, diff, az, el, cs_ghi = clearsky_hour(site, geo, t)
        if el <= 0:
            continue
        age = max(now - t, 0) / 86400.0
        kt = None
        if satellite_ghi is not None and satellite_ghi.get(t) is not None and cs_ghi > 50:
            kt = satellite_ghi[t] / cs_ghi
        temp = air_temp.get(t) if air_temp else None
        rows.append((t, p, beam, diff, az, el, 0.5 ** (age / half_life_days), age, kt, temp))
    if not rows:
        return LearnedArray(learned_at=now)

    # optional satellite pre-filter: only clear hours are used for efficiency and shading
    with_kt = [r for r in rows if r[8] is not None]
    clear_filter = satellite_ghi is not None and len(with_kt) >= MIN_CLEAR_COVERAGE * len(rows)
    basis = [r for r in with_kt if r[8] >= CLEAR_KT] if clear_filter else rows
    if len(basis) < 50:                                  # too few clear hours: fall back to all hours
        basis, clear_filter = rows, False

    # With the satellite filter, expectations are scaled to the measured irradiance (clearness kt) and
    # medians are used: clouds are already removed, so a high percentile would only pick satellite errors.
    # Without it, high percentiles separate clear conditions from clouds and occasional curtailment.
    def scale(r) -> float:
        return min(r[8], 1.2) if clear_filter else 1.0

    exp = {r[0]: geo.peak_w * (r[2] + r[3]) / 1000 * scale(r) for r in basis}
    limit = 0.4 * max(exp.values())
    strong = [r for r in basis if exp[r[0]] > limit and r[1] / exp[r[0]] <= 1.5]

    # temperature coefficient from the upper envelope per cell-temperature bin
    temp_rows = [(r[9] + HEATING_K * (r[2] + r[3]) / 1000, r[1] / exp[r[0]], r[6]) for r in strong if r[9] is not None]
    if datasheet_temp_coeff is not None:
        temp_coeff, temp_learned, temp_source = datasheet_temp_coeff, False, "datasheet"
    elif clear_filter:
        # only clear hours: with cloudy hours, summer clouds and winter sun share temperature bins
        temp_coeff, temp_learned = _learn_temp_coeff(temp_rows)
        temp_source = "learned" if temp_learned else "default"
    else:
        temp_coeff, temp_learned, temp_source = DEFAULT_TEMP_COEFF, False, "default"
    if not temp_rows:                                    # no temperature history: efficiency absorbs it
        temp_coeff, temp_source = 0.0, "none"

    def f_temp(r) -> float:
        return temperature_factor(temp_coeff, r[9], r[2] + r[3]) if r[9] is not None else 1.0

    # efficiency at 25 °C from strong expected hours
    def eff_quantile(source, q: float) -> float:
        ratios = [(r[1] / (exp[r[0]] * f_temp(r)), r[6]) for r in source]
        return weighted_quantile([v for v, _ in ratios], [w for _, w in ratios], q) if ratios else DEFAULT_EFFICIENCY

    efficiency = eff_quantile(strong, 0.95)

    # beam transmission per sun-position cell: all hours (P90), refined by clear hours (median) where enough exist
    def observe(source, scaled: bool) -> dict[tuple[int, int], list[tuple[float, float, float]]]:
        obs: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        for r in source:
            t, p, beam, diff, az, el, weight, age = r[:8]
            k = scale(r) if scaled else 1.0
            beam_w = geo.peak_w * beam / 1000 * k
            if beam_w <= MIN_BEAM_W:
                continue
            tr = (p / (efficiency * f_temp(r)) - geo.peak_w * diff / 1000 * k) / beam_w
            obs.setdefault(_cell(az, el), []).append((min(max(tr, -0.2), 1.3), weight, age))
        return obs

    def cell_values(obs, q: float) -> dict[str, float]:
        out = {}
        for (ai, ei), items in obs.items():
            if len(items) < MIN_CELL_SAMPLES:
                continue
            value = weighted_quantile([v for v, _, _ in items], [w for _, w, _ in items], q)
            recent = [v for v, _, a in items if a <= RECENT_DAYS]
            if len(recent) >= 3:
                recent_value = _quantile(recent, q)
                if abs(recent_value - value) > CHANGE_THRESHOLD:     # e.g. a tree was cut or a building added
                    value = recent_value
            out[_key(ai, ei)] = round(min(max(value, 0.0), 1.0), 3)
        return out

    cells = cell_values(observe(rows, scaled=False), 0.9)
    if clear_filter:
        # median over clear hours whose sun position is unshaded; the median does not ignore shadows
        probe = LearnedArray(cells=cells)
        unshaded = [r for r in strong if transmission(probe, r[4], r[5]) >= 0.95]
        if len(unshaded) >= 50:
            efficiency = eff_quantile(unshaded, 0.5)
            cells = cell_values(observe(rows, scaled=False), 0.9)
        cells.update(cell_values(observe(basis, scaled=True), 0.5))

    learned = LearnedArray(efficiency=round(efficiency, 4), temp_coeff=round(temp_coeff, 5), temp_learned=temp_learned,
                           temp_source=temp_source,
                           cells=cells, samples=len(rows), clear_filter=clear_filter,
                           first_sample=rows[0][0], learned_at=now)

    # agreement between satellite clearness and the installation's own clearness (unshaded hours)
    if with_kt:
        xs, ys = [], []
        for r in with_kt:
            t, p, beam, diff, az, el = r[:6]
            e = geo.peak_w * (beam + diff) / 1000
            if el < 15 or geo.peak_w * beam / 1000 < 150 or transmission(learned, az, el) < 0.95:
                continue
            xs.append(min(r[8], 1.3))
            ys.append(min(p / (efficiency * f_temp(r) * e), 1.3))
        corr = _correlation(xs, ys)
        learned.agreement = round(corr, 3) if corr is not None else None

    reachable = reachable_cells(site, geo)
    learned.coverage = round(len(reachable & {tuple(map(int, k.split(","))) for k in cells}) / len(reachable), 3) \
        if reachable else 0.0
    return learned


def reachable_cells(site: Site, geo: ArrayGeometry, year_start: float = 1767225600.0) -> set[tuple[int, int]]:
    """Cells that hourly samples can fill within a year (same criteria as learning)."""
    visits: dict[tuple[int, int], int] = {}
    for k in range(365 * 24):
        beam, _, az, el, _ = clearsky_hour(site, geo, year_start + k * 3600)
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
            poa = beam * transmission(learned, sun.azimuth, sun.elevation) + diff
            total += geo.peak_w * learned.efficiency * temperature_factor(learned.temp_coeff, wh.temp, poa) * poa / 1000
        out[wh.start] = total / SUBSTEPS
    return out
