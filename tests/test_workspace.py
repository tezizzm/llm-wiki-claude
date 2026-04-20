"""Unit tests for scripts.workspace.

These tests cover the dataclass's frozen semantics, the ``repo_root()``
helper, the exception hierarchy, ``resolve_workspace`` precedence, and the
config-file / env resolvers plus ``load_env`` fallback semantics.
"""

import os
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts import workspace
from scripts.workspace import (
    WorkspaceError,
    WorkspaceInvalidError,
    WorkspaceNotFoundError,
    WorkspacePaths,
    ensure_workspace_exists,
    ensure_workspace_writable,
    load_env,
    repo_root,
    resolve_env,
    resolve_ingest_settings,
    resolve_schema,
    resolve_sync_config,
    resolve_wikiignore,
    resolve_workspace,
    resolve_workspace_for_init,
)


def _build_paths(root: Path) -> WorkspacePaths:
    """Construct a WorkspacePaths instance from a root directory.

    Intentionally duplicates the derivation rules from ARCHITECTURE §3.1 so
    this test file can stand alone without a resolver helper.
    """

    wiki_dir = root / "wiki"
    state_dir = root / "state"
    repo = repo_root()
    return WorkspacePaths(
        root=root,
        source="default",
        repo_root=repo,
        raw_dir=root / "raw" / "inbox",
        wiki_dir=wiki_dir,
        summaries_dir=wiki_dir / "summaries",
        topics_dir=wiki_dir / "topics",
        entities_dir=wiki_dir / "entities",
        state_dir=state_dir,
        manifest_path=state_dir / "manifest.json",
        sync_manifest_path=state_dir / "sync_manifest.json",
        last_ingest_run_path=state_dir / "last_ingest_run.json",
        ingest_events_path=state_dir / "ingest_events.jsonl",
        ingest_report_path=state_dir / "last_ingest_report.md",
        index_path=root / "index.md",
        log_path=root / "log.md",
        env_path=root / ".env",
        sync_config_path=root / "sync-sources.local.json",
        sync_fallback_config_path=repo / "sync-sources.json",
        ingest_settings_path=root / "ingest-settings.local.json",
        ingest_fallback_settings_path=repo / "ingest-settings.json",
        schema_path=root / "schemas" / "AGENTS.md",
        schema_fallback_path=repo / "schemas" / "AGENTS.md",
        wikiignore_path=root / ".wikiignore",
        wikiignore_fallback_path=repo / ".wikiignore",
        env_fallback_path=repo / ".env",
    )


def test_workspacepaths_is_frozen(tmp_path: Path) -> None:
    """Assigning to any field must raise FrozenInstanceError."""
    paths = _build_paths(tmp_path)

    with pytest.raises(FrozenInstanceError):
        paths.root = tmp_path / "other"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        paths.state_dir = tmp_path / "state2"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        paths.source = "flag"  # type: ignore[misc]


def test_repo_root_returns_expected_path() -> None:
    """repo_root() must resolve to scripts/workspace.py's parents[1]."""
    expected = Path(workspace.__file__).resolve().parents[1]

    result = repo_root()

    assert result == expected
    assert result.is_absolute()
    assert result == result.resolve()
    assert result.exists(), f"expected repo root to exist: {result}"
    assert (result / "scripts").is_dir(), (
        f"expected repo root to contain scripts/: {result}"
    )


def test_workspace_exceptions_exist(tmp_path: Path) -> None:
    """Exception classes are wired correctly and carry the expected payload."""
    # Base hierarchy
    assert issubclass(WorkspaceError, Exception)
    assert issubclass(WorkspaceNotFoundError, WorkspaceError)
    assert issubclass(WorkspaceInvalidError, WorkspaceError)

    # NotFound carries the offending path
    missing = tmp_path / "does-not-exist"
    err = WorkspaceNotFoundError(missing)
    assert err.path == missing
    assert str(missing) in str(err)
    assert isinstance(err, WorkspaceError)

    # Invalid is raiseable and catchable as WorkspaceError
    with pytest.raises(WorkspaceError):
        raise WorkspaceInvalidError("not a directory")


# ---------------------------------------------------------------------------
# resolve_workspace() precedence tests
# ---------------------------------------------------------------------------


