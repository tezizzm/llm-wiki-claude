import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLI_PATH = ROOT / "scripts" / "cli.py"
VERSION_PATH = ROOT / "scripts" / "version.py"

cli_spec = importlib.util.spec_from_file_location("cli_module", CLI_PATH)
cli = importlib.util.module_from_spec(cli_spec)
assert cli_spec and cli_spec.loader
cli_spec.loader.exec_module(cli)

version_spec = importlib.util.spec_from_file_location("version_module", VERSION_PATH)
version = importlib.util.module_from_spec(version_spec)
assert version_spec and version_spec.loader
version_spec.loader.exec_module(version)


def test_read_version_matches_version_file():
    assert version.read_version() == Path("VERSION").read_text(encoding="utf-8").strip()


def test_cli_version_flag_prints_version():
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("Expected SystemExit from argparse version flag")

    assert version.read_version() in stdout.getvalue()
