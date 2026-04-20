"""Integration tests for scripts/lint.py structured output and exit codes.

Workspace-aware refactor (LWC-7yge): tests run lint against the
``tmp_workspace`` fixture (tests/conftest.py) instead of monkey-patching
module-level ``ROOT`` / ``WIKI_DIR`` constants -- those constants have been
deleted.
"""

import textwrap
from pathlib import Path

import pytest

from scripts import cli, lint
from scripts.workspace import WorkspacePaths


def _make_passing_page(wiki_dir: Path, name: str = "good-page") -> Path:
    """Create a wiki page that passes all four checks."""
    content = textwrap.dedent(f"""\
        # {name}

        Source: https://example.com/doc

        This is a sufficiently long page that exceeds the 200 character minimum
        threshold required by the page length check. We need to add enough text
        here to make sure the stripped content is well above 200 characters so
        that the check passes reliably every time we run the test suite.

        See also [[{name}]] for more details on this topic.
    """)
    page = wiki_dir / f"{name}.md"
    page.write_text(content, encoding="utf-8")
    return page


def _make_failing_page(wiki_dir: Path, name: str = "bad-page") -> Path:
    """Create a wiki page that fails all four checks (no source, too short, no links)."""
    page = wiki_dir / f"{name}.md"
    page.write_text("Short page.", encoding="utf-8")
    return page


# ---------------------------------------------------------------------------
# AC 2: lint.ROOT and lint.WIKI_DIR are deleted
# ---------------------------------------------------------------------------


def test_lint_module_constants_deleted():
    """lint.ROOT and lint.WIKI_DIR must not exist (AC 2)."""
    assert not hasattr(lint, "ROOT"), "lint.ROOT must be deleted after LWC-7yge"
    assert not hasattr(lint, "WIKI_DIR"), "lint.WIKI_DIR must be deleted after LWC-7yge"


# ---------------------------------------------------------------------------
# AC 1: main signature accepts (argv, workspace)
# ---------------------------------------------------------------------------


def test_lint_main_signature_accepts_argv_and_workspace(tmp_workspace: WorkspacePaths, capsys):
    """lint.main(argv, workspace) -> int must be directly callable."""
    result = lint.main([], tmp_workspace)
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# AC: all checks pass -> [PASS] lines + exit code 0
# ---------------------------------------------------------------------------


class TestAllChecksPassing:
    """All checks pass -> [PASS] lines + exit code 0."""

    def test_all_pass_exit_code(self, tmp_workspace: WorkspacePaths, capsys):
        _make_passing_page(tmp_workspace.wiki_dir, "alpha")
        _make_passing_page(tmp_workspace.wiki_dir, "beta")

        result = lint.main([], tmp_workspace)

        assert result == 0

    def test_all_pass_output_format(self, tmp_workspace: WorkspacePaths, capsys):
        _make_passing_page(tmp_workspace.wiki_dir, "alpha")
        _make_passing_page(tmp_workspace.wiki_dir, "beta")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "[PASS] Source attribution" in output
        assert "[PASS] Page length" in output
        assert "[PASS] Internal links" in output
        assert "[PASS] Orphaned links" in output
        assert "[FAIL]" not in output

    def test_all_pass_summary_line(self, tmp_workspace: WorkspacePaths, capsys):
        _make_passing_page(tmp_workspace.wiki_dir, "alpha")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "0 checks failed, 4 checks passed" in output


# ---------------------------------------------------------------------------
# AC: specific failures -> [FAIL] lines with details + exit code 1
# ---------------------------------------------------------------------------


