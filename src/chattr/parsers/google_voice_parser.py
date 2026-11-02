"""Parser for a Google Voice Takeout (`Takeout/Voice/Calls/`).

Each HTML page in the folder is one text conversation, one call, or one voicemail, written with
microformat classes (`hChatLog`/`message`/`dt`/`sender vcard`/`tel`/`fn`/`<q>` for texts;
`haudio`/`published`/`duration`/`contributor vcard`/`full-text`/`<audio>` for calls and
voicemails). The kind in the file name (`<name> - Text|Received|Placed|Missed|Voicemail - <time>`)
is authoritative; the page's own wording is the fallback.

A conversation is the counterpart (or the set of counterparts of a group), so one person's texts,
calls and voicemails share one timeline. Times keep the offset the page states. "Me" is the owner:
owner records have no sender handle. Voicemail audio is stored by content hash when an
`attachments_dir` is given, whatever the file's extension.

The page is read with a small tree built on the standard library's `html.parser`.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator, Optional, Union

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
        for page in sorted(folder.rglob("*.html")):
            for counterparts, record in self._page(page):
                key = (
                    ",".join(sorted(p.number or p.name or "unknown" for p in counterparts))
                    or "unknown"
                )
                grouped.setdefault(key, []).append(
                    (counterparts[0] if counterparts else _Person(None, None), record)
                )
                bucket = names.setdefault(key, {})
                for person in counterparts:
                    bucket.setdefault(person.number or person.name or "unknown", person.name)
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
            return self._texts(log, page)
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

    def _texts(self, log: Node, page: Path) -> list[tuple[list[_Person], MessageRecord]]:
        entries = []
        for message in log.find_all(cls="message"):
            when = _moment((message.find(cls="dt") or Node("x")).attrs.get("title"))
            sender = _person(message.find(cls="sender"))
            quote = message.find("q")
            if when is None:
                continue
            entries.append((sender, when, quote.text().strip() if quote else "", message))
        counterparts: dict[str, _Person] = {}
        for sender, *_ in entries:
            if not sender.is_owner:
                counterparts.setdefault(sender.number or sender.name or "unknown", sender)
        if not counterparts:  # only the owner wrote: the file name says who it was to
            label = page.stem.split(" - ")[0]
            counterparts[label] = _Person(None, label)
        people = list(counterparts.values())
        result = []
        for sender, when, text, message in entries:
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
                        attachments=self._pictures(message, page),
                    ),
                )
            )
        return result

    def _pictures(self, message: Node, page: Path) -> tuple[AttachmentRecord, ...]:
        found = []
        for node in message.find_all("img") + message.find_all("a", "photo"):
            source = node.attrs.get("src") or node.attrs.get("href") or ""
            path = page.parent / source if source else None
            if path and path.is_file():
                found.append(self._file(path))
        return tuple(found)

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
