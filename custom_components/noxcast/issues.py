"""Repairs issues: problems a user should see without opening the panel's diagnostics.

Each issue exists exactly while its condition holds; it is recomputed after every forecast update
(which also follows every learning run) and removed when the entry unloads.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from . import views
from .const import CONF_BATTERY_POWER, CONF_SOC_SENSORS, DOMAIN
from .coordinator import NoxcastConfigEntry

NO_ARRAYS = "no_arrays"
MISSING_SENSORS = "missing_sensors"
WEATHER_UNAVAILABLE = "weather_unavailable"
ARRAY_CHECK = "array_check_"          # + subentry id
# only the hints that are configuration errors for sure, as the panel's "Check" badge: direction and season
# can come from shade as well, so they stay in the panel and the diagnostics
CERTAIN_HINTS = {"peak", "high"}


def _battery_names(entry: NoxcastConfigEntry) -> dict[str, str]:
    return {sid: entry.subentries[sid].title for sid in entry.runtime_data.batteries if sid in entry.subentries}


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
    return out


@callback
def async_update_issues(hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
    """Create the issues that hold now and delete the ones that no longer do."""
    wanted = wanted_issues(hass, entry)
    for issue_id, (severity, placeholders) in wanted.items():
        ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=False, is_persistent=False, severity=severity,
                              translation_key=ARRAY_CHECK.rstrip("_") if issue_id.startswith(ARRAY_CHECK) else issue_id,
                              translation_placeholders=placeholders)
    async_clear_issues(hass, keep=set(wanted))


@callback
def async_clear_issues(hass: HomeAssistant, keep: set[str] | None = None) -> None:
    """Delete Noxcast's issues except those in keep."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id not in (keep or set()):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
