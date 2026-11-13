"""Websocket payloads of the panel and the day card (views.py), built from plain data without Home Assistant."""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from conftest import load

archive = load("archive")
bt = load("battery")
explain = load("explain")
fc = load("forecasting")
lf = load("load")
model = load("model")
solar = load("solar")
views = load("views")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(tilt=35, azimuth=180, peak_w=1000)
TZ = ZoneInfo("Europe/Berlin")
DAY = date(2026, 7, 1)
A = datetime.combine(DAY, time(), tzinfo=TZ).timestamp()        # local midnight
H = 3600.0


def day_start(d: date) -> float:
    return datetime.combine(d, time(), tzinfo=TZ).timestamp()


def arrays():
    return {"roof": SimpleNamespace(name="Roof", sensors=("sensor.pv1", "sensor.pv2"), geometry=GEO),
            "wall": SimpleNamespace(name="Wall", sensors=("sensor.pv3",),
                                    geometry=model.ArrayGeometry(90, 270, 600, extra=(model.Plane(90, 90, 200),)))}


def data(**kw) -> object:
    hours = {A + h * H: max(0.0, 400 - 40 * abs(h % 24 - 13)) + 0.04 for h in range(72)}
    d = fc.NoxcastData(
        forecast={None: dict(hours), "roof": dict(hours)}, clear={None: {t: v * 1.5 for t, v in hours.items()}},
        morning={"roof": {A + 12 * H: 300.04}}, expected={"roof": {A + 12 * H: 310.06}, None: {A + 12 * H: 310.0}},
        held={"roof": {A + 12 * H: 305.0}}, actual={"roof": {A + 10 * H: 200.0}}, correction={"roof": 0.05},
        effects={"roof": {"loss": {A + 9 * H: 12.34}, "gain": {}, "periods": [{"kind": "shade"}]}},
        updated=datetime(2026, 7, 1, 10, tzinfo=timezone.utc), day_start=A,
        band={"hours": {A + 12 * H: (250.04, 350.06)}, "days": {"1": (2.0, 5.0)}},
        weather=[model.WeatherHour(A + h * H, max(0.0, 700 - 80 * abs(h - 13)), temp=20.0) for h in range(48)])
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_series_and_band_rounding_and_window():
    assert views.series({2.0: 1.26, 1.0: 3.0}) == {"t": [1.0, 2.0], "wh": [3.0, 1.3]}
    assert views.series({1.0: 1.0, 2.0: 2.0, 3.0: 3.0}, 2.0, 3.0) == {"t": [2.0], "wh": [2.0]}
    assert views.band_series({"hours": {5.0: (1.04, 2.06), 1.0: (0.0, 0.0)}}, 2.0) == {"t": [5.0], "p10": [1.0], "p90": [2.1]}
    assert views.band_series(None) == {"t": [], "p10": [], "p90": []}
    assert views.columns([(1.0, 2, 3)], "a", "b") == {"t": [1.0], "a": [2], "b": [3]}


def test_learned_arrays():
    la = model.LearnedArray(efficiency=0.9, efficiency_months={"2026-06": [0.91, 120]})
    out = views.learned_arrays(arrays(), {"roof": la}, datetime(2026, 7, 1, 2, tzinfo=timezone.utc))
    assert [list(a) for a in out] == [["id", "name", "tilt", "azimuth", "peak_w", "planes", "learned", "facade_cells",
                                       "last_learning"]] * 2
    assert out[0]["learned"] == {**la.as_dict(), "efficiency_months": {"2026-06": {"efficiency": 0.91, "hours": 120}}}
    assert la.efficiency_months == {"2026-06": [0.91, 120]}                       # the stored state stays as it is
    assert out[0]["last_learning"] == "2026-07-01T02:00:00+00:00"
    assert out[1]["learned"] is None and out[1]["facade_cells"] == {}
    assert out[1]["planes"] == [{"tilt": 90, "azimuth": 270, "peak_w": 400}, {"tilt": 90, "azimuth": 90, "peak_w": 200}]


