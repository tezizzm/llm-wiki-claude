# Changelog

This file tracks user-visible changes and release-relevant migration notes.

## Unreleased

_None yet._

## 0.2.0 - 2026-04-15

### Added

- Added a documented release hygiene workflow, compatibility guidance, and a release readiness checklist.
- Added config schema versioning, compatibility warnings, a `llm-wiki doctor` command, and a release-prep template.
- Added a dedicated support policy describing the supported workflows, best-effort maintenance promise, no-warranty posture, and contribution expectations.
- Added a clearer product promise describing the current best-fit use cases and plausible future directions.
- Added a richer `doctor` health-check with structural checks, capability advisories, and demo artifact validation.
- Added a `refresh-fast` smoke test to verify the main sync-plus-ingest workflow end to end.

### Changed

- Refined `doctor` to stay read-only during wiki output checks and to treat missing API credentials as warnings rather than hard failures.
- Tightened release readiness tooling with `make release-check` and `make doctor`.

## 0.1.0 - 2026-04-14

### Added

- Added a generic, config-driven sync workflow with collision-safe naming and prune support.
- Added `refresh` and `refresh-fast` workflows for sync plus ingest.
- Added generic tracked templates for sync and ingest settings with local override support.
- Added tests for sync and ingest behavior plus GitHub Actions CI.
- Added Apache-2.0 licensing and public-starter README guidance.
- Added source-aware incremental ingest cleanup, lightweight ingest event logging, and refreshed demo/docs guidance including an Obsidian workflow.
- Added a CLI `--version` flag and tracked demo sample-output artifacts.

### Changed

- Tightened ingest filtering to reduce overproduction and low-value artifacts.
- Centralized versioning on the tracked `VERSION` file.
