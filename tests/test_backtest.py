"""PV backtest on synthetic data: replay, hold-out with baseline, filters, and that nothing is changed."""
import copy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from conftest import load

archive = load("archive")
backtest = load("backtest")
ln = load("learning")
model = load("model")
solar = load("solar")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(30, 180, 1000)
UTC = timezone.utc
DAY0 = 1776729600.0                     # 2026-04-21 00:00 UTC
NOW = DAY0 + 90 * 86400
ARRAY = ln.ArrayConfig("roof", "Roof", ("pv",), GEO, None)
TRUE = model.LearnedArray(efficiency=0.9)


def even(t: float) -> bool:
    return datetime.fromtimestamp(t, UTC).date().toordinal() % 2 == 0


def noon(t: float) -> bool:
    return 9 <= datetime.fromtimestamp(t, UTC).hour <= 13


def hours(days: int = 90) -> list[float]:
    return [DAY0 + h * 3600 for h in range(days * 24)]


def history(days: int = 90, dip=lambda t: 1.0, satellite: bool = True) -> "ln.PvHistory":
    """Clear days of an array that follows the model exactly, with an optional factor per hour."""
    ts = hours(days)
    sat = {t: model.clearsky_weather(SITE, t).ghi for t in ts}
    fc = model.forecast(SITE, GEO, TRUE, [model.WeatherHour(t, sat[t], temp=15.0) for t in ts])
    return ln.PvHistory({"pv": {t: fc[t] * dip(t) for t in ts}}, {}, {}, {}, {}, {t: 15.0 for t in ts}, sat if satellite else None)


def _archive(days: int, factor: float = 1.5, first: float = DAY0 + 60 * 86400, truth=None) -> dict:
    """Day-ahead forecasts on the true weather, off by a factor, with the actual values."""
    arc: dict = {}
    for d in range(days):
        a = first + d * 86400
        ts = [a + i * 3600 for i in range(24)]
        weather = {t: (model.clearsky_weather(SITE, t).ghi, 15.0) for t in ts}
        fc = model.forecast(SITE, GEO, TRUE, [model.WeatherHour(t, weather[t][0], temp=15.0) for t in ts])
        when = datetime.fromtimestamp(a, UTC).date()
        archive.snapshot(arc, when, "2026-01-01T00:00:00+00:00", weather, {"roof": {t: fc[t] * factor for t in ts}})
        archive.add_actuals(arc, "roof", truth or fc, NOW)
    return arc


