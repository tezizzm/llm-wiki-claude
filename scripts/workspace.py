"""Workspace path construction for the LLM wiki starter.

This module owns the single source of truth for workspace path layout. Every
command-line entry point (sync, ingest, query, lint, doctor) consumes a
``WorkspacePaths`` instance instead of deriving its own ``ROOT`` / derived
constants from ``__file__``.

See ARCHITECTURE.md §3 for the full contract. This file intentionally stays
small and pure: no I/O, no filesystem validation. Path existence and
validation are the job of the resolver built in a later story.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WorkspaceSource = Literal["flag", "env", "default"]


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Frozen, absolute, resolved path layout for a workspace.

    All ``Path`` fields are expected to be absolute and resolved
    (``Path.resolve()``) at construction time. This dataclass is pure data;
    it performs no validation or filesystem access on its own.

    The field order below matches ARCHITECTURE §3.1 and MUST NOT change; the
    resolver and downstream consumers depend on the declared order.
    """

    root: Path
    source: WorkspaceSource
    repo_root: Path
    raw_dir: Path                           # root / 'raw' / 'inbox'
    wiki_dir: Path                          # root / 'wiki'
    summaries_dir: Path                     # wiki_dir / 'summaries'
    topics_dir: Path                        # wiki_dir / 'topics'
    entities_dir: Path                      # wiki_dir / 'entities'
    state_dir: Path                         # root / 'state'
    manifest_path: Path                     # state_dir / 'manifest.json'
    sync_manifest_path: Path                # state_dir / 'sync_manifest.json'
    last_ingest_run_path: Path              # state_dir / 'last_ingest_run.json'
    ingest_events_path: Path                # state_dir / 'ingest_events.jsonl'
    ingest_report_path: Path                # state_dir / 'last_ingest_report.md'
    index_path: Path                        # root / 'index.md'
    log_path: Path                          # root / 'log.md'
    env_path: Path                          # root / '.env'
    sync_config_path: Path                  # root / 'sync-sources.local.json'
    sync_fallback_config_path: Path         # repo_root / 'sync-sources.json'
    ingest_settings_path: Path              # root / 'ingest-settings.local.json'
    ingest_fallback_settings_path: Path     # repo_root / 'ingest-settings.json'
    schema_path: Path                       # root / 'schemas' / 'AGENTS.md'
    schema_fallback_path: Path              # repo_root / 'schemas' / 'AGENTS.md'
    wikiignore_path: Path                   # root / '.wikiignore'
    wikiignore_fallback_path: Path          # repo_root / '.wikiignore'
    env_fallback_path: Path                 # repo_root / '.env'


def repo_root() -> Path:
    """Return the absolute path to the repository root.

    This consolidates the ``Path(__file__).resolve().parents[1]`` idiom that
    currently lives in every script module. Returned path is absolute and
    resolved.
    """

    return Path(__file__).resolve().parents[1]


class WorkspaceError(Exception):
    """Base class for workspace resolution / validation errors."""


class WorkspaceNotFoundError(WorkspaceError):
    """``--workspace`` or env var points at a non-existent path."""

    def __init__(self, path: Path):
        super().__init__(str(path))
        self.path = path


class WorkspaceInvalidError(WorkspaceError):
    """Path exists but is not a directory, or is missing required subdirs."""


def _build_workspace_paths(root: Path, source: WorkspaceSource) -> WorkspacePaths:
    """Construct a ``WorkspacePaths`` from a resolved ``root`` directory.

    ``root`` must already be an absolute, normalized ``Path``. This helper
    performs no filesystem validation; it only fills in every derived field
    per ARCHITECTURE §3.1.
    """

    repo = repo_root()
    wiki_dir = root / "wiki"
    state_dir = root / "state"
    return WorkspacePaths(
        root=root,
        source=source,
        repo_root=repo,
        raw_dir=root / "raw" / "inbox",
        wiki_dir=wiki_dir,
        summaries_dir=wiki_dir / "summaries",
        topics_dir=wiki_dir / "topics",
        entities_dir=wiki_dir / "entities",
        state_dir=state_dir,
        manifest_path=state_dir / "manifest.json",
        sync_manifest_path=state_dir / "sync_manifest.json",
        last_ingest_run_path=state_dir / "last_ingest_run.json",
        ingest_events_path=state_dir / "ingest_events.jsonl",
        ingest_report_path=state_dir / "last_ingest_report.md",
        index_path=root / "index.md",
        log_path=root / "log.md",
        env_path=root / ".env",
        sync_config_path=root / "sync-sources.local.json",
        sync_fallback_config_path=repo / "sync-sources.json",
        ingest_settings_path=root / "ingest-settings.local.json",
        ingest_fallback_settings_path=repo / "ingest-settings.json",
        schema_path=root / "schemas" / "AGENTS.md",
        schema_fallback_path=repo / "schemas" / "AGENTS.md",
        wikiignore_path=root / ".wikiignore",
        wikiignore_fallback_path=repo / ".wikiignore",
        env_fallback_path=repo / ".env",
    )


def _resolve_against_cwd(raw: Path, cwd: Path | None) -> Path:
    """Resolve ``raw`` against ``cwd`` (defaulting to ``Path.cwd()``) if relative.

    Always calls ``.resolve(strict=False)`` so '..' segments are collapsed
    and the returned path is absolute. ``strict=False`` means the path does
    not need to exist on disk.
    """

    base = cwd if cwd is not None else Path.cwd()
    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (base / raw).resolve(strict=False)


def resolve_workspace(
    path_arg: str | None,
    env_var: str | None,
    cwd: Path | None = None,
) -> WorkspacePaths:
    """Apply precedence --workspace flag > env var > repo-root default.

    - If ``path_arg`` is non-empty, ``source='flag'`` and root derives from it.
    - Else if ``env_var`` is non-empty, ``source='env'`` and root derives from it.
    - Else ``source='default'`` and root is ``repo_root()``.

    Relative paths are resolved against ``cwd`` (or ``Path.cwd()`` if None).
    All returned ``WorkspacePaths`` fields are absolute and normalized.

    Raises ``WorkspaceNotFoundError`` when ``source`` is ``'flag'`` or ``'env'``
    and the resolved root does not exist. The repo-root default is always
    taken as-is; its existence is guaranteed by install.
    """

    source: WorkspaceSource
    if path_arg is not None and path_arg != "":
        source = "flag"
        raw = Path(path_arg)
        root = _resolve_against_cwd(raw, cwd)
    elif env_var is not None and env_var != "":
        source = "env"
        raw = Path(env_var)
        root = _resolve_against_cwd(raw, cwd)
    else:
        source = "default"
        root = repo_root().resolve(strict=False)

    if source in ("flag", "env") and not root.exists():
        raise WorkspaceNotFoundError(root)

    return _build_workspace_paths(root, source)


def resolve_workspace_for_init(
    path_arg: str,
    cwd: Path | None = None,
) -> Path:
    """Resolve a target path for ``llm-wiki init`` without requiring it to exist.

    Used by the ``init`` command only. Returns an absolute, resolved ``Path``;
    does NOT check existence and does NOT build a ``WorkspacePaths``. Relative
    paths are resolved against ``cwd`` (or ``Path.cwd()`` if None).
    """

    raw = Path(path_arg)
    return _resolve_against_cwd(raw, cwd)
