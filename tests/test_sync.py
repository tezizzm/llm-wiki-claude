import json
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYNC_PATH = ROOT / "scripts" / "sync.py"
spec = importlib.util.spec_from_file_location("sync_module", SYNC_PATH)
sync = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sync)

from scripts.workspace import resolve_workspace


def _workspace_at(path: Path):
    """Return a WorkspacePaths rooted at ``path`` (source='flag')."""
    return resolve_workspace(str(path), None)


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


def test_sync_file_adds_hash_suffix_on_collision(tmp_path):
    source_root = tmp_path / "source"
    raw_dir = tmp_path / "raw" / "inbox"
    state_dir = tmp_path / "state"
    source_root.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    workspace = _workspace_at(tmp_path)

    first = source_root / "docs" / "overview.md"
    second = source_root / "guides" / "overview.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    manifest = {"files": {}}
    result1 = sync.sync_file(
        workspace, first, "proj", source_root,
        {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False,
    )
    result2 = sync.sync_file(
        workspace, second, "proj", source_root,
        {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False,
    )

    assert result1["target_name"] == "proj__overview.md"
    assert result2["target_name"].startswith("proj__overview__")
    assert result2["target_name"].endswith(".md")
    assert (raw_dir / result1["target_name"]).exists()
    assert (raw_dir / result2["target_name"]).exists()


def test_sync_file_reports_unchanged_on_second_run(tmp_path):
    source_root = tmp_path / "source"
    raw_dir = tmp_path / "raw" / "inbox"
    state_dir = tmp_path / "state"
    source_root.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    workspace = _workspace_at(tmp_path)

    path = source_root / "README.md"
    path.write_text("hello", encoding="utf-8")
    manifest = {"files": {}}

    first = sync.sync_file(
        workspace, path, "proj", source_root,
        {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False,
    )
    second = sync.sync_file(
        workspace, path, "proj", source_root,
        {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False,
    )

    assert first["status"] == "copied"
    assert second["status"] == "unchanged"


def test_prune_only_removes_managed_files_for_available_sources(tmp_path, capsys):
    raw_dir = tmp_path / "raw" / "inbox"
    raw_dir.mkdir(parents=True)

    workspace = _workspace_at(tmp_path)

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
        workspace=workspace,
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


def test_resolve_config_path_prefers_local_override(tmp_path):
    local = tmp_path / "sync-sources.local.json"
    fallback = tmp_path / "sync-sources.json"
    local.write_text("{}", encoding="utf-8")
    fallback.write_text("{}", encoding="utf-8")

    # Build a workspace with primary=local and fallback=tmp_path/sync-sources.json.
    # The repo-root fallback path in WorkspacePaths is repo_root/sync-sources.json,
    # so we need a workspace whose sync_fallback_config_path points at our
    # tmp fallback.  Easiest: monkey-construct via resolve_workspace on tmp_path
    # and override the fallback field via dataclasses.replace.
    from dataclasses import replace

    workspace = replace(
        _workspace_at(tmp_path),
        sync_fallback_config_path=fallback,
    )

    assert sync.resolve_config_path(workspace) == local


def test_resolve_config_path_falls_back_when_local_missing(tmp_path):
    fallback = tmp_path / "sync-sources.json"
    fallback.write_text("{}", encoding="utf-8")

    from dataclasses import replace

    workspace = replace(
        _workspace_at(tmp_path),
        sync_fallback_config_path=fallback,
    )
    # No sync-sources.local.json in the workspace root -> should fall back.
    assert sync.resolve_config_path(workspace) == fallback


def test_resolve_config_path_uses_explicit_override(tmp_path):
    explicit = tmp_path / "custom.json"
    explicit.write_text("{}", encoding="utf-8")

    workspace = _workspace_at(tmp_path)
    assert sync.resolve_config_path(workspace, str(explicit)) == explicit


def test_template_sync_config_is_generic():
    config = json.loads(Path("sync-sources.json").read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    source = config["sources"][0]
    assert source["name"] == "my-project"
    assert source["root"] == "/absolute/path/to/your/project"


def test_prepare_sync_config_warns_for_missing_schema_version(tmp_path):
    config_path = tmp_path / "sync-sources.json"
    prepared, warnings = sync.prepare_sync_config({"sources": []}, config_path)

    assert prepared["schema_version"] == 1
    assert "missing `schema_version`" in warnings[0]


# ---------------------------------------------------------------------------
# Story LWC-btzz: module-level path constants are deleted
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
def test_deleted_module_constants_raise_attribute_error(attr):
    """The six path constants listed in ARCHITECTURE §5.3 must not exist."""
    import scripts.sync as sync_pkg  # re-import via installed package name

    assert not hasattr(sync_pkg, attr), (
        f"sync.{attr} should have been deleted by LWC-btzz but still exists"
    )


def test_sync_schema_version_is_kept():
    """SYNC_SCHEMA_VERSION is a schema literal, not a path; must remain."""
    import scripts.sync as sync_pkg

    assert sync_pkg.SYNC_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Story LWC-btzz: integration smoke for main(argv, workspace)
# ---------------------------------------------------------------------------


def test_main_runs_against_tmp_workspace(tmp_path, capsys):
    """``main([], workspace)`` populates raw/inbox from a minimal sync config."""
    import scripts.sync as sync_pkg

    # Build a workspace with a workspace-local sync config pointing at a
    # tmp source tree.
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "state").mkdir()
    (workspace_root / "raw" / "inbox").mkdir(parents=True)

    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "README.md").write_text("hello", encoding="utf-8")

    (workspace_root / "sync-sources.local.json").write_text(
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

    workspace = _workspace_at(workspace_root)
    code = sync_pkg.main([], workspace)

    assert code == 0
    assert (workspace_root / "raw" / "inbox" / "demo__readme.md").exists()
    assert (workspace_root / "state" / "sync_manifest.json").exists()


def test_cli_dispatches_sync_with_workspace(tmp_path, monkeypatch, capsys):
    """``llm-wiki --workspace X sync`` routes via DISPATCH and prints banner."""
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "state").mkdir()
    (workspace_root / "raw" / "inbox").mkdir(parents=True)

    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "README.md").write_text("hello", encoding="utf-8")

    (workspace_root / "sync-sources.local.json").write_text(
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

    from scripts import cli

    code = cli.main(["--workspace", str(workspace_root), "sync"])
    assert code == 0

    out = capsys.readouterr().out
    # Banner is printed for non-default workspace sources
    assert f"Workspace: {workspace_root} (from --workspace)" in out
    # Copy actually happened
    assert (workspace_root / "raw" / "inbox" / "demo__readme.md").exists()
