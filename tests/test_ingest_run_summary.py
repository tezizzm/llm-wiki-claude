"""Tests for LWC-n3um: claude_api.call_claude integration + run_summary event.

These tests cover the acceptance criteria documented in the story:

1. ingest.py imports nothing from ``anthropic`` directly.
2. Totals accumulate across every ``call_claude`` invocation.
3. A ``run_summary`` event is appended to ``workspace.ingest_events_path``
   just before ``main()`` returns 0, with the exact shape required by
   ARCHITECTURE §10.4.
4. The end-of-run stdout summary line follows the ``_format_tokens`` rules
   (``~12.3K`` / bare integer) and is the final line of stdout.
5. Atomicity: mid-run crashes leave no ``run_summary`` event and no summary
   line; zero-call success still emits both; ``run_summary`` appears exactly
   once per successful run.
6. Integration: end-to-end, a mocked ``call_claude`` wrapper (not the raw SDK)
   drives ingest through ``main()`` and produces the expected artifacts.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Load ingest via spec like the sibling tests do -- keeps the in-process module
# identity stable so monkeypatching ``ingest.call_claude_json`` works cleanly.
INGEST_PATH = ROOT / "scripts" / "ingest.py"
_spec = importlib.util.spec_from_file_location("ingest_module_lwc_n3um", INGEST_PATH)
ingest = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(ingest)

from scripts import claude_api
from scripts.workspace import resolve_workspace


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def _write_ingest_settings(root: Path) -> None:
    """Copy the tracked ingest-settings.json into ``root`` so main() can load it."""

    (root / "ingest-settings.json").write_text(
        (ROOT / "ingest-settings.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _write_schema(root: Path) -> None:
    schemas = root / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "AGENTS.md").write_text("schema", encoding="utf-8")


def _build_workspace(root: Path, *, with_sources: int = 1):
    """Set up a minimal workspace rooted at ``root`` and return WorkspacePaths."""

    root.mkdir(parents=True, exist_ok=True)
    raw = root / "raw" / "inbox"
    raw.mkdir(parents=True, exist_ok=True)
    (root / ".wikiignore").write_text("", encoding="utf-8")
    _write_schema(root)
    _write_ingest_settings(root)
    for idx in range(with_sources):
        (raw / f"src{idx}.md").write_text(
            f"# Source {idx}\n\nContent {idx}.\n", encoding="utf-8"
        )
    return resolve_workspace(str(root), None)


def _read_events(workspace) -> List[dict]:
    path = workspace.ingest_events_path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fake_response(text: str, input_tokens: int, output_tokens: int) -> SimpleNamespace:
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FakeClient:
    """Stand-in for an ``anthropic.Anthropic`` client.

    Each call to ``messages.create`` pops the next response from
    ``self._responses`` (a list of ``(text, input_tokens, output_tokens)``
    tuples).  Tests drive token counts through this queue.
    """

    def __init__(self, responses: List[tuple[str, int, int]]):
        self._responses = list(responses)
        self.calls: List[dict] = []
        self.messages = self._Messages(self)

    class _Messages:
        def __init__(self, outer: "_FakeClient"):
            self._outer = outer

        def create(self, **kwargs: Any) -> Any:
            self._outer.calls.append(kwargs)
            text, inp, out = self._outer._responses.pop(0)
            return _fake_response(text, inp, out)


VALID_RESPONSE_JSON = json.dumps(
    {
        "title": "Demo",
        "summary": "Short summary.",
        "key_facts": ["Fact one"],
        "topics": [],
        "entities": [],
        "open_questions": [],
        "topic_summaries": {},
        "entity_summaries": {},
    }
)


# ---------------------------------------------------------------------------
# AC 1: no direct anthropic import from scripts/ingest.py
# ---------------------------------------------------------------------------


def test_ingest_no_direct_anthropic_import():
    """scripts/ingest.py must not import ``anthropic`` directly.

    All SDK access flows through ``scripts.claude_api``; ingest may depend on
    that wrapper module, but must not reference ``anthropic`` itself.
    """

    text = INGEST_PATH.read_text(encoding="utf-8")
    # Strip comments and docstrings-ish content naively -- any line with an
    # import must not mention the ``anthropic`` package as a top-level import.
    assert not re.search(r"(?m)^\s*import\s+anthropic\b", text), (
        "scripts/ingest.py imports the anthropic SDK directly"
    )
    assert not re.search(r"(?m)^\s*from\s+anthropic\b", text), (
        "scripts/ingest.py imports from the anthropic SDK directly"
    )


# ---------------------------------------------------------------------------
# _read_events helper coverage: missing-file branch
#
# The populated-file branch is covered end-to-end by every main()-driven test
# below.  The missing-file branch is not otherwise reached (all main() paths
# emit at least the run_summary event before any ``_read_events`` call), so we
# exercise it directly against a real filesystem workspace here.  No mocks.
# ---------------------------------------------------------------------------


def test_read_events_returns_empty_list_when_events_file_missing(tmp_path):
    """Missing-file branch of ``_read_events``: returns [] via FileNotFoundError."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=0)
    assert not workspace.ingest_events_path.exists()
    assert _read_events(workspace) == []


