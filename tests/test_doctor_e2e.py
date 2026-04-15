"""E2e tests for ``llm-wiki doctor`` via the CLI entry point.

Each test invokes ``cli.main(["doctor"])`` and asserts on captured stdout
and the ``SystemExit`` code.  Filesystem fixtures use ``tmp_path`` and
``monkeypatch`` -- no mocks beyond path redirection.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli, doctor
import scripts.sync as sync_mod
import scripts.ingest as ingest_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_env(tmp_path: Path) -> None:
    """Create a .env with a valid API key."""
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n")


def _setup_sync(tmp_path: Path, monkeypatch) -> Path:
    """Create a valid sync config with a reachable source root.

    Returns the path to the sync config file.
    """
    source_dir = tmp_path / "sources"
    source_dir.mkdir(exist_ok=True)
    sync_config = {
        "schema_version": 1,
        "sources": [{"name": "test", "root": str(source_dir)}],
    }
    sync_path = tmp_path / "sync-sources.json"
    sync_path.write_text(json.dumps(sync_config))
    monkeypatch.setattr(sync_mod, "resolve_config_path", lambda *a, **kw: sync_path)
    return sync_path


def _setup_ingest(tmp_path: Path, monkeypatch) -> None:
    """Create valid ingest settings and monkeypatch resolution."""
    ingest_path = tmp_path / "ingest-settings.json"
    ingest_path.write_text(json.dumps({}))
    monkeypatch.setattr(
        ingest_mod, "resolve_ingest_settings_path", lambda *a, **kw: ingest_path
    )


def _setup_demo(tmp_path: Path) -> None:
    """Create demo artifact files."""
    demo_dir = tmp_path / "demo" / "sample-output"
    demo_dir.mkdir(parents=True)
    (demo_dir / "index.md").write_text("# Demo")
    (demo_dir / "last_ingest_run.json").write_text("{}")
    (demo_dir / "last_ingest_report.md").write_text("# Report")


def _setup_valid_project(tmp_path: Path, monkeypatch) -> None:
    """Set up a fully-valid project so that ``doctor`` reports all PASS."""
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    _setup_env(tmp_path)
    _setup_sync(tmp_path, monkeypatch)
    _setup_ingest(tmp_path, monkeypatch)
    _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)


def _run_doctor(capsys):
    """Run ``cli.main(["doctor"])`` and return (exit_code, stdout_text)."""
    try:
        cli.main(["doctor"])
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    else:
        raise AssertionError("Expected SystemExit from cli.main(['doctor'])")
    output = capsys.readouterr().out
    return code, output


# ---------------------------------------------------------------------------
# AC-1: valid setup -> all [PASS] lines, exit code 0
# ---------------------------------------------------------------------------

class TestAllPassValidSetup:
    """AC-1: cli.main(["doctor"]) with valid setup -> all [PASS], exit 0."""

    def test_exit_code_zero(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        code, _ = _run_doctor(capsys)
        assert code == 0

    def test_all_lines_are_pass(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        _, output = _run_doctor(capsys)

        # Every line that starts with [ must be [PASS]
        check_lines = [
            line for line in output.splitlines() if line.strip().startswith("[")
        ]
        assert len(check_lines) >= 1, "Expected at least one check line"
        for line in check_lines:
            assert line.strip().startswith("[PASS]"), (
                f"Expected [PASS] but got: {line}"
            )

    def test_expected_checks_present(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        _, output = _run_doctor(capsys)

        expected = [
            "[PASS] Python version",
            "[PASS] Environment",
            "[PASS] Sync config",
            "[PASS] Source paths",
            "[PASS] Ingest settings",
            "[PASS] Wiki output",
            "[PASS] Demo artifacts",
        ]
        for marker in expected:
            assert marker in output, f"Missing expected marker: {marker}"

    def test_no_fail_lines(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        _, output = _run_doctor(capsys)
        assert "[FAIL]" not in output


# ---------------------------------------------------------------------------
# AC-2: missing .env -> [FAIL] Environment, exit code 1
# ---------------------------------------------------------------------------

class TestMissingEnv:
    """AC-2: missing .env -> [FAIL] Environment in output, exit 1."""

    def test_exit_code_one(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").unlink()
        code, _ = _run_doctor(capsys)
        assert code == 1

    def test_fail_environment_in_output(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").unlink()
        _, output = _run_doctor(capsys)
        assert "[FAIL] Environment" in output

    def test_hint_present(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").unlink()
        _, output = _run_doctor(capsys)
        assert "Hint:" in output


# ---------------------------------------------------------------------------
# AC-3: unreachable source path -> [FAIL] Source paths with path listed
# ---------------------------------------------------------------------------

class TestUnreachableSourcePath:
    """AC-3: unreachable source path -> [FAIL] Source paths with path."""

    def test_fail_source_paths_in_output(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(doctor, "ROOT", tmp_path)
        _setup_env(tmp_path)

        bogus_path = tmp_path / "does_not_exist"
        sync_config = {
            "schema_version": 1,
            "sources": [{"name": "ghost", "root": str(bogus_path)}],
        }
        sync_path = tmp_path / "sync-sources.json"
        sync_path.write_text(json.dumps(sync_config))
        monkeypatch.setattr(
            sync_mod, "resolve_config_path", lambda *a, **kw: sync_path
        )

        _setup_ingest(tmp_path, monkeypatch)
        _setup_demo(tmp_path)
        monkeypatch.chdir(tmp_path)

        code, output = _run_doctor(capsys)

        assert code == 1
        assert "[FAIL] Source paths" in output
        assert str(bogus_path) in output

    def test_hint_present_for_source_paths(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(doctor, "ROOT", tmp_path)
        _setup_env(tmp_path)

        bogus_path = tmp_path / "does_not_exist"
        sync_config = {
            "schema_version": 1,
            "sources": [{"name": "ghost", "root": str(bogus_path)}],
        }
        sync_path = tmp_path / "sync-sources.json"
        sync_path.write_text(json.dumps(sync_config))
        monkeypatch.setattr(
            sync_mod, "resolve_config_path", lambda *a, **kw: sync_path
        )

        _setup_ingest(tmp_path, monkeypatch)
        _setup_demo(tmp_path)
        monkeypatch.chdir(tmp_path)

        _, output = _run_doctor(capsys)
        assert "Hint:" in output


# ---------------------------------------------------------------------------
# AC-4: [PASS]/[FAIL] prefix used consistently for ALL checks
# ---------------------------------------------------------------------------

class TestConsistentPrefixes:
    """AC-4: every check line uses [PASS] or [FAIL] prefix consistently."""

    def test_all_pass_prefix_format(self, tmp_path, monkeypatch, capsys):
        """In a valid setup, every check line starts with [PASS]."""
        _setup_valid_project(tmp_path, monkeypatch)
        _, output = _run_doctor(capsys)

        check_lines = [
            line
            for line in output.splitlines()
            if line.strip().startswith("[")
        ]
        for line in check_lines:
            stripped = line.strip()
            assert stripped.startswith("[PASS]") or stripped.startswith("[FAIL]"), (
                f"Check line has unexpected prefix: {line}"
            )

    def test_mixed_pass_fail_prefix_format(self, tmp_path, monkeypatch, capsys):
        """With a broken env, remaining checks still use [PASS]/[FAIL]."""
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").unlink()

        _, output = _run_doctor(capsys)

        check_lines = [
            line
            for line in output.splitlines()
            if line.strip().startswith("[")
        ]
        assert len(check_lines) >= 2, "Expected multiple check lines"
        has_pass = any(l.strip().startswith("[PASS]") for l in check_lines)
        has_fail = any(l.strip().startswith("[FAIL]") for l in check_lines)
        assert has_pass, "Expected at least one [PASS] line"
        assert has_fail, "Expected at least one [FAIL] line"

        for line in check_lines:
            stripped = line.strip()
            assert stripped.startswith("[PASS]") or stripped.startswith("[FAIL]"), (
                f"Check line has unexpected prefix: {line}"
            )


# ---------------------------------------------------------------------------
# AC-5: Hint: message appears for each failed check
# ---------------------------------------------------------------------------

class TestHintMessages:
    """AC-5: Hint: message appears for each failed check."""

    def test_hint_for_missing_env(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").unlink()
        _, output = _run_doctor(capsys)

        assert "[FAIL] Environment" in output
        assert "Hint:" in output

    def test_hint_for_placeholder_key(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        (tmp_path / ".env").write_text(
            "ANTHROPIC_API_KEY=your_anthropic_api_key_here\n"
        )
        _, output = _run_doctor(capsys)

        assert "[FAIL] Environment" in output
        assert "Hint:" in output

    def test_hint_for_unreachable_source(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(doctor, "ROOT", tmp_path)
        _setup_env(tmp_path)

        sync_config = {
            "schema_version": 1,
            "sources": [
                {"name": "gone", "root": str(tmp_path / "nope")},
            ],
        }
        sync_path = tmp_path / "sync-sources.json"
        sync_path.write_text(json.dumps(sync_config))
        monkeypatch.setattr(
            sync_mod, "resolve_config_path", lambda *a, **kw: sync_path
        )
        _setup_ingest(tmp_path, monkeypatch)
        _setup_demo(tmp_path)
        monkeypatch.chdir(tmp_path)

        _, output = _run_doctor(capsys)
        assert "[FAIL] Source paths" in output
        assert "Hint:" in output

    def test_hint_for_wiki_dir_failure(self, tmp_path, monkeypatch, capsys):
        _setup_valid_project(tmp_path, monkeypatch)
        # Block wiki/ creation by placing a file there
        (tmp_path / "wiki").write_text("blocker")

        _, output = _run_doctor(capsys)
        assert "[FAIL] Wiki output" in output
        assert "Hint:" in output
