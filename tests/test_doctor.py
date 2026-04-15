import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCTOR_PATH = ROOT / "scripts" / "doctor.py"
spec = importlib.util.spec_from_file_location("doctor_module", DOCTOR_PATH)
doctor = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(doctor)


def test_doctor_main_reports_ok(tmp_path, monkeypatch, capsys):
    _setup_minimal_project(tmp_path, monkeypatch)

    code = doctor.main()
    output = capsys.readouterr().out

    assert code == 0
    assert "Version:" in output
    assert "[PASS] Sync config" in output
    assert "[PASS] Ingest settings" in output
    assert "[PASS] Demo artifacts" in output
    assert "[PASS] Python version" in output


# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------

def test_python_version_pass(tmp_path, monkeypatch, capsys):
    """Python version >= 3.9 should print PASS with version string."""
    _setup_minimal_project(tmp_path, monkeypatch)
    code = doctor.main()
    output = capsys.readouterr().out
    ver_str = sys.version.split()[0]
    assert f"[PASS] Python version -- {ver_str}" in output


# ---------------------------------------------------------------------------
# .env / API key checks
# ---------------------------------------------------------------------------

def test_env_pass_with_valid_key(tmp_path, monkeypatch, capsys):
    """When .env has a real API key, print PASS."""
    _setup_minimal_project(tmp_path, monkeypatch)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Environment -- .env found with API key" in output


def test_env_warn_missing(tmp_path, monkeypatch, capsys):
    """When .env does not exist, print WARN (capability advisory, not structural failure)."""
    _setup_minimal_project(tmp_path, monkeypatch)
    (tmp_path / ".env").unlink()

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[WARN] Environment -- .env file not found" in output
    assert "required for ingest and query" in output
    assert code == 0


