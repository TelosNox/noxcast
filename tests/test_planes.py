"""Several orientations (planes) in one array: per-plane physics against the equivalent plane."""
import math

import pytest
from conftest import load

solar = load("solar")
model = load("model")
arraycheck = load("arraycheck")
shade = load("shade")

SITE = solar.Site(48.97, 8.45, 113)
DNI_EXTRA = 1367.0


def _sun(elevation: float, azimuth: float) -> solar.SunPosition:
    return solar.SunPosition(azimuth, elevation, 90.0 - elevation)


def _beam(geo: model.ArrayGeometry, elevation: float, azimuth: float) -> float:
    """Beam only (no diffuse), W/m² of the array's peak power."""
    return model.array_irradiance(geo, _sun(elevation, azimuth), 1000.0, 0.0, 0.0, DNI_EXTRA)[0]


def _equal_planes(*orientations: tuple[float, float], peak: float = 400.0) -> model.ArrayGeometry:
    (tilt, azimuth), *rest = orientations
    extra = tuple(model.Plane(t, a, peak) for t, a in rest)
    return model.ArrayGeometry(tilt, azimuth, peak * len(orientations), extra=extra)


def _equivalent(geo: model.ArrayGeometry) -> tuple[float, float, float]:
    """Equivalent plane: peak-weighted sum of the module normals -> (tilt, azimuth, |sum|)."""
    n = [0.0, 0.0, 0.0]
    for tilt, azimuth, share in geo.planes:
        t, a = math.radians(tilt), math.radians(azimuth)
        n[0] += share * math.sin(t) * math.sin(a)
        n[1] += share * math.sin(t) * math.cos(a)
        n[2] += share * math.cos(t)
    length = math.sqrt(sum(x * x for x in n))
    return math.degrees(math.acos(n[2] / length)), math.degrees(math.atan2(n[0], n[1])) % 360, length


@pytest.fixture
def no_iam(monkeypatch):
    """Pure projection (cos of the incidence angle), as in the reference numbers of docs/concept.md."""
    monkeypatch.setattr(solar, "iam_martin_ruiz", lambda aoi: 1.0 if aoi < 90 else 0.0)


def test_planes_and_shares():
    geo = model.ArrayGeometry(30, 180, 1200, extra=(model.Plane(30, 90, 400), model.Plane(30, 270, 200)))
    assert geo.planes == ((30, 180, 0.5), (30, 90, 1 / 3), (30, 270, 1 / 6))
    assert [p["peak_w"] for p in geo.describe()] == [600, 400, 200]
    single = model.ArrayGeometry(30, 180, 1000)
    assert single.planes == ((30, 180, 1.0),)
    assert single.describe() == [{"tilt": 30, "azimuth": 180, "peak_w": 1000}]


@pytest.mark.usefixtures("no_iam")
@pytest.mark.parametrize("elevation, azimuth, ratio", [
    (10, 90, 1.76), (10, 135, 1.25), (20, 90, 1.20), (30, 90, 1.00), (40, 135, 1.00), (60, 180, 1.00),
])
def test_east_south_west_against_equivalent_plane(elevation, azimuth, ratio):
    geo = _equal_planes((30, 180), (30, 90), (30, 270))
    tilt, facing, factor = _equivalent(geo)
    assert tilt == pytest.approx(10.9, abs=0.05) and facing == pytest.approx(180, abs=1e-6)
    assert factor == pytest.approx(0.882, abs=0.0005)
    equivalent = factor * _beam(model.ArrayGeometry(tilt, facing, 1200), elevation, azimuth)
    assert _beam(geo, elevation, azimuth) / equivalent == pytest.approx(ratio, abs=0.005)


@pytest.mark.usefixtures("no_iam")
def test_east_west_against_flat_equivalent():
    geo = _equal_planes((30, 90), (30, 270))
    tilt, _, factor = _equivalent(geo)
    assert tilt == pytest.approx(0.0, abs=1e-6) and factor == pytest.approx(0.87, abs=0.005)
    equivalent = factor * _beam(model.ArrayGeometry(0, 180, 800), 10, 90)
    assert _beam(geo, 10, 90) / equivalent == pytest.approx(2.14, abs=0.005)


def test_each_plane_clips_on_its_own():
    """Sun behind the west modules: they give nothing, they do not take from the east modules."""
    geo = _equal_planes((90, 90), (90, 270))
    east = model.ArrayGeometry(90, 90, 400)
    assert _beam(geo, 20, 90) == pytest.approx(0.5 * _beam(east, 20, 90))
    assert _beam(geo, 20, 90) > 0 and _beam(geo, 20, 270) == pytest.approx(_beam(geo, 20, 90))
    beam, diff, _ = model.array_irradiance(geo, _sun(20, 90), 800.0, 100.0, 400.0, DNI_EXTRA)
    assert beam > 0 and diff > 0


def test_same_orientation_twice_is_one_plane():
    split = model.ArrayGeometry(30, 135, 1000, extra=(model.Plane(30, 135, 400),))
    single = model.ArrayGeometry(30, 135, 1000)
    weather = [model.clearsky_weather(SITE, 1781913600.0 + h * 3600, 20.0) for h in range(24)]
    a, b = model.forecast(SITE, split, model.LearnedArray(), weather), model.forecast(SITE, single, model.LearnedArray(), weather)
    assert all(a[t] == pytest.approx(b[t], rel=1e-9, abs=1e-9) for t in a)


