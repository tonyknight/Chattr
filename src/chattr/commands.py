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
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

from chattr.config import Settings
from chattr.pages import render_pages
from chattr.people import Aliases, Contacts, PeopleResolver
from chattr.render import Renderer
from chattr.rebuild import RebuildError, RebuildResult, rebuild_database
from chattr.records import ConversationRecord
from chattr.transcription import Transcriber
from chattr.translation import (
    TranslationStats,
    TranslationUnavailable,
    Translator,
    make_translator,
    translate_archive,
)
from chattr.database_manager import DatabaseManager, ImportCounts, PeopleResult
from chattr.imessage_export import export_to_json, print_message_summary
from chattr.media_archive import MediaItem, archive_media
from chattr.importing import conversation_for, import_conversations, import_messages
from chattr.parsers.google_voice_parser import GoogleVoiceParser
from chattr.parsers.imessage_database_parser import iMessageDatabaseParser
from chattr.parsers.imessage_parser import IMessageParser
from chattr.parsers.mbox_parser import MboxParser
from chattr.parsers.messenger_parser import InstagramParser, MessengerParser
from chattr.parsers.voicemail_parser import DATABASE as VOICEMAIL_DATABASE, VoicemailParser
from chattr.parsers.whatsapp_parser import WhatsAppParser

PathLike = Union[str, Path]

#: Services converted through the common parser path: key → (display name, parser class).
SERVICES: dict[str, tuple[str, Any]] = {
    "whatsapp": ("WhatsApp", WhatsAppParser),
    "messenger": ("Messenger", MessengerParser),
    "instagram": ("Instagram", InstagramParser),
    "mail": ("Mail", MboxParser),
    "googlevoice": ("Google Voice", GoogleVoiceParser),
    "voicemail": ("Voicemail", VoicemailParser),
}

#: Services whose export is a folder, not a single file.
FOLDER_SERVICES = {"googlevoice", "voicemail"}

#: Services whose parsers return one batch per conversation (and keep attachments by hash).
BATCH_SERVICES = {"mail", "googlevoice", "voicemail"}

#: Output folder for each service (matches preflight's `Output/` tree).
OUTPUT_FOLDERS = {
    "whatsapp": "WhatsApp",
    "messenger": "Messenger",
    "instagram": "Instagram",
    "imessage": "iMessage",
    "mail": "Mail",
    "googlevoice": "GoogleVoice",
    "voicemail": "Voicemail",
}

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
    unresolved: int = 0  # handles of this service that no contact or alias could name


def central_database(settings: Settings) -> Path:
    """The central archive for this Chattr root: `<root>/Database/chattr.sqlite`."""
    return Path(settings.root) / "Database" / "chattr.sqlite"


def output_folder(settings: Settings, folder: str) -> Path:
    path = Path(settings.root) / "Output" / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_resolver(
    settings: Settings,
    owner: Optional[str] = None,
    aliases: Optional[PathLike] = None,
    contacts: Optional[PathLike] = None,
    region: Optional[str] = None,
) -> PeopleResolver:
    """The people resolver for these inputs, falling back to the settings.

    Contacts and region come from `global.contacts`, the owner from `global.owner`. A file that
    can't be read is a `CommandError`, so a mistyped path stops the run before anything is imported.
    """
    region = region or str(settings.get("global.contacts.phone_region", "US"))
    contacts_file = contacts or settings.get_expanded_path("global.contacts.vcf_file_path") or None
    try:
        return PeopleResolver(
            contacts=Contacts.from_vcf(contacts_file, region) if contacts_file else None,
            aliases=Aliases.from_json(aliases, region) if aliases else None,
            owner_name=owner or settings.get("global.owner.name", "") or None,
            owner_identifiers=settings.get("global.owner.identifiers", []) or [],
            region=region,
        )
    except (OSError, ValueError) as error:
        raise CommandError(str(error)) from error


def _resolve(db: DatabaseManager, resolver: PeopleResolver, service: str) -> int:
    """Link this service's handles to people; returns how many are still unnamed."""
    return len(db.resolve_people(resolver, service=service).unresolved)


def people(
    settings: Settings,
    owner: Optional[str] = None,
    aliases: Optional[PathLike] = None,
    contacts: Optional[PathLike] = None,
    region: Optional[str] = None,
) -> PeopleResult:
    """Resolve every handle in the central archive with these inputs; report what's unnamed."""
    resolver = make_resolver(settings, owner, aliases, contacts, region)
    if not central_database(settings).exists():
        return PeopleResult()
    with DatabaseManager(central_database(settings)) as db:
        return db.resolve_people(resolver)


