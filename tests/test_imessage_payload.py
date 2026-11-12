"""iMessage balloons: link previews, Apple Pay, messages from apps, and send effects.

Ported from imessage-exporter 5.0.1; see NOTICE.
"""

from __future__ import annotations

import plistlib
import sqlite3
from pathlib import Path
from typing import Any

from fixtures import family
from fixtures.builders.imessage import SCHEMA, apple_time

from chattr import commands
from chattr.config import Settings
from chattr.imessage_payload import describe, effect, unarchive
from chattr.parsers.imessage_parser import IMessageParser

ANA = family.ANA.phones[0]
URL = "com.apple.messages.URLBalloonProvider"
PAY = "com.apple.PassbookUIService.PeerPaymentMessagesExtension"
APP = "com.apple.messages.MSMessageExtensionBalloonPlugin:0000000000:com.example.Game.MessagesExtension"


def archive(root: Any) -> bytes:
    """A minimal NSKeyedArchiver: dictionaries, lists, strings and `("url", …)` for an NSURL."""
    objects: list[Any] = ["$null"]

    def add(value: Any) -> plistlib.UID:
        index = len(objects)
        objects.append(None)
        if isinstance(value, dict):
            keys = [add(k) for k in value]
            objects[index] = {"NS.keys": keys, "NS.objects": [add(v) for v in value.values()]}
        elif isinstance(value, list):
            objects[index] = {"NS.objects": [add(v) for v in value]}
        elif isinstance(value, tuple) and value[0] == "url":
            objects[index] = {"NS.base": plistlib.UID(0), "NS.relative": add(value[1])}
        else:
            objects[index] = value
        return plistlib.UID(index)

    top = add(root)
    return plistlib.dumps(
        {
            "$archiver": "NSKeyedArchiver",
            "$version": 100000,
            "$objects": objects,
            "$top": {"root": top},
        },
        fmt=plistlib.FMT_BINARY,
    )


LINK = archive(
    {
        "richLinkMetadata": {
            "title": "Pastéis de nata",
            "summary": "The recipe our grandmother used.",
            "URL": ("url", "https://example.org/pasteis"),
            "originalURL": ("url", "https://ex.org/p"),
            "siteName": "Receitas",
            "richLinkIsPlaceholder": False,
        }
    }
)
PAYMENT = archive(
    {"ldtext": "$20 Payment", "an": "Apple Cash", "userInfo": {"caption": "$20 Payment"}}
)
GAME = archive(
    {
        "an": "GamePigeon",
        "URL": ("url", "https://example.org/game"),
        "userInfo": {"image-title": "8 Ball", "image-subtitle": "Your move", "caption": "Play"},
    }
)


def test_an_archive_becomes_plain_values() -> None:
    assert unarchive(archive({"a": ["x", {"b": "y"}], "u": ("url", "https://example.org")})) == {
        "a": ["x", {"b": "y"}], "u": {"URL": "https://example.org"}}  # fmt: skip
    for bad in (None, b"", b"nonsense", plistlib.dumps({"not": "an archive"})):
        assert unarchive(bad) is None


def test_a_link_preview_gives_its_title_summary_site_and_address() -> None:
    link = describe(URL, LINK)
    assert link is not None and link.kind == "link"
    assert (link.title, link.site, link.url) == (
        "Pastéis de nata",
        "Receitas",
        "https://example.org/pasteis",
    )
    assert link.summary == "The recipe our grandmother used."


def test_a_link_with_no_readable_preview_is_still_a_link() -> None:
    link = describe(URL, b"damaged")
    assert link is not None and link.kind == "link" and link.url is None and link.line == "Link"


def test_apple_pay_and_app_messages_say_what_they_are() -> None:
    pay = describe(PAY, PAYMENT)
    assert pay is not None and pay.kind == "apple_pay" and pay.line == "Apple Pay: $20 Payment"
    game = describe(APP, GAME)
    assert game is not None and game.kind == "app" and game.app == "GamePigeon"
    assert game.line == "GamePigeon: 8 Ball · Your move" and game.url == "https://example.org/game"


