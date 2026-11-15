"""Fixed power limits per PV sensor: the installation, the cuts per period, the forecast cap and its band."""
from datetime import datetime, timedelta, timezone

from conftest import load

fc = load("forecasting")
ct = load("curtailment")
inst = load("installation")
archive = load("archive")
bd = load("band")
ln = load("learning")
btest = load("backtest")
model = load("model")
solar = load("solar")

SITE = solar.Site(48.0, 8.0, 300.0)
ARRAYS = {"a": ln.ArrayConfig("a", "A", ("pv1", "pv2"), model.ArrayGeometry(30, 180, 1000), None),
          "b": ln.ArrayConfig("b", "B", ("pv3",), model.ArrayGeometry(30, 90, 500), None)}
DAY = datetime(2026, 6, 1, tzinfo=timezone.utc)
DAY_START = DAY.timestamp()
DAY_END = DAY_START + 86400
LIMITED = inst.build({"a": ["pv1", "pv2"], "b": ["pv3"]}, {"pv1": 200, "pv2": 200})


def weather(k: float = 0.8):
    return [model.WeatherHour(DAY_START + h * 3600, model.clearsky_weather(SITE, DAY_START + h * 3600).ghi * k, temp=15.0)
            for h in range(48)]


def run(hour, installation=None, actual=None, limited=None, curtailed=frozenset(), k=0.8):
    now = DAY + timedelta(hours=hour)
    return fc.pv(SITE, ARRAYS, {}, weather(k), {}, fc.Today(), actual or {}, {}, now, now, DAY_START, DAY_END,
                 curtailed, installation, limited)


def test_an_array_has_a_limit_only_when_every_sensor_has_one():
    assert LIMITED.array_limit("a") == 400 and LIMITED.array_limit("b") is None
    assert LIMITED.limited_arrays() == {"a": 400}
    partly = inst.build({"a": ["pv1", "pv2"]}, {"pv1": 200, "pv2": 0})          # 0 is no limit
    assert partly.array_limit("a") is None and partly.limited_arrays() == {}


def test_cuts_take_the_limit_closest_to_the_modules_first():
    house = inst.build({"a": ["pv1", "pv2"], "b": ["pv3"]}, {"pv1": 200, "pv2": 200}, ["soc"], 95)
    obs = ct.Observed({"pv1": ct.Stat(150, 192), "pv2": ct.Stat(100, 120), "pv3": ct.Stat(300)}, soc={"soc": 97})
    cuts = ct.cuts(house, obs)
    assert cuts["a"].reason is ct.Reason.INPUT_LIMIT and cuts["a"].fixed and cuts["a"].node == "pv1"
    assert cuts["b"].reason is ct.Reason.BATTERY_FULL and not cuts["b"].fixed and not cuts["b"].certain
    below = ct.Observed({"pv1": ct.Stat(150, 189)}, soc={"soc": 50})       # under 95 % of the limit: free
    assert ct.cuts(house, below) == {}
    # without a configured battery a state of charge says nothing
    assert ct.cuts(LIMITED, ct.Observed({}, soc={"soc": 100})) == {}


def test_limited_hours_from_the_period_maxima():
    peaks = {"pv1": {0.0: 191.0, 3600.0: 150.0}, "pv2": {7200.0: 199.0}, "pv3": {0.0: 1000.0}}
    assert ct.limited_hours(LIMITED, "a", peaks) == {0.0, 7200.0}
    assert ct.limited_hours(LIMITED, "b", peaks) == set()


def test_cap_takes_only_the_part_of_an_hour_above_the_limit():
    ramp = [0.0, 100.0, 200.0, 300.0, 400.0, 500.0]                     # mean 250
    assert ct.cap_hour(250.0, 1000.0, ramp) == 250.0                    # never above: exactly unchanged
    assert ct.cap_hour(250.0, 300.0, ramp) == (0 + 100 + 200 + 300 + 300 + 300) / 6
    assert ct.cap_hour(250.0, 300.0, None) == 250.0 and ct.cap_hour(500.0, 300.0, [0.0] * 6) == 300.0
    assert ct.cap_hour(0.0, 300.0, ramp) == 0.0


