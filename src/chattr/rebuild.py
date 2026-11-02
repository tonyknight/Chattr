"""Rebuild `chattr.sqlite` from the rendered pages.

The pages are the durable copy of the domain store: each carries a JSON data island
(`<script type="application/json" id="chattr-data">`, schema `chattr-page-1`, see `render.py`) with
every stored column of its messages. `rebuild_database` reads them all and writes the rows back
*as the island has them*: the ids, content hashes and versions are carried over, not recomputed, so
a rebuilt database has the same conversation and message ids and the same attachments, reactions,
handles and participants. What a page can't hold is the import log; the rebuild records one `imports`
row describing itself. A conversation with no messages has no page and is not rebuilt.

The rebuild writes in one transaction and refuses a database that already holds messages.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from chattr.database_manager import DatabaseManager, _json
from chattr.render import SCHEMA

PathLike = Union[str, Path]
_ISLAND = re.compile(r'<script type="application/json" id="chattr-data">(.*?)</script>', re.S)


class RebuildError(Exception):
    """The rebuild can't proceed (for example the target database already holds data)."""


@dataclass
class RebuildResult:
    pages: int = 0
    conversations: int = 0
    messages: int = 0
    attachments: int = 0
    reactions: int = 0
    handles: int = 0
    skipped: list[str] = field(default_factory=list)


def read_islands(site: PathLike) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    """Every page's data island under `site`, and the files that couldn't be used (with why)."""
    documents: list[tuple[Path, dict[str, Any]]] = []
    skipped: list[str] = []
    for page in sorted(Path(site).rglob("*.html")):
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            skipped.append(f"{page}: unreadable ({error})")
            continue
        match = _ISLAND.search(text)
        if not match:
            continue  # not one of our pages (the index, other HTML)
        try:
            document = json.loads(match.group(1))
        except ValueError as error:
            skipped.append(f"{page}: damaged data island ({error})")
            continue
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            skipped.append(
                f"{page}: unknown data schema {document.get('schema') if isinstance(document, dict) else '?'}"
            )
            continue
        documents.append((page, document))
    return documents, skipped


def _handle_key(service: str, identifier: str) -> tuple[str, str]:
    return service, identifier.strip().lower()


def rebuild_database(site: PathLike, db_path: PathLike) -> RebuildResult:
    """Create `db_path` from the pages under `site`."""
    documents, skipped = read_islands(site)
    result = RebuildResult(pages=len(documents), skipped=skipped)
    if not documents:
        detail = f" ({len(skipped)} unusable)" if skipped else ""
        raise RebuildError(f"No Chattr pages found under {site}{detail}.")
    target = Path(db_path)
    if target.exists():
        with sqlite3.connect(target) as probe:
            has = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
            ).fetchone()
            if has and probe.execute("SELECT 1 FROM messages LIMIT 1").fetchone():
                raise RebuildError(f"{target} already holds messages; move it away first.")
    created = not target.exists()
    try:
        with DatabaseManager(target) as manager:
            conn = manager.connection
            with conn:  # one transaction: all of it or none
                import_id = int(
                    conn.execute(
                        "INSERT INTO imports (service, source_path, started_at, finished_at) "
                        "VALUES ('Rebuild', ?, ?, ?)",
                        (str(site), *(datetime.now(timezone.utc).isoformat(),) * 2),
                    ).lastrowid
                    or 0
                )
                handle_ids: dict[tuple[str, str], int] = {}
                seen_conversations: set[str] = set()
                for page, document in documents:
                    try:
                        _apply(conn, document, import_id, handle_ids, seen_conversations, result)
                    except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
                        raise RebuildError(
                            f"{page.name} can't be applied ({type(error).__name__}: {error}); "
                            "nothing was written."
                        ) from error
                conn.execute(
                    "UPDATE imports SET inserted = ? WHERE id = ?", (result.messages, import_id)
                )
            result.handles = len(handle_ids)
    except RebuildError:
        if created:
            target.unlink(missing_ok=True)  # don't leave an empty, half-made database
        raise
    return result


def _handle_id(
    conn: sqlite3.Connection, handle_ids: dict[tuple[str, str], int], handle: dict[str, Any]
) -> int:
    key = _handle_key(handle["service"], handle["identifier"])
    if key not in handle_ids:
        conn.execute(
            "INSERT INTO handles (service, identifier, normalized, display_name, person) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (service, normalized) DO UPDATE SET "
            "display_name = COALESCE(excluded.display_name, handles.display_name), "
            "person = COALESCE(excluded.person, handles.person)",
            (
                handle["service"],
                handle["identifier"],
                handle.get("normalized") or key[1],
                handle.get("display_name"),
                handle.get("person"),
            ),
        )
        handle_ids[key] = int(
            conn.execute(
                "SELECT id FROM handles WHERE service = ? AND normalized = ?",
                (key[0], handle.get("normalized") or key[1]),
            ).fetchone()[0]
        )
    return handle_ids[key]


