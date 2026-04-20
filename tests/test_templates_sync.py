"""Drift + secret-leak guard for scripts/templates/ vs repo-root copies.

Per ARCHITECTURE §9.3, we keep both the repo-root copies (live config for the
0.2.0 repo-root-default workspace) and the scripts/templates/ package copies
(readable via importlib.resources). These MUST stay byte-identical. This test
fails if any pair drifts.

Per BUSINESS §8, template env files must contain ONLY placeholder values -- a
real API key committed here would land inside every new workspace via
`llm-wiki init`. We grep for realistic secret shapes and enforce that the
ANTHROPIC_API_KEY line uses a known placeholder.
"""

import re

import pytest

from scripts.workspace import repo_root


PAIRS = [
    ("scripts/templates/env.example", ".env.example"),
    ("scripts/templates/sync-sources.json", "sync-sources.json"),
    ("scripts/templates/ingest-settings.json", "ingest-settings.json"),
    ("scripts/templates/wikiignore", ".wikiignore"),
    ("scripts/templates/schemas/AGENTS.md", "schemas/AGENTS.md"),
]


def test_templates_package_importable():
    import scripts.templates  # noqa: F401
    import scripts.templates.schemas  # noqa: F401


@pytest.mark.parametrize("pkg_rel,repo_rel", PAIRS)
def test_template_matches_repo_root(pkg_rel, repo_rel):
    root = repo_root()
    pkg_bytes = (root / pkg_rel).read_bytes()
    repo_bytes = (root / repo_rel).read_bytes()
    assert pkg_bytes == repo_bytes, (
        f"{pkg_rel} and {repo_rel} have diverged. Update both in the same commit."
    )


def test_templates_readable_via_importlib_resources():
    from importlib.resources import files
    import scripts.templates as t

    content = (files(t) / "env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in content  # minimal content sanity check

    from scripts.templates import schemas as s

    schema_content = (files(s) / "AGENTS.md").read_text(encoding="utf-8")
    assert len(schema_content) > 0


# Anthropic keys start with 'sk-ant-' followed by a long opaque blob.
# Any non-placeholder realistic-looking key is a leak.
# We also catch other common secret shapes (sk-proj-, generic long base64).
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),   # real Anthropic key
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),  # project-scoped real key
    re.compile(r"AKIA[0-9A-Z]{16}"),             # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),         # GitHub PAT
]

# Only these placeholder values are acceptable on the RHS of ANTHROPIC_API_KEY=.
# Keep this list tight: adding real-looking values here defeats the guard.
ALLOWED_PLACEHOLDERS = {
    "",
    "your-api-key-here",
    "your_anthropic_api_key_here",
    "sk-ant-...",
    "sk-ant-xxxx",
    "<set-me>",
    "REPLACE_ME",
}

ENV_FILES = [
    "scripts/templates/env.example",
    ".env.example",
]


@pytest.mark.parametrize("env_rel", ENV_FILES)
def test_env_template_has_no_real_secrets(env_rel):
    root = repo_root()
    text = (root / env_rel).read_text(encoding="utf-8")
    for pat in SECRET_PATTERNS:
        m = pat.search(text)
        assert m is None, (
            f"{env_rel} contains a value matching {pat.pattern!r}: {m.group(0)!r}. "
            f"Env templates must contain ONLY placeholder values."
        )


def test_env_template_api_key_is_placeholder_only():
    """Specifically assert ANTHROPIC_API_KEY line has a known placeholder value."""
    root = repo_root()
    for env_rel in ENV_FILES:
        text = (root / env_rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("ANTHROPIC_API_KEY="):
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                assert value in ALLOWED_PLACEHOLDERS, (
                    f"{env_rel} ANTHROPIC_API_KEY={value!r} is not a recognized "
                    f"placeholder. Allowed: {sorted(ALLOWED_PLACEHOLDERS)}"
                )
