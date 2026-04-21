"""E2e tests for the 15-minute onboarding path.

Validates DESIGN.md Journey 1: a new user can go from git clone to a
generated wiki using the demo corpus.  Only the Anthropic API client is
mocked -- all filesystem setup, CLI dispatch, sync, ingest, doctor, lint,
and query exercise real code paths.

Test file: tests/test_onboarding_e2e.py
Story: LWC-4lh9
"""

import json
import os
import re
import shutil
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli, doctor, ingest, query
import scripts.sync as sync_mod
import scripts.ingest as ingest_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEMO_RAW_DIR = ROOT / "demo" / "raw-inbox"


def _make_mock_client() -> MagicMock:
    """Return a mock Anthropic client that returns plausible ingest JSON."""
    client = MagicMock()

    def _create_response(**kwargs):
        # Determine if this is an ingest call (returns JSON) or query call
        system = kwargs.get("system", "")
        if "wiki compiler" in system.lower():
            # Ingest call -- return valid JSON for ingest_file
            source_file = ""
            messages = kwargs.get("messages", [])
            if messages:
                content = messages[0].get("content", "")
                match = re.search(r"Source file:\s*(\S+)", content)
                if match:
                    source_file = match.group(1)

            title = Path(source_file).stem.replace("-", " ").title() if source_file else "Demo Page"
            response_json = json.dumps({
                "title": title,
                "summary": f"This is a summary of {title}. It covers the key concepts and architecture decisions documented in the source material.",
                "key_facts": [
                    f"{title} is a core component",
                    "It integrates with multiple subsystems",
                    "Source: demo corpus",
                ],
                "topics": ["capability-registry", "workflow-orchestration"],
                "entities": ["demomesh"],
                "open_questions": ["How does scaling work?"],
                "topic_summaries": {
                    "capability-registry": "A registry for tracking agent capabilities and routing decisions.",
                    "workflow-orchestration": "Durable workflow orchestration for coordinating agent execution.",
                },
                "entity_summaries": {
                    "demomesh": "A fictional control plane for orchestrating specialized AI agents.",
                },
            })
            block = MagicMock()
            block.type = "text"
            block.text = response_json
            response = MagicMock()
            response.content = [block]
            # LWC-n3um: scripts.claude_api.call_claude reads usage.input_tokens
            # / usage.output_tokens as ints; the default MagicMock auto-attr
            # would return a MagicMock and break JSON serialization of the
            # claude_api_call event.
            response.usage = MagicMock(input_tokens=0, output_tokens=0)
            return response
        else:
            # Query call
            block = MagicMock()
            block.type = "text"
            block.text = "Based on the wiki content, here is the answer to your question.\n\nSources:\n- wiki/summaries/demo-product.md"
            response = MagicMock()
            response.content = [block]
            response.usage = MagicMock(input_tokens=0, output_tokens=0)
            return response

    client.messages.create.side_effect = _create_response
    return client


def _setup_project_root(tmp_path: Path) -> Path:
    """Set up a project directory that mirrors the real project layout.

    Copies the demo corpus and essential config files into tmp_path so the
    full onboarding path can execute against it.
    """
    project = tmp_path / "llm-wiki-claude"
    project.mkdir()

    # .env with a fake but valid-looking API key
    (project / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-key-for-e2e\n"
        "ANTHROPIC_INGEST_MODEL=claude-haiku-4-5\n"
    )

    # Copy .env.example
    shutil.copy2(ROOT / ".env.example", project / ".env.example")

    # Sync config pointing at demo corpus
    sync_config = {
        "schema_version": 1,
        "sources": [
            {
                "name": "demo",
                "root": str(DEMO_RAW_DIR),
                "include": ["*.md"],
                "exclude": [],
                "naming": {"mode": "preserve_path", "prefix": "demo"},
            }
        ],
    }
    (project / "sync-sources.local.json").write_text(json.dumps(sync_config, indent=2))
    shutil.copy2(ROOT / "sync-sources.json", project / "sync-sources.json")

    # Ingest settings (tracked defaults)
    shutil.copy2(ROOT / "ingest-settings.json", project / "ingest-settings.json")

    # VERSION file
    shutil.copy2(ROOT / "VERSION", project / "VERSION")

    # schemas dir
    schemas_dir = ROOT / "schemas"
    if schemas_dir.is_dir():
        shutil.copytree(schemas_dir, project / "schemas")

    # .wikiignore
    wikiignore = ROOT / ".wikiignore"
    if wikiignore.exists():
        shutil.copy2(wikiignore, project / ".wikiignore")

    # Demo artifacts for doctor checks
    demo_dir = project / "demo" / "sample-output"
    demo_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "demo" / "sample-output" / "index.md", demo_dir / "index.md")
    shutil.copy2(
        ROOT / "demo" / "sample-output" / "last_ingest_run.json",
        demo_dir / "last_ingest_run.json",
    )
    shutil.copy2(
        ROOT / "demo" / "sample-output" / "last_ingest_report.md",
        demo_dir / "last_ingest_report.md",
    )

    # Create necessary directories
    (project / "raw" / "inbox").mkdir(parents=True)
    (project / "wiki").mkdir(parents=True)
    (project / "state").mkdir(parents=True)

    return project


