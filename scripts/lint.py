"""Lint the generated wiki under a workspace.

Workspace-aware refactor (LWC-7yge): every path flows from a
``WorkspacePaths`` instance passed to :func:`main`; no helper reaches into a
module global for a path.  See ARCHITECTURE §5.3, §7.3 for the contract.

The four structured checks (source attribution, page length, internal links,
orphaned links) operate on the ``wiki/`` tree of the workspace and honor the
workspace-local ``.wikiignore`` with repo-root fallback (via
:func:`scripts.workspace.resolve_wikiignore`).  Lint is strictly read-only;
it never mutates wiki pages, manifests, or any other workspace file.
"""

import argparse
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import List

from scripts.workspace import (
    WorkspacePaths,
    resolve_wikiignore,
)


def load_wikiignore(workspace: WorkspacePaths) -> List[str]:
    """Return the wiki ignore patterns for ``workspace``.

    Uses :func:`scripts.workspace.resolve_wikiignore` so the workspace-local
    ``.wikiignore`` wins and we transparently fall back to the repo-root
    copy when the workspace does not ship one.  When neither location has
    a ``.wikiignore``, returns ``[]`` (lint still runs and reports on every
    discovered page).
    """

    patterns: List[str] = []
    try:
        path, _is_fallback = resolve_wikiignore(workspace)
    except FileNotFoundError:
        # Neither the workspace-local nor repo-root .wikiignore exists; lint
        # still runs, it just has no patterns to filter pages by.
        return patterns

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _is_ignored(workspace: WorkspacePaths, page: Path, patterns: List[str]) -> bool:
    """Return True if ``page`` matches any entry in ``patterns``.

    Matches both the basename and the workspace-relative posix path so
    patterns like ``wiki/drafts/*.md`` and ``*.tmp`` both work.
    """

    if not patterns:
        return False
    name = page.name
    try:
        rel = page.relative_to(workspace.root).as_posix()
    except ValueError:
        rel = page.as_posix()
    for pattern in patterns:
        if fnmatch(name, pattern) or fnmatch(rel, pattern):
            return True
    return False


def scan_wiki(workspace: WorkspacePaths) -> List[Path]:
    """Return the list of wiki pages to lint under ``workspace``.

    Walks ``workspace.wiki_dir`` recursively, collecting ``*.md`` files, and
    filters out pages whose paths match the workspace's ``.wikiignore``
    (workspace-local -> repo-root fallback, same precedence as ingest).
    Returns an empty list when the wiki directory does not yet exist.
    """

    pages: List[Path] = []
    if not workspace.wiki_dir.exists():
        # Lint is called against a workspace that has not yet been populated;
        # the main entry point will print 'No wiki pages found.' and exit 0.
        return pages
    patterns = load_wikiignore(workspace)
    for page in sorted(workspace.wiki_dir.rglob("*.md")):
        if not page.is_file():
            continue
        if _is_ignored(workspace, page, patterns):
            continue
        pages.append(page)
    return pages


def check_source_attribution(workspace: WorkspacePaths, pages, all_names) -> dict:
    """Every page must cite its source ('Source', 'Source file', or 'Source contribution')."""
    failures: List[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if "Source" not in text and "Source file" not in text and "Source contribution" not in text:
            failures.append(str(page.relative_to(workspace.root)))
    return {
        "name": "Source attribution",
        "description": "all pages cite their source" if not failures else f"{len(failures)} pages missing source citation",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_page_length(workspace: WorkspacePaths, pages, all_names) -> dict:
    """Every page's stripped body must be at least 200 characters."""
    failures: List[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        char_count = len(text.strip())
        if char_count < 200:
            failures.append(f"{page.relative_to(workspace.root)} ({char_count} chars)")
    return {
        "name": "Page length",
        "description": "all pages meet minimum length" if not failures else f"{len(failures)} pages below minimum threshold",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_internal_links(workspace: WorkspacePaths, pages, all_names) -> dict:
    """Every page must have at least one ``[[wikilink]]``."""
    failures: List[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        links = re.findall(r"\[\[([^\]]+)\]\]", text)
        if not links:
            failures.append(str(page.relative_to(workspace.root)))
    return {
        "name": "Internal links",
        "description": "all pages have at least one internal link" if not failures else f"{len(failures)} pages with no internal links",
        "passed": len(failures) == 0,
        "details": failures,
    }


def check_orphaned_links(workspace: WorkspacePaths, pages, all_names) -> dict:
    """Every page with wikilinks must have at least one link that resolves."""
    failures: List[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        links = re.findall(r"\[\[([^\]]+)\]\]", text)
        if links:
            resolved = sum(1 for l in links if l in all_names)
            if resolved == 0:
                unresolved = [l for l in links if l not in all_names]
                failures.append(
                    f"{page.relative_to(workspace.root)}: {', '.join('[[' + l + ']]' for l in unresolved)}"
                )
    return {
        "name": "Orphaned links",
        "description": "all links resolve to existing pages" if not failures else f"{len(failures)} pages with only unresolved links",
        "passed": len(failures) == 0,
        "details": failures,
    }


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """lint entry point.

    Signature matches the workspace-aware dispatch contract in
    ``scripts.cli``: every subcommand is called as
    ``fn(remaining_argv, workspace)`` and returns an int exit code.

    All paths are resolved from ``workspace``; no module-level path constants
    are consulted.  ``argv`` is parsed for future extension but ``lint``
    accepts no flags today; any trailing noise is consumed by argparse so
    ``make lint`` and ``llm-wiki lint`` behave the same.
    """

    parser = argparse.ArgumentParser(
        prog="lint",
        description="Lint the generated wiki under a workspace.",
    )
    parser.parse_args(argv)

    pages = scan_wiki(workspace)
    if not pages:
        print("No wiki pages found.")
        return 0

    all_names = {p.stem for p in pages}
    checks = [
        check_source_attribution(workspace, pages, all_names),
        check_page_length(workspace, pages, all_names),
        check_internal_links(workspace, pages, all_names),
        check_orphaned_links(workspace, pages, all_names),
    ]

    for check in checks:
        if check["passed"]:
            print(f"[PASS] {check['name']} -- {check['description']}")
        else:
            print(f"[FAIL] {check['name']} -- {check['description']}")
            for detail in check["details"]:
                print(f"       - {detail}")

    passed = sum(1 for c in checks if c["passed"])
    failed = len(checks) - passed
    print(f"\n{failed} checks failed, {passed} checks passed")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    # Direct execution path: build a default workspace from the repo root.
    import sys as _sys

    from scripts.workspace import resolve_workspace

    raise SystemExit(main(_sys.argv[1:], resolve_workspace(None, None)))
