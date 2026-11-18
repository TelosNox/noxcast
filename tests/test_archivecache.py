"""Local copy of the weather archives: what is fetched again and what is kept."""
from custom_components.noxcast import archivecache as ac

DAY = 86400
T0 = 1_780_000_000 - 1_780_000_000 % DAY          # a UTC midnight
KEY = ["https://archive.example/v1", 0.0, 0.0]


def _hours(a: float, b: float, value=lambda t: 10.0) -> dict:
    return {t: value(t) for t in range(int(a), int(b), 3600)}


def test_without_a_copy_the_whole_history_is_fetched():
    assert ac.fetch_from(None, KEY, T0) == T0
    assert ac.fetch_from({}, KEY, T0) == T0


def test_only_the_last_days_are_fetched_again():
    entry = ac.merged(None, KEY, {"temperature": _hours(T0, T0 + 100 * DAY)}, T0, T0)
    since = ac.fetch_from(entry, KEY, T0 + DAY)
    assert since == T0 + (100 - 1 - ac.REFRESH_DAYS) * DAY       # midnight before newest value minus the window
    assert ac.fetch_from(entry, KEY, T0 + 95 * DAY) == T0 + 95 * DAY


def test_another_source_fetches_everything_again():
    entry = ac.merged(None, KEY, {"temperature": _hours(T0, T0 + 30 * DAY)}, T0, T0)
    other = ["https://archive.example/v1", 0.0, 1.0]
    assert ac.fetch_from(entry, other, T0) == T0
    fresh = ac.merged(entry, other, {"temperature": _hours(T0 + 20 * DAY, T0 + 30 * DAY)}, T0 + 20 * DAY, T0)
    assert min(ac.series(fresh, "temperature")) == T0 + 20 * DAY            # nothing of the other source kept


def test_a_copy_beginning_after_the_window_is_fetched_whole():
    entry = ac.merged(None, KEY, {"temperature": _hours(T0 + 10 * DAY, T0 + 30 * DAY)}, T0 + 10 * DAY, T0 + 10 * DAY)
    assert ac.fetch_from(entry, KEY, T0) == T0


def test_fetched_hours_replace_the_preliminary_ones_and_old_hours_drop_out():
    entry = ac.merged(None, KEY, {"temperature": _hours(T0, T0 + 30 * DAY), "snowfall": {T0 + 5 * DAY: 1.0}}, T0, T0)
    since = ac.fetch_from(entry, KEY, T0 + 2 * DAY)
    entry = ac.merged(entry, KEY, {"temperature": _hours(since, T0 + 31 * DAY, lambda t: 20.0), "snowfall": {}},
                      since, T0 + 2 * DAY)
    temp = ac.series(entry, "temperature")
    assert min(temp) == T0 + 2 * DAY and max(temp) == T0 + 31 * DAY - 3600
    assert temp[since - 3600] == 10.0 and temp[since] == 20.0
    assert ac.series(entry, "snowfall") == {T0 + 5 * DAY: 1.0}             # before since: kept


def test_gaps_survive_the_packing():
    values = {T0: 1.0, T0 + 5 * 3600: 2.5}
    entry = ac.merged(None, KEY, {"ghi": values}, T0, T0)
    assert ac.series(entry, "ghi") == values
    assert ac.series(entry, "missing") == {} and ac.series(None, "ghi") == {}
