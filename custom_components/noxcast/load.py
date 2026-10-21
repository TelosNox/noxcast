"""Load forecast: base-load profile, short-term correction and backtest.

Pure functions on hourly series {hour start (UTC ts): mean W}; no Home Assistant imports, so the
model can be tested and replayed offline.

Base load = house load minus every configured consumer. It is learned as the median per hour of
day and day type, so rare runs and spikes do not bend the profile. Fixed consumers are forecast by
their kind: "profile" uses their own median profile, "program" learned start rates and the power
curve of a run (a washer that runs one evening in three is not a median of zero), "session" the
on-probability per hour and, while on, the learned remaining duration for that time of day,
"regulated" (air conditioning, heat pump) a hinge of daily energy against the daily mean outside
temperature, spread over the day by its mean hourly shape.
Flexible consumers only count while they run, since the forecast is meant to choose their moment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
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
KIND_PROFILE, KIND_PROGRAM, KIND_SESSION, KIND_REGULATED = "profile", "program", "session", "regulated"
REGULATED_DAYS = 365         # learning window of regulated consumers: the relation needs the seasons
REGULATED_MIN_DAYS = 14
REGULATED_MIN_R2 = 0.3       # weaker relations are dropped; the consumer falls back to its profile
RUN_DAMPING_H = 2.0          # a running regulated consumer: its current power fades into the model
ON_W = 30.0                  # hourly mean above which a program or session counts as on
MAX_RUN_HOURS = 12           # longest program run that is learned as one run


class DayTypeZone(tzinfo):
    """A time zone that also knows the day type of some dates, from a day-type sensor.

    Every part of the model asks `day_type(ts, tz)`; carrying the known dates in the zone keeps all
    signatures as they are. Dates without an entry fall back to workday / weekend.
    """

    def __init__(self, zone: tzinfo, types: dict[date, str]) -> None:
        self.zone, self.types = zone, types

    def utcoffset(self, dt):
        return self.zone.utcoffset(dt.replace(tzinfo=self.zone) if dt is not None else None)

    def dst(self, dt):
        return self.zone.dst(dt.replace(tzinfo=self.zone) if dt is not None else None)

    def tzname(self, dt):
        return self.zone.tzname(dt.replace(tzinfo=self.zone) if dt is not None else None)

    def fromutc(self, dt):
        return self.zone.fromutc(dt.replace(tzinfo=self.zone)).replace(tzinfo=self)


def day_type(ts: float, tz: tzinfo) -> str:
    local = datetime.fromtimestamp(ts, tz)
    types = getattr(tz, "types", None)
    if types and local.date() in types:
        return types[local.date()]
    return WEEKEND if local.weekday() >= 5 else WORKDAY


DAY_TYPE_STATES = {"on": WORKDAY, "off": WEEKEND}   # e.g. the Workday integration: a holiday counts as weekend


def day_types_from_states(changes: list[tuple[float, str]], tz: tzinfo, start: float, end: float) -> dict[date, str]:
    """Day type of each local date in [start, end): the sensor's state at local noon.

    changes: (time, state) sorted by time, including the state at `start`. On / off map to workday /
    weekend; other states (e.g. an input_select "home office") become day types of their own.
    """
    out: dict[date, str] = {}
    day = datetime.fromtimestamp(start, tz).replace(hour=12, minute=0, second=0, microsecond=0)
    i, state = 0, None
    while day.timestamp() < end:
        noon = day.timestamp()
        while i < len(changes) and changes[i][0] <= noon:
            state = changes[i][1]
            i += 1
        if noon >= start and state not in (None, "unknown", "unavailable", ""):
            out[day.date()] = DAY_TYPE_STATES.get(state, state)
        day = (day + timedelta(days=1, hours=2)).replace(hour=12)
    return out


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
            groups.setdefault(day_type(t, tz), [[] for _ in range(24)])[local_hour(t, tz)].append(w)
            seen.add(datetime.fromtimestamp(t, tz).date())
    every = [[w for g in groups.values() for w in g[h]] for h in range(24)]
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


def blocks(series: dict[float, float], threshold: float = ON_W) -> list[list[tuple[float, float]]]:
    """Runs of consecutive hours at or above the threshold, as [(hour start, W), ...]."""
    out: list[list[tuple[float, float]]] = []
    for t in sorted(series):
        if series[t] < threshold:
            continue
        if out and out[-1][-1][0] == t - H:
            out[-1].append((t, series[t]))
        else:
            out.append([(t, series[t])])
    return out


def _days_by_type(series: dict[float, float], tz: tzinfo) -> dict[str, int]:
    days: dict[str, set] = {WORKDAY: set(), WEEKEND: set()}
    for t in series:
        days.setdefault(day_type(t, tz), set()).add(datetime.fromtimestamp(t, tz).date())
    return {k: len(v) for k, v in days.items()}


def _local_midnight(ts: float, tz: tzinfo) -> float:
    return datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@dataclass
class ProgramModel:
    """A fixed program: expected starts per local hour and day type, the most runs a day, a run's power curve."""

    starts: dict[str, list[float]]     # day type -> starts per day in each local hour
    runs_max: dict[str, int]           # day type -> most runs seen on one day
    shape: list[float]                 # mean W in hour 0, 1, ... after a start
    runs: int = 0

    def as_dict(self) -> dict:
        return {"starts": self.starts, "runs_max": self.runs_max, "shape": self.shape, "runs": self.runs}

    @classmethod
    def from_dict(cls, d: dict) -> ProgramModel:
        return cls({k: list(v) for k, v in d["starts"].items()}, dict(d["runs_max"]), list(d["shape"]), int(d.get("runs", 0)))


