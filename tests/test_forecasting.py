"""Hourly forecast pipeline: today's measurements, morning fix, totals, held values, load, band, battery, plan."""
from datetime import datetime, timedelta, timezone

from conftest import load

fc = load("forecasting")
ln = load("learning")
lf = load("load")
bt = load("battery")
pl = load("plan")
model = load("model")
explain = load("explain")
solar = load("solar")

SITE = solar.Site(48.0, 8.0, 300.0)
ARRAYS = {"a": ln.ArrayConfig("a", "A", ("pv1",), model.ArrayGeometry(30, 180, 1000), None),
          "b": ln.ArrayConfig("b", "B", ("pv2",), model.ArrayGeometry(30, 90, 500), None)}
DAY = datetime(2026, 6, 1, tzinfo=timezone.utc)
DAY_START = DAY.timestamp()
DAY_END = DAY_START + 86400


def weather():
    return [model.WeatherHour(DAY_START + h * 3600, model.clearsky_weather(SITE, DAY_START + h * 3600).ghi * 0.8, temp=15.0)
            for h in range(48)]


def run(hour, before=None, actual=None):
    now = DAY + timedelta(hours=hour)
    return fc.pv(SITE, ARRAYS, {}, weather(), {}, before or fc.Today(), actual or {}, {}, now, now, DAY_START, DAY_END)


def test_curtailed_hours_from_the_highest_state_of_charge():
    now = DAY_START + 12 * 3600 + 20 * 60
    hourly = {"soc1": {DAY_START + 9 * 3600: 90.0, DAY_START + 10 * 3600: 96.0},
              "soc2": {DAY_START + 9 * 3600: 95.0, DAY_START + 10 * 3600: None}}
    recent = {"soc1": {DAY_START + 11 * 3600 + 300: 97.0, DAY_START + 12 * 3600: 80.0}}
    assert fc.curtailed_hours(hourly, recent, [80.0], 95.0, now) == {
        DAY_START + 9 * 3600, DAY_START + 10 * 3600, DAY_START + 11 * 3600}
    # the live state counts for the running hour
    assert DAY_START + 12 * 3600 in fc.curtailed_hours({}, {}, [80.0, 95.0], 95.0, now)
    assert fc.curtailed_hours({}, {}, [], 95.0, now) == set()


def test_today_actual_takes_the_just_ended_and_running_hour_from_5_minute_values():
    now = DAY_START + 12 * 3600 + 20 * 60
    hourly = {"pv1": {DAY_START + 10 * 3600: 400.0}}
    recent = {"pv1": {DAY_START + 11 * 3600 + i * 300: 500.0 for i in range(12)}
              | {DAY_START + 12 * 3600 + i * 300: 600.0 for i in range(4)}}
    hours, running = fc.today_actual({"a": ARRAYS["a"]}, hourly, recent, {"pv1": 1.0}, DAY_START, now)
    assert hours["a"] == {DAY_START + 10 * 3600: 400.0, DAY_START + 11 * 3600: 500.0}
    assert running["a"] == (DAY_START + 12 * 3600, 600.0, 4 / 12)


def test_morning_fix_totals_and_held_hours():
    early, today, _ = run(5)
    assert early.morning_fixed is None and today.morning_fixed is None
    assert early.forecast[None] == {t: early.forecast["a"][t] + early.forecast["b"][t] for t in early.forecast["a"]}
    later, today, _ = run(7, today)
    assert today.morning_fixed == DAY + timedelta(hours=7) and later.morning["a"] == today.morning["a"]
    assert max(today.morning["a"]) < DAY_END
    # the steps are fixed with the morning values and add up to them
    steps = today.morning_steps["a"]
    assert steps and later.morning_steps is today.morning_steps
    assert all(abs(s["result"] - today.morning["a"][t]) < 0.1 for t, s in steps.items())
    assert all(abs(sum(s[k] for k in explain.STEPS) - s["result"]) < 0.5 for s in steps.values())
    # the next hour keeps the fixed morning; hours that have begun keep their held value
    nxt, today2, _ = run(8, today, {"a": {DAY_START + 7 * 3600: 0.0}})
    assert nxt.morning["a"] == later.morning["a"] and today2.morning_fixed == today.morning_fixed
    assert today2.morning_steps == today.morning_steps
    assert nxt.held["a"][DAY_START + 7 * 3600] == later.held["a"][DAY_START + 7 * 3600]
    assert nxt.expected["a"][DAY_START + 7 * 3600] == 0.0       # measured
    # a new day drops the fix until the morning hour
    _, today3, _ = fc.pv(SITE, ARRAYS, {}, weather(), {}, today2, {}, {}, DAY + timedelta(hours=27),
                         DAY + timedelta(hours=27), DAY_END, DAY_END + 86400)
    assert today3.morning_fixed is None and today3.morning == {} and today3.morning_steps == {}


