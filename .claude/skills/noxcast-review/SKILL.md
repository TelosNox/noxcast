---
name: noxcast-review
description: Review everything a Noxcast release contains since the last tag, against the rules tests cannot check (compatibility, physics, explainability, texts for users, privacy, correctness). Use when preparing a release or pre-release, before its release PR is created.
---

# Noxcast release review

CI checks the mechanics on every PR: tests, architecture boundaries, README structure, lint, privacy patterns, the
changelog section. This review covers what needs judgement, once per release: a merge publishes nothing, a release
does. Findings become fix PRs before the release PR. Questions that only the whole release answers are part of it:
is the release breaking in sum, was each stored format stepped once and migrated, does the changelog tell the
release as a whole?

The backtest is not part of it. It measures a single model change and stays with each PR (`## Backtest`, see
`CLAUDE.md`); here it is only checked that those PRs have one.

## 1. Range

Run `git fetch --tags origin` first. The range starts at the last tag:

- **Pre-release:** the last tag, pre-releases included: `git describe --tags --abbrev=0 --match "v*" origin/main`.
- **Stable release:** the last stable tag, since stable users get everything after it:
  `git describe --tags --abbrev=0 --match "v*" --exclude "v*b*" origin/main`. Without one yet, the first tag.
  Reviews of the pre-releases in between narrow the work; the "Whole release" questions still cover the full range.

The range is `<tag>..origin/main`. List the merged PRs with `git log --oneline <tag>..origin/main` and the changed
files with `git diff --stat <tag> origin/main`.

## 2. Split by area

Large diffs are reviewed badly in one piece. Start one subagent (Agent tool, `general-purpose`) per area that has
changes, in parallel. Each brief holds the range, the files of its area (`git diff <tag> origin/main -- <files>`),
the list of merged PRs, and its checklist points copied from below. Ask for each point "nothing found" or findings,
each with file:line, the concrete scenario that goes wrong, and how sure it is. The subagents change nothing.

| Area | Files | Points |
|---|---|---|
| Model | `model.py`, `shade.py`, `solar.py`, `effects.py`, `fine.py`, `conditions.py`, `band.py`, `intraday.py`, `learning.py`, `forecasting.py`, `weather.py`, `sources.py`, `load.py`, `battery.py`, `plan.py`, `flows.py`, `backtest.py`, `arraycheck.py`, `events.py` | B, C, F, and A for learned and stored data |
| Interface | `const.py`, `__init__.py`, `coordinator.py`, `archive.py`, `config_flow.py`, `sensor.py`, `binary_sensor.py`, `image.py`, `services.yaml`, `views.py`, `panel.py`, `explain.py`, `diagnostics.py`, `issues.py`, `energy.py`, `manifest.json`, and any file in `custom_components/` the table does not name | A, C, F |
| Frontend and texts | `frontend/*.js`, `visual.py`, `brand/`, `strings.json`, `translations/`, `README*.md`, `CHANGELOG.md` | A (card options and payloads), D, F |
| Whole release | the list of merged PRs, their descriptions (`gh pr view <n>`), the full diff | A in sum, D for the changelog as a whole, E, the backtest sections |

**A. Compatibility** (`CLAUDE.md`, Compatibility)
- Entity IDs, unique IDs, attribute names and their schema (`ATTR_SCHEMA_VERSION`), websocket payloads
  (`WS_SCHEMA_VERSION`, the same number in every frontend script), stored data (`STORAGE_VERSION`,
  `LEARNED_VERSION`, `band.VERSION`, `backtest.VERSION`), config entries (`VERSION`, `MINOR_VERSION` with a
  migration in `__init__.py`), service names and fields, card options.
- Changed without a version step or migration? Stepped more than once where once would do? Does a user lose
  something learned, and does the changelog say so? A breaking change sits in the `### Breaking` group at the top
  of `Unreleased` with what the user has to do, and the version step fits it (`CLAUDE.md`, Versioning).

**B. Physics and model**
- Clear sky with the learned model is the upper bound. Bands, intraday corrections and similar adjustments vary
  the cloud part (clearness), never act as a factor on power. More than a few percent above clear sky is a bug.
- Units (W, Wh, kWh, W/m², %, degrees), UTC against local time, daylight saving days (23 and 25 hours), the sun
  position at the middle of the hour for hourly means, period start as the key of a `Series`.
- Learning excludes curtailment (full battery, zero export, inverter or sensor limit), outages, snow and partial
  hours. New learning paths use the same filters (`usable_samples`).
- Physics first, learned parameters second, statistical correction last.

**C. Explainability**
- Every new or changed number can be traced: does it reach `explain.py`, the diagnostics or the panel? A factor
  that is applied but nowhere shown is a finding.

**D. Texts for users**
- The changelog section says what changes for users, not how the code changed, under the right headings; every
  merged PR that users notice has its entry, and entries of PRs that undid each other are merged or removed.
- `README.md` and `README.de.md` say the same in content; the German text uses the terms of the German user
  interface.
- `strings.json`, `translations/en.json` and `translations/de.json` say the same.

**E. Privacy**
- No location, sensor IDs from a real installation, Home Assistant URLs, tokens or exported history anywhere in the
  release: code, tests, fixtures, screenshots, changelog. The privacy check right before publishing ("How a release runs") stays a step of its own.

**F. Correctness**
- Bugs: wrong conditions, missing None or empty cases, async mistakes (blocking calls in the event loop, missing
  await), state that survives where it should not, errors swallowed silently, and interactions between PRs of the
  release that each looked fine alone.

**Backtest sections.** Every merged PR that changed the PV model (the files in `PV_MODEL` of
`.claude/hooks/guard.py`) has a `## Backtest` section with numbers or a reason. A missing one is a finding; a reason
like "export too short" goes to the maintainer as an open point.

## 3. Act on it and report

Fix findings in PRs of their own before the release PR (the release PR itself contains only the changelog and
manifest changes), or give a reason for each one you do not follow. Then put
a `## Review` section into the release PR's description:

```
## Review
Range: v0.1.0b4..main, 15 PRs
- Model (B, C, F, A): band could exceed clear sky on snow days – fixed in #181
- Interface (A, C, F): nothing found
- Frontend and texts (A, D, F): changelog entry of #174 described the code – rewritten in #182
- Whole release: not breaking; STORAGE_VERSION unchanged; backtest sections complete
- Not followed: None case of the archive – the coordinator never passes None (coordinator.py:412)
Open for the maintainer: #175 has no backtest numbers (export too short)
```

The fix PRs merged after the review belong to the release too: review their range (`<end of the first range>..
origin/main`) the same way, only the areas they touch, and name both ranges in the `Range:` line.

The Claude Code hook refuses `gh pr create` on a `release/...` branch without a `## Review` section.
