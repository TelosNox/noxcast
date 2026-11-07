# Noxcast – working rules

Noxcast is a Home Assistant integration for transparent PV generation forecasts (physics + learned
geometry and shading + weather correction). Load forecast and surplus planning follow later as
optional parts of the same integration. See `docs/concept.md`.

## Language
- Code, comments, docs, commit messages: English.
- Conversation with the maintainer: German.
- The README exists in English (`README.md`) and German (`README.de.md`). A change to one goes into the other in the
  same PR, with the German terms of the German user interface. `tests/test_readme.py` checks that both have the same
  headings, images and code. Changelog and `docs/` stay English only.

## License
- GPL-3.0. Only add dependencies with GPL-3.0-compatible licenses (MIT, BSD, Apache-2.0, LGPL, GPL).

## Privacy
- Never commit location data, sensor IDs, Home Assistant URLs, tokens or exported history.
  Personal values live in `*.local.json` (git-ignored); commit only `*.example.json`.
- `.github/scripts/check_privacy.py` looks for tokens, Home Assistant addresses, private IP addresses and precise
  coordinates (`tests/test_privacy.py` runs it on every tracked file), and the Claude Code hook also for the values
  from the local files before every commit, push and pull request. A made-up value in a test is marked with
  `privacy-ok` on its line.

## Git
- Work reaches `main` only through a pull request. Finished work goes on a branch from `main` and gets a PR with
  squash auto-merge, without asking; this is the maintainer's standing approval for PR work.
- Merging a PR does not publish anything for users. Only a release does (see below).
- Several sessions share the main checkout. Code work runs in a worktree of its own
  (`git worktree add .claude/worktrees/<topic> -b <prefix>/<topic> origin/main`); the main checkout never switches
  branches. The hook in `.claude/hooks/guard.py` blocks a branch switch there and any commit on `main`.
- Branch names: `feat/`, `fix/`, `refactor/`, `docs/`, `chore/`, `ci/`, `test/` or `release/`, then a short
  kebab-case topic.
- The PR title becomes the squash commit on `main`: one sentence on what changes, from the user's point of view where
  users notice it (e.g. "Battery forecast respects the output limit of balcony inverters").
- A PR that changes the PV model (`PV_MODEL` in `.claude/hooks/guard.py`) has a `## Backtest` section in its
  description: hold-out, in-sample and baseline per array from `main` and from the branch, or the reason why there
  are none (`docs/concept.md`, PV backtest). The hook refuses such a PR without it.

## Code
- Functional core, imperative shell (`docs/concept.md`, Architecture): computations are pure functions without Home
  Assistant imports; the coordinator, `panel.py` and the other shell modules only fetch, store and publish.
  `tests/test_boundaries.py` enforces this. A new module is part of the core unless it is added to `SHELL` there.
- Comments explain why, not what.
- `ruff check .` must pass (CI job `lint`, rules pinned in `pyproject.toml`). Lines may exceed 120 characters where
  splitting hurts readability; the limit is not enforced.
- The tests run before every push: the git hook `.githooks/pre-push` runs the whole suite and stops the push if a
  test fails or changes are uncommitted. Set up once per clone: `git config core.hooksPath .githooks`, and in the
  main checkout `uv venv -p 3.14 .venv` and `uv pip install -r requirements_test.txt` (worktrees use that `.venv`).
  By hand: `.venv/Scripts/python -m pytest -p tests.windows`. `tests/windows.py` lets the Home Assistant tests
  run on Windows. A push takes about two minutes longer; never skip the hook (`--no-verify`). CI (Linux) stays
  the reference.
- `tests/snapshots/pinned.json` holds the results of the pinned scenario (`test_learning_and_forecast_are_pinned`),
  compared with a tolerance. A deliberate change to results, stored data or payloads updates it with
  `--update-snapshots` in the same PR; its diff shows what changed and is checked like code.