# ---------------------------------------------------------------------------
# AC 2 + 7: totals accumulate across calls / event+line agree
# ---------------------------------------------------------------------------


def test_ingest_totals_accumulate_across_calls(tmp_path, monkeypatch, capsys):
    """totals['input']/totals['output'] are the sum of every call's usage."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=3)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_INGEST_MODEL", "claude-haiku-4-5")

    client = _FakeClient(
        [
            (VALID_RESPONSE_JSON, 100, 10),
            (VALID_RESPONSE_JSON, 200, 20),
            (VALID_RESPONSE_JSON, 700, 30),
        ]
    )
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    capsys.readouterr()

    events = _read_events(workspace)
    run_summary = [e for e in events if e["event"] == "run_summary"]
    assert len(run_summary) == 1
    assert run_summary[0]["total_input_tokens"] == 1000
    assert run_summary[0]["total_output_tokens"] == 60
    assert run_summary[0]["api_call_count"] == 3


def test_ingest_run_summary_and_summary_line_agree(tmp_path, monkeypatch, capsys):
    """The run_summary event fields and the printed summary line agree on totals."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=2)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = _FakeClient(
        [
            (VALID_RESPONSE_JSON, 1500, 400),
            (VALID_RESPONSE_JSON, 2500, 600),
        ]
    )
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    run_summary = [e for e in _read_events(workspace) if e["event"] == "run_summary"][0]
    summary_line = out.strip().splitlines()[-1]
    # Line format: "Used ~4.0K input / ~1.0K output tokens this run."
    expected = (
        f"Used {ingest._format_tokens(run_summary['total_input_tokens'])} input "
        f"/ {ingest._format_tokens(run_summary['total_output_tokens'])} output tokens this run."
    )
    assert summary_line == expected


# ---------------------------------------------------------------------------
# AC 3: run_summary event shape
# ---------------------------------------------------------------------------


def test_ingest_run_summary_event_shape(tmp_path, monkeypatch, capsys):
    """The event emitted at end of main() carries the exact fields ARCHITECTURE §10.4 requires."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = _FakeClient([(VALID_RESPONSE_JSON, 321, 42)])
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    capsys.readouterr()

    events = _read_events(workspace)
    last = events[-1]
    assert last["event"] == "run_summary"
    assert set(last.keys()) == {
        "event",
        "ts",
        "workspace",
        "model",
        "total_input_tokens",
        "total_output_tokens",
        "api_call_count",
    }
    # ts is ISO 8601 with tz offset (parseable by fromisoformat on 3.11+).
    import datetime as dt
    assert dt.datetime.fromisoformat(last["ts"]).tzinfo is not None
    # workspace is an absolute path string (== str(workspace.root)).
    assert last["workspace"] == str(workspace.root)
    assert Path(last["workspace"]).is_absolute()
    assert last["model"] == "claude-haiku-4-5"
    assert last["total_input_tokens"] == 321
    assert last["total_output_tokens"] == 42
    assert last["api_call_count"] == 1


# ---------------------------------------------------------------------------
# AC 4: summary line format (non-zero + zero-call)
# ---------------------------------------------------------------------------


def test_ingest_summary_line_format_nonzero(tmp_path, monkeypatch, capsys):
    """Non-zero totals render as ``~{n/1000:.1f}K`` (one decimal)."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = _FakeClient([(VALID_RESPONSE_JSON, 12345, 3120)])
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    last_line = out.strip().splitlines()[-1]
    assert last_line == "Used ~12.3K input / ~3.1K output tokens this run."


