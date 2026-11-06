"""The snapshot comparison itself: what it tolerates and what it must catch."""
import pytest

import snapshot

T = 1781190000.0                  # some hour start, UTC seconds


def test_platform_noise_passes_but_real_changes_show():
    assert snapshot.differences(snapshot.plain(0.1 + 0.2), snapshot.plain(0.3)) == []
    assert snapshot.differences(snapshot.plain(310.06), snapshot.plain(310.1)) != []          # a payload rounding
    assert snapshot.differences(snapshot.plain(T), snapshot.plain(T + 3600)) != []             # an hour off
    assert snapshot.plain(T + 0.5) == T + 0.5                                                  # not rounded


def test_types_and_key_order_count():
    assert snapshot.differences(1, 1.0) and snapshot.differences(True, 1)
    assert snapshot.differences({"a": 1, "b": 2}, {"b": 2, "a": 1}) == [".: key order changed"]


def test_unchanged_parts_are_marked_but_order_and_changes_stay_visible():
    a, b = {"date": "d1", "v": 1}, {"date": "d2", "v": 2}
    first, second = snapshot.steps([{"days": [a, b], "x": 1}, {"days": [a, b], "x": 2}])
    assert second == {"days": snapshot.SAME, "x": 2}
    _, swapped = snapshot.steps([{"days": [a, b]}, {"days": [b, a]}])
    _, kept = snapshot.steps([{"days": [a, b, {"date": "d3"}]}, {"days": [a, b, {"date": "d4"}]}])
    assert swapped["days"] == ["= previous d2", "= previous d1"] != ["= previous d1", "= previous d2"]
    assert kept["days"][:2] == ["= previous d1", "= previous d2"] and kept["days"][2] == {"date": "d4"}
    _, reordered = snapshot.steps([{"k": {"a": 1, "b": 2}}, {"k": {"b": 2, "a": 1}}])
    assert reordered != {"k": snapshot.SAME}


def test_a_missing_file_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "DIR", tmp_path)
    with pytest.raises(AssertionError, match="missing"):
        snapshot.check("x", {"a": 1.5}, update=False)
    snapshot.check("x", {"a": 1.5}, update=True)
    snapshot.check("x", {"a": 1.5}, update=False)
    with pytest.raises(AssertionError, match=r"\.a: 1\.5 -> 2\.5"):
        snapshot.check("x", {"a": 2.5}, update=False)
