"""A synthetic iMessage `chat.db` (macOS Messages database) for the fixture family.

The schema is a subset of Apple's: the tables and columns Chattr reads. Values follow Apple's
conventions as publicly documented facts:
- `message.date` counts from 2001-01-01 UTC, in seconds on old databases and nanoseconds on new;
- `chat.style` is 43 for groups and 45 for one-to-one;
- tapbacks use `associated_message_type` 2000-2005 (added) and 3000-3005 (removed), with
  `associated_message_guid` of the form `p:<part>/<guid>` or `bp:<guid>`;
- message text may exist only in `attributedBody`, an NSAttributedString in Apple's
  "typedstream" archive format.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fixtures import family
from fixtures.builders.media import GIF_1X1, png

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
ATTACHMENTS_PREFIX = "~/Library/Messages/Attachments"

SCHEMA = """
CREATE TABLE handle (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, country TEXT,
    service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT
);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER,
    state INTEGER, account_id TEXT, chat_identifier TEXT, service_name TEXT, room_name TEXT,
    display_name TEXT, group_id TEXT
);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER, UNIQUE (chat_id, handle_id));
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT,
    attributedBody BLOB, service TEXT, handle_id INTEGER DEFAULT 0, date INTEGER,
    date_read INTEGER, date_delivered INTEGER, is_from_me INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0, associated_message_guid TEXT,
    associated_message_type INTEGER DEFAULT 0, associated_message_emoji TEXT,
    balloon_bundle_id TEXT, cache_has_attachments INTEGER DEFAULT 0,
    thread_originator_guid TEXT, date_edited INTEGER DEFAULT 0, other_handle INTEGER DEFAULT 0,
    group_action_type INTEGER DEFAULT 0
);
CREATE TABLE chat_message_join (
    chat_id INTEGER, message_id INTEGER, message_date INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE attachment (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, created_date INTEGER,
    filename TEXT, uti TEXT, mime_type TEXT, transfer_name TEXT, total_bytes INTEGER,
    is_sticker INTEGER DEFAULT 0, hide_attachment INTEGER DEFAULT 0
);
CREATE TABLE message_attachment_join (
    message_id INTEGER, attachment_id INTEGER, UNIQUE (message_id, attachment_id)
);
"""


def apple_time(moment: datetime, *, nanoseconds: bool = True) -> int:
    """Apple Messages timestamp: seconds (old databases) or nanoseconds since 2001-01-01 UTC."""
    seconds = int((moment - APPLE_EPOCH).total_seconds())
    return seconds * 1_000_000_000 if nanoseconds else seconds


def _typedstream_int(value: int) -> bytes:
    """Typedstream integer encoding: one byte when small, else a 0x81 tag and 2 bytes LE."""
    if 0 <= value < 0x80:
        return bytes([value])
    if value <= 0x7FFF:
        return b"\x81" + value.to_bytes(2, "little")
    return b"\x82" + value.to_bytes(4, "little")


def attributed_body(text: str) -> bytes:
    """A minimal NSAttributedString typedstream holding `text` (enough for parsers to find it)."""
    data = text.encode("utf-8")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        + _typedstream_int(len(data))
        + data
        + b"\x86\x84\x02iI\x01"
        + _typedstream_int(len(text))
        + b"\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x00\x86\x86"
    )


class _Writer:
    def __init__(self, conn: sqlite3.Connection, out: Path) -> None:
        self.conn = conn
        self.out = out
        self.handles: dict[str, int] = {}
        self.counter = 0

    def handle(self, ident: str) -> int:
        if ident not in self.handles:
            cur = self.conn.execute(
                "INSERT INTO handle (id, country, service, uncanonicalized_id) VALUES (?, 'us', 'iMessage', ?)",
                (ident, ident),
            )
            self.handles[ident] = int(cur.lastrowid or 0)
        return self.handles[ident]

    def chat(self, ident: str, members: list[str], *, style: int, name: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO chat (guid, style, state, chat_identifier, service_name, display_name) "
            "VALUES (?, ?, 3, ?, 'iMessage', ?)",
            (f"iMessage;{'+' if style == 43 else '-'};{ident}", style, ident, name or ""),
        )
        chat_id = int(cur.lastrowid or 0)
        for member in members:
            self.conn.execute(
                "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
                (chat_id, self.handle(member)),
            )
        return chat_id

    def guid(self, prefix: str = "MSG") -> str:
        self.counter += 1
        return f"{prefix}-0000-4000-8000-{self.counter:012d}"

    def message(
        self,
        chat_id: int,
        moment: datetime,
        *,
        sender: str | None,
        text: str | None,
        body_only: bool = False,
        nanoseconds: bool = True,
        tapback: tuple[int, str] | None = None,
    ) -> tuple[int, str]:
        guid = self.guid()
        handle_id = 0 if sender is None else self.handle(sender)
        body = attributed_body(text) if text is not None else None
        assoc_type, assoc_guid = tapback if tapback else (0, None)
        date = apple_time(moment, nanoseconds=nanoseconds)
        cur = self.conn.execute(
            "INSERT INTO message (guid, text, attributedBody, service, handle_id, date, "
            "date_read, date_delivered, is_from_me, is_read, associated_message_guid, "
            "associated_message_type) VALUES (?, ?, ?, 'iMessage', ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                guid,
                None if body_only else text,
                body,
                handle_id,
                date,
                date,
                date,
                1 if sender is None else 0,
                assoc_guid,
                assoc_type,
            ),
        )
        message_id = int(cur.lastrowid or 0)
        self.conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?, ?, ?)",
            (chat_id, message_id, date),
        )
        return message_id, guid

    def attach(self, message_id: int, moment: datetime, name: str, data: bytes, mime: str) -> None:
        guid = self.guid("ATT")
        relative = f"{guid[-2:]}/{guid[-4:-2]}/{guid}/{name}"
        local = self.out / "Attachments" / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        cur = self.conn.execute(
            "INSERT INTO attachment (guid, created_date, filename, mime_type, transfer_name, "
            "total_bytes) VALUES (?, ?, ?, ?, ?, ?)",
            (
                guid,
                apple_time(moment, nanoseconds=False),
                f"{ATTACHMENTS_PREFIX}/{relative}",
                mime,
                name,
                len(data),
            ),
        )
        self.conn.execute(
            "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
            (message_id, int(cur.lastrowid or 0)),
        )
        self.conn.execute(
            "UPDATE message SET cache_has_attachments = 1 WHERE ROWID = ?", (message_id,)
        )


def build_imessage(out_dir: Path) -> Path:
    """Write `chat.db` and its `Attachments/` folder into `out_dir`; return the database path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "chat.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        w = _Writer(conn, out_dir)
        ana, joao = family.ANA.phones[0], family.JOAO.emails[0]

        # One-to-one with Ana. Early messages come from an old database (seconds epoch).
        ana_chat = w.chat(ana, [ana], style=45)
        w.message(
            ana_chat,
            family.at(2016, 3, 12, 9, 15),
            sender=ana,
            text="Bom dia Mãe! Landed safely.",
            nanoseconds=False,
        )
        _, hello = w.message(
            ana_chat,
            family.at(2016, 3, 12, 9, 17),
            sender=None,
            text="Thank God. Call me when you're settled.",
            nanoseconds=False,
        )
        w.message(
            ana_chat,
            family.at(2016, 3, 12, 9, 18),
            sender=ana,
            text=None,
            tapback=(2001, f"p:0/{hello}"),
            nanoseconds=False,
        )  # loved
        w.message(
            ana_chat,
            family.at(2023, 6, 4, 18, 30),
            sender=ana,
            text=family.LONG_MESSAGE,
            body_only=True,
        )
        photos_id, photos = w.message(
            ana_chat, family.at(2023, 6, 4, 18, 41), sender=ana, text="Two of the best ones"
        )
        same_second = family.at(2023, 6, 4, 18, 41)
        w.attach(photos_id, same_second, "IMG_0001.PNG", png(rgb=(180, 140, 90)), "image/png")
        w.attach(photos_id, same_second, "IMG_0002.PNG", png(rgb=(90, 140, 180)), "image/png")
        w.message(
            ana_chat,
            family.at(2023, 6, 4, 18, 45),
            sender=None,
            text=None,
            tapback=(2000, f"p:1/{photos}"),
        )  # liked the second part (an attachment)
        w.message(
            ana_chat,
            family.at(2023, 6, 4, 18, 46),
            sender=None,
            text=None,
            tapback=(3000, f"p:1/{photos}"),
        )  # like removed

        # Group chat: owner, Ana and João.
        group = w.chat("chat100000000000000001", [ana, joao], style=43, name=family.GROUP_NAME)
        _, plan = w.message(
            group,
            family.at(2024, 12, 20, 19, 0),
            sender=joao,
            text="Christmas at my place this year?",
        )
        w.message(
            group,
            family.at(2024, 12, 20, 19, 2),
            sender=ana,
            text=None,
            tapback=(2003, f"bp:{plan}"),
        )  # laughed, legacy bp: form
        sticker_id, _ = w.message(
            group, family.at(2024, 12, 20, 19, 5), sender=None, text="Yes! I'll bring the bacalhau."
        )
        w.attach(sticker_id, family.at(2024, 12, 20, 19, 5), "sticker.gif", GIF_1X1, "image/gif")

        # A message from an unknown number.
        stranger = w.chat(family.UNKNOWN_HANDLE, [family.UNKNOWN_HANDLE], style=45)
        w.message(
            stranger,
            family.at(2025, 1, 8, 12, 0),
            sender=family.UNKNOWN_HANDLE,
            text="Your package could not be delivered.",
        )
        conn.commit()
    finally:
        conn.close()
    return db_path