def test_today_arrays_and_totals():
    d = data()
    out = views.today_arrays(arrays(), d, A + 86400, A + 2 * 86400)
    roof = out[0]
    assert roof["expected"] == {"t": [A + 12 * H], "wh": [310.1]} and roof["forecast"] == {"t": [A + 12 * H], "wh": [305.0]}
    assert roof["loss"] == {"t": [A + 9 * H], "wh": [12.3]} and roof["periods"] == [{"kind": "shade"}]
    assert len(roof["tomorrow"]["wh"]) == 24 and roof["tomorrow_wh"] == 4001
    assert roof["updated"] == "2026-07-01T10:00:00+00:00" and out[1]["tomorrow"] == {"t": [], "wh": []}
    assert out[1]["tomorrow_wh"] == 0
    sim = bt.Simulation(points=[(A + 12 * H, 0.512345)], full_at=A + 14 * H,
                        hours={A + 12 * H: {"lost": 10.04}, A + 30 * H: {"lost": 99.0}})
    d.battery = {"sim": sim, "storage": bt.Battery(5000, 0.5, 0.9, 0.1), "day_end": A + 86400, "charge_start": A + 32 * H}
    d.load = lf.LoadForecast({A + 12 * H: 456.78}, {}, {}, 1.0)
    d.plan = {"c1": {"now": SimpleNamespace(pv_share=0.12345), "today": SimpleNamespace(start=1.0, end=2.0, pv_share=0.5)},
              "c2": {"now": SimpleNamespace(pv_share=0.0), "today": None}}
    totals = views.today_totals(d, {"c1": SimpleNamespace(name="Washer")}, 3, DAY, day_start)
    assert list(totals) == ["days", "load", "band", "battery", "plan"]
    assert totals["load"] == {"t": [A + 12 * H], "wh": [456.8]}
    assert totals["band"] == {"t": [A + 12 * H], "p10": [250.0], "p90": [350.1]}
    assert totals["battery"] == {"points": {"t": [A + 12 * H], "soc": [0.5123]}, "full": A + 14 * H, "empty": None, "lost_today": 10.0,
                                 "charge_start": A + 32 * H, "max": 0.9, "min": 0.1}
    assert totals["plan"] == [{"name": "Washer", "now": 0.123, "start": 1.0, "end": 2.0, "pv": 0.5},
                              {"name": "c2", "now": 0.0, "start": None, "end": None, "pv": None}]
    assert list(views.today_totals(data(band=None), {}, 3, DAY, day_start)) == ["days", "plan"]


def test_day_totals_need_most_hours_and_take_the_band():
    out = views.day_totals(data(), 4, DAY, day_start)
    assert [d["date"] for d in out] == ["2026-07-01", "2026-07-02", "2026-07-03"]     # the fourth day has no hours
    assert out[0] == {"date": "2026-07-01", "start": A, "wh": 4001, "clear_wh": 6001, "p10": None, "p90": None,
                      "expected_wh": None, "morning_wh": None}                        # too few hours, as for the sensors
    assert out[1]["p10"] == 2000 and out[1]["p90"] == 5000                           # band keys as stored (str)
    assert out[1]["expected_wh"] is None and out[1]["morning_wh"] is None            # today only
    full = {A + h * H: 100.4 for h in range(24)}
    assert views.day_totals(data(morning={None: full}), 1, DAY, day_start)[0]["morning_wh"] == 2410
    assert views.day_totals(data(expected={None: full}), 1, DAY, day_start)[0]["expected_wh"] == 2410


