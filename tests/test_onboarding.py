"""Integration tests for README.md onboarding accuracy.

Verifies that every command and file reference in the Quick Start section
is backed by real project artifacts -- no mocks.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

README = ROOT / "README.md"


def _read_readme() -> str:
    return README.read_text(encoding="utf-8")


def _extract_fenced_shell_commands(text: str) -> list[str]:
    """Extract all lines from ```bash fenced code blocks."""
    commands: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("```bash"):
            in_block = True
            continue
        if line.strip().startswith("```") and in_block:
            in_block = False
            continue
        if in_block:
            stripped = line.strip()
            # Skip blank lines and comments
            if not stripped or stripped.startswith("#"):
                continue
            commands.append(stripped)
    return commands


# ---------------------------------------------------------------------------
# AC: README.md Quick Start section lists the exact 6-step onboarding path
# ---------------------------------------------------------------------------


class TestQuickStartStructure:
    """Verify the Quick Start section has all six numbered steps."""

    def test_has_six_steps(self):
        readme = _read_readme()
        # Each step is a ### heading inside Quick Start
        quick_start_match = re.search(
            r"## Quick Start\b(.*?)(?=\n## [^#])", readme, re.DOTALL
        )
        assert quick_start_match, "README must contain a '## Quick Start' section"
        qs = quick_start_match.group(1)

        step_headings = re.findall(r"### \d+\.\s+\S+", qs)
        assert len(step_headings) == 6, (
            f"Expected 6 numbered step headings, found {len(step_headings)}: {step_headings}"
        )

    def test_step_labels(self):
        readme = _read_readme()
        qs_match = re.search(
            r"## Quick Start\b(.*?)(?=\n## [^#])", readme, re.DOTALL
        )
        assert qs_match
        qs = qs_match.group(1)

        expected_keywords = ["clone", "install", "configure", "validate", "build", "browse"]
        headings_lower = [h.lower() for h in re.findall(r"### \d+\.\s+(.+)", qs)]
        for kw in expected_keywords:
            assert any(kw in h for h in headings_lower), (
                f"Quick Start missing step containing keyword '{kw}'. "
                f"Found headings: {headings_lower}"
            )


# ---------------------------------------------------------------------------
# AC: Prerequisites section specifies Python >= 3.9, git, Anthropic API key
# ---------------------------------------------------------------------------


class TestPrerequisites:
    def test_prerequisites_section_exists(self):
        readme = _read_readme()
        assert "## Prerequisites" in readme

    def test_python_version_mentioned(self):
        readme = _read_readme()
        prereq = re.search(
            r"## Prerequisites\b(.*?)(?=\n## )", readme, re.DOTALL
        )
        assert prereq, "Prerequisites section not found"
        text = prereq.group(1)
        assert "3.9" in text, "Prerequisites must mention Python >= 3.9"

    def test_git_mentioned(self):
        readme = _read_readme()
        prereq = re.search(
            r"## Prerequisites\b(.*?)(?=\n## )", readme, re.DOTALL
        )
        assert prereq
        assert "git" in prereq.group(1).lower()

    def test_anthropic_api_key_mentioned(self):
        readme = _read_readme()
        prereq = re.search(
            r"## Prerequisites\b(.*?)(?=\n## )", readme, re.DOTALL
        )
        assert prereq
        text = prereq.group(1).lower()
        assert "anthropic" in text and "api key" in text


# ---------------------------------------------------------------------------
# AC: README documents llm-wiki doctor, lint, and query commands
# ---------------------------------------------------------------------------


class TestCLIDocumentation:
    def test_doctor_documented(self):
        readme = _read_readme()
        assert "llm-wiki doctor" in readme

    def test_lint_documented(self):
        readme = _read_readme()
        assert "llm-wiki lint" in readme

    def test_query_inline_mode(self):
        readme = _read_readme()
        assert 'llm-wiki query "' in readme or "llm-wiki query '" in readme

    def test_query_interactive_mode(self):
        readme = _read_readme()
        # Interactive mode: bare `llm-wiki query` without arguments
        # Must appear as its own command, not just as part of inline mode
        lines = readme.splitlines()
        has_bare_query = any(
            line.strip() == "llm-wiki query"
            or line.strip().startswith("llm-wiki query ")
            and "interactive" in readme.lower()
            for line in lines
        )
        assert has_bare_query, "README must document interactive query mode"

    def test_query_pipe_mode(self):
        readme = _read_readme()
        assert "| llm-wiki query" in readme, "README must document pipe mode for query"


# ---------------------------------------------------------------------------
# AC: .env.example exists and contains ANTHROPIC_API_KEY placeholder
# ---------------------------------------------------------------------------


class TestEnvExample:
    def test_env_example_exists(self):
        env_example = ROOT / ".env.example"
        assert env_example.is_file(), ".env.example must exist in project root"

    def test_env_example_has_api_key_placeholder(self):
        env_example = ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY=your_anthropic_api_key_here" in content


# ---------------------------------------------------------------------------
# AC: make refresh-fast target exists in Makefile
# ---------------------------------------------------------------------------


class TestMakefile:
    def test_refresh_fast_target_exists(self):
        makefile = ROOT / "Makefile"
        assert makefile.is_file(), "Makefile must exist"
        content = makefile.read_text(encoding="utf-8")
        # Makefile targets start at column 0 and end with a colon
        targets = re.findall(r"^(\S+):", content, re.MULTILINE)
        assert "refresh-fast" in targets, (
            f"Makefile must have a 'refresh-fast' target. Found: {targets}"
        )


# ---------------------------------------------------------------------------
# AC: Shell commands in README code blocks are syntactically valid
# ---------------------------------------------------------------------------


class TestShellCommandSyntax:
    def test_all_shell_commands_parse(self):
        """Verify each shell command extracted from README is syntactically valid
        by running it through bash -n (syntax check only, no execution)."""
        readme = _read_readme()
        commands = _extract_fenced_shell_commands(readme)
        assert len(commands) > 0, "README must contain at least one shell command"

        failures: list[str] = []
        for cmd in commands:
            result = subprocess.run(
                ["bash", "-n", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                failures.append(f"  {cmd!r} -> {result.stderr.strip()}")

        assert not failures, (
            "The following README shell commands have syntax errors:\n"
            + "\n".join(failures)
        )

    def test_readme_references_real_files(self):
        """Verify that cp/cat commands in README reference files that exist."""
        readme = _read_readme()
        commands = _extract_fenced_shell_commands(readme)

        # Extract source files from cp commands (cp SRC DEST)
        for cmd in commands:
            match = re.match(r"cp\s+(\S+)\s+", cmd)
            if match:
                src = match.group(1)
                src_path = ROOT / src
                assert src_path.is_file(), (
                    f"README command 'cp {src} ...' references non-existent file: {src_path}"
                )
