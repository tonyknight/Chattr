"""Unified schema v0 is created by ordered, idempotent migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chattr.schema import CURRENT_VERSION, apply_migrations

TABLES = {
    "conversations",
    "handles",
    "participants",
    "messages",
    "attachments",
    "reactions",
    "imports",
    "schema_version",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r[0] for r in rows}


def test_fresh_database_reaches_the_current_version(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    apply_migrations(conn)
    assert TABLES <= _tables(conn)
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == CURRENT_VERSION == 2


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    apply_migrations(conn)
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    apply_migrations(conn)
    after = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    assert before == after
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == CURRENT_VERSION


def test_message_columns_cover_the_data_model(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    apply_migrations(conn)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    for column in (
        "id",
        "conversation_id",
        "source_id",
        "timestamp_utc",
        "tz_offset_minutes",
        "time_precision",
        "tz_assumed",
        "sender_handle_id",
        "is_from_owner",
        "content_type",
        "body",
        "language",
        "translation",
        "transcript",
        "reply_to",
        "mail_headers",
        "service_specific",
        "import_id",
        "content_hash",
    ):
        assert column in columns, column


def test_legacy_view_keeps_the_old_column_names(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    apply_migrations(conn)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(messages_view)")}
    assert {"timestamp", "message_author", "message_sender", "body", "type", "service"} <= columns


def test_the_view_shows_a_voicemails_transcript_as_its_text(tmp_path: Path) -> None:
    from chattr.schema import MIGRATION_1

    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.executescript(MIGRATION_1)  # a database from before version 2
    conn.execute("INSERT INTO schema_version VALUES (1, 'then')")
    conn.execute(
        "INSERT INTO conversations (id, service, source_id, type) VALUES ('c', 'Voicemail', 'x', 'individual')"
    )
    for message_id, body, transcript in (
        ("a", "typed text", None),
        ("b", None, "spoken words"),
        ("c", "both", "ignored"),
    ):
        conn.execute(
            "INSERT INTO messages (id, conversation_id, timestamp_utc, content_type, body, transcript, content_hash) "
            "VALUES (?, 'c', '2024-01-01T00:00:00+00:00', 'voicemail', ?, ?, 'h')",
            (message_id, body, transcript),
        )
    assert apply_migrations(conn) == CURRENT_VERSION  # upgrades in place
    rows = dict(conn.execute("SELECT id, body FROM messages_view").fetchall())
    assert rows == {"a": "typed text", "b": "spoken words", "c": "both"}
    assert conn.execute("SELECT body FROM messages WHERE id = 'b'").fetchone()[0] is None
