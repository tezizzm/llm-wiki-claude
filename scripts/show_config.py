import os
from dotenv import load_dotenv

def main():
    load_dotenv()
    ingest_model = os.getenv("ANTHROPIC_INGEST_MODEL", "claude-haiku-4-5")
    query_model = os.getenv("ANTHROPIC_QUERY_MODEL", "claude-sonnet-4-6")
    lint_model = os.getenv("ANTHROPIC_LINT_MODEL", "claude-haiku-4-5")

    print("Current Claude model configuration:")
    print(f"- Ingest model: {ingest_model}")
    print(f"- Query model:  {query_model}")
    print(f"- Lint model:   {lint_model}")

if __name__ == "__main__":
    main()
