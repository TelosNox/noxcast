"""Intraday correction: ratio of the last hours, decay into the coming hours."""
from conftest import load

intraday = load("intraday")

H = 3600.0
FORECAST = {h * H: 500.0 for h in range(8, 18)}


def test_ratio_weights_recent_hours():
    actual = {8 * H: 500.0, 9 * H: 500.0, 10 * H: 1000.0}
    r = intraday.correction_ratio(FORECAST, actual, now=11 * H + 600, peak_w=1000)
    assert abs(r - (1000 + 0.6 * 500 + 0.36 * 500) / (500 * 1.96)) < 1e-9


def test_no_ratio_without_energy_or_hours():
    assert intraday.correction_ratio(FORECAST, {}, now=11 * H, peak_w=1000) is None
    night = {h * H: 1.0 for h in range(0, 5)}
    assert intraday.correction_ratio(night, night, now=5 * H, peak_w=1000) is None


def test_ratio_is_limited():
    actual = {10 * H: 5000.0}
    assert intraday.correction_ratio(FORECAST, actual, now=11 * H, peak_w=1000) == intraday.RATIO_RANGE[1]


def test_expected_uses_actual_then_decaying_correction():
    actual = {8 * H: 400.0, 9 * H: 700.0}
    now = 10 * H
    out = intraday.expected_hours(FORECAST, actual, now, ratio=2.0)
    assert out[8 * H] == 400.0 and out[9 * H] == 700.0
    assert out[10 * H] == 1000.0                                    # current hour: full correction
    assert abs(out[12 * H] - 500 * 1.5) < 1e-9                      # two hours ahead: half
    assert out[17 * H] < 600                                        # far ahead: almost the forecast
    assert intraday.expected_hours(FORECAST, {}, now, None)[11 * H] == 500.0