def _apply(
    conn: sqlite3.Connection,
    document: dict[str, Any],
    import_id: int,
    handle_ids: dict[tuple[str, str], int],
    seen_conversations: set[str],
    result: RebuildResult,
) -> None:
    for handle in document["handles"]:
        _handle_id(conn, handle_ids, handle)
    by_ref = {_handle_key(h["service"], h["identifier"]): h for h in document["handles"]}

    def lookup(ref: Optional[dict[str, str]]) -> Optional[int]:
        if not ref:
            return None
        key = _handle_key(ref["service"], ref["identifier"])
        if key not in handle_ids:  # a handle the island did not list: keep the row consistent
            _handle_id(conn, handle_ids, by_ref.get(key, {**ref, "display_name": None}))
        return handle_ids[key]

    for conversation in document["conversations"]:
        if conversation["id"] in seen_conversations:
            continue
        seen_conversations.add(conversation["id"])
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, service, source_id, type, name, "
            "service_specific) VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversation["id"],
                conversation["service"],
                conversation["source_id"],
                conversation["type"],
                conversation["name"],
                _json(conversation.get("service_specific")),
            ),
        )
        result.conversations += 1
        for participant in conversation.get("participants", []):
            conn.execute(
                "INSERT OR IGNORE INTO participants (conversation_id, handle_id, role) "
                "VALUES (?, ?, ?)",
                (conversation["id"], lookup(participant), participant.get("role", "member")),
            )

    for message in document["messages"]:
        conn.execute(
            "INSERT OR IGNORE INTO messages (id, conversation_id, source_id, timestamp_utc, "
            "tz_offset_minutes, time_precision, tz_assumed, sender_handle_id, is_from_owner, "
            "content_type, body, language, translation, transcript, reply_to, edit_history, "
            "forwarded_from, delivery_status, mail_headers, service_specific, import_id, "
            "content_hash, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            (
                message["id"],
                message["conversation_id"],
                message["source_id"],
                message["timestamp_utc"],
                message["tz_offset_minutes"],
                message["time_precision"],
                int(message["tz_assumed"]),
                lookup(message["sender"]),
                int(message["is_from_owner"]),
                message["content_type"],
                message["body"],
                message["language"],
                message["translation"],
                message["transcript"],
                message["reply_to"],
                message["edit_history"],
                message["forwarded_from"],
                message["delivery_status"],
                _json(message["mail_headers"]),
                _json(message["service_specific"]),
                import_id,
                message["content_hash"],
                message["version"],
            ),
        )
        result.messages += 1
        for attachment in message["attachments"]:
            conn.execute(
                "INSERT OR IGNORE INTO attachments (sha256, message_id, original_filename, "
                "archival_filename, mime_type, size, storage_path, processing_info) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attachment["sha256"],
                    message["id"],
                    attachment["original_filename"],
                    attachment["archival_filename"],
                    attachment["mime_type"],
                    attachment["size"],
                    attachment["storage_path"],
                    _json(attachment.get("processing_info")),
                ),
            )
            result.attachments += 1
        for reaction in message["reactions"]:
            conn.execute(
                "INSERT OR IGNORE INTO reactions (message_id, target_part, sender_handle_id, "
                "type, emoji, timestamp_utc, action) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message["id"],
                    reaction["target_part"],
                    lookup(reaction["sender"]),
                    reaction["type"],
                    reaction["emoji"],
                    reaction["timestamp_utc"],
                    reaction["action"],
                ),
            )
            result.reactions += 1


# --- Comparing two databases -----------------------------------------------------------------


def _rows(db: sqlite3.Connection, sql: str) -> list[tuple[Any, ...]]:
    return sorted(
        (tuple(r) for r in db.execute(sql)),
        key=lambda r: tuple("" if v is None else str(v) for v in r),
    )


_COMPARISONS = {
    "conversations": "SELECT * FROM conversations",
    "messages": (
        "SELECT id, conversation_id, source_id, timestamp_utc, tz_offset_minutes, time_precision, "
        "tz_assumed, is_from_owner, content_type, body, language, translation, transcript, "
        "reply_to, edit_history, forwarded_from, delivery_status, mail_headers, service_specific, "
        "content_hash, version, "
        "(SELECT service || '|' || normalized FROM handles WHERE id = sender_handle_id) FROM messages"
    ),
    "handles": "SELECT service, identifier, normalized, display_name, person FROM handles",
    "participants": (
        "SELECT p.conversation_id, h.service, h.normalized, p.role "
        "FROM participants p JOIN handles h ON h.id = p.handle_id"
    ),
    "attachments": "SELECT * FROM attachments",
    "reactions": (
        "SELECT r.message_id, r.target_part, h.service, h.normalized, r.type, r.emoji, "
        "r.timestamp_utc, r.action FROM reactions r LEFT JOIN handles h ON h.id = r.sender_handle_id"
    ),
}


def compare_databases(a: PathLike, b: PathLike) -> list[str]:
    """What differs between two Chattr databases (ids and row contents; not row numbers)."""
    differences: list[str] = []
    with sqlite3.connect(a) as left, sqlite3.connect(b) as right:
        for table, sql in _COMPARISONS.items():
            first, second = _rows(left, sql), _rows(right, sql)
            if first == second:
                continue
            only_first = [r for r in first if r not in second]
            only_second = [r for r in second if r not in first]
            differences.append(
                f"{table}: {len(first)} rows vs {len(second)}; {len(only_first)} only in the "
                f"first (e.g. {only_first[:1]}), {len(only_second)} only in the second "
                f"(e.g. {only_second[:1]})"
            )
    return differences
