"""5-minute archive: periods with the sun in front of the modules, per sensor, bounded to FINE_DAYS."""
from datetime import date

from conftest import load

fn = load("fine")

DAY = 1_790_000_000 - 1_790_000_000 % 86400          # some UTC midnight


def useful(ts: float) -> bool:                         # sun in front of the modules 08:00-16:00
    return DAY + 8 * 3600 <= ts < DAY + 16 * 3600


def test_day_steps_keep_only_useful_periods_in_whole_watts():
    five = {DAY + k * 300: 100.4 + k for k in range(288)}
    del five[DAY + 9 * 3600]                            # a missing period
    steps = fn.day_steps(five, DAY, DAY + 86400, useful)
    assert steps["t0"] == DAY + 8 * 3600 and len(steps["w"]) == 96
    assert steps["w"][0] == round(100.4 + 96)
    assert steps["w"][12] is None
    assert fn.series({"d": {"s": steps}}, "s")[DAY + 8 * 3600] == steps["w"][0]


def test_day_without_data_or_sun():
    assert fn.day_steps({}, DAY, DAY + 86400, useful) is None
    assert fn.day_steps({DAY: 5.0}, DAY, DAY + 86400, lambda ts: False) is None


def test_store_stays_bounded_and_missing_days_are_fetched_once():
    store = {}
    today = date(2027, 6, 1)
    assert len(fn.missing_days(store, {"a"}, today)) == fn.STATS_DAYS
    for d in fn.missing_days(store, {"a"}, today):
        fn.add_day(store, d, "a", None)                  # no data: marked, not fetched again
    assert fn.missing_days(store, {"a"}, today) == []
    assert fn.missing_days(store, {"a", "b"}, today)     # a new sensor fills its days too
    store["2020-01-01"] = {"a": {"t0": 0, "w": [1]}, "gone": {"t0": 0, "w": [1]}}
    store["2027-05-30"]["gone"] = {"t0": 0, "w": [1]}
    fn.prune(store, date(2024, 6, 1), {"a"})
    assert "2020-01-01" not in store and all("gone" not in d for d in store.values())