def _monkeypatch_roots(project: Path, monkeypatch) -> None:
    """Redirect all ROOT/path constants in scripts to the tmp project dir.

    doctor no longer has a module-level ROOT (AC-2 of LWC-tkbs); instead it
    consumes a WorkspacePaths.  We route it by setting LLM_WIKI_WORKSPACE so
    ``cli.main(['doctor'])`` resolves the workspace to the project dir.
    """
    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(project))
    # ingest no longer has module-level path constants (LWC-4z0t); it derives
    # paths from the WorkspacePaths passed via cli.DISPATCH.

    # sync is now workspace-aware (LWC-btzz): the LLM_WIKI_WORKSPACE env var
    # set above routes cli.main(['sync']) to the tmp project without needing
    # to patch module-level path constants.

    # lint is workspace-aware (LWC-7yge); the LLM_WIKI_WORKSPACE env var set
    # above routes cli.main(['lint']) to the tmp project without needing to
    # patch module-level path constants.

    # query is workspace-aware (LWC-zaz2); the LLM_WIKI_WORKSPACE env var set
    # above routes cli.main(['query', ...]) to the tmp project without needing
    # to patch module-level path constants.

    monkeypatch.chdir(project)


def _run_doctor(capsys):
    """Run doctor and return (exit_code, output)."""
    try:
        cli.main(["doctor"])
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    else:
        raise AssertionError("Expected SystemExit from cli.main(['doctor'])")
    return code, capsys.readouterr().out


def _run_lint(capsys):
    """Run lint and return (exit_code, output)."""
    try:
        cli.main(["lint"])
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    else:
        raise AssertionError("Expected SystemExit from cli.main(['lint'])")
    return code, capsys.readouterr().out


def _run_sync(monkeypatch):
    """Run sync to populate raw/inbox from demo corpus."""
    # sync.main is now workspace-aware (LWC-btzz): invoke it via the cli so
    # the workspace (set via LLM_WIKI_WORKSPACE in _monkeypatch_roots) is
    # resolved through the canonical path.
    cli.main(["sync"])


def _run_ingest(mock_client, monkeypatch):
    """Run ingest with a mocked API client via the CLI dispatch path.

    After LWC-4z0t, ``ingest.main`` is a workspace-aware entry point
    (``main(argv, workspace) -> int``) wired into ``cli.DISPATCH``.  We route
    through ``cli.main(['ingest'])`` so the WorkspacePaths resolution and the
    DISPATCH contract are exercised end-to-end; ``LLM_WIKI_WORKSPACE`` was set
    in ``_monkeypatch_roots`` so the resolver finds the tmp project dir.
    """
    with patch.object(ingest_mod, "init_client", return_value=(mock_client, "claude-haiku-4-5")):
        cli.main(["ingest"])


# ---------------------------------------------------------------------------
# Fixture: fully-built project with demo corpus
# ---------------------------------------------------------------------------

@pytest.fixture
def built_project(tmp_path, monkeypatch, capsys):
    """Set up project, run sync + ingest with mock API, return project path."""
    project = _setup_project_root(tmp_path)
    _monkeypatch_roots(project, monkeypatch)

    # Step 1: sync demo corpus into raw/inbox
    _run_sync(monkeypatch)

    # Step 2: ingest with mocked API client
    mock_client = _make_mock_client()
    _run_ingest(mock_client, monkeypatch)

    # Clear captured output so tests start clean
    capsys.readouterr()

    return project


# ---------------------------------------------------------------------------
# AC: E2e test: from clean state, run documented setup with demo corpus;
#     verify llm-wiki doctor exits 0
# ---------------------------------------------------------------------------

class TestDoctorExitsZero:
    """After documented setup with demo corpus, doctor should pass all checks."""

    def test_doctor_exits_zero(self, built_project, capsys):
        code, output = _run_doctor(capsys)
        assert code == 0, f"doctor exit code was {code}, output:\n{output}"

    def test_doctor_all_pass(self, built_project, capsys):
        """Under the new FAIL/WARN/OK policy, 'all pass' means 0 failures."""
        code, output = _run_doctor(capsys)
        lines = [line for line in output.splitlines() if line.strip()]
        assert lines[-1].startswith("doctor: 0 failures"), (
            f"Expected summary to report 0 failures; got: {lines[-1]}"
        )
        assert code == 0


# ---------------------------------------------------------------------------
# AC: E2e test: after make refresh-fast with demo corpus,
#     wiki/summaries/ contains at least one .md file
# ---------------------------------------------------------------------------

