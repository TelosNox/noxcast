"""Weather model comparison on synthetic data: fair hours, the mean of two models, and no change for chance."""
import copy
import random
from datetime import timezone

from conftest import load

hindcast = load("hindcast")
ln = load("learning")
model = load("model")
solar = load("solar")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(30, 180, 1000)
TRUE = model.LearnedArray(efficiency=0.9)
DAY0 = 1767225600.0                     # 2026-01-01 00:00 UTC
DAYS = 90
ARRAYS = {"roof": ln.ArrayConfig("roof", "Roof", ("pv",), GEO, None)}
TS = [DAY0 + h * 3600 for h in range(DAYS * 24)]
SKY = {t: model.clearsky_weather(SITE, t).ghi for t in TS}
_rng = random.Random(7)
CLOUDS = {t: _rng.uniform(0.15, 1.0) for t in TS}       # the true clearness of each hour


def _hours(factor=lambda t: 1.0, keep=lambda t: True) -> list:
    """A model's forecast: the true weather times a factor (its error), GHI only (split by Erbs)."""
    return [model.WeatherHour(t, SKY[t] * min(1.0, CLOUDS[t] * factor(t)), temp=15.0) for t in TS if keep(t)]


def _history() -> "ln.PvHistory":
    actual = model.forecast(SITE, GEO, TRUE, _hours())
    return ln.PvHistory({"pv": actual}, {}, {}, {}, {}, None, None)


def _run(weather: dict, current: list[str], history=None) -> dict:
    return hindcast.run(SITE, ARRAYS, {"roof": TRUE}, history or _history(), weather, current, 95.0, [],
                        timezone.utc, TS[-1])


def _noise(seed: int, spread: float):
    rng = random.Random(seed)
    values = {t: rng.gauss(1.0, spread) for t in TS}
    return lambda t: max(0.0, values[t])


def test_the_mean_of_two_opposite_models_wins_and_is_recommended():
    # one model too low, one too high: their hourly mean is the truth, as with ICON and ECMWF IFS
    weather = {"low": _hours(lambda t: 0.8), "high": _hours(lambda t: 1.2), "noisy": _hours(_noise(1, 0.4))}
    result = _run(weather, ["noisy"])
    assert result["status"] == "ok" and result["days"] == DAYS
    best = result["combos"][0]
    assert best["models"] == ["low", "high"] and best["day_error"] < 0.05      # clipped at clear sky: not exact
    singles = {c["models"][0]: c for c in result["combos"] if len(c["models"]) == 1}
    assert singles["low"]["bias"] < -0.1 and singles["high"]["bias"] > 0.1
    rec = result["recommendation"]
    assert rec["verdict"] == "change" and rec["models"] == ["low", "high"]
    assert all(c["share"] >= hindcast.RECOMMEND_SHARE for c in rec["checks"]) and rec["gain"] > 0
    assert len(result["combos"]) == 3 + 3 and {"clear", "mixed", "overcast"} == set(best["classes"])
    assert sorted(best["months"]) == ["2026-01", "2026-02", "2026-03"]


def test_the_current_choice_is_kept_when_it_is_best():
    weather = {"low": _hours(lambda t: 0.8), "high": _hours(lambda t: 1.2)}
    rec = _run(weather, ["low", "high"])["recommendation"]
    assert rec["verdict"] == "current_best" and rec["current_rank"] == 1


def test_models_that_differ_by_chance_lead_to_no_change():
    # equally good models: one of the many combinations is always best, but not on unseen days
    weather = {f"m{i}": _hours(_noise(10 + i, 0.3)) for i in range(5)}
    result = _run(weather, ["m0", "m1"])
    assert result["recommendation"]["verdict"] in ("keep", "current_best")


def test_a_model_with_gaps_is_skipped_and_does_not_shrink_the_comparison():
    weather = {"a": _hours(lambda t: 0.9), "b": _hours(lambda t: 1.1),
               "short": _hours(keep=lambda t: t >= DAY0 + 60 * 86400), "none": []}
    result = _run(weather, ["a", "b"])
    skipped = {s["model"]: s for s in result["skipped"]}
    assert skipped["none"]["reason"] == "no_data" and skipped["short"]["reason"] == "coverage"
    assert 0.2 < skipped["short"]["coverage"] < 0.5
    assert result["days"] == DAYS and all("short" not in c["models"] for c in result["combos"])


def test_a_current_choice_with_a_skipped_model_gets_no_recommendation():
    result = _run({"a": _hours(), "b": _hours(lambda t: 1.1), "none": []}, ["a", "none"])
    assert result["status"] == "ok" and result["recommendation"] is None


def test_too_few_days_and_nothing_learned():
    weather = {"a": _hours(keep=lambda t: t < DAY0 + 30 * 86400)}
    result = _run(weather, ["a"])
    assert result["status"] == "not_enough_data" and result["needed"] == hindcast.MIN_DAYS and result["days"] == 30
    assert hindcast.run(SITE, ARRAYS, {}, _history(), weather, ["a"], 95.0, [], timezone.utc, TS[-1])["status"] == "not_learned"


def test_every_combination_is_scored_on_the_same_hours_and_inputs_stay_unchanged():
    weather = {"a": _hours(lambda t: 0.9), "b": _hours(_noise(3, 0.2)), "c": _hours(lambda t: 1.05)}
    history = _history()
    before = (copy.deepcopy(weather), copy.deepcopy(history.power))
    result = _run(weather, ["a", "b", "c"], history)
    assert {c["hours"] for c in result["combos"]} == {result["hours"]}
    assert ["a", "b", "c"] in [c["models"] for c in result["combos"]]      # the current choice of three as well
    assert (weather, history.power) == before


def test_excluded_and_full_battery_hours_do_not_count():
    weather = {"a": _hours(), "b": _hours(lambda t: 1.1)}
    history = _history()
    full = ln.PvHistory(history.power, {}, {}, {}, {t: 100.0 for t in TS if t < DAY0 + 10 * 86400}, None, None)
    result = hindcast.run(SITE, ARRAYS, {"roof": TRUE}, full, weather, ["a"], 95.0,
                          [(DAY0 + 20 * 86400, DAY0 + 25 * 86400)], timezone.utc, TS[-1])
    assert result["days"] == DAYS - 10 - 5


def test_a_stored_result_of_another_version_is_dropped():
    assert hindcast.valid({"version": hindcast.VERSION, "status": "ok"})
    assert hindcast.valid({"version": hindcast.VERSION + 1}) is None and hindcast.valid(None) is None
