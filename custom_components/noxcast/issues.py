"""Repairs issues: problems a user should see without opening the panel's diagnostics.

Each issue exists exactly while its condition holds; it is recomputed after every forecast update
(which also follows every learning run) and removed when the entry is deleted. A reload or restart keeps
them, because deleting an issue also forgets that the user ignored it.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from . import plateau as pt
from . import systems as sy
from . import views
from .const import CONF_BATTERY_POWER, CONF_SOC_SENSORS, DOMAIN
from .coordinator import NoxcastConfigEntry

NO_ARRAYS = "no_arrays"
MISSING_SENSORS = "missing_sensors"
WEATHER_UNAVAILABLE = "weather_unavailable"
ARRAY_CHECK = "array_check_"          # + subentry id
LIMIT_SUGGESTION = "limit_suggestion_"  # + subentry id; fixable (repairs.py)
BATTERY_WITHOUT_PV = "battery_without_pv_"  # + battery subentry id
# only the hints that are configuration errors for sure, as the panel's "Check" badge: direction and season
# can come from shade as well, so they stay in the panel and the diagnostics
CERTAIN_HINTS = {"peak", "high"}


def _battery_names(entry: NoxcastConfigEntry) -> dict[str, str]:
    return {sid: entry.subentries[sid].title for sid in entry.runtime_data.batteries if sid in entry.subentries}


def limit_suggestions(entry: NoxcastConfigEntry) -> dict[str, dict]:
    """Array id -> suggested limit (plateau.suggestion) for arrays without one that the user has not rejected."""
    c = entry.runtime_data
    limits = c.limits
    out = {}
    for sid, arr in c.arrays.items():
        sensors = list(arr.sensors)
        if any(i.limit_w is not None for i in c.installation.inputs_of(sid)):
            continue
        suggested = pt.suggestion(sensors, limits.get("detected") or {})
        if suggested and not pt.dismissed(sensors, suggested, limits.get("dismissed") or {}):
            out[sid] = suggested
    return out


def _limit_placeholders(name: str, sensors: list[str], suggested: dict) -> dict[str, str]:
    return {"name": name, "sensors": ", ".join(sensors), "peak": f"{suggested['peak_w']:.0f}",
            "limit": f"{suggested['limit_w']:.0f}", "flat_days": str(suggested["flat_days"]),
            "window_days": str(suggested["window_days"])}


@callback
def wanted_issues(hass: HomeAssistant, entry: NoxcastConfigEntry) -> dict[str, tuple[ir.IssueSeverity, dict[str, str]]]:
    """Issue id -> (severity, placeholders) for every problem that holds now."""
    c = entry.runtime_data
    out: dict[str, tuple[ir.IssueSeverity, dict[str, str]]] = {}
    if not c.arrays:
        out[NO_ARRAYS] = (ir.IssueSeverity.WARNING, {})
    # before Home Assistant has started, entities of other integrations may still be on their way
    if hass.is_running:
        roles = views.sensor_roles(c.arrays, entry.data.get(CONF_SOC_SENSORS), c.house_load, c.consumers,
                                   c.batteries, _battery_names(entry))
        # the card's grid and battery power sensors are no inputs of the forecast, so sensor_roles leaves them out
        shown = [c.grid] + [d.get(CONF_BATTERY_POWER) for d in c.batteries.values()]
        ids = {entity_id for entity_id, _, _ in roles} | {e for e in shown if e}
        missing = sorted(e for e in ids if hass.states.get(e) is None)
        if missing:
            out[MISSING_SENSORS] = (ir.IssueSeverity.ERROR, {"sensors": ", ".join(missing)})
    # the stored forecast stands in for a day; a failed update means even that is too old
    if not c.last_update_success:
        out[WEATHER_UNAVAILABLE] = (ir.IssueSeverity.ERROR, {})
    for sid, arr in c.arrays.items():
        la = c.learned.get(sid)
        if la is not None and CERTAIN_HINTS & set(la.hints):
            out[ARRAY_CHECK + sid] = (ir.IssueSeverity.WARNING, {"name": arr.name})
    # a battery before the output of a device with PV, but no array in its system: the migration could not know
    # which arrays sit behind it (until answered, it computes as before)
    subs = {sid: (sub.subentry_type, sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()}
    for sid in sy.battery_without_pv(subs):
        system = c.systems.get(subs[sid][2].get("system") or "")
        out[BATTERY_WITHOUT_PV + sid] = (ir.IssueSeverity.WARNING, {"name": subs[sid][1],
                                                                    "system": system.name if system else ""})
    for sid, suggested in limit_suggestions(entry).items():
        arr = c.arrays[sid]
        out[LIMIT_SUGGESTION + sid] = (ir.IssueSeverity.WARNING,
                                       _limit_placeholders(arr.name, list(arr.sensors), suggested))
    return out


def _translation_key(issue_id: str) -> str:
    for prefix in (ARRAY_CHECK, LIMIT_SUGGESTION, BATTERY_WITHOUT_PV):
        if issue_id.startswith(prefix):
            return prefix.rstrip("_")
    return issue_id


@callback
def async_update_issues(hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
    """Create the issues that hold now and delete the ones that no longer do."""
    wanted = wanted_issues(hass, entry)
    keep = set(wanted)
    if not hass.is_running:               # sensors are not checked yet: an existing issue may still hold
        keep.add(MISSING_SENSORS)
    for issue_id, (severity, placeholders) in wanted.items():
        # the fix flow (repairs.py) finds the array and the values again from the issue's data
        limit = issue_id.startswith(LIMIT_SUGGESTION)
        data = {"entry_id": entry.entry_id, "subentry_id": issue_id.removeprefix(LIMIT_SUGGESTION)} if limit else None
        ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=limit, is_persistent=False, severity=severity,
                              translation_key=_translation_key(issue_id), translation_placeholders=placeholders,
                              data=data)
    async_clear_issues(hass, keep=keep)


@callback
def async_clear_issues(hass: HomeAssistant, keep: set[str] | None = None) -> None:
    """Delete Noxcast's issues except those in keep."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id not in (keep or set()):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
