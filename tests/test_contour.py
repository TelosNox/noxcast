"""Shading contour: learned obstacle edges per azimuth column (synthetic site, see synthetic_site.py)."""
import functools
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import synthetic_site as ss
from conftest import load

solar = load("solar")
model = load("model")
shade = load("shade")
visual = load("visual")

GEO = model.ArrayGeometry(tilt=35, azimuth=180, peak_w=1000)
STEEP = model.ArrayGeometry(tilt=60, azimuth=135, peak_w=1000)     # the real example of the zigzag map


@functools.cache
def case(mask: str, geo=GEO, split: str = "solis"):
    data = ss.generate(solar, model, geo, ss.MASKS[mask], split=split)
    learned = model.learn(data["site"], geo, data["samples"], data["now"], satellite_ghi=data["sat"],
                          steady=data["steady"])
    return data, learned


def edges(learned, kind: str) -> dict[int, tuple[float, float]]:
    return {int(k): (c[kind], c["t_" + kind]) for k, c in learned.contour.items() if kind in c}


def improves(geo, learned, data, hours) -> bool:
    """The learned shading makes the forecast better than none would."""
    with_shade = ss.forecast_error(model, geo, learned, data, hours)
    return with_shade <= ss.forecast_error(model, geo, model.without_shade(learned), data, hours) + 0.002


def test_free_site_has_no_edges_on_the_noon_path():
    """Haze at Feb/Oct noon (a quarter of the clear hours 25-50 % low) does not become shade."""
    for geo in (GEO, STEEP):
        for split in ("solis", "erbs"):
            data, learned = case("free", geo, split)
            assert learned.shading_version == model.SHADING_VERSION and learned.steady_filter
            noon = {c for c in range(15, 21)}                                   # 150-210°
            assert not noon & (set(edges(learned, "low")) | set(edges(learned, "high"))), learned.contour
            assert improves(geo, learned, data, data["noon"])
            assert learned.coverage > 0.9


def test_low_wall_gives_a_sharp_lower_edge():
    data, learned = case("wall")
    low = edges(learned, "low")
    for c in range(11, 15):                                                     # 110-150°
        assert abs(low[c][0] - 15) <= 1.0 and low[c][1] < 0.1, low
    assert set(low) <= set(range(9, 16)) and not edges(learned, "high")
    assert improves(GEO, learned, data, data["clear"])


def test_overhang_gives_an_upper_edge():
    data, learned = case("overhang")
    high = edges(learned, "high")
    reached = [int(k) for k, c in learned.contour.items() if 11 <= int(k) <= 19 and c["seen"][1] > 50]
    assert len(reached) >= 6
    for c in reached:
        assert abs(high[c][0] - 45) <= 1.5 and high[c][1] < 0.1, high
    assert set(high) <= set(range(9, 21)) and not edges(learned, "low")
    assert improves(GEO, learned, data, data["clear"])


def test_tree_keeps_its_partial_transmission():
    data, learned = case("tree")
    low = edges(learned, "low")
    for c in range(20, 24):                                                     # 200-240°
        assert abs(low[c][0] - 25) <= 1.0 and abs(low[c][1] - 0.4) < 0.15, low
    assert set(low) <= set(range(19, 25)) and not edges(learned, "high")
    assert improves(GEO, learned, data, data["clear"])


def test_shadow_where_the_sun_is_nearly_behind_the_plane():
    """A vertical south facade: morning sun from 100-110° at low elevation carries little direct
    light, so its hours are mostly left out of the hourly fit. The cell estimates still see the
    house; the column must not come out free (and be extrapolated free below what the hours saw)."""
    facade = model.ArrayGeometry(tilt=90, azimuth=180, peak_w=1000)
    data, learned = case("morning", facade)
    low = edges(learned, "low")
    for c in (10, 11, 12):                                                      # 100-130°
        assert abs(low[c][0] - 20) <= 1.5 and low[c][1] < 0.1, low
    site = data["site"]
    morning = [t for t in data["clear"] if 100 <= solar.sun_position(site, t + 1800).azimuth <= 110
               and solar.sun_position(site, t + 1800).elevation < 18]
    assert len(morning) >= 10
    assert ss.forecast_error(model, facade, learned, data, morning) < \
        0.5 * ss.forecast_error(model, facade, model.without_shade(learned), data, morning)


def _column(ai: int, values: dict[int, float]) -> dict[str, float]:
    return {f"{ai},{ei}": v for ei, v in values.items()}


