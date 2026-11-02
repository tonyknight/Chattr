"""IMessageParser: a macOS `chat.db` as normalized records, one batch per conversation.

It sits on top of `iMessageDatabaseParser` (the reader that knows chat.db's tables, typedstream
bodies, epochs, tapbacks and spam rules) and shapes what that reader returns into the records
the archive stores:

- one `ConversationBatch` per chat, keyed by the chat's GUID (service-qualified, so an iMessage
  and an SMS chat with the same person stay apart);
- each message keyed by its own GUID, so re-importing is idempotent;
- tapbacks are not messages: they become reactions on the message and part they target;
- the owner has no handle in chat.db, so owner messages have `sender=None, is_from_owner=True`.

`parse` returns batches rather than the legacy list of dicts. The collector contract (ticket 20)
generalizes this for every source.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional, Union

from chattr.parsers.base_parser import BaseParser
from chattr.parsers.batch import ConversationBatch
from chattr.parsers.imessage_database_parser import (
    Attachment,
    Message,
    Tapback,
    iMessageDatabaseParser,
    tapback_kind,
)
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord, ReactionRecord

SERVICE = "iMessage"
#: Apple's placeholder for an attachment inside message text.
OBJECT_REPLACEMENT = "￼"
#: Messages that are not in any chat still need a home.
ORPHAN_CHAT = "(no chat)"

PathLike = Union[str, Path]

__all__ = ["ConversationBatch", "IMessageParser"]


def _hash_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class IMessageParser(BaseParser):
    """Parse a `chat.db` into conversations of normalized messages."""

    def __init__(
        self,
        contacts: Optional[PathLike] = None,
        region: str = "US",
        attachment_root: Optional[PathLike] = None,
        spam_mode: str = "aggressive",
        workers: int = 4,
        batch_size: int = 1000,
        chat: Optional[int] = None,
        limit: Optional[int] = None,
        owner: Optional[str] = None,
        aliases: Optional[dict[str, str]] = None,
    ) -> None:
        self.contacts = contacts
        self.region = region
        self.attachment_root = Path(attachment_root).expanduser() if attachment_root else None
        self.spam_mode = spam_mode
        self.workers = workers
        self.batch_size = batch_size
        self.chat = chat
        self.limit = limit
        self.owner = owner
        self.aliases = aliases or {}
        #: Spam-filter counts from the last `parse`, for reporting.
        self.spam_stats: Optional[dict[str, int]] = None

    # --- BaseParser -------------------------------------------------------------------------

    def parse(self, file_path: PathLike) -> list[ConversationBatch]:
        reader = iMessageDatabaseParser(
            db_path=str(Path(file_path).expanduser()),
            vcf_path=str(Path(self.contacts).expanduser()) if self.contacts else None,
            region=self.region,
            attachment_root=str(self.attachment_root) if self.attachment_root else None,
            enable_spam_filtering=self.spam_mode != "disabled",
            aggressive_spam_filtering=self.spam_mode == "aggressive",
        )
        try:
            messages = self._read(reader)
            self.spam_stats = reader.get_spam_filtering_stats()
            return self._batches(reader, messages)
        finally:
            reader.close()

    # --- Reading ----------------------------------------------------------------------------

    def _read(self, reader: iMessageDatabaseParser) -> list[Message]:
        if self.limit:
            messages = reader.get_messages_with_fallback(limit=self.limit, chat_filter=self.chat)
        else:
            messages = reader.get_all_messages_parallel(
                chat_filter=self.chat, max_workers=self.workers, batch_size=self.batch_size
            )
        messages = reader.enrich_messages_with_attachments_parallel(messages)
        messages = reader.enrich_messages_with_tapbacks_parallel(messages)
        return sorted(messages, key=lambda m: (m.timestamp, m.rowid))

    def _batches(
        self, reader: iMessageDatabaseParser, messages: list[Message]
    ) -> list[ConversationBatch]:
        batches: dict[Optional[int], ConversationBatch] = {}
        for message in messages:
            if reader.is_tapback_message(message):
                continue  # carried as a reaction on its target
            batch = batches.get(message.chat_id)
            if batch is None:
                batch = batches[message.chat_id] = ConversationBatch(
                    self._conversation(reader, message.chat_id)
                )
            batch.records.append(self._record(reader, message))
        return list(batches.values())

    def _conversation(
        self, reader: iMessageDatabaseParser, chat_id: Optional[int]
    ) -> ConversationRecord:
        chat = reader.enhanced_chats.get(chat_id) if chat_id is not None else None
        if chat is None:
            return ConversationRecord(service=SERVICE, source_id=ORPHAN_CHAT, name=ORPHAN_CHAT)
        source_id = reader.chat_guids.get(chat.rowid) or chat.chat_identifier
        name = reader._generate_chat_name(chat)
        if not chat.is_group_chat and not (chat.display_name or "").strip():
            # A one-to-one chat is named after the person when the contacts know them.
            name = (
                self._name(chat.chat_identifier, reader._enhance_contact_name(chat.chat_identifier))
                or name
            )
        return ConversationRecord(
            service=SERVICE,
            source_id=source_id,
            type="group" if chat.is_group_chat else "individual",
            name=name,
            participants=tuple(chat.participants or ()),
            service_specific={"chat_identifier": chat.chat_identifier, "style": chat.style},
        )

    # --- Messages ---------------------------------------------------------------------------

    def _name(self, identifier: Optional[str], fallback: Optional[str]) -> Optional[str]:
        if identifier and identifier in self.aliases:
            return self.aliases[identifier]
        if fallback and fallback in self.aliases:
            return self.aliases[fallback]
        return fallback

    def _record(self, reader: iMessageDatabaseParser, message: Message) -> MessageRecord:
        # Handle 0 is not a person: it is the owner in group chats, or no sender at all.
        raw_handle = (
            reader.handles.get(message.handle_id)
            if message.handle_id and not message.is_from_me
            else None
        )
        sender_name: Optional[str]
        if message.is_from_me:
            sender_name = self.owner or "Me"
        elif raw_handle:
            sender_name = self._name(raw_handle, reader._get_sender_name(message.handle_id, False))
        else:
            sender_name = None
        attachments = tuple(self._attachment(a) for a in (message.attachments or []))
        text = message.text
        placeholder_only = text is not None and not text.replace(OBJECT_REPLACEMENT, "").strip()
        if message.spam_reason:
            content_type = "system"
        elif attachments and (not text or placeholder_only):
            content_type = "media"
        else:
            content_type = "text"
        if placeholder_only:
            text = None
        extras: dict[str, Any] = {"transport": message.service}
        for key, value in (
            ("spam_reason", message.spam_reason),
            ("balloon_bundle_id", message.balloon_bundle_id),
            ("read_at", _iso(message.date_read)),
            ("delivered_at", _iso(message.date_delivered)),
        ):
            if value:
                extras[key] = value
        return MessageRecord(
            timestamp=message.timestamp,
            sender=raw_handle,
            sender_name=sender_name,
            body=text,
            content_type=content_type,
            source_id=message.guid,
            is_from_owner=message.is_from_me,
            attachments=attachments,
            reactions=tuple(self._reaction(reader, t) for t in (message.tapbacks or [])),
            service_specific=extras,
        )

    def _attachment(self, attachment: Attachment) -> AttachmentRecord:
        name = attachment.transfer_name or os.path.basename(attachment.filename or "")
        digest = _hash_file(self._source_path(attachment)) if attachment.filename else None
        if digest is None:  # file not on this machine: identify it by its chat.db GUID instead
            digest = hashlib.sha256(f"attachment:{attachment.guid or name}".encode()).hexdigest()
        return AttachmentRecord(
            sha256=digest,
            original_filename=name or None,
            mime_type=attachment.mime_type,
            size=attachment.total_bytes or None,
        )

    def _source_path(self, attachment: Attachment) -> Path:
        from chattr.attachment_manager import IMESSAGE_ATTACHMENTS_PREFIX

        filename = attachment.filename or ""
        if self.attachment_root and filename.startswith(IMESSAGE_ATTACHMENTS_PREFIX):
            return self.attachment_root / filename[len(IMESSAGE_ATTACHMENTS_PREFIX) :]
        return Path(filename).expanduser()

    def _reaction(self, reader: iMessageDatabaseParser, tapback: Tapback) -> ReactionRecord:
        return ReactionRecord(
            type=tapback_kind(tapback.type),
            sender=tapback.sender_id,
            timestamp=tapback.timestamp,
            action="removed" if tapback.action == "Removed" else "added",
            target_part=tapback.component_index,
        )


def _iso(value: Optional[int]) -> Optional[str]:
    from chattr import apple_time

    moment = apple_time.to_utc(value)
    return moment.isoformat() if moment else None
