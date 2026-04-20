"""End-to-end fallback resolution tests (LWC-b5ja).

Proves that when a workspace is missing one of the five fallback-eligible
files, the repo-root tracked default is used, doctor surfaces it in its
resolution block, and ``--verbose`` surfaces it on non-doctor commands.

The five fallback-eligible files (DESIGN §11, ARCHITECTURE §7):

- ``sync-sources.local.json``     -> repo-root ``sync-sources.json``
- ``ingest-settings.local.json``  -> repo-root ``ingest-settings.json``
- ``schemas/AGENTS.md``           -> repo-root ``schemas/AGENTS.md``
- ``.wikiignore``                 -> repo-root ``.wikiignore``
- ``.env``                        -> repo-root ``.env``

For each file, a test verifies:

1. The fallback resolver returns the repo-root path with ``is_fallback=True``
   BEFORE any CLI invocation (non-emptiness precondition per
   knowledge/patterns/non-emptiness-assertion-ordering-before-exit-code).
2. Running the corresponding command against a workspace missing the primary
   copy still succeeds.

Plus:

- ``doctor`` resolution block test verifies ``fallback -> <path>`` appears
  for missing primary and plain ``<path>`` for present primary.
- ``--verbose`` on a non-doctor command prints the same resolution block.
- Non-verbose runs do NOT print the resolution block on non-doctor commands.
- Edge case: ``.env`` missing in both locations -> doctor FAILs.

All tests call ``monkeypatch.delenv('LLM_WIKI_WORKSPACE', raising=False)`` to
keep developer-shell environments from leaking into the assertion surface.
"""

from __future__ import annotations

import os
from dataclasses import replace

