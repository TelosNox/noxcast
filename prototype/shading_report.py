"""Shading per month and hour of day, plus a beam-transmission map over sun azimuth × elevation.

Shading in an hour = 1 - (90th percentile of actual / expected clear-sky power). The 90th
percentile picks the typical clear day of that month and hour; clouds and occasional
curtailment fall below it.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common import DATA, OUT, array_frame, arrays, load_config, location

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

cfg = load_config()
loc = location(cfg)
tz = cfg["location"]["timezone"]
power = pd.read_pickle(DATA / "power.pkl")
soc_path = DATA / "soc.pkl"
soc = pd.read_pickle(soc_path).max(axis=1) if soc_path.exists() else None
OUT.mkdir(exist_ok=True)

arrs = arrays(cfg)
fig_m, axes_m = plt.subplots(len(arrs), 1, figsize=(13, 5 * len(arrs)), squeeze=False)
fig_s, axes_s = plt.subplots(1, len(arrs), figsize=(7.5 * len(arrs), 5.5), squeeze=False)

for ax_m, ax_s, arr in zip(axes_m[:, 0], axes_s[0], arrs):
    d, eta = array_frame(loc, arr, power, cfg, soc)
    print(f"\n=== {arr.name}: data from {d.index.min():%Y-%m-%d}, efficiency {eta:.3f}")

    # --- month × hour table ---
    m = d.copy()
    m["exp"] = eta * arr.peak_w * (m.beam + m["diff"]) / 1000
    m = m[m.exp > 25]
    lt = m.index.tz_convert(tz)
    m["mon"] = [f"{t.year}-{t.month:02d}" for t in lt]
    m["h"] = lt.hour
    m["r"] = (m.p / m.exp).clip(0, 1.2)
    agg = m.groupby(["mon", "h"]).agg(r=("r", lambda x: x.quantile(0.9)), exp=("exp", "mean"))
    loss = (1 - agg.r.clip(upper=1)).unstack() * 100
    energy = (agg.exp * (1 - agg.r.clip(upper=1))).groupby(level=0).sum() / agg.exp.groupby(level=0).sum() * 100
    loss.index = [f"{MONTHS[int(i[5:]) - 1]} {i[2:4]}" for i in loss.index]
    print(loss.round(0).to_string(na_rep="."))
    im = ax_m.imshow(loss.values, cmap="RdYlGn_r", vmin=0, vmax=100, aspect="auto")
    ax_m.set_xticks(range(len(loss.columns)), loss.columns)
    ax_m.set_yticks(range(len(loss.index)), [f"{i}  ({v:.0f} %)" for i, v in zip(loss.index, energy.values)])
    for (i, j), v in np.ndenumerate(loss.values):
        if not np.isnan(v):
            ax_m.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8)
    ax_m.set_title(f"{arr.name} – shading per hour in % (in brackets: energy loss on clear days)")
    ax_m.set_xlabel("Hour (local time, start)")

    # --- beam transmission over sun position ---
    s = d[(d.beam * arr.peak_w / 1000 > 60) & d.el.notna()].copy()
    s["tr"] = ((s.p / eta - arr.peak_w * s["diff"] / 1000) / (arr.peak_w * s.beam / 1000)).clip(-0.2, 1.3)
    s["azb"] = (s.az // 10) * 10 + 5
    s["elb"] = (s.el // 5) * 5 + 2.5
    g = s.groupby(["elb", "azb"]).tr.agg(lambda x: x.quantile(0.9) if len(x) >= 4 else np.nan).unstack()
    ims = ax_s.pcolormesh(g.columns, g.index, g.values, cmap="RdYlGn", vmin=0, vmax=1.1, shading="nearest")
    ax_s.set_title(f"{arr.name} (efficiency {eta:.2f})")
    ax_s.set_xlabel("Sun azimuth °")
    ax_s.set_ylabel("Sun elevation °")
    g.to_csv(OUT / f"transmission_{arr.name.lower()}.csv")

fig_m.colorbar(im, ax=axes_m.ravel().tolist(), label="Shading %", shrink=0.6)
fig_m.savefig(OUT / "shading_month_hour.png", dpi=110, bbox_inches="tight")
fig_s.colorbar(ims, ax=axes_s.ravel().tolist(), label="Share of direct light reaching the array")
fig_s.savefig(OUT / "shading_map.png", dpi=110, bbox_inches="tight")
print(f"\nWritten to {OUT}")
