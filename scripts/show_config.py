"""Show the resolved config paths for a workspace.

Workspace-aware refactor (LWC-nk0x): every path flows from a
``WorkspacePaths`` instance passed to :func:`main`; no helper reaches into a
module global for a path. The command is the user-facing answer to "why is
ingest reading the wrong config?" -- it prints one line per fallback-eligible
artifact (sync-sources, ingest-settings, schemas/AGENTS.md, .wikiignore, .env)
so that the primary vs repo-root fallback choice is visible.

Output shape (one line per artifact):

    Workspace root: /path/to/workspace
    sync-sources.local.json: /path/to/workspace/sync-sources.local.json
    ingest-settings.local.json: fallback -> /path/to/repo/ingest-settings.json
    schemas/AGENTS.md: /path/to/workspace/schemas/AGENTS.md
    .wikiignore: fallback -> /path/to/repo/.wikiignore
    .env: <none found>

The ``fallback -> `` prefix appears when the workspace-local copy is missing
and the repo-root default was selected. ``<none found>`` prints when neither
location has the file (only ``.env`` is allowed to be absent in both).

The banner ("Workspace: ... (from --workspace)") is NOT printed by this
module; ``cli.main`` handles it exactly once per invocation so subcommands
stay silent about workspace source.
"""

import argparse
from pathlib import Path

from scripts.workspace import (
    WorkspacePaths,
    resolve_env,
    resolve_ingest_settings,
    resolve_schema,
    resolve_sync_config,
    resolve_wikiignore,
)


def _format_resolution_line(label: str, path: Path, is_fallback: bool) -> str:
    """Render a single artifact line with the ``fallback -> `` prefix.

    ``is_fallback=True`` means the workspace-local copy was missing and we
    resolved to the repo-root default. The format matches DESIGN §7.4 /
    doctor's resolution block so users see identical strings across commands.
    """

    if is_fallback:
        return f"{label}: fallback -> {path}"
    return f"{label}: {path}"


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """show-config entry point.

    Signature matches the workspace-aware dispatch contract in
    ``scripts.cli``: every subcommand is called as
    ``fn(remaining_argv, workspace)`` and returns an int exit code.

    ``argv`` is parsed for future extension but ``show-config`` accepts no
    flags today; any trailing noise is consumed by argparse so ``make config``
    and ``llm-wiki show-config`` behave the same.
    """

    parser = argparse.ArgumentParser(
        prog="show-config",
        description="Print the resolved config paths for the active workspace.",
    )
    parser.parse_args(argv)

    print(f"Workspace root: {workspace.root}")

    # sync-sources.local.json
    try:
        path, is_fallback = resolve_sync_config(workspace)
        print(_format_resolution_line("sync-sources.local.json", path, is_fallback))
    except FileNotFoundError:
        print("sync-sources.local.json: <none found>")

    # ingest-settings.local.json
    try:
        path, is_fallback = resolve_ingest_settings(workspace)
        print(_format_resolution_line("ingest-settings.local.json", path, is_fallback))
    except FileNotFoundError:
        print("ingest-settings.local.json: <none found>")

    # schemas/AGENTS.md
    try:
        path, is_fallback = resolve_schema(workspace)
        print(_format_resolution_line("schemas/AGENTS.md", path, is_fallback))
    except FileNotFoundError:
        print("schemas/AGENTS.md: <none found>")

    # .wikiignore
    try:
        path, is_fallback = resolve_wikiignore(workspace)
        print(_format_resolution_line(".wikiignore", path, is_fallback))
    except FileNotFoundError:
        print(".wikiignore: <none found>")

    # .env  (special: allowed to be absent in both locations)
    env_path, env_is_fallback = resolve_env(workspace)
    if env_path is None:
        print(".env: <none found>")
    else:
        print(_format_resolution_line(".env", env_path, env_is_fallback))

    return 0
