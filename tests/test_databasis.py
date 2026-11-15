"""Every daylight hour with what learning made of it (databasis.py), and the redacted diagnostics (redact.py)."""
import csv
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path

from conftest import load

solar = load("solar")
model = load("model")
ln = load("learning")
db = load("databasis")
rd = load("redact")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(tilt=30, azimuth=180, peak_w=1000)
START = 1780272000.0          # 2026-06-01 UTC
DAYS = 20


def _power() -> dict[float, float]:
    """Clear days at 60 % of the physics, day 5 without output, day 9 at its 950 W limit around noon."""
    out = {}
    for d in range(DAYS):
        for h in range(24):
            t = START + d * 86400 + h * 3600
            w = 0.6 * model.forecast(SITE, GEO, model.LearnedArray(), [model.clearsky_weather(SITE, t)]).get(t, 0.0)
            out[t] = 0.0 if d == 5 else 950.0 if d == 9 and 10 <= h <= 14 else w
    return out


def _history(power: dict[float, float], **kw) -> ln.PvHistory:
    sat = {t: model.clearsky_weather(SITE, t).ghi for t in power}
    return ln.PvHistory({"s": power}, {"s": dict(power)}, {"s": dict(power)}, {}, kw.pop("soc", {}),
                        {t: 20.0 for t in power}, sat, **kw)


def _arr() -> ln.ArrayConfig:
    return ln.ArrayConfig("a", "Roof", ("s",), GEO, None, limits=(("s", 950.0),))


def test_status_agrees_with_what_learning_used():
    power = _power()
    full = frozenset(START + 2 * 86400 + h * 3600 for h in range(10, 14))           # battery full at noon of day 2
    history = _history(power, full={"a": full})
    excl = [(START + 12 * 86400, START + 13 * 86400)]
    arr = _arr()
    samples, _, floors = ln.array_samples(arr, history, 95.0, excl, skip=ln.left_out(SITE, arr, history).hours)
    reasons = ln.hour_reasons(SITE, arr, history, 95.0, excl)
    assert {t for t, (why, _) in reasons.items() if not why} == {t for t, _ in samples}
    assert {t for t, (_, low) in reasons.items() if low} == floors & {t for t, _ in samples}
    assert all(reasons[t][0] == ("battery_full",) for t in full)
    noon5 = START + 5 * 86400 + 12 * 3600
    assert reasons[noon5][0] == ("no_output",)
    assert reasons[START + 12 * 86400 + 12 * 3600][0] == ("excluded",)
    assert reasons[START + 9 * 86400 + 11 * 3600][1] == ("at_limit",)


def test_table_lists_daylight_hours_with_the_model_split():
    power = _power()
    history = _history(power, full={"a": frozenset({START + 2 * 86400 + 12 * 3600})})
    arr = _arr()
    learned = ln.learn_array(SITE, arr, history, START + DAYS * 86400, 95.0, [])
    table = db.hour_table(SITE, arr, learned, history, 95.0, [])
    rows = [dict(zip(table["columns"], r)) for r in table["rows"]]
    assert rows and all(r["clear_w"] is not None for r in rows)                   # sun up only, satellite for all
    by = {r["start"]: r for r in rows}
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()  # noqa: E731
    noon = by[iso(START + 12 * 3600)]
    assert noon["status"] == "used" and noon["reasons"] == "" and noon["efficiency"] is not None
    parts = sum(noon[c] for c in ("clear_w", "clouds_w", "shade_w", "reflection_w", "direction_w", "heat_w"))
    assert abs(parts - noon["model_w"]) < 0.5                                    # the steps add up to the model
    assert by[iso(START + 2 * 86400 + 12 * 3600)]["reasons"] == "battery_full"
    assert by[iso(START + 5 * 86400 + 12 * 3600)]["status"] == "left_out"
    assert by[iso(START + 9 * 86400 + 11 * 3600)]["status"] == "lower_bound"
    text = db.to_csv(table)
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == db.COLUMNS and len(parsed) == len(rows) + 1
    assert all(v in ("", "0", "1") for v in (r[db.COLUMNS.index("steady")] for r in parsed[1:]))


def _privacy():
    path = Path(__file__).parent.parent / ".github/scripts/check_privacy.py"
    spec = importlib.util.spec_from_file_location("check_privacy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redaction_keeps_the_structure_and_removes_what_identifies_a_home():
    diag = {
        "config": {"soc_sensors": ["sensor.home_battery_soc"], "house_load": "sensor.house_power"},
        "options": {"api_key": "abc", "archive_url": "http://192.168.1.20:5000/v1/archive",      # privacy-ok
                    "satellite_url": "https://satellite-api.open-meteo.com/v1/archive"},
        "arrays": {"x": {"sensors": ["sensor.pv1", "sensor.pv10"], "latitude": 48.1234}},       # privacy-ok
        "limits": {"detected": {"sensor.pv1": {"w": 600}}},
        "archive_errors": ["sensor.pv1 has no statistics; http://my.duckdns.org/x refused",     # privacy-ok
                           "my.duckdns.org answered 500", "10.0.0.7: ClientConnectorError"],    # privacy-ok
        "weather_url": "https://api.open-meteo.com/v1/forecast?apikey=xyz&latitude=48.12",     # privacy-ok
        "note": "open-meteo.com answered 429", "version": "2026.10.1",
    }
    out = rd.redact(diag)
    text = json.dumps(out)
    assert not any(s in text for s in ("home_battery", "house_power", "pv1", "192.168", "duckdns", "abc", "48.1", "10.0.0.7"))  # privacy-ok
    pv1, pv10 = out["arrays"]["x"]["sensors"]
    assert pv1 != pv10 and out["limits"]["detected"] == {pv1: {"w": 600}}         # the same sensor, the same alias
    assert out["archive_errors"] == [f"{pv1} has no statistics; {rd.REDACTED} refused", f"{rd.REDACTED} answered 500",
                                     f"{rd.REDACTED}: ClientConnectorError"]
    assert out["weather_url"] == "https://api.open-meteo.com/v1/forecast"        # no query: a key may sit in it
    assert out["options"]["satellite_url"].endswith("open-meteo.com/v1/archive")   # the public default stays
    assert out["note"] == "open-meteo.com answered 429" and out["version"] == "2026.10.1"
    assert _privacy().scan(json.dumps(out, indent=1)) == []
