import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# After LWC-4z0t, cli dispatches to ``scripts.ingest.main`` via DISPATCH, so
# tests must patch the canonical ``scripts.ingest``/``scripts.sync`` modules
# that cli imports, not a sibling instance loaded via ``importlib.util``.
from scripts import cli, ingest, sync  # noqa: E402


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
    sync_config_text = json.dumps(
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
    )
    # Workspace-local primary copy (LWC-btzz: sync now resolves from workspace)
    (root / "sync-sources.local.json").write_text(sync_config_text, encoding="utf-8")
    # Also keep the plain name for any legacy reads in ingest/lint.
    (root / "sync-sources.json").write_text(sync_config_text, encoding="utf-8")
    (root / "ingest-settings.json").write_text(
        Path("ingest-settings.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # sync is now workspace-aware; route it by pointing LLM_WIKI_WORKSPACE at
    # the tmp project so ``cli.main`` resolves the workspace to this dir.
    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(root))

    # ingest no longer has module-level path constants (LWC-4z0t); it derives
    # paths from the WorkspacePaths passed via cli.DISPATCH.  We point cli at
    # this workspace by setting LLM_WIKI_WORKSPACE so resolve_workspace picks
    # up ``root`` during cli.main(['refresh-fast']).
    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(root))
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

    cli.main(["refresh-fast"])

    assert (raw_dir / "demo__readme.md").exists()
    assert (wiki_dir / "summaries" / "demo-readme.md").exists()
    summary = json.loads((state_dir / "last_ingest_run.json").read_text(encoding="utf-8"))
    assert summary["processed"] == 1
