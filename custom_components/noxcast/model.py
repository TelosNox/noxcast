"""Learning and forecasting for one PV array (pure Python, no Home Assistant imports).

Model:  P = peak_w · efficiency₂₅ · f_temp · (beam_poa · T(sun) · D(sun) + diffuse_poa(S) + R(sun) · DNI) / 1000

- efficiency₂₅: system efficiency at 25 °C cell temperature, learned from hours with strong
  expected output.
- f_temp = 1 + temp_coeff · (T_cell − 25 °C), T_cell = T_air + heating · POA / 1000 W/m².
  The temperature coefficient comes from the datasheet. Without it, it is learned from the upper
  envelope of actual/expected per cell-temperature bin, only from satellite-confirmed clear hours,
  shrunk towards a typical value and rejected when implausible; otherwise the typical value is used.
  The heating at 1000 W/m² depends on the mounting (free behind about 30 K, close to a roof
  clearly more) and is configured per array; hourly data do not separate it reliably from
  seasonal effects.
- T: beam transmission (1 = unshaded), a contour per 10° azimuth column: a lower edge (ground
  obstacles, shaded below) and an upper edge (overhang, shaded above), each with the transmission
  of its shaded segment; learned from hours where direct light is geometrically possible. A free
  value per cell cannot be separated from hourly means (one hour spans ~15° of azimuth at noon)
  and produced zigzag artefacts; an edge has the shape of a real obstacle, and is only introduced
  where it clearly beats free sky. Per-cell estimates (90th percentile, or the clear-hour median
  with the satellite filter) remain for the efficiency and as data for the contour without steady
  hours.
- D: direction factor (may exceed 1) on coarse bins, the median of what remains at unshaded
  positions on clear hours: geometry and model errors that are not shade.
  Efficiency, D, R and the clear-hour refinement of T use only steady hours when the power
  sensors' hourly minimum and maximum are known: a satellite pixel can call an hour clear while
  clouds pass over the installation, and such hours (cloud-edge peaks cut at the inverter limit,
  restarts, dropouts) only add noise. With several sensors per array one steady sensor is enough
  (a cloud reaches all of them, a shadow edge not), and sensors far apart mark a partial shadow.
  On steady clear hours the contour is fitted so that the forecast, integrated over the sub-steps
  of each hour, meets the hourly means: an edge crossed within the hour is then placed by the
  shifting sun path over many days instead of being smeared over the hour.
- R: reflected light per coarse sun-position bin, relative to the direct normal irradiance: sunlit
  walls or roofs in front of the module. It does not depend on how the sun meets the module, so
  it is learned where the module itself gets little direct light; elsewhere it cannot be told
  apart from D. Only additive, never negative.
  D and R are learned against the measured irradiance split as in the forecast.
- Several orientations in one array (modules facing different ways on the same power sensors):
  beam_poa and diffuse_poa are the peak-weighted sums over the planes, each plane clipped at zero
  on its own; T, D, R and S stay per array (see array_irradiance and docs/concept.md).
- S: sky view, the share of the isotropic sky diffuse the plane sees (houses opposite, an overhang
  above). Learned from overcast hours, which carry almost only diffuse light; the other parameters
  are learned again with it, since clear hours contain diffuse light too.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .solar import (
    Site, SunPosition, clearsky_simplified_solis, erbs, extra_radiation, plane_of_array, sun_position,
)

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
HEATING_K = 30.0                 # cell heating in K at 1000 W/m² on the plane, free behind (default)
DEFAULT_TEMP_COEFF = -0.0035     # per K, typical crystalline module
TEMP_COEFF_PLAUSIBLE = (-0.005, -0.002)   # learned values outside are rejected
TEMP_BIN_K = 5.0
TEMP_MIN_BIN_SAMPLES = 15
TEMP_FULL_TRUST_BINS = 8         # bins needed to trust the learned coefficient completely
FALLBACK_AIR_TEMP = 15.0         # when a forecast has no temperature
CLEAR_KT = 0.8                   # satellite clearness index for a clear hour
MIN_CLEAR_COVERAGE = 0.3         # use the satellite filter only if it covers this share of samples
MIN_UNSHADED_HOURS = 20          # clear hours at unshaded positions needed for the efficiency median
STEADY_BAND = 0.15               # steady hour: a sensor's minimum and maximum within ±15 % of its mean
SPLIT_RATIO = 0.6                # sensors of one array this far apart (relative to their peaks): a shadow on part of it
SPLIT_MIN_SHARE = 0.05           # ... above this share of the sensor's peak (low light is noise)
MIN_STEADY_HOURS = 30            # steady clear hours needed to learn efficiency, D and R from them alone
SHADE_CORE = 0.7                 # below: shade (temperature, haze and model errors do not reach this far)
SHADE_FREE = 0.9                 # from here: free (dead band for temperature, haze and model errors)
SHADE_NEAR_CELLS = 2             # 0.7-0.9 is shade only with a core within this many cells
SHADE_FIT_BEAM_SHARE = 0.7       # ... hours whose expected light is mostly direct (else diffuse-model errors enter)
CONTOUR_SOFT = 1.0               # half width (°) of a contour edge: keeps the sub-step integration smooth
CONTOUR_STEP = 0.5               # search step (°) of an edge elevation
CONTOUR_MIN_HOURS = 6            # hours with a real share of their beam in a column, to decide on it
CONTOUR_PENALTY = 25.0           # an edge must lower the robust squared error by this many noise variances ...
CONTOUR_MIN_GAIN = 0.2           # ... and by this share of the column's error without it
CONTOUR_MIN_FREE = 0.05          # beam share in the free band needed for an edge of 0.7-0.9 (else: not shade)
CONTOUR_SWEEPS = 6
CONTOUR_ROUNDS = 3               # robust reweighting rounds
SHADING_VERSION = 2              # 1: free cells (stored before the contours), 2: contour per azimuth column
DIRECTION_AZ_STEP = 20.0         # coarse bins of the direction factor
DIRECTION_EL_STEP = 15.0
DIRECTION_MIN_SAMPLES = 8
DIRECTION_LIMITS = (0.8, 1.2)
REFLECTION_MAX_BEAM_SHARE = 0.3  # learn reflection only where beam on the plane is below this share of DNI
REFLECTION_MIN_DNI = 100.0       # W/m²
REFLECTION_LIMIT = 0.3           # W/m² on the plane per W/m² DNI
OVERCAST_KT = 0.4                # satellite clearness below this: almost only diffuse light
SKY_VIEW_MIN_CLEARSKY = 100.0    # W/m² clear-sky GHI, so that overcast hours carry a usable signal
SKY_VIEW_MIN_HOURS = 30
SKY_VIEW_LIMITS = (0.2, 1.0)
SKY_VIEW_ITERATIONS = 4


@dataclass(frozen=True)
class Plane:
    """A further module orientation within one array (modules whose power is measured together)."""
    tilt: float
    azimuth: float
    peak_w: float


@dataclass(frozen=True)
class ArrayGeometry:
    tilt: float                  # main plane
    azimuth: float
    peak_w: float                # the whole array, all planes together
    heating: float = HEATING_K   # cell heating in K at 1000 W/m², depends on the mounting
    extra: tuple[Plane, ...] = ()  # further orientations; the main plane has peak_w minus their peak

    @property
    def planes(self) -> tuple[tuple[float, float, float], ...]:
        """(tilt, azimuth, share of peak_w) per plane, the main plane first."""
        if not self.extra:
            return ((self.tilt, self.azimuth, 1.0),)
        rest = sum(p.peak_w for p in self.extra)
        return ((self.tilt, self.azimuth, (self.peak_w - rest) / self.peak_w),
                *((p.tilt, p.azimuth, p.peak_w / self.peak_w) for p in self.extra))

    def describe(self) -> list[dict]:
        """Every plane with tilt, azimuth and peak power, the main plane first (panel, diagnostics)."""
        main = self.peak_w - sum(p.peak_w for p in self.extra)
        return [{"tilt": self.tilt, "azimuth": self.azimuth, "peak_w": main},
                *({"tilt": p.tilt, "azimuth": p.azimuth, "peak_w": p.peak_w} for p in self.extra)]


@dataclass(frozen=True)
class WeatherHour:
    start: float                 # UNIX seconds, start of the hour
    ghi: float                   # mean global horizontal irradiance over the hour, W/m²
    dni: float | None = None     # optional; split from GHI (Erbs) when missing
    dhi: float | None = None
    temp: float | None = None    # air temperature °C
    # archived for evaluating sources, not used by the model:
    cloud: float | None = None   # total cloud cover %, middle of the hour
    sun: float | None = None     # sunshine share of the hour, 0-1
    wind: float | None = None    # wind speed m/s (10 m), middle of the hour
    wind_dir: float | None = None  # wind direction ° (from), middle of the hour
    # for the weather entity only:
    precip: float | None = None  # precipitation in the hour, mm
    code: int | None = None      # WMO weather code of the hour (thunderstorm and fog cannot be averaged)
    humidity: float | None = None  # relative humidity %, middle of the hour


@dataclass
class LearnedArray:
    efficiency: float = DEFAULT_EFFICIENCY                     # at 25 °C cell temperature
    temp_coeff: float = DEFAULT_TEMP_COEFF                     # per K
    temp_learned: bool = False
    temp_source: str = "default"                               # datasheet | learned | default | none
    cells: dict[str, float] = field(default_factory=dict)      # "az_idx,el_idx" -> transmission (version 2: display)
    contour: dict[str, dict] = field(default_factory=dict)     # "az_idx" -> edges of the column, see transmission()
    shading_version: int = 1                                   # 1: cells are the shading map, 2: the contour is
    direction: dict[str, float] = field(default_factory=dict)  # coarse "az_idx,el_idx" -> beam factor (non-shade)
    sky_view: float = 1.0                                      # share of the isotropic sky diffuse seen
    reflection: dict[str, float] = field(default_factory=dict) # coarse "az_idx,el_idx" -> plane W/m² per W/m² DNI
    coverage: float = 0.0                                      # share of reachable cells with an edge decision
    samples: int = 0
    clear_filter: bool = False                                 # satellite pre-filter was applied
    steady_filter: bool = False                                # only steady hours for efficiency, D and R
    agreement: float | None = None                             # correlation satellite vs. installation
    first_sample: float | None = None
    learned_at: float | None = None
    # explanations for the panel, not used by the forecast (arraycheck.py)
    months: list[dict] = field(default_factory=list)           # clear-sky balance per month
    efficiency_months: dict[str, list] = field(default_factory=dict)   # "YYYY-MM" -> [efficiency, hours]
    geometry_check: dict | None = None                         # best fitting tilt / azimuth

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


def steady_hours(series: list[tuple[dict[float, float | None], dict[float, float | None],
                                    dict[float, float | None]]]) -> set[float]:
    """Hours with a calm sky over the array: [(mean, min, max) per sensor] -> hour starts.

    From the hourly minimum and maximum of long-term statistics. Passing clouds, a peak cut at the
    inverter limit, a restart or a dropout show as a minimum or maximum far from the mean. With
    several sensors (modules or inputs of one array) an hour counts when at least one of them was
    steady – a cloud reaches all of them, a shadow edge moving over one input does not – or when
    the sensors are far apart relative to their peaks: one part shaded, the other not. Such hours
    are what the shading map needs, and a steady test on the sum would drop them.
    """
    if not series:
        return set()
    present = set.intersection(*({t for t, m in mean.items() if m is not None and m > 0} for mean, _, _ in series))
    hours: set[float] = set()
    for mean, low, high in series:
        hours |= {t for t in present if low.get(t) is not None and high.get(t) is not None
                  and low[t] >= (1 - STEADY_BAND) * mean[t] and high[t] <= (1 + STEADY_BAND) * mean[t]}
    if len(series) > 1:
        peaks = [_quantile([mean[t] for t in present], 0.95) if present else 0.0 for mean, _, _ in series]
        if all(peaks):
            for t in present:
                shares = [mean[t] / peak for (mean, _, _), peak in zip(series, peaks)]
                if max(shares) >= SPLIT_MIN_SHARE and min(shares) < SPLIT_RATIO * max(shares):
                    hours.add(t)
    return hours


def temperature_factor(temp_coeff: float, air_temp: float | None, poa_w: float, heating: float = HEATING_K) -> float:
    """Output factor relative to 25 °C cell temperature; heating: cell heating in K at 1000 W/m²."""
    t_air = FALLBACK_AIR_TEMP if air_temp is None else air_temp
    t_cell = t_air + heating * poa_w / 1000.0
    return max(1.0 + temp_coeff * (t_cell - TEMP_REF), 0.5)


def array_irradiance(geo: ArrayGeometry, sun: SunPosition, dni: float, dhi: float, ghi: float,
                     dni_extra: float = 1367.0, sky_view: float = 1.0) -> tuple[float, float, float]:
    """Effective (beam, diffuse) irradiance on the array's modules in W/m² of its peak power, and the heating.

    With several orientations each plane is computed on its own and the results are weighted by
    the plane's share of the peak power. Each plane clips at zero by itself: a sun behind one
    module gives that module no beam, it does not take any from the others. This is what the
    equivalent plane (normalised sum of module normals) cannot do, see docs/concept.md.

    The heating (K at 1000 W/m²) is that of the mounting for one plane. With several planes each
    module heats with its own irradiance, and since the temperature factor is linear in it, the sum
    over planes of share · f(POA_i) · POA_i equals f(POA) · POA of the weighted mean POA when the
    heating is scaled by Σ share · POA_i² / POA². The planes' shares of the irradiance come from
    the model without shade, direction factor and reflection (learned per array, not per plane).
    """
    if not geo.extra:
        beam, diff = plane_of_array(geo.tilt, geo.azimuth, sun, dni, dhi, ghi, dni_extra, sky_view=sky_view)
        return beam, diff, geo.heating
    beam = diff = square = 0.0
    for tilt, azimuth, share in geo.planes:
        b, d = plane_of_array(tilt, azimuth, sun, dni, dhi, ghi, dni_extra, sky_view=sky_view)
        beam += share * b
        diff += share * d
        square += share * (b + d) ** 2
    poa = beam + diff
    return beam, diff, geo.heating * square / (poa * poa) if poa > 0 else geo.heating


def clearsky_heating(site: Site, geo: ArrayGeometry, start: float, sky_view: float = 1.0) -> float:
    """Heating (K at 1000 W/m²) for the hourly mean clear-sky irradiance, see array_irradiance."""
    if not geo.extra:
        return geo.heating
    acc = _Heating(geo)
    dni_extra = extra_radiation(start + 1800)
    for i in range(SUBSTEPS):
        sun = sun_position(site, start + (i + 0.5) * 3600 / SUBSTEPS)
        if sun.elevation <= 0:
            continue
        ghi, dni, dhi = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
        acc.add(*array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=sky_view))
    return acc.value


class _Heating:
    """Hourly heating from sub-steps: weighted by the squared irradiance, as its effect on the output."""

    def __init__(self, geo: ArrayGeometry) -> None:
        self.default = geo.heating
        self.planes = bool(geo.extra)                    # one plane: the mounting's heating, unchanged
        self.num = self.den = 0.0

    def add(self, beam: float, diff: float, heating: float) -> None:
        if not self.planes:
            return
        square = (beam + diff) ** 2
        self.num += heating * square
        self.den += square

    @property
    def value(self) -> float:
        return self.num / self.den if self.den > 0 else self.default


def clearsky_hour(site: Site, geo: ArrayGeometry, start: float,
                  sky_view: float = 1.0) -> tuple[float, float, float, float, float]:
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
        b, d, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=sky_view)
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
          datasheet_temp_coeff: float | None = None, half_life_days: float = HALF_LIFE_DAYS,
          steady: set[float] | None = None) -> LearnedArray:
    """Learn efficiency, temperature coefficient, shading map and sky view from (hour_start, mean power W) samples.

    air_temp: air temperature (°C) at the middle of each hour.
    satellite_ghi: measured hourly mean GHI (W/m²) used to pre-select clear hours (optional).
    datasheet_temp_coeff: Pmax temperature coefficient per K; used as fixed value instead of learning it.
      Learning over cell-temperature bins also picks up irradiance-dependent model errors, so a known
      datasheet value is more robust.
    steady: hour starts in which the power stayed steady (see steady_hours); efficiency, direction
      factor and reflection then use only those of the clear hours.

    The sky view needs satellite irradiance. It and the other parameters depend on each other, so
    they are learned alternately until the sky view settles.
    """
    args = dict(air_temp=air_temp, satellite_ghi=satellite_ghi, datasheet_temp_coeff=datasheet_temp_coeff,
                half_life_days=half_life_days, steady=steady)
    learned = _learn(site, geo, samples, now, sky_view=1.0, **args)
    if satellite_ghi is None:
        return learned
    for _ in range(SKY_VIEW_ITERATIONS):
        sky = learn_sky_view(site, geo, learned, samples, satellite_ghi, air_temp)
        if sky is None or abs(sky - learned.sky_view) < 0.01:
            break
        learned = _learn(site, geo, samples, now, sky_view=sky, **args)
    return learned


def learn_sky_view(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                   satellite_ghi: dict[float, float], air_temp: dict[float, float] | None = None) -> float | None:
    """Share of the isotropic sky diffuse the plane sees, from overcast hours (None without enough of them).

    What the installation produced beyond beam and ground reflection, relative to the sky diffuse a
    free plane would get from the measured irradiance.
    """
    seen = free = 0.0
    hours = 0
    for t, p in samples:
        ghi = satellite_ghi.get(t)
        cs_ghi = clearsky_hour(site, geo, t)[4]
        if ghi is None or cs_ghi < SKY_VIEW_MIN_CLEARSKY or ghi / cs_ghi >= OVERCAST_KT:
            continue
        dni_extra = extra_radiation(t + 1800)
        rest = sky = 0.0
        heat = _Heating(geo)
        for i in range(SUBSTEPS):
            sun = sun_position(site, t + (i + 0.5) * 3600 / SUBSTEPS)
            if sun.elevation <= 0:
                continue
            dni, dhi = erbs(ghi, sun.zenith, dni_extra)
            beam, ground, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=0.0)
            _, diff, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra)
            if geo.extra:
                heat.add(*array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view))
            rest += beam * transmission(learned, sun.azimuth, sun.elevation) * direction_factor(
                learned, sun.azimuth, sun.elevation) + ground + reflection_factor(
                learned, sun.azimuth, sun.elevation) * dni
            sky += diff - ground
        rest, sky = rest / SUBSTEPS, sky / SUBSTEPS
        temp = air_temp.get(t) if air_temp else None
        f = temperature_factor(learned.temp_coeff, temp, rest + sky * learned.sky_view, heat.value) \
            if temp is not None else 1.0
        seen += p * 1000 / (geo.peak_w * learned.efficiency * f) - rest
        free += sky
        hours += 1
    if hours < SKY_VIEW_MIN_HOURS or free <= 0:
        return None
    return round(min(max(seen / free, SKY_VIEW_LIMITS[0]), SKY_VIEW_LIMITS[1]), 3)


def _learn(site: Site, geo: ArrayGeometry, samples: list[tuple[float, float]], now: float, *,
           air_temp: dict[float, float] | None, satellite_ghi: dict[float, float] | None,
           datasheet_temp_coeff: float | None, half_life_days: float, sky_view: float,
           steady: set[float] | None = None) -> LearnedArray:
    rows = []
    for t, p in samples:
        beam, diff, az, el, cs_ghi = clearsky_hour(site, geo, t, sky_view)
        if el <= 0:
            continue
        age = max(now - t, 0) / 86400.0
        kt = None
        if satellite_ghi is not None and satellite_ghi.get(t) is not None and cs_ghi > 50:
            kt = satellite_ghi[t] / cs_ghi
        temp = air_temp.get(t) if air_temp else None
        heating = clearsky_heating(site, geo, t, sky_view) if temp is not None else geo.heating
        rows.append((t, p, beam, diff, az, el, 0.5 ** (age / half_life_days), age, kt, temp, heating))
    if not rows:
        return LearnedArray(sky_view=sky_view, learned_at=now, shading_version=SHADING_VERSION)

    # optional satellite pre-filter: only clear hours are used for efficiency and shading
    with_kt = [r for r in rows if r[8] is not None]
    clear_filter = satellite_ghi is not None and len(with_kt) >= MIN_CLEAR_COVERAGE * len(rows)
    basis = [r for r in with_kt if r[8] >= CLEAR_KT] if clear_filter else rows
    if len(basis) < 50:                                  # too few clear hours: fall back to all hours
        basis, clear_filter = rows, False
    # steady hours only, when there are enough of them: the satellite may call an hour clear while
    # clouds pass over the installation
    calm = [r for r in basis if r[0] in steady] if clear_filter and steady is not None else []
    steady_filter = len(calm) >= MIN_STEADY_HOURS
    if not steady_filter:
        calm = basis

    # With the satellite filter, expectations are scaled to the measured irradiance (clearness kt) and
    # medians are used: clouds are already removed, so a high percentile would only pick satellite errors.
    # Without it, high percentiles separate clear conditions from clouds and occasional curtailment.
    def scale(r) -> float:
        return min(r[8], 1.2) if clear_filter else 1.0

    exp = {r[0]: geo.peak_w * (r[2] + r[3]) / 1000 * scale(r) for r in basis}
    limit = 0.4 * max(exp.values())
    strong = [r for r in basis if exp[r[0]] > limit and r[1] / exp[r[0]] <= 1.5]

    # temperature coefficient from the upper envelope per cell-temperature bin
    temp_rows = [(r[9] + r[10] * (r[2] + r[3]) / 1000, r[1] / exp[r[0]], r[6])
                 for r in strong if r[9] is not None]
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
        return temperature_factor(temp_coeff, r[9], r[2] + r[3], r[10]) if r[9] is not None else 1.0

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
        # median over clear hours whose sun position is unshaded; the median does not ignore shadows.
        # With little data (new installation) all clear hours: shadows pull it slightly low, the safe side,
        # while a high percentile over clear hours would only pick satellite errors.
        probe = LearnedArray(cells=cells)
        unshaded = [r for r in strong if transmission(probe, r[4], r[5]) >= 0.95]
        steady_unshaded = [r for r in unshaded if r[0] in steady] if steady_filter else []
        if len(steady_unshaded) >= MIN_UNSHADED_HOURS:
            unshaded = steady_unshaded
        source = unshaded if len(unshaded) >= MIN_UNSHADED_HOURS else strong
        if source:
            efficiency = eff_quantile(source, 0.5)
            cells = cell_values(observe(rows, scaled=False), 0.9)
        cells.update(cell_values(observe(calm, scaled=True), 0.5))

    # The contour: fitted to the shaped cell estimates; with steady clear hours fitted so that the
    # forecast, integrated over the sub-steps, meets the hourly means, the cell estimates completing
    # it where the hours cannot tell (sun nearly behind the plane)
    eqs = _contour_equations(site, geo, calm, lambda r: efficiency * f_temp(r), satellite_ghi, sky_view) \
        if steady_filter else []
    contour = _fit_contour(_cell_equations(shape_cells(cells)), 2)
    if len(eqs) >= MIN_STEADY_HOURS:
        contour = _fit_contour(eqs, CONTOUR_MIN_HOURS, contour)
    shading = LearnedArray(contour=contour, shading_version=SHADING_VERSION, sky_view=sky_view)
    direction: dict[str, float] = {}
    reflection: dict[str, float] = {}
    if clear_filter:
        # what remains at unshaded positions on clear hours is not shade; kept apart from the shading
        # map, smooth and coarse. Modelled with the measured irradiance split as in the forecast.
        probe = shading
        parts = []                                       # (bin, plane-equivalent observed, beam, diffuse, dni)
        for r in calm:
            t, p, _, _, az, el = r[:6]
            if transmission(probe, az, el) < 0.999:
                continue
            beam, diff, dni, heating = _measured_parts(site, geo, probe, t, satellite_ghi[t])
            if dni < REFLECTION_MIN_DNI:
                continue
            f = temperature_factor(temp_coeff, r[9], beam + diff, heating) if r[9] is not None else 1.0
            observed = p * 1000 / (geo.peak_w * efficiency * f)
            parts.append(((int(az // DIRECTION_AZ_STEP), int(el // DIRECTION_EL_STEP)), observed, beam, diff, dni))

        # reflected light where the module itself gets little direct light: sunlit walls in front
        refl_obs: dict[tuple[int, int], list[float]] = {}
        for b, observed, beam, diff, dni in parts:
            if beam < REFLECTION_MAX_BEAM_SHARE * dni:
                refl_obs.setdefault(b, []).append((observed - beam - diff) / dni)
        refl_bins = {b: min(max(_quantile(v, 0.5), 0.0), REFLECTION_LIMIT) for b, v in refl_obs.items()
                     if len(v) >= DIRECTION_MIN_SAMPLES}
        reflection = {_key(*b): round(v, 3) for b, v in refl_bins.items() if v >= 0.005}

        # the rest on the beam part, in both directions: geometry and model errors
        dir_obs: dict[tuple[int, int], list[float]] = {}
        for b, observed, beam, diff, dni in parts:
            if geo.peak_w * beam / 1000 <= MIN_BEAM_W:
                continue
            dir_obs.setdefault(b, []).append((observed - diff - refl_bins.get(b, 0.0) * dni) / beam)
        direction = {_key(*b): round(min(max(_quantile(v, 0.5), DIRECTION_LIMITS[0]), DIRECTION_LIMITS[1]), 3)
                     for b, v in dir_obs.items() if len(v) >= DIRECTION_MIN_SAMPLES}
    reachable = reachable_cells(site, geo)
    cells = display_cells(shading, reachable)
    learned = LearnedArray(efficiency=round(efficiency, 4), temp_coeff=round(temp_coeff, 5), temp_learned=temp_learned,
                           temp_source=temp_source,
                           cells=cells, contour=contour, shading_version=SHADING_VERSION,
                           direction=direction, reflection=reflection, sky_view=sky_view,
                           samples=len(rows), clear_filter=clear_filter, steady_filter=steady_filter,
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

    learned.coverage = contour_coverage(contour, reachable)
    return learned


def _column_weights(azimuth: float) -> list[tuple[int, float]]:
    """Azimuth columns and weights of the linear interpolation between column centres in transmission()."""
    fa = azimuth / AZ_STEP - 0.5
    a0 = math.floor(fa)
    da = fa - a0
    return [(ai % 36, w) for ai, w in ((a0, 1 - da), (a0 + 1, da)) if w > 0]


def _ramp(x: float) -> float:
    """Shaded share x degrees inside an edge, soft over ±CONTOUR_SOFT."""
    return min(max((x + CONTOUR_SOFT) / (2 * CONTOUR_SOFT), 0.0), 1.0)


def column_transmission(column: dict, elevation: float) -> float:
    """One azimuth column: its segment's transmission below the lower or above the upper edge, else 1."""
    value = 1.0
    if "low" in column:
        value -= (1 - column["t_low"]) * _ramp(column["low"] - elevation)
    if "high" in column:
        value -= (1 - column["t_high"]) * _ramp(elevation - column["high"])
    return value


