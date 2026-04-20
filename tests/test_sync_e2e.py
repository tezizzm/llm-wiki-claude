"""End-to-end test for ``llm-wiki --workspace PATH sync`` (epic LWC-iof3 capstone; story LWC-w397).

Proves the primary user-visible outcome for the sync-refactor epic: a user
can scaffold a fresh workspace via ``llm-wiki init`` and then, with a real
``sync-sources.local.json`` pointing at a real source directory, run
``llm-wiki --workspace PATH sync`` and see actual files copied into
``PATH/raw/inbox/`` with ``PATH/state/sync_manifest.json`` written.

The flow runs entirely through real ``subprocess`` invocations of
``python -m scripts.cli`` -- there are NO mocks, stubs, fakes, or
monkeypatches. Every assertion checks what actually ended up on disk or
what actually came back on stdout.

Contract references:

- DESIGN §4.1/§4.2 -- ``Workspace: <path> (from --workspace)`` banner plus
  blank line when the workspace was resolved via the ``--workspace`` flag.
- ARCHITECTURE §5.1-§5.3, §6, §7.3 -- sync resolves every path via a
  ``WorkspacePaths`` instance, writes into ``workspace.raw_dir`` and
  ``workspace.sync_manifest_path``.
- Story AC-1..AC-4 (LWC-w397) -- the assertions below are 1:1 with the
  acceptance criteria in the story.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(
    cmd_args: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``python -m scripts.cli ...`` as a real subprocess.

    We always strip ``LLM_WIKI_WORKSPACE`` from the child env so that the
    developer running the tests locally with an exported workspace does
    not silently change the target; callers that want it set must pass it
    explicitly.

    ``ANTHROPIC_API_KEY`` gets a dummy default because the child process
    loads ``.env`` via ``scripts.workspace.load_env`` and some codepaths
    expect *some* value to be present. Sync itself does not call Claude --
    AC-4 -- so the dummy value is sufficient.

    ``PYTHONPATH`` is pinned to this worktree's repo root so the child
    imports this worktree's ``scripts`` package, not a globally-installed
    copy that might lag behind.
    """

    env_use = {**os.environ, **(env or {})}
    env_use.pop("LLM_WIKI_WORKSPACE", None)
    if env and "LLM_WIKI_WORKSPACE" in env:
        env_use["LLM_WIKI_WORKSPACE"] = env["LLM_WIKI_WORKSPACE"]
    env_use.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy-sync-e2e")
    env_use["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env_use.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "scripts.cli", *cmd_args],
        capture_output=True,
        text=True,
        env=env_use,
        cwd=str(REPO_ROOT),
    )


