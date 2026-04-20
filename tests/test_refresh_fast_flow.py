"""End-to-end ``refresh-fast`` flow tests (LWC-1idw refactor).

Prior to LWC-1idw this file hand-built a tmp workspace, monkeypatched ingest
module internals (including the deprecated ``ingest.call_claude_json``), and
set the LLM_WIKI_WORKSPACE env var twice.  After the refactor:

* The shared ``tmp_workspace`` fixture from ``tests/conftest.py`` (LWC-tkbs)
  provides the baseline workspace.  The test adds only what's missing:
  a ``sync-sources.local.json`` that points at a real source root, plus
  ``.wikiignore`` and ``schemas/AGENTS.md`` for ingest.
* ``sync.main([], workspace)`` and ``ingest.main([], workspace)`` are called
  directly against the fixture-built ``WorkspacePaths``.
* The Claude mock patches ``scripts.claude_api.call_claude`` -- as imported
  into ``scripts.ingest`` -- and returns a real ``ClaudeCallResult`` whose
  ``text`` field is JSON-shaped so the real ``ingest.call_claude_json``
  parser produces non-empty wiki output.  Returning raw echo text would pass
  trivially with zero wiki files; the non-emptiness asserts below catch that.
* The summary line printed by ingest ("Used ... tokens this run.") is
  captured and asserted to be the final line of stdout.

See ARCHITECTURE §5.3, §10.4, §11.1, §11.3.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# After LWC-4z0t, cli dispatches to ``scripts.ingest.main`` via DISPATCH, so
# tests must patch the canonical ``scripts.ingest``/``scripts.sync`` modules
# that cli imports -- not a sibling instance loaded via importlib.
from scripts import cli, ingest, sync  # noqa: E402
from scripts.claude_api import ClaudeCallResult  # noqa: E402


DEFAULT_PAYLOAD = {
    "title": "Demo Source",
    "summary": "Summary",
    "key_facts": ["Fact"],
    "topics": ["Capability Registry"],
    "entities": ["DemoMesh"],
    "open_questions": [],
    "topic_summaries": {"Capability Registry": "Registry summary"},
    "entity_summaries": {"DemoMesh": "Entity summary"},
}


def _claude_api_mock(payload: dict, *, input_tokens: int = 123, output_tokens: int = 45):
    """Return a ``scripts.claude_api.call_claude`` replacement.

    Matches the keyword-only signature of the real ``call_claude`` and returns
    a ``ClaudeCallResult`` whose ``text`` is a JSON-shaped payload the ingest
    parser accepts.  ``**kw`` tolerates any future keyword additions without
    breaking this test.
    """

    def _fn(*, client=None, model="fake-model", system=None, messages=None,
            max_tokens=None, context=None, workspace=None, log_event=True, **kw):
        return ClaudeCallResult(
            text=json.dumps(payload),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )

    return _fn


def _write_sync_config(workspace, source_root: Path) -> None:
    """Overwrite the fixture's sync-sources.local.json with a real source."""
    payload = {
        "schema_version": 1,
        "sources": [
            {
                "name": "demo",
                "root": str(source_root),
                "include": ["README.md"],
                "exclude": [],
                "naming": {"mode": "preserve_path", "prefix": "demo"},
            }
        ],
    }
    workspace.sync_config_path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_schema(workspace) -> None:
    (workspace.root / "schemas").mkdir(parents=True, exist_ok=True)
    (workspace.root / "schemas" / "AGENTS.md").write_text("Schema", encoding="utf-8")
    (workspace.root / ".wikiignore").write_text("", encoding="utf-8")


