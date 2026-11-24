"""Backtest of the PV model on exported data: a model change must show here that it is better on unlearned days.

Runs the same functions as the integration (custom_components/noxcast/backtest.py) and prints the metrics as
JSON. To compare two model versions, run this script from both checkouts on the same files.

    python prototype/backtest.py --config config.local.json --statistics data/hourly.json \
        --satellite data/satellite.json --temperature data/temperature.json --archive data/archive.json

--config      as config.example.json: location (with timezone), arrays and curtailment; per array optionally
              id (the array id used in the archive), heating, temp_coeff (per K), valid_since (ISO date),
              limits ({sensor: fixed limit in W}, e.g. the inputs of an inverter) and
              extra ([{tilt, azimuth, peak_w}]; peak_w of the array is that of its first plane).
              Optionally output_limits: [{arrays: [array ids], limit_w}], the fixed output limits of systems
              without a battery (installation.Cap), one entry per phase where a limit holds per phase: learning
              counts the hours at them as lower bounds, and both backtests cap the forecast at them.
--statistics  recorder/statistics_during_period answer: {sensor: [{start (ms), mean, min, max}]}, power in W.
--satellite   Open-Meteo archive answer (or its "hourly" block) with shortwave_radiation; optional.
--temperature Open-Meteo archive answer (or its "hourly" block) with temperature_2m and optionally snowfall
              (cm, for the snow filter); optional.
--archive     the day-ahead archive, as stored ({"days": ...}) or plain; optional.
--fine        the 5-minute archive, as stored (.storage/noxcast.<entry id>.fine) or plain {"days": ...}; optional.
              Adds "fine_holdout": would learning from it be better? Takes much longer than the rest.
--now         ISO time of the run, the reference for ageing; default: the last hour of the statistics.
--scorecard   print the hold-out scorecard as a Markdown table instead of the JSON, for a pull request.
--swap        ISO date of a synthetic device swap (docs/concept.md, System swap, proof): every array is frozen up to
              that day (swap.freeze), the later power comes from renamed sensors scaled by --swap-factor (default
              0.95 with fixed limits, else 1.05, so that the limit bounds stay comparable), and learning and
              hold-out run with and without the swap. Prints per array the efficiency against the expected one, the
              ratio, how far the shading map moved and the hold-out error of both.

All these files hold personal data and stay out of the repository (*.local.json, prototype/data/).
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import pathlib
import sys
import time
import types
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "noxcast"
_pkg = types.ModuleType("noxcast")               # the pure modules only, without the package __init__
_pkg.__path__ = [str(PKG)]
sys.modules["noxcast"] = _pkg
archive = importlib.import_module("noxcast.archive")
backtest = importlib.import_module("noxcast.backtest")
ln = importlib.import_module("noxcast.learning")
model = importlib.import_module("noxcast.model")
solar = importlib.import_module("noxcast.solar")


def _hourly(path: str | None, name: str) -> dict[float, float]:
    """{UTC hour as Open-Meteo labels it: value}; sources.py reads the archives the same way."""
    if not path:
        return {}
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    data = data.get("hourly", data)
    return {datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp(): v
            for t, v in zip(data["time"], data[name]) if v is not None}


def _snowfall(path: str | None) -> dict[float, float]:
    """The temperature archive's snowfall (cm), if it was fetched with it; else nothing."""
    if not path:
        return {}
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return _hourly(path, "snowfall") if "snowfall" in data.get("hourly", data) else {}


def _statistics(raw: dict[str, list[dict]], kind: str) -> dict[str, dict[float, float | None]]:
    return {sensor: {r["start"] / 1000: r.get(kind) for r in rows} for sensor, rows in raw.items()}


def _array(cfg: dict, tz: ZoneInfo) -> tuple[str, ln.ArrayConfig]:
    extra = tuple(model.Plane(float(p["tilt"]), float(p["azimuth"]), float(p["peak_w"])) for p in cfg.get("extra", ()))
    geometry = model.ArrayGeometry(float(cfg["tilt"]), float(cfg["azimuth"]),
                                   float(cfg["peak_w"]) + sum(p.peak_w for p in extra),
                                   float(cfg.get("heating", model.HEATING_K)), extra)
    since = cfg.get("valid_since")
    valid_since = datetime.combine(date.fromisoformat(since), datetime.min.time(), tz).timestamp() if since else None
    sid = cfg.get("id", cfg["name"])
    limits = tuple((s, float(w)) for s, w in sorted((cfg.get("limits") or {}).items()))
    return sid, ln.ArrayConfig(sid, cfg["name"], tuple(cfg["sensors"]), geometry, valid_since, cfg.get("temp_coeff"),
                               limits)


