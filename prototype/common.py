"""Shared helpers for the Noxcast prototype: configuration, Home Assistant access, PV physics."""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pvlib
import websocket

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "out"


@dataclass
class Array:
    name: str
    sensors: list[str]
    tilt: float
    azimuth: float
    peak_w: float


def load_config(path: pathlib.Path | None = None) -> dict:
    path = path or HERE / "config.local.json"
    if not path.exists():
        raise SystemExit(f"Missing {path.name} – copy config.example.json and fill in your values.")
    return json.loads(path.read_text(encoding="utf-8"))


def arrays(cfg: dict) -> list[Array]:
    return [Array(**a) for a in cfg["arrays"]]


def location(cfg: dict) -> pvlib.location.Location:
    loc = cfg["location"]
    return pvlib.location.Location(loc["latitude"], loc["longitude"], loc["timezone"], loc.get("altitude", 0))


class HomeAssistant:
    """Minimal synchronous WebSocket client for one-shot commands."""

    def __init__(self, cfg: dict):
        token_file = pathlib.Path(cfg["home_assistant"]["token_file"])
        if not token_file.is_absolute():
            token_file = HERE / token_file
        self.ws = websocket.create_connection(cfg["home_assistant"]["url"], timeout=120)
        self.ws.recv()
        self.ws.send(json.dumps({"type": "auth", "access_token": token_file.read_text().strip()}))
        if json.loads(self.ws.recv())["type"] != "auth_ok":
            raise SystemExit("Home Assistant authentication failed")
        self._id = 0

    def call(self, msg: dict) -> dict:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, **msg}))
        res = json.loads(self.ws.recv())
        if not res.get("success", False):
            raise RuntimeError(res.get("error"))
        return res["result"]

    def statistics(self, ids: list[str], start: str, kind: str = "mean", period: str = "hour") -> pd.DataFrame:
        res = self.call({"type": "recorder/statistics_during_period", "start_time": start,
                         "period": period, "statistic_ids": ids, "types": [kind]})
        return pd.DataFrame({k: pd.Series({pd.Timestamp(r["start"], unit="ms", tz="UTC"): r[kind] for r in v})
                             for k, v in res.items()})

    def weather_hourly(self, entity: str) -> pd.DataFrame:
        res = self.call({"type": "call_service", "domain": "weather", "service": "get_forecasts",
                         "service_data": {"type": "hourly"}, "target": {"entity_id": entity},
                         "return_response": True})
        return pd.DataFrame(res["response"][entity]["forecast"])


def poa_components(loc, times, dni, dhi, ghi, arr: Array) -> tuple[pd.Series, pd.Series]:
    """Effective beam (after incidence-angle losses) and diffuse plane-of-array irradiance in W/m²."""
    sp = loc.get_solarposition(times)
    poa = pvlib.irradiance.get_total_irradiance(arr.tilt, arr.azimuth, sp.apparent_zenith, sp.azimuth,
                                                dni, ghi, dhi, albedo=0.2)
    aoi = pvlib.irradiance.aoi(arr.tilt, arr.azimuth, sp.apparent_zenith, sp.azimuth)
    beam = (poa.poa_direct * pvlib.iam.physical(aoi)).fillna(0).clip(lower=0)
    return beam, poa.poa_diffuse.fillna(0).clip(lower=0)


def clearsky_hourly(loc, start, end, arr: Array) -> pd.DataFrame:
    """Hourly mean clear-sky beam/diffuse POA plus mean sun position, indexed by hour start (UTC).

    Computed at 10-minute resolution so an hourly mean matches the sun path within the hour.
    """
    t = pd.date_range(start, end, freq="10min", inclusive="left")
    sp = loc.get_solarposition(t)
    cs = loc.get_clearsky(t, model="simplified_solis", solar_position=sp)
    beam, diff = poa_components(loc, t, cs.dni, cs.dhi, cs.ghi, arr)
    return pd.DataFrame({"beam": beam, "diff": diff,
                         "az": sp.azimuth.where(sp.elevation > 0),
                         "el": sp.elevation.where(sp.elevation > 0)}).resample("1h").mean()


def learning_mask(index: pd.DatetimeIndex, cfg: dict, soc: pd.Series | None) -> np.ndarray:
    """True for hours usable for learning (no known curtailment)."""
    tz = cfg["location"]["timezone"]
    ok = np.ones(len(index), dtype=bool)
    for start, end in cfg.get("curtailment", {}).get("exclude_periods", []):
        ok &= ~((index >= pd.Timestamp(start, tz=tz)) & (index < pd.Timestamp(end, tz=tz) + pd.Timedelta("1D")))
    if soc is not None:
        full = cfg["curtailment"].get("soc_full_pct", 95)
        ok &= ~(soc.reindex(index) >= full).to_numpy()
    return ok


def array_frame(loc, arr: Array, power: pd.DataFrame, cfg: dict, soc: pd.Series | None) -> tuple[pd.DataFrame, float]:
    """Join measured power with clear-sky expectation, filter, and estimate system efficiency."""
    p = power[arr.sensors].sum(axis=1, min_count=len(arr.sensors))
    commissioned = p[p > 5].index.min()
    cs = clearsky_hourly(loc, p.index.min(), p.index.max() + pd.Timedelta("1h"), arr)
    d = cs.join(p.rename("p"), how="inner").dropna(subset=["p"])
    d = d[(d.index >= commissioned) & learning_mask(d.index, cfg, soc)]
    exp = arr.peak_w * (d.beam + d["diff"]) / 1000
    strong = exp > 0.4 * exp.max()
    eta = float((d.p[strong] / exp[strong]).quantile(0.95))
    return d, eta
