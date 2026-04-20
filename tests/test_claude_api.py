"""Tests for scripts.claude_api -- the single SDK call site for Anthropic.

These tests mock the Anthropic SDK end-to-end via a fake client: we never make
a real network call.  The integration test at the bottom exercises the full
JSONL event-file round-trip, writing to a real temp path and parsing it back.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.claude_api import (
    ClaudeCallResult,
    _append_event,
    _extract_text,
    _now_iso,
    call_claude,
)
from scripts.workspace import resolve_workspace


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_text_block(text: str) -> SimpleNamespace:
    """Build a stand-in for anthropic.types.TextBlock."""

    return SimpleNamespace(type="text", text=text)


def _make_non_text_block(block_type: str = "thinking") -> SimpleNamespace:
    """Build a stand-in for a non-text content block (e.g. ThinkingBlock)."""

    # Deliberately omit .text to prove _extract_text doesn't touch it for
    # non-text blocks.
    return SimpleNamespace(type=block_type, thinking="hidden reasoning")


def _make_response(
    content_blocks: list[Any],
    input_tokens: int,
    output_tokens: int,
) -> SimpleNamespace:
    """Build a stand-in for anthropic.types.Message."""

    return SimpleNamespace(
        content=content_blocks,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


class FakeAnthropicClient:
    """Minimal stand-in for anthropic.Anthropic used to capture call args.

    ``response`` is returned verbatim from ``messages.create``.  The client
    records the kwargs of every call in ``self.calls`` so tests can assert
    what went over the wire.
    """

    def __init__(self, response: Any):
        self._response = response
        self.calls: list[dict] = []
        self.messages = self._Messages(self)

    class _Messages:
        def __init__(self, outer: "FakeAnthropicClient"):
            self._outer = outer

        def create(self, **kwargs: Any) -> Any:
            self._outer.calls.append(kwargs)
            return self._outer._response


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    """A workspace rooted at tmp_path; state_dir is created lazily by the code."""

    return resolve_workspace(str(tmp_path), env_var=None)


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file into a list of dicts."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# ClaudeCallResult
# ---------------------------------------------------------------------------


def test_claude_call_result_is_frozen():
    """The dataclass is immutable -- attribute assignment must raise."""

    result = ClaudeCallResult(
        text="hello",
        input_tokens=1,
        output_tokens=2,
        model="claude-haiku-4-5",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.text = "mutated"  # type: ignore[misc]


def test_claude_call_result_fields():
    """All four expected fields are present and carry the assigned values."""

    result = ClaudeCallResult(
        text="the answer",
        input_tokens=100,
        output_tokens=50,
        model="claude-haiku-4-5",
    )
    assert result.text == "the answer"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# call_claude: usage + text extraction
# ---------------------------------------------------------------------------


def test_call_claude_extracts_usage(tmp_workspace):
    """input_tokens / output_tokens come straight from response.usage."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=100,
        output_tokens=50,
    )
    client = FakeAnthropicClient(response)

    result = call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="raw/inbox/foo.md",
        workspace=tmp_workspace,
        log_event=False,
    )

    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.model == "claude-haiku-4-5"


def test_call_claude_extracts_text(tmp_workspace):
    """A single text block flows through to result.text."""

    response = _make_response(
        [_make_text_block("hello")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    result = call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=False,
    )

    assert result.text == "hello"


def test_call_claude_concatenates_multiple_text_blocks(tmp_workspace):
    """Multiple text blocks are concatenated in order, no separator."""

    response = _make_response(
        [_make_text_block("a"), _make_text_block("b")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    result = call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=False,
    )

    assert result.text == "ab"


def test_call_claude_skips_non_text_blocks(tmp_workspace):
    """Non-text blocks (e.g. thinking) are silently ignored by _extract_text."""

    response = _make_response(
        [
            _make_text_block("before"),
            _make_non_text_block("thinking"),
            _make_text_block("after"),
        ],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    result = call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=False,
    )

    assert result.text == "beforeafter"


# ---------------------------------------------------------------------------
# call_claude: event-file behavior
# ---------------------------------------------------------------------------


def test_call_claude_writes_event_when_log_event_true(tmp_workspace):
    """log_event=True appends exactly one JSONL event with the six required fields."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1523,
        output_tokens=412,
    )
    client = FakeAnthropicClient(response)

    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="raw/inbox/architecture-overview.md",
        workspace=tmp_workspace,
        log_event=True,
    )

    events = _read_jsonl(tmp_workspace.ingest_events_path)
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "claude_api_call"
    assert event["model"] == "claude-haiku-4-5"
    assert event["input_tokens"] == 1523
    assert event["output_tokens"] == 412
    assert event["context"] == "raw/inbox/architecture-overview.md"
    assert "ts" in event
    # Exactly six fields, no extras.
    assert set(event.keys()) == {
        "event",
        "ts",
        "model",
        "input_tokens",
        "output_tokens",
        "context",
    }


def test_call_claude_no_event_when_log_event_false(tmp_workspace):
    """log_event=False writes nothing to ingest_events_path."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=False,
    )

    path = tmp_workspace.ingest_events_path
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_call_claude_event_context_field_populated(tmp_workspace):
    """The 'context' field in the event matches the passed arg verbatim."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ingest_pipeline",
        workspace=tmp_workspace,
        log_event=True,
    )

    events = _read_jsonl(tmp_workspace.ingest_events_path)
    assert events[0]["context"] == "ingest_pipeline"


def test_call_claude_no_cost_fields(tmp_workspace):
    """No forbidden economic keys appear in the event dict."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=True,
    )

    events = _read_jsonl(tmp_workspace.ingest_events_path)
    event = events[0]
    serialized = json.dumps(event).lower()
    for forbidden in ("$", "cost", "usd", "price"):
        assert forbidden not in serialized, (
            f"event contained forbidden economic token {forbidden!r}: {event}"
        )


