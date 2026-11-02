"""The work behind Chattr's menu and its `chattr-cli` subcommands.

Every function here takes explicit arguments and never prompts. The interactive menu and the
non-interactive subcommands both call these, so anything the menu does can be scripted.

Routes for a conversion:
- "central": import into the central archive (`<root>/Database/chattr.sqlite`); HTML goes to
  `Output/Chattr/`.
- "service": convert into the service's own folder (`Output/<Service>/`) as JSON (and optional
  HTML), without touching the central archive.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from chattr.config import Settings
from chattr.database_manager import DatabaseManager, ImportCounts
from chattr.html_generator import HTMLGenerator
from chattr.imessage_export import export_to_json, print_message_summary
from chattr.importing import conversation_for, import_messages
from chattr.parsers.imessage_database_parser import iMessageDatabaseParser
from chattr.parsers.messenger_parser import MessengerParser
from chattr.parsers.whatsapp_parser import WhatsAppParser

PathLike = Union[str, Path]

#: Services converted through the common parser path: key → (display name, parser class).
SERVICES: dict[str, tuple[str, Any]] = {
    "whatsapp": ("WhatsApp", WhatsAppParser),
    "messenger": ("Messenger", MessengerParser),
}

#: Output folder for each service (matches preflight's `Output/` tree).
OUTPUT_FOLDERS = {"whatsapp": "WhatsApp", "messenger": "Messenger", "imessage": "iMessage"}

#: Name of the HTML page for the whole central archive (Chattr's earlier name for it).
ARCHIVE_PAGE = "All_Chats_Archive.html"

#: File name of iMessage's native JSON export (Chattr's earlier example name for it).
IMESSAGE_EXPORT = "imessage_export.json"

#: Spam filtering modes for iMessage, as named in `Services/iMessage.json`.
SPAM_MODES = ("aggressive", "conservative", "disabled")

#: Diagnostics `doctor` can run against an iMessage database.
DOCTOR_CHECKS = ("info", "connection", "spam")


class CommandError(Exception):
    """A command couldn't run; the message explains why in plain words."""


@dataclass
class ConvertResult:
    service: str
    counts: ImportCounts
    json_path: Optional[Path] = None
    html_path: Optional[Path] = None


def central_database(settings: Settings) -> Path:
    """The central archive for this Chattr root: `<root>/Database/chattr.sqlite`."""
    return Path(settings.root) / "Database" / "chattr.sqlite"


def output_folder(settings: Settings, folder: str) -> Path:
    path = Path(settings.root) / "Output" / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def _apply_aliases(messages: list[dict[str, Any]], aliases: Optional[PathLike]) -> None:
    """Rename senders using a JSON file of {identifier or name: person name}."""
    if not aliases:
        return
    try:
        mapping = json.loads(Path(aliases).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"Can't read the aliases file {aliases}: {error}") from error
    if not isinstance(mapping, dict):
        raise CommandError(f"The aliases file must contain a JSON object: {aliases}")
    for message in messages:
        author = message.get("author")
        if author in mapping:
            message["author"] = mapping[author]


def _write_conversation_html(
    db: DatabaseManager, conversation_id: str, name: str, folder: Path, owner: Optional[str]
) -> Optional[Path]:
    rows = db.get_messages_for_conversation(conversation_id)
    if not rows:
        return None
    first, last = rows[0]["timestamp"][:10], rows[-1]["timestamp"][:10]
    path = folder / f"[{name}] ({first} to {last}).html"
    HTMLGenerator().generate_html(rows, str(path), main_author=owner)
    return path


def convert(
    settings: Settings,
    service: str,
    export: PathLike,
    route: str = "central",
    html: bool = False,
    owner: Optional[str] = None,
    aliases: Optional[PathLike] = None,
) -> ConvertResult:
    """Convert one WhatsApp or Messenger export along the chosen route."""
    key = service.lower()
    if key not in SERVICES:
        raise CommandError(f"Unknown service '{service}'. Choose one of: {', '.join(SERVICES)}.")
    if route not in ("central", "service"):
        raise CommandError("Route must be 'central' or 'service'.")
    export = Path(export)
    if not export.is_file():
        raise CommandError(f"Export file not found: {export}")
    name, parser_class = SERVICES[key]
    messages = parser_class().parse(str(export))
    if not messages:
        raise CommandError(f"No messages found in {export}")
    _apply_aliases(messages, aliases)
    conversation = conversation_for(name, export)

    if route == "central":
        with DatabaseManager(central_database(settings)) as db:
            counts = import_messages(db, settings, name, export, messages, owner=owner)
            html_path = (
                _write_conversation_html(
                    db,
                    conversation.id,
                    conversation.source_id,
                    output_folder(settings, "Chattr"),
                    owner,
                )
                if html
                else None
            )
        return ConvertResult(name, counts, html_path=html_path)

    folder = output_folder(settings, OUTPUT_FOLDERS[key])
    with DatabaseManager(":memory:") as db:
        counts = import_messages(db, None, name, export, messages, owner=owner)
        json_path = folder / f"{conversation.source_id}.json"
        db.export_json(name, json_path)
        html_path = (
            _write_conversation_html(db, conversation.id, conversation.source_id, folder, owner)
            if html
            else None
        )
    return ConvertResult(name, counts, json_path=json_path, html_path=html_path)


