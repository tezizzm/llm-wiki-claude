"""E2e tests for lint PASS/FAIL output via the CLI entry point.

Exercises cli.main(["lint"]) end-to-end: output format, exit codes,
summary line, and file immutability.
"""

import hashlib
import textwrap
from pathlib import Path

import pytest

from scripts import cli, lint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_passing_page(wiki_dir: Path, name: str = "good-page") -> Path:
    """Create a wiki page that passes all four lint checks."""
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


def _snapshot_dir(directory: Path) -> dict[str, str]:
    """Return {relative_path: sha256} for every file in directory."""
    result = {}
    for f in sorted(directory.rglob("*")):
        if f.is_file():
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            result[str(f.relative_to(directory))] = digest
    return result


def _setup_wiki(tmp_path, monkeypatch, pages_fn):
    """Create wiki dir, apply pages_fn, and monkeypatch lint constants."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    pages_fn(wiki_dir)
    monkeypatch.setattr(lint, "WIKI_DIR", wiki_dir)
    monkeypatch.setattr(lint, "ROOT", tmp_path)
    return wiki_dir


# ---------------------------------------------------------------------------
# AC 1: Clean wiki -> all [PASS], summary 0 failed, exit code 0
# ---------------------------------------------------------------------------

class TestCleanWikiAllPass:
    """cli.main(["lint"]) with a clean wiki produces all PASS lines and exit 0."""

    def test_exit_code_zero(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
            _make_passing_page(d, "beta"),
        ))

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 0

    def test_all_pass_lines_present(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
            _make_passing_page(d, "beta"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        assert "[PASS] Source attribution" in output
        assert "[PASS] Page length" in output
        assert "[PASS] Internal links" in output
        assert "[PASS] Orphaned links" in output

    def test_no_fail_lines(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
            _make_passing_page(d, "beta"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        assert "[FAIL]" not in output

    def test_summary_zero_failed(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        assert "0 checks failed, 4 checks passed" in output


# ---------------------------------------------------------------------------
# AC 2: Wiki with issues -> [FAIL] lines with details, SystemExit code 1
# ---------------------------------------------------------------------------

class TestFailingWikiExitCode:
    """cli.main(["lint"]) with issues produces FAIL lines and exit 1."""

    def test_exit_code_one(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["lint"])

        assert exc_info.value.code == 1

    def test_fail_lines_present(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        assert "[FAIL]" in output

    def test_fail_details_include_file_paths(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_failing_page(d, "broken"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        assert "wiki/broken.md" in output

    def test_mixed_pass_fail_summary(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "good"),
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        lines = output.strip().splitlines()
        summary = lines[-1]
        parts = summary.split(",")
        failed_count = int(parts[0].strip().split()[0])
        passed_count = int(parts[1].strip().split()[0])
        assert failed_count + passed_count == 4
        assert failed_count > 0
        assert passed_count > 0


# ---------------------------------------------------------------------------
# AC 3: Output format matches DESIGN.md spec
# ---------------------------------------------------------------------------

class TestOutputFormat:
    """Output uses [PASS]/[FAIL] prefixes, indented details, and summary."""

    def test_pass_line_format(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        lines = output.strip().splitlines()
        pass_lines = [l for l in lines if l.startswith("[PASS]")]
        assert len(pass_lines) == 4
        for line in pass_lines:
            # Format: [PASS] Name -- description
            assert " -- " in line

    def test_fail_line_format(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        lines = output.strip().splitlines()
        fail_lines = [l for l in lines if l.startswith("[FAIL]")]
        assert len(fail_lines) > 0
        for line in fail_lines:
            # Format: [FAIL] Name -- summary
            assert " -- " in line

    def test_detail_lines_indentation(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        lines = output.strip().splitlines()
        detail_lines = [l for l in lines if l.startswith("       - ")]
        assert len(detail_lines) > 0, "Expected indented detail lines (7 spaces + dash)"

    def test_summary_line_format(self, tmp_path, monkeypatch, capsys):
        _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
            _make_failing_page(d, "bad"),
        ))

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        output = capsys.readouterr().out
        lines = output.strip().splitlines()
        summary = lines[-1]
        # Format: "N checks failed, M checks passed"
        assert "checks failed" in summary
        assert "checks passed" in summary
        # Verify it matches the exact pattern
        import re
        assert re.match(r"^\d+ checks failed, \d+ checks passed$", summary)


# ---------------------------------------------------------------------------
# AC 4: Lint does not modify any files
# ---------------------------------------------------------------------------

class TestLintFileImmutability:
    """Lint must not create, modify, or delete any files."""

    def test_clean_wiki_no_file_changes(self, tmp_path, monkeypatch, capsys):
        wiki_dir = _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "alpha"),
            _make_passing_page(d, "beta"),
        ))

        before = _snapshot_dir(tmp_path)

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        after = _snapshot_dir(tmp_path)
        assert before == after, "Lint modified files in a clean wiki"

    def test_failing_wiki_no_file_changes(self, tmp_path, monkeypatch, capsys):
        wiki_dir = _setup_wiki(tmp_path, monkeypatch, lambda d: (
            _make_passing_page(d, "good"),
            _make_failing_page(d, "bad"),
        ))

        before = _snapshot_dir(tmp_path)

        with pytest.raises(SystemExit):
            cli.main(["lint"])

        after = _snapshot_dir(tmp_path)
        assert before == after, "Lint modified files in a failing wiki"