def test_single_plane_is_unchanged():
    """One plane: exactly the former formula (plane_of_array of the configured plane, the mounting's heating)."""
    geo = model.ArrayGeometry(35, 160, 900, 40.0)
    learned = model.LearnedArray(efficiency=0.91, temp_coeff=-0.004, cells={"10,2": 0.3, "11,3": 0.6},
                                 direction={"5,1": 1.05}, reflection={"4,0": 0.05}, sky_view=0.85)
    start = 1781913600.0                                 # 2026-06-20 UTC
    weather = [model.WeatherHour(start + h * 3600, 50.0 + 30 * h, None, None, 18.0) for h in range(24)]
    sun = _sun(25, 120)
    assert model.array_irradiance(geo, sun, 700.0, 120.0, 450.0, DNI_EXTRA, sky_view=0.85) == \
        (*solar.plane_of_array(35, 160, sun, 700.0, 120.0, 450.0, DNI_EXTRA, sky_view=0.85), 40.0)
    assert model.clearsky_heating(SITE, geo, start + 10 * 3600) == 40.0

    expected = {}
    for wh in weather:                                   # the forecast as it was written before planes existed
        dni_extra = solar.extra_radiation(wh.start + 1800)
        total = 0.0
        for i in range(model.SUBSTEPS):
            s = solar.sun_position(SITE, wh.start + (i + 0.5) * 3600 / model.SUBSTEPS)
            if s.elevation <= 0 or wh.ghi <= 0:
                continue
            dni, dhi = solar.erbs(wh.ghi, s.zenith, dni_extra)
            beam, diff = solar.plane_of_array(geo.tilt, geo.azimuth, s, dni, dhi, wh.ghi, dni_extra, sky_view=0.85)
            poa = beam * model.transmission(learned, s.azimuth, s.elevation) * model.direction_factor(
                learned, s.azimuth, s.elevation) + diff + model.reflection_factor(learned, s.azimuth, s.elevation) * dni
            total += geo.peak_w * learned.efficiency * model.temperature_factor(
                learned.temp_coeff, wh.temp, poa, geo.heating) * poa / 1000
        expected[wh.start] = total / model.SUBSTEPS
    assert model.forecast(SITE, geo, learned, weather) == expected


def _symmetric_site(day: float) -> solar.Site:
    """A site whose solar noon falls on 12:00 UTC of the day, so clock hours lie symmetric around it."""
    site = solar.Site(48.0, 0.0, 0)
    noon = max((day + s for s in range(10 * 3600, 14 * 3600, 10)), key=lambda t: solar.sun_position(site, t).elevation)
    return solar.Site(48.0, (noon - day - 43200) / 240.0, 0)       # 4 minutes per degree, east is earlier


def test_vertical_east_west_is_symmetric_and_productive():
    """Vertical east and west modules on one input: the equivalent plane is undefined, the planes are not."""
    day = 1789603200.0                                   # 2026-09-17 UTC, close to the equinox
    site = _symmetric_site(day)
    geo = _equal_planes((90, 90), (90, 270))
    weather = [model.clearsky_weather(site, day + h * 3600, 15.0) for h in range(24)]
    out = model.forecast(site, geo, model.LearnedArray(), weather)
    hour = {h: out[day + h * 3600] for h in range(24)}
    assert hour[7] > 20 and hour[16] > 20                # morning: east modules, afternoon: west modules
    for k in range(1, 7):
        assert hour[12 - k] == pytest.approx(hour[11 + k], rel=0.01)
    # entering only one of the real orientations misses the other half of the day
    east = model.forecast(site, model.ArrayGeometry(90, 90, 800), model.LearnedArray(), weather)
    assert east[day + 16 * 3600] < 0.6 * hour[16]


def test_heating_follows_each_plane():
    """Each module heats with its own irradiance: more heating than the mean irradiance alone would give."""
    geo = _equal_planes((90, 90), (90, 270))
    beam, diff, heating = model.array_irradiance(geo, _sun(20, 90), 800.0, 100.0, 400.0, DNI_EXTRA)
    east, west = (sum(solar.plane_of_array(90, a, _sun(20, 90), 800.0, 100.0, 400.0, DNI_EXTRA)) for a in (90, 270))
    assert heating > geo.heating
    per_plane = sum(0.5 * model.temperature_factor(-0.004, 25.0, p, geo.heating) * p for p in (east, west))
    assert model.temperature_factor(-0.004, 25.0, beam + diff, heating) * (beam + diff) == pytest.approx(per_plane)


def test_behind_only_when_behind_every_plane():
    geo = _equal_planes((90, 90), (90, 270))
    steps = shade.day_profile(SITE, geo, None, 1789603200.0)
    assert steps and all(s.state != shade.BEHIND for s in steps)
    south = shade.day_profile(SITE, model.ArrayGeometry(90, 180, 800), None, 1781913600.0)
    assert any(s.state == shade.BEHIND for s in south)


def test_learning_recovers_efficiency_with_planes():
    """Synthetic east+west array: learning with the planes finds the efficiency, without inventing shade."""
    geo = _equal_planes((30, 90), (30, 270))
    start = 1774915200.0                                 # 2026-03-31 UTC
    samples = []
    for h in range(90 * 24):
        t = start + h * 3600
        beam, diff, *_ = model.clearsky_hour(SITE, geo, t)
        samples.append((t, geo.peak_w * 0.93 * (beam + diff) / 1000))
    learned = model.learn(SITE, geo, samples, now=start + 90 * 86400)
    assert learned.efficiency == pytest.approx(0.93, abs=0.01)
    assert min(learned.cells.values()) >= 0.9
    assert arraycheck.check_geometry(SITE, geo, learned, samples, {t: 500.0 for t, _ in samples}) is None
