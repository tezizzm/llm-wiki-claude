# Demo Dataset

This folder contains a tiny tracked corpus you can use to understand the starter workflow without pointing at your own repository first.

Suggested flow:

1. Create a temporary local sync override that points at `demo/raw-inbox/`, or copy the files in `demo/raw-inbox/` into `raw/inbox/`
2. Run `llm-wiki ingest --dry-run` to preview what will be generated
3. Run `llm-wiki ingest --reconcile` for a clean first build
4. Inspect `wiki/`, `index.md`, and `state/last_ingest_run.json`

## Sample Artifacts

If you want to understand the output shape before running anything, inspect the tracked sample artifacts:

- `demo/sample-output/index.md`
- `demo/sample-output/last_ingest_run.json`
- `demo/sample-output/last_ingest_report.md`
- `demo/sample-output/wiki/`

## Obsidian Workflow

1. Open this repo folder as an Obsidian vault
2. Add `wiki/`, `index.md`, and `log.md` to your favorites or bookmarks
3. Use `index.md` as the human entrypoint and let Obsidian backlinks show topic/entity connections
4. Run `make refresh-fast` while writing source material, then refresh Obsidian to pick up new wiki pages
5. Use `llm-wiki query` when you want an LLM answer grounded in the generated vault instead of manually browsing pages

The demo corpus is intentionally small and human-readable.