def test_forecast_is_capped_last_and_keeps_the_uncapped_values():
    plain, _, _ = run(10)
    capped, _, _ = run(10, LIMITED)
    assert plain.uncapped == {} and plain.limits == {}
    assert capped.limits == {"a": 400}
    assert max(capped.forecast["a"].values()) <= 400 + 1e-9 < max(plain.forecast["a"].values())
    assert capped.uncapped["forecast"]["a"] == plain.forecast["a"]
    assert capped.forecast["b"] == plain.forecast["b"]                  # an array without limit stays as it is
    assert capped.forecast[None] == {t: capped.forecast["a"][t] + capped.forecast["b"][t] for t in capped.forecast["a"]}
    assert capped.uncapped["forecast"][None] == plain.forecast[None]
    for name in ("morning", "expected", "held"):
        assert max(getattr(capped, name)["a"].values()) <= 400 + 1e-9
    # the clear-sky output stays the uncapped upper bound
    assert capped.clear["a"] == plain.clear["a"]


def test_measured_hours_stay_measured_and_hours_at_the_limit_only_raise_the_correction():
    t9, t10 = DAY_START + 9 * 3600, DAY_START + 10 * 3600
    actual = {"a": {t9: 395.0, t10: 398.0}}
    plain, _, _ = run(11, LIMITED, actual)
    assert plain.expected["a"][t9] == 395.0                              # what was measured is not capped
    # the uncapped forecast is far above the limit: measuring the limit says nothing about clouds
    assert plain.correction["a"] is not None and plain.correction["a"] < -0.3
    skipped, _, _ = run(11, LIMITED, actual, {"a": {t9, t10}})
    assert skipped.correction["a"] is None


def test_the_band_varies_the_clouds_of_the_uncapped_forecast_and_is_capped_afterwards():
    band = {"version": 2, "daily": {"all": [-0.3, 0.1, 20]}, "hourly": {"all": [-0.4, 0.1, 200]}}
    now = DAY_START + 9 * 3600
    plain, _, _ = run(9, k=0.5)
    fc.surplus(plain, SITE, band, [], [], now, DAY_END)
    capped, _, _ = run(9, LIMITED, k=0.5)
    fc.surplus(capped, SITE, band, [], [], now, DAY_END)
    limit_total = {t: 400 + capped.clear["b"].get(t, 0.0) * bd.CLEARNESS_MAX for t in capped.band["hours"]}
    for t, (lo, hi) in capped.band["hours"].items():
        assert lo <= capped.forecast[None][t] + 1e-6 and capped.forecast[None][t] <= hi + 1e-6
        assert hi <= limit_total[t] + 1e-6
        assert hi <= plain.band["hours"][t][1] + 1e-6
    assert capped.band["days"][1][1] < plain.band["days"][1][1]
    assert capped.band["days"][1][0] <= capped.band["days"][1][1] and set(capped.band["days"]) == set(plain.band["days"])


def test_archive_flags_hours_at_the_limit_and_the_band_skips_them():
    day = {"2026-06-01": {"hours": {str(DAY_START): {"forecast": {"a": 400.0}},
                                                               str(DAY_START + 3600): {"forecast": {"a": 300.0}}}}}
    archive.add_actuals(day, "a", {DAY_START: 395.0, DAY_START + 3600: 250.0}, DAY_START + 7200,
                        limited={DAY_START})
    hours = archive.days(day)[0].hours
    assert [h.limited for h in hours] == [True, False] and not any(h.curtailed for h in hours)
    assert "limited" in archive.MEASURED_FIELDS


def test_record_archive_flags_the_hours_of_a_limited_array():
    t = DAY_START + 12 * 3600
    store = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 400.0, "b": 200.0}}}}}
    history = ln.PvHistory({"pv1": {t: 199.0}, "pv2": {t: 196.0}, "pv3": {t: 200.0}}, {},
                           {"pv1": {t: 200.0}, "pv2": {t: 199.0}, "pv3": {t: 210.0}}, {}, {})
    ln.record_archive(store, SITE, ARRAYS, {}, history, t + 7200, 95.0, LIMITED)
    hour = store["2026-06-01"]["hours"][str(t)]
    assert hour["limited"] is True and hour["actual"] == {"a": 395.0, "b": 200.0}
    plain = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 400.0}}}}}
    ln.record_archive(plain, SITE, ARRAYS, {}, history, t + 7200, 95.0)
    assert "limited" not in plain["2026-06-01"]["hours"][str(t)]


