# Changelog

All notable changes to Noxcast are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor version can contain
breaking changes.

## Unreleased

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
