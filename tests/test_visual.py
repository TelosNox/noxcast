"""Shading map rendering: valid SVG, colours, gaps and labels."""
import xml.etree.ElementTree as ET
from datetime import date

from conftest import load

solar = load("solar")
model = load("model")
visual = load("visual")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=30, azimuth=180, peak_w=1000)


def test_colors():
    assert visual.transmission_color(0.0).startswith("#dc")
    assert visual.transmission_color(1.0) == visual.transmission_color(1.5)


def test_gap_is_filled_from_neighbours():
    cells = {"18,6": 0.2, "19,6": 0.4, "17,6": 0.6}
    assert abs(visual.display_value(cells, 18, 7) - 0.4) < 1e-9
    assert visual.display_value(cells, 25, 2) is None


def test_render_is_valid_svg_with_title_and_figures():
    learned = model.LearnedArray(efficiency=0.94, cells={"18,6": 0.1, "19,6": 1.0}, coverage=0.5, learned_at=1.0,
                                 temp_coeff=-0.0026, temp_source="datasheet", agreement=0.95)
    reachable = model.reachable_cells(SITE, GEO)
    svg = visual.render_shading_map(SITE, learned, reachable, date(2026, 10, 12), "Roof <A&B>", "de", GEO)
    root = ET.fromstring(svg)
    texts = " ".join(el.text or "" for el in root.iter("{http://www.w3.org/2000/svg}text"))
    assert "Roof <A&B>" in texts and "Datenblatt" in texts and "21. Jun" in texts
    assert len(list(root.iter("{http://www.w3.org/2000/svg}polyline"))) == 4
    assert "Module 180°" in texts
    dot = next(root.iter("{http://www.w3.org/2000/svg}circle"))           # perpendicular sun at 60° elevation
    assert "180° / 60°" in dot.find("{http://www.w3.org/2000/svg}title").text


def test_render_without_learned_state():
    svg = visual.render_shading_map(SITE, None, set(), date(2026, 10, 12), "Empty", "en")
    assert "not learned yet" in svg
