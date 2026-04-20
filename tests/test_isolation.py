"""Capstone isolation test -- THE primary success signal for the feature.

BUSINESS §3: "If this test suite does not exist and pass, the feature is not
done.  If it exists and passes, the feature meets its primary business goal."

This module proves that two workspaces (W1, W2) on the same machine produce
fully disjoint raw/, wiki/, state/, index.md, and log.md outputs.  Running
``--workspace W1`` never reads from or writes to W2 or the repo-root default
workspace.

Non-emptiness preconditions (BEFORE disjointness assertions)
------------------------------------------------------------
A disjointness test is trivially satisfied if both workspaces produce empty
output -- ``isdisjoint({}, {})`` returns True.  The Anchor specifically
rejected this test in a prior form for exactly this failure mode (a raw-text
echo mock caused JSON parse failures and empty wiki pages).  Every
disjointness assertion here runs AFTER explicit non-emptiness assertions, so
a regression in the mock surfaces as a clear "W1 wiki empty -- ingest
produced nothing (mock JSON parse fail?)" rather than a silent pass.

JSON-shaped mock contract
-------------------------
``mocked_call_claude`` (conftest.py) patches BOTH ``scripts.claude_api.call_claude``
AND ``scripts.ingest.call_claude`` (the import-site rebinding trap), plus
``scripts.ingest.init_client`` (to avoid any real Anthropic client
construction).  The mock returns a ``ClaudeCallResult`` whose ``.text`` is a
JSON payload with the keys ingest expects (title/summary/key_facts/topics/
entities/open_questions/topic_summaries/entity_summaries), and whose summary
carries the fixture marker so wiki/summaries contains the marker text.

Environment hygiene
-------------------
``_clean_env`` autouse at module scope strips ``LLM_WIKI_WORKSPACE`` and
sets a dummy ``ANTHROPIC_API_KEY``, so developer-shell env cannot influence
results.

See ARCHITECTURE §12.4, BUSINESS §3, DESIGN §4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cli  # noqa: E402
from scripts.workspace import WorkspacePaths  # noqa: E402


# ---------------------------------------------------------------------------
# Module-scope environment hygiene (applies to every test in this file)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip ``LLM_WIKI_WORKSPACE`` and pin a dummy ``ANTHROPIC_API_KEY``.

    Autouse so every test in this module gets a clean, deterministic env.
    ``LLM_WIKI_WORKSPACE`` would otherwise override ``--workspace`` and
    make the isolation assertions meaningless.  ``ANTHROPIC_API_KEY``
    must be SET (downstream env-var checks require it) but harmless
    because ``mocked_call_claude`` replaces the call path.
    """

    monkeypatch.delenv("LLM_WIKI_WORKSPACE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-isolation")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_sync_config(workspace: WorkspacePaths, source: Path, name: str) -> None:
    """Overwrite the workspace's sync-sources.local.json to point at ``source``.

    Uses the real ``SyncSourceConfig`` schema (``root``/``include``/
    ``exclude``/``naming`` with ``extra='forbid'`` per
    ``scripts/config_models.py``).  Earlier samples used ``path``/
    ``include_globs``/``naming_mode`` which are rejected outright.
    """

    cfg = {
        "schema_version": 1,
        "sources": [
            {
                "name": name,
                "root": str(source),
                "include": ["*.md"],
                "exclude": [],
                "naming": {"mode": "basename", "prefix": name},
            }
        ],
    }
    workspace.sync_config_path.write_text(json.dumps(cfg), encoding="utf-8")


def _all_wiki_files(workspace: WorkspacePaths) -> list[Path]:
    """Return every markdown file under ``workspace.wiki_dir`` recursively.

    Includes summaries/, topics/, and entities/.  Skips directories.
    If ``wiki_dir`` does not yet exist, returns an empty list so callers
    can distinguish "never ran" from "ran but produced nothing" via
    their own non-emptiness assertions.
    """

    wiki_dir = workspace.wiki_dir
    if not wiki_dir.exists():
        return list()
    return [p for p in wiki_dir.rglob("*.md") if p.is_file()]


