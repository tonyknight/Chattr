"""`chattr-cli convert mail`: MBOX threads into the archive, with offsets and attachments."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings


def _convert(root: Path, export: Path, **options):
    return commands.convert(Settings.load(root), "mail", export, **options)


def _rows(root: Path, sql: str, *params):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql, params).fetchall()


def test_threads_become_conversations_of_email_messages(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(chattr_root, fixture_family.paths["mbox"])
    assert result.service == "Mail" and result.counts.inserted == 4
    assert _rows(
        chattr_root,
        "SELECT name, type, COUNT(*) FROM conversations c JOIN messages m ON m.conversation_id = c.id GROUP BY c.id ORDER BY 3",
    ) == [
        ("Wedding update", "individual", 1),
        ("Fotos de São Martinho", "individual", 3),
    ]
    assert _rows(chattr_root, "SELECT DISTINCT content_type FROM messages") == [("email",)]


def test_the_senders_offset_reaches_the_database(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"])
    rows = _rows(
        chattr_root,
        "SELECT timestamp_utc, tz_offset_minutes, tz_assumed FROM messages "
        "WHERE timestamp_utc LIKE '2019-11-02%' ORDER BY timestamp_utc",
    )
    assert rows[0] == ("2019-11-02T09:30:00+00:00", 0, 0)
    assert rows[1][1:] == (-300, 0)  # Maria wrote from UTC-5: 09:15 local, 14:15 UTC
    assert rows[1][0] == "2019-11-02T14:15:00+00:00"


def test_headers_and_both_bodies_are_stored(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"])
    ((headers, specific),) = _rows(
        chattr_root,
        "SELECT mail_headers, service_specific FROM messages WHERE body LIKE 'Olá Maria%'",
    )
    assert json.loads(headers)["labels"] == ["Inbox", "Família"]
    assert "<b>São Martinho</b>" in json.loads(specific)["body_html"]


def test_attachments_are_kept_by_hash_in_the_mail_folder(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"])
    ((sha, name, stored),) = _rows(
        chattr_root, "SELECT sha256, original_filename, storage_path FROM attachments"
    )
    store = chattr_root / "Output" / "Mail" / "Attachments"
    assert name == "familia.png" and stored == f"{sha[:2]}/{sha}.png"
    assert (store / stored).is_file()


def test_the_attachment_folder_can_be_chosen(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"], attachments_dir=tmp_path / "Elsewhere")
    assert list((tmp_path / "Elsewhere").rglob("*.png"))
    assert not (chattr_root / "Output" / "Mail" / "Attachments").exists()


def test_reimporting_changes_nothing(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"])
    again = _convert(chattr_root, fixture_family.paths["mbox"])
    assert (again.counts.inserted, again.counts.updated, again.counts.unchanged) == (0, 0, 4)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM attachments") == [(1,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM conversations") == [(2,)]


def test_owner_by_address_or_name_and_aliases(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({family.JOAO.emails[0]: "João (brother)"}), encoding="utf-8")
    _convert(
        chattr_root, fixture_family.paths["mbox"], owner=family.OWNER.emails[0], aliases=aliases
    )
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE is_from_owner = 1") == [(1,)]
    assert ("João (brother)",) in _rows(chattr_root, "SELECT display_name FROM handles")


def test_the_state_file_is_updated(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, fixture_family.paths["mbox"])
    state = json.loads((chattr_root / "Services" / "Mail.json").read_text())["state"]
    assert state["total_messages_imported"] == 4
    assert (chattr_root / "Output" / "Mail").is_dir()


def test_json_route_and_html(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = _convert(chattr_root, fixture_family.paths["mbox"], route="service", html=True)
    assert (
        result.json_path
        == chattr_root / "Output" / "Mail" / f"{fixture_family.paths['mbox'].stem}.json"
    )
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 4
    pages = list((chattr_root / "Output" / "Mail").glob("*.html"))
    assert len(pages) == 2 and any("Fotos de São Martinho" in p.name for p in pages)
    assert not commands.central_database(Settings.load(chattr_root)).exists()


def test_html_for_the_central_archive_is_one_page_per_thread(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(chattr_root, fixture_family.paths["mbox"], html=True)
    assert result.html_path is not None
    assert len(list((chattr_root / "Output" / "Chattr").glob("*.html"))) == 2


def test_the_subcommand(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--root", str(chattr_root), "convert", "mail", str(fixture_family.paths["mbox"])]
    assert cli.main(args) == 0
    assert "Mail: 4 new" in capsys.readouterr().out


def test_imessage_flags_are_rejected_for_mail(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--root",
                str(chattr_root),
                "convert",
                "mail",
                str(fixture_family.paths["mbox"]),
                "--region",
                "US",
            ]
        )
    assert "iMessage only" in capsys.readouterr().err


def test_preflight_prepares_the_mail_folder_and_settings(chattr_root: Path) -> None:
    assert (chattr_root / "Output" / "Mail").is_dir()
    assert (chattr_root / "Services" / "Mail.json").is_file()
