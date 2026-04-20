"""End-to-end capstone for the query + lint refactor epic (LWC-rv0u / LWC-4rpn).

Both tests drive the full ``llm-wiki --workspace X <cmd>`` pipeline from
``scripts.cli.main`` through workspace resolution, banner emission, env
loading, and subcommand dispatch.  The workspace is scaffolded via
``scripts.init.main`` (the same entry point the user invokes) and then
pre-seeded with a couple of wiki pages -- running the real ingest pipeline
would force a network call and is not the contract this capstone verifies.

Only the query path needs ``scripts.claude_api.call_claude`` mocked; lint
is pure-Python I/O over the wiki tree and never talks to Anthropic.  The
mock is applied via the **dual-patch** pattern required by the vault-level
guidance and by ``scripts.query``'s import style (``from scripts.claude_api
import call_claude`` creates a *second* binding at ``scripts.query.call_claude``
that must be patched alongside the origin).

Non-emptiness is asserted BEFORE the exit code so that a query pipeline
that silently returned nothing would fail on the substance of its output,
not on a secondary exit-code assertion that happened to pass because we
never hit the Claude call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.cli as cli
from scripts.claude_api import ClaudeCallResult


def _seed_wiki(ws_root: Path) -> None:
    """Drop a couple of wiki pages so query/lint have something to chew on.

    Writes two summary pages under ``wiki/summaries/`` and a minimal
    ``index.md`` at the workspace root.  Matches the layout ``scripts.query``
    and ``scripts.lint`` discover: ``WorkspacePaths.index_path`` is
    ``root / 'index.md'`` and ``WorkspacePaths.summaries_dir`` is
    ``root / 'wiki' / 'summaries'``.
    """
    summaries = ws_root / "wiki" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    (summaries / "alpha.md").write_text(
        "# alpha\n\nAlpha subsystem summary.\n", encoding="utf-8"
    )
    (summaries / "beta.md").write_text(
        "# beta\n\nBeta subsystem summary.\n", encoding="utf-8"
    )
    (ws_root / "index.md").write_text(
        "# index\n- alpha\n- beta\n", encoding="utf-8"
    )


def test_query_end_to_end(tmp_path, monkeypatch, capsys):
    """``llm-wiki --workspace X query "..."`` answers against a seeded wiki.

    Flow: init scaffolds ``ws/``, seed drops two summaries + index, Claude is
    dual-patched to a deterministic answer, and ``cli.main`` dispatches
    through the workspace banner + subcommand.  Assertions in order:

    1. The mocked answer appears in stdout (non-emptiness BEFORE exit code).
    2. ``rc == 0`` -- the CLI path completed cleanly.
    3. The banner line ``Workspace: ... (from --workspace)`` was emitted.
    """
    from scripts import init as init_mod

    assert init_mod.main([str(tmp_path / "ws")]) == 0
    ws_root = (tmp_path / "ws").resolve()
    _seed_wiki(ws_root)

    def fake_call_claude(
        *,
        client=None,
        model="claude-test-model",
        system=None,
        messages=None,
        max_tokens=None,
        context=None,
        workspace=None,
        log_event=True,
        **kw,
    ):
        return ClaudeCallResult(
            text="ALPHA answer based on wiki pages.",
            input_tokens=20,
            output_tokens=10,
            model=model,
        )

    # Dual-patch pattern: ``scripts.query`` did ``from scripts.claude_api
    # import call_claude``, creating a second binding that must be patched
    # alongside the origin so any future rename/refactor keeps working.
    monkeypatch.setattr("scripts.claude_api.call_claude", fake_call_claude)
    monkeypatch.setattr("scripts.query.call_claude", fake_call_claude)

    # Isolate the workspace contract: clear the env override so ``--workspace``
    # is the sole resolver, and supply a dummy API key so ``init_client``
    # succeeds without ever touching a real Anthropic endpoint.
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-query-lint-e2e")

    rc = cli.main(["--workspace", str(ws_root), "query", "what is alpha?"])
    captured = capsys.readouterr()

    # Non-emptiness BEFORE anything else (per Anchor's guidance / AC3).
    assert "ALPHA answer" in captured.out, (
        f"query produced no answer: {captured.out!r}"
    )
    assert rc == 0, captured.err
    assert "Workspace:" in captured.out  # banner (cli.py _print_banner_if_needed)


def test_lint_end_to_end(tmp_path, monkeypatch, capsys):
    """``llm-wiki --workspace X lint`` runs end-to-end against a seeded wiki.

    Lint never calls Claude, so no mock is required.  Seeded pages will
    fail several structural checks (no source attribution, no internal
    links, etc.), which is fine -- the capstone only asserts the pipeline
    completed (exit code 0 or 1), emitted the banner, and produced some
    stdout.
    """
    from scripts import init as init_mod

    assert init_mod.main([str(tmp_path / "ws")]) == 0
    ws_root = (tmp_path / "ws").resolve()
    _seed_wiki(ws_root)

    # Clear the env override (AC5) so ``--workspace`` owns resolution.
    # Dummy API key keeps parity with test_query_end_to_end; lint never
    # reads it, but the CLI shares env handling across subcommands.
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-query-lint-e2e")

    # ``cli.main`` raises ``SystemExit`` for the lint subcommand (cli.py
    # dispatches ``raise SystemExit(DISPATCH["lint"](...))``), so we capture
    # the exit code off the exception rather than a plain return value.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--workspace", str(ws_root), "lint"])
    rc = excinfo.value.code
    captured = capsys.readouterr()

    # Non-emptiness BEFORE exit code (mirrors the query test's ordering and
    # Anchor's guidance: assert the pipeline produced output first, then
    # worry about the exit status).
    assert captured.out.strip() != ""
    # Lint exits 0 when every check passed, 1 when any failed.  Seeded
    # pages deliberately fail structural checks; either outcome is valid
    # for this capstone -- we only care that the CLI pipeline completed.
    assert rc in (0, 1)
    assert "Workspace:" in captured.out  # banner