def learn_program(series: dict[float, float], tz: tzinfo, until: float, days: int = PROFILE_DAYS) -> ProgramModel | None:
    """Start rates, runs per day and power curve from the runs in [until - days, until)."""
    start = until - days * 86400
    window = {t: w for t, w in series.items() if start <= t < until}
    runs = [b[:MAX_RUN_HOURS] for b in blocks(window)]
    if not runs:
        return None
    ndays = _days_by_type(window, tz)
    counts: dict[str, list[int]] = {k: [0] * 24 for k in ndays}
    per_day: dict = {}
    for b in runs:
        t0 = b[0][0]
        k = day_type(t0, tz)
        counts.setdefault(k, [0] * 24)[local_hour(t0, tz)] += 1
        d = (datetime.fromtimestamp(t0, tz).date(), k)
        per_day[d] = per_day.get(d, 0) + 1
    runs_max = {k: 0 for k in counts}
    for (_, k), n in per_day.items():
        runs_max[k] = max(runs_max[k], n)
    length = max(len(b) for b in runs)
    shape = [round(sum(b[i][1] for b in runs if i < len(b)) / len(runs), 1) for i in range(length)]
    starts = {k: [round(c / ndays[k], 4) if ndays.get(k) else 0.0 for c in counts[k]] for k in counts}
    return ProgramModel(starts, runs_max, shape, len(runs))


def runs_since(series: dict[float, float], since: float, now: float | None = None, running: bool = False) -> int:
    """Runs that started at or after `since`.

    With `running`, a run that reaches into the last full hour before now is the one still going;
    it is left out, since the forecast counts a running run itself.
    """
    runs = [b for b in blocks(series) if b[0][0] >= since]
    if running and now is not None and runs and runs[-1][-1][0] == now - now % H - H:
        runs.pop()
    return len(runs)


def program_expected(model: ProgramModel, tz: tzinfo, now: float, end: float, runs_today: int,
                     running: bool = False) -> dict[float, float]:
    """Expected mean W per hour from runs that have not started yet.

    Each hour carries its start rate times the run's power curve. Per day the expected starts are
    capped at the most runs seen on one day, minus the runs already done today (a running run
    counts as done), so a third run is not predicted after two.
    """
    hour = now - now % H
    by_day: dict[float, list[tuple[float, float]]] = {}
    t = hour
    while t < end:
        if not (running and t == hour):
            share = (t + H - now) / H if t == hour else 1.0
            rate = model.starts.get(day_type(t, tz), [0.0] * 24)[local_hour(t, tz)] * share
            by_day.setdefault(_local_midnight(t + H / 2, tz), []).append((t, rate))
        t += H
    today = _local_midnight(now, tz)
    out: dict[float, float] = {}
    for day, rates in by_day.items():
        cap = model.runs_max.get(day_type(day + H, tz), 0)
        if day == today:
            cap -= runs_today + (1 if running else 0)
        total = sum(r for _, r in rates)
        scale = min(1.0, max(cap, 0) / total) if total > 0 else 0.0
        for ts, rate in rates:
            for i, w in enumerate(model.shape):
                if ts + i * H < end:
                    out[ts + i * H] = out.get(ts + i * H, 0.0) + rate * scale * w
    return out


