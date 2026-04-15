"""E2e tests for three-mode query UX via the CLI entrypoint.

These tests exercise the full path: cli.main() -> query.main() -> ask().
Only the Anthropic API client is mocked.
"""

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli, query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_WIKI_TEXT = "FILE: index.md\nSome wiki content about testing.\n"


def _make_mock_client(answer_text: str = "Mock answer.") -> MagicMock:
    """Return a mock Anthropic client whose messages.create returns answer_text."""
    client = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = answer_text
    response = MagicMock()
    response.content = [block]
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# AC1: Inline arg query with populated wiki
# ---------------------------------------------------------------------------

def test_e2e_inline_query_prints_model_and_answer(capsys):
    """llm-wiki query "test question" with populated wiki -> output contains [Model: and answer."""
    client = _make_mock_client("The inline answer about testing.")
    with patch.object(query, "init_client", return_value=(client, "claude-test-model")), \
         patch.object(query, "read_all_wiki_text", return_value=FAKE_WIKI_TEXT):
        cli.main(["query", "test question"])

    out = capsys.readouterr().out
    assert "[Model: " in out
    assert "The inline answer about testing." in out
    client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# AC2: Piped stdin query
# ---------------------------------------------------------------------------

def test_e2e_stdin_pipe_prints_answer(capsys):
    """echo "test" | llm-wiki query -> answer printed."""
    client = _make_mock_client("The piped answer.")
    fake_stdin = io.StringIO("test\n")
    fake_stdin.isatty = lambda: False

    with patch.object(query, "init_client", return_value=(client, "claude-test-model")), \
         patch.object(query, "read_all_wiki_text", return_value=FAKE_WIKI_TEXT), \
         patch("sys.stdin", fake_stdin):
        cli.main(["query"])

    out = capsys.readouterr().out
    assert "The piped answer." in out
    client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# AC3: No wiki pages -> error message and hint
# ---------------------------------------------------------------------------

def test_e2e_no_wiki_pages_error_and_hint(capsys):
    """query with no wiki pages -> exact error message and hint."""
    client = _make_mock_client("Should not appear.")
    with patch.object(query, "init_client", return_value=(client, "claude-test-model")), \
         patch.object(query, "read_all_wiki_text", return_value=""):
        cli.main(["query", "anything"])

    out = capsys.readouterr().out
    assert "Error: No wiki pages found. Nothing to query." in out
    assert "Hint:" in out
    assert client.messages.create.call_count == 0


# ---------------------------------------------------------------------------
# AC4: Interactive mode banner
# ---------------------------------------------------------------------------

def test_e2e_interactive_mode_prints_banner(capsys):
    """Interactive mode prints banner: llm-wiki query (interactive) -- type "exit" or Ctrl-D to quit."""
    client = _make_mock_client("Interactive answer.")
    fake_inputs = iter(["test question", "exit"])

    with patch.object(query, "init_client", return_value=(client, "claude-test-model")), \
         patch.object(query, "read_all_wiki_text", return_value=FAKE_WIKI_TEXT), \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        cli.main(["query"])

    out = capsys.readouterr().out
    assert 'llm-wiki query (interactive) -- type "exit" or Ctrl-D to quit' in out
