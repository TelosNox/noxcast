"""Backtest of the PV model on exported data: a model change must show here that it is better on unlearned days.

Runs the same functions as the integration (custom_components/noxcast/backtest.py) and prints the metrics as
JSON. To compare two model versions, run this script from both checkouts on the same files.

    python prototype/backtest.py --config config.local.json --statistics data/hourly.json \
        --satellite data/satellite.json --temperature data/temperature.json --archive data/archive.json

--config      as config.example.json: location (with timezone), arrays and curtailment; per array optionally
              id (the array id used in the archive), heating, temp_coeff (per K), valid_since (ISO date) and
              extra ([{tilt, azimuth, peak_w}]; peak_w of the array is that of its first plane).
--statistics  recorder/statistics_during_period answer: {sensor: [{start (ms), mean, min, max}]}, power in W.
--satellite   Open-Meteo archive answer (or its "hourly" block) with shortwave_radiation; optional.
--temperature Open-Meteo archive answer (or its "hourly" block) with temperature_2m; optional.
--archive     the day-ahead archive, as stored ({"days": ...}) or plain; optional.
--fine        the 5-minute archive, as stored (.storage/noxcast.<entry id>.fine) or plain {"days": ...}; optional.
              Adds "fine_holdout": would learning from it be better? Takes much longer than the rest.
--now         ISO time of the run, the reference for ageing; default: the last hour of the statistics.

All these files hold personal data and stay out of the repository (*.local.json, prototype/data/).
"""
from __future__ import annotations

import argparse
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
    return sid, ln.ArrayConfig(sid, cfg["name"], tuple(cfg["sensors"]), geometry, valid_since, cfg.get("temp_coeff"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest of the PV model on exported data")
    parser.add_argument("--config", required=True)
    parser.add_argument("--statistics", required=True)
    parser.add_argument("--satellite")
    parser.add_argument("--temperature")
    parser.add_argument("--archive")
    parser.add_argument("--fine")
    parser.add_argument("--now")
    args = parser.parse_args()

    config = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    loc = config["location"]
    tz = ZoneInfo(loc.get("timezone", "UTC"))
    site = solar.Site(loc["latitude"], loc["longitude"], loc.get("altitude", 0.0))
    arrays = dict(_array(a, tz) for a in config["arrays"])
    raw = json.loads(pathlib.Path(args.statistics).read_text(encoding="utf-8"))
    curtailment = config.get("curtailment", {})
    socs = _statistics({s: raw[s] for s in curtailment.get("soc_sensors", []) if s in raw}, "max")
    # the hour ending at the label is the mean of the hour before; temperatures are instants: mean of two
    satellite = {t - 3600: max(v, 0.0) for t, v in _hourly(args.satellite, "shortwave_radiation").items()}
    instant = _hourly(args.temperature, "temperature_2m")
    air = {t: (v + instant[t + 3600]) / 2 for t, v in instant.items() if t + 3600 in instant}
    history = ln.PvHistory(_statistics(raw, "mean"), _statistics(raw, "min"), _statistics(raw, "max"), {},
                           ln.highest(socs), air or None, satellite or None)
    last = max(t for s in arrays.values() for sensor in s.sensors for t in history.power.get(sensor, {}))
    now = datetime.fromisoformat(args.now).timestamp() if args.now else last + 3600
    soc_full = float(curtailment.get("soc_full_pct", 95))
    exclusions = [(datetime.combine(date.fromisoformat(a), datetime.min.time(), tz).timestamp(),
                   datetime.combine(date.fromisoformat(b), datetime.max.time(), tz).timestamp())
                  for a, b in curtailment.get("exclude_periods", [])]
    stored = json.loads(pathlib.Path(args.archive).read_text(encoding="utf-8")) if args.archive else {}
    days = archive.from_store(stored) if "days" in stored else stored

    start = time.monotonic()
    learned = ln.learn_arrays(site, arrays, history, now, soc_full, exclusions)
    learning = time.monotonic() - start
    result = backtest.run(site, arrays, learned, history, days, now, soc_full, exclusions, tz)
    if args.fine:
        fine = json.loads(pathlib.Path(args.fine).read_text(encoding="utf-8"))
        fine = fine.get("data", fine).get("days", {})
        result["fine_holdout"] = backtest.fine_holdout(site, arrays, history, fine, now, soc_full, exclusions, tz)
    names = {sid: arr.name for sid, arr in arrays.items()}
    print(json.dumps({"names": names, "seconds": {"learning": round(learning, 1),
                                                   "backtest": round(time.monotonic() - start - learning, 1)}, **result},
                     indent=2))


if __name__ == "__main__":
    main()