def test_band_learning_leaves_out_hours_whose_forecast_was_capped():
    store = {}
    for d in range(50):
        hours = {}
        for h in (11, 12):
            t = DAY_START + d * 86400 + h * 3600
            hours[str(t)] = {"ghi": 800.0, "forecast": {"a": 400.0 if h == 12 else 300.0}, "actual": {"a": 200.0}}
        store[(DAY.date() + timedelta(days=d)).isoformat()] = {"hours": hours}
    kwargs = dict(clear_ghi=lambda ts: 900.0, clear_wh=lambda ts, ids: 1000.0)
    every = bd.learn_band(store, **kwargs)
    skipped = bd.learn_band(store, **kwargs, limits={"a": 400.0})
    assert every["hourly"]["all"][2] == 100 and skipped["hourly"]["all"][2] == 50
    assert skipped["hourly"]["all"][0] == -0.1                          # only the uncapped hours: (200 - 300) / 1000


def test_the_archived_model_on_measured_weather_is_capped_like_the_forecast():
    t = DAY_START + 12 * 3600
    store = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 400.0}}}}}
    history = ln.PvHistory({"pv1": {t: 199.0}, "pv2": {t: 196.0}}, {}, {"pv1": {t: 200.0}, "pv2": {t: 199.0}}, {}, {},
                           {t: 20.0}, {t: 900.0})
    learned = {"a": model.LearnedArray(), "b": model.LearnedArray()}
    ln.record_archive(store, SITE, {"a": ARRAYS["a"]}, learned, history, t + 7200, 95.0, LIMITED)
    assert store["2026-06-01"]["hours"][str(t)]["measured"]["a"] <= 400.0 + 1e-6
    plain = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 400.0}}}}}
    ln.record_archive(plain, SITE, {"a": ARRAYS["a"]}, learned, history, t + 7200, 95.0)
    assert plain["2026-06-01"]["hours"][str(t)]["measured"]["a"] > 600.0


sy = load("systems")
bt = load("battery")
STREAM = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load", "output_limit_w": 800}),
          "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "charges_from": "own_pv"}),
          "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p", "charges_from": "house"}),
          "r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all"}),
          "south": ("array", "South", {"system": "u"}), "roof": ("array", "Roof", {"system": "r"}),
          "bu": ("battery", "Ultra battery", {"system": "u", "soc_sensor": "soc.u", "max_soc": 100}),
          "ba": ("battery", "AC Pro battery", {"system": "a", "soc_sensor": "soc.a", "max_soc": 90})}
BATTERIES = {k: v[2] for k, v in STREAM.items() if v[0] == "battery"}


def test_a_full_battery_counts_for_the_arrays_it_can_curtail():
    systems = sy.systems_of(STREAM)
    arrays = {"south": ["pv.s"], "roof": ["pv.r"]}
    free = inst.build(arrays, {}, [], 95, systems, BATTERIES, "free")
    # the Ultra follows the load together with the AC Pro: both batteries; the roof delivers everything
    assert set(free.stores_of("south")) == {inst.Store("soc.u", 95.0), inst.Store("soc.a", 88.0)}
    assert free.stores_of("roof") == ()
    # zero export: the roof's inverter holds the export at zero without knowing the battery at the house grid,
    # so that battery being full changes nothing for the roof
    zero = inst.build(arrays, {}, [], 95, systems, BATTERIES, "none")
    assert zero.stores_of("roof") == () and set(zero.stores_of("south")) == set(free.stores_of("south"))
    # the old household setting still counts for every array
    old = inst.build(arrays, {}, ["soc.x"], 95, systems, BATTERIES, "free")
    assert inst.Store("soc.x", 95.0) in old.stores_of("roof") and old.soc_sensors() == ["soc.a", "soc.u", "soc.x"]
    soc = {"soc.u": {0.0: 96.0, 3600.0: 50.0}, "soc.a": {3600.0: 89.0, 7200.0: 80.0}}
    assert free.full_hours("south", soc) == {0.0, 3600.0} and free.full_hours("roof", soc) == set()
    cut = ct.cuts(free, ct.Observed({"pv.s": ct.Stat(300), "pv.r": ct.Stat(300)}, soc={"soc.a": 90.0}))
    assert cut["south"].reason is ct.Reason.BATTERY_FULL and cut["south"].node == "soc.a" and "roof" not in cut


