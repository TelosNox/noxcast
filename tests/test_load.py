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
    running = lf.forecast(base, [lf.Consumer("w", lf.TIMING_FLEXIBLE, washer)], UTC, now, end, 1.0, {"w": lf.Run(2000.0)})
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


def test_program_counts_its_remaining_run():
    end_learn = MONDAY + 28 * 86400
    base = lf.learn_profile(days(28, lambda d, h: 200.0), UTC, end_learn)
    now = end_learn + 10 * H + 1800                      # 10:30, washer runs 120 more minutes at 2 kW
    run = {"w": lf.Run(2000.0, 120)}
    fc = lf.forecast(base, [lf.Consumer("w", lf.TIMING_FLEXIBLE, lf.Profile())], UTC, now, end_learn + 24 * H, 1.0, run)
    parts = fc.consumers["w"]
    assert parts[end_learn + 10 * H] == 2000.0           # whole rest of the running hour
    assert parts[end_learn + 11 * H] == 2000.0
    assert parts[end_learn + 12 * H] == 1000.0           # half of 12:00-13:00
    assert parts[end_learn + 13 * H] == 0.0
    energy = lf.remaining({t: v for t, v in parts.items()}, now, end_learn + 24 * H)
    assert abs(energy - 2000.0 * 2) < 1e-6


def washer_series(n):
    # a 2-hour run (2000 W, then 800 W) at 19:00 on every third day, otherwise off
    return days(n, lambda d, h: (2000.0 if h == 19 else 800.0 if h == 20 else 0.0) if d % 3 == 0 else 0.0)


def test_program_learns_start_rate_and_run_curve():
    until = MONDAY + 42 * 86400
    m = lf.learn_program(washer_series(42), UTC, until)
    assert m.shape == [2000.0, 800.0] and m.runs == 14
    assert m.runs_max == {lf.WORKDAY: 1, lf.WEEKEND: 1}
    assert 0.3 < m.starts[lf.WORKDAY][19] < 0.37 and m.starts[lf.WORKDAY][10] == 0.0
    assert lf.learn_profile(washer_series(42), UTC, until).at(until + 19 * H, UTC) == 0.0   # the median misses it


def test_program_forecast_expects_a_share_of_a_run_and_stops_after_the_daily_maximum():
    until = MONDAY + 42 * 86400                          # a Monday
    c = lf.learned_consumer("washer", lf.TIMING_FIXED, lf.KIND_PROGRAM, washer_series(42), UTC, until)
    now = until + 17 * H
    fc = lf.forecast(lf.Profile(fallback=[100.0] * 24), [c], UTC, now, until + 86400, 1.0)
    rate = c.program.starts[lf.WORKDAY][19]
    assert abs(fc.consumers["washer"][until + 19 * H] - round(rate * 2000, 1)) < 0.2
    assert abs(fc.consumers["washer"][until + 20 * H] - round(rate * 800, 1)) < 0.2
    c.runs_today = 1                                     # today's run is done: no further start expected
    fc = lf.forecast(lf.Profile(fallback=[100.0] * 24), [c], UTC, now, until + 86400, 1.0)
    assert fc.consumers["washer"][until + 19 * H] == 0.0


def test_runs_since_counts_blocks_that_started_today():
    s = {0.0: 500.0, H: 500.0, 3 * H: 400.0, 5 * H: 10.0}
    assert lf.runs_since(s, 0.0) == 2 and lf.runs_since(s, 2 * H) == 1
    assert lf.runs_since(s, 0.0, 4 * H + 60, running=True) == 1      # the run in the last hour is still going


def pc_series(n):
    # on from 18 to 23 on workdays at 150 W, off at weekends
    return days(n, lambda d, h: 150.0 if d % 7 < 5 and 18 <= h < 23 else 5.0)


def test_session_runs_until_the_learned_end_when_on():
    until = MONDAY + 42 * 86400
    m = lf.learn_session(pc_series(42), UTC, until)
    assert m.power == 150.0 and m.remaining[20] == 3.0 and m.p_on[lf.WORKDAY][20] == 1.0 and m.p_on[lf.WEEKEND][20] == 0.0
    now = until + 20 * H + 600
    on = lf.session_expected(m, UTC, now, until + 86400, 200.0)
    assert on[until + 20 * H] == 200.0 and on[until + 22 * H] == 200.0 and on[until + 23 * H] == 0.0
    off = lf.session_expected(m, UTC, now, until + 86400, None)
    assert off[until + 21 * H] == 150.0


def test_kinds_round_trip_and_backtest_runs_with_them():
    until = MONDAY + 42 * 86400
    p = lf.learn_program(washer_series(42), UTC, until)
    assert lf.ProgramModel.from_dict(p.as_dict()) == p
    s = lf.learn_session(pc_series(42), UTC, until)
    assert lf.SessionModel.from_dict(s.as_dict()) == s
    washer, pc = washer_series(60), pc_series(60)
    house = {t: 300.0 + washer[t] + pc[t] for t in washer}
    now = MONDAY + 60 * 86400
    plain = lf.backtest(house, {"washer": washer, "pc": pc}, {}, UTC, now)
    kinds = lf.backtest(house, {"washer": washer, "pc": pc}, {}, UTC, now, kinds={"washer": lf.KIND_PROGRAM, "pc": lf.KIND_SESSION})
    assert kinds["cases"] == plain["cases"] > 0
    # the median misses the washer on every third day; the program model gets the energy right on average
    assert plain["bias_kwh"] < -0.5 and abs(kinds["bias_kwh"]) < 0.15
