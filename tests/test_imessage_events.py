"""iMessage: edited and unsent messages, replies, and the events of a group.

Ported from imessage-exporter 5.0.1 (`message_types/edited.rs`, `models/group_action.rs`); see NOTICE.
"""

from __future__ import annotations

import plistlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fixtures import family
from fixtures.builders.imessage import SCHEMA, apple_time, attributed_body

from chattr import commands
from chattr.config import Settings
from chattr.imessage_events import announcement, edit_history
from chattr.parsers.imessage_parser import IMessageParser

ANA, JOAO = family.ANA.phones[0], family.JOAO.phones[0]
EXTRA = """
ALTER TABLE message ADD COLUMN message_summary_info BLOB;
ALTER TABLE message ADD COLUMN group_title TEXT;
ALTER TABLE message ADD COLUMN share_status INTEGER DEFAULT 0;
ALTER TABLE message ADD COLUMN share_direction INTEGER DEFAULT 0;
ALTER TABLE message ADD COLUMN thread_originator_part TEXT;
ALTER TABLE message ADD COLUMN date_retracted INTEGER DEFAULT 0;
"""


def _summary(
    edits: dict[int, list[tuple[int, str]]] | None = None,
    unsent: list[int] | None = None,
    parts: int = 1,
) -> bytes:
    """A `message_summary_info` blob as Messages writes it (a binary property list)."""
    root: dict[str, object] = {"otr": {str(n): {"lo": 0, "le": 5} for n in range(parts)}}
    if edits:
        root["ec"] = {
            str(part): [{"d": stamp, "t": attributed_body(text)} for stamp, text in events]
            for part, events in edits.items()
        }
    if unsent:
        root["rp"] = unsent
    return plistlib.dumps(root, fmt=plistlib.FMT_BINARY)


def _seconds(moment: datetime) -> int:
    """Seconds since Apple's epoch, as edit times are stored."""
    return int((moment - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds())


# --- Edit history -----------------------------------------------------------------------------


def test_each_edit_is_read_with_its_time_and_text() -> None:
    first = _seconds(datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc))
    history = edit_history(_summary({0: [(first, "see you at 5"), (first + 60, "see you at 6")]}))
    assert history.unsent_parts == [] and not history.fully_unsent
    assert [(e.part, e.text) for e in history.edits] == [(0, "see you at 5"), (0, "see you at 6")]
    assert history.edits[0].at.isoformat() == "2024-05-01T10:00:00+00:00"
    assert history.edits[1].at.isoformat() == "2024-05-01T10:01:00+00:00"


def test_unsent_parts_are_named_and_a_message_can_be_wholly_unsent() -> None:
    assert edit_history(_summary(unsent=[0])).fully_unsent
    partly = edit_history(_summary(unsent=[1], parts=2))
    assert partly.unsent_parts == [1] and not partly.fully_unsent


def test_no_summary_or_a_damaged_one_is_no_history() -> None:
    for blob in (None, b"", b"not a plist", plistlib.dumps({"other": 1}, fmt=plistlib.FMT_BINARY)):
        history = edit_history(blob)
        assert history.edits == [] and history.unsent_parts == [] and not history.fully_unsent


# --- Group events -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "text"),
    [
        (
            {"item_type": 1, "group_action_type": 0, "other_handle": 7, "handle_id": 3},
            "Ana added João to the conversation.",
        ),
        (
            {"item_type": 1, "group_action_type": 0, "other_handle": 3, "handle_id": 3},
            "Ana changed their phone number.",
        ),
        (
            {"item_type": 1, "group_action_type": 1, "other_handle": 7, "handle_id": 3},
            "Ana removed João from the conversation.",
        ),
        (
            {"item_type": 2, "group_title": "Costa Family"},
            "Ana named the conversation “Costa Family”.",
        ),
        ({"item_type": 2, "group_title": ""}, "Ana removed the conversation's name."),
        ({"item_type": 3, "group_action_type": 0}, "Ana left the conversation."),
        ({"item_type": 3, "group_action_type": 1}, "Ana changed the group photo."),
        ({"item_type": 3, "group_action_type": 2}, "Ana removed the group photo."),
        ({"item_type": 3, "group_action_type": 4}, "Ana changed the conversation's background."),
        ({"item_type": 3, "group_action_type": 6}, "Ana removed the conversation's background."),
        ({"item_type": 4, "share_status": 0}, "Ana started sharing location."),
        ({"item_type": 4, "share_status": 1}, "Ana stopped sharing location."),
        ({"item_type": 5}, "Ana kept an audio message."),
        ({"item_type": 7}, "Sharing of the Emergency SOS transcript stopped."),
        ({"item_type": 8}, "A location was sent to emergency services."),
        (
            {"item_type": 3, "group_action_type": 99},
            "Ana made a change to the group that Caixa does not know (type 3, action 99).",
        ),
        ({"item_type": 12}, "Messages recorded an event Caixa does not know (type 12)."),
    ],
)
def test_events_read_as_a_sentence(row: dict[str, object], text: str) -> None:
    names = {3: "Ana", 7: "João"}
    assert announcement({"handle_id": 3, **row}, actor="Ana", name_of=names.get) == text


