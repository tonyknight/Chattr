"""Messenger and Instagram exports: encoding, UTC times, whole threads, links, attachments."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fixtures import FamilyExports, family
from fixtures.builders.meta import mojibake

from chattr.parsers.messenger_parser import InstagramParser, MessengerParser

JOAO, OWNER = family.JOAO.usernames["messenger"], family.OWNER.usernames["messenger"]


@pytest.fixture(scope="module")
def messenger(fixture_family: FamilyExports):
    parser = MessengerParser()
    return parser, parser.parse(str(fixture_family.paths["messenger"]))


def _by_text(messages, text):
    (found,) = [m for m in messages if (m["message"] or "").startswith(text)]
    return found


def test_messages_come_oldest_first_with_decoded_names_and_text(messenger) -> None:
    _, messages = messenger
    assert [m["author"] for m in messages] == [JOAO, OWNER, JOAO, JOAO, OWNER]
    assert messages[0]["message"] == "Olá mana! Já chegaste a Lisboa?"
    assert messages[-1]["message"] == "Perfeito, até às oito. Beijinhos e saudações à família!"
    assert [m["timestamp"] for m in messages] == sorted(m["timestamp"] for m in messages)


def test_times_are_utc_not_the_machines_local_time(messenger, monkeypatch) -> None:
    _, messages = messenger
    assert messages[0]["timestamp"] == datetime(2019, 7, 20, 18, 0, tzinfo=timezone.utc)
    assert messages[0]["timestamp"].utcoffset() == timezone.utc.utcoffset(None)
    assert messages[0]["precision"] == "second"


def test_the_thread_is_exposed_for_naming_the_conversation(messenger) -> None:
    parser, _ = messenger
    assert parser.thread["title"] == JOAO
    assert parser.thread["participants"] == [JOAO, OWNER]
    assert parser.thread["thread_path"] == "inbox/joaocosta_1234567890"


def test_encoding_is_fixed_in_reactions_and_link_text(messenger) -> None:
    _, messages = messenger
    (reaction,) = _by_text(messages, "Cheguei")["reactions"]
    assert reaction == {"emoji": "❤️", "actor": JOAO}
    assert _by_text(messages, "The restaurant")["link_text"] == "Tasca do Zé — cozinha tradicional"


def test_shared_links_are_stored_not_folded_into_the_body(messenger) -> None:
    _, messages = messenger
    shared = _by_text(messages, "The restaurant")
    assert shared["shared_link"] == "https://example.org/tasca-do-ze"
    assert "[Shared Link" not in shared["message"] and "Preview" not in shared["message"]
    assert shared["message"] == "The restaurant: https://example.org/tasca-do-ze"


def test_photos_are_recorded_with_their_files(messenger, fixture_family) -> None:
    _, messages = messenger
    (photo,) = [m for m in messages if m["type"] == "image"]
    assert photo["message"] is None
    (attachment,) = photo["attachments"]
    assert attachment["kind"] == "image" and attachment["name"].endswith(".png")
    assert Path(attachment["path"]).is_file()
    assert photo["attachment_path"] == attachment["path"]  # the single-file key still works


# --- Generated threads ----------------------------------------------------------------------


def _thread(tmp_path: Path, messages: list[dict], **extra) -> Path:
    folder = tmp_path / "inbox" / "thread_1"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"participants": [{"name": "A"}, {"name": "B"}], "messages": messages, **extra}
    path = folder / "message_1.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _msg(ms: int, sender: str = "A", **fields) -> dict:
    return {"sender_name": sender, "timestamp_ms": ms, **fields}


def test_a_thread_split_over_several_files_is_read_whole(tmp_path: Path) -> None:
    first = _thread(tmp_path, [_msg(3_000, content="three"), _msg(2_000, content="two")])
    (first.parent / "message_2.json").write_text(
        json.dumps({"messages": [_msg(1_000, content="one")]}), encoding="utf-8"
    )
    (first.parent / "message_10.json").write_text(
        json.dumps({"messages": [_msg(500, content="zero")]}), encoding="utf-8"
    )
    messages = MessengerParser().parse(str(first))
    assert [m["message"] for m in messages] == ["zero", "one", "two", "three"]


def test_text_that_is_already_utf8_is_left_alone(tmp_path: Path) -> None:
    path = _thread(tmp_path, [_msg(1_000, content="já está certo ❤️", sender="João")])
    (message,) = MessengerParser().parse(str(path))
    assert message["message"] == "já está certo ❤️" and message["author"] == "João"


def test_double_encoded_text_is_repaired(tmp_path: Path) -> None:
    path = _thread(tmp_path, [_msg(1_000, content=mojibake("já está"), sender=mojibake("João"))])
    (message,) = MessengerParser().parse(str(path))
    assert message["message"] == "já está" and message["author"] == "João"


def test_millisecond_precision_is_kept_when_present(tmp_path: Path) -> None:
    path = _thread(tmp_path, [_msg(1_700_000_000_250, content="x")])
    (message,) = MessengerParser().parse(str(path))
    assert message["precision"] == "millisecond" and message["timestamp"].microsecond == 250_000


@pytest.mark.parametrize(
    ("fields", "kind"),
    [
        ({"videos": [{"uri": "v.mp4"}]}, "video"),
        ({"audio_files": [{"uri": "a.mp4"}]}, "audio"),
        ({"files": [{"uri": "d.pdf"}]}, "document"),
        ({"sticker": {"uri": "s.png"}}, "sticker"),
        ({"gifs": [{"uri": "g.gif"}]}, "image"),
    ],
)
def test_every_attachment_kind_is_recorded(tmp_path: Path, fields: dict, kind: str) -> None:
    (message,) = MessengerParser().parse(str(_thread(tmp_path, [_msg(1_000, **fields)])))
    assert message["type"] == kind
    assert [a["kind"] for a in message["attachments"]] == [kind]


def test_all_photos_of_a_message_are_kept(tmp_path: Path) -> None:
    photos = [{"uri": f"p{i}.jpg"} for i in range(3)]
    (message,) = MessengerParser().parse(str(_thread(tmp_path, [_msg(1_000, photos=photos)])))
    assert [a["name"] for a in message["attachments"]] == ["p0.jpg", "p1.jpg", "p2.jpg"]


def test_calls_unsent_messages_and_group_events_keep_their_place(tmp_path: Path) -> None:
    path = _thread(
        tmp_path,
        [
            _msg(4_000, type="Subscribe", content="A added B to the group."),
            _msg(3_000, is_unsent=True),
            _msg(2_000, call_duration=125, type="Call"),
            _msg(1_000, content="hello"),
        ],
    )
    hello, call, unsent, event = MessengerParser().parse(str(path))
    assert call["type"] == "call" and call["call_duration"] == 125
    assert unsent["unsent"] is True and unsent["message"] is None
    assert event["type"] == "system"
    assert hello["type"] == "text"


def test_instagram_is_the_same_format_with_its_own_service(fixture_family) -> None:
    parser = InstagramParser()
    messages = parser.parse(str(fixture_family.paths["instagram"]))
    assert messages[0]["message"] == "Vi a tua foto do Douro — que vista!"
    assert parser.service == "Instagram" and MessengerParser().service == "Messenger"


def test_a_broken_file_gives_no_messages(tmp_path: Path) -> None:
    broken = tmp_path / "message_1.json"
    broken.write_text("{not json")
    assert MessengerParser().parse(str(broken)) == []
    assert MessengerParser().parse(str(tmp_path / "missing.json")) == []
