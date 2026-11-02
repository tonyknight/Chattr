"""WhatsApp exports: iOS, Android and US-locale formats, indentation, placeholders, attachments."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.parsers.whatsapp_parser import WhatsAppParser

LRM = "‎"


def _chat(fixture_family: FamilyExports, folder: str) -> Path:
    return fixture_family.paths["whatsapp"] / folder / "_chat.txt"


@pytest.fixture(scope="module")
def ios(fixture_family: FamilyExports):
    parser = WhatsAppParser()
    return parser, parser.parse(str(_chat(fixture_family, "WhatsApp Chat - Ana Costa")))


@pytest.fixture(scope="module")
def android(fixture_family: FamilyExports):
    parser = WhatsAppParser()
    return parser, parser.parse(str(_chat(fixture_family, "WhatsApp Chat with Rui Pereira")))


@pytest.fixture(scope="module")
def us(fixture_family: FamilyExports):
    parser = WhatsAppParser()
    return parser, parser.parse(str(_chat(fixture_family, "WhatsApp Chat with Ana Costa (US)")))


def _by_text(messages, text):
    (found,) = [m for m in messages if (m["message"] or "").startswith(text)]
    return found


# --- iOS ------------------------------------------------------------------------------------


def test_ios_messages_and_times(ios) -> None:
    parser, messages = ios
    assert len(messages) == 7
    assert parser.date_order == "iso" and parser.date_order_certain
    assert _by_text(messages, "Bom dia")["timestamp"] == datetime(2024, 3, 2, 8, 30, 12)
    assert _by_text(messages, "Bom dia")["precision"] == "second"
    assert _by_text(messages, "Que lindo")["author"] == family.OWNER.name


def test_continuation_lines_keep_their_indentation(ios) -> None:
    _, messages = ios
    assert _by_text(messages, "Shopping list")["message"] == (
        "Shopping list for Sunday:\n  - bacalhau\n  - azeite\n  - pastéis de nata"
    )


def test_omitted_media_stays_as_a_placeholder(ios) -> None:
    _, messages = ios
    omitted = _by_text(messages, "image omitted")
    assert omitted["omitted"] is True and omitted["type"] == "image"
    assert omitted["author"] == family.OWNER.name


def test_the_encryption_notice_is_a_system_message(ios) -> None:
    _, messages = ios
    notice = messages[0]
    assert notice["type"] == "system" and notice["author"] is None
    assert notice["message"].startswith("Messages and calls are end-to-end encrypted")


# --- Android --------------------------------------------------------------------------------


def test_android_day_first_dates_and_minute_precision(android) -> None:
    parser, messages = android
    assert parser.date_order == "day-first" and parser.date_order_certain
    reunion = _by_text(messages, "Olá Maria")
    assert reunion["timestamp"] == datetime(2024, 4, 15, 10, 1)
    assert reunion["precision"] == "minute" and reunion["author"] == family.RUI.name
    assert _by_text(messages, "See you")["timestamp"] == datetime(2024, 4, 16, 9, 15)


def test_android_indentation_system_notice_and_placeholder(android) -> None:
    _, messages = android
    assert _by_text(messages, "Everyone")["message"] == (
        "Everyone from the old street:\n  Teresa, Paulo and the twins"
    )
    assert messages[0]["type"] == "system" and messages[0]["author"] is None
    placeholder = _by_text(messages, "<Media omitted>")
    assert placeholder["omitted"] is True and placeholder["type"] == "media"


# --- US locale ------------------------------------------------------------------------------


def test_us_twelve_hour_clock_and_month_first(us) -> None:
    parser, messages = us
    assert parser.date_order == "month-first" and not parser.date_order_certain
    assert _by_text(messages, "Flight lands")["timestamp"] == datetime(2025, 2, 9, 9, 5)
    assert _by_text(messages, "Landed!")["timestamp"] == datetime(2025, 2, 9, 15, 52)
    assert _by_text(messages, "Landed!")["message"] == "Landed! Bags are slow:\n  carousel 7"


# --- Generated variants ---------------------------------------------------------------------


def _write(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "_chat.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_twelve_oclock_converts_correctly(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "1/2/25, 12:05 AM - Ana: just after midnight",
        "1/2/25, 12:05 PM - Ana: just after noon",
    )
    first, second = WhatsAppParser().parse(path)
    assert first["timestamp"].hour == 0 and second["timestamp"].hour == 12


def test_ambiguous_24_hour_files_assume_day_first_and_say_so(tmp_path: Path) -> None:
    parser = WhatsAppParser()
    (message,) = parser.parse(_write(tmp_path, "03/04/2024, 10:00 - Ana: hi"))
    assert message["timestamp"] == datetime(2024, 4, 3, 10, 0)
    assert parser.date_order == "day-first" and not parser.date_order_certain


def test_month_first_is_recognised_from_the_dates(tmp_path: Path) -> None:
    parser = WhatsAppParser()
    messages = parser.parse(_write(tmp_path, "4/13/24, 10:00 - Ana: a", "4/14/24, 11:00 - Ana: b"))
    assert parser.date_order == "month-first" and parser.date_order_certain
    assert [m["timestamp"].day for m in messages] == [13, 14]


def test_bracketed_locale_dates(tmp_path: Path) -> None:
    (message,) = WhatsAppParser().parse(_write(tmp_path, f"{LRM}[13/03/2024, 14:05:09] Ana: hello"))
    assert message["timestamp"] == datetime(2024, 3, 13, 14, 5, 9)
    assert message["author"] == "Ana" and message["message"] == "hello"


def test_narrow_no_break_space_before_am_pm(tmp_path: Path) -> None:
    (message,) = WhatsAppParser().parse(_write(tmp_path, "3/14/24, 2:07\u202fPM - Ana: us"))
    assert message["timestamp"] == datetime(2024, 3, 14, 14, 7)


def test_a_utf8_bom_and_blank_lines_are_harmless(tmp_path: Path) -> None:
    path = tmp_path / "_chat.txt"
    path.write_bytes(
        "﻿[2024-03-01, 10:00:00] Ana: first\n\n[2024-03-01, 10:01:00] Ana: second\n".encode()
    )
    messages = WhatsAppParser().parse(str(path))
    assert [m["message"] for m in messages] == ["first", "second"]


def test_a_missing_file_gives_no_messages(tmp_path: Path) -> None:
    assert WhatsAppParser().parse(str(tmp_path / "nothing.txt")) == []


def test_any_line_without_a_sender_is_a_system_notice(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '15/04/2024, 10:00 - Rui created group "Reunion"',
        "15/04/2024, 10:01 - Rui added Maria",
        "15/04/2024, 10:02 - Rui: hello",
    )
    created, added, hello = WhatsAppParser().parse(path)
    assert (created["type"], created["author"]) == ("system", None)
    assert (added["type"], added["author"]) == ("system", None)
    assert hello["type"] == "text"
