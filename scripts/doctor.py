"""Doctor command: workspace diagnostics with FAIL/WARN/OK policy.

See DESIGN §7 for the full specification.  Doctor is the first diagnostic
front door after the walking skeleton lands.  It accepts a workspace and
emits:

1. A resolution block for the five fallback-eligible files.
2. ``FAIL: ...`` / ``WARN: ...`` lines (FAILs first, in the order defined
   in DESIGN §7.1; then WARNs in the same table order).
3. A summary line in the exact form ``doctor: N failures, M warnings``.

Exit code is 1 iff any FAIL check fired, 0 otherwise.

All check functions have the signature ``(workspace) -> (severity, message)``
so they can be listed in policy order and executed uniformly.  Severity is a
``Literal['FAIL', 'WARN', 'OK']`` and ``message`` is ``None`` when severity
is ``'OK'``.
"""

import json
import os
from pathlib import Path
from typing import Callable, Literal

from dotenv import dotenv_values

from scripts.workspace import (
    WorkspaceError,
    WorkspacePaths,
    ensure_workspace_exists,
    resolve_env,
    resolve_ingest_settings,
    resolve_schema,
    resolve_sync_config,
    resolve_wikiignore,
)

Severity = Literal["FAIL", "WARN", "OK"]
CheckResult = tuple[Severity, str | None]
CheckFn = Callable[[WorkspacePaths], CheckResult]

# Kept here (not a workspace path constant) because it is referenced by both
# the sync placeholder check and the publishable tracked template.  Deliberately
# not exported from workspace.py -- this is doctor-internal policy.
GENERIC_SYNC_ROOT_PLACEHOLDER = "/absolute/path/to/your/project"


# ---------------------------------------------------------------------------
# Structural FAIL checks (DESIGN §7.1 rows 1-6)
# ---------------------------------------------------------------------------


def check_state_dir_exists(workspace: WorkspacePaths) -> CheckResult:
    """state/ must exist."""
    if workspace.state_dir.is_dir():
        return ("OK", None)
    return ("FAIL", f"state/ directory missing: {workspace.state_dir}")


def check_raw_inbox_dir_exists(workspace: WorkspacePaths) -> CheckResult:
    """raw/inbox/ must exist."""
    if workspace.raw_dir.is_dir():
        return ("OK", None)
    return ("FAIL", f"raw/inbox/ directory missing: {workspace.raw_dir}")


def check_wiki_subdirs_exist(workspace: WorkspacePaths) -> CheckResult:
    """All three wiki/ subdirectories must exist (summaries, topics, entities)."""
    missing: list[str] = []
    for subdir, label in (
        (workspace.summaries_dir, "wiki/summaries"),
        (workspace.topics_dir, "wiki/topics"),
        (workspace.entities_dir, "wiki/entities"),
    ):
        if not subdir.is_dir():
            missing.append(label)
    if missing:
        return (
            "FAIL",
            f"wiki subdirectory missing: {', '.join(missing)}",
        )
    return ("OK", None)


def check_sync_config_present(workspace: WorkspacePaths) -> CheckResult:
    """Either workspace sync-sources.local.json or repo-root sync-sources.json must exist."""
    try:
        resolve_sync_config(workspace)
    except FileNotFoundError:
        return (
            "FAIL",
            (
                "sync config missing: neither "
                f"{workspace.sync_config_path} nor "
                f"{workspace.sync_fallback_config_path} exists"
            ),
        )
    return ("OK", None)


