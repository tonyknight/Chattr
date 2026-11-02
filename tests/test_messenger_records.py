"""Messenger and Instagram threads reach the archive with their people, media, reactions, links."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings

JOAO = family.JOAO.usernames["messenger"]


def _convert(root: Path, service: str, export: Path, **options):
    return commands.convert(Settings.load(root), service, export, **options)


def _rows(root: Path, sql: str, *params):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql, params).fetchall()


def test_the_conversation_is_named_by_its_title_and_keyed_by_its_thread(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, "messenger", fixture_family.paths["messenger"])
    assert _rows(chattr_root, "SELECT service, source_id, type, name FROM conversations") == [
        ("Messenger", "inbox/joaocosta_1234567890", "individual", JOAO)
    ]


def test_messages_times_and_senders(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(
        chattr_root,
        "messenger",
        fixture_family.paths["messenger"],
        owner=family.OWNER.usernames["messenger"],
    )
    rows = _rows(
        chattr_root,
        "SELECT timestamp_utc, is_from_owner, tz_assumed FROM messages ORDER BY timestamp_utc",
    )
    assert len(rows) == 5
    assert rows[0] == ("2019-07-20T18:00:00+00:00", 0, 0)  # an exact UTC instant, not assumed
    assert sum(r[1] for r in rows) == 2


def test_attachments_reactions_and_links(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, "messenger", fixture_family.paths["messenger"])
    photo = next(fixture_family.paths["messenger"].parent.rglob("photos/*.png"))
    assert _rows(chattr_root, "SELECT sha256, original_filename, mime_type FROM attachments") == [
        (hashlib.sha256(photo.read_bytes()).hexdigest(), photo.name, "image/png")
    ]
    assert _rows(chattr_root, "SELECT type, emoji FROM reactions") == [("Reaction", "❤️")]
    ((body, extra),) = _rows(
        chattr_root,
        "SELECT body, service_specific FROM messages WHERE service_specific LIKE '%shared_link%'",
    )
    assert body == "The restaurant: https://example.org/tasca-do-ze"
    assert json.loads(extra) == {
        "shared_link": "https://example.org/tasca-do-ze",
        "link_text": "Tasca do Zé — cozinha tradicional",
    }
    assert _rows(chattr_root, "SELECT content_type FROM messages WHERE body IS NULL") == [
        ("media",)
    ]


def test_reimporting_changes_nothing(chattr_root: Path, fixture_family: FamilyExports) -> None:
    first = _convert(chattr_root, "messenger", fixture_family.paths["messenger"])
    second = _convert(chattr_root, "messenger", fixture_family.paths["messenger"])
    assert (second.counts.inserted, second.counts.unchanged) == (0, first.counts.inserted)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM reactions") == [(1,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM attachments") == [(1,)]


def test_instagram_is_its_own_service(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = _convert(chattr_root, "instagram", fixture_family.paths["instagram"])
    assert result.service == "Instagram"
    assert _rows(chattr_root, "SELECT service, name FROM conversations") == [
        ("Instagram", family.RUI.usernames["instagram"])
    ]
    state = json.loads((chattr_root / "Services" / "Instagram.json").read_text())["state"]
    assert state["total_messages_imported"] == 2
    assert (chattr_root / "Output" / "Instagram").is_dir()


def test_more_than_two_participants_make_a_group(chattr_root: Path, tmp_path: Path) -> None:
    folder = tmp_path / "inbox" / "family_1"
    folder.mkdir(parents=True)
    (folder / "message_1.json").write_text(
        json.dumps(
            {
                "participants": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
                "title": "Family",
                "thread_path": "inbox/family_1",
                "messages": [
                    {"sender_name": "A", "timestamp_ms": 1_700_000_000_000, "content": "hi"}
                ],
            }
        )
    )
    _convert(chattr_root, "messenger", folder / "message_1.json")
    assert _rows(chattr_root, "SELECT type, name FROM conversations") == [("group", "Family")]


def test_json_and_html_files_are_named_by_the_title_not_the_thread_path(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(
        chattr_root, "messenger", fixture_family.paths["messenger"], route="service", html=True
    )
    assert result.json_path is not None and result.json_path.name == f"{JOAO}.json"
    site = chattr_root / "Output" / "Messenger"
    assert result.html_path == site / "index.html"
    assert (site / "Messages" / JOAO / f"(2019) {JOAO}.html").is_file()


@pytest.mark.parametrize("service", ["messenger", "instagram"])
def test_the_subcommand_converts_both(
    chattr_root: Path, fixture_family: FamilyExports, service: str
) -> None:
    export = fixture_family.paths[service]
    assert cli.main(["--root", str(chattr_root), "convert", service, str(export)]) == 0
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages")[0][0] > 0
