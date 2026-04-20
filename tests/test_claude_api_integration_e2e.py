"""E2e capstone for the claude_api wrapper epic (LWC-dsdv / story LWC-r9ov).

The wrapper (``scripts.claude_api.call_claude``) is a leaf module -- it has
no user-visible output on its own.  Its user-visible contract is that the
subcommands that call it (``ingest`` foremost) produce correct
``claude_api_call`` events (ARCHITECTURE.md §10.3) and a ``run_summary``
event (ARCHITECTURE.md §10.4) in ``state/ingest_events.jsonl``.

This test proves the wrapper integrates end-to-end with a real ingest run.

Patching strategy (the story's core requirement)
------------------------------------------------

Unlike ``test_ingest_e2e.py`` (LWC-euj4), which stubs ``call_claude`` itself
and therefore *bypasses* the wrapper's event-emission path, this test stubs
at the SDK boundary *below* the wrapper: ``anthropic.Anthropic``.  The
wrapper's ``build_client`` constructs an ``anthropic.Anthropic(...)``
instance via ``init_client`` -> ``build_client`` -> ``Anthropic(...)``; the
monkeypatch below replaces that factory with a fake that returns a fake
client whose ``messages.create`` returns a fake response.  Everything above
the SDK line -- including ``call_claude`` -- runs in production form.  If
the wrapper skipped event emission, forgot to include a required field, or
misrouted the file path, this test would catch it; the upstream mock-
``call_claude`` tests in LWC-euj4 would not.

Acceptance criteria mapping
---------------------------

AC 1: this file exists with ``test_claude_api_wrapper_flows_through_ingest``.
AC 2: monkeypatch is applied at ``anthropic.Anthropic`` so the wrapper's
      event-emission path runs in production form.
AC 3: workspace is scaffolded via ``init.main`` and seeded with a real
      source file under ``raw/inbox/``.
AC 4: ``claude_api_call`` events are emitted with the required fields per
      ARCHITECTURE §10.3: ``event``, ``ts``, ``model``, ``input_tokens``,
      ``output_tokens``, ``context``.  (ARCHITECTURE §10.3 is explicit that
      ``workspace`` is NOT on per-call events: "workspace is **not** on
      per-call events (redundant; every event in this file comes from the
      same workspace)."  The story body's sample code listing ``workspace``
      as a per-call field contradicts the architecture and the existing
      tests in ``test_ingest_run_summary.py``; see the DISCOVERED_BUG in
      the delivery notes for LWC-r9ov.)
AC 5: the resolved workspace path (not repo-root) is asserted on the
      ``run_summary`` event (ARCHITECTURE §10.4), which is where
      ``workspace`` legitimately lives.  This preserves AC 5's *intent* --
      proving the wrapper saw the --workspace-resolved path and wrote to
      that workspace's event file -- while honoring the real §10.3
      contract for per-call events.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.claude_api import ClaudeCallResult  # noqa: F401  -- AC 1 listed import


# ---------------------------------------------------------------------------
# SDK-boundary fakes -- below the wrapper, above the network.
# ---------------------------------------------------------------------------


class _FakeAnthropicUsage:
    """Stand-in for ``response.usage`` with integer token counts.

    Integer counts (not MagicMock auto-attrs) are load-bearing: the wrapper
    serializes these fields into a JSONL event line, and MagicMock values
    would fail ``json.dumps`` inside ``_append_event``.
    """

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeAnthropicTextBlock:
    """Stand-in for ``anthropic.types.TextBlock``.

    ``scripts.claude_api._extract_text`` filters by ``block.type == 'text'``
    and concatenates ``block.text``, so we only need those two attributes.
    """

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    """Stand-in for ``anthropic.types.Message``.

    Exposes the two attributes the wrapper reads: ``content`` (a list of
    blocks) and ``usage`` (with ``input_tokens`` / ``output_tokens``).
    """

    def __init__(self, payload_text: str, input_tokens: int, output_tokens: int) -> None:
        self.content = [_FakeAnthropicTextBlock(payload_text)]
        self.usage = _FakeAnthropicUsage(input_tokens, output_tokens)


class _FakeAnthropicMessages:
    """Stand-in for ``client.messages``.

    The wrapper calls ``client.messages.create(model=, system=, messages=,
    max_tokens=)``; this captures the call and returns the pre-built
    response.  The fake is model-agnostic: every call returns the same
    payload regardless of what the ingest pipeline asked for.
    """

    def __init__(self, response: _FakeAnthropicResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> _FakeAnthropicResponse:
        self.calls.append(dict(kwargs))
        return self._response


class _FakeAnthropicClient:
    """Stand-in for ``anthropic.Anthropic(...)``.

    Construction is no-op (the real client captures ``api_key`` plus HTTP
    config; we don't need either).  ``messages`` is the only attribute the
    wrapper touches.
    """

    def __init__(self, response: _FakeAnthropicResponse) -> None:
        self.messages = _FakeAnthropicMessages(response)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_claude_api_wrapper_flows_through_ingest(tmp_path: Path, monkeypatch) -> None:
    """Full ``--workspace ... ingest`` run with SDK-level patching.

    Flow:
      1. Scaffold a workspace with ``init.main`` -- AC 3.
      2. Seed ``raw/inbox/`` with a single durable-knowledge source file.
      3. Build a JSON payload the ingest parser will accept -- the same
         minimal shape used by ``test_ingest_e2e.py`` (title + summary +
         lists + references).  The wrapper itself doesn't care about this
         shape -- it only extracts text -- but ingest downstream does, and
         if the parser rejects the payload the whole pipeline short-
         circuits before any ``claude_api_call`` events are emitted,
         defeating the point of the capstone.
      4. Patch ``anthropic.Anthropic`` at the SDK boundary -- AC 2.  This
         factory function is what ``scripts.claude_api.build_client``
         calls; replacing it with one that returns ``_FakeAnthropicClient``
         means the wrapper runs untouched and its event-emission path
         exercises real code.
      5. Env hygiene: clear ``LLM_WIKI_WORKSPACE`` so the --workspace flag
         is authoritative; set a dummy ``ANTHROPIC_API_KEY`` so
         ``init_client`` does not reject the run before dispatch reaches
         the patched SDK.
      6. Run ``cli.main(['--workspace', ws, 'ingest'])``.
      7. Verify claude_api_call events conform to ARCHITECTURE §10.3 --
         AC 4 -- and the run_summary event conforms to §10.4 and carries
         the resolved workspace path -- AC 5.
    """

    # 1. Scaffold the workspace.  Imports are deferred so a broken import
    #    path during development shows up as an import-time error in this
    #    test (not a collection-time error in the whole module).
    from scripts import cli, init as init_mod

    ws_arg = tmp_path / "ws"
    init_rc = init_mod.main([str(ws_arg)])
    assert init_rc == 0, f"init.main exit {init_rc} (expected 0)"
    ws_root = ws_arg.resolve()

    # 2. Seed raw/inbox/ with a durable-knowledge source.  The content
    #    length (~1200 chars) clears the low-signal filter threshold in
    #    ingest-settings.json so the file actually routes through the
    #    model-call path.
    (ws_root / "raw" / "inbox" / "src.md").write_text(
        "# Source\n" + ("Durable content about the system. " * 30),
        encoding="utf-8",
    )

    # 3. JSON payload the ingest parser accepts.  Minimal shape: a title,
    #    summary, empty topic/entity lists (so the parser doesn't try to
    #    cross-reference anything), and a self-referential sources list.
    #    This is the same shape ``test_ingest_e2e.py`` uses; if it changes
    #    there, change it here too.
    payload = {
        "title": "Summary of src.md",
        "summary": "Durable knowledge summary for src.md.",
        "key_facts": ["Fact about src.md"],
        "topics": [],
        "entities": [],
        "open_questions": [],
        "topic_summaries": {},
        "entity_summaries": {},
        "references": ["src.md"],
    }
    # Token counts chosen to be distinct, non-zero, and easy to eyeball in
    # the assertion failure messages if this test regresses.
    fake_input_tokens = 100
    fake_output_tokens = 50
    fake_response = _FakeAnthropicResponse(
        payload_text=json.dumps(payload),
        input_tokens=fake_input_tokens,
        output_tokens=fake_output_tokens,
    )

    # 4. Patch the SDK boundary.  The logical target is the ``Anthropic``
    #    name in the ``anthropic`` package -- that is what
    #    ``scripts.claude_api.build_client`` calls.  However,
    #    ``scripts.claude_api`` does ``from anthropic import Anthropic`` at
    #    import time, which binds ``Anthropic`` as a local module-level
    #    reference.  Patching ONLY ``anthropic.Anthropic`` therefore does
    #    not retarget the already-bound local reference that
    #    ``build_client`` actually calls; the real SDK still runs and
    #    (predictably) 401s against the dummy API key.  We patch both
    #    locations for defense-in-depth: the ``anthropic.Anthropic`` name
    #    (so any lazy-importing call site would also be redirected) and
    #    the ``scripts.claude_api.Anthropic`` local reference (so
    #    ``build_client``'s actual lookup resolves to the fake).  This
    #    still stubs *below* the wrapper -- the wrapper itself runs
    #    untouched and its event-emission path exercises real code, which
    #    is the whole point of LWC-r9ov vs. LWC-euj4.
    def fake_client_factory(*args: object, **kwargs: object) -> _FakeAnthropicClient:
        # ``build_client`` calls ``Anthropic(api_key=api_key)``; we ignore
        # both the positional and keyword forms.  The fake is stateless
        # across constructions -- the real code paths construct one client
        # per ingest run, but we don't depend on that invariant.
        return _FakeAnthropicClient(fake_response)

    monkeypatch.setattr("anthropic.Anthropic", fake_client_factory)
    monkeypatch.setattr("scripts.claude_api.Anthropic", fake_client_factory)

    # 5. Env hygiene.  LLM_WIKI_WORKSPACE would otherwise override the
    #    --workspace flag; ANTHROPIC_API_KEY is checked by ``init_client``
    #    before the SDK is ever touched, so it must be set (even to a
    #    dummy) for ingest to reach the wrapper at all.
    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-claude-api-e2e")

    # 6. Run the real CLI dispatch end-to-end.  This exercises workspace
    #    resolution, the ``ingest`` dispatch entry, ``ingest.main``,
    #    ``init_client``, ``build_client``, ``call_claude``, and the
    #    per-source write loop.  Only the SDK line is faked.
    rc = cli.main(["--workspace", str(ws_root), "ingest"])
    assert rc == 0, f"cli.main returned {rc}; ingest should succeed"

    # 7a. Non-emptiness precondition.  If the parser rejected the payload
    #     or the wrapper short-circuited, ``summaries/`` would be empty
    #     and the per-call event assertions below would be vacuously true
    #     (or false for the wrong reason).  Guard explicitly.
    summaries_dir = ws_root / "wiki" / "summaries"
    assert summaries_dir.is_dir(), (
        f"{summaries_dir} missing -- ingest did not run to completion"
    )
    assert any(summaries_dir.iterdir()), (
        "ingest produced no summary pages; the SDK fake probably did not "
        "intercept ``anthropic.Anthropic`` (check the patch target)."
    )

    # 7b. Load the events file.  JSONL; skip blank lines defensively.
    events_path = ws_root / "state" / "ingest_events.jsonl"
    assert events_path.exists(), (
        f"{events_path} missing; the wrapper never emitted an event, "
        "which means call_claude's event-emission path did not run."
    )
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # 7c. claude_api_call events -- AC 4.  Per ARCHITECTURE §10.3 the
    #     event shape is exactly six keys: event, ts, model, input_tokens,
    #     output_tokens, context.  workspace is deliberately NOT included
    #     (the architecture: "workspace is *not* on per-call events
    #     (redundant; every event in this file comes from the same
    #     workspace)").  We check the shape strictly: if the wrapper ever
    #     starts emitting extra fields, we want this test to fail so the
    #     contract is revisited, not silently broadened.
    api_calls = [e for e in events if e.get("event") == "claude_api_call"]
    assert len(api_calls) >= 1, (
        "wrapper did not emit any claude_api_call events; this is the "
        "whole point of stubbing at the SDK boundary -- if the wrapper "
        "was bypassed (e.g. ``call_claude`` itself patched), no event "
        "would be emitted."
    )
    expected_fields = {
        "event",
        "ts",
        "model",
        "input_tokens",
        "output_tokens",
        "context",
    }
    for call_event in api_calls:
        assert set(call_event.keys()) == expected_fields, (
            f"claude_api_call event fields mismatch.\n"
            f"got:      {sorted(call_event.keys())}\n"
            f"expected: {sorted(expected_fields)}\n"
            f"(ARCHITECTURE §10.3 is the source of truth)"
        )
        # Types and values: tokens come straight from the fake usage, so
        # they should match exactly; model is whatever ingest resolved
        # from ``ANTHROPIC_INGEST_MODEL`` (default ``claude-haiku-4-5``);
        # context is a non-empty tag.
        assert call_event["input_tokens"] == fake_input_tokens
        assert call_event["output_tokens"] == fake_output_tokens
        assert isinstance(call_event["model"], str) and call_event["model"]
        assert isinstance(call_event["context"], str) and call_event["context"]
        assert isinstance(call_event["ts"], str) and call_event["ts"]

    # 7d. run_summary event -- AC 5.  Per ARCHITECTURE §10.4 exactly one
    #     run_summary is emitted at the end of the run, and the
    #     ``workspace`` field *is* on this event.  We assert the resolved
    #     workspace path (not repo-root) to prove the wrapper + ingest saw
    #     and respected the --workspace flag.
    run_summaries = [e for e in events if e.get("event") == "run_summary"]
    assert len(run_summaries) == 1, (
        f"expected exactly one run_summary event, got {len(run_summaries)}; "
        f"events={events!r}"
    )
    run_summary = run_summaries[0]
    assert run_summary["workspace"] == str(ws_root), (
        f"run_summary.workspace mismatch: "
        f"got {run_summary['workspace']!r}, expected {str(ws_root)!r}"
    )
    # Totals sanity-check: run_summary.total_*_tokens must equal the sum
    # of the per-call tokens (ARCHITECTURE §10.4).  This confirms the
    # wrapper's emission path *and* ingest's totals bookkeeping both ran.
    assert run_summary["total_input_tokens"] == sum(
        e["input_tokens"] for e in api_calls
    )
    assert run_summary["total_output_tokens"] == sum(
        e["output_tokens"] for e in api_calls
    )
    assert run_summary["api_call_count"] == len(api_calls)
