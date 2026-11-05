---
name: noxcast-review
description: Review a Noxcast branch before its pull request is created, against the rules tests cannot check (compatibility, physics, explainability, texts for users, privacy). Use before every `gh pr create` in this repository, and again when an open PR changes substantially.
---

# Noxcast review before the pull request

CI checks the mechanics: tests, architecture boundaries, README structure, lint, privacy patterns, the changelog
section. This review covers what needs judgement. The author of the change is usually a Claude session too, so the
review runs in a fresh context with a fixed checklist; a general "look over it" adds little.

## 1. Scope and tier

Look at `git diff --stat origin/main...HEAD` and pick the highest tier any changed file reaches:

| Tier | Files | Checklist points |
|---|---|---|
| Model | `model.py`, `shade.py`, `solar.py`, `effects.py`, `fine.py`, `conditions.py`, `band.py`, `intraday.py`, `learning.py`, `forecasting.py`, `weather.py`, `sources.py`, `load.py`, `battery.py`, `plan.py`, `flows.py` | all, and the backtest (step 3) for PV model changes |
| Interface | `const.py`, `__init__.py`, `coordinator.py`, `archive.py`, `config_flow.py`, `sensor.py`, `binary_sensor.py`, `image.py`, `services.yaml`, `views.py`, `panel.py`, `explain.py`, `diagnostics.py`, `issues.py` | A, C, D, E, F |
| Frontend and texts | `frontend/*.js`, `strings.json`, `translations/`, `README*.md`, `CHANGELOG.md` | A (card options and payloads only), D, E, F |
| Other | tests, `docs/`, CI, `prototype/` | E, F |

## 2. The review

Start a subagent (Agent tool, `general-purpose`) with a brief that holds: the diff range `origin/main...HEAD`, the
tier, what the change is meant to do (one or two sentences, not the reasoning that led to it), and the checklist
points of the tier copied from below. Ask it to read the diff and the code around it, and to report per point
either "nothing found" or findings, each with file:line, the concrete scenario that goes wrong, and how sure it is.
It changes nothing.

**A. Compatibility** (`CLAUDE.md`, Compatibility)
- Entity IDs, unique IDs, attribute names and their schema (`ATTR_SCHEMA_VERSION`), websocket payloads
  (`WS_SCHEMA_VERSION`, the same number in every frontend script), stored data (`STORAGE_VERSION`,
  `LEARNED_VERSION`, `band.VERSION`, `backtest.VERSION`), config entries (`VERSION`, `MINOR_VERSION` with a
  migration in `__init__.py`), service names and fields, card options.
- Changed without a version step or migration? Does a user lose something learned, and does the changelog say so?
  A breaking change is marked **Breaking** with what the user has to do.

**B. Physics and model**
- Clear sky with the learned model is the upper bound. Bands, intraday corrections and similar adjustments vary
  the cloud part (clearness), never act as a factor on power. More than a few percent above clear sky is a bug.
- Units (W, Wh, kWh, W/m², %, degrees), UTC against local time, daylight saving days (23 and 25 hours), the sun
  position at the middle of the hour for hourly means, period start as the key of a `Series`.
- Learning excludes curtailment (full battery, zero export, inverter or sensor limit), outages, snow and partial
  hours. New learning paths use the same filters (`usable_samples`).
- Physics first, learned parameters second, statistical correction last. Nothing that a black box decides where
  statistics suffice.

**C. Explainability**
- Every new or changed number can be traced: does it reach `explain.py`, the diagnostics or the panel, so a user
  can see where it comes from? A new factor that is applied but nowhere shown is a finding.

**D. Texts for users**
- The changelog entry says what changes for users, not how the code changed; it sits under the right heading.
- `README.md` and `README.de.md` say the same in content (the test checks only structure); the German text uses the
  terms of the German user interface.
- `strings.json`, `translations/en.json` and `translations/de.json` change together and say the same.

**E. Privacy**
- No location, sensor IDs from a real installation, Home Assistant URLs, tokens or exported history in code, tests,
  fixtures, screenshots or the PR text. Made-up values are marked `privacy-ok` where the check would flag them.

**F. Correctness**
- Bugs in the changed code: wrong conditions, missing None or empty cases, async mistakes (blocking calls in the
  event loop, missing await), state that survives where it should not, errors swallowed silently.

## 3. Backtest for PV model changes

The rule in `docs/concept.md` (PV backtest): a model change shows before its PR that it is better on unlearned
days. Run `prototype/backtest.py` from `main` and from the branch on the same exported files of the main checkout
(`prototype/data/`, git-ignored) and compare hold-out, in-sample and baseline per array. Better in-sample with a
worse hold-out is overfitting and does not count. If the export is missing or too short, say so in the PR and ask
the maintainer; do not leave the backtest out silently. Never put the exported data or the location into the PR.

## 4. Act on it and report

Fix what is right, and give a reason for each finding you do not follow. Then put a `## Review` section into the
PR description:

```
## Review
Tier: Model
- A Compatibility: nothing found
- B Physics: band could exceed clear sky on snow days – fixed (clip in band.py)
- C Explainability: nothing found
- D Texts: nothing found
- E Privacy: nothing found
- F Correctness: None case of the archive – not followed, the coordinator never passes None (coordinator.py:412)
Backtest (hold-out, daily energy error, main → branch): Roof S 9.8 % → 8.1 %, Balcony 12.4 % → 12.2 %
```

The Claude Code hook refuses `gh pr create` without a `## Review` section.
