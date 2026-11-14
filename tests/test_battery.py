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


def test_battery_just_below_its_limit_counts_as_full_while_it_has_surplus():
    # the battery stopped charging at 96 % with a limit of 97 %: the surplus is lost from now on
    b = bt.Battery(5000, 0.96, max_soc=0.97, efficiency=1.0)
    out = bt.day_simulation([b], flat(800, [14]), flat(300, [14]), None, 14 * H, 15 * H)
    sim = out["sim"]
    assert abs(out["held_wh"] - 50) < 1e-6
    assert sim.full_at == 14 * H and sim.lost_from == 14 * H and sim.charged_wh == 0
    # without surplus now the level says nothing about where it stops: it charges up to the limit later
    out = bt.day_simulation([b], flat(800, [15]), {14 * H: 500, 15 * H: 300}, None, 14 * H, 16 * H)
    assert out["held_wh"] == 0.0 and out["sim"].charged_wh > 0
    # further below the tolerance it still charges up to the limit
    out = bt.day_simulation([bt.Battery(5000, 0.94, max_soc=0.97, efficiency=1.0)], flat(800, [14]), flat(300, [14]),
                            None, 14 * H, 15 * H)
    assert out["held_wh"] == 0.0 and abs(out["sim"].charged_wh - 150) < 1e-6


def test_lost_when_full_leaves_out_surplus_above_the_charge_power():
    b = bt.Battery(1000, 0.5, max_soc=1.0, efficiency=1.0, max_charge_w=300)
    sim = bt.simulate(b, flat(1000, range(10, 13)), flat(200, range(10, 13)), 10 * H, 13 * H)
    # 10 h: 300 Wh charged, 500 Wh above the charge power (battery at 80 %); 11 h: full after 200 Wh
    assert sim.hours[10 * H]["lost"] == 500 and bt.lost_when_full(sim, 1.0) == {11 * H: 600.0, 12 * H: 800.0}


def test_held_back_is_per_battery():
    full = bt.Battery(5000, 0.96, max_soc=0.97)                    # stopped: 50 Wh held back
    charging = bt.Battery(5000, 0.50, max_soc=1.0)                 # still takes everything up to its limit
    above = bt.Battery(1000, 0.99, max_soc=0.97)                   # above its limit: nothing to hold back
    assert abs(bt.held_back([full, charging, above]) - 50) < 1e-6
    assert bt.held_back([bt.Battery(5000, None)]) == 0.0


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
    # empty for the night: between the last point above the limit (+ tolerance) and the next one
    assert pred["full_at"] == 7000.0 and pred["max_soc"] == 1.0 and pred["charged"] and pred["night"]
    assert abs(pred["empty_at"] - (7200 + (1.0 - 0.12) / 0.9 * 82800)) < 1e-6
    soc = bt.combined_soc([(5000.0, {t: min(1.0, 0.3 + t / 20000) for t in range(0, 90000, 300)}),
                           (5000.0, {t: min(1.0, 0.3 + t / 20000) for t in range(0, 90000, 300)})])
    soc.update({t: 0.1 for t in range(60000, 90000, 300)})
    out = bt.outcome(soc, pred, day_end=86400.0)
    assert out["full_at"] == 13800 and out["empty_at"] == 60000 and out["max_soc"] == 1.0 and out["charged"]
    assert out["day_complete"] and not out["complete"]                 # night not yet over
    review = bt.prediction_review({"2026-10-20": pred, "2026-10-21": {**pred, "made": 1e9}}, soc, lambda k: 86400.0)
    assert [r["date"] for r in review] == ["2026-10-20"]


def test_empty_for_the_night_is_the_last_time_the_battery_runs_empty():
    # empty in the morning, a little charge at noon, empty again, charged once more, empty for the night at 19:00
    soc = {6: 0.1, 11: 0.1, 12: 0.4, 13: 0.2, 14: 0.1, 15: 0.3, 16: 0.5, 18: 0.3, 19: 0.1, 23: 0.1, 30: 0.1}
    curve = [(h * H, s) for h, s in soc.items()]
    assert bt.night_empty(curve, 0.1) == (True, 19 * H)
    charged, at = bt.night_empty(curve, 0.1, [])            # hourly simulation without its times: linear
    assert charged and 18 * H < at < 19 * H
    # the simulation's own time at the limit wins over the line between the hourly points; earlier ones do not count
    assert bt.night_empty(curve, 0.1, [6 * H, 14 * H, 18.5 * H, 19 * H, 20 * H]) == (True, 18.5 * H)
    assert bt.night_empty([(h * H, 0.1) for h in range(6, 30)], 0.1) == (False, None)    # never charged
    assert bt.night_empty([(6 * H, 0.1), (12 * H, 0.8), (30 * H, 0.3)], 0.1, []) == (True, None)  # holds the night