def test_load_entry_and_model_summaries():
    prog = lf.ProgramModel({"workday": [0.5] + [0.0] * 23, "weekend": [0.25] * 24}, {"workday": 1}, [1000.0, 200.0], 7)
    sess = lf.SessionModel({"workday": [None, 0.5] + [0.0] * 22, "weekend": [0.0] * 24}, 120.0, [1.0] * 24)
    reg = lf.RegulatedModel("heat", 15.0, 1000.0, 200.0, [1 / 24] * 24, 0.8, 40)
    consumers = {"dw": SimpleNamespace(name="Dishwasher", timing="fixed", sensor=None, energy="sensor.dw", kind="program"),
                 "tv": SimpleNamespace(name="TV", timing="fixed", sensor="sensor.tv", kind="session"),
                 "hp": SimpleNamespace(name="HP", timing="fixed", sensor="sensor.hp", kind="regulated"),
                 "w": SimpleNamespace(name="Washer", timing="flexible", sensor="sensor.w")}
    models = {"dw": {"program": prog}, "tv": {"session": sess}, "hp": {"regulated": reg}}
    profile = lf.Profile({"workday": [1.0] * 24, "weekend": [2.0] * 24})
    forecast = lf.LoadForecast({}, {2.0: 5.0, 1.0: 4.0}, {"dw": {1.0: 3.0}}, 1.1, 20.0, 9.0)
    out = views.load_entry("sensor.house", profile, consumers, {"w": profile}, models, forecast, {"mae": 1}, None, None)
    assert list(out) == ["house_load", "profile", "consumers", "forecast", "backtest", "events", "last_learning"]
    dw, tv, hp, w = out["consumers"]
    assert dw["sensor"] == "sensor.dw" and dw["model"] == {
        "runs": 7, "per_day": {"workday": 0.5, "weekend": 6.0}, "runs_max": {"workday": 1}, "energy_wh": 1200,
        "hours": 2, "expected": [500.0, 100.0] + [0.0] * 22}
    assert tv["model"]["expected"][:3] == [0.0, 60.0, 0.0] and hp["model"] == {
        "mode": "heat", "threshold": 15.0, "base_wh": 1000.0, "slope_wh": 200.0, "r2": 0.8, "days": 40}
    assert w["kind"] == "profile" and w["model"] is None and w["profile"] == profile.as_dict()
    assert out["forecast"] == {"k": 1.1, "sustained": 20.0, "sustained_since": 9.0,
                               "base": {"t": [1.0, 2.0], "w": [4.0, 5.0]}, "consumers": {"dw": {"t": [1.0], "w": [3.0]}}}
    assert views.load_entry("s", None, {}, {}, {}, None, None, None, None)["forecast"] is None
    events = {"days": 30.0, "runs": 4, "groups": [{"power_w": 2000, "count": 4, "hours": [8, 11], "last": 1.0}]}
    out = views.load_entry("s", None, {}, {}, {}, None, None, events, None)
    assert out["events"] == {"days": 30.0, "runs": 4, "groups": [{"power_w": 2000, "count": 4, "hours": {"q1": 8, "q3": 11},
                                                                  "last": 1.0}]}
    assert events["groups"][0]["hours"] == [8, 11]                                  # the stored events stay as they are


def test_sensor_roles_rows_and_diagnostics():
    consumers = {"dw": SimpleNamespace(name="DW", sensor=None, energy="sensor.dw", remaining="sensor.rem", elapsed=None)}
    batteries = {"b1": {"soc_sensor": "sensor.soc", "max_soc_entity": "number.max"}, "b2": {"soc_sensor": "sensor.soc2"}}
    roles = views.sensor_roles(arrays(), ["sensor.soc"], "sensor.house", consumers, batteries, {"b1": "Big"})
    assert roles == [("sensor.pv1", "pv", "Roof"), ("sensor.pv2", "pv", "Roof"), ("sensor.pv3", "pv", "Wall"),
                     ("sensor.soc", "curtailment_soc", "Noxcast"), ("sensor.house", "house_load", "Noxcast"),
                     ("sensor.dw", "consumer_energy", "DW"), ("sensor.rem", "remaining_time", "DW"),
                     ("sensor.soc", "battery_soc", "Big"), ("number.max", "battery_max", "Big"),
                     ("sensor.soc2", "battery_soc", "")]
    state = SimpleNamespace(state="12", attributes={"unit_of_measurement": "W"},
                            last_updated=datetime(2026, 7, 1, tzinfo=timezone.utc))
    row = views.sensor_row("sensor.pv1", "pv", "Roof", state, [{"start": A}, {"start": A + H}], True, 720)
    assert row == {"entity_id": "sensor.pv1", "role": "pv", "owner": "Roof", "state": "12", "unit": "W",
                   "state_class": None, "statistics": True, "coverage": 0.003, "first": "2026-06-30T22:00:00+00:00",
                   "updated": "2026-07-01T00:00:00+00:00"}
    row = views.sensor_row("sensor.x", "pv", "Roof", None, [{"start": datetime(2026, 7, 1, tzinfo=timezone.utc)}], False, 720)
    assert row["state"] is None and row["coverage"] is None and row["first"] == "2026-07-01T00:00:00+00:00"
    arc = {"2026-07-01": {"hours": {str(int(A)): {"ghi": 0.0, "forecast": {}, "actual": {}}}}, "2026-07-02": {"hours": {}}}
    entry = views.diagnostics_entry(
        "Noxcast", weather_models=["icon"], data=data(weather_fetched=datetime(2026, 7, 1, tzinfo=timezone.utc)),
        last_learning=None, learning_seconds=12.5, backtest_seconds=None, learning_error="boom", archive_errors={}, arrays=arrays(),
        learned={"roof": model.LearnedArray(efficiency=0.9, samples=5)}, exclusions=[{"start": A, "end": A + H}],
        archive=arc, fine={"s": {"a": {}}, "t": {"b": {1: 2}}}, sensors=[row])
    assert entry == {"title": "Noxcast",
                     "weather": {"source": "open_meteo", "models": ["icon"], "updated": "2026-07-01T00:00:00+00:00", "hours": 72},
                     "learning": {"last": None, "seconds": 12.5, "backtest_seconds": None, "error": "boom", "archive_errors": {}},
                     "arrays": [{"name": "Roof", "samples": 5, "coverage": 0.0, "efficiency": 0.9, "hints": []},
                                {"name": "Wall", "samples": 0, "coverage": None, "efficiency": None, "hints": []}],
                     "exclusions": [{"start": "2026-06-30T22:00:00+00:00", "end": "2026-06-30T23:00:00+00:00", "note": ""}],
                     "archive": {"days": 2, "complete": 1}, "fine": {"days": 1}, "sensors": [row]}
    assert views.diagnostics_entry("N", weather_models=[], data=None, last_learning=None, learning_seconds=None,
                                   backtest_seconds=None, learning_error=None, archive_errors={}, arrays={}, learned={}, exclusions=[], archive={}, fine={},
                                   sensors=[])["weather"] == {"source": "open_meteo", "models": [], "updated": None, "hours": 0}


