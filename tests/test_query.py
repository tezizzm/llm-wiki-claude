"""Integration tests for scripts/query.py three-mode auto-detection."""

import io
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _patch_init_and_wiki(client, model="test-model", wiki_text="FILE: index.md\nSome wiki content.\n"):
    """Return a combined patch context manager for init_client and read_all_wiki_text."""
    return (
        patch.object(query, "init_client", return_value=(client, model)),
        patch.object(query, "read_all_wiki_text", return_value=wiki_text),
    )


# ---------------------------------------------------------------------------
# ask() unit test
# ---------------------------------------------------------------------------

def test_ask_returns_model_response():
    client = _make_mock_client("The answer is 42.")
    result = query.ask(client, "test-model", "wiki content", "What is the answer?")
    assert result == "The answer is 42."
    client.messages.create.assert_called_once()
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    assert call_kwargs["max_tokens"] == 2500
    assert call_kwargs["temperature"] == 0.2
    assert "What is the answer?" in call_kwargs["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Inline mode
# ---------------------------------------------------------------------------

def test_inline_mode_prints_answer_and_exits(capsys):
    client = _make_mock_client("Inline answer.")
    patches = _patch_init_and_wiki(client)
    with patches[0], patches[1]:
        sys.argv = ["query.py", "What is X?"]
        query.main()
    out = capsys.readouterr().out
    assert "Inline answer." in out
    assert "[Model: test-model]" in out


def test_inline_mode_via_cli(capsys):
    """Verify cli.py passes query args through to query.main()."""
    from scripts import cli

    client = _make_mock_client("CLI inline answer.")
    patches = _patch_init_and_wiki(client)
    with patches[0], patches[1]:
        cli.main(["query", "What is Y?"])
    out = capsys.readouterr().out
    assert "CLI inline answer." in out


# ---------------------------------------------------------------------------
# Interactive TTY mode
# ---------------------------------------------------------------------------

def test_interactive_mode_banner_and_prompt(capsys):
    """Interactive mode prints banner and prompts with '> '."""
    client = _make_mock_client("Interactive answer.")
    patches = _patch_init_and_wiki(client)

    # Simulate: user types a question, then "exit"
    fake_inputs = iter(["What is X?", "exit"])
    with patches[0], patches[1], \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    assert "llm-wiki query (interactive mode)" in out
    assert "Interactive answer." in out
    assert "[Model: test-model]" in out


def test_interactive_mode_model_tag_only_on_first_answer(capsys):
    """[Model: ...] is printed before the first answer only."""
    client = _make_mock_client("Answer.")
    patches = _patch_init_and_wiki(client)

    fake_inputs = iter(["Q1", "Q2", "exit"])
    with patches[0], patches[1], \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    assert out.count("[Model: test-model]") == 1


def test_interactive_mode_empty_input_ignored(capsys):
    """Empty input lines are silently ignored."""
    client = _make_mock_client("Answer.")
    patches = _patch_init_and_wiki(client)

    fake_inputs = iter(["", "  ", "What?", "exit"])
    with patches[0], patches[1], \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    # Only one call to ask (the "What?" question)
    assert client.messages.create.call_count == 1


def test_interactive_mode_quit_exits(capsys):
    """'quit' exits the interactive loop."""
    client = _make_mock_client("Answer.")
    patches = _patch_init_and_wiki(client)

    fake_inputs = iter(["quit"])
    with patches[0], patches[1], \
         patch("builtins.input", side_effect=fake_inputs), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        sys.argv = ["query.py"]
        query.main()

    assert client.messages.create.call_count == 0


def test_interactive_mode_ctrl_d_exits(capsys):
    """Ctrl-D (EOFError) exits the interactive loop cleanly."""
    client = _make_mock_client("Answer.")
    patches = _patch_init_and_wiki(client)

    with patches[0], patches[1], \
         patch("builtins.input", side_effect=EOFError), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    assert "llm-wiki query (interactive mode)" in out


# ---------------------------------------------------------------------------
# Stdin pipe mode
# ---------------------------------------------------------------------------

def test_stdin_pipe_mode(capsys, monkeypatch):
    """Piped stdin is read as the question."""
    client = _make_mock_client("Piped answer.")
    patches = _patch_init_and_wiki(client)

    fake_stdin = io.StringIO("What is piped?\n")
    fake_stdin.isatty = lambda: False

    with patches[0], patches[1], \
         patch("sys.stdin", fake_stdin):
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    assert "Piped answer." in out
    assert "[Model: test-model]" in out


def test_stdin_pipe_mode_empty_input(capsys):
    """Empty piped stdin produces no output."""
    client = _make_mock_client("Should not appear.")
    patches = _patch_init_and_wiki(client)

    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: False

    with patches[0], patches[1], \
         patch("sys.stdin", fake_stdin):
        sys.argv = ["query.py"]
        query.main()

    out = capsys.readouterr().out
    assert "Should not appear." not in out
    assert client.messages.create.call_count == 0


# ---------------------------------------------------------------------------
# Error: no wiki pages
# ---------------------------------------------------------------------------

def test_no_wiki_pages_error(capsys):
    """When wiki text is empty, print error with hint."""
    client = _make_mock_client("Should not appear.")
    with patch.object(query, "init_client", return_value=(client, "test-model")), \
         patch.object(query, "read_all_wiki_text", return_value=""):
        sys.argv = ["query.py", "What?"]
        query.main()

    out = capsys.readouterr().out
    assert "Error: No wiki pages found. Nothing to query." in out
    assert "Hint:" in out
    assert client.messages.create.call_count == 0


def test_no_wiki_pages_whitespace_only(capsys):
    """Whitespace-only wiki text is treated as empty."""
    client = _make_mock_client("Should not appear.")
    with patch.object(query, "init_client", return_value=(client, "test-model")), \
         patch.object(query, "read_all_wiki_text", return_value="   \n  "):
        sys.argv = ["query.py", "What?"]
        query.main()

    out = capsys.readouterr().out
    assert "Error: No wiki pages found." in out
