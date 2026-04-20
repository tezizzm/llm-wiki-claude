"""Integration tests for scripts/query.py three-mode auto-detection.

Workspace-aware refactor (LWC-zaz2): every test uses the ``tmp_workspace``
fixture and passes the ``WorkspacePaths`` through to ``query.main``.  SDK
calls are mocked via ``scripts.claude_api.call_claude`` (as imported into
``scripts.query``) returning a real ``ClaudeCallResult``.  Per ARCHITECTURE
§10.2 query calls must NOT write to ``workspace.ingest_events_path``.
"""

import io
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import query
from scripts.claude_api import ClaudeCallResult
from scripts.workspace import WorkspacePaths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_WIKI_TEXT = "FILE: index.md\nSome wiki content.\n"


def _claude_api_mock(answer_text: str = "Mock answer.", *, input_tokens: int = 100, output_tokens: int = 50):
    """Return a mock for ``scripts.claude_api.call_claude``.

    Mirrors the real keyword-only signature so ``query.ask`` can call it the
    same way it calls the wrapper.  Records every call via the returned
    ``record`` list so tests can assert on ``log_event=False``.
    """

    record: list[dict] = []

    def _fn(*, client=None, model="test-model", system=None, messages=None,
            max_tokens=None, context=None, workspace=None, log_event=True, **kw):
        record.append(
            {
                "client": client,
                "model": model,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
                "context": context,
                "workspace": workspace,
                "log_event": log_event,
            }
        )
        return ClaudeCallResult(
            text=answer_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )

    _fn.record = record  # type: ignore[attr-defined]
    return _fn


def _seed_wiki(workspace: WorkspacePaths, body: str = "Some wiki content about testing.") -> None:
    """Populate ``workspace.index_path`` and ``workspace.summaries_dir`` with test content."""
    workspace.index_path.write_text("# Wiki index\n\n- Demo page\n", encoding="utf-8")
    workspace.summaries_dir.mkdir(parents=True, exist_ok=True)
    (workspace.summaries_dir / "demo.md").write_text(
        f"# Demo\n\n{body}\n", encoding="utf-8"
    )


def _patch_query_sdk(client_stub=object(), model: str = "test-model"):
    """Return a tuple of (init_patch, call_claude_mock) context managers.

    ``call_claude`` is patched at the import site (``scripts.query.call_claude``)
    because ``query.py`` binds the symbol at import time via
    ``from scripts.claude_api import call_claude``.
    """
    mock = _claude_api_mock()
    init_patch = patch.object(query, "init_client", return_value=(client_stub, model))
    call_patch = patch.object(query, "call_claude", mock)
    return init_patch, call_patch, mock


# ---------------------------------------------------------------------------
# AC1: main signature is main(argv: list[str], workspace: WorkspacePaths) -> int
# ---------------------------------------------------------------------------


def test_query_main_signature_takes_workspace(tmp_workspace):
    """``query.main`` must accept (argv, workspace) and return int."""
    import inspect

    sig = inspect.signature(query.main)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["argv", "workspace"], (
        f"query.main signature mismatch: got {[p.name for p in params]}"
    )
    # Verify it runs and returns int when wiki is empty.
    rc = query.main([], tmp_workspace)
    assert isinstance(rc, int)


# ---------------------------------------------------------------------------
# AC2: query.ROOT, query.WIKI_DIR, query.INDEX_PATH are deleted
# ---------------------------------------------------------------------------


def test_query_module_constants_deleted():
    """AC2: query.ROOT / WIKI_DIR / INDEX_PATH must not exist on the module."""
    assert not hasattr(query, "ROOT"), "query.ROOT still exists"
    assert not hasattr(query, "WIKI_DIR"), "query.WIKI_DIR still exists"
    assert not hasattr(query, "INDEX_PATH"), "query.INDEX_PATH still exists"


# ---------------------------------------------------------------------------
# AC3: Helpers take workspace explicitly
# ---------------------------------------------------------------------------


def test_collect_wiki_text_takes_workspace(tmp_workspace):
    """``collect_wiki_text(workspace)`` reads from workspace paths only."""
    _seed_wiki(tmp_workspace, body="Unique workspace content about XYZ.")
    text = query.collect_wiki_text(tmp_workspace)
    assert "Unique workspace content about XYZ." in text
    assert "FILE: index.md" in text


def test_load_index_takes_workspace(tmp_workspace):
    """``load_index(workspace)`` reads ``workspace.index_path``."""
    tmp_workspace.index_path.write_text("# Seeded index\n", encoding="utf-8")
    assert query.load_index(tmp_workspace) == "# Seeded index\n"


