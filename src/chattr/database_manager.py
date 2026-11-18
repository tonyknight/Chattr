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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from chattr.people import PeopleResolver, normalize_identifier
from chattr.records import (
    ConversationRecord,
    MessageRecord,
    assign_ids,
    records_from_parser,
)
from chattr.schema import apply_migrations


@dataclass
class UnresolvedHandle:
    """A handle no contact or alias could name: it needs a human to link it to a person."""

    service: str
    identifier: str
    display_name: Optional[str]
    messages: int
    first_seen: Optional[str]
    last_seen: Optional[str]


@dataclass
class PeopleResult:
    newly_resolved: int = 0  # handles that gained or changed their person this time
    owner_handles: int = 0  # handles that are the archive owner
    unresolved: list[UnresolvedHandle] = field(default_factory=list)


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


@dataclass
class GroupTidy:
    """What `DatabaseManager.tidy_groups` removed."""

    groups_removed: list[str] = field(default_factory=list)  # "<service>: <group name>"
    members_removed: int = 0  # places in groups of people who never wrote there
    handles_removed: int = 0
    unchecked: set[str] = field(default_factory=set)  # services whose owner is not known yet


@dataclass
class Removed:
    """What `DatabaseManager.remove_people` took out."""

    conversations: int = 0
    messages: int = 0
    handles: int = 0
    files: list[str] = field(default_factory=list)  # storage paths no message shows any more


def _looks_like_phone(identifier: Optional[str]) -> bool:
    return normalize_identifier(identifier, "US").startswith("+")


