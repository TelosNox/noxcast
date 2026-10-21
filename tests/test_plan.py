"""Planning flexible consumers: best window, shares, several consumers by priority."""
from conftest import load

pl = load("plan")

H = 3600.0


def test_best_window_lies_in_the_surplus():
    surplus = {h * H: (1500 if 11 <= h < 14 else 200) for h in range(8, 18)}
    run = pl.RunProfile("washer", 1000, 120)
    out = pl.plan([run], surplus, {}, 8 * H, 18 * H)["washer"]
    assert 11 * H <= out["best"].start and out["best"].end <= 14 * H
    assert out["best"].pv_share == 1.0
    assert abs(out["now"].pv_share - 0.2) < 1e-9          # at 8:00 only 200 W of 1000 W


def test_lost_surplus_breaks_ties_and_earlier_wins():
    surplus = {h * H: 2000 for h in range(9, 17)}
    lost = {h * H: (2000 if h >= 14 else 0) for h in range(9, 17)}
    run = pl.RunProfile("dryer", 1500, 60)
    best = pl.plan([run], surplus, lost, 9 * H, 17 * H)["dryer"]["best"]
    assert best.start == 14 * H and best.lost_share == 1.0


def test_second_consumer_gets_what_is_left():
    surplus = {12 * H: 1200, 13 * H: 1200}
    runs = [pl.RunProfile("washer", 1000, 60), pl.RunProfile("dryer", 1000, 60)]
    out = pl.plan(runs, surplus, {}, 12 * H, 14 * H)
    assert out["washer"]["best"].pv_share == 1.0
    assert out["dryer"]["best"].pv_share == 1.0 and out["dryer"]["best"].start != out["washer"]["best"].start


def test_no_window_when_the_run_does_not_fit():
    assert pl.plan([pl.RunProfile("x", 500, 180)], {12 * H: 800}, {}, 12 * H, 13 * H)["x"]["best"] is None


def test_start_is_aligned_to_quarter_hours():
    out = pl.plan([pl.RunProfile("x", 100, 30)], {h * H: 500 for h in range(10, 13)}, {}, 10 * H + 600, 13 * H)
    assert out["x"]["best"].start % 900 == 0


def test_learn_run_from_hourly_means():
    series = {}
    for d in range(5):                                    # washer: 2 kW for 1.5 h, starting at :30
        t0 = d * 86400
        series.update({t0 + 10 * H: 1000.0, t0 + 11 * H: 2000.0, t0 + 12 * H: 0.0, t0 + 13 * H: 0.0})
    run = pl.learn_run("washer", series)
    assert run.power_w == 2000 and run.minutes == 90 and run.energy_wh == 3000


def test_learn_run_needs_runs():
    assert pl.learn_run("x", {0.0: 0.0, H: 500.0}) is None