def test_known_balloons_are_named_even_without_a_payload() -> None:
    assert describe("com.apple.SafetyMonitorApp.SafetyMonitorMessages", None).line == "Check In"  # type: ignore[union-attr]
    assert describe("com.apple.Handwriting.HandwritingProvider", None).line == "Handwritten message"  # type: ignore[union-attr]
    assert describe(None, LINK) is None


def test_send_effects_are_named() -> None:
    assert effect("com.apple.MobileSMS.expressivesend.impact") == "Sent with Slam"
    assert effect("com.apple.messages.effect.CKConfettiEffect") == "Sent with Confetti"
    assert effect(None) is None and effect("com.apple.something.new") is None


# --- Through the importer and onto the page -------------------------------------------------------


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript(
        SCHEMA + "ALTER TABLE message ADD COLUMN payload_data BLOB;"
        "ALTER TABLE message ADD COLUMN expressive_send_style_id TEXT;"
    )
    db.execute("INSERT INTO handle (ROWID, id, service) VALUES (1, ?, 'iMessage')", (ANA,))
    db.execute(
        "INSERT INTO chat (ROWID, guid, style, chat_identifier, service_name) VALUES (1, 'chat-1', 45, ?, 'iMessage')",
        (ANA,),
    )
    db.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    rows = [
        (1, "https://example.org/pasteis", URL, LINK, None),
        (2, None, PAY, PAYMENT, None),
        (3, None, APP, GAME, None),
        (4, "Parabéns!", None, None, "com.apple.messages.effect.CKHappyBirthdayEffect"),
        (5, None, URL, LINK, None),
    ]
    for rowid, text, bundle, payload, style in rows:
        db.execute(
            "INSERT INTO message (ROWID, guid, text, service, handle_id, date, balloon_bundle_id, payload_data, "
            "expressive_send_style_id) VALUES (?, ?, ?, 'iMessage', 1, ?, ?, ?, ?)",
            (
                rowid,
                f"g-{rowid}",
                text,
                apple_time(family.at(2024, 5, 1, 10, rowid)),
                bundle,
                payload,
                style,
            ),
        )
        db.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, ?)", (rowid,))
    db.commit()
    db.close()
    return path


def test_balloons_are_imported_with_what_they_show(tmp_path: Path) -> None:
    (batch,) = IMessageParser(spam_mode="disabled").parse(_database(tmp_path))
    records = {r.source_id: r for r in batch.records}
    link = records["g-1"].service_specific
    assert (
        link["shared_link"] == "https://example.org/pasteis"
        and link["link_text"] == "Pastéis de nata"
    )
    assert (
        link["link_summary"] == "The recipe our grandmother used."
        and link["link_site"] == "Receitas"
    )
    assert records["g-1"].body is None  # the address is the link, not also the text
    assert records["g-5"].service_specific["shared_link"] == "https://example.org/pasteis"
    assert records["g-2"].body == "Apple Pay: $20 Payment"
    assert records["g-2"].service_specific["app"] == {"kind": "apple_pay", "name": "Apple Pay"}
    assert records["g-3"].body == "GamePigeon: 8 Ball · Your move"
    assert (
        records["g-4"].body == "Parabéns!"
        and records["g-4"].service_specific["effect"] == "Sent with Balloons"
    )


def test_balloons_are_not_mistaken_for_spam(tmp_path: Path) -> None:
    (batch,) = IMessageParser(spam_mode="aggressive").parse(_database(tmp_path))
    assert not [r for r in batch.records if "spam_reason" in r.service_specific]


def test_the_page_shows_the_preview_and_the_effect(chattr_root: Path, tmp_path: Path) -> None:
    settings = Settings.load(chattr_root)
    commands.convert_imessage(settings, db_path=_database(tmp_path), spam_mode="disabled")
    commands.generate_html(settings, out=tmp_path / "out")
    page = next((tmp_path / "out" / "People").rglob("(2024) *.html")).read_text(encoding="utf-8")
    assert 'href="https://example.org/pasteis"' in page and "Pastéis de nata" in page
    assert "The recipe our grandmother used." in page and "Receitas" in page
    assert "Apple Pay: $20 Payment" in page and "Sent with Balloons" in page
