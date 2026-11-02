"""`build_all` creates every source, deterministically."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fixtures import FamilyExports, build_all

SOURCES = {
    "contacts",
    "imessage",
    "whatsapp",
    "messenger",
    "instagram",
    "mbox",
    "google_voice",
    "iphone_voicemail",
}


def _fingerprint(root: Path) -> dict[str, str]:
    """Hash every file; SQLite databases by their logical content, everything else by bytes."""
    prints: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".db":
            conn = sqlite3.connect(path)
            dump = "\n".join(conn.iterdump())
            conn.close()
            prints[rel] = hashlib.sha256(dump.encode("utf-8")).hexdigest()
        else:
            prints[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return prints


def test_build_all_creates_every_source(fixture_family: FamilyExports) -> None:
    assert set(fixture_family.paths) == SOURCES
    for name, path in fixture_family.paths.items():
        assert path.exists(), name


def test_two_builds_are_identical(tmp_path: Path) -> None:
    build_all(tmp_path / "a")
    build_all(tmp_path / "b")
    assert _fingerprint(tmp_path / "a") == _fingerprint(tmp_path / "b")
