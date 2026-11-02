"""Plan and describe the pages rendered from `chattr.sqlite`.

Pages are organized for people, not platforms (see the Requirements):

    Messages/<Person>/(YYYY) <Person>.html           every platform, one page per year
    Messages/Groups/<Group>/(YYYY) <Group>.html
    Mail/<YYYY>/(YYYY-MM-DD) <sender> — <subject>.html     one page per mail thread

A conversation belongs to its *counterpart*: the non-owner person behind its handles, or the handle
itself while it has no person, so one person's iMessage, WhatsApp, Google Voice, voicemail and
Messenger history share one page set. Two-person chats need the owner configured so the counterpart
can be told from the owner (on WhatsApp and Messenger the owner is just another name); without it a
chat falls back to its own name. Years and dates are the event's **local** time: UTC plus the stored
offset (UTC as stored when the zone is unknown).

`Renderer` loads the archive once, plans the pages, and builds each page's *data island*: a JSON
document with every stored column of its messages, so a database can be rebuilt from the pages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from chattr.attachment_manager import safe_name
from chattr.database_manager import DatabaseManager

SCHEMA = "chattr-page-1"
MAIL_SERVICE = "Mail"
_CHAT_PREFIXES = ("WhatsApp Chat with ", "WhatsApp Chat - ")


def local_moment(timestamp_utc: str, offset_minutes: Optional[int]) -> datetime:
    """The event's wall-clock time (naive): UTC plus the stored offset, or UTC when unknown."""
    moment = datetime.fromisoformat(timestamp_utc)
    if offset_minutes is not None:
        moment = moment + timedelta(minutes=offset_minutes)
    return moment.replace(tzinfo=None)


@dataclass
class PageSpec:
    kind: str  # "person" | "group" | "mail"
    title: str
    year: int
    folder: str
    filename: str
    conversation_ids: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.filename}"


def _chat_label(name: str) -> str:
    for prefix in _CHAT_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _handle_label(row: Any) -> str:
    return row["person"] or row["display_name"] or row["identifier"]


