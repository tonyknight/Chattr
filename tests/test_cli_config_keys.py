"""Every configuration key the CLI reads exists in a freshly prepared Chattr root."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import chattr
from chattr import preflight
from chattr.config import Settings

CLI_SOURCE = Path(chattr.__file__).resolve().parent / "cli.py"
KEY_CALL = re.compile(r"""\.get(?:_expanded_path)?\(\s*['"]([a-z_]+(?:\.[a-z_]+)+)['"]""")


def cli_keys() -> list[str]:
    return sorted(set(KEY_CALL.findall(CLI_SOURCE.read_text(encoding="utf-8"))))


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("CHATTR_ROOT", raising=False)
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False)
    return Settings.load(root)


def test_the_cli_reads_configuration_keys() -> None:
    assert cli_keys(), "expected the CLI to read settings"


@pytest.mark.parametrize("key", cli_keys())
def test_every_cli_key_resolves(settings: Settings, key: str) -> None:
    missing = object()
    assert settings.get(key, missing) is not missing, f"cli.py reads '{key}', which doesn't exist"


def test_chattr_cli_with_root_starts_and_exits_cleanly(tmp_path: Path) -> None:
    import os
    import shutil
    import site
    import subprocess

    home, root = tmp_path / "home", tmp_path / "Chattr"
    home.mkdir()
    # Isolate HOME (so ~/.chattr.json is the test's) but keep per-user packages importable:
    # Python derives the user site folder from HOME unless PYTHONUSERBASE is set.
    env = {k: v for k, v in os.environ.items() if k != "CHATTR_ROOT"} | {
        "HOME": str(home),
        "PYTHONUSERBASE": site.getuserbase(),
    }
    exe = shutil.which("chattr-cli")
    assert exe
    result = subprocess.run(
        [exe, "--root", str(root)], input="5\n", capture_output=True, text=True, env=env,
        timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "Database" / "chattr.sqlite").is_file()
    assert (root / "Services" / "iMessage.json").is_file()


def test_cli_database_lives_in_the_root(settings: Settings) -> None:
    from chattr.cli import database_path

    assert database_path(settings) == settings.root / "Database" / "chattr.sqlite"
