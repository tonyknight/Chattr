"""Tapbacks find their targets in every GUID form, including attachments, and removals cancel."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings
from chattr.parsers import imessage_database_parser as parser_module
from chattr.parsers.imessage_database_parser import (
    iMessageDatabaseParser,
    message_parts,
    net_tapbacks,
    parse_associated_guid,
)


def test_every_guid_form_names_its_target_and_part() -> None:
    assert parse_associated_guid("p:0/ABC-123") == ("ABC-123", 0)
    assert parse_associated_guid("p:2/ABC-123") == ("ABC-123", 2)
    assert parse_associated_guid("bp:ABC-123") == ("ABC-123", 0)
    assert parse_associated_guid("ABC-123") == ("ABC-123", 0)
    assert parse_associated_guid("") is None and parse_associated_guid(None) is None


def test_parts_are_the_text_then_the_attachments() -> None:
    assert message_parts("hello", 2) == [None, 0, 1]
    assert message_parts(None, 2) == [0, 1]
    assert message_parts("￼", 1) == [0]  # an attachment-only message
    assert message_parts("look ￼ and ￼", 2) == [None, 0, None, 1]
    assert message_parts("hello", 0) == [None]


@pytest.fixture
def reader(fixture_family: FamilyExports):
    reader = iMessageDatabaseParser(db_path=str(fixture_family.paths["imessage"]))
    yield reader
    reader.close()


def _enriched(reader):
    return {
        m.text: m
        for m in reader.enrich_messages_with_tapbacks_parallel(reader.get_messages_with_fallback())
        if m.text
    }


def test_plain_and_legacy_forms_attach_to_their_messages(reader) -> None:
    messages = _enriched(reader)
    (liked,) = messages["Thank God. Call me when you're settled."].tapbacks
    assert (liked.type, liked.sender, liked.action) == ("Liked", family.ANA.phones[0], "Added")
    (laughed,) = messages["Christmas at my place this year?"].tapbacks
    assert laughed.type == "Laughed" and laughed.component_index == 0  # the legacy bp: form


def test_attachment_tapbacks_reach_the_message_with_their_part(reader) -> None:
    tapbacks = _enriched(reader)["Two of the best ones"].tapbacks
    assert [(t.type, t.action, t.component_index) for t in tapbacks] == [
        ("Loved", "Added", 1),
        ("Removed Love", "Removed", 1),
    ]


def test_a_removal_cancels_the_matching_tapback(reader) -> None:
    tapbacks = _enriched(reader)["Two of the best ones"].tapbacks
    assert net_tapbacks(tapbacks) == []
    liked = _enriched(reader)["Thank God. Call me when you're settled."].tapbacks
    assert net_tapbacks(liked) == liked


def test_single_message_lookup_agrees_with_the_bulk_load(reader) -> None:
    bulk = _enriched(reader)
    for text, message in bulk.items():
        assert reader.get_tapbacks_for_message(message.guid) == message.tapbacks, text


def test_bulk_loading_scales_past_sqlite_variable_limits(tmp_path: Path, fixture_family) -> None:
    db_path = tmp_path / "chat.db"
    shutil.copy(fixture_family.paths["imessage"], db_path)
    with sqlite3.connect(db_path) as db:
        db.executemany(
            "INSERT INTO message (guid, text, service, date, is_read) VALUES (?, 'x', 'iMessage', 1, 1)",
            [(f"BULK-{n}",) for n in range(40_000)],
        )
    big = iMessageDatabaseParser(db_path=str(db_path))
    try:
        messages = big.enrich_messages_with_tapbacks_parallel(big.get_all_messages_parallel())
    finally:
        big.close()
    (hello,) = [m for m in messages if m.text == "Thank God. Call me when you're settled."]
    assert len(messages) > 40_000 and len(hello.tapbacks or []) == 1


def _json(chattr_root: Path, db_path: Path) -> dict[str, dict]:
    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        spam_mode="disabled",
        workers=1,
        route="service",
    )
    assert result.json_path is not None
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    return {m["text"]: m for m in data if m["text"]}


def test_native_json_shows_net_tapbacks_on_messages_and_attachments(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    db_path = tmp_path / "chat.db"
    shutil.copy(fixture_family.paths["imessage"], db_path)
    with sqlite3.connect(db_path) as db:
        target = db.execute(
            "SELECT guid FROM message WHERE text = 'Two of the best ones'"
        ).fetchone()[0]
        db.execute(  # a laugh on the second attachment (part 2) that is never removed
            "INSERT INTO message (guid, text, service, handle_id, date, is_read, is_from_me, "
            "associated_message_type, associated_message_guid) "
            "VALUES ('TAP-ATT', NULL, 'iMessage', 0, 800000000000000000, 1, 1, 2003, ?)",
            (f"p:2/{target}",),
        )
    messages = _json(chattr_root, db_path)
    photos = messages["Two of the best ones"]
    assert photos["tapbacks"] == []  # the like on part 1 was removed
    assert [a.get("tapbacks") for a in photos["attachments"]] == [
        [],
        [{"type": "Laughed", "sender": "Me", "action": "Added"}],
    ]
    plan = messages["Christmas at my place this year?"]
    assert [t["type"] for t in plan["tapbacks"]] == ["Laughed"]
    assert parser_module.parse_associated_guid(f"p:2/{target}") == (target, 2)