def test_call_claude_ts_is_iso8601_with_tz(tmp_workspace):
    """AC 8: the 'ts' field is ISO 8601 with timezone offset."""

    import datetime as dt

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
        context="ctx",
        workspace=tmp_workspace,
        log_event=True,
    )

    events = _read_jsonl(tmp_workspace.ingest_events_path)
    ts = events[0]["ts"]
    # fromisoformat parses the '+00:00' suffix on Python 3.11+; we then assert
    # the tzinfo is populated so we don't accept a naive datetime.
    parsed = dt.datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


def test_call_claude_forwards_model_system_messages_max_tokens(tmp_workspace):
    """client.messages.create receives the caller's arguments verbatim."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)

    messages = [{"role": "user", "content": "hello"}]
    call_claude(
        client=client,
        model="claude-haiku-4-5",
        system="you are helpful",
        messages=messages,
        max_tokens=321,
        context="ctx",
        workspace=tmp_workspace,
        log_event=False,
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["system"] == "you are helpful"
    assert call["messages"] == messages
    assert call["max_tokens"] == 321


def test_call_claude_is_keyword_only(tmp_workspace):
    """Positional arguments beyond self raise TypeError -- API is keyword-only."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=1,
        output_tokens=1,
    )
    client = FakeAnthropicClient(response)
    with pytest.raises(TypeError):
        # All positional -- should fail because signature is keyword-only.
        call_claude(  # type: ignore[misc]
            client,
            "claude-haiku-4-5",
            "sys",
            [{"role": "user", "content": "hi"}],
            16,
            "ctx",
            tmp_workspace,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_extract_text_empty_content_returns_empty_string():
    """A response with no content blocks yields the empty string, not None."""

    response = SimpleNamespace(content=[])
    assert _extract_text(response) == ""


def test_extract_text_only_non_text_blocks_returns_empty():
    """If no text blocks are present, extraction returns ''."""

    response = SimpleNamespace(content=[_make_non_text_block("thinking")])
    assert _extract_text(response) == ""


def test_append_event_creates_parent_dirs(tmp_path):
    """_append_event creates missing parent directories."""

    target = tmp_path / "deep" / "nested" / "events.jsonl"
    _append_event(target, {"event": "x", "ts": "2026-04-20T00:00:00+00:00"})

    assert target.exists()
    line = target.read_text(encoding="utf-8").strip()
    assert json.loads(line) == {"event": "x", "ts": "2026-04-20T00:00:00+00:00"}


def test_append_event_appends_rather_than_overwrites(tmp_path):
    """Calling _append_event twice writes two lines."""

    target = tmp_path / "events.jsonl"
    _append_event(target, {"a": 1})
    _append_event(target, {"b": 2})

    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": 2}]


def test_now_iso_returns_tz_aware_string():
    """_now_iso returns an ISO 8601 string with a timezone."""

    import datetime as dt

    ts = _now_iso()
    parsed = dt.datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Integration: three calls, round-trip through the real event file
# ---------------------------------------------------------------------------


def test_event_file_roundtrip(tmp_workspace):
    """Three call_claude calls produce three ordered, parseable JSONL events."""

    response = _make_response(
        [_make_text_block("ok")],
        input_tokens=10,
        output_tokens=5,
    )
    client = FakeAnthropicClient(response)

    contexts = [
        "raw/inbox/a.md",
        "raw/inbox/b.md",
        "ingest_pipeline",
    ]
    for ctx in contexts:
        call_claude(
            client=client,
            model="claude-haiku-4-5",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
            context=ctx,
            workspace=tmp_workspace,
            log_event=True,
        )

    events = _read_jsonl(tmp_workspace.ingest_events_path)
    assert len(events) == 3
    assert [e["context"] for e in events] == contexts
    for event in events:
        assert event["event"] == "claude_api_call"
        assert event["model"] == "claude-haiku-4-5"
        assert event["input_tokens"] == 10
        assert event["output_tokens"] == 5
        assert "ts" in event
