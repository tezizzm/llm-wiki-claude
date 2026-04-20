"""Tests for ``scripts.init`` -- directory + template file scaffolding.

Covers the acceptance criteria from LWC-zsy4:

1. ``main(argv) -> int`` parses ``PATH [--force]``.
2. All six subdirectories are created idempotently.
3. All six template files plus ``index.md`` + ``log.md`` are written.
4. Re-running on a populated workspace skips existing template files.
5. ``--force`` overwrites every template file including ``.env``.
6. ``--force`` never modifies ``raw/``, ``wiki/``, or ``state/`` contents.
7. Targeting an existing regular file prints DESIGN §10.3 text + exits 1.
8. Targeting a location we cannot write to prints DESIGN §10.4 text +
   exits 1 (skipped on Windows).
"""

from __future__ import annotations

import os
import stat
import sys
from importlib.resources import files
from pathlib import Path

import pytest

import scripts.templates as templates_pkg
import scripts.templates.schemas as templates_schemas_pkg
from scripts import init as init_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


EXPECTED_SUBDIRS = (
    "raw/inbox",
    "wiki/summaries",
    "wiki/topics",
    "wiki/entities",
    "state",
    "schemas",
)

EXPECTED_FILES = (
    ".env.example",
    ".env",
    ".wikiignore",
    "sync-sources.local.json",
    "ingest-settings.local.json",
    "schemas/AGENTS.md",
    "index.md",
    "log.md",
)


