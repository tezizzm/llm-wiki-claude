"""Ingest pipeline: build wiki artifacts from files in ``raw/inbox``.

Every helper that needs a filesystem location receives a :class:`WorkspacePaths`
instance; no module-level path constants remain.  ``INGEST_SCHEMA_VERSION``
stays because it is a schema-version literal, not a path.

See ARCHITECTURE.md §5.3 / §6 for the contract; the per-helper signature
changes here are a direct translation of that section.  The ``main`` signature
``main(argv: list[str], workspace: WorkspacePaths) -> int`` matches the
workspace-aware dispatch contract used by every subcommand under
``DISPATCH`` in ``scripts.cli``.
"""

import argparse
import os
import re
import json
import hashlib
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fnmatch import fnmatch
from html.parser import HTMLParser

from dotenv import load_dotenv
from pydantic import ValidationError
from pypdf import PdfReader

from scripts.claude_api import (
    ClaudeCallResult,
    _append_event as _append_jsonl_event,
    _now_iso,
    build_client,
    call_claude,
)
from scripts.config_models import IngestSettingsConfig
from scripts.workspace import (
    WorkspacePaths,
    ensure_workspace_writable,
    resolve_ingest_settings,
    resolve_schema,
    resolve_wikiignore,
)

INGEST_SCHEMA_VERSION = 1


def _format_tokens(n: int) -> str:
    """Format ``n`` for the end-of-run summary line.

    - ``n >= 1000`` -> one-decimal K (e.g. ``'~12.3K'``, ``'~1.0K'``).
    - ``n < 1000``  -> the bare integer as a string (e.g. ``'850'``, ``'0'``).
    """

    if n >= 1000:
        return f"~{n / 1000:.1f}K"
    return str(n)

DEFAULT_INGEST_SETTINGS: Dict[str, Any] = {
    "schema_version": INGEST_SCHEMA_VERSION,
    "max_source_chars": 20000,
    "max_topics": 6,
    "max_entities": 6,
    "low_signal_sources": {
        "opaque_task_regex": r"^AM-[a-z0-9]{4}\.md$",
        "name_patterns": [
            "review_checklist",
            "review_for_claude",
            "claude_prompt",
            "_review_",
        ],
    },
    "topics": {
        "min_chars": 4,
        "max_words": 5,
        "blocked_suffixes": [
            " struct",
            " interface",
            " enum",
            " type",
            " function",
            " method",
            " variable",
            " component",
            " package",
            " file",
        ],
        "blocked_prefix_patterns": [
            r"\b(am-[a-z0-9]{4}|issue\d+)\b",
        ],
    },
    "entities": {
        "min_chars": 3,
        "max_words": 4,
        "allowlist": [
            "agentmesh",
            "claude",
            "dapr",
            "go",
            "kubernetes",
            "mcp",
            "python",
            "temporal",
        ],
        "blocked_suffixes": [
            " struct",
            " interface",
            " enum",
            " type",
            " function",
            " method",
            " variable",
            " component",
            " package",
            " file",
        ],
        "blocked_identifier_suffixes": [
            "Adapter",
            "Client",
            "Config",
            "Dispatcher",
            "Emitter",
            "Engine",
            "Event",
            "Handler",
            "Manager",
            "Pipeline",
            "Policy",
            "Registry",
            "Request",
            "Response",
            "Result",
            "Rule",
            "State",
            "Task",
            "Validator",
            "Worker",
        ],
        "blocked_prefix_patterns": [
            r"\b(am-[a-z0-9]{4}|issue\d+)\b",
        ],
        "blocked_word_fragments": [
            "persona",
            "stakeholder",
        ],
        "blocked_single_word_prefixes": [
            "exec",
            "task",
            "policy",
            "lease",
            "route",
        ],
    },
}

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s\-_/]", "", text)
    text = re.sub(r"[\s/_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-") or "untitled"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged

def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def prepare_ingest_settings(raw_config: Dict[str, Any], settings_path: Path) -> tuple[Dict[str, Any], List[str]]:
    payload = dict(raw_config)
    warnings: List[str] = []
    deprecated_version = payload.pop("config_version", None)
    schema_version = payload.get("schema_version")

    if deprecated_version is not None:
        warnings.append(
            f"{settings_path.name}: `config_version` is deprecated; use `schema_version`."
        )
        if schema_version is None:
            payload["schema_version"] = deprecated_version
            schema_version = deprecated_version
    if schema_version is None:
        payload["schema_version"] = INGEST_SCHEMA_VERSION
        warnings.append(
            f"{settings_path.name}: missing `schema_version`; assuming `{INGEST_SCHEMA_VERSION}` for backward compatibility."
        )
        schema_version = INGEST_SCHEMA_VERSION
    if int(schema_version) != INGEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported ingest settings schema_version `{schema_version}` in {settings_path}; expected `{INGEST_SCHEMA_VERSION}`."
        )
    return payload, warnings

