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


def test_cli_dispatches_init(tmp_path, monkeypatch, capsys):
    """`llm-wiki init PATH` dispatches to scripts.init.main and scaffolds."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    target = tmp_path / "ws-x"
    assert not target.exists()

    code = cli.main(["init", str(target)])
    assert code == 0

    # init scaffolds the workspace directory structure.
    assert target.is_dir()
    assert (target / "raw" / "inbox").is_dir()
    assert (target / "wiki" / "summaries").is_dir()
    assert (target / "wiki" / "topics").is_dir()
    assert (target / "wiki" / "entities").is_dir()
    assert (target / "state").is_dir()
    assert (target / "schemas").is_dir()

    # No global --workspace was provided, so no "ignored" note should appear.
    captured = capsys.readouterr()
    assert "ignored for init" not in captured.err


def test_cli_init_ignores_workspace_flag(tmp_path, monkeypatch, capsys):
    """--workspace alongside init prints a note and still runs init on PATH."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    ignored_ws = tmp_path / "foo"
    target = tmp_path / "ws"
    assert not target.exists()

    code = cli.main(["--workspace", str(ignored_ws), "init", str(target)])
    assert code == 0

    captured = capsys.readouterr()
    assert (
        "Note: --workspace is ignored for init; init uses the "
        "positional PATH argument."
    ) in captured.err

    # init ran against the positional PATH (not the --workspace value).
    assert target.is_dir()
    assert (target / "raw" / "inbox").is_dir()
    # The --workspace target was NOT scaffolded.
    assert not ignored_ws.exists()


def test_cli_init_no_banner(tmp_path, monkeypatch, capsys):
    """init must never trigger the workspace-aware banner (DESIGN §4.1)."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    target = tmp_path / "ws"

    # Without --workspace
    code = cli.main(["init", str(target)])
    assert code == 0
    out1 = capsys.readouterr().out
    assert "Workspace:" not in out1

    # With --workspace (which is ignored for init)
    target2 = tmp_path / "ws2"
    ignored = tmp_path / "ignored"
    code = cli.main(["--workspace", str(ignored), "init", str(target2)])
    assert code == 0
    out2 = capsys.readouterr().out
    assert "Workspace:" not in out2


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


# ---------------------------------------------------------------------------
# refresh / refresh-fast DISPATCH wiring (LWC-szm3)
#
# These tests exercise the canonical ``scripts.cli`` module (not the
# importlib-loaded ``cli_module`` fixture above) because they need to
# monkeypatch the ``DISPATCH`` entries that ``_handle_refresh`` and
# ``_handle_refresh_fast`` call into. ``scripts.cli.DISPATCH['sync']`` and
# ``DISPATCH['ingest']`` hold module-level function references; patching the
# underlying ``scripts.sync.main`` / ``scripts.ingest.main`` attributes would
# not rewire the dispatch table. Patching the table entries directly keeps
# the test hermetic.
# ---------------------------------------------------------------------------


from scripts import cli as _scripts_cli  # noqa: E402  (late import by design)


def _make_refresh_stubs(sync_rc: int = 0, ingest_rc: int = 0):
    """Build (sync_stub, ingest_stub, calls) recording every dispatch call.

    ``calls`` is a list of ``(name, argv, workspace)`` tuples preserving order
    so tests can assert both the argv each stage received AND that the same
    ``WorkspacePaths`` instance was passed to both.
    """

    calls: list[tuple[str, list[str], object]] = []

    def sync_stub(argv, workspace):
        calls.append(("sync", list(argv), workspace))
        return sync_rc

    def ingest_stub(argv, workspace):
        calls.append(("ingest", list(argv), workspace))
        # Mirror the real ingest token-summary line so refresh/refresh-fast
        # tests can assert it shows up as the last line of stdout.
        print("Used 1 input / 1 output tokens this run.")
        return ingest_rc

    return sync_stub, ingest_stub, calls


def _patch_dispatch(monkeypatch, sync_stub, ingest_stub):
    """Patch DISPATCH entries in a scope-safe way."""
    monkeypatch.setitem(_scripts_cli.DISPATCH, "sync", sync_stub)
    monkeypatch.setitem(_scripts_cli.DISPATCH, "ingest", ingest_stub)


def test_cli_refresh_banner_once(tmp_path, monkeypatch, capsys):
    """``--workspace ... refresh`` prints the banner exactly once."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, _ = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh"])
    assert rc == 0

    out = capsys.readouterr().out
    banner_line = f"Workspace: {workspace_root} (from --workspace)"
    assert out.count(banner_line) == 1
    # Banner is the very first line of output
    assert out.splitlines()[0] == banner_line