class Renderer:
    """The archive, loaded once, planned into pages."""

    def __init__(self, db: DatabaseManager) -> None:
        conn = db.connection
        self.conversations: dict[str, dict[str, Any]] = {
            r["id"]: dict(r) for r in conn.execute("SELECT * FROM conversations")
        }
        self.handles: dict[int, dict[str, Any]] = {
            r["id"]: dict(r) for r in conn.execute("SELECT * FROM handles")
        }
        self.messages: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT m.*, c.service AS service FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id ORDER BY m.timestamp_utc, m.id"
        ):
            message = dict(row)
            message["local"] = local_moment(message["timestamp_utc"], message["tz_offset_minutes"])
            self.messages[message["id"]] = message
        self.attachments: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute("SELECT * FROM attachments ORDER BY rowid"):
            self.attachments.setdefault(row["message_id"], []).append(dict(row))
        self.reactions: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute("SELECT * FROM reactions ORDER BY id"):
            self.reactions.setdefault(row["message_id"], []).append(dict(row))
        self.participants: dict[str, list[int]] = {}
        self.owner_handles: set[int] = set()
        for row in conn.execute("SELECT * FROM participants"):
            self.participants.setdefault(row["conversation_id"], []).append(row["handle_id"])
            if row["role"] == "owner":
                self.owner_handles.add(row["handle_id"])
        for message in self.messages.values():
            if message["is_from_owner"] and message["sender_handle_id"]:
                self.owner_handles.add(message["sender_handle_id"])
        self.pages: list[PageSpec] = self._plan()

    # --- Planning ---------------------------------------------------------------------------

    def _by_conversation(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for message in self.messages.values():
            grouped.setdefault(message["conversation_id"], []).append(message)
        return grouped

    def _counterpart(self, conversation: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        handle_ids = set(self.participants.get(conversation["id"], []))
        handle_ids |= {m["sender_handle_id"] for m in messages if m["sender_handle_id"]}
        people = {_handle_label(self.handles[h]) for h in handle_ids if h not in self.owner_handles}
        if len(people) == 1:
            return next(iter(people))
        return _chat_label(conversation["name"] or conversation["source_id"])

    def _plan(self) -> list[PageSpec]:
        pages: dict[tuple[str, str, int], PageSpec] = {}
        for conversation_id, messages in self._by_conversation().items():
            conversation = self.conversations[conversation_id]
            if conversation["service"] == MAIL_SERVICE:
                spec = self._mail_page(conversation, messages)
                pages[("mail", spec.path, spec.year)] = spec
                continue
            group = conversation["type"] == "group"
            title = (
                conversation["name"] or conversation["source_id"]
                if group
                else self._counterpart(conversation, messages)
            )
            kind = "group" if group else "person"
            folder_name = safe_name(title, "Unknown")
            folder = f"Messages/Groups/{folder_name}" if group else f"Messages/{folder_name}"
            for message in messages:
                year = message["local"].year
                key = (kind, folder, year)
                page = pages.get(key)
                if page is None:
                    page = pages[key] = PageSpec(
                        kind, title, year, folder, f"({year}) {folder_name}.html"
                    )
                if conversation_id not in page.conversation_ids:
                    page.conversation_ids.append(conversation_id)
                page.message_ids.append(message["id"])
        for spec in pages.values():
            spec.message_ids.sort(key=lambda m: (self.messages[m]["timestamp_utc"], m))
        return sorted(pages.values(), key=lambda p: (p.folder, p.year, p.filename))

    def _mail_page(self, conversation: dict[str, Any], messages: list[dict[str, Any]]) -> PageSpec:
        first = messages[0]
        sender_id = first["sender_handle_id"]
        sender = _handle_label(self.handles[sender_id]) if sender_id else "unknown sender"
        subject = conversation["name"] or "(no subject)"
        date = first["local"]
        name = f"({date:%Y-%m-%d}) {safe_name(sender)} — {safe_name(subject, '(no subject)', 100)}.html"
        return PageSpec(
            "mail",
            subject,
            date.year,
            f"Mail/{date.year}",
            name,
            [conversation["id"]],
            [m["id"] for m in messages],
        )

    # --- The data island --------------------------------------------------------------------

    def document(self, spec: PageSpec) -> dict[str, Any]:
        """Everything stored about this page's messages, as plain JSON-able data."""
        handle_ids: set[int] = set()
        for cid in spec.conversation_ids:
            handle_ids |= set(self.participants.get(cid, []))
        messages = []
        for mid in spec.message_ids:
            row = self.messages[mid]
            if row["sender_handle_id"]:
                handle_ids.add(row["sender_handle_id"])
            messages.append(self._message_document(row))
            handle_ids |= {
                r["sender_handle_id"] for r in self.reactions.get(mid, []) if r["sender_handle_id"]
            }
        handles = [self._handle_document(self.handles[h]) for h in sorted(handle_ids)]
        conversations = []
        for cid in spec.conversation_ids:
            conversation = dict(self.conversations[cid])
            conversation["service_specific"] = _loads(conversation.get("service_specific"))
            conversation["participants"] = [
                {
                    "service": self.handles[h]["service"],
                    "identifier": self.handles[h]["identifier"],
                    "role": self._role(cid, h),
                }
                for h in self.participants.get(cid, [])
            ]
            conversations.append(conversation)
        return {
            "schema": SCHEMA,
            "kind": spec.kind,
            "title": spec.title,
            "year": spec.year,
            "conversations": conversations,
            "handles": handles,
            "messages": messages,
        }

    def _role(self, conversation_id: str, handle_id: int) -> str:
        return "owner" if handle_id in self.owner_handles else "member"

    @staticmethod
    def _handle_document(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in ("service", "identifier", "normalized", "display_name", "person")
        }

    def _message_document(self, row: dict[str, Any]) -> dict[str, Any]:
        sender = None
        if row["sender_handle_id"]:
            handle = self.handles[row["sender_handle_id"]]
            sender = {"service": handle["service"], "identifier": handle["identifier"]}
        document = {
            key: row[key]
            for key in (
                "id", "conversation_id", "source_id", "timestamp_utc", "tz_offset_minutes",
                "time_precision", "language", "translation", "transcript", "reply_to",
                "edit_history", "forwarded_from", "delivery_status", "content_type", "body",
                "content_hash", "version",
            )
        }  # fmt: skip
        document["tz_assumed"] = bool(row["tz_assumed"])
        document["is_from_owner"] = bool(row["is_from_owner"])
        document["sender"] = sender
        document["mail_headers"] = _loads(row["mail_headers"])
        document["service_specific"] = _loads(row["service_specific"])
        document["attachments"] = [
            {
                "sha256": a["sha256"],
                "original_filename": a["original_filename"],
                "archival_filename": a["archival_filename"],
                "mime_type": a["mime_type"],
                "size": a["size"],
                "storage_path": a["storage_path"],
                "processing_info": _loads(a["processing_info"]),
            }
            for a in self.attachments.get(row["id"], [])
        ]
        document["reactions"] = [
            {
                "target_part": r["target_part"],
                "sender": self._sender_ref(r["sender_handle_id"]),
                "type": r["type"],
                "emoji": r["emoji"],
                "timestamp_utc": r["timestamp_utc"],
                "action": r["action"],
            }
            for r in self.reactions.get(row["id"], [])
        ]
        return document

    def _sender_ref(self, handle_id: Optional[int]) -> Optional[dict[str, str]]:
        if not handle_id:
            return None
        handle = self.handles[handle_id]
        return {"service": handle["service"], "identifier": handle["identifier"]}


def _loads(value: Optional[str]) -> Any:
    return json.loads(value) if value else {}


__all__ = ["PageSpec", "Renderer", "local_moment", "SCHEMA"]