def _assert_all_paths_absolute_and_clean(ws: WorkspacePaths) -> None:
    """Every Path field on ``ws`` must be absolute and contain no '..' parts."""
    for field_name in ws.__dataclass_fields__:
        value = getattr(ws, field_name)
        if isinstance(value, Path):
            assert value.is_absolute(), (
                f"{field_name} is not absolute: {value}"
            )
            assert ".." not in value.parts, (
                f"{field_name} contains '..': {value}"
            )


def test_resolve_default_returns_repo_root() -> None:
    """With no flag and no env var, resolve_workspace returns the repo-root default."""
    ws = resolve_workspace(None, None)

    assert ws.source == "default"
    assert ws.root == repo_root()
    assert ws.repo_root == repo_root()


def test_resolve_flag_wins_over_env(tmp_path: Path) -> None:
    """Flag takes precedence over env var when both are provided and both exist."""
    flag_dir = tmp_path / "flag-ws"
    env_dir = tmp_path / "env-ws"
    flag_dir.mkdir()
    env_dir.mkdir()

    ws = resolve_workspace(str(flag_dir), str(env_dir))

    assert ws.source == "flag"
    assert ws.root == flag_dir.resolve()


def test_resolve_env_used_when_no_flag(tmp_path: Path) -> None:
    """With no flag but an env var, source='env' and root derives from env."""
    env_dir = tmp_path / "env-ws"
    env_dir.mkdir()

    ws = resolve_workspace(None, str(env_dir))

    assert ws.source == "env"
    assert ws.root == env_dir.resolve()


def test_resolve_empty_string_flag_falls_through_to_env(tmp_path: Path) -> None:
    """An empty-string flag value should not be treated as 'flag supplied'."""
    env_dir = tmp_path / "env-ws"
    env_dir.mkdir()

    ws = resolve_workspace("", str(env_dir))

    assert ws.source == "env"
    assert ws.root == env_dir.resolve()


def test_resolve_empty_env_falls_through_to_default() -> None:
    """An empty-string env var should not be treated as 'env supplied'."""
    ws = resolve_workspace(None, "")

    assert ws.source == "default"
    assert ws.root == repo_root()


def test_resolve_relative_path_resolves_against_cwd(tmp_path: Path) -> None:
    """A relative flag path resolves against the injected cwd."""
    target = tmp_path / "relative-dir"
    target.mkdir()

    ws = resolve_workspace("relative-dir", None, cwd=tmp_path)

    assert ws.source == "flag"
    assert ws.root == target.resolve()


def test_resolve_relative_env_resolves_against_cwd(tmp_path: Path) -> None:
    """A relative env-var path also resolves against the injected cwd."""
    target = tmp_path / "env-relative"
    target.mkdir()

    ws = resolve_workspace(None, "env-relative", cwd=tmp_path)

    assert ws.source == "env"
    assert ws.root == target.resolve()


def test_resolve_nonexistent_flag_raises(tmp_path: Path) -> None:
    """A non-existent --workspace flag path raises WorkspaceNotFoundError."""
    missing = tmp_path / "does-not-exist"

    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        resolve_workspace(str(missing), None)

    assert excinfo.value.path == missing.resolve()


def test_resolve_nonexistent_env_raises(tmp_path: Path) -> None:
    """A non-existent env-var path raises WorkspaceNotFoundError."""
    missing = tmp_path / "also-missing"

    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        resolve_workspace(None, str(missing))

    assert excinfo.value.path == missing.resolve()


def test_resolve_nonexistent_default_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """resolve_workspace(None, None) never raises, even if repo_root were missing.

    The repo-root default is always taken as-is per ARCHITECTURE §3.3 — its
    existence is guaranteed by install. We simulate a missing repo-root by
    patching ``repo_root`` to return a path that does not exist, and confirm
    no exception is raised.
    """

    fake_root = tmp_path / "not-installed"
    # Deliberately do NOT create fake_root on disk.
    monkeypatch.setattr(workspace, "repo_root", lambda: fake_root)

    ws = resolve_workspace(None, None)

    assert ws.source == "default"
    assert ws.root == fake_root.resolve()


def test_workspace_paths_all_absolute_and_resolved(tmp_path: Path) -> None:
    """Every Path field on the returned WorkspacePaths is absolute and '..'-free."""
    ws_dir = tmp_path / "clean-ws"
    ws_dir.mkdir()

    # Pass a relative path with '..' segments to force normalization.
    nested = tmp_path / "nested"
    nested.mkdir()
    relative_with_dotdot = f"nested/../clean-ws"

    ws = resolve_workspace(relative_with_dotdot, None, cwd=tmp_path)

    assert ws.root == ws_dir.resolve()
    _assert_all_paths_absolute_and_clean(ws)