def test_lone_dark_spot_is_no_edge():
    """The zigzag map: 25-30° dark in alternating columns while 20-25° below is free. No ground
    obstacle can do that, and the sky above is free as well."""
    cells = {}
    for ai, dark in zip(range(16, 21), (1.0, 0.57, 0.85, 0.39, 0.89)):
        cells.update(_column(ai, {3: 1.0, 4: 1.0, 5: dark, **{e: 1.0 for e in range(6, 12)}}))
    contour = model._fit_contour(model._cell_equations(cells), 2)
    assert set(contour) == {str(c) for c in range(16, 21)}
    assert not any(model._has_edge(c) for c in contour.values())


def test_lower_edge_from_cells_with_extrapolation():
    cells = _column(12, {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0, 4: 1.0, 5: 1.0})
    contour = model._fit_contour(model._cell_equations(cells), 2)
    assert abs(contour["12"]["low"] - 15) <= 0.5 and contour["12"]["t_low"] < 0.05
    learned = model.LearnedArray(contour=contour, shading_version=model.SHADING_VERSION)
    assert model.transmission(learned, 125, 60) == 1.0                   # never visited: above the edge, free
    assert model.transmission(learned, 125, -2) < 0.05                   # below: shaded


def test_transmission_soft_edge_and_columns():
    wall = {"low": 15.0, "t_low": 0.0, "seen": [5.0, 40.0]}
    learned = model.LearnedArray(contour={"12": wall, "13": {"seen": [5.0, 40.0]}, "14": dict(wall)},
                                 shading_version=model.SHADING_VERSION)
    t = model.transmission
    assert t(learned, 125, 13.9) == 0.0 and t(learned, 125, 15) == 0.5 and t(learned, 125, 16.1) == 1.0
    assert abs(t(learned, 130, 10) - 0.5) < 1e-9                          # half way to a free column
    assert t(learned, 135, 10) == 1.0                                    # a column decided free stays free
    gap = model.LearnedArray(contour={"12": wall, "14": dict(wall)}, shading_version=model.SHADING_VERSION)
    assert t(gap, 135, 10) == 0.0                                        # undecided between two edges: their mean
    assert t(gap, 150, 10) == 0.5 and model.known_transmission(gap, 175, 10) is None
    overhang = model.LearnedArray(contour={"18": {"high": 45.0, "t_high": 0.2, "seen": [15.0, 62.0]}},
                                  shading_version=model.SHADING_VERSION)
    assert abs(t(overhang, 185, 50) - 0.2) < 1e-9 and t(overhang, 185, 40) == 1.0
    assert model.transmission(model.without_shade(learned), 125, 10) == 1.0


def test_stored_cells_keep_working_and_contour_round_trips():
    old = {"efficiency": 0.9, "cells": {"12,2": 0.0}, "coverage": 0.4}         # stored before the contours
    learned = model.LearnedArray.from_dict(old)
    assert learned.shading_version == 1 and model.transmission(learned, 125, 12.5) == 0.0
    new = model.LearnedArray(contour={"12": {"low": 15.0, "t_low": 0.0, "seen": [5.0, 40.0]}},
                             shading_version=model.SHADING_VERSION, cells={"12,2": 0.0})
    loaded = model.LearnedArray.from_dict(new.as_dict())
    assert loaded == new and model.transmission(loaded, 125, 10) == 0.0


def test_coverage_counts_decided_columns_within_their_data():
    reachable = {(12, e) for e in range(10)}
    contour = {"12": {"seen": [10.0, 30.0]}}
    assert model.contour_coverage(contour, reachable) == 0.6               # 5°-35° of 0°-50°
    assert model.contour_coverage({}, reachable) == 0.0


def test_shade_today_and_map_follow_the_contour():
    _, learned = case("wall")
    site = solar.Site(ss.SITE_LAT, ss.SITE_LON, ss.SITE_ALT)
    tz = timezone(timedelta(hours=1))
    day = datetime(2026, 12, 1, tzinfo=tz).timestamp()
    periods = shade.shade_periods(shade.day_profile(site, GEO, learned, day))
    assert periods and periods[0]["min_transmission"] < 0.1
    assert datetime.fromtimestamp(periods[0]["start"], tz).hour <= 9
    svg = visual.render_shading_map(site, learned, model.reachable_cells(site, GEO), date(2026, 12, 1), "Wall", "en", GEO)
    lines = [el for el in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}line") if "edge" in el.get("class", "")]
    assert len(lines) == len(edges(learned, "low")) and "obstacle below 15" in svg