def load_ingest_settings(workspace: WorkspacePaths) -> Dict[str, Any]:
    """Load ingest settings from ``workspace``, merging with defaults.

    Uses ``resolve_ingest_settings`` so the workspace-local copy wins over the
    repo-root fallback.  Returns a fully-validated settings dict with the
    low-signal regex pre-compiled under ``opaque_task_regex_compiled``.
    """

    path, _is_fallback = resolve_ingest_settings(workspace)
    data = load_json_file(path, {})
    prepared, _warnings = prepare_ingest_settings(data, path)
    merged = merge_dicts(DEFAULT_INGEST_SETTINGS, prepared)
    settings = IngestSettingsConfig.model_validate(merged).model_dump()
    settings["low_signal_sources"]["opaque_task_regex_compiled"] = re.compile(
        settings["low_signal_sources"]["opaque_task_regex"],
        re.IGNORECASE,
    )
    return settings

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def canonicalize_term(text: str) -> str:
    text = normalize_whitespace(text)
    text = text.strip(" -_./")
    return text

def is_low_signal_source(path: Path, settings: Dict[str, Any]) -> bool:
    name = path.name.lower()
    opaque_re = settings["low_signal_sources"]["opaque_task_regex_compiled"]
    if opaque_re.match(path.name):
        return True
    patterns = settings["low_signal_sources"]["name_patterns"]
    return any(pattern in name for pattern in patterns)

def frontmatter_value(text: str, key: str) -> Optional[str]:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    frontmatter = text[4:end]
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")

def normalize_terms(values: List[str]) -> List[str]:
    seen = set()
    cleaned = []
    for value in values:
        term = canonicalize_term(str(value))
        if not term:
            continue
        key = slugify(term)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
    return cleaned

def is_low_value_topic(topic: str, settings: Dict[str, Any]) -> bool:
    text = topic.strip()
    lowered = text.lower()
    topic_settings = settings["topics"]
    if not text or len(lowered) < topic_settings["min_chars"]:
        return True
    if "." in text or "/" in text or "_" in text:
        return True
    if any(re.search(pattern, lowered) for pattern in topic_settings["blocked_prefix_patterns"]):
        return True
    if len(text.split()) > topic_settings["max_words"]:
        return True
    if lowered.endswith(tuple(topic_settings["blocked_suffixes"])):
        return True
    return False

def is_low_value_entity(entity: str, settings: Dict[str, Any]) -> bool:
    text = entity.strip()
    lowered = text.lower()
    entity_settings = settings["entities"]
    if not text or len(lowered) < entity_settings["min_chars"]:
        return True
    if "." in text or "/" in text or "_" in text:
        return True
    if any(re.search(pattern, lowered) for pattern in entity_settings["blocked_prefix_patterns"]):
        return True
    if lowered.endswith(tuple(entity_settings["blocked_suffixes"])):
        return True
    if any(fragment in lowered for fragment in entity_settings["blocked_word_fragments"]):
        return True
    if re.fullmatch(r"[a-z0-9-]+", text) and "-" in text:
        return True
    if len(text.split()) > entity_settings["max_words"]:
        return True
    if " " not in text:
        if lowered in set(entity_settings["allowlist"]):
            return False
        if re.fullmatch(r"[a-z][a-z0-9]{11,}", text):
            return True
        if re.fullmatch(r"[A-Z][A-Za-z0-9]+", text) and any(
            text.endswith(suffix) for suffix in entity_settings["blocked_identifier_suffixes"]
        ):
            return True
        if lowered.startswith(tuple(entity_settings["blocked_single_word_prefixes"])):
            return True
    return False

def select_terms(values: List[str], max_items: int, reject_fn, settings: Dict[str, Any]) -> List[str]:
    cleaned = normalize_terms(values)
    selected = [value for value in cleaned if not reject_fn(value, settings)]
    return selected[:max_items]

def load_manifest(workspace: WorkspacePaths) -> Dict[str, Any]:
    if workspace.manifest_path.exists():
        return json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    return {"files": {}}

def save_manifest(workspace: WorkspacePaths, manifest: Dict[str, Any]) -> None:
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    workspace.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

def ensure_dirs(workspace: WorkspacePaths, include_wiki: bool = True) -> None:
    dirs = [workspace.raw_dir, workspace.state_dir]
    if include_wiki:
        dirs.extend([workspace.summaries_dir, workspace.topics_dir, workspace.entities_dir])
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    if include_wiki:
        if not workspace.index_path.exists():
            workspace.index_path.write_text("# Wiki Index\n\n", encoding="utf-8")
        if not workspace.log_path.exists():
            workspace.log_path.write_text("# Ingestion Log\n\n", encoding="utf-8")

def reset_derived_outputs(workspace: WorkspacePaths) -> None:
    for path in [workspace.summaries_dir, workspace.topics_dir, workspace.entities_dir]:
        if path.exists():
            shutil.rmtree(path)
    for file_path in [workspace.index_path, workspace.log_path, workspace.manifest_path]:
        if file_path.exists():
            file_path.unlink()
    ensure_dirs(workspace)

def load_ignore_patterns(workspace: WorkspacePaths) -> List[str]:
    """Return the wiki ignore patterns, honoring workspace + repo-root fallback.

    Uses ``resolve_wikiignore`` so the workspace-local ``.wikiignore`` wins and
    falls back to the repo-root template when missing.  When neither file
    exists, a defensive built-in list is returned so dotfiles and OS cruft are
    still filtered.
    """

    try:
        path, _is_fallback = resolve_wikiignore(workspace)
    except FileNotFoundError:
        return [
            ".DS_Store",
            "._*",
            ".*",
            "*.tmp",
            "*.swp",
            "*.bak",
            "Thumbs.db",
        ]

    patterns: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns

