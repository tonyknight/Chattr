"""`--contacts`, `--aliases` and the owner at conversion time, and `chattr-cli people`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings


def _rows(root: Path, sql: str, *params):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql, params).fetchall()


def _whatsapp(fixture_family: FamilyExports) -> Path:
    return fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"


def test_whatsapp_senders_are_resolved_by_name_and_the_owner_is_derived(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = commands.convert(
        Settings.load(chattr_root),
        "whatsapp",
        _whatsapp(fixture_family),
        contacts=fixture_family.paths["contacts"],
        owner=family.OWNER.name,
    )
    assert result.unresolved == 0
    assert _rows(chattr_root, "SELECT identifier, person FROM handles ORDER BY identifier") == [
        (family.ANA.name, family.ANA.name),
        (family.OWNER.name, family.OWNER.name),
    ]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE is_from_owner = 1") == [(2,)]


def test_without_contacts_the_handles_are_counted_as_unresolved(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = commands.convert(Settings.load(chattr_root), "whatsapp", _whatsapp(fixture_family))
    assert result.unresolved == 2


def test_imessage_phone_numbers_and_emails_match_the_contacts(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    db_path = fixture_family.paths["imessage"]
    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        contacts=fixture_family.paths["contacts"],
        spam_mode="disabled",
        workers=1,
    )
    assert result.unresolved == 1  # the unknown number
    people = dict(_rows(chattr_root, "SELECT identifier, person FROM handles"))
    assert people[family.ANA.phones[0]] == family.ANA.name
    assert people[family.JOAO.emails[0]] == family.JOAO.name
    assert people[family.UNKNOWN_HANDLE] is None


def test_mail_and_google_voice_match_by_address_and_number(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    mail = commands.convert(
        settings,
        "mail",
        fixture_family.paths["mbox"],
        contacts=fixture_family.paths["contacts"],
        owner=family.OWNER.name,
    )
    voice = commands.convert(
        settings,
        "googlevoice",
        fixture_family.paths["google_voice"],
        contacts=fixture_family.paths["contacts"],
    )
    assert mail.unresolved == 0  # Maria is the owner, everyone else is in the contacts
    assert voice.unresolved == 1  # the stranger's number
    people = dict(_rows(chattr_root, "SELECT identifier, person FROM handles"))
    assert people[family.JOAO.emails[0]] == family.JOAO.name
    assert people[family.ANA.phones[0]] == family.ANA.name


def test_the_owner_and_contacts_can_come_from_the_settings(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    path = chattr_root / "config.json"
    data = json.loads(path.read_text())
    data["global"]["owner"] = {"name": family.OWNER.name, "identifiers": [family.OWNER.emails[0]]}
    data["global"]["contacts"]["vcf_file_path"] = str(fixture_family.paths["contacts"])
    path.write_text(json.dumps(data))
    result = commands.convert(Settings.load(chattr_root), "mail", fixture_family.paths["mbox"])
    assert result.unresolved == 0
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE is_from_owner = 1") == [(1,)]


def test_the_defaults_have_an_owner_section(chattr_root: Path) -> None:
    owner = json.loads((chattr_root / "config.json").read_text())["global"]["owner"]
    assert owner == {"name": "", "identifiers": []}


def test_resolved_names_reach_the_json(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = commands.convert(
        Settings.load(chattr_root),
        "mail",
        fixture_family.paths["mbox"],
        contacts=fixture_family.paths["contacts"],
        route="service",
    )
    assert result.json_path is not None
    messages = json.loads(result.json_path.read_text(encoding="utf-8"))["messages"]
    joao = [m for m in messages if m["sender"] == family.JOAO.emails[0]]
    assert joao and {m["person"] for m in joao} == {family.JOAO.name}


def test_resolved_names_reach_the_pages(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = commands.convert(
        Settings.load(chattr_root),
        "mail",
        fixture_family.paths["mbox"],
        contacts=fixture_family.paths["contacts"],
        html=True,
    )
    assert result.html_path is not None
    pages = "".join(p.read_text(encoding="utf-8") for p in result.html_path.parent.glob("*.html"))
    assert family.JOAO.name in pages


def test_a_bad_contacts_file_stops_before_anything_is_imported(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    broken = tmp_path / "c.vcf"
    broken.write_text("not a vcard")
    with pytest.raises(commands.CommandError, match="contacts"):
        commands.convert(
            Settings.load(chattr_root), "whatsapp", _whatsapp(fixture_family), contacts=broken
        )
    assert not commands.central_database(Settings.load(chattr_root)).exists()


# --- The command line -----------------------------------------------------------------------


def _main(root: Path, *args: str) -> int:
    return cli.main(["--root", str(root), *args])


def test_convert_says_when_handles_need_linking(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(chattr_root, "convert", "whatsapp", str(_whatsapp(fixture_family))) == 0
    assert "2 WhatsApp handle(s) have no person yet" in capsys.readouterr().out
    assert (
        _main(
            chattr_root,
            "convert",
            "whatsapp",
            str(_whatsapp(fixture_family)),
            "--contacts",
            str(fixture_family.paths["contacts"]),
            "--owner",
            family.OWNER.name,
            "--region",
            "US",
        )
        == 0
    )
    assert "no person yet" not in capsys.readouterr().out


def test_the_people_command_lists_and_links(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = fixture_family.paths["imessage"]
    _main(
        chattr_root,
        "convert",
        "imessage",
        str(db_path),
        "--spam-mode",
        "disabled",
        "--workers",
        "1",
    )
    capsys.readouterr()
    assert _main(chattr_root, "people") == 0
    out = capsys.readouterr().out
    assert family.UNKNOWN_HANDLE in out and "3 handle(s) have no person yet" in out

    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({family.UNKNOWN_HANDLE: "Delivery Service"}))
    assert (
        _main(
            chattr_root,
            "people",
            "--aliases",
            str(aliases),
            "--contacts",
            str(fixture_family.paths["contacts"]),
        )
        == 0
    )
    assert "Every handle in the archive has a person." in capsys.readouterr().out
    assert _rows(
        chattr_root, "SELECT person FROM handles WHERE identifier = ?", family.UNKNOWN_HANDLE
    ) == [("Delivery Service",)]


def test_the_people_command_can_print_json(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(chattr_root, "convert", "whatsapp", str(_whatsapp(fixture_family)))
    capsys.readouterr()
    assert _main(chattr_root, "people", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert {u["identifier"] for u in listed} == {family.ANA.name, family.OWNER.name}
    assert set(listed[0]) == {
        "service",
        "identifier",
        "display_name",
        "messages",
        "first_seen",
        "last_seen",
    }


def test_the_people_command_on_an_empty_archive(
    chattr_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(chattr_root, "people") == 0
    assert "Every handle in the archive has a person." in capsys.readouterr().out
