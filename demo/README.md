# Demo Dataset

This folder contains a tiny tracked corpus you can use to understand the starter workflow without pointing at your own repository first.

Suggested flow:

1. Create a temporary local sync override that points at `demo/raw-inbox/`, or copy the files in `demo/raw-inbox/` into `raw/inbox/`
2. Run `llm-wiki ingest --dry-run` to preview what will be generated
3. Run `llm-wiki ingest --reconcile` for a clean first build
4. Inspect `wiki/`, `index.md`, and `state/last_ingest_run.json`

## Running the demo

The demo runs against the repo-root workspace by default:

```bash
llm-wiki doctor
make refresh         # or: make refresh-fast
make query
make lint
```

No `--workspace` flag is needed. The demo intentionally exercises the repo-root default to pin backward compatibility with 0.2.0.

## Running the demo against a separate workspace (optional)

If you want to see how the multi-workspace feature works using the demo data:

```bash
llm-wiki --workspace /tmp/llm-wiki-demo init
cp -r demo/* /tmp/llm-wiki-demo/     # or symlink to demo sources in /tmp/llm-wiki-demo/sync-sources.local.json
llm-wiki --workspace /tmp/llm-wiki-demo refresh-fast
llm-wiki --workspace /tmp/llm-wiki-demo query
```

This is entirely optional; the default demo runs in the repo root.

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