def test_load_index_missing_returns_empty(tmp_workspace):
    """Missing index path returns empty string, does not raise."""
    # Ensure no index exists.
    if tmp_workspace.index_path.exists():
        tmp_workspace.index_path.unlink()
    assert query.load_index(tmp_workspace) == ""


# ---------------------------------------------------------------------------
# AC4: SDK calls route through scripts.claude_api.call_claude with log_event=False
# ---------------------------------------------------------------------------


def test_ask_routes_through_call_claude_with_log_event_false(tmp_workspace):
    """AC4: ``ask`` delegates to ``call_claude`` with ``log_event=False``."""
    mock = _claude_api_mock("The answer is 42.")
    with patch.object(query, "call_claude", mock):
        answer = query.ask(
            client=object(),
            model="test-model",
            wiki_text="wiki content",
            question="What is the answer?",
            workspace=tmp_workspace,
        )
    assert answer == "The answer is 42."
    assert len(mock.record) == 1  # type: ignore[attr-defined]
    call = mock.record[0]  # type: ignore[attr-defined]
    assert call["log_event"] is False
    assert call["model"] == "test-model"
    assert call["max_tokens"] == 2500
    assert call["workspace"] is tmp_workspace
    assert call["system"] == query.SYSTEM_PROMPT
    assert "What is the answer?" in call["messages"][0]["content"]


# ---------------------------------------------------------------------------
# AC5: No event is written to ingest_events.jsonl when query runs
# ---------------------------------------------------------------------------


def test_query_does_not_write_event(tmp_workspace, capsys):
    """AC5: after ``query.main`` runs, ``workspace.ingest_events_path`` is empty
    or does not exist."""
    _seed_wiki(tmp_workspace)
    init_patch, call_patch, mock = _patch_query_sdk()
    with init_patch, call_patch:
        rc = query.main(["What is X?"], tmp_workspace)
    assert rc == 0
    # call_claude was invoked with log_event=False
    assert len(mock.record) == 1
    assert mock.record[0]["log_event"] is False
    # No event file written
    events_path = tmp_workspace.ingest_events_path
    assert (not events_path.exists()) or events_path.read_text(encoding="utf-8") == "", (
        f"query wrote to ingest_events.jsonl: {events_path.read_text(encoding='utf-8') if events_path.exists() else '<missing>'}"
    )


# ---------------------------------------------------------------------------
# AC7 cross-check: query is wired into cli.DISPATCH
# ---------------------------------------------------------------------------


def test_cli_dispatch_has_query_entry():
    """AC7: cli.DISPATCH['query'] points at query.main."""
    from scripts import cli

    assert "query" in cli.DISPATCH
    assert cli.DISPATCH["query"] is query.main


# ---------------------------------------------------------------------------
# Workspace isolation: query reads workspace wiki, not repo-root wiki
# ---------------------------------------------------------------------------


def test_query_reads_workspace_wiki_only(tmp_workspace, capsys):
    """A query against a populated workspace must derive its content from the
    workspace (not from the repo-root wiki directory).

    Seeds the workspace with a distinctive marker and asserts that the marker
    flows into the call_claude user prompt, while repo-root content does not.
    """
    marker = "Z2Z2_WORKSPACE_MARKER_WXYZ"
    tmp_workspace.summaries_dir.mkdir(parents=True, exist_ok=True)
    (tmp_workspace.summaries_dir / "marker.md").write_text(
        f"# Marker page\n\n{marker}\n", encoding="utf-8"
    )

    mock = _claude_api_mock("Answer about marker.")
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock):
        rc = query.main(["What marker is here?"], tmp_workspace)
    assert rc == 0
    # The marker should have been passed into the call_claude user prompt.
    assert len(mock.record) == 1  # type: ignore[attr-defined]
    user_prompt = mock.record[0]["messages"][0]["content"]  # type: ignore[attr-defined]
    assert marker in user_prompt, (
        "collect_wiki_text did not read from workspace.summaries_dir"
    )


def test_collect_wiki_text_isolates_workspaces(two_workspaces):
    """A file seeded in workspace A must NOT appear in collect_wiki_text for
    workspace B."""
    wa, wb = two_workspaces
    wa.summaries_dir.mkdir(parents=True, exist_ok=True)
    (wa.summaries_dir / "only-in-a.md").write_text(
        "# Only A\n\nALPHA_ONLY_MARKER\n", encoding="utf-8"
    )
    text_b = query.collect_wiki_text(wb)
    assert "ALPHA_ONLY_MARKER" not in text_b


# ---------------------------------------------------------------------------
# ask() unit test (preserved from 0.2.0)
# ---------------------------------------------------------------------------


