"""An import run records itself in the database and in the service's state file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import preflight
from chattr.config import Settings, load_settings
from chattr.database_manager import DatabaseManager
from chattr.importing import conversation_for, import_messages
from chattr.parsers.whatsapp_parser import WhatsAppParser


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHATTR_ROOT", raising=False)
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False)
    return root


def _chat(fixture_family: FamilyExports) -> Path:
    return fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"


def test_import_updates_the_imports_row_and_service_state(
    root: Path, fixture_family: FamilyExports
) -> None:
    chat = _chat(fixture_family)
    messages = WhatsAppParser().parse(str(chat))
    settings = Settings.load(root)
    with DatabaseManager(root / "Database" / "chattr.sqlite") as db:
        counts = import_messages(db, settings, "WhatsApp", chat, messages, owner=family.OWNER.name)
        row = db.conn.execute("SELECT * FROM imports").fetchone()
    assert counts.inserted == len(messages) > 0
    assert row["finished_at"] and row["inserted"] == counts.inserted
    state = json.loads((root / "Services" / "WhatsApp.json").read_text())["state"]
    assert state["total_messages_imported"] == counts.inserted
    assert state["last_export_file"] == str(chat)
    assert state["last_import_date"]


def test_reimport_leaves_the_running_total_unchanged(
    root: Path, fixture_family: FamilyExports
) -> None:
    chat = _chat(fixture_family)
    messages = WhatsAppParser().parse(str(chat))
    with DatabaseManager(root / "Database" / "chattr.sqlite") as db:
        import_messages(db, Settings.load(root), "WhatsApp", chat, messages)
        second = import_messages(db, Settings.load(root), "WhatsApp", chat, messages)
    assert second.inserted == 0
    state = json.loads((root / "Services" / "WhatsApp.json").read_text())["state"]
    assert state["total_messages_imported"] == len(messages)


def test_conversation_is_named_after_the_export_folder(fixture_family: FamilyExports) -> None:
    conversation = conversation_for("WhatsApp", _chat(fixture_family))
    assert conversation.source_id == "WhatsApp Chat - Ana Costa"


def test_cli_import_path_records_state(
    root: Path, fixture_family: FamilyExports, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chattr import cli

    chat = _chat(fixture_family)
    load_settings(str(root))
    monkeypatch.setattr("builtins.input", lambda *_: str(chat))
    cli.process_and_import(
        WhatsAppParser(), "WhatsApp", DatabaseManager(root / "Database" / "chattr.sqlite")
    )
    state = json.loads((root / "Services" / "WhatsApp.json").read_text())["state"]
    assert state["total_messages_imported"] > 0
