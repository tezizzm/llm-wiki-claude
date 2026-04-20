"""Unit tests for scripts.cli argv preprocessing, banner, and dispatch."""

import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLI_PATH = ROOT / "scripts" / "cli.py"
VERSION_PATH = ROOT / "scripts" / "version.py"

cli_spec = importlib.util.spec_from_file_location("cli_module", CLI_PATH)
cli = importlib.util.module_from_spec(cli_spec)
assert cli_spec and cli_spec.loader
cli_spec.loader.exec_module(cli)

version_spec = importlib.util.spec_from_file_location("version_module", VERSION_PATH)
version = importlib.util.module_from_spec(version_spec)
assert version_spec and version_spec.loader
version_spec.loader.exec_module(version)


# ---------------------------------------------------------------------------
# Preserved smoke tests
# ---------------------------------------------------------------------------


def test_read_version_matches_version_file():
    assert version.read_version() == Path("VERSION").read_text(encoding="utf-8").strip()


def test_cli_version_flag_prints_version():
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("Expected SystemExit from argparse version flag")

    assert version.read_version() in stdout.getvalue()


# ---------------------------------------------------------------------------
# _extract_global_workspace
# ---------------------------------------------------------------------------


def test_extract_workspace_flag_before_subcommand():
    value, rest = cli._extract_global_workspace(["--workspace", "/tmp/x", "doctor"])
    assert value == "/tmp/x"
    assert rest == ["doctor"]


def test_extract_workspace_flag_after_subcommand():
    value, rest = cli._extract_global_workspace(["doctor", "--workspace", "/tmp/x"])
    assert value == "/tmp/x"
    assert rest == ["doctor"]


def test_extract_workspace_equals_form():
    value, rest = cli._extract_global_workspace(["--workspace=/tmp/x", "doctor"])
    assert value == "/tmp/x"
    assert rest == ["doctor"]


def test_extract_workspace_absent():
    value, rest = cli._extract_global_workspace(["doctor"])
    assert value is None
    assert rest == ["doctor"]


def test_extract_workspace_after_subcommand_with_further_args():
    value, rest = cli._extract_global_workspace(
        ["ingest", "--workspace", "/tmp/x", "--dry-run"]
    )
    assert value == "/tmp/x"
    assert rest == ["ingest", "--dry-run"]


# ---------------------------------------------------------------------------
# _extract_global_verbose
# ---------------------------------------------------------------------------


def test_extract_verbose_flag():
    verbose, rest = cli._extract_global_verbose(["--verbose", "doctor"])
    assert verbose is True
    assert rest == ["doctor"]


def test_extract_verbose_short_flag():
    verbose, rest = cli._extract_global_verbose(["doctor", "-v"])
    assert verbose is True
    assert rest == ["doctor"]


def test_extract_verbose_absent():
    verbose, rest = cli._extract_global_verbose(["doctor"])
    assert verbose is False
    assert rest == ["doctor"]


# ---------------------------------------------------------------------------
# _print_banner_if_needed
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path, source: str):
    from scripts.workspace import resolve_workspace

    if source == "flag":
        return resolve_workspace(str(tmp_path), None)
    if source == "env":
        return resolve_workspace(None, str(tmp_path))
    return resolve_workspace(None, None)


def test_banner_prints_for_flag(tmp_path, capsys):
    workspace = _make_workspace(tmp_path, "flag")
    cli._print_banner_if_needed(workspace)
    captured = capsys.readouterr()
    expected = f"Workspace: {tmp_path} (from --workspace)\n\n"
    assert captured.out == expected
    assert captured.err == ""


def test_banner_prints_for_env(tmp_path, capsys):
    workspace = _make_workspace(tmp_path, "env")
    cli._print_banner_if_needed(workspace)
    captured = capsys.readouterr()
    expected = f"Workspace: {tmp_path} (from LLM_WIKI_WORKSPACE)\n\n"
    assert captured.out == expected


def test_banner_silent_for_default(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path, "default")
    cli._print_banner_if_needed(workspace)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Workspace-not-found error path
# ---------------------------------------------------------------------------