def test_cli_refresh_fast_banner_once(tmp_path, monkeypatch, capsys):
    """``--workspace ... refresh-fast`` prints the banner exactly once."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, _ = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh-fast"])
    assert rc == 0

    out = capsys.readouterr().out
    banner_line = f"Workspace: {workspace_root} (from --workspace)"
    assert out.count(banner_line) == 1
    assert out.splitlines()[0] == banner_line


def test_cli_refresh_passes_same_workspace(tmp_path, monkeypatch):
    """Both sync and ingest must receive the identical WorkspacePaths object.

    The dispatcher resolves the workspace exactly once; the same instance is
    threaded through both stages so no stage re-derives paths from ``__file__``
    or an env var.
    """
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh"])
    assert rc == 0

    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]
    _, sync_argv, sync_ws = calls[0]
    _, _ingest_argv, ingest_ws = calls[1]
    # Same object identity -- not just equal paths.
    assert sync_ws is ingest_ws
    # Sync received --prune (refresh = prune + ingest).
    assert sync_argv == ["--prune"]


def test_cli_refresh_fast_passes_same_workspace(tmp_path, monkeypatch):
    """Same invariant as above, but for ``refresh-fast`` (no --prune)."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh-fast"])
    assert rc == 0

    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]
    _, sync_argv, sync_ws = calls[0]
    _, _ingest_argv, ingest_ws = calls[1]
    assert sync_ws is ingest_ws
    # refresh-fast = sync (no prune) + ingest.
    assert sync_argv == []


def test_cli_refresh_sync_failure_skips_ingest(tmp_path, monkeypatch):
    """If sync returns non-zero, ingest is NOT called and the rc propagates."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs(sync_rc=7)
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh"])
    assert rc == 7
    # Only sync should have been invoked.
    assert [name for name, _argv, _ws in calls] == ["sync"]


def test_cli_refresh_fast_sync_failure_skips_ingest(tmp_path, monkeypatch):
    """refresh-fast: sync failure also skips ingest and propagates rc."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs(sync_rc=3)
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh-fast"])
    assert rc == 3
    assert [name for name, _argv, _ws in calls] == ["sync"]


def test_cli_refresh_success_returns_0(tmp_path, monkeypatch):
    """Both stages succeed -> cli.main returns 0."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh"])
    assert rc == 0
    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]


def test_cli_refresh_fast_success_returns_0(tmp_path, monkeypatch):
    """refresh-fast: both stages succeed -> cli.main returns 0."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh-fast"])
    assert rc == 0
    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]


def test_cli_refresh_token_summary_at_end(tmp_path, monkeypatch, capsys):
    """The token-summary line is the LAST line of refresh-fast stdout.

    DESIGN §8.2: there is no refresh-level aggregation; the line comes from
    ingest.main's _emit_run_summary and therefore must be the final stdout
    line when refresh-fast completes successfully.
    """
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, _ = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["--workspace", str(workspace_root), "refresh-fast"])
    assert rc == 0

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert lines, "expected some output"
    last = lines[-1]
    assert last.startswith("Used "), last
    assert last.endswith(" tokens this run."), last


def test_cli_refresh_repo_root_default_no_banner(tmp_path, monkeypatch, capsys):
    """Repo-root default (no --workspace, no env) prints no banner.

    AC 5: ``make refresh`` / ``make refresh-fast`` from the repo root must
    behave 0.2.0-identical (no banner). The token-summary line still prints
    because ingest always emits it, but the banner remains suppressed since
    the workspace source is 'default'.
    """
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["refresh"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Workspace:" not in out
    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]


def test_cli_refresh_fast_repo_root_default_no_banner(tmp_path, monkeypatch, capsys):
    """Repo-root default for refresh-fast also suppresses the banner."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(["refresh-fast"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Workspace:" not in out
    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]


def test_cli_refresh_dry_run_skips_ingest(tmp_path, monkeypatch):
    """``refresh --dry-run`` runs sync in dry-run mode and skips ingest."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(
        ["--workspace", str(workspace_root), "refresh", "--dry-run"]
    )
    assert rc == 0
    assert [name for name, _argv, _ws in calls] == ["sync"]
    _, sync_argv, _ws = calls[0]
    assert "--dry-run" in sync_argv
    assert "--prune" in sync_argv


def test_cli_refresh_reconcile_passes_flag_to_ingest(tmp_path, monkeypatch):
    """``refresh --reconcile`` threads --reconcile through to ingest only."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    sync_stub, ingest_stub, calls = _make_refresh_stubs()
    _patch_dispatch(monkeypatch, sync_stub, ingest_stub)

    rc = _scripts_cli.main(
        ["--workspace", str(workspace_root), "refresh", "--reconcile"]
    )
    assert rc == 0
    assert [name for name, _argv, _ws in calls] == ["sync", "ingest"]
    _, sync_argv, _ = calls[0]
    _, ingest_argv, _ = calls[1]
    assert "--reconcile" not in sync_argv
    assert ingest_argv == ["--reconcile"]
