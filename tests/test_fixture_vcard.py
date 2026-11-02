"""The synthetic family's vCard: complete, fictional, deterministic."""

from __future__ import annotations

import re
from pathlib import Path

from fixtures import family
from fixtures.builders.vcard import build_vcard

FICTIONAL_PHONE = re.compile(r"^\+1555010\d|^\+1555019\d")


def test_every_known_handle_is_in_the_vcard(tmp_path: Path) -> None:
    path = build_vcard(tmp_path)
    text = path.read_text(encoding="utf-8")
    for person in family.CONTACTS:
        assert f"FN:{person.name}" in text
        for phone in person.phones:
            assert phone in text
        for email in person.emails:
            assert email in text


def test_handles_are_fictional() -> None:
    for person in family.PEOPLE:
        for phone in person.phones:
            assert FICTIONAL_PHONE.match(phone), phone
        for email in person.emails:
            assert email.endswith(("@example.com", "@example.org")), email
    assert FICTIONAL_PHONE.match(family.UNKNOWN_HANDLE)


def test_unknown_handle_is_not_a_contact(tmp_path: Path) -> None:
    text = build_vcard(tmp_path).read_text(encoding="utf-8")
    assert family.UNKNOWN_HANDLE not in text


def test_two_builds_are_identical(tmp_path: Path) -> None:
    first = build_vcard(tmp_path / "a").read_bytes()
    second = build_vcard(tmp_path / "b").read_bytes()
    assert first == second
