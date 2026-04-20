"""Shared test fixtures for workspace-aware commands.

``tmp_workspace`` hand-builds a workspace directory tree that mirrors what
``llm-wiki init`` will eventually produce (ARCHITECTURE §11.3).  Until that
command lands in a later epic, every fixture that needs a clean, populated
workspace for doctor/sync/ingest/query/lint tests lives here.

Once init is implemented, this fixture will be simplified to a single call
into ``init.main`` -- that refactor is tracked as its own story in the init
epic.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workspace import WorkspacePaths, repo_root, resolve_workspace


def _populate_workspace(root: Path) -> None:
    """Create the minimum directory tree + config files for a clean workspace.

    Mirrors what ``init`` will produce.  The workspace is "clean" in the
    FAIL/WARN/OK sense: every structural prerequisite present, no warnings.
    """
    # Structural directories
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "topics").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)

    # Seed raw/inbox with one placeholder so "empty" WARN doesn't fire.
    (root / "raw" / "inbox" / "placeholder.md").write_text(
        "# placeholder\n", encoding="utf-8"
    )

    # sync-sources.local.json with a real, existing source root.
    sources_root = root / "sources"
    sources_root.mkdir(exist_ok=True)
    sync_config = {
        "schema_version": 1,
        "sources": [{"name": "test", "root": str(sources_root)}],
    }
    (root / "sync-sources.local.json").write_text(
        json.dumps(sync_config), encoding="utf-8"
    )

    # ingest-settings.local.json -- empty object is valid (defaults merge).
    (root / "ingest-settings.local.json").write_text("{}", encoding="utf-8")

    # .env with a real API key
    (root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n", encoding="utf-8")


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> WorkspacePaths:
    """Build a clean workspace under ``tmp_path`` and return its WorkspacePaths.

    The returned ``WorkspacePaths`` carries ``source='flag'`` so that banner
    and resolution logic see a non-default workspace.  Tests that need the
    repo-root ``default`` case should use ``resolve_workspace(None, None)``
    directly.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    _populate_workspace(root)
    # Build via resolve_workspace so every derived field is computed in the
    # canonical way (and so the source is 'flag', matching real CLI usage).
    return resolve_workspace(str(root), None)


@pytest.fixture
def two_workspaces(tmp_path: Path) -> tuple[WorkspacePaths, WorkspacePaths]:
    """Two independent clean workspaces in the same tmp_path.

    Used by isolation tests (an operation against workspace A must not touch
    files under workspace B).
    """
    root_a = tmp_path / "workspace_a"
    root_b = tmp_path / "workspace_b"
    root_a.mkdir()
    root_b.mkdir()
    _populate_workspace(root_a)
    _populate_workspace(root_b)
    return (
        resolve_workspace(str(root_a), None),
        resolve_workspace(str(root_b), None),
    )
