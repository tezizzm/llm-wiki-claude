"""Unit tests for scripts.doctor with the FAIL/WARN/OK policy (DESIGN §7).

All tests here use the ``tmp_workspace`` fixture from tests/conftest.py;
monkeypatching of module-level doctor attributes is forbidden (AC-8).

The primary contract under test is the exact stdout shape and the exit-code
policy; individual check functions are also exercised directly to cover
every row of the DESIGN §7.1 table.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import doctor
from scripts.workspace import WorkspacePaths, resolve_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_doctor(workspace: WorkspacePaths, capsys) -> tuple[int, str]:
    code = doctor.main([], workspace)
    out = capsys.readouterr().out
    return code, out


def _summary_line(out: str) -> str:
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, "expected at least one line of output"
    return lines[-1]


def _fail_warn_lines(out: str) -> tuple[list[str], list[str]]:
    fails = [line for line in out.splitlines() if line.startswith("FAIL:")]
    warns = [line for line in out.splitlines() if line.startswith("WARN:")]
    return fails, warns


# ---------------------------------------------------------------------------
# Happy path: clean workspace
# ---------------------------------------------------------------------------


def test_doctor_clean_workspace(tmp_workspace, capsys):
    """AC-1 / AC-6 / AC-7: a fresh workspace -> 0 failures, 0 warnings, exit 0."""
    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 0
    fails, warns = _fail_warn_lines(out)
    assert fails == []
    assert warns == []
    assert _summary_line(out) == "doctor: 0 failures, 0 warnings"


# ---------------------------------------------------------------------------
# DESIGN §7.1 FAIL rows
# ---------------------------------------------------------------------------


def test_doctor_missing_state_dir(tmp_workspace, capsys):
    import shutil

    shutil.rmtree(tmp_workspace.state_dir)

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("state/" in line for line in fails)


def test_doctor_missing_raw_inbox(tmp_workspace, capsys):
    import shutil

    shutil.rmtree(tmp_workspace.raw_dir)

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("raw/inbox/" in line for line in fails)


def test_doctor_missing_wiki_subdirs(tmp_workspace, capsys):
    import shutil

    shutil.rmtree(tmp_workspace.topics_dir)
    shutil.rmtree(tmp_workspace.entities_dir)

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("wiki/topics" in line for line in fails)
    assert any("wiki/entities" in line for line in fails)


def test_doctor_missing_sync_config_and_fallback(tmp_workspace, capsys):
    """Both workspace sync-sources.local.json AND repo-root fallback missing -> FAIL."""
    tmp_workspace.sync_config_path.unlink()
    # Redirect fallback to a non-existent path so the repo's real fallback
    # doesn't mask the FAIL.
    redirected = replace_fallbacks(tmp_workspace, sync_fallback=tmp_workspace.root / "no-such.json")

    code, out = _run_doctor(redirected, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("sync config missing" in line for line in fails)


def test_doctor_malformed_sync_json(tmp_workspace, capsys):
    tmp_workspace.sync_config_path.write_text("{not valid json", encoding="utf-8")

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("malformed JSON" in line for line in fails)


def test_doctor_malformed_ingest_json(tmp_workspace, capsys):
    tmp_workspace.ingest_settings_path.write_text(
        "definitely not json", encoding="utf-8"
    )

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any("malformed JSON" in line for line in fails)


def test_doctor_missing_env_in_both(tmp_workspace, capsys):
    """Neither workspace .env nor repo-root .env -> FAIL."""
    tmp_workspace.env_path.unlink()
    redirected = replace_fallbacks(
        tmp_workspace, env_fallback=tmp_workspace.root / "no.env"
    )

    code, out = _run_doctor(redirected, capsys)

    assert code == 1
    fails, _ = _fail_warn_lines(out)
    assert any(".env missing" in line for line in fails)


# ---------------------------------------------------------------------------
# DESIGN §7.1 WARN rows
# ---------------------------------------------------------------------------


def test_doctor_placeholder_sources(tmp_workspace, capsys):
    """sync-sources.local.json holds the generic placeholder root -> WARN."""
    tmp_workspace.sync_config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {"name": "my-project", "root": "/absolute/path/to/your/project"},
                ],
            }
        ),
        encoding="utf-8",
    )

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any("placeholder" in line for line in warns)


def test_doctor_empty_raw_inbox(tmp_workspace, capsys):
    """raw/inbox/ exists but is empty -> WARN."""
    for child in tmp_workspace.raw_dir.iterdir():
        child.unlink()

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any("raw/inbox/ is empty" in line for line in warns)


def test_doctor_missing_api_key(tmp_workspace, capsys):
    """ANTHROPIC_API_KEY missing/empty -> WARN."""
    tmp_workspace.env_path.write_text("OTHER_VAR=hello\n", encoding="utf-8")

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any("ANTHROPIC_API_KEY" in line for line in warns)


def test_doctor_placeholder_api_key(tmp_workspace, capsys):
    """ANTHROPIC_API_KEY set to the template placeholder -> WARN."""
    tmp_workspace.env_path.write_text(
        "ANTHROPIC_API_KEY=your_anthropic_api_key_here\n", encoding="utf-8"
    )

    code, out = _run_doctor(tmp_workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any("ANTHROPIC_API_KEY" in line for line in warns)


def test_doctor_inside_outer_repo_missing_gitignore(tmp_path, capsys):
    """Workspace lives inside an outer git repo, lacking .gitignore -> WARN."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    ws_root = outer / "my-wiki"
    ws_root.mkdir()
    _populate_clean(ws_root)
    # Deliberately do NOT write .gitignore.

    workspace = resolve_workspace(str(ws_root), None)

    code, out = _run_doctor(workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any(".gitignore" in line for line in warns)


def test_doctor_inside_outer_repo_partial_gitignore(tmp_path, capsys):
    """Outer repo + .gitignore exists but lacks one of the required entries -> WARN."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    ws_root = outer / "my-wiki"
    ws_root.mkdir()
    _populate_clean(ws_root)
    (ws_root / ".gitignore").write_text(
        # Missing wiki/
        ".env\nraw/\nstate/\n",
        encoding="utf-8",
    )

    workspace = resolve_workspace(str(ws_root), None)

    code, out = _run_doctor(workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    assert any(".gitignore" in line and "wiki/" in line for line in warns)


def test_doctor_inside_outer_repo_with_gitignore_ok(tmp_path, capsys):
    """Outer repo + complete .gitignore -> no WARN from this check."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    ws_root = outer / "my-wiki"
    ws_root.mkdir()
    _populate_clean(ws_root)
    (ws_root / ".gitignore").write_text(
        ".env\nraw/\nstate/\nwiki/\n", encoding="utf-8"
    )

    workspace = resolve_workspace(str(ws_root), None)

    code, out = _run_doctor(workspace, capsys)

    assert code == 0
    _, warns = _fail_warn_lines(out)
    # No warning lines should mention .gitignore.
    assert not any(".gitignore" in line for line in warns)


# ---------------------------------------------------------------------------
# Exit-code policy
# ---------------------------------------------------------------------------


def test_doctor_exit_code_1_for_any_fail(tmp_workspace, capsys):
    import shutil

    shutil.rmtree(tmp_workspace.state_dir)

    code, _ = _run_doctor(tmp_workspace, capsys)
    assert code == 1


def test_doctor_exit_code_0_for_warn_only(tmp_workspace, capsys):
    for child in tmp_workspace.raw_dir.iterdir():
        child.unlink()

    code, out = _run_doctor(tmp_workspace, capsys)
    assert code == 0
    fails, warns = _fail_warn_lines(out)
    assert fails == []
    assert warns, "expected at least one WARN line"


# ---------------------------------------------------------------------------
# Resolution block
# ---------------------------------------------------------------------------


def test_doctor_resolution_block_shows_primary_vs_fallback(tmp_workspace, capsys):
    """Primary files are shown plain; missing-but-fallback files show 'fallback -> '."""
    # Delete the workspace wikiignore to force fallback.  The real repo-root
    # .wikiignore exists in the repo, so the resolver will find it.
    if tmp_workspace.wikiignore_path.exists():
        tmp_workspace.wikiignore_path.unlink()

    _, out = _run_doctor(tmp_workspace, capsys)

    # sync-sources.local.json uses the workspace copy (primary).
    assert (
        f"sync-sources.local.json: {tmp_workspace.sync_config_path}" in out
    ), out
    # .wikiignore resolved via fallback into the repo root.
    assert "fallback -> " in out
    assert ".wikiignore: fallback -> " in out


def test_doctor_resolution_block_env_none_found(tmp_workspace, capsys):
    """When .env is missing in both locations, resolution block shows '<none found>'."""
    tmp_workspace.env_path.unlink()
    redirected = replace_fallbacks(
        tmp_workspace, env_fallback=tmp_workspace.root / "nope.env"
    )

    _, out = _run_doctor(redirected, capsys)

    assert ".env: <none found>" in out


def test_doctor_resolution_block_precedes_fail_warn(tmp_workspace, capsys):
    """AC-5: output order is resolution block -> FAIL lines -> WARN lines -> summary."""
    import shutil

    shutil.rmtree(tmp_workspace.state_dir)
    for child in tmp_workspace.raw_dir.iterdir():
        child.unlink()

    _, out = _run_doctor(tmp_workspace, capsys)

    lines = out.splitlines()
    # Find indices
    res_idx = next(
        i for i, l in enumerate(lines) if l.startswith("sync-sources.local.json:")
    )
    fail_idx = next(i for i, l in enumerate(lines) if l.startswith("FAIL:"))
    warn_idx = next(i for i, l in enumerate(lines) if l.startswith("WARN:"))
    summary_idx = next(i for i, l in enumerate(lines) if l.startswith("doctor:"))

    assert res_idx < fail_idx < warn_idx < summary_idx


def test_doctor_output_order_fails_before_warns(tmp_workspace, capsys):
    """AC-5: all FAILs precede all WARNs in the output."""
    import shutil

    shutil.rmtree(tmp_workspace.state_dir)
    for child in tmp_workspace.raw_dir.iterdir():
        child.unlink()

    _, out = _run_doctor(tmp_workspace, capsys)

    seen_warn = False
    for line in out.splitlines():
        if line.startswith("WARN:"):
            seen_warn = True
        elif line.startswith("FAIL:"):
            assert not seen_warn, "FAIL line appeared after a WARN line"


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


def test_doctor_summary_line_format_exact(tmp_workspace, capsys):
    """AC-6: last line is exactly 'doctor: {n_fails} failures, {n_warns} warnings'."""
    import shutil

    # Produce 2 FAILs and 1 WARN: missing state + missing raw/inbox + missing api key.
    shutil.rmtree(tmp_workspace.state_dir)
    shutil.rmtree(tmp_workspace.raw_dir)
    tmp_workspace.env_path.write_text("OTHER=1\n", encoding="utf-8")

    _, out = _run_doctor(tmp_workspace, capsys)

    assert out.rstrip().splitlines()[-1] == "doctor: 2 failures, 1 warnings"


def test_doctor_summary_line_zero_case(tmp_workspace, capsys):
    _, out = _run_doctor(tmp_workspace, capsys)
    assert out.rstrip().splitlines()[-1] == "doctor: 0 failures, 0 warnings"


# ---------------------------------------------------------------------------
# Repo-root default (0.2.0 compatibility -- AC-10)
# ---------------------------------------------------------------------------


def test_doctor_repo_root_default_unchanged_happy_path(capsys):
    """AC-10: llm-wiki doctor with no flag resolves the repo-root default workspace.

    The repo ships sync-sources.json, ingest-settings.json, and schemas/AGENTS.md
    under the repo root so the resolution block reports them (via fallback,
    since the repo root has no ``.local.json`` variants checked in).
    """
    from scripts.workspace import repo_root

    ws = resolve_workspace(None, None)
    assert ws.source == "default"
    assert ws.root == repo_root()

    # We don't assert on the exit code here because a fresh clone does not
    # have state/, raw/inbox/ or wiki/ subdirs by policy; what we DO assert
    # is that the last line is the new summary format (not the old [PASS]
    # text) and that the resolution block is emitted.
    code, out = _run_doctor(ws, capsys)

    lines = [line for line in out.splitlines() if line.strip()]
    assert any(line.startswith("sync-sources.local.json:") for line in lines)
    assert lines[-1].startswith("doctor: ")
    assert lines[-1].endswith(" warnings")
    # Code is either 0 or 1 depending on whether state/ etc. exist in the
    # current repo checkout; both are valid outputs of the new policy.
    assert code in (0, 1)


# ---------------------------------------------------------------------------
# Check-function unit tests (direct invocation, for tight coverage)
# ---------------------------------------------------------------------------


def test_check_state_dir_exists_ok(tmp_workspace):
    assert doctor.check_state_dir_exists(tmp_workspace) == ("OK", None)


def test_check_state_dir_exists_fail(tmp_workspace):
    import shutil
    shutil.rmtree(tmp_workspace.state_dir)
    sev, msg = doctor.check_state_dir_exists(tmp_workspace)
    assert sev == "FAIL"
    assert msg is not None and "state/" in msg


def test_check_raw_inbox_dir_exists_fail(tmp_workspace):
    import shutil
    shutil.rmtree(tmp_workspace.raw_dir)
    sev, msg = doctor.check_raw_inbox_dir_exists(tmp_workspace)
    assert sev == "FAIL"


def test_check_wiki_subdirs_exist_partial(tmp_workspace):
    import shutil
    shutil.rmtree(tmp_workspace.summaries_dir)
    sev, msg = doctor.check_wiki_subdirs_exist(tmp_workspace)
    assert sev == "FAIL"
    assert msg is not None and "wiki/summaries" in msg


def test_check_config_json_well_formed_bad(tmp_workspace):
    tmp_workspace.sync_config_path.write_text("garbage", encoding="utf-8")
    sev, msg = doctor.check_config_json_well_formed(tmp_workspace)
    assert sev == "FAIL"
    assert msg is not None and "malformed JSON" in msg


def test_check_sync_placeholder_sources_ok(tmp_workspace):
    # Default fixture uses a real path, not the placeholder.
    assert doctor.check_sync_placeholder_sources(tmp_workspace) == ("OK", None)


def test_check_raw_inbox_empty_ok(tmp_workspace):
    assert doctor.check_raw_inbox_empty(tmp_workspace) == ("OK", None)


def test_check_anthropic_api_key_ok(tmp_workspace):
    assert doctor.check_anthropic_api_key(tmp_workspace) == ("OK", None)


# ---------------------------------------------------------------------------
# Isolation: two workspaces don't touch each other
# ---------------------------------------------------------------------------


def test_doctor_two_workspaces_isolated(two_workspaces, capsys):
    ws_a, ws_b = two_workspaces

    # Break ws_a only.
    import shutil
    shutil.rmtree(ws_a.state_dir)

    code_a, out_a = _run_doctor(ws_a, capsys)
    assert code_a == 1
    assert "state/" in out_a

    code_b, out_b = _run_doctor(ws_b, capsys)
    assert code_b == 0
    assert out_b.rstrip().splitlines()[-1] == "doctor: 0 failures, 0 warnings"


# ---------------------------------------------------------------------------
# Internal helpers used only in this test module
# ---------------------------------------------------------------------------


def _populate_clean(root: Path) -> None:
    """Miniature copy of conftest._populate_workspace for non-fixture paths."""
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "topics").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "inbox" / "placeholder.md").write_text("# placeholder\n")
    sources = root / "sources"
    sources.mkdir(exist_ok=True)
    (root / "sync-sources.local.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [{"name": "test", "root": str(sources)}],
            }
        ),
        encoding="utf-8",
    )
    (root / "ingest-settings.local.json").write_text("{}", encoding="utf-8")
    (root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n", encoding="utf-8")


def replace_fallbacks(
    ws: WorkspacePaths,
    *,
    sync_fallback: Path | None = None,
    ingest_fallback: Path | None = None,
    schema_fallback: Path | None = None,
    wikiignore_fallback: Path | None = None,
    env_fallback: Path | None = None,
) -> WorkspacePaths:
    """Return a copy of ``ws`` with select fallback paths replaced.

    WorkspacePaths is frozen; we rebuild with dataclasses.replace for tests
    that need to steer the resolver away from the real repo root.
    """
    from dataclasses import replace

    kwargs: dict[str, Path] = {}
    if sync_fallback is not None:
        kwargs["sync_fallback_config_path"] = sync_fallback
    if ingest_fallback is not None:
        kwargs["ingest_fallback_settings_path"] = ingest_fallback
    if schema_fallback is not None:
        kwargs["schema_fallback_path"] = schema_fallback
    if wikiignore_fallback is not None:
        kwargs["wikiignore_fallback_path"] = wikiignore_fallback
    if env_fallback is not None:
        kwargs["env_fallback_path"] = env_fallback
    return replace(ws, **kwargs)
