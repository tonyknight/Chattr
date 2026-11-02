"""The synthetic iMessage chat.db contains every case the iMessage tickets depend on."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fixtures import family
from fixtures.builders.imessage import ATTACHMENTS_PREFIX, build_imessage

TABLES = {
    "handle",
    "chat",
    "chat_handle_join",
    "message",
    "chat_message_join",
    "attachment",
    "message_attachment_join",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, sqlite3.Connection]:
    out = tmp_path_factory.mktemp("imessage")
    db_path = build_imessage(out)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return out, conn


def test_schema_has_the_tables_chattr_reads(built: tuple[Path, sqlite3.Connection]) -> None:
    _, conn = built
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_long_message_lives_only_in_attributed_body(
    built: tuple[Path, sqlite3.Connection],
) -> None:
    _, conn = built
    text = family.LONG_MESSAGE.encode("utf-8")
    length = len(text)
    assert length > 255
    expected = b"\x81" + length.to_bytes(2, "little") + text
    rows = conn.execute(
        "SELECT text, attributedBody FROM message WHERE attributedBody IS NOT NULL"
    ).fetchall()
    matches = [r for r in rows if expected in bytes(r["attributedBody"])]
    assert len(matches) == 1
    assert matches[0]["text"] is None


def test_both_epoch_formats_are_present(built: tuple[Path, sqlite3.Connection]) -> None:
    _, conn = built
    dates = [r["date"] for r in conn.execute("SELECT date FROM message")]
    assert any(0 < d < 10**10 for d in dates), "expected a seconds-based date"
    assert any(d > 10**16 for d in dates), "expected a nanoseconds-based date"


def test_one_message_has_two_attachments_with_the_same_timestamp(
    built: tuple[Path, sqlite3.Connection],
) -> None:
    _, conn = built
    rows = conn.execute(
        """
        SELECT j.message_id, COUNT(*) AS n, COUNT(DISTINCT a.created_date) AS times
        FROM message_attachment_join j JOIN attachment a ON a.ROWID = j.attachment_id
        GROUP BY j.message_id HAVING n >= 2
        """
    ).fetchall()
    assert any(r["times"] == 1 for r in rows)


def test_tapbacks_cover_text_attachment_bp_and_removal(
    built: tuple[Path, sqlite3.Connection],
) -> None:
    _, conn = built
    rows = conn.execute(
        "SELECT associated_message_guid AS g, associated_message_type AS t FROM message "
        "WHERE associated_message_type BETWEEN 2000 AND 3999"
    ).fetchall()
    guids = [r["g"] for r in rows]
    assert any(g.startswith("p:0/") for g in guids)
    assert any(g.startswith("p:1/") for g in guids)
    assert any(g.startswith("bp:") for g in guids)
    assert any(3000 <= r["t"] < 4000 for r in rows), "expected a removed tapback"


def test_group_chat_with_two_other_participants(built: tuple[Path, sqlite3.Connection]) -> None:
    _, conn = built
    row = conn.execute(
        """
        SELECT c.ROWID AS id, COUNT(j.handle_id) AS n FROM chat c
        JOIN chat_handle_join j ON j.chat_id = c.ROWID
        WHERE c.style = 43 AND c.display_name = ? GROUP BY c.ROWID
        """,
        (family.GROUP_NAME,),
    ).fetchone()
    assert row is not None and row["n"] == 2


def test_a_message_comes_from_the_unknown_handle(built: tuple[Path, sqlite3.Connection]) -> None:
    _, conn = built
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM message m JOIN handle h ON h.ROWID = m.handle_id WHERE h.id = ?",
        (family.UNKNOWN_HANDLE,),
    ).fetchone()
    assert row["n"] >= 1


def test_attachment_files_exist_under_the_remapped_root(
    built: tuple[Path, sqlite3.Connection],
) -> None:
    out, conn = built
    filenames = [r["filename"] for r in conn.execute("SELECT filename FROM attachment")]
    assert filenames
    for name in filenames:
        assert name.startswith(ATTACHMENTS_PREFIX), name
        local = out / "Attachments" / name[len(ATTACHMENTS_PREFIX) :].lstrip("/")
        assert local.is_file(), local
