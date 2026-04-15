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
