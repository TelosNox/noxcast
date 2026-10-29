"""Battery simulation: expected PV against the load forecast, hour by hour, until tomorrow's charge start.

Pure functions, no Home Assistant imports. All batteries are simulated as one storage: capacities
add up, limits and efficiencies are weighted by capacity, power limits add up.

Energy flow per hour (Wh, for the part of the hour from now on):
  surplus = PV - load. A surplus charges the battery up to the upper limit and the charge power;
  what does not fit is "lost" (fed in or curtailed, depending on the system). A deficit
  discharges down to the lower limit plus reserve and the discharge power; the rest comes from
  the grid. Losses: the round-trip efficiency is split evenly into charging and discharging.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

H = 3600.0
COUPLING_DC, COUPLING_AC = "dc", "ac"
DEFAULT_EFFICIENCY = {COUPLING_DC: 0.95, COUPLING_AC: 0.90}   # round trip


@dataclass(frozen=True)
class Battery:
    capacity_wh: float
    soc: float | None                 # 0..1, None = unknown
    max_soc: float = 1.0              # 0..1
    min_soc: float = 0.0              # 0..1, including the reserve
    efficiency: float = 0.95          # round trip
    max_charge_w: float | None = None
    max_discharge_w: float | None = None


def combine(batteries: list[Battery]) -> Battery | None:
    """All batteries as one storage; batteries without a known state of charge are left out."""
    known = [b for b in batteries if b.capacity_wh > 0 and b.soc is not None]
    cap = sum(b.capacity_wh for b in known)
    if not cap:
        return None

    def weighted(attr: str) -> float:
        return sum(getattr(b, attr) * b.capacity_wh for b in known) / cap

    def power(attr: str) -> float | None:
        values = [getattr(b, attr) for b in known]
        return None if any(v is None for v in values) else sum(values)

    return Battery(cap, weighted("soc"), weighted("max_soc"), weighted("min_soc"), weighted("efficiency"),
                   power("max_charge_w"), power("max_discharge_w"))


@dataclass
class Simulation:
    points: list[tuple[float, float]] = field(default_factory=list)   # (time, state of charge 0..1)
    full_at: float | None = None          # first time the upper limit is reached
    empty_at: float | None = None         # first time the lower limit is reached with load left over
    lost_from: float | None = None        # first hour with surplus that does not fit
    lost_wh: float = 0.0                  # surplus that does not fit (fed in or curtailed)
    charged_wh: float = 0.0               # surplus taken by the battery (before losses)
    grid_wh: float = 0.0                  # load not covered by PV or battery
    surplus_wh: float = 0.0               # PV above load
    max_soc: float = 0.0
    max_soc_at: float | None = None
    hours: dict[float, dict] = field(default_factory=dict)              # per hour: surplus, battery, lost, grid, soc

    def soc_at(self, ts: float) -> float | None:
        """State of charge at a time, linear between the simulated points."""
        if not self.points:
            return None
        if ts <= self.points[0][0]:
            return self.points[0][1]
        for (a, fa), (b, fb) in zip(self.points, self.points[1:]):
            if a <= ts <= b:
                return fa + (fb - fa) * (ts - a) / (b - a or 1)
        return self.points[-1][1]


def simulate(battery: Battery, pv: dict[float, float], load: dict[float, float], now: float, end: float) -> Simulation:
    """Simulate from now until end. pv and load: Wh per hour start (mean W of the hour)."""
    sim = Simulation()
    cap = battery.capacity_wh
    e = (battery.soc if battery.soc is not None else battery.min_soc) * cap
    e_max, e_min = battery.max_soc * cap, battery.min_soc * cap
    eta = math.sqrt(max(0.01, min(1.0, battery.efficiency)))
    sim.points.append((now, e / cap))
    sim.max_soc, sim.max_soc_at = e / cap, now
    t = now - now % H
    while t < end:
        start = max(t, now)
        share = (min(t + H, end) - start) / H
        if share <= 0:
            t += H
            continue
        net = (pv.get(t, 0.0) - load.get(t, 0.0)) * share
        hour = {"surplus": round(max(net, 0.0), 1), "battery": 0.0, "lost": 0.0, "grid": 0.0}
        if net > 0:
            sim.surplus_wh += net
            room = max(e_max - e, 0.0) / eta                       # surplus the battery can still take
            limit = battery.max_charge_w * share if battery.max_charge_w else math.inf
            take = min(net, room, limit)
            e += take * eta
            lost = net - take
            if take < net and room <= limit and sim.full_at is None:
                sim.full_at = start + (take / net) * (min(t + H, end) - start)
            if lost > 0.5 and sim.lost_from is None:
                sim.lost_from = start
            sim.charged_wh += take
            sim.lost_wh += lost
            hour.update(battery=round(take, 1), lost=round(lost, 1))
        elif net < 0:
            need = -net
            avail = max(e - e_min, 0.0) * eta                      # load the battery can still cover
            limit = battery.max_discharge_w * share if battery.max_discharge_w else math.inf
            give = min(need, avail, limit)
            e -= give / eta
            if give < need and avail <= limit and sim.empty_at is None:
                sim.empty_at = start + (give / need) * (min(t + H, end) - start)
            sim.grid_wh += need - give
            hour.update(battery=round(-give, 1), grid=round(need - give, 1))
        point = min(t + H, end)
        sim.points.append((point, e / cap))
        if e / cap > sim.max_soc + 1e-9:
            sim.max_soc, sim.max_soc_at = e / cap, point
        hour["soc"] = round(e / cap, 4)
        sim.hours[t] = hour
        t += H
    if sim.full_at is not None and sim.max_soc_at is not None and sim.full_at < sim.max_soc_at:
        sim.max_soc_at = sim.full_at
    return sim


def charge_start(pv: dict[float, float], load: dict[float, float], start: float, end: float) -> float | None:
    """First hour in [start, end) with PV above the load: from here the battery charges again."""
    for t in sorted(pv):
        if start <= t < end and pv[t] > load.get(t, math.inf):
            return t
    return None


def day_simulation(batteries: list[Battery], pv: dict[float, float], load: dict[float, float] | None,
                   band_hours: dict[float, tuple[float, float]] | None, now: float, day_end: float) -> dict | None:
    """All batteries as one storage, simulated with expected PV against the load forecast until tomorrow's charge start.

    {"sim", "charge_start", "storage", "day_end"}, plus with a PV band "band": the surplus lost today and
    the time full with PV at P10 and at P90. None without batteries or load forecast.
    """
    if not batteries or load is None:
        return None
    storage = combine(batteries)
    if storage is None:
        return None
    tomorrow_end = day_end + 86400
    start = charge_start(pv, load, day_end, tomorrow_end) or tomorrow_end
    sim = simulate(storage, pv, load, now, start)
    out = {"sim": sim, "charge_start": start, "storage": storage, "day_end": day_end}
    if band_hours:
        # the same simulation with PV at P10 and at P90: how much surplus is lost, and when full, in a weak and a strong case
        sims = [simulate(storage, {**pv, **{t: lh[i] for t, lh in band_hours.items()}}, load, now, start) for i in (0, 1)]
        out["band"] = {"lost_today": [round(sum(h["lost"] for t, h in s.hours.items() if t < day_end) / 1000, 3)
                                      for s in sims],
                       "full_at": [s.full_at for s in sims]}
    return out


def soc_days(series: list[tuple[float, list[tuple[float, float, float]]]], day_of) -> list[dict]:
    """Daily lowest and highest state of charge of all batteries as one storage, from hourly statistics.

    series: per battery (capacity_wh, [(hour start, min 0..1, max 0..1)]). Hours missing for one
    battery are left out, so the weighting stays the same. day_of: hour start -> day key.
    """
    cap = sum(c for c, _ in series)
    if not cap:
        return []
    by_hour: dict[float, list[tuple[float, float, float]]] = {}
    for c, rows in series:
        for t, lo, hi in rows:
            by_hour.setdefault(t, []).append((c, lo, hi))
    days: dict[str, dict] = {}
    for t in sorted(by_hour):
        parts = by_hour[t]
        if len(parts) < len(series):
            continue
        lo = sum(c * v for c, v, _ in parts) / cap
        hi = sum(c * v for c, _, v in parts) / cap
        d = days.setdefault(day_of(t), {"date": day_of(t), "min": lo, "max": hi, "hours": 0})
        d["min"], d["max"], d["hours"] = min(d["min"], lo), max(d["max"], hi), d["hours"] + 1
    return [{**d, "min": round(d["min"], 4), "max": round(d["max"], 4)} for d in days.values()]


# ---------------------------------------------------------------- prediction against what happened

LIMIT_TOLERANCE = 0.02          # within 2 percentage points of a limit counts as reached (as in the review)
PREDICTION_DAYS = 30            # predictions kept


def prediction(sim: Simulation, storage: Battery, made: float, day_end: float, until: float) -> dict:
    """What the morning simulation says about the day: when full, when empty, the highest state of charge.

    "empty" is looked for until `until` (the next charge start), since the battery usually runs empty
    in the night; "full" and the highest state of charge belong to the day itself.
    """
    today = [s for t, s in sim.points if t < day_end]
    return {"made": made, "until": until,
            "full_at": sim.full_at if sim.full_at is not None and sim.full_at < day_end else None,
            "empty_at": sim.empty_at if sim.empty_at is not None and sim.empty_at < until else None,
            "max_soc": round(max(today), 4) if today else None,
            "max": storage.max_soc, "min": storage.min_soc}


def remember(log: dict[str, dict], day: date, battery: dict, now: float) -> dict[str, dict] | None:
    """The log with the day's first prediction (see day_simulation) added and old days dropped; None if it has one."""
    key = day.isoformat()
    if key in log:
        return None
    log = {**log, key: prediction(battery["sim"], battery["storage"], now, battery["day_end"], battery["charge_start"])}
    oldest = (day - timedelta(days=PREDICTION_DAYS)).isoformat()
    return {k: v for k, v in log.items() if k >= oldest}