def test_battery_entry_and_reviews():
    batteries = {"b1": {"soc_sensor": "sensor.soc", "capacity": 5}, "b2": {"capacity": 2}}
    assert views.soc_capacities(batteries) == {"sensor.soc": 5000.0}
    plain = views.battery_entry(batteries, {"b1": "Big"}, None, A)
    assert plain == {"batteries": [{"name": "Big", "capacity_kwh": 5, "soc_sensor": "sensor.soc"},
                                   {"name": "", "capacity_kwh": 2, "soc_sensor": None}]}
    sim = bt.Simulation(full_at=A + 14 * H, lost_wh=10.6, grid_wh=1.4, charged_wh=99.5)
    d = data(load=lf.LoadForecast({A + 11 * H: 100.04, A + 12 * H: 200.06, A + 13 * H: 300.0}, {}, {}, 1.0),
             battery={"sim": sim, "storage": bt.Battery(5000, 0.5, 0.9, 0.1, 0.95, 2000), "day_end": A + 86400,
                      "charge_start": A + 32 * H})
    out = views.battery_entry(batteries, {}, d, A + 11.5 * H)
    assert list(out) == ["batteries", "storage", "hours", "day_end", "charge_start", "sim"]
    assert out["hours"] == {"t": [A + 11 * H, A + 12 * H, A + 13 * H], "pv": [320.0, 310.0, 400.0],
                            "load": [100.0, 200.1, 300.0], "capped": [0.0, 0.0, 0.0], "discharge": [None, None, None]}
    # with an inverter output limit: PV above it per hour and what the batteries may deliver
    d.battery["limits"] = bt.Limits({A + 12 * H: 50.04}, {A + 11 * H: 800.0, A + 12 * H: 0.0})
    capped = views.battery_entry(batteries, {}, d, A + 11.5 * H)["hours"]
    assert capped["capped"] == [0.0, 50.0, 0.0] and capped["discharge"] == [800.0, 0.0, None]
    # an hour without a cap gets the storage's own discharge power, as in the simulation, not "no limit"
    d.battery["storage"] = bt.Battery(5000, 0.5, 0.9, 0.1, 0.95, 2000, 1500)
    assert views.battery_entry(batteries, {}, d, A + 11.5 * H)["hours"]["discharge"] == [800.0, 0.0, 1500]
    d.battery["limits"] = bt.Limits()
    assert views.battery_entry(batteries, {}, d, A + 11.5 * H)["hours"]["discharge"] == [1500, 1500, 1500]
    d.battery["storage"] = bt.Battery(5000, 0.5, 0.9, 0.1, 0.95, 2000)
    assert out["sim"] == {"full": A + 14 * H, "empty": None, "lost_wh": 11, "grid_wh": 1, "charged_wh": 100}
    assert out["storage"]["max_charge_w"] == 2000 and out["storage"]["max_discharge_w"] is None
    assert out["storage"]["held_wh"] == 0.0                       # the room batteries near their limit do not use
    stats = {"sensor.soc": [{"start": A, "min": 20.0, "max": 60.0}, {"start": datetime.fromtimestamp(A + H, timezone.utc),
                                                                     "min": 30.0, "max": 90.0},
                            {"start": A + 2 * H, "min": None, "max": 50.0}]}
    days = views.soc_review(stats, {"sensor.soc": 5000.0}, lambda t: "d")
    assert days == [{"date": "d", "min": 0.2, "max": 0.9, "hours": 2}]
    log = {"2026-07-01": {"made": A, "until": A + 86400, "max": 0.9, "min": 0.1}}
    short = {"sensor.soc": [{"start": A + i * 300, "mean": 50.0 + i} for i in range(60)]}
    review = views.prediction_review(log, short, {"sensor.soc": 5000.0}, lambda key: A + 86400)
    assert review == [{"date": "2026-07-01", "predicted": log["2026-07-01"],
                       "actual": {"full_at": A + 38 * 300, "empty_at": None, "charged": True, "max_soc": 1.09, "complete": False,
                                  "day_complete": False}}]


