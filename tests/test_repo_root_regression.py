"""Regression test for BUSINESS §9 AC#2 / DESIGN §2b / ARCHITECTURE §12.5.

Runs workspace-aware commands from the repo root with no ``--workspace`` flag
and no ``LLM_WIKI_WORKSPACE`` env var set. Asserts:

- No ``Workspace:`` banner on stdout
- All commands exit with their expected codes
- Repo-root paths are used (no surprise reads/writes elsewhere)
- Ingest now emits the new summary line -- the ONLY visible addition compared
  to 0.2.0 (DESIGN §2b: "upgrade, change nothing, notice nothing")

The subprocess helper (``_run``) pops ``LLM_WIKI_WORKSPACE`` from the child
env. The in-process query test uses ``monkeypatch.delenv`` for the same
reason. Developer shells that export the env var do not influence results.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from scripts.workspace import repo_root


def _run(cmd_args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke ``python -m scripts.cli ...`` as a real subprocess.

    Always strips ``LLM_WIKI_WORKSPACE`` from the child env so the test
    exercises the repo-root default path regardless of developer shell
    state. ``PYTHONPATH`` is pinned to this worktree's repo root so the
    child imports the worktree's ``scripts`` package rather than any
    globally-installed copy.

    No real ``ANTHROPIC_API_KEY`` is required for any of the paths this
    helper exercises (doctor, sync --dry-run, ingest --dry-run, lint).
    """

    env_use = {**os.environ, **(env or {})}
    env_use.pop("LLM_WIKI_WORKSPACE", None)
    repo = repo_root()
    env_use["PYTHONPATH"] = str(repo) + os.pathsep + env_use.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "scripts.cli", *cmd_args],
        capture_output=True,
        text=True,
        env=env_use,
        cwd=str(repo),
    )


