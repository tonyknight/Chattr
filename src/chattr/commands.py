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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from chattr.config import Settings
from chattr.database_manager import DatabaseManager, ImportCounts
from chattr.html_generator import HTMLGenerator
from chattr.importing import conversation_for, import_messages
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
    mapping = json.loads(Path(aliases).read_text(encoding="utf-8"))
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