def _apply_aliases(messages: list[dict[str, Any]], aliases: Optional[PathLike]) -> None:
    """Rename senders using a JSON file of {identifier or name: person name}."""
    if not aliases:
        return
    mapping = _aliases_mapping(aliases)
    for message in messages:
        author = message.get("author")
        if author in mapping:
            message["author"] = mapping[author]


def _conversation(
    service: str, export: Path, parser: Any, messages: list[dict[str, Any]]
) -> ConversationRecord:
    """The conversation an export holds.

    Exports that describe their thread (Messenger, Instagram) are keyed by its path and named by
    its title; the others by the export's folder or file name. More than two people make a group.
    """
    thread = getattr(parser, "thread", None) or {}
    if thread.get("thread_path"):
        participants = tuple(thread.get("participants") or ())
        conversation = ConversationRecord(
            service=service,
            source_id=thread["thread_path"],
            name=thread.get("title") or thread["thread_path"],
            participants=participants,
        )
        talking = max(len(participants), len({m["author"] for m in messages if m.get("author")}))
    else:
        conversation = conversation_for(service, export)
        talking = len({m["author"] for m in messages if m.get("author")})
    return replace(conversation, type="group") if talking > 2 else conversation


def _file_stem(conversation: ConversationRecord) -> str:
    """A conversation's name, safe to use in a file name."""
    return (
        re.sub(r'[\\/:*?"<>|]+', "-", conversation.name or conversation.source_id).strip() or "chat"
    )


def attachment_roots(settings: Settings, extra: Iterable[PathLike] = ()) -> list[Path]:
    """Folders the pages may link attachment files from: those given, the pages' own `Messages`
    folder (where the per-person attachment folders go) and each service's `Attachments` folder."""
    outputs = Path(settings.root) / "Output"
    return [
        *(Path(p) for p in extra),
        outputs / "Chattr" / "Messages",
        *sorted(outputs.glob("*/Attachments")),
    ]


def _render_db(db: DatabaseManager, out: Path, roots: Sequence[PathLike]) -> Optional[Path]:
    """Render everything in `db` under `out`; returns the index page (None when it holds nothing)."""
    renderer = Renderer(db)
    if not renderer.pages:
        return None
    render_pages(renderer, out, roots)
    return out / "index.html"


def _convert_batches(
    settings: Settings,
    key: str,
    export: Path,
    route: str,
    html: bool,
    owner: Optional[str],
    aliases: Optional[PathLike],
    attachments_dir: Optional[PathLike],
    transcriber: Optional[Transcriber],
    resolver: PeopleResolver,
) -> ConvertResult:
    """Import a service whose parser returns one batch per conversation (mail, Google Voice,
    voicemail).

    Attachments extracted from the export (mail attachments, voicemail audio) are kept by hash in
    `attachments_dir`, by default `<root>/Output/<Service>/Attachments`.
    """
    name, parser_class = SERVICES[key]
    folder = output_folder(settings, OUTPUT_FOLDERS[key])
    roots = attachment_roots(settings, [attachments_dir] if attachments_dir else [])
    options: dict[str, Any] = {"transcriber": transcriber} if key == "voicemail" else {}
    parser = parser_class(
        owner=owner,
        aliases=_aliases_mapping(aliases),
        attachments_dir=attachments_dir or folder / "Attachments",
        **options,
    )
    try:
        batches = parser.parse(export)
    except FileNotFoundError as error:
        raise CommandError(f"No {VOICEMAIL_DATABASE} found in {export}") from error
    except sqlite3.DatabaseError as error:
        raise CommandError(f"Can't read the {name} database in {export}: {error}") from error
    if not batches:
        raise CommandError(f"No {name} items found in {export}")
    if route == "central":
        with DatabaseManager(central_database(settings)) as db:
            counts = import_conversations(db, settings, name, export, batches)
            unresolved = _resolve(db, resolver, name)
            html_path = _render_db(db, output_folder(settings, "Chattr"), roots) if html else None
        return ConvertResult(name, counts, html_path=html_path, unresolved=unresolved)
    with DatabaseManager(":memory:") as db:
        counts = import_conversations(db, None, name, export, batches)
        unresolved = _resolve(db, resolver, name)
        json_path = folder / f"{export.stem if export.is_file() else OUTPUT_FOLDERS[key]}.json"
        db.export_json(name, json_path)
        html_path = _render_db(db, folder, roots) if html else None
    return ConvertResult(
        name, counts, json_path=json_path, html_path=html_path, unresolved=unresolved
    )


