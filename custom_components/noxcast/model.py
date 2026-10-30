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
- T: beam transmission (1 = unshaded) per sun-position cell, learned from hours where direct
  light is geometrically possible. The 90th percentile per cell represents clear conditions;
  clouds and occasional curtailment fall below it. Optionally, satellite irradiance pre-selects
  clear hours. The map holds shade only: per azimuth it rises with elevation to a free band and may
  fall again (overhang); values from 0.9 count as free, 0.7-0.9 only next to a clear core.
- Facade: a row of buildings opposite the array, as one straight edge: its height seen at right
  angles to the row (an angle) and the row's direction; towards the sides the row is further away,
  tan(edge) = tan(height angle) · cos(azimuth − row direction). Fitted to clear hours, kept only if
  it clearly explains the shade in its sector, and drawn on along the row as long as the data do
  not contradict it. Learned cells stay as they are; the facade fills the cells without a learned
  value, sun positions a few clear winter days cannot reach.
- D: direction factor (may exceed 1) on coarse bins, the median of what remains at unshaded
  positions on clear hours: geometry and model errors that are not shade.
  Efficiency, D, R and the clear-hour refinement of T use only steady hours when the power
  sensors' hourly minimum and maximum are known: a satellite pixel can call an hour clear while
  clouds pass over the installation, and such hours (cloud-edge peaks cut at the inverter limit,
  restarts, dropouts) only add noise. With several sensors per array one steady sensor is enough
  (a cloud reaches all of them, a shadow edge not), and sensors far apart mark a partial shadow.
  On steady clear hours T is finally fitted so that the forecast, integrated over the sub-steps
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

Everything is written for periods of an hour, the resolution of long-term statistics and of the weather. Learning
and forecasting also take another period length (seconds): a sample is then the mean of such a period, and a
minimum number of hours stays the same observed time (see _periods).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from .solar import (
    Site, SunPosition, clearsky_simplified_solis, erbs, extra_radiation, plane_of_array, sun_position,
)

from .const import Series

AZ_STEP = 10.0
EL_STEP = 5.0
HOUR = 3600.0                    # seconds, the default period
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
SHADE_FIT_PRIOR = 0.01           # shading fit: pull towards the per-cell estimate, relative to a typical cell's weight
SHADE_FIT_BEAM_SHARE = 0.7       # ... hours whose expected light is mostly direct (else diffuse-model errors enter)
SHADE_FIT_SWEEPS = 400
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
FACADE_WIDTH = 8.0               # soft facade edge (°): the sun's bright aureole and an uneven roofline
FACADE_MAX_ANGLE = 85.0          # the row is drawn on at most this far from its direction
FACADE_MIN_GAIN = 0.2            # the facade must remove this share of the error in its sector against no shade
FACADE_MIN_SPAN = 30.0           # ... over at least this many degrees of azimuth
FACADE_MAX_EL = FACADE_WIDTH / 2 + 40.0   # sun positions above can not be behind a facade


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


# format of LearnedArray in the store: raise when a field changes meaning. Learned state is only a cache of the
# history, so a state of another version is dropped and relearned at startup instead of being migrated.
LEARNED_VERSION = 1


@dataclass
class LearnedArray:
    efficiency: float = DEFAULT_EFFICIENCY                     # at 25 °C cell temperature
    temp_coeff: float = DEFAULT_TEMP_COEFF                     # per K
    temp_learned: bool = False
    temp_source: str = "default"                               # datasheet | learned | default | none
    cells: dict[str, float] = field(default_factory=dict)      # "az_idx,el_idx" -> transmission
    direction: dict[str, float] = field(default_factory=dict)  # coarse "az_idx,el_idx" -> beam factor (non-shade)
    sky_view: float = 1.0                                      # share of the isotropic sky diffuse seen
    reflection: dict[str, float] = field(default_factory=dict) # coarse "az_idx,el_idx" -> plane W/m² per W/m² DNI
    coverage: float = 0.0                                      # share of reachable cells learned
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
    # a row of buildings opposite: {"direction", "height", "start", "end", "gain"}, see learn_facade
    facade: dict | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> LearnedArray:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------- helpers

def _substeps(period: float) -> int:
    """Sub-steps of a period: about 10 minutes long, a short period is a single one."""
    return max(1, round(period / HOUR * SUBSTEPS))


