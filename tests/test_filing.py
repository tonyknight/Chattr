"""Filing attachments where a person would look for them: under the person, by source and date.

    People/<Person>/<Service>/(YYYY-MM-DD) HH-MM-SS <sender>.<ext>
    Groups/<Group>/<Service>/…
    Mail/<YYYY>/Attachments/(YYYY-MM-DD) HH-MM-SS <sender> — <name>.<ext>

Importers leave files wherever is convenient (by content hash, in a working folder); filing moves
them, records the new place in the database and reports what it did. It is safe to repeat, follows a
person who is renamed, and makes a copy a browser can play of audio it cannot (`.amr`).
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chattr import blobs
from chattr.database_manager import DatabaseManager
from chattr.filing import file_attachments
from chattr.pages import render_pages
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord
from chattr.render import Renderer

ANA, JOAO = "Ana Costa", "João Costa"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def amr(seconds: float = 1.0) -> bytes:
    """A real (silent) AMR-NB recording: the header and 50 frames a second at 12.2 kbit/s."""
    return b"#!AMR\n" + (b"\x3c" + bytes(31)) * int(50 * seconds)


def _blob(staging: Path, data: bytes, suffix: str, name: str | None = None) -> AttachmentRecord:
    digest, relative = blobs.store_by_hash(staging, data, suffix)
    return AttachmentRecord(digest, name or f"original{suffix}", None, len(data), relative)


def _at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)  # type: ignore[arg-type]


class Archive:
    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "chattr.sqlite"
        self.staging = tmp_path / "incoming"
        self.out = tmp_path / "out"

    def add(self, conversation: ConversationRecord, *records: MessageRecord) -> None:
        with DatabaseManager(self.database) as db:
            db.upsert(conversation, list(records), db.start_import(conversation.service, "test"))
            db.commit()

    def file(self, **options):
        with DatabaseManager(self.database) as db:
            renderer = Renderer(db)
            filed = file_attachments(db, renderer, self.out, [self.staging], **options)
            self.written = render_pages(renderer, self.out, [self.staging])
        return filed

    def paths(self) -> list[str]:
        with sqlite3.connect(self.database) as db:
            return [row[0] for row in db.execute("SELECT storage_path FROM attachments ORDER BY 1")]

    def files(self, under: str = "") -> list[str]:
        root = self.out / under
        return sorted(
            str(p.relative_to(self.out))
            for p in root.rglob("*")
            if p.is_file() and p.suffix != ".html" and p.name != ".chattr-pages"
        )


@pytest.fixture
def archive(tmp_path: Path) -> Archive:
    return Archive(tmp_path)


def _voicemail(archive: Archive, caller: str, when: datetime, data: bytes | None = None) -> None:
    archive.add(
        ConversationRecord("Voicemail", caller, name=caller, participants=(caller,)),
        MessageRecord(
            timestamp=when,
            sender=caller,
            sender_name=caller,
            content_type="voicemail",
            attachments=(_blob(archive.staging, data or amr(), ".amr"),),
        ),
    )


# --- Where things go ------------------------------------------------------------------------------


def test_a_voicemail_goes_under_the_caller_by_source_and_date(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))
    filed = archive.file(make_playable=False)
    wanted = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.amr"
    assert archive.paths() == [wanted] and (archive.out / wanted).is_file()
    assert filed.created == [wanted] and filed.moved == []
    assert not any(p.is_file() for p in archive.staging.rglob("*"))  # moved, not copied


def test_several_files_on_one_message_are_numbered(archive: Archive) -> None:
    archive.add(
        ConversationRecord("WhatsApp", "chat-ana", name=ANA, participants=(ANA,)),
        MessageRecord(
            timestamp=_at(2024, 3, 1, 14, 5, 9),
            sender=ANA,
            sender_name=ANA,
            attachments=(
                _blob(archive.staging, b"one", ".PNG", "IMG-1.PNG"),
                _blob(archive.staging, b"two", ".jpg", "IMG-2.jpg"),
            ),
        ),
    )
    archive.file()
    assert archive.files() == [
        f"People/{ANA}/WhatsApp/(2024-03-01) 14-05-09 {ANA} 1.png",
        f"People/{ANA}/WhatsApp/(2024-03-01) 14-05-09 {ANA} 2.jpg",
    ]


def test_a_groups_files_go_under_the_group(archive: Archive) -> None:
    archive.add(
        ConversationRecord(
            "WhatsApp", "chat-family", type="group", name="Costa Family", participants=(ANA, JOAO)
        ),
        MessageRecord(
            timestamp=_at(2024, 3, 2, 9, 0, 0),
            sender=JOAO,
            sender_name=JOAO,
            attachments=(_blob(archive.staging, b"photo", ".jpg"),),
        ),
    )
    archive.file()
    assert archive.files() == [f"Groups/Costa Family/WhatsApp/(2024-03-02) 09-00-00 {JOAO}.jpg"]


def test_a_mail_attachment_keeps_its_own_name_after_the_date_and_sender(archive: Archive) -> None:
    archive.add(
        ConversationRecord("Mail", "thread-1", name="Fotos", participants=("joao@example.org",)),
        MessageRecord(
            timestamp=_at(2019, 11, 2, 10, 15, 0),
            sender="joao@example.org",
            sender_name=JOAO,
            content_type="email",
            body="Segue em anexo.",
            mail_headers={"subject": "Fotos"},
            attachments=(_blob(archive.staging, b"png", ".png", "familia.png"),),
        ),
    )
    archive.file()
    assert archive.files() == [f"Mail/2019/Attachments/(2019-11-02) 10-15-00 {JOAO} — familia.png"]


def test_two_different_files_that_want_one_name_both_survive(archive: Archive) -> None:
    for data in (amr(1), amr(2)):  # two voicemails from Ana in the same second
        _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), data)
    # (they are the same message twice only if their content is the same; here it differs)
    archive.file(make_playable=False)
    assert len(archive.files()) == len(set(archive.paths())) >= 1


def test_the_local_time_names_the_file_not_utc(archive: Archive) -> None:
    from datetime import timedelta

    lisbon_summer = timezone(timedelta(hours=1))
    _voicemail(archive, ANA, datetime(2022, 6, 14, 23, 30, 0, tzinfo=lisbon_summer))
    archive.file(make_playable=False)
    assert archive.files() == [f"People/{ANA}/Voicemail/(2022-06-14) 23-30-00 {ANA}.amr"]


# --- Safe to repeat, and it follows people --------------------------------------------------------


def test_filing_again_moves_nothing(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))
    archive.file(make_playable=False)
    before = archive.files()
    again = archive.file(make_playable=False)
    assert again.created == [] and again.moved == [] and archive.files() == before


def test_importing_the_same_export_again_leaves_one_copy(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))
    archive.file(make_playable=False)
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))  # the importer stages it again
    again = archive.file(make_playable=False)
    assert archive.files() == [f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.amr"]
    assert again.created == [] and again.duplicates == 1
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_a_person_who_is_named_takes_their_files_with_them(archive: Archive) -> None:
    _voicemail(archive, "+15550101", _at(2018, 12, 21, 15, 2, 31))
    archive.file(make_playable=False)
    assert archive.files() == ["People/+15550101/Voicemail/(2018-12-21) 15-02-31 +15550101.amr"]
    with sqlite3.connect(archive.database) as db:
        db.execute("UPDATE handles SET person = ?", (ANA,))
    moved = archive.file(make_playable=False)
    new = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.amr"
    assert archive.files() == [new]
    assert moved.moved == [("People/+15550101/Voicemail/(2018-12-21) 15-02-31 +15550101.amr", new)]
    assert not (archive.out / "People" / "+15550101").exists()  # the empty folder is tidied away


def test_a_file_outside_the_roots_is_left_alone(archive: Archive, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    archive.add(
        ConversationRecord("WhatsApp", "chat-ana", name=ANA, participants=(ANA,)),
        MessageRecord(
            timestamp=_at(2024, 3, 1, 14, 5, 9),
            sender=ANA,
            sender_name=ANA,
            attachments=(AttachmentRecord("0" * 64, "x.txt", None, 6, "../secret.txt"),),
        ),
    )
    archive.file()
    assert (tmp_path / "secret.txt").read_text() == "secret" and archive.files() == []


def test_a_file_that_is_missing_is_reported_as_missing_on_the_page(archive: Archive) -> None:
    archive.add(
        ConversationRecord("iMessage", "chat-ana", name=ANA, participants=(ANA,)),
        MessageRecord(
            timestamp=_at(2024, 3, 1, 14, 5, 9),
            sender=ANA,
            sender_name=ANA,
            attachments=(AttachmentRecord("1" * 64, "IMG_1.heic", "image/heic", 9, None),),
        ),
    )
    archive.file()
    page = next(p for p in archive.written if p.name.startswith("(2024)")).read_text("utf-8")
    assert "IMG_1.heic (file not in this archive)" in page


# --- Audio a browser can play ---------------------------------------------------------------------


@needs_ffmpeg
def test_an_amr_recording_gets_a_copy_a_browser_can_play(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), amr(3))
    filed = archive.file()
    stem = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}"
    assert archive.files() == [f"{stem}.amr", f"{stem}.m4a"]  # the original is kept beside it
    assert filed.playable_made == 1 and filed.unplayable == []
    assert archive.paths() == [f"{stem}.amr"]  # the database records the original
    playable = (archive.out / f"{stem}.m4a").read_bytes()
    assert playable[4:8] == b"ftyp"  # an MPEG-4 file
    assert len(playable) < 3 * len(amr(3))  # of about the same size


@needs_ffmpeg
def test_the_page_plays_the_copy_and_links_the_original(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), amr(2))
    archive.file()
    page = next(p for p in archive.written if p.name.startswith("(2018)")).read_text("utf-8")
    audio = page[page.index("<audio") : page.index("</audio>")]
    assert audio.index(".m4a") < audio.index(".amr")  # the playable one is offered first
    assert 'type="audio/mp4"' in audio
    assert "Original recording" in page


@needs_ffmpeg
def test_the_copy_is_made_once_and_moves_with_its_original(archive: Archive) -> None:
    _voicemail(archive, "+15550101", _at(2018, 12, 21, 15, 2, 31), amr(2))
    assert archive.file().playable_made == 1
    assert archive.file().playable_made == 0
    with sqlite3.connect(archive.database) as db:
        db.execute("UPDATE handles SET person = ?", (ANA,))
    archive.file()
    stem = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}"
    assert archive.files() == [f"{stem}.amr", f"{stem}.m4a"]


def test_without_ffmpeg_the_original_is_filed_and_the_gap_is_reported(
    archive: Archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chattr import filing

    monkeypatch.setattr(filing.shutil, "which", lambda name: None)
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))
    filed = archive.file()
    original = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.amr"
    assert archive.files() == [original] and filed.unplayable == [original]


def test_audio_a_browser_already_plays_is_not_copied(archive: Archive) -> None:
    archive.add(
        ConversationRecord("Google Voice", ANA, name=ANA, participants=(ANA,)),
        MessageRecord(
            timestamp=_at(2018, 9, 8, 10, 22, 45),
            sender=ANA,
            sender_name=ANA,
            content_type="voicemail",
            attachments=(_blob(archive.staging, b"ID3 mp3", ".mp3"),),
        ),
    )
    filed = archive.file()
    assert archive.files() == [f"People/{ANA}/Google Voice/(2018-09-08) 10-22-45 {ANA}.mp3"]
    assert filed.playable_made == 0 and filed.unplayable == []
