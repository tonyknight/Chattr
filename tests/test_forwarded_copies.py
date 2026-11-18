"""Google Voice can forward the texts people send to the owner's phone as ordinary SMS, and Messages
keeps them: the same message twice, once from Google Voice (with both sides of the conversation)
and once in iMessage's database (the other person's side only, often in a conversation since
deleted). The Messages copy goes; Google Voice's record stays."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from chattr.database_manager import DatabaseManager
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord

ME = "Maria Costa"
RUI_GV, RUI_SMS = "+1 (555) 010-9876", "+15550109876"  # the same number, written two ways
ANA = "+15550101111"


def _at(minute: int) -> datetime:
    return datetime(2024, 3, 1, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _msg(minute: int, sender: str | None, text: str, *files: AttachmentRecord) -> MessageRecord:
    return MessageRecord(
        timestamp=_at(minute), sender=sender, sender_name=sender or ME, body=text,
        is_from_owner=sender is None, attachments=files,
    )  # fmt: skip


def _archive(tmp_path: Path) -> Path:
    path = tmp_path / "chattr.sqlite"
    picture = AttachmentRecord("c" * 64, "p.jpg", "image/jpeg", 3, "People/Rui/iMessage/p.jpg")
    with DatabaseManager(path) as db:
        db.upsert(
            ConversationRecord("Google Voice", "gv-rui", name="Rui", participants=(RUI_GV,)),
            [_msg(0, RUI_GV, "Are you coming tonight?"), _msg(3, None, "Yes, at 8"),
             _msg(10, RUI_GV, "Great  see you")],
        )  # fmt: skip
        db.upsert(  # the forwarded copies, kept by Messages in a chat since deleted
            ConversationRecord("iMessage", "no-chat:" + RUI_SMS, name=RUI_SMS, participants=(RUI_SMS,)),
            [_msg(1, RUI_SMS, "Are you coming tonight?", picture),
             _msg(11, RUI_SMS, "Great see you")],
        )  # fmt: skip
        db.upsert(  # a real iMessage conversation: same words, but another person, or much later
            ConversationRecord("iMessage", "chat-ana", name=ANA, participants=(ANA,)),
            [_msg(2, ANA, "Are you coming tonight?"), _msg(4, None, "No")],
        )  # fmt: skip
        db.upsert(
            ConversationRecord("iMessage", "chat-rui", name=RUI_SMS, participants=(RUI_SMS,)),
            [_msg(600, RUI_SMS, "Are you coming tonight?")],
        )  # fmt: skip
        db.commit()
    return path


def _bodies(db: DatabaseManager, service: str) -> list[str]:
    return [
        r[0]
        for r in db.connection.execute(
            "SELECT m.body FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.service = ? ORDER BY m.timestamp_utc",
            (service,),
        )
    ]


def test_a_forwarded_copy_goes_and_google_voice_keeps_both_sides(tmp_path: Path) -> None:
    with DatabaseManager(_archive(tmp_path)) as db:
        dropped = db.drop_forwarded_copies()
        assert dropped.messages == 2
        assert dropped.files == ["People/Rui/iMessage/p.jpg"]
        assert _bodies(db, "Google Voice") == [
            "Are you coming tonight?",
            "Yes, at 8",
            "Great  see you",
        ]
        # what stays in iMessage: Ana's (another number) and Rui's ten hours later
        assert _bodies(db, "iMessage") == [
            "Are you coming tonight?",
            "No",
            "Are you coming tonight?",
        ]
        # the conversation that held only copies is gone
        assert not db.connection.execute(
            "SELECT 1 FROM conversations WHERE source_id = ?", ("no-chat:" + RUI_SMS,)
        ).fetchone()
        assert db.drop_forwarded_copies().messages == 0  # safe to repeat


def test_nothing_goes_without_google_voice(tmp_path: Path) -> None:
    path = tmp_path / "chattr.sqlite"
    with DatabaseManager(path) as db:
        db.upsert(
            ConversationRecord("iMessage", "chat-rui", name=RUI_SMS, participants=(RUI_SMS,)),
            [_msg(1, RUI_SMS, "hello")],
        )
        db.commit()
        assert db.drop_forwarded_copies().messages == 0
