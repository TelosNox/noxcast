"""Today's effects: shade loss and reflection gain, only significant periods."""
from datetime import datetime, timedelta, timezone

from conftest import load

solar = load("solar")
model = load("model")
effects = load("effects")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=60, azimuth=135, peak_w=920)
TZ = timezone(timedelta(hours=2))
DAY = datetime(2026, 10, 14, tzinfo=TZ).timestamp()


def _cells(value: float) -> dict[str, float]:
    return {f"{a},{e}": value for a in range(36) for e in range(14)}


def _weather(factor: float = 1.0) -> list:
    return [model.WeatherHour(w.start, w.ghi * factor, temp=12.0)
            for w in (model.clearsky_weather(SITE, DAY + h * 3600) for h in range(24))]


def _morning_shade() -> model.LearnedArray:
    cells = _cells(1.0)
    for a in range(9, 15):                                # east to south-east shaded at low sun
        for e in range(5):
            cells[f"{a},{e}"] = 0.1
    return model.LearnedArray(efficiency=0.9, cells=cells)


def test_shade_loss_is_reported_with_its_period():
    learned = _morning_shade()
    loss, gain = effects.hourly_effects(SITE, GEO, learned, _weather())
    assert sum(loss.values()) > 100 and sum(gain.values()) == 0
    periods = effects.significant_periods(SITE, GEO, learned, DAY, loss, gain, 3000)
    assert len(periods) == 1 and periods[0]["type"] == "shade" and periods[0]["wh"] > 50
    assert datetime.fromtimestamp(periods[0]["start"], TZ).hour < 10


def test_clear_sky_share_bounds_the_share_of_today():
    learned = _morning_shade()
    shade_clear, refl_clear = effects.clear_shares(SITE, GEO, learned, _weather())
    assert max(shade_clear.values()) > 0.5 and all(0.0 <= v <= 1.0 for v in shade_clear.values())
    assert max(refl_clear.values(), default=0.0) == 0.0
    loss, _ = effects.hourly_effects(SITE, GEO, learned, _weather(0.3))          # cloudy: diffuse light, less loss
    free = model.forecast(SITE, GEO, model.LearnedArray(efficiency=0.9), _weather(0.3))
    for t, wh in loss.items():
        if free.get(t, 0.0) > 50:
            assert wh / free[t] <= shade_clear[t] + 0.02


def test_shade_is_shown_even_when_it_costs_little():
    learned = _morning_shade()
    loss, gain = effects.hourly_effects(SITE, GEO, learned, _weather(0.05))   # overcast: shade costs little
    periods = effects.significant_periods(SITE, GEO, learned, DAY, loss, gain, 3000)
    assert [p["type"] for p in periods] == ["shade"] and periods[0]["wh"] < 15


def test_small_reflection_is_dropped():
    learned = model.LearnedArray(efficiency=0.9, cells=_cells(1.0), reflection={"11,1": 0.05, "12,1": 0.05})
    loss, gain = effects.hourly_effects(SITE, GEO, learned, _weather(0.05))
    assert effects.significant_periods(SITE, GEO, learned, DAY, loss, gain, 3000) == []


def test_reflection_gain_is_a_period():
    learned = model.LearnedArray(efficiency=0.9, cells=_cells(1.0), reflection={"11,1": 0.05, "12,1": 0.05,
                                                                                "11,0": 0.05, "12,0": 0.05})
    loss, gain = effects.hourly_effects(SITE, GEO, learned, _weather())
    periods = effects.significant_periods(SITE, GEO, learned, DAY, loss, gain, 300)
    assert any(p["type"] == "reflection" for p in periods)