def test_output_limit_without_battery_is_a_fixed_limit():
    one = {"s": ("system", "Inverter", {"name": "Inverter", "template": "inverter", "output_limit_w": 600}),
           "x": ("array", "X", {"system": "s"})}
    single = inst.build({"x": ["pv1", "pv2"]}, {"pv1": 400, "pv2": 400}, systems=sy.systems_of(one))
    assert single.array_limit("x") == 600 and single.limited_arrays() == {"x": 600}     # the tighter one wins
    two = {**one, "y": ("array", "Y", {"system": "s"})}
    shared = inst.build({"x": ["pv1"], "y": ["pv2"]}, systems=sy.systems_of(two))
    # shared by two arrays: lower bounds when they reach it together, and a cap of their sum (cap_group)
    assert shared.array_limit("x") is None and [c.arrays for c in shared.shared_caps("x")] == [("x", "y")]
    peaks = {"pv1": {0.0: 400.0, 3600.0: 100.0}, "pv2": {0.0: 200.0, 3600.0: 100.0}}
    assert ct.limited_hours(shared, "x", peaks) == {0.0} == ct.limited_hours(shared, "y", peaks)
    cut = ct.cuts(shared, ct.Observed({"pv1": ct.Stat(380), "pv2": ct.Stat(200)}))
    assert cut["y"].reason is ct.Reason.OUTPUT_LIMIT and cut["y"].fixed and cut["y"].node == "s"
    # a battery below it, or a limit per phase, makes it no fixed limit
    battery = {**one, "b": ("battery", "B", {"system": "s", "soc_sensor": "soc"})}
    assert inst.build({"x": ["pv1"]}, systems=sy.systems_of(battery)).caps == ()
    phase = {**one, "s": ("system", "Inverter", {"name": "Inverter", "output_limit_w": 600, "limit_per_phase": True})}
    assert inst.build({"x": ["pv1"]}, systems=sy.systems_of(phase)).caps == ()


def test_learning_leaves_out_full_hours_and_bounds_hours_at_a_shared_limit():
    arr = ln.ArrayConfig("x", "X", ("pv1",), model.ArrayGeometry(30, 180, 1000), None,
                         caps=((("pv1", "pv2"), 600.0),))
    assert arr.limit_w is None                                          # shared: no limit of its own
    history = ln.PvHistory({"pv1": {0.0: 300.0, 3600.0: 350.0, 7200.0: 100.0}, "pv2": {0.0: 300.0, 3600.0: 100.0}},
                           {}, {}, {}, {}, full={"x": frozenset({3600.0})})
    samples, _, floors = ln.array_samples(arr, history, 95.0, [])
    assert [t for t, _ in samples] == [0.0, 7200.0] and floors == {0.0}
    alone = ln.ArrayConfig("x", "X", ("pv1",), model.ArrayGeometry(30, 180, 1000), None, caps=((("pv1",), 600.0),))
    assert alone.limit_w == 600.0


def test_subsystems_share_the_output_limit_in_the_battery_forecast():
    shared = ("p", 800.0, ())
    ultra = bt.Battery(2000, 0.5, arrays=("south",), shared=shared)
    pro = bt.Battery(2000, 0.5, max_discharge_w=1200, shared=shared)
    caps = bt.limits([ultra, pro], {"south": {0.0: 1000.0, 3600.0: 300.0}}, [0.0, 3600.0])
    # 1000 W of PV behind 800 W together: 200 W only for the batteries, and they deliver nothing more
    assert caps.capped == {0.0: 200.0} and caps.discharge == {0.0: 0.0, 3600.0: 500.0}


def test_a_pv_only_subsystem_under_a_shared_limit_depends_on_the_other_batteries():
    micro = {**STREAM, "m": ("system", "Micro", {"name": "Micro", "template": "inverter", "parent": "p",
                                                 "regulation": "all"}),
             "balcony": ("array", "Balcony", {"system": "m"})}
    systems = sy.systems_of(micro)
    built = inst.build({"south": ["pv.s"], "balcony": ["pv.b"]}, {}, [], 95, systems, BATTERIES, "free",
                       max_soc={"bu": 80})
    # the shared 800 W may hold back the micro-inverter once the batteries are full; a limit from an entity counts
    assert set(built.stores_of("balcony")) == {inst.Store("soc.a", 88.0), inst.Store("soc.u", 78.0)}
    effects = {(e["node"], e["setting"]): e for e in sy.effects({}, systems, micro)}
    # zero export (the default) adds nothing: the roof is no part of Stream
    assert effects[("u", "full_learning")]["value"] == 2 and effects[("a", "full_learning")]["value"] == 2


