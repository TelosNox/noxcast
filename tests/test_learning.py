"""Learning pipeline on fetched data: arrays, archive values, load models, and the day filter."""
from datetime import timezone

from conftest import load

ln = load("learning")
lf = load("load")
model = load("model")
solar = load("solar")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(30, 180, 1000)
DAY0 = 1780272000.0                     # 2026-06-01 00:00 UTC
DAYS = 30
NOW = DAY0 + DAYS * 86400


def history(**kw) -> "ln.PvHistory":
    series = {}
    for h in range(DAYS * 24):
        t = DAY0 + h * 3600
        beam, diff, *_ = model.clearsky_hour(SITE, GEO, t)
        series[t] = 0.5 * GEO.peak_w * 0.92 * (beam + diff) / 1000      # two sensors, half each
    power = {"pv1": series, "pv2": {t: v / 1000 for t, v in series.items()}}     # the second one in kW
    return ln.PvHistory(power, {}, {}, {"pv2": 1000.0}, {}, **kw)


ARRAY = ln.ArrayConfig("roof", "Roof", ("pv1", "pv2"), GEO, None)


def even(ts: float) -> bool:
    return int(ts // 86400) % 2 == 0


def test_arrays_are_learned_from_the_summed_sensors():
    learned = ln.learn_arrays(SITE, {"roof": ARRAY}, history(), NOW, 95, [])["roof"]
    assert abs(learned.efficiency - 0.92) < 0.03 and learned.samples > 200
    assert learned.months                                       # panel explanations are set


def test_day_filter_learns_only_from_kept_days():
    hist = history()
    full = ln.learn_array(SITE, ARRAY, hist, NOW, 95, [])
    assert ln.learn_array(SITE, ARRAY, hist, NOW, 95, [], keep=lambda ts: True).as_dict() == full.as_dict()
    half = ln.learn_array(SITE, ARRAY, hist, NOW, 95, [], keep=even)
    assert 0.4 < half.samples / full.samples < 0.6
    assert abs(half.efficiency - full.efficiency) < 0.01


def test_record_archive_adds_actuals_and_flags_full_battery():
    t = DAY0 + 10 * 3600
    archive = {"2026-06-01": {"hours": {str(int(t)): {"ghi": 500.0, "forecast": {"roof": 400.0}}}}}
    hist = history()
    hist = ln.PvHistory(hist.power, {}, {}, hist.factors, {t: 100.0})
    ln.record_archive(archive, SITE, {"roof": ARRAY}, {"roof": model.LearnedArray()}, hist, NOW, 95)
    hour = archive["2026-06-01"]["hours"][str(int(t))]
    assert hour["actual"]["roof"] == round(hist.power["pv1"][t] * 2, 1) and hour["curtailed"]


def test_highest_and_sum_series():
    assert ln.highest({"a": {1.0: 50.0, 2.0: None}, "b": {1.0: 80.0, 2.0: 20.0}}) == {1.0: 80.0, 2.0: 20.0}
    assert ln.sum_series([{1.0: 1.0, 2.0: 2.0}, {2.0: 3.0}]) == {2.0: 5.0}
    assert ln.sum_series([{1.0: 1.0}, {}]) == {}


def test_load_models_and_day_filter():
    days = lf.PROFILE_DAYS + lf.BACKTEST_DAYS + 1
    start = NOW - days * 86400
    house = {start + h * 3600: 300.0 + (900.0 if h % 24 == 10 and (h // 24) % 3 == 0 else 0.0) for h in range(days * 24)}
    washer = {t: 900.0 if v > 1000 else 0.0 for t, v in house.items()}
    consumers = {"w": ln.ConsumerConfig("w", "Washer", "washer", lf.TIMING_FLEXIBLE)}
    hist = ln.LoadHistory({"house": house, "washer": washer}, {t: 250.0 for t in house})
    out = ln.learn_load("house", consumers, hist, timezone.utc, timezone.utc, NOW, NOW)
    assert out.profile.days and out.floor.days and out.runs["w"] == {"power_w": 900.0, "minutes": 60}
    assert out.events is None                                   # no 5-minute values: no suggestions
    kept = ln.learn_load("house", consumers, hist, timezone.utc, timezone.utc, NOW, NOW, keep=even)
    assert kept.profile.days < out.profile.days
    assert ln.learn_load("house", consumers, ln.LoadHistory({}, {}), timezone.utc, timezone.utc, NOW, NOW) is None