class TestSummariesExist:
    """After refresh-fast with demo corpus, summaries/ has at least one .md."""

    def test_summaries_dir_has_md_files(self, built_project):
        summaries_dir = built_project / "wiki" / "summaries"
        md_files = list(summaries_dir.glob("*.md"))
        assert len(md_files) >= 1, (
            f"Expected at least one .md in wiki/summaries/, found: {md_files}"
        )


# ---------------------------------------------------------------------------
# AC: E2e test: after refresh, wiki/topics/ contains at least one .md file
# ---------------------------------------------------------------------------

class TestTopicsExist:
    """After refresh-fast with demo corpus, topics/ has at least one .md."""

    def test_topics_dir_has_md_files(self, built_project):
        topics_dir = built_project / "wiki" / "topics"
        md_files = list(topics_dir.glob("*.md"))
        assert len(md_files) >= 1, (
            f"Expected at least one .md in wiki/topics/, found: {md_files}"
        )


# ---------------------------------------------------------------------------
# AC: E2e test: after refresh, index.md exists with at least one
#     wikilink or markdown link
# ---------------------------------------------------------------------------

class TestIndexExists:
    """After refresh-fast, index.md exists and contains links."""

    def test_index_md_exists(self, built_project):
        index = built_project / "index.md"
        assert index.is_file(), "index.md must exist after ingest"

    def test_index_has_links(self, built_project):
        index = built_project / "index.md"
        content = index.read_text(encoding="utf-8")
        # Check for markdown links [text](url) or wikilinks [[target]]
        has_md_link = bool(re.search(r"\[.*?\]\(.*?\)", content))
        has_wikilink = bool(re.search(r"\[\[.*?\]\]", content))
        assert has_md_link or has_wikilink, (
            f"index.md must contain at least one link. Content:\n{content}"
        )


# ---------------------------------------------------------------------------
# AC: E2e test: llm-wiki lint exits 0 on demo output
# ---------------------------------------------------------------------------

class TestLintExitsZero:
    """After refresh-fast with demo corpus, lint should pass."""

    def test_lint_exits_zero(self, built_project, capsys):
        code, output = _run_lint(capsys)
        assert code == 0, f"lint exit code was {code}, output:\n{output}"


# ---------------------------------------------------------------------------
# AC: E2e test: llm-wiki query "test" runs without error (mock API client)
# ---------------------------------------------------------------------------

class TestQueryRuns:
    """After refresh-fast, query "test" runs without error using mock client."""

    def test_query_runs_without_error(self, built_project, capsys):
        mock_client = _make_mock_client()
        with patch.object(query, "init_client", return_value=(mock_client, "claude-test-model")):
            cli.main(["query", "test"])

        out = capsys.readouterr().out
        assert "[Model:" in out
        # The mock should have been called
        assert mock_client.messages.create.call_count >= 1


# ---------------------------------------------------------------------------
# AC: E2e test: install + doctor completes in under 2 minutes
# ---------------------------------------------------------------------------

class TestDoctorPerformance:
    """Doctor completes in well under 2 minutes (120 seconds)."""

    def test_doctor_completes_in_under_120_seconds(self, built_project, capsys):
        start = time.monotonic()
        code, _ = _run_doctor(capsys)
        elapsed = time.monotonic() - start
        assert elapsed < 120, (
            f"doctor took {elapsed:.1f}s, must complete in under 120s"
        )
        assert code == 0


# ---------------------------------------------------------------------------
# AC: README.md setup instructions match actual commands needed
# ---------------------------------------------------------------------------

class TestReadmeInstructionsMatch:
    """README Quick Start documents the actual commands used in the test."""

    def test_readme_has_quick_start(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "## Quick Start" in readme

    def test_readme_documents_six_steps(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        qs_match = re.search(
            r"## Quick Start\b(.*?)(?=\n## [^#])", readme, re.DOTALL
        )
        assert qs_match, "README must contain a '## Quick Start' section"
        qs = qs_match.group(1)
        step_headings = re.findall(r"### \d+\.\s+\S+", qs)
        assert len(step_headings) >= 6, (
            f"Expected at least 6 step headings, found {len(step_headings)}: {step_headings}"
        )

    def test_readme_documents_env_copy(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "cp .env.example .env" in readme

    def test_readme_documents_sync_config_copy(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "cp sync-sources.json sync-sources.local.json" in readme

    def test_readme_documents_doctor(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "llm-wiki doctor" in readme

    def test_readme_documents_refresh_fast(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "make refresh-fast" in readme or "refresh-fast" in readme

    def test_readme_documents_venv_creation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "python3 -m venv" in readme

    def test_readme_documents_pip_install(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "pip install" in readme

    def test_env_example_exists(self):
        assert (ROOT / ".env.example").is_file()

    def test_sync_sources_json_exists(self):
        assert (ROOT / "sync-sources.json").is_file()

    def test_makefile_has_refresh_fast(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        targets = re.findall(r"^(\S+):", makefile, re.MULTILINE)
        assert "refresh-fast" in targets