def test_a_limit_per_phase_waits_for_the_phases():
    subs = {**STREAM, "p": ("system", "Stream", {"name": "Stream", "output_limit_w": 800, "limit_per_phase": True})}
    effects = {(e["node"], e["setting"]): e["status"] for e in sy.effects({}, sy.systems_of(subs), subs)}
    assert effects[("p", "output_limit")] == sy.NOT_YET


def test_a_battery_charging_at_its_maximum_counts_like_a_full_one_where_the_rest_cannot_go_out():
    power = {**STREAM, "bu": ("battery", "Ultra battery", {"system": "u", "soc_sensor": "soc.u", "max_soc": 100,
                                                          "battery_power": "p.u", "max_charge_w": 1000}),
             "ba": ("battery", "AC Pro battery", {"system": "a", "soc_sensor": "soc.a", "battery_power": "p.a",
                                                  "battery_power_invert": True, "max_charge_w": 500})}
    systems = sy.systems_of(power)
    batteries = {k: v[2] for k, v in power.items() if v[0] == "battery"}
    built = inst.build({"south": ["pv.s"], "roof": ["pv.r"]}, {}, [], 95, systems, batteries, "free")
    assert [c.sensor for c in built.chargers()] == ["p.a", "p.u"]
    # an inverted sensor reads charging as negative; kW to W
    means = inst.charge_power(built.chargers(), {"p.u": {0.0: 0.9, 3600.0: 0.5}, "p.a": {7200.0: -0.45}},
                              {"p.u": 1000.0, "p.a": 1000.0})
    assert means == {"p.u": {0.0: 900.0, 3600.0: 500.0}, "p.a": {7200.0: 450.0}}
    assert built.charge_hours("south", means, {}) == ({0.0, 7200.0}, set())
    assert built.charge_hours("roof", means, {}) == (set(), set())
    cut = ct.cuts(built, ct.Observed({"pv.s": ct.Stat(900)}, charging={"p.u": 990.0}))
    assert cut["south"].reason is ct.Reason.CHARGE_LIMIT and cut["south"].fixed
    # learning keeps the hour as a lower bound instead of leaving it out
    arr = ln.ArrayConfig("south", "South", ("pv.s",), model.ArrayGeometry(30, 180, 1000), None)
    history = ln.PvHistory({"pv.s": {0.0: 900.0, 3600.0: 400.0}}, {}, {}, {}, {}, charge={"south": frozenset({0.0})})
    samples, _, floors = ln.array_samples(arr, history, 95.0, [])
    assert [t for t, _ in samples] == [0.0, 3600.0] and floors == {0.0}
    # delivering everything without a limit, the rest goes out: charging at its maximum curtails nothing
    free = {**power, "p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "all"})}
    assert inst.build({"south": ["pv.s"]}, {}, [], 95, sy.systems_of(free), batteries, "free").chargers() == []
    # the household's zero export alone changes nothing: a system that delivers everything lets the rest go out
    assert inst.build({"south": ["pv.s"]}, {}, [], 95, sy.systems_of(free), batteries, "none").chargers() == []
    effects = {(e["node"], e["setting"]): e for e in sy.effects({"feed_in_mode": "free"}, systems, power)}
    assert effects[("u", "charge_learning")]["status"] == sy.SIMPLIFIED and effects[("u", "charge_learning")]["value"] == 1000
    unknown = {(e["node"], e["setting"]): e for e in sy.effects({"feed_in_mode": "free"}, systems, STREAM)}
    assert unknown[("u", "charge_learning")]["status"] == sy.NOT_YET


def test_the_charge_maximum_is_read_from_means_not_from_spikes():
    power = {**STREAM, "bu": ("battery", "Ultra battery", {"system": "u", "soc_sensor": "soc.u",
                                                          "battery_power": "p.u", "max_charge_w": 1000})}
    systems = sy.systems_of(power)
    batteries = {k: v[2] for k, v in power.items() if v[0] == "battery"}
    built = inst.build({"south": ["pv.s"]}, {}, [], 95, systems, batteries, "free")
    h = 3600.0
    # hours known only by their mean: from 85 % at the maximum, from 70 % unsure, below free
    hourly = {"p.u": {0.0: 860.0, h: 750.0, 2 * h: 690.0, 3 * h: 400.0}}
    assert built.charge_hours("south", hourly, {}) == ({0.0}, {h})
    # with kept 5-minute means they decide: one period at the maximum makes the hour a lower bound, whatever the
    # hourly mean; an hour whose periods all stay below is free even with a high mean
    fine = {"p.u": {3 * h + 600: 960.0, 3 * h + 900: 300.0, 0.0: 900.0, 300.0: 900.0}}
    assert built.charge_hours("south", hourly, fine) == ({3 * h}, {h})
    # learning leaves an unsure hour out, as one with a full battery; the backtest too
    arr = ln.ArrayConfig("south", "South", ("pv.s",), model.ArrayGeometry(30, 180, 1000), None)
    history = ln.PvHistory({"pv.s": {0.0: 900.0, h: 400.0, 2 * h: 500.0}}, {}, {}, {}, {},
                           charge={"south": frozenset({0.0})}, unsure={"south": frozenset({h})})
    samples, _, floors = ln.array_samples(arr, history, 95.0, [])
    assert [t for t, _ in samples] == [0.0, 2 * h] and floors == {0.0}
    assert set(btest.usable(arr, history, 95.0, [])) == {2 * h}


SHARED = {"s": ("system", "Inverter", {"name": "Inverter", "template": "inverter", "output_limit_w": 900}),
          "a": ("array", "A", {"system": "s"}), "b": ("array", "B", {"system": "s"})}
SHARED_INST = inst.build({"a": ["pv1", "pv2"], "b": ["pv3"]}, systems=sy.systems_of(SHARED))


def test_a_shared_limit_caps_the_sum_at_every_step_and_shares_the_cut_by_power():
    east, west = [400.0, 300.0, 200.0, 100.0], [100.0, 200.0, 300.0, 400.0]     # mean 250 each
    profiles = {"e": east, "w": west}
    # the sum is 500 at every step: under 500 nothing changes, not even by rounding
    assert ct.cap_group({"e": 250.0, "w": 250.0}, profiles.get, {}, [(("e", "w"), 500.0)]) == {"e": 250.0, "w": 250.0}
    out = ct.cap_group({"e": 500.0, "w": 250.0}, profiles.get, {}, [(("e", "w"), 900.0)])
    # e at 800, 600, 400, 200 and w at 100, 200, 300, 400: only the first step (900) is not above; at the second
    # 800 of 600 + 200: e gives up 3/4 of the cut
    steps_e = [800.0, 600.0, 400.0, 200.0]
    steps_w = [100.0, 200.0, 300.0, 400.0]
    capped = [(x, y) if x + y <= 900 else (x * 900 / (x + y), y * 900 / (x + y)) for x, y in zip(steps_e, steps_w)]
    assert abs(out["e"] - sum(x for x, _ in capped) / 4) < 1e-9 and abs(out["w"] - sum(y for _, y in capped) / 4) < 1e-9
    assert out["e"] + out["w"] <= 900 + 1e-9
    # own limits alone are cap_hour; an array without a profile counts with its mean at every step
    ramp = [0.0, 100.0, 200.0, 300.0, 400.0, 500.0]
    assert ct.cap_group({"x": 250.0}, {"x": ramp}.get, {"x": 300.0}, []) == {"x": ct.cap_hour(250.0, 300.0, ramp)}
    flat = ct.cap_group({"x": 600.0, "y": 600.0}, {"x": ramp}.get, {}, [(("x", "y"), 900.0)])
    assert abs(flat["x"] + flat["y"] - (sum(min(x * 2.4 + 600, 900) for x in ramp) / 6)) < 1e-9
    assert ct.cap_group({"x": 0.0, "y": -1.0}, profiles.get, {}, [(("x", "y"), 1.0)]) == {"x": 0.0, "y": -1.0}


def test_a_limit_shared_by_several_arrays_caps_their_forecast_together():
    plain, _, _ = run(10)
    capped, _, _ = run(10, SHARED_INST)
    assert capped.limits == {} and capped.caps == [(("a", "b"), 900.0)] and capped.capped == {"a", "b"}
    assert max(plain.forecast[None].values()) > 900
    for name in ("forecast", "morning", "expected"):
        series = getattr(capped, name)
        assert max(series[None].values()) <= 900 + 1e-6
        assert capped.uncapped[name][None] == getattr(plain, name)[None]
    for t in capped.held["a"]:
        assert capped.held["a"][t] + capped.held["b"].get(t, 0.0) <= 900 + 1e-6
    # each array keeps its share: neither is cut below what the other leaves it, and below the limit nothing changes
    for t, v in plain.forecast[None].items():
        if v <= 900 * 0.8:
            assert capped.forecast["a"][t] == plain.forecast["a"][t] and capped.forecast["b"][t] == plain.forecast["b"][t]
        assert capped.forecast["a"][t] <= plain.forecast["a"][t] + 1e-9 and capped.forecast["b"][t] <= plain.forecast["b"][t] + 1e-9
    assert capped.clear["a"] == plain.clear["a"]


def test_the_band_of_a_shared_limit_stays_below_it():
    band = {"version": 2, "daily": {"all": [-0.3, 0.1, 20]}, "hourly": {"all": [-0.4, 0.1, 200]}}
    now = DAY_START + 9 * 3600
    plain, _, _ = run(9, k=0.5)
    fc.surplus(plain, SITE, band, [], [], now, DAY_END)
    capped, _, _ = run(9, SHARED_INST, k=0.5)
    fc.surplus(capped, SITE, band, [], [], now, DAY_END)
    for t, (lo, hi) in capped.band["hours"].items():
        assert hi <= 900 + 1e-6 and lo <= capped.forecast[None][t] + 1e-6 <= hi + 2e-6
    assert capped.band["days"][1][1] < plain.band["days"][1][1]


def test_a_shared_limit_without_battery_counts_exactly():
    effects = {(e["node"], e["setting"]): e["status"] for e in sy.effects({}, sy.systems_of(SHARED), SHARED)}
    assert effects[("s", "output_limit")] == sy.EXACT


def test_band_learning_leaves_out_hours_whose_sum_reached_a_shared_limit():
    store = {}
    for d in range(50):
        hours = {}
        for h in (11, 12):
            t = DAY_START + d * 86400 + h * 3600
            hours[str(t)] = {"ghi": 800.0, "forecast": {"a": 500.0, "b": 400.0 if h == 12 else 200.0},
                             "actual": {"a": 300.0, "b": 100.0}}
        store[(DAY.date() + timedelta(days=d)).isoformat()] = {"hours": hours}
    kwargs = dict(clear_ghi=lambda ts: 900.0, clear_wh=lambda ts, ids: 1000.0)
    skipped = bd.learn_band(store, **kwargs, caps=[(("a", "b"), 900.0)])
    assert skipped["hourly"]["all"][2] == 50 and skipped["hourly"]["all"][0] == -0.3     # (400 - 700) / 1000


def test_nested_shared_limits_cut_from_the_inside_out():
    flat = {"a": [1.0], "b": [1.0], "c": [1.0]}.get
    out = ct.cap_group({"a": 400.0, "b": 400.0, "c": 600.0}, flat, {}, [(("a", "b", "c"), 1000.0), (("a", "b"), 500.0)])
    # the subsystem first: a and b at 250, then the parent takes 1000 of 1100 off all three
    assert abs(sum(out.values()) - 1000.0) < 1e-9 and abs(out["a"] + out["b"] - 500.0 * 1000 / 1100) < 1e-9


def test_the_archived_model_on_measured_weather_is_capped_by_a_shared_limit():
    t = DAY_START + 12 * 3600
    store = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 500.0, "b": 400.0}}}}}
    history = ln.PvHistory({"pv1": {t: 300.0}, "pv2": {t: 300.0}, "pv3": {t: 300.0}}, {}, {}, {}, {},
                           {t: 20.0}, {t: 900.0})
    learned = {"a": model.LearnedArray(), "b": model.LearnedArray()}
    ln.record_archive(store, SITE, ARRAYS, learned, history, t + 7200, 95.0, SHARED_INST)
    measured = store["2026-06-01"]["hours"][str(t)]["measured"]
    assert measured["a"] + measured["b"] <= 900.0 + 0.2
    plain = {"2026-06-01": {"hours": {str(t): {"forecast": {"a": 500.0, "b": 400.0}}}}}
    ln.record_archive(plain, SITE, ARRAYS, learned, history, t + 7200, 95.0)
    assert sum(plain["2026-06-01"]["hours"][str(t)]["measured"].values()) > 1000.0