def test_resolve_default_paths_include_repo_root_fallbacks() -> None:
    """For the default workspace, fallback paths point into the repo root.

    Primary sync_config_path has ``.local.json`` suffix (may not exist);
    sync_fallback_config_path is the repo-root ``sync-sources.json`` that
    preserved 0.2.0 behavior relies on (per ARCHITECTURE §3.1).
    """

    ws = resolve_workspace(None, None)

    assert ws.sync_config_path == repo_root() / "sync-sources.local.json"
    assert ws.sync_fallback_config_path == repo_root() / "sync-sources.json"
    # The repo actually ships the fallback config, so it must exist on disk.
    assert ws.sync_fallback_config_path.exists()


# ---------------------------------------------------------------------------
# resolve_workspace_for_init() tests
# ---------------------------------------------------------------------------


def test_resolve_workspace_for_init_allows_nonexistent(tmp_path: Path) -> None:
    """Init must accept a path that does not yet exist on disk."""
    target = tmp_path / "does" / "not" / "yet" / "exist"

    result = resolve_workspace_for_init(str(target))

    assert result == target.resolve()
    assert result.is_absolute()


def test_resolve_workspace_for_init_resolves_relative_against_cwd(
    tmp_path: Path,
) -> None:
    """A relative init target resolves against the injected cwd."""
    result = resolve_workspace_for_init("new-ws", cwd=tmp_path)

    assert result == (tmp_path / "new-ws").resolve()
    assert result.is_absolute()


def test_resolve_workspace_for_init_does_not_check_existence(
    tmp_path: Path,
) -> None:
    """Even with a cwd whose child does not exist, init does not raise."""
    # Note: tmp_path exists, child does not.
    result = resolve_workspace_for_init("brand-new", cwd=tmp_path)

    assert result == (tmp_path / "brand-new").resolve()
    assert not result.exists()


def test_resolve_workspace_for_init_absolute_path_passthrough(
    tmp_path: Path,
) -> None:
    """An absolute path is returned resolved, unchanged in value."""
    absolute_target = tmp_path / "absolute-init-target"

    result = resolve_workspace_for_init(str(absolute_target))

    assert result == absolute_target.resolve()


# ---------------------------------------------------------------------------
# Config resolver fixtures and helpers
# ---------------------------------------------------------------------------


def _make_workspace(
    workspace_root: Path,
    repo: Path,
) -> WorkspacePaths:
    """Build a WorkspacePaths with workspace_root and a chosen repo_root.

    We synthesize an isolated workspace without relying on
    ``resolve_workspace`` so each test can control both the primary and
    fallback directory independently, including "fallback also missing"
    scenarios.
    """

    wiki_dir = workspace_root / "wiki"
    state_dir = workspace_root / "state"
    return WorkspacePaths(
        root=workspace_root,
        source="flag",
        repo_root=repo,
        raw_dir=workspace_root / "raw" / "inbox",
        wiki_dir=wiki_dir,
        summaries_dir=wiki_dir / "summaries",
        topics_dir=wiki_dir / "topics",
        entities_dir=wiki_dir / "entities",
        state_dir=state_dir,
        manifest_path=state_dir / "manifest.json",
        sync_manifest_path=state_dir / "sync_manifest.json",
        last_ingest_run_path=state_dir / "last_ingest_run.json",
        ingest_events_path=state_dir / "ingest_events.jsonl",
        ingest_report_path=state_dir / "last_ingest_report.md",
        index_path=workspace_root / "index.md",
        log_path=workspace_root / "log.md",
        env_path=workspace_root / ".env",
        sync_config_path=workspace_root / "sync-sources.local.json",
        sync_fallback_config_path=repo / "sync-sources.json",
        ingest_settings_path=workspace_root / "ingest-settings.local.json",
        ingest_fallback_settings_path=repo / "ingest-settings.json",
        schema_path=workspace_root / "schemas" / "AGENTS.md",
        schema_fallback_path=repo / "schemas" / "AGENTS.md",
        wikiignore_path=workspace_root / ".wikiignore",
        wikiignore_fallback_path=repo / ".wikiignore",
        env_fallback_path=repo / ".env",
    )


