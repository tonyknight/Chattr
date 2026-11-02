"""Parser for MBOX mail (Gmail Takeout and any other mbox file).

Reads the file with the standard library `mailbox` and `email` modules under the default policy,
which decodes RFC 2047 headers and body charsets. Each mail becomes a `MessageRecord` of content
type `email`:

- `sender` is the address, `sender_name` the decoded display name;
- `body` is the plain text (derived from the HTML when the mail is HTML-only); the HTML stays in
  `service_specific["body_html"]`;
- the time keeps the sender's own UTC offset from the `Date` header; a missing or unparseable date
  falls back to the mbox `From ` line (UTC) and is marked assumed;
- `source_id` is the normalized Message-ID (a hash of the headers when there is none);
- `mail_headers` keeps the message id, in-reply-to, references, Gmail thread id and labels,
  subject, and the to/cc/bcc lists.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import mailbox
import mimetypes
import re
from datetime import datetime, timezone
from email.message import EmailMessage, MIMEPart
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional, Union

from chattr import blobs
from chattr.parsers.base_parser import BaseParser
from chattr.parsers.batch import ConversationBatch
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord

SERVICE = "Mail"
PathLike = Union[str, Path]
_FROM_LINE_TIME = re.compile(r"^From \S+\s+(?P<when>.+?)\s*$")


def normalize_message_id(value: Optional[str]) -> Optional[str]:
    """`<Id@Host>` → `id@host`: no brackets or spaces, lower case."""
    if not value:
        return None
    match = re.search(r"<([^<>]+)>", value)
    cleaned = (match.group(1) if match else value).strip().lower()
    return cleaned or None


def _message_ids(value: Optional[str]) -> list[str]:
    """Every message id in a References or In-Reply-To header, in order."""
    if not value:
        return []
    found = re.findall(r"<([^<>]+)>", str(value))
    return [normalized for i in found if (normalized := normalize_message_id(i))]


class _TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Readable plain text from HTML: block tags become line breaks, whitespace is collapsed."""
    extractor = _TextExtractor()
    extractor.feed(html)
    text = "".join(extractor.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _people(message: EmailMessage, header: str) -> list[dict[str, str]]:
    values = [str(v) for v in message.get_all(header, [])]
    return [
        {"name": name, "address": address.lower()}
        for name, address in getaddresses(values)
        if address
    ]


def _body(part: Optional[MIMEPart]) -> Optional[str]:
    if part is None:
        return None
    try:
        return str(part.get_content())
    except (LookupError, UnicodeError):  # an unknown charset: decode loosely rather than fail
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""


_PREFIX = re.compile(r"^\s*(?:re|fwd?|aw|sv|vs|res|rv|enc)\s*:\s*", re.IGNORECASE)


def _strip_prefixes(subject: str) -> str:
    """`Re: Fwd: Dinner?` → `Dinner?`"""
    previous = None
    while previous != subject:
        previous, subject = subject, _PREFIX.sub("", subject)
    return subject.strip()


def _attachment_parts(message: EmailMessage) -> list[MIMEPart]:
    """The parts of a mail that are files (nested multiparts included), not its body."""
    parts: list[MIMEPart] = []
    for part in message.iter_attachments():
        if part.is_multipart() and part.get_content_type() != "message/rfc822":
            parts.extend(_attachment_parts(part))
        else:
            parts.append(part)
    return parts


class MboxParser(BaseParser):
    """Parse an MBOX file into conversations of mail."""

    def __init__(
        self,
        owner: Optional[str] = None,
        aliases: Optional[dict[str, str]] = None,
        attachments_dir: Optional[PathLike] = None,
    ):
        self.owner = (owner or "").strip().lower()
        self.aliases = aliases or {}
        #: Where attachments are kept, by hash. Without it they are hashed but not saved.
        self.attachments_dir = Path(attachments_dir) if attachments_dir else None

    # --- BaseParser -------------------------------------------------------------------------

    def parse(self, file_path: PathLike) -> list[ConversationBatch]:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        records: dict[str, MessageRecord] = {}
        box = mailbox.mbox(str(path), create=False)
        try:
            for key in box.keys():
                message = email.message_from_bytes(box.get_bytes(key), policy=email.policy.default)
                assert isinstance(message, EmailMessage)
                record = self._record(message, box.get_message(key).get_from())
                existing = records.get(record.source_id or "")
                records[record.source_id or ""] = (
                    self._merge(existing, record) if existing else record
                )
        finally:
            box.close()
        return self._threads(sorted(records.values(), key=lambda r: r.timestamp))

    # --- Threads ----------------------------------------------------------------------------

    @staticmethod
    def _thread_key(record: MessageRecord, known: dict[str, MessageRecord]) -> str:
        """The thread a mail belongs to.

        Gmail's own thread id when the mail has one. Otherwise the root of its
        References/In-Reply-To chain: `References` starts at the thread's first mail, and the root
        stays the same even if that mail isn't in the file. A chain through known mails is
        followed to its top (cycles stop it).
        """
        headers = record.mail_headers
        if headers.get("thread_id"):
            return f"gmail:{headers['thread_id']}"
        references = headers.get("references") or []
        key = references[0] if references else (headers.get("in_reply_to") or record.source_id)
        seen: set[str] = set()
        while key in known and key not in seen:
            seen.add(key)
            parent_headers = known[key].mail_headers
            parent_refs = parent_headers.get("references") or []
            parent = parent_refs[0] if parent_refs else parent_headers.get("in_reply_to")
            if not parent or parent == key:
                break
            key = parent
        return f"msgid:{key}"

    def _threads(self, records: list[MessageRecord]) -> list[ConversationBatch]:
        known = {r.source_id: r for r in records if r.source_id}
        grouped: dict[str, list[MessageRecord]] = {}
        for record in records:
            grouped.setdefault(self._thread_key(record, known), []).append(record)
        batches = []
        for key, members in grouped.items():
            people: dict[str, None] = {}
            for member in members:
                for address in [member.sender or ""] + [
                    p["address"]
                    for field in ("to", "cc")
                    for p in member.mail_headers.get(field, [])
                ]:
                    if address:
                        people.setdefault(address, None)
            subject = _strip_prefixes(members[0].mail_headers.get("subject") or "")
            conversation = ConversationRecord(
                service=SERVICE,
                source_id=key,
                type="group" if len(people) > 2 else "individual",
                name=subject or "(no subject)",
                participants=tuple(people),
                service_specific={"thread": key},
            )
            batches.append(ConversationBatch(conversation, members))
        return sorted(batches, key=lambda b: b.records[0].timestamp)

    # --- One mail ---------------------------------------------------------------------------

    def _record(self, message: EmailMessage, from_line: Optional[str]) -> MessageRecord:
        sender_name, address = (getaddresses([str(message.get("From", ""))]) or [("", "")])[0]
        sender: Optional[str] = address.lower() or None
        timestamp, assumed = self._time(message, from_line)
        message_id = normalize_message_id(message.get("Message-ID"))
        references = _message_ids(message.get("References"))
        in_reply_to = next(iter(_message_ids(message.get("In-Reply-To"))), None)
        subject = str(message.get("Subject", "") or "")
        labels = [
            label.strip()
            for label in str(message.get("X-Gmail-Labels", "") or "").split(",")
            if label.strip()
        ]
        headers: dict[str, Any] = {
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "thread_id": str(message.get("X-GM-THRID", "") or "").strip() or None,
            "labels": labels,
            "subject": subject,
            "to": _people(message, "To"),
            "cc": _people(message, "Cc"),
            "bcc": _people(message, "Bcc"),
        }
        plain = _body(message.get_body(preferencelist=("plain",)))
        html = _body(message.get_body(preferencelist=("html",)))
        text = (plain if plain is not None else html_to_text(html or "")).strip()
        extras: dict[str, Any] = {"body_html": html} if html else {}
        attachments = tuple(self._attachments(message))
        source_id = message_id or self._synthetic_id(sender, timestamp, subject, text)
        headers["message_id"] = message_id
        return MessageRecord(
            timestamp=timestamp,
            sender=sender,
            sender_name=self._name(sender, sender_name or None),
            body=text or None,
            content_type="email",
            source_id=source_id,
            is_from_owner=self._is_owner(sender, sender_name),
            tz_assumed=assumed,
            reply_to=in_reply_to,
            mail_headers=headers,
            service_specific=extras,
            attachments=attachments,
        )

    def _attachments(self, message: EmailMessage) -> list[AttachmentRecord]:
        """Every file the mail carries (attachments, inline files, forwarded mails), by hash."""
        records = []
        for index, part in enumerate(_attachment_parts(message), start=1):
            if part.get_content_type() == "message/rfc822":
                inner = part.get_payload(0)
                data = inner.as_bytes() if hasattr(inner, "as_bytes") else b""
            else:
                payload = part.get_payload(decode=True)
                data = payload if isinstance(payload, bytes) else b""
            if not data:
                continue
            mime = part.get_content_type()
            name = part.get_filename()
            if not name:
                extension = mimetypes.guess_extension(mime) or ".bin"
                name = f"attachment-{index}{extension}"
            if self.attachments_dir:
                digest, relative = blobs.store_by_hash(
                    self.attachments_dir, data, Path(name).suffix
                )
            else:
                digest, relative = hashlib.sha256(data).hexdigest(), None
            records.append(AttachmentRecord(digest, name, mime, len(data), relative))
        return records

    @staticmethod
    def _time(message: EmailMessage, from_line: Optional[str]) -> tuple[datetime, bool]:
        """The send time with the sender's offset, and whether the zone had to be assumed."""
        try:
            moment = parsedate_to_datetime(str(message.get("Date")))
        except (TypeError, ValueError, IndexError):
            moment = None
        if moment is not None:
            if moment.tzinfo is None:  # "-0000": the sender's zone is unknown
                return moment.replace(tzinfo=timezone.utc), True
            return moment, False
        match = _FROM_LINE_TIME.match(f"From {from_line}" if from_line else "")
        if match:
            try:
                return (
                    datetime.strptime(match["when"], "%a %b %d %H:%M:%S %Y").replace(
                        tzinfo=timezone.utc
                    ),
                    True,
                )
            except ValueError:
                pass
        return datetime(1970, 1, 1, tzinfo=timezone.utc), True

    @staticmethod
    def _synthetic_id(sender: Optional[str], when: datetime, subject: str, body: str) -> str:
        digest = hashlib.sha256(
            "\x1f".join([sender or "", when.isoformat(), subject, body]).encode("utf-8")
        )
        return "nomid-" + digest.hexdigest()[:32]

    def _name(self, address: Optional[str], name: Optional[str]) -> Optional[str]:
        return self.aliases.get(address or "") or self.aliases.get(name or "") or name or address

    def _is_owner(self, address: Optional[str], name: str) -> bool:
        return bool(self.owner) and self.owner in {(address or "").lower(), name.strip().lower()}

    @staticmethod
    def _merge(first: MessageRecord, other: MessageRecord) -> MessageRecord:
        """The same mail seen twice (Gmail repeats a mail under each label): union the labels."""
        labels = list(first.mail_headers.get("labels", []))
        labels += [l for l in other.mail_headers.get("labels", []) if l not in labels]
        headers = {**first.mail_headers, "labels": labels}
        from dataclasses import replace

        return replace(first, mail_headers=headers)
