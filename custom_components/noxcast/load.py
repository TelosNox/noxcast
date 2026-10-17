"""Load forecast: base-load profile, short-term correction and backtest.

Pure functions on hourly series {hour start (UTC ts): mean W}; no Home Assistant imports, so the
model can be tested and replayed offline.

Base load = house load minus every configured consumer. It is learned as the median per hour of
day and day type, so rare runs and spikes do not bend the profile. Consumers are forecast from
their own profile when their timing is fixed; flexible consumers only count while they run,
since the forecast is meant to choose their moment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from statistics import median

H = 3600.0
PROFILE_DAYS = 42            # learning window of the profiles
MIN_SAMPLES = 3              # values a cell needs; otherwise the profile over all days is used
K_HOURS = 4                  # hours compared for the short-term correction
K_MIN, K_MAX = 0.35, 1.3     # clamp of the correction factor
K_DECAY_H = 2.0              # e-folding time of the correction towards 1 (half-life ~1.4 h)
BACKTEST_DAYS = 14
BACKTEST_ISSUE_HOURS = (6, 12, 18)
WORKDAY, WEEKEND = "workday", "weekend"

TIMING_FIXED, TIMING_FLEXIBLE = "fixed", "flexible"


def day_type(ts: float, tz: tzinfo) -> str:
    return WEEKEND if datetime.fromtimestamp(ts, tz).weekday() >= 5 else WORKDAY


def local_hour(ts: float, tz: tzinfo) -> int:
    return datetime.fromtimestamp(ts + H / 2, tz).hour       # middle of the hour, safe across DST changes


def base_series(load: dict[float, float], consumers: list[dict[float, float]]) -> dict[float, float]:
    """House load minus all consumers per hour.

    Before a consumer's first value it did not exist yet and counts as 0; later gaps drop the hour,
    since its share is unknown.
    """
    firsts = [min(c) if c else math.inf for c in consumers]
    out = {}
    for t, w in load.items():
        total, ok = w, True
        for c, first in zip(consumers, firsts):
            if t in c:
                total -= c[t]
            elif t >= first:
                ok = False
                break
        if ok:
            out[t] = max(total, 0.0)
    return out


@dataclass
class Profile:
    """Median W per (day type, local hour), with the all-days median as fallback."""

    cells: dict[str, list[float | None]] = field(default_factory=dict)
    fallback: list[float | None] = field(default_factory=lambda: [None] * 24)
    days: int = 0

    def at(self, ts: float, tz: tzinfo) -> float | None:
        h = local_hour(ts, tz)
        v = self.cells.get(day_type(ts, tz), [None] * 24)[h]
        return v if v is not None else self.fallback[h]

    def as_dict(self) -> dict:
        return {"cells": self.cells, "fallback": self.fallback, "days": self.days}

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        return cls(cells={k: list(v) for k, v in d.get("cells", {}).items()},
                   fallback=list(d.get("fallback", [None] * 24)), days=int(d.get("days", 0)))


def learn_profile(series: dict[float, float], tz: tzinfo, until: float, days: int = PROFILE_DAYS) -> Profile:
    """Profile from the hours in [until - days, until)."""
    start = until - days * 86400
    groups: dict[str, list[list[float]]] = {WORKDAY: [[] for _ in range(24)], WEEKEND: [[] for _ in range(24)]}
    seen = set()
    for t, w in series.items():
        if start <= t < until and w is not None:
            groups[day_type(t, tz)][local_hour(t, tz)].append(w)
            seen.add(datetime.fromtimestamp(t, tz).date())
    every = [groups[WORKDAY][h] + groups[WEEKEND][h] for h in range(24)]
    return Profile(
        cells={k: [round(median(v), 1) if len(v) >= MIN_SAMPLES else None for v in g] for k, g in groups.items()},
        fallback=[round(median(v), 1) if len(v) >= MIN_SAMPLES else None for v in every],
        days=len(seen))


def correction(recent: dict[float, float], profile: Profile, tz: tzinfo, now: float) -> float:
    """Ratio of the measured base load of the last full hours to the profile of the same hours."""
    hour = now - now % H
    actual = expected = 0.0
    n = 0
    for i in range(1, K_HOURS + 1):
        t = hour - i * H
        p = profile.at(t, tz)
        if t in recent and p is not None:
            actual += recent[t]
            expected += p
            n += 1
    if n < 2 or expected <= 0:
        return 1.0
    return min(K_MAX, max(K_MIN, actual / expected))


def k_at(k: float, now: float, t: float) -> float:
    """Correction for hour t: the current trend for the next hours, the profile further ahead."""
    dist = max(0.0, (t + H / 2 - now) / H)
    return 1 + (k - 1) * math.exp(-dist / K_DECAY_H)


@dataclass
class Consumer:
    name: str
    timing: str
    profile: Profile


@dataclass
class LoadForecast:
    hours: dict[float, float]              # Wh per hour start, from the running hour on
    base: dict[float, float]
    consumers: dict[str, dict[float, float]]
    k: float


def forecast(base_profile: Profile, consumers: list[Consumer], tz: tzinfo, now: float, end: float, k: float,
             running: dict[str, float] | None = None) -> LoadForecast:
    """Hourly load from the running hour until `end`.

    running: current power (W) per consumer name. A running consumer keeps it for the rest of the
    running hour; fixed consumers otherwise follow their profile, flexible ones count zero.
    """
    running = running or {}
    hours: dict[float, float] = {}
    base: dict[float, float] = {}
    parts: dict[str, dict[float, float]] = {c.name: {} for c in consumers}
    t = now - now % H
    while t < end:
        b = base_profile.at(t, tz)
        if b is not None:
            base[t] = round(b * k_at(k, now, t), 1)
        for c in consumers:
            if t <= now < t + H and c.name in running:
                v = running[c.name]
            elif c.timing == TIMING_FIXED:
                v = c.profile.at(t, tz) or 0.0
            else:
                v = 0.0
            parts[c.name][t] = round(v, 1)
        if t in base:
            hours[t] = round(base[t] + sum(p[t] for p in parts.values()), 1)
        t += H
    return LoadForecast(hours, base, parts, round(k, 3))


def remaining(hours: dict[float, float], now: float, end: float) -> float:
    """Wh from now until end; the running hour counts with its remaining share."""
    total = 0.0
    for t, wh in hours.items():
        if t + H <= now or t >= end:
            continue
        total += wh * min(1.0, (t + H - now) / H)
    return total


def backtest(load: dict[float, float], consumer_series: dict[str, dict[float, float]], timing: dict[str, str],
             tz: tzinfo, now: float, days: int = BACKTEST_DAYS) -> dict:
    """Replay past days: forecast the rest of the day at fixed hours with the knowledge of that moment.

    Compares the model with a naive mean of the last 7 days per hour (what the day card did) and
    returns mean absolute errors of the remaining energy in kWh.
    """
    names = list(consumer_series)
    base = base_series(load, [consumer_series[n] for n in names])
    total = load
    today = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    errors, naive_errors = [], []
    for d in range(days, 0, -1):
        day = today - timedelta(days=d)
        day_start = day.timestamp()
        day_end = (day + timedelta(days=1)).timestamp()
        bp = learn_profile(base, tz, day_start)
        cons = [Consumer(n, timing.get(n, TIMING_FIXED), learn_profile(consumer_series[n], tz, day_start))
                for n in names]
        naive = _naive_profile(total, tz, day_start)
        for issue_hour in BACKTEST_ISSUE_HOURS:
            issue = day.replace(hour=issue_hour).timestamp()
            actual_hours = [total.get(t) for t in _hours(issue, day_end)]
            if not actual_hours or any(v is None for v in actual_hours):
                continue
            actual = sum(actual_hours)
            k = correction(base, bp, tz, issue)
            fc = forecast(bp, cons, tz, issue, day_end, k)
            if len(fc.hours) < len(actual_hours):
                continue
            errors.append(abs(remaining(fc.hours, issue, day_end) - actual))
            nv = [naive.get(local_hour(t, tz)) for t in _hours(issue, day_end)]
            if all(v is not None for v in nv):
                naive_errors.append(abs(sum(nv) - actual))
    def mae(e: list[float]) -> float | None:
        return round(sum(e) / len(e) / 1000, 3) if e else None
    return {"days": days, "cases": len(errors), "mae_kwh": mae(errors), "naive_mae_kwh": mae(naive_errors),
            "issue_hours": list(BACKTEST_ISSUE_HOURS)}


def _hours(start: float, end: float) -> list[float]:
    return [start + i * H for i in range(int((end - start) // H))]


def _naive_profile(series: dict[float, float], tz: tzinfo, until: float) -> dict[int, float]:
    groups: dict[int, list[float]] = {}
    for t, w in series.items():
        if until - 7 * 86400 <= t < until:
            groups.setdefault(local_hour(t, tz), []).append(w)
    return {h: sum(v) / len(v) for h, v in groups.items()}
