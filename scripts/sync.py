"""Sync source files into a workspace's ``raw/inbox/``.

Workspace-aware refactor (LWC-btzz): every path flows from a
``WorkspacePaths`` instance passed to :func:`main`; no helper reaches into a
module global for a path.  See ARCHITECTURE §5.1-§5.3, §6, §7.3 for the full
contract.

The only module-level constant that remains is
:data:`SYNC_SCHEMA_VERSION`; it is a schema-version literal, not a path.
"""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from scripts.config_models import SyncConfig
from scripts.workspace import (
    WorkspacePaths,
    ensure_workspace_writable,
    resolve_sync_config,
)


SYNC_SCHEMA_VERSION = 1


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify_part(text: str) -> str:
    text = text.lower().strip()
    cleaned = []
    for ch in text:
        if ch.isalnum():
            cleaned.append(ch)
        else:
            cleaned.append("-")
    value = "".join(cleaned).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "untitled"


def normalize_patterns(values: List[str]) -> List[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def matches_any(path_text: str, patterns: List[str]) -> bool:
    return any(fnmatch(path_text, pattern) for pattern in patterns)


def iter_matching_files(root: Path, include: List[str], exclude: List[str]) -> List[Path]:
    matched = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if include and not matches_any(rel, include):
            continue
        if exclude and matches_any(rel, exclude):
            continue
        matched.append(path)
    return matched


def source_key(source_name: str, source_root: Path, rel_path: Path) -> str:
    return f"{source_name}::{source_root.resolve()}::{rel_path.as_posix()}"


def build_target_name(source_name: str, rel_path: Path, naming: Dict[str, Any]) -> str:
    mode = naming.get("mode", "preserve_path")
    prefix = slugify_part(naming.get("prefix", source_name))
    suffix = rel_path.suffix.lower()
    stem_parts = [prefix]

    if mode == "basename":
        stem_parts.append(slugify_part(rel_path.stem))
    else:
        for part in rel_path.with_suffix("").parts:
            stem_parts.append(slugify_part(part))

    stem = "__".join(part for part in stem_parts if part)
    return f"{stem}{suffix}"


def load_sync_manifest(workspace: WorkspacePaths) -> Dict[str, Any]:
    """Load the sync manifest from ``workspace.sync_manifest_path`` (or default)."""
    return load_json(workspace.sync_manifest_path, {"files": {}})


def prepare_sync_config(raw_config: Dict[str, Any], config_path: Path) -> tuple[Dict[str, Any], List[str]]:
    payload = dict(raw_config)
    warnings: List[str] = []
    deprecated_version = payload.pop("config_version", None)
    schema_version = payload.get("schema_version")

    if deprecated_version is not None:
        warnings.append(
            f"{config_path.name}: `config_version` is deprecated; use `schema_version`."
        )
        if schema_version is None:
            payload["schema_version"] = deprecated_version
            schema_version = deprecated_version
    if schema_version is None:
        payload["schema_version"] = SYNC_SCHEMA_VERSION
        warnings.append(
            f"{config_path.name}: missing `schema_version`; assuming `{SYNC_SCHEMA_VERSION}` for backward compatibility."
        )
        schema_version = SYNC_SCHEMA_VERSION
    if int(schema_version) != SYNC_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported sync config schema_version `{schema_version}` in {config_path}; expected `{SYNC_SCHEMA_VERSION}`."
        )
    return payload, warnings


def resolve_config_path(
    workspace: WorkspacePaths,
    explicit_config: Optional[str] = None,
) -> Path:
    """Resolve the sync config path for this invocation.

    Precedence:
      1. ``--config`` (``explicit_config``) when supplied.
      2. ``workspace.sync_config_path`` when it exists.
      3. ``workspace.sync_fallback_config_path`` otherwise.

    Unlike :func:`scripts.workspace.resolve_sync_config` this helper never
    raises on a missing fallback: it returns the fallback path so callers can
    print a user-facing "config not found at <path>" message.
    """
    if explicit_config:
        return Path(explicit_config).expanduser()
    try:
        path, _ = resolve_sync_config(workspace)
        return path
    except FileNotFoundError:
        return workspace.sync_fallback_config_path


def sync_file(
    workspace: WorkspacePaths,
    path: Path,
    source_name: str,
    source_root: Path,
    naming: Dict[str, Any],
    sync_manifest: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    rel_path = path.relative_to(source_root)
    rel_text = rel_path.as_posix()
    key = source_key(source_name, source_root, rel_path)
    source_sha = sha256_file(path)
    proposed_name = build_target_name(source_name, rel_path, naming)
    target_name = proposed_name
    existing = sync_manifest["files"].get(target_name)

    if existing and existing.get("source_key") != key:
        short = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        stem = Path(proposed_name).stem
        suffix = Path(proposed_name).suffix
        target_name = f"{stem}__{short}{suffix}"
        existing = sync_manifest["files"].get(target_name)

    target_path = workspace.raw_dir / target_name
    if target_path.exists() and not existing:
        short = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        stem = Path(proposed_name).stem
        suffix = Path(proposed_name).suffix
        target_name = f"{stem}__{short}{suffix}"
        target_path = workspace.raw_dir / target_name
        existing = sync_manifest["files"].get(target_name)

    status = "copied"
    if existing and existing.get("source_key") == key and existing.get("source_sha256") == source_sha:
        status = "unchanged"
    elif not dry_run:
        workspace.raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target_path)

    sync_manifest["files"][target_name] = {
        "source_name": source_name,
        "source_root": str(source_root.resolve()),
        "source_path": rel_text,
        "source_key": key,
        "source_sha256": source_sha,
        "synced_at": utc_now(),
    }

    return {
        "status": status,
        "source_path": rel_text,
        "target_name": target_name,
    }


