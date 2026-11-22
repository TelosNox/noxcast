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


def test_surplus_without_room_is_fed_in_up_to_the_feed_in_limit():
    # a full battery: 1000 Wh surplus per hour, nothing fits; the second hour is simulated from its half on
    b = bt.Battery(1000, 1.0, efficiency=1.0)
    pv, load_ = flat(1400, [10, 11]), flat(400, [10, 11])

    def run(grid):
        return bt.simulate(b, pv, load_, 10 * H, 11.5 * H, grid=grid)
    free = run(None)                                               # free feed-in: everything goes into the grid
    assert free.lost_wh == free.fed_in_wh == 1500 and free.curtailed_wh == 0
    assert free.hours[10 * H] == {"surplus": 1000, "battery": 0, "lost": 1000, "grid": 0, "fed_in": 1000,
                                       "curtailed": 0, "soc": 1.0}
    zero = run(bt.FeedIn(0.0))                                     # zero export: everything is curtailed
    assert zero.fed_in_wh == 0 and zero.curtailed_wh == zero.lost_wh == 1500
    limited = run(bt.FeedIn(600.0))                                # 600 W: 600 Wh, then 300 Wh of the half hour
    assert limited.fed_in_wh == 900 and limited.curtailed_wh == 600 and limited.lost_wh == 1500
    assert limited.hours[11 * H]["fed_in"] == 300 and limited.hours[11 * H]["curtailed"] == 200
    # a system that never feeds in (40 % of the PV) and one that always does (20 %): the latter counts against the
    # limit, which the inverters following it hold at the grid connection
    mixed = run(bt.FeedIn(300.0, {10 * H: 0.2}, {10 * H: 0.4}))
    assert mixed.hours[10 * H]["fed_in"] == 300 and mixed.hours[10 * H]["curtailed"] == 700
    under_zero = run(bt.FeedIn(0.0, {10 * H: 0.2}, {10 * H: 0.4}))   # it feeds in anyway, the others hold zero
    assert under_zero.hours[10 * H]["fed_in"] == 200
    free_rule = run(bt.FeedIn(None, {}, {10 * H: 0.4}))
    assert free_rule.hours[10 * H]["fed_in"] == 600 and free_rule.hours[11 * H]["fed_in"] == 500


def test_capped_pv_without_room_is_curtailed_not_fed_in():
    # 1000 Wh above an 800 W output limit: the battery takes 300 Wh of it, 700 Wh cannot reach house or grid
    b = bt.Battery(1000, 0.7, efficiency=1.0, output_w=800, arrays=("balcony",))
    pv = {12 * H: 1800.0}
    caps = bt.limits([b], {"balcony": pv}, [12 * H])
    sim = bt.simulate(bt.combine([b]), pv, {12 * H: 300.0}, 12 * H, 13 * H, caps)
    assert sim.charged_wh == 300 and sim.lost_wh == 1200
    assert sim.fed_in_wh == 500 and sim.curtailed_wh == 700        # the 500 Wh above the load go out, freely


def test_feed_in_shares_come_from_the_arrays():
    pv = {"roof": {10 * H: 3000.0, 11 * H: 0.0}, "balcony": {10 * H: 1000.0}, "stream": {10 * H: 1000.0}}
    rule = bt.feed_in(800.0, ("balcony",), ("stream",), pv, [10 * H, 11 * H])
    assert rule == bt.FeedIn(800.0, {10 * H: 0.2}, {10 * H: 0.2})
    assert bt.feed_in(None, (), (), pv, [10 * H]) == bt.FeedIn()
    # the balcony behind a 500 W output limit: only what gets past it counts (500 of 4500 Wh)
    capped = bt.feed_in(0.0, ("balcony",), (), pv, [10 * H], [(("balcony",), 500.0)])
    assert abs(capped.free[10 * H] - 500 / 4500) < 1e-9
    shared = bt.Battery(1000, 0.5, arrays=("balcony",), shared=("p", 1500.0, ("stream",)))
    assert bt.output_ceilings([shared, bt.Battery(1000, 0.5, output_w=800, arrays=("roof",))]) == [
        (("roof",), 800), (("balcony", "stream"), 1500.0)]
    out = bt.day_simulation([bt.Battery(1000, 1.0, efficiency=1.0)], {10 * H: 5000.0}, {10 * H: 1000.0}, None,
                            10 * H, 11 * H, pv, (0.0, ("balcony",), ("stream",)))
    assert out["feed_in"] == bt.FeedIn(0.0, {10 * H: 0.2}, {10 * H: 0.2})
    assert out["sim"].fed_in_wh == 800 and out["sim"].curtailed_wh == 3200


