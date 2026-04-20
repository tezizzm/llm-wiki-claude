"""Unified CLI dispatcher for the ``llm-wiki`` command.

This module owns the top-level argv preprocessing for workspace resolution.
argparse cannot cleanly handle a *global* flag that can appear on either side
of the subcommand, so we scan ``argv`` manually for ``--workspace`` and
``--verbose`` before dispatching to the per-subcommand argparser.

Precedence and banner rules follow DESIGN §3-§4 and §11. The workspace is
resolved once per invocation, banner is emitted to stdout when the source is
``flag`` or ``env``, ``load_env`` is applied, and then the subcommand runs.
"""

import argparse
import os
import sys
from typing import Callable

from scripts import doctor, ingest, lint, query, sync
from scripts.version import read_version
from scripts.workspace import (
    WorkspaceNotFoundError,
    WorkspacePaths,
    load_env,
    resolve_env,
    resolve_ingest_settings,
    resolve_schema,
    resolve_sync_config,
    resolve_wikiignore,
    resolve_workspace,
)


def _extract_global_workspace(argv: list[str]) -> tuple[str | None, list[str]]:
    """Scan ``argv`` for ``--workspace`` / ``--workspace=PATH`` and remove it.

    Returns ``(value, rest)`` where ``rest`` is ``argv`` with both the flag
    and its argument stripped. Supports both placement styles:

        llm-wiki --workspace X doctor
        llm-wiki doctor --workspace X
        llm-wiki --workspace=X doctor

    Only the first occurrence is consumed; any subsequent occurrence is left
    in ``rest`` so argparse downstream can complain about it if it wants.
    """

    result: list[str] = []
    value: str | None = None
    i = 0
    consumed = False
    while i < len(argv):
        token = argv[i]
        if not consumed and token == "--workspace":
            if i + 1 >= len(argv):
                # Missing value -- preserve token so downstream surfacing sees it
                result.append(token)
                i += 1
                continue
            value = argv[i + 1]
            consumed = True
            i += 2
            continue
        if not consumed and token.startswith("--workspace="):
            value = token[len("--workspace=") :]
            consumed = True
            i += 1
            continue
        result.append(token)
        i += 1
    return value, result


def _extract_global_verbose(argv: list[str]) -> tuple[bool, list[str]]:
    """Scan ``argv`` for ``--verbose`` / ``-v`` and remove it.

    Returns ``(bool, rest)``. ``--verbose`` is a global flag because
    DESIGN §11.2 uses it on any workspace-aware command to print the
    resolution block after the banner.
    """

    result: list[str] = []
    found = False
    for token in argv:
        if token in ("--verbose", "-v"):
            found = True
            continue
        result.append(token)
    return found, result


def _print_banner_if_needed(workspace: WorkspacePaths) -> None:
    """Print the DESIGN §4.2 banner to stdout when source is non-default.

    Silent when ``workspace.source == 'default'`` so that 0.2.0 output is
    byte-identical in the repo-root default path.
    """

    if workspace.source == "default":
        return
    label = {"flag": "--workspace", "env": "LLM_WIKI_WORKSPACE"}[workspace.source]
    print(f"Workspace: {workspace.root} (from {label})")
    print()  # DESIGN §4.2 mandates the trailing blank line


def _print_verbose_resolution_block(workspace: WorkspacePaths) -> None:
    """Print the DESIGN §11.2 resolution block to stdout.

    Each line reports the resolved path for a fallback-eligible file, using
    the ``fallback -> `` prefix when the workspace-local copy was missing and
    the repo-root default was selected. ``.env`` missing in both locations
    prints ``<none found>`` per §11.3.
    """

    print("Resolved config:")

    def _line(label: str, path, is_fallback: bool) -> str:
        if is_fallback:
            return f"  {label}: fallback -> {path}"
        return f"  {label}: {path}"

    try:
        sync_path, sync_fb = resolve_sync_config(workspace)
        print(_line("sync-sources.local.json", sync_path, sync_fb))
    except FileNotFoundError:
        print("  sync-sources.local.json: <none found>")

    try:
        ingest_path, ingest_fb = resolve_ingest_settings(workspace)
        print(_line("ingest-settings.local.json", ingest_path, ingest_fb))
    except FileNotFoundError:
        print("  ingest-settings.local.json: <none found>")

    try:
        schema_path, schema_fb = resolve_schema(workspace)
        print(_line("schemas/AGENTS.md", schema_path, schema_fb))
    except FileNotFoundError:
        print("  schemas/AGENTS.md: <none found>")

    try:
        ignore_path, ignore_fb = resolve_wikiignore(workspace)
        print(_line(".wikiignore", ignore_path, ignore_fb))
    except FileNotFoundError:
        print("  .wikiignore: <none found>")

    env_path, env_fb = resolve_env(workspace)
    if env_path is None:
        print("  .env: <none found>")
    else:
        print(_line(".env", env_path, env_fb))

    print()  # trailing blank line before subcommand output