def combined_soc(series: list[tuple[float, dict[float, float]]]) -> dict[float, float]:
    """All batteries as one storage: state of charge 0..1 weighted by capacity, where every battery has a value.

    series: per battery (capacity_wh, {time: state of charge 0..1}).
    """
    cap = sum(c for c, _ in series)
    if not cap or not series:
        return {}
    common = set(series[0][1])
    for _, s in series[1:]:
        common &= set(s)
    return {t: sum(c * s[t] for c, s in series) / cap for t in sorted(common)}


def outcome(soc: dict[float, float], pred: dict, day_end: float, step: float = 300.0) -> dict | None:
    """What happened to a prediction: first time full and empty in the same windows, highest state of charge.

    soc: combined state of charge per period start (5-minute statistics). `complete` tells whether the
    data already cover the whole window, i.e. whether "not empty" is final.
    """
    made, until = pred["made"], pred["until"]
    day = sorted((t, s) for t, s in soc.items() if made <= t < day_end)
    if not day:
        return None
    full = next((t for t, s in day if s >= pred["max"] - LIMIT_TOLERANCE), None)
    empty = next((t for t, s in sorted(soc.items()) if made <= t < until and s <= pred["min"] + LIMIT_TOLERANCE), None)
    last = max(soc)
    return {"full_at": full, "empty_at": empty, "max_soc": round(max(s for _, s in day), 4),
            "complete": last + step >= until, "day_complete": last + step >= day_end}


def prediction_review(log: dict[str, dict], soc: dict[float, float], day_end_of) -> list[dict]:
    """Logged predictions with their outcome, oldest first; days without data are left out.

    day_end_of: date key -> end of that local day (timestamp).
    """
    out = []
    for key in sorted(log):
        pred = log[key]
        result = outcome(soc, pred, day_end_of(key))
        if result is not None:
            out.append({"date": key, "predicted": pred, "actual": result})
    return out
