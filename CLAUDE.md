# Noxcast – working rules

Noxcast is a Home Assistant integration for transparent PV generation forecasts (physics + learned
geometry and shading + weather correction). Load forecast and surplus planning follow later as
optional parts of the same integration. See `docs/concept.md`.

## Language
- Code, comments, docs, commit messages: English.
- Conversation with the maintainer: German.

## License
- GPL-3.0. Only add dependencies with GPL-3.0-compatible licenses (MIT, BSD, Apache-2.0, LGPL, GPL).

## Privacy
- Never commit location data, sensor IDs, Home Assistant URLs, tokens or exported history.
  Personal values live in `*.local.json` (git-ignored); commit only `*.example.json`.

## Git
- No commits or pushes without explicit approval from the maintainer.

## Design principles
- Every number must be explainable: physics first, learned parameters second, statistical
  correction last. No black-box models where statistics suffice.
- Stable public interface: entity IDs and attribute schemas are versioned; never rename silently.
- Data quality before modelling: curtailment (full battery / zero export / inverter limit),
  outages, snow and partial hours are excluded from learning.
- Hourly mean values belong to the sun position at the middle of the hour.
