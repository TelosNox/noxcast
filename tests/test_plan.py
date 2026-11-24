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


def test_no_window_without_surplus():
    # without surplus every start ties at 0 %; the earliest, at midnight, must not become the recommendation
    night = {h * H: 0.0 for h in range(24)}
    assert pl.plan([pl.RunProfile("x", 500, 120)], night, {}, 0.0, 24 * H)["x"]["best"] is None
    little = {12 * H: 20.0}                               # 20 Wh of a 1 kWh run: 2 %
    assert pl.plan([pl.RunProfile("x", 500, 120)], little, {}, 0.0, 24 * H)["x"]["best"] is None


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


def test_start_now_needs_the_threshold_and_nearly_the_best_share():
    w = lambda share: pl.Window(0, 3600, share, 0.0)
    assert pl.start_now(w(0.8), w(0.82), 0.7)                 # good enough and within 5 points of the best
    assert not pl.start_now(w(0.8), w(1.0), 0.7)              # a clearly better window comes later today
    assert not pl.start_now(w(0.6), w(0.6), 0.7)              # below the threshold
    assert pl.start_now(w(0.7), None, 0.7)                    # no run fits later today: the threshold decides
    assert pl.start_now(w(0.0), w(0.0), 0.0)                  # threshold 0: any time is as good as the best


bt = load("battery")


def _day(pv_w, load_w=300.0, hours=range(0, 48)):
    """PV pv_w from 9 to 16 each day, a flat load."""
    return ({h * H: (pv_w if 9 <= h % 24 < 16 else 0.0) for h in hours}, {h * H: load_w for h in hours})


def test_replay_repeats_the_day_simulation():
    pv, load_ = _day(1500)
    b = bt.day_simulation([bt.Battery(5000, 0.3, efficiency=0.9)], pv, load_, None, 6 * H, 24 * H, ahead=(load_, 48 * H))
    again = bt.replay(b, pv, load_, 6 * H, b["charge_start"])
    assert (again.grid_wh, again.lost_wh, again.points) == (b["sim"].grid_wh, b["sim"].lost_wh, b["sim"].points)


def test_with_a_battery_that_stays_below_full_a_run_costs_the_night():
    # 1500 W PV against 300 W load: 8.4 kWh surplus, the 20 kWh battery never fills; what a run takes at noon is
    # missing in the night, so it is no free surplus although PV is far above the load
    pv, load_ = _day(1500)
    b = bt.day_simulation([bt.Battery(20000, 0.1, efficiency=1.0)], pv, load_, None, 6 * H, 24 * H, ahead=(load_, 48 * H))
    run = pl.RunProfile("washer", 1000, 60)
    cost = bt.planning_cost(b, pv, load_, 6 * H)
    out = pl.day_plan([run], pv, load_, {}, 6 * H, 24 * H, cost)["washer"]
    assert out["today"] is None or out["today"].pv_share < 0.5
    without = pl.day_plan([run], pv, load_, {}, 6 * H, 24 * H)["washer"]
    assert without["today"].pv_share == 1.0                        # the surplus alone looked free


def test_with_a_full_battery_the_run_goes_where_surplus_is_lost():
    # a small battery fills before noon and then loses surplus: a run in the sun only delays the full battery, so it
    # costs nothing, while one before sunrise empties the battery and needs the grid
    pv, load_ = _day(1500)
    b = bt.day_simulation([bt.Battery(2000, 0.5, efficiency=1.0)], pv, load_, None, 6 * H, 24 * H, ahead=(load_, 48 * H))
    run = pl.RunProfile("dryer", 1000, 60)
    cost = bt.planning_cost(b, pv, load_, 6 * H)
    out = pl.day_plan([run], pv, load_, {}, 6 * H, 24 * H, cost)["dryer"]
    today, tomorrow = out["today"], out["tomorrow"]
    assert today.pv_share == 1.0 and today.lost_share == 1.0
    assert 9 * H <= today.start and today.end <= 16 * H
    assert out["now"].pv_share < 1.0
    assert tomorrow.pv_share == 1.0 and tomorrow.lost_share == 1.0 and 24 * H + 9 * H <= tomorrow.start


def test_a_second_run_counts_the_first_one():
    pv, load_ = _day(1500)
    b = bt.day_simulation([bt.Battery(2000, 0.5, efficiency=1.0)], pv, load_, None, 6 * H, 24 * H, ahead=(load_, 48 * H))
    runs = [pl.RunProfile("washer", 1000, 60), pl.RunProfile("dryer", 1000, 60)]
    out = pl.day_plan(runs, pv, load_, {}, 6 * H, 24 * H, bt.planning_cost(b, pv, load_, 6 * H))
    w, d = out["washer"]["today"], out["dryer"]["today"]
    assert w.start != d.start and w.lost_share == 1.0 and d.lost_share == 1.0