def _has_edge(column: dict | None) -> bool:
    return bool(column) and ("low" in column or "high" in column)


def _contour_value(contour: dict[str, dict], ai: int, elevation: float) -> float | None:
    """A column's transmission; a column without a decision between two with an edge takes their mean, else None."""
    column = contour.get(str(ai))
    if column is not None:
        return column_transmission(column, elevation)
    left, right = contour.get(str((ai - 1) % 36)), contour.get(str((ai + 1) % 36))
    if _has_edge(left) and _has_edge(right):
        return (column_transmission(left, elevation) + column_transmission(right, elevation)) / 2
    return None


def _contour_equations(site: Site, geo: ArrayGeometry, rows: list[tuple], factor: Callable[[tuple], float],
                       measured_ghi: dict[float, float], sky_view: float) -> list[tuple[float, dict[int, list], float]]:
    """Steady clear hours as equations over the azimuth columns their sun path crossed.

    Per hour: (observed beam on the plane, {column: [(elevation, beam coefficient)] per sub-step},
    weight). The forecast integrates the sub-steps, so an hour's mean is the sum of the
    coefficients times the transmission at each sub-step (columns weighted as in transmission()).
    Beam and diffuse come from the measured irradiance split exactly as in the forecast, so the
    contour corrects the forecast's own chain: a clear-sky estimate of the beam that is too high at
    low sun would otherwise turn into a shadow there. Only hours whose expected light is mostly
    direct take part; otherwise errors of the diffuse model (sun nearly behind the plane) would
    darken the map. factor(row): efficiency x temperature factor.
    """
    eqs = []
    for r in rows:
        t, p = r[:2]
        dni_extra = extra_radiation(t + 1800)
        columns: dict[int, list[tuple[float, float]]] = {}
        beam = diff = 0.0
        suns = [sun_position(site, t + (i + 0.5) * 3600 / SUBSTEPS) for i in range(SUBSTEPS)]
        clear = [clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)[0] if sun.elevation > 0 else 0.0
                 for sun in suns]
        kt = measured_ghi[t] * SUBSTEPS / sum(clear) if sum(clear) > 0 else 0.0
        for sun, cs in zip(suns, clear):
            ghi = cs * kt
            if ghi <= 0:
                continue
            dni, dhi = erbs(ghi, sun.zenith, dni_extra)
            b, d, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=sky_view)
            b, d = b / SUBSTEPS, d / SUBSTEPS
            diff += d
            if b <= 0:
                continue
            beam += b
            for ai, w in _column_weights(sun.azimuth):
                columns.setdefault(ai, []).append((sun.elevation, b * w))
        if geo.peak_w * beam / 1000 <= MIN_BEAM_W or beam < SHADE_FIT_BEAM_SHARE * (beam + diff):
            continue
        eqs.append((p * 1000 / (geo.peak_w * factor(r)) - diff, columns, r[6]))
    return eqs