def normalize_source(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(source["name"]),
        "root": Path(str(source["root"])).expanduser(),
        "include": normalize_patterns(source.get("include", [])),
        "exclude": normalize_patterns(source.get("exclude", [])),
        "naming": source.get("naming", {}),
    }


def prune_managed_files(
    workspace: WorkspacePaths,
    selected_source_names: List[str],
    desired_targets: set[str],
    available_source_names: set[str],
    sync_manifest: Dict[str, Any],
    dry_run: bool,
) -> int:
    removed = 0
    for target_name, record in list(sync_manifest.get("files", {}).items()):
        source_name = record.get("source_name")
        if source_name not in selected_source_names:
            continue
        if source_name not in available_source_names:
            continue
        if target_name in desired_targets:
            continue

        raw_path = workspace.raw_dir / target_name
        print(f"  pruned    raw/inbox/{target_name}")
        if not dry_run and raw_path.exists():
            raw_path.unlink()
        if not dry_run:
            sync_manifest["files"].pop(target_name, None)
        removed += 1
    return removed


def main(argv: list[str], workspace: WorkspacePaths) -> int:
    """Sync entry point.

    Signature matches the workspace-aware dispatch contract in
    ``scripts.cli``: every subcommand is called as
    ``fn(remaining_argv, workspace)`` and returns an int exit code.

    All paths are resolved from ``workspace``; no module-level path constants
    are consulted.
    """
    parser = argparse.ArgumentParser(description="Sync selected source files into raw/inbox.")
    parser.add_argument("--config", help="Path to sync config JSON.")
    parser.add_argument("--source", action="append", dest="sources", help="Specific source name(s) to sync.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without copying files.")
    parser.add_argument("--prune", action="store_true", help="Remove previously synced files that no longer match the active config.")
    args = parser.parse_args(argv)

    config_path = resolve_config_path(workspace, args.config)
    if not config_path.exists():
        print(f"Configuration error: sync config not found at {config_path}")
        return 0
    try:
        config = load_json(config_path, {})
        prepared_config, warnings = prepare_sync_config(config, config_path)
        validated = SyncConfig.model_validate(prepared_config)
        sources = [normalize_source(source) for source in validated.model_dump().get("sources", [])]
    except json.JSONDecodeError:
        print(f"Configuration error: invalid JSON in {config_path}")
        return 0
    except ValidationError as exc:
        print(f"Configuration error in {config_path}:")
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"])
            print(f"- {field}: {error['msg']}")
        return 0
    if args.sources:
        wanted = set(args.sources)
        sources = [source for source in sources if source["name"] in wanted]

    if not sources:
        print(f"No sync sources configured in {config_path}.")
        return 0

    for warning in warnings:
        print(f"Compatibility warning: {warning}")

    # Ensure writable subdirectories exist before we begin copying / writing
    # the manifest.  ``ensure_workspace_writable`` is idempotent.
    ensure_workspace_writable(workspace)

    sync_manifest = load_sync_manifest(workspace)
    total_copied = 0
    total_unchanged = 0
    total_pruned = 0
    desired_targets: set[str] = set()
    available_source_names: set[str] = set()
    selected_source_names = [source["name"] for source in sources]

    for source in sources:
        root = source["root"]
        if not root.exists():
            print(f"Skipping missing source root: {root}")
            continue

        available_source_names.add(source["name"])
        matches = iter_matching_files(root, source["include"], source["exclude"])
        print(f"[{source['name']}] matched {len(matches)} file(s)")

        for path in matches:
            result = sync_file(
                workspace=workspace,
                path=path,
                source_name=source["name"],
                source_root=root,
                naming=source["naming"],
                sync_manifest=sync_manifest,
                dry_run=args.dry_run,
            )
            desired_targets.add(result["target_name"])
            print(
                f"  {result['status']:9} "
                f"{result['source_path']} -> raw/inbox/{result['target_name']}"
            )
            if result["status"] == "unchanged":
                total_unchanged += 1
            else:
                total_copied += 1

    if args.prune:
        total_pruned = prune_managed_files(
            workspace=workspace,
            selected_source_names=selected_source_names,
            desired_targets=desired_targets,
            available_source_names=available_source_names,
            sync_manifest=sync_manifest,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        save_json(workspace.sync_manifest_path, sync_manifest)

    print(f"Done. Copied: {total_copied}. Unchanged: {total_unchanged}. Pruned: {total_pruned}.")
    return 0


if __name__ == "__main__":
    # Direct execution path: build a default workspace from the repo root.
    import sys as _sys

    from scripts.workspace import resolve_workspace

    raise SystemExit(main(_sys.argv[1:], resolve_workspace(None, None)))
