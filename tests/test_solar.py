"""Validate the pure-Python physics against pvlib (test-only dependency)."""
import numpy as np
import pandas as pd
import pvlib
import pytest

from conftest import load

solar = load("solar")

SITE = solar.Site(48.97, 8.45, 113)
LOC = pvlib.location.Location(48.97, 8.45, "UTC", 113)
TIMES = pd.date_range("2026-01-01", "2027-01-01", freq="37min", tz="UTC", inclusive="left")


@pytest.fixture(scope="module")
def ref():
    sp = LOC.get_solarposition(TIMES)
    cs = LOC.get_clearsky(TIMES, model="simplified_solis", solar_position=sp)
    return sp, cs


def _ts(t):
    return t.timestamp()


def test_sun_position(ref):
    sp, _ = ref
    pos = [solar.sun_position(SITE, _ts(t)) for t in TIMES]
    elev = np.array([p.elevation for p in pos])
    az = np.array([p.azimuth for p in pos])
    up = sp.apparent_elevation.to_numpy() > 1
    assert np.max(np.abs(elev[up] - sp.apparent_elevation.to_numpy()[up])) < 0.1
    daz = (az[up] - sp.azimuth.to_numpy()[up] + 180) % 360 - 180
    assert np.max(np.abs(daz)) < 0.1


def test_extra_radiation():
    ref = pvlib.irradiance.get_extra_radiation(TIMES, method="spencer").to_numpy()
    mine = np.array([solar.extra_radiation(_ts(t)) for t in TIMES])
    assert np.max(np.abs(mine - ref)) < 0.5


def test_clearsky(ref):
    sp, cs = ref
    dni_extra = pvlib.irradiance.get_extra_radiation(TIMES).to_numpy()
    mine = np.array([solar.clearsky_simplified_solis(e, SITE.pressure, x)
                     for e, x in zip(sp.apparent_elevation.to_numpy(), dni_extra)])
    for i, col in enumerate(["ghi", "dni", "dhi"]):
        assert np.max(np.abs(mine[:, i] - cs[col].to_numpy())) < 1.0, col


def test_erbs(ref):
    sp, cs = ref
    ghi = cs.ghi * 0.6
    expected = pvlib.irradiance.erbs(ghi, sp.zenith, TIMES)
    dni_extra = pvlib.irradiance.get_extra_radiation(TIMES).to_numpy()
    mine = np.array([solar.erbs(g, z, x) for g, z, x in zip(ghi.to_numpy(), sp.zenith.to_numpy(), dni_extra)])
    assert np.max(np.abs(mine[:, 0] - expected.dni.fillna(0).to_numpy())) < 1.0
    assert np.max(np.abs(mine[:, 1] - expected.dhi.fillna(0).to_numpy())) < 1.0


@pytest.mark.parametrize("tilt,azimuth", [(60, 135), (30, 225), (90, 180), (0, 180)])
def test_plane_of_array(ref, tilt, azimuth):
    sp, cs = ref
    poa = pvlib.irradiance.get_total_irradiance(tilt, azimuth, sp.apparent_zenith, sp.azimuth,
                                                cs.dni, cs.ghi, cs.dhi, albedo=0.2)
    aoi = pvlib.irradiance.aoi(tilt, azimuth, sp.apparent_zenith, sp.azimuth)
    beam_ref = (poa.poa_direct * pvlib.iam.martin_ruiz(aoi)).fillna(0).clip(lower=0).to_numpy()
    diff_ref = poa.poa_diffuse.fillna(0).to_numpy()
    mine = []
    for t, dni, dhi, ghi, el, az in zip(TIMES, cs.dni, cs.dhi, cs.ghi, sp.apparent_elevation, sp.azimuth):
        sun = solar.SunPosition(az, el, 90 - el)
        mine.append(solar.plane_of_array(tilt, azimuth, sun, dni, dhi, ghi))
    mine = np.array(mine)
    assert np.max(np.abs(mine[:, 0] - beam_ref)) < 1.0
    assert np.max(np.abs(mine[:, 1] - diff_ref)) < 1.0
