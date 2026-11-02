"""Deleting chattr.sqlite and rebuilding it from the rendered pages gives the same records."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.rebuild import compare_databases, rebuild_database
from chattr.render import Renderer


@pytest.fixture(scope="module")
def rebuilt(full_archive, tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("rebuild")
    site = base / "site"
    roots = [full_archive.media, *sorted((full_archive.root / "Output").glob("*/Attachments"))]
    with DatabaseManager(full_archive.database) as db:
        render_pages(Renderer(db), site, roots)
    target = base / "rebuilt.sqlite"
    result = rebuild_database(site, target)
    return site, target, result


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_the_rebuilt_database_matches_the_original_exactly(full_archive, rebuilt) -> None:
    _, target, _ = rebuilt
    assert compare_databases(full_archive.database, target) == []


@pytest.mark.parametrize(
    "table", ["conversations", "messages", "attachments", "reactions", "handles", "participants"]
)
def test_every_count_is_identical(full_archive, rebuilt, table: str) -> None:
    _, target, _ = rebuilt
    assert _count(target, table) == _count(full_archive.database, table) > 0


def test_ids_hashes_and_versions_are_carried_over_not_recomputed(full_archive, rebuilt) -> None:
    _, target, _ = rebuilt
    query = "SELECT id, content_hash, version FROM messages ORDER BY id"
    with sqlite3.connect(full_archive.database) as a, sqlite3.connect(target) as b:
        assert a.execute(query).fetchall() == b.execute(query).fetchall()


def test_the_result_reports_what_was_read(full_archive, rebuilt) -> None:
    site, _, result = rebuilt
    assert result.messages == _count(full_archive.database, "messages")
    assert result.conversations == _count(full_archive.database, "conversations")
    assert result.pages == len([p for p in site.rglob("(*) *.html")])
    assert result.skipped == []


def test_the_legacy_view_gives_the_same_rows(full_archive, rebuilt) -> None:
    _, target, _ = rebuilt
    query = "SELECT * FROM messages_view ORDER BY id"
    with sqlite3.connect(full_archive.database) as a, sqlite3.connect(target) as b:
        assert a.execute(query).fetchall() == b.execute(query).fetchall()


def test_rendering_the_rebuilt_database_gives_identical_pages(
    full_archive, rebuilt, tmp_path: Path
) -> None:
    site, target, _ = rebuilt
    again = tmp_path / "again"
    roots = [full_archive.media, *sorted((full_archive.root / "Output").glob("*/Attachments"))]
    with DatabaseManager(target) as db:
        render_pages(Renderer(db), again, roots)
    first = {
        p.relative_to(site): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in site.rglob("*.html")
    }
    second = {
        p.relative_to(again): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in again.rglob("*.html")
    }
    assert first == second and len(first) > 10


def test_rebuilding_twice_into_fresh_files_is_repeatable(rebuilt, tmp_path: Path) -> None:
    site, target, _ = rebuilt
    other = tmp_path / "other.sqlite"
    rebuild_database(site, other)
    assert compare_databases(target, other) == []


def test_the_comparison_notices_differences(full_archive, rebuilt, tmp_path: Path) -> None:
    _, target, _ = rebuilt
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(target.read_bytes())
    with sqlite3.connect(broken) as db:
        db.execute("DELETE FROM messages WHERE rowid = (SELECT MIN(rowid) FROM messages)")
        db.execute("UPDATE handles SET person = 'Someone Else' WHERE id = 1")
    differences = compare_databases(full_archive.database, broken)
    assert any("messages" in d for d in differences) and any("handles" in d for d in differences)