def test_card_config():
    batteries = {"b1": {"soc_sensor": "sensor.soc", "capacity": 5, "max_soc": 100, "max_soc_entity": "number.max",
                        "min_soc": 10, "battery_power": "", "battery_power_invert": True}}
    out = views.card_config(arrays(), "sensor.house", None, True, None, batteries, {}, "weather.noxcast")
    assert out == {"pv": {"groups": [{"entities": ["sensor.pv1", "sensor.pv2"], "name": "Roof", "array": "Roof"},
                                     {"entities": ["sensor.pv3"], "name": "Wall", "array": "Wall"}]},
                   "home": {"entity": "sensor.house"}, "grid": {},
                   "batteries": [{"invert": True, "soc": "sensor.soc", "capacity": 5, "max_soc": "number.max",
                                  "min_soc": 10}],
                   "export": False, "weather": "weather.noxcast"}
    out = views.card_config({}, None, "sensor.grid", None, True, {}, {}, None)
    assert out["grid"] == {"entity": "sensor.grid", "invert": False} and out["home"] == {} and out["export"] is True


def test_day_view_of_today_merges_archive_and_current_data():
    arc = {"2026-07-01": {"issued": "x", "hours": {str(int(A + h * H)): {"ghi": 500.0, "temp": 15.0, "forecast": {"roof": 9.0}}
                                                   for h in (4, 5, 12)}}}
    d = data()
    view = views.day_view(SITE, arrays(), {"roof": model.LearnedArray(efficiency=0.9)}, archive.day(arc, DAY), d,
                          A, A + 86400, A + 12.5 * H)
    roof, wall = view["arrays"]
    assert list(roof) == ["id", "name", "peak_w", "steps", "steps_from", "efficiency", "day_ahead", "actual", "forecast", "morning",
                          "held", "expected", "correction", "periods"]
    none = {"t": [], "wh": []}
    assert roof["day_ahead"] == {"t": [A + 4 * H, A + 5 * H, A + 12 * H], "wh": [9.0, 9.0, 9.0]} and wall["day_ahead"] == none
    assert roof["actual"] == {"t": [A + 10 * H], "wh": [200.0]} and wall["actual"] == none
    assert wall["efficiency"] == model.DEFAULT_EFFICIENCY
    assert roof["correction"] == 0.05 and wall["correction"] is None and len(roof["forecast"]["t"]) == 24
    assert view["band"] == {"t": [A + 12 * H], "p10": [250.0], "p90": [350.1]} and view["history"] == []
    w = view["weather"]
    i = w["t"].index(A + 12 * H)
    assert w["ghi"][i] == 620.0 and w["temp"][i] == 20.0                            # the current source wins
    hours = [model.WeatherHour(t, w.ghi, None, None, w.temp) for t, w in sorted(
        {**{h.start: h for h in explain.archived_weather(archive.day(arc, DAY))},
         **{w.start: w for w in d.weather if A <= w.start < A + 86400}}.items())]
    steps = explain.hour_steps(SITE, GEO, model.LearnedArray(efficiency=0.9), hours)
    assert roof["steps"] == {str(int(t)): s for t, s in steps.items()} and roof["steps_from"] == "current"
    # after the morning fix the steps stored with it, even where newer weather says otherwise
    fixed = {A + 12 * H: explain.unpacked([500.0, -150.0, -20.0, 0.0, 0.0, -30.0])}
    d.morning_steps = {"roof": fixed}
    view = views.day_view(SITE, arrays(), {"roof": model.LearnedArray(efficiency=0.9)}, archive.day(arc, DAY), d,
                          A, A + 86400, A + 12.5 * H)
    roof, wall = view["arrays"]
    assert roof["steps_from"] == "morning" and roof["steps"] == {str(int(A + 12 * H)): fixed[A + 12 * H]}
    assert roof["steps"][str(int(A + 12 * H))]["result"] == 300.0 and wall["steps_from"] == "current"
    # a coming day: no archive, only the forecast
    later = views.day_view(SITE, arrays(), {}, None, d, A + 86400, A + 2 * 86400, A + 12.5 * H)
    assert list(later["arrays"][0]) == ["id", "name", "peak_w", "steps", "steps_from", "efficiency", "day_ahead", "actual",
                                        "forecast"]
    assert later["arrays"][0]["steps_from"] == "current"
    assert views.day_pv(view) == {A + 10 * H: 200.0}
    assert views.day_pv({"arrays": [{"actual": {"t": [1.0], "wh": [2.0]}}, {"actual": {"t": [1.0, 2.0], "wh": [3.0, 1.0]}},
                                    {}]}) == {1.0: 5.0, 2.0: 1.0}