def _cell(s: dict) -> str:
    if not s["hours"] or s["hour_error"] is None:          # no hours, or no actual energy in them
        return "–"
    return f"{100 * s['hour_error']:.1f} % / {100 * s['bias']:+.1f} %"


def scorecard_table(holdout: dict, names: dict[str, str]) -> str:
    """The scorecard per array: hour error / bias of hold-out and in-sample, and the hours, per subset."""
    cards = {sid: r["scorecard"] for sid, r in holdout["arrays"].items() if "scorecard" in r}
    head = ["Subset"] + [f"{names[sid]} {part}" for sid in cards for part in ("hold-out", "in-sample", "hours")]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    subsets = list(dict.fromkeys(name for card in cards.values() for name in card))
    for name in subsets:
        cells = [name]
        for card in cards.values():
            part = card.get(name)
            cells += [_cell(part["holdout"]), _cell(part["in_sample"]), str(part["holdout"]["hours"])] if part else ["–"] * 3
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows + ["", "Hour error / bias in % of the actual energy."])


def _swapped(site: solar.Site, arrays: dict[str, ln.ArrayConfig], history: ln.PvHistory, until: float,
             resume: float, factor: float) -> tuple[dict[str, ln.ArrayConfig], ln.PvHistory]:
    """The installation and history after a synthetic swap: the hours up to until frozen, the later power on renamed
    sensors, scaled by factor, as if a new device had delivered it."""
    swap = importlib.import_module("noxcast.swap")
    new = {s: f"{s}_new" for arr in arrays.values() for s in arr.sensors}
    frozen = {sid: [swap.freeze(site, sid, history, ln.Segment(until, resume, arr.sensors, arr.limits, arr.caps))]
              for sid, arr in arrays.items()}

    def later(stats: dict[str, dict[float, float | None]]) -> dict[str, dict[float, float | None]]:
        return {new[s]: {t: v * factor * history.factors.get(s, 1.0) for t, v in series.items()
                         if t >= resume and v is not None} for s, series in stats.items() if s in new}

    def kept(sets: dict[str, frozenset]) -> dict[str, frozenset]:
        return {sid: frozenset(t for t in hours if t >= resume) for sid, hours in sets.items()}
    live = ln.PvHistory(later(history.power), later(history.power_min), later(history.power_max), {},
                        history.air_temp, history.satellite_ghi, kept(history.full), kept(history.charge),
                        kept(history.held), kept(history.unsure), history.snowfall, output=kept(history.output))
    after = {sid: dataclasses.replace(
        arr, sensors=tuple(new[s] for s in arr.sensors), limits=tuple((new[s], w) for s, w in arr.limits),
        caps=tuple((tuple(new[s] for s in cap[0]), *cap[1:]) for cap in arr.caps),
        segments=swap.segments(frozen[sid])) for sid, arr in arrays.items()}
    return after, swap.merge(live, frozen)