def _sun_path(site: Site, start: float, period: float) -> list[SunPosition]:
    """The sun at the middle of each sub-step of a period, also below the horizon."""
    n = _substeps(period)
    return [sun_position(site, start + (i + 0.5) * period / n) for i in range(n)]


def _periods(hours: float, period: float) -> float:
    """So many hours of observation in periods: a threshold means the same observed time at any period."""
    return hours * HOUR / period


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


def clearsky_heating(site: Site, geo: ArrayGeometry, start: float, sky_view: float = 1.0, period: float = HOUR) -> float:
    """Heating (K at 1000 W/m²) for the mean clear-sky irradiance of a period, see array_irradiance."""
    if not geo.extra:
        return geo.heating
    acc = _Heating(geo)
    dni_extra = extra_radiation(start + period / 2)
    for sun in _sun_path(site, start, period):
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


def clearsky_hour(site: Site, geo: ArrayGeometry, start: float, sky_view: float = 1.0,
                  period: float = HOUR) -> tuple[float, float, float, float, float]:
    """Means of clear-sky (beam, diffuse) on the plane over a period, mean sun (azimuth, elevation) and GHI.

    The sun position is averaged over the sub-steps where the sun is up, so an hourly mean value
    belongs to the sun path within that hour, not to the hour start.
    """
    beam = diff = ghi_sum = 0.0
    sx = sy = el_sum = 0.0
    up = 0
    pressure = site.pressure
    dni_extra = extra_radiation(start + period / 2)
    for sun in _sun_path(site, start, period):
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
    n = _substeps(period)
    return beam / n, diff / n, az, el_sum / up, ghi_sum / n


# ---------------------------------------------------------------------------- learning

