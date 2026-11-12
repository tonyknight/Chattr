"""iMessage attachments: copied out of the Mac's store when they are there, and counted when not.

Messages downloads an attachment from iCloud only when its part of the conversation has been shown,
so a database usually names files that are not on the Mac. Those are reported by conversation, so a
person can go and load them in Messages before importing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings


def _attachment_root(fixture_family: FamilyExports) -> Path:
    return fixture_family.paths["imessage"].parent / "Attachments"


def _copy(fixture_family: FamilyExports, tmp_path: Path) -> tuple[Path, Path]:
    """The fixture's chat.db and attachment store, copied so a test can remove files from it."""
    import shutil

    folder = tmp_path / "Messages"
    shutil.copytree(fixture_family.paths["imessage"].parent, folder)
    return folder / "chat.db", folder / "Attachments"


def _import(chattr_root: Path, database: Path, root: Path, staging: Path):
    settings = Settings.load(chattr_root)
    result = commands.convert_imessage(
        settings, db_path=database, attachment_root=root, attachments_dir=staging,
        spam_mode="disabled",
    )  # fmt: skip
    return settings, result


def test_attachments_on_the_mac_are_copied_and_recorded(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    staging = tmp_path / "incoming"
    settings, result = _import(
        chattr_root, fixture_family.paths["imessage"], _attachment_root(fixture_family), staging
    )
    with sqlite3.connect(commands.central_database(settings)) as db:
        rows = db.execute(
            "SELECT original_filename, storage_path, sha256 FROM attachments ORDER BY 1"
        ).fetchall()
    assert [name for name, _, _ in rows] == ["IMG_0001.PNG", "IMG_0002.PNG", "sticker.gif"]
    for _, stored, digest in rows:
        assert stored and (staging / stored).is_file() and digest in stored
    assert result.missing_attachments == 0
    # the Mac's own store is untouched
    assert len(list(_attachment_root(fixture_family).rglob("IMG_000*.PNG"))) == 2


def test_published_pages_show_the_files_under_the_person(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    staging = tmp_path / "incoming"
    settings, _ = _import(
        chattr_root, fixture_family.paths["imessage"], _attachment_root(fixture_family), staging
    )
    out = tmp_path / "out"
    commands.publish(settings, out=out, attachments_dirs=[staging])
    photos = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.jpg"))
    assert len(photos) == 2 and all("/iMessage/(2023-06-04)" in photo for photo in photos)
    page = next(out.rglob("(2023) *.html")).read_text(encoding="utf-8")
    assert "file not in this archive" not in page and "<img" in page


def test_a_file_that_is_not_on_the_mac_is_counted_not_copied(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    database, root = _copy(fixture_family, tmp_path)
    next(root.rglob("IMG_0001.PNG")).unlink()  # still in iCloud
    staging = tmp_path / "incoming"
    settings, result = _import(chattr_root, database, root, staging)
    assert result.missing_attachments == 1
    with sqlite3.connect(commands.central_database(settings)) as db:
        stored = dict(db.execute("SELECT original_filename, storage_path FROM attachments"))
    assert stored["IMG_0001.PNG"] is None and stored["IMG_0002.PNG"]


def test_importing_again_once_the_file_has_arrived_brings_it_in(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    database, root = _copy(fixture_family, tmp_path)
    photo = next(root.rglob("IMG_0001.PNG"))
    data = photo.read_bytes()
    photo.unlink()
    staging = tmp_path / "incoming"
    settings, first = _import(chattr_root, database, root, staging)
    assert first.missing_attachments == 1
    photo.write_bytes(data)  # loaded in Messages since
    _, second = _import(chattr_root, database, root, staging)
    assert second.missing_attachments == 0
    with sqlite3.connect(commands.central_database(settings)) as db:
        rows = db.execute(
            "SELECT storage_path FROM attachments WHERE original_filename = 'IMG_0001.PNG'"
        ).fetchall()
    assert len(rows) == 1 and rows[0][0]  # the same attachment, now with its file


# --- The report -----------------------------------------------------------------------------------


def test_the_report_says_which_conversations_miss_files(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    database, root = _copy(fixture_family, tmp_path)
    for photo in root.rglob("IMG_000*.PNG"):
        photo.unlink()
    report = commands.missing_attachments(database, root)
    assert (report.total, report.missing) == (3, 2) and report.missing_bytes > 0
    (worst,) = report.conversations
    assert worst.missing == 2 and worst.total == 2
    assert worst.first == worst.last == "2023-06-04"
    assert worst.name == family.ANA.phones[0]
    assert [f.name for f in worst.files] == ["IMG_0001.PNG", "IMG_0002.PNG"]
    assert {f.reason for f in worst.files} == {"file not on this Mac"}


def test_nothing_missing_is_an_empty_report(fixture_family: FamilyExports) -> None:
    report = commands.missing_attachments(
        fixture_family.paths["imessage"], _attachment_root(fixture_family)
    )
    assert report.missing == 0 and report.conversations == []


def test_an_attachment_with_no_path_is_its_own_reason(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    database, root = _copy(fixture_family, tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE attachment SET filename = NULL WHERE transfer_name = 'sticker.gif'")
    report = commands.missing_attachments(database, root)
    assert report.missing == 1
    assert report.conversations[0].files[0].reason == "no file was ever recorded"


def test_the_command_line_prints_the_report(
    fixture_family: FamilyExports, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, root = _copy(fixture_family, tmp_path)
    for photo in root.rglob("IMG_000*.PNG"):
        photo.unlink()
    assert cli.main(["missing", str(database), "--attachment-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "3 attachments, 2 missing" in out and family.ANA.phones[0] in out
    assert "IMG_0001.PNG" not in out  # the summary; the files come with --list
    assert (
        cli.main(
            [
                "missing",
                str(database),
                "--attachment-root",
                str(root),
                "--list",
                family.ANA.phones[0][-4:],
            ]
        )
        == 0
    )
    assert "IMG_0001.PNG" in capsys.readouterr().out
