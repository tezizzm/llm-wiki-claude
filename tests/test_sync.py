"""Unit tests for scripts.sync with the workspace-aware contract (LWC-btzz).

All tests in this module use the ``tmp_workspace`` fixture from
``tests/conftest.py`` (LWC-tkbs) and call ``sync.main(argv, workspace)``
directly or route through ``cli.main`` with ``LLM_WIKI_WORKSPACE``.

Monkeypatching of module-level ``sync`` path constants is FORBIDDEN: those
constants were deleted in LWC-btzz. See ARCHITECTURE §5.3, §11.1, §11.3.
"""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import sync
from scripts.workspace import WorkspacePaths, resolve_workspace


# ---------------------------------------------------------------------------
# Pure helpers -- exercised directly, no workspace required
# ---------------------------------------------------------------------------


def test_build_target_name_preserves_relative_path():
    rel = Path("docs/architecture/overview.md")
    name = sync.build_target_name(
        source_name="agentmesh",
        rel_path=rel,
        naming={"mode": "preserve_path", "prefix": "agentmesh"},
    )
    assert name == "agentmesh__docs__architecture__overview.md"


def test_build_target_name_basename_mode():
    rel = Path("docs/architecture/overview.md")
    name = sync.build_target_name(
        source_name="agentmesh",
        rel_path=rel,
        naming={"mode": "basename", "prefix": "agentmesh"},
    )
    assert name == "agentmesh__overview.md"


def test_prepare_sync_config_warns_for_missing_schema_version(tmp_path):
    config_path = tmp_path / "sync-sources.json"
    prepared, warnings = sync.prepare_sync_config({"sources": []}, config_path)

    assert prepared["schema_version"] == 1
    assert "missing `schema_version`" in warnings[0]


# ---------------------------------------------------------------------------
# sync_file: naming modes, collision detection, manifest behavior
# ---------------------------------------------------------------------------


