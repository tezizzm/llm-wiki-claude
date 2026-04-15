import json
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYNC_PATH = ROOT / "scripts" / "sync.py"
spec = importlib.util.spec_from_file_location("sync_module", SYNC_PATH)
sync = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sync)


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


def test_sync_file_adds_hash_suffix_on_collision(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    raw_dir = tmp_path / "raw" / "inbox"
    state_dir = tmp_path / "state"
    source_root.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(sync, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sync, "STATE_DIR", state_dir)

    first = source_root / "docs" / "overview.md"
    second = source_root / "guides" / "overview.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    manifest = {"files": {}}
    result1 = sync.sync_file(first, "proj", source_root, {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False)
    result2 = sync.sync_file(second, "proj", source_root, {"mode": "basename", "prefix": "proj"}, manifest, dry_run=False)

    assert result1["target_name"] == "proj__overview.md"
    assert result2["target_name"].startswith("proj__overview__")
    assert result2["target_name"].endswith(".md")
    assert (raw_dir / result1["target_name"]).exists()
    assert (raw_dir / result2["target_name"]).exists()


def test_sync_file_reports_unchanged_on_second_run(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    raw_dir = tmp_path / "raw" / "inbox"
    state_dir = tmp_path / "state"
    source_root.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(sync, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sync, "STATE_DIR", state_dir)

    path = source_root / "README.md"
    path.write_text("hello", encoding="utf-8")
    manifest = {"files": {}}

    first = sync.sync_file(path, "proj", source_root, {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False)
    second = sync.sync_file(path, "proj", source_root, {"mode": "preserve_path", "prefix": "proj"}, manifest, dry_run=False)

    assert first["status"] == "copied"
    assert second["status"] == "unchanged"


def test_prune_only_removes_managed_files_for_available_sources(tmp_path, monkeypatch, capsys):
    raw_dir = tmp_path / "raw" / "inbox"
    raw_dir.mkdir(parents=True)
    monkeypatch.setattr(sync, "RAW_DIR", raw_dir)

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


def test_resolve_config_path_prefers_local_override(tmp_path, monkeypatch):
    local = tmp_path / "sync-sources.local.json"
    fallback = tmp_path / "sync-sources.json"
    local.write_text("{}", encoding="utf-8")
    fallback.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sync, "SYNC_CONFIG_PATH", local)
    monkeypatch.setattr(sync, "SYNC_FALLBACK_CONFIG_PATH", fallback)

    assert sync.resolve_config_path() == local


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