## Releases
Noxcast is delivered in releases. HACS offers users the latest release, not the latest commit on `main`.

### Versioning
- Semantic versions: tag `vX.Y.Z`, the same `X.Y.Z` in `custom_components/noxcast/manifest.json`.
- While the version is 0.x: minor for new features or breaking changes, patch for fixes only.
- A test release before a stable one: tag `vX.Y.ZbN`, manifest `X.Y.ZbN`, marked as a pre-release on GitHub.

### Changelog
- `CHANGELOG.md` follows Keep a Changelog: `## Unreleased` on top, then `## [X.Y.Z] - date` per version, grouped
  into Breaking, Added, Changed, Fixed and Removed.
- Every PR that users can notice adds its entry to `Unreleased` in the same PR. Internal refactoring and tests need
  no entry. The `changelog` workflow fails a PR that changes `custom_components/` without changing `Unreleased`,
  unless a line of its description starts with "No changelog entry:" and the reason.
- Entries are in English and written for users: what changes for them, not how the code changed.
- A breaking change goes into a `### Breaking` group at the top of its version section, above Added, so it is not
  lost among the other entries. It says what the user has to do.

### Compatibility
- These count as breaking unless a migration keeps them working: entity IDs, attribute schemas
  (`ATTR_SCHEMA_VERSION`), websocket payloads (`WS_SCHEMA_VERSION`), stored data (`STORAGE_VERSION`,
  `LEARNED_VERSION`), config entries (`VERSION`, `MINOR_VERSION`), service names and card options.
- Stored data that a new version replaces must not silently lose what was learned without a changelog entry.
- `homeassistant` in `hacs.json` names the oldest version the tests actually cover.

### What a release contains
1. `Unreleased` moved to a new version section with today's date.
2. The new version in `manifest.json`.
3. A tag on the `main` commit with exactly these two changes, and CI green on that commit.
4. A GitHub release for the tag, with that changelog section as its text. Pushing the tag runs
   `.github/workflows/release.yml`, which refuses a tag that disagrees with the manifest or changelog, or is not
   on `main`, and creates the release.

### How a release runs
1. Before the release PR, the `noxcast-review` skill (`.claude/skills/noxcast-review`) reviews everything since the
   last tag, split by area, in fresh subagents: compatibility, physics, explainability, texts, privacy, correctness.
   Findings are fixed in PRs of their own. Its `## Review` section goes into the release PR; the hook refuses a PR
   from a `release/...` branch without it.
2. A release PR makes changes 1 and 2 of "What a release contains" and contains nothing else.
3. A release is published only on the maintainer's explicit go, each time. A merged PR or a green CI is not one.
4. If a release changes learning, stored data or a migration, a pre-release first runs on the maintainer's Home
   Assistant through at least one nightly learning run.
5. Before publishing, check the release against the privacy rule above.
6. A published tag or release is never moved, replaced or deleted. A mistake gets a new patch version.

## Design principles
- Every number must be explainable: physics first, learned parameters second, statistical
  correction last. No black-box models where statistics suffice.
- Stable public interface: entity IDs and attribute schemas are versioned; never rename silently.
- Data quality before modelling: curtailment (full battery / zero export / inverter limit),
  outages, snow and partial hours are excluded from learning.
- Hourly mean values belong to the sun position at the middle of the hour.
- Clear sky is the upper bound for what Noxcast adjusts itself. Uncertainty bands, intraday corrections and similar
  adjustments vary the cloud part (clearness between overcast and clear), never act as a factor on PV power, and
  do not push the clear-sky output with the learned model (shade, efficiency) up by more than a few percent, for
  model errors. A blanket upward factor would produce made-up values. Irradiance that the weather source reports is
  a fact and is not clipped: if it exceeds the clear-sky model, the simplified model has a gap, not the data.
- Every model change proves itself in the backtest (`backtest.py`, `docs/concept.md`, PV backtest) before it is
  merged; the PR says what changed in the metrics.
