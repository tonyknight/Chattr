"""Every service can still be exported as JSON, now straight from the unified database."""

from __future__ import annotations

import json
from pathlib import Path

from fixtures import FamilyExports, family

from chattr.database_manager import DatabaseManager
from chattr.importing import import_messages
from chattr.parsers.whatsapp_parser import WhatsAppParser


def test_whatsapp_records_export_as_json(fixture_family: FamilyExports, tmp_path: Path) -> None:
    chat = fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    messages = WhatsAppParser().parse(str(chat))
    out = tmp_path / "whatsapp.json"
    with DatabaseManager(tmp_path / "chattr.sqlite") as db:
        import_messages(db, None, "WhatsApp", chat, messages, owner=family.OWNER.name)
        written = db.export_json("WhatsApp", out)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert written == len(messages) == len(data["messages"])
    assert data["service"] == "WhatsApp"
    first = data["messages"][0]
    for key in ("id", "conversation", "timestamp_utc", "sender", "body", "content_type"):
        assert key in first, key
    assert any(m["body"] == "Que lindo! Buy two." and m["is_from_owner"] for m in data["messages"])


def test_imessage_native_json_export_is_still_available() -> None:
    from chattr import imessage_export

    assert callable(imessage_export.export_to_json)
