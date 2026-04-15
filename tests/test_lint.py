"""Integration tests for scripts/lint.py structured output and exit codes."""

import textwrap
from pathlib import Path

from scripts import lint


def _make_passing_page(wiki_dir, name="good-page"):
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


def _make_failing_page(wiki_dir, name="bad-page"):
    """Create a wiki page that fails all four checks (no source, too short, no links)."""
    page = wiki_dir / f"{name}.md"
    page.write_text("Short page.", encoding="utf-8")
    return page


class TestAllChecksPassing:
    """Integration test: all checks pass -> [PASS] lines + exit code 0."""

    def test_all_pass_exit_code(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_passing_page(wiki_dir, "alpha")
        _make_passing_page(wiki_dir, "beta")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        result = lint.main()

        assert result == 0

    def test_all_pass_output_format(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_passing_page(wiki_dir, "alpha")
        _make_passing_page(wiki_dir, "beta")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "[PASS] Source attribution" in output
        assert "[PASS] Page length" in output
        assert "[PASS] Internal links" in output
        assert "[PASS] Orphaned links" in output
        assert "[FAIL]" not in output

    def test_all_pass_summary_line(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_passing_page(wiki_dir, "alpha")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "0 checks failed, 4 checks passed" in output


class TestFailingChecks:
    """Integration test: specific failures -> [FAIL] lines with details + exit code 1."""

    def test_failures_exit_code(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_failing_page(wiki_dir, "bad")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        result = lint.main()

        assert result == 1

    def test_source_attribution_fail(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_failing_page(wiki_dir, "no-source")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "[FAIL] Source attribution" in output
        assert "wiki/no-source.md" in output

    def test_page_length_fail_with_char_count(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page = wiki_dir / "tiny.md"
        page.write_text("Short.", encoding="utf-8")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "[FAIL] Page length" in output
        assert "wiki/tiny.md" in output
        assert "chars)" in output

    def test_internal_links_fail(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page = wiki_dir / "no-links.md"
        page.write_text("No links here. " * 30 + "\nSource: x", encoding="utf-8")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "[FAIL] Internal links" in output
        assert "wiki/no-links.md" in output

    def test_orphaned_links_fail(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page = wiki_dir / "orphan-page.md"
        page.write_text(
            "Source: x\n" + "x" * 200 + "\n[[nonexistent-target]]",
            encoding="utf-8",
        )

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        assert "[FAIL] Orphaned links" in output
        assert "[[nonexistent-target]]" in output

    def test_fail_detail_indentation(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_failing_page(wiki_dir, "bad")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
        output = capsys.readouterr().out

        # Detail lines should start with 7 spaces + dash
        lines = output.strip().splitlines()
        detail_lines = [l for l in lines if l.startswith("       - ")]
        assert len(detail_lines) > 0, "Expected indented detail lines under [FAIL]"


class TestSummaryLine:
    """Integration test: summary line counts are correct."""

    def test_mixed_results_summary(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        # One good page, one bad page -- should produce a mix of pass/fail
        _make_passing_page(wiki_dir, "good")
        _make_failing_page(wiki_dir, "bad")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        lint.main()
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

    def test_no_pages_returns_zero(self, tmp_path, monkeypatch, capsys):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        result = lint.main()

        assert result == 0
        output = capsys.readouterr().out
        assert "No wiki pages found." in output


class TestCheckFunctionContracts:
    """Verify each check function returns the structured result dict."""

    def test_check_returns_required_keys(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_passing_page(wiki_dir, "test-page")

        pages = list(wiki_dir.rglob("*.md"))
        all_names = {p.stem for p in pages}

        for check_fn in [
            lint.check_source_attribution,
            lint.check_page_length,
            lint.check_internal_links,
            lint.check_orphaned_links,
        ]:
            result = check_fn(pages, all_names)
            assert "name" in result
            assert "description" in result
            assert "passed" in result
            assert "details" in result
            assert isinstance(result["name"], str)
            assert isinstance(result["description"], str)
            assert isinstance(result["passed"], bool)
            assert isinstance(result["details"], list)


class TestCliIntegration:
    """Verify cli.py dispatches lint with SystemExit for exit code propagation."""

    def test_cli_lint_raises_system_exit_on_failure(self, tmp_path, monkeypatch):
        from scripts import cli

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_failing_page(wiki_dir, "bad")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        import pytest
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 1

    def test_cli_lint_raises_system_exit_zero_on_pass(self, tmp_path, monkeypatch):
        from scripts import cli

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        _make_passing_page(wiki_dir, "good")

        monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
        monkeypatch.setattr(lint, "ROOT", tmp_path)

        import pytest
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 0