def test_score_is_relative_to_the_actual_energy():
    day = lambda t: date(2026, 1, 1) + timedelta(days=int(t // 86400))
    s = backtest.score({0.0: (110.0, 100.0), 3600.0: (90.0, 100.0), 86400.0: (150.0, 100.0)}, day)
    assert s["days"] == 2 and s["hours"] == 3
    assert s["day_error"] == round((0 + 50) / 300, 4)        # errors within a day cancel out
    assert s["hour_error"] == round(70 / 300, 4) and s["bias"] == round(50 / 300, 4)
    assert s["hour_mae_wh"] == round(70 / 3, 1)
    assert backtest.score({}, day)["day_error"] is None


def test_noise_cells_show_as_a_worse_holdout_than_in_sample():
    """A dip only on even days is learned by that half as a shadow, which the other days do not have."""
    noisy = backtest.run(SITE, {"roof": ARRAY}, {}, history(dip=lambda t: 0.6 if even(t) and noon(t) else 1.0), {},
                         NOW, 95, [], UTC)["holdout"]
    clean = backtest.run(SITE, {"roof": ARRAY}, {}, history(), {}, NOW, 95, [], UTC)["holdout"]
    r = noisy["arrays"]["roof"]
    assert r["status"] == "ok" and r["holdout"]["hours"] == r["in_sample"]["hours"] > 500
    assert r["holdout"]["hour_error"] > 1.5 * r["in_sample"]["hour_error"]
    c = clean["arrays"]["roof"]
    assert c["holdout"]["hour_error"] < 0.03 and abs(c["holdout"]["hour_error"] - c["in_sample"]["hour_error"]) < 0.005
    assert noisy["total"]["holdout"] == r["holdout"]         # one array: the total is that array


def test_baseline_shows_what_a_real_shadow_map_contributes():
    """A shadow on all days at the same hours: the map holds on unlearned days, without it the error is large."""
    r = backtest.run(SITE, {"roof": ARRAY}, {}, history(dip=lambda t: 0.5 if noon(t) else 1.0), {}, NOW, 95, [], UTC)["holdout"]["arrays"]["roof"]
    assert r["baseline"]["hour_error"] > 2 * r["holdout"]["hour_error"]
    assert r["baseline"]["bias"] > 2 * abs(r["holdout"]["bias"])
    assert abs(r["holdout"]["hour_error"] - r["in_sample"]["hour_error"]) < 0.02


def test_replay_compares_the_forecast_then_with_todays_model():
    hist = history()
    learned = {"roof": ln.learn_arrays(SITE, {"roof": ARRAY}, hist, NOW, 95, [])["roof"]}
    r = backtest.run(SITE, {"roof": ARRAY}, learned, hist, _archive(10), NOW, 95, [], UTC)["replay"]
    roof = r["arrays"]["roof"]
    assert roof["status"] == "ok" and roof["now"]["days"] == roof["then"]["days"] == 10
    assert abs(roof["then"]["bias"] - 0.5) < 0.01 and roof["then"]["day_error"] > 0.45  # issued 50 % too high
    assert roof["now"]["day_error"] < 0.03                   # today's model on the same weather: right
    assert r["total"]["now"] == roof["now"]


def test_filters_are_those_of_learning_plus_the_archives_curtailed_flag():
    hist = history()
    learned = ln.learn_arrays(SITE, {"roof": ARRAY}, hist, NOW, 95, [])
    arc = _archive(10)
    plain = backtest.run(SITE, {"roof": ARRAY}, learned, hist, arc, NOW, 95, [], UTC)
    lit = plain["replay"]["arrays"]["roof"]["now"]["hours"]

    # a full battery in one hour (usable_samples and the flag written with it) and a whole excluded day
    first = DAY0 + 60 * 86400
    noon_hour = first + 11 * 3600
    full = ln.PvHistory(hist.power, {}, {}, {}, {noon_hour: 100.0}, hist.air_temp, hist.satellite_ghi)
    flagged = copy.deepcopy(arc)
    flagged[datetime.fromtimestamp(first, UTC).date().isoformat()]["hours"][str(int(noon_hour))]["curtailed"] = True
    r = backtest.run(SITE, {"roof": ARRAY}, learned, full, flagged, NOW, 95, [], UTC)
    assert r["replay"]["arrays"]["roof"]["now"]["hours"] == lit - 1
    assert r["holdout"]["arrays"]["roof"]["holdout"]["hours"] == plain["holdout"]["arrays"]["roof"]["holdout"]["hours"] - 1

    excluded = [(first + 86400, first + 2 * 86400)]
    r = backtest.run(SITE, {"roof": ARRAY}, learned, hist, arc, NOW, 95, excluded, UTC)
    assert r["replay"]["arrays"]["roof"]["now"]["days"] == 9
    assert r["holdout"]["arrays"]["roof"]["holdout"]["days"] == plain["holdout"]["arrays"]["roof"]["holdout"]["days"] - 1

    # flagged only in the archive: not counted either, and an incomplete day is left out
    r = backtest.run(SITE, {"roof": ARRAY}, learned, hist, flagged, NOW, 95, [], UTC)
    assert r["replay"]["arrays"]["roof"]["now"]["hours"] == lit - 1
    partial = copy.deepcopy(arc)
    day = partial[datetime.fromtimestamp(first, UTC).date().isoformat()]["hours"]
    del day[str(int(first + 12 * 3600))]["actual"]
    r = backtest.run(SITE, {"roof": ARRAY}, learned, hist, partial, NOW, 95, [], UTC)
    assert r["replay"]["arrays"]["roof"]["now"]["days"] == 9


def test_a_run_changes_nothing():
    hist = history(dip=lambda t: 0.6 if even(t) and noon(t) else 1.0)
    learned = ln.learn_arrays(SITE, {"roof": ARRAY}, hist, NOW, 95, [])
    arc = _archive(10)
    before = copy.deepcopy((learned, arc, hist))
    result = backtest.run(SITE, {"roof": ARRAY}, learned, hist, arc, NOW, 95, [], UTC)
    assert result["holdout"]["total"]["status"] == result["replay"]["total"]["status"] == "ok"
    assert (learned, arc, hist) == before


def test_too_little_data_gives_no_number():
    hist = history(days=20)                                 # ten days per half, fourteen needed
    with patch.object(ln, "learn_arrays", side_effect=AssertionError("nothing to learn on")):
        r = backtest.run(SITE, {"roof": ARRAY}, {"roof": TRUE}, hist, _archive(3, first=DAY0), NOW, 95, [], UTC)
    for part, needed in ((r["holdout"], 2 * backtest.MIN_FOLD_DAYS), (r["replay"], backtest.MIN_REPLAY_DAYS)):
        for res in (part["arrays"]["roof"], part["total"]):
            assert res["status"] == "not_enough_data" and res["reason"] == "few_days" and res["needed"] == needed
            assert "holdout" not in res and "now" not in res
    assert r["holdout"]["total"]["days"] == 20 and r["replay"]["total"]["days"] == 3


def test_holdout_needs_measured_irradiance():
    r = backtest.run(SITE, {"roof": ARRAY}, {}, history(satellite=False), {}, NOW, 95, [], UTC)["holdout"]
    assert r["total"]["status"] == "not_enough_data" and r["total"]["reason"] == "no_satellite"


def test_total_holds_the_arrays_with_enough_data():
    young = ln.ArrayConfig("new", "New", ("pv",), GEO, NOW - 10 * 86400)
    r = backtest.run(SITE, {"roof": ARRAY, "new": young}, {}, history(), {}, NOW, 95, [], UTC)["holdout"]
    assert r["arrays"]["new"]["status"] == "not_enough_data" and r["arrays"]["roof"]["status"] == "ok"
    assert r["total"]["holdout"] == r["arrays"]["roof"]["holdout"]


def test_days_are_local_days():
    """A day runs from local midnight: a fixed offset of 12 h moves the hours of a test day."""
    tz = timezone(timedelta(hours=12))
    r = backtest.run(SITE, {"roof": ARRAY}, {}, history(), {}, NOW, 95, [], tz)["holdout"]["arrays"]["roof"]
    assert r["holdout"]["days"] + r["in_sample"]["days"] == 2 * r["holdout"]["days"]
    assert r["holdout"]["days"] == 91                        # the light of a UTC day spreads over two local days


def test_old_result_is_dropped():
    assert backtest.valid({"version": backtest.VERSION, "replay": {}}) and backtest.valid({"version": 0}) is None
    assert backtest.valid(None) is None and backtest.valid("x") is None
