"""The tool is called "Chattr" everywhere in the package."""

from __future__ import annotations

import re
from pathlib import Path

import chattr

OLD_NAMES = re.compile(r"Chatter|chatter|CHATTER|Chat2HTML|chat2html|Chat Archiver")
PACKAGE_DIR = Path(chattr.__file__).resolve().parent


def test_no_old_names_in_the_package() -> None:
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".html", ".txt"}:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD_NAMES.search(line):
                    offenders.append(f"{path.relative_to(PACKAGE_DIR)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders[:20])


def test_preflight_uses_the_new_names() -> None:
    from chattr import preflight

    assert preflight.CHATTR_CONFIG_FILE == ".chattr.json"
    assert preflight.DEFAULT_CHATTR_ROOT == "~/Documents/Chattr"
    assert "Chattr" in preflight.CHATTR_STRUCTURE["Output"]


def test_default_database_file_is_chattr_sqlite() -> None:
    text = (PACKAGE_DIR / "defaults" / "config.json").read_text(encoding="utf-8")
    assert "{CHATTR_ROOT}/Database/chattr.sqlite" in text
