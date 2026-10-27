"""A synthetic year of hourly data for shading tests (and for comparing model versions).

Site at 51.0° N, 10.0° E. Clear and cloudy days, measurement noise, and in February and October a
quarter of the clear noon hours reduced by 25-50 % (haze the clear and steady filters miss).
The model modules are passed in, so the same data can be fed to another version of model.py.
"""
import random
import time

SITE_LAT, SITE_LON, SITE_ALT = 51.0, 10.0, 200.0
START = 1767225600.0             # 2026-01-01 UTC
EFF = 0.92
HAZE_MONTHS = (2, 10)

MASKS = {
    "free": lambda az, el: 1.0,
    "wall": lambda az, el: 0.0 if 90 <= az <= 150 and el < 15 else 1.0,          # low wall, east to south-east
    "overhang": lambda az, el: 0.0 if 100 <= az <= 200 and el > 45 else 1.0,     # balcony roof above
    "tree": lambda az, el: 0.4 if 200 <= az <= 240 and el < 25 else 1.0,         # tree, partly transparent
    "morning": lambda az, el: 0.0 if 70 <= az <= 130 and el < 20 else 1.0,       # house east, sun nearly behind
}


def generate(solar, model, geo, mask, seed: int = 7, days: int = 365, split: str = "solis") -> dict:
    """Samples, satellite GHI and steady hours as learning gets them, plus the truth for evaluation.

    truth[t]: hourly mean power without noise and haze; weather[t]: hourly mean (GHI, DNI, DHI) the
    installation received (the forecast gets the GHI, split like in learning); noon: the clear noon
    hours of February and October; clear: all clear daylight hours.
    split: beam/diffuse split of clear hours, "solis" (clear-sky physics) or "erbs" (the statistical
    split the forecast applies to a measured GHI). The fit uses the forecast's split, so "solis" is
    a model error it does not know about, "erbs" none.
    """
    site = solar.Site(SITE_LAT, SITE_LON, SITE_ALT)
    rng = random.Random(seed)
    samples, sat, steady, truth, weather, noon, clear = [], {}, set(), {}, {}, set(), set()
    for d in range(days):
        is_clear = rng.random() < 0.5
        day_cloud = 1.0 if is_clear else rng.uniform(0.15, 0.85)
        for h in range(24):
            t = START + d * 86400 + h * 3600
            dni_extra = solar.extra_radiation(t + 1800)
            cloud = 1.0 if is_clear else min(max(day_cloud + rng.gauss(0, 0.1), 0.05), 0.9)
            power = ghi_sum = dni_sum = dhi_sum = 0.0
            for i in range(model.SUBSTEPS):
                sun = solar.sun_position(site, t + (i + 0.5) * 3600 / model.SUBSTEPS)
                if sun.elevation <= 0:
                    continue
                ghi, dni, dhi = solar.clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
                if not is_clear or split == "erbs":
                    ghi *= cloud
                    dni, dhi = solar.erbs(ghi, sun.zenith, dni_extra)
                beam, diff = solar.plane_of_array(geo.tilt, geo.azimuth, sun, dni, dhi, ghi, dni_extra)
                power += geo.peak_w * EFF * (beam * mask(sun.azimuth, sun.elevation) + diff) / 1000
                ghi_sum, dni_sum, dhi_sum = ghi_sum + ghi, dni_sum + dni, dhi_sum + dhi
            n = model.SUBSTEPS
            power, mean_ghi = power / n, ghi_sum / n
            truth[t] = power
            weather[t] = (mean_ghi, dni_sum / n, dhi_sum / n)
            measured = power * (1 + rng.gauss(0, 0.02)) if power > 0 else 0.0
            if is_clear and power > 0:
                clear.add(t)
                mid = solar.sun_position(site, t + 1800)
                if time.gmtime(t).tm_mon in HAZE_MONTHS and 150 <= mid.azimuth <= 210:
                    noon.add(t)
                    if rng.random() < 0.25:
                        measured *= rng.uniform(0.5, 0.75)
                steady.add(t)
            samples.append((t, measured))
            sat[t] = mean_ghi * (1 + rng.gauss(0, 0.03)) if mean_ghi > 0 else 0.0
    return {"site": site, "samples": samples, "sat": sat, "steady": steady, "truth": truth,
            "weather": weather, "noon": noon, "clear": clear, "now": START + days * 86400}


def forecast_error(model, geo, learned, data, hours) -> float:
    """Mean absolute hourly error relative to the mean true power over the given hours."""
    hours = [t for t in hours if data["truth"][t] > 0]
    weather = [model.WeatherHour(t, data["weather"][t][0]) for t in hours]
    fc = model.forecast(data["site"], geo, learned, weather)
    total = sum(data["truth"][t] for t in hours)
    return sum(abs(fc[t] - data["truth"][t]) for t in hours) / total if total else 0.0


def shade_errors(solar, model, geo, learned, data, mask) -> tuple[float, float]:
    """(false shade, shade error) over the sun positions of the year with direct light on the plane.

    false shade: share of free positions with a learned transmission below 0.9;
    shade error: mean |learned - true| transmission at shaded positions (0 without an obstacle).
    """
    site = data["site"]
    free = false = 0
    shaded, err = 0, 0.0
    for d in range(0, 365, 3):
        for m in range(0, 1440, 20):
            sun = solar.sun_position(site, START + d * 86400 + m * 60)
            if sun.elevation <= 2 or solar.angle_of_incidence(geo.tilt, geo.azimuth, sun.zenith, sun.azimuth) >= 80:
                continue
            true = mask(sun.azimuth, sun.elevation)
            value = model.transmission(learned, sun.azimuth, sun.elevation)
            if true >= 1.0:
                free += 1
                false += value < 0.9
            else:
                shaded += 1
                err += abs(value - true)
    return false / max(free, 1), err / shaded if shaded else 0.0
