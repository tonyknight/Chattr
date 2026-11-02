"""Commands do the work behind both the menu and the subcommands (WhatsApp, Messenger, HTML)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings


def _whatsapp(fixture_family: FamilyExports) -> Path:
    return fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"


def _count(db: Path) -> int:
    """Messages in a database; an archive that was never created holds none."""
    if not db.exists():
        return 0
    return sqlite3.connect(db).execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def test_central_route_imports_into_the_archive(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert(
        settings, "whatsapp", _whatsapp(fixture_family), owner=family.OWNER.name
    )
    assert result.counts.inserted > 0
    assert _count(commands.central_database(settings)) == result.counts.inserted
    state = json.loads((chattr_root / "Services" / "WhatsApp.json").read_text())["state"]
    assert state["total_messages_imported"] == result.counts.inserted


def test_service_route_writes_json_and_leaves_the_archive_alone(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert(settings, "whatsapp", _whatsapp(fixture_family), route="service")
    assert (
        result.json_path is not None
        and result.json_path.parent == chattr_root / "Output" / "WhatsApp"
    )
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == result.counts.total > 0
    assert _count(commands.central_database(settings)) == 0


def test_messenger_converts_with_fixed_encoding(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert(
        settings, "messenger", fixture_family.paths["messenger"], route="service"
    )
    assert result.json_path is not None
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    senders = {m["sender"] for m in data["messages"]}
    assert family.JOAO.name in senders


def test_html_is_written_as_pages_per_person_per_year(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert(
        settings, "whatsapp", _whatsapp(fixture_family), html=True, owner=family.OWNER.name
    )
    site = chattr_root / "Output" / "Chattr"
    assert result.html_path == site / "index.html"
    page = site / "Messages" / family.ANA.name / f"(2024) {family.ANA.name}.html"
    assert "Que lindo! Buy two." in page.read_text(encoding="utf-8")
    assert commands.generate_html(settings) == site / "index.html"


def test_generating_html_for_an_empty_archive_says_so(chattr_root: Path) -> None:
    assert commands.generate_html(Settings.load(chattr_root)) is None


def test_aliases_rename_senders(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({family.ANA.name: "Ana (daughter)"}))
    settings = Settings.load(chattr_root)
    result = commands.convert(
        settings, "whatsapp", _whatsapp(fixture_family), route="service", aliases=aliases
    )
    assert result.json_path is not None
    senders = {m["sender"] for m in json.loads(result.json_path.read_text())["messages"]}
    assert "Ana (daughter)" in senders and family.ANA.name not in senders


def test_an_unreadable_aliases_file_is_a_command_error(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text("{not json")
    settings = Settings.load(chattr_root)
    with pytest.raises(commands.CommandError, match="aliases"):
        commands.convert(
            settings, "whatsapp", _whatsapp(fixture_family), route="service", aliases=aliases
        )
    with pytest.raises(commands.CommandError, match="aliases"):
        commands.convert(
            settings, "whatsapp", _whatsapp(fixture_family), aliases=tmp_path / "missing.json"
        )
