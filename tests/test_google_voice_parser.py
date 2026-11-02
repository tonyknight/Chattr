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