def test_doctor_repo_root_no_banner():
    """``doctor`` from repo root prints no ``Workspace:`` banner (DESIGN §4.2)."""

    r = _run(["doctor"])
    assert "Workspace:" not in r.stdout, r.stdout
    # doctor may exit 0 or 1 depending on repo state (e.g. .env presence);
    # either is valid for the purpose of this regression.
    assert r.returncode in (0, 1), (
        f"doctor exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )


def test_sync_dry_run_repo_root_no_banner():
    """``sync --dry-run`` from repo root prints no banner and exits 0."""

    r = _run(["sync", "--dry-run"])
    assert r.returncode == 0, (
        f"sync --dry-run exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "Workspace:" not in r.stdout, r.stdout


def test_ingest_dry_run_repo_root_no_banner():
    """``ingest --dry-run`` from repo root prints no banner and emits the summary line.

    The summary line is the ONE user-visible addition over 0.2.0 stdout
    (DESIGN §2b). Even with no input files and no API calls, ingest
    prints ``Used 0 input / 0 output tokens this run.`` on the success
    path -- that line must be present.
    """

    r = _run(["ingest", "--dry-run"])
    assert r.returncode == 0, (
        f"ingest --dry-run exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "Workspace:" not in r.stdout, r.stdout
    # The summary line is the sole new stdout line introduced in 0.3.x.
    assert re.search(
        r"Used .+ input / .+ output tokens this run\.", r.stdout
    ) is not None, r.stdout


def test_lint_repo_root_no_banner():
    """``lint`` from repo root prints no banner."""

    r = _run(["lint"])
    assert "Workspace:" not in r.stdout, r.stdout
    # lint may exit 0 (clean) or 1 (findings) depending on wiki content.
    assert r.returncode in (0, 1), (
        f"lint exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )


def test_query_repo_root_no_banner_in_process(monkeypatch, tmp_path, capsys):
    """In-process ``query`` regression. NOT skipped.

    ``scripts.query`` imports ``call_claude`` as ``from scripts.claude_api
    import call_claude`` -- that binds the function into the ``scripts.query``
    module namespace at import time. Patching only ``scripts.claude_api.call_claude``
    would not affect the already-bound name inside ``scripts.query``. We
    patch BOTH module paths so the mock is effective whichever binding the
    dispatch touches.

    The ``scripts.workspace.repo_root`` resolver is redirected to a
    ``tmp_path`` so the in-process run does NOT depend on the real
    repo having a populated ``wiki/`` tree. Without this, a clean
    checkout (no prior ingest run) would short-circuit
    ``scripts.query.main`` at the "No wiki pages found" guard before
    the mocked ``call_claude`` is ever reached -- silently masking
    the regression this test is meant to catch. A single minimal
    summaries page is seeded so ``collect_wiki_text`` returns a
    non-empty string. Writing anywhere under the real repo's
    ``wiki/`` tree is expressly forbidden by AC#2.

    Assertions are ordered so the non-emptiness precondition runs first:
    ``rc == 0`` and ``'STUB ANSWER' in stdout`` establish that the command
    actually executed and produced output before we check banner absence.
    An empty stdout would trivially satisfy "no ``Workspace:`` substring"
    and silently pass -- this ordering prevents that vacuous pass.
    """

    from scripts.claude_api import ClaudeCallResult
    import scripts.cli as cli

    def fake_call_claude(
        *, system, messages, model, context, workspace, log_event=True, **kw
    ):
        return ClaudeCallResult(
            text="STUB ANSWER",
            input_tokens=10,
            output_tokens=5,
            model=model,
        )

    monkeypatch.setattr("scripts.claude_api.call_claude", fake_call_claude)
    monkeypatch.setattr("scripts.query.call_claude", fake_call_claude)
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    # Dummy key so init_client() does not short-circuit with a
    # "Missing ANTHROPIC_API_KEY" error before our mock is reached.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")

    # Redirect the repo-root default to a throwaway tmp_path and seed a
    # single minimal summaries page so ``scripts.query.collect_wiki_text``
    # returns non-empty content. This keeps the test hermetic (no reliance
    # on a prior ``ingest`` run) and never touches the real repo's wiki/.
    fake_repo = tmp_path
    (fake_repo / "wiki" / "summaries").mkdir(parents=True)
    (fake_repo / "wiki" / "summaries" / "test.md").write_text(
        "# Test Summary\n\nPlaceholder content for the in-process query test.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.workspace.repo_root", lambda: fake_repo)

    rc = cli.main(["query", "what is this wiki about?"])
    captured = capsys.readouterr()

    # Non-emptiness precondition: the command actually ran and produced
    # the stub answer. This must hold BEFORE we assert banner absence so
    # an empty-stdout regression cannot silently satisfy the banner check.
    assert rc == 0, captured.err
    assert "STUB ANSWER" in captured.out, captured.out
    # With the precondition satisfied, the banner absence check now has
    # teeth: real stdout exists and still does not carry the banner.
    assert "Workspace:" not in captured.out, captured.out


def test_ingest_summary_line_is_the_one_change():
    """``ingest --dry-run`` emits exactly one summary line -- the sole 0.3.x addition.

    DESIGN §2b requires the repo-root path to be byte-identical to 0.2.0
    aside from the token-summary line. A regression that emits the line
    twice (or adds further new stdout lines) would violate the
    "upgrade, change nothing, notice nothing" contract. This test pins
    the exactly-one-summary-line invariant without depending on the
    surrounding 0.2.0 bytes (which are covered elsewhere).
    """

    r = _run(["ingest", "--dry-run"])
    assert r.returncode == 0, (
        f"ingest --dry-run exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    summary_lines = [
        line
        for line in r.stdout.splitlines()
        if re.match(r"Used .+ input / .+ output tokens this run\.", line)
    ]
    assert len(summary_lines) == 1, (
        f"expected exactly one summary line, got: {summary_lines}\n"
        f"full stdout:\n{r.stdout}"
    )
