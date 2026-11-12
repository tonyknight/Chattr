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
from typing import Any, Callable, Optional, Union

from chattr import blobs
from chattr.imessage_groups import handle_groups, merged_chats

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
        person_for: Optional[Callable[[str], Optional[str]]] = None,
        attachments_dir: Optional[PathLike] = None,
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
        #: The person an address belongs to, when the contacts know (used to join addresses).
        self.person_for = person_for
        #: Where to copy the attachments that are on this Mac (by content hash; filed later).
        self.attachments_dir = Path(attachments_dir) if attachments_dir else None
        #: Attachments the database names whose files are not on this Mac (after `parse`).
        self.missing_attachments = 0
        self.handle_groups: list[list[str]] = []
        self._conversation_of: dict[int, int] = {}
        self._members: dict[int, list[int]] = {}
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
            self._group(reader)
            self.missing_attachments = 0
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

    def _group(self, reader: iMessageDatabaseParser) -> None:
        """Work out which handles are one person and which chats are one conversation."""
        links: list[tuple[int, int]] = []
        if self.person_for is not None:
            by_person: dict[str, int] = {}
            for rowid, address in reader.handles.items():
                person = self.person_for(address) if rowid else None
                if person:
                    links.append((rowid, by_person.setdefault(person, rowid)))
        groups = handle_groups(reader.conn, reader.capabilities, links)
        self._conversation_of = merged_chats(reader.conn, reader.capabilities, groups)
        self._members = {}
        for chat, representative in self._conversation_of.items():
            self._members.setdefault(representative, []).append(chat)
        by_group: dict[int, set[str]] = {}
        for rowid, group in groups.items():
            address = (reader.handles.get(rowid) or "").strip()
            if address:
                by_group.setdefault(group, set()).add(address)
        #: Addresses the database (or the contacts) says are one person, where there are several.
        self.handle_groups = [sorted(found) for found in by_group.values() if len(found) > 1]

    def _batches(
        self, reader: iMessageDatabaseParser, messages: list[Message]
    ) -> list[ConversationBatch]:
        batches: dict[Optional[int], ConversationBatch] = {}
        for message in messages:
            if reader.is_tapback_message(message):
                continue  # carried as a reaction on its target
            chat = message.chat_id
            if chat is not None:  # a split conversation is imported as the one it is
                chat = self._conversation_of.get(chat, chat)
            batch = batches.get(chat)
            if batch is None:
                batch = batches[chat] = ConversationBatch(self._conversation(reader, chat))
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
            participants=self._participants(reader, chat),
            service_specific={"chat_identifier": chat.chat_identifier, "style": chat.style},
        )

    def _participants(self, reader: iMessageDatabaseParser, chat: Any) -> tuple[str, ...]:
        """Everyone in the conversation: the people of every chat row merged into it."""
        found: list[str] = []
        for member in sorted(self._members.get(chat.rowid, [chat.rowid])):
            other = reader.enhanced_chats.get(member)
            for address in (other.participants if other else None) or ():
                if address not in found:
                    found.append(address)
        return tuple(found)

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
        source = self._source_path(attachment) if attachment.filename else None
        digest = _hash_file(source) if source else None
        stored = None
        if digest is None:  # file not on this machine: identify it by its chat.db GUID instead
            self.missing_attachments += 1
            digest = hashlib.sha256(f"attachment:{attachment.guid or name}".encode()).hexdigest()
        elif self.attachments_dir is not None and source is not None:
            try:  # a copy; the Mac's own store is never changed
                stored = blobs.store_file_by_hash(self.attachments_dir, source, digest)
            except OSError:
                self.missing_attachments += 1
        return AttachmentRecord(
            sha256=digest,
            original_filename=name or None,
            mime_type=attachment.mime_type,
            size=attachment.total_bytes or None,
            storage_path=stored,
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
