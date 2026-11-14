"""Parser for a Google Voice Takeout (`Takeout/Voice/Calls/`).

Each HTML page in the folder is one text conversation, one call, or one voicemail, written with
microformat classes (`hChatLog`/`message`/`dt`/`sender vcard`/`tel`/`fn`/`<q>` for texts;
`haudio`/`published`/`duration`/`contributor vcard`/`full-text`/`<audio>` for calls and
voicemails). The kind in the file name (`<name> - Text|Received|Placed|Missed|Voicemail - <time>`)
is authoritative; the page's own wording is the fallback.

A conversation is the counterpart (or the set of counterparts of a group), so one person's texts,
calls and voicemails share one timeline. Google writes one page per day of texting, so:

- a day on which only the owner wrote names nobody inside the page; the counterpart comes from the
  file name and is matched to the number that name has on other pages;
- a group page lists its members (`participants`), and those, not whoever happened to write that
  day, are the conversation;
- pictures and videos sent by text sit beside the page (`<page name>-<n>-<m>.jpg`) and the page
  links them without the extension; "MMS Received"/"MMS Sent" is Google's placeholder for a
  message that was only a picture, not something anyone said;
- a voicemail recording whose page is missing is imported from its file name alone.
 Times keep the offset the page states. "Me" is the owner:
owner records have no sender handle. Voicemail audio is stored by content hash when an
`attachments_dir` is given, whatever the file's extension.

The page is read with a small tree built on the standard library's `html.parser`.
"""

from __future__ import annotations

import dataclasses
import glob
import hashlib
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator, Optional, Union
from urllib.parse import unquote

from chattr import blobs
from chattr.parsers.base_parser import BaseParser
from chattr.parsers.batch import ConversationBatch
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord

SERVICE = "Google Voice"
PathLike = Union[str, Path]