def test_outlook_runs_48_hours_past_the_charge_start_but_not_past_the_pv_forecast():
    b = bt.Battery(10000, 0.5, efficiency=1.0)
    days = range(0, 96)
    pv = {h * H: (2000.0 if h % 24 in range(10, 15) else 0.0) for h in days}
    load_ = flat(400.0, days)
    now = 12 * H
    out = bt.day_simulation([b], pv, flat(400.0, range(0, 48)), None, now, 24 * H, ahead=(load_, 96 * H))
    ahead = out["ahead"]
    assert ahead.points[0] == (now, 0.5) and ahead.points[-1][0] == now + 48 * H
    assert out["sim"].points[-1][0] == out["charge_start"] < ahead.points[-1][0]
    # the same hours give the same levels as the simulation until the charge start
    assert ahead.points[:len(out["sim"].points)] == out["sim"].points
    # it ends where the PV forecast ends, and without a load ahead there is none
    short = bt.day_simulation([b], pv, load_, None, now, 24 * H, ahead=(load_, 40 * H))
    assert short["ahead"].points[-1][0] == 40 * H
    assert "ahead" not in bt.day_simulation([b], pv, load_, None, now, 24 * H)


def test_one_system_full_loses_its_surplus_while_the_other_still_charges():
    # roof: small battery, much PV; balcony: big battery, little PV. As one storage the balcony's room would
    # take the roof's surplus; as separate systems it cannot reach it
    roof = bt.Battery(1000, 0.5, efficiency=1.0, arrays=("r",), unit="roof")
    balcony = bt.Battery(5000, 0.2, efficiency=1.0, arrays=("b",), unit="balcony")
    arrays = {"r": flat(1500, [11, 12]), "b": flat(300, [11, 12])}
    pv, load_ = flat(1800, [11, 12]), flat(300, [11, 12])
    out = bt.day_simulation([roof, balcony], pv, load_, None, 11 * H, 13 * H, arrays)
    per, sim = out["systems"], out["sim"]
    # the load is covered by both systems' PV in proportion: roof 250, balcony 50 Wh per hour
    assert per["roof"].full_at is not None and abs(per["roof"].lost_wh - (2 * 1250 - 500)) < 1e-6
    assert per["balcony"].full_at is None and abs(per["balcony"].charged_wh - 2 * 250) < 1e-6
    assert abs(sim.lost_wh - per["roof"].lost_wh) < 1e-6 and sim.full_at == per["roof"].full_at
    assert out["lost_full"] and min(out["lost_full"]) == 11 * H
    assert [u.key for u in out["units"]] == ["balcony", "roof"]          # for the what-if (views.unit_entry)
    one = bt.day_simulation([bt.Battery(1000, 0.5, efficiency=1.0), bt.Battery(5000, 0.2, efficiency=1.0)],
                            pv, load_, None, 11 * H, 13 * H)
    assert one["sim"].lost_wh == 0 and "systems" not in one


