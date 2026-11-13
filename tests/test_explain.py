"""Hour steps of the day view: the steps add up to the forecast."""
from conftest import load

solar = load("solar")
model = load("model")
explain = load("explain")

SITE = solar.Site(48.0, 8.0, 300.0)
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


def test_limit_step_ends_the_steps_at_the_capped_forecast():
    step = explain.unpacked([900.0, -100.0, 0.0, 0.0, 0.0, -20.0])            # 780 Wh before the cap
    out = explain.with_limit({1.0: step, 2.0: step, 3.0: step, 4.0: step}, {1.0: 600.0, 2.0: 780.0, 4.0: 779.7})
    assert out[1.0]["limit"] == -180.0 and out[1.0]["result"] == 600.0
    assert sum(out[1.0][k] for k in (*explain.STEPS, "limit")) == 600.0       # the steps still add up
    assert all("limit" not in out[t] for t in (2.0, 3.0, 4.0))                # equal, no value, only rounding
    assert step["result"] == 780.0                                             # the input is left as it was
