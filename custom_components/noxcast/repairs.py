"""Fix flows for Repairs issues: the limit Noxcast suggests from flat measurements (issues.LIMIT_SUGGESTION).

Nothing changes until the user answers. "Set limit" and "Other value" write the limit into the array's settings,
as if entered under *Power limit* there; the entry then reloads with it. "There is no limit" keeps the array as it
is and stops the notice until the level the sensors stay at moves (plateau.dismissed).
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from . import issues
from .const import CONF_POWER_SENSORS, CONF_SENSOR_LIMITS

LIMIT = "limit_w"


class LimitSuggestionFlow(RepairsFlow):
    """Set limit / Other value / There is no limit."""

    def __init__(self, entry_id: str, subentry_id: str) -> None:
        self._entry_id = entry_id
        self._subentry_id = subentry_id

    def _current(self) -> tuple[Any, Any, dict] | None:
        """(entry, array subentry, suggestion) while the suggestion still holds."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or getattr(entry, "runtime_data", None) is None:
            return None
        sub = entry.subentries.get(self._subentry_id)
        suggested = issues.limit_suggestions(entry).get(self._subentry_id)
        return (entry, sub, suggested) if sub is not None and suggested else None

    def _placeholders(self, sub, suggested: dict) -> dict[str, str]:
        return issues._limit_placeholders(sub.title, list(sub.data[CONF_POWER_SENSORS]), suggested)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        current = self._current()
        if current is None:
            return self.async_abort(reason="limit_gone")
        _, sub, suggested = current
        return self.async_show_menu(step_id="init", menu_options=["set_limit", "other_value", "no_limit"],
                                    description_placeholders=self._placeholders(sub, suggested))

    async def async_step_set_limit(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        current = self._current()
        if current is None:
            return self.async_abort(reason="limit_gone")
        return self._apply(current, current[2]["limit_w"])

    async def async_step_other_value(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        current = self._current()
        if current is None:
            return self.async_abort(reason="limit_gone")
        _, sub, suggested = current
        if user_input is not None:
            return self._apply(current, float(user_input[LIMIT]))
        schema = vol.Schema({vol.Required(LIMIT, default=suggested["limit_w"]): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=100000, step=1, unit_of_measurement="W",
                                          mode=selector.NumberSelectorMode.BOX))})
        return self.async_show_form(step_id="other_value", data_schema=schema,
                                    description_placeholders=self._placeholders(sub, suggested))

    async def async_step_no_limit(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        current = self._current()
        if current is None:
            return self.async_abort(reason="limit_gone")
        entry, sub, suggested = current
        await entry.runtime_data.async_dismiss_limit(list(sub.data[CONF_POWER_SENSORS]), suggested["peak_w"])
        return self.async_create_entry(data={})

    def _apply(self, current: tuple[Any, Any, dict], limit_w: float) -> RepairsFlowResult:
        """The array's limit, one for every sensor, as the array's settings store it."""
        entry, sub, _ = current
        data = dict(sub.data)
        data[CONF_SENSOR_LIMITS] = {s: float(limit_w) for s in sub.data[CONF_POWER_SENSORS]}
        self.hass.config_entries.async_update_subentry(entry, sub, data=data)
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str,
                                data: dict[str, str | int | float | None] | None) -> RepairsFlow:
    if issue_id.startswith(issues.LIMIT_SUGGESTION) and data:
        return LimitSuggestionFlow(str(data["entry_id"]), str(data["subentry_id"]))
    raise ValueError(f"unknown repair {issue_id}")
