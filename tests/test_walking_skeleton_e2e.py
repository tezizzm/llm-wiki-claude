"""Walking-skeleton end-to-end tests (epic LWC-knel capstone; story LWC-ck0b).

These tests prove the primary user-visible outcome for the walking-skeleton
epic: a user can run ``llm-wiki init PATH`` followed by
``llm-wiki --workspace PATH doctor`` (or the env-var form) and get a correct
banner plus a clean FAIL/WARN/OK summary from the fresh, feature-complete
workspace with no FAILs.

The entire flow is exercised through real ``subprocess`` invocations of
``python -m scripts.cli`` -- there are NO mocks, stubs, fakes, or
monkeypatches. Every assertion checks what actually ended up on disk or
what actually came back on stdout/stderr.

Banner and summary byte-for-byte contracts come from DESIGN:
- §4.1/§4.2 -- banner: ``Workspace: <path> (from --workspace|from LLM_WIKI_WORKSPACE)``
  followed by a blank line. Silent when the source is ``default``.
- §7.3 -- summary: ``doctor: N failures, M warnings``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.workspace import repo_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(
    cmd_args: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``python -m scripts.cli ...`` as a real subprocess.

    We always strip ``LLM_WIKI_WORKSPACE`` from the child env so that the
    developer running the tests locally with an exported workspace does
    not silently change the target. Callers pass the env var in explicitly
    when they want it.

    ``PYTHONPATH`` is pinned to this worktree's repo root so the child
    imports this worktree's ``scripts`` package, not a globally-installed
    copy that might lag behind.
    """

    env_use = {**os.environ, **(env or {})}
    env_use.pop("LLM_WIKI_WORKSPACE", None)
    if env and "LLM_WIKI_WORKSPACE" in env:
        # Caller explicitly wants the env var set; restore it after the pop.
        env_use["LLM_WIKI_WORKSPACE"] = env["LLM_WIKI_WORKSPACE"]
    env_use.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy-e2e")
    env_use["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env_use.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "scripts.cli", *cmd_args],
        capture_output=True,
        text=True,
        env=env_use,
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DOCTOR_SUMMARY_RE = re.compile(r"doctor: \d+ failures?, \d+ warnings?")


def _assert_workspace_scaffolded(ws: Path) -> None:
    """Minimal shape assertions covering everything doctor needs to pass."""
    assert (ws / ".env").is_file(), f".env missing in {ws}"
    assert (ws / "sync-sources.local.json").is_file()
    assert (ws / "ingest-settings.local.json").is_file()
    assert (ws / "schemas" / "AGENTS.md").is_file()
    assert (ws / "raw" / "inbox").is_dir()
    assert (ws / "wiki" / "summaries").is_dir()
    assert (ws / "wiki" / "topics").is_dir()
    assert (ws / "wiki" / "entities").is_dir()
    assert (ws / "state").is_dir()


# ---------------------------------------------------------------------------
# AC 1-2: init then --workspace doctor, end-to-end
# ---------------------------------------------------------------------------


def test_init_then_doctor_end_to_end(tmp_path: Path) -> None:
    """A scaffolded workspace produces a clean doctor run through the CLI.

    Steps:
      1. ``llm-wiki init /tmp/e2e_ws`` -- exit 0, workspace shape on disk,
         summary mentions the resolved path.
      2. ``llm-wiki --workspace /tmp/e2e_ws doctor`` -- exit 0, banner
         present (DESIGN §4.1), summary line present (DESIGN §7.3), no
         FAIL lines in stdout.
    """
    ws = tmp_path / "e2e_ws"
    r_init = _run(["init", str(ws)])
    assert r_init.returncode == 0, (
        f"init exit {r_init.returncode}\n"
        f"stdout:\n{r_init.stdout}\nstderr:\n{r_init.stderr}"
    )
    # init output sanity: ``Initialized workspace at`` uses the RESOLVED
    # target path (see scripts/init.py). On macOS this means the path is
    # prefixed with ``/private`` because ``/var`` is a symlink.
    assert str(ws.resolve()) in r_init.stdout, (
        f"resolved workspace path missing from init stdout:\n{r_init.stdout}"
    )

    # Shape-on-disk checks so we know the scaffold actually produced what
    # doctor is about to verify.
    _assert_workspace_scaffolded(ws)

    # Now run doctor against the scaffolded workspace. A clean init should
    # have zero FAILs (the workspace is structurally complete) and may have
    # WARNs such as placeholder sources, empty raw/inbox/, and the default
    # ANTHROPIC_API_KEY placeholder.
    r_doc = _run(["--workspace", str(ws), "doctor"])
    assert r_doc.returncode == 0, (
        f"doctor exit {r_doc.returncode}\n"
        f"stdout:\n{r_doc.stdout}\nstderr:\n{r_doc.stderr}"
    )

    lines = r_doc.stdout.splitlines()
    # DESIGN §4.1/§4.2: banner is first line, label is exactly ``from --workspace``.
    assert lines[0] == f"Workspace: {ws.resolve()} (from --workspace)", (
        f"banner mismatch; first line was: {lines[0]!r}"
    )
    assert lines[1] == "", (
        f"DESIGN §4.2 requires a blank line after the banner; got: {lines[1]!r}"
    )
    # DESIGN §7.3 summary line exists somewhere in stdout.
    assert _DOCTOR_SUMMARY_RE.search(r_doc.stdout) is not None, (
        f"doctor summary line missing from stdout:\n{r_doc.stdout}"
    )
    # No FAIL lines for a just-init'd workspace.
    assert "FAIL" not in r_doc.stdout, (
        f"unexpected FAIL in doctor output:\n{r_doc.stdout}"
    )
    # Summary reports 0 failures (the important invariant for this epic).
    assert "doctor: 0 failures" in r_doc.stdout, (
        f"expected '0 failures' in summary; got:\n{r_doc.stdout}"
    )

    # Doctor operates on the workspace path, not the repo root: the
    # resolution block mentions the workspace copies, not the repo-root
    # fallbacks.
    assert str(ws.resolve()) in r_doc.stdout
    assert "fallback -> " not in r_doc.stdout, (
        f"doctor should have used workspace-local configs, not fallbacks:\n"
        f"{r_doc.stdout}"
    )


# ---------------------------------------------------------------------------
# AC 3: env var variant
# ---------------------------------------------------------------------------


def test_init_then_doctor_with_env_var(tmp_path: Path) -> None:
    """``LLM_WIKI_WORKSPACE`` is honored with the banner source ``env``.

    Per DESIGN §4.1, the banner must still print even when the workspace
    was resolved via env var (not a flag).
    """
    ws = tmp_path / "env_ws"
    r_init = _run(["init", str(ws)])
    assert r_init.returncode == 0, r_init.stderr

    _assert_workspace_scaffolded(ws)

    # Use env var instead of --workspace flag; banner still prints with
    # the ``from LLM_WIKI_WORKSPACE`` label.
    r_doc = _run(["doctor"], env={"LLM_WIKI_WORKSPACE": str(ws)})
    assert r_doc.returncode == 0, (
        f"doctor exit {r_doc.returncode}\n"
        f"stdout:\n{r_doc.stdout}\nstderr:\n{r_doc.stderr}"
    )

    lines = r_doc.stdout.splitlines()
    assert lines[0] == f"Workspace: {ws.resolve()} (from LLM_WIKI_WORKSPACE)", (
        f"banner mismatch; first line was: {lines[0]!r}"
    )
    assert lines[1] == ""
    assert _DOCTOR_SUMMARY_RE.search(r_doc.stdout) is not None
    assert "FAIL" not in r_doc.stdout
    assert "doctor: 0 failures" in r_doc.stdout


# ---------------------------------------------------------------------------
# AC 4: baseline regression -- default (repo-root) source is silent
# ---------------------------------------------------------------------------


def test_repo_root_doctor_still_silent(tmp_path: Path) -> None:
    """Without --workspace or LLM_WIKI_WORKSPACE, no banner appears.

    This is the DESIGN §4.1 baseline: the repo-root default path stays
    byte-identical to 0.2.0 output, i.e. no banner. Exit code may be 0 or
    1 depending on whether the repo itself has a clean state/raw/wiki
    layout -- both are valid.
    """
    r = _run(["doctor"], cwd=repo_root())

    # No banner in either form.
    assert "Workspace:" not in r.stdout, (
        f"repo-root doctor must not print a banner; got:\n{r.stdout}"
    )
    assert "from --workspace" not in r.stdout
    assert "from LLM_WIKI_WORKSPACE" not in r.stdout
    # Summary line still required.
    assert _DOCTOR_SUMMARY_RE.search(r.stdout) is not None, (
        f"doctor summary line missing:\n{r.stdout}"
    )
    # Either exit is acceptable in the repo-root default.
    assert r.returncode in (0, 1), (
        f"unexpected exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