@dataclass
class SessionModel:
    """A session (PC, TV): on-probability per hour and day type, power while on, remaining hours by hour of day."""

    p_on: dict[str, list[float | None]]
    power: float
    remaining: list[float | None]      # median hours still on, counting the hour it is seen on

    def as_dict(self) -> dict:
        return {"p_on": self.p_on, "power": self.power, "remaining": self.remaining}

    @classmethod
    def from_dict(cls, d: dict) -> SessionModel:
        return cls({k: list(v) for k, v in d["p_on"].items()}, float(d["power"]), list(d["remaining"]))


def learn_session(series: dict[float, float], tz: tzinfo, until: float, days: int = PROFILE_DAYS) -> SessionModel | None:
    """On-probability, power while on and remaining hours from [until - days, until)."""
    start = until - days * 86400
    window = {t: w for t, w in series.items() if start <= t < until}
    on = [w for w in window.values() if w >= ON_W]
    if not on:
        return None
    seen: dict[str, list[list[int]]] = {WORKDAY: [[0, 0] for _ in range(24)], WEEKEND: [[0, 0] for _ in range(24)]}
    for t, w in window.items():
        cell = seen.setdefault(day_type(t, tz), [[0, 0] for _ in range(24)])[local_hour(t, tz)]
        cell[0] += 1
        cell[1] += w >= ON_W
    rest: list[list[int]] = [[] for _ in range(24)]
    for b in blocks(window):
        for i, (t, _) in enumerate(b):
            rest[local_hour(t, tz)].append(len(b) - i)
    return SessionModel(
        p_on={k: [round(c[1] / c[0], 3) if c[0] >= MIN_SAMPLES else None for c in g] for k, g in seen.items()},
        power=round(median(on), 1),
        remaining=[float(median(r)) if r else None for r in rest])


def session_expected(model: SessionModel, tz: tzinfo, now: float, end: float, on_power: float | None) -> dict[float, float]:
    """Mean W per hour: while on, the current power for the learned remaining hours; otherwise probability × power."""
    hour = now - now % H
    on_until = hour
    if on_power is not None and on_power >= ON_W:
        on_until = hour + (model.remaining[local_hour(hour, tz)] or 1.0) * H
    out: dict[float, float] = {}
    t = hour
    while t < end:
        if t < on_until:
            out[t] = on_power * min(1.0, (on_until - t) / H)
        else:
            dt, h = day_type(t, tz), local_hour(t, tz)
            p = model.p_on.get(dt, [None] * 24)[h]
            if p is None:
                p = model.p_on.get(WEEKEND if dt == WORKDAY else WORKDAY, [None] * 24)[h] or 0.0
            out[t] = p * model.power
        t += H
    return out


@dataclass
class RegulatedModel:
    """Daily energy = base + slope × degrees beyond the threshold (below it for heating, above for cooling)."""

    mode: str                          # "heat" or "cool"
    threshold: float                   # °C
    base_wh: float                     # Wh per day at and beyond the comfortable side
    slope_wh: float                    # Wh per day and K
    shape: list[float]                 # share of the daily energy per local hour, sums to 1
    r2: float
    days: int

    def day_wh(self, temp: float) -> float:
        k = max(0.0, self.threshold - temp) if self.mode == "heat" else max(0.0, temp - self.threshold)
        return self.base_wh + self.slope_wh * k

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> RegulatedModel:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def daily_means(series: dict[float, float], tz: tzinfo) -> dict:
    """Mean per local date, from days with at least 20 hours."""
    days: dict = {}
    for t, v in series.items():
        if v is not None:
            days.setdefault(datetime.fromtimestamp(t, tz).date(), []).append(v)
    return {d: sum(v) / len(v) for d, v in days.items() if len(v) >= 20}


