from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION"


def read_version() -> str:
    return VERSION_PATH.read_text(encoding="utf-8").strip()
