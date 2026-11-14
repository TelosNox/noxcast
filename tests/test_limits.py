"""Fixed power limits per PV sensor: the installation, the cuts per period, the forecast cap and its band."""
from datetime import datetime, timedelta, timezone

from conftest import load

fc = load("forecasting")
ct = load("curtailment")
inst = load("installation")
archive = load("archive")
bd = load("band")
ln = load("learning")
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
    assert free.stores_of("south") == (inst.Store("soc.u", 95.0), inst.Store("soc.a", 88.0))
    assert free.stores_of("roof") == ()
    # zero export: the roof's surplus can only go into the battery at the house grid
    zero = inst.build(arrays, {}, [], 95, systems, BATTERIES, "none")
    assert zero.stores_of("roof") == (inst.Store("soc.a", 88.0),)
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
    # shared by two arrays: lower bounds when they reach it together, but no cap of a single forecast
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
