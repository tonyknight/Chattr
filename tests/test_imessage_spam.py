"""The spam filter tags messages instead of dropping them, and it does so on every path."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports

from chattr.parsers.imessage_database_parser import iMessageDatabaseParser

NOISE_GUIDS = {"RCS-1": "RCS System Message", "UNK-1": "Unknown Sender Unread"}


@pytest.fixture
def noisy_db(tmp_path: Path, fixture_family: FamilyExports) -> Path:
    """The fixture chat.db plus unread, text-less rows of the kinds the filter targets."""
    db_path = tmp_path / "chat.db"
    shutil.copy(fixture_family.paths["imessage"], db_path)
    with sqlite3.connect(db_path) as db:
        chat_id = db.execute("SELECT MIN(ROWID) FROM chat").fetchone()[0]
        unknown = db.execute("SELECT MIN(ROWID) FROM handle").fetchone()[0]
        rows = [
            ("RCS-1", "RCS", unknown, 0, 0, None),
            ("UNK-1", "iMessage", 0, 0, 0, None),  # handle 0 → sender "Unknown"
            ("TAP-1", "iMessage", unknown, 0, 2000, "p:0/MSG-0000-4000-8000-000000000002"),
        ]
        for guid, service, handle, from_me, assoc_type, assoc_guid in rows:
            cur = db.execute(
                "INSERT INTO message (guid, text, service, handle_id, date, is_from_me, is_read, "
                "associated_message_type, associated_message_guid) "
                "VALUES (?, NULL, ?, ?, 800000000000000000, ?, 0, ?, ?)",
                (guid, service, handle, from_me, assoc_type, assoc_guid),
            )
            db.execute(
                "INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?, ?, 0)",
                (chat_id, cur.lastrowid),
            )
    return db_path


def _read(db_path: Path, mode: str, parallel: bool):
    reader = iMessageDatabaseParser(
        db_path=str(db_path),
        enable_spam_filtering=mode != "disabled",
        aggressive_spam_filtering=mode == "aggressive",
    )
    try:
        messages = (
            reader.get_all_messages_parallel(max_workers=2, batch_size=3)
            if parallel
            else reader.get_messages_with_fallback()
        )
        return messages, reader.get_spam_filtering_stats()
    finally:
        reader.close()


@pytest.mark.parametrize("parallel", [False, True])
def test_noise_is_tagged_not_dropped(noisy_db: Path, parallel: bool) -> None:
    messages, stats = _read(noisy_db, "conservative", parallel)
    by_guid = {m.guid: m for m in messages}
    for guid, reason in NOISE_GUIDS.items():
        assert by_guid[guid].spam_reason == reason
    assert stats is not None and stats["total_suppressed"] == len(NOISE_GUIDS)


def test_both_paths_tag_the_same_messages(noisy_db: Path) -> None:
    single, single_stats = _read(noisy_db, "aggressive", parallel=False)
    parallel, parallel_stats = _read(noisy_db, "aggressive", parallel=True)
    tags = lambda ms: {m.guid: m.spam_reason for m in ms}  # noqa: E731
    assert tags(single) == tags(parallel)
    assert single_stats == parallel_stats
    assert len(single) == len(parallel)


@pytest.mark.parametrize("parallel", [False, True])
def test_disabled_mode_tags_nothing(noisy_db: Path, parallel: bool) -> None:
    messages, stats = _read(noisy_db, "disabled", parallel)
    assert all(m.spam_reason is None for m in messages) and stats is None


@pytest.mark.parametrize("parallel", [False, True])
def test_an_unread_tapback_is_not_noise(noisy_db: Path, parallel: bool) -> None:
    messages, _ = _read(noisy_db, "aggressive", parallel)
    (tapback,) = [m for m in messages if m.guid == "TAP-1"]
    assert tapback.spam_reason is None


def test_aggressive_mode_tags_at_least_what_conservative_does(noisy_db: Path) -> None:
    conservative, _ = _read(noisy_db, "conservative", parallel=True)
    aggressive, _ = _read(noisy_db, "aggressive", parallel=True)
    tagged = lambda ms: {m.guid for m in ms if m.spam_reason}  # noqa: E731
    assert tagged(conservative) <= tagged(aggressive)


def test_native_json_carries_the_tag_and_keeps_every_message(
    chattr_root: Path, noisy_db: Path
) -> None:
    import json

    from chattr import commands
    from chattr.config import Settings

    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=noisy_db,
        spam_mode="conservative",
        workers=2,
        route="service",
    )
    assert result.json_path is not None
    exported = {m["guid"]: m for m in json.loads(result.json_path.read_text(encoding="utf-8"))}
    assert exported["RCS-1"]["spam_reason"] == "RCS System Message"
    assert exported["TAP-1"]["spam_reason"] is None
    with sqlite3.connect(noisy_db) as db:
        assert len(exported) == db.execute("SELECT COUNT(*) FROM message").fetchone()[0]