def generate_html(
    settings: Settings, out: Optional[PathLike] = None, owner: Optional[str] = None
) -> Optional[Path]:
    """Render the whole central archive as one HTML page; returns its path (None if empty)."""
    with DatabaseManager(central_database(settings)) as db:
        rows = db.get_all_messages()
    if not rows:
        return None
    path = Path(out) if out else output_folder(settings, "Chattr") / ARCHIVE_PAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    HTMLGenerator().generate_html(rows, str(path), main_author=owner)
    return path


def _print_spam_stats(parser: iMessageDatabaseParser) -> None:
    stats = parser.get_spam_filtering_stats()
    if not stats or not stats["total_suppressed"]:
        return
    print(
        f"Spam filtering flagged {stats['total_suppressed']:,} low-value messages (kept, tagged):"
    )
    for category, count in stats.items():
        if category != "total_suppressed" and count > 0:
            print(f"  - {category.replace('_', ' ').title()}: {count:,}")


def _open_imessage(
    db_path: PathLike,
    contacts: Optional[PathLike] = None,
    region: str = "US",
    attachment_root: Optional[PathLike] = None,
    spam_mode: str = "aggressive",
) -> iMessageDatabaseParser:
    if spam_mode not in SPAM_MODES:
        raise CommandError(
            f"Unknown spam mode '{spam_mode}'. Choose one of: {', '.join(SPAM_MODES)}."
        )
    db_path = Path(db_path).expanduser()
    if not db_path.is_file():
        raise CommandError(f"iMessage database not found: {db_path}")
    try:
        return iMessageDatabaseParser(
            db_path=str(db_path),
            vcf_path=str(Path(contacts).expanduser()) if contacts else None,
            region=region,
            attachment_root=str(Path(attachment_root).expanduser()) if attachment_root else None,
            enable_spam_filtering=spam_mode != "disabled",
            aggressive_spam_filtering=spam_mode == "aggressive",
        )
    except PermissionError as error:  # the parser's message explains macOS Full Disk Access
        raise CommandError(str(error)) from error
    except sqlite3.DatabaseError as error:
        raise CommandError(f"Can't read the iMessage database {db_path}: {error}") from error