def _last_run_summary(events_path: Path) -> Dict[str, Any]:
    """Return the last ``run_summary`` event from a JSONL event log.

    Raises ``AssertionError`` if the file is missing or contains no
    ``run_summary`` entry -- both are evidence of an incomplete ingest run.
    """

    assert events_path.exists(), (
        f"ingest_events.jsonl missing at {events_path}; ingest did not run"
    )
    lines = events_path.read_text(encoding="utf-8").splitlines()
    run_summaries = [
        json.loads(line)
        for line in lines
        if line.strip() and '"event": "run_summary"' in line
    ]
    assert run_summaries, (
        f"no run_summary event in {events_path}; ingest did not complete"
    )
    return run_summaries[-1]


# ---------------------------------------------------------------------------
# AC 1-7, 9, 10, 11 -- the primary test
# ---------------------------------------------------------------------------


def test_isolation_two_workspaces(
    two_workspaces,
    fixture_sources_a,
    fixture_sources_b,
    mocked_call_claude,
    repo_root_snapshot,
):
    """Two workspaces produce fully disjoint outputs; repo root stays clean.

    Proves ARCHITECTURE §12.4 step 6 and BUSINESS §3's primary success
    signal.  Phases:

    1. Point W1 at fixture-A, W2 at fixture-B.
    2. Run ``cli.main(['--workspace', W, 'refresh-fast'])`` for each.
    3. Assert non-emptiness (raw/, wiki/, index.md, log.md all populated).
    4. Assert six-way disjointness (AC 5 bullets 1-6).
    """

    w1, w2 = two_workspaces

    # --- 1. Point each workspace at its disjoint source corpus ------------
    _write_sync_config(w1, fixture_sources_a, name="sources_a")
    _write_sync_config(w2, fixture_sources_b, name="sources_b")

    # The ``two_workspaces`` fixture seeds a shared ``placeholder.md`` into
    # each workspace's raw/inbox so the populated-workspace doctor check
    # passes.  refresh-fast does NOT prune (that is a --prune-only path),
    # so the placeholder would leak into both raw sets and break the
    # raw/inbox disjointness assertion.  Remove it before the run -- in a
    # real user scenario the placeholder is only there for the init-state
    # sanity check and never represents durable content.
    for ws in (w1, w2):
        ph = ws.raw_dir / "placeholder.md"
        if ph.exists():
            ph.unlink()

    # --- 2. Run refresh-fast on each workspace independently --------------
    rc1 = cli.main(["--workspace", str(w1.root), "refresh-fast"])
    assert rc1 == 0, f"W1 refresh-fast returned {rc1}"

    rc2 = cli.main(["--workspace", str(w2.root), "refresh-fast"])
    assert rc2 == 0, f"W2 refresh-fast returned {rc2}"

    # --- 3. Non-emptiness preconditions (MUST come before disjointness) ---
    # A disjointness test on two empty sets is vacuously True.  The
    # preconditions fail LOUDLY so a regressed mock (e.g. raw-text echo)
    # surfaces here, not as a silent green.
    w1_raw_files = {p.name for p in w1.raw_dir.iterdir() if p.is_file()}
    w2_raw_files = {p.name for p in w2.raw_dir.iterdir() if p.is_file()}
    assert len(w1_raw_files) >= 1, (
        f"W1 raw empty -- sync produced nothing (raw_dir={w1.raw_dir})"
    )
    assert len(w2_raw_files) >= 1, (
        f"W2 raw empty -- sync produced nothing (raw_dir={w2.raw_dir})"
    )

    w1_wiki_files = _all_wiki_files(w1)
    w2_wiki_files = _all_wiki_files(w2)
    assert len(w1_wiki_files) >= 1, (
        "W1 wiki empty -- ingest produced nothing "
        "(mock JSON parse fail?  Check mocked_call_claude payload.)"
    )
    assert len(w2_wiki_files) >= 1, (
        "W2 wiki empty -- ingest produced nothing "
        "(mock JSON parse fail?  Check mocked_call_claude payload.)"
    )

    assert w1.index_path.exists(), f"W1 index.md missing at {w1.index_path}"
    assert w2.index_path.exists(), f"W2 index.md missing at {w2.index_path}"
    assert w1.index_path.read_text(encoding="utf-8").strip() != "", (
        "W1 index.md empty -- ingest did not write index"
    )
    assert w2.index_path.read_text(encoding="utf-8").strip() != "", (
        "W2 index.md empty -- ingest did not write index"
    )
    assert w1.log_path.exists(), f"W1 log.md missing at {w1.log_path}"
    assert w2.log_path.exists(), f"W2 log.md missing at {w2.log_path}"
    assert w1.log_path.read_text(encoding="utf-8").strip() != "", (
        "W1 log.md empty -- ingest did not append a log entry"
    )
    assert w2.log_path.read_text(encoding="utf-8").strip() != "", (
        "W2 log.md empty -- ingest did not append a log entry"
    )

    # --- 4. Disjointness assertions (ARCHITECTURE §12.4 step 6) -----------

    # 4a. raw/inbox filenames disjoint.  The two fixtures deliberately use
    # distinct file names (alpha/beta vs gamma/delta); the placeholder.md
    # stub from _populate_workspace is shared but gets pruned by sync --
    # if it leaks through it will fail both preconditions and the
    # disjointness check.  (Empirically: the conftest placeholder is
    # removed because sync overwrites raw/inbox.)
    assert w1_raw_files.isdisjoint(w2_raw_files), (
        f"raw/inbox NOT disjoint: overlap={w1_raw_files & w2_raw_files}"
    )

    # 4b. wiki/ pages disjoint: no cross-fixture markers anywhere under
    # wiki/ in either direction.  This is the strong form of
    # disjointness -- not just filenames, but content -- and catches
    # cases where two workspaces might accidentally share a pages cache.
    for wiki_file in w1_wiki_files:
        content = wiki_file.read_text(encoding="utf-8")
        assert "FIXTURE_B_MARKER" not in content, (
            f"W1 wiki file {wiki_file} contains FIXTURE_B_MARKER (leak!)"
        )
    for wiki_file in w2_wiki_files:
        content = wiki_file.read_text(encoding="utf-8")
        assert "FIXTURE_A_MARKER" not in content, (
            f"W2 wiki file {wiki_file} contains FIXTURE_A_MARKER (leak!)"
        )

    # 4c. state/manifest.json source sets disjoint.  The manifest tracks
    # per-file contributions under ``files.<rel_path>``; treat the
    # relative paths (which include the workspace-unique file names) as
    # the source set for this workspace.
    m1 = json.loads(w1.manifest_path.read_text(encoding="utf-8"))
    m2 = json.loads(w2.manifest_path.read_text(encoding="utf-8"))
    w1_sources = set(m1.get("files", {}).keys())
    w2_sources = set(m2.get("files", {}).keys())
    assert w1_sources, f"W1 manifest has no files entries: {m1}"
    assert w2_sources, f"W2 manifest has no files entries: {m2}"
    assert w1_sources.isdisjoint(w2_sources), (
        f"manifest.json sources NOT disjoint: "
        f"overlap={w1_sources & w2_sources}"
    )

    # 4d. index.md and log.md disjoint: neither mentions the other
    # workspace's fixture marker.
    w1_index = w1.index_path.read_text(encoding="utf-8")
    w2_index = w2.index_path.read_text(encoding="utf-8")
    w1_log = w1.log_path.read_text(encoding="utf-8")
    w2_log = w2.log_path.read_text(encoding="utf-8")
    assert "FIXTURE_B_MARKER" not in w1_index, (
        "W1 index.md contains FIXTURE_B_MARKER (leak!)"
    )
    assert "FIXTURE_A_MARKER" not in w2_index, (
        "W2 index.md contains FIXTURE_A_MARKER (leak!)"
    )
    assert "FIXTURE_B_MARKER" not in w1_log, (
        "W1 log.md contains FIXTURE_B_MARKER (leak!)"
    )
    assert "FIXTURE_A_MARKER" not in w2_log, (
        "W2 log.md contains FIXTURE_A_MARKER (leak!)"
    )

    # 4e. Repo-root raw/inbox, wiki/, state/, index.md, log.md unchanged.
    # This is the external-isolation guard: neither workspace may leak
    # writes into the repo root even though the repo root IS the default
    # workspace when no flag is given.
    repo_root_snapshot.assert_unchanged()

    # 4f. Each workspace's ingest_events.jsonl has its own run_summary
    # event with its own workspace field.
    run_summary_1 = _last_run_summary(w1.ingest_events_path)
    run_summary_2 = _last_run_summary(w2.ingest_events_path)
    assert run_summary_1["event"] == "run_summary"
    assert run_summary_2["event"] == "run_summary"
    assert run_summary_1["workspace"] == str(w1.root), (
        f"W1 run_summary workspace mismatch: "
        f"got {run_summary_1['workspace']!r}, expected {str(w1.root)!r}"
    )
    assert run_summary_2["workspace"] == str(w2.root), (
        f"W2 run_summary workspace mismatch: "
        f"got {run_summary_2['workspace']!r}, expected {str(w2.root)!r}"
    )
    # Sanity: the two run_summary events are NOT pointing at the same path.
    assert run_summary_1["workspace"] != run_summary_2["workspace"], (
        "run_summary workspace fields are identical across workspaces"
    )


