"""Commands do the work behind both the menu and the subcommands (WhatsApp, Messenger, HTML)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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


def test_html_is_written_for_the_conversation_and_the_archive(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert(
        settings, "whatsapp", _whatsapp(fixture_family), html=True, owner=family.OWNER.name
    )
    assert (
        result.html_path is not None
        and result.html_path.parent == chattr_root / "Output" / "Chattr"
    )
    assert result.html_path.name.startswith("[WhatsApp Chat - Ana Costa] (")
    assert "Que lindo! Buy two." in result.html_path.read_text(encoding="utf-8")
    archive = commands.generate_html(settings, owner=family.OWNER.name)
    assert archive == chattr_root / "Output" / "Chattr" / "All_Chats_Archive.html"
    assert archive.is_file()


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
