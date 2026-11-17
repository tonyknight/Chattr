"""Removing people from the archive: their one-to-one conversations and files go; what they wrote
in a group stays there; they are not listed to be named again."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from chattr.database_manager import DatabaseManager
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord

ME, ANA, SPAM = "Maria Costa", "Ana Costa", "+15550199"


def _at(day: int) -> datetime:
    return datetime(2024, 3, day, 10, 0, tzinfo=timezone.utc)


def _msg(day: int, sender: str | None, text: str, *files: AttachmentRecord) -> MessageRecord:
    mine = sender is None
    return MessageRecord(
        timestamp=_at(day), sender=sender, sender_name=ME if mine else sender, body=text,
        is_from_owner=mine, attachments=files,
    )  # fmt: skip


def _file(digest: str, path: str) -> AttachmentRecord:
    return AttachmentRecord(digest * 64, "photo.jpg", "image/jpeg", 3, path)


def _archive(tmp_path: Path) -> Path:
    path = tmp_path / "chattr.sqlite"
    with DatabaseManager(path) as db:
        db.upsert(
            ConversationRecord("iMessage", "spam", name=SPAM, participants=(SPAM,)),
            [_msg(1, SPAM, "You won!", _file("a", "People/+15550199/iMessage/a.jpg")),
             _msg(2, None, "stop")],
        )  # fmt: skip
        db.upsert(
            ConversationRecord("WhatsApp", "ana", name=ANA, participants=(ANA,)),
            [_msg(3, ANA, "Olá", _file("b", "People/Ana Costa/WhatsApp/b.jpg")),
             _msg(4, ANA, "the same picture again", _file("b", "People/Ana Costa/WhatsApp/b.jpg"))],
        )  # fmt: skip
        db.upsert(
            ConversationRecord("WhatsApp", "family", type="group", name="Family",
                               participants=(ANA, SPAM)),
            [_msg(5, ANA, "hi all"), _msg(6, SPAM, "hello"), _msg(7, None, "welcome")],
        )  # fmt: skip
        db.connection.execute("UPDATE handles SET person = ? WHERE identifier = ?", (ANA, ANA))
        db.commit()
    return path


def _only(*identifiers: str):
    return lambda service, identifier, person: identifier in identifiers


def test_a_removed_numbers_conversation_and_files_go(tmp_path: Path) -> None:
    with DatabaseManager(_archive(tmp_path)) as db:
        removed = db.remove_people(_only(SPAM))
        assert (removed.conversations, removed.messages) == (1, 2)  # theirs and the reply
        assert removed.files == ["People/+15550199/iMessage/a.jpg"]
        left = [r[0] for r in db.connection.execute("SELECT body FROM messages ORDER BY 1")]
        assert "You won!" not in left and "stop" not in left
        assert "hello" in left  # what the number wrote in the group stays in the group
        assert db.connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 2


def test_a_removed_person_is_not_listed_to_be_named_and_stays_unnamed(tmp_path: Path) -> None:
    with DatabaseManager(_archive(tmp_path)) as db:
        db.remove_people(_only(ANA))
        assert ANA not in [h.identifier for h in db.unresolved_handles()]
        db.name_handles("WhatsApp", lambda value: value)  # naming passes her by from now on
        person = db.connection.execute(
            "SELECT person FROM handles WHERE identifier = ?", (ANA,)
        ).fetchone()[0]
        assert person is None
        bodies = [r[0] for r in db.connection.execute("SELECT body FROM messages")]
        assert "Olá" not in bodies and "hi all" in bodies


def test_a_file_another_message_still_shows_is_kept(tmp_path: Path) -> None:
    path = _archive(tmp_path)
    with DatabaseManager(path) as db:
        db.upsert(
            ConversationRecord("WhatsApp", "family", type="group", name="Family"),
            [_msg(8, None, "forwarding it", _file("b", "People/Ana Costa/WhatsApp/b.jpg"))],
        )
        db.commit()
        removed = db.remove_people(_only(ANA))
        assert removed.files == []  # the group message still shows that file


def test_removing_is_repeatable_and_leaves_everyone_else(tmp_path: Path) -> None:
    with DatabaseManager(_archive(tmp_path)) as db:
        db.remove_people(_only(SPAM))
        again = db.remove_people(_only(SPAM))
        assert (again.conversations, again.messages, again.files) == (0, 0, [])
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE c.source_id = 'ana'"
            ).fetchone()[0]
            == 2
        )


def test_mail_and_the_owner_are_never_removed(tmp_path: Path) -> None:
    path = _archive(tmp_path)
    with DatabaseManager(path) as db:
        db.upsert(
            ConversationRecord("Mail", "thread", name="Hello", participants=("ana@example.org",)),
            [MessageRecord(timestamp=_at(9), sender="ana@example.org", sender_name=ANA,
                           content_type="email", body="mail")],
        )  # fmt: skip
        db.commit()
        removed = db.remove_people(lambda service, identifier, person: True)
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM messages WHERE content_type = 'email'"
            ).fetchone()[0]
            == 1
        )
        assert removed.conversations == 2  # the two one-to-one chats, not mail, not the group