def should_ignore_file(workspace: WorkspacePaths, path: Path, ignore_patterns: List[str]) -> bool:
    if not path.is_file():
        return True

    name = path.name
    rel_path = path.relative_to(workspace.root).as_posix()

    for pattern in ignore_patterns:
        if fnmatch(name, pattern) or fnmatch(rel_path, pattern):
            return True

    return False

def has_digest_been_seen(manifest: Dict[str, Any], digest: str, exclude_rel_path: Optional[str] = None) -> bool:
    for rel_path, record in manifest.get("files", {}).items():
        if exclude_rel_path is not None and rel_path == exclude_rel_path:
            continue
        if record.get("sha256") == digest:
            return True
    return False

def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")

class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        return " ".join(self._text).strip()


def extract_html_text(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1", errors="ignore")
    parser = _HTMLTextExtractor()
    parser.feed(content)
    text = parser.get_text()
    if text:
        return text
    return f"[HTML parsed but no extractable text found: {path.name}]"


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n".join(pages).strip()
    if text:
        return text
    return f"[PDF parsed but no extractable text found: {path.name}]"

def extract_rst_text(path: Path) -> str:
    try:
        from docutils.core import publish_doctree
    except ImportError:
        return "[RST support requires docutils: pip install docutils]"
    raw = read_text_file(path)
    try:
        doctree = publish_doctree(raw,
                                  settings_overrides={'report_level': 5, 'halt_level': 5})
        text = doctree.astext().strip()
    except Exception:
        text = raw  # fallback to raw text
    if not text:
        return f"[RST parsed but no extractable text found: {path.name}]"
    return text


def extract_docx_text(path: Path) -> str:
    try:
        from docx import Document
    except ImportError:
        return "[DOCX support requires python-docx: pip install python-docx]"
    try:
        doc = Document(str(path))
    except Exception as exc:
        return f"[DOCX parsing failed for {path.name}: {exc}]"
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs).strip()
    if not text:
        return f"[DOCX parsed but no extractable text found: {path.name}]"
    return text


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv"}:
        return read_text_file(path)
    if ext == ".pdf":
        return extract_pdf_text(path)
    if ext in {".html", ".htm"}:
        return extract_html_text(path)
    if ext == ".rst":
        return extract_rst_text(path)
    if ext == ".docx":
        return extract_docx_text(path)
    return f"[Unsupported file type for direct parsing: {path.name}]"