from scripts import cli, doctor
from scripts.workspace import (
    WorkspacePaths,
    load_env,
    resolve_env,
    resolve_ingest_settings,
    resolve_schema,
    resolve_sync_config,
    resolve_wikiignore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> int:
    """Run ``cli.main(argv)`` handling the mixed return/SystemExit contract.

    ``cli.main`` returns an int for sync/ingest/query/show-config/refresh and
    raises ``SystemExit(rc)`` for lint/doctor.  Normalize to an int so every
    test can assert on the same shape.
    """

    try:
        rc = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
        return int(code)
    if rc is None:
        return 0
    return int(rc)


# ---------------------------------------------------------------------------
# AC-1 / AC-2: each fallback-eligible file produces a working command when the
# workspace copy is missing.
# ---------------------------------------------------------------------------


def test_sync_config_fallback_used_when_workspace_missing(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """Workspace missing sync-sources.local.json -> repo-root sync-sources.json used."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    tmp_workspace.sync_config_path.unlink()

    # Non-emptiness precondition: resolver must return the repo-root fallback.
    path, is_fallback = resolve_sync_config(tmp_workspace)
    assert is_fallback is True, f"expected fallback resolution, got {path!r}"
    assert path == tmp_workspace.sync_fallback_config_path, path

    # Note: the global CLI does not round-trip ``--dry-run`` through argparse
    # REMAINDER (documented pre-existing issue), so we invoke plain ``sync``.
    # The repo-root template carries a placeholder source root that does not
    # exist on disk; sync prints "Skipping missing source root" and returns
    # 0 -- the key assertion is that fallback resolution did not crash.
    rc = _run_cli(["--workspace", str(tmp_workspace.root), "sync"])
    assert rc == 0, capsys.readouterr().out


def test_ingest_settings_fallback_used_when_workspace_missing(
    tmp_workspace: WorkspacePaths, monkeypatch, mocked_call_claude, capsys
) -> None:
    """Workspace missing ingest-settings.local.json -> repo-root copy used."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    tmp_workspace.ingest_settings_path.unlink()

    path, is_fallback = resolve_ingest_settings(tmp_workspace)
    assert is_fallback is True, f"expected fallback resolution, got {path!r}"
    assert path == tmp_workspace.ingest_fallback_settings_path, path

    # ``--dry-run`` does not survive the CLI REMAINDER parser (documented
    # pre-existing issue); we run full ingest against the ``mocked_call_claude``
    # fixture, which patches the model call AND ``init_client`` so no network
    # traffic occurs.
    rc = _run_cli(["--workspace", str(tmp_workspace.root), "ingest"])
    assert rc == 0, capsys.readouterr().out


def test_schema_fallback(
    tmp_workspace: WorkspacePaths, monkeypatch, mocked_call_claude, capsys
) -> None:
    """Workspace missing schemas/AGENTS.md -> repo-root copy used.

    ``schemas/AGENTS.md`` is consulted by ingest when building prompts; a
    full ingest run (mocked at the Anthropic SDK boundary) must succeed
    when only the repo-root copy is available.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    if tmp_workspace.schema_path.exists():
        tmp_workspace.schema_path.unlink()

    path, is_fallback = resolve_schema(tmp_workspace)
    assert is_fallback is True, f"expected fallback resolution, got {path!r}"
    assert path == tmp_workspace.schema_fallback_path, path

    rc = _run_cli(["--workspace", str(tmp_workspace.root), "ingest"])
    assert rc == 0, capsys.readouterr().out


def test_wikiignore_fallback(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """Workspace missing .wikiignore -> repo-root .wikiignore used.

    Lint's exit code depends on whether the fixture wiki pages satisfy the
    four checks, so we accept 0 or 1 -- the key assertion is that fallback
    resolution did not raise and the command produced output.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    if tmp_workspace.wikiignore_path.exists():
        tmp_workspace.wikiignore_path.unlink()

    path, is_fallback = resolve_wikiignore(tmp_workspace)
    assert is_fallback is True, f"expected fallback resolution, got {path!r}"
    assert path == tmp_workspace.wikiignore_fallback_path, path

    rc = _run_cli(["--workspace", str(tmp_workspace.root), "lint"])
    assert rc in (0, 1), capsys.readouterr().out


def test_env_fallback_used_when_workspace_missing(
    tmp_workspace: WorkspacePaths, monkeypatch, tmp_path
) -> None:
    """Workspace missing .env -> ``env_fallback_path`` is used by ``load_env``.

    We synthesize an isolated fallback ``.env`` inside ``tmp_path`` (rather
    than depending on the real repo-root ``.env``, which may or may not be
    present on a dev machine or in CI) and redirect ``env_fallback_path``
    via ``dataclasses.replace``.  This guarantees the test never skips and
    its assertions are hermetic.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_WIKI_TEST_FALLBACK_MARKER", raising=False)
    tmp_workspace.env_path.unlink()

    fake_fallback = tmp_path / "fallback.env"
    fake_fallback.write_text(
        "ANTHROPIC_API_KEY=sk-ant-from-fallback\n"
        "LLM_WIKI_TEST_FALLBACK_MARKER=fallback-loaded\n",
        encoding="utf-8",
    )
    redirected = replace(tmp_workspace, env_fallback_path=fake_fallback)

    # Non-emptiness precondition: resolver reports the redirected fallback.
    env_path, is_fallback = resolve_env(redirected)
    assert is_fallback is True, f"expected fallback resolution, got {env_path!r}"
    assert env_path == fake_fallback, env_path

    # load_env must merge the fallback file into os.environ without raising.
    merged = load_env(redirected)
    assert merged.get("LLM_WIKI_TEST_FALLBACK_MARKER") == "fallback-loaded", merged
    assert os.environ.get("LLM_WIKI_TEST_FALLBACK_MARKER") == "fallback-loaded"
    # ANTHROPIC_API_KEY landed too (and since the test cleared it above, the
    # setdefault write actually took effect).
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-from-fallback"


# ---------------------------------------------------------------------------
# AC-3: doctor resolution block distinguishes primary vs fallback
# ---------------------------------------------------------------------------


def test_doctor_resolution_block_shows_fallback(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """Doctor prints ``ingest-settings.local.json: fallback -> <path>`` when missing."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    tmp_workspace.ingest_settings_path.unlink()

    # Precondition: resolver reports the fallback path.
    path, is_fallback = resolve_ingest_settings(tmp_workspace)
    assert is_fallback is True
    assert path == tmp_workspace.ingest_fallback_settings_path

    _run_cli(["--workspace", str(tmp_workspace.root), "doctor"])
    out = capsys.readouterr().out

    assert "ingest-settings.local.json: fallback ->" in out, out
    # The fallback line points to the actual repo-root file, not an opaque
    # marker.
    assert str(tmp_workspace.ingest_fallback_settings_path) in out, out


def test_doctor_resolution_block_shows_primary(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """Doctor prints the plain path (no ``fallback ->``) when primary exists."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    # Sanity: ingest-settings.local.json is present on a fresh tmp_workspace.
    assert tmp_workspace.ingest_settings_path.exists()

    path, is_fallback = resolve_ingest_settings(tmp_workspace)
    assert is_fallback is False
    assert path == tmp_workspace.ingest_settings_path

    _run_cli(["--workspace", str(tmp_workspace.root), "doctor"])
    out = capsys.readouterr().out

    # Plain form is present; fallback form is not.
    assert (
        f"ingest-settings.local.json: {tmp_workspace.ingest_settings_path}"
        in out
    ), out
    assert "ingest-settings.local.json: fallback ->" not in out, out


# ---------------------------------------------------------------------------
# AC-4 / AC-5: --verbose resolution block on non-doctor commands
# ---------------------------------------------------------------------------


def test_verbose_shows_fallback_on_non_doctor_command(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """``--verbose lint`` prints the ``Resolved config:`` block with fallback lines."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    if tmp_workspace.wikiignore_path.exists():
        tmp_workspace.wikiignore_path.unlink()

    path, is_fallback = resolve_wikiignore(tmp_workspace)
    assert is_fallback is True
    assert path == tmp_workspace.wikiignore_fallback_path

    _run_cli(
        ["--workspace", str(tmp_workspace.root), "--verbose", "lint"]
    )
    out = capsys.readouterr().out

    assert "Resolved config:" in out, out
    assert ".wikiignore: fallback ->" in out, out
    assert str(tmp_workspace.wikiignore_fallback_path) in out, out


def test_non_verbose_silent_on_fallback(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """Non-verbose lint does NOT print the resolution block even with fallbacks in play."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    if tmp_workspace.wikiignore_path.exists():
        tmp_workspace.wikiignore_path.unlink()

    # Sanity: the fallback IS in effect; we want to be sure the silence is
    # because --verbose is absent, not because there is no fallback to print.
    _path, is_fallback = resolve_wikiignore(tmp_workspace)
    assert is_fallback is True

    _run_cli(["--workspace", str(tmp_workspace.root), "lint"])
    out = capsys.readouterr().out

    assert "Resolved config:" not in out, out
    assert "fallback ->" not in out, out


# ---------------------------------------------------------------------------
# AC-6: edge case -- .env missing in both locations -> doctor FAILs
# ---------------------------------------------------------------------------


def test_all_five_fallbacks_missing_env_both_places(
    tmp_workspace: WorkspacePaths, monkeypatch, capsys
) -> None:
    """``.env`` absent in workspace AND in repo-root -> doctor FAIL + exit 1.

    We cannot delete the real repo-root ``.env`` (it may be present on the
    developer machine and is outside this test's blast radius), so we
    redirect ``env_fallback_path`` to a non-existent location to simulate
    the "both absent" state.  ``resolve_env`` must report ``(None, False)``
    and ``doctor`` must exit 1 with a ``.env missing`` FAIL line.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tmp_workspace.env_path.unlink()

    redirected = replace(
        tmp_workspace,
        env_fallback_path=tmp_workspace.root / "no-such.env",
    )

    # Precondition: resolver reports no env anywhere.
    env_path, env_is_fallback = resolve_env(redirected)
    assert env_path is None, env_path
    assert env_is_fallback is False

    # Bypass cli.main so we can pass the redirected WorkspacePaths directly.
    rc = doctor.main([], redirected)
    out = capsys.readouterr().out

    assert rc == 1, out
    assert ".env missing" in out, out
    # Resolution block additionally reports '<none found>' on the .env line.
    assert ".env: <none found>" in out, out
