"""Shading map on a synthetic site: no spurious cores from a few bad hours, real obstacles stay sharp."""
import random

from conftest import load

solar = load("solar")
model = load("model")

SITE = solar.Site(51.0, 10.0, 200)
GEO = model.ArrayGeometry(tilt=60, azimuth=135, peak_w=1000)
START = 1767225600.0          # 2026-01-01 UTC
EFF = 0.9
RARE = [(31, 59), (288, 304)]  # mid-February, late October: the noon sun at 25-30° elevation


def free(az: float, el: float) -> float:
    return 1.0


def obstacles(az: float, el: float) -> float:
    """A low wall to the east and a pole south-south-east."""
    wall = 90 <= az <= 150 and el < 15
    pole = 170 <= az < 180 and el < 40
    return 0.0 if wall or pole else 1.0


def make_year(mask, seed: int):
    """One year of hourly means with satellite GHI and steady hours; clear days are steady.

    The power carries 3 % noise. In February and October, around noon, a quarter of the clear hours
    lose 25-50 % (haze the satellite and the steady test miss), and most clear days there are
    not steady, so few hours inform those cells.
    """
    rng = random.Random(seed)
    samples, sat, steady, truth = [], {}, set(), {}
    for d in range(365):
        clear = rng.random() < 0.45
        cloud = 1.0 if clear else rng.uniform(0.1, 0.6)
        rare = any(a <= d < b for a, b in RARE)
        calm = clear and not (rare and rng.random() < 0.6)
        for h in range(24):
            t = START + d * 86400 + h * 3600
            dni_extra = solar.extra_radiation(t + 1800)
            power = ghi_sum = 0.0
            for i in range(model.SUBSTEPS):
                sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
                if sun.elevation <= 0:
                    continue
                ghi, dni, dhi = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, dni_extra)
                beam, diff = solar.plane_of_array(GEO.tilt, GEO.azimuth, sun, dni * cloud, dhi, ghi, dni_extra)
                power += GEO.peak_w * EFF * (beam * mask(sun.azimuth, sun.elevation) + diff) / 1000
                ghi_sum += ghi * (1.0 if clear else 0.3 + 0.5 * cloud)
            power /= model.SUBSTEPS
            truth[t] = power
            if power > 0:
                power *= 1 + rng.gauss(0, 0.03)
                if clear and rare and 9 <= h <= 13 and rng.random() < 0.25:
                    power *= rng.uniform(0.5, 0.75)
                if calm:
                    steady.add(t)
            samples.append((t, power))
            sat[t] = ghi_sum / model.SUBSTEPS
    return samples, sat, steady, truth


def learn_year(mask, seed: int):
    samples, sat, steady, _ = make_year(mask, seed)
    learned = model.learn(SITE, GEO, samples, now=START + 365 * 86400, satellite_ghi=sat, steady=steady)
    assert learned.steady_filter
    return {tuple(map(int, k.split(","))): v for k, v in learned.cells.items()}


def test_no_isolated_cores_without_obstacle():
    for seed in (2, 7, 14):                  # seeds that gave isolated cores on the noon path before
        cells = learn_year(free, seed)
        shaded = {c: v for c, v in cells.items() if v < model.SHADE_FREE}
        assert not shaded, (seed, shaded)


def test_real_obstacles_keep_sharp_edges():
    cells = learn_year(obstacles, 5)
    wall = [cells[(a, e)] for a in range(10, 15) for e in range(3) if (a, e) in cells]
    assert len(wall) >= 12 and max(wall) < 0.1
    above = [cells[(a, e)] for a in range(9, 15) for e in range(4, 9) if (a, e) in cells]
    assert above and min(above) == 1.0                                # the edge at 15° stays sharp
    pole = [cells[(17, e)] for e in range(3, 8)]
    assert max(pole) < 0.2                                           # one cell wide, over 15-40°
    beside = [cells[(a, e)] for a in (16, 18) for e in range(3, 8)]
    assert min(beside) == 1.0
    assert min(cells[(17, e)] for e in range(9, 12)) == 1.0          # free above the pole


def _map(rows: dict[int, list[float]], first: int) -> dict[str, float]:
    return {f"{first + i},{e}": v for e, values in rows.items() for i, v in enumerate(values)}


def test_shape_removes_zigzag_along_azimuth():
    # 160-200° on the sun path at 25-30°: alternating light and dark, the cells below free-ish
    cells = _map({3: [1.0] * 5, 4: [1.0, 0.88, 0.84, 1.0, 1.0], 5: [1.0, 0.57, 0.85, 0.39, 0.89],
                  6: [1.0] * 5, 7: [1.0] * 5}, 16)
    out = model.shape_cells(cells)
    assert all(v == 1.0 for v in out.values()), out


def test_shape_keeps_compact_core_block():
    cells = _map({3: [1.0] * 5, 4: [1.0, 0.4, 0.5, 1.0, 1.0], 5: [1.0, 0.45, 0.6, 1.0, 1.0],
                  6: [1.0] * 5}, 16)
    out = model.shape_cells(cells)
    assert out["17,4"] == 0.4 and out["18,4"] == 0.5 and out["17,5"] == 0.45 and out["18,5"] == 0.6
    assert out["16,4"] == 1.0 and out["19,5"] == 1.0


def test_shape_lone_core_needs_a_monotone_column():
    # a single dark cell between free ones: noise; the same cell as the dark end of a profile: shade
    lone = model.shape_cells(_map({4: [1.0, 1.0, 1.0], 5: [1.0, 0.5, 1.0], 6: [1.0, 1.0, 1.0]}, 10))
    assert lone["11,5"] == 1.0
    ground = model.shape_cells(_map({4: [1.0, 0.5, 1.0], 5: [1.0, 0.8, 1.0], 6: [1.0, 1.0, 1.0]}, 10))
    assert ground["11,4"] == 0.5 and ground["11,5"] == 0.8
    dip = model.shape_cells(_map({4: [1.0, 0.85, 1.0], 5: [1.0, 0.5, 1.0], 6: [1.0, 1.0, 1.0]}, 10))
    assert dip["11,5"] == 1.0 and dip["11,4"] == 1.0                # darker above brighter: not monotone
