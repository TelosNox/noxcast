"""Array checks: geometry fit, efficiency by month, clear-sky balance per month."""
from conftest import load

solar = load("solar")
model = load("model")
ac = load("arraycheck")

SITE = solar.Site(48.97, 8.45, 113)
TRUE = model.ArrayGeometry(tilt=35, azimuth=200, peak_w=1000)
START = 1767225600.0          # 2026-01-01 UTC
EFF = 0.9


def clear_samples(geo, days=range(60, 300, 3)):
    samples, ghi = [], {}
    for d in days:
        for h in range(24):
            t = START + d * 86400 + h * 3600
            beam, diff, _, el, cs = model.clearsky_hour(SITE, geo, t)
            if el > 0:
                samples.append((t, 1000 * EFF * (beam + diff) / 1000))
                ghi[t] = cs
    return samples, ghi


def test_geometry_check_finds_the_azimuth_the_data_fits():
    samples, ghi = clear_samples(TRUE)
    wrong = model.ArrayGeometry(tilt=35, azimuth=170, peak_w=1000)
    res = ac.check_geometry(SITE, wrong, model.LearnedArray(efficiency=EFF), samples, ghi)
    assert abs(res["azimuth"] - 200) <= 4 and abs(res["tilt"] - 35) <= 4
    assert res["deviates"] and res["improvement"] > 0.5 and res["spread"] < 0.01


def test_geometry_check_confirms_a_fitting_geometry_and_needs_the_satellite():
    samples, ghi = clear_samples(TRUE)
    res = ac.check_geometry(SITE, TRUE, model.LearnedArray(efficiency=EFF), samples, ghi)
    assert not res["deviates"] and abs(res["azimuth"] - 200) <= 4
    assert ac.check_geometry(SITE, TRUE, model.LearnedArray(efficiency=EFF), samples, None) is None


def test_efficiency_by_month_recovers_the_efficiency():
    samples, ghi = clear_samples(TRUE)
    months = ac.efficiency_by_month(SITE, TRUE, model.LearnedArray(efficiency=EFF), samples, ghi)
    assert len(months) >= 7
    assert all(abs(v - EFF) < 0.01 for v, n in months.values())


def test_month_balance_shows_shade_and_heat():
    shaded = model.LearnedArray(efficiency=EFF, temp_coeff=-0.004,
                                cells={f"{a},{e}": 0.0 for a in range(9, 24) for e in range(0, 4)})
    air = {START + m * 30 * 86400 + 12 * 3600: 25.0 for m in range(12)}
    months = ac.month_balance(SITE, TRUE, shaded, 2026, air)
    dec, jun = months[11], months[5]
    assert dec["shade"] < jun["shade"] <= 0                # low winter sun is hit harder by a low obstacle
    assert dec["shade"] < -0.3
    assert jun["temperature"] < 0 and jun["air_temp"] == 25.0
    assert dec["reflection"] == 0 and jun["clear_wh"] > dec["clear_wh"]
