"""Every daylight hour with what learning made of it (databasis.py), and the redacted diagnostics (redact.py)."""
import csv
from datetime import date, datetime, timezone
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


def _row(start: str, status: str, reasons: str = "", power: float = 500.0, clear: float = 800.0,
         model: float | None = 500.0, kt: float | None = 0.6, bound: float = 900.0, steady: bool = True) -> list:
    r = dict.fromkeys(db.COLUMNS)
    r.update(start=start, status=status, reasons=reasons, power_w=power, clear_w=clear, model_w=model, kt=kt,
             bound_w=bound, steady=steady)
    return [r[c] for c in db.COLUMNS]


def _hours(day: int, month: int = 6, **kw) -> list[list]:
    return [_row(f"2026-{month:02d}-{day:02d}T{h:02d}:00:00+00:00", **kw) for h in range(8, 16)]


def test_summary_weighs_by_clear_sky_energy_and_names_anomalies():
    rows = []
    for d in range(1, 21):                        # used hours 10 % off the model: none within 5 %
        rows += _hours(d, status="used", power=450.0)
    for d in range(21, 25):                       # "battery full" while delivering the model: did not limit
        rows += _hours(d, status="left_out", reasons="battery_full", power=500.0, model=505.0)
    rows += _hours(25, status="left_out", reasons="no_output", power=0.0, kt=0.8)        # bright: an outage
    rows += _hours(26, status="used", power=200.0)                                       # 40 % of the model
    rows += [_row("2026-06-27T12:00:00+00:00", "left_out", "before_use")]                # not data quality
    rows += _hours(27, status="used", power=40.0, clear=50.0, model=40.0)                # too dark for the rules
    out = db.summary(SITE, {"columns": db.COLUMNS, "rows": rows}, 1000.0)
    assert out["shares"]["battery"] == round(4 * 8 * 800 / (26 * 8 * 800 + 8 * 50), 3)  # per hour: 800 or 50
    assert set(out["months"]) == {"2026-06"}
    keys = {a["key"]: a for a in out["anomalies"]}
    assert set(keys) == {"battery_matches", "bright_outage", "unexplained"}
    assert keys["battery_matches"]["count"] == 4 and keys["unexplained"]["value"] == 0.4
    assert keys["unexplained"]["day"] == "2026-06-26"                    # a date: its noon would give the longitude


def test_days_stored_as_noon_times_load_as_dates():
    d = model.solar_day(SITE, datetime(2026, 6, 26, 12, tzinfo=timezone.utc).timestamp())
    noon = d * 86400 + 43200 - SITE.longitude * 240                     # how versions before 4 stored a day
    learned = model.LearnedArray(efficiency=0.8, dead_days=[noon], snow_days=[noon, noon + 86400],
                                 basis={"version": 3, "anomalies": [{"key": "unexplained", "day": noon}]})
    out = db.dated(SITE, learned)
    assert out.dead_days == ["2026-06-26"] and out.snow_days == ["2026-06-26", "2026-06-27"]
    assert out.basis == {"version": db.SUMMARY_VERSION, "anomalies": [{"key": "unexplained", "day": "2026-06-26"}]}
    assert db.dated(SITE, out).dead_days == ["2026-06-26"]               # already dates: unchanged


def test_a_snow_window_closed_only_at_its_longest_is_an_anomaly():
    table = {"columns": db.COLUMNS, "rows": []}
    assert db.summary(SITE, table, 1000.0)["anomalies"] == []
    end = datetime(2026, 1, 9, 0, tzinfo=timezone.utc).timestamp()       # the window's first hour after the cap
    out = db.summary(SITE, table, 1000.0, [end - 20 * 86400, end])
    assert out["anomalies"] == [{"key": "snow_cap", "count": 2, "value": None, "day": "2026-01-08"}]


