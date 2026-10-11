"""Solar forecast for the Home Assistant energy dashboard."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str) -> dict[str, dict[str, float]] | None:
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or not hasattr(entry, "runtime_data") or entry.runtime_data.data is None:
        return None
    total = entry.runtime_data.data.total
    return {"wh_hours": {dt_util.utc_from_timestamp(t).isoformat(): round(wh) for t, wh in total.items()}}
