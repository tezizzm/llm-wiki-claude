"""E2e tests: HTML, RST, and DOCX files through the full ingest pipeline.

Story LWC-ijhd -- validates that extract_html_text, extract_rst_text, and
extract_docx_text integrate correctly with the ingest pipeline, producing
summary pages in wiki/summaries/ and index.md references.

Only the Anthropic API client is mocked; all file I/O is real.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# After LWC-4z0t, cli dispatches to ``scripts.ingest.main`` via DISPATCH, so
# patches must target the canonical ``scripts.ingest`` module that cli
# imports, not a sibling instance loaded through ``importlib.util``.
from scripts import cli, ingest, sync  # noqa: E402


FAKE_CLAUDE_RESPONSE = {
    "title": "Test Page",
    "summary": "A meaningful summary of the ingested content.",
    "key_facts": ["Extracted from a real file"],
    "topics": ["Document Processing"],
    "entities": ["TestProject"],
    "open_questions": [],
    "topic_summaries": {"Document Processing": "How documents get processed"},
    "entity_summaries": {"TestProject": "The test project entity"},
}


def _create_docx(path: Path, text: str) -> None:
    """Create a real .docx file with the given paragraph text."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    doc.save(str(path))


def _scaffold(tmp_path, monkeypatch):
    """Set up a tmp workspace with all dirs and monkeypatch ingest/sync/cli paths."""
    root = tmp_path
    source_root = root / "source"
    source_root.mkdir(parents=True)

    raw_dir = root / "raw" / "inbox"
    wiki_dir = root / "wiki"
    state_dir = root / "state"
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "AGENTS.md").write_text("Schema", encoding="utf-8")
    (root / ".wikiignore").write_text("", encoding="utf-8")
    (root / "ingest-settings.json").write_text(
        Path(ROOT / "ingest-settings.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr(sync, "ROOT", root)
    monkeypatch.setattr(sync, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sync, "STATE_DIR", state_dir)
    monkeypatch.setattr(sync, "SYNC_CONFIG_PATH", root / "sync-sources.local.json")
    monkeypatch.setattr(sync, "SYNC_FALLBACK_CONFIG_PATH", root / "sync-sources.json")
    monkeypatch.setattr(sync, "SYNC_MANIFEST_PATH", state_dir / "sync_manifest.json")

    # ingest no longer has module-level path constants (LWC-4z0t); it derives
    # paths from the WorkspacePaths passed via cli.DISPATCH.  We point cli at
    # this workspace by setting LLM_WIKI_WORKSPACE so resolve_workspace picks
    # up ``root`` during cli.main(['refresh-fast']).
    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(root))
    monkeypatch.setattr(ingest, "init_client", lambda: ("fake-client", "fake-model"))
    monkeypatch.setattr(
        ingest,
        "call_claude_json",
        lambda *args, **kwargs: FAKE_CLAUDE_RESPONSE,
    )

    return root, source_root, raw_dir, wiki_dir, state_dir


def _write_sync_sources(root, source_root, files):
    """Write sync-sources.json to include the specified files from source_root."""
    (root / "sync-sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "name": "demo",
                        "root": str(source_root),
                        "include": files,
                        "exclude": [],
                        "naming": {"mode": "preserve_path", "prefix": "demo"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# AC 1: HTML file -> ingest pipeline -> summary page in wiki/summaries/
# ---------------------------------------------------------------------------


def test_html_file_e2e(tmp_path, monkeypatch):
    root, source_root, raw_dir, wiki_dir, state_dir = _scaffold(tmp_path, monkeypatch)
    html_content = "<html><head><title>Test</title></head><body><h1>Hello HTML</h1><p>Real content here.</p></body></html>"
    (source_root / "page.html").write_text(html_content, encoding="utf-8")
    _write_sync_sources(root, source_root, ["page.html"])

    cli.main(["refresh-fast"])

    # Summary page was produced
    summary_path = wiki_dir / "summaries" / "demo-page.md"
    assert summary_path.exists(), f"Expected summary at {summary_path}"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Test Page" in summary_text

    # extract_text produces real content, not placeholder
    raw_file = raw_dir / "demo__page.html"
    assert raw_file.exists()
    extracted = ingest.extract_text(raw_file)
    assert "Hello HTML" in extracted
    assert "[" not in extracted or "HTML parsed but no extractable text" not in extracted


# ---------------------------------------------------------------------------
# AC 2: RST file -> ingest pipeline -> summary page
# ---------------------------------------------------------------------------


def test_rst_file_e2e(tmp_path, monkeypatch):
    root, source_root, raw_dir, wiki_dir, state_dir = _scaffold(tmp_path, monkeypatch)
    rst_content = (
        "==========\n"
        "RST Title\n"
        "==========\n"
        "\n"
        "This is a reStructuredText document with real content.\n"
        "\n"
        "- Item one\n"
        "- Item two\n"
    )
    (source_root / "notes.rst").write_text(rst_content, encoding="utf-8")
    _write_sync_sources(root, source_root, ["notes.rst"])

    cli.main(["refresh-fast"])

    summary_path = wiki_dir / "summaries" / "demo-notes.md"
    assert summary_path.exists(), f"Expected summary at {summary_path}"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Test Page" in summary_text

    raw_file = raw_dir / "demo__notes.rst"
    assert raw_file.exists()
    extracted = ingest.extract_text(raw_file)
    assert "RST Title" in extracted
    assert "reStructuredText document" in extracted
    assert "[RST parsed but no extractable text" not in extracted


# ---------------------------------------------------------------------------
# AC 3: DOCX file -> ingest pipeline -> summary page
# ---------------------------------------------------------------------------


def test_docx_file_e2e(tmp_path, monkeypatch):
    root, source_root, raw_dir, wiki_dir, state_dir = _scaffold(tmp_path, monkeypatch)
    _create_docx(source_root / "report.docx", "This is a DOCX document with substantial content for testing.")
    _write_sync_sources(root, source_root, ["report.docx"])

    cli.main(["refresh-fast"])

    summary_path = wiki_dir / "summaries" / "demo-report.md"
    assert summary_path.exists(), f"Expected summary at {summary_path}"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Test Page" in summary_text

    raw_file = raw_dir / "demo__report.docx"
    assert raw_file.exists()
    extracted = ingest.extract_text(raw_file)
    assert "DOCX document with substantial content" in extracted
    assert "[DOCX parsed but no extractable text" not in extracted


# ---------------------------------------------------------------------------
# AC 4: All three file types together -> all produce summary pages and
#        index.md references them
# ---------------------------------------------------------------------------


def test_all_file_types_together_e2e(tmp_path, monkeypatch):
    root, source_root, raw_dir, wiki_dir, state_dir = _scaffold(tmp_path, monkeypatch)

    # Create all three file types with distinct content
    html_content = "<html><body><h1>Combined HTML</h1><p>HTML body text.</p></body></html>"
    (source_root / "page.html").write_text(html_content, encoding="utf-8")

    rst_content = (
        "Combined RST\n"
        "=============\n"
        "\n"
        "RST body text for combined test.\n"
    )
    (source_root / "notes.rst").write_text(rst_content, encoding="utf-8")

    _create_docx(source_root / "report.docx", "DOCX body text for combined test.")

    _write_sync_sources(root, source_root, ["page.html", "notes.rst", "report.docx"])

    cli.main(["refresh-fast"])

    # All three summary pages were produced
    expected_summaries = [
        wiki_dir / "summaries" / "demo-page.md",
        wiki_dir / "summaries" / "demo-notes.md",
        wiki_dir / "summaries" / "demo-report.md",
    ]
    for sp in expected_summaries:
        assert sp.exists(), f"Expected summary at {sp}"

    # index.md was written and references all three summaries
    index_path = root / "index.md"
    assert index_path.exists(), "index.md should exist"
    index_text = index_path.read_text(encoding="utf-8")
    assert "Demo Page" in index_text or "demo-page" in index_text
    assert "Demo Notes" in index_text or "demo-notes" in index_text
    assert "Demo Report" in index_text or "demo-report" in index_text

    # Ingest run summary confirms all three were processed
    run_data = json.loads((state_dir / "last_ingest_run.json").read_text(encoding="utf-8"))
    assert run_data["processed"] == 3


# ---------------------------------------------------------------------------
# AC 6: extract_text() produces non-placeholder content for each file type
# ---------------------------------------------------------------------------


def test_extract_text_produces_real_content(tmp_path):
    """Verify extract_text dispatches correctly and returns real content,
    not placeholder strings, for HTML, RST, and DOCX files."""

    # HTML
    html_path = tmp_path / "sample.html"
    html_path.write_text(
        "<html><body><p>Real HTML paragraph content.</p></body></html>",
        encoding="utf-8",
    )
    html_text = ingest.extract_text(html_path)
    assert "Real HTML paragraph content" in html_text
    assert not html_text.startswith("[")

    # RST
    rst_path = tmp_path / "sample.rst"
    rst_path.write_text(
        "Title\n=====\n\nReal RST paragraph content.\n",
        encoding="utf-8",
    )
    rst_text = ingest.extract_text(rst_path)
    assert "Real RST paragraph content" in rst_text
    assert not rst_text.startswith("[")

    # DOCX
    docx_path = tmp_path / "sample.docx"
    _create_docx(docx_path, "Real DOCX paragraph content.")
    docx_text = ingest.extract_text(docx_path)
    assert "Real DOCX paragraph content" in docx_text
    assert not docx_text.startswith("[")
