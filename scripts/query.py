import os
from pathlib import Path

from dotenv import load_dotenv
import anthropic

ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "wiki"
INDEX_PATH = ROOT / "index.md"

def init_client():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_anthropic_api_key_here":
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY. Copy .env.example to .env and set a real Anthropic API key."
        )
    client = anthropic.Anthropic(api_key=api_key)
    model = os.getenv("ANTHROPIC_QUERY_MODEL", "claude-sonnet-4-6")
    return client, model

def read_all_wiki_text(max_chars: int = 60000) -> str:
    chunks = []
    if INDEX_PATH.exists():
        chunks.append(f"FILE: index.md\n{INDEX_PATH.read_text(encoding='utf-8')}\n")
    for folder in ["summaries", "topics", "entities"]:
        for fp in sorted((WIKI_DIR / folder).glob("*.md")):
            text = fp.read_text(encoding="utf-8")
            chunks.append(f"FILE: {fp.relative_to(ROOT).as_posix()}\n{text}\n")
    joined = "\n\n".join(chunks)
    return joined[:max_chars]

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


def ask(client, model: str, wiki_text: str, question: str) -> str:
    """Send a single question to the model and return the answer text."""
    user_prompt = f"""Question:
{question}

Wiki content:
{wiki_text}
"""
    response = client.messages.create(
        model=model,
        max_tokens=2500,
        temperature=0.2,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": user_prompt,
            }
        ],
    )
    return extract_text_blocks(response)


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Query the local wiki.")
    parser.add_argument("question", nargs="?", default=None, help="Inline question text.")
    args = parser.parse_args()

    try:
        client, model = init_client()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return

    wiki_text = read_all_wiki_text()
    if not wiki_text.strip():
        print("Error: No wiki pages found. Nothing to query.")
        print("Hint: Run 'llm-wiki sync' and 'llm-wiki ingest' first to populate the wiki.")
        return

    # Inline mode: question provided as argument
    if args.question is not None:
        answer = ask(client, model, wiki_text, args.question)
        print(f"\n[Model: {model}]\n")
        print(answer)
        return

    # Stdin pipe mode: not a TTY, read from stdin
    if not sys.stdin.isatty():
        question = sys.stdin.read().strip()
        if question:
            answer = ask(client, model, wiki_text, question)
            print(f"\n[Model: {model}]\n")
            print(answer)
        return

    # Interactive TTY mode: prompt loop
    print("llm-wiki query (interactive mode)")
    print("Type your question and press Enter. Type 'exit' or 'quit' to leave.\n")
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
        answer = ask(client, model, wiki_text, question)
        if first_answer:
            print(f"\n[Model: {model}]\n")
            first_answer = False
        else:
            print()
        print(answer)


if __name__ == "__main__":
    main()