@pytest.fixture
def isolated_ws(tmp_path: Path) -> WorkspacePaths:
    """A workspace with an empty workspace root and an empty synthetic repo root.

    Both primary and fallback locations are empty -- tests opt in to creating
    specific files to exercise primary / fallback / both-missing branches
    without being influenced by the real repo's shipped fallback files.
    """

    ws_root = tmp_path / "workspace"
    repo = tmp_path / "repo"
    ws_root.mkdir()
    repo.mkdir()
    (repo / "schemas").mkdir()
    (ws_root / "schemas").mkdir()
    return _make_workspace(ws_root, repo)


# ---------------------------------------------------------------------------
# resolve_sync_config
# ---------------------------------------------------------------------------


def test_resolve_sync_config_primary(isolated_ws: WorkspacePaths) -> None:
    """Workspace has sync-sources.local.json -> returns it with is_fallback=False."""
    isolated_ws.sync_config_path.write_text("{}", encoding="utf-8")
    isolated_ws.sync_fallback_config_path.write_text("{}", encoding="utf-8")

    path, is_fallback = resolve_sync_config(isolated_ws)

    assert path == isolated_ws.sync_config_path
    assert is_fallback is False


def test_resolve_sync_config_fallback(isolated_ws: WorkspacePaths) -> None:
    """Workspace missing, repo-root fallback present -> returns fallback, True."""
    isolated_ws.sync_fallback_config_path.write_text("{}", encoding="utf-8")

    path, is_fallback = resolve_sync_config(isolated_ws)

    assert path == isolated_ws.sync_fallback_config_path
    assert is_fallback is True


def test_resolve_sync_config_both_missing_raises(
    isolated_ws: WorkspacePaths,
) -> None:
    """Both missing -> raises FileNotFoundError naming both expected paths."""
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_sync_config(isolated_ws)

    msg = str(excinfo.value)
    assert str(isolated_ws.sync_config_path) in msg
    assert str(isolated_ws.sync_fallback_config_path) in msg


# ---------------------------------------------------------------------------
# resolve_ingest_settings
# ---------------------------------------------------------------------------


def test_resolve_ingest_settings_primary(isolated_ws: WorkspacePaths) -> None:
    """Workspace has ingest-settings.local.json -> returns it, is_fallback=False."""
    isolated_ws.ingest_settings_path.write_text("{}", encoding="utf-8")
    isolated_ws.ingest_fallback_settings_path.write_text("{}", encoding="utf-8")

    path, is_fallback = resolve_ingest_settings(isolated_ws)

    assert path == isolated_ws.ingest_settings_path
    assert is_fallback is False


def test_resolve_ingest_settings_fallback(isolated_ws: WorkspacePaths) -> None:
    """Only the repo-root fallback exists -> returns fallback, True."""
    isolated_ws.ingest_fallback_settings_path.write_text("{}", encoding="utf-8")

    path, is_fallback = resolve_ingest_settings(isolated_ws)

    assert path == isolated_ws.ingest_fallback_settings_path
    assert is_fallback is True


def test_resolve_ingest_settings_both_missing_raises(
    isolated_ws: WorkspacePaths,
) -> None:
    """Both missing -> FileNotFoundError referencing both paths."""
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_ingest_settings(isolated_ws)

    msg = str(excinfo.value)
    assert str(isolated_ws.ingest_settings_path) in msg
    assert str(isolated_ws.ingest_fallback_settings_path) in msg


# ---------------------------------------------------------------------------
# resolve_schema
# ---------------------------------------------------------------------------


def test_resolve_schema_primary(isolated_ws: WorkspacePaths) -> None:
    """Workspace has schemas/AGENTS.md -> returns it, is_fallback=False."""
    isolated_ws.schema_path.write_text("# schema", encoding="utf-8")
    isolated_ws.schema_fallback_path.write_text("# schema", encoding="utf-8")

    path, is_fallback = resolve_schema(isolated_ws)

    assert path == isolated_ws.schema_path
    assert is_fallback is False


def test_resolve_schema_fallback(isolated_ws: WorkspacePaths) -> None:
    """Only the repo-root schemas/AGENTS.md exists -> returns fallback, True."""
    isolated_ws.schema_fallback_path.write_text("# schema", encoding="utf-8")

    path, is_fallback = resolve_schema(isolated_ws)

    assert path == isolated_ws.schema_fallback_path
    assert is_fallback is True


