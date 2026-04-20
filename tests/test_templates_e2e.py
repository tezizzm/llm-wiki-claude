"""End-to-end tests for the ``scripts.templates`` package (epic LWC-6tno capstone).

Story: LWC-drjq.

Proves two user-visible invariants for the templates epic:

1. ``scripts/templates/`` is a proper package whose data files are readable
   via :mod:`importlib.resources` under ``pip install -e .`` (ARCHITECTURE
   §9.2). If the package data cannot be read at runtime, ``llm-wiki init``
   cannot scaffold a workspace.
2. Running :func:`scripts.init.main` actually writes the packaged template
   bytes to the target workspace -- byte-for-byte equality between the
   packaged source and what lands on disk, covering the full chain from
   ``importlib.resources`` through ``init`` to the filesystem.

These tests have NO mocks, NO stubs, NO fakes, and NO monkeypatches. They
read the real package via ``importlib.resources`` and call ``init.main``
directly against a pytest ``tmp_path`` -- every assertion checks what
actually ended up on disk.

Template -> workspace destination mapping
-----------------------------------------

``init`` writes package data to workspace files under the following names
(see ``scripts/init.py`` and ``tests/test_init.py``):

- ``env.example``            -> ``.env.example`` AND ``.env`` (same bytes)
- ``sync-sources.json``      -> ``sync-sources.local.json``
- ``ingest-settings.json``   -> ``ingest-settings.local.json``
- ``wikiignore``             -> ``.wikiignore``
- ``schemas/AGENTS.md``      -> ``schemas/AGENTS.md``

``TEMPLATE_FILES`` below records the package-name / workspace-destination
pairs so the byte-equality assertions in
:func:`test_init_consumes_templates_end_to_end` match what ``init`` actually
produces on disk. The ``env.example`` template is written verbatim to
``.env.example``; ``init`` also duplicates those bytes into ``.env`` per
ARCHITECTURE §8.3 (that second destination is asserted explicitly inside
the test rather than doubled up in the list).
"""

from __future__ import annotations

import importlib.resources as ir
from pathlib import Path


# Package-filename -> workspace-relative destination written by ``init``.
# Order matches the ``Created:`` groups in DESIGN §5.2 output. ``.env`` is a
# second destination for the same template bytes and is covered by an
# additional assertion inside the e2e test so the list stays 1:1 with
# unique template files.
TEMPLATE_FILES = [
    ("env.example", ".env.example"),
    ("sync-sources.json", "sync-sources.local.json"),
    ("ingest-settings.json", "ingest-settings.local.json"),
    ("wikiignore", ".wikiignore"),
]


# ---------------------------------------------------------------------------
# AC 2: package is importable
# ---------------------------------------------------------------------------


def test_template_package_is_importable() -> None:
    """``scripts.templates`` must be importable as a package.

    ``pyproject.toml`` declares ``scripts.templates`` under
    ``[tool.setuptools] packages`` and ships its data files via
    ``[tool.setuptools.package-data]`` (ARCHITECTURE §9.2). Under
    ``pip install -e .`` the package must be importable and must expose a
    ``__package__`` attribute so :func:`importlib.resources.files` can
    resolve it.
    """
    import scripts.templates

    assert hasattr(scripts.templates, "__package__")
    assert scripts.templates.__package__ == "scripts.templates"


# ---------------------------------------------------------------------------
# AC 3: every top-level template file is readable via importlib.resources
# ---------------------------------------------------------------------------


def test_every_template_file_readable_via_importlib_resources() -> None:
    """Each packaged template file must be readable via importlib.resources.

    Covers ``env.example``, ``sync-sources.json``, ``ingest-settings.json``,
    and ``wikiignore``. We require non-empty text so we also catch a
    silently-empty resource (e.g. a build that shipped an empty placeholder).
    """
    for pkg_name, _ in TEMPLATE_FILES:
        text = (
            ir.files("scripts.templates")
            .joinpath(pkg_name)
            .read_text(encoding="utf-8")
        )
        assert text, f"{pkg_name} empty or unreadable via importlib.resources"


# ---------------------------------------------------------------------------
# AC 4: sub-package schemas/AGENTS.md is readable
# ---------------------------------------------------------------------------


def test_schemas_agents_md_readable_via_importlib_resources() -> None:
    """``scripts.templates.schemas`` is a sub-package and its ``AGENTS.md``
    must be readable via importlib.resources.

    Some loaders do NOT descend into subpackages via a simple ``/``-joined
    traversal, so ``init`` reads this file by naming the subpackage
    explicitly. We mirror that access pattern here.
    """
    text = (
        ir.files("scripts.templates.schemas")
        .joinpath("AGENTS.md")
        .read_text(encoding="utf-8")
    )
    assert text, "scripts/templates/schemas/AGENTS.md empty or unreadable"


# ---------------------------------------------------------------------------
# AC 5: init.main consumes the templates and writes them byte-for-byte to disk
# ---------------------------------------------------------------------------


def test_init_consumes_templates_end_to_end(tmp_path: Path) -> None:
    """Running :func:`scripts.init.main` scaffolds a workspace whose files
    match the packaged template bytes.

    This is the full end-to-end path: we call ``init.main`` directly (no
    subprocess here -- we want a clean in-process check that the loaded
    ``scripts.templates`` package is what ``init`` actually writes), then
    read each workspace file and compare it byte-for-byte to the package
    resource.

    No mocks, no monkeypatches, no fakes. Real templates, real filesystem.
    """
    from scripts import init as init_mod

    ws = tmp_path / "ws"
    rc = init_mod.main([str(ws)])
    assert rc == 0, f"init.main exit {rc} (expected 0)"

    # Every template file listed in TEMPLATE_FILES must land at its
    # documented workspace destination with byte-identical content.
    for pkg_name, dest_name in TEMPLATE_FILES:
        pkg_text = (
            ir.files("scripts.templates")
            .joinpath(pkg_name)
            .read_text(encoding="utf-8")
        )
        dest_text = (ws / dest_name).read_text(encoding="utf-8")
        assert pkg_text == dest_text, (
            f"{dest_name} does not match template {pkg_name} byte-for-byte"
        )

    # ARCHITECTURE §8.3: ``.env`` is also written from ``env.example``.
    env_template = (
        ir.files("scripts.templates")
        .joinpath("env.example")
        .read_text(encoding="utf-8")
    )
    assert (ws / ".env").read_text(encoding="utf-8") == env_template, (
        ".env does not match env.example byte-for-byte"
    )

    # The ``schemas/AGENTS.md`` sub-package resource must also land on disk
    # byte-for-byte at ``<workspace>/schemas/AGENTS.md``.
    schema_template = (
        ir.files("scripts.templates.schemas")
        .joinpath("AGENTS.md")
        .read_text(encoding="utf-8")
    )
    assert (ws / "schemas" / "AGENTS.md").read_text(encoding="utf-8") == (
        schema_template
    ), "schemas/AGENTS.md does not match packaged template byte-for-byte"
