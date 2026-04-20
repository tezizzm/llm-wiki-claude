"""E2e tests for three-mode query UX via the CLI entrypoint.

Workspace-aware refactor (LWC-zaz2): tests drive ``cli.main(['query', ...])``
against a workspace resolved by ``--workspace`` or the repo-root default,
rather than monkey-patching module-level path constants.  SDK calls are
mocked via ``scripts.claude_api.call_claude`` (as imported into
``scripts.query``).
"""

import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli, query
from scripts.claude_api import ClaudeCallResult
from scripts.workspace import WorkspacePaths


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claude_api_mock(answer_text: str = "Mock answer.", *, input_tokens: int = 100, output_tokens: int = 50):
    """Return a mock for ``scripts.claude_api.call_claude`` (query-side)."""

    record: list[dict] = []

    def _fn(*, client=None, model="claude-test-model", system=None, messages=None,
            max_tokens=None, context=None, workspace=None, log_event=True, **kw):
        record.append(
            {
                "client": client,
                "model": model,
                "messages": messages,
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


def _seed_wiki(workspace: WorkspacePaths) -> None:
    """Populate a minimal wiki under ``workspace`` so query has content."""
    workspace.index_path.write_text("# Index\n\n- Demo\n", encoding="utf-8")
    workspace.summaries_dir.mkdir(parents=True, exist_ok=True)
    (workspace.summaries_dir / "demo.md").write_text(
        "# Demo\n\nSome wiki content about testing.\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# AC1: Inline arg query with populated wiki
# ---------------------------------------------------------------------------


def test_e2e_inline_query_prints_model_and_answer(tmp_workspace, capsys, monkeypatch):
    """llm-wiki query "test question" with populated wiki -> output contains [Model: and answer."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("The inline answer about testing.")
    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock):
        cli.main(["--workspace", str(tmp_workspace.root), "query", "test question"])

    out = capsys.readouterr().out
    assert "[Model: " in out
    assert "The inline answer about testing." in out
    assert len(mock.record) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC2: Piped stdin query
# ---------------------------------------------------------------------------


def test_e2e_stdin_pipe_prints_answer(tmp_workspace, capsys, monkeypatch):
    """echo "test" | llm-wiki query -> answer printed."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("The piped answer.")
    fake_stdin = io.StringIO("test\n")
    fake_stdin.isatty = lambda: False

    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("sys.stdin", fake_stdin):
        cli.main(["--workspace", str(tmp_workspace.root), "query"])

    out = capsys.readouterr().out
    assert "The piped answer." in out
    assert len(mock.record) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC3: No wiki pages -> error message and hint
# ---------------------------------------------------------------------------


def test_e2e_no_wiki_pages_error_and_hint(tmp_workspace, capsys, monkeypatch):
    """query with no wiki pages -> exact error message and hint."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    # tmp_workspace's wiki subtree is empty; no seeding required.
    mock = _claude_api_mock("Should not appear.")
    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock):
        cli.main(["--workspace", str(tmp_workspace.root), "query", "anything"])

    out = capsys.readouterr().out
    assert "Error: No wiki pages found. Nothing to query." in out
    assert "Hint:" in out
    assert len(mock.record) == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AC4: Interactive mode banner
# ---------------------------------------------------------------------------


def test_e2e_interactive_mode_prints_banner(tmp_workspace, capsys, monkeypatch):
    """Interactive mode prints banner: llm-wiki query (interactive) -- type "exit" or Ctrl-D to quit."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Interactive answer.")
    fake_inputs = iter(["test question", "exit"])

    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        cli.main(["--workspace", str(tmp_workspace.root), "query"])

    out = capsys.readouterr().out
    assert 'llm-wiki query (interactive) -- type "exit" or Ctrl-D to quit' in out


# ---------------------------------------------------------------------------
# AC8 cross-check: banner lifecycle for query
# ---------------------------------------------------------------------------


def test_query_e2e_banner_on_workspace(tmp_workspace, capsys, monkeypatch):
    """``llm-wiki --workspace X query "q"`` prints the DESIGN §4.2 banner."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Banner answer.")
    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock):
        cli.main(["--workspace", str(tmp_workspace.root), "query", "What?"])

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"Workspace: {tmp_workspace.root} (from --workspace)"
    # Banner is emitted exactly once.
    assert out.count("Workspace: ") == 1


def test_query_e2e_repo_root_silent(tmp_path, capsys, monkeypatch):
    """``llm-wiki query`` from the repo-root default prints no banner.

    Uses the no-wiki-pages path so we don't need to seed the real repo-root
    wiki directory with fixtures.  The point of the test is the ABSENCE of
    the banner -- 0.2.0 behavior must be byte-identical on the default path.
    """
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    # Force collect_wiki_text to return empty so the command short-circuits on
    # 'No wiki pages found.' without touching the real repo-root wiki.
    mock = _claude_api_mock("Should not appear.")
    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock), \
         patch.object(query, "collect_wiki_text", return_value=""):
        cli.main(["query", "What?"])

    out = capsys.readouterr().out
    assert "from --workspace" not in out
    assert "from LLM_WIKI_WORKSPACE" not in out
    assert "Workspace: " not in out


# ---------------------------------------------------------------------------
# AC5 cross-check: query does not write to ingest_events.jsonl at the e2e level
# ---------------------------------------------------------------------------


def test_query_e2e_does_not_write_event(tmp_workspace, capsys, monkeypatch):
    """End-to-end: after ``cli.main(['query', ...])`` the ingest events file
    under the workspace is still absent / empty."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    _seed_wiki(tmp_workspace)
    mock = _claude_api_mock("Answer.")
    with patch.object(query, "init_client", return_value=(object(), "claude-test-model")), \
         patch.object(query, "call_claude", mock):
        cli.main(["--workspace", str(tmp_workspace.root), "query", "q"])

    events_path = tmp_workspace.ingest_events_path
    assert (not events_path.exists()) or events_path.read_text(encoding="utf-8") == "", (
        "query wrote to ingest_events.jsonl: "
        f"{events_path.read_text(encoding='utf-8') if events_path.exists() else '<missing>'}"
    )
    # And the mock was invoked with log_event=False
    assert len(mock.record) == 1  # type: ignore[attr-defined]
    assert mock.record[0]["log_event"] is False  # type: ignore[attr-defined]