def test_workspace_not_found_error_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    missing = tmp_path / "definitely-missing"
    assert not missing.exists()

    code = cli.main(["--workspace", str(missing), "doctor"])
    assert code == 2

    captured = capsys.readouterr()
    expected = (
        f"Workspace error: {missing} does not exist. "
        f"Run `llm-wiki init {missing}` first.\n"
    )
    assert captured.err == expected
    # Banner is suppressed on the error path
    assert "Workspace:" not in captured.out


# ---------------------------------------------------------------------------
# Precedence: flag > env > default
# ---------------------------------------------------------------------------


def test_workspace_precedence_flag_beats_env(tmp_path, monkeypatch):
    flag_ws = tmp_path / "flag-ws"
    env_ws = tmp_path / "env-ws"
    flag_ws.mkdir()
    env_ws.mkdir()

    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(env_ws))

    from scripts.workspace import resolve_workspace

    import os

    resolved = resolve_workspace(
        str(flag_ws), os.environ.get("LLM_WIKI_WORKSPACE")
    )
    assert resolved.source == "flag"
    assert resolved.root == flag_ws.resolve()


def test_workspace_precedence_env_beats_default(tmp_path, monkeypatch):
    env_ws = tmp_path / "env-ws"
    env_ws.mkdir()

    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(env_ws))

    from scripts.workspace import resolve_workspace

    import os

    resolved = resolve_workspace(None, os.environ.get("LLM_WIKI_WORKSPACE"))
    assert resolved.source == "env"
    assert resolved.root == env_ws.resolve()


# ---------------------------------------------------------------------------
# Verbose resolution block
# ---------------------------------------------------------------------------


def test_verbose_prints_resolution_block(tmp_path, monkeypatch, capsys):
    """--verbose prints resolution block after banner, before subcommand."""
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "schemas").mkdir()

    # Populate only SOME workspace-local files so we exercise both primary and
    # fallback rendering.
    (workspace_root / "sync-sources.local.json").write_text("{}")
    (workspace_root / "schemas" / "AGENTS.md").write_text("# agents")
    # ingest-settings.local.json missing -> falls back to repo-root.
    # .wikiignore missing -> may fall back to repo-root.
    # .env: create so we see it resolve to the workspace copy.
    (workspace_root / ".env").write_text("FOO=bar\n")

    workspace = _make_workspace(workspace_root, "flag")

    cli._print_verbose_resolution_block(workspace)
    out = capsys.readouterr().out

    assert "Resolved config:" in out
    assert (
        f"  sync-sources.local.json: {workspace_root / 'sync-sources.local.json'}"
        in out
    )
    assert f"  schemas/AGENTS.md: {workspace_root / 'schemas' / 'AGENTS.md'}" in out
    assert f"  .env: {workspace_root / '.env'}" in out
    # Trailing blank line is part of the contract
    assert out.endswith("\n\n")


def test_verbose_resolution_block_shows_fallback(tmp_path, capsys):
    """When workspace-local copy is missing, render 'fallback -> <path>'."""
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    # Leave every workspace-local file absent so the repo-root fallbacks apply.

    workspace = _make_workspace(workspace_root, "flag")
    cli._print_verbose_resolution_block(workspace)
    out = capsys.readouterr().out

    # The repo-root config files exist in this repo, so each line should
    # include the 'fallback -> ' marker.
    assert "fallback -> " in out


# ---------------------------------------------------------------------------
# Full main() dispatch behaviour
# ---------------------------------------------------------------------------


def test_main_init_not_implemented(monkeypatch, capsys):
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    code = cli.main(["init", "/tmp/whatever"])
    assert code == 2
    captured = capsys.readouterr()
    assert "not yet implemented" in captured.err


def test_main_banner_plus_doctor_uses_flag_workspace(tmp_path, monkeypatch, capsys):
    """Passing --workspace prints the banner and routes to doctor."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    # doctor.main() raises SystemExit with an int code; catch and ignore the
    # code so we can inspect stdout.
    with pytest.raises(SystemExit):
        cli.main(["--workspace", str(workspace_root), "doctor"])

    captured = capsys.readouterr()
    first_line = captured.out.split("\n", 1)[0]
    assert first_line == f"Workspace: {workspace_root} (from --workspace)"
    # Blank line follows
    assert captured.out.split("\n", 2)[1] == ""
