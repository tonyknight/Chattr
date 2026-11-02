"""The synthetic MBOX (Gmail Takeout style) covers the email cases the MBOX parser needs."""

from __future__ import annotations

import mailbox
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

from fixtures import family
from fixtures.builders.mbox import build_mbox


@pytest.fixture(scope="module")
def messages(tmp_path_factory: pytest.TempPathFactory) -> list[Message]:
    path = build_mbox(tmp_path_factory.mktemp("mbox"))
    return list(mailbox.mbox(path))


def test_mbox_has_several_messages(messages: list[Message]) -> None:
    assert len(messages) >= 4


def test_some_headers_are_rfc2047_encoded(messages: list[Message]) -> None:
    raw_subjects = [str(m["Subject"]) for m in messages]
    raw_from = [str(m["From"]) for m in messages]
    assert any("=?utf-8?" in s.lower() for s in raw_subjects)
    assert any("=?utf-8?" in f.lower() for f in raw_from)


def test_an_html_only_message_exists(messages: list[Message]) -> None:
    def kinds(m: Message) -> set[str]:
        return {part.get_content_type() for part in m.walk() if not part.is_multipart()}

    assert any(kinds(m) == {"text/html"} for m in messages)


def test_a_multipart_alternative_message_exists(messages: list[Message]) -> None:
    assert any(m.get_content_type() == "multipart/alternative" for m in messages) or any(
        part.get_content_type() == "multipart/alternative" for m in messages for part in m.walk()
    )


def test_a_png_attachment_exists(messages: list[Message]) -> None:
    attachments = [
        part
        for m in messages
        for part in m.walk()
        if part.get_content_disposition() == "attachment"
    ]
    assert any(part.get_content_type() == "image/png" for part in attachments)


def test_a_thread_is_linked_by_headers(messages: list[Message]) -> None:
    by_id = {m["Message-ID"]: m for m in messages}
    replies = [m for m in messages if m["In-Reply-To"]]
    assert replies
    for reply in replies:
        assert reply["In-Reply-To"] in by_id
        assert reply["In-Reply-To"] in str(reply["References"])


def test_gmail_headers_are_present(messages: list[Message]) -> None:
    assert all(m["X-GM-THRID"] for m in messages)
    assert all(m["X-Gmail-Labels"] for m in messages)


def test_dates_use_different_offsets(messages: list[Message]) -> None:
    offsets = {parsedate_to_datetime(str(m["Date"])).utcoffset() for m in messages}
    assert len(offsets) >= 2


def test_two_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build_mbox(tmp_path / "a").read_bytes()
    second = build_mbox(tmp_path / "b").read_bytes()
    assert first == second


def test_correspondents_are_the_family(messages: list[Message]) -> None:
    addresses = " ".join(str(m["From"]) + str(m["To"]) for m in messages)
    assert family.JOAO.emails[0] in addresses
    assert family.OWNER.emails[0] in addresses
