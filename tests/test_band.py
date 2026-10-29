"""Forecast band: clearness shifts per weather class from the archive, bounded by a clear sky."""
import random

from conftest import load

bd = load("band")

CLEAR = 800.0
CLEAR_WH = 600.0             # clear-sky output of the array per hour


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


def learn(a):
    return bd.learn_band(a, lambda ts: CLEAR, lambda ts, ids: CLEAR_WH * len(ids))


def test_band_is_narrow_for_clear_and_wide_for_overcast_forecasts():
    band = learn(archive())
    lo_c, hi_c, n_c = band["daily"]["clear"]
    lo_o, hi_o, n_o = band["daily"]["overcast"]
    assert n_c == n_o == 30
    assert hi_c - lo_c < 0.05 < hi_o - lo_o
    lo, hi = bd.day_band(band, 5000.0, 6000.0, 0.9)
    assert 4700 < lo < 5000 < hi < 5300
    hours = bd.hour_band(band, {0.0: 100.0, 3600.0: 100.0}, {0.0: 600.0, 3600.0: 600.0}, {0.0: 0.2, 3600.0: 0.95})
    assert hours[0.0][1] - hours[0.0][0] > hours[3600.0][1] - hours[3600.0][0]


def test_band_never_goes_above_a_clear_sky():
    band = {"version": bd.VERSION, "daily": {"mixed": [-0.3, 0.8, 30]}, "hourly": {"mixed": [-0.3, 0.8, 300]}}
    lo, hi = bd.day_band(band, 4600.0, 8300.0, 0.5)       # 4.6 kWh forecast, 8.3 kWh with a clear sky
    assert hi == 8300.0 * bd.CLEARNESS_MAX and lo == 4600.0 - 0.3 * 8300.0
    lo, hi = bd.day_band(band, 1000.0, 8300.0, 0.5)
    assert lo == 0.0                                      # and never below no sun
    hours = bd.hour_band(band, {0.0: 400.0}, {0.0: 450.0}, {0.0: 0.5})
    assert hours[0.0][1] == 450.0 * bd.CLEARNESS_MAX


def test_unknown_class_falls_back_to_all_days_and_curtailed_hours_are_skipped():
    a = archive()
    band = learn(a)
    assert "mixed" not in band["daily"] and bd.shifts(band, "daily", 0.5) == tuple(band["daily"]["all"][:2])
    for day in a.values():                     # curtailed hours with a low actual must not widen the band
        for h in day["hours"].values():
            h["curtailed"] = True
    assert learn(a) == {"version": bd.VERSION, "daily": {}, "hourly": {}}
    assert bd.day_band(None, 1000.0, 2000.0, 0.9) is None


def test_days_without_actuals_are_ignored():
    a = archive(days=4)
    del a["d0"]["hours"][next(iter(a["d0"]["hours"]))]["actual"]
    assert learn(a)["daily"] == {}             # too few days for any class


def test_a_band_stored_as_ratios_is_dropped():
    assert bd.valid({"daily": {"clear": [0.9, 1.1, 30]}, "hourly": {}}) is None
    band = learn(archive())
    assert bd.valid(band) is band