_KINDS = {
    "text": "text",
    "received": "received",
    "placed": "placed",
    "missed": "missed",
    "voicemail": "voicemail",
}
_LABELS = (
    ("placed call", "placed"),
    ("received call", "received"),
    ("missed call", "missed"),
    ("voicemail", "voicemail"),
)
_AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".amr", ".opus")
_PLACEHOLDERS = {"mms received", "mms sent"}
_NUMBER = re.compile(r"^\+?[\d\s().-]{6,}$")
_STAMP = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d)_(\d\d)_(\d\d)Z$")
_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration(text: str) -> Optional[int]:
    """An ISO 8601 duration (`PT5M12S`) in whole seconds; None when it isn't one."""
    match = _DURATION.match(text.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


# --- A tiny DOM -----------------------------------------------------------------------------


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Union["Node", str]] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag == "br":
                parts.append("\n")
            else:
                parts.append(child.text())
        return "".join(parts)

    def walk(self) -> Iterator["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, tag: Optional[str] = None, cls: Optional[str] = None) -> list["Node"]:
        return [
            n
            for n in self.walk()
            if (tag is None or n.tag == tag) and (cls is None or cls in n.classes)
        ]

    def find(self, tag: Optional[str] = None, cls: Optional[str] = None) -> Optional["Node"]:
        found = self.find_all(tag, cls)
        return found[0] if found else None


class _Builder(HTMLParser):
    VOID = {"br", "img", "meta", "link", "input", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _tree(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    return builder.root


def _moment(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass
class _Person:
    number: Optional[str]
    name: Optional[str]

    @property
    def is_owner(self) -> bool:
        return (self.name or "").strip().lower() == "me"


def _person(vcard: Optional[Node]) -> _Person:
    if vcard is None:
        return _Person(None, None)
    link = vcard.find("a", "tel")
    number = (link.attrs.get("href", "") if link else "").removeprefix("tel:").strip() or None
    fn = vcard.find(cls="fn")
    name = fn.text().strip() if fn else None
    if name and name == number:
        name = None  # an unknown number is not a name
    return _Person(number, name or None)


def _with_sender(record: MessageRecord, number: Optional[str]) -> MessageRecord:
    return dataclasses.replace(record, sender=number) if number else record


class GoogleVoiceParser(BaseParser):
    """Parse a Google Voice Takeout folder into conversations of texts, calls and voicemails."""

    def __init__(
        self,
        owner: Optional[str] = None,
        aliases: Optional[dict[str, str]] = None,
        attachments_dir: Optional[PathLike] = None,
    ) -> None:
        self.owner = owner
        self.aliases = aliases or {}
        self.attachments_dir = Path(attachments_dir) if attachments_dir else None

    # --- BaseParser -------------------------------------------------------------------------

    def parse(self, file_path: PathLike) -> list[ConversationBatch]:
        folder = Path(file_path)
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        grouped: dict[str, list[tuple[_Person, MessageRecord]]] = {}
        names: dict[str, dict[str, Optional[str]]] = {}
        self._used: set[Path] = set()
        found: list[tuple[list[_Person], MessageRecord]] = []
        for page in sorted(folder.rglob("*.html")):
            found.extend(self._page(page))
        found.extend(self._loose_voicemail(folder))
        # A name that always goes with one number stands for that number where only the name is
        # known (a page on which only the owner wrote).
        numbers: dict[str, set[str]] = {}
        for counterparts, _ in found:
            for person in counterparts:
                if person.number and person.name:
                    numbers.setdefault(person.name.casefold(), set()).add(person.number)
        for counterparts, record in found:
            for person in counterparts:
                if person.number is None and person.name:
                    known = numbers.get(person.name.casefold(), set())
                    if _NUMBER.match(person.name):
                        person.number, person.name = re.sub(r"[^\d+]", "", person.name), None
                    elif len(known) == 1:
                        person.number = next(iter(known))
                    if record.content_type == "voicemail" and record.sender is None:
                        record = _with_sender(record, person.number)
            key = (
                ",".join(sorted(p.number or p.name or "unknown" for p in counterparts)) or "unknown"
            )
            grouped.setdefault(key, []).append(
                (counterparts[0] if counterparts else _Person(None, None), record)
            )
            bucket = names.setdefault(key, {})
            for person in counterparts:
                who = person.number or person.name or "unknown"
                bucket[who] = bucket.get(who) or person.name
        batches = []
        for key, items in grouped.items():
            records = sorted((r for _, r in items), key=lambda r: r.timestamp)
            people = names[key]
            labels = [self._name(number, name) or number for number, name in people.items()]
            batches.append(
                ConversationBatch(
                    ConversationRecord(
                        service=SERVICE,
                        source_id=key,
                        type="group" if len(people) > 1 else "individual",
                        name=", ".join(label for label in labels if label) or key,
                        participants=tuple(people),
                    ),
                    records,
                )
            )
        return sorted(batches, key=lambda b: b.records[0].timestamp)

    # --- One page ---------------------------------------------------------------------------

    def _page(self, page: Path) -> list[tuple[list[_Person], MessageRecord]]:
        try:
            tree = _tree(page.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []
        hint = self._kind_from_name(page)
        log = tree.find(cls="hChatLog")
        if log is not None:
            return self._texts(log, page, tree.find(cls="participants"))
        audio = tree.find(cls="haudio")
        if audio is not None:
            return self._call(audio, page, hint)
        return []

    @staticmethod
    def _kind_from_name(page: Path) -> Optional[str]:
        parts = page.stem.split(" - ")
        return _KINDS.get(parts[-2].strip().lower()) if len(parts) >= 3 else None

    def _name(self, number: Optional[str], name: Optional[str]) -> Optional[str]:
        return self.aliases.get(number or "") or self.aliases.get(name or "") or name

    def _owner_name(self) -> str:
        return self.owner or "Me"

    def _texts(
        self, log: Node, page: Path, members: Optional[Node] = None
    ) -> list[tuple[list[_Person], MessageRecord]]:
        entries = []
        for message in log.find_all(cls="message"):
            when = _moment((message.find(cls="dt") or Node("x")).attrs.get("title"))
            sender = _person(message.find(cls="sender"))
            quote = message.find("q")
            if when is None:
                continue
            entries.append((sender, when, quote.text().strip() if quote else "", message))
        counterparts: dict[str, _Person] = {}
        for card in members.find_all(cls="vcard") if members is not None else []:
            member = _person(card)  # a group's members, whoever wrote on this page
            if not member.is_owner and (member.number or member.name):
                counterparts.setdefault(member.number or member.name or "unknown", member)
        for sender, *_ in entries:
            if not sender.is_owner:
                counterparts.setdefault(sender.number or sender.name or "unknown", sender)
        if not counterparts:  # only the owner wrote: the file name says who it was to
            label = page.stem.split(" - ")[0]
            counterparts[label] = _Person(None, label)
        people = list(counterparts.values())
        result = []
        for sender, when, text, message in entries:
            files = self._pictures(message, page)
            if files and text.strip().casefold() in _PLACEHOLDERS:
                text = ""
            result.append(
                (
                    people,
                    MessageRecord(
                        timestamp=when,
                        sender=None if sender.is_owner else sender.number,
                        sender_name=(
                            self._owner_name()
                            if sender.is_owner
                            else self._name(sender.number, sender.name)
                        ),
                        body=text or None,
                        content_type="text",
                        is_from_owner=sender.is_owner,
                        attachments=files,
                    ),
                )
            )
        return result

    def _pictures(self, message: Node, page: Path) -> tuple[AttachmentRecord, ...]:
        """The pictures, videos and other files a text carried."""
        found = []
        nodes = message.find_all("img") + message.find_all("a", "photo")
        nodes += message.find_all("a", "video") + message.find_all("a", "vcard")
        nodes += message.find_all("audio")
        for node in nodes:
            source = node.attrs.get("src") or node.attrs.get("href") or ""
            path = self._beside(page, source)
            if path and path not in self._used:
                self._used.add(path)
                found.append(self._file(path))
        return tuple(found)

    @staticmethod
    def _beside(page: Path, source: str) -> Optional[Path]:
        """The file a page links: Google leaves the extension off, and may escape the name."""
        if not source or "://" in source:
            return None
        for name in dict.fromkeys((source, unquote(source))):
            exact = page.parent / name
            if exact.is_file():
                return exact
            matches = sorted(
                f
                for f in page.parent.glob(glob.escape(name) + ".*")
                if f.is_file() and f.suffix.lower() != ".html"
            )
            if matches:
                return matches[0]
        return None

    def _loose_voicemail(self, folder: Path) -> list[tuple[list[_Person], MessageRecord]]:
        """Voicemail recordings that no page carried, read from their names:
        `Mary Afraimi - Voicemail - 2021-01-06T18_05_49Z.mp3`."""
        result = []
        for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() in _AUDIO_SUFFIXES):
            parts = path.stem.split(" - ")
            stamp = _STAMP.match(parts[-1]) if len(parts) >= 3 else None
            if path in self._used or not stamp or parts[-2].strip().lower() != "voicemail":
                continue
            when = _moment(f"{stamp[1]}T{stamp[2]}:{stamp[3]}:{stamp[4]}+00:00")
            label = " - ".join(parts[:-2]).strip()
            if when is None or not label:
                continue
            self._used.add(path)
            caller = _Person(None, label)
            record = MessageRecord(
                timestamp=when,
                sender=None,
                sender_name=self._name(None, label),
                content_type="voicemail",
                attachments=(self._file(path),),
            )
            result.append(([caller], record))
        return result

    def _call(
        self, audio: Node, page: Path, hint: Optional[str]
    ) -> list[tuple[list[_Person], MessageRecord]]:
        label = (audio.find(cls="fn") or Node("x")).text().strip()
        kind = hint if hint and hint != "text" else None
        if kind is None:
            lowered = label.lower()
            kind = next((k for prefix, k in _LABELS if lowered.startswith(prefix)), None)
        if kind is None:
            return []
        when = _moment((audio.find(cls="published") or Node("x")).attrs.get("title"))
        if when is None:
            return []
        contributor = _person(audio.find(cls="contributor"))
        duration = parse_duration((audio.find(cls="duration") or Node("x")).attrs.get("title", ""))
        extras: dict[str, Any] = {}
        if kind == "voicemail":
            transcript = audio.find(cls="full-text")
            extras = {"duration_seconds": duration}
            record = MessageRecord(
                timestamp=when,
                sender=contributor.number,
                sender_name=self._name(contributor.number, contributor.name),
                transcript=transcript.text().strip() if transcript else None,
                content_type="voicemail",
                service_specific={k: v for k, v in extras.items() if v is not None},
                attachments=self._voicemail_audio(audio, page),
            )
        else:
            from_owner = kind == "placed"
            extras = {"direction": kind, "duration_seconds": duration}
            record = MessageRecord(
                timestamp=when,
                sender=None if from_owner else contributor.number,
                sender_name=(
                    self._owner_name()
                    if from_owner
                    else self._name(contributor.number, contributor.name)
                ),
                content_type="call",
                is_from_owner=from_owner,
                service_specific={k: v for k, v in extras.items() if v is not None},
            )
        return [([contributor], record)]

    def _voicemail_audio(self, audio: Node, page: Path) -> tuple[AttachmentRecord, ...]:
        element = audio.find("audio")
        source = (element.attrs.get("src") if element else None) or ""
        if not source and element:
            link = element.find("a")
            source = link.attrs.get("href", "") if link else ""
        candidates = [page.parent / source] if source else []
        candidates += [page.with_suffix(suffix) for suffix in _AUDIO_SUFFIXES]
        for path in candidates:
            if path.is_file():
                self._used.add(path)
                return (self._file(path),)
        return ()

    def _file(self, path: Path) -> AttachmentRecord:
        data = path.read_bytes()
        if self.attachments_dir:
            digest, relative = blobs.store_by_hash(self.attachments_dir, data, path.suffix)
        else:
            digest, relative = hashlib.sha256(data).hexdigest(), None
        return AttachmentRecord(
            digest, path.name, mimetypes.guess_type(path.name)[0], len(data), relative
        )
