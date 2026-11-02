"""Chattr's one database manager for every service, on the unified schema (see `schema.py`).

Imports are idempotent: each record has a stable id (see `records.py`), so re-importing an
export, or a newer export that overlaps an older one, inserts only what is new. A message whose
content changed (same id, different `content_hash`) is updated and its `version` incremented.

Use as a context manager:

    with DatabaseManager(path) as db:
        import_id = db.start_import("WhatsApp", export_path)
        counts = db.upsert(conversation, records, import_id)
        db.finish_import(import_id, counts)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from chattr.records import (
    ConversationRecord,
    MessageRecord,
    assign_ids,
    records_from_parser,
)
from chattr.schema import apply_migrations


@dataclass
class ImportCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _utc_fields(moment: datetime) -> tuple[str, Optional[int]]:
    """ISO UTC time and the local offset in minutes; naive times are stored as given."""
    offset = moment.utcoffset() if moment.tzinfo is not None else None
    if offset is None:
        return moment.isoformat(), None
    return moment.astimezone(timezone.utc).isoformat(), int(offset.total_seconds() // 60)


def _sha256_of(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> Optional[str]:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if value else None


class DatabaseManager:
    """Handles all interactions with Chattr's SQLite database."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self.conn: Optional[sqlite3.Connection] = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("DatabaseManager must be used as a context manager (with ...:)")
        return self.conn

    def __enter__(self) -> "DatabaseManager":
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        apply_migrations(self.conn)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            self.conn.close()
            self.conn = None

    def create_tables(self) -> None:
        """Bring the schema up to date (kept for older callers)."""
        apply_migrations(self._db)

    # --- Import runs ------------------------------------------------------------------------

    def start_import(self, service: str, source: Union[str, Path, None] = None) -> int:
        """Record the start of an import; returns its id."""
        path = Path(source) if source else None
        cur = self._db.execute(
            "INSERT INTO imports (service, source_path, source_sha256, started_at) "
            "VALUES (?, ?, ?, ?)",
            (
                service,
                str(path) if path else None,
                _sha256_of(path) if path else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return int(cur.lastrowid or 0)

    def finish_import(
        self, import_id: int, counts: ImportCounts, source_cursor: Optional[str] = None
    ) -> None:
        self._db.execute(
            "UPDATE imports SET finished_at = ?, inserted = ?, updated = ?, unchanged = ?, "
            "source_cursor = ? WHERE id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                counts.inserted,
                counts.updated,
                counts.unchanged,
                source_cursor,
                import_id,
            ),
        )

    # --- Upserts ----------------------------------------------------------------------------

    def _handle_id(self, service: str, identifier: str, display_name: Optional[str]) -> int:
        normalized = identifier.strip().lower()
        self._db.execute(
            "INSERT INTO handles (service, identifier, normalized, display_name) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (service, normalized) DO UPDATE SET "
            "display_name = COALESCE(excluded.display_name, handles.display_name)",
            (service, identifier, normalized, display_name),
        )
        row = self._db.execute(
            "SELECT id FROM handles WHERE service = ? AND normalized = ?", (service, normalized)
        ).fetchone()
        return int(row[0])

    def _participant(self, conversation_id: str, handle_id: int) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO participants (conversation_id, handle_id) VALUES (?, ?)",
            (conversation_id, handle_id),
        )

    def upsert(
        self,
        conversation: ConversationRecord,
        records: list[MessageRecord],
        import_id: Optional[int] = None,
    ) -> ImportCounts:
        """Insert new messages, update changed ones, count unchanged ones."""
        db = self._db
        service = conversation.service
        db.execute(
            "INSERT INTO conversations (id, service, source_id, type, name, service_specific) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET type = excluded.type, "
            "name = COALESCE(excluded.name, conversations.name), "
            "service_specific = COALESCE(excluded.service_specific, conversations.service_specific)",
            (
                conversation.id,
                service,
                conversation.source_id,
                conversation.type,
                conversation.name,
                _json(conversation.service_specific),
            ),
        )
        for identifier in conversation.participants:
            self._participant(conversation.id, self._handle_id(service, identifier, None))

        counts = ImportCounts()
        for record in assign_ids(conversation, records):
            handle_id = None
            if record.sender:
                handle_id = self._handle_id(service, record.sender, record.sender_name)
                self._participant(conversation.id, handle_id)
            timestamp_utc, offset = _utc_fields(record.timestamp)
            values: dict[str, Any] = {
                "conversation_id": conversation.id,
                "source_id": record.source_id,
                "timestamp_utc": timestamp_utc,
                "tz_offset_minutes": offset,
                "time_precision": record.time_precision,
                "tz_assumed": int(record.tz_assumed or offset is None),
                "sender_handle_id": handle_id,
                "is_from_owner": int(record.is_from_owner),
                "content_type": record.content_type,
                "body": record.body,
                "language": record.language,
                "transcript": record.transcript,
                "reply_to": record.reply_to,
                "mail_headers": _json(record.mail_headers),
                "service_specific": _json(record.service_specific),
                "import_id": import_id,
                "content_hash": record.content_hash,
            }
            existing = db.execute(
                "SELECT content_hash FROM messages WHERE id = ?", (record.id,)
            ).fetchone()
            if existing is None:
                columns = ", ".join(["id", *values])
                marks = ", ".join("?" for _ in range(len(values) + 1))
                db.execute(
                    f"INSERT INTO messages ({columns}) VALUES ({marks})",
                    (record.id, *values.values()),
                )
                counts.inserted += 1
            elif existing["content_hash"] != record.content_hash:
                assignments = ", ".join(f"{key} = ?" for key in values)
                db.execute(
                    f"UPDATE messages SET {assignments}, version = version + 1 WHERE id = ?",
                    (*values.values(), record.id),
                )
                counts.updated += 1
            else:
                counts.unchanged += 1

            for attachment in record.attachments:
                db.execute(
                    "INSERT OR IGNORE INTO attachments (sha256, message_id, original_filename, "
                    "mime_type, size, storage_path) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attachment.sha256,
                        record.id,
                        attachment.original_filename,
                        attachment.mime_type,
                        attachment.size,
                        attachment.storage_path,
                    ),
                )
            for reaction in record.reactions:
                reactor = (
                    self._handle_id(service, reaction.sender, None) if reaction.sender else None
                )
                when = _utc_fields(reaction.timestamp)[0] if reaction.timestamp else None
                db.execute(
                    "INSERT OR IGNORE INTO reactions (message_id, target_part, sender_handle_id, "
                    "type, emoji, timestamp_utc, action) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        reaction.target_part,
                        reactor,
                        reaction.type,
                        reaction.emoji,
                        when,
                        reaction.action,
                    ),
                )
        return counts

    def insert_messages(
        self,
        messages: list[dict[str, Any]],
        service_name: str,
        author_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        conversation: Optional[str] = None,
    ) -> ImportCounts:
        """Upsert the dicts today's WhatsApp/Messenger parsers return (compatibility wrapper)."""
        record = ConversationRecord(
            service=service_name,
            source_id=conversation or author_name or f"{service_name} import",
            name=author_name or conversation,
        )
        return self.upsert(record, records_from_parser(messages, owner=owner_name))

    # --- Reading ----------------------------------------------------------------------------

    def get_all_messages(self) -> list[sqlite3.Row]:
        """Every message, oldest first, with the legacy column names (see `messages_view`)."""
        return self._db.execute("SELECT * FROM messages_view ORDER BY timestamp").fetchall()

    def get_all_messages_by_author(self, author: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM messages_view WHERE message_author = ? ORDER BY timestamp", (author,)
        ).fetchall()

    def get_all_authors(self) -> list[sqlite3.Row]:
        return self._db.execute("SELECT DISTINCT message_author FROM messages_view").fetchall()