def test_refresh_fast_sync_then_ingest_populates_wiki(tmp_workspace, tmp_path, monkeypatch, capsys):
    """sync.main + ingest.main on the fixture workspace -> populated raw/wiki.

    Covers LWC-1idw AC 4 directly: summary line prints, and every structural
    output directory (raw/inbox, wiki/summaries, wiki/topics, wiki/entities)
    contains >= 1 file after the flow completes.
    """
    # Source checkout with a single README.md that sync will pick up.
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Demo Source\n\nA durable product README.",
        encoding="utf-8",
    )

    # Overwrite the conftest fixture's sync config so it targets our source
    # root; add schemas/ and .wikiignore that ingest requires.
    _write_sync_config(tmp_workspace, source_root)
    _seed_schema(tmp_workspace)
    # Drop the placeholder raw file so the only file ingest sees comes from
    # sync -- keeps the "files produced by sync" assertion precise.
    placeholder = tmp_workspace.raw_dir / "placeholder.md"
    if placeholder.exists():
        placeholder.unlink()

    # --- Sync phase: sync.main writes into tmp_workspace.raw_dir. ---
    rc_sync = sync.main([], tmp_workspace)
    assert rc_sync == 0

    raw_files = sorted(p.name for p in tmp_workspace.raw_dir.iterdir())
    assert raw_files == ["demo__readme.md"], (
        f"sync did not populate raw/inbox as expected: got {raw_files}"
    )

    # --- Ingest phase: mock call_claude (JSON-shaped result), call main. ---
    monkeypatch.setattr(ingest, "init_client", lambda: ("fake-client", "fake-model"))
    monkeypatch.setattr(ingest, "call_claude", _claude_api_mock(DEFAULT_PAYLOAD))

    rc_ingest = ingest.main([], tmp_workspace)
    assert rc_ingest == 0

    # Non-emptiness precondition (LWC-1idw AC 3): every wiki subdir has >= 1
    # file.  Empty output means the mock failed to produce parseable JSON; we
    # want the test to fail loudly in that case rather than silently passing.
    summaries = list(tmp_workspace.summaries_dir.glob("*.md"))
    topics = list(tmp_workspace.topics_dir.glob("*.md"))
    entities = list(tmp_workspace.entities_dir.glob("*.md"))
    assert len(summaries) >= 1, f"wiki/summaries empty after ingest: got {summaries}"
    assert len(topics) >= 1, f"wiki/topics empty after ingest: got {topics}"
    assert len(entities) >= 1, f"wiki/entities empty after ingest: got {entities}"

    # Specific expected artifact from the single raw file.
    assert (tmp_workspace.summaries_dir / "demo-readme.md").exists()

    # Run summary JSON matches processed count.
    summary = json.loads(tmp_workspace.last_ingest_run_path.read_text(encoding="utf-8"))
    assert summary["processed"] == 1

    # Summary line is the last line of stdout and uses _format_tokens.
    out = capsys.readouterr().out
    last_line = out.strip().splitlines()[-1]
    expected = (
        f"Used {ingest._format_tokens(123)} input "
        f"/ {ingest._format_tokens(45)} output tokens this run."
    )
    assert last_line == expected

    # And the run_summary event is exactly one entry at the end of the log.
    events = [
        json.loads(line)
        for line in tmp_workspace.ingest_events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    run_summaries = [e for e in events if e["event"] == "run_summary"]
    assert len(run_summaries) == 1
    assert run_summaries[0]["total_input_tokens"] == 123
    assert run_summaries[0]["total_output_tokens"] == 45
    assert run_summaries[0]["api_call_count"] == 1


def test_refresh_fast_cli_dispatch_end_to_end(tmp_workspace, tmp_path, monkeypatch, capsys):
    """``cli.main(['refresh-fast'])`` with ``LLM_WIKI_WORKSPACE`` set runs the
    full flow through DISPATCH (LWC-4z0t) and produces the same populated wiki.

    Complements the direct-main test above by proving the CLI wiring is intact:
    a user running ``llm-wiki refresh-fast`` against this workspace gets the
    same end state as calling sync.main + ingest.main manually.
    """
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "# Demo Source\n\nA durable product README.",
        encoding="utf-8",
    )

    _write_sync_config(tmp_workspace, source_root)
    _seed_schema(tmp_workspace)
    placeholder = tmp_workspace.raw_dir / "placeholder.md"
    if placeholder.exists():
        placeholder.unlink()

    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(tmp_workspace.root))
    monkeypatch.setattr(ingest, "init_client", lambda: ("fake-client", "fake-model"))
    monkeypatch.setattr(ingest, "call_claude", _claude_api_mock(DEFAULT_PAYLOAD))

    rc = cli.main(["refresh-fast"])
    assert rc == 0

    # Sync populated raw/inbox.
    assert (tmp_workspace.raw_dir / "demo__readme.md").exists()

    # Ingest populated each wiki subdir.
    assert list(tmp_workspace.summaries_dir.glob("*.md"))
    assert list(tmp_workspace.topics_dir.glob("*.md"))
    assert list(tmp_workspace.entities_dir.glob("*.md"))
    assert (tmp_workspace.summaries_dir / "demo-readme.md").exists()

    summary = json.loads(tmp_workspace.last_ingest_run_path.read_text(encoding="utf-8"))
    assert summary["processed"] == 1

    # Summary line printed.
    out = capsys.readouterr().out
    assert "tokens this run." in out
    last_line = out.strip().splitlines()[-1]
    assert last_line.startswith("Used ")
    assert last_line.endswith(" tokens this run.")