def check_config_json_well_formed(workspace: WorkspacePaths) -> CheckResult:
    """All present config JSON files must parse as valid JSON."""
    checked: list[tuple[str, Path]] = []
    # Missing files are reported by check_sync_config_present / check_env_present;
    # here we only validate JSON of files that DO exist.
    try:
        sync_path, _ = resolve_sync_config(workspace)
    except FileNotFoundError:
        sync_path = None
    if sync_path is not None:
        checked.append(("sync config", sync_path))
    try:
        ingest_path, _ = resolve_ingest_settings(workspace)
    except FileNotFoundError:
        ingest_path = None
    if ingest_path is not None:
        checked.append(("ingest settings", ingest_path))

    for label, path in checked:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return (
                "FAIL",
                f"{label} has malformed JSON ({path}): {exc.msg} at line {exc.lineno}",
            )
        except OSError as exc:
            return (
                "FAIL",
                f"{label} could not be read ({path}): {exc}",
            )
    return ("OK", None)


def check_env_present(workspace: WorkspacePaths) -> CheckResult:
    """At least one .env must exist (workspace or repo-root)."""
    env_path, _ = resolve_env(workspace)
    if env_path is None:
        return (
            "FAIL",
            (
                ".env missing: neither "
                f"{workspace.env_path} nor "
                f"{workspace.env_fallback_path} exists"
            ),
        )
    return ("OK", None)


# ---------------------------------------------------------------------------
# WARN checks (DESIGN §7.1 rows 7-10)
# ---------------------------------------------------------------------------


