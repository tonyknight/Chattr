"""What a Messages database can tell us, found by looking at it.

Schemas vary independently of the macOS release: two Macs on one build can carry different
`message` columns. `Capabilities.determine` reads the live schema once, and `message_select`
then asks only for columns that exist, giving `NULL` for the rest, so every row has every column
the reader knows and nothing downstream cares which macOS wrote the database.

Derived from imessage-exporter 5.0.1 (`tables/capabilities.rs`, `tables/messages/columns.rs`); see
NOTICE.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: The `message` columns the reader uses, lower case, in a fixed order.
MESSAGE_COLUMNS = (
    "rowid", "guid", "text", "service", "handle_id", "destination_caller_id", "subject", "date",
    "date_read", "date_delivered", "is_from_me", "is_read", "item_type", "other_handle",
    "share_status", "share_direction", "group_title", "group_action_type",
    "message_action_type", "error", "associated_message_guid", "associated_message_type",
    "balloon_bundle_id", "expressive_send_style_id", "thread_originator_guid",
    "thread_originator_part", "date_edited", "date_retracted", "associated_message_emoji",
    "filter_action", "filter_sub_action", "attributedbody", "message_summary_info",
    "payload_data",
)  # fmt: skip


class NotMessagesDatabase(Exception):
    """The file is a database, but it has no `message` table."""


def _columns(db: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(str(row[1]).lower() for row in db.execute(f"PRAGMA table_info({table})"))


def _tables(db: sqlite3.Connection) -> frozenset[str]:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return frozenset(str(row[0]).lower() for row in rows)


@dataclass(frozen=True)
class Capabilities:
    """What this database's schema holds (column names in lower case)."""

    message: frozenset[str]
    attachment: frozenset[str]
    handle: frozenset[str]
    chat: frozenset[str]
    tables: frozenset[str]

    @classmethod
    def determine(cls, db: sqlite3.Connection) -> "Capabilities":
        tables = _tables(db)
        if "message" not in tables:
            raise NotMessagesDatabase(
                "This is not a Messages database: it has no 'message' table. Point at chat.db "
                "(on a Mac: ~/Library/Messages/chat.db)."
            )
        known = set(MESSAGE_COLUMNS)
        return cls(
            message=frozenset(_columns(db, "message") & known),
            attachment=_columns(db, "attachment") if "attachment" in tables else frozenset(),
            handle=_columns(db, "handle") if "handle" in tables else frozenset(),
            chat=_columns(db, "chat") if "chat" in tables else frozenset(),
            tables=tables,
        )

    @property
    def replies(self) -> bool:
        return "thread_originator_guid" in self.message

    @property
    def edits(self) -> bool:
        return "date_edited" in self.message

    @property
    def tapbacks(self) -> bool:
        return "associated_message_guid" in self.message

    @property
    def filter_actions(self) -> bool:
        """Apple's filter categories; the two columns are one feature and count only together."""
        return {"filter_action", "filter_sub_action"} <= self.message

    @property
    def recoverable_messages(self) -> bool:
        return "chat_recoverable_message_join" in self.tables

    @property
    def chat_lookup(self) -> bool:
        return "chat_lookup" in self.tables

    @property
    def person_centric_ids(self) -> bool:
        return "person_centric_id" in self.handle

    def describe(self) -> dict[str, bool]:
        """The capabilities by name, for a diagnostic report."""
        return {
            "Reply threads": self.replies,
            "Edited and unsent messages": self.edits,
            "Tapbacks": self.tapbacks,
            "Recently deleted messages": self.recoverable_messages,
            "Message filter categories": self.filter_actions,
            "Person-centric handle IDs": self.person_centric_ids,
            "Conversation lookup table": self.chat_lookup,
        }


def message_select(caps: Capabilities) -> str:
    """`SELECT … FROM message m LEFT JOIN chat_message_join c` with every known column: the real
    one where the database has it, `NULL` where it does not. Add `WHERE`/`ORDER BY` after it."""
    paired = {"filter_action", "filter_sub_action"}
    parts = []
    for column in MESSAGE_COLUMNS:
        present = column in caps.message and (column not in paired or caps.filter_actions)
        parts.append(f"m.{column} AS {column}" if present else f"NULL AS {column}")
    return (
        "SELECT " + ", ".join(parts) + ", c.chat_id AS chat_id, "
        "(SELECT COUNT(*) FROM message_attachment_join a WHERE m.ROWID = a.message_id) "
        "AS num_attachments "
        "FROM message AS m LEFT JOIN chat_message_join AS c ON m.ROWID = c.message_id"
    )
