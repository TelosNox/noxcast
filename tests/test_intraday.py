"""Intraday correction on clearness: fewer clouds than forecast lift cloudy hours, not sunny ones."""
from conftest import load

intraday = load("intraday")

H = 3600.0
CLEAR = {h * H: 800.0 for h in range(8, 18)}
CLOUDY = {h * H: 400.0 for h in range(8, 18)}          # clearness 0.5


def test_shift_is_energy_weighted_clearness_difference():
    actual = {9 * H: 400.0, 10 * H: 800.0}                # last hour clear instead of cloudy
    shift = intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000)
    assert abs(shift - (400 + 0.6 * 0) / (800 * 1.6)) < 1e-9


def test_no_shift_without_light_or_hours():
    assert intraday.clearness_shift(CLOUDY, {}, CLEAR, now=11 * H, peak_w=1000) is None
    dark = {h * H: 1.0 for h in range(5)}
    assert intraday.clearness_shift(dark, dark, dark, now=5 * H, peak_w=1000) is None


def test_sunny_forecast_is_not_lifted_above_clear_sky():
    forecast = {**CLOUDY, **{h * H: 800.0 for h in range(11, 18)}}     # afternoon already forecast sunny
    out = intraday.expected_hours(forecast, {}, CLEAR, now=11 * H, shift=0.5)
    assert out[11 * H] <= 800 * intraday.CLEARNESS_MAX + 1e-9


def test_cloudy_hours_get_fewer_clouds_with_decay():
    out = intraday.expected_hours(CLOUDY, {8 * H: 700.0}, CLEAR, now=10 * H, shift=0.4)
    assert out[8 * H] == 700.0 and out[9 * H] == 400.0                # past: actual, else forecast
    assert abs(out[10 * H] - 800 * (0.5 + 0.4 * 0.5)) < 1e-9         # current hour: half the shift
    assert abs(out[11 * H] - 800 * (0.5 + 0.4 * 0.25)) < 1e-9        # one hour ahead: a quarter
    assert out[17 * H] - 400 < 2


def test_negative_shift_never_below_zero():
    out = intraday.expected_hours(CLOUDY, {}, CLEAR, now=10 * H, shift=-1.1)
    assert all(v >= 0 for v in out.values())
