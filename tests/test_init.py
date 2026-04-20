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
