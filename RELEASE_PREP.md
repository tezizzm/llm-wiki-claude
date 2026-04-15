# Release Prep: 0.2.0

Prepared on 2026-04-15.

## Version

- Current `VERSION`: `0.2.0`
- Planned version: `0.2.0`
- Reason for bump: minor release for release-process hygiene, support policy, config compatibility/versioning, doctor workflow, and stronger end-to-end verification

## Changelog

- [x] `Unreleased` reviewed
- [x] Breaking changes called out if needed
- [x] Config compatibility notes added if needed

## Validation

- [x] `make release-check`
- [x] `llm-wiki doctor`
- [x] Demo sample artifacts reviewed

## Output And Config Review

- [x] Tracked templates are still generic
- [x] No local-only files are staged
- [x] Sample output still reflects observable behavior

## Notes

- Risks: no major blockers identified; remaining risk is normal early-release polish around evolving heuristics rather than release-process gaps
- Follow-ups: tag `0.2.0`, publish release notes from `CHANGELOG.md`, then continue iterating on merged-page heuristics and broader corpus support as future minor releases
