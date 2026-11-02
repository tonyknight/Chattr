"""`chattr-cli rebuild`: safe, clear, and all-or-nothing."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from chattr import cli
from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.rebuild import RebuildError, compare_databases, rebuild_database
from chattr.render import Renderer


@pytest.fixture
def site(full_archive, tmp_path: Path) -> Path:
    out = tmp_path / "site"
    roots = [full_archive.media, *sorted((full_archive.root / "Output").glob("*/Attachments"))]
    with DatabaseManager(full_archive.database) as db:
        render_pages(Renderer(db), out, roots)
    return out


def _count(path: Path) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])


def test_the_command_rebuilds_a_database(full_archive, site: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "again.sqlite"
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path / "Chattr"),
                "rebuild",
                "--from",
                str(site),
                "--db",
                str(target),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"{_count(full_archive.database)} messages" in out
    assert compare_databases(full_archive.database, target) == []


def test_the_defaults_are_the_pages_and_the_database_of_the_root(
    full_archive, tmp_path: Path, chattr_root: Path
) -> None:
    pages = chattr_root / "Output" / "Chattr"
    with DatabaseManager(full_archive.database) as db:
        render_pages(Renderer(db), pages, [full_archive.media])
    assert cli.main(["--root", str(chattr_root), "rebuild"]) == 0
    assert (
        compare_databases(full_archive.database, chattr_root / "Database" / "chattr.sqlite") == []
    )


def test_it_refuses_a_database_that_holds_messages(
    full_archive, site: Path, tmp_path: Path, capsys
) -> None:
    existing = tmp_path / "existing.sqlite"
    shutil.copy(full_archive.database, existing)
    before = existing.read_bytes()
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path / "Chattr"),
                "rebuild",
                "--from",
                str(site),
                "--db",
                str(existing),
            ]
        )
        == 1
    )
    assert "move it away" in capsys.readouterr().out
    assert existing.read_bytes() == before


def test_an_empty_database_is_fine(site: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    with DatabaseManager(empty):
        pass
    assert rebuild_database(site, empty).messages > 0


def test_an_empty_or_missing_site_is_a_plain_error(tmp_path: Path, capsys) -> None:
    root = tmp_path / "Chattr"
    assert cli.main(["--root", str(root), "rebuild", "--from", str(tmp_path / "nothing")]) == 1
    assert "No Chattr pages" in capsys.readouterr().out
    (tmp_path / "blank").mkdir()
    assert cli.main(["--root", str(root), "rebuild", "--from", str(tmp_path / "blank")]) == 1
    assert not (root / "Database" / "chattr.sqlite").exists()


def _a_page(site: Path) -> Path:
    return next(p for p in sorted(site.rglob("(*) *.html")) if "Ana Costa" in p.name)


def test_damaged_or_foreign_pages_are_skipped_and_reported(
    full_archive, site: Path, tmp_path: Path, capsys
) -> None:
    damaged = _a_page(site)
    damaged.write_text(
        re.sub(
            r'(id="chattr-data">).*?(</script>)', r"\1{not json\2", damaged.read_text(), flags=re.S
        )
    )
    other = next(p for p in sorted(site.rglob("(*) *.html")) if p != damaged)
    other.write_text(
        other.read_text().replace('"schema":"chattr-page-1"', '"schema":"chattr-page-9"')
    )
    (site / "notes.html").write_text("<html><body>hello</body></html>")
    target = tmp_path / "r.sqlite"
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path / "Chattr"),
                "rebuild",
                "--from",
                str(site),
                "--db",
                str(target),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "2 page(s) skipped" in out and "damaged data island" in out and "chattr-page-9" in out
    assert "notes.html" not in out  # not one of ours: ignored, not an error
    assert 0 < _count(target) < _count(full_archive.database)


def test_a_page_that_cannot_be_applied_leaves_nothing_behind(site: Path, tmp_path: Path) -> None:
    page = _a_page(site)
    text = page.read_text()
    island = json.loads(re.search(r'id="chattr-data">(.*?)</script>', text, re.S).group(1))
    del island["messages"][0]["timestamp_utc"]  # a message the database can't take
    page.write_text(
        re.sub(
            r'(id="chattr-data">).*?(</script>)',
            lambda m: m.group(1) + json.dumps(island) + m.group(2),
            text,
            flags=re.S,
        )
    )
    target = tmp_path / "partial.sqlite"
    with pytest.raises(RebuildError, match=re.escape(page.name)):
        rebuild_database(site, target)
    assert not target.exists()  # nothing half-built
    existing = tmp_path / "kept.sqlite"
    with DatabaseManager(existing):
        pass
    with pytest.raises(RebuildError):
        rebuild_database(site, existing)
    assert _count(existing) == 0  # rolled back
