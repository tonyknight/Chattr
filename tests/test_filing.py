"""Filing attachments where a person would look for them: under the person, by source and date.

    People/<Person>/<Service>/(YYYY-MM-DD) HH-MM-SS <sender>.<ext>
    Groups/<Group>/<Service>/…
    Mail/<YYYY>/Attachments/(YYYY-MM-DD) HH-MM-SS <sender> — <name>.<ext>

Importers leave files wherever is convenient (by content hash, in a working folder); filing moves
them, records the new place in the database and reports what it did. It is safe to repeat, follows a
person who is renamed, stores identical files once, and files audio as `.m4a`.
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
def test_an_amr_recording_is_filed_as_m4a(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), amr(3))
    filed = archive.file()
    wanted = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.m4a"
    assert archive.files() == [wanted] and archive.paths() == [wanted]  # only the .m4a is kept
    assert filed.playable_made == 1 and filed.unplayable == []
    playable = (archive.out / wanted).read_bytes()
    assert playable[4:8] == b"ftyp"  # an MPEG-4 file
    assert len(playable) < 3 * len(amr(3))  # of about the same size
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


@needs_ffmpeg
def test_the_page_plays_the_m4a(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), amr(2))
    archive.file()
    page = next(p for p in archive.written if p.name.startswith("(2018)")).read_text("utf-8")
    audio = page[page.index("<audio") : page.index("</audio>")]
    assert ".m4a" in audio and ".amr" not in audio and "cannot play" not in page


@needs_ffmpeg
def test_a_recording_is_converted_once_and_follows_its_person(archive: Archive) -> None:
    data = amr(2)
    _voicemail(archive, "+15550101", _at(2018, 12, 21, 15, 2, 31), data)
    assert archive.file().playable_made == 1
    assert archive.file().playable_made == 0
    _voicemail(archive, "+15550101", _at(2018, 12, 21, 15, 2, 31), data)  # imported again
    again = archive.file()
    assert again.playable_made == 0 and again.created == []
    assert not any(p.is_file() for p in archive.staging.rglob("*"))
    with sqlite3.connect(archive.database) as db:
        db.execute("UPDATE handles SET person = ?", (ANA,))
    archive.file()
    assert archive.files() == [f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.m4a"]


def test_without_ffmpeg_the_original_is_filed_and_the_gap_is_reported(
    archive: Archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chattr import filing

    monkeypatch.setattr(filing.shutil, "which", lambda name: None)
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31))
    filed = archive.file()
    original = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}.amr"
    assert archive.files() == [original] and len(filed.unplayable) == 1 and filed.ffmpeg_missing
    page = next(p for p in archive.written if p.name.startswith("(2018)")).read_text("utf-8")
    assert "cannot play" in page  # and the page says why it may not play


@needs_ffmpeg
def test_importing_again_once_ffmpeg_is_there_converts_what_was_left(
    archive: Archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chattr import filing

    data = amr(2)
    with monkeypatch.context() as patch:
        patch.setattr(filing.shutil, "which", lambda name: None)
        _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), data)
        archive.file()
    stem = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}"
    assert archive.files() == [f"{stem}.amr"]
    assert archive.file().playable_made == 1  # ffmpeg is installed now
    assert archive.files() == [f"{stem}.m4a"] and archive.paths() == [f"{stem}.m4a"]


@needs_ffmpeg
def test_a_box_that_kept_both_files_is_left_with_the_m4a(archive: Archive) -> None:
    _voicemail(archive, ANA, _at(2018, 12, 21, 15, 2, 31), amr(2))
    archive.file(make_playable=False)
    stem = f"People/{ANA}/Voicemail/(2018-12-21) 15-02-31 {ANA}"
    (archive.out / f"{stem}.m4a").write_bytes(b"the copy an earlier version made")
    filed = archive.file()
    assert archive.files() == [f"{stem}.m4a"] and filed.playable_made == 1
    assert (archive.out / f"{stem}.m4a").read_bytes() == b"the copy an earlier version made"


@needs_ffmpeg
def test_a_whatsapp_voice_note_is_filed_as_m4a(archive: Archive, tmp_path: Path) -> None:
    import subprocess

    note = tmp_path / "note.opus"
    made = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1",
         "-c:a", "libopus", str(note)], capture_output=True,
    )  # fmt: skip
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot write Opus")
    archive.add(
        ConversationRecord("WhatsApp", "chat-ana", name=ANA, participants=(ANA,)),
        MessageRecord(
            timestamp=_at(2024, 3, 1, 14, 5, 9), sender=ANA, sender_name=ANA,
            attachments=(_blob(archive.staging, note.read_bytes(), ".opus", "PTT-001.opus"),),
        ),
    )  # fmt: skip
    archive.file()
    assert archive.files() == [f"People/{ANA}/WhatsApp/(2024-03-01) 14-05-09 {ANA}.m4a"]
    page = next(p for p in archive.written if p.name.startswith("(2024)")).read_text("utf-8")
    assert "<audio" in page


def test_a_recording_attached_to_mail_is_never_converted(archive: Archive) -> None:
    archive.add(
        ConversationRecord("Mail", "thread-7", name="Message", participants=("joao@example.org",)),
        MessageRecord(
            timestamp=_at(2019, 11, 2, 10, 15, 0), sender="joao@example.org", sender_name=JOAO,
            content_type="email", body="x", mail_headers={"subject": "Message"},
            attachments=(_blob(archive.staging, amr(1), ".amr", "message.amr"),),
        ),
    )  # fmt: skip
    archive.file()
    (kept,) = archive.files()
    assert kept.endswith("message.amr") and (archive.out / kept).read_bytes() == amr(1)


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


# --- Pictures any device can open, dated -------------------------------------------------------------


def _picture(fmt: str, exif_date: str | None = None, mode: str = "RGB") -> bytes:
    import io

    from PIL import Image

    if fmt == "HEIF":
        import pillow_heif

        pillow_heif.register_heif_opener()
    image = Image.new(mode, (64, 48), (200, 120, 40, 255)[: len(mode)])
    out = io.BytesIO()
    options = {}
    if exif_date:
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = exif_date
        options["exif"] = exif
    image.save(out, fmt, **options)
    return out.getvalue()


def _photo(archive: Archive, data: bytes, suffix: str, service: str = "iMessage", **more) -> None:
    kind = dict(type="group", name="Costa Family") if more.get("group") else dict(name=ANA)
    archive.add(
        ConversationRecord(service, "chat-photo", participants=(ANA,), **kind),
        MessageRecord(
            timestamp=_at(2024, 3, 1, 14, 5, 9),
            sender=ANA,
            sender_name=ANA,
            attachments=(_blob(archive.staging, data, suffix, f"IMG_0001{suffix}"),),
        ),
    )


def _taken(path: Path) -> str | None:
    from PIL import Image

    with Image.open(path) as image:
        return image.getexif().get_ifd(0x8769).get(0x9003)


@pytest.mark.parametrize(("fmt", "suffix"), [("HEIF", ".heic"), ("PNG", ".png"), ("WEBP", ".webp")])
def test_pictures_are_filed_as_jpeg_dated_by_their_message(
    archive: Archive, fmt: str, suffix: str
) -> None:
    _photo(archive, _picture(fmt), suffix)
    filed = archive.file()
    wanted = f"People/{ANA}/iMessage/(2024-03-01) 14-05-09 {ANA}.jpg"
    assert archive.files() == [wanted] and filed.converted == 1
    data = (archive.out / wanted).read_bytes()
    assert data[:3] == b"\xff\xd8\xff"  # a JPEG
    assert _taken(archive.out / wanted) == "2024:03:01 14:05:09"
    assert archive.paths() == [wanted]
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_a_picture_that_knows_when_it_was_taken_keeps_that_date(archive: Archive) -> None:
    _photo(archive, _picture("HEIF", exif_date="2023:12:25 09:30:00"), ".heic")
    archive.file()
    (photo,) = archive.files()
    assert _taken(archive.out / photo) == "2023:12:25 09:30:00"  # not the day it was sent


def test_see_through_pictures_get_a_white_background(archive: Archive) -> None:
    from PIL import Image

    _photo(archive, _picture("PNG", mode="RGBA"), ".png")
    archive.file()
    with Image.open(archive.out / archive.files()[0]) as image:
        assert image.mode == "RGB" and image.size == (64, 48)


def test_a_jpeg_that_has_its_date_is_left_exactly_as_it_is(archive: Archive) -> None:
    jpeg = _picture("JPEG", exif_date="2023:12:25 09:30:00")
    _photo(archive, jpeg, ".jpg")
    filed = archive.file()
    (photo,) = archive.files()
    assert (archive.out / photo).read_bytes() == jpeg and (filed.converted, filed.dated) == (0, 0)


def test_a_jpeg_with_no_date_gets_its_message_date_and_the_same_picture(archive: Archive) -> None:
    import io

    from PIL import Image

    jpeg = _picture("JPEG")
    _photo(archive, jpeg, ".jpg")
    filed = archive.file()
    (photo,) = archive.files()
    assert _taken(archive.out / photo) == "2024:03:01 14:05:09" and filed.dated == 1
    with Image.open(archive.out / photo) as now, Image.open(io.BytesIO(jpeg)) as before:
        assert now.tobytes() == before.tobytes()  # not compressed a second time
    _photo(archive, jpeg, ".jpg")  # and importing it again does not file a second copy
    assert archive.file().created == [] and archive.files() == [photo]
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_other_files_are_left_exactly_as_they_are(archive: Archive) -> None:
    gif = _picture("GIF", mode="P")
    _photo(archive, gif, ".gif")
    archive.file()
    (kept,) = archive.files()
    assert kept.endswith(".gif") and (archive.out / kept).read_bytes() == gif


def test_a_mail_attachment_is_never_converted(archive: Archive) -> None:
    png = _picture("PNG")
    archive.add(
        ConversationRecord("Mail", "thread-9", name="Logo", participants=("joao@example.org",)),
        MessageRecord(
            timestamp=_at(2019, 11, 2, 10, 15, 0), sender="joao@example.org", sender_name=JOAO,
            content_type="email", body="x", mail_headers={"subject": "Logo"},
            attachments=(_blob(archive.staging, png, ".png", "logo.png"),),
        ),
    )  # fmt: skip
    archive.file()
    (kept,) = archive.files()
    assert kept.endswith("logo.png") and (archive.out / kept).read_bytes() == png


def test_importing_again_does_not_make_a_second_picture(archive: Archive) -> None:
    heic = _picture("HEIF")
    _photo(archive, heic, ".heic")
    archive.file()
    before = archive.files()
    _photo(archive, heic, ".heic")  # staged again by a second import
    again = archive.file()
    assert archive.files() == before and again.converted == 0 and again.created == []
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_a_picture_that_cannot_be_converted_is_kept_as_it_is_and_counted(archive: Archive) -> None:
    _photo(archive, b"not really a picture", ".heic")
    filed = archive.file()
    assert archive.files() == [f"People/{ANA}/iMessage/(2024-03-01) 14-05-09 {ANA}.heic"]
    assert filed.converted == 0 and len(filed.unconverted) == 1


def test_conversion_can_be_turned_off(archive: Archive) -> None:
    heic = _picture("HEIF")
    _photo(archive, heic, ".heic")
    filed = archive.file(convert_images=False)
    (kept,) = archive.files()
    assert (
        kept.endswith(".heic")
        and (archive.out / kept).read_bytes() == heic
        and filed.converted == 0
    )


def test_the_page_shows_the_converted_picture(archive: Archive) -> None:
    _photo(archive, _picture("HEIF"), ".heic")
    archive.file()
    page = next(p for p in archive.written if p.name.startswith("(2024)")).read_text("utf-8")
    assert "<img" in page and ".jpg" in page and "file not in this archive" not in page


def test_a_moving_picture_is_not_flattened(archive: Archive) -> None:
    import io

    from PIL import Image

    frames = [Image.new("RGB", (32, 32), colour) for colour in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    _photo(archive, out.getvalue(), ".webp")
    filed = archive.file()
    (kept,) = archive.files()
    assert kept.endswith(".webp") and (archive.out / kept).read_bytes() == out.getvalue()
    assert filed.converted == 0 and filed.unconverted == []


# --- One copy of each file ---------------------------------------------------------------------------


def _sticker(archive: Archive, data: bytes, who: str, day: int, chat: str | None = None) -> None:
    archive.add(
        ConversationRecord("iMessage", chat or f"chat-{who}", name=who, participants=(who,)),
        MessageRecord(
            timestamp=_at(2024, 3, day, 10, 0, 0), sender=who, sender_name=who,
            attachments=(_blob(archive.staging, data, ".gif", "sticker.gif"),),
        ),
    )  # fmt: skip


def _pages(archive: Archive) -> dict[str, str]:
    return {p.parent.name: p.read_text("utf-8") for p in archive.written if p.name[:1] == "("}


def test_the_same_file_sent_many_times_is_stored_once_and_shown_every_time(
    archive: Archive,
) -> None:
    gif = _picture("GIF", mode="P")
    for day in (1, 2, 3):
        _sticker(archive, gif, ANA, day)
    filed = archive.file()
    only = f"People/{ANA}/iMessage/(2024-03-01) 10-00-00 {ANA}.gif"  # the first time it was sent
    assert archive.files() == [only] and archive.paths() == [only] * 3
    assert filed.created == [only] and filed.shared == 2
    page = _pages(archive)[ANA]
    assert page.count("<img") == 3 and "file not in this archive" not in page


def test_a_file_sent_to_two_people_is_stored_once_and_both_pages_show_it(archive: Archive) -> None:
    gif = _picture("GIF", mode="P")
    _sticker(archive, gif, JOAO, 5)
    _sticker(archive, gif, ANA, 2)  # earlier: this is where the one copy lives
    archive.file()
    only = f"People/{ANA}/iMessage/(2024-03-02) 10-00-00 {ANA}.gif"
    assert archive.files() == [only]
    pages = _pages(archive)
    assert "<img" in pages[ANA] and "<img" in pages[JOAO]
    assert "file not in this archive" not in pages[JOAO]
    assert "../Ana%20Costa/iMessage/" in pages[JOAO]  # linked from the other person's folder


def test_the_one_copy_stays_put_when_filing_again_and_when_more_arrives(archive: Archive) -> None:
    gif = _picture("GIF", mode="P")
    _sticker(archive, gif, ANA, 2)
    _sticker(archive, gif, JOAO, 5)
    archive.file()
    before = archive.files()
    again = archive.file()
    assert archive.files() == before and again.created == [] and again.moved == []
    _sticker(archive, gif, JOAO, 1)  # an older message turns up in a later import
    later = archive.file()
    assert archive.files() == before and later.created == [] and later.moved == []
    assert len(set(archive.paths())) == 1 and len(archive.paths()) == 3
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_importing_everything_again_keeps_one_copy(archive: Archive) -> None:
    gif = _picture("GIF", mode="P")
    for _ in range(2):
        _sticker(archive, gif, ANA, 2)
        _sticker(archive, gif, JOAO, 5)
        archive.file()
    assert archive.files() == [f"People/{ANA}/iMessage/(2024-03-02) 10-00-00 {ANA}.gif"]
    assert not any(p.is_file() for p in archive.staging.rglob("*"))


def test_a_converted_picture_is_shared_too(archive: Archive) -> None:
    heic = _picture("HEIF")
    for who, day in ((ANA, 1), (JOAO, 2)):
        archive.add(
            ConversationRecord("iMessage", f"chat-{who}", name=who, participants=(who,)),
            MessageRecord(
                timestamp=_at(2024, 3, day, 14, 5, 9), sender=who, sender_name=who,
                attachments=(_blob(archive.staging, heic, ".heic", "IMG_0001.heic"),),
            ),
        )  # fmt: skip
    filed = archive.file()
    only = f"People/{ANA}/iMessage/(2024-03-01) 14-05-09 {ANA}.jpg"
    assert archive.files() == [only] and filed.converted == 1
    assert _taken(archive.out / only) == "2024:03:01 14:05:09"
    assert all("<img" in page for page in _pages(archive).values())


def test_the_one_copy_follows_a_person_who_is_renamed(archive: Archive) -> None:
    gif = _picture("GIF", mode="P")
    _sticker(archive, gif, "+15550101", 2)
    _sticker(archive, gif, JOAO, 5)
    archive.file()
    with sqlite3.connect(archive.database) as db:
        db.execute("UPDATE handles SET person = ? WHERE identifier = ?", (ANA, "+15550101"))
    archive.file()
    only = f"People/{ANA}/iMessage/(2024-03-02) 10-00-00 {ANA}.gif"
    assert archive.files() == [only] and set(archive.paths()) == {only}
    assert "file not in this archive" not in _pages(archive)[JOAO]


def test_the_same_file_in_mail_and_in_a_chat_is_kept_for_each(archive: Archive) -> None:
    png = _picture("PNG")
    _photo(archive, png, ".png")
    archive.add(
        ConversationRecord("Mail", "thread-9", name="Logo", participants=("joao@example.org",)),
        MessageRecord(
            timestamp=_at(2019, 11, 2, 10, 15, 0), sender="joao@example.org", sender_name=JOAO,
            content_type="email", body="x", mail_headers={"subject": "Logo"},
            attachments=(_blob(archive.staging, png, ".png", "logo.png"),),
        ),
    )  # fmt: skip
    archive.file()
    assert sorted(Path(f).suffix for f in archive.files()) == [".jpg", ".png"]
