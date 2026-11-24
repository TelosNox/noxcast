"""Hour steps of the day view: the steps add up to the forecast."""
from conftest import load

solar = load("solar")
model = load("model")
explain = load("explain")
archive = load("archive")
H = 3600.0

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


def _hour(ts, forecast, actual=None, measured=None, steps=None, curtailed=False, limited=False):
    return archive.Hour(ts, 500.0, 20.0, {}, {"a": forecast}, None if actual is None else {"a": actual}, {}, curtailed,
                        None if measured is None else 400.0, None if measured is None else {"a": measured},
                        {"a": steps} if steps else {}, limited=limited)


def test_deviation_splits_actual_minus_forecast_into_parts_that_add_up():
    free = [400.0, -50.0, 0.0, 0.0, 0.0, -10.0]              # no shade
    shaded = [400.0, -50.0, -100.0, 0.0, 0.0, -10.0]         # shade takes 100 of 350 Wh after clouds
    hours = [_hour(0.0, 340.0, actual=300.0, measured=360.0, steps=free),          # clearer sky, model 60 too high
             _hour(H, 240.0, actual=150.0, measured=250.0, steps=shaded),          # miss behind learned shade
             _hour(2 * H, 340.0, actual=200.0, measured=340.0, steps=free, curtailed=True),
             _hour(3 * H, 100.0, actual=80.0),                                      # no satellite: not split
             _hour(4 * H, 50.0)]                                                    # no actual value
    base = {0.0: 340.0, H: 240.0, 2 * H: 340.0, 3 * H: 100.0, 4 * H: 50.0}
    parts = explain.deviation(hours, "a", base)
    assert parts[0.0] == {"weather": 20.0, "model": -60.0}
    assert parts[H] == {"weather": 10.0, "shade": -100.0}
    assert parts[2 * H] == {"weather": 0.0, "limit": -140.0}
    assert parts[3 * H] == {"open": -20.0} and parts[4 * H] == {"open": -50.0}
    total = sum(v for p in parts.values() for v in p.values())
    assert abs(sum(base.values()) + total - (300 + 150 + 200 + 80)) < 1e-6