def test_sync_copies_files_into_workspace(tmp_path: Path) -> None:
    """``llm-wiki --workspace X sync`` copies real source files into X/raw/inbox/.

    Steps (AC-1..AC-4 of LWC-w397):
      1. Create a real source directory with two ``.md`` files.
      2. ``llm-wiki init <ws>`` -- scaffold the workspace through the real CLI.
      3. Overwrite ``<ws>/sync-sources.local.json`` with a valid config that
         points at the real source root we just populated. The config uses
         the real ``SyncConfig`` schema (``root`` / ``include`` / nested
         ``naming``) so it validates against ``scripts.config_models``.
      4. ``llm-wiki --workspace <ws> sync`` -- run sync through the real CLI.
      5. Assert raw/inbox/ contains recognizable copies of both source files.
      6. Assert the banner printed because ``--workspace`` was passed
         (DESIGN §4.1/§4.2).
      7. Assert ``<ws>/state/sync_manifest.json`` was written.
    """
    ws = tmp_path / "ws"
    src = tmp_path / "src_repo"
    src.mkdir()
    (src / "note_alpha.md").write_text("# Alpha\nContent A.\n", encoding="utf-8")
    (src / "note_beta.md").write_text("# Beta\nContent B.\n", encoding="utf-8")

    # Scaffold workspace via real init -- no test-only shortcut.
    r_init = _run(["init", str(ws)])
    assert r_init.returncode == 0, (
        f"init exit {r_init.returncode}\n"
        f"stdout:\n{r_init.stdout}\nstderr:\n{r_init.stderr}"
    )
    # Shape sanity: init must have produced the destination we're about to
    # overwrite with a real config.
    assert (ws / "sync-sources.local.json").is_file()
    assert (ws / "raw" / "inbox").is_dir()
    assert (ws / "state").is_dir()

    # Point the workspace at the real source directory. The payload below
    # matches the real ``SyncConfig`` schema (scripts/config_models.py), which
    # declares ``extra='forbid'`` -- unknown keys would make sync skip the
    # config silently, so this literal shape matters.
    cfg = {
        "schema_version": 1,
        "sources": [
            {
                "name": "src_repo",
                "root": str(src),
                "include": ["*.md"],
                "exclude": [],
                "naming": {"mode": "basename", "prefix": "src_repo"},
            }
        ],
    }
    (ws / "sync-sources.local.json").write_text(
        json.dumps(cfg), encoding="utf-8"
    )

    # Run sync through the CLI with the --workspace flag.
    r = _run(["--workspace", str(ws), "sync"])
    assert r.returncode == 0, (
        f"sync exit {r.returncode}\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )

    # AC-3: >= 1 file in raw/inbox/ with recognizable source names. We
    # populated two sources and expect two destinations -- assert on both so
    # a regression that loses one of them still fails loudly.
    inbox = ws / "raw" / "inbox"
    files = list(inbox.iterdir())
    assert len(files) >= 2, (
        f"sync produced fewer than two files in {inbox}: {files}\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    # Collision-safe naming slugifies non-alphanumerics to ``-``, so
    # ``note_alpha.md`` lands as ``src-repo__note-alpha.md`` (LWC-btzz's
    # ``slugify_part`` in scripts/sync.py). Recognize the stem in either
    # ``_`` or ``-`` form so the assertion survives future naming tweaks
    # that merely change the separator policy.
    def _has_stem(stem: str, fnames: list[str]) -> bool:
        needles = {stem, stem.replace("_", "-"), stem.replace("-", "_")}
        return any(any(needle in n for needle in needles) for n in fnames)

    names = [p.name for p in files]
    assert _has_stem("note_alpha", names), (
        f"missing note_alpha in {names}\nstdout:\n{r.stdout}"
    )
    assert _has_stem("note_beta", names), (
        f"missing note_beta in {names}\nstdout:\n{r.stdout}"
    )

    # Files on disk must match the source bytes -- real copies, not
    # empty placeholders.
    alpha_dest = next(
        p for p in files if "note-alpha" in p.name or "note_alpha" in p.name
    )
    beta_dest = next(
        p for p in files if "note-beta" in p.name or "note_beta" in p.name
    )
    assert alpha_dest.read_text(encoding="utf-8") == "# Alpha\nContent A.\n"
    assert beta_dest.read_text(encoding="utf-8") == "# Beta\nContent B.\n"

    # AC-3 (banner): ``--workspace`` triggers the DESIGN §4.1/§4.2 banner.
    assert "Workspace:" in r.stdout, (
        f"banner missing from sync stdout:\n{r.stdout}"
    )
    assert f"Workspace: {ws.resolve()} (from --workspace)" in r.stdout, (
        f"banner text mismatch; got:\n{r.stdout}"
    )

    # Sync also prints a ``Done. Copied: N.`` summary (scripts/sync.py).
    # At least 1 file must have been copied.
    assert "Done. Copied:" in r.stdout, (
        f"missing sync summary in stdout:\n{r.stdout}"
    )
    assert "Done. Copied: 0." not in r.stdout, (
        f"sync reported 0 files copied; stdout:\n{r.stdout}"
    )

    # AC-3 (manifest): sync manifest written under the workspace's state/.
    assert (ws / "state" / "sync_manifest.json").is_file(), (
        f"sync_manifest.json missing under {ws / 'state'}"
    )
    # Manifest content is valid JSON with a 'files' key recording both
    # targets -- this is what enables safe ``sync --prune`` later.
    manifest = json.loads(
        (ws / "state" / "sync_manifest.json").read_text(encoding="utf-8")
    )
    assert "files" in manifest, f"manifest missing 'files' key: {manifest}"
    assert len(manifest["files"]) >= 2, (
        f"manifest should record both source files; got: {manifest}"
    )
