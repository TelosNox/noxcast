"""Diagnostics download: configuration, learned state, every daylight hour and forecast.

Without location, entity IDs (stable aliases instead) and addresses of private services (redact.py), so that it can
be posted in a public issue.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import band as bd
from . import fine as fn
from .const import Series
from .model import clearsky_weather
from .redact import redact
from .coordinator import NoxcastConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NoxcastConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data

    def by_hour(series: Series) -> dict[str, float]:
        return {dt_util.utc_from_timestamp(t).isoformat(): round(v, 1) for t, v in series.items()}

    # every daylight hour with what learning made of it; archive_errors stay those of the last learning run
    hours_error = None
    try:
        hours = await coordinator.async_hour_tables()
    except Exception as err:  # noqa: BLE001 – the rest of the download stays useful
        _LOGGER.exception("Hours for the diagnostics failed")
        hours, hours_error = {}, str(err) or type(err).__name__

    # how often the actual value fell inside the published band, on the days the band learns from
    site = coordinator.site
    band_coverage_error = None
    try:
        band_coverage = await hass.async_add_executor_job(bd.coverage, coordinator._eval_archive(),
                                                          lambda ts: clearsky_weather(site, ts).ghi)
    except Exception as err:  # noqa: BLE001 – the rest of the download stays useful
        _LOGGER.exception("Band coverage for the diagnostics failed")
        band_coverage, band_coverage_error = None, str(err) or type(err).__name__

    raw = {
        "config": dict(entry.data),
        "options": dict(entry.options),
        "archive_errors": coordinator.archive_errors,
        "arrays": {
            sid: {
                "name": arr.name, "sensors": list(arr.sensors), "tilt": arr.geometry.tilt,
                "azimuth": arr.geometry.azimuth, "peak_w": arr.geometry.peak_w, "planes": arr.geometry.describe(),
                "valid_since": arr.valid_since,
                "learned": coordinator.learned[sid].as_dict() if sid in coordinator.learned else None,
                "hours": hours.get(sid),
                "forecast_wh": by_hour(data.arrays.get(sid, {})) if data else None,
            }
            for sid, arr in coordinator.arrays.items()
        },
        "hours_error": hours_error,
        "exclusions": coordinator.exclusions,
        # limits seen in the measurements, and the levels the user answered "There is no limit" to (plateau.py)
        "limits": coordinator.limits,
        "last_learning": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
        "last_learning_error": coordinator.last_learning_error,
        "forecast_archive": coordinator.forecast_archive,
        "fine_archive": fn.summary(coordinator.fine),
        "band": coordinator.band,
        "band_coverage": band_coverage,
        "band_coverage_error": band_coverage_error,
        "forecast_updated": data.updated.isoformat() if data and data.updated else None,
    }
    return await hass.async_add_executor_job(redact, raw)        # every string of every hour table: not in the loop
