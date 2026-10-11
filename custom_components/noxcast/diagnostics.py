"""Diagnostics download: configuration, learned state and forecast (location is not included)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_API_KEY
from .coordinator import NoxcastConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NoxcastConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data

    def hours(series: dict[float, float]) -> dict[str, float]:
        return {dt_util.utc_from_timestamp(t).isoformat(): round(v, 1) for t, v in series.items()}

    return {
        "config": dict(entry.data),
        "options": async_redact_data(dict(entry.options), {CONF_API_KEY}),
        "archive_errors": coordinator.archive_errors,
        "arrays": {
            sid: {
                "name": arr.name, "sensors": list(arr.sensors), "tilt": arr.geometry.tilt,
                "azimuth": arr.geometry.azimuth, "peak_w": arr.geometry.peak_w, "valid_since": arr.valid_since,
                "learned": coordinator.learned[sid].as_dict() if sid in coordinator.learned else None,
                "forecast_wh": hours(data.arrays.get(sid, {})) if data else None,
            }
            for sid, arr in coordinator.arrays.items()
        },
        "exclusions": coordinator.exclusions,
        "last_learning": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
        "last_learning_error": coordinator.last_learning_error,
        "forecast_updated": data.updated.isoformat() if data and data.updated else None,
    }
