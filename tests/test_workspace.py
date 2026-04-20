"""Unit tests for scripts.workspace.

These tests cover the dataclass's frozen semantics, the ``repo_root()``
helper, and the exception hierarchy. Path resolution behaviour (flag / env /
default precedence) is covered by sibling stories in the walking skeleton.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts import workspace
from scripts.workspace import (
    WorkspaceError,
    WorkspaceInvalidError,
    WorkspaceNotFoundError,
    WorkspacePaths,
    repo_root,
    resolve_workspace,
    resolve_workspace_for_init,
)


def _build_paths(root: Path) -> WorkspacePaths:
    """Construct a WorkspacePaths instance from a root directory.

    Intentionally duplicates the derivation rules from ARCHITECTURE §3.1 so
    this test file can stand alone without a resolver helper.
    """

    wiki_dir = root / "wiki"
    state_dir = root / "state"
    repo = repo_root()
    return WorkspacePaths(
        root=root,
        source="default",
        repo_root=repo,
        raw_dir=root / "raw" / "inbox",
        wiki_dir=wiki_dir,
        summaries_dir=wiki_dir / "summaries",
        topics_dir=wiki_dir / "topics",
        entities_dir=wiki_dir / "entities",
        state_dir=state_dir,
        manifest_path=state_dir / "manifest.json",
        sync_manifest_path=state_dir / "sync_manifest.json",
        last_ingest_run_path=state_dir / "last_ingest_run.json",
        ingest_events_path=state_dir / "ingest_events.jsonl",
        ingest_report_path=state_dir / "last_ingest_report.md",
        index_path=root / "index.md",
        log_path=root / "log.md",
        env_path=root / ".env",
        sync_config_path=root / "sync-sources.local.json",
        sync_fallback_config_path=repo / "sync-sources.json",
        ingest_settings_path=root / "ingest-settings.local.json",
        ingest_fallback_settings_path=repo / "ingest-settings.json",
        schema_path=root / "schemas" / "AGENTS.md",
        schema_fallback_path=repo / "schemas" / "AGENTS.md",
        wikiignore_path=root / ".wikiignore",
        wikiignore_fallback_path=repo / ".wikiignore",
        env_fallback_path=repo / ".env",
    )


def test_workspacepaths_is_frozen(tmp_path: Path) -> None:
    """Assigning to any field must raise FrozenInstanceError."""
    paths = _build_paths(tmp_path)

    with pytest.raises(FrozenInstanceError):
        paths.root = tmp_path / "other"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        paths.state_dir = tmp_path / "state2"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        paths.source = "flag"  # type: ignore[misc]


def test_repo_root_returns_expected_path() -> None:
    """repo_root() must resolve to scripts/workspace.py's parents[1]."""
    expected = Path(workspace.__file__).resolve().parents[1]

    result = repo_root()

    assert result == expected
    assert result.is_absolute()
    assert result == result.resolve()
    assert result.exists(), f"expected repo root to exist: {result}"
    assert (result / "scripts").is_dir(), (
        f"expected repo root to contain scripts/: {result}"
    )


def test_workspace_exceptions_exist(tmp_path: Path) -> None:
    """Exception classes are wired correctly and carry the expected payload."""
    # Base hierarchy
    assert issubclass(WorkspaceError, Exception)
    assert issubclass(WorkspaceNotFoundError, WorkspaceError)
    assert issubclass(WorkspaceInvalidError, WorkspaceError)

    # NotFound carries the offending path
    missing = tmp_path / "does-not-exist"
    err = WorkspaceNotFoundError(missing)
    assert err.path == missing
    assert str(missing) in str(err)
    assert isinstance(err, WorkspaceError)

    # Invalid is raiseable and catchable as WorkspaceError
    with pytest.raises(WorkspaceError):
        raise WorkspaceInvalidError("not a directory")


# ---------------------------------------------------------------------------
# resolve_workspace() precedence tests
# ---------------------------------------------------------------------------


def _assert_all_paths_absolute_and_clean(ws: WorkspacePaths) -> None:
    """Every Path field on ``ws`` must be absolute and contain no '..' parts."""
    for field_name in ws.__dataclass_fields__:
        value = getattr(ws, field_name)
        if isinstance(value, Path):
            assert value.is_absolute(), (
                f"{field_name} is not absolute: {value}"
            )
            assert ".." not in value.parts, (
                f"{field_name} contains '..': {value}"
            )


def test_resolve_default_returns_repo_root() -> None:
    """With no flag and no env var, resolve_workspace returns the repo-root default."""
    ws = resolve_workspace(None, None)

    assert ws.source == "default"
    assert ws.root == repo_root()
    assert ws.repo_root == repo_root()


def test_resolve_flag_wins_over_env(tmp_path: Path) -> None:
    """Flag takes precedence over env var when both are provided and both exist."""
    flag_dir = tmp_path / "flag-ws"
    env_dir = tmp_path / "env-ws"
    flag_dir.mkdir()
    env_dir.mkdir()

    ws = resolve_workspace(str(flag_dir), str(env_dir))

    assert ws.source == "flag"
    assert ws.root == flag_dir.resolve()


def test_resolve_env_used_when_no_flag(tmp_path: Path) -> None:
    """With no flag but an env var, source='env' and root derives from env."""
    env_dir = tmp_path / "env-ws"
    env_dir.mkdir()

    ws = resolve_workspace(None, str(env_dir))

    assert ws.source == "env"
    assert ws.root == env_dir.resolve()