def _make_source_file(source_root: Path, rel_path: str, body: str) -> Path:
    """Write a source file at ``source_root / rel_path`` and return the path."""
    path = source_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_sync_file_adds_hash_suffix_on_collision(tmp_workspace, tmp_path):
    """Two sources with the same basename collide -> second gets a hash suffix."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    first = _make_source_file(source_root, "docs/overview.md", "first")
    second = _make_source_file(source_root, "guides/overview.md", "second")

    manifest: dict = {"files": {}}
    result1 = sync.sync_file(
        tmp_workspace, first, "proj", source_root,
        {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False,
    )
    result2 = sync.sync_file(
        tmp_workspace, second, "proj", source_root,
        {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False,
    )

    assert result1["target_name"] == "proj__overview.md"
    assert result2["target_name"].startswith("proj__overview__")
    assert result2["target_name"].endswith(".md")
    assert (tmp_workspace.raw_dir / result1["target_name"]).exists()
    assert (tmp_workspace.raw_dir / result2["target_name"]).exists()


def test_sync_file_reports_unchanged_on_second_run(tmp_workspace, tmp_path):
    """Re-syncing an unchanged source file yields status='unchanged'."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    path = _make_source_file(source_root, "README.md", "hello")

    manifest: dict = {"files": {}}
    first = sync.sync_file(
        tmp_workspace, path, "proj", source_root,
        {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False,
    )
    second = sync.sync_file(
        tmp_workspace, path, "proj", source_root,
        {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False,
    )

    assert first["status"] == "copied"
    assert second["status"] == "unchanged"


def test_sync_file_dry_run_does_not_write(tmp_workspace, tmp_path):
    """Dry-run reports the planned action but does not copy the file."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    path = _make_source_file(source_root, "README.md", "hello")

    manifest: dict = {"files": {}}
    result = sync.sync_file(
        tmp_workspace, path, "proj", source_root,
        {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=True,
    )

    assert result["status"] == "copied"
    assert not (tmp_workspace.raw_dir / result["target_name"]).exists()


# ---------------------------------------------------------------------------
# Prune behavior
# ---------------------------------------------------------------------------


def test_prune_only_removes_managed_files_for_available_sources(
    tmp_workspace, capsys
):
    """Prune only touches files managed by a currently-available source."""
    raw_dir = tmp_workspace.raw_dir
    managed = raw_dir / "managed.md"
    other_source = raw_dir / "other.md"
    unmanaged = raw_dir / "unmanaged.md"
    for path in [managed, other_source, unmanaged]:
        path.write_text(path.stem, encoding="utf-8")

    manifest = {
        "files": {
            "managed.md": {"source_name": "proj"},
            "other.md": {"source_name": "missing-source"},
        }
    }

    removed = sync.prune_managed_files(
        workspace=tmp_workspace,
        selected_source_names=["proj", "missing-source"],
        desired_targets=set(),
        available_source_names={"proj"},
        sync_manifest=manifest,
        dry_run=False,
    )

    assert removed == 1
    assert not managed.exists()
    assert other_source.exists()
    assert unmanaged.exists()
    assert "managed.md" not in manifest["files"]
    assert "other.md" in manifest["files"]
    assert "pruned" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# resolve_config_path: primary vs. fallback vs. explicit
# ---------------------------------------------------------------------------


def test_resolve_config_path_prefers_local_override(tmp_workspace, tmp_path):
    """Workspace's sync-sources.local.json wins over the repo-root fallback."""
    # tmp_workspace already has sync-sources.local.json populated by the fixture.
    assert tmp_workspace.sync_config_path.exists()

    # Redirect the fallback to a sibling path that also exists, so the resolver
    # MUST return the primary even though a fallback is available.
    fallback = tmp_path / "alt-sync-sources.json"
    fallback.write_text("{}", encoding="utf-8")
    workspace = replace(tmp_workspace, sync_fallback_config_path=fallback)

    assert sync.resolve_config_path(workspace) == tmp_workspace.sync_config_path


def test_resolve_config_path_falls_back_when_local_missing(tmp_workspace, tmp_path):
    """Workspace-local sync-sources.local.json missing -> fallback path wins."""
    tmp_workspace.sync_config_path.unlink()

    fallback = tmp_path / "fallback-sync-sources.json"
    fallback.write_text("{}", encoding="utf-8")
    workspace = replace(tmp_workspace, sync_fallback_config_path=fallback)

    assert sync.resolve_config_path(workspace) == fallback


def test_resolve_config_path_uses_explicit_override(tmp_workspace, tmp_path):
    """An explicit --config argument beats both primary and fallback."""
    explicit = tmp_path / "custom.json"
    explicit.write_text("{}", encoding="utf-8")

    assert (
        sync.resolve_config_path(tmp_workspace, str(explicit)) == explicit
    )


def test_resolve_config_path_returns_fallback_when_neither_exists(
    tmp_workspace, tmp_path
):
    """Primary missing and fallback missing -> returns fallback path (caller surfaces error)."""
    tmp_workspace.sync_config_path.unlink()
    missing_fallback = tmp_path / "does-not-exist.json"
    workspace = replace(
        tmp_workspace, sync_fallback_config_path=missing_fallback
    )

    # The helper is documented to "never raise on a missing fallback: it
    # returns the fallback path so callers can print a user-facing error."
    assert sync.resolve_config_path(workspace) == missing_fallback


# ---------------------------------------------------------------------------
# Template sync config (repo-root default)
# ---------------------------------------------------------------------------


def test_template_sync_config_is_generic():
    """The checked-in repo-root sync-sources.json is a generic template."""
    config = json.loads(Path("sync-sources.json").read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    source = config["sources"][0]
    assert source["name"] == "my-project"
    assert source["root"] == "/absolute/path/to/your/project"


# ---------------------------------------------------------------------------
# LWC-btzz: module-level path constants are deleted (AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr",
    [
        "ROOT",
        "RAW_DIR",
        "STATE_DIR",
        "SYNC_CONFIG_PATH",
        "SYNC_FALLBACK_CONFIG_PATH",
        "SYNC_MANIFEST_PATH",
    ],
)
def test_sync_module_constants_deleted(attr):
    """The six path constants listed in ARCHITECTURE §5.3 must not exist.

    Per LWC-of8w AC-3, every constant must raise ``AttributeError`` when
    accessed via ``getattr`` without a default. See ARCHITECTURE §5.3 for
    the canonical list.
    """
    with pytest.raises(AttributeError):
        getattr(sync, attr)


def test_sync_schema_version_is_kept():
    """SYNC_SCHEMA_VERSION is a schema literal, not a path; it must remain."""
    assert sync.SYNC_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# LWC-btzz integration: main(argv, workspace) against the tmp_workspace fixture
# ---------------------------------------------------------------------------


def _write_sync_sources(
    workspace: WorkspacePaths,
    source_root: Path,
    *,
    local: bool = True,
) -> None:
    """Write a minimal sync-sources config with one real source root."""
    payload = {
        "schema_version": 1,
        "sources": [
            {
                "name": "demo",
                "root": str(source_root),
                "include": ["README.md"],
                "exclude": [],
                "naming": {"mode": "preserve_path", "prefix": "demo"},
            }
        ],
    }
    target = (
        workspace.sync_config_path if local else workspace.sync_fallback_config_path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_main_runs_against_tmp_workspace(tmp_workspace, tmp_path, capsys):
    """``main([], workspace)`` populates raw/inbox from a minimal sync config."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(tmp_workspace, source_root, local=True)

    code = sync.main([], tmp_workspace)

    assert code == 0
    assert (tmp_workspace.raw_dir / "demo__readme.md").exists()
    assert tmp_workspace.sync_manifest_path.exists()


def test_main_dry_run_does_not_touch_raw_inbox(tmp_workspace, tmp_path):
    """``main(['--dry-run'], workspace)`` copies nothing to raw/inbox."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(tmp_workspace, source_root, local=True)

    code = sync.main(["--dry-run"], tmp_workspace)

    assert code == 0
    assert not (tmp_workspace.raw_dir / "demo__readme.md").exists()


def test_main_prune_removes_orphaned_targets(tmp_workspace, tmp_path):
    """``main(['--prune'], workspace)`` drops files no longer in the source."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(tmp_workspace, source_root, local=True)

    # First run: seed the manifest + raw/inbox with demo__readme.md.
    assert sync.main([], tmp_workspace) == 0
    assert (tmp_workspace.raw_dir / "demo__readme.md").exists()

    # Remove the source file; rerun with --prune.
    (source_root / "README.md").unlink()
    assert sync.main(["--prune"], tmp_workspace) == 0
    assert not (tmp_workspace.raw_dir / "demo__readme.md").exists()


def test_sync_fallback_config_used_when_workspace_missing_local(
    tmp_workspace, tmp_path, monkeypatch, capsys
):
    """LWC-of8w AC-4: workspace missing sync-sources.local.json -> fallback wins.

    The workspace root has no sync-sources.local.json, but the resolver's
    fallback (a sibling JSON file in this test, standing in for the repo-root
    template) provides a valid config. ``main`` must proceed successfully.
    """
    # Remove the fixture's workspace-local sync config so the resolver has to
    # consult the fallback path.
    tmp_workspace.sync_config_path.unlink()
    assert not tmp_workspace.sync_config_path.exists()

    # Build a usable fallback with one real source root, then splice it into
    # the workspace via dataclasses.replace (WorkspacePaths is frozen).
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")

    fallback = tmp_path / "fallback-sync-sources.json"
    fallback.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "name": "demo",
                        "root": str(source_root),
                        "include": ["README.md"],
                        "exclude": [],
                        "naming": {"mode": "preserve_path", "prefix": "demo"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workspace = replace(tmp_workspace, sync_fallback_config_path=fallback)

    # resolve_config_path must return the fallback, and main must succeed.
    assert sync.resolve_config_path(workspace) == fallback

    code = sync.main([], workspace)
    assert code == 0
    assert (workspace.raw_dir / "demo__readme.md").exists()
    assert workspace.sync_manifest_path.exists()


def test_main_with_missing_config_and_no_fallback_prints_error(
    tmp_workspace, tmp_path, capsys
):
    """When both primary and fallback are absent, main reports and returns 0."""
    tmp_workspace.sync_config_path.unlink()
    workspace = replace(
        tmp_workspace,
        sync_fallback_config_path=tmp_path / "does-not-exist.json",
    )

    code = sync.main([], workspace)
    assert code == 0
    out = capsys.readouterr().out
    assert "sync config not found" in out


def test_cli_dispatches_sync_with_workspace_flag(tmp_workspace, tmp_path, capsys):
    """``llm-wiki --workspace X sync`` routes via DISPATCH and prints banner."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(tmp_workspace, source_root, local=True)

    from scripts import cli

    code = cli.main(["--workspace", str(tmp_workspace.root), "sync"])
    assert code == 0

    out = capsys.readouterr().out
    # Banner is printed for non-default workspace sources
    assert f"Workspace: {tmp_workspace.root} (from --workspace)" in out
    # Copy actually happened
    assert (tmp_workspace.raw_dir / "demo__readme.md").exists()


def test_cli_dispatches_sync_with_env_var(
    tmp_workspace, tmp_path, monkeypatch, capsys
):
    """``LLM_WIKI_WORKSPACE=X llm-wiki sync`` also routes through DISPATCH."""
    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(tmp_workspace, source_root, local=True)

    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(tmp_workspace.root))

    from scripts import cli

    code = cli.main(["sync"])
    assert code == 0

    out = capsys.readouterr().out
    assert (
        f"Workspace: {tmp_workspace.root} (from LLM_WIKI_WORKSPACE)" in out
    )
    assert (tmp_workspace.raw_dir / "demo__readme.md").exists()


# ---------------------------------------------------------------------------
# Isolation: sync against workspace A does not touch workspace B
# ---------------------------------------------------------------------------


def test_sync_two_workspaces_isolated(two_workspaces, tmp_path):
    """Running sync in workspace A leaves workspace B's raw/inbox untouched."""
    ws_a, ws_b = two_workspaces

    source_root = tmp_path / "src"
    source_root.mkdir()
    _make_source_file(source_root, "README.md", "hello")
    _write_sync_sources(ws_a, source_root, local=True)

    # Snapshot ws_b's raw/inbox BEFORE the ws_a sync runs.
    before = sorted(p.name for p in ws_b.raw_dir.iterdir())

    code = sync.main([], ws_a)
    assert code == 0

    after = sorted(p.name for p in ws_b.raw_dir.iterdir())
    assert before == after
    # And the sync actually produced the expected file in ws_a.
    assert (ws_a.raw_dir / "demo__readme.md").exists()
