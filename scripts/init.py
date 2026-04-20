"""Scaffold a new llm-wiki workspace.

Implements ``llm-wiki init PATH [--force]`` (ARCHITECTURE §8.1-§8.3,
DESIGN §5.1). Creates the workspace directory tree and writes template
files from the ``scripts.templates`` package.

Scope through story LWC-7wkk:

- Directory + template file scaffolding (LWC-zsy4).
- Workspace ``.gitignore`` writing (DESIGN §6.2) (LWC-7wkk).
- Git-safety detection: walk up from the target's parent looking for an
  outer ``.git`` entry (ARCHITECTURE §8.5, DESIGN §6.3) (LWC-7wkk).
- Does NOT emit the final structured summary output (separate story
  LWC-wn2r); a minimal message is printed for now so the command is
  usable end-to-end. The outer-repo warning block is likewise emitted
  as a minimal placeholder pending LWC-wn2r.

Error contract (ARCHITECTURE §8.3, DESIGN §10.3/§10.4):

- Target path exists and is not a directory -> exit 1, message on stderr.
- Target path (or its parent) is not writable -> exit 1, message on stderr.

--force semantics (ARCHITECTURE §8.3 final rule, DESIGN §5.3):

- Every template file (including ``.env``) is overwritten.
- User-authored content under ``raw/``, ``wiki/``, and ``state/`` is
  never touched -- init only writes the template file set.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

import scripts.templates as templates_pkg
import scripts.templates.schemas as templates_schemas_pkg
from scripts.workspace import resolve_workspace_for_init


# ---------------------------------------------------------------------------
# Template readers
# ---------------------------------------------------------------------------


def _read_template(name: str) -> str:
    """Return the text of a template from ``scripts.templates``.

    ``name`` is the filename within the package (e.g. ``env.example``,
    ``wikiignore``, ``sync-sources.json``). For ``schemas/AGENTS.md`` use
    :func:`_read_schema_template`.
    """

    return (files(templates_pkg) / name).read_text(encoding="utf-8")


def _read_schema_template() -> str:
    """Return the text of ``schemas/AGENTS.md`` from ``scripts.templates.schemas``."""

    return (files(templates_schemas_pkg) / "AGENTS.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _write_from_template(
    dest: Path,
    template_name: str,
    force: bool,
    created: list[str],
    skipped: list[str],
    overwrote: list[str],
) -> None:
    """Write a template file to ``dest``.

    - Existing destination + ``force=False`` -> skipped.
    - Existing destination + ``force=True``  -> overwritten.
    - Missing destination                    -> created.

    The special template name ``schemas/AGENTS.md`` routes through
    :func:`_read_schema_template` so we read from the ``schemas`` subpackage
    (importlib.resources traversal does not descend into subpackages via a
    simple ``/`` join on some loaders).
    """

    if template_name == "schemas/AGENTS.md":
        content = _read_schema_template()
    else:
        content = _read_template(template_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if force:
            dest.write_text(content, encoding="utf-8")
            overwrote.append(str(dest))
        else:
            skipped.append(str(dest))
    else:
        dest.write_text(content, encoding="utf-8")
        created.append(str(dest))


def _write_raw(
    dest: Path,
    content: str,
    force: bool,
    created: list[str],
    skipped: list[str],
    overwrote: list[str],
) -> None:
    """Write a literal ``content`` string to ``dest`` with the same policy as
    :func:`_write_from_template` (skip existing unless ``force``).
    """

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if force:
            dest.write_text(content, encoding="utf-8")
            overwrote.append(str(dest))
        else:
            skipped.append(str(dest))
    else:
        dest.write_text(content, encoding="utf-8")
        created.append(str(dest))


# ---------------------------------------------------------------------------
# .gitignore + git-safety (DESIGN §6.2, §6.3; ARCHITECTURE §8.5)
# ---------------------------------------------------------------------------


# Fixed workspace .gitignore content (DESIGN §6.2). The leading comment line
# is part of the contract -- tests compare byte-for-byte.
_GITIGNORE_CONTENT = (
    "# llm-wiki workspace -- local state, not for commit\n"
    ".env\n"
    "raw/\n"
    "state/\n"
    "wiki/\n"
)


def _write_gitignore(
    target: Path,
    force: bool,
    created: list[str],
    skipped: list[str],
    overwrote: list[str],
) -> None:
    """Write the fixed DESIGN §6.2 ``.gitignore`` to ``target``.

    Create/skip/overwrite semantics match :func:`_write_raw`:

    - Missing destination                    -> created.
    - Existing destination + ``force=False`` -> skipped.
    - Existing destination + ``force=True``  -> overwritten.
    """

    _write_raw(
        target / ".gitignore",
        _GITIGNORE_CONTENT,
        force,
        created,
        skipped,
        overwrote,
    )


def _detect_outer_git_repo(target: Path) -> Path | None:
    """Walk up from ``target.parent`` looking for an enclosing git repo.

    Returns the first ancestor directory containing a ``.git`` entry (file,
    directory, or symlink), else ``None``. ``.git`` at ``target`` itself is
    intentionally NOT detected -- a workspace that *is* its own git repo is
    fine.

    ``target.parent`` is resolved via ``Path.resolve()`` so symlinked target
    directories walk the real filesystem tree, not the symlink chain.
    """

    # Resolve the target first so a symlinked target directory walks the real
    # filesystem tree, not the symlink chain. ``strict=False`` lets us resolve
    # paths that do not yet exist (init may run on a yet-to-be-created dir).
    start = target.resolve(strict=False).parent
    current = start
    # ``current.parent == current`` only at the filesystem root.
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="llm-wiki init")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if not args.path:
        parser.error("PATH is required")
    return args


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


_SUBDIRS = (
    "raw/inbox",
    "wiki/summaries",
    "wiki/topics",
    "wiki/entities",
    "state",
    "schemas",
)


def main(argv: list[str]) -> int:
    """Entry point for ``llm-wiki init``.

    Returns 0 on success, 1 on the documented error conditions (target
    is a regular file, or target is not writable).
    """

    args = _parse(argv)
    target = resolve_workspace_for_init(args.path)

    if target.exists() and not target.is_dir():
        print(
            f"Init error: {target} is a file, not a directory. "
            f"Choose a directory path.",
            file=sys.stderr,
        )
        return 1

    try:
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(
            f"Init error: cannot write to {target} (permission denied).",
            file=sys.stderr,
        )
        return 1

    created: list[str] = []
    skipped: list[str] = []
    overwrote: list[str] = []

    # 1. Directories (idempotent; never listed in created/skipped/overwrote).
    try:
        for sub in _SUBDIRS:
            (target / sub).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(
            f"Init error: cannot write to {target} (permission denied).",
            file=sys.stderr,
        )
        return 1

    # 2. Template files. Bare init skips existing; --force overwrites all
    #    template files including .env (ARCHITECTURE §8.3 final rule).
    try:
        _write_from_template(
            target / ".env.example", "env.example", args.force,
            created, skipped, overwrote,
        )
        _write_from_template(
            target / ".env", "env.example", args.force,
            created, skipped, overwrote,
        )
        _write_from_template(
            target / ".wikiignore", "wikiignore", args.force,
            created, skipped, overwrote,
        )
        _write_from_template(
            target / "sync-sources.local.json", "sync-sources.json", args.force,
            created, skipped, overwrote,
        )
        _write_from_template(
            target / "ingest-settings.local.json", "ingest-settings.json", args.force,
            created, skipped, overwrote,
        )
        _write_from_template(
            target / "schemas" / "AGENTS.md", "schemas/AGENTS.md", args.force,
            created, skipped, overwrote,
        )

        # 3. Empty / placeholder files. Treated like templates: force
        #    overwrites, bare init skips if the file already exists.
        _write_raw(
            target / "index.md", "# Wiki\n", args.force,
            created, skipped, overwrote,
        )
        _write_raw(
            target / "log.md", "", args.force,
            created, skipped, overwrote,
        )

        # 4. Workspace .gitignore (DESIGN §6.2). Always written; same
        #    create/skip/overwrite policy as template files.
        _write_gitignore(target, args.force, created, skipped, overwrote)
    except PermissionError:
        print(
            f"Init error: cannot write to {target} (permission denied).",
            file=sys.stderr,
        )
        return 1

    # 5. Git-safety detection (ARCHITECTURE §8.5, DESIGN §6.3). Walk up from
    #    the target's parent; if a ``.git`` entry lives in any ancestor, the
    #    workspace is nested inside an existing repository and the user gets
    #    a warning so they don't accidentally commit wiki state into it.
    outer_repo = _detect_outer_git_repo(target)

    # 6. Structured summary output is owned by story LWC-wn2r. For now emit
    #    a minimal completion message + the DESIGN §6.3 warning block (so
    #    the command is observably correct end-to-end).
    print(f"Initialized workspace at {target}")
    if created:
        print(f"  created:    {len(created)}")
    if overwrote:
        print(f"  overwrote:  {len(overwrote)}")
    if skipped:
        print(f"  skipped:    {len(skipped)}")

    if outer_repo is not None:
        print(
            f"\nWarning: {target} is inside an existing\n"
            f"git repository ({outer_repo}). A workspace .gitignore\n"
            f"was written covering .env, raw/, state/, and wiki/, but you\n"
            f"should verify before committing."
        )

    return 0


if __name__ == "__main__":  # pragma: no cover - module is invoked via CLI
    raise SystemExit(main(sys.argv[1:]))
