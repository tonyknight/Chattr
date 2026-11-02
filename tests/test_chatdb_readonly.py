"""Chattr never writes to a `chat.db`: every connection to one is read-only."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports

import chattr
from chattr import chatdb
from chattr.imessage_tools import DatabaseDiagnostics

PACKAGE = Path(chattr.__file__).resolve().parent
CHAT_DB_MODULES = [
    PACKAGE / "imessage_tools.py",
    PACKAGE / "parsers" / "imessage_database_parser.py",
    PACKAGE / "parsers" / "imessage_parser.py",
    PACKAGE / "imessage_export.py",
    PACKAGE / "commands.py",
]


@pytest.mark.parametrize("module", CHAT_DB_MODULES, ids=lambda p: p.name)
def test_modules_that_read_chat_db_never_open_it_directly(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    assert not re.search(
        r"sqlite3\.connect\(", source
    ), f"{module.name} opens a database with sqlite3.connect; use chatdb.connect_readonly"


def test_the_helper_cannot_write(fixture_family: FamilyExports) -> None:
    conn = chatdb.connect_readonly(fixture_family.paths["imessage"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] > 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE intruder (a)")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE message SET text = 'changed'")
    finally:
        conn.close()


def test_a_missing_database_is_an_error_not_a_new_empty_file(tmp_path: Path) -> None:
    missing = tmp_path / "chat.db"
    with pytest.raises(sqlite3.OperationalError):
        chatdb.connect_readonly(missing)
    assert not missing.exists()


def test_paths_with_special_characters_open(tmp_path: Path, fixture_family: FamilyExports) -> None:
    odd = tmp_path / "My Messages #1 ?x=y" / "chat.db"
    odd.parent.mkdir()
    odd.write_bytes(fixture_family.paths["imessage"].read_bytes())
    conn = chatdb.connect_readonly(odd)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0] > 0
    finally:
        conn.close()


def _fingerprint(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def test_diagnostics_leave_a_read_only_copy_untouched(
    tmp_path: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "chat.db"
    db_path.write_bytes(fixture_family.paths["imessage"].read_bytes())
    os.chmod(db_path, 0o444)
    before = _fingerprint(db_path)
    DatabaseDiagnostics(
        str(db_path), str(fixture_family.paths["imessage"].parent / "Attachments")
    ).run_full_diagnostic()
    assert _fingerprint(db_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chat.db"]  # no journal or wal files
