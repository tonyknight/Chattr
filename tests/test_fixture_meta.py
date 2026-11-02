"""The synthetic Messenger and Instagram exports, including Meta's double-encoded text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fixtures import family
from fixtures.builders.meta import build_meta


def fix_meta_encoding(text: str) -> str:
    """The standard fix for Meta exports: undo the UTF-8-as-latin-1 double encoding."""
    return text.encode("latin-1").decode("utf-8")


@pytest.fixture(scope="module")
def threads(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_meta(tmp_path_factory.mktemp("meta"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_messenger_and_instagram_threads_exist(threads: dict[str, Path]) -> None:
    assert set(threads) == {"messenger", "instagram"}
    assert threads["messenger"].name == "message_1.json"
    assert "messages" in threads["messenger"].parts and "inbox" in threads["messenger"].parts
    assert threads["instagram"].name == "message_1.json"


def test_messages_are_newest_first_with_millisecond_timestamps(threads: dict[str, Path]) -> None:
    for path in threads.values():
        stamps = [m["timestamp_ms"] for m in _load(path)["messages"]]
        assert stamps == sorted(stamps, reverse=True)
        assert all(s > 10**12 for s in stamps)


def test_text_is_double_encoded_and_the_fix_recovers_it(threads: dict[str, Path]) -> None:
    data = _load(threads["messenger"])
    raw_names = {p["name"] for p in data["participants"]}
    assert family.JOAO.name not in raw_names
    assert family.JOAO.name in {fix_meta_encoding(n) for n in raw_names}
    contents = [m.get("content", "") for m in data["messages"]]
    assert any("Ã" in c for c in contents), "expected mojibake in raw content"
    assert any("ç" in fix_meta_encoding(c) for c in contents)


def test_messenger_has_a_photo_a_share_and_a_reaction(threads: dict[str, Path]) -> None:
    path = threads["messenger"]
    messages = _load(path)["messages"]
    photos = [p for m in messages for p in m.get("photos", [])]
    assert photos
    # message_1.json → <thread> → inbox → messages → your_facebook_activity → export root
    export_root = path.parents[4]
    for photo in photos:
        assert (export_root / photo["uri"]).is_file(), photo["uri"]
    assert any("share" in m and "link" in m["share"] for m in messages)
    assert any(m.get("reactions") for m in messages)


def test_instagram_thread_is_with_rui(threads: dict[str, Path]) -> None:
    data = _load(threads["instagram"])
    names = {fix_meta_encoding(p["name"]) for p in data["participants"]}
    assert family.RUI.usernames["instagram"] in names