def test_array_added_after_the_morning_fix_is_fixed_at_its_first_update():
    def at(hour, before, arrays, factor):
        now = DAY + timedelta(hours=hour)
        hours = [model.WeatherHour(w.start, w.ghi * factor, temp=w.temp) for w in weather()]
        return fc.pv(SITE, arrays, {}, hours, {}, before, {}, {}, now, now, DAY_START, DAY_END)

    _, today, _ = at(7, fc.Today(), {"a": ARRAYS["a"]}, 1.0)
    added, today, _ = at(9, today, ARRAYS, 1.0)          # "b" is set up at 9
    assert set(today.morning) == {"a", "b"} and today.morning_steps["b"]
    assert today.morning_late == {"b": DAY + timedelta(hours=9)}
    assert added.fixed_at("a") == DAY + timedelta(hours=7) and added.fixed_at("b") == DAY + timedelta(hours=9)
    assert added.fixed_at(None) == DAY + timedelta(hours=9)
    # new weather at 10 moves the forecast, not the fixed values of the late array
    later, today, _ = at(10, today, ARRAYS, 0.5)
    assert later.morning["b"] == added.morning["b"] != {t: v for t, v in later.forecast["b"].items() if t < DAY_END}
    assert later.morning[None] == added.morning[None]
    # the next day starts without late arrays
    _, nxt, _ = fc.pv(SITE, ARRAYS, {}, weather(), {}, today, {}, {}, DAY + timedelta(hours=31),
                      DAY + timedelta(hours=31), DAY_END, DAY_END + 86400)
    assert nxt.morning_late == {} and nxt.morning_fixed == DAY + timedelta(hours=31)

def test_surplus_band_battery_and_plan():
    result, _, _ = run(9)
    hours = sorted(t for t in result.forecast[None] if t >= DAY_START + 9 * 3600)
    result.load = lf.LoadForecast({t: 100.0 for t in hours}, {}, {}, 1.0)
    band = {"version": 2, "daily": {"all": [-0.3, 0.1, 20]}, "hourly": {"all": [-0.4, 0.1, 200]}}
    battery = bt.Battery(2000, 0.8, 1.0, 0.1, 0.95)
    runs = [pl.RunProfile("w", 500, 60)]
    fc.surplus(result, SITE, band, [battery], runs, DAY_START + 9 * 3600, DAY_END)
    assert set(result.band) == {"hours", "days", "rest"} and result.band["days"][1][0] < result.band["days"][1][1]
    assert result.battery["sim"].full_at and set(result.battery["band"]) == {"lost_today", "full_at", "lost_p90"}
    # the strong case loses at least as much as expected, hour by hour
    p90 = result.battery["band"]["lost_p90"]
    assert all(p90[t] >= h["lost"] - 1e-6 for t, h in result.battery["sim"].hours.items()) and sum(p90.values()) > 0
    assert result.plan["w"]["today"].pv_share == 1.0
    fc.surplus(result, SITE, None, [], [], DAY_START + 9 * 3600, DAY_END)
    assert result.band is None and result.battery is None and result.plan == {}


def test_consumers_are_planned_only_on_pv_that_reaches_the_house():
    # 1000 Wh of PV behind an 800 W cap, 100 Wh load: 200 above the cap only the battery can take
    t = DAY_START + 12 * 3600
    battery = bt.Battery(1000, 1.0, efficiency=1.0, output_w=800, array="a")
    sim = bt.simulate(battery, {t: 1000.0}, {t: 100.0}, t, t + 3600, caps := bt.limits([battery], {"a": {t: 1000.0}}, [t]))
    house, lost = fc.usable({t: 1000.0}, {"sim": sim, "limits": caps}, t)
    assert house == {t: 800.0} and lost == {t: 700.0}          # full battery: the 200 are curtailed, not usable
    # the house draws more than the cap lets through: the battery takes the capped PV, nothing is usable
    battery = bt.Battery(1000, 0.5, efficiency=1.0, output_w=800, array="a")
    sim = bt.simulate(battery, {t: 1000.0}, {t: 1500.0}, t, t + 3600, caps)
    assert fc.usable({t: 1000.0}, {"sim": sim, "limits": caps}, t) == ({t: 800.0}, {t: 0.0})
    assert fc.usable({t: 1000.0}, None, t) == ({t: 1000.0}, {})
    # half of the running hour is left: half of the capped PV, which the battery with room takes completely
    battery = bt.Battery(1000, 0.5, efficiency=1.0, output_w=800, array="a")
    sim = bt.simulate(battery, {t: 1000.0}, {t: 100.0}, t + 1800, t + 3600, caps)
    assert fc.usable({t: 1000.0}, {"sim": sim, "limits": caps}, t + 1800)[1] == {t: sim.hours[t]["lost"]}


def test_load_forecast_counts_running_consumers():
    now = DAY_START + 9 * 3600
    profile = lf.Profile()
    consumers = {"w": ln.ConsumerConfig("w", "Washer", "washer", lf.TIMING_FLEXIBLE)}
    out = fc.load("house", consumers, profile, None, {}, {}, {}, None, {"w": lf.Run(1200.0, 30.0)}, timezone.utc,
                  timezone.utc, now, now + 6 * 3600, DAY_START)
    assert out.consumers["w"][now] == 600.0                      # half an hour of 1200 W
