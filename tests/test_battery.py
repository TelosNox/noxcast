"""Battery simulation: limits, losses, power limits, full and empty times."""
from conftest import load

bt = load("battery")

H = 3600.0


def flat(value, hours):
    return {h * H: value for h in hours}


def test_combine_weights_by_capacity_and_skips_unknown():
    a = bt.Battery(2000, 0.5, max_soc=1.0, min_soc=0.1, efficiency=0.9, max_charge_w=800)
    b = bt.Battery(4000, 0.2, max_soc=0.9, min_soc=0.1, efficiency=0.96, max_charge_w=1200)
    c = bt.Battery(1000, None)
    s = bt.combine([a, b, c])
    assert s.capacity_wh == 6000 and abs(s.soc - 0.3) < 1e-9
    assert abs(s.max_soc - (2000 * 1.0 + 4000 * 0.9) / 6000) < 1e-9
    assert s.max_charge_w == 2000 and s.max_discharge_w is None
    assert bt.combine([c]) is None


def test_surplus_fills_up_then_is_lost():
    b = bt.Battery(1000, 0.5, max_soc=1.0, efficiency=1.0)
    pv, load_ = flat(600, range(10, 14)), flat(200, range(10, 14))   # 400 Wh surplus per hour
    sim = bt.simulate(b, pv, load_, 10 * H, 14 * H)
    assert sim.charged_wh == 500 and sim.lost_wh == 1600 - 500
    assert abs(sim.full_at - (11 * H + 0.25 * H)) < 1e-6          # 100 Wh of the second hour fit
    assert sim.lost_from == 11 * H and sim.max_soc == 1.0


def test_charge_power_limit_loses_surplus_without_being_full():
    b = bt.Battery(5000, 0.2, efficiency=1.0, max_charge_w=300)
    sim = bt.simulate(b, flat(1000, [12]), flat(200, [12]), 12 * H, 13 * H)
    assert sim.charged_wh == 300 and sim.lost_wh == 500 and sim.full_at is None


def test_deficit_discharges_to_the_lower_limit_with_losses():
    b = bt.Battery(1000, 0.5, min_soc=0.1, efficiency=0.81)          # 0.9 per direction
    sim = bt.simulate(b, {}, flat(200, range(20, 24)), 20 * H, 24 * H)
    # 400 Wh stored above the limit cover 360 Wh of load
    assert abs(sim.grid_wh - (800 - 360)) < 1e-6
    assert 21 * H < sim.empty_at < 22 * H
    assert abs(sim.points[-1][1] - 0.1) < 1e-9


def test_running_hour_counts_its_rest_and_soc_interpolates():
    b = bt.Battery(1000, 0.5, efficiency=1.0)
    sim = bt.simulate(b, flat(400, [10]), flat(0, [10]), 10 * H + 1800, 11 * H)
    assert sim.charged_wh == 200
    assert abs(sim.soc_at(10 * H + 2700) - 0.6) < 1e-9


def test_charge_start_is_the_first_hour_with_pv_above_load():
    pv = {h * H: (0 if h < 32 else 100 if h == 32 else 300) for h in range(24, 48)}
    load_ = {h * H: 200 for h in range(24, 48)}
    assert bt.charge_start(pv, load_, 24 * H, 48 * H) == 33 * H
    assert bt.charge_start(pv, load_, 24 * H, 30 * H) is None


def test_soc_days_weights_by_capacity_and_skips_incomplete_hours():
    day = lambda t: "a" if t < 10 else "b"
    days = bt.soc_days([(1000, [(0, 0.2, 0.4), (1, 0.5, 1.0), (10, 0.1, 0.3)]),
                        (3000, [(0, 0.6, 0.8), (1, 0.7, 0.9)])], day)
    assert days == [{"date": "a", "min": 0.5, "max": 0.925, "hours": 2}]


