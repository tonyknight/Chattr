"""A Messages database is read by the columns it has, not by a guess at its macOS version.

Schemas differ between macOS releases and even between two Macs on one release. The reader looks at
the live schema once and asks only for what is there; everything missing reads as empty.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports

from chattr.imessage_schema import (
    MESSAGE_COLUMNS,
    Capabilities,
    NotMessagesDatabase,
    message_select,
)
from chattr.parsers.imessage_database_parser import iMessageDatabaseParser

OLD = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT NOT NULL, service TEXT NOT NULL);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT,
                   style INTEGER, service_name TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, handle_id INTEGER,
                      service TEXT, date INTEGER, is_from_me INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, guid TEXT, filename TEXT, mime_type TEXT,
                         transfer_name TEXT, total_bytes INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
INSERT INTO handle VALUES (1, '+15550101', 'SMS');
INSERT INTO chat VALUES (1, 'SMS;-;+15550101', '+15550101', NULL, 45, 'SMS');
INSERT INTO chat_handle_join VALUES (1, 1);
INSERT INTO message VALUES (1, 'g-1', 'an old message', 1, 'SMS', 300000000, 0);
INSERT INTO message VALUES (2, 'g-2', 'my answer', 0, 'SMS', 300000060, 1);
INSERT INTO chat_message_join VALUES (1, 1), (1, 2);
"""


def _database(path: Path, script: str) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(script)
    return path


@pytest.fixture
def old(tmp_path: Path) -> Path:
    return _database(tmp_path / "chat.db", OLD)


def _capabilities(path: Path) -> Capabilities:
    with sqlite3.connect(path) as db:
        return Capabilities.determine(db)


def test_a_current_database_has_what_the_reader_can_use(fixture_family: FamilyExports) -> None:
    caps = _capabilities(fixture_family.paths["imessage"])
    assert {"guid", "attributedbody", "associated_message_guid", "date_edited"} <= caps.message
    assert caps.replies and caps.edits and caps.tapbacks
    assert not caps.recoverable_messages and not caps.filter_actions  # not in this fixture


def test_an_old_database_is_read_for_what_it_has(old: Path) -> None:
    caps = _capabilities(old)
    assert caps.message == {"rowid", "guid", "text", "handle_id", "service", "date", "is_from_me"}
    assert not (caps.replies or caps.edits or caps.tapbacks or caps.filter_actions)


def test_the_query_names_only_columns_that_exist_and_fills_the_rest(old: Path) -> None:
    caps = _capabilities(old)
    sql = message_select(caps)
    assert "m.text AS text" in sql and "NULL AS attributedbody" in sql
    assert "m.attributedbody" not in sql.lower().replace("null as attributedbody", "")
    with sqlite3.connect(old) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(sql + " ORDER BY m.date").fetchone()
    assert set(MESSAGE_COLUMNS) <= set(row.keys())  # every column is always there to read
    assert row["text"] == "an old message" and row["date_edited"] is None and row["chat_id"] == 1


def test_an_old_database_imports_without_a_fallback_or_a_complaint(
    old: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = iMessageDatabaseParser(str(old), enable_spam_filtering=False)
    try:
        messages = sorted(parser.get_messages_with_fallback(), key=lambda m: m.date)
        batched = sorted(parser.get_all_messages_parallel(max_workers=1), key=lambda m: m.date)
    finally:
        parser.close()
    assert [m.text for m in messages] == ["an old message", "my answer"]
    assert [m.guid for m in batched] == [m.guid for m in messages]
    assert messages[0].item_type == 0 and messages[0].associated_message_guid is None
    assert "schema failed" not in capsys.readouterr().out


def test_columns_the_reader_does_not_know_are_ignored(old: Path) -> None:
    with sqlite3.connect(old) as db:
        db.execute("ALTER TABLE message ADD COLUMN something_apple_added_next_year TEXT")
    parser = iMessageDatabaseParser(str(old), enable_spam_filtering=False)
    try:
        assert len(parser.get_messages_with_fallback()) == 2
    finally:
        parser.close()


def test_optional_tables_and_paired_columns_are_detected(old: Path) -> None:
    with sqlite3.connect(old) as db:
        db.executescript(
            "ALTER TABLE message ADD COLUMN filter_action INTEGER;"
            "CREATE TABLE chat_recoverable_message_join (chat_id INTEGER, message_id INTEGER);"
            "CREATE TABLE chat_lookup (identifier TEXT, domain TEXT, chat INTEGER);"
            "ALTER TABLE handle ADD COLUMN person_centric_id TEXT;"
        )
    caps = _capabilities(old)
    assert caps.recoverable_messages and caps.chat_lookup and caps.person_centric_ids
    assert not caps.filter_actions  # only one of the pair: treated as absent
    with sqlite3.connect(old) as db:
        db.execute("ALTER TABLE message ADD COLUMN filter_sub_action INTEGER")
    assert _capabilities(old).filter_actions


def test_a_database_that_is_not_messages_says_so(tmp_path: Path) -> None:
    other = _database(tmp_path / "other.db", "CREATE TABLE notes (id INTEGER, body TEXT);")
    with pytest.raises(NotMessagesDatabase, match="not a Messages database"):
        _capabilities(other)
    with pytest.raises(Exception, match="not a Messages database"):
        iMessageDatabaseParser(str(other), enable_spam_filtering=False)


def test_what_the_database_supports_can_be_listed(fixture_family: FamilyExports) -> None:
    described = _capabilities(fixture_family.paths["imessage"]).describe()
    assert described["Reply threads"] is True and described["Recently deleted messages"] is False
    assert set(described) == {
        "Reply threads", "Edited and unsent messages", "Tapbacks", "Recently deleted messages",
        "Message filter categories", "Person-centric handle IDs", "Conversation lookup table",
    }  # fmt: skip