def test_env_warn_placeholder_key(tmp_path, monkeypatch, capsys):
    """When .env has the placeholder key, print WARN (not FAIL)."""
    _setup_minimal_project(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=your_anthropic_api_key_here\n")

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[WARN] Environment -- ANTHROPIC_API_KEY is not set or still placeholder" in output
    assert code == 0


def test_env_warn_empty_key(tmp_path, monkeypatch, capsys):
    """When .env has ANTHROPIC_API_KEY set to empty string, print WARN."""
    _setup_minimal_project(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[WARN] Environment -- ANTHROPIC_API_KEY is not set or still placeholder" in output
    assert code == 0


def test_env_warn_key_missing_from_file(tmp_path, monkeypatch, capsys):
    """When .env exists but has no ANTHROPIC_API_KEY line, print WARN."""
    _setup_minimal_project(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("OTHER_VAR=hello\n")

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[WARN] Environment -- ANTHROPIC_API_KEY is not set or still placeholder" in output
    assert code == 0


# ---------------------------------------------------------------------------
# Source paths check
# ---------------------------------------------------------------------------

def test_source_paths_pass(tmp_path, monkeypatch, capsys):
    """When all source roots exist, print PASS."""
    source_dir = tmp_path / "my_source"
    source_dir.mkdir()

    sync_config = {
        "schema_version": 1,
        "sources": [{"name": "test", "root": str(source_dir)}],
    }
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text(json.dumps(sync_config))

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)
    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    _setup_sync(sync_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Source paths -- all source roots reachable" in output


def test_source_paths_fail(tmp_path, monkeypatch, capsys):
    """When source roots don't exist, print FAIL with details."""
    sync_config = {
        "schema_version": 1,
        "sources": [
            {"name": "missing1", "root": str(tmp_path / "nonexistent1")},
            {"name": "missing2", "root": str(tmp_path / "nonexistent2")},
        ],
    }
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text(json.dumps(sync_config))

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)
    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    _setup_sync(sync_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[FAIL] Source paths -- 2 source roots not found" in output
    assert f"- {tmp_path / 'nonexistent1'} (source: missing1)" in output
    assert f"- {tmp_path / 'nonexistent2'} (source: missing2)" in output
    assert "Hint: Check the \"root\" values in" in output
    assert code == 1


def test_source_paths_warn_for_generic_tracked_template(tmp_path, monkeypatch, capsys):
    """The publishable tracked template should warn, not fail, when it still has the generic placeholder root."""
    sync_config = {
        "schema_version": 1,
        "sources": [
            {"name": "my-project", "root": "/absolute/path/to/your/project"},
        ],
    }
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text(json.dumps(sync_config))

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)
    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    _setup_sync(sync_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[WARN] Source paths -- using the generic tracked template placeholder" in output
    assert "sync-sources.local.json" in output
    assert code == 0


def test_source_paths_skipped_when_sync_invalid(tmp_path, monkeypatch, capsys):
    """When sync config is invalid, source path check is skipped (not failed)."""
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text("NOT VALID JSON")

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)
    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    _setup_sync(sync_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "Source paths" not in output
    assert "[FAIL] Sync config" in output


# ---------------------------------------------------------------------------
# Wiki output directory check
# ---------------------------------------------------------------------------

def test_wiki_dir_pass_exists(tmp_path, monkeypatch, capsys):
    """When wiki/ already exists, print PASS."""
    (tmp_path / "wiki").mkdir()
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_minimal_project(tmp_path, monkeypatch)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Wiki output -- wiki/ directory exists" in output


def test_wiki_dir_pass_will_be_created(tmp_path, monkeypatch, capsys):
    """When wiki/ doesn't exist but root is writable, print PASS and do NOT create the dir."""
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_minimal_project(tmp_path, monkeypatch)

    assert not (tmp_path / "wiki").exists()
    code = doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Wiki output -- wiki/ directory will be created on first run" in output
    assert not (tmp_path / "wiki").exists()  # doctor must not create the directory


def test_wiki_dir_fail(tmp_path, monkeypatch, capsys):
    """When wiki/ cannot be created, print FAIL with hint."""
    # Create a file named 'wiki' to block directory creation
    (tmp_path / "wiki").write_text("blocker")
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_minimal_project(tmp_path, monkeypatch)

    code = doctor.main()
    output = capsys.readouterr().out
    assert "[FAIL] Wiki output -- cannot create wiki/ directory" in output
    assert "Hint: Check file permissions in the project root." in output
    assert code == 1


# ---------------------------------------------------------------------------
# Reformatted existing checks
# ---------------------------------------------------------------------------

def test_sync_config_reformatted_pass(tmp_path, monkeypatch, capsys):
    """Sync config output uses [PASS] prefix."""
    _setup_minimal_project(tmp_path, monkeypatch)
    doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Sync config --" in output


def test_ingest_settings_reformatted_pass(tmp_path, monkeypatch, capsys):
    """Ingest settings output uses [PASS] prefix."""
    _setup_minimal_project(tmp_path, monkeypatch)
    doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Ingest settings --" in output


def test_demo_artifacts_reformatted_pass(tmp_path, monkeypatch, capsys):
    """Demo artifacts output uses [PASS] prefix."""
    _setup_minimal_project(tmp_path, monkeypatch)
    doctor.main()
    output = capsys.readouterr().out
    assert "[PASS] Demo artifacts" in output


def test_exit_code_zero_all_pass(tmp_path, monkeypatch, capsys):
    """Exit code 0 when all checks pass."""
    _setup_minimal_project(tmp_path, monkeypatch)
    code = doctor.main()
    assert code == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_env(tmp_path: Path) -> None:
    """Create a .env with a valid API key."""
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n")


def _setup_sync(sync_path: Path, monkeypatch) -> None:
    """Monkeypatch sync.resolve_config_path to return the given path."""
    import scripts.sync as sync_mod
    monkeypatch.setattr(sync_mod, "resolve_config_path", lambda *a, **kw: sync_path)


def _setup_ingest(tmp_path: Path, monkeypatch) -> None:
    """Create valid ingest settings and monkeypatch its path resolution."""
    import scripts.ingest as ingest_mod
    ingest_path = tmp_path / "ingest-settings.json"
    ingest_path.write_text(json.dumps({}))
    monkeypatch.setattr(ingest_mod, "resolve_ingest_settings_path", lambda *a, **kw: ingest_path)


def _setup_demo(tmp_path: Path) -> None:
    """Create demo artifact files (only the tracked ones)."""
    demo_dir = tmp_path / "demo" / "sample-output"
    demo_dir.mkdir(parents=True)
    (demo_dir / "last_ingest_run.json").write_text("{}")
    (demo_dir / "last_ingest_report.md").write_text("# Report")


def _setup_minimal_project(tmp_path: Path, monkeypatch) -> None:
    """Set up a minimal project in tmp_path for isolated doctor tests.

    Creates: .env with valid key, valid sync config with a real source dir,
    ingest settings, and demo artifacts. Monkeypatches path resolution and
    changes cwd to tmp_path so relative demo paths resolve correctly.
    """
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)

    # Sync config with a source root that exists
    source_dir = tmp_path / "sources"
    source_dir.mkdir(exist_ok=True)
    sync_config = {
        "schema_version": 1,
        "sources": [{"name": "test", "root": str(source_dir)}],
    }
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text(json.dumps(sync_config))
    _setup_sync(sync_path, monkeypatch)

    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
