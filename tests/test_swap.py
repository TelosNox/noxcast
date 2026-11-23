"""System swap: frozen hours of a replaced device keep what learning saw (swap.py, docs/concept.md System swap)."""
import json

from conftest import load

ln = load("learning")
model = load("model")
solar = load("solar")
sw = load("swap")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(30, 180, 1000)
DAY0 = 1780272000.0                     # 2026-06-01 00:00 UTC
DAYS = 30
NOW = DAY0 + DAYS * 86400
SWAP = DAY0 + 20 * 86400                # the old device's last hour ends here, the new one counts from a day later
RESUME = SWAP + 86400


def clear(t: float) -> float:
    beam, diff, *_ = model.clearsky_hour(SITE, GEO, t)
    return GEO.peak_w * 0.92 * (beam + diff) / 1000


HOURS = [DAY0 + h * 3600 for h in range(DAYS * 24)]
FULL = frozenset({DAY0 + 2 * 86400 + 11 * 3600, RESUME + 86400 + 12 * 3600})
OLD = ln.ArrayConfig("roof", "Roof", ("old",), GEO, None)


def before() -> "ln.PvHistory":
    """What learning saw before the swap: the old sensor over all days, the new one not yet there."""
    power = {"old": {t: clear(t) for t in HOURS}}
    return ln.PvHistory(power, power, power, {}, full={"roof": FULL})


def after() -> "ln.PvHistory":
    """After it: the old sensor's statistics are gone, the new one counts in kW from the day after the swap."""
    power = {"new": {t: clear(t) / 1000 for t in HOURS if t >= RESUME}}
    return ln.PvHistory(power, power, power, {"new": 1000.0}, full={"roof": frozenset(t for t in FULL if t >= RESUME)})


def swapped() -> tuple["ln.ArrayConfig", "ln.PvHistory"]:
    segment = ln.Segment(SWAP, RESUME, ("old",))
    frozen = json.loads(json.dumps(sw.freeze(SITE, "roof", before(), segment)))      # as the store keeps it
    arr = ln.ArrayConfig("roof", "Roof", ("new",), GEO, None, segments=sw.segments([frozen]))
    return arr, sw.merge(after(), {"roof": [frozen]})


def test_learning_across_a_swap_is_learning_without_one():
    arr, history = swapped()
    # the same hours without a swap, the swap day left out
    reference = ln.learn_array(SITE, OLD, before(), NOW, [(SWAP, RESUME)])
    learned = ln.learn_array(SITE, arr, history, NOW, [])
    assert learned.samples == reference.samples
    assert abs(learned.efficiency - reference.efficiency) < 1e-6
    assert learned.cells.keys() == reference.cells.keys()


def test_frozen_hours_keep_the_filters_and_only_daylight():
    arr, history = swapped()
    assert history.full["roof"] == FULL                      # the frozen hour before, the live one after
    power = ln.array_power(arr, history.power, history.factors)
    assert not [t for t in power if SWAP <= t < RESUME]      # the swap day belongs to no one
    assert all(solar.sun_position(SITE, t + 1800).elevation > -10 for t in power if t < SWAP)
    assert abs(power[RESUME + 12 * 3600] - clear(RESUME + 12 * 3600)) < 1e-6


def test_each_segment_has_its_own_limits():
    limited = ln.Segment(SWAP, RESUME, ("old",), limits=(("old", 300.0),))
    frozen = sw.freeze(SITE, "roof", before(), limited)
    arr = ln.ArrayConfig("roof", "Roof", ("new",), GEO, None, segments=sw.segments([frozen]))
    floors = ln.floor_hours(arr, sw.merge(after(), {"roof": [frozen]}))
    assert floors and max(floors) < SWAP                     # the new device has no limit


def test_a_sensor_that_stays_runs_on_live_after_the_swap():
    # a battery swap: the PV sensor stays, its frozen copy holds before, the live statistics after
    live = before()
    frozen = sw.freeze(SITE, "roof", live, ln.Segment(SWAP, RESUME, ("old",), device=False))
    raised = {t: v * 2 for t, v in live.power["old"].items()}
    merged = sw.merge(ln.PvHistory({"old": raised}, {}, {}, {}), {"roof": [frozen]})
    assert merged.power["old"][DAY0 + 12 * 3600] == round(clear(DAY0 + 12 * 3600), 1)
    assert merged.power["old"][RESUME + 12 * 3600] == raised[RESUME + 12 * 3600]
    assert sw.segments([frozen])[0].device is False


