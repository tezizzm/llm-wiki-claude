"""Shared test fixtures for workspace-aware commands.

``tmp_workspace`` hand-builds a workspace directory tree that mirrors what
``llm-wiki init`` will eventually produce (ARCHITECTURE §11.3).  Until that
command lands in a later epic, every fixture that needs a clean, populated
workspace for doctor/sync/ingest/query/lint tests lives here.

Once init is implemented, this fixture will be simplified to a single call
into ``init.main`` -- that refactor is tracked as its own story in the init
epic.

Isolation-test fixtures (LWC-rv1g): ``fixture_sources_a``,
``fixture_sources_b``, ``mocked_call_claude``, ``repo_root_snapshot`` support
the capstone disjointness test in ``test_isolation.py``.  They are imported
by name; keep their contracts stable -- the isolation test is the single
most important test in the suite (BUSINESS §3).
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workspace import WorkspacePaths, repo_root, resolve_workspace


def _populate_workspace(root: Path) -> None:
    """Create the minimum directory tree + config files for a clean workspace.

    Mirrors what ``init`` will produce.  The workspace is "clean" in the
    FAIL/WARN/OK sense: every structural prerequisite present, no warnings.
    """
    # Structural directories
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "topics").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)

    # Seed raw/inbox with one placeholder so "empty" WARN doesn't fire.
    (root / "raw" / "inbox" / "placeholder.md").write_text(
        "# placeholder\n", encoding="utf-8"
    )

    # sync-sources.local.json with a real, existing source root.
    sources_root = root / "sources"
    sources_root.mkdir(exist_ok=True)
    sync_config = {
        "schema_version": 1,
        "sources": [{"name": "test", "root": str(sources_root)}],
    }
    (root / "sync-sources.local.json").write_text(
        json.dumps(sync_config), encoding="utf-8"
    )

    # ingest-settings.local.json -- empty object is valid (defaults merge).
    (root / "ingest-settings.local.json").write_text("{}", encoding="utf-8")

    # .env with a real API key
    (root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n", encoding="utf-8")


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> WorkspacePaths:
    """Build a clean workspace under ``tmp_path`` and return its WorkspacePaths.

    The returned ``WorkspacePaths`` carries ``source='flag'`` so that banner
    and resolution logic see a non-default workspace.  Tests that need the
    repo-root ``default`` case should use ``resolve_workspace(None, None)``
    directly.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    _populate_workspace(root)
    # Build via resolve_workspace so every derived field is computed in the
    # canonical way (and so the source is 'flag', matching real CLI usage).
    return resolve_workspace(str(root), None)


@pytest.fixture
def two_workspaces(tmp_path: Path) -> tuple[WorkspacePaths, WorkspacePaths]:
    """Two independent clean workspaces in the same tmp_path.

    Used by isolation tests (an operation against workspace A must not touch
    files under workspace B).
    """
    root_a = tmp_path / "workspace_a"
    root_b = tmp_path / "workspace_b"
    root_a.mkdir()
    root_b.mkdir()
    _populate_workspace(root_a)
    _populate_workspace(root_b)
    return (
        resolve_workspace(str(root_a), None),
        resolve_workspace(str(root_b), None),
    )


# ---------------------------------------------------------------------------
# Isolation-test fixtures (LWC-rv1g)
# ---------------------------------------------------------------------------
#
# The isolation capstone (BUSINESS §3) proves that two workspaces produce
# fully disjoint raw/, wiki/, state/, index.md, and log.md outputs without
# touching the repo-root default workspace.  These fixtures supply the
# disjoint source corpora, the JSON-shaped mock (raw-text echo would cause
# vacuous passes -- see story), and the repo-root snapshot guard.


# Padding prose is durable-knowledge content that clears the low-signal
# filter (IngestSettings.max_source_chars floor + name_patterns).  Each
# fixture places its marker in the first 200 chars so it propagates through
# the JSON echo mock into the wiki output.
_PADDING_A = "Durable knowledge about alpha subsystem. " * 60
_PADDING_B = "Durable knowledge about beta subsystem. " * 60


@pytest.fixture
def fixture_sources_a(tmp_path: Path) -> Path:
    """Source directory whose files carry FIXTURE_A_MARKER in the heading.

    Padded past the low-signal floor so ingest does not skip these files;
    the marker sits within the first 200 chars so the JSON-echo mock
    captures it in the summary field and propagates it into the wiki.
    """

    src = tmp_path / "sources_a"
    src.mkdir()
    (src / "alpha.md").write_text(
        "# Alpha FIXTURE_A_MARKER\n" + _PADDING_A,
        encoding="utf-8",
    )
    (src / "beta.md").write_text(
        "# Beta FIXTURE_A_MARKER\n" + _PADDING_A,
        encoding="utf-8",
    )
    return src


