"""iMessage: messages the database keeps without a chat (the chat was deleted, or never joined).

They used to be gathered into one conversation, "(no chat)", that mixed everyone together. Each
now goes back to the person it was with: into their own conversation when there is one, or a
conversation of its own with them when there is not. A message the owner sent with no recipient
recorded keeps a conversation of its own, and a group's events, with no group to belong to, go.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fixtures import family
from fixtures.builders.imessage import SCHEMA, apple_time

from chattr import commands
from chattr.config import Settings
from chattr.database_manager import DatabaseManager
from chattr.parsers.imessage_parser import NO_RECIPIENT, ORPHAN_CHAT, IMessageParser

ANA, JOAO, RUI = family.ANA.phones[0], family.JOAO.phones[0], "+15550109876"


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "chat.db"
    if path.exists():
        return path
    db = sqlite3.connect(path)
    db.executescript(SCHEMA + "ALTER TABLE message ADD COLUMN reply_to_guid TEXT;")
    for rowid, address in ((1, ANA), (2, JOAO), (3, RUI)):
        db.execute(
            "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, 'iMessage')", (rowid, address)
        )
    db.execute(
        "INSERT INTO chat (ROWID, guid, style, chat_identifier, service_name) VALUES (1, 'chat-ana', 45, ?, 'iMessage')",
        (ANA,),
    )
    db.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    rows = [
        # (rowid, text, handle, from me, item type, in Ana's chat)
        (1, "in her chat", 1, 0, 0, True),
        (2, "from Ana, chat gone", 1, 0, 0, False),
        (3, "to Ana, chat gone", 1, 1, 0, False),
        (4, "from João, never in a chat", 2, 0, 0, False),
        (5, "to João", 2, 1, 0, False),
        (6, "from Rui", 3, 0, 0, False),
        (7, "sent, nobody recorded", 0, 1, 0, False),
        (8, None, 2, 0, 1, False),  # João joined a group that is gone
        # the owner's replies whose recipient was not recorded, but which say what they followed
        (9, "answer to Rui", 0, 1, 0, False),
        (10, "and again", 0, 1, 0, False),
        (11, "answer to Ana", 0, 1, 0, False),
    ]
    follows = {9: "g-6", 10: "g-9", 11: "g-1"}
    for rowid, text, handle, mine, item, in_chat in rows:
        db.execute(
            "INSERT INTO message (ROWID, guid, text, service, handle_id, is_from_me, item_type, date, reply_to_guid) "
            "VALUES (?, ?, ?, 'iMessage', ?, ?, ?, ?, ?)",
            (
                rowid,
                f"g-{rowid}",
                text,
                handle,
                mine,
                item,
                apple_time(family.at(2024, 5, 1, 10, rowid)),
                follows.get(rowid),
            ),
        )
        if in_chat:
            db.execute(
                "INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (rowid,)
            )
    db.commit()
    db.close()
    return path


def _by_conversation(tmp_path: Path) -> dict[str, tuple[str, set[str]]]:
    batches = IMessageParser(spam_mode="disabled").parse(_database(tmp_path))
    return {
        b.conversation.source_id: (b.conversation.type, {r.source_id for r in b.records})
        for b in batches
    }


def test_each_message_without_a_chat_goes_back_to_the_person_it_was_with(tmp_path: Path) -> None:
    found = _by_conversation(tmp_path)
    assert ORPHAN_CHAT not in found  # nobody is mixed together any more
    # Ana had a conversation: what was with her, either way, joins it
    assert found["chat-ana"] == ("individual", {"g-1", "g-2", "g-3", "g-11"})
    # João and Rui had none: a conversation of their own each, with the owner's messages to them
    joao = next(v for k, v in found.items() if JOAO in k)
    rui = next(v for k, v in found.items() if RUI in k)
    assert joao == ("individual", {"g-4", "g-5"})
    assert rui == ("individual", {"g-6", "g-9", "g-10"})


def test_a_message_sent_to_nobody_recorded_is_kept_apart_and_a_lost_groups_events_go(
    tmp_path: Path,
) -> None:
    found = _by_conversation(tmp_path)
    assert found[NO_RECIPIENT] == ("group", {"g-7"})  # not a person: with the groups
    everything = set().union(*(ids for _, ids in found.values()))
    assert "g-8" not in everything


def test_the_conversations_are_named_for_the_person(tmp_path: Path) -> None:
    batches = IMessageParser(spam_mode="disabled").parse(_database(tmp_path))
    rui = next(b.conversation for b in batches if RUI in b.conversation.source_id)
    assert rui.participants == (RUI,) and rui.name


def test_importing_again_replaces_the_old_mixed_conversation(
    chattr_root: Path, tmp_path: Path
) -> None:
    settings = Settings.load(chattr_root)
    database = commands.central_database(settings)
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    with DatabaseManager(database) as db:  # as an earlier version left it
        conn = db._db
        conn.execute(
            "INSERT INTO conversations (id, service, source_id, type, name) VALUES ('imessage:old', 'iMessage', ?, 'individual', ?)",
            (ORPHAN_CHAT, ORPHAN_CHAT),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, source_id, timestamp_utc, content_type, body, is_from_owner, content_hash) "
            "VALUES ('m-old', 'imessage:old', 'g-2', '2024-05-01T10:02:00Z', 'text', 'x', 0, 'h')"
        )
        conn.commit()
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    with DatabaseManager(database) as db:
        conn = db._db
        assert not conn.execute(
            "SELECT 1 FROM conversations WHERE source_id = ?", (ORPHAN_CHAT,)
        ).fetchone()
        assert not conn.execute("SELECT 1 FROM messages WHERE id = 'm-old'").fetchone()


def test_a_reply_with_no_recipient_follows_the_message_it_answered(tmp_path: Path) -> None:
    """Messages records, for each message, the one it came after in its conversation
    (`reply_to_guid`). A reply of the owner's that names nobody goes where that message went,
    and so does a run of replies after it; each says how it was placed."""
    batches = IMessageParser(spam_mode="disabled").parse(_database(tmp_path))
    records = {r.source_id: r for b in batches for r in b.records}
    for guid in ("g-9", "g-10", "g-11"):
        assert records[guid].is_from_owner
        assert records[guid].service_specific.get("placed_by") == "reply_to"
    assert "placed_by" not in records["g-7"].service_specific  # nothing to follow: kept apart


def test_importing_again_moves_replies_out_of_the_no_recipient_conversation(
    chattr_root: Path, tmp_path: Path
) -> None:
    """A box imported before replies were followed has them under 'Sent, recipient not recorded';
    the next full import takes them out of it, so none is there twice."""
    settings = Settings.load(chattr_root)
    database = commands.central_database(settings)
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    with DatabaseManager(database) as db:  # as the previous version left it
        conn = db._db
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, service, source_id, type, name) VALUES ('imessage:nr', 'iMessage', ?, 'group', 'x')",
            (NO_RECIPIENT,),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, source_id, timestamp_utc, content_type, body, is_from_owner, content_hash) "
            "VALUES ('m-stale', (SELECT id FROM conversations WHERE source_id = ?), 'g-9', '2024-05-01T10:09:00Z', 'text', 'x', 1, 'h')",
            (NO_RECIPIENT,),
        )
        conn.commit()
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    with DatabaseManager(database) as db:
        rows = db._db.execute("SELECT id FROM messages WHERE source_id = 'g-9'").fetchall()
    assert len(rows) == 1 and rows[0][0] != "m-stale"