def test_snow_cover_is_not_an_outage_and_known_limits_and_the_bound():
    rows = []
    for d in range(1, 4):                         # covered under a bright sky after snowfall: snow, not an outage
        rows += _hours(d, month=1, status="left_out", reasons="no_output snow", power=0.0, kt=0.8)
    for d in range(4, 20):                        # a configured limit is no anomaly
        rows += _hours(d, month=1, status="lower_bound", reasons="at_limit")
    for d in range(20, 22):                       # above the clear-sky bound of the learned model in every hour
        rows += _hours(d, month=1, status="used", power=700.0, bound=600.0)
    for d in range(22, 30):                       # above it under broken cloud only: sunlit clouds, no anomaly
        rows += _hours(d, month=1, status="used", power=1000.0, bound=600.0, steady=False)
    out = db.summary(SITE, {"columns": db.COLUMNS, "rows": rows}, 1000.0)
    assert out["shares"]["snow"] > 0 and "no_output" not in out["shares"]
    keys = {a["key"]: a for a in out["anomalies"]}
    assert set(keys) == {"above_clear"} and keys["above_clear"]["count"] == 16
    assert db.summary(SITE, {"columns": db.COLUMNS, "rows": []}, 1000.0) == {
        "version": db.SUMMARY_VERSION, "hours": 0, "shares": {}, "months": {}, "anomalies": [], "days": {"day": [], "cat": [], "ratio": [], "input": [], "band": [], "battery": []}}


def test_input_spread_follows_the_sky():
    assert db.input_spread(0.0) == db.INPUT_SPREAD[0][1] and db.input_spread(1.0) == db.INPUT_SPREAD[-1][1]
    assert db.input_spread(0.45) == (0.07 + 0.065) / 2
    assert all(db.input_spread(a) >= db.input_spread(b) for a, b in [(0.1, 0.3), (0.3, 0.6), (0.6, 0.9)])


def test_model_spread_from_the_bright_days():
    assert db.model_spread([(0.0, 0.8)] * 5) == db.MODEL_SPREAD                    # too few bright days
    calm = [((-1) ** k * 0.005, 0.8) for k in range(31)]                            # within the input's 2 %
    assert db.model_spread(calm) == 0.0
    # a seasonal course (a tilt that does not fit) is no noise: it keeps its colour instead of widening the band
    drift = [(-0.1 + 0.2 * k / 39, 0.8) for k in range(40)] + [(-1.0, 0.1)] * 30   # dull days do not count
    assert db.model_spread(drift) == 0.0
    noisy = [((-1) ** k * 0.025, 0.8) for k in range(31)]                           # day to day ±2.5 %
    assert abs(db.model_spread(noisy) - (1.4826 ** 2 * 0.05 ** 2 / 2 - 0.02 ** 2) ** 0.5) < 1e-9
    assert db.model_spread([((-1) ** k * 0.3, 0.8) for k in range(31)]) == db.MODEL_SPREAD_MAX


def test_calendar_colours_a_day_by_its_deviation_or_its_main_reason():
    rows = _hours(1, status="used", power=425.0)                                   # 85 % of the model
    rows += _hours(2, status="left_out", reasons="no_output snow", power=0.0)      # left out: snow first
    day3 = _hours(3, status="used")
    day3[3:5] = [_row(r[0], "left_out", "battery_full") for r in day3[3:5]]        # two noon hours battery full
    rows += day3
    rows += _hours(4, status="used", clear=10.0, model=10.0)                        # too dull to compare
    rows += _hours(5, status="used", power=350.0, clear=2000.0)                     # 70 % under a dull sky (25 %)
    out = db.summary(SITE, {"columns": db.COLUMNS, "rows": rows}, 1000.0)
    assert out["days"] == {"day": ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"],
                           "cat": ["used", "snow", "used", "used", "used"], "ratio": [0.85, None, 1.0, None, 0.7],
                           # 85 % under a bright sky is more than satellite and model noise cause, 70 % under a dull
                           # one is not (too few bright days: the default noise)
                           "input": [False, False, True, False, True], "band": [0.113, None, 0.113, None, 0.428],
                           "battery": [0, 0, 2, 0, 0]}
    table = {"version": db.VERSION, "columns": db.COLUMNS, "rows": rows}
    assert db.day_rows(SITE, table, date(2026, 6, 3))["rows"] == day3
    assert db.day_rows(SITE, table, date(2026, 7, 3))["rows"] == []
    # only the year before the last day
    old = _hours(1, month=1, status="used") + _hours(2, month=1, status="used")
    out = db.summary(SITE, {"columns": db.COLUMNS, "rows": old + [
        _row(r[0].replace("2026", "2027"), "used") for r in old[:8]]}, 1000.0)
    assert out["days"]["day"] == ["2026-01-02", "2027-01-01"]
