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
    assert version == CURRENT_VERSION == 1


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chattr.sqlite")
    apply_migrations(conn)
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    apply_migrations(conn)
    after = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    assert before == after
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


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
