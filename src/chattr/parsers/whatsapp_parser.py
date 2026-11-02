"""Parser for WhatsApp's "Export chat" text files (iOS, Android and locale variants).

Three layouts are understood (the first two numbers of a date may be day/month or month/day):

    [2024-03-01, 14:05:09] Name: text          iOS (ISO date); bracketed locale dates too
    15/04/2024, 10:01 - Name: text             Android, 24-hour
    2/9/25, 9:05 AM - Name: text               US locale, 12-hour (AM/PM, often after U+202F)

Day/month order is decided from the dates in the file (a first number above 12 means day-first, a
second number above 12 means month-first). When every date is ambiguous the parser assumes
month-first for 12-hour clocks (the US layout) and day-first otherwise, and records that in
`date_order_certain`.

Each message is a dict: `timestamp` (naive: the export carries no time zone), `author` (None for
system notices), `message`, `type`, `precision`, and where they apply `attachment_path` and
`omitted`. Continuation lines keep their indentation. Media placeholders ("image omitted",
"<Media omitted>") stay as messages. Placeholder and system-notice wording is recognized in English
only so far.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Optional

from chattr.parsers.base_parser import BaseParser

MARKS = "‎‏‪‫‬"

_IOS_START = re.compile(rf"^[{MARKS}]*\[(?P<stamp>[^\]]+)\]\s+(?P<rest>.*)$")
_ANDROID_START = re.compile(
    rf"^[{MARKS}]*(?P<stamp>\d{{1,4}}[/.\-]\d{{1,2}}[/.\-]\d{{1,4}},?\s+\d{{1,2}}[:.]\d{{2}}"
    r"(?:[:.]\d{2})?(?:[\s ]*[AaPp]\.?[Mm]\.?)?)\s+-\s+(?P<rest>.*)$"
)
_STAMP = re.compile(
    r"^(?P<a>\d{1,4})[/.\-](?P<b>\d{1,2})[/.\-](?P<c>\d{1,4}),?\s+"
    r"(?P<hour>\d{1,2})[:.](?P<minute>\d{2})(?:[:.](?P<second>\d{2}))?"
    r"(?:[\s ]*(?P<meridiem>[AaPp])\.?[Mm]\.?)?$"
)
_ATTACHED_IOS = re.compile(rf"^[{MARKS}]*<attached:\s*(?P<name>.+?)>$")
_ATTACHED_ANDROID = re.compile(rf"^[{MARKS}]*(?P<name>.+?)\s+\(file attached\)$")
_OMITTED = re.compile(
    r"^<?(?P<kind>media|image|video|audio|sticker|gif|document|contact card)\s+omitted>?$",
    re.IGNORECASE,
)
_SYSTEM_NOTICES = ("messages and calls are end-to-end encrypted",)

_KINDS = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image",
    ".heic": "image", ".mp4": "video", ".mov": "video", ".3gp": "video", ".avi": "video",
    ".opus": "audio", ".m4a": "audio", ".aac": "audio", ".mp3": "audio", ".ogg": "audio",
    ".caf": "audio", ".pdf": "document", ".docx": "document", ".xlsx": "document",
    ".vcf": "document", ".txt": "document",
}  # fmt: skip
_OMITTED_KINDS = {"media": "media", "gif": "image", "contact card": "document"}


class WhatsAppParser(BaseParser):
    """Parses exported WhatsApp chat .txt files."""

    def __init__(self) -> None:
        self.date_order: Optional[str] = None  # "iso" | "day-first" | "month-first"
        self.date_order_certain = True

    # --- Dates ------------------------------------------------------------------------------

    def _decide_order(self, stamps: list[str]) -> None:
        day_first = month_first = twelve_hour = False
        iso = bool(stamps)
        for stamp in stamps:
            match = _STAMP.match(stamp.strip())
            if not match:
                continue
            twelve_hour = twelve_hour or bool(match["meridiem"])
            if len(match["a"]) == 4:
                continue
            iso = False
            day_first = day_first or int(match["a"]) > 12
            month_first = month_first or int(match["b"]) > 12
        if iso:
            self.date_order, self.date_order_certain = "iso", True
        elif day_first != month_first:
            self.date_order, self.date_order_certain = (
                "day-first" if day_first else "month-first",
                True,
            )
        else:  # all ambiguous (or contradictory): the 12-hour clock marks the US layout
            self.date_order = "month-first" if twelve_hour else "day-first"
            self.date_order_certain = False

    def _parse_stamp(self, text: str) -> Optional[tuple[datetime.datetime, str]]:
        match = _STAMP.match(text.strip())
        if not match:
            return None
        first, second, third = match["a"], int(match["b"]), match["c"]
        if len(first) == 4:
            year, month, day = int(first), second, int(third)
        else:
            a, b = int(first), second
            day, month = (a, b) if self.date_order == "day-first" else (b, a)
            year = int(third) + (2000 if len(third) <= 2 else 0)
        hour = int(match["hour"])
        if match["meridiem"]:
            hour = hour % 12 + (12 if match["meridiem"].lower() == "p" else 0)
        has_seconds = match["second"] is not None
        try:
            moment = datetime.datetime(
                year, month, day, hour, int(match["minute"]), int(match["second"] or 0)
            )
        except ValueError:
            return None
        return moment, "second" if has_seconds else "minute"

    # --- Parsing ----------------------------------------------------------------------------

    def parse(self, file_path):
        """Parse a WhatsApp chat .txt export into standardized message dictionaries."""
        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            print(f"Error: Input chat file not found at '{file_path}'.")
            return []
        except Exception as error:
            print(f"An unexpected error occurred while parsing the chat file: {error}")
            return []

        starts = [self._match_start(line) for line in lines]
        self._decide_order([m[0] for m in starts if m])
        if self.date_order and not self.date_order_certain:
            print(f"Note: the dates in {file_path} are ambiguous; reading them {self.date_order}.")

        base = Path(file_path).parent
        messages: list[dict[str, Any]] = []
        current: Optional[dict[str, Any]] = None
        for line, start in zip(lines, starts):
            parsed = self._parse_stamp(start[0]) if start else None
            if start and parsed:
                if current:
                    messages.append(self._finish(current, base))
                current = {
                    "timestamp": parsed[0],
                    "precision": parsed[1],
                    "rest": start[1],
                }
            elif current is not None:
                current["rest"] += "\n" + line
        if current:
            messages.append(self._finish(current, base))
        return messages

    @staticmethod
    def _match_start(line: str) -> Optional[tuple[str, str]]:
        for pattern in (_IOS_START, _ANDROID_START):
            match = pattern.match(line)
            if match:
                return match["stamp"], match["rest"]
        return None

    def _finish(self, raw: dict[str, Any], base: Path) -> dict[str, Any]:
        rest: str = raw["rest"]
        author: Optional[str]
        author, separator, body = rest.partition(": ")
        if not separator:  # a notice with no sender (Android)
            author, body = None, rest
        body = body.lstrip(MARKS + " ").rstrip()
        message: dict[str, Any] = {
            "timestamp": raw["timestamp"],
            "precision": raw["precision"],
            "author": author.strip().lstrip(MARKS) if author else None,
            "message": body,
            "type": "text",
        }
        first_line = body.split("\n", 1)[0]
        attached = _ATTACHED_IOS.match(first_line) or _ATTACHED_ANDROID.match(first_line)
        omitted = _OMITTED.match(body)
        if attached and "\n" not in body:
            name = attached["name"].strip(MARKS + " ")
            path = base / name
            message["type"] = _KINDS.get(Path(name).suffix.lower(), "media")
            message["attachment_name"] = name
            message["message"] = None
            if path.is_file():
                message["attachment_path"] = str(path)
        elif omitted:
            kind = omitted["kind"].lower()
            message["type"] = _OMITTED_KINDS.get(kind, kind)
            message["omitted"] = True
        elif body.lower().startswith(_SYSTEM_NOTICES):
            message["type"] = "system"
            message["author"] = None
        return message
