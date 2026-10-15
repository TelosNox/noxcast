"""Synthetic end-to-end check: a known shading mask and efficiency must be recovered."""
import math
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


TEMP_COEFF = -0.0026


def make_temperature_samples(days: int = 365):
    """Like make_samples, with a seasonal air temperature and a true temperature loss."""
    rng = random.Random(2)
    samples, air, sat = [], {}, {}
    for d in range(days):
        cloud = 1.0 if rng.random() < 0.35 else rng.uniform(0.0, 0.7)
        season = -math.cos(2 * math.pi * (d - 15) / 365)
        for h in range(24):
            t = START + d * 86400 + h * 3600
            temp = 10 + 12 * season + 5 * math.sin(math.pi * (h - 8) / 12) + rng.gauss(0, 2)
            beam, diff, _, _, cs_ghi = model.clearsky_hour(SITE, GEO, t)
            sat[t] = cs_ghi * (1.0 if cloud == 1.0 else 0.3 + 0.5 * cloud)
            poa = (beam + diff) * (cloud if cloud < 1 else 1)
            factor = 1 + TEMP_COEFF * (temp + model.HEATING_K * poa / 1000 - 25)
            samples.append((t, synthetic_power(t, cloud) * factor))
            air[t] = temp
    return samples, air, sat


def test_learn_temperature_coefficient():
    samples, air, sat = make_temperature_samples()
    learned = model.learn(SITE, GEO, samples, now=START + 365 * 86400, air_temp=air, satellite_ghi=sat)
    assert learned.temp_learned and learned.temp_source == "learned"
    assert abs(learned.temp_coeff - TEMP_COEFF) < 0.0012
    assert abs(learned.efficiency - EFF) < 0.04


def test_without_temperature_no_correction():
    learned = model.learn(SITE, GEO, make_samples(30), now=START + 30 * 86400)
    assert learned.temp_coeff == 0.0 and not learned.temp_learned


def test_satellite_filter_and_agreement():
    samples = make_samples()
    rng = random.Random(1)
    sat = {}
    for d in range(365):                      # same cloud sequence as make_samples
        cloud = 1.0 if rng.random() < 0.35 else rng.uniform(0.0, 0.7)
        for h in range(24):
            t = START + d * 86400 + h * 3600
            *_, cs_ghi = model.clearsky_hour(SITE, GEO, t)
            sat[t] = cs_ghi * (1.0 if cloud == 1.0 else 0.3 + 0.5 * cloud)
    learned = model.learn(SITE, GEO, samples, now=START + 365 * 86400, satellite_ghi=sat)
    assert learned.clear_filter
    assert abs(learned.efficiency - EFF) < 0.03
    assert learned.agreement is not None and learned.agreement > 0.8


def test_datasheet_coefficient_is_used_fixed():
    samples, air, _ = make_temperature_samples(120)
    learned = model.learn(SITE, GEO, samples, now=START + 120 * 86400, air_temp=air, datasheet_temp_coeff=-0.0026)
    assert learned.temp_coeff == -0.0026 and learned.temp_source == "datasheet"
    assert abs(learned.efficiency - EFF) < 0.04


def test_without_satellite_uses_typical_coefficient():
    samples, air, _ = make_temperature_samples(120)
    learned = model.learn(SITE, GEO, samples, now=START + 120 * 86400, air_temp=air)
    assert learned.temp_coeff == model.DEFAULT_TEMP_COEFF and learned.temp_source == "default"


def test_implausible_learned_coefficient_is_rejected():
    rows = [(t_cell, 0.9 + 0.001 * t_cell, 1.0) for t_cell in range(0, 60) for _ in range(20)]   # rises with heat
    coeff, learned = model._learn_temp_coeff(rows)
    assert coeff == model.DEFAULT_TEMP_COEFF and not learned


def _column(ai, values, start=0):
    return {f"{ai},{start + i}": v for i, v in enumerate(values)}


def test_shape_raises_dips_and_keeps_cores():
    cells = _column(18, [0.2, 0.5, 1.0, 0.85, 1.0, 1.0])          # dip above the free band start
    out = model.shape_cells(cells)
    assert out["18,0"] == 0.2 and out["18,1"] == 0.5
    assert out["18,3"] == 1.0


