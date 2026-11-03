# Changelog

All notable changes to Noxcast are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor version can contain
breaking changes.

## Unreleased

### Added
- A "Panorama" view next to "Map" and "Year grid" under "Learned": the view from the module, direction across and height
  up at the same scale as the eye sees them. It shows the obstacles learned from the measurements, the horizon line
  where measurements are still missing, the module's own plane (roof, wall, railing) and the sun paths of June,
  March/September and December, so the learned shade can be compared with the real surroundings. If the edge of the own
  plane looks clearly different on site, tilt or azimuth are configured wrong.

## [0.1.0b2] - 2026-11-03

### Added
- The diagnostics download contains, per PV array, the hours learning used: measured power, clear-sky power, the
  satellite's clearness, temperature, shade and what each hour says about the efficiency. This shows where an
  implausible efficiency comes from. The download takes a few seconds longer, since it fetches the history again.
- Each array under "Learned" shows whether its configuration fits the measurements: the highest hourly power against
  the configured peak power, the efficiency per sun direction next to the one per month, and plain hints when the peak
  power, the sensor, the azimuth or the tilt look wrong. Such an array gets a "Check" badge, and the diagnostics tab
  lists it. Shown after the next learning run.

## [0.1.0b1] - 2026-11-02

### Added

- PV forecast per array and in total for up to 7 days, hourly, with a P10–P90 band, from Open-Meteo weather.
- Today's forecast fixed at 06:00, the expected day from measurements plus the corrected rest, and the remaining energy.
- Nightly learning from your own history: efficiency, shading map, reflection, direction factor, sky view and
  temperature loss.
- Accuracy view from a day-ahead archive, with the forecast error split into weather and model.
- A backtest of the current model on past days and on days it did not learn from.
- Optional house load forecast, battery simulation until the next charge start, and planning for flexible consumers.
- Sidebar panel, a day card, a weather card and a weather entity for dashboards.
- `noxcast.exclude_period` and `noxcast.relearn` services for administrators.
- A diagnostics section and download showing what data Noxcast reads and how complete it is.

### Fixed

- Day section: the lanes below the chart show the full array names instead of their first six characters. A long name
  reaches into the chart; a bar behind it stays faint, so its start remains visible. The tooltip of a bar names its array.
- Diagnostics and load forecast on a phone: long entity IDs wrap instead of being cut off.
- Day card: the list behind a figure shows a long name on two lines before shortening it, and the full name in a tooltip.