def test_resolve_empty_string_flag_falls_through_to_env(tmp_path: Path) -> None:
    """An empty-string flag value should not be treated as 'flag supplied'."""
    env_dir = tmp_path / "env-ws"
    env_dir.mkdir()

    ws = resolve_workspace("", str(env_dir))

    assert ws.source == "env"
    assert ws.root == env_dir.resolve()


def test_resolve_empty_env_falls_through_to_default() -> None:
    """An empty-string env var should not be treated as 'env supplied'."""
    ws = resolve_workspace(None, "")

    assert ws.source == "default"
    assert ws.root == repo_root()


def test_resolve_relative_path_resolves_against_cwd(tmp_path: Path) -> None:
    """A relative flag path resolves against the injected cwd."""
    target = tmp_path / "relative-dir"
    target.mkdir()

    ws = resolve_workspace("relative-dir", None, cwd=tmp_path)

    assert ws.source == "flag"
    assert ws.root == target.resolve()


def test_resolve_relative_env_resolves_against_cwd(tmp_path: Path) -> None:
    """A relative env-var path also resolves against the injected cwd."""
    target = tmp_path / "env-relative"
    target.mkdir()

    ws = resolve_workspace(None, "env-relative", cwd=tmp_path)

    assert ws.source == "env"
    assert ws.root == target.resolve()


def test_resolve_nonexistent_flag_raises(tmp_path: Path) -> None:
    """A non-existent --workspace flag path raises WorkspaceNotFoundError."""
    missing = tmp_path / "does-not-exist"

    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        resolve_workspace(str(missing), None)

    assert excinfo.value.path == missing.resolve()


def test_resolve_nonexistent_env_raises(tmp_path: Path) -> None:
    """A non-existent env-var path raises WorkspaceNotFoundError."""
    missing = tmp_path / "also-missing"

    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        resolve_workspace(None, str(missing))

    assert excinfo.value.path == missing.resolve()


def test_resolve_nonexistent_default_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """resolve_workspace(None, None) never raises, even if repo_root were missing.

    The repo-root default is always taken as-is per ARCHITECTURE §3.3 — its
    existence is guaranteed by install. We simulate a missing repo-root by
    patching ``repo_root`` to return a path that does not exist, and confirm
    no exception is raised.
    """

    fake_root = tmp_path / "not-installed"
    # Deliberately do NOT create fake_root on disk.
    monkeypatch.setattr(workspace, "repo_root", lambda: fake_root)

    ws = resolve_workspace(None, None)

    assert ws.source == "default"
    assert ws.root == fake_root.resolve()


def test_workspace_paths_all_absolute_and_resolved(tmp_path: Path) -> None:
    """Every Path field on the returned WorkspacePaths is absolute and '..'-free."""
    ws_dir = tmp_path / "clean-ws"
    ws_dir.mkdir()

    # Pass a relative path with '..' segments to force normalization.
    nested = tmp_path / "nested"
    nested.mkdir()
    relative_with_dotdot = f"nested/../clean-ws"

    ws = resolve_workspace(relative_with_dotdot, None, cwd=tmp_path)

    assert ws.root == ws_dir.resolve()
    _assert_all_paths_absolute_and_clean(ws)


def test_resolve_default_paths_include_repo_root_fallbacks() -> None:
    """For the default workspace, fallback paths point into the repo root.

    Primary sync_config_path has ``.local.json`` suffix (may not exist);
    sync_fallback_config_path is the repo-root ``sync-sources.json`` that
    preserved 0.2.0 behavior relies on (per ARCHITECTURE §3.1).
    """

    ws = resolve_workspace(None, None)

    assert ws.sync_config_path == repo_root() / "sync-sources.local.json"
    assert ws.sync_fallback_config_path == repo_root() / "sync-sources.json"
    # The repo actually ships the fallback config, so it must exist on disk.
    assert ws.sync_fallback_config_path.exists()


# ---------------------------------------------------------------------------
# resolve_workspace_for_init() tests
# ---------------------------------------------------------------------------


def test_resolve_workspace_for_init_allows_nonexistent(tmp_path: Path) -> None:
    """Init must accept a path that does not yet exist on disk."""
    target = tmp_path / "does" / "not" / "yet" / "exist"

    result = resolve_workspace_for_init(str(target))

    assert result == target.resolve()
    assert result.is_absolute()


def test_resolve_workspace_for_init_resolves_relative_against_cwd(
    tmp_path: Path,
) -> None:
    """A relative init target resolves against the injected cwd."""
    result = resolve_workspace_for_init("new-ws", cwd=tmp_path)

    assert result == (tmp_path / "new-ws").resolve()
    assert result.is_absolute()


def test_resolve_workspace_for_init_does_not_check_existence(
    tmp_path: Path,
) -> None:
    """Even with a cwd whose child does not exist, init does not raise."""
    # Note: tmp_path exists, child does not.
    result = resolve_workspace_for_init("brand-new", cwd=tmp_path)

    assert result == (tmp_path / "brand-new").resolve()
    assert not result.exists()


def test_resolve_workspace_for_init_absolute_path_passthrough(
    tmp_path: Path,
) -> None:
    """An absolute path is returned resolved, unchanged in value."""
    absolute_target = tmp_path / "absolute-init-target"

    result = resolve_workspace_for_init(str(absolute_target))

    assert result == absolute_target.resolve()