def test_shape_dead_band_and_edges():
    cells = {**_column(10, [0.3, 0.8, 0.95]), **_column(20, [1.0, 0.8, 1.0], start=5)}
    out = model.shape_cells(cells)
    assert out["10,1"] == 0.8                                       # transition next to a core
    assert out["10,2"] == 1.0                                       # dead band
    assert out["20,6"] == 1.0                                       # isolated 0.8: temperature, haze


def test_shape_side_edge_counts():
    # a wall to the side: shade changes across azimuth, not elevation
    cells = {"12,5": 0.3, "13,5": 0.8, "14,5": 1.0, "12,6": 0.35, "13,6": 0.82, "14,6": 1.0}
    out = model.shape_cells(cells)
    assert out["13,5"] == 0.8 and out["13,6"] == 0.82


def test_shape_overhang_from_above():
    out = model.shape_cells(_column(22, [1.0, 1.0, 1.0, 0.8, 0.4]))
    assert out["22,4"] == 0.4 and out["22,3"] == 0.8 and out["22,0"] == 1.0


def test_direction_factor_applies_to_beam_and_may_exceed_one():
    learned = model.LearnedArray(efficiency=0.9, temp_coeff=0.0)
    boosted = model.LearnedArray(efficiency=0.9, temp_coeff=0.0,
                                 direction={f"{a},{e}": 1.1 for a in range(18) for e in range(6)})
    hour = model.WeatherHour(start=1781258400.0, ghi=800.0)        # June noon, clear
    base = model.forecast(SITE, GEO, learned, [hour])[hour.start]
    more = model.forecast(SITE, GEO, boosted, [hour])[hour.start]
    assert 1.03 < more / base < 1.1                                  # beam only, diffuse unchanged
    assert model.direction_factor(model.LearnedArray(), 180, 40) == 1.0


def test_clearsky_profile_matches_hourly_clear_sky():
    learned = model.LearnedArray(efficiency=0.9, temp_coeff=0.0)
    start = 1781258400.0
    profile = model.clearsky_profile(SITE, GEO, learned, start)
    hourly = model.forecast(SITE, GEO, learned, [model.clearsky_weather(SITE, start)])[start]
    assert len(profile) == 12 and abs(sum(profile) / 12 / hourly - 1) < 0.02


SKY_VIEW = 0.5


def make_sky_view_samples(days: int = 240):
    """Clear and overcast days for a plane that sees only half of the sky; satellite GHI as measured."""
    rng = random.Random(3)
    samples, sat = [], {}
    for d in range(days):
        cloud = 1.0 if rng.random() < 0.5 else 0.25
        for h in range(24):
            t = START + d * 86400 + h * 3600
            dni_extra = solar.extra_radiation(t + 1800)
            power = ghi_sum = 0.0
            for i in range(model.SUBSTEPS):
                sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
                if sun.elevation <= 0:
                    continue
                ghi = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, dni_extra)[0] * cloud
                dni, dhi = solar.erbs(ghi, sun.zenith, dni_extra)
                beam, diff = solar.plane_of_array(GEO.tilt, GEO.azimuth, sun, dni, dhi, ghi, dni_extra, sky_view=SKY_VIEW)
                power += GEO.peak_w * EFF * (beam + diff) / 1000
                ghi_sum += ghi
            samples.append((t, power / model.SUBSTEPS))
            sat[t] = ghi_sum / model.SUBSTEPS
    return samples, sat


def test_sky_view_is_learned_from_overcast_hours():
    samples, sat = make_sky_view_samples()
    learned = model.learn(SITE, GEO, samples, now=START + 240 * 86400, satellite_ghi=sat)
    assert abs(learned.sky_view - SKY_VIEW) < 0.08
    assert abs(learned.efficiency - EFF) < 0.03
    overcast = [t for t, _ in samples if sat[t] > 30 and sat[t] / max(model.clearsky_hour(SITE, GEO, t)[4], 1) < 0.4]
    weather = [model.WeatherHour(start=t, ghi=sat[t]) for t in overcast]
    fc = model.forecast(SITE, GEO, learned, weather)
    actual = dict(samples)
    assert abs(sum(fc.values()) / sum(actual[t] for t in overcast) - 1) < 0.08


def test_sky_view_needs_satellite_data():
    assert model.learn(SITE, GEO, make_samples(60), now=START + 60 * 86400).sky_view == 1.0