def synthetic_swap(site: solar.Site, arrays: dict[str, ln.ArrayConfig], history: ln.PvHistory, now: float,
                   exclusions: list[tuple[float, float]], tz: ZoneInfo, day: date, factor: float | None) -> dict:
    """Learning and hold-out with and without a synthetic swap on day (see --swap)."""
    until = datetime.combine(day, datetime.min.time(), tz).timestamp()
    resume = datetime.combine(date.fromordinal(day.toordinal() + 1), datetime.min.time(), tz).timestamp()
    factor = factor or (0.95 if any(arr.limits or arr.caps for arr in arrays.values()) else 1.05)
    after, swapped = _swapped(site, arrays, history, until, resume, factor)
    # the reference leaves the swap day out too, so that both learn from the same hours
    plain = exclusions + [(until, resume)]
    day_of = backtest.local_day(tz)
    out: dict = {"swap": day.isoformat(), "factor": factor, "arrays": {}}
    before = ln.learn_arrays(site, arrays, history, now, plain)
    learned = ln.learn_arrays(site, after, swapped, now, exclusions)
    hold = {"reference": backtest.holdout(site, arrays, history, now, plain, day_of),
            "swapped": backtest.holdout(site, after, swapped, now, exclusions, day_of)}
    for sid, arr in arrays.items():
        a, b = before[sid], learned[sid]
        common = set(a.cells) & set(b.cells)
        moved = sorted(abs(a.cells[k] - b.cells[k]) for k in common)

        def error(name: str, sid: str = sid) -> float | None:
            r = hold[name]["arrays"].get(sid, {})
            return r.get("holdout", {}).get("hour_error") if r.get("status") == backtest.OK else None
        out["arrays"][arr.name] = {
            "efficiency": {"reference": round(a.efficiency, 4), "swapped": round(b.efficiency, 4),
                           "expected": round(a.efficiency * factor, 4)},
            "ratios": b.segment_ratios,
            "cells": {"reference": len(a.cells), "swapped": len(b.cells), "common": len(common),
                      "mean_moved": round(sum(moved) / len(moved), 4) if moved else None,
                      "p90_moved": round(moved[int(0.9 * (len(moved) - 1))], 4) if moved else None},
            "holdout_hour_error": {"reference": error("reference"), "swapped": error("swapped")},
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest of the PV model on exported data")
    parser.add_argument("--config", required=True)
    parser.add_argument("--statistics", required=True)
    parser.add_argument("--satellite")
    parser.add_argument("--temperature")
    parser.add_argument("--archive")
    parser.add_argument("--fine")
    parser.add_argument("--now")
    parser.add_argument("--scorecard", action="store_true")
    parser.add_argument("--swap")
    parser.add_argument("--swap-factor", type=float)
    args = parser.parse_args()

    config = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    loc = config["location"]
    tz = ZoneInfo(loc.get("timezone", "UTC"))
    site = solar.Site(loc["latitude"], loc["longitude"], loc.get("altitude", 0.0))
    arrays = dict(_array(a, tz) for a in config["arrays"])
    caps = [(tuple(c["arrays"]), float(c["limit_w"])) for c in config.get("output_limits", ())]
    arrays = {sid: dataclasses.replace(arr, caps=tuple((tuple(s for a in members for s in arrays[a].sensors), w)
                                                       for members, w in caps if sid in members))
              for sid, arr in arrays.items()}
    # as installation.limited_arrays and forecast_caps: a limit over one array alone caps it, the others the sum
    limits = {sid: arr.limit_w for sid, arr in arrays.items() if arr.limit_w is not None}
    shared = [(members, w) for members, w in caps if len(members) > 1]
    raw = json.loads(pathlib.Path(args.statistics).read_text(encoding="utf-8"))
    curtailment = config.get("curtailment", {})
    socs = _statistics({s: raw[s] for s in curtailment.get("soc_sensors", []) if s in raw}, "max")
    # the hour ending at the label is the mean of the hour before; temperatures are instants: mean of two
    satellite = {t - 3600: max(v, 0.0) for t, v in _hourly(args.satellite, "shortwave_radiation").items()}
    instant = _hourly(args.temperature, "temperature_2m")
    air = {t: (v + instant[t + 3600]) / 2 for t, v in instant.items() if t + 3600 in instant}
    snowfall = {t - 3600: max(v, 0.0) for t, v in _snowfall(args.temperature).items()}
    soc_full = float(curtailment.get("soc_full_pct", 95))
    # without the battery configuration every array counts as curtailed while the highest state of charge is full
    full = frozenset(t for t, v in ln.highest(socs).items() if v >= soc_full)
    history = ln.PvHistory(_statistics(raw, "mean"), _statistics(raw, "min"), _statistics(raw, "max"), {},
                           air or None, satellite or None, {arr.subentry_id: full for arr in arrays.values()},
                           snowfall=snowfall or None)
    last = max(t for s in arrays.values() for sensor in s.sensors for t in history.power.get(sensor, {}))
    now = datetime.fromisoformat(args.now).timestamp() if args.now else last + 3600
    exclusions = [(datetime.combine(date.fromisoformat(a), datetime.min.time(), tz).timestamp(),
                   datetime.combine(date.fromisoformat(b), datetime.max.time(), tz).timestamp())
                  for a, b in curtailment.get("exclude_periods", [])]
    stored = json.loads(pathlib.Path(args.archive).read_text(encoding="utf-8")) if args.archive else {}
    days = archive.from_store(stored) if "days" in stored else stored

    if args.swap:
        print(json.dumps(synthetic_swap(site, arrays, history, now, exclusions, tz, date.fromisoformat(args.swap),
                                        args.swap_factor), indent=2))
        return
    start = time.monotonic()
    learned = ln.learn_arrays(site, arrays, history, now, exclusions)
    learning = time.monotonic() - start
    result = backtest.run(site, arrays, learned, history, days, now, exclusions, tz, limits=limits,
                          caps=shared)
    if args.fine:
        fine = json.loads(pathlib.Path(args.fine).read_text(encoding="utf-8"))
        fine = fine.get("data", fine).get("days", {})
        result["fine_holdout"] = backtest.fine_holdout(site, arrays, history, fine, now, exclusions, tz)
    names = {sid: arr.name for sid, arr in arrays.items()}
    if args.scorecard:
        print(scorecard_table(result["holdout"], names))
        return
    print(json.dumps({"names": names, "seconds": {"learning": round(learning, 1),
                                                   "backtest": round(time.monotonic() - start - learning, 1)}, **result},
                     indent=2))


if __name__ == "__main__":
    main()
