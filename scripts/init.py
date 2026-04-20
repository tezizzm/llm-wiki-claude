"""Scaffold a new llm-wiki workspace.

Implements ``llm-wiki init PATH [--force]`` (ARCHITECTURE §8.1-§8.3,
DESIGN §5.1). Creates the workspace directory tree and writes template
files from the ``scripts.templates`` package.

Scope through story LWC-wn2r:

- Directory + template file scaffolding (LWC-zsy4).
- Workspace ``.gitignore`` writing (DESIGN §6.2) (LWC-7wkk).
- Git-safety detection: walk up from the target's parent looking for an
  outer ``.git`` entry (ARCHITECTURE §8.5, DESIGN §6.3) (LWC-7wkk).
- Structured final summary output matching DESIGN §5.2 (first run),
  DESIGN §5.3 (idempotent re-run / mixed outcome), and DESIGN §6.3
  (outer-repo warning block) byte-for-byte (LWC-wn2r).

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
    "# llm-wiki workspace \u2014 local state, not for commit\n"
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
# Summary output (DESIGN §5.2, §5.3, §6.3)
# ---------------------------------------------------------------------------


# Display groups, in order, matching DESIGN §5.2 layout exactly. Each group
# is a list of workspace-relative names; directories end in ``/`` in the
# rendered line but are tracked internally without the trailing slash so we
# can match them against the internal ``created``/``skipped``/``overwrote``
# lists (which store absolute file paths, not directories).
#
# Group 4 is the directories group: it is printed ONLY on a pristine first
# run (nothing skipped, nothing overwritten). On any re-run we omit it
# because the directories already existed.
_DISPLAY_GROUPS_FILES: tuple[tuple[str, ...], ...] = (
    (".env.example", ".env", ".gitignore", ".wikiignore"),
    ("sync-sources.local.json", "ingest-settings.local.json"),
    ("schemas/AGENTS.md",),
    ("index.md", "log.md"),
)

# Rendered exactly as it appears in DESIGN §5.2: one line, trailing slashes,
# brace-expansion shorthand for the wiki/ children.
_DIRECTORY_GROUP_LINE = "raw/inbox/, wiki/{summaries,topics,entities}/, state/"


def _rel_names(paths: list[str], target: Path) -> set[str]:
    """Convert absolute file paths (as stored by the write helpers) to a set
    of workspace-relative POSIX-style names (e.g. ``schemas/AGENTS.md``)."""

    names: set[str] = set()
    for p in paths:
        rel = Path(p).resolve().relative_to(target.resolve())
        names.add(rel.as_posix())
    return names


def _render_section(title: str, names: set[str], include_dirs: bool) -> list[str]:
    """Render a titled section (``Created:`` / ``Skipped (already exist):`` /
    ``Overwrote:``) for the given set of relative file names.

    The section is omitted entirely (empty list returned) if ``names`` is
    empty and ``include_dirs`` is False. Returns the list of output lines
    (no trailing blank line).
    """

    body_lines: list[str] = []
    for group in _DISPLAY_GROUPS_FILES[:3]:
        in_group = [n for n in group if n in names]
        if in_group:
            body_lines.append("  " + ", ".join(in_group))
    if include_dirs:
        # Directory group lives between the schemas/AGENTS.md group and the
        # index.md/log.md group in DESIGN §5.2.
        body_lines.append("  " + _DIRECTORY_GROUP_LINE)
    last_group = _DISPLAY_GROUPS_FILES[3]
    in_last = [n for n in last_group if n in names]
    if in_last:
        body_lines.append("  " + ", ".join(in_last))

    if not body_lines:
        # Empty-section signal -- caller omits the title entirely, so no
        # stray ``Created:\n\n`` blocks leak into the output.
        return list(body_lines)
    return [title, *body_lines]


def _render_warning(target: Path, outer_repo: Path) -> list[str]:
    """Render the DESIGN §6.3 outer-repo warning block.

    The line breaks are part of the locked text -- tests compare byte-for-byte.
    """

    return [
        f"Warning: {target} is inside an existing",
        f"git repository ({outer_repo}). A workspace .gitignore",
        "was written covering .env, raw/, state/, and wiki/, but you",
        "should verify before committing.",
    ]


def _render_next_steps(original_path_arg: str) -> list[str]:
    """Render the DESIGN §5.2 ``Next steps:`` block.

    Step 3 uses ``original_path_arg`` verbatim -- the one place where init
    preserves the user's notation (so a user who typed ``~/wikis/foo`` sees
    that back in the suggested command and can copy-paste it directly).
    """

    return [
        "Next steps:",
        "  1. Edit .env and set ANTHROPIC_API_KEY",
        "  2. Edit sync-sources.local.json to point at your sources",
        f"  3. Run: llm-wiki --workspace {original_path_arg} refresh-fast",
    ]


def _print_summary(
    target: Path,
    original_path_arg: str,
    created: list[str],
    skipped: list[str],
    overwrote: list[str],
    outer_repo: Path | None,
) -> None:
    """Emit the DESIGN §5.2 / §5.3 / §6.3 structured init output.

    - If nothing was created or overwritten, emit the idempotent no-op
      message (`Workspace already initialized at ...`) followed by a blank
      line and the ``Next steps:`` block.
    - Otherwise emit ``Initialized workspace at {target}`` followed by
      ``Created:``, ``Overwrote:``, and ``Skipped (already exist):``
      sections (each omitted when empty).
    - If ``outer_repo`` is not None, the DESIGN §6.3 warning block prints
      between the file sections and ``Next steps:``.
    - ``Next steps:`` always prints, even on a fully idempotent re-run.
    """

    created_names = _rel_names(created, target)
    skipped_names = _rel_names(skipped, target)
    overwrote_names = _rel_names(overwrote, target)

    lines: list[str] = []

    if not created and not overwrote:
        # Fully idempotent re-run. DESIGN §5.3: single message, then Next steps.
        lines.append(f"Workspace already initialized at {target}. No changes made.")
        lines.append("")
    else:
        lines.append(f"Initialized workspace at {target}")
        lines.append("")

        # DESIGN §5.2: directory group appears under Created on a pristine
        # first run (nothing skipped, nothing overwritten). On any re-run
        # the directories already existed, so we omit the group.
        first_run = not skipped and not overwrote
        created_section = _render_section(
            "Created:", created_names, include_dirs=first_run
        )
        if created_section:
            lines.extend(created_section)
            lines.append("")

        overwrote_section = _render_section(
            "Overwrote:", overwrote_names, include_dirs=False
        )
        if overwrote_section:
            lines.extend(overwrote_section)
            lines.append("")

        skipped_section = _render_section(
            "Skipped (already exist):", skipped_names, include_dirs=False
        )
        if skipped_section:
            lines.extend(skipped_section)
            lines.append("")

    # DESIGN §6.3 warning block prints between Created:/Overwrote:/Skipped:
    # and Next steps:. The warning is advisory; callers keep exit code 0.
    if outer_repo is not None:
        lines.extend(_render_warning(target, outer_repo))
        lines.append("")

    # DESIGN §5.2 / §5.3: Next steps prints on EVERY init, even on a fully
    # idempotent no-op re-run.
    lines.extend(_render_next_steps(original_path_arg))

    print("\n".join(lines))


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

    # 6. Structured summary output (DESIGN §5.2, §5.3, §6.3).
    _print_summary(
        target,
        args.path,
        created,
        skipped,
        overwrote,
        outer_repo,
    )

    return 0


if __name__ == "__main__":  # pragma: no cover - module is invoked via CLI
    raise SystemExit(main(sys.argv[1:]))