def _cell_equations(cells: dict[str, float]) -> list[tuple[float, dict[int, list], float]]:
    """Cell estimates as equations: a cell's value is the mean transmission over its elevation span."""
    return [(v, {ai: [((ei + (m + 0.5) / 10) * EL_STEP, 0.1) for m in range(10)]}, 1.0)
            for ai, ei, v in ((*map(int, k.split(",")), v) for k, v in cells.items())]


def _edge_scan(items: list[tuple[float, float, list]], sign: int,
               limit: float | None = None) -> tuple[float, float, float] | None:
    """Best single edge of a column: (squared error, edge elevation, transmission of the shaded segment).

    items: (excess, weight, points) per hour; excess: how far the hour falls short of the prediction
    without this edge, points: (elevation, beam coefficient) of its sub-steps in this column.
    sign +1: lower edge (shaded below), -1: upper edge (elevations mirrored). For every candidate
    elevation (CONTOUR_STEP apart) the segment's transmission is least squares within 0..1. The
    sums are updated as the scan passes the sub-steps: only those within the soft edge are
    recomputed. limit: highest mirrored edge (keeps a lower edge below an upper one).
    """
    pts = sorted((sign * el, a, j) for j, (_, _, points) in enumerate(items) for el, a in points)
    if not pts:
        return None
    excess = [e for e, _, _ in items]
    weight = [w for _, w, _ in items]
    see = sum(w * e * e for e, w in zip(excess, weight))
    full = [0.0] * len(items)                           # beam coefficient entirely on the shaded side
    shaded = [0.0] * len(items)
    s_el = s_ll = 0.0
    end = pts[-1][0] + CONTOUR_SOFT if limit is None else min(pts[-1][0] + CONTOUR_SOFT, limit)
    i_in = i_full = 0
    touched: set[int] = set()
    best = None
    g = math.floor((pts[0][0] - CONTOUR_SOFT) / CONTOUR_STEP) * CONTOUR_STEP
    while g <= end + 1e-9:
        while i_full < len(pts) and pts[i_full][0] + CONTOUR_SOFT <= g:
            full[pts[i_full][2]] += pts[i_full][1]
            touched.add(pts[i_full][2])
            i_full += 1
        while i_in < len(pts) and pts[i_in][0] - CONTOUR_SOFT < g:
            i_in += 1
        part: dict[int, float] = {}
        for x, a, j in pts[i_full:i_in]:                 # within the soft edge: _ramp without its limits
            part[j] = part.get(j, 0.0) + a * (g - x + CONTOUR_SOFT) / (2 * CONTOUR_SOFT)
        for j in touched | part.keys():
            new = full[j] + part.get(j, 0.0)
            s_el += weight[j] * excess[j] * (new - shaded[j])
            s_ll += weight[j] * (new * new - shaded[j] * shaded[j])
            shaded[j] = new
        touched = set(part)
        if s_ll > 1e-12:
            u = min(max(s_el / s_ll, 0.0), 1.0)
            sse = see - 2 * u * s_el + u * u * s_ll
            if best is None or sse < best[0] - 1e-9 * see:
                best = (sse, g, u)
        g += CONTOUR_STEP
    return (best[0], sign * best[1], 1 - best[2]) if best else None