def test_resolve_schema_both_missing_raises(isolated_ws: WorkspacePaths) -> None:
    """Both missing -> FileNotFoundError referencing both paths."""
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_schema(isolated_ws)

    msg = str(excinfo.value)
    assert str(isolated_ws.schema_path) in msg
    assert str(isolated_ws.schema_fallback_path) in msg


# ---------------------------------------------------------------------------
# resolve_wikiignore
# ---------------------------------------------------------------------------


def test_resolve_wikiignore_primary(isolated_ws: WorkspacePaths) -> None:
    """Workspace has .wikiignore -> returns it, is_fallback=False."""
    isolated_ws.wikiignore_path.write_text("*.tmp\n", encoding="utf-8")
    isolated_ws.wikiignore_fallback_path.write_text("*.tmp\n", encoding="utf-8")

    path, is_fallback = resolve_wikiignore(isolated_ws)

    assert path == isolated_ws.wikiignore_path
    assert is_fallback is False


def test_resolve_wikiignore_fallback(isolated_ws: WorkspacePaths) -> None:
    """Only the repo-root .wikiignore exists -> returns fallback, True."""
    isolated_ws.wikiignore_fallback_path.write_text("*.tmp\n", encoding="utf-8")

    path, is_fallback = resolve_wikiignore(isolated_ws)

    assert path == isolated_ws.wikiignore_fallback_path
    assert is_fallback is True


def test_resolve_wikiignore_both_missing_raises(
    isolated_ws: WorkspacePaths,
) -> None:
    """Both missing -> FileNotFoundError referencing both paths."""
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_wikiignore(isolated_ws)

    msg = str(excinfo.value)
    assert str(isolated_ws.wikiignore_path) in msg
    assert str(isolated_ws.wikiignore_fallback_path) in msg


# ---------------------------------------------------------------------------
# resolve_env (.env is allowed to be absent in BOTH locations)
# ---------------------------------------------------------------------------


def test_resolve_env_workspace_exists(isolated_ws: WorkspacePaths) -> None:
    """Workspace .env present -> returns (workspace_env, False)."""
    isolated_ws.env_path.write_text("A=ws\n", encoding="utf-8")
    isolated_ws.env_fallback_path.write_text("A=root\n", encoding="utf-8")

    path, is_fallback = resolve_env(isolated_ws)

    assert path == isolated_ws.env_path
    assert is_fallback is False


def test_resolve_env_fallback(isolated_ws: WorkspacePaths) -> None:
    """Only repo-root .env exists -> returns (fallback, True)."""
    isolated_ws.env_fallback_path.write_text("A=root\n", encoding="utf-8")

    path, is_fallback = resolve_env(isolated_ws)

    assert path == isolated_ws.env_fallback_path
    assert is_fallback is True


def test_resolve_env_both_missing(isolated_ws: WorkspacePaths) -> None:
    """Both .env locations missing -> returns (None, False). No raise."""
    path, is_fallback = resolve_env(isolated_ws)

    assert path is None
    assert is_fallback is False


# ---------------------------------------------------------------------------
# load_env
# ---------------------------------------------------------------------------