def usable_samples(power: Series, *, soc: Series | None = None, soc_full: float = 95.0,
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


def _learn_temp_coeff(rows: list[tuple[float, float, float]], period: float = HOUR) -> tuple[float, bool]:
    """(cell temperature, ratio, _) rows -> temperature coefficient from the upper envelope per bin."""
    bins: dict[int, list[float]] = {}
    for t_cell, ratio, _ in rows:
        bins.setdefault(int(t_cell // TEMP_BIN_K), []).append(ratio)
    env = [((b + 0.5) * TEMP_BIN_K, _quantile(v, 0.9)) for b, v in sorted(bins.items())
           if len(v) >= _periods(TEMP_MIN_BIN_SAMPLES, period)]
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
          air_temp: Series | None = None, satellite_ghi: Series | None = None,
          datasheet_temp_coeff: float | None = None, half_life_days: float = HALF_LIFE_DAYS,
          steady: set[float] | None = None, period: float = HOUR) -> LearnedArray:
    """Learn efficiency, temperature coefficient, shading map and sky view from (period start, mean power W) samples.

    air_temp: air temperature (°C) at the middle of each period.
    satellite_ghi: measured mean GHI (W/m²) per period used to pre-select clear periods (optional).
    datasheet_temp_coeff: Pmax temperature coefficient per K; used as fixed value instead of learning it.
      Learning over cell-temperature bins also picks up irradiance-dependent model errors, so a known
      datasheet value is more robust.
    steady: hour starts in which the power stayed steady (see steady_hours); efficiency, direction
      factor and reflection then use only those of the clear hours.
    period: length of a sample in seconds; air_temp, satellite_ghi and steady use the same period starts.

    The sky view needs satellite irradiance. It and the other parameters depend on each other, so
    they are learned alternately until the sky view settles.
    """
    args = dict(air_temp=air_temp, satellite_ghi=satellite_ghi, datasheet_temp_coeff=datasheet_temp_coeff,
                half_life_days=half_life_days, steady=steady, period=period)
    learned = _learn(site, geo, samples, now, sky_view=1.0, **args)
    if satellite_ghi is None:
        return learned
    for _ in range(SKY_VIEW_ITERATIONS):
        sky = learn_sky_view(site, geo, learned, samples, satellite_ghi, air_temp, period)
        if sky is None or abs(sky - learned.sky_view) < 0.01:
            break
        learned = _learn(site, geo, samples, now, sky_view=sky, **args)
    learned.facade = learn_facade(site, geo, learned, samples, satellite_ghi, air_temp, period)
    return learned


def learn_sky_view(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                   satellite_ghi: Series, air_temp: Series | None = None, period: float = HOUR) -> float | None:
    """Share of the isotropic sky diffuse the plane sees, from overcast hours (None without enough of them).

    What the installation produced beyond beam and ground reflection, relative to the sky diffuse a
    free plane would get from the measured irradiance.
    """
    seen = free = 0.0
    hours = 0
    n = _substeps(period)
    for t, p in samples:
        ghi = satellite_ghi.get(t)
        cs_ghi = clearsky_hour(site, geo, t, period=period)[4]
        if ghi is None or cs_ghi < SKY_VIEW_MIN_CLEARSKY or ghi / cs_ghi >= OVERCAST_KT:
            continue
        dni_extra = extra_radiation(t + period / 2)
        rest = sky = 0.0
        heat = _Heating(geo)
        for sun in _sun_path(site, t, period):
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
        rest, sky = rest / n, sky / n
        temp = air_temp.get(t) if air_temp else None
        f = temperature_factor(learned.temp_coeff, temp, rest + sky * learned.sky_view, heat.value) \
            if temp is not None else 1.0
        seen += p * 1000 / (geo.peak_w * learned.efficiency * f) - rest
        free += sky
        hours += 1
    if hours < _periods(SKY_VIEW_MIN_HOURS, period) or free <= 0:
        return None
    return round(min(max(seen / free, SKY_VIEW_LIMITS[0]), SKY_VIEW_LIMITS[1]), 3)


def _learn(site: Site, geo: ArrayGeometry, samples: list[tuple[float, float]], now: float, *,
           air_temp: Series | None, satellite_ghi: Series | None,
           datasheet_temp_coeff: float | None, half_life_days: float, sky_view: float,
           steady: set[float] | None = None, period: float = HOUR) -> LearnedArray:
    rows = []
    for t, p in samples:
        beam, diff, az, el, cs_ghi = clearsky_hour(site, geo, t, sky_view, period)
        if el <= 0:
            continue
        age = max(now - t, 0) / 86400.0
        kt = None
        if satellite_ghi is not None and satellite_ghi.get(t) is not None and cs_ghi > 50:
            kt = satellite_ghi[t] / cs_ghi
        temp = air_temp.get(t) if air_temp else None
        heating = clearsky_heating(site, geo, t, sky_view, period) if temp is not None else geo.heating
        rows.append((t, p, beam, diff, az, el, 0.5 ** (age / half_life_days), age, kt, temp, heating))
    if not rows:
        return LearnedArray(sky_view=sky_view, learned_at=now)

    # optional satellite pre-filter: only clear hours are used for efficiency and shading
    with_kt = [r for r in rows if r[8] is not None]
    clear_filter = satellite_ghi is not None and len(with_kt) >= MIN_CLEAR_COVERAGE * len(rows)
    basis = [r for r in with_kt if r[8] >= CLEAR_KT] if clear_filter else rows
    if len(basis) < _periods(50, period):                                  # too few clear hours: fall back to all hours
        basis, clear_filter = rows, False
    # steady hours only, when there are enough of them: the satellite may call an hour clear while
    # clouds pass over the installation
    calm = [r for r in basis if r[0] in steady] if clear_filter and steady is not None else []
    steady_filter = len(calm) >= _periods(MIN_STEADY_HOURS, period)
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
        temp_coeff, temp_learned = _learn_temp_coeff(temp_rows, period)
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
            if len(items) < _periods(MIN_CELL_SAMPLES, period):
                continue
            value = weighted_quantile([v for v, _, _ in items], [w for _, w, _ in items], q)
            recent = [v for v, _, a in items if a <= RECENT_DAYS]
            if len(recent) >= _periods(3, period):
                recent_value = _quantile(recent, q)
                if abs(recent_value - value) > CHANGE_THRESHOLD:     # e.g. a tree was cut or a building added
                    value = recent_value
            out[_key(ai, ei)] = round(min(max(value, 0.0), 1.0), 3)
        return out

    def envelope() -> dict[str, float]:
        """P90 over all hours; with the satellite only in cells that also have clear hours, since
        in a cell with hardly any clear hour (winter noon) the P90 is a cloud, not a shadow."""
        out = cell_values(observe(rows, scaled=False), 0.9)
        if not clear_filter:
            return out
        seen = {_key(*c) for c, items in observe(basis, scaled=False).items()
                if len(items) >= _periods(MIN_CELL_SAMPLES, period)}
        return {k: v for k, v in out.items() if k in seen}

    cells = envelope()
    if clear_filter:
        # median over clear hours whose sun position is unshaded; the median does not ignore shadows.
        # With little data (new installation) all clear hours: shadows pull it slightly low, the safe side,
        # while a high percentile over clear hours would only pick satellite errors.
        probe = LearnedArray(cells=cells)
        unshaded = [r for r in strong if transmission(probe, r[4], r[5]) >= 0.95]
        steady_unshaded = [r for r in unshaded if r[0] in steady] if steady_filter else []
        if len(steady_unshaded) >= _periods(MIN_UNSHADED_HOURS, period):
            unshaded = steady_unshaded
        source = unshaded if len(unshaded) >= _periods(MIN_UNSHADED_HOURS, period) else strong
        if source:
            efficiency = eff_quantile(source, 0.5)
            cells = envelope()
        cells.update(cell_values(observe(calm, scaled=True), 0.5))
        if steady_filter:
            cells = _fit_cells(site, geo, calm, cells, lambda r: efficiency * f_temp(r), scale, period)

    cells = shape_cells(cells)
    direction: dict[str, float] = {}
    reflection: dict[str, float] = {}
    if clear_filter:
        # what remains at unshaded positions on clear hours is not shade; kept apart from the shading
        # map, smooth and coarse. Modelled with the measured irradiance split as in the forecast.
        probe = LearnedArray(cells=cells, sky_view=sky_view)
        parts = []                                       # (bin, plane-equivalent observed, beam, diffuse, dni)
        for r in calm:
            t, p, _, _, az, el = r[:6]
            if transmission(probe, az, el) < 0.999:
                continue
            beam, diff, dni, heating = _measured_parts(site, geo, probe, t, satellite_ghi[t], period)
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
                     if len(v) >= _periods(DIRECTION_MIN_SAMPLES, period)}
        reflection = {_key(*b): round(v, 3) for b, v in refl_bins.items() if v >= 0.005}

        # the rest on the beam part, in both directions: geometry and model errors
        dir_obs: dict[tuple[int, int], list[float]] = {}
        for b, observed, beam, diff, dni in parts:
            if geo.peak_w * beam / 1000 <= MIN_BEAM_W:
                continue
            dir_obs.setdefault(b, []).append((observed - diff - refl_bins.get(b, 0.0) * dni) / beam)
        direction = {_key(*b): round(min(max(_quantile(v, 0.5), DIRECTION_LIMITS[0]), DIRECTION_LIMITS[1]), 3)
                     for b, v in dir_obs.items() if len(v) >= _periods(DIRECTION_MIN_SAMPLES, period)}
    learned = LearnedArray(efficiency=round(efficiency, 4), temp_coeff=round(temp_coeff, 5), temp_learned=temp_learned,
                           temp_source=temp_source,
                           cells=cells, direction=direction, reflection=reflection, sky_view=sky_view,
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

    reachable = reachable_cells(site, geo)
    learned.coverage = round(len(reachable & {tuple(map(int, k.split(","))) for k in cells}) / len(reachable), 3) \
        if reachable else 0.0
    return learned


def _bilinear(azimuth: float, elevation: float) -> list[tuple[tuple[int, int], float]]:
    """Cells and weights of the bilinear interpolation in transmission()."""
    fa, fe = azimuth / AZ_STEP - 0.5, elevation / EL_STEP - 0.5
    a0, e0 = math.floor(fa), math.floor(fe)
    da, de = fa - a0, fe - e0
    return [((ai % 36, ei), wa * we) for ai, wa in ((a0, 1 - da), (a0 + 1, da))
            for ei, we in ((e0, 1 - de), (e0 + 1, de)) if wa * we > 0 and ei >= 0]


def _fit_cells(site: Site, geo: ArrayGeometry, rows: list[tuple], cells: dict[str, float],
               factor: Callable[[tuple], float], scale: Callable[[tuple], float],
               period: float = HOUR) -> dict[str, float]:
    """Shading cells such that the forecast, integrated over the sub-steps of each hour, meets the hourly means.

    The per-cell estimate gives each hour to the cell of its mid-hour sun position, so a shadow
    edge crossed during the hour is smeared over about one and a half cells, and the forecast,
    which integrates over sub-steps, smears it again. Here every hour is an equation over the cells
    its sun path crossed (bilinear weights as in transmission()); over many days the path of a
    given clock hour shifts across the edge, which places it within the hour. Least squares with
    bounds 0..1 and a weak pull towards the per-cell estimate (cells the hours barely touch),
    solved by projected Gauss-Seidel; one reweighting damps hours that do not fit
    (a cloud the steady test missed). Only hours whose expected light on the plane is mostly
    direct take part; otherwise errors of the diffuse model (sun nearly behind the plane) would
    darken the cells. rows: steady clear hours; factor(row): efficiency x temperature factor;
    scale(row): satellite clearness.
    """
    eqs = []
    n = _substeps(period)
    for r in rows:
        t, p, _, diff = r[:4]
        k = scale(r)
        dni_extra = extra_radiation(t + period / 2)
        coef: dict[tuple[int, int], float] = {}
        for sun in _sun_path(site, t, period):
            if sun.elevation <= 0:
                continue
            ghi, dni, dhi = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
            beam, _, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra)
            for cell, w in _bilinear(sun.azimuth, sun.elevation):
                coef[cell] = coef.get(cell, 0.0) + beam * k / n * w
        beam = sum(coef.values())
        if geo.peak_w * beam / 1000 <= MIN_BEAM_W or beam < SHADE_FIT_BEAM_SHARE * (beam + diff * k):
            continue
        eqs.append((p * 1000 / (geo.peak_w * factor(r)) - diff * k, coef, r[6]))
    if len(eqs) < _periods(MIN_STEADY_HOURS, period):
        return cells
    probe = LearnedArray(cells=cells)                     # cells without an estimate start from their neighbours
    prior = {c: cells[_key(*c)] if _key(*c) in cells else transmission(probe, (c[0] + 0.5) * AZ_STEP,
                                                                       (c[1] + 0.5) * EL_STEP)
             for _, coef, _ in eqs for c in coef}

    def solve(weights: list[float]) -> dict[tuple[int, int], float]:
        normal: dict[tuple[int, int], dict[tuple[int, int], float]] = {c: {} for c in prior}
        rhs = dict.fromkeys(prior, 0.0)
        for (y, coef, w), extra in zip(eqs, weights):
            for c, a in coef.items():
                rhs[c] += w * extra * a * y
                row = normal[c]
                for c2, a2 in coef.items():
                    row[c2] = row.get(c2, 0.0) + w * extra * a * a2
        pull = SHADE_FIT_PRIOR * _quantile([row[c] for c, row in normal.items()], 0.5)
        for c, row in normal.items():
            row[c] += pull
            rhs[c] += pull * prior[c]
        value = dict(prior)
        for _ in range(SHADE_FIT_SWEEPS):
            change = 0.0
            for c, row in normal.items():
                v = (rhs[c] - sum(a * value[c2] for c2, a in row.items() if c2 != c)) / row[c]
                v = min(max(v, 0.0), 1.0)
                change = max(change, abs(v - value[c]))
                value[c] = v
            if change < 1e-4:
                break
        return value

    value = solve([1.0] * len(eqs))
    residuals = [y - sum(a * value[c] for c, a in coef.items()) for y, coef, _ in eqs]
    centre = _quantile(residuals, 0.5)
    spread = 1.4826 * _quantile([abs(x - centre) for x in residuals], 0.5)
    if spread > 0:
        value = solve([min(1.0, 2 * spread / max(abs(x), 1e-9)) for x in residuals])
    out = dict(cells)
    out.update({_key(*c): round(v, 3) for c, v in value.items()})
    return out


def _measured_parts(site: Site, geo: ArrayGeometry, learned: LearnedArray, start: float, ghi: float,
                    period: float = HOUR) -> tuple[float, float, float, float]:
    """Means over the period of shaded beam and diffuse on the plane and of DNI, from measured GHI (Erbs split), and the heating."""
    dni_extra = extra_radiation(start + period / 2)
    beam_sum = diff_sum = dni_sum = 0.0
    heat = _Heating(geo)
    for sun in _sun_path(site, start, period):
        if sun.elevation <= 0 or ghi <= 0:
            continue
        dni, dhi = erbs(ghi, sun.zenith, dni_extra)
        beam, diff, heating = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view)
        beam_sum += beam * transmission(learned, sun.azimuth, sun.elevation)
        diff_sum += diff
        dni_sum += dni
        heat.add(beam, diff, heating)
    n = _substeps(period)
    return beam_sum / n, diff_sum / n, dni_sum / n, heat.value


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