def _fit_column(items: list[tuple[float, float, list]], penalty: float, fallback: dict | None = None) -> dict:
    """Edges of one azimuth column: none, lower, upper or both, chosen by the penalised robust error.

    An edge must lower the weighted squared error by penalty (noise variances) and by
    CONTOUR_MIN_GAIN of the error without it, so noise does not create shadows. Most of the hours
    entirely on its shaded side must be dark (closer to the segment's transmission than to free
    sky): a lone dark spot with free sky below it cannot pass as a lower edge whose transmission is a
    compromise between the spot and the free sky. Transmissions from SHADE_FREE count as free; one
    between SHADE_CORE and SHADE_FREE needs free sky observed on the other side of the edge – a whole
    column a little darker is no shade but model error (direction factor).
    fallback: a column from other data whose lower or upper edge is taken where this column has
    none, unless it raises the error by more than the penalty (the hours contradict it).
    """
    def loss(column: dict, pts: list) -> float:
        return sum(a * (1 - column_transmission(column, el)) for el, a in pts)

    def sse(column: dict) -> float:
        return sum(w * (e - loss(column, pts)) ** 2 for e, w, pts in items)

    beam = sum(w * a for _, w, pts in items for _, a in pts)

    def dark(column: dict, kind: str) -> bool:
        """Most of the hours entirely inside the segment are closer to its transmission than to free sky."""
        t, edge = column["t_" + kind], column[kind]
        inside = total = 0.0
        for e, w, pts in items:
            a = sum(a for _, a in pts)
            if a > 0 and all(el <= edge - CONTOUR_SOFT if kind == "low" else el >= edge + CONTOUR_SOFT for el, _ in pts):
                total += w * a
                inside += w * a if 1 - e / a < (1 + t) / 2 else 0.0
        return total > 0 and inside >= 0.5 * total

    def plausible(column: dict) -> bool:
        kinds = [k for k in ("low", "high") if k in column]
        if any(column["t_" + k] >= SHADE_FREE or not dark(column, k) for k in kinds):
            return False
        free = sum(w * a for _, w, pts in items for el, a in pts if column_transmission(column, el) >= 1.0)
        if len(kinds) == 2 or any(column["t_" + k] >= SHADE_CORE for k in kinds):
            return free >= CONTOUR_MIN_FREE * beam
        return True

    def edge(kind: str, scan: tuple[float, float, float]) -> dict:
        return {kind: round(scan[1], 1), "t_" + kind: round(scan[2], 3)}

    singles = []
    for kind, sign in (("low", 1), ("high", -1)):
        scan = _edge_scan(items, sign)
        if scan is not None:
            column = edge(kind, scan)
            singles.append((sse(column), column))
    none = sse({})
    best_sse, best, edges = none, {}, 0
    for value, column in sorted(singles, key=lambda c: c[0]):
        if value + penalty < none and value < (1 - CONTOUR_MIN_GAIN) * none and plausible(column):
            best_sse, best, edges = value, column, 1
            break
    if len(singles) == 2 and all(value + penalty < none for value, _ in singles):    # each edge helps on its own
        column = dict(min(singles, key=lambda c: c[0])[1])
        for _ in range(2):                       # alternate: each edge fitted with the other one fixed
            for kind, sign in (("high", -1), ("low", 1)) if "low" in column else (("low", 1), ("high", -1)):
                other = {k: v for k, v in column.items() if not k.endswith(kind)}
                bound = other.get("low" if kind == "high" else "high")
                limit = None if bound is None else -(bound + 2 * CONTOUR_SOFT) if kind == "high" \
                    else bound - 2 * CONTOUR_SOFT
                scan = _edge_scan([(e - loss(other, pts), w, pts) for e, w, pts in items], sign, limit)
                if scan is None:
                    break
                column = {**other, **edge(kind, scan)}
        if "low" in column and "high" in column and column["low"] + 2 * CONTOUR_SOFT <= column["high"]:
            value = sse(column)
            if (value + (2 - edges) * penalty < best_sse and value < (1 - CONTOUR_MIN_GAIN) * best_sse
                    and plausible(column)):
                best, best_sse = column, value
    for kind in ("low", "high"):
        if fallback and kind in fallback and kind not in best:
            column = {**best, kind: fallback[kind], "t_" + kind: fallback["t_" + kind]}
            if "low" in column and "high" in column and column["low"] + 2 * CONTOUR_SOFT > column["high"]:
                continue
            value = sse(column)
            if value <= best_sse + penalty:
                best, best_sse = column, value
    return best


