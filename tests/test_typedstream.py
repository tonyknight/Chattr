"""attributedBody: the text of an NSAttributedString is decoded, never guessed at or truncated."""

from __future__ import annotations

import sqlite3

import pytest
from fixtures import FamilyExports, family
from fixtures.builders.imessage import attributed_body

from chattr import typedstream
from chattr.parsers.imessage_database_parser import iMessageDatabaseParser


@pytest.mark.parametrize(
    "text",
    [
        "Hi",
        "x" * 127,  # one-byte length ends here
        "y" * 128,  # first two-byte length
        family.LONG_MESSAGE,  # over 255 bytes
        "Bom dia Mãe! Está tudo bem? 😀🇵🇹",
        "z" * 70_000,  # four-byte length
        "line one\nline two\ttabbed",
    ],
)
def test_text_round_trips(text: str) -> None:
    assert typedstream.attributed_text(attributed_body(text)) == text


def test_text_with_the_old_heuristics_trap_characters_is_complete() -> None:
    # A control character mid-message stopped the old pattern matcher there.
    text = "before\x0bafter and a long tail " + "q" * 300
    assert typedstream.attributed_text(attributed_body(text)) == text


@pytest.mark.parametrize("blob", [None, b"", b"not a typedstream", b"\x04\x0bstreamtyped"])
def test_anything_else_is_none(blob: bytes | None) -> None:
    assert typedstream.attributed_text(blob) is None


def test_a_truncated_stream_is_none_not_a_partial_message() -> None:
    blob = attributed_body("a perfectly good message " * 20)
    assert typedstream.attributed_text(blob[: len(blob) // 2]) is None


def test_the_reader_returns_the_whole_long_message(fixture_family: FamilyExports) -> None:
    with sqlite3.connect(fixture_family.paths["imessage"]) as db:
        blobs = [
            row[0]
            for row in db.execute(
                "SELECT attributedBody FROM message WHERE text IS NULL AND attributedBody IS NOT NULL"
            )
        ]
    (blob,) = [b for b in blobs if b"finally sorted" in b]
    reader = object.__new__(iMessageDatabaseParser)
    assert reader._extract_text_from_attributed_body(blob) == family.LONG_MESSAGE
