"""Normalized records every parser produces, with stable IDs.

Stable IDs make imports idempotent: re-importing an export, or a newer export that overlaps an
older one, maps each message to the same row.

- Conversation id: `<service>:<sha256(service, source conversation id)[:20]>`.
- Message id: from the source's own message id when it has one (iMessage GUID, email
  Message-ID). Otherwise from (service, conversation, time, sender, body, occurrence), where
  occurrence numbers identical messages at the same moment in the same conversation, so genuine
  repeats ("ok", "ok") stay distinct.
- `content_hash` covers the message's content, so a changed message is detected on re-import.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _digest(*parts: object) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConversationRecord:
    service: str
    source_id: str
    type: str = "individual"  # "individual" | "group"
    name: Optional[str] = None
    participants: tuple[str, ...] = ()
    service_specific: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.service.lower()}:{_digest(self.service, self.source_id)[:20]}"


@dataclass(frozen=True)
class AttachmentRecord:
    sha256: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    storage_path: Optional[str] = None


@dataclass(frozen=True)
class ReactionRecord:
    type: str
    sender: Optional[str] = None
    emoji: Optional[str] = None
    timestamp: Optional[datetime] = None
    action: str = "added"  # "added" | "removed"
    target_part: Optional[int] = None


@dataclass(frozen=True)
class MessageRecord:
    timestamp: datetime
    sender: Optional[str]  # handle identifier as the source gives it; None for system notices
    body: Optional[str] = None
    content_type: str = "text"
    source_id: Optional[str] = None
    sender_name: Optional[str] = None
    is_from_owner: bool = False
    time_precision: str = "second"
    tz_assumed: bool = False
    language: Optional[str] = None
    transcript: Optional[str] = None
    reply_to: Optional[str] = None
    mail_headers: dict[str, Any] = field(default_factory=dict)
    service_specific: dict[str, Any] = field(default_factory=dict)
    attachments: tuple[AttachmentRecord, ...] = ()
    reactions: tuple[ReactionRecord, ...] = ()
    id: str = ""
    content_hash: str = ""


def content_hash(record: MessageRecord) -> str:
    return _digest(
        record.content_type,
        record.body,
        record.transcript,
        record.sender,
        record.timestamp.isoformat(),
        json.dumps(record.mail_headers, sort_keys=True, default=str),
        json.dumps(sorted(a.sha256 for a in record.attachments)),
    )


def assign_ids(
    conversation: ConversationRecord, records: list[MessageRecord]
) -> list[MessageRecord]:
    """Return copies of `records` with stable `id` and `content_hash` set."""
    occurrences: dict[str, int] = {}
    result = []
    for record in records:
        if record.source_id:
            message_id = f"{conversation.id}:{_digest(conversation.service, record.source_id)[:24]}"
        else:
            key = _digest(
                conversation.service,
                conversation.source_id,
                record.timestamp.isoformat(),
                record.sender,
                record.body,
            )
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            message_id = f"{conversation.id}:{_digest(key, occurrence)[:24]}"
        result.append(replace(record, id=message_id, content_hash=content_hash(record)))
    return result


#: How the older parsers' `type` values map to the unified content types.
_LEGACY_TYPES = {
    "text": "text",
    "image": "media",
    "video": "media",
    "gallery": "media",
    "audio": "media",
    "document": "media",
    "sticker": "media",
    "media": "media",
    "system": "system",
}


def _attachment_from_parser(message: dict[str, Any]) -> tuple[AttachmentRecord, ...]:
    """The file a legacy parser dict points to: hashed when it is on disk, else just named."""
    path, name = message.get("attachment_path"), message.get("attachment_name")
    if not path and not name:
        return ()
    name = name or Path(str(path)).name
    mime = mimetypes.guess_type(name)[0]
    if path and Path(path).is_file():
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return (AttachmentRecord(digest, name, mime, Path(path).stat().st_size),)
    # The export names a file that wasn't included: identify it by name instead.
    return (AttachmentRecord(_digest("attachment", name), name, mime, None),)


def records_from_parser(
    messages: list[dict[str, Any]], owner: Optional[str] = None
) -> list[MessageRecord]:
    """Convert the dicts today's WhatsApp/Messenger parsers return into MessageRecords.

    Those parsers give `timestamp` (naive local time), `author`, `message` and `type`, plus
    optional `attachment_path`/`attachment_name`, `shared_link`, `precision` and `omitted`. Naive
    times are marked `tz_assumed`; an empty message is stored as no body.
    """
    records = []
    for message in messages:
        timestamp = message["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        author = message.get("author")
        extras = {
            key: message[key]
            for key in ("attachment_path", "shared_link", "omitted")
            if message.get(key)
        }
        records.append(
            MessageRecord(
                timestamp=timestamp,
                sender=author,
                sender_name=author,
                body=message.get("message") or None,
                content_type=_LEGACY_TYPES.get(message.get("type") or "text", "text"),
                is_from_owner=bool(owner) and author == owner,
                time_precision=message.get("precision") or "second",
                tz_assumed=timestamp.tzinfo is None,
                service_specific=extras,
                attachments=_attachment_from_parser(message),
            )
        )
    return records
