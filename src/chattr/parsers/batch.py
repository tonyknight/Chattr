"""The shape batch parsers return: one conversation and its messages."""

from __future__ import annotations

from dataclasses import dataclass, field

from chattr.records import ConversationRecord, MessageRecord


@dataclass
class ConversationBatch:
    """A conversation (a chat, a mail thread) with its normalized messages, oldest first."""

    conversation: ConversationRecord
    records: list[MessageRecord] = field(default_factory=list)
