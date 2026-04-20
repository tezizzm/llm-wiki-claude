"""E2e capstone for the ``--workspace refresh-fast`` chain (LWC-oie0).

Proves that a user invoking ``llm-wiki --workspace <ws> refresh-fast`` against a
freshly-``init``-scaffolded workspace runs sync then ingest in one invocation,
prints exactly ONE banner line at the top (DESIGN §4.2) and exactly ONE
token-summary line at the end (DESIGN §8.2), and produces non-empty output in
both ``raw/inbox/`` and ``wiki/summaries/``.

Unlike ``test_refresh_fast_flow.py`` -- which uses the shared ``tmp_workspace``
fixture and calls ``sync.main`` / ``ingest.main`` directly -- this test
exercises the real dispatcher entry point (``scripts.cli.main``) with the
``--workspace`` global flag, so the banner/env/workspace resolution code path
is covered end-to-end as well.

Mock contract: ``scripts.claude_api.call_claude`` is replaced with a fake that
returns a JSON-shaped ``ClaudeCallResult``.  Returning raw text would parse
into an empty payload and the non-emptiness assertions below would fail
loudly -- the JSON shape is load-bearing per ARCHITECTURE §10.2.

Schema caveat: the story's sample config used ``path``/``include_globs``/
``naming_mode`` keys, but ``scripts.config_models.SyncSourceConfig`` (``extra =
'forbid'``) actually requires ``root``/``include``/``exclude``/``naming:
{mode, prefix}``.  The real schema is used here.

See ARCHITECTURE §4.1, §4.2, §5.3, §8.2, §10.2, §10.4.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# cli dispatches refresh-fast to sync.main + ingest.main via DISPATCH.  We
# import ``ingest`` here so we can patch its ``init_client`` (avoids building
# a real Anthropic client) and its ``call_claude`` import-site binding
# (``from scripts.claude_api import call_claude`` rebinds the name inside
# ``scripts.ingest``, so patching only the origin module is insufficient).
from scripts import cli, ingest  # noqa: E402
from scripts import init as init_mod  # noqa: E402
from scripts.claude_api import ClaudeCallResult  # noqa: E402


# JSON payload the ingest pipeline expects per ARCHITECTURE §10.2.  Keys mirror
# the schema ingest.call_claude_json parses: title/summary + topic/entity
# populates wiki/summaries/, wiki/topics/, wiki/entities/ respectively.
_E2E_PAYLOAD = {
    "title": "Hello",
    "summary": "Durable summary of hello content.",
    "key_facts": ["Hello is durable knowledge."],
    "topics": ["Hello Knowledge"],
    "entities": ["HelloEntity"],
    "open_questions": [],
    "topic_summaries": {"Hello Knowledge": "Topic summary."},
    "entity_summaries": {"HelloEntity": "Entity summary."},
}


def _json_shaped_mock():
    """Return a ``call_claude`` replacement that yields JSON-shaped text.

    Matches the keyword-only signature of the real
    :func:`scripts.claude_api.call_claude`; uses ``**kw`` to tolerate any
    future keyword additions without requiring a test update.
    """

    def _fn(*, client=None, model="fake-model", system=None, messages=None,
            max_tokens=None, context=None, workspace=None, log_event=True, **kw):
        return ClaudeCallResult(
            text=json.dumps(_E2E_PAYLOAD),
            input_tokens=30,
            output_tokens=15,
            model=model,
        )

    return _fn


def test_refresh_fast_end_to_end(tmp_path, monkeypatch, capsys):
    """``cli.main(['--workspace', ws, 'refresh-fast'])`` runs sync then ingest
    against an init-scaffolded workspace with a seeded tmp source repo, and
    emits exactly one banner line and one token-summary line.

    Covers LWC-oie0 ACs 1-6:
      1. Test file + function exist (this).
      2. Workspace is scaffolded via ``init.main`` and sync is configured to
         point at a tmp_path source dir.
      3. ``scripts.claude_api.call_claude`` is mocked with a JSON-shaped
         payload (no raw echo).
      4. Exactly ONE ``Workspace: ...`` banner line AND exactly ONE
         ``Used ... tokens this run.`` summary line, both regex-matched.
      5. Non-emptiness: ``raw/inbox`` and ``wiki/summaries`` each have >= 1
         file after the chain completes.
      6. Environment hygiene: ``LLM_WIKI_WORKSPACE`` is unset so resolution
         uses the ``--workspace`` flag (banner label: ``from --workspace``);
         a dummy ``ANTHROPIC_API_KEY`` is set to satisfy any downstream
         env-var checks even though the Anthropic client is patched out.
    """

    # --- 1. Scaffold the workspace via init.main ---------------------------
    ws = tmp_path / "ws"
    rc_init = init_mod.main([str(ws)])
    assert rc_init == 0, "init.main failed to scaffold the workspace"
    ws_root = ws.resolve()

    # --- 2. Seed a tmp source repo with durable-knowledge content ----------
    # sync needs enough prose that ingest does not drop the source under its
    # low-signal filter.  "Durable knowledge about hello." * 30 >> the
    # minimum-size threshold and contains no opaque-task markers.
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.md").write_text(
        "# Hello\n" + ("Durable knowledge about hello. " * 30),
        encoding="utf-8",
    )

    # --- 3. Rewrite sync-sources.local.json to point at the seeded source --
    # Init's template points at a placeholder path; overwrite with the REAL
    # SyncSourceConfig schema (extra='forbid' -- see scripts/config_models.py
    # for the source of truth).  The story's sample used path/include_globs/
    # naming_mode which would fail schema validation.
    cfg = {
        "schema_version": 1,
        "sources": [
            {
                "name": "src",
                "root": str(src),
                "include": ["*.md"],
                "exclude": [],
                "naming": {"mode": "basename", "prefix": "src"},
            }
        ],
    }
    (ws_root / "sync-sources.local.json").write_text(
        json.dumps(cfg), encoding="utf-8"
    )

    # --- 4. Patch out the real Anthropic call path -------------------------
    # Two patches because scripts.ingest does ``from scripts.claude_api
    # import call_claude`` (import-site rebinding).  Patching only the
    # origin would leave the ingest module still pointing at the real
    # function.  Story prompt explicitly calls this pattern out.
    monkeypatch.setattr("scripts.claude_api.call_claude", _json_shaped_mock())
    monkeypatch.setattr(ingest, "call_claude", _json_shaped_mock())
    # init_client returns a real Anthropic client by default; override so the
    # test does not require network or a real API key.
    monkeypatch.setattr(ingest, "init_client", lambda: ("fake-client", "fake-model"))

    # --- 5. Environment hygiene --------------------------------------------
    # Drop any ambient LLM_WIKI_WORKSPACE so workspace source resolves via
    # --workspace (banner label: 'from --workspace').  Set a dummy API key
    # so any env-var assertion downstream is satisfied (client is patched).
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-refresh-e2e")

    # --- 6. Run the chain via the real CLI entry point ---------------------
    rc = cli.main(["--workspace", str(ws_root), "refresh-fast"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    # --- 7. Non-emptiness asserts FIRST (AC 5) -----------------------------
    # Run these before banner/summary asserts so that a failure to produce
    # wiki output (e.g. the mock returned raw text that failed JSON parse)
    # surfaces a clear "no files written" failure rather than a downstream
    # "no summary line" failure.
    raw_files = list((ws_root / "raw" / "inbox").iterdir())
    summary_files = list((ws_root / "wiki" / "summaries").iterdir())
    assert len(raw_files) >= 1, (
        f"sync did not populate raw/inbox: got {raw_files}"
    )
    assert len(summary_files) >= 1, (
        f"ingest did not populate wiki/summaries: got {summary_files}"
    )

    # --- 8. Exactly ONE banner line (DESIGN §4.2) --------------------------
    banner_lines = [
        line for line in captured.out.splitlines() if line.startswith("Workspace:")
    ]
    assert len(banner_lines) == 1, (
        f"expected exactly 1 banner line, got {len(banner_lines)}: {banner_lines}"
    )

    # --- 9. Exactly ONE token-summary line (DESIGN §8.2) -------------------
    summary_re = re.compile(r"Used .+ input / .+ output tokens this run\.")
    summary_lines = [
        line for line in captured.out.splitlines() if summary_re.match(line)
    ]
    assert len(summary_lines) == 1, (
        f"expected exactly 1 token-summary line, got {len(summary_lines)}: "
        f"{summary_lines}"
    )