def init_client() -> tuple[Any, str]:
    """Construct the Anthropic client plus resolve the ingest model name.

    The client is typed as ``Any`` because ``scripts.ingest`` MUST NOT import
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
    model = os.getenv("ANTHROPIC_INGEST_MODEL", "claude-haiku-4-5")
    client = build_client(api_key)
    return client, model

def get_schema_text(workspace: WorkspacePaths) -> str:
    """Read the schemas/AGENTS.md text, falling back through ``resolve_schema``.

    Returns ``""`` when neither the workspace nor the repo-root copy exists;
    downstream code tolerates an empty schema string (ingest will still call
    the model, just without rule text in the system prompt).
    """

    try:
        path, _is_fallback = resolve_schema(workspace)
    except FileNotFoundError:
        return ""
    return path.read_text(encoding="utf-8")

def call_claude_json(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    workspace: WorkspacePaths,
    totals: Dict[str, int],
    context: str,
) -> Dict[str, Any]:
    """Call Claude for a single source and parse a JSON object from the reply.

    Routes through :func:`scripts.claude_api.call_claude` (the sole SDK call
    site in the project) so every request emits a ``claude_api_call`` event
    and its token usage is accumulated into ``totals`` for the end-of-run
    ``run_summary``.
    """

    result: ClaudeCallResult = call_claude(
        client=client,
        model=model,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4000,
        context=context,
        workspace=workspace,
        log_event=True,
    )
    totals["input"] += result.input_tokens
    totals["output"] += result.output_tokens
    totals["calls"] += 1

    text = result.text.strip()

    # Extract the outermost JSON object, tolerating any surrounding prose or fences
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    else:
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    return json.loads(text)

def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def append_event(workspace: WorkspacePaths, event_type: str, **fields: Any) -> None:
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat() + "Z",
        "event": event_type,
        **fields,
    }
    with workspace.ingest_events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

def append_log(workspace: WorkspacePaths, message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    with workspace.log_path.open("a", encoding="utf-8") as f:
        f.write(f"- {timestamp} {message}\n")

def extract_heading(content: str) -> str:
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if not match:
        return ""
    return canonicalize_term(match.group(1))

def parse_contribution_blocks(text: str) -> List[Dict[str, str]]:
    pattern = re.compile(
        r"<!-- SOURCE:(?P<source>[^\n]+) -->\n(?P<content>.*?)\n<!-- /SOURCE:(?P=source) -->",
        re.DOTALL,
    )
    blocks = []
    for match in pattern.finditer(text):
        blocks.append(
            {
                "source_file": match.group("source"),
                "content": match.group("content").strip(),
            }
        )
    return blocks

def normalize_contribution_file(path: Path) -> bool:
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    blocks = parse_contribution_blocks(existing)
    if not blocks:
        return False
    deduped: Dict[str, str] = {}
    for block in blocks:
        deduped[block["source_file"]] = block["content"]
    normalized = "\n\n---\n\n".join(
        render_contribution_block(source_file, content)
        for source_file, content in sorted(deduped.items())
    ) + "\n"
    if normalized == existing:
        return False
    write_markdown(path, normalized)
    return True

def canonical_page_slug(slug: str) -> str:
    if slug.endswith("ies") and len(slug) > 4:
        return slug[:-3] + "y"
    if slug.endswith("s") and not slug.endswith("ss") and len(slug) > 4:
        return slug[:-1]
    return slug

def count_contributions(path: Path) -> int:
    if not path.exists():
        return 0
    return len(parse_contribution_blocks(path.read_text(encoding="utf-8")))

def collect_page_stats(workspace: WorkspacePaths) -> Dict[str, Any]:
    topic_files = sorted(workspace.topics_dir.glob("*.md"))
    entity_files = sorted(workspace.entities_dir.glob("*.md"))
    return {
        "summaries": len(list(workspace.summaries_dir.glob("*.md"))),
        "topics": len(topic_files),
        "entities": len(entity_files),
        "topic_contributions": sum(count_contributions(path) for path in topic_files),
        "entity_contributions": sum(count_contributions(path) for path in entity_files),
    }

def refine_merged_pages(workspace: WorkspacePaths, settings: Dict[str, Any]) -> Dict[str, int]:
    actions = {
        "topic_alias_merges": 0,
        "entity_alias_merges": 0,
        "topic_normalizations": 0,
        "entity_normalizations": 0,
        "topic_low_value_prunes": 0,
        "entity_low_value_prunes": 0,
    }
    specs = [
        ("topic", workspace.topics_dir, is_low_value_topic, "topic_alias_merges", "topic_normalizations", "topic_low_value_prunes"),
        ("entity", workspace.entities_dir, is_low_value_entity, "entity_alias_merges", "entity_normalizations", "entity_low_value_prunes"),
    ]
    for page_kind, directory, reject_fn, merge_key, normalize_key, prune_key in specs:
        for path in sorted(directory.glob("*.md")):
            if normalize_contribution_file(path):
                actions[normalize_key] += 1
                append_event(workspace, "page_normalized", page_kind=page_kind, slug=path.stem)

        for path in sorted(directory.glob("*.md")):
            blocks = parse_contribution_blocks(path.read_text(encoding="utf-8"))
            if not blocks:
                continue
            title = extract_heading(blocks[0]["content"]) or path.stem.replace("-", " ").title()
            if reject_fn(title, settings):
                path.unlink()
                actions[prune_key] += 1
                append_event(workspace, "page_pruned_low_value", page_kind=page_kind, slug=path.stem, title=title)
                continue

            canonical_slug = canonical_page_slug(path.stem)
            if canonical_slug == path.stem:
                continue

            target_path = directory / f"{canonical_slug}.md"
            for block in blocks:
                upsert_source_contribution(target_path, block["source_file"], block["content"])
            path.unlink()
            actions[merge_key] += 1
            append_event(
                workspace,
                "page_alias_merged",
                page_kind=page_kind,
                from_slug=path.stem,
                to_slug=canonical_slug,
                contributions=len(blocks),
            )
            normalize_contribution_file(target_path)
    return actions

def update_index(workspace: WorkspacePaths) -> None:
    sections = ["# Wiki Index", ""]
    for label, folder in [
        ("Summaries", workspace.summaries_dir),
        ("Topics", workspace.topics_dir),
        ("Entities", workspace.entities_dir),
    ]:
        sections.append(f"## {label}")
        files = sorted(folder.glob("*.md"))
        if not files:
            sections.append("")
            sections.append("_None yet._")
            sections.append("")
            continue
        for fp in files:
            rel = fp.relative_to(workspace.root).as_posix()
            title = fp.stem.replace("-", " ").title()
            sections.append(f"- [{title}]({rel})")
        sections.append("")
    workspace.index_path.write_text("\n".join(sections), encoding="utf-8")

def build_summary_markdown(
    title: str,
    source_file: str,
    summary: str,
    key_facts: List[str],
    topics: List[str],
    entities: List[str],
    open_questions: List[str],
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topics_links = "\n".join([f"- [[{slugify(t)}]]" for t in topics]) or "- None"
    entity_links = "\n".join([f"- [[{slugify(e)}]]" for e in entities]) or "- None"
    facts = "\n".join([f"- {x}" for x in key_facts]) or "- None"
    questions = "\n".join([f"- {x}" for x in open_questions]) or "- None"

    return f"""# {title}

**Last updated:** {today}  
**Source file:** `{source_file}`

## Summary
{summary}

## Key facts
{facts}

## Topics
{topics_links}

## Entities
{entity_links}

## Open questions
{questions}
"""

def build_topic_markdown(
    topic: str,
    source_file: str,
    summary: str,
    related_entities: List[str],
    related_topics: List[str],
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ent = "\n".join([f"- [[{slugify(e)}]]" for e in related_entities]) or "- None"
    rel = "\n".join(
        [f"- [[{slugify(t)}]]" for t in related_topics if slugify(t) != slugify(topic)]
    ) or "- None"

    return f"""# {topic}

