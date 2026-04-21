# Contributing

## Getting Started

**Option A — uv (recommended, fastest):**

```bash
uv sync --dev
```

`uv` manages the Python version, virtualenv, and all dependencies in one step.
Install uv first if you don't have it: https://docs.astral.sh/uv/getting-started/installation/

**Option B — standard pip:**

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip setuptools wheel
pip install -e '.[dev]'
```

Then:

1. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`
2. Copy `sync-sources.json` to `sync-sources.local.json`
3. Optionally copy `ingest-settings.json` to `ingest-settings.local.json`

## After Pulling Changes

If a pull adds or changes dependencies in `pyproject.toml`, re-run your install step:

```bash
# uv
uv sync --dev

# pip
pip install -e '.[dev]'
```

This keeps your local environment in sync and prevents `ModuleNotFoundError` failures when running tests.

## Local Checks

- `make test`
- `python3 -m py_compile scripts/*.py`
- `llm-wiki`
- `make sync-dry-run`

## Contribution Guidelines

- Keep tracked files generic and publishable.
- Do not commit `.env`, `sync-sources.local.json`, `ingest-settings.local.json`, generated wiki output, or synced raw corpus.
- Prefer small, focused PRs.
- Add or update tests when changing sync or ingest behavior.
- Keep changes aligned with the supported workflows and expectations documented in [`SUPPORT.md`](/Users/martez/src/llm-wiki-claude/SUPPORT.md).

## Release Notes

- Update `CHANGELOG.md` for user-visible changes.
- Keep new release notes under `Unreleased` until a version is actually being cut.
- Bump `VERSION` only when preparing a release tag or a deliberate release-prep branch.
- If you change output shape or observability files, update `demo/sample-output/`.
- If you change config shape, document compatibility or migration notes in `CHANGELOG.md`.
- Follow the full release checklist in [`RELEASING.md`](/Users/martez/src/llm-wiki-claude/RELEASING.md).
- Use [`RELEASE_PREP_TEMPLATE.md`](/Users/martez/src/llm-wiki-claude/RELEASE_PREP_TEMPLATE.md) when assembling a release-prep pass.
