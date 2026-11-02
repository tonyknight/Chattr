"""Parser for Meta's "Download Your Information" message exports: Messenger and Instagram.

Both services write the same JSON: a thread folder holding `message_1.json` (and `message_2.json`,
… when the thread is long), newest message first, with `timestamp_ms` in UTC and every text value
double-encoded (UTF-8 bytes read as latin-1). Giving the parser `message_1.json` reads the whole
thread, oldest message first.

Each message is a dict: `timestamp` (aware UTC), `precision`, `author`, `message` (the text the
person wrote, or None), `type`, and where they apply `attachments` (each with `path`, `name`,
`kind`), `reactions` (`emoji`, `actor`), `shared_link` with `link_text`, `call_duration`, `unsent`.
After `parse`, `thread` holds the thread's title, participants and `thread_path`.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Optional

from chattr.parsers.base_parser import BaseParser

#: (JSON key, kind) for each list of attachments a message can hold.
_ATTACHMENT_LISTS = (
    ("photos", "image"),
    ("videos", "video"),
    ("audio_files", "audio"),
    ("files", "document"),
    ("gifs", "image"),
)


def _fix_facebook_encoding(text: Optional[str]) -> str:
    """Repair Meta's double-encoded strings; text that is already proper UTF-8 is left alone."""
    if text is None:
        return ""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean(text: Optional[str]) -> Optional[str]:
    return None if text is None else _fix_facebook_encoding(text)


class MessengerParser(BaseParser):
    """Parses exported Facebook Messenger chat .json files."""

    service = "Messenger"

    def __init__(self) -> None:
        self.thread: dict[str, Any] = {}

    # --- Reading ----------------------------------------------------------------------------

    @staticmethod
    def _thread_files(first: Path) -> list[Path]:
        """`message_1.json`, `message_2.json`, … in numeric order."""
        siblings = []
        for path in first.parent.glob("message_*.json"):
            number = re.fullmatch(r"message_(\d+)\.json", path.name)
            if number:
                siblings.append((int(number[1]), path))
        files = [path for _, path in sorted(siblings)]
        return files or [first]

    def parse(self, file_path):
        """Parse a Messenger/Instagram thread (`message_1.json`) into message dictionaries."""
        first = Path(file_path)
        raw: list[dict[str, Any]] = []
        self.thread = {}
        for index, path in enumerate(self._thread_files(first)):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except FileNotFoundError:
                print(f"Error: Input chat file not found at '{path}'.")
                return []
            except json.JSONDecodeError:
                print(f"Error: The file at '{path}' is not a valid JSON file.")
                return []
            if index == 0 or not self.thread:
                self.thread = {
                    "title": _clean(data.get("title")),
                    "participants": [
                        _fix_facebook_encoding(p.get("name")) for p in data.get("participants", [])
                    ],
                    "thread_path": data.get("thread_path") or first.parent.name,
                }
            raw.extend(reversed(data.get("messages", [])))  # each file is newest first

        messages = [m for m in (self._message(item, first) for item in raw) if m]
        messages.sort(key=lambda m: m["timestamp"])  # stable: equal times keep their order
        return messages

    # --- One message ------------------------------------------------------------------------

    def _message(self, data: dict[str, Any], first: Path) -> Optional[dict[str, Any]]:
        timestamp_ms = data.get("timestamp_ms")
        if not timestamp_ms:
            return None
        moment = datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.timezone.utc)
        content = _clean(data.get("content")) or None
        message: dict[str, Any] = {
            "timestamp": moment,
            "precision": "millisecond" if timestamp_ms % 1000 else "second",
            "author": _clean(data.get("sender_name")) or "Unknown",
            "message": content,
            "type": "text",
        }

        attachments = self._attachments(data, first)
        if attachments:
            message["attachments"] = attachments
            message["type"] = attachments[0]["kind"]
            if attachments[0].get("path"):
                message["attachment_path"] = attachments[0]["path"]  # single-file consumers

        share = data.get("share")
        if isinstance(share, dict) and share.get("link"):
            message["shared_link"] = share["link"]
            if share.get("share_text"):
                message["link_text"] = _fix_facebook_encoding(share["share_text"])

        reactions = [
            {
                "emoji": _fix_facebook_encoding(r.get("reaction")),
                "actor": _fix_facebook_encoding(r.get("actor")),
            }
            for r in data.get("reactions") or []
        ]
        if reactions:
            message["reactions"] = reactions

        if data.get("call_duration") is not None:
            message["type"] = "call"
            message["call_duration"] = data["call_duration"]
        if data.get("is_unsent"):
            message["unsent"] = True
        if data.get("type") in ("Subscribe", "Unsubscribe"):
            message["type"] = "system"
        return message

    @staticmethod
    def _root_for(uri: str, first: Path) -> Optional[Path]:
        """The folder `uri` is relative to: the export root, found by walking up from the thread."""
        for folder in [first.parent, *first.parents]:
            if (folder / uri).is_file():
                return folder
        return None

    def _attachments(self, data: dict[str, Any], first: Path) -> list[dict[str, Any]]:
        found = []
        entries: list[tuple[dict[str, Any], str]] = []
        for key, kind in _ATTACHMENT_LISTS:
            entries += [(item, kind) for item in data.get(key) or [] if isinstance(item, dict)]
        sticker = data.get("sticker")
        if isinstance(sticker, dict):
            entries.append((sticker, "sticker"))
        for item, kind in entries:
            uri = item.get("uri")
            if not uri:
                continue
            attachment: dict[str, Any] = {"kind": kind, "name": Path(uri).name}
            root = self._root_for(uri, first)
            if root:
                attachment["path"] = str(root / uri)
            found.append(attachment)
        return found


class InstagramParser(MessengerParser):
    """Instagram direct messages: the same export layout as Messenger."""

    service = "Instagram"
