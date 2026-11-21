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


def covered(days=40, clear_day=True, curtail=None):
    """Archive with a published band: actual of the day 5.0 kWh, band per day and hour from the day index."""
    out = {}
    for d in range(days):
        hours = {}
        for h in range(8, 18):
            ts = d * 86400 + h * 3600
            actual = 500.0
            hours[str(ts)] = {"ghi": CLEAR * (0.9 if clear_day else 0.25), "forecast": {"a": 480.0, "b": 0.0},
                              "actual": {"a": actual, "b": 0.0}, "band": [400.0, 600.0] if d % 5 else [520.0, 600.0]}
            if curtail is not None and d == curtail and h == 12:
                hours[str(ts)]["curtailed"] = True
        # every 10th day below the band, every 10th above, the rest inside
        band = [5.5, 6.0] if d % 10 == 0 else [4.0, 4.5] if d % 10 == 1 else [4.5, 5.5]
        out[f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"] = {"hours": hours, "band": band}
    return out


def test_coverage_counts_days_and_hours_against_the_published_band():
    c = bd.coverage(covered(), lambda ts: CLEAR)
    assert c["min_days"] == bd.COVERAGE_MIN_DAYS and c["target"] == [0.1, 0.8, 0.1]
    assert c["days"]["all"] == {"n": 40, "days": 40, "below": 4, "inside": 32, "above": 4}
    assert c["days"]["clear"] == c["days"]["all"] and "overcast" not in c["days"]
    # every 5th day the hourly band starts above the actual: 8 of 40 days, 10 hours each
    assert c["hours"]["clear"] == {"n": 400, "days": 40, "below": 80, "inside": 320, "above": 0}
    assert c["left_out"] == 0


def test_coverage_leaves_out_curtailed_hours_and_their_day():
    c = bd.coverage(covered(curtail=2), lambda ts: CLEAR)
    assert c["days"]["all"]["n"] == 39 and c["left_out"] == 1
    assert c["hours"]["all"]["n"] == 399


def test_coverage_uses_the_weather_class_of_the_forecast_and_skips_days_without_band():
    a = covered(days=3, clear_day=False)
    a["2026-02-01"] = {"hours": {"0": {"ghi": 100.0, "forecast": {"a": 1.0}, "actual": {"a": 1.0}}}}   # archived before bands
    a["2026-02-02"] = {"hours": {"0": {"ghi": 100.0, "forecast": {"a": 1.0}, "actual": None}}, "band": [0.0, 1.0]}
    c = bd.coverage(a, lambda ts: CLEAR)
    assert set(c["days"]) == {"all", "overcast"} and c["days"]["all"]["n"] == 3


def test_coverage_leaves_out_hours_without_the_actual_of_every_array():
    a = covered()
    hour = a["2026-01-04"]["hours"][str(3 * 86400 + 12 * 3600)]
    del hour["actual"]["b"]                          # the second sensor was unavailable
    c = bd.coverage(a, lambda ts: CLEAR)
    assert c["days"]["all"]["n"] == 39 and c["left_out"] == 1 and c["hours"]["all"]["n"] == 399


def test_quantiles_leave_a_tenth_of_new_days_below_and_above_even_with_few_days():
    """Rank floor / ceil of q (n + 1): with 8 days the band spans all of them, with 99 it is the 10th and 90th value."""
    few = [float(v) for v in range(8)]
    assert (bd._quantile(few, bd.Q_LOW), bd._quantile(few, bd.Q_HIGH)) == (0.0, 7.0)
    many = [float(v) for v in range(1, 100)]
    assert (bd._quantile(many, bd.Q_LOW), bd._quantile(many, bd.Q_HIGH)) == (10.0, 90.0)
    assert bd._quantile([3.0], bd.Q_LOW) == bd._quantile([3.0], bd.Q_HIGH) == 3.0


def test_hours_without_every_array_actual_do_not_teach_the_band():
    a = archive()
    for day in a.values():                       # a second array without its actual value: as if its sensor failed
        for h in day["hours"].values():
            h["forecast"]["b"] = 100.0
    band = learn(a)
    assert band["daily"] == {} and band["hourly"] == {}