# ---------------------------------------------------------------------------
# DISPATCH registry (DESIGN §5)
# ---------------------------------------------------------------------------
#
# Maps subcommand name -> workspace-aware entry point with the shared
# signature ``fn(argv_rest: list[str], workspace: WorkspacePaths) -> int``.
# Subcommands are migrated into this registry story by story; the legacy
# ``sys.argv`` dispatch below continues to service anything not yet wired.

DISPATCH: dict[str, Callable[[list[str], WorkspacePaths], int]] = {
    "doctor": doctor.main,
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for subcommand dispatch.

    ``--workspace`` and ``--verbose`` are pre-stripped from argv before this
    parser ever sees them, so they are deliberately absent here.
    """

    parser = argparse.ArgumentParser(description="Unified CLI for the LLM wiki starter.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {read_version()}")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="Sync source files into raw/inbox.")
    sync_parser.add_argument("sync_args", nargs=argparse.REMAINDER)

    ingest_parser = subparsers.add_parser("ingest", help="Build wiki artifacts from raw sources.")
    ingest_parser.add_argument("ingest_args", nargs=argparse.REMAINDER)

    query_parser = subparsers.add_parser("query", help="Ask a question against the local wiki.")
    query_parser.add_argument("query_args", nargs=argparse.REMAINDER)
    subparsers.add_parser("lint", help="Lint the generated wiki.")
    subparsers.add_parser("doctor", help="Validate local config, versioning, and demo artifact readiness.")

    refresh_parser = subparsers.add_parser("refresh", help="Run sync with prune and then ingest.")
    refresh_parser.add_argument("--dry-run", action="store_true", help="Dry run sync before ingesting.")
    refresh_parser.add_argument("--reconcile", action="store_true", help="Rebuild derived wiki artifacts before ingest.")

    fast_parser = subparsers.add_parser("refresh-fast", help="Run sync without prune and then ingest.")
    fast_parser.add_argument("--dry-run", action="store_true", help="Dry run sync before ingesting.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Steps:
      1. Normalize ``argv`` to a list (defaulting to ``sys.argv[1:]``).
      2. Extract the global ``--workspace`` and ``--verbose`` flags.
      3. Short-circuit ``init`` (not yet implemented).
      4. Resolve the workspace via ``resolve_workspace``.
      5. Print the banner (non-default sources only) and optional verbose
         resolution block.
      6. Apply ``load_env(workspace)``.
      7. Dispatch to the subcommand using the existing argparse-based
         mechanics. Workspace wiring into individual commands (beyond
         ``load_env`` + banner) is left to follow-up stories.
    """

    argv = list(sys.argv[1:] if argv is None else argv)

    # --version must be honored without requiring workspace resolution, since
    # argparse raises SystemExit inside parse_args.
    if argv and argv[0] == "--version":
        parser = _build_parser()
        parser.parse_args(argv)  # exits
        return 0

    path_arg, argv = _extract_global_workspace(argv)
    verbose, argv = _extract_global_verbose(argv)

    subcommand = argv[0] if argv else None

    if subcommand == "init":
        print(
            "Error: `llm-wiki init` is not yet implemented in this release.",
            file=sys.stderr,
        )
        return 2

    env_var = os.environ.get("LLM_WIKI_WORKSPACE")
    try:
        workspace = resolve_workspace(path_arg, env_var)
    except WorkspaceNotFoundError as exc:
        print(
            f"Workspace error: {exc.path} does not exist. "
            f"Run `llm-wiki init {exc.path}` first.",
            file=sys.stderr,
        )
        return 2

    _print_banner_if_needed(workspace)
    if verbose:
        _print_verbose_resolution_block(workspace)
    load_env(workspace)

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync":
        sys.argv = ["sync.py", *args.sync_args]
        sync.main()
        return 0
    if args.command == "ingest":
        sys.argv = ["ingest.py", *args.ingest_args]
        ingest.main()
        return 0
    if args.command == "query":
        sys.argv = ["query.py", *args.query_args]
        query.main()
        return 0
    if args.command == "lint":
        raise SystemExit(lint.main())
    if args.command == "doctor":
        raise SystemExit(DISPATCH["doctor"]([], workspace))
    if args.command == "refresh":
        sync_argv = ["sync.py", "--prune"]
        if args.dry_run:
            sync_argv.append("--dry-run")
        sys.argv = sync_argv
        sync.main()
        if not args.dry_run:
            ingest_argv = ["ingest.py"]
            if args.reconcile:
                ingest_argv.append("--reconcile")
            sys.argv = ingest_argv
            ingest.main()
        return 0
    if args.command == "refresh-fast":
        sync_argv = ["sync.py"]
        if args.dry_run:
            sync_argv.append("--dry-run")
        sys.argv = sync_argv
        sync.main()
        if not args.dry_run:
            sys.argv = ["ingest.py"]
            ingest.main()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