def learn_regulated(series: dict[float, float], air_temp: dict[float, float], tz: tzinfo, until: float,
                    days: int = REGULATED_DAYS) -> RegulatedModel | None:
    """Hinge fit of daily energy against daily mean temperature, heating or cooling, whichever fits better."""
    start = until - days * 86400
    window = {t: w for t, w in series.items() if start <= t < until}
    energy = {d: w * 24 for d, w in daily_means(window, tz).items()}
    temps = daily_means({t: v for t, v in air_temp.items() if start <= t < until}, tz)
    pairs = [(temps[d], e) for d, e in energy.items() if d in temps]
    if len(pairs) < REGULATED_MIN_DAYS:
        return None
    ys = [e for _, e in pairs]
    my = sum(ys) / len(ys)
    total = sum((y - my) ** 2 for y in ys)
    if total <= 0:
        return None
    best = None
    for mode in ("heat", "cool"):
        for threshold in [x / 2 for x in range(0, 61)]:          # 0 .. 30 °C in 0.5 K steps
            xs = [max(0.0, threshold - t) if mode == "heat" else max(0.0, t - threshold) for t, _ in pairs]
            mx = sum(xs) / len(xs)
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx <= 0 or sum(1 for x in xs if x > 0) < 5:
                continue
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            if slope <= 0:
                continue
            intercept = my - slope * mx
            r2 = 1 - sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys)) / total
            if best is None or r2 > best[0]:
                best = (r2, mode, threshold, max(intercept, 0.0), slope)
    if best is None or best[0] < REGULATED_MIN_R2:
        return None
    hours = [0.0] * 24
    for t, w in window.items():
        hours[local_hour(t, tz)] += max(w, 0.0)
    s = sum(hours)
    shape = [round(h / s, 4) for h in hours] if s > 0 else [round(1 / 24, 4)] * 24
    r2, mode, threshold, intercept, slope = best
    return RegulatedModel(mode, threshold, round(intercept, 1), round(slope, 1), shape, round(r2, 3), len(pairs))


def regulated_expected(model: RegulatedModel, tz: tzinfo, now: float, end: float, day_temp: dict,
                       run_power: float | None = None) -> dict[float, float]:
    """Mean W per hour: the day's energy from its mean forecast temperature, spread by the hourly shape.

    A running consumer starts at its current power, fading into the model over a few hours.
    Days without a temperature use the latest known one.
    """
    out: dict[float, float] = {}
    hour = now - now % H
    known = [day_temp[d] for d in sorted(day_temp)]
    t = hour
    while t < end:
        d = datetime.fromtimestamp(t, tz).date()
        temp = day_temp.get(d, known[-1] if known else None)
        v = model.day_wh(temp) * model.shape[local_hour(t, tz)] if temp is not None else 0.0
        if run_power is not None:
            w = math.exp(-(t - hour) / H / RUN_DAMPING_H)
            v = w * run_power + (1 - w) * v
        out[t] = v
        t += H
    return out


@dataclass
class Consumer:
    name: str
    timing: str
    profile: Profile
    kind: str = KIND_PROFILE
    program: ProgramModel | None = None
    session: SessionModel | None = None
    runs_today: int = 0
    regulated: RegulatedModel | None = None
    day_temp: dict = field(default_factory=dict)        # local date -> mean air temperature (forecast)


@dataclass
class Run:
    """A consumer running now: its power and, for a program, the minutes it still runs."""

    power: float
    minutes: float | None = None


def run_value(run: Run, now: float, t: float) -> float | None:
    """Mean W of hour t (for the part from now on) caused by a run; None after the run.

    Without a known remaining time the run only counts for the running hour.
    """
    start = max(t, now)
    if t + H <= now:
        return None
    if run.minutes is None:
        return run.power if t <= now < t + H else None
    stop = now + run.minutes * 60
    if stop <= t and not t <= now < t + H:
        return None
    overlap = max(0.0, min(t + H, stop) - start)
    return run.power * overlap / (t + H - start)


@dataclass
class LoadForecast:
    hours: dict[float, float]              # Wh per hour start, from the running hour on
    base: dict[float, float]
    consumers: dict[str, dict[float, float]]
    k: float