**Last updated:** {today}  
**Source contribution:** `{source_file}`

## Summary
{summary}

## Related entities
{ent}

## Related topics
{rel}
"""

def build_entity_markdown(
    entity: str,
    source_file: str,
    summary: str,
    related_topics: List[str],
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rel = "\n".join([f"- [[{slugify(t)}]]" for t in related_topics]) or "- None"

    return f"""# {entity}

**Last updated:** {today}  
**Source contribution:** `{source_file}`

## Summary
{summary}

## Related topics
{rel}
"""

def contribution_start_marker(source_file: str) -> str:
    return f"<!-- SOURCE:{source_file} -->"

def contribution_end_marker(source_file: str) -> str:
    return f"<!-- /SOURCE:{source_file} -->"

def render_contribution_block(source_file: str, content: str) -> str:
    return (
        f"{contribution_start_marker(source_file)}\n"
        f"{content.rstrip()}\n"
        f"{contribution_end_marker(source_file)}"
    )

def upsert_source_contribution(path: Path, source_file: str, content: str) -> None:
    block = render_contribution_block(source_file, content)
    if not path.exists():
        write_markdown(path, block + "\n")
        return

    existing = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(contribution_start_marker(source_file))}\n.*?\n{re.escape(contribution_end_marker(source_file))}",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(block, existing).strip() + "\n"
    else:
        updated = existing.rstrip() + "\n\n---\n\n" + block + "\n"
    write_markdown(path, updated)

def remove_source_contribution(path: Path, source_file: str) -> bool:
    if not path.exists():
        return False

    existing = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?:\n\n---\n\n)?{re.escape(contribution_start_marker(source_file))}\n.*?\n{re.escape(contribution_end_marker(source_file))}\n?",
        re.DOTALL,
    )
    updated, count = pattern.subn("", existing)
    if count == 0:
        return False

    cleaned = updated.strip()
    if cleaned:
        write_markdown(path, cleaned + "\n")
    else:
        path.unlink()
    return True

def cleanup_source_artifacts(workspace: WorkspacePaths, source_name: str, record: Dict[str, Any]) -> Dict[str, int]:
    removed = {"summaries": 0, "topics": 0, "entities": 0}

    summary_rel_path = record.get("summary_path")
    if summary_rel_path:
        summary_path = workspace.root / summary_rel_path
    else:
        summary_path = workspace.summaries_dir / f"{slugify(Path(source_name).stem)}.md"
    if summary_path.exists():
        summary_path.unlink()
        removed["summaries"] += 1

    for slug in record.get("topic_slugs", []):
        if remove_source_contribution(workspace.topics_dir / f"{slug}.md", source_name):
            removed["topics"] += 1

    for slug in record.get("entity_slugs", []):
        if remove_source_contribution(workspace.entities_dir / f"{slug}.md", source_name):
            removed["entities"] += 1

    return removed

def ingest_file(
    workspace: WorkspacePaths,
    client: Any,
    model: str,
    path: Path,
    settings: Dict[str, Any],
    previous_record: Optional[Dict[str, Any]] = None,
    *,
    totals: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    raw_text = extract_text(path)[: settings["max_source_chars"]]
    schema_text = get_schema_text(workspace)
    source_title = frontmatter_value(raw_text, "title")
    max_topics = settings["max_topics"]
    max_entities = settings["max_entities"]

    system_prompt = f"""You are an LLM wiki compiler.
Follow these rules:

{schema_text}

Return strict JSON only, with no markdown fence and no commentary.

Required JSON keys:
- title
- summary
- key_facts
- topics
- entities
- open_questions
- topic_summaries
- entity_summaries

Rules:
- key_facts/topics/entities/open_questions must be arrays of strings
- topic_summaries must be an object mapping topic -> short summary
- entity_summaries must be an object mapping entity -> short summary
- Be concise and grounded in the source
- Prefer durable product concepts over workflow bookkeeping or code symbols
- Return at most {max_topics} topics and at most {max_entities} entities
- Do not emit issue IDs, task IDs, filenames, test names, structs, interfaces, methods, packages, or env vars as topics/entities
"""

    user_prompt = f"""Source file: {path.name}

