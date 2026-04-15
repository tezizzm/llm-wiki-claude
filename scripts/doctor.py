import json
import sys
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from scripts import ingest, sync
from scripts.config_models import IngestSettingsConfig, SyncConfig
from scripts.version import read_version

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    messages: list[str] = []
    ok = True

    # --- Version display ---
    messages.append(f"Version: {read_version()}")

    # --- Python version check ---
    ver = sys.version_info
    ver_str = sys.version.split()[0]
    if ver >= (3, 9):
        messages.append(f"[PASS] Python version -- {ver_str}")
    else:
        ok = False
        messages.append(f"[FAIL] Python version -- {ver_str} (requires >= 3.9)")

    # --- .env / API key check ---
    env_path = ROOT / ".env"
    if env_path.exists():
        env_vars = dotenv_values(env_path)
        api_key = env_vars.get("ANTHROPIC_API_KEY", "")
        if api_key and api_key != "your_anthropic_api_key_here":
            messages.append("[PASS] Environment -- .env found with API key")
        else:
            ok = False
            messages.append("[FAIL] Environment -- ANTHROPIC_API_KEY is not set or still placeholder")
            messages.append("       Hint: Set a real Anthropic API key in .env")
    else:
        ok = False
        messages.append("[FAIL] Environment -- .env file not found")
        messages.append("       Hint: Copy .env.example to .env and paste your Anthropic API key.")

    # --- Sync config check (existing, reformatted) ---
    sync_path = sync.resolve_config_path()
    sync_valid = False
    prepared_sync = None
    if sync_path.exists():
        try:
            raw_sync = sync.load_json(sync_path, {})
            prepared_sync, sync_warnings = sync.prepare_sync_config(raw_sync, sync_path)
            SyncConfig.model_validate(prepared_sync)
            messages.append(f"[PASS] Sync config -- {sync_path}")
            sync_valid = True
            for warning in sync_warnings:
                messages.append(f"       Sync warning: {warning}")
        except (json.JSONDecodeError, ValidationError, RuntimeError) as exc:
            ok = False
            messages.append(f"[FAIL] Sync config -- {exc}")
    else:
        ok = False
        messages.append(f"[FAIL] Sync config -- missing ({sync_path})")

    # --- Source paths check (only if sync config is valid) ---
    if sync_valid and prepared_sync is not None:
        validated = SyncConfig.model_validate(prepared_sync)
        unreachable = []
        for source in validated.sources:
            if not Path(source.root).expanduser().is_dir():
                unreachable.append((source.root, source.name))
        if unreachable:
            ok = False
            messages.append(f"[FAIL] Source paths -- {len(unreachable)} source roots not found")
            for root, name in unreachable:
                messages.append(f"       - {root} (source: {name})")
            messages.append(f"       Hint: Check the \"root\" values in {sync_path}.")
        else:
            messages.append("[PASS] Source paths -- all source roots reachable")

    # --- Ingest settings check (existing, reformatted) ---
    ingest_path = ingest.resolve_ingest_settings_path()
    if ingest_path.exists():
        try:
            raw_ingest = ingest.load_json_file(ingest_path, {})
            prepared_ingest, ingest_warnings = ingest.prepare_ingest_settings(raw_ingest, ingest_path)
            merged_ingest = ingest.merge_dicts(ingest.DEFAULT_INGEST_SETTINGS, prepared_ingest)
            IngestSettingsConfig.model_validate(merged_ingest)
            messages.append(f"[PASS] Ingest settings -- {ingest_path}")
            for warning in ingest_warnings:
                messages.append(f"       Ingest warning: {warning}")
        except (json.JSONDecodeError, ValidationError, RuntimeError) as exc:
            ok = False
            messages.append(f"[FAIL] Ingest settings -- {exc}")
    else:
        ok = False
        messages.append(f"[FAIL] Ingest settings -- missing ({ingest_path})")

    # --- Wiki output directory check ---
    wiki_dir = ROOT / "wiki"
    try:
        wiki_dir.mkdir(parents=True, exist_ok=True)
        messages.append("[PASS] Wiki output -- wiki/ directory ready")
    except OSError:
        ok = False
        messages.append("[FAIL] Wiki output -- cannot create wiki/ directory")
        messages.append("       Hint: Check file permissions in the project root.")

    # --- Demo artifacts check (existing, reformatted) ---
    demo_paths = [
        Path("demo/sample-output/index.md"),
        Path("demo/sample-output/last_ingest_run.json"),
        Path("demo/sample-output/last_ingest_report.md"),
    ]
    missing_demo = [str(path) for path in demo_paths if not path.exists()]
    if missing_demo:
        ok = False
        messages.append(f"[FAIL] Demo artifacts -- missing {', '.join(missing_demo)}")
    else:
        messages.append("[PASS] Demo artifacts")

    print("\n".join(messages))
    return 0 if ok else 1
