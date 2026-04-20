"""Query the local wiki under a workspace.

Workspace-aware refactor (LWC-zaz2): every path flows from a
``WorkspacePaths`` instance passed to :func:`main`; no helper reaches into a
module global for a path.  See ARCHITECTURE.md §5.3 and §10.2 for the
contract.

Per ARCHITECTURE §10.2 query calls are NOT logged to
``workspace.ingest_events_path``.  The sole SDK call routes through
:func:`scripts.claude_api.call_claude` with ``log_event=False`` so the event
log stays reserved for ingest-phase token bookkeeping.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from typing import Any

from dotenv import load_dotenv

from scripts.claude_api import ClaudeCallResult, build_client, call_claude
from scripts.workspace import WorkspacePaths


def init_client() -> tuple[Any, str]:
    """Construct the Anthropic client plus resolve the query model name.

    The client is typed as ``Any`` because ``scripts.query`` MUST NOT import
    ``anthropic`` directly; SDK access goes through :mod:`scripts.claude_api`.
    The concrete runtime type is ``anthropic.Anthropic`` via
    :func:`scripts.claude_api.build_client`.
    """

    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_anthropic_api_key_here":
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY. Copy .env.example to .env and set a real Anthropic API key."
        )
    model = os.getenv("ANTHROPIC_QUERY_MODEL", "claude-sonnet-4-6")
    client = build_client(api_key)
    return client, model


def collect_wiki_text(workspace: WorkspacePaths, max_chars: int = 60000) -> str:
    """Return the concatenated wiki text under ``workspace``.

    Reads ``workspace.index_path`` first (when present) then the ``summaries``,
    ``topics``, and ``entities`` subdirectories of ``workspace.wiki_dir`` in a
    deterministic sort order.  The returned string is truncated to
    ``max_chars`` characters to stay within the model's context window.
    """

    chunks: list[str] = []
    if workspace.index_path.exists():
        chunks.append(
            f"FILE: index.md\n{workspace.index_path.read_text(encoding='utf-8')}\n"
        )

    folder_map = {
        "summaries": workspace.summaries_dir,
        "topics": workspace.topics_dir,
        "entities": workspace.entities_dir,
    }
    for folder_name, folder_path in folder_map.items():
        if not folder_path.exists():
            continue
        for fp in sorted(folder_path.glob("*.md")):
            text = fp.read_text(encoding="utf-8")
            chunks.append(f"FILE: wiki/{folder_name}/{fp.name}\n{text}\n")

    joined = "\n\n".join(chunks)
    return joined[:max_chars]


def load_index(workspace: WorkspacePaths) -> str:
    """Return the contents of ``workspace.index_path`` or an empty string.

    An empty string is the documented sentinel for "no index present" -- it
    mirrors the 0.2.0 ``read_all_wiki_text`` path that quietly skipped the
    index chunk when ``INDEX_PATH`` did not exist.  Callers use ``str.strip()``
    to distinguish "absent" from "present but whitespace-only".
    """

    path = workspace.index_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    return str()


def extract_text_blocks(response) -> str:
    parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


SYSTEM_PROMPT = """You answer questions using a local markdown wiki.
Be grounded in the wiki content only.
If the wiki does not contain enough information, say so plainly.
End with a short 'Sources' section listing the markdown files you relied on."""


def ask(
    client: Any,
    model: str,
    wiki_text: str,
    question: str,
    workspace: WorkspacePaths,
) -> str:
    """Send a single question to the model and return the answer text.

    Routes through :func:`scripts.claude_api.call_claude` with
    ``log_event=False`` so no event is appended to
    ``workspace.ingest_events_path``.  Per ARCHITECTURE §10.2, query calls are
    interactive Q&A -- not part of the ingest pipeline -- and must not pollute
    the ingest event log.
    """

    user_prompt = f"""Question:
{question}

Wiki content:
{wiki_text}
"""
    result: ClaudeCallResult = call_claude(
        client=client,
        model=model,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": user_prompt,
            }
        ],
        max_tokens=2500,
        context=question[:100],
        workspace=workspace,
        log_event=False,
    )
    return result.text.strip()


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """query entry point.

    Signature matches the workspace-aware dispatch contract in
    ``scripts.cli``: every subcommand is called as
    ``fn(remaining_argv, workspace)`` and returns an int exit code.

    All paths are resolved from ``workspace``; no module-level path constants
    are consulted.  Supports three input modes:

    * Inline argument: ``query "the question"``
    * Stdin pipe: ``echo "the question" | query``
    * Interactive TTY: bare ``query`` command opens a prompt loop
    """

    parser = argparse.ArgumentParser(
        prog="query",
        description="Ask a question against the local wiki.",
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Inline question text.",
    )
    args = parser.parse_args(argv)

    try:
        client, model = init_client()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 0

    wiki_text = collect_wiki_text(workspace)
    if not wiki_text.strip():
        print("Error: No wiki pages found. Nothing to query.")
        print("Hint: Run 'llm-wiki sync' and 'llm-wiki ingest' first to populate the wiki.")
        return 0

    # Inline mode: question provided as argument
    if args.question is not None:
        answer = ask(client, model, wiki_text, args.question, workspace)
        print(f"\n[Model: {model}]\n")
        print(answer)
        return 0

    # Stdin pipe mode: not a TTY, read from stdin
    if not sys.stdin.isatty():
        question = sys.stdin.read().strip()
        if question:
            answer = ask(client, model, wiki_text, question, workspace)
            print(f"\n[Model: {model}]\n")
            print(answer)
        return 0

    # Interactive TTY mode: prompt loop
    print("llm-wiki query (interactive) -- type \"exit\" or Ctrl-D to quit")
    first_answer = True
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            break
        answer = ask(client, model, wiki_text, question, workspace)
        if first_answer:
            print(f"\n[Model: {model}]\n")
            first_answer = False
        else:
            print()
        print(answer)
    return 0


if __name__ == "__main__":
    # Direct execution path: build a default workspace from the repo root.
    from scripts.workspace import resolve_workspace

    raise SystemExit(main(sys.argv[1:], resolve_workspace(None, None)))
