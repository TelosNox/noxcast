"""Snow in the forecast: only where the array has shown in its history how snow lies on it."""
import pytest
from conftest import load
from test_dead_days import GEO, SITE, START, _cold, _snow_power

model = load("model")
explain = load("explain")
fc = load("forecasting")

H = 3600.0
LEARNED = {"hours": 5, "share": 0.2, "windows": 3}


def _sky(days):
    return {t: model.clearsky_weather(SITE, t).ghi for t in _snow_power(days, {})}


def test_snowfalls_the_array_cleared_from_teach_how_long_and_how_much_snow_holds_back():
    snowfall = {START + 86400: 3.0, START + 10 * 86400: 3.0}
    power = _snow_power(14, {1: 0.3, 2: 0.3, 10: 0.3, 11: 0.3})
    _, _, cleared = model.snow_scan(SITE, GEO, power, snowfall, _cold(14), _sky(14))
    assert len(cleared) == 2
    for hours, share in cleared:
        assert 25 <= hours <= 40                       # two days of daylight, until the next morning shows free modules
        assert share == pytest.approx(0.3, abs=0.1)
    learned = model.learn_snow(cleared)
    assert learned["windows"] == 2 and learned["share"] == pytest.approx(0.3, abs=0.1)


def test_nothing_is_learned_without_satellite_or_from_one_snowfall():
    snowfall = {START + 86400: 3.0, START + 10 * 86400: 3.0}
    power = _snow_power(14, {1: 0.3, 2: 0.3, 10: 0.3, 11: 0.3})
    assert model.snow_scan(SITE, GEO, power, snowfall, _cold(14))[2] == []
    assert model.learn_snow([(30, 0.3)]) is None


def _weather(temp, snow_at=START, cm=2.0):
    return [model.WeatherHour(START + h * H, 300.0, temp=temp, snow=cm if START + h * H == snow_at else None)
            for h in range(24)]


def _day_fc():
    return {START + h * H: 100.0 if 8 <= h <= 16 else 0.0 for h in range(24)}


def test_forecast_snow_covers_the_learned_daylight_hours():
    cover = model.snow_cover(SITE, model.LearnedArray(snow=LEARNED), _weather(-2.0), _day_fc())
    assert cover == {START + h * H: 0.2 for h in range(8, 13)}


def test_no_cover_without_learned_snow_in_warm_air_or_after_thaw():
    assert model.snow_cover(SITE, model.LearnedArray(), _weather(-2.0), _day_fc()) == {}
    assert model.snow_cover(SITE, model.LearnedArray(snow=LEARNED), _weather(5.0), _day_fc()) == {}
    assert model.snow_cover(SITE, model.LearnedArray(snow=LEARNED), _weather(-2.0, cm=0.5), _day_fc()) == {}
    # 2 °C after the snow: 24 K h are reached at noon
    thaw = model.snow_cover(SITE, model.LearnedArray(snow=LEARNED), _weather(2.0), _day_fc())
    assert thaw == {START + h * H: 0.2 for h in range(8, 12)}


def test_the_snow_step_explains_the_snowed_forecast():
    weather = [model.WeatherHour(START + h * H, model.clearsky_weather(SITE, START + h * H).ghi, temp=-2.0,
                                 snow=2.0 if h == 1 else None) for h in range(24)]
    learned = model.LearnedArray(snow=LEARNED)
    published = fc.snowed(SITE, learned, weather, model.forecast(SITE, GEO, learned, weather))
    steps = explain.hour_steps(SITE, GEO, learned, weather)
    snowy = [t for t, s in steps.items() if "snow" in s]
    assert len(snowy) == 5
    for t, s in steps.items():
        assert s["result"] == pytest.approx(published[t], abs=0.1)
        assert sum(v for k, v in s.items() if k != "result") == pytest.approx(s["result"], abs=0.5)
        assert explain.unpacked(explain.packed(s)) == pytest.approx(s, abs=0.5)
    # snow of the hours before still lies on the modules: the steps of a later start continue its cover
    later = explain.hour_steps(SITE, GEO, learned, weather[5:], weather[:5])
    assert [t for t, s in later.items() if "snow" in s] == [t for t in snowy if t >= START + 5 * H]
    assert [t for t, s in explain.hour_steps(SITE, GEO, learned, weather[5:]).items() if "snow" in s] == []
