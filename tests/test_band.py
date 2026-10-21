"""Forecast band: actual / forecast quantiles per weather class from the archive."""
import random

from conftest import load

bd = load("band")

CLEAR = 800.0


def archive(days=60, seed=3):
    rng = random.Random(seed)
    out = {}
    for d in range(days):
        clear_day = d % 2 == 0
        hours = {}
        for h in range(8, 18):
            ts = d * 86400 + h * 3600
            ghi = CLEAR * (0.9 if clear_day else 0.25)
            fc = 500.0 if clear_day else 150.0
            spread = 0.05 if clear_day else 0.5          # overcast forecasts are much less reliable
            hours[str(ts)] = {"ghi": ghi, "forecast": {"a": fc}, "actual": {"a": fc * rng.uniform(1 - spread, 1 + spread)}}
        out[f"d{d}"] = {"hours": hours}
    return out


def test_band_is_narrow_for_clear_and_wide_for_overcast_forecasts():
    band = bd.learn_band(archive(), lambda ts: CLEAR)
    lo_c, hi_c, n_c = band["daily"]["clear"]
    lo_o, hi_o, n_o = band["daily"]["overcast"]
    assert n_c == n_o == 30
    assert hi_c - lo_c < 0.06 < 0.2 < hi_o - lo_o
    lo, hi = bd.day_band(band, 10000.0, 0.9)
    assert 9500 < lo < 10000 < hi < 10500
    hours = bd.hour_band(band, {0.0: 100.0, 3600.0: 100.0}, {0.0: 0.2, 3600.0: 0.95})
    assert hours[0.0][1] - hours[0.0][0] > hours[3600.0][1] - hours[3600.0][0]


def test_unknown_class_falls_back_to_all_days_and_curtailed_hours_are_skipped():
    a = archive()
    band = bd.learn_band(a, lambda ts: CLEAR)
    assert "mixed" not in band["daily"] and bd.ratios(band, "daily", 0.5) == tuple(band["daily"]["all"][:2])
    for day in a.values():                     # curtailed hours with a low actual must not widen the band
        for h in day["hours"].values():
            h["curtailed"] = True
    assert bd.learn_band(a, lambda ts: CLEAR) == {"daily": {}, "hourly": {}}
    assert bd.day_band(None, 1000.0, 0.9) is None


def test_days_without_actuals_are_ignored():
    a = archive(days=4)
    del a["d0"]["hours"][next(iter(a["d0"]["hours"]))]["actual"]
    band = bd.learn_band(a, lambda ts: CLEAR)
    assert band["daily"] == {}                 # too few days for any class
