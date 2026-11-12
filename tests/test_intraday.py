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


def test_running_hour_counts_with_its_measured_share():
    # forecast cloudy, the running hour is clear for its first half: the shift appears before the hour ends
    partial = intraday.Partial(start=10 * H, power=800.0, share=0.5, profile=1.0)
    shift = intraday.clearness_shift(CLOUDY, {}, CLEAR, now=10.5 * H, peak_w=1000, partial=partial)
    assert abs(shift - 0.5) < 1e-9
    # with a completed hour as forecast, the half hour weighs less than a full hour
    shift = intraday.clearness_shift(CLOUDY, {9 * H: 400.0}, CLEAR, now=10.5 * H, peak_w=1000, partial=partial)
    assert abs(shift - (0.5 * 400) / (0.5 * 800 + 0.6 * 800)) < 1e-9


def test_partial_is_scaled_by_the_clear_sky_profile():
    # early in a morning hour the clear sky is only 60 % of the hour mean: 240 W measured is the forecast
    partial = intraday.Partial(start=10 * H, power=240.0, share=0.25, profile=0.6)
    shift = intraday.clearness_shift(CLOUDY, {}, CLEAR, now=10.25 * H, peak_w=1000, partial=partial)
    assert abs(shift) < 1e-9


def test_curtailed_hour_below_forecast_is_left_out_of_the_window():
    # 10:00 was cut by a full battery: it does not count as clouds, and 9:00 keeps its weight of an older hour
    actual = {9 * H: 600.0, 10 * H: 100.0}
    shift = intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000, curtailed={10 * H})
    assert abs(shift - 0.6 * 200 / (0.6 * 800)) < 1e-9
    # the window does not reach further back for it
    actual = {8 * H: 800.0, 9 * H: 600.0, 10 * H: 100.0, 11 * H: 100.0}
    shift = intraday.clearness_shift(CLOUDY, actual, CLEAR, now=12 * H, peak_w=1000, curtailed={10 * H, 11 * H})
    assert abs(shift - 200 / 800) < 1e-9


def test_all_hours_curtailed_below_forecast_give_no_correction():
    actual = {8 * H: 100.0, 9 * H: 100.0, 10 * H: 100.0}
    curtailed = set(actual)
    assert intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000, curtailed=curtailed) is None


def test_curtailed_hour_may_raise_but_never_lower_the_shift():
    # cut, yet still clearer than forecast: the sky was at least that clear
    actual = {9 * H: 400.0, 10 * H: 700.0}
    shift = intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000, curtailed={10 * H})
    assert shift == intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000)
    assert shift > 0
    # a curtailed hour above the forecast but below the others does not pull a larger shift down
    actual = {9 * H: 800.0, 10 * H: 450.0}
    free = intraday.clearness_shift(CLOUDY, {9 * H: 800.0}, CLEAR, now=11 * H, peak_w=1000)
    shift = intraday.clearness_shift(CLOUDY, actual, CLEAR, now=11 * H, peak_w=1000, curtailed={10 * H})
    assert abs(shift - free) < 1e-9


def test_running_hour_with_a_full_battery_is_left_out():
    partial = intraday.Partial(start=10 * H, power=100.0, share=0.5, profile=1.0)
    shift = intraday.clearness_shift(CLOUDY, {9 * H: 400.0}, CLEAR, now=10.5 * H, peak_w=1000, partial=partial,
                                     curtailed={10 * H})
    assert abs(shift) < 1e-9


def test_held_forecast_keeps_hours_that_have_begun():
    day = 1_000_000.0
    first = intraday.held_forecast({}, {day + 3600 * h: 100.0 for h in range(8, 12)}, day + 3600 * 7, day)
    later = intraday.held_forecast(first, {day + 3600 * h: 60.0 for h in range(10, 12)}, day + 3600 * 9.5, day)
    assert later[day + 3600 * 9] == 100.0            # running hour keeps its value
    assert later[day + 3600 * 10] == 60.0            # hours ahead follow the latest forecast
    assert intraday.held_forecast(later, {}, day + 86400 + 3600, day + 86400) == {}   # a new day starts empty