def forecast(base_profile: Profile, consumers: list[Consumer], tz: tzinfo, now: float, end: float, k: float,
             running: dict[str, Run] | None = None) -> LoadForecast:
    """Hourly load from the running hour until `end`; values are mean W of the part from now on.

    running: consumers running now. A program with a known remaining time counts with its power
    until it ends; otherwise a run counts for the running hour only. Fixed consumers add what their
    kind expects (profile, program starts still to come, session), flexible ones count zero.
    """
    running = running or {}
    hours: dict[float, float] = {}
    base: dict[float, float] = {}
    parts: dict[str, dict[float, float]] = {c.name: {} for c in consumers}
    expected: dict[str, dict[float, float]] = {}       # fixed programs and sessions by their learned model
    for c in consumers:
        if c.timing != TIMING_FIXED:
            continue
        run = running.get(c.name)
        if c.kind == KIND_PROGRAM and c.program:
            expected[c.name] = program_expected(c.program, tz, now, end, c.runs_today, run is not None)
        elif c.kind == KIND_SESSION and c.session:
            expected[c.name] = session_expected(c.session, tz, now, end, run.power if run else None)
        elif c.kind == KIND_REGULATED and c.regulated:
            expected[c.name] = regulated_expected(c.regulated, tz, now, end, c.day_temp, run.power if run else None)
    t = now - now % H
    while t < end:
        b = base_profile.at(t, tz)
        if b is not None:
            base[t] = round(b * k_at(k, now, t), 1)
        for c in consumers:
            run = running.get(c.name)
            v = run_value(run, now, t) if run else None
            if c.name in expected:                 # a session's run is part of its expectation
                v = expected[c.name].get(t, 0.0) + (0.0 if c.kind in (KIND_SESSION, KIND_REGULATED) else v or 0.0)
            elif v is not None:
                pass
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
             tz: tzinfo, now: float, days: int = BACKTEST_DAYS, kinds: dict[str, str] | None = None,
             air_temp: dict[float, float] | None = None) -> dict:
    """Replay past days: forecast the rest of the day at fixed hours with the knowledge of that moment.

    Regulated consumers get the measured temperature of the day, i.e. a perfect temperature forecast.
    Compares the model with a naive mean of the last 7 days per hour (what the day card did) and
    returns mean absolute errors of the remaining energy in kWh, plus the model's mean signed error.
    """
    names = list(consumer_series)
    base = base_series(load, [consumer_series[n] for n in names])
    total = load
    today = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    errors, naive_errors, signed = [], [], []
    for d in range(days, 0, -1):
        day = today - timedelta(days=d)
        day_start = day.timestamp()
        day_end = (day + timedelta(days=1)).timestamp()
        bp = learn_profile(base, tz, day_start)
        cons = [learned_consumer(n, timing.get(n, TIMING_FIXED), (kinds or {}).get(n, KIND_PROFILE),
                                 consumer_series[n], tz, day_start, air_temp) for n in names]
        day_temp = daily_means({t: v for t, v in (air_temp or {}).items() if day_start <= t < day_end}, tz)
        for c in cons:
            c.day_temp = day_temp
        naive = _naive_profile(total, tz, day_start)
        for issue_hour in BACKTEST_ISSUE_HOURS:
            issue = day.replace(hour=issue_hour).timestamp()
            actual_hours = [total.get(t) for t in _hours(issue, day_end)]
            if not actual_hours or any(v is None for v in actual_hours):
                continue
            actual = sum(actual_hours)
            k = correction(base, bp, tz, issue)
            for c in cons:
                c.runs_today = runs_since({t: w for t, w in consumer_series[c.name].items() if day_start <= t < issue}, day_start)
            fc = forecast(bp, cons, tz, issue, day_end, k)
            if len(fc.hours) < len(actual_hours):
                continue
            errors.append(abs(remaining(fc.hours, issue, day_end) - actual))
            signed.append(remaining(fc.hours, issue, day_end) - actual)
            nv = [naive.get(local_hour(t, tz)) for t in _hours(issue, day_end)]
            if all(v is not None for v in nv):
                naive_errors.append(abs(sum(nv) - actual))
    def mae(e: list[float]) -> float | None:
        return round(sum(e) / len(e) / 1000, 3) if e else None
    # the median profile minimises the absolute error, but misses rare runs completely; the bias shows
    # whether the energy over many days comes out right, which matters for the battery
    return {"days": days, "cases": len(errors), "mae_kwh": mae(errors), "naive_mae_kwh": mae(naive_errors),
            "bias_kwh": round(sum(signed) / len(signed) / 1000, 3) if signed else None,
            "issue_hours": list(BACKTEST_ISSUE_HOURS)}


def learned_consumer(name: str, timing: str, kind: str, series: dict[float, float], tz: tzinfo, until: float,
                     air_temp: dict[float, float] | None = None) -> Consumer:
    """A consumer with its profile and, for fixed programs, sessions and regulated ones, the model of its kind."""
    c = Consumer(name, timing, learn_profile(series, tz, until), kind)
    if timing == TIMING_FIXED and kind == KIND_PROGRAM:
        c.program = learn_program(series, tz, until)
    elif timing == TIMING_FIXED and kind == KIND_SESSION:
        c.session = learn_session(series, tz, until)
    elif timing == TIMING_FIXED and kind == KIND_REGULATED and air_temp:
        c.regulated = learn_regulated(series, air_temp, tz, until)
    return c


def _hours(start: float, end: float) -> list[float]:
    return [start + i * H for i in range(int((end - start) // H))]


def _naive_profile(series: dict[float, float], tz: tzinfo, until: float) -> dict[int, float]:
    groups: dict[int, list[float]] = {}
    for t, w in series.items():
        if until - 7 * 86400 <= t < until:
            groups.setdefault(local_hour(t, tz), []).append(w)
    return {h: sum(v) / len(v) for h, v in groups.items()}
