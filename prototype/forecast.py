"""Day-ahead forecast: clear-sky geometry + learned beam transmission + weather irradiance.

Usage: python forecast.py [ha|openmeteo]   (default: weather.source from config)
"""
import json
import sys
import urllib.request

import numpy as np
import pandas as pd
import pvlib

from common import DATA, OUT, HomeAssistant, array_frame, arrays, load_config, location, poa_components

cfg = load_config()
loc = location(cfg)
tz = cfg["location"]["timezone"]
source = sys.argv[1] if len(sys.argv) > 1 else cfg["weather"]["source"]
power = pd.read_pickle(DATA / "power.pkl")
soc_path = DATA / "soc.pkl"
soc = pd.read_pickle(soc_path).max(axis=1) if soc_path.exists() else None
ha = HomeAssistant(cfg)
now = pd.Timestamp.now(tz=tz)

# --- 1. Learn efficiency and beam transmission per local hour from the last ~4 weeks ---
# (sun path similar to the coming days; a full model would use the sun-position map instead)
learned = {}
for arr in arrays(cfg):
    d, eta = array_frame(loc, arr, power, cfg, soc)
    w = d[(d.index >= now - pd.Timedelta("28D")) & (d.beam * arr.peak_w / 1000 > 30)].copy()
    w["tr"] = ((w.p / eta - arr.peak_w * w["diff"] / 1000) / (arr.peak_w * w.beam / 1000)).clip(0, 1)
    w["h"] = w.index.tz_convert(tz).hour
    tr = w.groupby("h").tr.agg(lambda x: x.quantile(0.9) if len(x) >= 3 else np.nan)
    learned[arr.name] = (eta, tr)
    print(f"{arr.name}: efficiency {eta:.3f}, beam transmission per hour {tr.round(2).to_dict()}")

# --- 2. Weather: hourly means of the preceding hour, re-indexed to hour start ---
if source == "openmeteo":
    lat, lon = cfg["location"]["latitude"], cfg["location"]["longitude"]
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,cloud_cover"
           "&models=icon_seamless&timezone=UTC&forecast_days=3")
    om = pd.DataFrame(json.load(urllib.request.urlopen(url))["hourly"])
    wx = om.set_index(pd.to_datetime(om.time, utc=True) - pd.Timedelta("1h"))
    ghi, dni, dhi = wx.shortwave_radiation, wx.direct_normal_irradiance, wx.diffuse_radiation
    clouds = wx.cloud_cover
else:
    fc = ha.weather_hourly(cfg["weather"]["entity"])
    fc.index = pd.to_datetime(fc.datetime, utc=True) - pd.Timedelta("1h")
    ghi, dni, dhi = fc.sun_irradiance.astype(float), None, None
    clouds = fc.cloud_coverage

t10 = pd.date_range(ghi.index.min(), ghi.index.max() + pd.Timedelta("1h"), freq="10min", inclusive="left")
up = (loc.get_solarposition(t10).elevation > 0).astype(float)
ghi10 = ghi.reindex(t10, method="ffill") * up
if dni is None:  # split global irradiance into direct and diffuse
    erbs = pvlib.irradiance.erbs(ghi10, loc.get_solarposition(t10).zenith, t10)
    dni10, dhi10 = erbs.dni.fillna(0), erbs.dhi.fillna(0)
else:
    dni10, dhi10 = dni.reindex(t10, method="ffill") * up, dhi.reindex(t10, method="ffill") * up

out = {}
for arr in arrays(cfg):
    eta, tr = learned[arr.name]
    beam, diff = poa_components(loc, t10, dni10, dhi10, ghi10, arr)
    h = pd.DataFrame({"beam": beam, "diff": diff}).resample("1h").mean()
    T = h.index.tz_convert(tz).hour.map(lambda x: tr.get(x, 1.0)).fillna(1.0).to_numpy()
    out[arr.name] = arr.peak_w * eta * (h.beam * T + h["diff"]) / 1e6       # kWh per hour
out = pd.DataFrame(out)
out["total"] = out.sum(axis=1)
out["clouds"] = clouds.reindex(out.index)
out.index = out.index.tz_convert(tz)

# --- 3. Actuals today and optional comparison forecast from another integration ---
actual = ha.statistics([s for a in arrays(cfg) for s in a.sensors], now.normalize().isoformat()).sum(axis=1) / 1000
actual.index = actual.index.tz_convert(tz)
compare = {}
if cfg.get("compare"):
    states = {s["entity_id"]: s for s in ha.call({"type": "get_states"})}
    for key, ent in cfg["compare"].items():
        a = states[ent]["attributes"]
        compare[key] = (float(states[ent]["state"]), a.get("hours") or a.get("hourly_forecast") or {})

OUT.mkdir(exist_ok=True)
out.to_csv(OUT / f"forecast_{source}_{now:%Y%m%d_%H%M}.csv")
for day, key in zip(sorted(set(out.index.date))[:3], ["today", "tomorrow", "day_after"]):
    o = out[out.index.date == day].copy()
    o["actual"] = actual.reindex(o.index)
    line = f"\n{day}: Noxcast {o.total.sum():.2f} kWh (" + ", ".join(f"{a.name} {o[a.name].sum():.2f}" for a in arrays(cfg)) + ")"
    if key in compare:
        o["compare"] = [float(compare[key][1].get(f"{t.hour:02d}:00", 0) or 0) for t in o.index]
        line += f" | compare {compare[key][0]:.2f} kWh"
    print(line + f" | actual so far {actual[actual.index.date == day].sum():.2f} kWh")
    print(o[o.total > 0.005].round(3).to_string())