class TestFailingChecks:
    """Specific failures produce [FAIL] lines with details + exit code 1."""

    def test_failures_exit_code(self, tmp_workspace: WorkspacePaths, capsys):
        _make_failing_page(tmp_workspace.wiki_dir, "bad")

        result = lint.main([], tmp_workspace)

        assert result == 1

    def test_source_attribution_fail(self, tmp_workspace: WorkspacePaths, capsys):
        _make_failing_page(tmp_workspace.wiki_dir, "no-source")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "[FAIL] Source attribution" in output
        assert "wiki/no-source.md" in output

    def test_page_length_fail_with_char_count(self, tmp_workspace: WorkspacePaths, capsys):
        page = tmp_workspace.wiki_dir / "tiny.md"
        page.write_text("Short.", encoding="utf-8")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "[FAIL] Page length" in output
        assert "wiki/tiny.md" in output
        assert "chars)" in output

    def test_internal_links_fail(self, tmp_workspace: WorkspacePaths, capsys):
        page = tmp_workspace.wiki_dir / "no-links.md"
        page.write_text("No links here. " * 30 + "\nSource: x", encoding="utf-8")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "[FAIL] Internal links" in output
        assert "wiki/no-links.md" in output

    def test_orphaned_links_fail(self, tmp_workspace: WorkspacePaths, capsys):
        page = tmp_workspace.wiki_dir / "orphan-page.md"
        page.write_text(
            "Source: x\n" + "x" * 200 + "\n[[nonexistent-target]]",
            encoding="utf-8",
        )

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        assert "[FAIL] Orphaned links" in output
        assert "[[nonexistent-target]]" in output

    def test_fail_detail_indentation(self, tmp_workspace: WorkspacePaths, capsys):
        _make_failing_page(tmp_workspace.wiki_dir, "bad")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        # Detail lines should start with 7 spaces + dash
        lines = output.strip().splitlines()
        detail_lines = [l for l in lines if l.startswith("       - ")]
        assert len(detail_lines) > 0, "Expected indented detail lines under [FAIL]"


# ---------------------------------------------------------------------------
# AC: summary line counts are correct
# ---------------------------------------------------------------------------


class TestSummaryLine:
    """Summary line counts are correct."""

    def test_mixed_results_summary(self, tmp_workspace: WorkspacePaths, capsys):
        # One good page, one bad page -- should produce a mix of pass/fail
        _make_passing_page(tmp_workspace.wiki_dir, "good")
        _make_failing_page(tmp_workspace.wiki_dir, "bad")

        lint.main([], tmp_workspace)
        output = capsys.readouterr().out

        # The summary line should always have exactly 4 total checks
        lines = output.strip().splitlines()
        summary = lines[-1]
        assert "checks failed" in summary
        assert "checks passed" in summary
        # Parse counts
        parts = summary.split(",")
        failed_count = int(parts[0].strip().split()[0])
        passed_count = int(parts[1].strip().split()[0])
        assert failed_count + passed_count == 4

    def test_no_pages_returns_zero(self, tmp_workspace: WorkspacePaths, capsys):
        result = lint.main([], tmp_workspace)

        assert result == 0
        output = capsys.readouterr().out
        assert "No wiki pages found." in output


# ---------------------------------------------------------------------------
# Verify each check function returns the structured result dict
# ---------------------------------------------------------------------------


class TestCheckFunctionContracts:
    """Each check function returns the structured result dict."""

    def test_check_returns_required_keys(self, tmp_workspace: WorkspacePaths):
        _make_passing_page(tmp_workspace.wiki_dir, "test-page")

        pages = lint.scan_wiki(tmp_workspace)
        all_names = {p.stem for p in pages}

        for check_fn in [
            lint.check_source_attribution,
            lint.check_page_length,
            lint.check_internal_links,
            lint.check_orphaned_links,
        ]:
            result = check_fn(tmp_workspace, pages, all_names)
            assert "name" in result
            assert "description" in result
            assert "passed" in result
            assert "details" in result
            assert isinstance(result["name"], str)
            assert isinstance(result["description"], str)
            assert isinstance(result["passed"], bool)
            assert isinstance(result["details"], list)


# ---------------------------------------------------------------------------
# AC 6: cli.py DISPATCH['lint'] = lint.main
# ---------------------------------------------------------------------------