def unresolved_rows(conn: sqlite3.Connection, schema: str = "") -> list[dict[str, Any]]:
    """Handles no person is known for, the busiest first, as dicts (`service`, `identifier`,
    `display_name`, `messages`, `first_seen`, `last_seen`). `schema` names an attached database.

    Not listed: someone who never wrote anything and is only a member of groups (or only reacted
    in one), and someone known only by a phone number who wrote only in groups. They are listed
    once they have a conversation with the owner of their own.
    """
    p = f"{schema}." if schema else ""
    cursor = conn.execute(
        "SELECT h.service, h.identifier, h.display_name, COUNT(m.id) AS messages, "
        "MIN(m.timestamp_utc) AS first_seen, MAX(m.timestamp_utc) AS last_seen, "
        f"(EXISTS (SELECT 1 FROM {p}participants pa JOIN {p}conversations c ON c.id = "
        "pa.conversation_id WHERE pa.handle_id = h.id AND c.type != 'group') OR "
        f"EXISTS (SELECT 1 FROM {p}messages d JOIN {p}conversations c ON c.id = d.conversation_id "
        "WHERE d.sender_handle_id = h.id AND c.type != 'group')) AS direct "
        f"FROM {p}handles h LEFT JOIN {p}messages m ON m.sender_handle_id = h.id "
        "WHERE h.person IS NULL AND h.removed = 0 "
        "GROUP BY h.id ORDER BY messages DESC, h.service, h.identifier"
    )
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return [
        r for r in rows
        if r["direct"] or (r["messages"] and not _looks_like_phone(r["identifier"]))
    ]  # fmt: skip


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

    def commit(self) -> None:
        self._db.commit()

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

    @property
    def connection(self) -> sqlite3.Connection:
        """The open database, for readers (the renderer) that need their own queries."""
        return self._db

    # --- People -----------------------------------------------------------------------------

    def resolve_people(
        self, resolver: PeopleResolver, service: Optional[str] = None
    ) -> PeopleResult:
        """Link handles to people, derive the owner, and report the handles still unnamed.

        A handle's `person` is set from the aliases, then the contacts. A person already set is
        kept unless an alias or contact now names someone else. The owner's handles get the
        owner's name, their participant rows the role `owner`, and their messages `is_from_owner`.
        Safe to repeat.
        """
        db = self._db
        result = PeopleResult()
        query = "SELECT id, service, identifier, display_name, person, removed FROM handles"
        rows = db.execute(
            query + (" WHERE service = ?" if service else ""), (service,) if service else ()
        ).fetchall()
        for row in rows:
            person = resolver.person_for(row["identifier"], row["display_name"])
            if person and person != row["person"] and not row["removed"]:
                db.execute("UPDATE handles SET person = ? WHERE id = ?", (person, row["id"]))
                result.newly_resolved += 1
            if resolver.owner_name and resolver.is_owner(row["identifier"], row["display_name"]):
                result.owner_handles += 1
                db.execute(
                    "UPDATE participants SET role = 'owner' WHERE handle_id = ?", (row["id"],)
                )
                db.execute(
                    "UPDATE messages SET is_from_owner = 1 "
                    "WHERE sender_handle_id = ? AND is_from_owner = 0",
                    (row["id"],),
                )
        result.newly_resolved += self._share_names_within_groups()
        result.unresolved = self.unresolved_handles(service)
        db.commit()
        return result

    def group_handles(self, service: str, groups: Iterable[Iterable[str]]) -> None:
        """Record that each group of identifiers is one person (the source says so)."""
        for group in groups:
            ids = sorted({self._handle_id(service, identifier, None) for identifier in group})
            if len(ids) < 2:
                continue
            key = f"{service}:{ids[0]}"
            for handle_id in ids:
                self._db.execute(
                    "INSERT INTO handle_groups (handle_id, group_key) VALUES (?, ?) "
                    "ON CONFLICT (handle_id) DO UPDATE SET group_key = excluded.group_key",
                    (handle_id, key),
                )
        self._db.commit()

    def _share_names_within_groups(self) -> int:
        """In a group where exactly one name is known, give it to the members with none. Two
        different names in one group are left alone: someone chose them."""
        shared = 0
        rows = self._db.execute(
            "SELECT g.group_key, h.id, h.person FROM handle_groups g JOIN handles h ON h.id = g.handle_id"
        ).fetchall()
        groups: dict[str, list[tuple[int, Optional[str]]]] = {}
        for key, handle_id, person in rows:
            groups.setdefault(key, []).append((handle_id, person))
        for members in groups.values():
            names = {person for _, person in members if person}
            if len(names) != 1:
                continue
            (name,) = names
            for handle_id, person in members:
                if not person:
                    self._db.execute(
                        "UPDATE handles SET person = ? WHERE id = ?", (name, handle_id)
                    )
                    shared += 1
        return shared

    def mark_owner(self, service: str, identifier: str, owner_name: str) -> int:
        """Say that `identifier` on `service` is the owner (a different name they use there):
        its handle takes the owner's name, its places in conversations the role `owner`, and its
        messages are the owner's. Returns how many messages that made the owner's."""
        rows = self._db.execute(
            "SELECT id FROM handles WHERE service = ? AND normalized = ?",
            (service, identifier.strip().lower()),  # as `_handle_id` keys them
        ).fetchall()
        changed = 0
        for (handle_id,) in rows:
            self._db.execute("UPDATE handles SET person = ? WHERE id = ?", (owner_name, handle_id))
            self._db.execute(
                "UPDATE participants SET role = 'owner' WHERE handle_id = ?", (handle_id,)
            )
            changed += self._db.execute(
                "UPDATE messages SET is_from_owner = 1 WHERE sender_handle_id = ? "
                "AND is_from_owner = 0",
                (handle_id,),
            ).rowcount
        self._db.commit()
        return changed

    def name_handles(self, service: str, name_of: Callable[[Optional[str]], Optional[str]]) -> int:
        """Give a person to this service's unnamed handles that `name_of` knows (by identifier,
        then display name); returns how many were named. A handle already named is left alone."""
        named = 0
        rows = self._db.execute(
            "SELECT id, identifier, display_name FROM handles "
            "WHERE service = ? AND person IS NULL AND removed = 0",
            (service,),
        ).fetchall()
        for row in rows:
            person = name_of(row["identifier"]) or name_of(row["display_name"])
            if person:
                self._db.execute("UPDATE handles SET person = ? WHERE id = ?", (person, row["id"]))
                named += 1
        self._db.commit()
        return named

    def unresolved_handles(self, service: Optional[str] = None) -> list[UnresolvedHandle]:
        """Handles with no person yet, the busiest first (see `unresolved_rows`)."""
        return [
            UnresolvedHandle(
                r["service"], r["identifier"], r["display_name"], r["messages"],
                r["first_seen"], r["last_seen"],
            )
            for r in unresolved_rows(self._db)
            if service is None or r["service"] == service
        ]  # fmt: skip

    def remove_people(self, match: Callable[[str, str, Optional[str]], bool]) -> "Removed":
        """Take people out of the archive. `match(service, identifier, person)` says which
        handles; the owner's own are never taken, and a handle removed before stays removed
        (what was imported for it since goes too).

        Every conversation that is not a group and not mail, and is with nobody but them, is
        deleted, messages and all. The handles are marked removed: never named, never listed to
        be named. What they wrote in a group stays in the group. Returns what went, with the
        files (`storage_path`) that no remaining message shows. Safe to repeat.
        """
        db = self._db
        owners = {
            r[0] for r in db.execute("SELECT handle_id FROM participants WHERE role = 'owner'")
        } | {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT sender_handle_id FROM messages "
                "WHERE is_from_owner = 1 AND sender_handle_id IS NOT NULL"
            )
        }
        targets = {
            r["id"]
            for r in db.execute(
                "SELECT id, service, identifier, person, removed FROM handles"
            ).fetchall()
            if r["id"] not in owners
            and (r["removed"] or match(r["service"], r["identifier"], r["person"]))
        }
        result = Removed()
        if not targets:
            return result
        with_whom: dict[str, set[int]] = {}
        for conversation_id, handle_id in db.execute(
            "SELECT p.conversation_id, p.handle_id FROM participants p "
            "JOIN conversations c ON c.id = p.conversation_id "
            "WHERE c.type != 'group' AND c.service != 'Mail' AND p.role != 'owner' "
            "UNION SELECT m.conversation_id, m.sender_handle_id FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.type != 'group' AND c.service != 'Mail' AND m.is_from_owner = 0 "
            "AND m.sender_handle_id IS NOT NULL"
        ):
            with_whom.setdefault(conversation_id, set()).add(handle_id)
        paths: set[str] = set()
        for conversation_id, others in with_whom.items():
            if not (others - owners) or not (others - owners) <= targets:
                continue
            ids = "SELECT id FROM messages WHERE conversation_id = ?"
            paths |= {
                r[0]
                for r in db.execute(
                    f"SELECT storage_path FROM attachments WHERE message_id IN ({ids}) "
                    "AND storage_path IS NOT NULL",
                    (conversation_id,),
                )
            }
            db.execute(f"DELETE FROM reactions WHERE message_id IN ({ids})", (conversation_id,))
            db.execute(f"DELETE FROM attachments WHERE message_id IN ({ids})", (conversation_id,))
            result.messages += db.execute(
                "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).rowcount
            db.execute("DELETE FROM participants WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            result.conversations += 1
        for path in sorted(paths):
            still = db.execute(
                "SELECT 1 FROM attachments WHERE storage_path = ? LIMIT 1", (path,)
            ).fetchone()
            if not still:
                result.files.append(path)
        marks = ",".join("?" for _ in targets)
        result.handles = db.execute(
            f"UPDATE handles SET person = NULL, removed = 1 WHERE id IN ({marks}) "
            "AND (removed = 0 OR person IS NOT NULL)",
            sorted(targets),
        ).rowcount
        db.commit()
        return result

    def drop_conversation(self, service: str, source_id: str) -> list[str]:
        """Delete a conversation and everything in it (it is being imported differently now).
        Returns the files (`storage_path`) that no other message shows. Safe to repeat."""
        db = self._db
        found = db.execute(
            "SELECT id FROM conversations WHERE service = ? AND source_id = ?", (service, source_id)
        ).fetchall()
        paths: set[str] = set()
        for (conversation_id,) in found:
            ids = "SELECT id FROM messages WHERE conversation_id = ?"
            paths |= {
                r[0]
                for r in db.execute(
                    f"SELECT storage_path FROM attachments WHERE message_id IN ({ids}) "
                    "AND storage_path IS NOT NULL",
                    (conversation_id,),
                )
            }
            db.execute(f"DELETE FROM reactions WHERE message_id IN ({ids})", (conversation_id,))
            db.execute(f"DELETE FROM attachments WHERE message_id IN ({ids})", (conversation_id,))
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM participants WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        db.commit()
        return sorted(
            p
            for p in paths
            if not db.execute(
                "SELECT 1 FROM attachments WHERE storage_path = ? LIMIT 1", (p,)
            ).fetchone()
        )

    def tidy_groups(self) -> GroupTidy:
        """Apply the rules for group conversations, from any source:

        1. a group the owner never wrote in is removed, messages and all (groups someone added
           them to and they left, spam). Only for a service whose owner is known in this database
           (some message is marked as theirs): otherwise every group would go;
        2. a member who never wrote in a group is not part of it here: their place among its
           participants goes, a reaction of theirs keeps its emoji but not who sent it, and a
           handle left with nothing is removed;
        3. (in `unresolved_rows`) a member known only by a phone number, who wrote only in groups,
           is never listed to be named.

        Safe to repeat. Returns what was removed.
        """
        db = self._db
        result = GroupTidy()
        known = {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT c.service FROM messages m JOIN conversations c "
                "ON c.id = m.conversation_id WHERE m.is_from_owner = 1"
            )
        }
        silent = db.execute(
            "SELECT c.id, c.service, COALESCE(c.name, c.source_id) FROM conversations c "
            "WHERE c.type = 'group' AND NOT EXISTS (SELECT 1 FROM messages m "
            "WHERE m.conversation_id = c.id AND m.is_from_owner = 1)"
        ).fetchall()
        for conversation_id, service, name in silent:
            if service not in known:
                result.unchecked.add(service)
                continue
            ids = "SELECT id FROM messages WHERE conversation_id = ?"
            db.execute(f"DELETE FROM reactions WHERE message_id IN ({ids})", (conversation_id,))
            db.execute(f"DELETE FROM attachments WHERE message_id IN ({ids})", (conversation_id,))
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM participants WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            result.groups_removed.append(f"{service}: {name}")
        groups = "SELECT id FROM conversations WHERE type = 'group'"
        result.members_removed = db.execute(
            f"DELETE FROM participants WHERE conversation_id IN ({groups}) AND role != 'owner' "
            "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = "
            "participants.conversation_id AND m.sender_handle_id = participants.handle_id)"
        ).rowcount
        db.execute(
            "UPDATE reactions SET sender_handle_id = NULL WHERE sender_handle_id IS NOT NULL "
            f"AND message_id IN (SELECT id FROM messages WHERE conversation_id IN ({groups})) "
            "AND NOT EXISTS (SELECT 1 FROM messages w JOIN messages t ON t.id = reactions.message_id "
            "WHERE w.conversation_id = t.conversation_id AND w.sender_handle_id = "
            "reactions.sender_handle_id)"
        )
        orphans = (
            "SELECT h.id FROM handles h WHERE NOT EXISTS (SELECT 1 FROM participants p "
            "WHERE p.handle_id = h.id) AND NOT EXISTS (SELECT 1 FROM messages m "
            "WHERE m.sender_handle_id = h.id) AND NOT EXISTS (SELECT 1 FROM reactions r "
            "WHERE r.sender_handle_id = h.id)"
        )
        db.execute(f"DELETE FROM handle_groups WHERE handle_id IN ({orphans})")
        result.handles_removed = db.execute(f"DELETE FROM handles WHERE id IN ({orphans})").rowcount
        db.commit()
        return result

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

            if record.attachments:
                # A file that was not there last time is recorded without one, under a stand-in
                # hash. When it arrives, that stand-in goes: the import is the truth. A row that
                # has its file is never dropped.
                marks = ", ".join("?" for _ in record.attachments)
                db.execute(
                    f"DELETE FROM attachments WHERE message_id = ? AND storage_path IS NULL "
                    f"AND sha256 NOT IN ({marks})",
                    (record.id, *(a.sha256 for a in record.attachments)),
                )
            for attachment in record.attachments:
                db.execute(
                    "INSERT INTO attachments (sha256, message_id, original_filename, "
                    "mime_type, size, storage_path) VALUES (?, ?, ?, ?, ?, ?) "
                    # a later import that brings the file wins over a row that had none, and
                    # over a place the file has since been moved from (filing sorts that out)
                    # … except a file that was converted when it was filed (a HEIC photo kept as
                    # JPEG): the same original staged again must not be filed a second time
                    "ON CONFLICT (sha256, message_id) DO UPDATE SET storage_path = CASE "
                    "WHEN attachments.processing_info IS NOT NULL "
                    "AND attachments.storage_path IS NOT NULL THEN attachments.storage_path "
                    "ELSE COALESCE(excluded.storage_path, attachments.storage_path) END",
                    (
                        attachment.sha256,
                        record.id,
                        attachment.original_filename,
                        attachment.mime_type,
                        attachment.size,
                        attachment.storage_path,
                    ),
                )
            if record.reactions:  # the import is the truth: replace, so re-imports never duplicate
                db.execute("DELETE FROM reactions WHERE message_id = ?", (record.id,))
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

    def get_messages_for_conversation(self, conversation_id: str) -> list[sqlite3.Row]:
        """One conversation's messages, oldest first, with the legacy column names."""
        return self._db.execute(
            "SELECT * FROM messages_view WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()

    def get_all_messages_by_author(self, author: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM messages_view WHERE message_author = ? ORDER BY timestamp", (author,)
        ).fetchall()

    def export_json(self, service: str, path: Union[str, Path]) -> int:
        """Write every stored message of `service` to a JSON file; return how many."""
        rows = self._db.execute(
            "SELECT m.id, c.source_id AS conversation, c.type AS conversation_type, "
            "m.timestamp_utc, m.tz_offset_minutes, m.time_precision, m.tz_assumed, "
            "h.identifier AS sender, h.display_name AS sender_name, h.person AS person, "
            "m.is_from_owner, "
            "m.content_type, m.body, m.transcript, m.mail_headers, m.service_specific "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "LEFT JOIN handles h ON h.id = m.sender_handle_id "
            "WHERE c.service = ? ORDER BY m.timestamp_utc, m.id",
            (service,),
        ).fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            item["is_from_owner"] = bool(item["is_from_owner"])
            item["tz_assumed"] = bool(item["tz_assumed"])
            for key in ("mail_headers", "service_specific"):
                item[key] = json.loads(item[key]) if item[key] else {}
            messages.append(item)
        document = {"service": service, "schema_version": 1, "messages": messages}
        Path(path).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return len(messages)

    def get_all_authors(self) -> list[sqlite3.Row]:
        return self._db.execute("SELECT DISTINCT message_author FROM messages_view").fetchall()