def test_systems_share_the_discharge_evenly_up_to_what_each_can_give():
    a = bt.Battery(5000, 0.5, efficiency=1.0, max_discharge_w=200, unit="a")
    b = bt.Battery(5000, 0.5, efficiency=1.0, unit="b")
    sims, whole = bt.simulate_units(bt.units_of([a, b], {}, [20 * H], False), {}, flat(600, [20]), 20 * H, 21 * H)
    assert abs(sims["a"].hours[20 * H]["battery"] + 200) < 1e-6 and abs(sims["b"].hours[20 * H]["battery"] + 400) < 1e-6
    assert whole.grid_wh == 0 and abs(whole.hours[20 * H]["soc"] - 0.44) < 1e-6
    # one runs empty: the other takes over, then the grid
    low = bt.Battery(5000, 0.02, efficiency=1.0, unit="a")
    sims, whole = bt.simulate_units(bt.units_of([low, b], {}, [20 * H], False), {}, flat(600, [20]), 20 * H, 21 * H)
    assert abs(sims["a"].hours[20 * H]["battery"] + 100) < 1e-6 and abs(sims["b"].hours[20 * H]["battery"] + 500) < 1e-6
    assert sims["a"].empty_at is None and whole.grid_wh == 0
    tiny = bt.Battery(1000, 0.2, efficiency=1.0, unit="b")
    sims, whole = bt.simulate_units(bt.units_of([low, tiny], {}, [20 * H], False), {}, flat(600, [20]), 20 * H, 21 * H)
    assert abs(whole.grid_wh - 600 + 100 + 200) < 1e-6 and sims["a"].empty_at == sims["b"].empty_at == 21 * H


def test_a_fixed_power_system_delivers_it_whatever_the_load():
    fixed = bt.Battery(2000, 0.5, efficiency=1.0, unit="f", fixed_w=200)
    other = bt.Battery(5000, 0.5, efficiency=1.0, unit="o")
    sims, whole = bt.simulate_units(bt.units_of([fixed, other], {}, [2 * H], False), {}, flat(100, [2]), 2 * H, 3 * H)
    assert abs(sims["f"].hours[2 * H]["battery"] + 200) < 1e-6 and sims["o"].hours[2 * H]["battery"] == 0
    assert abs(whole.lost_wh - 100) < 1e-6 and abs(whole.fed_in_wh - 100) < 1e-6   # above the load: out to the grid


def test_a_battery_at_the_house_grid_charges_from_what_the_others_send_out():
    pv_sys = bt.Battery(1000, 1.0, efficiency=1.0, arrays=("r",), unit="roof")
    ac = bt.Battery(2000, 0.5, efficiency=1.0, unit="ac", from_house=True)
    arrays = {"r": flat(1000, [12])}
    sims, whole = bt.simulate_units(bt.units_of([pv_sys, ac], arrays, [12 * H], True), flat(1000, [12]),
                                    flat(200, [12]), 12 * H, 13 * H)
    assert abs(sims["roof"].lost_wh - 800) < 1e-6 and abs(sims["ac"].charged_wh - 800) < 1e-6
    assert whole.lost_wh == 0 and abs(whole.hours[12 * H]["soc"] - (1000 + 1800) / 3000) < 1e-4


def test_pv_of_systems_without_a_battery_covers_the_load_first_and_counts_as_lost_for_the_house():
    a = bt.Battery(1000, 1.0, efficiency=1.0, unit="a")
    b = bt.Battery(1000, 1.0, efficiency=1.0, unit="b")
    sims, whole = bt.simulate_units(bt.units_of([a, b], {}, [12 * H], True), flat(500, [12]), flat(200, [12]),
                                    12 * H, 13 * H)
    assert sims["a"].lost_wh == sims["b"].lost_wh == 0
    assert abs(whole.lost_wh - 300) < 1e-6 and abs(whole.hours[12 * H]["surplus"] - 300) < 1e-6


def test_batteries_of_one_system_stay_one_storage():
    a = bt.Battery(1000, 0.5, efficiency=1.0, unit="s")
    b = bt.Battery(5000, 0.2, efficiency=1.0, unit="s")
    pv, load_ = flat(1800, [11, 12]), flat(300, [11, 12])
    same = bt.day_simulation([a, b], pv, load_, None, 11 * H, 13 * H)
    plain = bt.day_simulation([bt.Battery(1000, 0.5, efficiency=1.0), bt.Battery(5000, 0.2, efficiency=1.0)],
                              pv, load_, None, 11 * H, 13 * H)
    assert "systems" not in same and same["sim"].hours == plain["sim"].hours
    names = {"s": "Roof"}
    assert bt.system_figures(same, names) == []
