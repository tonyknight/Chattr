"""Google Voice Takeout: texts, calls and voicemails become one timeline per counterpart."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.parsers.google_voice_parser import GoogleVoiceParser, parse_duration


@pytest.fixture(scope="module")
def batches(fixture_family: FamilyExports):
    return GoogleVoiceParser().parse(fixture_family.paths["google_voice"])


def _all(batches):
    return sorted((r for b in batches for r in b.records), key=lambda r: r.timestamp)


def _by_type(batches, content_type: str):
    return [r for r in _all(batches) if r.content_type == content_type]


# --- The fixture ----------------------------------------------------------------------------


def test_every_page_item_becomes_a_record_of_the_right_type(batches) -> None:
    records = _all(batches)
    assert len(records) == 6
    assert [r.content_type for r in records] == [
        "text", "text", "call", "call", "call", "voicemail",
    ]  # fmt: skip


def test_texts_have_time_offset_sender_and_text(batches) -> None:
    first, second = _by_type(batches, "text")
    assert first.body == "Mãe, can you pick me up at 6?"
    assert first.timestamp == family.at(2018, 9, 3, 17, 40, 12)
    assert first.timestamp.utcoffset() == timedelta(hours=-5)
    assert first.sender == family.ANA.phones[0] and first.sender_name == family.ANA.name
    assert not first.is_from_owner and not first.tz_assumed
    assert second.is_from_owner and second.sender is None
    assert second.body == "Yes. The usual corner."


def test_calls_carry_direction_and_duration(batches) -> None:
    received, placed, missed = _by_type(batches, "call")
    assert received.service_specific == {"direction": "received", "duration_seconds": 312}
    assert placed.service_specific == {"direction": "placed", "duration_seconds": 95}
    assert missed.service_specific == {"direction": "missed", "duration_seconds": 0}
    assert received.sender == family.ANA.phones[0] and not received.is_from_owner
    assert placed.is_from_owner and placed.sender is None
    assert missed.sender == family.UNKNOWN_HANDLE and missed.sender_name is None
    assert received.body is None


def test_a_voicemail_has_its_transcript_and_duration(batches) -> None:
    (voicemail,) = _by_type(batches, "voicemail")
    assert (
        voicemail.transcript == "Hi Mãe, it's Ana. I'm running late, see you at dinner. Love you."
    )
    assert voicemail.service_specific["duration_seconds"] == 18
    assert voicemail.sender == family.ANA.phones[0]
    assert voicemail.timestamp == family.at(2018, 9, 8, 10, 22, 45)


def test_the_voicemail_audio_is_attached_whatever_its_extension(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    store = tmp_path / "Attachments"
    result = GoogleVoiceParser(attachments_dir=store).parse(fixture_family.paths["google_voice"])
    (voicemail,) = _by_type(result, "voicemail")
    (audio,) = voicemail.attachments
    source = next(fixture_family.paths["google_voice"].glob("*Voicemail*.mp3"))
    assert audio.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert audio.original_filename == source.name and audio.size == source.stat().st_size
    assert audio.storage_path == f"{audio.sha256[:2]}/{audio.sha256}.mp3"
    assert (store / audio.storage_path).read_bytes() == source.read_bytes()


def test_the_parents_of_the_calls_folder_work_too(fixture_family: FamilyExports) -> None:
    takeout = fixture_family.paths["google_voice"].parents[1]  # …/Takeout
    assert len(_all(GoogleVoiceParser().parse(takeout))) == 6
    assert len(_all(GoogleVoiceParser().parse(takeout / "Voice"))) == 6


# --- Durations and edge cases ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("PT5M12S", 312),
        ("PT95S", 95),
        ("PT1H2M3S", 3723),
        ("PT0S", 0),
        ("PT1M", 60),
        ("", None),
        ("bad", None),
    ],
)
def test_iso_durations(text: str, seconds: int | None) -> None:
    assert parse_duration(text) == seconds


def _page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def _text_message(when: str, tel: str, name: str, text: str, cls: str = "sender vcard") -> str:
    return (
        f'<div class="message"><abbr class="dt" title="{when}">x</abbr>: '
        f'<cite class="{cls}"><a class="tel" href="tel:{tel}"><span class="fn">{name}</span></a></cite>: '
        f"<q>{text}</q></div>"
    )


def test_other_html_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "Greeting.html").write_text(_page("<p>nothing here</p>"))
    (tmp_path / "Bio.txt").write_text("not html")
    assert GoogleVoiceParser().parse(tmp_path) == []


def test_text_escaping_and_line_breaks_are_decoded(tmp_path: Path) -> None:
    body = _text_message(
        "2020-01-02T03:04:05.000-05:00", "+15550101", "Ana", "Tom &amp; Jerry &lt;3<br/>second line"
    )
    (tmp_path / "Ana - Text - 2020-01-02T08_04_05Z.html").write_text(
        _page(f'<div class="hChatLog hfeed">{body}</div>')
    )
    (record,) = _all(GoogleVoiceParser().parse(tmp_path))
    assert record.body == "Tom & Jerry <3\nsecond line"


def test_a_group_conversation_records_each_sender(tmp_path: Path) -> None:
    messages = "".join(
        [
            _text_message("2020-01-02T03:04:05.000-05:00", "+15550101", "Ana", "hi all"),
            _text_message("2020-01-02T03:05:00.000-05:00", "+15550102", "Rui", "hello"),
            _text_message(
                "2020-01-02T03:06:00.000-05:00", "+15550000", "Me", "welcome", "sender vcard"
            ),
        ]
    )
    (tmp_path / "Group Conversation - 2020-01-02T08_04_05Z.html").write_text(
        _page(f'<div class="hChatLog hfeed">{messages}</div>')
    )
    (batch,) = GoogleVoiceParser().parse(tmp_path)
    assert [r.sender_name for r in batch.records] == ["Ana", "Rui", "Me"]
    assert batch.conversation.type == "group"
    assert set(batch.conversation.participants) == {"+15550101", "+15550102"}


def test_a_page_with_an_unknown_kind_is_classified_by_its_content(tmp_path: Path) -> None:
    call = (
        '<div class="haudio"><span class="fn">Placed call to Ana</span>'
        '<abbr class="published" title="2020-01-02T03:04:05.000+00:00">x</abbr>'
        '<cite class="contributor vcard"><a class="tel" href="tel:+15550101"><span class="fn">Ana</span></a></cite>'
        '<abbr class="duration" title="PT7S">(00:07)</abbr></div>'
    )
    (tmp_path / "Ana - Strange - 2020-01-02T03_04_05Z.html").write_text(_page(call))
    (record,) = _all(GoogleVoiceParser().parse(tmp_path))
    assert record.content_type == "call" and record.service_specific["direction"] == "placed"


def test_a_voicemail_without_audio_still_imports(tmp_path: Path) -> None:
    voicemail = (
        '<div class="haudio"><span class="fn">Voicemail from Ana</span>'
        '<abbr class="published" title="2020-01-02T03:04:05.000+00:00">x</abbr>'
        '<cite class="contributor vcard"><a class="tel" href="tel:+15550101"><span class="fn">Ana</span></a></cite>'
        '<abbr class="duration" title="PT7S">(00:07)</abbr>'
        '<span class="description"><span class="full-text">call me</span></span></div>'
    )
    (tmp_path / "Ana - Voicemail - 2020-01-02T03_04_05Z.html").write_text(_page(voicemail))
    (record,) = _all(GoogleVoiceParser(attachments_dir=tmp_path / "s").parse(tmp_path))
    assert record.content_type == "voicemail" and record.transcript == "call me"
    assert record.attachments == ()


def test_a_missing_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        GoogleVoiceParser().parse(tmp_path / "nothing")


# --- What a real Takeout does that the first fixture did not ----------------------------------------


def _mms(when: str, tel: str, name: str, inner: str, text: str = "MMS Received") -> str:
    return (
        f'<div class="message"><abbr class="dt" title="{when}">x</abbr>: '
        f'<cite class="sender vcard"><a class="tel" href="tel:{tel}"><abbr class="fn" title="">{name}'
        f"</abbr></a></cite>: <q>{text}</q><div>{inner}</div></div>"
    )


def test_a_picture_is_found_though_the_page_leaves_off_its_extension(tmp_path: Path) -> None:
    stem = "Stella Ellis - Text - 2019-09-15T23_14_04Z"
    (tmp_path / f"{stem}-27-1.jpg").write_bytes(b"a jpeg")
    picture = f'<img src="{stem}-27-1" alt="Image MMS Attachment" />'
    body = _mms("2019-09-15T16:14:04.000-07:00", "+15550107", "Stella Ellis", picture)
    (tmp_path / f"{stem}.html").write_text(_page(f'<div class="hChatLog hfeed">{body}</div>'))
    (record,) = _all(GoogleVoiceParser().parse(tmp_path))
    (photo,) = record.attachments
    assert photo.original_filename == f"{stem}-27-1.jpg" and photo.mime_type == "image/jpeg"
    assert photo.sha256 == hashlib.sha256(b"a jpeg").hexdigest()
    assert record.body is None  # "MMS Received" is Google's placeholder, not something said


def test_a_video_and_text_sent_with_a_picture_are_kept(tmp_path: Path) -> None:
    stem = "Ana - Text - 2020-01-02T08_04_05Z"
    (tmp_path / f"{stem}-1-1.mp4").write_bytes(b"a video")
    (tmp_path / f"{stem}-2-1.jpg").write_bytes(b"a jpeg")
    video = f'<a class="video" href="{stem}-1-1">Video MMS Attachment</a>'
    picture = f'<img src="{stem}-2-1" alt="Image MMS Attachment" />'
    body = _mms("2020-01-02T03:04:05.000-05:00", "+15550101", "Ana", video) + _mms(
        "2020-01-02T03:05:05.000-05:00", "+15550101", "Ana", picture, "Look at this"
    )
    (tmp_path / f"{stem}.html").write_text(_page(f'<div class="hChatLog hfeed">{body}</div>'))
    first, second = _all(GoogleVoiceParser().parse(tmp_path))
    assert [a.original_filename for a in first.attachments] == [f"{stem}-1-1.mp4"]
    assert second.body == "Look at this" and len(second.attachments) == 1


def test_a_day_when_only_i_wrote_joins_the_same_conversation(tmp_path: Path) -> None:
    theirs = _text_message(
        "2015-01-25T12:03:49.000-08:00", "+15550103", "Todd Suzanski", "I am here"
    )
    mine = _text_message("2015-01-26T13:27:02.000-08:00", "+15550000", "Me", "How did it go?")
    for day, body in (("2015-01-25T20_03_49Z", theirs), ("2015-01-26T21_27_02Z", mine)):
        (tmp_path / f"Todd Suzanski - Text - {day}.html").write_text(
            _page(f'<div class="hChatLog hfeed">{body}</div>')
        )
    (batch,) = GoogleVoiceParser(owner="Maria Costa").parse(tmp_path)
    assert batch.conversation.participants == ("+15550103",)
    assert [(r.sender_name, r.is_from_owner) for r in batch.records] == [
        ("Todd Suzanski", False),
        ("Maria Costa", True),
    ]


def test_a_page_named_for_a_number_where_only_i_wrote(tmp_path: Path) -> None:
    mine = _text_message("2015-01-26T13:27:02.000-08:00", "+15550000", "Me", "Hello?")
    (tmp_path / "+15550103 - Text - 2015-01-26T21_27_02Z.html").write_text(
        _page(f'<div class="hChatLog hfeed">{mine}</div>')
    )
    (batch,) = GoogleVoiceParser().parse(tmp_path)
    assert batch.conversation.participants == ("+15550103",)


def test_a_group_is_one_conversation_whoever_wrote_that_day(tmp_path: Path) -> None:
    def card(tel: str, name: str) -> str:
        return f'<cite class="sender vcard"><a class="tel" href="tel:{tel}"><span class="fn">{name}</span></a></cite>'

    members = (
        '<div class="participants">Group conversation with: '
        f'{card("+15550101", "Ana")}, {card("+15550102", "Rui")}</div>'
    )
    days = (
        (
            "2020-01-02T08_04_05Z",
            _text_message("2020-01-02T03:04:05.000-05:00", "+15550101", "Ana", "hi"),
        ),
        (
            "2020-01-03T08_04_05Z",
            _text_message("2020-01-03T03:04:05.000-05:00", "+15550102", "Rui", "yo"),
        ),
        (
            "2020-01-04T08_04_05Z",
            _text_message("2020-01-04T03:04:05.000-05:00", "+15550000", "Me", "ok"),
        ),
    )
    for day, body in days:
        (tmp_path / f"Group Conversation - {day}.html").write_text(
            _page(f'{members}<div class="hChatLog hfeed">{body}</div>')
        )
    (batch,) = GoogleVoiceParser().parse(tmp_path)
    assert batch.conversation.type == "group" and len(batch.records) == 3
    assert set(batch.conversation.participants) == {"+15550101", "+15550102"}


def test_a_voicemail_recording_with_no_page_is_still_imported(tmp_path: Path) -> None:
    (tmp_path / "Mary Afraimi - Voicemail - 2021-01-06T18_05_49Z.mp3").write_bytes(b"ID3 audio")
    (tmp_path / "Mary Afraimi - Received - 2021-01-05T10_00_00Z.mp3").write_bytes(b"not voicemail")
    (record,) = _all(GoogleVoiceParser().parse(tmp_path))
    assert record.content_type == "voicemail" and record.sender_name == "Mary Afraimi"
    assert record.timestamp == datetime(2021, 1, 6, 18, 5, 49, tzinfo=timezone.utc)
    assert [a.original_filename for a in record.attachments] == [
        "Mary Afraimi - Voicemail - 2021-01-06T18_05_49Z.mp3"
    ]


def test_a_recording_its_page_already_carries_is_not_imported_twice(
    fixture_family: FamilyExports,
) -> None:
    voicemails = _by_type(
        GoogleVoiceParser().parse(fixture_family.paths["google_voice"]), "voicemail"
    )
    assert len(voicemails) == len({r.timestamp for r in voicemails})


# --- Speech-to-text on the recordings ------------------------------------------------------------------


def _voicemail_page(tmp_path: Path, name: str, stamp: str, google_text: str | None) -> Path:
    stem = f"{name} - Voicemail - {stamp}"
    said = f'<span class="full-text">{google_text}</span>' if google_text is not None else ""
    (tmp_path / f"{stem}.html").write_text(
        _page(
            '<div class="haudio"><span class="fn">Voicemail from</span>'
            f'<abbr class="published" title="2021-01-06T10:05:49.000-08:00">x</abbr>'
            '<cite class="contributor vcard"><a class="tel" href="tel:+15550101">'
            f'<span class="fn">{name}</span></a></cite>{said}'
            f'<abbr class="duration" title="PT12S">(00:12)</abbr><audio controls src="{stem}.mp3">'
            "</audio></div>"
        )
    )
    recording = tmp_path / f"{stem}.mp3"
    recording.write_bytes(f"audio of {stem}".encode())
    return recording


def test_voicemail_recordings_are_transcribed_when_asked(tmp_path: Path) -> None:
    from chattr.transcription import StubTranscriber

    first = _voicemail_page(tmp_path, "Ana", "2021-01-06T18_05_49Z", "googles gess at it")
    second = _voicemail_page(tmp_path, "Ana", "2021-01-07T18_05_49Z", None)
    loose = tmp_path / "Rui - Voicemail - 2021-01-08T18_05_49Z.mp3"
    loose.write_bytes(b"audio with no page")
    stub = StubTranscriber({first.name: "Hi, call me back.", second.name: "Olá!", loose.name: " "})
    records = _all(GoogleVoiceParser(transcriber=stub).parse(tmp_path))
    one, two, three = records
    assert sorted(p.name for p in stub.calls) == sorted([first.name, second.name, loose.name])
    assert one.transcript == "Hi, call me back."  # the local model's, not Google's
    assert one.service_specific["google_transcript"] == "googles gess at it"
    assert one.service_specific["transcript_model"] == "stub"
    assert two.transcript == "Olá!" and "google_transcript" not in two.service_specific
    assert three.transcript is None  # nothing was said: no transcript, and no error either


def test_a_recording_that_cannot_be_transcribed_keeps_googles_words(tmp_path: Path) -> None:
    from chattr.transcription import Transcript

    _voicemail_page(tmp_path, "Ana", "2021-01-06T18_05_49Z", "what Google heard")

    class Broken:
        def transcribe(self, audio: Path) -> Transcript:
            raise RuntimeError("the model would not load")

    (record,) = _all(GoogleVoiceParser(transcriber=Broken()).parse(tmp_path))
    assert record.transcript == "what Google heard"
    assert record.service_specific["transcription_error"] == "the model would not load"


def test_without_a_transcriber_googles_words_are_the_transcript(tmp_path: Path) -> None:
    _voicemail_page(tmp_path, "Ana", "2021-01-06T18_05_49Z", "what Google heard")
    (record,) = _all(GoogleVoiceParser().parse(tmp_path))
    assert (
        record.transcript == "what Google heard"
        and "google_transcript" not in record.service_specific
    )
