"""Workspace path construction for the LLM wiki starter.

This module owns the single source of truth for workspace path layout. Every
command-line entry point (sync, ingest, query, lint, doctor) consumes a
``WorkspacePaths`` instance instead of deriving its own ``ROOT`` / derived
constants from ``__file__``.

See ARCHITECTURE.md §3 for the full contract. Path construction itself stays
pure: no I/O at dataclass construction time. Config resolvers and the
``load_env`` helper live here because they are conceptually workspace-scoped
and share the fallback rules documented in ARCHITECTURE §3.4.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

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


# ---------------------------------------------------------------------------
# Config path resolvers (ARCHITECTURE §3.4)
# ---------------------------------------------------------------------------
#
# Each resolver implements the same contract:
#
#   if workspace.<primary>.exists(): return (primary, False)
#   elif workspace.<fallback>.exists(): return (fallback, True)
#   else: raise FileNotFoundError naming both expected paths
#
# The boolean return value is ``is_fallback``: ``True`` when the workspace-local
# copy is missing and we resolved to the repo-root default. Callers (doctor,
# show_config, sync, ingest) use this to surface "using repo-root fallback"
# messaging without reimplementing the precedence rules.


def _resolve_with_fallback(
    primary: Path,
    fallback: Path,
) -> tuple[Path, bool]:
    """Shared primary -> fallback resolution helper.

    Returns ``(primary, False)`` if ``primary`` exists, else ``(fallback, True)``
    if ``fallback`` exists, else raises ``FileNotFoundError`` naming both paths.
    """

    if primary.exists():
        return primary, False
    if fallback.exists():
        return fallback, True
    raise FileNotFoundError(
        f"Neither {primary} nor {fallback} exists"
    )


def resolve_sync_config(workspace: WorkspacePaths) -> tuple[Path, bool]:
    """Resolve the sync-sources config path with repo-root fallback.

    Returns ``(path, is_fallback)``. Raises ``FileNotFoundError`` if neither
    ``workspace.sync_config_path`` nor ``workspace.sync_fallback_config_path``
    exists on disk.
    """

    return _resolve_with_fallback(
        workspace.sync_config_path,
        workspace.sync_fallback_config_path,
    )


def resolve_ingest_settings(workspace: WorkspacePaths) -> tuple[Path, bool]:
    """Resolve the ingest-settings config path with repo-root fallback.

    Returns ``(path, is_fallback)``. Raises ``FileNotFoundError`` if neither
    ``workspace.ingest_settings_path`` nor
    ``workspace.ingest_fallback_settings_path`` exists on disk.
    """

    return _resolve_with_fallback(
        workspace.ingest_settings_path,
        workspace.ingest_fallback_settings_path,
    )


def resolve_schema(workspace: WorkspacePaths) -> tuple[Path, bool]:
    """Resolve the schemas/AGENTS.md path with repo-root fallback.

    Returns ``(path, is_fallback)``. Raises ``FileNotFoundError`` if neither
    ``workspace.schema_path`` nor ``workspace.schema_fallback_path`` exists
    on disk.
    """

    return _resolve_with_fallback(
        workspace.schema_path,
        workspace.schema_fallback_path,
    )


def resolve_wikiignore(workspace: WorkspacePaths) -> tuple[Path, bool]:
    """Resolve the .wikiignore path with repo-root fallback.

    Returns ``(path, is_fallback)``. Raises ``FileNotFoundError`` if neither
    ``workspace.wikiignore_path`` nor ``workspace.wikiignore_fallback_path``
    exists on disk.
    """

    return _resolve_with_fallback(
        workspace.wikiignore_path,
        workspace.wikiignore_fallback_path,
    )


def resolve_env(workspace: WorkspacePaths) -> tuple[Path | None, bool]:
    """Resolve the .env path with repo-root fallback.

    Unlike the other resolvers, ``.env`` is allowed to be absent in both
    locations -- it is optional upstream of ``doctor``. Returns:

    - ``(workspace.env_path, False)`` if the workspace copy exists
    - ``(workspace.env_fallback_path, True)`` if only the repo-root copy exists
    - ``(None, False)`` if neither exists
    """

    if workspace.env_path.exists():
        return workspace.env_path, False
    if workspace.env_fallback_path.exists():
        return workspace.env_fallback_path, True
    return None, False


# ---------------------------------------------------------------------------
# load_env (ARCHITECTURE §3.5, DESIGN §11)
# ---------------------------------------------------------------------------


def load_env(workspace: WorkspacePaths) -> dict[str, str]:
    """Load .env with two-level fallback into ``os.environ``.

    Precedence (lowest -> highest; later wins):

    1. ``workspace.env_fallback_path`` (repo-root ``.env``) if it exists
    2. ``workspace.env_path`` (workspace ``.env``) if it exists
    3. Real ``os.environ`` -- ALWAYS wins over both .env files

    Invariants (ARCHITECTURE §14, CRITICAL):

    - Never overwrites a pre-existing ``os.environ`` key
      (uses ``os.environ.setdefault``, never ``os.environ[k] = v``).
    - Idempotent: calling twice produces the same ``os.environ`` state as once.
    - Does not read ``LLM_WIKI_WORKSPACE``; the ``workspace`` parameter is
      authoritative.

    Returns the merged ``{key: value}`` dict sourced from the .env files (before
    the ``os.environ.setdefault`` application), so callers can observe what the
    .env files contained even when real environment values already shadowed
    them.
    """

    merged: dict[str, str] = {}

    fallback = workspace.env_fallback_path
    if fallback.exists():
        merged.update(
            {k: v for k, v in dotenv_values(fallback).items() if v is not None}
        )

    primary = workspace.env_path
    if primary.exists():
        merged.update(
            {k: v for k, v in dotenv_values(primary).items() if v is not None}
        )

    # Apply to os.environ using setdefault so real environment values always
    # win. This is the critical contract from DESIGN §11: load_env never
    # clobbers a value the user already exported.
    for key, value in merged.items():
        os.environ.setdefault(key, value)

    return merged


# ---------------------------------------------------------------------------
# Workspace validation helpers
# ---------------------------------------------------------------------------


def ensure_workspace_exists(workspace: WorkspacePaths) -> None:
    """Validate that ``workspace.root`` exists and is a directory.

    - Raises ``WorkspaceNotFoundError`` if the root does not exist.
    - Raises ``WorkspaceInvalidError`` if the root exists but is not a
      directory.

    Used by all commands except ``init`` (which creates the workspace) to fail
    fast with a clear error message before any further filesystem work.
    """

    root = workspace.root
    if not root.exists():
        raise WorkspaceNotFoundError(root)
    if not root.is_dir():
        raise WorkspaceInvalidError(
            f"workspace root exists but is not a directory: {root}"
        )


def ensure_workspace_writable(workspace: WorkspacePaths) -> None:
    """Create writable workspace subdirectories if they are missing.

    Idempotent: safe to call on every invocation. Creates:

    - ``workspace.state_dir``
    - ``workspace.raw_dir`` (``raw/inbox``; parents must also be created)
    - ``workspace.summaries_dir``, ``workspace.topics_dir``,
      ``workspace.entities_dir`` (all under ``wiki/``)

    Called by ``sync`` and ``ingest`` before they write outputs. NOT called by
    ``doctor`` because doctor is strictly read-only.
    """

    for directory in (
        workspace.state_dir,
        workspace.raw_dir,
        workspace.summaries_dir,
        workspace.topics_dir,
        workspace.entities_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