def facade_transmission(facade: dict | None, azimuth: float, elevation: float) -> float | None:
    """Transmission behind the facade's edge (soft over FACADE_WIDTH), None outside its sector."""
    if not facade or (azimuth - facade["start"]) % 360 > facade["end"] - facade["start"]:
        return None
    c = max(math.cos(math.radians(azimuth - facade["direction"])), 0.0)
    edge = math.degrees(math.atan(math.tan(math.radians(facade["height"])) * c))
    return min(max((elevation - edge) / FACADE_WIDTH + 0.5, 0.0), 1.0)


def facade_cells(learned: LearnedArray) -> dict[str, float]:
    """Cells without a learned value that the facade fills (values at the cell centres), for display."""
    f = learned.facade
    if not f:
        return {}
    out = {}
    for ai in range(int(f["start"] // AZ_STEP), int(f["end"] // AZ_STEP) + 1):
        for ei in range(int(FACADE_MAX_EL // EL_STEP) + 1):
            key = _key(ai % 36, ei)
            v = facade_transmission(f, (ai + 0.5) * AZ_STEP, (ei + 0.5) * EL_STEP)
            if key not in learned.cells and v is not None:
                out[key] = round(v, 3)
    return out


def learn_facade(site: Site, geo: ArrayGeometry, learned: LearnedArray, samples: list[tuple[float, float]],
                 satellite_ghi: Series | None, air_temp: Series | None = None, period: float = HOUR) -> dict | None:
    """A row of buildings opposite, fitted to clear hours with the learned parameters, or None.

    Per row direction and height the clear hours are forecast with the facade and without any shade.
    The facade's sector grows from its direction to both sides, 10° at a time, as long as the facade
    explains those hours at least as well as a free sky; azimuths without such hours do not stop it,
    which draws the row on into sun positions only winter reaches. Only a facade that removes
    FACADE_MIN_GAIN of the error in its sector over FACADE_MIN_SPAN degrees is kept.
    """
    if not satellite_ghi:
        return None
    n = _substeps(period)
    hours = []                                            # (azimuth column, actual, [(az, el, beam, rest, heat, temp)])
    for t, p in samples:
        ghi = satellite_ghi.get(t)
        cs = clearsky_weather(site, t, period=period).ghi
        if ghi is None or cs <= 50 or ghi / cs < CLEAR_KT:
            continue
        dni_extra = extra_radiation(t + period / 2)
        temp = air_temp.get(t) if air_temp else None
        steps, low = [], False
        for sun in _sun_path(site, t, period):
            if sun.elevation <= 0:
                continue
            dni, dhi = erbs(ghi, sun.zenith, dni_extra)
            beam, diff, heating = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra, sky_view=learned.sky_view)
            steps.append((sun.azimuth, sun.elevation, beam * direction_factor(learned, sun.azimuth, sun.elevation),
                          diff + reflection_factor(learned, sun.azimuth, sun.elevation) * dni, heating, temp))
            low = low or (sun.elevation < FACADE_MAX_EL and beam > 0)
        if low:                                           # only hours a facade could shade tell facades apart
            hours.append((int(sun_position(site, t + period / 2).azimuth // AZ_STEP), p, steps))
    if not hours:
        return None

    def energy(steps, shade) -> float:
        total = 0.0
        for az, el, beam, rest, heating, temp in steps:
            poa = beam * shade(az, el) + rest
            total += poa * temperature_factor(learned.temp_coeff, temp, poa, heating)
        return geo.peak_w * learned.efficiency * total / 1000 / n

    free: dict[int, float] = {}
    for col, p, steps in hours:
        free[col] = free.get(col, 0.0) + (energy(steps, lambda az, el: 1.0) - p) ** 2

    def evaluate(direction: float, height: float) -> tuple[float, dict] | None:
        f = {"direction": direction, "height": height, "start": 0.0, "end": 360.0}
        err: dict[int, float] = {}
        for col, p, steps in hours:
            if abs((col + 0.5) * AZ_STEP - direction) < FACADE_MAX_ANGLE:
                err[col] = err.get(col, 0.0) + (energy(steps, lambda az, el: facade_transmission(f, az, el)) - p) ** 2
        centre = int(direction // AZ_STEP)
        if err.get(centre, 0.0) > free.get(centre, 0.0):
            return None
        cols = [centre]
        for step in (-1, 1):
            c = centre + step
            while abs((c + 0.5) * AZ_STEP - direction) < FACADE_MAX_ANGLE and err.get(c, 0.0) <= free.get(c, 0.0):
                cols.append(c)
                c += step
        shaded, open_ = sum(err.get(c, 0.0) for c in cols), sum(free.get(c, 0.0) for c in cols)
        if open_ <= 0 or (max(cols) - min(cols) + 1) * AZ_STEP < FACADE_MIN_SPAN:
            return None
        f.update(start=min(cols) * AZ_STEP, end=(max(cols) + 1) * AZ_STEP, gain=1 - shaded / open_)
        return sum(free.values()) - open_ + shaded, f

    best = None
    for direction in range(int(geo.azimuth) - 80, int(geo.azimuth) + 81, 10):
        for height in range(4, 41, 3):
            r = evaluate(float(direction), float(height))
            if r and (best is None or r[0] < best[0]):
                best = r
    if best is None:
        return None
    d0, h0 = best[1]["direction"], best[1]["height"]
    for direction in range(int(d0) - 6, int(d0) + 7, 2):
        for height in (h0 + x / 2 for x in range(-4, 5)):
            r = evaluate(float(direction), height)
            if r and r[0] < best[0]:
                best = r
    f = best[1]
    if f["gain"] < FACADE_MIN_GAIN:
        return None
    return {k: round(v, 3) if k == "gain" else round(v, 1) for k, v in f.items()}


def transmission(learned: LearnedArray, azimuth: float, elevation: float) -> float:
    """Bilinear interpolation between cell centres; a missing cell takes the facade there if one is
    learned, else missing cells fall back to neighbours, then 1."""
    fa = azimuth / AZ_STEP - 0.5
    fe = elevation / EL_STEP - 0.5
    a0, e0 = math.floor(fa), math.floor(fe)
    da, de = fa - a0, fe - e0
    num = den = 0.0
    for ai, wa in ((a0, 1 - da), (a0 + 1, da)):
        for ei, we in ((e0, 1 - de), (e0 + 1, de)):
            v = learned.cells.get(_key(ai % 36, ei))
            if v is None and ei >= 0 and learned.facade:
                v = facade_transmission(learned.facade, (ai + 0.5) * AZ_STEP, (ei + 0.5) * EL_STEP)
            if v is not None and wa * we > 0:
                num += v * wa * we
                den += wa * we
    if den > 0:
        return num / den
    ai, ei = _cell(azimuth, elevation)
    near = [learned.cells[k] for k in (_key((ai + x) % 36, ei + y) for x in (-1, 0, 1) for y in (-1, 0, 1))
            if k in learned.cells]
    return sum(near) / len(near) if near else 1.0


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


def clearsky_weather(site: Site, start: float, temp: float | None = None, period: float = HOUR) -> WeatherHour:
    """Mean clear-sky irradiance of a period as a weather hour (reference for clearness)."""
    ghi = dni = dhi = 0.0
    dni_extra = extra_radiation(start + period / 2)
    for sun in _sun_path(site, start, period):
        if sun.elevation > 0:
            g, n, d = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
            ghi, dni, dhi = ghi + g, dni + n, dhi + d
    n = _substeps(period)
    return WeatherHour(start, ghi / n, dni / n, dhi / n, temp)


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


def forecast(site: Site, geo: ArrayGeometry, learned: LearnedArray, weather: list[WeatherHour],
             period: float = HOUR) -> Series:
    """Expected energy per period (Wh), keyed by period start (UNIX seconds); weather: means of those periods."""
    out = {}
    n = _substeps(period)
    for wh in weather:
        dni_extra = extra_radiation(wh.start + period / 2)
        total = 0.0
        for sun in _sun_path(site, wh.start, period):
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
        out[wh.start] = total / n * (period / HOUR)
    return out


def measured_forecast(site: Site, geo: ArrayGeometry, learned: LearnedArray,
                      hours: list[tuple[float, float, float | None]], period: float = HOUR) -> Series:
    """The model on measured weather: what the forecast would have been with a perfect weather forecast."""
    return forecast(site, geo, learned, [WeatherHour(t, g, None, None, temp) for t, g, temp in hours], period)
