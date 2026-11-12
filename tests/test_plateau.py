"""Power limits seen in the measurements: flat at the maximum while the sun would have changed it (#248, E2)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.noxcast import plateau as pt
from custom_components.noxcast import solar
from custom_components.noxcast.model import ArrayGeometry, array_irradiance

UTC = timezone.utc


def day_of(t: float) -> date:
    return datetime.fromtimestamp(t, UTC).date()


def clear_minutes(site: solar.Site, geo: ArrayGeometry, start: float, minutes: int) -> list[float]:
    """Clear-sky power (W) per minute, with a fixed efficiency."""
    out = []
    for m in range(minutes):
        t = start + 60 * m + 30
        sun = solar.sun_position(site, t)
        if sun.elevation <= 0:
            out.append(0.0)
            continue
        dni_extra = solar.extra_radiation(t)
        ghi, dni, dhi = solar.clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
        beam, diffuse, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra)
        out.append(geo.peak_w * 0.9 * (beam + diffuse) / 1000)
    return out


def statistics(site, geo, first: date, days: int, limit=None, dropout_hours=(), drift=None):
    """Hourly mean, min and max (W) and 5-minute means of clear days; limit: W, or a function of the day index."""
    mean, low, high, fine = {}, {}, {}, {}
    for d in range(days):
        start = datetime(first.year, first.month, first.day, tzinfo=UTC).timestamp() + d * 86400
        minutes = clear_minutes(site, geo, start, 1440)
        cap = limit(d) if callable(limit) else limit
        if cap is not None:
            minutes = [min(v, cap) for v in minutes]
        for h in range(24):
            values = minutes[60 * h:60 * h + 60]
            if max(values) <= 0:
                continue
            t = start + 3600 * h
            mean[t], low[t], high[t] = sum(values) / 60, min(values), max(values)
            if h in dropout_hours:                 # a few seconds at 0 W: the minimum says nothing
                low[t], mean[t] = 0.0, mean[t] * 0.999
        for i in range(288):
            fine[start + 300 * i] = sum(minutes[5 * i:5 * i + 5]) / 5
    return mean, low, high, fine


SITE = solar.Site(48.0, 7.5, 200.0)      # solar noon near 11:30 UTC: the clock hour is centred on it
SOUTH = ArrayGeometry(30, 180, 1000)


@pytest.mark.parametrize("geo", [ArrayGeometry(0, 180, 1000), ArrayGeometry(10, 90, 1000, extra=()),
                                 SOUTH, ArrayGeometry(90, 180, 1000)])
@pytest.mark.parametrize("lon", [7.5, 15.0])
def test_clear_unlimited_days_never_count_even_at_the_solstice(geo, lon):
    # no clouds, so no cloud enhancement above the clear sky either: the hardest case for the detector. An hour
    # centred on solar noon is flat within 1 %, but the sun hardly changes the output then, so it is no evidence
    site = solar.Site(48.0, lon, 200.0)
    mean, low, high, fine = statistics(site, geo, date(2026, 6, 1), 40)
    now = datetime(2026, 7, 11, tzinfo=UTC).timestamp()
    assert pt.detect(mean, low, high, now, day_of, pt.clear_change(site, geo), fine) is None


def test_east_west_on_one_sensor_is_no_limit():
    from custom_components.noxcast.model import Plane
    geo = ArrayGeometry(15, 90, 1000, extra=(Plane(15, 270, 500),))
    mean, low, high, fine = statistics(SITE, geo, date(2026, 6, 1), 40)
    now = datetime(2026, 7, 11, tzinfo=UTC).timestamp()
    assert pt.detect(mean, low, high, now, day_of, pt.clear_change(SITE, geo), fine) is None


@pytest.mark.parametrize("share", [0.5, 0.7, 0.9])
def test_a_limit_is_found_after_three_clear_days(share):
    peak = max(clear_minutes(SITE, SOUTH, datetime(2026, 4, 1, tzinfo=UTC).timestamp(), 1440))
    limit = share * peak
    mean, low, high, fine = statistics(SITE, SOUTH, date(2026, 4, 1), 5, limit)
    change = pt.clear_change(SITE, SOUTH)
    day = lambda n: datetime(2026, 4, 1, tzinfo=UTC).timestamp() + n * 86400  # noqa: E731
    assert pt.detect(mean, low, high, day(2), day_of, change) is None          # two days are not enough
    found = pt.detect(mean, low, high, day(3), day_of, change)
    assert found is not None and found.flat_days == 3 and found.days == 3
    assert found.peak_w == pytest.approx(limit) and abs(found.limit_w / limit - 1) <= 0.01


def test_a_limit_that_drifts_with_the_temperature_is_found():
    # a current limit: the level differs by +-4 % from day to day; the suggestion is the highest level
    peak = max(clear_minutes(SITE, SOUTH, datetime(2026, 5, 1, tzinfo=UTC).timestamp(), 1440))
    levels = [0.6 * peak * (1 + 0.04 * x) for x in (-1, 1, -0.5, 0.5, 0)]
    mean, low, high, _ = statistics(SITE, SOUTH, date(2026, 5, 1), 5, lambda d: levels[d])
    found = pt.detect(mean, low, high, datetime(2026, 5, 4, tzinfo=UTC).timestamp(), day_of, pt.clear_change(SITE, SOUTH))
    assert found is not None and abs(found.limit_w / max(levels[:3]) - 1) <= 0.01


def test_dropouts_of_seconds_do_not_hide_a_limit():
    peak = max(clear_minutes(SITE, SOUTH, datetime(2026, 4, 1, tzinfo=UTC).timestamp(), 1440))
    mean, low, high, _ = statistics(SITE, SOUTH, date(2026, 4, 1), 3, 0.6 * peak, dropout_hours=range(24))
    found = pt.detect(mean, low, high, datetime(2026, 4, 4, tzinfo=UTC).timestamp(), day_of, pt.clear_change(SITE, SOUTH))
    assert found is not None


def test_a_short_plateau_counts_from_the_five_minute_archive():
    # limited just below the peak: about an hour at the limit, never a whole clock hour
    start = datetime(2026, 4, 1, tzinfo=UTC).timestamp()
    peak = max(clear_minutes(SITE, ArrayGeometry(60, 135, 1000), start, 1440))
    geo = ArrayGeometry(60, 135, 1000)
    mean, low, high, fine = statistics(SITE, geo, date(2026, 4, 1), 3, 0.97 * peak)
    change = pt.clear_change(SITE, geo)
    now = start + 3 * 86400
    assert pt.detect(mean, low, high, now, day_of, change) is None
    assert pt.detect(mean, low, high, now, day_of, change, fine) is not None


def test_a_stuck_sensor_or_a_day_above_the_level_is_no_limit():
    peak = max(clear_minutes(SITE, SOUTH, datetime(2026, 4, 1, tzinfo=UTC).timestamp(), 1440))
    mean, low, high, _ = statistics(SITE, SOUTH, date(2026, 4, 1), 4, 0.6 * peak)
    change = pt.clear_change(SITE, SOUTH)
    now = datetime(2026, 4, 5, tzinfo=UTC).timestamp()
    stuck = {t: 300.0 for t in high}
    assert pt.detect(stuck, stuck, stuck, now, day_of, change) is None
    # one hour of cloud enhancement above the plateau: the sensor is not limited there
    t = max(high)
    high = {**high, t: 0.8 * peak}
    assert pt.detect(mean, low, high, now, day_of, change) is None


def test_old_days_leave_the_window():
    peak = max(clear_minutes(SITE, SOUTH, datetime(2026, 4, 1, tzinfo=UTC).timestamp(), 1440))
    mean, low, high, _ = statistics(SITE, SOUTH, date(2026, 4, 1), 3, 0.6 * peak)
    change = pt.clear_change(SITE, SOUTH)
    later = datetime(2026, 4, 1, tzinfo=UTC) + timedelta(days=pt.WINDOW_DAYS + 2)
    assert pt.detect(mean, low, high, later.timestamp(), day_of, change) is None


def test_suggested_limits_are_round_and_close():
    assert pt.suggested_limit(501.3) == 500
    assert pt.suggested_limit(812.0) == 810
    assert pt.suggested_limit(224.4) == 225
    assert pt.suggested_limit(223.6) == 224                 # 225 would be 0.6 % off
    assert pt.suggested_limit(37.2) == 37


def test_suggestion_for_an_array_needs_every_sensor_at_one_level():
    a = pt.Plateau(501.0, 14, 60)
    b = pt.Plateau(498.0, 9, 58)
    assert pt.suggestion(["s1", "s2"], {"s1": a, "s2": b}) == {
        "peak_w": 501.0, "limit_w": 500.0, "flat_days": 9, "days": 60, "window_days": 60}
    assert pt.suggestion(["s1", "s2"], {"s1": a}) is None                       # one sensor alone caps nothing
    assert pt.suggestion(["s1", "s2"], {"s1": a, "s2": pt.Plateau(400.0, 9, 58)}) is None
    assert pt.suggestion(["s1"], {"s1": a.as_dict()})["limit_w"] == 500.0      # as stored


def test_there_is_no_limit_holds_until_the_level_moves():
    suggested = {"peak_w": 501.0}
    assert pt.dismissed(["s1"], suggested, {"s1": 495.0})
    assert not pt.dismissed(["s1"], {"peak_w": 540.0}, {"s1": 495.0})
    assert not pt.dismissed(["s1", "s2"], suggested, {"s1": 501.0})
    assert not pt.dismissed([], suggested, {})
