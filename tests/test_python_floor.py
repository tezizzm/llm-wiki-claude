"""Integration test guarding the declared Python floor in pyproject.toml.

scripts/workspace.py (per ARCHITECTURE.md §3) uses
``@dataclass(frozen=True, slots=True)``. The ``slots=True`` keyword argument
was added to ``dataclasses.dataclass`` in Python 3.10. If pyproject.toml
advertises a floor below 3.10, pip will happily install the package on 3.9
and the user will hit a ``TypeError`` at first import of any module that
transitively imports ``scripts.workspace``.

This test reads the live pyproject.toml from the repo root (no mocking, no
fixtures) and asserts the declared ``requires-python`` lower bound is
``>= (3, 10)``. Bug LWC-lw8o tracked the original mismatch.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

# ``tomllib`` ships in the standard library from Python 3.11. The new floor is
# 3.10, so we need a fallback for the 3.10 case. A regex-based read is safe
# here because we are only interested in a single string value that lives
# under the ``[project]`` table in a repo-controlled file.
try:  # pragma: no cover - branch depends on interpreter version
    import tomllib  # type: ignore[import-not-found]

    _HAVE_TOMLLIB = True
except ModuleNotFoundError:  # pragma: no cover - only taken on 3.10
    _HAVE_TOMLLIB = False


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

_REQUIRES_PYTHON_LINE = re.compile(
    r"""^\s*requires-python\s*=\s*["'](?P<value>[^"']+)["']\s*$""",
    re.MULTILINE,
)

_LOWER_BOUND = re.compile(
    r""">=\s*(?P<major>\d+)\.(?P<minor>\d+)(?:\.\d+)?"""
)


def _read_requires_python() -> str:
    """Return the raw ``requires-python`` string from pyproject.toml."""
    assert PYPROJECT.is_file(), f"pyproject.toml not found at {PYPROJECT}"

    if _HAVE_TOMLLIB:
        with PYPROJECT.open("rb") as fh:
            data = tomllib.load(fh)
        project = data.get("project")
        assert isinstance(project, dict), (
            "pyproject.toml is missing a [project] table"
        )
        value = project.get("requires-python")
        assert isinstance(value, str) and value, (
            "pyproject.toml [project] is missing requires-python"
        )
        return value

    # Fallback for Python 3.10 (no stdlib tomllib): scan the file for the
    # requires-python line directly. This is adequate because pyproject.toml
    # is repo-controlled and the field is always written as a simple string
    # on its own line.
    text = PYPROJECT.read_text(encoding="utf-8")
    match = _REQUIRES_PYTHON_LINE.search(text)
    assert match is not None, (
        "pyproject.toml does not declare requires-python on its own line"
    )
    return match.group("value")


def _parse_lower_bound(requires_python: str) -> Tuple[int, int]:
    """Extract the (major, minor) lower bound from a PEP 440 specifier."""
    match = _LOWER_BOUND.search(requires_python)
    assert match is not None, (
        f"requires-python {requires_python!r} has no >= lower bound; "
        "bug LWC-lw8o requires an explicit >=3.10 (or higher) floor"
    )
    return int(match.group("major")), int(match.group("minor"))


def test_requires_python_floor_is_at_least_310() -> None:
    """pyproject.toml must declare requires-python >= 3.10.

    Rationale: scripts/workspace.py uses @dataclass(slots=True), which is a
    3.10+ feature (ARCHITECTURE §3 is authoritative). Installing on 3.9
    would succeed metadata-wise but TypeError at first import.
    """
    requires_python = _read_requires_python()
    major, minor = _parse_lower_bound(requires_python)

    assert (major, minor) >= (3, 10), (
        f"requires-python lower bound is {major}.{minor}, but scripts/"
        f"workspace.py uses @dataclass(slots=True) which requires >=3.10. "
        f"Update pyproject.toml [project].requires-python to '>=3.10' "
        f"(or higher). See bug LWC-lw8o."
    )