@pytest.fixture
def fixture_sources_b(tmp_path: Path) -> Path:
    """Source directory whose files carry FIXTURE_B_MARKER in the heading.

    Counterpart to ``fixture_sources_a``; deliberately distinct file
    names (gamma/delta) so the raw/ disjointness check is non-trivial.
    """

    src = tmp_path / "sources_b"
    src.mkdir()
    (src / "gamma.md").write_text(
        "# Gamma FIXTURE_B_MARKER\n" + _PADDING_B,
        encoding="utf-8",
    )
    (src / "delta.md").write_text(
        "# Delta FIXTURE_B_MARKER\n" + _PADDING_B,
        encoding="utf-8",
    )
    return src


@pytest.fixture
def mocked_call_claude(monkeypatch):
    """Install a JSON-shaped ``call_claude`` replacement (dual-patched).

    Why JSON-shaped, not raw echo: ``scripts.ingest.call_claude_json`` parses
    ``result.text`` as JSON (ARCHITECTURE §10.2).  A raw text echo causes
    JSON parse failure -> empty wiki -> ``isdisjoint({}, {})`` is vacuously
    True.  The Anchor previously rejected this test for exactly that reason.

    Dual patch rationale: ``scripts.ingest`` does ``from scripts.claude_api
    import call_claude`` at module load, so patching only the origin module
    leaves ``scripts.ingest.call_claude`` still pointing at the real
    function.  We patch BOTH bindings so the ingest pipeline actually sees
    the mock.  We also patch ``scripts.ingest.init_client`` so no real
    Anthropic client is constructed during the test (even though the mock
    would short-circuit the call, ``init_client()`` still gets invoked in
    the non-dry-run path and would hit the network without a patch).

    The payload carries the source heading through the ``title`` and
    ``summary`` fields so FIXTURE_A_MARKER / FIXTURE_B_MARKER propagate
    into wiki/summaries/*.md -- which is the signal the disjointness
    assertions actually check.
    """

    from scripts import ingest
    from scripts.claude_api import ClaudeCallResult

    def _json_echo(*, client=None, model="fake-model", system=None,
                   messages=None, max_tokens=None, context=None,
                   workspace=None, log_event=True, **kw):
        user_prompt = ""
        if messages:
            last = messages[-1]
            content = last.get("content", "") if isinstance(last, dict) else ""
            if isinstance(content, str):
                user_prompt = content
        # Extract the first heading (# Title) out of the user prompt so the
        # marker travels into the wiki output.  The ingest prompt includes
        # the raw source verbatim, so the marker lives inside user_prompt.
        snippet = user_prompt[:400]
        payload = {
            "title": f"Summary of {context or 'source'}",
            "summary": f"Durable knowledge captured. Snippet: {snippet}",
            "key_facts": [f"Fact derived from {context or 'source'}."],
            "topics": [],
            "entities": [],
            "open_questions": [],
            "topic_summaries": {},
            "entity_summaries": {},
        }
        text = json.dumps(payload)
        return ClaudeCallResult(
            text=text,
            input_tokens=max(1, len(user_prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            model=model,
        )

    monkeypatch.setattr("scripts.claude_api.call_claude", _json_echo)
    monkeypatch.setattr(ingest, "call_claude", _json_echo)
    monkeypatch.setattr(
        ingest, "init_client", lambda: ("fake-client", "fake-model")
    )
    return _json_echo


class _RepoRootSnapshot:
    """Byte-hash snapshot of the repo-root workspace surfaces we must not mutate.

    Captures ``raw/inbox``, ``wiki/``, ``state/``, ``index.md``, and
    ``log.md`` at the repo root (the default workspace) so the isolation
    test can prove that running ``--workspace W1`` / ``--workspace W2``
    never writes to the repo root.
    """

    _TARGETS = ("raw/inbox", "wiki", "state", "index.md", "log.md")

    def __init__(self) -> None:
        self.root = repo_root()
        self.hashes: dict[str, object] = {}
        for rel in self._TARGETS:
            target = self.root / rel
            self.hashes[rel] = self._hash(target)

    @staticmethod
    def _hash(path: Path):
        """Return None if absent, hex sha256 for a file, or dict for a dir.

        Directory hashing enumerates every file recursively so appended
        or deleted files both show up as a dict-level diff.
        """

        if not path.exists():
            return None
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        out: dict[str, str] = {}
        for p in sorted(path.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(path))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
        return out

    def assert_unchanged(self) -> None:
        """Raise AssertionError if any captured surface has changed."""

        for rel, old in self.hashes.items():
            target = self.root / rel
            new = self._hash(target)
            assert old == new, (
                f"Repo-root {rel} changed during isolation test "
                f"(expected unchanged). Was: {old!r}; now: {new!r}"
            )


@pytest.fixture
def repo_root_snapshot() -> _RepoRootSnapshot:
    """Snapshot repo-root workspace surfaces and expose ``assert_unchanged()``.

    Exposed as a factory-free fixture: the snapshot is taken at fixture
    setup, before the test body runs.  Call ``.assert_unchanged()`` at the
    end of the test to prove no repo-root mutation occurred.
    """

    return _RepoRootSnapshot()