def test_ingest_summary_line_format_zero(tmp_path, monkeypatch, capsys):
    """Zero-call run prints 'Used 0 input / 0 output tokens this run.' (no tilde)."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # No sources -> init_client is still called, but no call_claude invocations.
    client = _FakeClient([])
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    last_line = out.strip().splitlines()[-1]
    assert last_line == "Used 0 input / 0 output tokens this run."


# ---------------------------------------------------------------------------
# AC 5: summary line is the last line of stdout
# ---------------------------------------------------------------------------


def test_ingest_summary_line_always_last(tmp_path, monkeypatch, capsys):
    """No stdout line follows the summary line on the success path."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=2)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = _FakeClient(
        [
            (VALID_RESPONSE_JSON, 10, 5),
            (VALID_RESPONSE_JSON, 20, 5),
        ]
    )
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    lines = out.splitlines()
    # Drop trailing blank lines then confirm the final non-empty line is the
    # summary line; anything after it would fail this assertion.
    while lines and not lines[-1].strip():
        lines.pop()
    assert lines, "expected stdout"
    assert lines[-1].startswith("Used ") and lines[-1].endswith(" this run.")
    # Also: the summary line must not appear earlier and then be followed by
    # other output.
    summary_indices = [i for i, line in enumerate(lines) if line.startswith("Used ") and line.endswith(" this run.")]
    assert summary_indices == [len(lines) - 1]


# ---------------------------------------------------------------------------
# AC 6: atomicity -- mid-run crash suppresses run_summary and summary line
# ---------------------------------------------------------------------------


