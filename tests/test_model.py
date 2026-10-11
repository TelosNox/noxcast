"""Synthetic end-to-end check: a known shading mask and efficiency must be recovered."""
import random

from conftest import load

solar = load("solar")
model = load("model")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=60, azimuth=135, peak_w=920)
START = 1767225600.0          # 2026-01-01 UTC
EFF = 0.93


def true_transmission(az: float, el: float) -> float:
    return 0.0 if (az < 120 and el < 20) else 1.0


def synthetic_power(t: float, cloud: float) -> float:
    """Mean power over the hour with the true mask applied per sub-step."""
    dni_extra = solar.extra_radiation(t + 1800)
    total = 0.0
    for i in range(model.SUBSTEPS):
        sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
        if sun.elevation <= 0:
            continue
        ghi, dni, dhi = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, dni_extra)
        beam, diff = solar.plane_of_array(GEO.tilt, GEO.azimuth, sun, dni * cloud, dhi, ghi)
        total += GEO.peak_w * EFF * (beam * true_transmission(sun.azimuth, sun.elevation) + diff) / 1000
    return total / model.SUBSTEPS


def make_samples(days: int = 365):
    rng = random.Random(1)
    samples = []
    for d in range(days):
        cloud = 1.0 if rng.random() < 0.35 else rng.uniform(0.0, 0.7)
        for h in range(24):
            t = START + d * 86400 + h * 3600
            samples.append((t, synthetic_power(t, cloud)))
    return samples


def test_learn_recovers_mask_and_efficiency():
    samples = make_samples()
    learned = model.learn(SITE, GEO, samples, now=START + 365 * 86400)
    assert abs(learned.efficiency - EFF) < 0.03
    # Cells well inside / outside the mask. An hourly mean spans ~15° of azimuth, so cells next
    # to the mask edge legitimately see a mix of shaded and unshaded sub-steps.
    shaded = [v for k, v in learned.cells.items() if (lambda a, e: a * 10 + 10 <= 100 and e * 5 + 5 <= 15)(*map(int, k.split(",")))]
    free = [v for k, v in learned.cells.items() if (lambda a, e: a * 10 >= 130 or e * 5 >= 25)(*map(int, k.split(",")))]
    assert shaded and max(shaded) < 0.2
    assert free and sum(free) / len(free) > 0.9
    assert learned.coverage > 0.8


def test_forecast_matches_clear_day():
    learned = model.learn(SITE, GEO, make_samples(), now=START + 365 * 86400)
    day = START + 170 * 86400
    weather = []
    for h in range(24):
        t = day + h * 3600
        # hourly mean clear-sky irradiance as "weather"
        g = d = n = 0.0
        for i in range(model.SUBSTEPS):
            sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
            cs = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, solar.extra_radiation(t))
            g, n, d = g + cs[0] / 6, n + cs[1] / 6, d + cs[2] / 6
        weather.append(model.WeatherHour(t, g, n, d))
    fc = model.forecast(SITE, GEO, learned, weather)
    expected = sum(synthetic_power(t, 1.0) for t in fc)
    assert abs(sum(fc.values()) - expected) / expected < 0.08