def _media_files(message: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """The files a parsed message carries, each with the dict that should record where it went."""
    if message.get("attachments"):
        return [(entry, entry["path"]) for entry in message["attachments"] if entry.get("path")]
    if message.get("attachment_path"):
        return [(message, message["attachment_path"])]
    return []


def _person_label(conversation: ConversationRecord) -> str:
    """Whose folder a conversation's files go in: the other person, or the group's name."""
    name = conversation.name or conversation.source_id
    for prefix in ("WhatsApp Chat with ", "WhatsApp Chat - "):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _archive_media(
    messages: list[dict[str, Any]],
    destination: PathLike,
    service: str,
    conversation: ConversationRecord,
) -> None:
    """Copy each message's media into the archive; record where it went on the message."""
    person, group = _person_label(conversation), conversation.type == "group"
    found = [
        (holder, path, m["timestamp"], m.get("author"))
        for m in messages
        for holder, path in _media_files(m)
    ]
    archived = archive_media(
        [MediaItem(path, when, sender, person, group) for _, path, when, sender in found],
        destination,
        service,
    )
    for holder, path, _, _ in found:
        info = archived.get(path)
        if info and info.archival_path and info.processing_status == "completed":
            holder["archival_path"] = info.archival_path


def convert(
    settings: Settings,
    service: str,
    export: PathLike,
    route: str = "central",
    html: bool = False,
    owner: Optional[str] = None,
    aliases: Optional[PathLike] = None,
    attachments_dir: Optional[PathLike] = None,
    transcriber: Optional[Transcriber] = None,
    contacts: Optional[PathLike] = None,
    region: Optional[str] = None,
) -> ConvertResult:
    """Convert one export along the chosen route.

    Afterwards the service's handles are linked to people (`contacts`: a vCard file, `aliases`, the
    owner from `owner` or the settings); `ConvertResult.unresolved` counts those still unnamed.

    `transcriber` (voicemail only) turns each voicemail's audio into a transcript locally.

    With `attachments_dir`, the media is also copied into that folder, renamed and dated by the
    message time (originals stay where they are).
    """
    key = service.lower()
    if key not in SERVICES:
        raise CommandError(f"Unknown service '{service}'. Choose one of: {', '.join(SERVICES)}.")
    if route not in ("central", "service"):
        raise CommandError("Route must be 'central' or 'service'.")
    export = Path(export)
    if key in FOLDER_SERVICES:
        if not export.exists():
            raise CommandError(f"Export folder not found: {export}")
        if not export.is_dir():
            raise CommandError(f"The {SERVICES[key][0]} export is a folder, not a file: {export}")
    elif not export.is_file():
        raise CommandError(f"Export file not found: {export}")
    name, parser_class = SERVICES[key]
    resolver = make_resolver(settings, owner, aliases, contacts, region)
    if key in BATCH_SERVICES:
        return _convert_batches(
            settings,
            key,
            export,
            route,
            html,
            owner,
            aliases,
            attachments_dir,
            transcriber,
            resolver,
        )
    parser = parser_class()
    messages = parser.parse(str(export))
    if not messages:
        raise CommandError(f"No messages found in {export}")
    _apply_aliases(messages, aliases)
    conversation = _conversation(name, export, parser, messages)
    if attachments_dir:
        _archive_media(messages, attachments_dir, name, conversation)
    roots = attachment_roots(settings, [attachments_dir] if attachments_dir else [])

    if route == "central":
        with DatabaseManager(central_database(settings)) as db:
            counts = import_messages(
                db, settings, name, export, messages, owner=owner, conversation=conversation
            )
            unresolved = _resolve(db, resolver, name)
            html_path = _render_db(db, output_folder(settings, "Chattr"), roots) if html else None
        return ConvertResult(name, counts, html_path=html_path, unresolved=unresolved)

    folder = output_folder(settings, OUTPUT_FOLDERS[key])
    with DatabaseManager(":memory:") as db:
        counts = import_messages(
            db, None, name, export, messages, owner=owner, conversation=conversation
        )
        unresolved = _resolve(db, resolver, name)
        json_path = folder / f"{_file_stem(conversation)}.json"
        db.export_json(name, json_path)
        html_path = _render_db(db, folder, roots) if html else None
    return ConvertResult(
        name, counts, json_path=json_path, html_path=html_path, unresolved=unresolved
    )


def generate_html(
    settings: Settings,
    out: Optional[PathLike] = None,
    attachments_dirs: Sequence[PathLike] = (),
) -> Optional[Path]:
    """Render the whole central archive as pages (see `chattr.render`); returns the index page.

    Returns None when the archive is empty. `attachments_dirs` are extra folders to link
    attachment files from, besides the usual ones.
    """
    if not central_database(settings).exists():
        return None
    with DatabaseManager(central_database(settings)) as db:
        return _render_db(
            db,
            Path(out) if out else output_folder(settings, "Chattr"),
            attachment_roots(settings, attachments_dirs),
        )


def rebuild(
    settings: Settings, site: Optional[PathLike] = None, db_path: Optional[PathLike] = None
) -> RebuildResult:
    """Recreate the database from the rendered pages (`chattr.rebuild`)."""
    folder = Path(site) if site else Path(settings.root) / "Output" / "Chattr"
    if not folder.is_dir():
        raise CommandError(f"No Chattr pages found: {folder} is not a folder.")
    try:
        result = rebuild_database(folder, db_path or central_database(settings))
    except RebuildError as error:
        raise CommandError(str(error)) from error
    return result


def translate(
    settings: Settings,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    service: Optional[str] = None,
    limit: Optional[int] = None,
    translator: Optional[Translator] = None,
) -> Optional[TranslationStats]:
    """Translate the central archive in place (see `chattr.translation`).

    Returns None when the archive doesn't exist yet. `translator` is for tests; otherwise it is
    built from the settings and the arguments (`model` turns translation on).
    """
    if translator is None:
        try:
            translator = make_translator(
                settings, endpoint=endpoint, model=model, enabled=True if model else None
            )
        except TranslationUnavailable as error:
            raise CommandError(str(error)) from error
        if translator is None:
            raise CommandError(
                "Translation is off. Give --model NAME (and --endpoint URL), or set "
                "global.ai_translation.enabled and .model in the settings."
            )
    if not central_database(settings).exists():
        return None
    with DatabaseManager(central_database(settings)) as db:
        return translate_archive(db, translator, service=service, limit=limit)


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


def _aliases_mapping(aliases: Optional[PathLike]) -> dict[str, str]:
    if not aliases:
        return {}
    try:
        mapping = json.loads(Path(aliases).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"Can't read the aliases file {aliases}: {error}") from error
    if not isinstance(mapping, dict):
        raise CommandError(f"The aliases file must contain a JSON object: {aliases}")
    return {str(key): str(value) for key, value in mapping.items()}


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
    route: str = "central",
    html: bool = False,
    owner: Optional[str] = None,
    aliases: Optional[PathLike] = None,
) -> ConvertResult:
    """Convert an iMessage database (`chat.db`) along the chosen route.

    - "central" (default): import every conversation into the central archive, once per message
      GUID, so running it again adds only what's new. `html` also writes a page per conversation.
    - "service": write iMessage's native JSON to `Output/iMessage/`; `attachments_dir` also
      copies attachments into an archive folder (the unified attachment pipeline is ticket 13).

    Unset options come from `services.imessage.preferences` (contacts and phone region from
    `global.contacts`).
    """
    if route not in ("central", "service"):
        raise CommandError("Route must be 'central' or 'service'.")
    if route == "service" and (html or owner or aliases):
        raise CommandError(
            "HTML, owner and aliases need the central archive; the native JSON route has none."
        )
    if route == "central" and attachments_dir:
        raise CommandError(
            "Archival attachment copies are made with the JSON route until the unified "
            "attachment pipeline is built."
        )
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
    db_path = Path(db_path).expanduser()

    if route == "central":
        return _import_imessage(
            settings,
            db_path,
            IMessageParser(
                contacts=contacts,
                region=str(region),
                attachment_root=attachment_root or None,
                spam_mode=str(spam_mode),
                workers=workers,
                batch_size=batch_size,
                chat=chat,
                limit=limit,
                owner=owner,
                aliases=_aliases_mapping(aliases),
            ),
            html,
            verbose,
            make_resolver(settings, owner, aliases, contacts, region),
        )

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


