"""End-to-end tests for ``llm-wiki init`` (epic LWC-xku0 capstone).

Story: LWC-f4l5.

These tests exercise the init command against a real filesystem via a real
``subprocess`` invocation of ``python -m scripts.cli init PATH``. There are
NO mocks, stubs, or fakes; every assertion checks what actually ended up on
disk or what actually came back on stdout/stderr.

The four ACs exercised here:

1. A clean PATH becomes a complete workspace -- every directory, every
   template file, every fixed placeholder (`.gitignore`, `index.md`,
   `log.md`, `schemas/AGENTS.md`) is present, exit code is 0, and the
   DESIGN §5.2 ``Created:`` + ``Next steps:`` sections appear on stdout.
2. Re-running ``init`` on an already-initialized workspace is a no-op:
   exit 0 and the DESIGN §5.3 ``already initialized`` message (or an
   equivalent ``Skipped (already exist):`` section) prints.
3. ``init --force`` on an existing workspace restores every template
   file to its canonical content and reports an ``Overwrote:`` section.
4. When the target sits inside an outer git repo, init emits the
   DESIGN §6.3 warning block pointing at the outer repo root, and
   still exits 0.

AC relevance (from the story body):

- AC 1: all four tests exist in tests/test_init_e2e.py.
- AC 2: each test invokes ``llm-wiki init`` via subprocess (no in-process mocking).
- AC 3: all tests pass after LWC-zsy4, LWC-7wkk, LWC-wn2r, LWC-bu8d are complete.
- AC 4: no mocks, no stubs, no fakes.

These are true end-to-end tests: we invoke the CLI the same way a shell
user would, and we assert against the real filesystem + the real output
streams.
"""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

import scripts.templates as templates_pkg
import scripts.templates.schemas as templates_schemas_pkg


# ---------------------------------------------------------------------------
# Expected workspace shape (DESIGN §5.2, ARCHITECTURE §8.1).
# ---------------------------------------------------------------------------


EXPECTED_FILES = (
    ".env.example",
    ".env",
    ".gitignore",
    ".wikiignore",
    "sync-sources.local.json",
    "ingest-settings.local.json",
    "schemas/AGENTS.md",
    "index.md",
    "log.md",
)

EXPECTED_DIRS = (
    "raw/inbox",
    "wiki/summaries",
    "wiki/topics",
    "wiki/entities",
    "state",
)

