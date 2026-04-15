import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLI_PATH = ROOT / "scripts" / "cli.py"
SYNC_PATH = ROOT / "scripts" / "sync.py"
INGEST_PATH = ROOT / "scripts" / "ingest.py"

cli_spec = importlib.util.spec_from_file_location("cli_module", CLI_PATH)
cli = importlib.util.module_from_spec(cli_spec)
assert cli_spec and cli_spec.loader
cli_spec.loader.exec_module(cli)

sync_spec = importlib.util.spec_from_file_location("sync_module", SYNC_PATH)
sync = importlib.util.module_from_spec(sync_spec)
assert sync_spec and sync_spec.loader
sync_spec.loader.exec_module(sync)

ingest_spec = importlib.util.spec_from_file_location("ingest_module", INGEST_PATH)
ingest = importlib.util.module_from_spec(ingest_spec)
assert ingest_spec and ingest_spec.loader
ingest_spec.loader.exec_module(ingest)


def test_refresh_fast_smoke_flow(tmp_path, monkeypatch):
    root = tmp_path
    source_root = root / "source"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text("# Demo Source\n\nA durable product README.", encoding="utf-8")

    raw_dir = root / "raw" / "inbox"
    wiki_dir = root / "wiki"
    state_dir = root / "state"
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "AGENTS.md").write_text("Schema", encoding="utf-8")
    (root / ".wikiignore").write_text("", encoding="utf-8")
    (root / "sync-sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "name": "demo",
                        "root": str(source_root),
                        "include": ["README.md"],
                        "exclude": [],
                        "naming": {"mode": "preserve_path", "prefix": "demo"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "ingest-settings.json").write_text(
        Path("ingest-settings.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr(sync, "ROOT", root)
    monkeypatch.setattr(sync, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sync, "STATE_DIR", state_dir)
    monkeypatch.setattr(sync, "SYNC_CONFIG_PATH", root / "sync-sources.local.json")
    monkeypatch.setattr(sync, "SYNC_FALLBACK_CONFIG_PATH", root / "sync-sources.json")
    monkeypatch.setattr(sync, "SYNC_MANIFEST_PATH", state_dir / "sync_manifest.json")

    monkeypatch.setattr(ingest, "ROOT", root)
    monkeypatch.setattr(ingest, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest, "WIKI_DIR", wiki_dir)
    monkeypatch.setattr(ingest, "SUMMARIES_DIR", wiki_dir / "summaries")
    monkeypatch.setattr(ingest, "TOPICS_DIR", wiki_dir / "topics")
    monkeypatch.setattr(ingest, "ENTITIES_DIR", wiki_dir / "entities")
    monkeypatch.setattr(ingest, "STATE_DIR", state_dir)
    monkeypatch.setattr(ingest, "MANIFEST_PATH", state_dir / "manifest.json")
    monkeypatch.setattr(ingest, "INDEX_PATH", root / "index.md")
    monkeypatch.setattr(ingest, "LOG_PATH", root / "log.md")
    monkeypatch.setattr(ingest, "SCHEMA_PATH", schemas_dir / "AGENTS.md")
    monkeypatch.setattr(ingest, "WIKIIGNORE_PATH", root / ".wikiignore")
    monkeypatch.setattr(ingest, "INGEST_SETTINGS_PATH", root / "ingest-settings.local.json")
    monkeypatch.setattr(ingest, "INGEST_FALLBACK_SETTINGS_PATH", root / "ingest-settings.json")
    monkeypatch.setattr(ingest, "LAST_INGEST_RUN_PATH", state_dir / "last_ingest_run.json")
    monkeypatch.setattr(ingest, "INGEST_EVENTS_PATH", state_dir / "ingest_events.jsonl")
    monkeypatch.setattr(ingest, "INGEST_REPORT_PATH", state_dir / "last_ingest_report.md")
    monkeypatch.setattr(ingest, "init_client", lambda: ("fake-client", "fake-model"))
    monkeypatch.setattr(
        ingest,
        "call_claude_json",
        lambda *args, **kwargs: {
            "title": "Demo Source",
            "summary": "Summary",
            "key_facts": ["Fact"],
            "topics": ["Capability Registry"],
            "entities": ["DemoMesh"],
            "open_questions": [],
            "topic_summaries": {"Capability Registry": "Registry summary"},
            "entity_summaries": {"DemoMesh": "Entity summary"},
        },
    )

    monkeypatch.setattr(cli, "sync", sync)
    monkeypatch.setattr(cli, "ingest", ingest)

    cli.main(["refresh-fast"])

    assert (raw_dir / "demo__readme.md").exists()
    assert (wiki_dir / "summaries" / "demo-readme.md").exists()
    summary = json.loads((state_dir / "last_ingest_run.json").read_text(encoding="utf-8"))
    assert summary["processed"] == 1