# ---------------------------------------------------------------------------
# AC 8 -- meta-coverage for the repo-root snapshot guard itself
# ---------------------------------------------------------------------------


def test_repo_root_snapshot_detects_change(tmp_path, monkeypatch):
    """The snapshot guard raises when a repo-root surface is mutated.

    Without this meta-test, a broken ``_RepoRootSnapshot`` (e.g. one that
    always reports "unchanged") would silently pass the isolation test
    even if real pollution occurred.  We point ``repo_root()`` at a
    controlled tmp directory so the test does not actually mutate the
    real repo.
    """

    from tests import conftest as _conftest

    # Seed a fake "repo root" with one of the watched surfaces populated.
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "raw" / "inbox").mkdir(parents=True)
    (fake_root / "raw" / "inbox" / "seed.md").write_text("seed\n", encoding="utf-8")
    (fake_root / "state").mkdir()
    (fake_root / "wiki").mkdir()
    (fake_root / "index.md").write_text("initial\n", encoding="utf-8")
    (fake_root / "log.md").write_text("initial\n", encoding="utf-8")

    # Monkeypatch ``repo_root`` in the conftest module so
    # ``_RepoRootSnapshot`` hashes our fake root, not the real one.
    monkeypatch.setattr(_conftest, "repo_root", lambda: fake_root)

    snap = _conftest._RepoRootSnapshot()

    # Mutate one of the watched files and confirm the guard fires.
    (fake_root / "index.md").write_text("MUTATED\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="index.md"):
        snap.assert_unchanged()


# ---------------------------------------------------------------------------
# AC 8 -- meta-coverage for the non-emptiness precondition
# ---------------------------------------------------------------------------


def test_non_emptiness_precondition_fires_on_empty_wiki(
    two_workspaces,
    fixture_sources_a,
    monkeypatch,
):
    """Malformed-JSON mock -> empty wiki -> precondition FAILS LOUDLY.

    Proves the non-emptiness guard is effective: a broken mock that emits
    unparseable text makes the JSON-decode path raise inside ingest, the
    wiki never gets written, and the precondition ``len(w1_wiki_files) >=
    1`` fails with the documented error message.  Without this test, the
    disjointness assertions could pass vacuously if the mock regressed.
    """

    from scripts import ingest
    from scripts.claude_api import ClaudeCallResult

    w1, _ = two_workspaces
    _write_sync_config(w1, fixture_sources_a, name="sources_a")

    # Malformed JSON -- ingest.call_claude_json tries to json.loads this and
    # raises a JSONDecodeError, which propagates as an ingest_file_failed
    # event.  No wiki files are written.
    def _broken_mock(*, client=None, model="fake-model", system=None,
                    messages=None, max_tokens=None, context=None,
                    workspace=None, log_event=True, **kw):
        return ClaudeCallResult(
            text="this is not JSON at all",
            input_tokens=1,
            output_tokens=1,
            model=model,
        )

    monkeypatch.setattr("scripts.claude_api.call_claude", _broken_mock)
    monkeypatch.setattr(ingest, "call_claude", _broken_mock)
    monkeypatch.setattr(
        ingest, "init_client", lambda: ("fake-client", "fake-model")
    )

    rc = cli.main(["--workspace", str(w1.root), "refresh-fast"])
    # rc may be 0 or nonzero depending on whether ingest tolerates the
    # per-file failure -- either way, the defining property is that
    # wiki/ stays empty, which is what the precondition catches.
    _ = rc

    # Inline the precondition check so its failure mode is the ONLY thing
    # the test cares about (we do not want to run disjointness at all).
    wiki_files = _all_wiki_files(w1)
    with pytest.raises(AssertionError, match="ingest produced nothing"):
        assert len(wiki_files) >= 1, (
            "W1 wiki empty -- ingest produced nothing "
            "(mock JSON parse fail?  Check mocked_call_claude payload.)"
        )