def test_prediction_against_outcome():
    st = bt.Battery(capacity_wh=10000, soc=0.3, max_soc=1.0, min_soc=0.1)
    sim = bt.Simulation(points=[(0.0, 0.3), (3600.0, 0.8), (7200.0, 1.0), (90000.0, 0.1)], full_at=7000.0, empty_at=80000.0)
    pred = bt.prediction(sim, st, made=0.0, day_end=86400.0, until=100000.0)
    assert pred["full_at"] == 7000.0 and pred["empty_at"] == 80000.0 and pred["max_soc"] == 1.0
    soc = bt.combined_soc([(5000.0, {t: min(1.0, 0.3 + t / 20000) for t in range(0, 90000, 300)}),
                           (5000.0, {t: min(1.0, 0.3 + t / 20000) for t in range(0, 90000, 300)})])
    soc.update({t: 0.1 for t in range(60000, 90000, 300)})
    out = bt.outcome(soc, pred, day_end=86400.0)
    assert out["full_at"] == 13800 and out["empty_at"] == 60000 and out["max_soc"] == 1.0
    assert out["day_complete"] and not out["complete"]                 # night not yet over
    review = bt.prediction_review({"2026-10-20": pred, "2026-10-21": {**pred, "made": 1e9}}, soc, lambda k: 86400.0)
    assert [r["date"] for r in review] == ["2026-10-20"]


def test_output_cap_limits_battery_and_pv_together():
    # 1600 W cap: with 500 W of its PV the battery delivers at most 1100 W, the rest of the load is grid
    b = bt.Battery(5000, 0.8, efficiency=1.0, output_w=1600, array="balcony")
    hours = [12 * H]
    caps = bt.limits([b], {"balcony": flat(500, [12])}, hours)
    assert caps.discharge == {12 * H: 1100} and caps.capped == {}
    sim = bt.simulate(b, flat(500, [12]), flat(2000, [12]), 12 * H, 13 * H, caps)
    assert sim.hours[12 * H]["battery"] == -1100 and sim.grid_wh == 400


def test_pv_above_the_cap_charges_the_battery_although_the_house_draws():
    # 2000 Wh PV behind an 800 W cap, 1500 Wh load: 800 reach the house, 1200 can only go into the battery
    b = bt.Battery(5000, 0.5, efficiency=1.0, output_w=800, array="balcony")
    caps = bt.limits([b], {"balcony": flat(2000, [12])}, [12 * H])
    assert caps.capped == {12 * H: 1200} and caps.discharge == {12 * H: 0}
    sim = bt.simulate(b, flat(2000, [12]), flat(1500, [12]), 12 * H, 13 * H, caps)
    assert sim.charged_wh == 1200 and sim.grid_wh == 700 and sim.hours[12 * H]["battery"] == 1200


def test_capped_pv_that_does_not_fit_is_lost():
    b = bt.Battery(1000, 1.0, efficiency=1.0, output_w=800, array="balcony")
    caps = bt.limits([b], {"balcony": flat(1000, [12])}, [12 * H])
    sim = bt.simulate(b, flat(1000, [12]), flat(100, [12]), 12 * H, 13 * H, caps)
    # 700 Wh surplus of the house plus 200 Wh above the cap, the battery is full
    assert sim.lost_wh == 900 and sim.charged_wh == 0


def test_caps_per_system_add_up_and_other_arrays_are_free():
    # two systems, each battery behind its own array; the roof has no cap
    a = bt.Battery(2000, 0.5, output_w=800, array="a")
    b = bt.Battery(2000, 0.5, output_w=1600, array="b", max_discharge_w=1000)
    pv = {"a": flat(300, [12]), "b": flat(1800, [12]), "roof": flat(5000, [12])}
    caps = bt.limits([a, b], pv, [12 * H])
    assert caps.capped == {12 * H: 200}                       # only b is above its cap
    assert caps.discharge == {12 * H: 500 + 0}                # a: 800 - 300, b: nothing left under the cap


def test_cap_without_array_limits_the_battery_alone_and_no_cap_changes_nothing():
    a = bt.Battery(2000, 0.5, output_w=800)
    assert bt.limits([a], {}, [12 * H]).discharge == {12 * H: 800}
    plain = bt.Battery(2000, 0.5, max_discharge_w=500)
    assert bt.limits([plain], {}, [12 * H]) == bt.Limits()


def test_caps_reach_past_the_charge_start():
    # the plan for tomorrow and the card need the caps after the battery starts charging again, too
    b = bt.Battery(2000, 0.5, efficiency=1.0, output_w=800, array="a")
    pv = {h * H: (1000.0 if h in (12, 36) else 0.0) for h in range(10, 40)}
    load = {h * H: 100.0 for h in range(10, 40)}
    out = bt.day_simulation([b], pv, load, None, 10 * H, 24 * H, {"a": pv})
    assert out["charge_start"] == 36 * H and out["limits"].capped[36 * H] == 200