def _tuples(v, path=""):
    """Paths of lists that hold lists (positional tuples) anywhere in a payload."""
    if isinstance(v, dict):
        return [p for k, x in v.items() for p in _tuples(x, f"{path}.{k}")]
    if isinstance(v, list):
        return ([path] if any(isinstance(x, (list, tuple)) for x in v) else []) + \
            [p for x in v for p in _tuples(x, f"{path}[]")]
    return []


METRIC = {"day_error": 0.1, "hour_error": 0.2, "bias": -0.01, "hour_mae_wh": 30.0, "days": 40, "hours": 400}
BACKTEST = {"version": 1, "computed_at": A, "seconds": 9.3,
            "replay": {"arrays": {"roof": {"status": "ok", "then": METRIC, "now": METRIC, "baseline": METRIC},
                                  "gone": {"status": "ok", "then": METRIC, "now": METRIC, "baseline": METRIC}},
                       "total": {"status": "ok", "then": METRIC, "now": METRIC, "baseline": METRIC}},
            "holdout": {"arrays": {"roof": {"status": "not_enough_data", "reason": "few_days", "days": 12, "needed": 28}},
                        "total": {"status": "not_enough_data", "reason": "few_days", "days": 12, "needed": 28}}}


def test_backtest_entry():
    e = views.backtest_entry(arrays(), BACKTEST)
    assert e["arrays"][0]["id"] == "roof" and e["computed_at"] == A and "seconds" not in e
    assert set(e["replay"]["arrays"]) == {"roof"} and e["replay"]["total"]["now"] == METRIC     # a removed array is not shown
    assert e["holdout"]["arrays"]["roof"]["needed"] == 28
    empty = views.backtest_entry(arrays(), None)                 # before the first run
    assert empty["replay"] is None and empty["holdout"] is None and empty["computed_at"] is None


def test_payloads_hold_no_positional_tuples():
    """Every value is named (see the conventions in views.py): no list of lists in any payload."""
    d = data(load=lf.LoadForecast({A + 12 * H: 456.78}, {A + 12 * H: 400.0}, {"c": {A + 12 * H: 50.0}}, 1.0))
    d.battery = {"sim": bt.Simulation(points=[(A + 12 * H, 0.5)], hours={}), "storage": bt.Battery(5000, 0.5, 0.9, 0.1),
                 "day_end": A + 86400, "charge_start": A + 32 * H}
    arc = {"2026-07-01": {"issued": "x", "hours": {str(int(A + 12 * H)): {"ghi": 500.0, "forecast": {"roof": 9.0}}},
                          "history": {str(A + 6 * H): {str(A + 12 * H): 300.0}}}}
    la = model.LearnedArray(efficiency=0.9, efficiency_months={"2026-06": [0.91, 120]})
    events = {"days": 30.0, "groups": [{"power_w": 2000, "hours": [8, 11]}]}
    payloads = {
        "today": {"arrays": views.today_arrays(arrays(), d, A + 86400, A + 2 * 86400),
                  **views.today_totals(d, {}, 3, DAY, day_start)},
        "day": views.day_view(SITE, arrays(), {}, archive.day(arc, DAY), d, A, A + 86400, A + 12.5 * H),
        "battery": views.battery_entry({"b1": {"capacity": 5}}, {}, d, A + 11.5 * H),
        "load": views.load_entry("s", None, {}, {}, {}, d.load, None, events, None),
        "learned": views.learned_arrays(arrays(), {"roof": la}, None),
        "backtest": views.backtest_entry(arrays(), BACKTEST),
        "overview": views.overview_entry(arrays(), d, {}, 3, DAY, day_start, arc, {}, [], {}, None),
    }
    assert payloads["day"]["history"] == [{"at": A + 6 * H, "t": [A + 12 * H], "wh": [300.0]}]
    assert {k: _tuples(v) for k, v in payloads.items()} == {k: [] for k in payloads}


