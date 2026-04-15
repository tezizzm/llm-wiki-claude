# Claude LLM Wiki Starter

This starter project gives you a local markdown-based personal wiki powered by Claude.

It was inspired by Andrej Karpathy's LLM wiki idea, shared on [X](https://x.com/karpathy) and in his GitHub gist, [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

This repo is designed to be shareable as a starter. Source material, generated wiki pages, local state, and secrets all stay out of git by default.

The tool is installable from source today and is meant to be used repo-first before worrying about package registry distribution.
The tracked `VERSION` file is the single source of truth for package and CLI versioning.

## Product Promise

This tool is best thought of as a compiled-context wiki for curated textual knowledge.

- It works especially well for onboarding docs, product context, architecture docs, process docs, and curated documentation from code repositories.
- It is strongest when the source material is durable and the goal is to build a browsable knowledge layer rather than rely only on raw retrieval.
- It is not yet optimized for “entire codebase understanding” where source code itself is the primary corpus.
- Possible future directions include richer repository understanding, broader extractors, stronger provenance, and better freshness/review workflows.

## Prerequisites

- **Python >= 3.9**
- **git**
- **Anthropic API key** -- sign up at [console.anthropic.com](https://console.anthropic.com) if you do not have one

## Quick Start

Go from `git clone` to a working wiki in under 15 minutes:

### 1. Clone

```bash
git clone https://github.com/martez/llm-wiki-claude.git
cd llm-wiki-claude
```

### 2. Create venv and install dependencies (~2 min)

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && pip install --no-build-isolation -e '.[dev]'
```

### 3. Configure (~1 min)

```bash
cp .env.example .env
# Edit .env and set your ANTHROPIC_API_KEY

cp sync-sources.json sync-sources.local.json
# Edit sync-sources.local.json with your source repo paths
```

### 4. Validate (~10 sec)

```bash
llm-wiki doctor
```

All checks should PASS. If any fail, fix the indicated issue before continuing.

### 5. Build (~5-10 min)

```bash
make refresh-fast
```

This runs an incremental sync (no prune) followed by ingestion of new or changed raw files.

### 6. Browse

Open the `wiki/` directory in [Obsidian](https://obsidian.md) or any markdown viewer.

## CLI Commands

The `llm-wiki` CLI provides a unified interface for all workflows:

```bash
llm-wiki doctor             # Pre-flight checks: config, versions, demo artifacts
llm-wiki lint               # Check wiki quality: weak pages, missing links
llm-wiki query "question"   # Query the wiki inline (single question, immediate answer)
llm-wiki query              # Interactive mode (ask multiple questions in a session)
echo "question" | llm-wiki query  # Pipe mode (feed a question via stdin)
llm-wiki sync               # Sync source files into raw/inbox/
llm-wiki ingest              # Build wiki artifacts from raw sources
llm-wiki refresh             # Sync with prune, then full ingest
llm-wiki refresh-fast        # Sync without prune, then incremental ingest
llm-wiki --version           # Show version
```

## Dual-model configuration

This starter supports separate model settings for different tasks:

- `ANTHROPIC_INGEST_MODEL` for ingestion and wiki compilation
- `ANTHROPIC_QUERY_MODEL` for answering questions from the wiki
- `ANTHROPIC_LINT_MODEL` reserved for future Claude-based lint workflows

Recommended defaults:

- Ingest: `claude-haiku-4-5`
- Query: `claude-sonnet-4-6`
- Lint: `claude-haiku-4-5`

## What it does

- `scripts/sync.py` copies selected files from one or more source repos into `raw/inbox/`
- `scripts/ingest.py` reads source files from `raw/inbox/` and creates wiki pages
- `scripts/query.py` answers questions from the generated wiki
- `scripts/lint.py` checks for weak pages and missing links
- `llm-wiki ...` provides a unified CLI across sync, ingest, query, lint, and refresh workflows
- `llm-wiki --version` reports the version from the tracked `VERSION` file
- `llm-wiki doctor` validates local config readiness, compatibility warnings, and tracked demo artifacts
- `make refresh` runs sync with prune and then rebuilds the wiki
- `make refresh-fast` runs incremental sync without prune, then ingests only new or changed raw files
- `llm-wiki ingest --dry-run` previews ingest actions and stale-source cleanup before writing anything
- `pytest` verifies sync naming, collision handling, and prune safety
- `ingest-settings.json` provides tracked default ingest heuristics, with `ingest-settings.local.json` available for local overrides

## Sync Configuration

Source syncing is driven by the tracked template [`sync-sources.json`](/Users/martez/src/llm-wiki-claude/sync-sources.json) and your machine-local override `sync-sources.local.json`.

Each source defines:

- `name`: stable label for the source repo
- `root`: absolute path to the source repo or docs folder
- `include`: glob patterns to copy
- `exclude`: glob patterns to skip
- `naming`: how files are named inside `raw/inbox/`

Recommended workflow:

1. Copy `sync-sources.json` to `sync-sources.local.json`
2. Replace the placeholder repo path with your real local source path
3. Keep `sync-sources.json` generic for the repo and use the local file for personal machine paths

Example template:

```json
{
  "sources": [
    {
      "name": "my-project",
      "root": "/Users/you/src/my-project",
      "include": ["README.md", "docs/**/*.md"],
      "exclude": ["**/*review*.md", "**/.DS_Store"],
      "naming": {
        "mode": "preserve_path",
        "prefix": "my-project"
      }
    }
  ]
}
```

## Sync Behavior

- Sync never silently overwrites a different source file that maps to the same raw filename.
- The default naming mode uses source-relative folders to produce stable names such as `agentmesh__docs__architecture.md`.
- If two different files still collide, the sync script adds a short deterministic hash suffix instead of replacing an existing file.
- File provenance is stored in [`state/sync_manifest.json`](/Users/martez/src/llm-wiki-claude/state/sync_manifest.json) so the raw copy can stay faithful to the original source file.
- `--prune` removes only files already managed by the sync manifest that are no longer matched by the current config.
- Prune never deletes unmanaged raw files, and it skips pruning for any configured source whose root path is currently missing.

## Ingest Configuration

Tracked defaults live in [`ingest-settings.json`](/Users/martez/src/llm-wiki-claude/ingest-settings.json).

- Copy it to `ingest-settings.local.json` if you want machine-local overrides.
- Use it to tune source truncation, topic/entity fanout, and filtering heuristics without editing Python.
- Incremental ingest now tracks per-source contributions inside shared topic/entity pages so changed or removed raw files can cleanly remove only their own sections.
- If you need to migrate older generated output that predates source-aware contributions, run `llm-wiki ingest --reconcile` once.

## Ingest Behavior

- `llm-wiki ingest --dry-run` writes a run summary to `state/last_ingest_run.json` and shows what would be processed, skipped, or cleaned.
- `state/ingest_events.jsonl` stores one JSON event per ingest step for lightweight local observability.
- `state/last_ingest_report.md` stores a human-readable ingest report with cleanup activity, page stats, processed files, and errors.
- Removed raw sources are cleaned from the manifest and have their prior summary/topic/entity contributions removed without forcing a full rebuild.
- Shared merged topic/entity pages are normalized after ingest, and obvious pluralized alias pages are folded into a canonical slug when possible.
- `--reconcile` is still available when you want a clean rebuild of derived wiki output from the current raw corpus.

## Source Types

- Text-like files: `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.csv`, `.py`
- PDF files with extractable text are also supported through `pypdf`

## Repo Layout

- Tracked: scripts, schema, docs, config templates, tests, CI, and starter metadata
- Ignored: `.env`, `sync-sources.local.json`, `raw/inbox/`, `wiki/`, `state/*.json`, and `.venv/`

## Demo

- A tiny tracked demo corpus lives in [`demo/`](/Users/martez/src/llm-wiki-claude/demo/README.md) for quick evaluation without pointing the repo at your own source tree.
- The demo now includes an Obsidian-friendly end-to-end workflow so you can treat the generated wiki as a local vault.
- Tracked sample outputs show what `index.md`, `state/last_ingest_run.json`, `state/last_ingest_report.md`, and representative generated wiki pages should look like after a healthy demo run.

## Publishing Notes

- The repo ships with a generic sync template, not a personal machine path.
- Local credentials stay in `.env`, which is ignored by git.
- CI validates the scripts and runs the test suite on every push and pull request.
- The project is licensed under Apache-2.0 in [`LICENSE`](/Users/martez/src/llm-wiki-claude/LICENSE).
- The support boundary, best-effort maintenance promise, and contribution expectations live in [`SUPPORT.md`](/Users/martez/src/llm-wiki-claude/SUPPORT.md).
- Contributor guidance lives in [`CONTRIBUTING.md`](/Users/martez/src/llm-wiki-claude/CONTRIBUTING.md).
- Release-process guidance, versioning expectations, and config-compatibility notes live in [`RELEASING.md`](/Users/martez/src/llm-wiki-claude/RELEASING.md).
- A reusable release-prep checklist lives in [`RELEASE_PREP_TEMPLATE.md`](/Users/martez/src/llm-wiki-claude/RELEASE_PREP_TEMPLATE.md).

## Notes

- The current extractor path supports text-like files such as `.txt`, `.md`, `.json`, `.yaml`, `.yml`, `.csv`, and `.py`
- PDFs with extractable text are supported
- The wiki pages are stored under `wiki/`