def test_an_ordinary_message_is_not_an_event() -> None:
    assert announcement({"item_type": 0}, actor="Ana", name_of=lambda _: None) is None


def test_an_event_with_nobody_behind_it_names_nobody() -> None:
    said = announcement({"item_type": 2, "group_title": "Trip"}, actor=None, name_of=lambda _: None)
    assert said == "The conversation was named “Trip”."
    assert (
        announcement({"item_type": 3, "group_action_type": 0}, actor=None, name_of=lambda _: None)
        == "Someone left the conversation."
    )


# --- Through the importer and onto the page -------------------------------------------------------


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA + EXTRA)
    db.execute(
        "INSERT INTO handle (ROWID, id, service) VALUES (1, ?, 'iMessage'), (2, ?, 'iMessage')",
        (ANA, JOAO),
    )
    db.execute(
        "INSERT INTO chat (ROWID, guid, style, chat_identifier, service_name, display_name) VALUES (1, 'chat-1', 43, 'chat1', 'iMessage', 'Costa Family')"
    )
    db.execute("INSERT INTO chat_handle_join VALUES (1, 1), (1, 2)")
    edited_at = _seconds(datetime(2024, 5, 1, 10, 5, tzinfo=timezone.utc))

    def add(rowid: int, minute: int, handle: int, text: str | None = None, **more: object) -> None:
        columns = {
            "ROWID": rowid,
            "guid": f"g-{rowid}",
            "text": text,
            "service": "iMessage",
            "handle_id": handle,
            "date": apple_time(family.at(2024, 5, 1, 10, minute)),
            "is_from_me": 0,
            **more,
        }
        marks = ", ".join("?" for _ in columns)
        db.execute(
            f"INSERT INTO message ({', '.join(columns)}) VALUES ({marks})", list(columns.values())
        )
        db.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (rowid,))

    add(1, 0, 1, "Where shall we meet?")
    add(2, 1, 2, "At the café", thread_originator_guid="g-1", thread_originator_part="0:0:20")
    add(
        3,
        2,
        1,
        "see you at 6",
        date_edited=apple_time(family.at(2024, 5, 1, 10, 6)),
        message_summary_info=_summary(
            {0: [(edited_at - 240, "see you at 5"), (edited_at, "see you at 6")]}
        ),
    )
    add(
        4,
        3,
        2,
        None,
        date_edited=apple_time(family.at(2024, 5, 1, 10, 7)),
        message_summary_info=_summary(unsent=[0]),
    )
    add(5, 4, 1, None, item_type=2, group_title="Costa Family")
    add(6, 5, 1, None, item_type=1, group_action_type=0, other_handle=2)
    add(7, 6, 2, None, item_type=3, group_action_type=0)
    db.commit()
    db.close()
    return path


def _records(tmp_path: Path):
    (batch,) = IMessageParser(spam_mode="disabled", aliases={ANA: "Ana", JOAO: "João"}).parse(
        _database(tmp_path)
    )
    return {r.source_id: r for r in batch.records}


def test_a_reply_points_at_what_it_answers(tmp_path: Path) -> None:
    records = _records(tmp_path)
    assert records["g-2"].reply_to == "g-1" and records["g-1"].reply_to is None


def test_an_edited_message_keeps_its_history(tmp_path: Path) -> None:
    record = _records(tmp_path)["g-3"]
    assert record.body == "see you at 6"
    edits = record.service_specific["edits"]
    assert [e["text"] for e in edits] == ["see you at 5", "see you at 6"]
    assert edits[0]["at"].startswith("2024-05-01T10:01:00")
    expected = family.at(2024, 5, 1, 10, 6).astimezone(timezone.utc).isoformat()
    assert record.service_specific["edited_at"] == expected


def test_an_unsent_message_says_so_instead_of_being_blank(tmp_path: Path) -> None:
    record = _records(tmp_path)["g-4"]
    assert record.content_type == "system" and record.body == "João unsent a message."
    assert record.service_specific["unsent"] is True


def test_group_events_are_sentences_not_empty_messages(tmp_path: Path) -> None:
    records = _records(tmp_path)
    assert records["g-5"].body == "Ana named the conversation “Costa Family”."
    assert records["g-6"].body == "Ana added João to the conversation."
    assert records["g-7"].body == "João left the conversation."
    assert {records[g].content_type for g in ("g-5", "g-6", "g-7")} == {"system"}
    assert records["g-5"].service_specific["event"] == {"item_type": 2, "group_action_type": 0}


def test_the_page_shows_replies_edits_and_events(chattr_root: Path, tmp_path: Path) -> None:
    settings = Settings.load(chattr_root)
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    index = commands.generate_html(settings, out=tmp_path / "out")
    assert index is not None
    page = next((tmp_path / "out" / "Groups").rglob("(2024) *.html")).read_text(encoding="utf-8")
    # the reply links to the message it answers, with a few of its words
    assert (
        'class="reply"' in page and "Where shall we meet?" in page.split('class="reply"')[1][:300]
    )
    assert 'href="#m-' in page.split('class="reply"')[1][:200]
    # the edit history can be opened
    assert "Edited" in page and "see you at 5" in page and "<details" in page
    # events and the unsent message are system lines
    for text in ("unsent a message.", "named the conversation", "left the conversation."):
        assert text in page