def convert_imessage(
    settings: Settings,
    db_path: Optional[PathLike] = None,
    out: Optional[PathLike] = None,
    contacts: Optional[PathLike] = None,
    region: Optional[str] = None,
    attachment_root: Optional[PathLike] = None,
    batch_size: Optional[int] = None,
    attachment_workers: Optional[int] = None,
    workers: Optional[int] = None,
    spam_mode: Optional[str] = None,
    chat: Optional[int] = None,
    limit: Optional[int] = None,
    verbose: bool = False,
    attachments_dir: Optional[PathLike] = None,
) -> ConvertResult:
    """Export an iMessage database (`chat.db`) to iMessage's native JSON in `Output/iMessage/`.

    Unset options come from `services.imessage.preferences` (contacts and phone region from
    `global.contacts`). Archival copies of attachments
    are written only when `attachments_dir` is given. Import into the central archive comes
    later (ticket 07).
    """
    db_path = db_path or settings.get_expanded_path(
        "services.imessage.preferences.database.default_db_path"
    )
    attachment_root = attachment_root or settings.get_expanded_path(
        "services.imessage.preferences.database.default_attachments_path"
    )
    contacts = contacts or settings.get_expanded_path("global.contacts.vcf_file_path") or None
    region = region or settings.get("global.contacts.phone_region", "US")
    spam_mode = spam_mode or settings.get(
        "services.imessage.preferences.spam_filtering.mode", "aggressive"
    )
    workers = workers or int(settings.get("services.imessage.preferences.performance.workers", 4))
    batch_size = batch_size or int(
        settings.get("services.imessage.preferences.performance.batch_size", 1000)
    )
    attachment_workers = attachment_workers or int(
        settings.get("services.imessage.preferences.attachments.general.max_attachment_workers", 4)
    )
    if not db_path:
        raise CommandError("No iMessage database given and none configured.")
    out_path = (
        Path(out) if out else output_folder(settings, OUTPUT_FOLDERS["imessage"]) / IMESSAGE_EXPORT
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parser = _open_imessage(db_path, contacts, str(region), attachment_root, str(spam_mode))
    try:
        if limit:
            messages = parser.get_messages_with_fallback(limit=limit, chat_filter=chat)
        else:
            messages = parser.get_all_messages_parallel(
                chat_filter=chat, max_workers=workers, batch_size=batch_size
            )
        if not messages:
            raise CommandError(f"No messages found in {db_path}")
        _print_spam_stats(parser)
        if verbose:
            for message in messages:
                print_message_summary(
                    message,
                    parser.get_attachments_for_message(message.rowid),
                    parser.get_tapbacks_for_message(message.guid),
                )
        export_to_json(
            messages,
            parser,
            str(out_path),
            attachments_dir=str(attachments_dir) if attachments_dir else None,
            source_attachments_dir=str(attachment_root or "~/Library/Messages/Attachments"),
            attachment_workers=attachment_workers,
        )
    finally:
        parser.close()
    counts = ImportCounts(inserted=len(messages))
    return ConvertResult("iMessage", counts, json_path=out_path)


def list_chats(db_path: PathLike) -> list[tuple[int, str]]:
    """The chats in an iMessage database, as (chat id, name) pairs sorted by id."""
    parser = _open_imessage(db_path, spam_mode="disabled")
    try:
        return sorted(parser.chats.items())
    finally:
        parser.close()


def doctor(db_path: PathLike, check: str = "info") -> bool:
    """Run one diagnostic against an iMessage database, printing what it finds.

    Returns True when the check succeeds and False (with the reason printed) when it fails.
    """
    if check not in DOCTOR_CHECKS:
        raise CommandError(f"Unknown check '{check}'. Choose one of: {', '.join(DOCTOR_CHECKS)}.")
    db_path = Path(db_path).expanduser()
    print(f"Database: {db_path}")
    if not db_path.is_file():
        print("❌ Database not found")
        return False
    try:
        if check == "info":
            _doctor_info(db_path)
        elif check == "connection":
            _doctor_connection(db_path)
        else:
            _doctor_spam(db_path)
    except Exception as error:  # report any failure in plain words; doctor never raises
        print(f"❌ {check} check failed: {error}")
        return False
    return True


def _doctor_info(db_path: Path) -> None:
    stat = os.stat(db_path)
    print(f"Size: {stat.st_size:,} bytes")
    print(f"Last modified: {datetime.fromtimestamp(stat.st_mtime)}")
    parser = _open_imessage(db_path, spam_mode="disabled")
    try:
        print(f"Handles: {len(parser.handles)}")
        print(f"Chats: {len(parser.chats)}")
        found = parser.get_messages_with_fallback(limit=1)
        print("Messages: ✅ found" if found else "Messages: ⚠️  none found")
    finally:
        parser.close()


def _doctor_connection(db_path: Path) -> None:
    parser = _open_imessage(db_path, spam_mode="disabled")
    try:
        print("✅ Database connection successful")
        print(f"✅ Loaded {len(parser.handles)} handles")
        print(f"✅ Loaded {len(parser.chats)} chats")
        messages = parser.get_messages_with_fallback(limit=5)
        print(f"✅ Retrieved {len(messages)} test messages")
    finally:
        parser.close()


def _doctor_spam(db_path: Path) -> None:
    for mode in SPAM_MODES[::-1]:
        print(f"\n--- {mode.title()} mode ---")
        parser = _open_imessage(db_path, spam_mode=mode)
        try:
            parser.get_messages_with_fallback(limit=1000)
            stats = parser.get_spam_filtering_stats()
            if stats:
                print(f"Messages suppressed: {stats['total_suppressed']:,}")
                for category, count in stats.items():
                    if category != "total_suppressed" and count > 0:
                        print(f"  - {category.replace('_', ' ').title()}: {count:,}")
            else:
                print("No spam filtering applied")
        finally:
            parser.close()
