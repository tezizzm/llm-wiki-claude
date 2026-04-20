"""End-to-end test for the ingest refactor epic (LWC-6snv capstone, story LWC-euj4).

This is the ONE integration test in the ingest epic that exercises the full
pipeline: ``llm-wiki --workspace X ingest`` through CLI dispatch, with a real
init-scaffolded workspace, a real source file in ``raw/inbox/``, the real
ingest parser, the real wiki writers, and the real run_summary event emission.

Nothing in the pipeline itself is mocked. The ONLY mock is
``scripts.claude_api.call_claude`` -- i.e. the upstream Anthropic SDK call.
That mock returns a ``ClaudeCallResult`` whose ``.text`` is a JSON payload the
ingest parser accepts, NOT a raw-text echo.  Raw-text echo would throw in
``json.loads`` inside ``call_claude_json`` and the ingest pipeline would never
produce wiki pages -- the non-emptiness precondition below is the guard against
that vacuous-pass mode.

Per LWC-1idw the canonical interception point for this mock is
``scripts.ingest.call_claude`` (the reference bound into the ingest namespace
at import time; patching ``scripts.claude_api.call_claude`` alone does not
redirect the already-bound reference).  We patch both attributes so the mock
works regardless of which path a future caller takes.

Acceptance criteria mapping
---------------------------

AC 1: this file exists with ``test_ingest_end_to_end_produces_wiki``.
AC 2: scaffold via ``init.main`` (not filesystem shortcuts).
AC 3: seed ``raw/inbox/`` with a source file, mock only ``call_claude``.
AC 4: ``ClaudeCallResult`` with JSON-shaped ``.text``.
AC 5: non-emptiness precondition (>= 1 file in ``wiki/summaries/``).
AC 6: ``run_summary`` event present exactly once with the correct workspace.
AC 7: env hygiene via ``monkeypatch.delenv``/``setenv``.
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.cli as cli
from scripts import init as init_mod
from scripts.claude_api import ClaudeCallResult


def test_ingest_end_to_end_produces_wiki(tmp_path: Path, monkeypatch) -> None:
    """Full --workspace ingest path produces non-empty wiki output.

    Flow:
      1. ``init.main`` scaffolds a fresh workspace under ``tmp_path/ws``.
      2. A single source file is written into ``raw/inbox/`` (bypassing sync,
         which is out of scope for this test).
      3. ``scripts.claude_api.call_claude`` is replaced with a fake that
         returns a ``ClaudeCallResult`` whose ``.text`` is a JSON object the
         ingest parser accepts.  We also patch the ingest module's already-
         imported reference; LWC-1idw documents why both targets matter.
      4. Env hygiene: ``LLM_WIKI_WORKSPACE`` is cleared so the --workspace
         flag is authoritative, and ``ANTHROPIC_API_KEY`` is set to a dummy
         value so ``init_client`` does not raise before dispatch reaches the
         patched call site.
      5. ``cli.main(['--workspace', ws, 'ingest'])`` runs the real dispatch.
      6. After the run: summaries exist, index.md/log.md are populated, and
         exactly one ``run_summary`` event landed in
         ``state/ingest_events.jsonl`` with the correct workspace field.
    """

    # 1. Scaffold a workspace via init.main -- AC 2.
    ws_root = tmp_path / "ws"
    init_rc = init_mod.main([str(ws_root)])
    assert init_rc == 0, f"init.main exit {init_rc} (expected 0)"
    ws_root = ws_root.resolve()

    # 2. Seed raw/inbox/ with one durable-knowledge source file -- AC 3.
    # Content is above the low-signal filter (>= ~200 chars) so ingest actually
    # routes it through the model-call path instead of skipping it.
    inbox = ws_root / "raw" / "inbox"
    assert inbox.is_dir(), (
        f"init did not create raw/inbox/ under {ws_root}; "
        "scaffolding contract broken"
    )
    (inbox / "source_x.md").write_text(
        "# Source X\n" + ("Durable knowledge about the X subsystem. " * 30),
        encoding="utf-8",
    )
    # ``init`` also drops a placeholder.md that ingest will see.  That is fine
    # -- ingest treats each file independently and the mock returns the same
    # payload regardless of context.

    # 3. JSON-shaped mock for ``call_claude`` -- AC 3 + AC 4.
    def fake_call_claude(
        *,
        client=None,
        model="fake-model",
        system=None,
        messages=None,
        max_tokens=None,
        context=None,
        workspace=None,
        log_event=True,
        **kw,
    ):
        # The ingest parser expects a JSON object in .text.  The shape below
        # is the minimal subset that passes ``call_claude_json`` -> the per-
        # source write path -> update_index / append_log without raising.
        payload = {
            "title": f"Summary of {context}",
            "summary": f"Durable knowledge summary for {context}.",
            "key_facts": [f"Fact about {context}"],
            "topics": [],
            "entities": [],
            "open_questions": [],
            "topic_summaries": {},
            "entity_summaries": {},
            "references": [context],
        }
        return ClaudeCallResult(
            text=json.dumps(payload),
            input_tokens=50,
            output_tokens=25,
            model=model,
        )

    # Patch at the source module (AC 3 literal) AND at ingest's imported
    # reference.  The latter is what call_claude_json actually looks up; the
    # former is a defense-in-depth hook for any future caller that imports
    # call_claude from the wrapper lazily.
    monkeypatch.setattr("scripts.claude_api.call_claude", fake_call_claude)
    monkeypatch.setattr("scripts.ingest.call_claude", fake_call_claude)

    # 4. Env hygiene -- AC 7.
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-ingest-e2e")

    # 5. Run through the real cli.main dispatch -- exercises --workspace
    # resolution + DISPATCH['ingest'] + ingest.main(argv, workspace).
    rc = cli.main(["--workspace", str(ws_root), "ingest"])
    assert rc == 0, f"cli.main returned {rc}; ingest should succeed"

    # 6. Non-emptiness precondition -- AC 5.  This is the guard against the
    # vacuous-pass mode where the mock returns raw text and the parser
    # silently produces empty wiki output.
    summaries_dir = ws_root / "wiki" / "summaries"
    assert summaries_dir.is_dir(), (
        f"{summaries_dir} missing -- ingest did not run to completion"
    )
    summaries = list(summaries_dir.iterdir())
    assert len(summaries) >= 1, (
        "ingest produced no summary pages; either the JSON mock shape is "
        "wrong or the fake did not actually intercept call_claude"
    )

    # index.md and log.md exist and are non-empty.  index.md is rewritten on
    # every ingest; log.md has append-per-source lines.
    index_path = ws_root / "index.md"
    log_path = ws_root / "log.md"
    assert index_path.exists(), f"{index_path} missing"
    assert log_path.exists(), f"{log_path} missing"
    assert log_path.read_text(encoding="utf-8").strip() != "", (
        f"{log_path} is empty; append_log never fired for any source"
    )

    # 7. run_summary event present exactly once with the right workspace
    # field -- AC 6.  ingest_events.jsonl is JSONL; filter for the end-of-run
    # event and verify both the count and the workspace string.
    events_path = ws_root / "state" / "ingest_events.jsonl"
    assert events_path.exists(), (
        f"{events_path} missing; ingest did not emit any events"
    )
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    run_summaries = [e for e in events if e.get("event") == "run_summary"]
    assert len(run_summaries) == 1, (
        f"expected exactly one run_summary event, got {len(run_summaries)}; "
        f"events={events!r}"
    )
    assert run_summaries[0]["workspace"] == str(ws_root), (
        f"run_summary.workspace mismatch: "
        f"got {run_summaries[0]['workspace']!r}, expected {str(ws_root)!r}"
    )
