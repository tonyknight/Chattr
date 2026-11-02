"""One DatabaseManager for every service: imports, upserts, idempotent re-imports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.database_manager import DatabaseManager
from chattr.parsers.whatsapp_parser import WhatsAppParser
from chattr.records import ConversationRecord, MessageRecord, records_from_parser


@pytest.fixture
def whatsapp(fixture_family: FamilyExports) -> tuple[Path, list[dict]]:
    chat = fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    return chat, WhatsAppParser().parse(str(chat))


def _conversation() -> ConversationRecord:
    return ConversationRecord(service="WhatsApp", source_id="WhatsApp Chat - Ana Costa")


def test_reimporting_the_same_export_adds_nothing(tmp_path: Path, whatsapp) -> None:
    chat, parsed = whatsapp
    records = records_from_parser(parsed, owner=family.OWNER.name)
    with DatabaseManager(str(tmp_path / "chattr.sqlite")) as db:
        first_import = db.start_import("WhatsApp", chat)
        first = db.upsert(_conversation(), records, first_import)
        db.finish_import(first_import, first)
        second_import = db.start_import("WhatsApp", chat)
        second = db.upsert(_conversation(), records, second_import)
        db.finish_import(second_import, second)
        total = db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        imports = db.conn.execute(
            "SELECT inserted, unchanged, source_sha256 FROM imports"
        ).fetchall()
    assert first.inserted == len(records) > 0
    assert second.inserted == 0 and second.unchanged == len(records)
    assert total == len(records)
    assert imports[0]["source_sha256"] and imports[1]["inserted"] == 0


def test_overlapping_subset_adds_nothing(tmp_path: Path, whatsapp) -> None:
    _, parsed = whatsapp
    records = records_from_parser(parsed, owner=family.OWNER.name)
    with DatabaseManager(str(tmp_path / "chattr.sqlite")) as db:
        db.upsert(_conversation(), records)
        overlap = db.upsert(_conversation(), records[2:])
    assert overlap.inserted == 0


def test_changed_message_is_counted_as_an_update(tmp_path: Path) -> None:
    conv = ConversationRecord(service="iMessage", source_id="chat-1")
    when = datetime(2023, 6, 4, 18, 30, tzinfo=timezone.utc)
    original = MessageRecord(timestamp=when, sender="+15550101", body="draft", source_id="GUID-9")
    edited = MessageRecord(timestamp=when, sender="+15550101", body="final", source_id="GUID-9")
    with DatabaseManager(str(tmp_path / "chattr.sqlite")) as db:
        db.upsert(conv, [original])
        counts = db.upsert(conv, [edited])
        row = db.conn.execute("SELECT body, version FROM messages").fetchone()
    assert counts.updated == 1 and counts.inserted == 0
    assert row["body"] == "final" and row["version"] == 2


def test_times_keep_offset_or_mark_it_assumed(tmp_path: Path) -> None:
    conv = ConversationRecord(service="iMessage", source_id="chat-2")
    aware = MessageRecord(timestamp=family.at(2024, 1, 2, 9, 0), sender="+15550101", body="aware")
    naive = MessageRecord(
        timestamp=datetime(2024, 1, 2, 9, 5), sender="+15550101", body="naive", tz_assumed=True
    )
    with DatabaseManager(str(tmp_path / "chattr.sqlite")) as db:
        db.upsert(conv, [aware, naive])
        rows = {
            r["body"]: r
            for r in db.conn.execute(
                "SELECT body, timestamp_utc, tz_offset_minutes, tz_assumed FROM messages"
            )
        }
    assert rows["aware"]["timestamp_utc"] == "2024-01-02T14:00:00+00:00"
    assert rows["aware"]["tz_offset_minutes"] == -300 and rows["aware"]["tz_assumed"] == 0
    assert rows["naive"]["tz_offset_minutes"] is None and rows["naive"]["tz_assumed"] == 1


def test_legacy_view_serves_existing_readers(tmp_path: Path, whatsapp) -> None:
    _, parsed = whatsapp
    with DatabaseManager(str(tmp_path / "chattr.sqlite")) as db:
        db.insert_messages(parsed, "WhatsApp")
        rows = db.get_all_messages()
    assert rows and {"timestamp", "message_sender", "body", "type"} <= set(rows[0].keys())
    assert any(r["message_sender"] == family.ANA.name for r in rows)