def check_sync_placeholder_sources(workspace: WorkspacePaths) -> CheckResult:
    """WARN when sync-sources.local.json still holds the generic placeholder root.

    Only consulted when a workspace-local copy exists; the repo-root template
    is expected to carry the placeholder, so we do not warn on fallback use.
    """
    if not workspace.sync_config_path.exists():
        return ("OK", None)

    try:
        data = json.loads(workspace.sync_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Handled by check_config_json_well_formed; don't double-report here.
        return ("OK", None)

    sources = data.get("sources")
    if not isinstance(sources, list):
        return ("OK", None)

    placeholder_sources: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("root") == GENERIC_SYNC_ROOT_PLACEHOLDER:
            name = source.get("name", "<unnamed>")
            placeholder_sources.append(str(name))

    if placeholder_sources:
        return (
            "WARN",
            (
                "sync-sources.local.json contains placeholder source roots: "
                + ", ".join(placeholder_sources)
            ),
        )
    return ("OK", None)


def check_raw_inbox_empty(workspace: WorkspacePaths) -> CheckResult:
    """WARN when raw/inbox/ is present but empty."""
    if not workspace.raw_dir.is_dir():
        # Covered by check_raw_inbox_dir_exists; do not double-report.
        return ("OK", None)
    try:
        contents = list(workspace.raw_dir.iterdir())
    except OSError as exc:
        return ("WARN", f"raw/inbox/ could not be listed: {exc}")
    if not contents:
        return ("WARN", f"raw/inbox/ is empty: {workspace.raw_dir}")
    return ("OK", None)


def check_anthropic_api_key(workspace: WorkspacePaths) -> CheckResult:
    """WARN when ANTHROPIC_API_KEY is missing / empty / placeholder in the resolved .env.

    The resolved .env is the workspace copy if present, else the repo-root
    copy.  When neither exists we return OK here (check_env_present already
    FAILed).  We deliberately consult the file, not ``os.environ``, so that
    doctor still warns when the user has exported the key but never wrote it
    to disk for future invocations.
    """
    env_path, _ = resolve_env(workspace)
    if env_path is None:
        return ("OK", None)
    try:
        values = dotenv_values(env_path)
    except OSError as exc:
        return ("WARN", f".env could not be read ({env_path}): {exc}")
    key = values.get("ANTHROPIC_API_KEY", "")
    if not key or key == "your_anthropic_api_key_here":
        return (
            "WARN",
            f"ANTHROPIC_API_KEY missing or placeholder in {env_path}",
        )
    return ("OK", None)


def check_gitignore_if_inside_outer_repo(workspace: WorkspacePaths) -> CheckResult:
    """WARN when workspace lives inside another git repo but lacks .gitignore coverage.

    An "outer" repo is any ancestor directory of ``workspace.root`` (strictly
    above, ignoring the workspace itself) that contains a ``.git`` directory
    and is NOT the repo root of llm-wiki itself.  When such a repo is found,
    the workspace must carry a ``.gitignore`` that covers ``.env``, ``raw/``,
    ``state/``, and ``wiki/`` or the user risks accidentally committing
    generated artifacts to the outer repo.
    """
    # Walk strictly ABOVE workspace.root
    current = workspace.root.parent
    outer_repo: Path | None = None
    while True:
        if (current / ".git").exists() and current != workspace.repo_root:
            outer_repo = current
            break
        if current.parent == current:
            break
        current = current.parent

    if outer_repo is None:
        return ("OK", None)

    gitignore = workspace.root / ".gitignore"
    required = (".env", "raw/", "state/", "wiki/")

    if not gitignore.exists():
        return (
            "WARN",
            (
                f"workspace is inside outer git repo {outer_repo} "
                f"but {gitignore} is missing"
            ),
        )

    try:
        content = gitignore.read_text(encoding="utf-8")
    except OSError as exc:
        return ("WARN", f".gitignore could not be read ({gitignore}): {exc}")

    entries = {line.strip() for line in content.splitlines() if line.strip()}

    def _covers(token: str) -> bool:
        # Accept exact match or trailing-slash variant.
        variants = {token, token.rstrip("/"), token.rstrip("/") + "/"}
        return bool(entries & variants)

    missing = [token for token in required if not _covers(token)]
    if missing:
        return (
            "WARN",
            (
                f"workspace is inside outer git repo {outer_repo} "
                f"but {gitignore} does not cover: {', '.join(missing)}"
            ),
        )
    return ("OK", None)


# ---------------------------------------------------------------------------
# Resolution block (DESIGN §7.4)
# ---------------------------------------------------------------------------


def _format_resolution_line(label: str, path: Path, is_fallback: bool) -> str:
    if is_fallback:
        return f"{label}: fallback -> {path}"
    return f"{label}: {path}"


def _print_resolution_block(workspace: WorkspacePaths) -> None:
    """Print the five-file resolution block per DESIGN §7.4."""
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


# ---------------------------------------------------------------------------
# Ordered check registry (DESIGN §7.1 rows in order)
# ---------------------------------------------------------------------------

_CHECKS: tuple[CheckFn, ...] = (
    check_state_dir_exists,
    check_raw_inbox_dir_exists,
    check_wiki_subdirs_exist,
    check_sync_config_present,
    check_config_json_well_formed,
    check_env_present,
    check_sync_placeholder_sources,
    check_raw_inbox_empty,
    check_anthropic_api_key,
    check_gitignore_if_inside_outer_repo,
)


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """Doctor entry point.

    Signature matches the workspace-aware dispatch contract: every subcommand
    in DISPATCH is called as ``fn(remaining_argv, workspace)`` and returns an
    int exit code.  Doctor takes no arguments today; any extra argv is
    ignored (preserving 0.2.0 behavior where ``llm-wiki doctor`` silently
    discards trailing noise).
    """
    # argv is preserved for future compatibility; doctor takes no flags today.
    del argv

    try:
        ensure_workspace_exists(workspace)
    except WorkspaceError as exc:
        print(f"doctor: workspace error: {exc}")
        return 1

    _print_resolution_block(workspace)

    fails: list[str] = []
    warns: list[str] = []
    for check in _CHECKS:
        severity, message = check(workspace)
        if severity == "FAIL" and message is not None:
            fails.append(message)
        elif severity == "WARN" and message is not None:
            warns.append(message)
        # OK results are not printed; only the summary reflects them.

    for msg in fails:
        print(f"FAIL: {msg}")
    for msg in warns:
        print(f"WARN: {msg}")

    print(f"doctor: {len(fails)} failures, {len(warns)} warnings")
    return 1 if fails else 0
