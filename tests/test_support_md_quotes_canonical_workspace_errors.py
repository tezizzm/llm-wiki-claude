"""Drift guard for SUPPORT.md multi-workspace error strings.

SUPPORT.md is the triage entry point for users who hit workspace errors. Its
quoted error text MUST match what ``scripts/cli.py`` actually emits, which in
turn matches DESIGN.md §10.1 and §10.2 byte-for-byte. A previous delivery
shipped paraphrased ``Error: ...`` strings that did not match the real output
(prefix ``Workspace error:``); the paraphrased strings are impossible for a
user to find by searching SUPPORT.md for the message in their terminal. This
test prevents that class of drift from recurring.

The assertions here intentionally target *substrings* of the canonical lines
rather than the full lines. That keeps the test robust to soft-wrap and
markdown rendering differences while still catching the exact drift pattern
that caused the prior rejection (prefix replacement, paraphrase, or the
invention of a distinct ``LLM_WIKI_WORKSPACE`` error message that the code
does not emit).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_MD = REPO_ROOT / "SUPPORT.md"


def _support_text() -> str:
    return SUPPORT_MD.read_text(encoding="utf-8")


def test_support_md_quotes_canonical_workspace_errors() -> None:
    """SUPPORT.md must quote DESIGN §10.1 and §10.2 error text byte-for-byte.

    Canonical sources:
      - DESIGN.md §10.1 / §10.2
      - scripts/cli.py:339 (``Workspace error: {exc.path} does not exist. ``
        ``Run `llm-wiki init {exc.path}` first.``)

    Both the ``--workspace`` flag and ``LLM_WIKI_WORKSPACE`` env var funnel
    through ``resolve_workspace()`` and raise the same
    ``WorkspaceNotFoundError``, so SUPPORT.md MUST NOT invent a separate
    env-var-specific error message.
    """
    support = _support_text()

    # Canonical DESIGN §10.1 substring (also scripts/cli.py:339).
    assert "Workspace error:" in support, (
        "SUPPORT.md must use the canonical `Workspace error:` prefix from "
        "DESIGN §10 / scripts/cli.py, not a paraphrase like `Error:`."
    )
    assert "does not exist. Run `llm-wiki init" in support, (
        "SUPPORT.md must quote DESIGN §10.1 / scripts/cli.py:339 verbatim: "
        "`Workspace error: <path> does not exist. Run `llm-wiki init <path>` "
        "first.`"
    )

    # Canonical DESIGN §10.2 substring.
    assert "is missing required files" in support, (
        "SUPPORT.md must quote DESIGN §10.2 verbatim: `Workspace error: "
        "<path> is missing required files. Run `llm-wiki --workspace <path> "
        "doctor` to see what is missing.`"
    )
    assert (
        "Run `llm-wiki --workspace /path/to/workspace doctor` to see what is missing."
        in support
    ), (
        "SUPPORT.md must quote DESIGN §10.2's full next-step suggestion "
        "byte-for-byte, including the backticks and `--workspace` flag."
    )

    # Paraphrased / invented strings that the prior (rejected) delivery used
    # must NOT reappear. These assertions encode the specific drift patterns
    # that caused the rejection.
    assert "Error: --workspace path does not exist" not in support, (
        "Paraphrase detected: SUPPORT.md previously used `Error: --workspace "
        "path does not exist` which does not match `scripts/cli.py` output."
    )
    assert "Error: LLM_WIKI_WORKSPACE" not in support, (
        "Invented error detected: `scripts/cli.py` does NOT emit a distinct "
        "`LLM_WIKI_WORKSPACE` error — both flag and env var share the §10.1 "
        "text via `resolve_workspace()` / `WorkspaceNotFoundError`."
    )
    assert "does not look like an llm-wiki workspace" not in support, (
        "Paraphrase detected: SUPPORT.md previously said `does not look like "
        "an llm-wiki workspace (missing sync-sources.local.json)` which "
        "contradicts DESIGN §10.2 (missing local config is a silent fallback)."
    )


def test_support_md_has_multi_workspace_section() -> None:
    """AC #1: SUPPORT.md must have a `Multi-workspace issues` section."""
    support = _support_text()
    assert "## Multi-workspace issues" in support, (
        "SUPPORT.md is missing the `## Multi-workspace issues` section "
        "required by the multi-workspace feature's triage entry point."
    )


def test_support_md_error_strings_match_cli_source() -> None:
    """SUPPORT.md's quoted §10.1 error must match what scripts/cli.py emits.

    This is the strongest form of the drift guard: we read the actual f-string
    template from ``scripts/cli.py`` and assert its fixed (non-interpolated)
    substrings appear in SUPPORT.md. If someone edits cli.py to change the
    message, this test fails until SUPPORT.md catches up.
    """
    cli_text = (REPO_ROOT / "scripts" / "cli.py").read_text(encoding="utf-8")

    # Fixed substrings from the f-string in scripts/cli.py:339. These are the
    # parts that do NOT depend on ``exc.path`` interpolation.
    assert "Workspace error: " in cli_text, (
        "scripts/cli.py no longer contains `Workspace error:` prefix — update "
        "SUPPORT.md and this test together."
    )
    assert "does not exist. " in cli_text
    assert "Run `llm-wiki init " in cli_text
    assert "` first." in cli_text

    support = _support_text()
    assert "Workspace error:" in support
    assert "does not exist. Run `llm-wiki init" in support
    assert "` first." in support
