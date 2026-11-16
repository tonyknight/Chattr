"""Facebook's friends lists: who the owner is (or was) friends with, to keep only those threads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chattr import commands
from chattr.config import Settings
from chattr.meta_friends import Friends, find_friend_files, load_friends


def _mojibake(text: str) -> str:
    return text.encode("utf-8").decode("latin-1")


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    return path


def _export(root: Path, layout: str = "connections/friends") -> Path:
    """A Meta export with a friends list and a removed-friends list, in one of its layouts."""
    names = {
        "connections/friends": ("your_friends.json", "friends_v2", "deleted_friends_v2"),
        "friends_and_followers": ("friends.json", "friends_v2", "deleted_friends_v2"),
        "friends": ("friends.json", "friends", "deleted_friends"),
    }[layout]
    _write(root / layout / names[0], {names[1]: [
        {"name": _mojibake("João Costa"), "timestamp": 1500000000},
        {"name": "Ana Costa", "timestamp": 1500000001},
    ]})  # fmt: skip
    _write(root / layout / "removed_friends.json", {names[2]: [
        {"name": "Rui Pereira", "timestamp": 1600000000},
    ]})  # fmt: skip
    _write(root / layout / "received_friend_requests.json", {"received_requests_v2": [
        {"name": "A Stranger", "timestamp": 1600000000},
    ]})  # fmt: skip
    thread = (
        root / "your_facebook_activity" / "messages" / "inbox" / "anacosta_1" / "message_1.json"
    )
    _write(thread, {"participants": [{"name": "Ana Costa"}], "messages": []})
    return thread


@pytest.mark.parametrize("layout", ["connections/friends", "friends_and_followers", "friends"])
def test_the_friends_files_are_found_from_a_thread_and_read(tmp_path: Path, layout: str) -> None:
    thread = _export(tmp_path, layout)
    files = find_friend_files(thread)
    assert sorted(p.name for p in files) == sorted(
        [
            "your_friends.json" if layout == "connections/friends" else "friends.json",
            "removed_friends.json",
        ]
    )
    friends = load_friends(files)
    assert friends.knows("João Costa") and friends.knows("ana  COSTA")  # encoding and spacing
    assert friends.knows("Rui Pereira")  # a removed friend still counts
    assert not friends.knows("A Stranger")  # a friend request is not a friend
    assert friends.name_of("ana costa") == "Ana Costa"
    assert len(friends) == 3


def test_a_folder_or_a_file_given_by_hand_is_read(tmp_path: Path) -> None:
    _export(tmp_path / "export")
    folder = tmp_path / "export" / "connections"
    assert len(load_friends(find_friend_files(folder))) == 3
    single = tmp_path / "export" / "connections" / "friends" / "your_friends.json"
    assert len(load_friends(find_friend_files(single))) == 3  # its removed list beside it too


def test_nothing_is_found_where_there_is_no_list(tmp_path: Path) -> None:
    thread = _write(tmp_path / "messages" / "inbox" / "x_1" / "message_1.json", {"messages": []})
    assert find_friend_files(thread) == []
    assert len(load_friends([])) == 0


def test_a_damaged_file_is_skipped(tmp_path: Path) -> None:
    bad = tmp_path / "friends" / "friends.json"
    bad.parent.mkdir()
    bad.write_text("not json")
    assert len(load_friends([bad])) == 0


def test_a_thread_with_nobody_on_the_list_is_left_out_and_nothing_is_stored(
    chattr_root: Path, tmp_path: Path
) -> None:
    thread = _write(tmp_path / "inbox" / "seller_1" / "message_1.json", {
        "participants": [{"name": "Marketplace Buyer"}, {"name": "Maria Costa"}],
        "messages": [{"sender_name": "Marketplace Buyer", "timestamp_ms": 1600000000000,
                      "content": "Is this still available?"}],
        "thread_path": "inbox/seller_1",
    })  # fmt: skip
    settings = Settings.load(chattr_root)
    friends = Friends.of(["Ana Costa"])
    with pytest.raises(commands.LeftOut) as left:
        commands.convert(settings, "messenger", thread, keep=friends.in_conversation)
    assert left.value.conversation.name == "inbox/seller_1"
    assert not commands.central_database(settings).exists()