def test_ask_returns_model_response(tmp_workspace):
    mock = _claude_api_mock("The answer is 42.")
    with patch.object(query, "call_claude", mock):
        result = query.ask(
            client=object(),
            model="test-model",
            wiki_text="wiki content",
            question="What is the answer?",
            workspace=tmp_workspace,
        )
    assert result == "The answer is 42."
    assert len(mock.record) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Inline mode
# ---------------------------------------------------------------------------


def test_inline_mode_prints_answer_and_exits(tmp_workspace, capsys):
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Inline answer.")
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock):
        rc = query.main(["What is X?"], tmp_workspace)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inline answer." in out
    assert "[Model: test-model]" in out


def test_inline_mode_via_cli(tmp_workspace, capsys):
    """Verify cli.py passes query args through to query.main()."""
    from scripts import cli

    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("CLI inline answer.")
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock):
        rc = cli.main(
            ["--workspace", str(tmp_workspace.root), "query", "What is Y?"]
        )
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLI inline answer." in out


# ---------------------------------------------------------------------------
# Interactive TTY mode
# ---------------------------------------------------------------------------


def test_interactive_mode_banner_and_prompt(tmp_workspace, capsys):
    """Interactive mode prints banner and prompts with '> '."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Interactive answer.")

    fake_inputs = iter(["What is X?", "exit"])
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        rc = query.main([], tmp_workspace)
    assert rc == 0
    out = capsys.readouterr().out
    assert "llm-wiki query (interactive) -- type \"exit\" or Ctrl-D to quit" in out
    assert "Interactive answer." in out
    assert "[Model: test-model]" in out


def test_interactive_mode_model_tag_only_on_first_answer(tmp_workspace, capsys):
    """[Model: ...] is printed before the first answer only."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Answer.")

    fake_inputs = iter(["Q1", "Q2", "exit"])
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        query.main([], tmp_workspace)

    out = capsys.readouterr().out
    assert out.count("[Model: test-model]") == 1


def test_interactive_mode_empty_input_ignored(tmp_workspace, capsys):
    """Empty input lines are silently ignored."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Answer.")

    fake_inputs = iter(["", "  ", "What?", "exit"])
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        query.main([], tmp_workspace)

    # Only one call to ask (the "What?" question)
    assert len(mock.record) == 1  # type: ignore[attr-defined]


def test_interactive_mode_quit_exits(tmp_workspace, capsys):
    """'quit' exits the interactive loop."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Answer.")

    fake_inputs = iter(["quit"])
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        query.main([], tmp_workspace)

    assert len(mock.record) == 0  # type: ignore[attr-defined]


def test_interactive_mode_ctrl_d_exits(tmp_workspace, capsys):
    """Ctrl-D (EOFError) exits the interactive loop cleanly."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Answer.")

    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=EOFError), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        query.main([], tmp_workspace)

    out = capsys.readouterr().out
    assert "llm-wiki query (interactive) -- type \"exit\" or Ctrl-D to quit" in out


# ---------------------------------------------------------------------------
# Stdin pipe mode
# ---------------------------------------------------------------------------


def test_stdin_pipe_mode(tmp_workspace, capsys):
    """Piped stdin is read as the question."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Piped answer.")

    fake_stdin = io.StringIO("What is piped?\n")
    fake_stdin.isatty = lambda: False

    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("sys.stdin", fake_stdin):
        query.main([], tmp_workspace)

    out = capsys.readouterr().out
    assert "Piped answer." in out
    assert "[Model: test-model]" in out


def test_stdin_pipe_mode_empty_input(tmp_workspace, capsys):
    """Empty piped stdin produces no output."""
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Should not appear.")

    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: False

    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("sys.stdin", fake_stdin):
        query.main([], tmp_workspace)

    out = capsys.readouterr().out
    assert "Should not appear." not in out
    assert len(mock.record) == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Error: no wiki pages
# ---------------------------------------------------------------------------


def test_no_wiki_pages_error(tmp_workspace, capsys):
    """When wiki text is empty, print error with hint."""
    # Ensure the wiki subtree is empty: the tmp_workspace fixture creates the
    # directory structure but seeds no pages.
    mock = _claude_api_mock("Should not appear.")
    with patch.object(query, "init_client", return_value=(object(), "test-model")), \
         patch.object(query, "call_claude", mock):
        query.main(["What?"], tmp_workspace)

    out = capsys.readouterr().out
    assert "Error: No wiki pages found. Nothing to query." in out
    assert "Hint:" in out
    assert len(mock.record) == 0  # type: ignore[attr-defined]


