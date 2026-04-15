# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

`llm-wiki-starter` is a local-first markdown wiki compiler powered by Claude. It runs a three-stage pipeline:

1. **Sync** — copies source files from configured repos into `raw/inbox/`
2. **Ingest** — calls Claude to generate wiki pages (summaries, topics, entities) from raw sources
3. **Query / Lint** — natural-language Q&A over the generated wiki, or quality-check the output

All generated wiki output lives under `wiki/`. State files (manifests, event logs, reports) live under `state/`. Neither directory is tracked in git.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate && pip install -r requirements.txt
. .venv/bin/activate && pip install --no-build-isolation -e '.[dev]'
cp .env.example .env   # fill in ANTHROPIC_API_KEY
```

## Common Commands

All commands require the venv activated, or use the Makefile targets.

```bash
# Validate setup
llm-wiki doctor

# Full rebuild (sync with prune, then ingest)
make refresh

# Incremental rebuild (sync without prune, then ingest)
make refresh-fast

# Individual stages
make sync          # copy sources, no prune
make sync-prune    # copy sources + remove orphans from raw/inbox/
make ingest        # run LLM pipeline on raw/inbox/
make query         # interactive Q&A against the wiki
make lint          # check orphan pages, weak links, missing sources

# Dry-run previews (no writes)
make sync-dry-run
make sync-prune-dry-run
llm-wiki ingest --dry-run

# Show resolved config
make config

# Tests
make test                  # pytest
make release-check         # compile all scripts + pytest + version smoke test

# Run a single test file
pytest tests/test_sync.py

# Run a specific test
pytest tests/test_ingest.py::test_low_signal_filter
```

## Architecture

### Entry point

`scripts/cli.py` is the unified CLI dispatcher (`llm-wiki` command). All sub-commands delegate to individual modules.

### Core modules (`scripts/`)

| Module | Role |
|--------|------|
| `sync.py` | Copies files from `sync-sources.json` into `raw/inbox/` with collision-safe naming (hash suffixes). Maintains `state/sync_manifest.json` to enable safe prune. |
| `ingest.py` | Main LLM pipeline (1100+ lines). Loads raw files → filters low-signal sources → calls Claude → writes `wiki/summaries/`, `wiki/topics/`, `wiki/entities/`, `wiki/index.md`, `wiki/log.md`. Tracks per-source contributions for incremental deletes. Writes observability artifacts to `state/`. |
| `query.py` | Reads all wiki text, sends to Claude with a user question. Grounded answers only — no invention. |
| `lint.py` | Checks for orphan pages, duplicate pages, stale pages, weak links, missing source references. |
| `doctor.py` | Validates `sync-sources.json` and `ingest-settings.json` schemas, checks demo artifacts, confirms API key is set. |
| `config_models.py` | Pydantic v2 models for both config files. Schema version is `1`. |

### Configuration files

- **`sync-sources.json`** — defines source repos, glob patterns, naming mode (`preserve_path` or `basename`), and collision strategy.
- **`ingest-settings.json`** — tunes the ingest pipeline: `max_source_chars` (20K), `max_topics`/`max_entities` (6 each), low-signal filter patterns, and blocked topic/entity suffixes to prevent code-symbol noise.

### Model configuration (`.env`)

```
ANTHROPIC_INGEST_MODEL=claude-haiku-4-5      # high-volume compilation
ANTHROPIC_QUERY_MODEL=claude-sonnet-4-6      # interactive Q&A
ANTHROPIC_LINT_MODEL=claude-haiku-4-5        # batch quality checks
```

### Ingest philosophy (`schemas/AGENTS.md`)

- `raw/` files are immutable source material — never modify them.
- Prefer durable knowledge (architecture docs, specs, design notes) over workflow exhaust (task IDs, review checklists, prompt artifacts).
- Keep topic/entity fanout small and high-signal; one source should update a handful of pages, not dozens.
- Do not create pages for code symbols (structs, interfaces, methods, env vars, etc.).

### Incremental safety

`sync --prune` removes files from `raw/inbox/` that are no longer in any source. The sync manifest (`state/sync_manifest.json`) tracks provenance so pruning never silently removes files that came from a different source. The ingest manifest (`state/manifest.json`) tracks per-source contributions so removing a source removes only its derived pages.

### Demo corpus

`demo/` contains a tiny tracked corpus used for smoke-testing and evaluation. It mirrors the real `raw/inbox/` → `wiki/` structure. Run `llm-wiki doctor` to verify demo artifacts are present and valid.

## Testing

Tests live in `tests/` and use pytest. Coverage areas:

- `test_sync.py` — naming modes, collision detection, manifest behavior
- `test_ingest.py` — pipeline workflows, low-signal filtering
- `test_refresh_fast.py` — incremental rebuild flow
- `test_cli.py` — CLI routing
- `test_doctor.py` — config validation

The CI pipeline (`.github/`) runs `pip install`, script compilation (`py_compile`), a CLI smoke test, and the full pytest suite on every push and PR.
