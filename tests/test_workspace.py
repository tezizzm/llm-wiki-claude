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