def _template_text(name: str) -> str:
    if name == "schemas/AGENTS.md":
        return (files(templates_schemas_pkg) / "AGENTS.md").read_text(encoding="utf-8")
    return (files(templates_pkg) / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC 1, 2, 3: initial scaffolding
# ---------------------------------------------------------------------------


def test_init_creates_all_expected_files_and_dirs(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    rc = init_module.main([str(target)])
    assert rc == 0

    for sub in EXPECTED_SUBDIRS:
        assert (target / sub).is_dir(), f"missing directory: {sub}"

    for rel in EXPECTED_FILES:
        assert (target / rel).is_file(), f"missing file: {rel}"

    # index.md must have the documented placeholder content.
    assert (target / "index.md").read_text(encoding="utf-8") == "# Wiki\n"
    # log.md must be empty.
    assert (target / "log.md").read_text(encoding="utf-8") == ""


def test_init_returns_zero_on_success(tmp_path: Path) -> None:
    assert init_module.main([str(tmp_path / "ws")]) == 0


def test_init_parses_path_and_force_flags(tmp_path: Path) -> None:
    # --force is accepted before or after the path argument.
    target_a = tmp_path / "a"
    assert init_module.main([str(target_a), "--force"]) == 0
    target_b = tmp_path / "b"
    assert init_module.main(["--force", str(target_b)]) == 0


def test_init_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        init_module.main([])


# ---------------------------------------------------------------------------
# AC 4: idempotent re-run skips existing template files
# ---------------------------------------------------------------------------


def test_init_idempotent_re_run_skips_existing(tmp_path: Path, capsys) -> None:
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0
    capsys.readouterr()  # clear first run's output

    # Mutate every template file so we can tell if the second run overwrote.
    marker = "SENTINEL-USER-EDIT"
    for rel in EXPECTED_FILES:
        (target / rel).write_text(marker, encoding="utf-8")

    assert init_module.main([str(target)]) == 0

    # Every file must still contain the user's marker -- bare init must not
    # overwrite any existing template file on a second run.
    for rel in EXPECTED_FILES:
        assert (target / rel).read_text(encoding="utf-8") == marker, (
            f"{rel} was modified by a bare re-run"
        )

    # Directories must still exist (idempotent mkdir).
    for sub in EXPECTED_SUBDIRS:
        assert (target / sub).is_dir()


# ---------------------------------------------------------------------------
# AC 5: --force overwrites template files (including .env)
# ---------------------------------------------------------------------------


def test_init_force_overwrites_template_files(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    # Mutate .env.example.
    env_example = target / ".env.example"
    env_example.write_text("MUTATED\n", encoding="utf-8")

    assert init_module.main([str(target), "--force"]) == 0
    assert env_example.read_text(encoding="utf-8") == _template_text("env.example")


def test_init_force_overwrites_dotenv(tmp_path: Path) -> None:
    """ARCHITECTURE §8.3 final rule: --force overwrites .env too."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    dotenv = target / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-user-secret\n", encoding="utf-8")

    assert init_module.main([str(target), "--force"]) == 0
    assert dotenv.read_text(encoding="utf-8") == _template_text("env.example")


def test_init_force_overwrites_every_template_file(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    # Mutate every template file AND the two placeholder files.
    for rel in EXPECTED_FILES:
        (target / rel).write_text("MUTATED", encoding="utf-8")

    assert init_module.main([str(target), "--force"]) == 0

    # Every template file must be restored to its canonical template content.
    template_map = {
        ".env.example": "env.example",
        ".env": "env.example",
        ".wikiignore": "wikiignore",
        "sync-sources.local.json": "sync-sources.json",
        "ingest-settings.local.json": "ingest-settings.json",
        "schemas/AGENTS.md": "schemas/AGENTS.md",
    }
    for dest_rel, template_name in template_map.items():
        assert (target / dest_rel).read_text(encoding="utf-8") == _template_text(
            template_name
        ), f"{dest_rel} was not restored from its template"

    # index.md and log.md are restored to their placeholder content.
    assert (target / "index.md").read_text(encoding="utf-8") == "# Wiki\n"
    assert (target / "log.md").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# AC 6: --force never touches raw/, wiki/, or state/
# ---------------------------------------------------------------------------


def test_init_force_never_touches_raw_wiki_state(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    # Seed user content under the three user-owned trees.
    raw_file = target / "raw" / "inbox" / "foo.md"
    wiki_file = target / "wiki" / "summaries" / "bar.md"
    state_file = target / "state" / "baz.json"
    raw_file.write_text("raw content", encoding="utf-8")
    wiki_file.write_text("wiki content", encoding="utf-8")
    state_file.write_text("{\"k\": 1}\n", encoding="utf-8")

    assert init_module.main([str(target), "--force"]) == 0

    # All three user-owned files must be bit-for-bit unchanged.
    assert raw_file.read_text(encoding="utf-8") == "raw content"
    assert wiki_file.read_text(encoding="utf-8") == "wiki content"
    assert state_file.read_text(encoding="utf-8") == "{\"k\": 1}\n"


# ---------------------------------------------------------------------------
# AC 7: target is an existing regular file -> exit 1 + DESIGN §10.3 message
# ---------------------------------------------------------------------------


def test_init_on_file_target_errors_cleanly(tmp_path: Path, capsys) -> None:
    file_target = tmp_path / "foo.txt"
    file_target.write_text("I am a file, not a directory.", encoding="utf-8")

    rc = init_module.main([str(file_target)])
    assert rc == 1

    captured = capsys.readouterr()
    # Error goes to stderr.
    assert "is a file, not a directory" in captured.err
    assert str(file_target) in captured.err
    # Nothing on stdout -- we bailed before writing anything.
    assert captured.out == ""

    # The file must be unchanged (we must not truncate or mkdir over it).
    assert file_target.read_text(encoding="utf-8") == "I am a file, not a directory."


# ---------------------------------------------------------------------------
# AC 8: no write permission -> exit 1 + DESIGN §10.4 message
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX permission bits only apply on Unix-like systems",
)
def test_init_on_permission_denied_errors_cleanly(tmp_path: Path, capsys) -> None:
    # Running as root bypasses chmod, so this assertion is unreliable for root.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("chmod-based permission test is unreliable when running as root")

    parent = tmp_path / "readonly"
    parent.mkdir()
    original_mode = parent.stat().st_mode
    os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)  # r-x------
    try:
        target = parent / "ws"
        rc = init_module.main([str(target)])
        assert rc == 1

        captured = capsys.readouterr()
        assert "permission denied" in captured.err.lower()
        assert str(target) in captured.err
        assert captured.out == ""
    finally:
        # Restore mode so tmp_path cleanup doesn't fail.
        os.chmod(parent, original_mode)


# ---------------------------------------------------------------------------
# Content integrity: what we wrote matches what the templates package ships
# ---------------------------------------------------------------------------


def test_init_written_content_matches_packaged_templates(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    expected_pairs = {
        ".env.example": "env.example",
        ".env": "env.example",
        ".wikiignore": "wikiignore",
        "sync-sources.local.json": "sync-sources.json",
        "ingest-settings.local.json": "ingest-settings.json",
        "schemas/AGENTS.md": "schemas/AGENTS.md",
    }
    for dest_rel, template_name in expected_pairs.items():
        assert (target / dest_rel).read_text(encoding="utf-8") == _template_text(
            template_name
        ), f"{dest_rel} does not match its source template"


# ---------------------------------------------------------------------------
# LWC-7wkk: workspace .gitignore (DESIGN §6.2)
# ---------------------------------------------------------------------------


# DESIGN §6.2 fixed content. The comment line is part of the contract, and
# tests compare byte-for-byte.
EXPECTED_GITIGNORE = (
    "# llm-wiki workspace \u2014 local state, not for commit\n"
    ".env\n"
    "raw/\n"
    "state/\n"
    "wiki/\n"
)


def test_init_writes_gitignore_with_expected_content(tmp_path: Path) -> None:
    """AC 1: init writes PATH/.gitignore with exactly the DESIGN §6.2 text."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    gitignore = target / ".gitignore"
    assert gitignore.is_file(), ".gitignore was not written"
    assert gitignore.read_text(encoding="utf-8") == EXPECTED_GITIGNORE


def test_init_gitignore_skipped_on_bare_reinit(tmp_path: Path) -> None:
    """AC 2: bare re-run must NOT overwrite an existing .gitignore."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    gitignore = target / ".gitignore"
    user_content = "# user-customized ignore\nmy-secret.txt\n"
    gitignore.write_text(user_content, encoding="utf-8")

    # Bare re-run must leave the user's content intact.
    assert init_module.main([str(target)]) == 0
    assert gitignore.read_text(encoding="utf-8") == user_content


def test_init_gitignore_overwritten_with_force(tmp_path: Path) -> None:
    """AC 2: --force restores .gitignore to canonical content."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    gitignore = target / ".gitignore"
    gitignore.write_text("MUTATED\n", encoding="utf-8")

    assert init_module.main([str(target), "--force"]) == 0
    assert gitignore.read_text(encoding="utf-8") == EXPECTED_GITIGNORE


# ---------------------------------------------------------------------------
# LWC-7wkk: outer-git-repo detection (ARCHITECTURE §8.5, DESIGN §6.3)
# ---------------------------------------------------------------------------


def test_init_detects_outer_git_repo_and_warns(
    tmp_path: Path, capsys
) -> None:
    """AC 3, 7: target inside an existing git repo returns that repo's root."""
    outer = tmp_path / "repo"
    (outer / ".git").mkdir(parents=True)
    target = outer / "subdir"

    assert init_module.main([str(target)]) == 0

    assert init_module._detect_outer_git_repo(target) == outer.resolve()

    captured = capsys.readouterr()
    # The minimal placeholder warning is emitted so end-to-end behavior is
    # observably correct even before LWC-wn2r lands.
    assert "inside an existing" in captured.out
    assert "git repository" in captured.out
    assert str(outer.resolve()) in captured.out


def test_init_does_not_warn_when_outside_git(
    tmp_path: Path, capsys
) -> None:
    """AC 3: a plain tmp_path has no enclosing .git -> returns None."""
    # Defensive: make sure no ancestor of tmp_path has a .git entry that would
    # confuse the walk-up. Pytest's tmp_path lives under a unique subdirectory
    # of the system temp dir, so this is normally safe.
    target = tmp_path / "ws"

    assert init_module.main([str(target)]) == 0

    # We only assert None if the *real* tmp_path has no enclosing repo. On CI
    # or in a sandbox this is always true; skip otherwise to avoid false
    # failures from developers running tests inside a repo whose temp dir is
    # nested under .git.
    if init_module._detect_outer_git_repo(tmp_path / "any") is None:
        assert init_module._detect_outer_git_repo(target) is None
        captured = capsys.readouterr()
        assert "Warning:" not in captured.out


def test_init_does_not_warn_when_target_is_own_repo(tmp_path: Path) -> None:
    """AC 4: .git at target itself is NOT treated as an outer repo."""
    # Set up tmp_path/ws/.git but leave tmp_path with no .git entry.
    if init_module._detect_outer_git_repo(tmp_path / "probe") is not None:
        pytest.skip("tmp_path is nested inside a real git repo; test is moot")

    target = tmp_path / "ws"
    target.mkdir()
    (target / ".git").mkdir()

    assert init_module._detect_outer_git_repo(target) is None


def test_init_detects_submodule_git_file(tmp_path: Path) -> None:
    """AC 5: a .git FILE (submodule/worktree pointer) still counts."""
    outer = tmp_path / "repo"
    outer.mkdir()
    # Submodules and linked worktrees use a .git file instead of a directory.
    (outer / ".git").write_text("gitdir: ../.git/modules/repo\n", encoding="utf-8")

    target = outer / "sub"
    assert init_module._detect_outer_git_repo(target) == outer.resolve()


def test_init_detects_outer_repo_through_symlinked_target(
    tmp_path: Path,
) -> None:
    """AC 6: walk-up resolves symlinks and finds the real parent repo."""
    if sys.platform.startswith("win"):
        pytest.skip("symlink semantics differ on Windows")

    outer = tmp_path / "repo"
    (outer / ".git").mkdir(parents=True)
    real_target = outer / "subdir"
    real_target.mkdir()

    link = tmp_path / "link"
    try:
        link.symlink_to(real_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not support symlinks")

    # Walking up from the symlink's parent (tmp_path) would NOT find the repo;
    # walking up from the resolved real path (outer/subdir) DOES.
    assert init_module._detect_outer_git_repo(link) == outer.resolve()


# ---------------------------------------------------------------------------
# LWC-wn2r: structured final output (DESIGN §5.2, §5.3, §6.3)
# ---------------------------------------------------------------------------


def _resolved(target: Path) -> Path:
    """Helper: the absolute, resolved path init uses in its output."""

    return target.resolve()


def test_init_output_format_matches_design_spec_first_run(
    tmp_path: Path, capsys
) -> None:
    """AC 1: first-run output matches DESIGN §5.2 byte-for-byte."""
    target = tmp_path / "ws"
    rc = init_module.main([str(target)])
    assert rc == 0

    captured = capsys.readouterr()
    expected = (
        f"Initialized workspace at {_resolved(target)}\n"
        "\n"
        "Created:\n"
        "  .env.example, .env, .gitignore, .wikiignore\n"
        "  sync-sources.local.json, ingest-settings.local.json\n"
        "  schemas/AGENTS.md\n"
        "  raw/inbox/, wiki/{summaries,topics,entities}/, state/\n"
        "  index.md, log.md\n"
        "\n"
        "Next steps:\n"
        "  1. Edit .env and set ANTHROPIC_API_KEY\n"
        "  2. Edit sync-sources.local.json to point at your sources\n"
        f"  3. Run: llm-wiki --workspace {target} refresh-fast\n"
    )
    assert captured.out == expected


def test_init_output_format_preserves_user_path_in_next_steps(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """AC 3: Next steps step 3 uses the user's ORIGINAL PATH argument
    verbatim (tilde token preserved), even though the first line prints
    the resolved absolute path."""
    # Run init from an isolated cwd so the relative '~/wikis/foo' token
    # becomes a real directory under tmp_path (Path does not expand '~';
    # init receives the literal string and _resolve_against_cwd stacks it
    # onto cwd). We're not testing tilde expansion here -- we're testing
    # that step 3 emits the ORIGINAL string byte-for-byte.
    monkeypatch.chdir(tmp_path)

    user_path = "~/wikis/foo"
    rc = init_module.main([user_path])
    assert rc == 0

    captured = capsys.readouterr()
    # Step 3 must keep the original tilde form so users can copy/paste it
    # into their own shell (where ~ will expand correctly).
    assert "  3. Run: llm-wiki --workspace ~/wikis/foo refresh-fast" in captured.out
    # The first line uses the resolved absolute path, not the user's literal
    # argument: it must start with the absolute cwd (not the tilde token).
    first_line = captured.out.splitlines()[0]
    assert first_line.startswith(f"Initialized workspace at {tmp_path}")


def test_init_idempotent_output(tmp_path: Path, capsys) -> None:
    """AC 1: fully idempotent re-run prints the DESIGN §5.3 no-op message
    and still includes the Next steps block (AC 2)."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0
    capsys.readouterr()  # discard first-run output

    # Second run: nothing to do.
    assert init_module.main([str(target)]) == 0

    captured = capsys.readouterr()
    expected = (
        f"Workspace already initialized at {_resolved(target)}. "
        "No changes made.\n"
        "\n"
        "Next steps:\n"
        "  1. Edit .env and set ANTHROPIC_API_KEY\n"
        "  2. Edit sync-sources.local.json to point at your sources\n"
        f"  3. Run: llm-wiki --workspace {target} refresh-fast\n"
    )
    assert captured.out == expected


def test_init_mixed_outcome_output(tmp_path: Path, capsys) -> None:
    """AC 1: mixed idempotent re-run (DESIGN §5.3 example).

    Delete .gitignore after first run; second bare run creates it and skips
    the rest. Output must list .gitignore under Created and the rest under
    Skipped (already exist). Directory group must NOT print (this is a
    re-run, not a first run)."""
    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0
    capsys.readouterr()

    # Remove exactly one template file so the second run has a mixed result.
    (target / ".gitignore").unlink()

    assert init_module.main([str(target)]) == 0

    captured = capsys.readouterr()
    expected = (
        f"Initialized workspace at {_resolved(target)}\n"
        "\n"
        "Created:\n"
        "  .gitignore\n"
        "\n"
        "Skipped (already exist):\n"
        "  .env.example, .env, .wikiignore\n"
        "  sync-sources.local.json, ingest-settings.local.json\n"
        "  schemas/AGENTS.md\n"
        "  index.md, log.md\n"
        "\n"
        "Next steps:\n"
        "  1. Edit .env and set ANTHROPIC_API_KEY\n"
        "  2. Edit sync-sources.local.json to point at your sources\n"
        f"  3. Run: llm-wiki --workspace {target} refresh-fast\n"
    )
    assert captured.out == expected


def test_init_warning_block_between_created_and_next_steps(
    tmp_path: Path, capsys
) -> None:
    """AC 4: when _detect_outer_git_repo returns a path, the warning block
    appears AFTER the Created: block and BEFORE the Next steps: block."""
    outer = tmp_path / "repo"
    (outer / ".git").mkdir(parents=True)
    target = outer / "subdir"

    assert init_module.main([str(target)]) == 0

    captured = capsys.readouterr()
    created_idx = captured.out.index("Created:")
    warning_idx = captured.out.index("Warning:")
    next_steps_idx = captured.out.index("Next steps:")
    # Strict ordering: Created: < Warning: < Next steps:.
    assert created_idx < warning_idx < next_steps_idx


def test_init_warning_uses_exact_design_text(tmp_path: Path, capsys) -> None:
    """AC 5: warning text matches DESIGN §6.3 byte-for-byte."""
    outer = tmp_path / "repo"
    (outer / ".git").mkdir(parents=True)
    target = outer / "subdir"

    assert init_module.main([str(target)]) == 0

    captured = capsys.readouterr()
    expected_warning = (
        f"Warning: {_resolved(target)} is inside an existing\n"
        f"git repository ({_resolved(outer)}). A workspace .gitignore\n"
        "was written covering .env, raw/, state/, and wiki/, but you\n"
        "should verify before committing.\n"
    )
    assert expected_warning in captured.out


def test_init_absent_warning_when_no_outer_repo(
    tmp_path: Path, capsys
) -> None:
    """AC 4: no warning block when the target is not inside an outer repo."""
    # Guard: pytest's tmp_path may be nested under the real tool repo in some
    # CI configurations. Skip the assertion in that case so we don't flake.
    if init_module._detect_outer_git_repo(tmp_path / "probe") is not None:
        pytest.skip("tmp_path is nested inside a real git repo; test is moot")

    target = tmp_path / "ws"
    assert init_module.main([str(target)]) == 0

    captured = capsys.readouterr()
    assert "Warning:" not in captured.out


def test_init_warning_does_not_affect_exit_code(
    tmp_path: Path,
) -> None:
    """AC 6: warning is advisory; init still returns 0 when it fires."""
    outer = tmp_path / "repo"
    (outer / ".git").mkdir(parents=True)
    target = outer / "subdir"

    assert init_module.main([str(target)]) == 0