def _fit_contour(eqs: list[tuple[float, dict[int, list], float]], min_hours: int,
                 fallback: dict[str, dict] | None = None) -> dict[str, dict]:
    """Contour per azimuth column such that the predicted equations meet the observed values.

    eqs: (observed, {column: [(elevation, coefficient)]}, weight); prediction: sum of the
    coefficients times the transmission. Columns are fitted one after the other with the others
    fixed (a few sweeps until nothing changes); a robust reweighting damps hours that do not fit
    (a cloud or haze the filters missed), as in two further rounds. Columns with fewer than
    min_hours equations carrying a real share (30 %) of their light get no decision. Each column
    also keeps the elevation range its data covers ("seen"): edges outside it are extrapolated.

    fallback: a contour from other data (the cell estimates), for where these equations cannot
    tell. A column without a decision here takes its fallback column unchanged; a decided column
    without a lower (upper) edge takes the fallback's edge unless the equations contradict it, i.e.
    unless it raises their error by more than the penalty of an edge (see _fit_column). Where the
    sun is nearly behind the plane the hours carry little direct light and would otherwise leave a
    real shadow there free – and extrapolate it free below the elevations they saw.
    """
    fallback = fallback or {}
    free = [sum(a for pts in columns.values() for _, a in pts) for _, columns, _ in eqs]
    by_column: dict[int, list[int]] = {}
    seen: dict[int, list[float]] = {}
    for j, (_, columns, _) in enumerate(eqs):
        for c, pts in columns.items():
            by_column.setdefault(c, []).append(j)
            if sum(a for _, a in pts) >= 0.3 * free[j]:
                els = [el for el, _ in pts]
                lo, hi = seen.get(c, [min(els), max(els), 0])[:2]
                seen[c] = [min(lo, *els), max(hi, *els), seen.get(c, [0, 0, 0])[2] + 1]
    decided = sorted(c for c, (_, _, n) in seen.items() if n >= min_hours)
    fixed = {int(k): v for k, v in fallback.items() if int(k) not in decided}
    if not decided:
        return dict(fallback)
    contour: dict[int, dict] = {c: {} for c in decided}
    loss: list[dict[int, float]] = [{} for _ in eqs]
    total = [0.0] * len(eqs)
    for c, column in fixed.items():                     # columns taken from the fallback stay as they are
        for j in by_column.get(c, []):
            loss[j][c] = sum(a * (1 - column_transmission(column, el)) for el, a in eqs[j][1][c])
            total[j] += loss[j][c]
    robust = [1.0] * len(eqs)
    floor = 0.01 * _quantile(free, 0.5)
    for rnd in range(CONTOUR_ROUNDS):
        residuals = [y - (f - lost) for (y, _, _), f, lost in zip(eqs, free, total)]
        centre = _quantile(residuals, 0.5)
        spread = 1.4826 * _quantile([abs(x - centre) for x in residuals], 0.5)
        if rnd and spread > 0:
            robust = [min(1.0, 2 * spread / max(abs(x), 1e-9)) for x in residuals]
        penalty = CONTOUR_PENALTY * max(spread, floor) ** 2
        for _ in range(CONTOUR_SWEEPS):
            changed = False
            for c in decided:
                items = [(free[j] - eqs[j][0] - (total[j] - loss[j].get(c, 0.0)), eqs[j][2] * robust[j], eqs[j][1][c])
                         for j in by_column[c]]
                column = _fit_column(items, penalty, fallback.get(str(c)))
                changed |= column != contour[c]
                contour[c] = column
                for j, (_, _, pts) in zip(by_column[c], items):
                    new = sum(a * (1 - column_transmission(column, el)) for el, a in pts)
                    total[j] += new - loss[j].get(c, 0.0)
                    loss[j][c] = new
            if not changed:
                break
    out = {c: dict(column) for c, column in fixed.items()}
    for c in decided:
        lo, hi = seen[c][:2]
        if str(c) in fallback:
            lo, hi = min(lo, fallback[str(c)]["seen"][0]), max(hi, fallback[str(c)]["seen"][1])
        out[c] = {**contour[c], "seen": [round(lo, 1), round(hi, 1)]}
    return {str(c): out[c] for c in sorted(out)}


