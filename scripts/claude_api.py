"""Single SDK call site for the Anthropic API.

This module is the one place the llm-wiki pipeline talks to Anthropic's SDK.
Every other module that needs to call Claude -- ingest, query, lint -- routes
through :func:`call_claude` rather than calling ``client.messages.create``
directly.  Centralizing here lets us emit token-usage events to the workspace
event log uniformly, without each caller re-implementing the bookkeeping.

See ARCHITECTURE.md sections 10.1, 10.2, 10.3, and 10.4 for the full contract.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from scripts.workspace import WorkspacePaths


@dataclass(frozen=True)
class ClaudeCallResult:
    """Frozen result of a single Anthropic ``messages.create`` invocation.

    ``text`` is the concatenation of every ``TextBlock`` in ``response.content``
    (other block types are skipped).  ``input_tokens`` / ``output_tokens`` come
    straight from ``response.usage``.  ``model`` echoes the model id the caller
    requested, so downstream code never has to track it separately.
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str


def call_claude(
    *,
    client: Anthropic,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    context: str,
    workspace: WorkspacePaths,
    log_event: bool = True,
) -> ClaudeCallResult:
    """Call ``client.messages.create`` and emit a token-usage event.

    Keyword-only to prevent positional misuse -- the argument list is long
    enough that positional calls would be unreadable and error-prone.

    When ``log_event`` is true (default), a single-line JSON event is appended
    to ``workspace.ingest_events_path`` capturing the model id, input/output
    token counts, ISO 8601 UTC timestamp, and the caller-supplied ``context``
    tag (e.g. the source file being ingested, or ``'ingest_pipeline'``).  Per
    ARCHITECTURE 10.3 the event file records only raw token counts -- no
    economic fields are ever emitted.
    """

    response = client.messages.create(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
    )
    usage = response.usage
    text = _extract_text(response)
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens

    if log_event:
        _append_event(
            workspace.ingest_events_path,
            {
                "event": "claude_api_call",
                "ts": _now_iso(),
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "context": context,
            },
        )

    return ClaudeCallResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )


def _extract_text(response: Any) -> str:
    """Concatenate the ``.text`` of every text-typed content block, in order.

    ``response.content`` is a list of content blocks; only ``TextBlock`` entries
    (``block.type == 'text'``) carry user-visible text.  Other block types --
    thinking, tool_use, etc. -- are silently skipped.  Returns an empty string
    when the response has no text blocks.
    """

    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def _append_event(path: Path, event: dict) -> None:
    """Append a single JSONL event line with a trailing newline.

    Creates ``path.parent`` if missing.  The write is a single ``f.write`` +
    ``f.flush`` on a text-mode handle opened in append mode; per
    ARCHITECTURE 10.4 we accept the small window where a crash mid-line leaves
    a malformed trailing line, and expect readers to skip unparseable lines
    rather than fsync every write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
        f.flush()


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with timezone suffix."""

    return datetime.datetime.now(datetime.timezone.utc).isoformat()
