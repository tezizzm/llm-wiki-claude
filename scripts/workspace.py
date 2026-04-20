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
