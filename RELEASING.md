# Releasing

This repo is not auto-published. Releases are prepared in-repo first so the workflow stays explicit and low-risk.

## Release Hygiene

Before cutting a version:

1. Run `make release-check`
2. Run `llm-wiki doctor`
3. Review `CHANGELOG.md` and move user-visible items from `Unreleased` into a dated version section
4. Bump `VERSION`
5. Confirm `llm-wiki --version` prints the bumped version
6. Review tracked demo sample artifacts if output shape or observability changed
7. Confirm tracked config templates are still generic and publishable
8. Confirm no local-only files or generated private corpus data are staged
9. Optionally fill in [`RELEASE_PREP_TEMPLATE.md`](/Users/martez/src/llm-wiki-claude/RELEASE_PREP_TEMPLATE.md) for the cut

## Pre-release Gates

The following gates must all pass before starting the changelog promotion and tag flow. Each gate maps to a real test shipped by the multi-workspace epics -- run them in this order and treat the first failure as a blocker:

- [ ] `make test` passes (all unit, integration, isolation, regression, fallback tests)
- [ ] `pytest tests/test_templates_sync.py -v` passes (`scripts/templates/` byte-equal to repo-root copies)
- [ ] `pytest tests/test_isolation.py -v` passes (the primary success signal -- workspace runs never touch the repo root)
- [ ] `pytest tests/test_repo_root_regression.py -v` passes (0.2.0 repo-root workflow unchanged)
- [ ] `pytest tests/test_fallback_resolution.py -v` passes (workspace -> repo-root fallback paths covered)
- [ ] `make release-check` passes
- [ ] Grep `CHANGELOG.md` for `$`, `cost`, `price`, and `pricing`. None of these words should appear in the Unreleased or new-version section (see ARCHITECTURE §10.6 -- this project does not publish pricing or cost guidance).
- [ ] `llm-wiki doctor` from repo root exits 0 (or the degraded state is documented in the release notes)

### Template Drift Reminder

If `scripts/templates/*.json` was modified intentionally, make sure the matching repo-root copy was updated too. The byte-equality test (`tests/test_templates_sync.py`) catches drift, but only if it is run. The reverse is equally true: if the repo-root copies were edited, sync `scripts/templates/` to match. A release that ships drifted templates will surface as a broken `llm-wiki init` for downstream users.

## Changelog Promotion

Once the pre-release gates are green, promote the changelog before tagging:

- [ ] Move the `[Unreleased]` block into a new `[0.3.0] - YYYY-MM-DD` section in `CHANGELOG.md`
- [ ] Create a fresh empty `[Unreleased]` block at the top of `CHANGELOG.md`
- [ ] Bump the version in `pyproject.toml` to `0.3.0`
- [ ] Commit with message: `Release 0.3.0`

## Post-release

After the `Release 0.3.0` commit lands:

- [ ] `git tag -a v0.3.0 -m "Release 0.3.0"`
- [ ] `git push origin v0.3.0`
- [ ] Verify the CI release workflow passes (if configured); otherwise confirm CI is green on the release commit.

## Versioning Policy

- Use SemVer-style versioning: `MAJOR.MINOR.PATCH`
- Bump `PATCH` for bug fixes, docs corrections, and non-breaking operational improvements
- Bump `MINOR` for new commands, new config fields, new observability outputs, or meaningful new capabilities that do not intentionally break existing setups
- Bump `MAJOR` when tracked config shape, expected workflows, or output contracts change incompatibly

## Config Compatibility

- Prefer additive config changes over renames or removals
- If a config key must change, support the old key for at least one release when practical
- Both tracked configs currently use `schema_version: 1`
- Call out config migrations explicitly under `Breaking Changes` in `CHANGELOG.md`
- Keep tracked templates generic; never convert tracked templates into machine-local examples

## Support Contract

- The plain-language project promise lives in [`SUPPORT.md`](/Users/martez/src/llm-wiki-claude/SUPPORT.md)
- This project is maintained as a best-effort local starter/tool, not a hosted service
- Config compatibility should be preserved within minor releases whenever practical
- Breaking config or output-contract changes should be reserved for major releases or clearly documented migrations

## Sample Artifact Expectations

Update `demo/sample-output/` when:

- the shape of `state/last_ingest_run.json` changes
- the shape of `state/last_ingest_report.md` changes
- generated topic/entity contribution formatting changes
- index layout changes in a user-visible way

You do not need to update sample artifacts for internal refactors that leave observable outputs unchanged.

## Suggested Cut Flow

1. Merge the final release-prep changes
2. Update `VERSION`
3. Move `Unreleased` notes into a new dated section in `CHANGELOG.md`
4. Run `make release-check`
5. Run `llm-wiki doctor`
6. Tag the repo only after those checks are green