Source text:
{raw_text}
"""

    # When callers do not supply a totals accumulator (e.g. direct unit tests of
    # ingest_file), use a throwaway local so call_claude_json's unconditional
    # update is safe.  The end-of-run run_summary path in main() always supplies
    # a real accumulator.
    local_totals = totals if totals is not None else {"input": 0, "output": 0, "calls": 0}
    data = call_claude_json(
        client,
        model,
        system_prompt,
        user_prompt,
        workspace=workspace,
        totals=local_totals,
        context=path.name,
    )

    title = source_title or data.get("title") or path.stem
    summary = data.get("summary", "")
    key_facts = data.get("key_facts", [])
    topics = select_terms(data.get("topics", []), max_topics, is_low_value_topic, settings)
    entities = select_terms(data.get("entities", []), max_entities, is_low_value_entity, settings)
    open_questions = data.get("open_questions", [])
    raw_topic_summaries = data.get("topic_summaries", {})
    raw_entity_summaries = data.get("entity_summaries", {})
    topic_summaries = {
        canonicalize_term(k): v
        for k, v in raw_topic_summaries.items()
        if canonicalize_term(k) in topics
    }
    entity_summaries = {
        canonicalize_term(k): v
        for k, v in raw_entity_summaries.items()
        if canonicalize_term(k) in entities
    }

    summary_path = workspace.summaries_dir / f"{slugify(path.stem)}.md"
    summary_md = build_summary_markdown(
        title=title,
        source_file=path.name,
        summary=summary,
        key_facts=key_facts,
        topics=topics,
        entities=entities,
        open_questions=open_questions,
    )
    write_markdown(summary_path, summary_md)

    previous_topic_slugs = set((previous_record or {}).get("topic_slugs", []))
    previous_entity_slugs = set((previous_record or {}).get("entity_slugs", []))
    current_topic_slugs = {slugify(topic) for topic in topics}
    current_entity_slugs = {slugify(entity) for entity in entities}

    for stale_slug in sorted(previous_topic_slugs - current_topic_slugs):
        remove_source_contribution(workspace.topics_dir / f"{stale_slug}.md", path.name)

    for stale_slug in sorted(previous_entity_slugs - current_entity_slugs):
        remove_source_contribution(workspace.entities_dir / f"{stale_slug}.md", path.name)

    for topic in topics:
        topic_path = workspace.topics_dir / f"{slugify(topic)}.md"
        topic_md = build_topic_markdown(
            topic=topic,
            source_file=path.name,
            summary=topic_summaries.get(topic, f"Topic derived from {path.name}."),
            related_entities=entities,
            related_topics=topics,
        )
        upsert_source_contribution(topic_path, path.name, topic_md)

    for entity in entities:
        entity_path = workspace.entities_dir / f"{slugify(entity)}.md"
        entity_md = build_entity_markdown(
            entity=entity,
            source_file=path.name,
            summary=entity_summaries.get(entity, f"Entity derived from {path.name}."),
            related_topics=topics,
        )
        upsert_source_contribution(entity_path, path.name, entity_md)

    append_log(workspace, f'Ingested `{path.name}` -> summary `{summary_path.relative_to(workspace.root).as_posix()}`')
    append_event(
        workspace,
        "ingest_file_completed",
        source_file=path.name,
        summary_path=summary_path.relative_to(workspace.root).as_posix(),
        topics=sorted(current_topic_slugs),
        entities=sorted(current_entity_slugs),
    )
    return {
        "summary_path": summary_path.relative_to(workspace.root).as_posix(),
        "topic_slugs": sorted(current_topic_slugs),
        "entity_slugs": sorted(current_entity_slugs),
    }

def save_last_ingest_run(workspace: WorkspacePaths, summary: Dict[str, Any]) -> None:
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    workspace.last_ingest_run_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

def write_last_ingest_report(workspace: WorkspacePaths, summary: Dict[str, Any]) -> None:
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    cleaned_stale = summary.get("cleaned_stale", {})
    merge_cleanup = summary.get("merge_cleanup", {})
    page_stats = summary.get("page_stats", {})
    processed_files = summary.get("processed_files", [])
    report = "\n".join(
        [
            "# Last Ingest Report",
            "",
            f"- Status: `{summary.get('status', 'unknown')}`",
            f"- Ran at (UTC): `{summary.get('ran_at_utc', 'unknown')}`",
            f"- Model: `{summary.get('model', 'n/a')}`",
            f"- Raw candidates: `{summary.get('raw_candidates', 0)}`",
            f"- Processed: `{summary.get('processed', 0)}`",
            f"- Reconciled: `{summary.get('reconciled', False)}`",
            "",
            "## Cleanup",
            f"- Stale manifest entries cleaned: `{cleaned_stale.get('manifest_entries', 0)}`",
            f"- Topic alias merges: `{merge_cleanup.get('topic_alias_merges', 0)}`",
            f"- Entity alias merges: `{merge_cleanup.get('entity_alias_merges', 0)}`",
            f"- Topic low-value prunes: `{merge_cleanup.get('topic_low_value_prunes', 0)}`",
            f"- Entity low-value prunes: `{merge_cleanup.get('entity_low_value_prunes', 0)}`",
            "",
            "## Page Stats",
            f"- Summary pages: `{page_stats.get('summaries', 0)}`",
            f"- Topic pages: `{page_stats.get('topics', 0)}`",
            f"- Entity pages: `{page_stats.get('entities', 0)}`",
            f"- Topic contributions: `{page_stats.get('topic_contributions', 0)}`",
            f"- Entity contributions: `{page_stats.get('entity_contributions', 0)}`",
            "",
            "## Processed Files",
        ]
    )
    processed_lines = [f"- `{name}`" for name in processed_files] or ["- None"]
    error_lines = [f"- `{name}`" for name in summary.get("errors", [])] or ["- None"]
    report += "\n" + "\n".join(processed_lines) + "\n\n## Errors\n" + "\n".join(error_lines) + "\n"
    workspace.ingest_report_path.write_text(report, encoding="utf-8")

def collect_raw_candidates(workspace: WorkspacePaths, ignore_patterns: List[str]) -> List[Path]:
    if not workspace.raw_dir.exists():
        return []
    return sorted([p for p in workspace.raw_dir.iterdir() if not should_ignore_file(workspace, p, ignore_patterns)])

def analyze_candidates(workspace: WorkspacePaths, files: List[Path], manifest: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    actions = {
        "to_ingest": [],
        "skipped_low_signal": [],
        "skipped_unchanged": [],
        "skipped_duplicates": [],
    }
    for path in files:
        if is_low_signal_source(path, settings):
            actions["skipped_low_signal"].append(path.name)
            continue
        digest = sha256_file(path)
        rel_path = str(path.relative_to(workspace.root))
        old = manifest["files"].get(rel_path)
        if old and old.get("sha256") == digest:
            actions["skipped_unchanged"].append(path.name)
            continue
        if not old and has_digest_been_seen(manifest, digest):
            actions["skipped_duplicates"].append(path.name)
            continue
        actions["to_ingest"].append(path)
    return actions

def _emit_run_summary(
    workspace: WorkspacePaths,
    model: str,
    totals: Dict[str, int],
) -> None:
    """Append the end-of-run ``run_summary`` event AND print the summary line.

    Per ARCHITECTURE §10.4 and §10.5 the two side effects are paired: readers
    treat the presence of a ``run_summary`` event in
    ``state/ingest_events.jsonl`` as proof the run completed, and the stdout
    summary line is the user-facing echo of the same counts.  Both are emitted
    only on the success path (inside ``main()``'s try block, just before
    ``return 0``); if any exception propagates out before this helper runs, the
    run_summary event and summary line are both absent -- which is the
    atomicity contract the story requires.
    """

    _append_jsonl_event(
        workspace.ingest_events_path,
        {
            "event": "run_summary",
            "ts": _now_iso(),
            "workspace": str(workspace.root),
            "model": model,
            "total_input_tokens": totals["input"],
            "total_output_tokens": totals["output"],
            "api_call_count": totals["calls"],
        },
    )
    print(
        f"Used {_format_tokens(totals['input'])} input "
        f"/ {_format_tokens(totals['output'])} output tokens this run."
    )


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """Ingest entry point -- workspace-aware dispatch signature.

    Matches the :data:`scripts.cli.DISPATCH` contract
    ``fn(argv, workspace) -> int``.  Returns ``0`` on a successful run
    (including ``--dry-run``, ``no input``, and setup/configuration-level
    failures that the CLI reports but does not want to propagate).  An
    exception that escapes the outer ``try`` is re-raised (and therefore the
    ``run_summary`` event / summary line are NOT emitted, per the atomicity
    contract in ARCHITECTURE §10.4).

    Atomicity invariant: the ``run_summary`` event appears in
    ``workspace.ingest_events_path`` IF AND ONLY IF this function returns 0.
    """

    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Build wiki artifacts from files in raw/inbox.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what ingest would do without calling the model or writing files.")
    parser.add_argument("--reconcile", action="store_true", help="Reset derived wiki artifacts before ingesting current raw sources.")
    args = parser.parse_args(argv)

    ensure_workspace_writable(workspace)

    # Resolve the model id up-front so every success path (including the
    # setup-error branches that return 0) can emit a run_summary with a real
    # model field.  If ANTHROPIC_INGEST_MODEL is unset we fall back to the
    # documented default.
    ingest_model = os.getenv("ANTHROPIC_INGEST_MODEL", "claude-haiku-4-5")
    totals: Dict[str, int] = {"input": 0, "output": 0, "calls": 0}

    try:
        compatibility_warnings: List[str] = []
        settings_path: Optional[Path] = None
        try:
            settings_path, _is_fallback = resolve_ingest_settings(workspace)
            raw_settings = load_json_file(settings_path, {})
            prepared_settings, compatibility_warnings = prepare_ingest_settings(raw_settings, settings_path)
            merged_settings = merge_dicts(DEFAULT_INGEST_SETTINGS, prepared_settings)
            settings = IngestSettingsConfig.model_validate(merged_settings).model_dump()
            settings["low_signal_sources"]["opaque_task_regex_compiled"] = re.compile(
                settings["low_signal_sources"]["opaque_task_regex"],
                re.IGNORECASE,
            )
            ensure_dirs(workspace, include_wiki=not args.dry_run)
            manifest = load_manifest(workspace)
            ignore_patterns = load_ignore_patterns(workspace)
            client = None
            model = ingest_model
            if not args.dry_run:
                client, model = init_client()
                ingest_model = model
        except FileNotFoundError as exc:
            print(f"Setup error: missing required file: {exc}")
            _emit_run_summary(workspace, ingest_model, totals)
            return 0
        except json.JSONDecodeError:
            print(f"Configuration error: invalid JSON in {settings_path}")
            _emit_run_summary(workspace, ingest_model, totals)
            return 0
        except ValidationError as exc:
            print(f"Configuration error in {settings_path}:")
            for error in exc.errors():
                field = ".".join(str(part) for part in error["loc"])
                print(f"- {field}: {error['msg']}")
            _emit_run_summary(workspace, ingest_model, totals)
            return 0
        except RuntimeError as exc:
            print(f"Configuration error: {exc}")
            _emit_run_summary(workspace, ingest_model, totals)
            return 0

        for warning in compatibility_warnings:
            print(f"Compatibility warning: {warning}")

        files = collect_raw_candidates(workspace, ignore_patterns)
        current_rel_paths = {str(path.relative_to(workspace.root)) for path in files}
        stale_manifest_entries = sorted(set(manifest.get("files", {}).keys()) - current_rel_paths)
        actions = analyze_candidates(workspace, files, manifest, settings)
        reconciled = False
        cleaned_stale = {"summaries": 0, "topics": 0, "entities": 0, "manifest_entries": 0}

        if args.reconcile:
            print("Reconciling derived wiki artifacts from current raw sources.")
            reset_derived_outputs(workspace)
            manifest = load_manifest(workspace)
            actions = analyze_candidates(workspace, files, manifest, settings)
            reconciled = True
        elif stale_manifest_entries and not args.dry_run:
            for rel_path in stale_manifest_entries:
                record = manifest["files"].get(rel_path, {})
                source_name = Path(rel_path).name
                removed = cleanup_source_artifacts(workspace, source_name, record)
                for key, value in removed.items():
                    cleaned_stale[key] += value
                manifest["files"].pop(rel_path, None)
                cleaned_stale["manifest_entries"] += 1
                append_event(
                    workspace,
                    "stale_source_cleaned",
                    source_file=source_name,
                    removed=removed,
                )
            save_manifest(workspace, manifest)
            update_index(workspace)

        if not files:
            print("No files found in raw/inbox/")
            save_last_ingest_run(
                workspace,
                {
                    "ran_at_utc": datetime.now(timezone.utc).isoformat() + "Z",
                    "status": "no_input",
                    "raw_candidates": 0,
                    "processed": 0,
                    "reconciled": reconciled,
                    "cleaned_stale": cleaned_stale,
                },
            )
            _emit_run_summary(workspace, ingest_model, totals)
            return 0

        if args.dry_run:
            dry_run_report = {
                "ran_at_utc": datetime.now(timezone.utc).isoformat() + "Z",
                "status": "dry_run",
                "settings_path": str(settings_path),
                "raw_candidates": len(files),
                "stale_manifest_entries": stale_manifest_entries,
                "would_reconcile": args.reconcile,
                "would_cleanup_stale": stale_manifest_entries,
                "would_process": [path.name for path in actions["to_ingest"]],
                "skipped_low_signal": actions["skipped_low_signal"],
                "skipped_unchanged": actions["skipped_unchanged"],
                "skipped_duplicates": actions["skipped_duplicates"],
            }
            save_last_ingest_run(workspace, dry_run_report)
            write_last_ingest_report(workspace, dry_run_report)
            print("Run summary:")
            print(json.dumps(dry_run_report, indent=2))
            _emit_run_summary(workspace, ingest_model, totals)
            return 0

        processed = 0
        processed_files = []
        errors = []

        for path in actions["to_ingest"]:
            digest = sha256_file(path)
            rel_path = str(path.relative_to(workspace.root))
            old_record = manifest["files"].get(rel_path, {})
            print(f"Ingesting with model {model}: {path.name}")
            append_event(workspace, "ingest_file_started", source_file=path.name, model=model)
            try:
                ingest_result = ingest_file(
                    workspace,
                    client,
                    model,
                    path,
                    settings,
                    previous_record=old_record,
                    totals=totals,
                )
            except Exception as e:
                error_message = f"{path.name}: {e}"
                print(f"ERROR ingesting {error_message}")
                append_log(workspace, f"ERROR ingesting `{path.name}`: {e}")
                append_event(workspace, "ingest_file_failed", source_file=path.name, error=str(e))
                errors.append(error_message)
                continue
            manifest["files"][rel_path] = {
                "sha256": digest,
                "last_ingested_utc": datetime.now(timezone.utc).isoformat() + "Z",
                "model": model,
                **ingest_result,
            }
            save_manifest(workspace, manifest)
            processed += 1
            processed_files.append(path.name)

        merge_cleanup = refine_merged_pages(workspace, settings)
        update_index(workspace)
        page_stats = collect_page_stats(workspace)
        run_report = {
            "ran_at_utc": datetime.now(timezone.utc).isoformat() + "Z",
            "status": "completed_with_errors" if errors else "completed",
            "model": model,
            "settings_path": str(settings_path),
            "raw_candidates": len(files),
            "reconciled": reconciled,
            "cleaned_stale": cleaned_stale,
            "merge_cleanup": merge_cleanup,
            "stale_manifest_entries": stale_manifest_entries,
            "processed": processed,
            "processed_files": processed_files,
            "skipped_low_signal": len(actions["skipped_low_signal"]),
            "skipped_unchanged": len(actions["skipped_unchanged"]),
            "skipped_duplicates": len(actions["skipped_duplicates"]),
            "page_stats": page_stats,
            "errors": errors,
        }
        save_last_ingest_run(workspace, run_report)
        write_last_ingest_report(workspace, run_report)
        print("Run summary:")
        print(json.dumps(run_report, indent=2))
        # run_summary event + summary line must be the LAST things to happen
        # inside this try block.  On any exception before this point, the
        # event is not written and the summary line is not printed.
        _emit_run_summary(workspace, model, totals)
        return 0
    except Exception:
        # Re-raise so the caller sees the failure AND so run_summary stays
        # absent from ingest_events.jsonl (atomicity invariant).
        raise
