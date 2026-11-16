"""Facebook's friends lists, from a Meta "Download your information" export.

The export lists the owner's friends and the people they have unfriended, by name. A Messenger
thread in which nobody is on either list is, almost always, someone the owner never knew:
Marketplace buyers and sellers, page bots, strangers. Keeping only threads with someone on the
lists cuts that out.

Meta has moved the files between versions of the export:

- `connections/friends/your_friends.json` and `removed_friends.json` (`friends_v2`,
  `deleted_friends_v2`);
- `friends_and_followers/friends.json` and `removed_friends.json` (the same keys);
- `friends/friends.json` and `removed_friends.json` (`friends`, `deleted_friends`).

Names are double-encoded like the rest of the export (UTF-8 read as latin-1) and are repaired.
Friend requests, sent or received, are not friends and are not read.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from chattr.parsers.messenger_parser import DEACTIVATED, _fix_facebook_encoding

PathLike = Union[str, Path]

#: Where the lists are, relative to the export's top folder, newest layout first.
FOLDERS = ("connections/friends", "friends_and_followers", "friends")
#: The files that hold friends (the owner's, then those they removed).
FILES = ("your_friends.json", "friends.json", "removed_friends.json")
#: How far up from a thread to look for the export's top folder.
_LEVELS = 8


def _key(name: Optional[str]) -> str:
    text = unicodedata.normalize("NFC", _fix_facebook_encoding(name or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class Friends:
    """The names on the friends lists, matched without regard to case or spacing."""

    names: dict[str, str] = field(default_factory=dict)  # matching key → the name as written

    @classmethod
    def of(cls, names: Iterable[str]) -> "Friends":
        friends = cls()
        for name in names:
            friends.add(name)
        return friends

    def add(self, name: Optional[str]) -> None:
        key = _key(name)
        if key:
            self.names.setdefault(key, unicodedata.normalize("NFC", _fix_facebook_encoding(name)))

    def knows(self, name: Optional[str]) -> bool:
        return _key(name) in self.names

    def name_of(self, name: Optional[str]) -> Optional[str]:
        return self.names.get(_key(name))

    def in_conversation(self, conversation: Any) -> bool:
        """True when someone in the conversation is on the lists, or is an account that was
        deactivated (it may have been a friend; the owner decides who it was). A thread that names
        nobody is kept: there is no telling who it was with."""
        people = list(getattr(conversation, "participants", ()) or ())
        return not people or any(self.knows(person) or _deactivated(person) for person in people)

    def __len__(self) -> int:
        return len(self.names)


def _deactivated(name: Optional[str]) -> bool:
    """`Facebook user` or `Facebook user (<thread>)`: an account Meta no longer names."""
    text = (name or "").strip().casefold()
    return any(text == word or text.startswith(word + " (") for word in DEACTIVATED)


def find_friend_files(start: PathLike) -> list[Path]:
    """The friends files for an export.

    `start` may be a thread (`message_1.json`) or any folder inside the export: the folders above
    it are tried, at the known places only, until the export's top folder is found. `start` may
    also be a friends file (its removed-friends file beside it is taken too) or a folder holding
    the lists.
    """
    start = Path(start)
    if start.is_file() and start.suffix.lower() == ".json" and start.name in FILES:
        return sorted(p for p in (start.parent / name for name in FILES) if p.is_file())
    if start.is_dir():
        inside = sorted(p for p in start.rglob("*.json") if p.name in FILES and _a_list(p))
        if inside:
            return inside
    for folder in [start, *start.parents][:_LEVELS]:
        for place in FOLDERS:
            found = sorted(p for p in (folder / place / n for n in FILES) if p.is_file())
            if found:
                return found
    return []


def _a_list(path: Path) -> bool:
    """A file named like a friends list that sits in a friends folder (not, say, an app's own)."""
    return any(path.parent.as_posix().endswith(place) for place in FOLDERS)


def load_friends(files: Iterable[PathLike]) -> Friends:
    """Everyone on these lists; a file that cannot be read is skipped."""
    friends = Friends()
    for file in files:
        try:
            data = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in _entries(data):
            friends.add(entry.get("name"))
    return friends


def _entries(data: Any) -> list[dict[str, Any]]:
    """The people in one file: a list of `{name, timestamp}`, either the whole file or under a
    key that names friends (never a key that names requests)."""
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)]
    found: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if "friend" in key.lower() and "request" not in key.lower():
                found.extend(_entries(value))
    return found
