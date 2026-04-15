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
