# Support Policy

This project is a local-first starter/tool for building an LLM-backed markdown wiki from curated source material.

## Promise Of The Tool

- The project is provided as-is under the Apache-2.0 license
- No warranties are provided
- Best-effort maintenance will be made to keep the supported local workflows healthy and documented
- Issues and pull requests are welcome

## Product Promise

This tool is intended to compile curated context into a smaller, navigable wiki layer.

It is a strong fit for:

- employee onboarding docs
- product and project context
- architecture and design docs
- internal process documentation
- curated documentation from code repositories

It is strongest when:

- the source corpus is mostly durable text
- concepts, topics, and entities matter more than raw chunk retrieval
- the goal is a browsable knowledge layer plus grounded querying

It is not yet optimized for:

- full source-code understanding as the primary corpus
- highly noisy or fast-changing operational data
- broad multi-format document extraction beyond text-like files and PDFs
- precision citation or provenance at chunk-query granularity

## Supported Workflows

The supported core workflows are:

- install from source in a local Python environment
- `llm-wiki sync`
- `llm-wiki refresh-fast`
- `llm-wiki refresh`
- `llm-wiki ingest --dry-run`
- `llm-wiki doctor`
- `llm-wiki lint`

## Stability Expectations

- The repo is intended to be dependable for local, repo-first use
- Config compatibility should be preserved within minor releases when practical
- Breaking config or output-contract changes should be clearly documented and preferably reserved for major releases
- Tracked config templates should remain generic and safe to publish

## Best-Effort Boundaries

Best-effort maintenance means:

- bugs in supported workflows should be fixed when practical
- docs should be kept aligned with observable behavior
- compatibility warnings should be added before avoidable breaking config changes

Best-effort does not mean:

- guaranteed response times
- guaranteed support for every environment or source corpus
- guaranteed backward compatibility for experimental or lightly documented behavior

## Experimental Areas

These areas may evolve faster than the core workflows:

- merged-page cleanup heuristics
- PDF extraction quality
- generated sample output conventions

## Possible Directions

If the tool evolves well, reasonable future directions include:

- stronger onboarding and internal knowledge-base workflows
- richer code-repo context compilation from docs plus selected code signals
- broader source extractors for additional file types
- better provenance and query-time citation behavior
- stronger review, lint, and freshness workflows for compiled knowledge

## How To Ask For Help

- Open an issue with the command used, what happened, and what you expected
- Include `llm-wiki doctor` output when relevant
- Pull requests are welcome, especially for docs, tests, compatibility fixes, and workflow polish

## Multi-workspace issues

This section is the triage entry point for errors and confusion around the
`--workspace` flag, the `LLM_WIKI_WORKSPACE` environment variable, and fallback
resolution to the repo-root defaults. Error strings below are quoted
byte-for-byte from DESIGN.md §10 and `scripts/cli.py`; if your terminal shows a
different prefix, please file an issue.

### Workspace path does not exist

This single error covers BOTH the `--workspace` flag and the
`LLM_WIKI_WORKSPACE` environment variable. Both paths funnel through the same
`resolve_workspace()` code path and emit identical text:

```
Workspace error: /path/to/workspace does not exist. Run `llm-wiki init /path/to/workspace` first.
```

Triggers:

- You passed `--workspace <path>` to any command other than `init`, and `<path>`
  does not exist on disk.
- You set `LLM_WIKI_WORKSPACE=<path>` in your environment, and `<path>` does not
  exist on disk.

Fixes (pick one):

- Run `llm-wiki init <path>` to scaffold the workspace, then re-run your
  command.
- Unset or correct `LLM_WIKI_WORKSPACE` (`unset LLM_WIKI_WORKSPACE` in
  bash/zsh).
- Create the directory manually before re-running — but note `init` is the
  supported way to populate template files.

### Workspace exists but is missing required files

When the workspace directory exists but is missing a required subdirectory or
file (see DESIGN §7.1 FAIL conditions), non-`doctor` commands fail with:

```
Workspace error: /path/to/workspace is missing required files. Run `llm-wiki --workspace /path/to/workspace doctor` to see what is missing.
```

Triggers:

- The workspace directory exists, but a required subdirectory or file is absent
  or unreadable. Non-`doctor` commands intentionally defer diagnosis to
  `doctor`.

Fix:

- Run `llm-wiki --workspace <path> doctor` to see the FAIL/WARN listing.
- Address each FAIL item — typically by re-running `llm-wiki init <path>`, or
  by restoring a file you deleted by accident.

Note: missing `sync-sources.local.json` on its own is NOT this error. Per
DESIGN §10.2, `sync-sources.local.json` falls back silently to the repo-root
`sync-sources.json` — see "Why is my workspace using repo-root config?" below.

### Fallback confusion

Fallback resolution (a workspace file is missing, so the repo-root default is
used) is a feature, not an error. See DESIGN §11.

**"Why is my workspace using repo-root config?"**
Because the workspace does not own that local file. Run
`llm-wiki --workspace <path> doctor` and look at the resolution block; any line
that reads `fallback -> <path>` indicates a file being read from the repo-root
copy. To override, create the local file inside the workspace.

**"How do I know which file is active?"**
`llm-wiki --workspace <path> doctor` always prints a resolution block listing
each fallback-eligible file. Each entry is either `<filename>: <path>`
(workspace-owned) or `<filename>: fallback -> <path>` (served from the repo
root). For non-`doctor` commands, add `--verbose` (DESIGN §11.2) to print the
same resolution block before subcommand output.

**"My workspace seems to ignore my sync-sources."**
A missing `sync-sources.local.json` is a silent fallback to the repo-root
`sync-sources.json`, not an error. Users who want full isolation should create
`sync-sources.local.json` inside the workspace so `doctor` reports it as
workspace-owned rather than as `fallback -> ...`.

### Init issues

**"init refuses to run: target exists and is not empty"**
Per DESIGN §5.3 / §10.4 this is NOT actually an error. `init` silently skips
existing files and creates missing ones. If you perceive it as refusing to
run, you are most likely hitting a permission error (`Init error: cannot write
to <path> (permission denied).`) or DESIGN §10.3 (`init` target is a regular
file, not a directory). Check file/directory permissions and whether the path
is a plain file.

**"What does `init --force` actually overwrite?"**
`init --force` overwrites template files only. It never touches `raw/`,
`wiki/`, or `state/`. IMPORTANT: `--force` WILL overwrite `.env`, so back it
up before running if it contains secrets you have not recorded elsewhere.

**"init warned about creating a workspace inside a git repo."**
This warning is intentional (DESIGN §6.6). The workspace `.gitignore` excludes
`raw/`, `wiki/`, `state/`, and `.env`, but the outer repo may have its own
ignore rules that do not exclude those paths, which can cause generated
artifacts to be committed by accident. Decide whether to keep the workspace
inside the outer repo (and verify the outer repo's ignore rules) or move the
workspace to its own directory tree outside the repo.