class TestCliIntegration:
    """cli.py dispatches lint with SystemExit for exit code propagation."""

    def test_dispatch_points_to_lint_main(self):
        """DISPATCH['lint'] must be wired to lint.main (AC 6)."""
        assert cli.DISPATCH["lint"] is lint.main

    def test_cli_lint_raises_system_exit_on_failure(self, tmp_workspace: WorkspacePaths, monkeypatch):
        _make_failing_page(tmp_workspace.wiki_dir, "bad")
        monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(tmp_workspace.root))

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 1

    def test_cli_lint_raises_system_exit_zero_on_pass(self, tmp_workspace: WorkspacePaths, monkeypatch):
        _make_passing_page(tmp_workspace.wiki_dir, "good")
        monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(tmp_workspace.root))

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# AC 4: load_wikiignore uses workspace.resolve_wikiignore (fallback-aware)
# ---------------------------------------------------------------------------


def test_lint_wikiignore_fallback(tmp_workspace: WorkspacePaths, tmp_path, capsys, monkeypatch):
    """Workspace missing .wikiignore falls back to repo-root .wikiignore.

    The workspace provided by ``tmp_workspace`` does not seed a
    ``.wikiignore``.  We redirect ``wikiignore_fallback_path`` at a
    tmp-resident file so the test does not depend on the live repo's
    ``.wikiignore`` contents, then assert that the fallback is read, its
    patterns are honored (a matching page is excluded from scanning), and
    that lint still exits 0 on a clean wiki.
    """
    from dataclasses import replace

    fallback = tmp_path / "repo-root-wikiignore"
    fallback.write_text("*.ignored.md\n", encoding="utf-8")

    # Sanity: workspace-local .wikiignore does not exist.
    assert not tmp_workspace.wikiignore_path.exists()

    # Redirect the fallback to our tmp file.
    ws = replace(tmp_workspace, wikiignore_fallback_path=fallback)

    # Create two pages: one ignored by the fallback pattern, one not.
    ignored = ws.wiki_dir / "drafts.ignored.md"
    ignored.write_text("too short", encoding="utf-8")  # would fail if scanned
    _make_passing_page(ws.wiki_dir, "alpha")

    # Verify the patterns came from the fallback.
    patterns = lint.load_wikiignore(ws)
    assert "*.ignored.md" in patterns

    # Verify scan_wiki honored the fallback patterns (drafts.ignored.md skipped).
    pages = lint.scan_wiki(ws)
    page_names = {p.name for p in pages}
    assert "drafts.ignored.md" not in page_names
    assert "alpha.md" in page_names

    # End-to-end: lint exits 0 because the only visible page passes.
    result = lint.main([], ws)
    assert result == 0


# ---------------------------------------------------------------------------
# load_wikiignore returns empty list when neither primary nor fallback exists
# ---------------------------------------------------------------------------


def test_lint_wikiignore_both_missing_returns_empty(tmp_workspace: WorkspacePaths, tmp_path):
    """No .wikiignore in workspace or fallback -> load_wikiignore returns [], lint still runs."""
    from dataclasses import replace

    absent = tmp_path / "absent-wikiignore"
    assert not absent.exists()
    ws = replace(tmp_workspace, wikiignore_fallback_path=absent)
    assert not ws.wikiignore_path.exists()

    patterns = lint.load_wikiignore(ws)
    assert patterns == []


# ---------------------------------------------------------------------------
# scan_wiki honors workspace-local .wikiignore
# ---------------------------------------------------------------------------


def test_scan_wiki_honors_local_wikiignore(tmp_workspace: WorkspacePaths):
    """Workspace-local .wikiignore wins over repo-root fallback."""
    tmp_workspace.wikiignore_path.write_text("*.skip.md\n", encoding="utf-8")

    skipped = tmp_workspace.wiki_dir / "drafts.skip.md"
    skipped.write_text("x", encoding="utf-8")
    _make_passing_page(tmp_workspace.wiki_dir, "alpha")

    pages = lint.scan_wiki(tmp_workspace)
    page_names = {p.name for p in pages}
    assert "drafts.skip.md" not in page_names
    assert "alpha.md" in page_names