# Byte-for-byte contract from DESIGN §6.2. Leading comment line included.
EXPECTED_GITIGNORE = (
    "# llm-wiki workspace \u2014 local state, not for commit\n"
    ".env\n"
    "raw/\n"
    "state/\n"
    "wiki/\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_init(
    cmd_args: list[str],
    env_overrides: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``python -m scripts.cli init ...`` as a real subprocess.

    We always strip ``LLM_WIKI_WORKSPACE`` from the child env so that the
    developer running the tests locally with an exported workspace does
    not silently change the target of ``init``.
    """
    env = {**os.environ, **(env_overrides or {})}
    env.pop("LLM_WIKI_WORKSPACE", None)
    # Make sure the child process imports THIS worktree's ``scripts``
    # package, not a globally-installed one that might lag behind.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "scripts.cli", "init", *cmd_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
    )


def _template_text(name: str) -> str:
    """Return the packaged template text (used to verify --force restores)."""
    if name == "schemas/AGENTS.md":
        return (files(templates_schemas_pkg) / "AGENTS.md").read_text(encoding="utf-8")
    return (files(templates_pkg) / name).read_text(encoding="utf-8")


def _assert_complete_workspace(ws: Path) -> None:
    """Every directory exists, every file exists."""
    for rel in EXPECTED_DIRS:
        assert (ws / rel).is_dir(), f"missing directory: {rel}"
    for rel in EXPECTED_FILES:
        assert (ws / rel).is_file(), f"missing file: {rel}"


# ---------------------------------------------------------------------------
# AC 1: clean path -> complete workspace + DESIGN §5.2 output
# ---------------------------------------------------------------------------


def test_init_clean_path_creates_every_expected_artifact(tmp_path: Path) -> None:
    """Invoking ``llm-wiki init PATH`` on a clean PATH must:

    - exit 0,
    - create every directory in ``EXPECTED_DIRS``,
    - create every file in ``EXPECTED_FILES``,
    - render a ``Created:`` section and a ``Next steps:`` section,
    - include the resolved absolute path in the summary banner.
    """
    ws = tmp_path / "fresh_ws"
    r = _run_init([str(ws)])

    assert r.returncode == 0, (
        f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )

    _assert_complete_workspace(ws)

    # .gitignore content must match DESIGN §6.2 exactly.
    assert (ws / ".gitignore").read_text(encoding="utf-8") == EXPECTED_GITIGNORE

    # index.md / log.md placeholders match DESIGN contract.
    assert (ws / "index.md").read_text(encoding="utf-8") == "# Wiki\n"
    assert (ws / "log.md").read_text(encoding="utf-8") == ""

    # Output contract (DESIGN §5.2).
    assert r.stdout.startswith(f"Initialized workspace at {ws.resolve()}"), (
        f"first line must be 'Initialized workspace at <resolved path>' "
        f"but stdout started with:\n{r.stdout[:200]}"
    )
    assert "Created:" in r.stdout
    # First-run Created: section must include the directory group
    # (DESIGN §5.2 example).
    assert "raw/inbox/, wiki/{summaries,topics,entities}/, state/" in r.stdout
    # Every template file must be mentioned in Created:.
    assert ".env.example, .env, .gitignore, .wikiignore" in r.stdout
    assert "sync-sources.local.json, ingest-settings.local.json" in r.stdout
    assert "schemas/AGENTS.md" in r.stdout
    assert "index.md, log.md" in r.stdout

    # Next steps: block + three documented steps.
    assert "Next steps:" in r.stdout
    assert "1. Edit .env and set ANTHROPIC_API_KEY" in r.stdout
    assert "2. Edit sync-sources.local.json to point at your sources" in r.stdout
    # Step 3 uses the ORIGINAL path arg verbatim (DESIGN §5.2, AC 3 of LWC-wn2r).
    assert f"3. Run: llm-wiki --workspace {ws} refresh-fast" in r.stdout

    # Nothing on stderr on the happy path.
    assert r.stderr == "", f"unexpected stderr:\n{r.stderr}"


# ---------------------------------------------------------------------------
# AC: re-running on an initialized workspace is idempotent
# ---------------------------------------------------------------------------


def test_init_idempotent_no_op_second_run(tmp_path: Path) -> None:
    """Re-running ``init`` on an already-initialized workspace:

    - exits 0,
    - does NOT modify any existing template file,
    - prints a DESIGN §5.3 ``already initialized`` notice (or at least
      reports a ``Skipped (already exist):`` section), and
    - still prints the ``Next steps:`` block.
    """
    ws = tmp_path / "idem_ws"

    r1 = _run_init([str(ws)])
    assert r1.returncode == 0, r1.stderr

    # Mutate every template file so we can detect unwanted overwrites.
    marker = "SENTINEL-USER-EDIT"
    for rel in EXPECTED_FILES:
        (ws / rel).write_text(marker, encoding="utf-8")

    r2 = _run_init([str(ws)])
    assert r2.returncode == 0, (
        f"second run exit {r2.returncode}\nstdout:\n{r2.stdout}\nstderr:\n{r2.stderr}"
    )

    # No file was overwritten -- every marker is intact.
    for rel in EXPECTED_FILES:
        assert (ws / rel).read_text(encoding="utf-8") == marker, (
            f"{rel} was modified by an idempotent re-run"
        )

    # DESIGN §5.3: the summary either says 'already initialized' (fully
    # idempotent) OR reports a Skipped section with all existing files.
    assert (
        "already initialized" in r2.stdout or "Skipped (already exist):" in r2.stdout
    ), f"expected idempotent message, got:\n{r2.stdout}"

    # Next steps: block always prints, even on a no-op.
    assert "Next steps:" in r2.stdout
    assert f"3. Run: llm-wiki --workspace {ws} refresh-fast" in r2.stdout

    # No stderr.
    assert r2.stderr == ""


# ---------------------------------------------------------------------------
# AC: --force overwrites every template file
# ---------------------------------------------------------------------------


def test_init_force_overwrites_templates(tmp_path: Path) -> None:
    """``llm-wiki init PATH --force`` on an existing workspace:

    - exits 0,
    - restores every template file to its packaged canonical content
      (including ``.env``, per ARCHITECTURE §8.3 final rule),
    - prints an ``Overwrote:`` section in the structured summary, and
    - never touches user-owned content under ``raw/``, ``wiki/``, or ``state/``.
    """
    ws = tmp_path / "force_ws"

    r1 = _run_init([str(ws)])
    assert r1.returncode == 0, r1.stderr

    # Seed user-owned content under the three user-owned subtrees. --force
    # must preserve these bit-for-bit.
    user_files = {
        ws / "raw" / "inbox" / "mydoc.md": "user raw content\n",
        ws / "wiki" / "summaries" / "mypage.md": "user wiki content\n",
        ws / "state" / "mymanifest.json": "{\"k\": 1}\n",
    }
    for p, content in user_files.items():
        p.write_text(content, encoding="utf-8")

    # Mutate every template file so we can detect that --force restored them.
    for rel in EXPECTED_FILES:
        (ws / rel).write_text("MUTATED-BY-USER\n", encoding="utf-8")

    r2 = _run_init([str(ws), "--force"])
    assert r2.returncode == 0, (
        f"--force exit {r2.returncode}\nstdout:\n{r2.stdout}\nstderr:\n{r2.stderr}"
    )

    # Every template file is restored from its packaged source.
    template_map = {
        ".env.example": "env.example",
        ".env": "env.example",
        ".wikiignore": "wikiignore",
        "sync-sources.local.json": "sync-sources.json",
        "ingest-settings.local.json": "ingest-settings.json",
        "schemas/AGENTS.md": "schemas/AGENTS.md",
    }
    for dest_rel, template_name in template_map.items():
        assert (ws / dest_rel).read_text(encoding="utf-8") == _template_text(
            template_name
        ), f"{dest_rel} was not restored from its template under --force"

    # .gitignore is also restored to canonical content.
    assert (ws / ".gitignore").read_text(encoding="utf-8") == EXPECTED_GITIGNORE
    # index.md / log.md placeholders are restored.
    assert (ws / "index.md").read_text(encoding="utf-8") == "# Wiki\n"
    assert (ws / "log.md").read_text(encoding="utf-8") == ""

    # The structured summary reports an Overwrote: section.
    assert "Overwrote:" in r2.stdout, (
        f"--force must report Overwrote:\nstdout:\n{r2.stdout}"
    )
    # Every template file name should appear in the Overwrote: section.
    assert ".env.example, .env, .gitignore, .wikiignore" in r2.stdout
    assert "sync-sources.local.json, ingest-settings.local.json" in r2.stdout
    assert "schemas/AGENTS.md" in r2.stdout
    assert "index.md, log.md" in r2.stdout

    # --force never touches user-owned content.
    for p, expected_content in user_files.items():
        assert p.read_text(encoding="utf-8") == expected_content, (
            f"{p} was modified by --force (user content must be preserved)"
        )

    # Next steps always prints.
    assert "Next steps:" in r2.stdout
    assert r2.stderr == ""


# ---------------------------------------------------------------------------
# AC: outer-git-repo warning (DESIGN §6.3)
# ---------------------------------------------------------------------------


def test_init_inside_outer_git_repo_prints_warning(tmp_path: Path) -> None:
    """When the target is nested inside an existing git repo, init must:

    - exit 0 (warning is advisory, not fatal),
    - print the DESIGN §6.3 ``Warning:`` block naming the outer repo root,
    - still scaffold the workspace successfully.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    # Simulate an outer git repo. A directory-shaped .git is enough; the
    # detector in scripts/init.py accepts any ``.git`` entry (file, dir,
    # symlink).
    (outer / ".git").mkdir()

    sub = outer / "subrepo_ws"
    r = _run_init([str(sub)])

    assert r.returncode == 0, (
        f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )

    # Workspace was still scaffolded despite the warning.
    _assert_complete_workspace(sub)

    # Warning block present and names the outer repo.
    assert "Warning:" in r.stdout
    assert "inside an existing" in r.stdout
    assert "git repository" in r.stdout
    # The warning must reference the resolved outer repo path.
    assert str(outer.resolve()) in r.stdout
    # DESIGN §6.3 canonical body sentence.
    assert ".gitignore" in r.stdout

    # Ordering: Created: < Warning: < Next steps: (DESIGN §5.2 + §6.3).
    created_idx = r.stdout.index("Created:")
    warning_idx = r.stdout.index("Warning:")
    next_steps_idx = r.stdout.index("Next steps:")
    assert created_idx < warning_idx < next_steps_idx, (
        f"section ordering wrong: Created@{created_idx} "
        f"Warning@{warning_idx} Next steps@{next_steps_idx}\n"
        f"stdout:\n{r.stdout}"
    )

    # No stderr (warning goes to stdout, not stderr).
    assert r.stderr == ""
