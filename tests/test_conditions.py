"""Weather condition of an hour and a day from the averaged values."""
from conftest import load

conditions = load("conditions")
model = load("model")


def hour(**kw):
    return model.WeatherHour(0.0, 0.0, **kw)


def test_hour_condition():
    assert conditions.hour_condition(hour(cloud=10), night=False) == "sunny"
    assert conditions.hour_condition(hour(cloud=10), night=True) == "clear-night"
    assert conditions.hour_condition(hour(cloud=50), night=False) == "partlycloudy"
    assert conditions.hour_condition(hour(cloud=90), night=False) == "cloudy"
    assert conditions.hour_condition(hour(cloud=90, precip=0.5, temp=12), night=False) == "rainy"
    assert conditions.hour_condition(hour(cloud=90, precip=6, temp=12), night=False) == "pouring"
    assert conditions.hour_condition(hour(cloud=90, precip=0.5, temp=-1), night=False) == "snowy"
    assert conditions.hour_condition(hour(cloud=90, precip=0.5, temp=1), night=False) == "snowy-rainy"
    assert conditions.hour_condition(hour(cloud=90, precip=0.5, code=95), night=False) == "lightning-rainy"
    assert conditions.hour_condition(hour(cloud=100, code=45), night=False) == "fog"
    assert conditions.hour_condition(hour(cloud=30, wind=12), night=False) == "windy"


def test_day_summary_uses_daylight_for_the_sky():
    night = [model.WeatherHour(h * 3600.0, 0.0, cloud=100, temp=5) for h in range(8)]
    day = [model.WeatherHour(h * 3600.0, 300.0, cloud=10, temp=15, precip=0.05) for h in range(8, 20)]
    s = conditions.day_summary(night + day, lambda t: 8 * 3600 <= t < 20 * 3600)
    assert s["condition"] == "sunny" and s["temp_max"] == 15 and s["temp_min"] == 5 and s["precip"] == 0.6
    wet = day[:4] + [model.WeatherHour(h * 3600.0, 50.0, cloud=100, temp=8, precip=1.0) for h in range(12, 20)]
    assert conditions.day_summary(wet, lambda t: True)["condition"] == "rainy"
