"""WhatsApp messages reach the archive with their media, precision and conversation type."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings


def _export(fixture_family: FamilyExports, folder: str) -> Path:
    return fixture_family.paths["whatsapp"] / folder / "_chat.txt"


def _convert(root: Path, export: Path, **options):
    return commands.convert(Settings.load(root), "whatsapp", export, **options)


def _rows(root: Path, sql: str):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql).fetchall()


def test_attached_media_is_recorded_with_its_hash(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, _export(fixture_family, "WhatsApp Chat - Ana Costa"))
    ((sha, name, mime, size),) = _rows(
        chattr_root, "SELECT sha256, original_filename, mime_type, size FROM attachments"
    )
    photo = next(
        (fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa").glob("*-PHOTO-*.png")
    )
    assert sha == hashlib.sha256(photo.read_bytes()).hexdigest()
    assert name == photo.name and mime == "image/png" and size == photo.stat().st_size


def test_media_notice_and_text_content_types(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, _export(fixture_family, "WhatsApp Chat - Ana Costa"))
    kinds = dict(_rows(chattr_root, "SELECT COALESCE(body, ''), content_type FROM messages"))
    assert kinds[""] == "media"  # the photo: no text of its own
    assert kinds["image omitted"] == "media"
    assert kinds["Mãe, look what I found at the market"] == "text"
    assert [
        r[0]
        for r in _rows(
            chattr_root, "SELECT content_type FROM messages WHERE sender_handle_id IS NULL"
        )
    ] == ["system"]


def test_minute_precision_and_assumed_time_zone(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, _export(fixture_family, "WhatsApp Chat with Rui Pereira"))
    assert set(_rows(chattr_root, "SELECT time_precision, tz_assumed FROM messages")) == {
        ("minute", 1)
    }


def test_a_missing_media_file_is_still_recorded(chattr_root: Path, tmp_path: Path) -> None:
    export = tmp_path / "WhatsApp Chat - X" / "_chat.txt"
    export.parent.mkdir()
    export.write_text("15/04/2024, 10:06 - Rui: IMG-20240415-WA0009.jpg (file attached)\n")
    _convert(chattr_root, export)
    assert _rows(chattr_root, "SELECT original_filename, size FROM attachments") == [
        ("IMG-20240415-WA0009.jpg", None)
    ]


def _chat(tmp_path: Path, *authors: str) -> Path:
    export = tmp_path / "WhatsApp Chat - Family" / "_chat.txt"
    export.parent.mkdir(exist_ok=True)
    export.write_text(
        "".join(f"15/04/2024, 10:{i:02d} - {a}: hello\n" for i, a in enumerate(authors)),
        encoding="utf-8",
    )
    return export


def test_more_than_two_people_make_a_group(chattr_root: Path, tmp_path: Path) -> None:
    _convert(chattr_root, _chat(tmp_path, "Ana", "Rui", "Maria", "Ana"))
    assert _rows(chattr_root, "SELECT type FROM conversations") == [("group",)]


def test_two_people_stay_a_one_to_one_chat(chattr_root: Path, tmp_path: Path) -> None:
    _convert(chattr_root, _chat(tmp_path, "Ana", "Maria", "Ana"))
    assert _rows(chattr_root, "SELECT type FROM conversations") == [("individual",)]


def test_html_shows_no_none_for_media_without_text(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(chattr_root, _export(fixture_family, "WhatsApp Chat - Ana Costa"), html=True)
    assert result.html_path is not None
    assert ">None<" not in result.html_path.read_text(encoding="utf-8")
