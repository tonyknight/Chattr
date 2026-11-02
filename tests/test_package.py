"""Chattr is an installable package with a `chattr` command."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

import pytest

MODULES = [
    "chattr",
    "chattr.cli",
    "chattr.config",
    "chattr.preflight",
    "chattr.database_manager",
    "chattr.html_generator",
    "chattr.attachment_manager",
    "chattr.imessage_tools",
    "chattr.parsers.base_parser",
    "chattr.parsers.whatsapp_parser",
    "chattr.parsers.messenger_parser",
    "chattr.parsers.imessage_database_parser",
]


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_package_data_is_bundled() -> None:
    from importlib.resources import files

    root = files("chattr")
    assert root.joinpath("templates", "template.html").is_file()
    assert root.joinpath("defaults", "config.json").is_file()
    assert root.joinpath("defaults", "Services", "iMessage.json").is_file()


def test_module_entry_point_prints_version() -> None:
    import chattr

    result = subprocess.run(
        [sys.executable, "-m", "chattr", "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert chattr.__version__ in result.stdout


def test_console_script_prints_version() -> None:
    import chattr

    exe = shutil.which("chattr-cli")
    assert exe, "the chattr-cli console script should be installed"
    result = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert chattr.__version__ in result.stdout


def test_no_bare_chattr_script_shadows_the_linux_tool() -> None:
    """`chattr` is a standard Linux command (e2fsprogs); Chattr must not install one."""
    from importlib.metadata import entry_points

    scripts = {
        ep.name for ep in entry_points(group="console_scripts") if ep.value.startswith("chattr.")
    }
    assert "chattr-cli" in scripts
    assert "chattr" not in scripts