def _import_imessage(
    settings: Settings,
    db_path: Path,
    parser: IMessageParser,
    html: bool,
    verbose: bool,
    resolver: PeopleResolver,
) -> ConvertResult:
    if not db_path.is_file():
        raise CommandError(f"iMessage database not found: {db_path}")
    try:
        batches = parser.parse(db_path)
    except PermissionError as error:  # the reader's message explains macOS Full Disk Access
        raise CommandError(str(error)) from error
    except sqlite3.DatabaseError as error:
        raise CommandError(f"Can't read the iMessage database {db_path}: {error}") from error
    if not batches:
        raise CommandError(f"No messages found in {db_path}")
    stats = parser.spam_stats
    if stats and stats.get("total_suppressed"):
        print(f"Spam filtering flagged {stats['total_suppressed']:,} messages (kept, tagged).")
    if verbose:
        for batch in batches:
            name = batch.conversation.name or batch.conversation.source_id
            print(f"  {name}: {len(batch.records):,} messages")
    html_path = None
    with DatabaseManager(central_database(settings)) as db:
        counts = import_conversations(db, settings, "iMessage", db_path, batches)
        unresolved = _resolve(db, resolver, "iMessage")
        if html:
            html_path = _render_db(
                db, output_folder(settings, "Chattr"), attachment_roots(settings)
            )
    return ConvertResult("iMessage", counts, html_path=html_path, unresolved=unresolved)


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