def display_cells(learned: LearnedArray, reachable: set[tuple[int, int]]) -> dict[str, float]:
    """Cell grid for display (panel, image) from the contour: mean transmission over each reachable cell."""
    out = {}
    for ai, ei in reachable:
        values = [_contour_value(learned.contour, ai, (ei + (m + 0.5) / 10) * EL_STEP) for m in range(10)]
        if values[0] is not None:
            out[_key(ai, ei)] = round(sum(values) / len(values), 3)
    return out


def contour_coverage(contour: dict[str, dict], reachable: set[tuple[int, int]]) -> float:
    """Share of reachable cells in a column with an edge decision, within the elevations its data covers."""
    def covered(ai: int, ei: int) -> bool:
        column = contour.get(str(ai))
        return column is not None and column["seen"][0] - EL_STEP <= (ei + 0.5) * EL_STEP <= column["seen"][1] + EL_STEP
    return round(sum(covered(*c) for c in reachable) / len(reachable), 3) if reachable else 0.0


def without_shade(learned: LearnedArray) -> LearnedArray:
    """The same learned state without any shading (effects, explanations)."""
    return replace(learned, cells={}, contour={})


def _measured_parts(site: Site, geo: ArrayGeometry, learned: LearnedArray, start: float,
                    ghi: float) -> tuple[float, float, float, float]:
    """Hourly means of shaded beam and diffuse on the plane and of DNI, from measured GHI (Erbs split), and the heating."""
    dni_extra = extra_radiation(start + 1800)
    beam_sum = diff_sum = dni_sum = 0.0
    heat = _Heating(geo)
    for i in range(SUBSTEPS):
        sun = sun_position(site, start + (i + 0.5) * 3600 / SUBSTEPS)
        if sun.elevation <= 0 or ghi <= 0:
            continue
        dni, dhi = erbs(ghi, sun.zenith, dni_extra)
        beam, diff, heating = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view)
        beam_sum += beam * transmission(learned, sun.azimuth, sun.elevation)
        diff_sum += diff
        dni_sum += dni
        heat.add(beam, diff, heating)
    return beam_sum / SUBSTEPS, diff_sum / SUBSTEPS, dni_sum / SUBSTEPS, heat.value


