"""Unit tests for scripts.show_config (LWC-nk0x).

All tests use the ``tmp_workspace`` fixture from tests/conftest.py so that
assertions about resolution lines see the exact paths the workspace owns.
The ``replace_fallbacks`` helper from test_doctor.py is duplicated locally
to keep this module self-contained (show_config has its own fallback
rendering to test).
"""

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli, show_config
from scripts.workspace import WorkspacePaths, resolve_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_show_config(workspace: WorkspacePaths, capsys) -> tuple[int, str]:
    code = show_config.main([], workspace)
    out = capsys.readouterr().out
    return code, out


def _replace_fallbacks(
    ws: WorkspacePaths,
    *,
    sync_fallback: Path | None = None,
    ingest_fallback: Path | None = None,
    schema_fallback: Path | None = None,
    wikiignore_fallback: Path | None = None,
    env_fallback: Path | None = None,
) -> WorkspacePaths:
    """Return a copy of ``ws`` with select fallback paths redirected.

    WorkspacePaths is frozen; we rebuild with dataclasses.replace for tests
    that need to steer the resolver away from the real repo root.
    """

    kwargs: dict[str, Path] = {}
    if sync_fallback is not None:
        kwargs["sync_fallback_config_path"] = sync_fallback
    if ingest_fallback is not None:
        kwargs["ingest_fallback_settings_path"] = ingest_fallback
    if schema_fallback is not None:
        kwargs["schema_fallback_path"] = schema_fallback
    if wikiignore_fallback is not None:
        kwargs["wikiignore_fallback_path"] = wikiignore_fallback
    if env_fallback is not None:
        kwargs["env_fallback_path"] = env_fallback
    return replace(ws, **kwargs)


# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-3: signature + primary paths render with workspace paths
# ---------------------------------------------------------------------------


def test_show_config_workspace_paths(tmp_workspace, capsys):
    """Workspace-local copies present -> each line reports the workspace path."""
    code, out = _run_show_config(tmp_workspace, capsys)

    assert code == 0
    assert f"Workspace root: {tmp_workspace.root}" in out
    assert (
        f"sync-sources.local.json: {tmp_workspace.sync_config_path}" in out
    )
    assert (
        f"ingest-settings.local.json: {tmp_workspace.ingest_settings_path}" in out
    )
    # schemas/AGENTS.md lives in the repo-root fallback for tmp_workspace
    # (conftest does not seed it), so the line must carry the fallback prefix.
    assert "schemas/AGENTS.md: " in out
    # .env is seeded in the workspace by conftest, so primary path shows.
    assert f".env: {tmp_workspace.env_path}" in out
    # No 'fallback -> ' on the workspace-local lines
    assert (
        f"sync-sources.local.json: fallback -> " not in out
    ), "workspace-local sync config should not render with fallback prefix"


# ---------------------------------------------------------------------------
# AC-3: 'fallback -> ' appears when workspace-local copy is missing
# ---------------------------------------------------------------------------


def test_show_config_fallback_paths(tmp_workspace, capsys):
    """No workspace-local ingest-settings.local.json -> fallback prefix renders."""
    # Remove the workspace copy so the repo-root default is selected.
    tmp_workspace.ingest_settings_path.unlink()

    code, out = _run_show_config(tmp_workspace, capsys)

    assert code == 0
    # The repo ships ingest-settings.json at the repo root, so the resolver
    # will find the fallback and render the prefix.
    assert "ingest-settings.local.json: fallback -> " in out
    assert str(tmp_workspace.ingest_fallback_settings_path) in out


# ---------------------------------------------------------------------------
# AC-3: '<none found>' renders when neither primary nor fallback exists
# ---------------------------------------------------------------------------


def test_show_config_env_missing(tmp_workspace, capsys):
    """No .env anywhere -> '.env: <none found>' line is present."""
    tmp_workspace.env_path.unlink()
    redirected = _replace_fallbacks(
        tmp_workspace, env_fallback=tmp_workspace.root / "absent.env"
    )

    code, out = _run_show_config(redirected, capsys)

    assert code == 0
    assert ".env: <none found>" in out
    # Sanity: the line must NOT also carry the fallback prefix.
    assert ".env: fallback -> " not in out


# ---------------------------------------------------------------------------
# AC-4: banner is NOT emitted by show_config itself
# ---------------------------------------------------------------------------


def test_show_config_repo_root_no_banner(tmp_path, monkeypatch, capsys):
    """Invoking via cli.main(['show-config']) from repo-root default prints no banner.

    When workspace.source == 'default', cli._print_banner_if_needed is silent.
    show_config itself never emits a 'Workspace:' banner line, so the output
    for the repo-root default must not contain one.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)

    rc = cli.main(["show-config"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Workspace:" not in out
    # show_config still prints its own "Workspace root:" line (a different
    # label from the banner), so make sure that IS present.
    assert "Workspace root: " in out


def test_show_config_with_workspace_flag_banner(tmp_path, monkeypatch, capsys):
    """Invoking via cli.main(['--workspace', ws, 'show-config']) prints banner.

    Banner comes from cli._print_banner_if_needed, NOT from show_config. The
    test asserts the banner is the first line and show_config's own output
    (including the 'Workspace root:' line) follows.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    rc = cli.main(["--workspace", str(workspace_root), "show-config"])
    assert rc == 0

    out = capsys.readouterr().out
    lines = out.splitlines()
    # First line is the banner, emitted once by cli.main.
    assert lines[0] == f"Workspace: {workspace_root} (from --workspace)"
    # And show_config's own "Workspace root:" line appears too.
    assert any(
        line == f"Workspace root: {workspace_root}" for line in lines
    ), out
    # Banner appears exactly once (show_config never re-emits it).
    assert out.count("Workspace: ") == 1
