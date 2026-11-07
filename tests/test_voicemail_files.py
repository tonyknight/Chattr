"""Voicemail exported as audio files named `(2018-12-21) 15 02 31 - Ana Costa.amr`, with no database."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fixtures import family

from chattr import commands
from chattr.config import Settings
from chattr.parsers.voicemail_parser import VoicemailParser, parse_audio_name
from chattr.transcription import StubTranscriber

ANA, JOAO = family.ANA.name, family.JOAO.name
FIRST = f"(2018-12-21) 15 02 31 - {ANA}.amr"
SECOND = f"(2019-01-03) 09 05 00 - {JOAO}.amr"
THIRD = f"(2019-02-10) 18 30 12 - {ANA}.m4a"


def _folder(root: Path, *names: str) -> Path:
    folder = root / "iPhone Voicemail"
    folder.mkdir()
    for name in names:
        (folder / name).write_bytes(f"audio of {name}".encode())
    return folder


def _records(batches):
    return sorted((r for b in batches for r in b.records), key=lambda r: r.timestamp)


# --- The name carries the time and the caller -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "moment", "caller"),
    [
        (FIRST, datetime(2018, 12, 21, 15, 2, 31), ANA),
        ("(2018-12-21) 15.02.31 - Ana Costa.amr", datetime(2018, 12, 21, 15, 2, 31), ANA),
        ("(2018-12-21) 15-02-31 - Ana Costa.amr", datetime(2018, 12, 21, 15, 2, 31), ANA),
        ("(2018-12-21) 15:02:31 - Ana Costa.amr", datetime(2018, 12, 21, 15, 2, 31), ANA),
        ("(2018-12-21) 15 02 - Ana Costa.amr", datetime(2018, 12, 21, 15, 2, 0), ANA),
        (
            "(2018-12-21) 15 02 31 - +1 555 0101.amr",
            datetime(2018, 12, 21, 15, 2, 31),
            "+1 555 0101",
        ),
        ("(2018-12-21) 15 02 31 - Ana - Work.amr", datetime(2018, 12, 21, 15, 2, 31), "Ana - Work"),
        ("(2018-12-21) 15 02 31.amr", datetime(2018, 12, 21, 15, 2, 31), None),
        ("(2018-12-21) 15 02 31 - .amr", datetime(2018, 12, 21, 15, 2, 31), None),
    ],
)
def test_a_name_gives_its_time_and_caller(name: str, moment: datetime, caller: str | None) -> None:
    assert parse_audio_name(name) == (moment, caller)


@pytest.mark.parametrize(
    "name",
    ["voicemail.db", "1.amr", "Ana Costa.amr", "(2018-13-45) 15 02 31 - Ana.amr", "notes.txt"],
)
def test_other_names_are_not_voicemail_names(name: str) -> None:
    assert parse_audio_name(name) is None


# --- The parser ----------------------------------------------------------------------------------


def test_each_file_is_a_voicemail_from_the_caller_at_the_time_in_its_name(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST, SECOND, THIRD)
    first, second, third = _records(VoicemailParser().parse(folder))
    assert {r.content_type for r in (first, second, third)} == {"voicemail"}
    assert [r.sender for r in (first, second, third)] == [ANA, JOAO, ANA]
    # the name gives a wall-clock time, not an instant: it is kept as written and marked as assumed
    assert first.timestamp == datetime(2018, 12, 21, 15, 2, 31) and first.timestamp.tzinfo is None
    assert first.tz_assumed
    assert not first.is_from_owner and first.body is None and first.transcript is None


def test_one_conversation_per_caller_named_after_them(tmp_path: Path) -> None:
    batches = VoicemailParser().parse(_folder(tmp_path, FIRST, SECOND, THIRD))
    assert sorted(b.conversation.source_id for b in batches) == sorted([ANA, JOAO])
    assert {b.conversation.name for b in batches} == {ANA, JOAO}
    assert {b.conversation.service for b in batches} == {"Voicemail"}
    assert {len(b.records) for b in batches} == {1, 2}


def test_a_file_without_a_caller_is_unknown(tmp_path: Path) -> None:
    folder = _folder(tmp_path, "(2018-12-21) 15 02 31.amr")
    (batch,) = VoicemailParser().parse(folder)
    assert batch.conversation.source_id == "unknown" and batch.records[0].sender is None


def test_audio_is_kept_by_hash_and_the_file_name_is_remembered(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST)
    store = tmp_path / "Attachments"
    (record,) = _records(VoicemailParser(attachments_dir=store).parse(folder))
    (audio,) = record.attachments
    assert audio.original_filename == FIRST and audio.storage_path
    assert (store / audio.storage_path).read_bytes() == f"audio of {FIRST}".encode()
    assert audio.storage_path.endswith(".amr")


def test_files_that_are_not_voicemail_are_left_out(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST, "notes.txt", "1.amr", ".DS_Store")
    assert len(_records(VoicemailParser().parse(folder))) == 1


def test_the_audio_may_be_in_folders_inside_the_given_one(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST)
    nested = folder / "2019"
    nested.mkdir()
    (nested / SECOND).write_bytes(b"x")
    assert len(_records(VoicemailParser().parse(folder))) == 2


def test_a_known_caller_name_is_mapped_by_aliases(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST)
    (record,) = _records(VoicemailParser(aliases={ANA: "Ana M. Costa"}).parse(folder))
    assert record.sender_name == "Ana M. Costa"


def test_transcripts_are_made_for_the_files_too(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST, SECOND)
    stub = StubTranscriber({FIRST: "Hi Mum, call me back.", SECOND: "Feliz Natal!"})
    first, second = _records(VoicemailParser(transcriber=stub).parse(folder))
    assert first.transcript == "Hi Mum, call me back." and second.transcript == "Feliz Natal!"


def test_a_folder_with_neither_a_database_nor_voicemail_files_is_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, "notes.txt")
    with pytest.raises(FileNotFoundError):
        VoicemailParser().parse(folder)


def test_a_database_still_wins_when_the_folder_has_both(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FIRST)
    with sqlite3.connect(folder / "voicemail.db") as db:
        db.execute(
            "CREATE TABLE voicemail (ROWID INTEGER, date INTEGER, sender TEXT, callback_num TEXT,"
            " duration INTEGER, trashed_date INTEGER)"
        )
        db.execute("INSERT INTO voicemail VALUES (1, 1640374200, NULL, '+15550102', 22, NULL)")
    (record,) = _records(VoicemailParser().parse(folder))
    assert record.sender == "+15550102"


# --- Through the importer -------------------------------------------------------------------------


def test_the_files_are_imported_and_land_with_their_audio(
    chattr_root: Path, tmp_path: Path
) -> None:
    folder = _folder(tmp_path, FIRST, SECOND)
    result = commands.convert(Settings.load(chattr_root), "voicemail", folder)
    assert result.service == "Voicemail" and result.counts.inserted == 2


def test_the_error_for_an_empty_folder_says_what_was_expected(
    chattr_root: Path, tmp_path: Path
) -> None:
    folder = _folder(tmp_path, "notes.txt")
    with pytest.raises(commands.CommandError, match=r"voicemail\.db.*\(2018-12-21\)"):
        commands.convert(Settings.load(chattr_root), "voicemail", folder)