def test_load_env_workspace_wins_over_fallback(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace .env overrides repo-root .env; only the workspace value lands."""
    monkeypatch.delenv("LWC_TEST_A", raising=False)
    isolated_ws.env_path.write_text("LWC_TEST_A=ws\n", encoding="utf-8")
    isolated_ws.env_fallback_path.write_text("LWC_TEST_A=root\n", encoding="utf-8")

    load_env(isolated_ws)

    assert os.environ["LWC_TEST_A"] == "ws"


def test_load_env_real_environ_wins_over_both(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """os.environ value set BEFORE load_env always wins over both .env files."""
    monkeypatch.setenv("LWC_TEST_A", "real")
    isolated_ws.env_path.write_text("LWC_TEST_A=ws\n", encoding="utf-8")
    isolated_ws.env_fallback_path.write_text("LWC_TEST_A=root\n", encoding="utf-8")

    load_env(isolated_ws)

    assert os.environ["LWC_TEST_A"] == "real"


def test_load_env_returns_merged_values_from_files(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returned dict reflects the merged file state (workspace wins), separate
    from what ended up in os.environ."""
    monkeypatch.delenv("LWC_TEST_A", raising=False)
    monkeypatch.delenv("LWC_TEST_B", raising=False)
    isolated_ws.env_path.write_text("LWC_TEST_A=ws\n", encoding="utf-8")
    isolated_ws.env_fallback_path.write_text(
        "LWC_TEST_A=root\nLWC_TEST_B=root_only\n", encoding="utf-8"
    )

    merged = load_env(isolated_ws)

    assert merged["LWC_TEST_A"] == "ws"
    assert merged["LWC_TEST_B"] == "root_only"


def test_load_env_idempotent(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling load_env twice produces the same os.environ state as one call."""
    monkeypatch.delenv("LWC_TEST_A", raising=False)
    monkeypatch.delenv("LWC_TEST_B", raising=False)
    isolated_ws.env_path.write_text(
        "LWC_TEST_A=ws\nLWC_TEST_B=two\n", encoding="utf-8"
    )

    load_env(isolated_ws)
    snapshot_after_first = dict(os.environ)

    load_env(isolated_ws)
    snapshot_after_second = dict(os.environ)

    assert snapshot_after_first == snapshot_after_second
    assert os.environ["LWC_TEST_A"] == "ws"
    assert os.environ["LWC_TEST_B"] == "two"


def test_load_env_does_not_overwrite_preexisting_value(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value already present in os.environ is NEVER overwritten by load_env.

    Guards the setdefault contract: we promise the user's exported environment
    is authoritative.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "preexisting")
    isolated_ws.env_path.write_text(
        "ANTHROPIC_API_KEY=from_file\n", encoding="utf-8"
    )

    load_env(isolated_ws)

    assert os.environ["ANTHROPIC_API_KEY"] == "preexisting"


def test_load_env_does_not_read_llm_wiki_workspace(
    isolated_ws: WorkspacePaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_env ignores LLM_WIKI_WORKSPACE; the workspace argument is authoritative.

    We point LLM_WIKI_WORKSPACE at a decoy directory that has its own .env.
    load_env must load from ``isolated_ws``, not the decoy.
    """

    decoy = tmp_path / "decoy-ws"
    decoy.mkdir()
    (decoy / ".env").write_text("LWC_TEST_FROM_DECOY=1\n", encoding="utf-8")

    monkeypatch.setenv("LLM_WIKI_WORKSPACE", str(decoy))
    monkeypatch.delenv("LWC_TEST_FROM_DECOY", raising=False)
    monkeypatch.delenv("LWC_TEST_A", raising=False)
    isolated_ws.env_path.write_text("LWC_TEST_A=ws\n", encoding="utf-8")

    load_env(isolated_ws)

    # The decoy's .env key must NOT have been read.
    assert "LWC_TEST_FROM_DECOY" not in os.environ
    # The real workspace's .env key MUST have been read.
    assert os.environ["LWC_TEST_A"] == "ws"


def test_load_env_tolerates_both_env_files_missing(
    isolated_ws: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_env returns an empty dict and mutates nothing when no .env exists."""
    monkeypatch.delenv("LWC_TEST_A", raising=False)
    before = dict(os.environ)

    result = load_env(isolated_ws)

    assert result == {}
    assert dict(os.environ) == before


def test_test_file_uses_monkeypatch_exclusively() -> None:
    """Meta-test: tests/test_workspace.py must not contain raw os.environ writes.

    Enforces the os.environ isolation hygiene contract from ARCHITECTURE §14.
    We scan this file's source for ``os.environ[...] = ...`` assignments and
    fail if any exist. Reads (``os.environ["FOO"]``) and ``os.environ.get(...)``
    are allowed; only the assignment form is banned.
    """

    this_file = Path(__file__).read_text(encoding="utf-8")

    # This meta-test necessarily discusses the banned form inside its own
    # docstring and comments; tokenize the file to scan code only.
    import io
    import tokenize

    pattern = re.compile(r"os\.environ\s*\[[^\]]+\]\s*=(?!=)")
    code_only: list[str] = []
    tokens = tokenize.tokenize(io.BytesIO(this_file.encode("utf-8")).readline)
    for tok in tokens:
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code_only.append(tok.string)
    code_text = " ".join(code_only)

    real_assignments = pattern.findall(code_text)

    assert real_assignments == [], (
        "tests/test_workspace.py must use monkeypatch.setenv / delenv, "
        "not direct os.environ[...] = ... assignments. "
        f"Found: {real_assignments}"
    )


# ---------------------------------------------------------------------------
# ensure_workspace_exists
# ---------------------------------------------------------------------------


def test_ensure_workspace_exists_ok(isolated_ws: WorkspacePaths) -> None:
    """A valid workspace root passes without raising."""
    # Returns None implicitly; just confirm no exception.
    assert ensure_workspace_exists(isolated_ws) is None


def test_ensure_workspace_exists_raises_for_missing(
    tmp_path: Path,
) -> None:
    """Missing root -> WorkspaceNotFoundError carrying the offending path."""
    missing = tmp_path / "not-there"
    ws = _make_workspace(missing, tmp_path / "repo")

    with pytest.raises(WorkspaceNotFoundError) as excinfo:
        ensure_workspace_exists(ws)

    assert excinfo.value.path == missing


def test_ensure_workspace_exists_raises_for_file(tmp_path: Path) -> None:
    """Path exists but is a file -> WorkspaceInvalidError."""
    file_path = tmp_path / "im-a-file"
    file_path.write_text("not a directory", encoding="utf-8")
    ws = _make_workspace(file_path, tmp_path / "repo")

    with pytest.raises(WorkspaceInvalidError):
        ensure_workspace_exists(ws)


# ---------------------------------------------------------------------------
# ensure_workspace_writable
# ---------------------------------------------------------------------------


def test_ensure_workspace_writable_creates_dirs(
    isolated_ws: WorkspacePaths,
) -> None:
    """All expected output directories are created."""
    # Pre-condition: state/raw/wiki subdirs do not exist yet.
    assert not isolated_ws.state_dir.exists()
    assert not isolated_ws.raw_dir.exists()
    assert not isolated_ws.summaries_dir.exists()
    assert not isolated_ws.topics_dir.exists()
    assert not isolated_ws.entities_dir.exists()

    ensure_workspace_writable(isolated_ws)

    assert isolated_ws.state_dir.is_dir()
    assert isolated_ws.raw_dir.is_dir()
    assert isolated_ws.summaries_dir.is_dir()
    assert isolated_ws.topics_dir.is_dir()
    assert isolated_ws.entities_dir.is_dir()


def test_ensure_workspace_writable_idempotent(
    isolated_ws: WorkspacePaths,
) -> None:
    """Calling twice is a no-op and does not error out."""
    ensure_workspace_writable(isolated_ws)
    ensure_workspace_writable(isolated_ws)  # must not raise

    # Still intact.
    assert isolated_ws.raw_dir.is_dir()
    assert isolated_ws.summaries_dir.is_dir()


# ---------------------------------------------------------------------------
# Integration: end-to-end fallback resolution
# ---------------------------------------------------------------------------


def test_fallback_resolution_end_to_end(
    isolated_ws: WorkspacePaths,
) -> None:
    """With no workspace-local config, callers can resolve + read the fallback.

    Mimics the real call pattern: resolver returns a path; caller opens it
    and parses the content. Ensures the tuple shape is usable end-to-end.
    """

    # Only the repo-root fallback exists.
    payload = '{"version": 1, "sources": []}'
    isolated_ws.sync_fallback_config_path.write_text(payload, encoding="utf-8")

    path, is_fallback = resolve_sync_config(isolated_ws)

    assert is_fallback is True
    assert path.read_text(encoding="utf-8") == payload


def test_fallback_applies_across_all_resolvers(
    isolated_ws: WorkspacePaths,
) -> None:
    """All four strict resolvers fall back to the repo-root copy uniformly."""
    isolated_ws.sync_fallback_config_path.write_text("{}", encoding="utf-8")
    isolated_ws.ingest_fallback_settings_path.write_text("{}", encoding="utf-8")
    isolated_ws.schema_fallback_path.write_text("# s", encoding="utf-8")
    isolated_ws.wikiignore_fallback_path.write_text("*.tmp\n", encoding="utf-8")

    for resolver, expected in (
        (resolve_sync_config, isolated_ws.sync_fallback_config_path),
        (resolve_ingest_settings, isolated_ws.ingest_fallback_settings_path),
        (resolve_schema, isolated_ws.schema_fallback_path),
        (resolve_wikiignore, isolated_ws.wikiignore_fallback_path),
    ):
        path, is_fallback = resolver(isolated_ws)
        assert path == expected
        assert is_fallback is True