def test_ingest_run_summary_absent_on_midrun_crash(tmp_path, monkeypatch, capsys):
    """A crash mid-run (uncaught exception) leaves no run_summary event.

    ``ingest.main()`` wraps the per-file ``ingest_file`` call in its own
    ``try/except Exception`` to record per-source failures without aborting
    the whole run -- so a direct raise from ``call_claude_json`` is handled
    gracefully.  The atomicity contract targets crashes that main() does NOT
    handle (e.g. a failure in ``save_manifest`` between files): the run
    aborts, the exception propagates, and run_summary must not appear.
    """

    workspace = _build_workspace(tmp_path / "ws", with_sources=2)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    monkeypatch.setattr(ingest, "init_client", lambda: (object(), "claude-haiku-4-5"))

    def call_json_updating_totals(*args, **kwargs):
        totals = kwargs["totals"]
        totals["input"] += 50
        totals["output"] += 10
        totals["calls"] += 1
        return json.loads(VALID_RESPONSE_JSON)

    monkeypatch.setattr(ingest, "call_claude_json", call_json_updating_totals)

    # Fail on the SECOND save_manifest call -- after one successful ingest,
    # before the loop finishes.  This is an uncaught exception in main().
    call_count = {"n": 0}
    original_save_manifest = ingest.save_manifest

    def failing_save_manifest(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated mid-run failure")
        return original_save_manifest(*args, **kwargs)

    monkeypatch.setattr(ingest, "save_manifest", failing_save_manifest)

    with pytest.raises(RuntimeError, match="simulated mid-run failure"):
        ingest.main([], workspace)

    out = capsys.readouterr().out
    events = _read_events(workspace)
    assert not any(e["event"] == "run_summary" for e in events)
    # Summary line must not appear anywhere in captured stdout.
    assert "tokens this run." not in out


def test_ingest_run_summary_absent_when_write_pages_crashes(tmp_path, monkeypatch, capsys):
    """A crash in the wiki-write step suppresses run_summary and summary line."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    monkeypatch.setattr(
        ingest,
        "init_client",
        lambda: (object(), "claude-haiku-4-5"),
    )
    monkeypatch.setattr(
        ingest,
        "call_claude_json",
        lambda *args, **kwargs: json.loads(VALID_RESPONSE_JSON),
    )

    def broken_update_index(_workspace):
        raise IOError("simulated write failure")

    monkeypatch.setattr(ingest, "update_index", broken_update_index)

    with pytest.raises(IOError, match="simulated write failure"):
        ingest.main([], workspace)

    out = capsys.readouterr().out
    events = _read_events(workspace)
    assert not any(e["event"] == "run_summary" for e in events)
    assert "this run." not in out


# ---------------------------------------------------------------------------
# AC 7: zero-call success still emits run_summary
# ---------------------------------------------------------------------------


def test_ingest_run_summary_present_on_zero_call_success(tmp_path, monkeypatch, capsys):
    """Zero sources -> main() returns 0 -> run_summary with api_call_count=0."""

    workspace = _build_workspace(tmp_path / "ws", with_sources=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(ingest, "init_client", lambda: (object(), "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    events = _read_events(workspace)
    run_summaries = [e for e in events if e["event"] == "run_summary"]
    assert len(run_summaries) == 1
    rs = run_summaries[0]
    assert rs["api_call_count"] == 0
    assert rs["total_input_tokens"] == 0
    assert rs["total_output_tokens"] == 0
    assert out.strip().splitlines()[-1] == "Used 0 input / 0 output tokens this run."


# ---------------------------------------------------------------------------
# AC 8: run_summary appears exactly once per successful run
# ---------------------------------------------------------------------------


def test_ingest_run_summary_appears_exactly_once_on_success(tmp_path, monkeypatch, capsys):
    """Back-to-back runs against fresh workspaces each produce exactly one run_summary."""

    for idx in range(2):
        workspace = _build_workspace(tmp_path / f"ws{idx}", with_sources=1)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = _FakeClient([(VALID_RESPONSE_JSON, 10, 5)])
        monkeypatch.setattr(ingest, "init_client", lambda c=client: (c, "claude-haiku-4-5"))

        rc = ingest.main([], workspace)
        assert rc == 0
        capsys.readouterr()

        run_summaries = [e for e in _read_events(workspace) if e["event"] == "run_summary"]
        assert len(run_summaries) == 1, (
            f"workspace {idx} had {len(run_summaries)} run_summary events"
        )


# ---------------------------------------------------------------------------
# AC 9: no cost fields
# ---------------------------------------------------------------------------


def test_run_summary_has_no_cost_fields(tmp_path, monkeypatch, capsys):
    """No ``$`` / ``cost`` / ``usd`` / ``price`` tokens appear in the event keys or values.

    The workspace path goes into the event verbatim; pytest's tmp_path can
    contain arbitrary words derived from the test function name, so we scrub
    that field before scanning for forbidden economic tokens.
    """

    workspace = _build_workspace(tmp_path / "ws", with_sources=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = _FakeClient([(VALID_RESPONSE_JSON, 10, 5)])
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    assert ingest.main([], workspace) == 0
    capsys.readouterr()

    run_summary = [e for e in _read_events(workspace) if e["event"] == "run_summary"][0]
    scrubbed = {k: v for k, v in run_summary.items() if k != "workspace"}
    serialized = json.dumps(scrubbed).lower()
    for forbidden in ("$", "cost", "usd", "price"):
        assert forbidden not in serialized, (
            f"event contained forbidden economic token {forbidden!r}: {scrubbed}"
        )
    # The workspace field's KEY name must also be clean.
    assert "workspace" in run_summary


# ---------------------------------------------------------------------------
# Integration: end-to-end through claude_api wrapper
# ---------------------------------------------------------------------------


def test_ingest_end_to_end_with_mocked_claude_api(tmp_path, monkeypatch, capsys):
    """main() drives the full pipeline through the real call_claude wrapper.

    We only replace the Anthropic client (the thing ``call_claude`` calls
    ``messages.create`` on).  Every other code path -- call_claude itself,
    event writing, totals accumulation, wiki writing -- runs unmodified.
    """

    workspace = _build_workspace(tmp_path / "ws", with_sources=2)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    client = _FakeClient(
        [
            (VALID_RESPONSE_JSON, 1234, 500),
            (VALID_RESPONSE_JSON, 999, 111),
        ]
    )
    monkeypatch.setattr(ingest, "init_client", lambda: (client, "claude-haiku-4-5"))

    rc = ingest.main([], workspace)
    assert rc == 0
    out = capsys.readouterr().out

    # Two summary pages written, one per source.
    summaries = sorted(p.name for p in workspace.summaries_dir.glob("*.md"))
    assert summaries == ["src0.md", "src1.md"]

    # Every ingest turn produced a claude_api_call event (six total fields).
    events = _read_events(workspace)
    api_calls = [e for e in events if e["event"] == "claude_api_call"]
    assert len(api_calls) == 2
    for call_event in api_calls:
        assert set(call_event.keys()) == {
            "event",
            "ts",
            "model",
            "input_tokens",
            "output_tokens",
            "context",
        }
        assert call_event["model"] == "claude-haiku-4-5"

    # And the run completed with a run_summary whose totals are the sum of the
    # two api_call events.
    run_summary = [e for e in events if e["event"] == "run_summary"][0]
    assert run_summary["total_input_tokens"] == sum(e["input_tokens"] for e in api_calls)
    assert run_summary["total_output_tokens"] == sum(e["output_tokens"] for e in api_calls)
    assert run_summary["api_call_count"] == len(api_calls)

    assert out.strip().splitlines()[-1] == "Used ~2.2K input / 611 output tokens this run."


# ---------------------------------------------------------------------------
# build_client / init_client routing
# ---------------------------------------------------------------------------


def test_init_client_uses_claude_api_build_client(monkeypatch):
    """``ingest.init_client`` builds the Anthropic client via scripts.claude_api."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")
    monkeypatch.setenv("ANTHROPIC_INGEST_MODEL", "claude-haiku-4-5")

    received: dict = {}

    def fake_build_client(api_key: str):
        received["api_key"] = api_key
        return SimpleNamespace(tag="fake-client")

    monkeypatch.setattr(ingest, "build_client", fake_build_client)

    client, model = ingest.init_client()
    assert client.tag == "fake-client"
    assert model == "claude-haiku-4-5"
    assert received["api_key"] == "sk-ant-real-key"