def test_replay_of_an_archived_day():
    arc = {"2026-07-01": {"hours": {str(int(A + h * H)): {"ghi": 600.0 if h else None, "temp": 18.0, "forecast": {}}
                                    for h in (0, 12)}}}
    weather = explain.archived_weather(archive.day(arc, DAY))
    assert weather == [model.WeatherHour(A, 0.0, None, None, 18.0), model.WeatherHour(A + 12 * H, 600.0, None, None, 18.0)]
    assert explain.archived_weather(None) == []
    la = model.LearnedArray(efficiency=0.9)
    out = explain.replay(SITE, {"roof": GEO, "new": GEO}, {"roof": la}, weather)
    assert out["roof"] == explain.hour_steps(SITE, GEO, la, weather) and list(out["roof"]) == [A + 12 * H]
    assert out["new"] == explain.hour_steps(SITE, GEO, model.LearnedArray(), weather)
    fc_ = model.forecast(SITE, GEO, la, weather)
    assert abs(out["roof"][A + 12 * H]["result"] - fc_[A + 12 * H]) < 0.05


def test_learning_stage_steps():
    la = lambda **kw: model.LearnedArray(**{"samples": 500, "first_sample": A - 200 * 86400, "learned_at": A,
                                             "coverage": 0.8, **kw})
    assert views.learning_stage(None) == "waiting"
    assert views.learning_stage(la(samples=0)) == "waiting"
    assert views.learning_stage(la(first_sample=A - 5 * 86400)) == "early"            # too few days
    assert views.learning_stage(la(samples=50)) == "early"                            # too few hours
    assert views.learning_stage(la(coverage=0.3)) == "growing"
    assert views.learning_stage(la()) == "settled"


def test_learning_status_payload():
    arrays = {"r": SimpleNamespace(name="Roof"), "b": SimpleNamespace(name="Balcony")}
    learned = {"r": model.LearnedArray(samples=500, first_sample=A - 30 * 86400, learned_at=A, coverage=0.4)}
    nxt = datetime(2026, 7, 2, 1, 30, tzinfo=TZ)
    out = views.learning_status(arrays, learned, datetime(2026, 7, 1, 1, 30, tzinfo=TZ), 95.0, None, False, nxt)
    assert out["running"] is False and out["seconds"] == 95.0 and out["next"] == nxt.isoformat()
    assert out["arrays"] == [
        {"id": "r", "name": "Roof", "stage": "growing", "hours": 500, "days": 30, "coverage": 0.4},
        {"id": "b", "name": "Balcony", "stage": "waiting", "hours": 0, "days": 0, "coverage": 0.0}]


def _archive_day(day: date, fc: float, act: float | None, hours: int = 24, curtailed: int = 0) -> dict:
    a = day_start(day)
    return {"hours": {str(int(a + h * H)): {"forecast": {"roof": fc / 2 / hours, "wall": fc / 2 / hours},
                                             "actual": None if act is None else {"roof": act / 2 / hours, "wall": act / 2 / hours},
                                             "curtailed": h < curtailed}
                      for h in range(hours)}}


def test_overview_today_sums_the_arrays_hour_by_hour():
    d = data(held={"roof": {A + 11 * H: 100.04, A + 12 * H: 200.0}, "wall": {A + 11 * H: 50.0, A + 12 * H: 60.0}},
             actual={"roof": {A + 11 * H: 90.0}, "wall": {A + 11 * H: 40.0, A + 12 * H: 1.0}},  # 12 h: roof not measured
             expected={None: {A + 12 * H: 270.0, A + 30 * H: 1.0}},
             morning={"roof": {A + 11 * H: 300.0, A + 12 * H: 310.0}, "wall": {A + 11 * H: 100.0}})  # 12 h: wall not fixed
    out = views.overview_today(arrays(), d, A, A + 86400)
    assert out["t"] == [A + 11 * H, A + 12 * H]                          # tomorrow's expected hour is not today
    assert out["forecast"] == [150.0, 260.0] and out["actual"] == [130.0, None] and out["expected"] == [None, 270.0]
    assert out["morning"] == [400.0, None]
    assert out["clear"] == [round(d.clear[None][A + 11 * H], 1), round(d.clear[None][A + 12 * H], 1)]
    assert out["p10"] == [None, 250.0] and out["p90"] == [None, 350.1]