def test_a_charge_starting_before_the_predicted_charge_start_is_not_a_night_the_battery_lasted():
    # empty at 21:00, the sun charges from 08:20 while the predicted charge start is 09:00
    day = {6: 0.1, 12: 0.8, 18: 0.4, 21: 0.1, 32: 0.1, 32.5: 0.14, 33 - 1 / 12: 0.2}
    assert bt.night_empty([(h * H, s) for h, s in day.items()], 0.1) == (True, 21 * H)
    # empty all day, only the next morning's early charge: not charged
    flat = {6: 0.1, 18: 0.1, 32: 0.1, 32.5: 0.15}
    assert bt.night_empty([(h * H, s) for h, s in flat.items()], 0.1) == (False, None)
    # charged and discharging all night, but never down to the limit: lasts the night
    lasts = {6: 0.1, 12: 0.9, 24: 0.5, 32: 0.3, 32.5: 0.35}
    assert bt.night_empty([(h * H, s) for h, s in lasts.items()], 0.1) == (True, None)


def test_output_cap_limits_battery_and_pv_together():
    # 1600 W cap: with 500 W of its PV the battery delivers at most 1100 W, the rest of the load is grid
    b = bt.Battery(5000, 0.8, efficiency=1.0, output_w=1600, arrays=("balcony",))
    hours = [12 * H]
    caps = bt.limits([b], {"balcony": flat(500, [12])}, hours)
    assert caps.discharge == {12 * H: 1100} and caps.capped == {}
    sim = bt.simulate(b, flat(500, [12]), flat(2000, [12]), 12 * H, 13 * H, caps)
    assert sim.hours[12 * H]["battery"] == -1100 and sim.grid_wh == 400


def test_pv_above_the_cap_charges_the_battery_although_the_house_draws():
    # 2000 Wh PV behind an 800 W cap, 1500 Wh load: 800 reach the house, 1200 can only go into the battery
    b = bt.Battery(5000, 0.5, efficiency=1.0, output_w=800, arrays=("balcony",))
    caps = bt.limits([b], {"balcony": flat(2000, [12])}, [12 * H])
    assert caps.capped == {12 * H: 1200} and caps.discharge == {12 * H: 0}
    sim = bt.simulate(b, flat(2000, [12]), flat(1500, [12]), 12 * H, 13 * H, caps)
    assert sim.charged_wh == 1200 and sim.grid_wh == 700 and sim.hours[12 * H]["battery"] == 1200


def test_capped_pv_that_does_not_fit_is_lost():
    b = bt.Battery(1000, 1.0, efficiency=1.0, output_w=800, arrays=("balcony",))
    caps = bt.limits([b], {"balcony": flat(1000, [12])}, [12 * H])
    sim = bt.simulate(b, flat(1000, [12]), flat(100, [12]), 12 * H, 13 * H, caps)
    # 700 Wh surplus of the house plus 200 Wh above the cap, the battery is full
    assert sim.lost_wh == 900 and sim.charged_wh == 0


def test_caps_per_system_add_up_and_other_arrays_are_free():
    # two systems, each battery behind its own array; the roof has no cap
    a = bt.Battery(2000, 0.5, output_w=800, arrays=("a",))
    b = bt.Battery(2000, 0.5, output_w=1600, arrays=("b",), max_discharge_w=1000)
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
    b = bt.Battery(2000, 0.5, efficiency=1.0, output_w=800, arrays=("a",))
    pv = {h * H: (1000.0 if h in (12, 36) else 0.0) for h in range(10, 40)}
    load = {h * H: 100.0 for h in range(10, 40)}
    out = bt.day_simulation([b], pv, load, None, 10 * H, 24 * H, {"a": pv})
    assert out["charge_start"] == 36 * H and out["limits"].capped[36 * H] == 200
