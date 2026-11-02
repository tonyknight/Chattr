"""Running an import: database rows, an `imports` record, and the service's state file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from chattr.config import Settings
from chattr.database_manager import DatabaseManager, ImportCounts
from chattr.records import ConversationRecord, records_from_parser

#: Display names used in the CLI → the key of their `Services/<Service>.json` file.
STATE_KEYS = {
    "whatsapp": "whatsapp",
    "facebook messenger": "messenger",
    "messenger": "messenger",
    "instagram": "instagram",
    "imessage": "imessage",
    "google voice": "googlevoice",
    "voicemail": "voicemail",
}

#: Export files whose folder, not their own name, identifies the conversation.
GENERIC_EXPORT_FILES = {"_chat.txt", "message_1.json"}


def conversation_for(service: str, source: Union[str, Path]) -> ConversationRecord:
    """The conversation an export file belongs to, named after the export."""
    path = Path(source)
    name = path.parent.name if path.name in GENERIC_EXPORT_FILES else path.stem
    return ConversationRecord(service=service, source_id=name, name=name)


def import_messages(
    db: DatabaseManager,
    settings: Optional[Settings],
    service: str,
    source: Union[str, Path],
    messages: list[dict[str, Any]],
    owner: Optional[str] = None,
    conversation: Optional[ConversationRecord] = None,
) -> ImportCounts:
    """Import parsed messages as one recorded run, then update the service's state."""
    conversation = conversation or conversation_for(service, source)
    import_id = db.start_import(service, source)
    counts = db.upsert(conversation, records_from_parser(messages, owner=owner), import_id)
    db.finish_import(import_id, counts)
    db.commit()
    _save_state(settings, service, source, counts)
    return counts


def import_conversations(
    db: DatabaseManager,
    settings: Optional[Settings],
    service: str,
    source: Union[str, Path],
    batches: Iterable[Any],
) -> ImportCounts:
    """Import many conversations (each a batch with `.conversation` and `.records`) as one run."""
    import_id = db.start_import(service, source)
    total = ImportCounts()
    for batch in batches:
        counts = db.upsert(batch.conversation, batch.records, import_id)
        total.inserted += counts.inserted
        total.updated += counts.updated
        total.unchanged += counts.unchanged
    db.finish_import(import_id, total)
    db.commit()
    _save_state(settings, service, source, total)
    return total


def _save_state(
    settings: Optional[Settings], service: str, source: Union[str, Path], counts: ImportCounts
) -> None:
    key = STATE_KEYS.get(service.lower())
    if settings is not None and key in settings.services():
        state = settings.get(f"services.{key}.state", {}) or {}
        settings.save_state(
            key,
            {
                "last_import_date": datetime.now(timezone.utc).isoformat(),
                "total_messages_imported": int(state.get("total_messages_imported") or 0)
                + counts.inserted,
                "last_export_file": str(source),
            },
        )