def test_recent_accuracy_counts_complete_days_as_the_accuracy_view():
    today = date(2026, 7, 20)
    arc = {"2026-07-19": _archive_day(date(2026, 7, 19), 11300, 10400, curtailed=2),
           "2026-07-18": _archive_day(date(2026, 7, 18), 9000, 10000),
           "2026-07-17": _archive_day(date(2026, 7, 17), 9000, None),        # not learned yet
           "2026-07-16": _archive_day(date(2026, 7, 16), 9000, 9000, hours=12),   # too few hours
           "2026-07-01": _archive_day(date(2026, 7, 1), 1000, 5000),          # before the 14 days
           "2026-07-20": _archive_day(today, 1000, 1000)}                     # today is not done
    out = views.recent_accuracy(arc, arrays(), today)
    assert out["yesterday"] == {"date": "2026-07-19", "forecast_wh": 11300, "actual_wh": 10400, "curtailed": 2}
    assert out["days"] == 2 and out["span"] == 14
    assert out["error"] == round((900 + 1000) / 20400, 3) and out["bias"] == round((900 - 1000) / 20400, 3)
    # an array without forecast in the archive (added later) leaves no complete day
    more = {**arrays(), "new": SimpleNamespace(name="New", sensors=(), geometry=GEO)}
    assert views.recent_accuracy(arc, more, today) == {"yesterday": None, "days": 0, "span": 14, "error": None, "bias": None}


def test_overview_consumers_decide_as_the_binary_sensor():
    win = lambda s, e, pv: SimpleNamespace(start=s, end=e, pv_share=pv, lost_share=0.0)   # noqa: E731
    plan = {"dw": {"now": win(1, 2, 0.88), "today": win(3, 4, 0.91), "tomorrow": win(5, 6, 0.745)},
            "wm": {"now": win(1, 2, 0.88), "today": None, "tomorrow": None}}
    consumers = {"dw": SimpleNamespace(name="Dishwasher", start_share=None), "wm": SimpleNamespace(name="Washer", start_share=90)}
    out = views.overview_consumers(consumers, plan)
    assert out == [{"name": "Dishwasher", "now": 0.88, "start_now": True, "today": {"start": 3, "end": 4, "pv": 0.91},
                    "tomorrow": {"start": 5, "end": 6, "pv": 0.745}},
                   {"name": "Washer", "now": 0.88, "start_now": False, "today": None, "tomorrow": None}]   # below its 90 %


def test_overview_entry():
    d = data()
    status = {"running": False, "last": None, "arrays": []}
    sensors = [("sensor.pv1", "pv", "Roof", "1200"), ("sensor.pv2", "pv", "Roof", "unavailable"),
               ("sensor.pv3", "pv", "Wall", None)]
    out = views.overview_entry(arrays(), d, {}, 3, DAY, day_start, {}, status, sensors,
                               {"b1": {"soc_sensor": "sensor.soc", "capacity": 5}}, "sensor.rest")
    assert list(out) == ["today", "day_end", "days", "battery", "load", "consumers", "accuracy", "status", "live"]
    assert out["day_end"] == A + 86400 and [x["date"] for x in out["days"]] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert out["battery"] is None and out["load"] is None and out["consumers"] == []
    assert out["status"] == {**status, "sensors": 3, "unavailable": [
        {"entity_id": "sensor.pv2", "role": "pv", "owner": "Roof"}, {"entity_id": "sensor.pv3", "role": "pv", "owner": "Wall"}]}
    assert out["live"] == {"pv": ["sensor.pv1", "sensor.pv2", "sensor.pv3"], "soc": [{"entity": "sensor.soc", "capacity": 5000.0}],
                           "remaining": "sensor.rest"}
    empty = views.overview_entry(arrays(), None, {}, 3, DAY, day_start, {}, status, [], {}, None)
    assert empty["today"] is None and empty["days"] == [] and empty["consumers"] == []


def test_recent_accuracy_ends_with_the_last_complete_day():
    """Before the nightly run yesterday has no actual: the range then ends the day before, as in the accuracy view."""
    today = date(2026, 7, 20)
    arc = {k.isoformat(): _archive_day(k, 1000, 1000) for k in (date(2026, 7, 4) + timedelta(days=i) for i in range(15))}
    arc["2026-07-19"] = _archive_day(date(2026, 7, 19), 1000, None)
    out = views.recent_accuracy(arc, arrays(), today)
    assert out["yesterday"] is None and out["days"] == 14 and out["error"] == 0.0     # 5 to 18 July
