"""iPhone voicemail: voicemail.db rows, their audio kept, transcripts from a local transcriber."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.parsers.voicemail_parser import VoicemailParser
from chattr.transcription import StubTranscriber, Transcript


def _records(batches):
    return sorted((r for b in batches for r in b.records), key=lambda r: r.timestamp)


@pytest.fixture(scope="module")
def folder(fixture_family: FamilyExports) -> Path:
    return fixture_family.paths["iphone_voicemail"]


def test_each_row_is_a_voicemail_with_its_caller_time_and_duration(folder: Path) -> None:
    joao, ana = _records(VoicemailParser().parse(folder))
    assert {joao.content_type, ana.content_type} == {"voicemail"}
    assert joao.sender == family.JOAO.phones[0] and ana.sender == family.ANA.phones[0]
    assert joao.timestamp == family.at(2021, 12, 24, 19, 30, tz=family.TZ_LISBON)
    assert ana.timestamp == family.at(2022, 6, 14, 9, 5)
    assert joao.timestamp.utcoffset() == timedelta(
        0
    )  # an exact instant; the phone's zone is unknown
    assert (
        joao.service_specific["duration_seconds"],
        ana.service_specific["duration_seconds"],
    ) == (22, 14)
    assert not joao.is_from_owner and joao.body is None and joao.transcript is None


def test_one_conversation_per_caller(folder: Path) -> None:
    batches = VoicemailParser().parse(folder)
    assert sorted(b.conversation.source_id for b in batches) == sorted(
        [family.JOAO.phones[0], family.ANA.phones[0]]
    )
    assert {b.conversation.service for b in batches} == {"Voicemail"}
    assert {b.conversation.type for b in batches} == {"individual"}


def test_the_database_file_can_be_given_instead_of_the_folder(folder: Path) -> None:
    assert len(_records(VoicemailParser().parse(folder / "voicemail.db"))) == 2


def test_audio_is_kept_by_hash_whatever_its_extension(folder: Path, tmp_path: Path) -> None:
    store = tmp_path / "Attachments"
    records = _records(VoicemailParser(attachments_dir=store).parse(folder))
    for record in records:
        (audio,) = record.attachments
        source = next(folder.glob(audio.original_filename or "-"))
        assert audio.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
        assert audio.storage_path == f"{audio.sha256[:2]}/{audio.sha256}.amr"
        assert (store / audio.storage_path).read_bytes() == source.read_bytes()
        assert audio.size == source.stat().st_size


def test_transcripts_come_from_the_transcriber_and_say_which_model(folder: Path) -> None:
    stub = StubTranscriber({"1.amr": "Feliz Natal, mana!", "2.amr": "Call me back."}, language="en")
    joao, ana = _records(VoicemailParser(transcriber=stub).parse(folder))
    assert (joao.transcript, ana.transcript) == ("Feliz Natal, mana!", "Call me back.")
    assert joao.service_specific["transcript_model"] == "stub"
    assert [p.name for p in stub.calls] == ["1.amr", "2.amr"]


def test_a_failing_transcriber_does_not_stop_the_import(folder: Path) -> None:
    class Flaky:
        def transcribe(self, audio: Path) -> Transcript:
            if audio.name == "1.amr":
                raise RuntimeError("the engine crashed")
            return Transcript("fine", None, "flaky")

    joao, ana = _records(VoicemailParser(transcriber=Flaky()).parse(folder))
    assert (
        joao.transcript is None
        and joao.service_specific["transcription_error"] == "the engine crashed"
    )
    assert ana.transcript == "fine" and "transcription_error" not in ana.service_specific


def test_an_empty_transcript_is_not_stored(folder: Path) -> None:
    joao, _ = _records(VoicemailParser(transcriber=StubTranscriber("")).parse(folder))
    assert joao.transcript is None


def test_aliases_and_owner_do_not_make_callers_the_owner(folder: Path) -> None:
    parser = VoicemailParser(aliases={family.ANA.phones[0]: "Ana (daughter)"}, owner="Maria")
    joao, ana = _records(parser.parse(folder))
    assert (
        ana.sender_name == "Ana (daughter)" and not ana.is_from_owner and joao.sender_name is None
    )


def _copy(folder: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "Voicemail"
    shutil.copytree(folder, copy)
    return copy


def test_a_missing_audio_file_still_gives_the_voicemail(folder: Path, tmp_path: Path) -> None:
    copy = _copy(folder, tmp_path)
    (copy / "1.amr").unlink()
    joao, _ = _records(VoicemailParser(attachments_dir=tmp_path / "s").parse(copy))
    assert joao.attachments == ()


def test_trashed_voicemails_are_kept_and_flagged(folder: Path, tmp_path: Path) -> None:
    copy = _copy(folder, tmp_path)
    with sqlite3.connect(copy / "voicemail.db") as db:
        db.execute("UPDATE voicemail SET trashed_date = 1700000000 WHERE ROWID = 1")
    joao, ana = _records(VoicemailParser().parse(copy))
    assert joao.service_specific["trashed"] is True and "trashed" not in ana.service_specific


def test_a_caller_with_no_number_is_unknown(folder: Path, tmp_path: Path) -> None:
    copy = _copy(folder, tmp_path)
    with sqlite3.connect(copy / "voicemail.db") as db:
        db.execute("UPDATE voicemail SET sender = NULL, callback_num = '' WHERE ROWID = 1")
    batches = VoicemailParser().parse(copy)
    assert "unknown" in {b.conversation.source_id for b in batches}


def test_missing_or_broken_input_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        VoicemailParser().parse(tmp_path / "nothing")
    with pytest.raises(FileNotFoundError):
        VoicemailParser().parse(tmp_path)  # a folder without voicemail.db
    broken = tmp_path / "voicemail.db"
    broken.write_text("not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        VoicemailParser().parse(broken)
