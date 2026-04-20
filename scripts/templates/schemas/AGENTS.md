# LLM Wiki Agent Rules

## Purpose
Maintain a persistent markdown wiki derived from source files in /raw.

## Source of truth
- Files in /raw are immutable source material.
- Never modify files in /raw.
- Files in /wiki are derived knowledge artifacts.
- Prefer product docs, architecture notes, specs, and durable design documents over workflow exhaust.
- Ignore opaque task fragments, review checklists, reviewer-targeted prompts, and OS/editor junk.

## Folder conventions
- /wiki/summaries: one page per ingested source
- /wiki/topics: concept/topic pages synthesized across sources
- /wiki/entities: people, companies, products, places, etc.

## Ingest workflow
For each new or changed source:
1. Read the raw source.
2. Create or update a summary page in /wiki/summaries.
3. Extract important topics and entities.
4. Create or update related pages in /wiki/topics and /wiki/entities.
5. Add cross-links between related pages.
6. Append a short line to /log.md.
7. Update /index.md if new pages were added.

When choosing topics and entities:
- Favor durable concepts a human would browse repeatedly.
- Do not create pages for issue IDs, task IDs, filenames, test names, structs, interfaces, methods, env vars, or other code-symbol bookkeeping.
- Keep the page fanout small and high-signal; a single source should usually update a handful of pages, not dozens.

## Page format
Each wiki page should contain:
- Title
- Last updated
- Source references
- Summary
- Key facts
- Open questions
- Related pages

## Query workflow
When answering a question:
1. Read /index.md first.
2. Inspect the most relevant wiki pages.
3. Read source summaries before raw material.
4. Use raw material only if needed.
5. Answer grounded in the wiki.
6. Do not invent facts not supported by sources.

## Lint workflow
Check for:
- orphan pages
- duplicate pages
- stale pages
- weak or missing links
- contradictory claims
- pages with no source references
