"""E2e tests for ``llm-wiki doctor`` dispatched via scripts.cli.

These tests drive the CLI all the way through the argv preprocessor, banner
emission, DISPATCH table, and workspace-aware doctor entry point.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli


def _run_cli(argv: list[str], capsys) -> tuple[int, str]:
    try:
        cli.main(argv)
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    else:
        code = 0
    output = capsys.readouterr().out
    return code, output


# ---------------------------------------------------------------------------
# AC-9: banner + summary line when --workspace is given
# ---------------------------------------------------------------------------


def test_cli_doctor_with_workspace_flag_banner_and_summary(tmp_workspace, capsys):
    """`llm-wiki --workspace X doctor` prints banner -> blank -> resolution -> summary."""
    code, out = _run_cli(["--workspace", str(tmp_workspace.root), "doctor"], capsys)

    assert code == 0
    lines = out.splitlines()
    assert lines[0] == f"Workspace: {tmp_workspace.root} (from --workspace)"
    assert lines[1] == ""
    # Resolution block starts right after the blank.
    assert lines[2].startswith("sync-sources.local.json:")
    assert lines[-1] == "doctor: 0 failures, 0 warnings"


def test_cli_doctor_with_workspace_flag_fails_when_missing_state(tmp_workspace, capsys):
    import shutil

    shutil.rmtree(tmp_workspace.state_dir)
    code, out = _run_cli(["--workspace", str(tmp_workspace.root), "doctor"], capsys)

    assert code == 1
    assert out.splitlines()[-1].startswith("doctor: ")
    assert "1 failures" in out.splitlines()[-1]


# ---------------------------------------------------------------------------
# AC-10: repo-root default -- no banner, same repo-root paths resolved
# ---------------------------------------------------------------------------


def test_cli_doctor_repo_root_default_no_banner(capsys):
    """Running ``llm-wiki doctor`` from the repo root with no flag emits no banner."""
    code, out = _run_cli(["doctor"], capsys)

    # Neither the --workspace nor env label should appear.
    assert "from --workspace" not in out
    assert "from LLM_WIKI_WORKSPACE" not in out
    # Summary line is still required.
    assert out.rstrip().splitlines()[-1].startswith("doctor: ")
    # Code is 0 or 1 depending on whether the repo has state/raw/wiki on disk.
    assert code in (0, 1)


# ---------------------------------------------------------------------------
# Subprocess smoke test -- the real console_scripts entry point
# ---------------------------------------------------------------------------


def test_subprocess_doctor_with_workspace_flag(tmp_path):
    """End-to-end: running the CLI module as a subprocess produces the banner + summary.

    Uses ``-m scripts.cli`` to avoid depending on a local ``pip install -e .``
    in CI sandboxes.
    """
    # Seed a clean workspace.
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "state").mkdir()
    (ws_root / "raw" / "inbox").mkdir(parents=True)
    (ws_root / "wiki" / "summaries").mkdir(parents=True)
    (ws_root / "wiki" / "topics").mkdir(parents=True)
    (ws_root / "wiki" / "entities").mkdir(parents=True)
    (ws_root / "raw" / "inbox" / "placeholder.md").write_text("# hi\n")
    (ws_root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n")
    src = ws_root / "sources"
    src.mkdir()
    import json as _json
    (ws_root / "sync-sources.local.json").write_text(
        _json.dumps({"schema_version": 1, "sources": [{"name": "t", "root": str(src)}]})
    )
    (ws_root / "ingest-settings.local.json").write_text("{}")

    result = subprocess.run(
        [sys.executable, "-m", "scripts.cli", "--workspace", str(ws_root), "doctor"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(
        f"Workspace: {ws_root} (from --workspace)\n\n"
    )
    assert result.stdout.rstrip().endswith("doctor: 0 failures, 0 warnings")
