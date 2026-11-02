"""MBOX mail: RFC 2047 headers, both bodies, the sender's own UTC offset, stable ids."""

from __future__ import annotations

import mailbox
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.parsers.mbox_parser import MboxParser


def _records(batches):
    return sorted((r for b in batches for r in b.records), key=lambda r: r.timestamp)


@pytest.fixture(scope="module")
def records(fixture_family: FamilyExports):
    return _records(MboxParser().parse(fixture_family.paths["mbox"]))


def _by_subject(records, subject: str):
    return [r for r in records if r.mail_headers["subject"] == subject]


def test_every_mail_becomes_one_email_record(records) -> None:
    assert len(records) == 4
    assert {r.content_type for r in records} == {"email"}


def test_rfc_2047_headers_are_decoded(records) -> None:
    first, *_ = _by_subject(records, "Fotos de São Martinho")
    assert first.sender == family.JOAO.emails[0]
    assert first.sender_name == family.JOAO.name  # an encoded From header
    assert first.mail_headers["to"] == [
        {"name": family.OWNER.name, "address": family.OWNER.emails[0]}
    ]


def test_the_senders_own_utc_offset_is_kept(records) -> None:
    first, second = records[0], records[1]
    assert first.timestamp == datetime(2019, 11, 2, 9, 30, tzinfo=timezone.utc)
    assert second.timestamp == family.at(2019, 11, 2, 9, 15)  # same instant, sent from -05:00
    assert second.timestamp.utcoffset() == timedelta(hours=-5)
    assert not first.tz_assumed and not second.tz_assumed


def test_plain_and_html_bodies_are_both_kept(records) -> None:
    first = _by_subject(records, "Fotos de São Martinho")[0]
    assert first.body == "Olá Maria,\n\nAs fotos da festa de São Martinho estão prontas."
    assert "<b>São Martinho</b>" in first.service_specific["body_html"]


def test_an_html_only_mail_gets_a_text_body_and_keeps_its_html(records) -> None:
    (wedding,) = _by_subject(records, "Wedding update")
    assert wedding.body == "Wedding update\n\nThe date is set: 14 June."
    assert "<h1>Wedding update</h1>" in wedding.service_specific["body_html"]


def test_a_plain_reply_has_no_html(records) -> None:
    reply = _by_subject(records, "Re: Fotos de São Martinho")[0]
    assert reply.body == "Que maravilha! Send them all, please."
    assert "body_html" not in reply.service_specific


def test_threading_headers_and_gmail_fields_are_kept(records) -> None:
    first = _by_subject(records, "Fotos de São Martinho")[0]
    second = _by_subject(records, "Re: Fotos de São Martinho")[0]
    assert first.source_id == "20191102093000.1@example.org"
    assert second.mail_headers["message_id"] == "20191102141500.2@example.com"
    assert second.mail_headers["in_reply_to"] == first.source_id
    assert second.mail_headers["references"] == [first.source_id]
    assert second.reply_to == first.source_id
    assert first.mail_headers["thread_id"] == "1790000000000000001"
    assert first.mail_headers["labels"] == ["Inbox", "Família"]


# --- Edge cases -----------------------------------------------------------------------------


def _mbox(tmp_path: Path, *messages: EmailMessage) -> Path:
    path = tmp_path / "mail.mbox"
    box = mailbox.mbox(path, create=True)
    for message in messages:
        box.add(message)
    box.flush()
    box.close()
    return path


def _mail(**headers: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = headers.pop("From", "Ana <ana@example.com>")
    message["To"] = headers.pop("To", "maria@example.com")
    message["Subject"] = headers.pop("Subject", "Hello")
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    message.set_content("body")
    return message


def test_a_message_without_a_message_id_gets_a_stable_one(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _mail(Date="Sat, 02 Nov 2019 09:30:00 +0000"))
    first = _records(MboxParser().parse(path))[0].source_id
    second = _records(MboxParser().parse(path))[0].source_id
    assert first and first == second and "@" not in first


def test_duplicate_message_ids_collapse_and_merge_labels(tmp_path: Path) -> None:
    a = _mail(
        Message_ID="<dup@example.com>",
        Date="Sat, 02 Nov 2019 09:30:00 +0000",
        X_Gmail_Labels="Inbox",
    )
    b = _mail(
        Message_ID="<dup@example.com>",
        Date="Sat, 02 Nov 2019 09:30:00 +0000",
        X_Gmail_Labels="Work,Inbox",
    )
    (record,) = _records(MboxParser().parse(_mbox(tmp_path, a, b)))
    assert record.mail_headers["labels"] == ["Inbox", "Work"]


def test_an_unparseable_date_falls_back_to_the_from_line_and_is_marked_assumed(
    tmp_path: Path,
) -> None:
    mail = _mail(Message_ID="<x@example.com>", Date="not a date")
    mail.set_unixfrom("From ana@example.com Sat Nov  2 09:30:00 2019")
    (record,) = _records(MboxParser().parse(_mbox(tmp_path, mail)))
    assert record.timestamp == datetime(2019, 11, 2, 9, 30, tzinfo=timezone.utc)
    assert record.tz_assumed


def test_a_date_without_a_zone_is_assumed(tmp_path: Path) -> None:
    mail = _mail(Message_ID="<y@example.com>", Date="Sat, 02 Nov 2019 09:30:00 -0000")
    (record,) = _records(MboxParser().parse(_mbox(tmp_path, mail)))
    assert record.tz_assumed


def test_cc_and_bcc_are_recorded(tmp_path: Path) -> None:
    mail = _mail(
        Message_ID="<z@example.com>",
        Date="Sat, 02 Nov 2019 09:30:00 +0000",
        Cc="Rui <rui@example.com>, joao@example.com",
        Bcc="hidden@example.com",
    )
    (record,) = _records(MboxParser().parse(_mbox(tmp_path, mail)))
    assert [c["address"] for c in record.mail_headers["cc"]] == [
        "rui@example.com",
        "joao@example.com",
    ]
    assert record.mail_headers["bcc"] == [{"name": "", "address": "hidden@example.com"}]


def test_an_empty_or_missing_mailbox_gives_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mbox"
    empty.write_bytes(b"")
    assert MboxParser().parse(empty) == []
    with pytest.raises(FileNotFoundError):
        MboxParser().parse(tmp_path / "missing.mbox")
