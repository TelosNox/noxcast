"""Hour steps of the day view: the steps add up to the forecast."""
from conftest import load

solar = load("solar")
model = load("model")
explain = load("explain")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=35, azimuth=180, peak_w=1000)
DAY = 1782864000.0            # 2026-07-01 UTC


def test_steps_add_up_to_the_forecast_and_show_each_effect():
    learned = model.LearnedArray(efficiency=0.9, temp_coeff=-0.004,
                                 cells={f"{a},{e}": 0.0 for a in range(0, 15) for e in range(0, 6)},
                                 reflection={"10,1": 0.1})
    weather = [model.WeatherHour(DAY + h * 3600, ghi, None, None, 28.0)
               for h, ghi in ((6, 120.0), (9, 300.0), (12, 850.0), (15, 500.0))]
    steps = explain.hour_steps(SITE, GEO, learned, weather)
    fc = model.forecast(SITE, GEO, learned, weather)
    for t, s in steps.items():
        total = s["clear"] + sum(s[k] for k in explain.STEPS[1:])
        assert abs(total - s["result"]) < 0.5 and abs(s["result"] - fc[t]) < 0.5
    morning, noon = steps[DAY + 6 * 3600], steps[DAY + 12 * 3600]
    assert morning["shade"] < 0 and noon["shade"] == 0       # low eastern sun is behind the obstacle
    assert noon["heat"] < 0 and noon["clouds"] < 0 < noon["clear"]


def test_clearness_pairs_weather_with_clear_sky():
    out = explain.clearness(SITE, [model.WeatherHour(DAY + 12 * 3600, 400.0), model.WeatherHour(DAY, 0.0)])
    ghi, cs = out[DAY + 12 * 3600]
    assert ghi == 400.0 and cs > 800 and DAY not in out
