"""Walk-forward replay of the forecast band on a day-ahead hindcast, for band changes (C6).

The forecast archive holds a published band only since 0.4 and too few days per weather class to judge it. This
replays a year instead: Open-Meteo's previous-runs archive gives what the weather models forecast one day ahead
(the same source as the weather model comparison, hindcast.py), today's learned model turns it into a forecast
per hour, and the exported statistics give the actual output. For every day the band is learned from the days
before it (at most EVAL_DAYS, as in the integration) and checked on that day: the share below P10, inside and
above P90, the width and the interval score (width plus 2 / 0.2 times the miss), each relative to the clear-sky
energy. Lower interval score is better; a wider band that only buys coverage does not win.

Run it from main and from the branch on the same files and compare:

    python prototype/band_replay.py --config prototype/config.local.json --statistics prototype/data/hourly.json \\
        --satellite prototype/data/satellite.json --temperature prototype/data/temperature.json \\
        --previous prototype/data/previous_day1.json [--fetch icon_seamless,ecmwf_ifs]

--fetch downloads the previous-runs file first (the location goes to Open-Meteo, as from the integration). Days
with possible curtailment or at a limit count only for their clean hours, as in band.coverage. The learned model
is learned on all days (in sample); the band is out of sample.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("noxcast_backtest", HERE / "backtest.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)                       # puts custom_components on the path
bd = importlib.import_module("noxcast.band")
bt = P.backtest
mdl = P.model
const = importlib.import_module("noxcast.const")

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARS = ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation", "temperature_2m")
ALPHA = 1 - (bd.Q_HIGH - bd.Q_LOW)


def fetch(path: pathlib.Path, models: list[str], loc: dict, first: date, last: date) -> None:
    out = {}
    for model in models:
        col = out.setdefault(model, {"time": [], **{v: [] for v in VARS}})
        a = first
        while a <= last:
            b = min(a + timedelta(days=110), last)
            q = urllib.parse.urlencode({"latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "UTC",
                                        "models": model, "start_date": a.isoformat(), "end_date": b.isoformat(),
                                        "hourly": ",".join(f"{v}_previous_day1" for v in VARS)})
            with urllib.request.urlopen(f"{URL}?{q}", timeout=120) as r:
                h = json.load(r)["hourly"]
            col["time"] += h["time"]
            for v in VARS:
                col[v] += h.get(f"{v}_previous_day1") or h.get(f"{v}_previous_day1_{model}") or [None] * len(h["time"])
            a = b + timedelta(days=1)
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")


def weather(columns: dict) -> dict[float, mdl.WeatherHour]:
    out = {}
    for s, g, n, d, t in zip(columns["time"], *(columns[v] for v in VARS)):
        if g is None:
            continue
        start = datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() - 3600   # mean of the hour before
        both = n is not None and d is not None
        out[start] = mdl.WeatherHour(start=start, ghi=max(g, 0.0), dni=n if both else None, dhi=d if both else None, temp=t)
    return out


def summary(rows: list[tuple[float, float, float, float]]) -> str:
    n = len(rows)
    if not n:
        return "n=0"
    below = sum(a < lo for a, lo, _, _ in rows)
    above = sum(a > hi for a, _, hi, _ in rows)
    clear = sum(s for *_, s in rows)
    width = sum(hi - lo for _, lo, hi, _ in rows)
    score = sum((hi - lo) + 2 / ALPHA * (max(lo - a, 0.0) + max(a - hi, 0.0)) for a, lo, hi, _ in rows)
    return (f"n={n:5d}  below {below / n:5.1%}  inside {(n - below - above) / n:5.1%}  above {above / n:5.1%}"
            f"  width {width / clear:5.1%}  interval score {score / clear:5.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward replay of the forecast band")
    for name in ("config", "statistics", "previous"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--satellite")
    parser.add_argument("--temperature")
    parser.add_argument("--fetch", help="comma-separated weather models: download --previous first")
    args = parser.parse_args()

    cfg = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    loc = cfg["location"]
    tz = ZoneInfo(loc.get("timezone", "UTC"))
    site = P.solar.Site(loc["latitude"], loc["longitude"], loc.get("altitude", 0.0))
    arrays = dict(P._array(a, tz) for a in cfg["arrays"])
    raw = json.loads(pathlib.Path(args.statistics).read_text(encoding="utf-8"))
    cur = cfg.get("curtailment", {})
    socs = P._statistics({s: raw[s] for s in cur.get("soc_sensors", []) if s in raw}, "max")
    sat = {t - 3600: max(v, 0.0) for t, v in P._hourly(args.satellite, "shortwave_radiation").items()}
    inst = P._hourly(args.temperature, "temperature_2m")
    air = {t: (v + inst[t + 3600]) / 2 for t, v in inst.items() if t + 3600 in inst}
    snow = {t - 3600: max(v, 0.0) for t, v in P._snowfall(args.temperature).items()}
    history = P.ln.PvHistory(P._statistics(raw, "mean"), P._statistics(raw, "min"), P._statistics(raw, "max"), {},
                             P.ln.highest(socs), air or None, sat or None, snowfall=snow or None)
    first = min(t for s in arrays.values() for sensor in s.sensors for t in history.power.get(sensor, {}))
    last = max(t for s in arrays.values() for sensor in s.sensors for t in history.power.get(sensor, {}))
    soc_full = float(cur.get("soc_full_pct", 95))
    excl = [(datetime.combine(date.fromisoformat(a), datetime.min.time(), tz).timestamp(),
             datetime.combine(date.fromisoformat(b), datetime.max.time(), tz).timestamp())
            for a, b in cur.get("exclude_periods", [])]
    previous = pathlib.Path(args.previous)
    if args.fetch:
        fetch(previous, args.fetch.split(","), loc, datetime.fromtimestamp(first, tz).date(),
              datetime.fromtimestamp(last, tz).date())
    learned = P.ln.learn_arrays(site, arrays, history, last + 3600, soc_full, excl)

    models = [weather(c) for c in json.loads(previous.read_text(encoding="utf-8")).values()]
    common = sorted(set.intersection(*(set(m) for m in models)))
    hours = mdl.mean_weather([[m[t] for t in common] for m in models])
    fc = {sid: mdl.forecast(site, a.geometry, learned[sid], hours) for sid, a in arrays.items()}
    sky = {sid: mdl.forecast(site, a.geometry, learned[sid], [mdl.clearsky_weather(site, t) for t in common])
           for sid, a in arrays.items()}
    clear_ghi = {t: mdl.clearsky_weather(site, t).ghi for t in common}
    use = {sid: bt.usable(a, history, soc_full, excl, site) for sid, a in arrays.items()}
    limit = {sid: sum(w for _, w in a.limits) if a.limits else None for sid, a in arrays.items()}
    day_of = bt.local_day(tz)

    # the hindcast in the archive's form, so learn_band reads it as it reads the real archive
    arch: dict[str, dict] = {}
    for w in hours:
        t = w.start
        if t > last:
            continue
        f = {sid: fc[sid].get(t, 0.0) for sid in arrays}
        h = {"ghi": w.ghi, "forecast": f, "actual": {sid: use[sid].get(t, 0.0) for sid in arrays}}
        if any(f[s] > bt.MIN_WH or sky[s].get(t, 0.0) > bt.MIN_WH for s in arrays) and any(t not in use[s] for s in arrays):
            h["curtailed"] = True
        if any(limit[s] and f[s] >= 0.95 * limit[s] for s in arrays):
            h["limited"] = True
        arch.setdefault(day_of(t).isoformat(), {"hours": {}})["hours"][str(int(t))] = h
    keys = sorted(k for k, v in arch.items() if len(v["hours"]) >= 20)

    def clear_wh(ts: float, ids: list[str]) -> float:
        return sum(sky[s].get(ts, 0.0) for s in ids)

    ids = list(arrays)
    days: dict[str, list] = {}
    hrs: dict[str, list] = {}
    for i, key in enumerate(keys):
        oldest = date.fromisoformat(key) - timedelta(days=const.EVAL_DAYS)
        window = {k: arch[k] for k in keys[:i] if date.fromisoformat(k) >= oldest}
        if len(window) < 30:
            continue
        band = bd.learn_band(window, lambda ts: clear_ghi.get(ts) or mdl.clearsky_weather(site, ts).ghi, clear_wh)
        ts = sorted(float(t) for t in arch[key]["hours"])
        rows = [arch[key]["hours"][str(int(t))] for t in ts]
        total = sum(sum(h["forecast"].values()) for h in rows)
        sky_total = sum(clear_wh(t, ids) for t in ts)
        c = sum(clear_ghi[t] for t in ts if clear_ghi[t] > 0)
        kt = sum(h["ghi"] for t, h in zip(ts, rows) if clear_ghi[t] > 0) / c if c else 0.0
        clean = not any(h.get("curtailed") or h.get("limited") for h in rows)
        if clean and sky_total > bd.MIN_DAY_WH and (r := bd.day_band(band, total, sky_total, kt)):
            days.setdefault(bd.weather_class(kt), []).append(
                (sum(sum(h["actual"].values()) for h in rows), r[0], r[1], sky_total))
        hb = bd.hour_band(band, {t: sum(h["forecast"].values()) for t, h in zip(ts, rows)},
                          {t: clear_wh(t, ids) for t in ts},
                          {t: h["ghi"] / clear_ghi[t] for t, h in zip(ts, rows) if clear_ghi[t] > 0})
        for t, h in zip(ts, rows):
            if t in hb and not (h.get("curtailed") or h.get("limited")) and clear_wh(t, ids) >= bd.MIN_HOUR_WH:
                hrs.setdefault(bd.weather_class(h["ghi"] / clear_ghi[t]), []).append(
                    (sum(h["actual"].values()), *hb[t], clear_wh(t, ids)))

    print(f"{len(keys)} days {keys[0]} to {keys[-1]}, target {bd.Q_LOW:.0%} below and {1 - bd.Q_HIGH:.0%} above")
    for label, rows in (("days", days), ("hours", hrs)):
        print(f"{label:5s} all       {summary([r for v in rows.values() for r in v])}")
        for name in ("clear", "mixed", "overcast"):
            if name in rows:
                print(f"{label:5s} {name:9s} {summary(rows[name])}")


if __name__ == "__main__":
    sys.exit(main())