def test_prune_keeps_the_learning_window():
    frozen = sw.freeze(SITE, "roof", before(), ln.Segment(SWAP, RESUME, ("old",)))
    store = {"roof": [frozen]}
    sw.prune(store, DAY0 + 10 * 86400)
    days = store["roof"][0]["power"]["old"]["mean"]
    assert min(days) == "2026-06-11" and all(t >= DAY0 + 10 * 86400 for t in store["roof"][0]["hours"]["full"])
    assert sw.sensors(store) == {"old"}
    sw.prune(store, SWAP)
    assert store == {"roof": []}


def test_without_a_swap_nothing_changes():
    history = before()
    assert sw.merge(history, {}) is history and sw.merge(history, {"roof": []}) is history
    assert ln.array_power(OLD, history.power, history.factors) == ln.scaled_sum(history.power, ("old",), {})


def sky() -> dict[float, float]:
    """Satellite irradiance of a clear sky, so that every hour counts as clear."""
    return {t: model.clearsky_hour(SITE, GEO, t)[4] for t in HOURS}


def better(step: float) -> tuple["ln.ArrayConfig", "ln.PvHistory"]:
    """A swap to a device that converts step times as much of the same light."""
    segment = ln.Segment(SWAP, RESUME, ("old",))
    frozen = sw.freeze(SITE, "roof", before(), segment)
    power = {"new": {t: clear(t) * step for t in HOURS if t >= RESUME}}
    live = ln.PvHistory(power, power, power, {}, satellite_ghi=sky())
    arr = ln.ArrayConfig("roof", "Roof", ("new",), GEO, None, segments=sw.segments([frozen]))
    return arr, sw.merge(live, {"roof": [frozen]})


def test_a_better_device_is_a_ratio_not_less_shade():
    arr, history = better(1.05)
    learned = ln.learn_array(SITE, arr, history, NOW, [])
    old = before()
    reference = ln.learn_array(SITE, OLD, ln.PvHistory(old.power, old.power, old.power, {}, satellite_ghi=sky()),
                               NOW, [(SWAP, RESUME)])
    [ratio] = learned.segment_ratios
    assert ratio["learned"] and ratio["until"] == SWAP and abs(ratio["ratio"] - 1.05) < 0.005
    assert abs(learned.efficiency - reference.efficiency * 1.05) < 0.005       # the newest device's
    assert learned.cells.keys() == reference.cells.keys()
    assert max(abs(learned.cells[k] - reference.cells[k]) for k in learned.cells) < 0.01
    # the model of an old hour is the old device's
    assert abs(ln.device_factor(arr, learned.segment_ratios, DAY0) - 1 / ratio["ratio"]) < 1e-9
    assert ln.device_factor(arr, learned.segment_ratios, RESUME) == 1.0


def test_the_ratio_waits_for_enough_hours_of_the_new_device():
    arr, history = better(1.05)
    few = {t: v for t, v in history.power["new"].items() if t < RESUME + 86400}       # one day of the new device
    history = ln.PvHistory({**history.power, "new": few}, history.power_min, history.power_max, {},
                           satellite_ghi=history.satellite_ghi)
    [ratio] = ln.learn_array(SITE, arr, history, NOW, []).segment_ratios
    assert ratio["ratio"] == 1.0 and not ratio["learned"] and ratio["hours"][1] < model.MIN_UNSHADED_HOURS


def test_a_battery_swap_keeps_one_efficiency():
    segment = ln.Segment(SWAP, RESUME, ("old",), device=False)
    arr = ln.ArrayConfig("roof", "Roof", ("old",), GEO, None, segments=(segment,))
    assert ln.devices(arr) == [(None, None)]
    assert ln.learn_array(SITE, arr, before(), NOW, []).segment_ratios == []
