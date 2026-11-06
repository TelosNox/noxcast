"""Readable snapshots of computed results, compared with a tolerance.

A snapshot is a JSON file in tests/snapshots/. A test hands its result to check(); the first run, or a run with
`--update-snapshots`, writes the file, later runs compare against it. A deliberate change is made by updating the
file and committing it: the diff of the pull request then shows which numbers change and by how much.

Floats are kept to SIGNIFICANT digits and compared with a relative tolerance, since bit-exact floats differ
between platforms (Windows and Linux libm) and would make a snapshot pass on one and fail on the other. Whole
numbers and values from EXACT on (timestamps) stay exact: no platform noise there, and an hour off must show.
Types count: 1, 1.0 and true differ.
"""
from __future__ import annotations

import dataclasses
import json
import math
import pathlib
from datetime import date, datetime

DIR = pathlib.Path(__file__).parent / "snapshots"
SIGNIFICANT = 6
REL_TOL = 1e-4          # far above platform noise, far below any change a model or payload change makes
ABS_TOL = 1e-6
EXACT = 1e8             # from here on (UTC seconds) values are neither rounded nor compared with a tolerance
SHOWN = 40              # differences listed in a failure
INLINE = 120            # a list or dict up to this many characters stays on one line
SAME = "= previous"     # in a sequence of states: this part did not change since the previous one


def plain(o):
    """A result as plain JSON values: dataclasses and objects as dicts, keys as strings, floats rounded."""
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"__": type(o).__name__, **{f.name: plain(getattr(o, f.name)) for f in dataclasses.fields(o)}}
    if isinstance(o, dict):
        return {k if isinstance(k, str) else _key(k): plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [plain(v) for v in o]
    if isinstance(o, (set, frozenset)):
        return sorted(plain(v) for v in o)
    if isinstance(o, float):
        return o if not math.isfinite(o) or o.is_integer() or abs(o) >= EXACT else float(f"{o:.{SIGNIFICANT}g}")
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if o is None or isinstance(o, (str, int, bool)):
        return o
    if hasattr(o, "__dict__"):
        return {"__": type(o).__name__, **{k: plain(v) for k, v in vars(o).items()}}
    return repr(o)


def _key(k) -> str:
    if isinstance(k, float) and k.is_integer():
        return str(int(k))
    return str(plain(k))


def differences(old, new, path: str = "") -> list[str]:
    """Where new differs from old beyond the tolerance, as "path: old -> new"; dict key order counts."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = [] if list(old) == list(new) or set(old) != set(new) else [f"{path or '.'}: key order changed"]
        for k in old.keys() | new.keys():
            if k not in new:
                out.append(f"{path}.{k}: removed")
            elif k not in old:
                out.append(f"{path}.{k}: added {_short(new[k])}")
            else:
                out += differences(old[k], new[k], f"{path}.{k}")
        return sorted(out)
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{path}: length {len(old)} -> {len(new)}"]
        return [d for i, (a, b) in enumerate(zip(old, new)) for d in differences(a, b, f"{path}[{i}]")]
    if type(old) is not type(new):
        return [f"{path}: {_short(old)} -> {_short(new)} (type)"]
    if isinstance(old, float):
        if math.isnan(old) and math.isnan(new):
            return []
        exact = max(abs(old), abs(new)) >= EXACT
        same = old == new if exact else math.isclose(old, new, rel_tol=REL_TOL, abs_tol=ABS_TOL)
        return [] if same else [f"{path}: {old} -> {new}"]
    return [] if old == new else [f"{path}: {_short(old)} -> {_short(new)}"]


def steps(states: list, depth: int = 6) -> list:
    """A sequence of states, each part that did not change since the previous state marked SAME (down to depth
    levels of dicts, e.g. one archived day), to keep the file small."""
    return [states[0], *(_same(prev, cur, depth) for prev, cur in zip(states, states[1:]))]


def _identical(a, b) -> bool:
    # key order and types count (1, 1.0 and true differ), as in differences()
    return json.dumps(a) == json.dumps(b)


def _same(prev, cur, depth: int):
    if _identical(prev, cur):
        return SAME
    if depth and isinstance(prev, dict) and isinstance(cur, dict):
        return {k: _same(prev[k], v, depth - 1) if k in prev else v for k, v in cur.items()}
    if depth and isinstance(prev, list) and isinstance(cur, list) and all(isinstance(x, dict) for x in prev + cur):
        # a list of days grows by a day: items line up by their date (kept in the marker, so order and duplicates
        # still show) if every date is there once; else by position
        dates = [[x.get("date") for x in prev], [x.get("date") for x in cur]]
        if all(None not in d and len(set(d)) == len(d) for d in dates):
            before = {x["date"]: x for x in prev}
            return [x if x["date"] not in before else f"{SAME} {x['date']}" if _identical(before[x["date"]], x)
                    else _same(before[x["date"]], x, depth - 1) for x in cur]
        if len(prev) == len(cur):
            return [_same(a, b, depth - 1) for a, b in zip(prev, cur)]
    return cur


def dumps(v, indent: str = "") -> str:
    """JSON with one line per item; short lists and dicts, and lists of plain values, inline: diffs stay readable."""
    flat = json.dumps(v, ensure_ascii=False)
    if not isinstance(v, (list, dict)) or len(flat) <= INLINE or (
            isinstance(v, list) and all(not isinstance(x, (list, dict)) for x in v)):
        return flat
    inner = indent + " "
    if isinstance(v, list):
        items = [inner + dumps(x, inner) for x in v]
        return "[\n" + ",\n".join(items) + "\n" + indent + "]"
    items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {dumps(x, inner)}" for k, x in v.items()]
    return "{\n" + ",\n".join(items) + "\n" + indent + "}"


def _short(v) -> str:
    s = json.dumps(v)
    return s if len(s) <= 80 else s[:77] + "..."


def check(name: str, value, update: bool) -> None:
    """Compare a result with tests/snapshots/<name>.json, or write the file if update is set."""
    path = DIR / f"{name}.json"
    new = json.loads(json.dumps(plain(value)))
    if not update and not path.exists():
        raise AssertionError(f"{path.name} is missing: run pytest --update-snapshots and commit the file")
    if update:
        DIR.mkdir(exist_ok=True)
        path.write_text(dumps(new) + "\n", encoding="utf-8", newline="\n")
        return
    diff = differences(json.loads(path.read_text(encoding="utf-8")), new)
    if diff:
        more = f"\n... and {len(diff) - SHOWN} more" if len(diff) > SHOWN else ""
        raise AssertionError(f"{len(diff)} differences to {path.name} (run pytest --update-snapshots if they are "
                             f"intended, then commit the file):\n" + "\n".join(diff[:SHOWN]) + more)
