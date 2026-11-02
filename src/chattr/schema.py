"""Chattr's unified database schema, created and upgraded by ordered migrations.

Schema v0 (migration 1) holds every service's conversations in one model:
- conversations: one chat or email thread, with a stable id;
- handles: a phone number, email address or username on a service;
- participants: which handles take part in which conversation;
- messages: one entry (text, media, call, voicemail, email, reaction, system notice);
- attachments: files attached to messages, identified by their SHA-256;
- reactions: tapbacks/reactions on messages, including attachment targets;
- imports: one row per import run, with source and counts;
- schema_version: which migrations have been applied.

Times are stored as `timestamp_utc` (ISO 8601), `tz_offset_minutes` (NULL when unknown),
`time_precision` (second, minute, day, month, year) and `tz_assumed` (1 when the export gave no
time zone).

`messages_view` keeps the column names older Chattr code used (`timestamp`, `message_author`,
`message_sender`, `body`, `type`, `service`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

MIGRATION_1 = """
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    source_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('individual', 'group')),
    name TEXT,
    service_specific TEXT,
    UNIQUE (service, source_id)
);

CREATE TABLE handles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    identifier TEXT NOT NULL,
    normalized TEXT NOT NULL,
    display_name TEXT,
    person TEXT,
    UNIQUE (service, normalized)
);

CREATE TABLE participants (
    conversation_id TEXT NOT NULL REFERENCES conversations (id),
    handle_id INTEGER NOT NULL REFERENCES handles (id),
    role TEXT NOT NULL DEFAULT 'member',
    PRIMARY KEY (conversation_id, handle_id)
);

CREATE TABLE imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    source_cursor TEXT
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id),
    source_id TEXT,
    timestamp_utc TEXT NOT NULL,
    tz_offset_minutes INTEGER,
    time_precision TEXT NOT NULL DEFAULT 'second',
    tz_assumed INTEGER NOT NULL DEFAULT 0,
    sender_handle_id INTEGER REFERENCES handles (id),
    is_from_owner INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT 'text'
        CHECK (content_type IN ('text', 'media', 'system', 'reaction', 'call', 'voicemail', 'email')),
    body TEXT,
    language TEXT,
    translation TEXT,
    transcript TEXT,
    reply_to TEXT,
    edit_history TEXT,
    forwarded_from TEXT,
    delivery_status TEXT,
    mail_headers TEXT,
    service_specific TEXT,
    import_id INTEGER REFERENCES imports (id),
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX messages_by_conversation_time ON messages (conversation_id, timestamp_utc);
CREATE INDEX messages_by_time ON messages (timestamp_utc);

CREATE TABLE attachments (
    sha256 TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages (id),
    original_filename TEXT,
    archival_filename TEXT,
    mime_type TEXT,
    size INTEGER,
    storage_path TEXT,
    processing_info TEXT,
    PRIMARY KEY (sha256, message_id)
);

CREATE TABLE reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES messages (id),
    target_part INTEGER,
    sender_handle_id INTEGER REFERENCES handles (id),
    type TEXT NOT NULL,
    emoji TEXT,
    timestamp_utc TEXT,
    action TEXT NOT NULL DEFAULT 'added' CHECK (action IN ('added', 'removed')),
    UNIQUE (message_id, target_part, sender_handle_id, type, action, timestamp_utc)
);

CREATE VIEW messages_view AS
SELECT
    m.id AS id,
    m.timestamp_utc AS timestamp,
    COALESCE(c.name, c.source_id) AS message_author,
    COALESCE(h.display_name, h.identifier) AS message_sender,
    m.body AS body,
    m.content_type AS type,
    c.service AS service,
    m.is_from_owner AS is_from_owner,
    m.conversation_id AS conversation_id
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
LEFT JOIN handles h ON h.id = m.sender_handle_id;
"""

#: Ordered migrations: (version, SQL). Never edit a released migration; append a new one.
# Version 2: a voicemail has no typed text, so the legacy view shows its transcript as the body
# (the pages and exports built on the view then show what was said).
MIGRATION_2 = """
DROP VIEW messages_view;
CREATE VIEW messages_view AS
SELECT
    m.id AS id,
    m.timestamp_utc AS timestamp,
    COALESCE(c.name, c.source_id) AS message_author,
    COALESCE(h.display_name, h.identifier) AS message_sender,
    COALESCE(m.body, m.transcript) AS body,
    m.content_type AS type,
    c.service AS service,
    m.is_from_owner AS is_from_owner,
    m.conversation_id AS conversation_id
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
LEFT JOIN handles h ON h.id = m.sender_handle_id;
"""

# Version 3: once a handle is resolved to a person, the person's name is the sender everywhere the
# legacy view is used (pages and exports).
MIGRATION_3 = """
DROP VIEW messages_view;
CREATE VIEW messages_view AS
SELECT
    m.id AS id,
    m.timestamp_utc AS timestamp,
    COALESCE(c.name, c.source_id) AS message_author,
    COALESCE(h.person, h.display_name, h.identifier) AS message_sender,
    COALESCE(m.body, m.transcript) AS body,
    m.content_type AS type,
    c.service AS service,
    m.is_from_owner AS is_from_owner,
    m.conversation_id AS conversation_id
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
LEFT JOIN handles h ON h.id = m.sender_handle_id;
"""

MIGRATIONS: list[tuple[int, str]] = [(1, MIGRATION_1), (2, MIGRATION_2), (3, MIGRATION_3)]
CURRENT_VERSION = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    """The highest applied migration, or 0 for a new database."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the database; return the resulting version."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = current_version(conn)
    for version, sql in MIGRATIONS:
        if version <= applied:
            continue
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
        applied = version
    return applied
