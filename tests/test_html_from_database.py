"""HTML is generated from database rows: synthetic WhatsApp export → parser → database → page."""

from __future__ import annotations

from pathlib import Path

from fixtures import FamilyExports, family

from chattr.database_manager import DatabaseManager
from chattr.html_generator import HTMLGenerator
from chattr.parsers.whatsapp_parser import WhatsAppParser


def test_whatsapp_export_renders_from_the_database(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    chat = fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    messages = WhatsAppParser().parse(str(chat))
    assert messages, "the parser should read the synthetic iOS export"

    manager = DatabaseManager(str(tmp_path / "chattr.sqlite"))
    with manager:
        manager.create_tables()
        manager.insert_messages(messages, "WhatsApp")
    with manager:
        rows = manager.get_all_messages()
    assert rows

    out = tmp_path / "chat.html"
    HTMLGenerator().generate_html(rows, str(out), main_author=family.OWNER.name)

    html = out.read_text(encoding="utf-8")
    assert "Que lindo! Buy two." in html
    assert family.ANA.name in html
    assert "2024-03-01" in html
    # The owner's message sits in a "sent" bubble; Ana's in a "received" one.
    owner_bubble = html.rfind('<div class="message ', 0, html.index("Que lindo! Buy two."))
    assert html.startswith('<div class="message sent">', owner_bubble)
    ana_bubble = html.rfind('<div class="message ', 0, html.index("Bom dia"))
    assert html.startswith('<div class="message received">', ana_bubble)