def test_a_system_following_the_load_without_battery_counts_held_back_hours_as_at_least():
    subs = {"b": ("system", "Balcony", {"name": "Balcony", "template": "inverter", "regulation": "load"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all"}),
            "s": ("system", "Stream", {"name": "Stream", "template": "pv_battery", "regulation": "load"}),
            "bal": ("array", "Balcony", {"system": "b"}), "roof": ("array", "Roof", {"system": "r"}),
            "south": ("array", "South", {"system": "s"}), "bs": ("battery", "Stream battery", {"system": "s"})}
    systems = sy.systems_of(subs)
    # only the system without a battery to take the rest; full power holds nothing back
    assert sy.held_back_arrays(systems) == ("bal",)
    built = inst.build({"bal": ["pv.b"], "roof": ["pv.r"], "south": ["pv.s"]}, {}, [], 95, systems, {}, "none")
    # the household stopped importing at some time in the hour (export negative): the device may have regulated
    low = {0.0: 400.0, 3600.0: 20.0, 7200.0: -150.0}
    assert built.held_hours("bal", low) == {3600.0, 7200.0} and built.held_hours("roof", low) == set()
    # a subsystem whose sibling has a battery under the same control: that battery takes the rest
    coupled = {**subs, "p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load"}),
               "b": ("system", "Balcony", {"name": "Balcony", "template": "inverter", "parent": "p"}),
               "s": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p"})}
    assert sy.held_back_arrays(sy.systems_of(coupled)) == ()
    arr = ln.ArrayConfig("bal", "Balcony", ("pv.b",), model.ArrayGeometry(30, 180, 800), None)
    history = ln.PvHistory({"pv.b": {0.0: 300.0, 3600.0: 250.0, 7200.0: 200.0}}, {}, {}, {}, {},
                           held={"bal": frozenset({3600.0, 7200.0})})
    samples, _, floors = ln.array_samples(arr, history, 95.0, [])
    assert [t for t, _ in samples] == [0.0, 3600.0, 7200.0] and floors == {3600.0, 7200.0}
    # the Installation tab: simplified with the grid sensor, not used yet without it
    rows = {(e["node"], e["setting"]): e["status"] for e in sy.effects({"grid_power": "sensor.grid"}, systems, subs)}
    assert rows[("b", "regulation")] == sy.SIMPLIFIED and rows[("b", "held_learning")] == sy.SIMPLIFIED
    assert ("r", "held_learning") not in rows and ("s", "held_learning") not in rows
    # the backtest leaves such an hour out as a measure of the forecast, like one at a charge ceiling
    assert set(btest.usable(arr, history, 95.0, [])) == {0.0}
    assert {(e["node"], e["setting"]): e["status"] for e in sy.effects({}, systems, subs)}[("b", "regulation")] \
        == sy.NOT_YET


def test_hours_with_the_export_at_the_feed_in_limit_count_as_at_least():
    subs = {"r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all"}),
            "b": ("system", "Balcony", {"name": "Balcony", "template": "inverter", "regulation": "load"}),
            "roof": ("array", "Roof", {"system": "r"}), "bal": ("array", "Balcony", {"system": "b"})}
    systems = sy.systems_of(subs)
    # 6000 W: an hour counts from 5700 W export on (5 %); 800 W: from 700 W (at least 100 W)
    assert inst.limit_threshold(6000) == -5700 and inst.limit_threshold(800) == -700
    built = inst.build({"roof": ["pv.r"], "bal": ["pv.b"]}, {}, [], 95, systems, {}, "limited",
                       export_limit=(("roof", "bal"), 6000))
    low = {0.0: 300.0, 3600.0: 50.0, 7200.0: -5600.0, 10800.0: -5800.0}
    assert built.held_hours("roof", low) == {10800.0}
    # the balcony also follows the load: the threshold that marks more hours wins
    assert built.held_hours("bal", low) == {3600.0, 7200.0, 10800.0}
    plain = inst.build({"roof": ["pv.r"]}, {}, [], 95, systems, {}, "limited")
    assert plain.held_hours("roof", low) == set() and "roof" not in plain.held
