"""Load forecast: base load without consumers, median profile per day type, decaying correction."""
from datetime import datetime, timezone

from conftest import load

lf = load("load")

H = 3600.0
UTC = timezone.utc
MONDAY = datetime(2026, 10, 5, tzinfo=UTC).timestamp()


def days(n, w_of_hour, start=MONDAY):
    return {start + d * 86400 + h * H: w_of_hour(d, h) for d in range(n) for h in range(24)}


def test_base_series_removes_consumers_and_drops_unknown_hours():
    load_ = {0.0: 500.0, H: 900.0, 2 * H: 400.0, 3 * H: 300.0}
    washer = {H: 600.0, 3 * H: 0.0}                     # missing at 2 H after its first value
    late = {3 * H: 50.0}                                 # did not exist before 3 H
    out = lf.base_series(load_, [washer, late])
    assert out == {0.0: 500.0, H: 300.0, 3 * H: 250.0}


def test_profile_is_median_per_day_type():
    # weekdays 200 W at noon, one weekday with a 3 kW spike; weekends 600 W
    series = days(28, lambda d, h: (600.0 if d % 7 >= 5 else (3000.0 if d == 2 and h == 12 else 200.0)))
    p = lf.learn_profile(series, UTC, MONDAY + 28 * 86400)
    assert p.at(MONDAY + 12 * H, UTC) == 200.0          # spike does not bend the median
    assert p.at(MONDAY + 5 * 86400 + 12 * H, UTC) == 600.0
    assert p.days == 28


def test_profile_falls_back_to_all_days():
    series = days(2, lambda d, h: 300.0)                 # two weekdays only: cells have too few values
    p = lf.learn_profile(series, UTC, MONDAY + 7 * 86400)
    assert p.cells[lf.WORKDAY][10] is None and p.at(MONDAY + 10 * H, UTC) is None
    series = days(7, lambda d, h: 300.0)
    p = lf.learn_profile(series, UTC, MONDAY + 7 * 86400)
    assert p.cells[lf.WEEKEND][10] is None and p.at(MONDAY + 5 * 86400 + 10 * H, UTC) == 300.0


def test_correction_is_clamped_and_decays_towards_one():
    p = lf.learn_profile(days(28, lambda d, h: 200.0), UTC, MONDAY + 28 * 86400)
    now = MONDAY + 28 * 86400 + 14 * H + 600
    recent = {now - now % H - i * H: 300.0 for i in range(1, 5)}
    k = lf.correction(recent, p, UTC, now)
    assert k == lf.K_MAX                                  # 1.5 clamped
    near, far = lf.k_at(k, now, now - now % H + H), lf.k_at(k, now, now - now % H + 8 * H)
    assert near > far > 1.0 and far - 1 < 0.01


def test_correction_needs_two_hours():
    p = lf.learn_profile(days(28, lambda d, h: 200.0), UTC, MONDAY + 28 * 86400)
    now = MONDAY + 28 * 86400 + 14 * H
    assert lf.correction({now - H: 400.0}, p, UTC, now) == 1.0


def test_flexible_consumer_only_counts_while_running():
    end_learn = MONDAY + 28 * 86400
    base = lf.learn_profile(days(28, lambda d, h: 200.0), UTC, end_learn)
    washer = lf.learn_profile(days(28, lambda d, h: 500.0 if h == 11 else 0.0), UTC, end_learn)
    now = end_learn + 10 * H + 1800
    end = end_learn + 24 * H
    fixed = lf.forecast(base, [lf.Consumer("w", lf.TIMING_FIXED, washer)], UTC, now, end, 1.0)
    flexible = lf.forecast(base, [lf.Consumer("w", lf.TIMING_FLEXIBLE, washer)], UTC, now, end, 1.0)
    running = lf.forecast(base, [lf.Consumer("w", lf.TIMING_FLEXIBLE, washer)], UTC, now, end, 1.0, {"w": 2000.0})
    assert fixed.hours[end_learn + 11 * H] == 700.0
    assert flexible.hours[end_learn + 11 * H] == 200.0
    assert running.hours[end_learn + 10 * H] == 2200.0 and running.hours[end_learn + 11 * H] == 200.0
    assert abs(lf.remaining(flexible.hours, now, end) - (0.5 * 200 + 13 * 200)) < 1e-6


def test_backtest_beats_naive_mean_when_a_consumer_is_flexible():
    # base 200 W; a flexible 2 kW washer ran at noon every third day: the naive mean spreads it over all days
    total = days(60, lambda d, h: 200.0 + (2000.0 if h == 12 and d % 3 == 0 else 0.0))
    washer = days(60, lambda d, h: 2000.0 if h == 12 and d % 3 == 0 else 0.0)
    now = MONDAY + 60 * 86400 + 8 * H
    result = lf.backtest(total, {"w": washer}, {"w": lf.TIMING_FLEXIBLE}, UTC, now)
    assert result["cases"] > 0 and result["mae_kwh"] is not None
    # the model forecasts base load only; its error is the washer on the days it ran
    assert result["mae_kwh"] < result["naive_mae_kwh"]


def test_profile_round_trip():
    p = lf.learn_profile(days(14, lambda d, h: 100.0 + h), UTC, MONDAY + 14 * 86400)
    assert lf.Profile.from_dict(p.as_dict()) == p
