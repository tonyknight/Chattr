"""IMessageParser: a chat.db becomes normalized records, per conversation, ready for the archive."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings
from chattr.parsers.base_parser import BaseParser
from chattr.parsers.imessage_parser import IMessageParser

TAPBACKS_IN_FIXTURE = 4  # 1 liked, loved + removed on an attachment, 1 laughed


@pytest.fixture(scope="module")
def batches(fixture_family: FamilyExports):
    db_path = fixture_family.paths["imessage"]
    return IMessageParser(
        attachment_root=db_path.parent / "Attachments", spam_mode="disabled", workers=1
    ).parse(db_path)


def _records(batches):
    return [r for b in batches for r in b.records]


def test_it_is_a_parser() -> None:
    assert issubclass(IMessageParser, BaseParser)


def test_one_batch_per_conversation_with_its_type_and_name(batches) -> None:
    by_name = {b.conversation.name: b.conversation for b in batches}
    group = by_name[family.GROUP_NAME]
    assert group.type == "group" and group.service == "iMessage"
    assert set(group.participants) == {family.ANA.phones[0], family.JOAO.emails[0]}
    assert sum(1 for b in batches if b.conversation.type == "individual") == 2
    assert len({b.conversation.source_id for b in batches}) == len(batches)  # chat GUIDs


def test_tapbacks_become_reactions_not_messages(batches, fixture_family) -> None:
    with sqlite3.connect(fixture_family.paths["imessage"]) as db:
        total = db.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    records = _records(batches)
    assert len(records) == total - TAPBACKS_IN_FIXTURE
    reactions = {r.body: r.reactions for r in records if r.reactions}
    liked = reactions["Thank God. Call me when you're settled."]
    assert [(x.type, x.action, x.target_part) for x in liked] == [("Liked", "added", 0)]
    assert [(x.type, x.action, x.target_part) for x in reactions["Two of the best ones"]] == [
        ("Loved", "added", 1),
        ("Loved", "removed", 1),
    ]


def test_messages_carry_stable_source_ids_and_utc_times(batches) -> None:
    records = {r.body: r for r in _records(batches)}
    old = records["Bom dia Mãe! Landed safely."]
    assert old.timestamp == family.at(2016, 3, 12, 9, 15)
    assert old.source_id and old.source_id.startswith("MSG-")
    assert old.sender == family.ANA.phones[0] and not old.is_from_owner
    assert records["Two of the best ones"].timestamp == family.at(2023, 6, 4, 18, 41)


def test_the_owner_is_marked_not_named_after_a_handle(batches) -> None:
    records = {r.body: r for r in _records(batches)}
    mine = records["Thank God. Call me when you're settled."]
    assert mine.is_from_owner and mine.sender is None


def test_long_messages_are_complete(batches) -> None:
    assert family.LONG_MESSAGE in {r.body for r in _records(batches)}


def test_attachments_are_hashed_from_their_files(batches, fixture_family) -> None:
    photos = {r.body: r for r in _records(batches)}["Two of the best ones"]
    root = fixture_family.paths["imessage"].parent / "Attachments"
    expected = {hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.PNG")}
    assert {a.sha256 for a in photos.attachments} == expected
    assert {a.original_filename for a in photos.attachments} == {"IMG_0001.PNG", "IMG_0002.PNG"}
    assert photos.content_type == "text"  # it has its own text; attachment-only would be media


def test_a_missing_attachment_file_is_still_recorded(fixture_family, tmp_path: Path) -> None:
    result = IMessageParser(attachment_root=tmp_path / "nowhere", spam_mode="disabled").parse(
        fixture_family.paths["imessage"]
    )
    photos = {r.body: r for b in result for r in b.records}["Two of the best ones"]
    assert len(photos.attachments) == 2 and len({a.sha256 for a in photos.attachments}) == 2


# --- Into the central archive -------------------------------------------------------------


def _import(root: Path, db_path: Path, **options):
    return commands.convert_imessage(
        Settings.load(root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        spam_mode="disabled",
        workers=1,
        **options,
    )


def _rows(root: Path, sql: str):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql).fetchall()


def test_central_import_is_the_default_and_is_idempotent(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    db_path = fixture_family.paths["imessage"]
    first = _import(chattr_root, db_path)
    assert first.json_path is None and first.counts.inserted == 7
    second = _import(chattr_root, db_path)
    assert (second.counts.inserted, second.counts.updated, second.counts.unchanged) == (0, 0, 7)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages") == [(7,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM reactions") == [(4,)]  # not 8
    assert _rows(chattr_root, "SELECT COUNT(*) FROM attachments") == [(3,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM conversations") == [(3,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM imports") == [(2,)]


def test_central_rows_hold_utc_group_type_and_owner(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _import(chattr_root, fixture_family.paths["imessage"])
    assert _rows(
        chattr_root,
        "SELECT timestamp_utc FROM messages WHERE body LIKE 'Bom dia%'",
    ) == [("2016-03-12T14:15:00+00:00",)]
    assert _rows(chattr_root, "SELECT type FROM conversations ORDER BY type") == [
        ("group",),
        ("individual",),
        ("individual",),
    ]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE is_from_owner = 1") == [(2,)]


def test_the_state_file_is_updated(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _import(chattr_root, fixture_family.paths["imessage"])
    state = json.loads((chattr_root / "Services" / "iMessage.json").read_text())["state"]
    assert state["total_messages_imported"] == 7 and state["last_import_date"]


def test_owner_and_aliases_name_senders(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({family.ANA.phones[0]: "Ana (daughter)"}))
    _import(chattr_root, fixture_family.paths["imessage"], owner="Maria", aliases=aliases)
    names = {r[0] for r in _rows(chattr_root, "SELECT display_name FROM handles")}
    assert "Ana (daughter)" in names


def test_service_route_still_writes_native_json(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _import(chattr_root, fixture_family.paths["imessage"], route="service")
    assert result.json_path == chattr_root / "Output" / "iMessage" / "imessage_export.json"
    assert len(json.loads(result.json_path.read_text(encoding="utf-8"))) == 11
    assert (
        _rows(chattr_root, "SELECT COUNT(*) FROM messages") == [(0,)]
        if (commands.central_database(Settings.load(chattr_root)).exists())
        else True
    )


def test_html_is_written_per_conversation(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = _import(chattr_root, fixture_family.paths["imessage"], html=True)
    assert result.html_path is not None
    pages = list((chattr_root / "Output" / "Chattr").glob("*.html"))
    assert len(pages) == 3
    assert any(family.GROUP_NAME in p.name for p in pages)


def test_unknown_route_is_a_command_error(chattr_root: Path, fixture_family: FamilyExports) -> None:
    with pytest.raises(commands.CommandError, match="Route"):
        _import(chattr_root, fixture_family.paths["imessage"], route="elsewhere")


# --- Found in review ------------------------------------------------------------------------


def _with_message(fixture_family: FamilyExports, tmp_path: Path, **columns: object) -> Path:
    """A copy of the fixture chat.db with one more message in the first chat."""
    import shutil

    db_path = tmp_path / "chat.db"
    shutil.copy(fixture_family.paths["imessage"], db_path)
    values = {"service": "iMessage", "date": 800_000_000_000_000_000, "is_read": 1, **columns}
    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            f"INSERT INTO message ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )
        db.execute(
            "INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (1, ?, 0)",
            (cur.lastrowid,),
        )
    return db_path


def test_a_received_message_without_a_handle_has_no_sender(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    db_path = _with_message(
        fixture_family, tmp_path, guid="NOHANDLE-1", text="Group renamed", handle_id=0, is_from_me=0
    )
    records = {
        r.source_id: r
        for b in IMessageParser(spam_mode="disabled").parse(db_path)
        for r in b.records
    }
    note = records["NOHANDLE-1"]
    assert note.sender is None and not note.is_from_owner
    assert "Me" not in {a.sender for a in records.values()}


def test_contacts_name_the_people_and_one_to_one_chats(fixture_family: FamilyExports) -> None:
    result = IMessageParser(contacts=fixture_family.paths["contacts"], spam_mode="disabled").parse(
        fixture_family.paths["imessage"]
    )
    names = {b.conversation.name for b in result}
    assert family.ANA.name in names  # the one-to-one chat with Ana, not her phone number
    assert family.ANA.phones[0] not in names
    senders = {r.sender_name for b in result for r in b.records if not r.is_from_owner}
    assert family.ANA.name in senders