def _isotonic(values: list[float], increasing: bool = True) -> list[float]:
    """Least-squares monotone fit (pool adjacent violators)."""
    sign = 1 if increasing else -1
    blocks: list[list[float]] = []                       # [mean, count]
    for v in values:
        blocks.append([sign * v, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            m2, c2 = blocks.pop()
            m1, c1 = blocks.pop()
            blocks.append([(m1 * c1 + m2 * c2) / (c1 + c2), c1 + c2])
    return [sign * m for m, c in blocks for _ in range(int(c))]


def _rise_fall(values: list[float]) -> list[float]:
    """Best fit that rises to a free band and may fall again above it."""
    best: tuple[float, list[float]] | None = None
    for k in range(len(values)):
        up = _isotonic(values[:k + 1], True)
        down = _isotonic(values[k:], False)
        fit = up + down[1:]
        fit[k] = max(up[-1], down[0])
        sse = sum((f - v) ** 2 for f, v in zip(fit, values))
        if best is None or sse < best[0]:
            best = (sse, fit)
    return best[1] if best else []


def shape_cells(cells: dict[str, float]) -> dict[str, float]:
    """Physical shape rules for a learned shading map.

    Obstacles on the ground shade low sun, overhangs shade high sun: per azimuth column the
    transmission rises with elevation to a free band and may fall again above it, never up and down
    in between. Dips in such a column are raised, a clear shadow core is never changed. Values from
    SHADE_FREE count as free (temperature, haze and model errors); values between SHADE_CORE and
    SHADE_FREE are shade only next to a core, in any direction, since side walls give edges across
    azimuth. A core without any shaded neighbour is noise.
    """
    columns: dict[int, list[tuple[int, float]]] = {}
    for key, v in cells.items():
        ai, ei = map(int, key.split(","))
        columns.setdefault(ai, []).append((ei, v))
    raised: dict[tuple[int, int], float] = {}
    for ai, items in columns.items():
        items.sort()
        fit = _rise_fall([v for _, v in items])
        for (ei, v), f in zip(items, fit):
            raised[(ai, ei)] = v if v < SHADE_CORE else max(v, f)

    def near(ai: int, ei: int, radius: int) -> list[float]:
        return [raised[n] for x in range(-radius, radius + 1) for y in range(-radius, radius + 1)
                if (x, y) != (0, 0) and (n := ((ai + x) % 36, ei + y)) in raised]

    out = {}
    for (ai, ei), v in raised.items():
        if v >= SHADE_FREE:
            v = 1.0
        elif v >= SHADE_CORE:
            if not any(n < SHADE_CORE for n in near(ai, ei, SHADE_NEAR_CELLS)):
                v = 1.0
        out[_key(ai, ei)] = round(v, 3)
    return out


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
    """Learned beam transmission at a sun position (1 = unshaded).

    Version 2: per azimuth column (10°) a lower edge (ground obstacles: shaded below it) and an upper
    edge (overhang: shaded above it), each with the transmission of its shaded segment (0 = wall,
    above 0 = tree or railing), soft over ±1° so the sub-step integration stays smooth. Linear
    between column centres; a column without a decision counts as free, unless both neighbours
    have an edge (their mean). Version 1 (cells stored before the contours, until the next learning
    run): bilinear interpolation between cell centres; missing cells fall back to neighbours, then 1.
    """
    if learned.shading_version >= SHADING_VERSION:
        value = 0.0
        for ai, w in _column_weights(azimuth):
            v = _contour_value(learned.contour, ai, elevation)
            value += w * (1.0 if v is None else v)
        return value
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


def known_transmission(learned: LearnedArray, azimuth: float, elevation: float) -> float | None:
    """Contour (version 2) only: transmission(), or None where no column nearby has a decision yet."""
    parts = [(w, _contour_value(learned.contour, ai, elevation)) for ai, w in _column_weights(azimuth)]
    if all(v is None for _, v in parts):
        return None
    return sum(w * (1.0 if v is None else v) for w, v in parts)


def direction_factor(learned: LearnedArray, azimuth: float, elevation: float) -> float:
    """Bilinear between coarse bin centres; bins without data count as 1."""
    return _coarse(learned.direction, azimuth, elevation, 1.0)


def reflection_factor(learned: LearnedArray, azimuth: float, elevation: float) -> float:
    """Reflected light on the plane per W/m² DNI; bilinear between coarse bin centres, missing bins 0."""
    return _coarse(learned.reflection, azimuth, elevation, 0.0)


def _coarse(values: dict[str, float], azimuth: float, elevation: float, default: float) -> float:
    if not values:
        return default
    fa = azimuth / DIRECTION_AZ_STEP - 0.5
    fe = elevation / DIRECTION_EL_STEP - 0.5
    a0, e0 = math.floor(fa), math.floor(fe)
    da, de = fa - a0, fe - e0
    value = 0.0
    for ai, wa in ((a0, 1 - da), (a0 + 1, da)):
        for ei, we in ((e0, 1 - de), (e0 + 1, de)):
            value += wa * we * values.get(_key(ai % 18, ei), default)
    return value


def clearsky_weather(site: Site, start: float, temp: float | None = None) -> WeatherHour:
    """Hourly mean clear-sky irradiance as a weather hour (reference for clearness)."""
    ghi = dni = dhi = 0.0
    dni_extra = extra_radiation(start + 1800)
    for i in range(SUBSTEPS):
        sun = sun_position(site, start + (i + 0.5) * 3600 / SUBSTEPS)
        if sun.elevation > 0:
            g, n, d = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
            ghi, dni, dhi = ghi + g, dni + n, dhi + d
    return WeatherHour(start, ghi / SUBSTEPS, dni / SUBSTEPS, dhi / SUBSTEPS, temp)


def clearsky_profile(site: Site, geo: ArrayGeometry, learned: LearnedArray, start: float, temp: float | None = None,
                     steps: int = 12) -> list[float]:
    """Clear-sky power (W) of one hour in equal steps, e.g. to compare a partly measured hour."""
    dni_extra = extra_radiation(start + 1800)
    out = []
    for i in range(steps):
        sun = sun_position(site, start + (i + 0.5) * 3600 / steps)
        if sun.elevation <= 0:
            out.append(0.0)
            continue
        ghi, dni, dhi = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
        beam, diff, heating = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view)
        poa = beam * transmission(learned, sun.azimuth, sun.elevation) * direction_factor(
            learned, sun.azimuth, sun.elevation) + diff + reflection_factor(learned, sun.azimuth, sun.elevation) * dni
        out.append(geo.peak_w * learned.efficiency * temperature_factor(learned.temp_coeff, temp, poa, heating)
                   * poa / 1000)
    return out


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
            beam, diff, heating = array_irradiance(geo, sun, dni, dhi, wh.ghi, dni_extra,
                                                   sky_view=learned.sky_view)
            poa = beam * transmission(learned, sun.azimuth, sun.elevation) * direction_factor(
                learned, sun.azimuth, sun.elevation) + diff + reflection_factor(learned, sun.azimuth, sun.elevation) * dni
            total += geo.peak_w * learned.efficiency * temperature_factor(
                learned.temp_coeff, wh.temp, poa, heating) * poa / 1000
        out[wh.start] = total / SUBSTEPS
    return out
