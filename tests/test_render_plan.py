"""Pages are planned per person per year, merging platforms, by the event's local date."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import family

from chattr.database_manager import DatabaseManager
from chattr.records import ConversationRecord, MessageRecord
from chattr.render import Renderer, local_moment


@pytest.fixture(scope="module")
def renderer(full_archive) -> Renderer:
    with DatabaseManager(full_archive.database) as db:
        return Renderer(db)


def _pages(renderer: Renderer, kind: str, title: str):
    return {p.year: p for p in renderer.pages if p.kind == kind and p.title == title}


def test_one_person_one_page_set_across_every_platform(renderer: Renderer) -> None:
    pages = _pages(renderer, "person", family.ANA.name)
    assert sorted(pages) == [2016, 2018, 2022, 2023, 2024, 2025]
    services = {
        renderer.messages[mid]["service"] for page in pages.values() for mid in page.message_ids
    }
    assert services == {"iMessage", "WhatsApp", "Google Voice", "Voicemail"}
    assert {p.folder for p in pages.values()} == {f"Messages/{family.ANA.name}"}
    assert pages[2023].filename == f"(2023) {family.ANA.name}.html"


def test_a_page_holds_its_year_in_time_order(renderer: Renderer) -> None:
    page = _pages(renderer, "person", family.ANA.name)[2024]
    stamps = [renderer.messages[m]["timestamp_utc"] for m in page.message_ids]
    assert stamps == sorted(stamps) and all(s.startswith("2024") for s in stamps)
    assert len(page.conversation_ids) == 1  # the 2024 WhatsApp chat


def test_a_group_has_its_own_folder(renderer: Renderer) -> None:
    (page,) = _pages(renderer, "group", family.GROUP_NAME).values()
    assert page.folder == f"Messages/Groups/{family.GROUP_NAME}" and page.year == 2024
    # Ana and João wrote in the group, but their own pages don't hold it
    assert all(
        renderer.messages[m]["conversation_id"] not in page.conversation_ids
        for p in renderer.pages
        if p.kind == "person"
        for m in p.message_ids
    )


def test_an_unresolved_handle_gets_a_folder_under_its_own_name(renderer: Renderer) -> None:
    pages = _pages(renderer, "person", family.UNKNOWN_HANDLE)
    assert sorted(pages) == [2018, 2025]  # a missed call, then a delivery text
    assert pages[2025].folder == f"Messages/{family.UNKNOWN_HANDLE}"
    assert {renderer.messages[m]["service"] for m in pages[2018].message_ids} == {"Google Voice"}


def test_an_alias_merges_a_username_into_the_person(renderer: Renderer) -> None:
    pages = _pages(renderer, "person", family.RUI.name)
    services = {renderer.messages[m]["service"] for p in pages.values() for m in p.message_ids}
    assert services == {"WhatsApp", "Instagram"} and sorted(pages) == [2022, 2024]


def test_mail_is_planned_per_thread_not_per_person(renderer: Renderer) -> None:
    mail = [p for p in renderer.pages if p.kind == "mail"]
    assert len(mail) == 2
    thread = next(p for p in mail if len(p.message_ids) == 3)
    assert thread.folder == "Mail/2019"
    assert thread.filename == f"(2019-11-02) {family.JOAO.name} — Fotos de São Martinho.html"
    assert not any(p.title == family.JOAO.emails[0] for p in renderer.pages if p.kind == "person")


def test_every_message_is_on_exactly_one_page(renderer: Renderer) -> None:
    placed = [m for p in renderer.pages for m in p.message_ids]
    assert len(placed) == len(set(placed)) == len(renderer.messages)


def test_page_paths_are_safe_and_unique(renderer: Renderer) -> None:
    paths = [p.path for p in renderer.pages]
    assert len(paths) == len(set(paths))
    assert all(".." not in path.split("/") and not path.startswith("/") for path in paths)


# --- Local time -----------------------------------------------------------------------------


def test_local_time_uses_the_stored_offset() -> None:
    late = local_moment("2025-01-01T04:30:00+00:00", -300)
    assert (late.year, late.month, late.day, late.hour) == (2024, 12, 31, 23)
    assert local_moment("2025-01-01T04:30:00+00:00", None).year == 2025  # zone unknown: as stored
    assert local_moment("2025-01-01T04:30:00+00:00", 60).hour == 5


def test_a_new_years_message_belongs_to_the_senders_year(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "t.sqlite") as db:
        conversation = ConversationRecord("WhatsApp", "chat", name="Friend")
        sent = family.at(2024, 12, 31, 23, 30)  # 04:30 UTC the next year, from UTC-5
        db.upsert(
            conversation,
            [MessageRecord(timestamp=sent, sender="Friend", sender_name="Friend", body="late")],
        )
        db.commit()
        page = Renderer(db).pages[0]
    assert page.year == 2024 and page.filename == "(2024) Friend.html"


def test_two_person_chats_fall_back_to_the_chat_name_without_an_owner(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "t.sqlite") as db:
        conversation = ConversationRecord("WhatsApp", "x", name="WhatsApp Chat with Ana Costa")
        db.upsert(
            conversation,
            [
                MessageRecord(timestamp=family.at(2024, 1, 1, 9, 0), sender="Ana Costa", body="a"),
                MessageRecord(
                    timestamp=family.at(2024, 1, 1, 9, 1), sender="Maria Costa", body="b"
                ),
            ],
        )
        db.commit()
        (page,) = Renderer(db).pages
    assert page.title == "Ana Costa"  # two senders, none known as the owner


def test_names_are_made_safe_for_folders(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "t.sqlite") as db:
        db.upsert(
            ConversationRecord("WhatsApp", "x", name="n"),
            [
                MessageRecord(
                    timestamp=family.at(2024, 1, 1, 0, 0),
                    sender="A/B: ../C",
                    body="a",
                    is_from_owner=False,
                )
            ],
        )
        db.commit()
        (page,) = Renderer(db).pages
    assert page.folder.count("/") == 1 and ".." not in page.folder.split("/")


# --- The data island document ---------------------------------------------------------------


def test_the_document_carries_everything_needed_to_rebuild_the_rows(renderer: Renderer) -> None:
    page = _pages(renderer, "person", family.ANA.name)[2023]
    document = renderer.document(page)
    assert document["schema"] == "chattr-page-1"
    assert (document["kind"], document["title"], document["year"]) == (
        "person",
        family.ANA.name,
        2023,
    )
    json.dumps(document)  # plain JSON
    ids = [m["id"] for m in document["messages"]]
    assert ids == page.message_ids
    assert {c["id"] for c in document["conversations"]} == set(page.conversation_ids)
    keys = {"id", "conversation_id", "source_id", "timestamp_utc", "tz_offset_minutes",
            "time_precision", "tz_assumed", "sender", "is_from_owner", "content_type", "body",
            "language", "translation", "transcript", "reply_to", "mail_headers",
            "service_specific", "content_hash", "version", "attachments", "reactions"}  # fmt: skip
    for message in document["messages"]:
        assert keys <= set(message)


def test_the_documents_handles_and_attachments_are_complete(renderer: Renderer) -> None:
    page = _pages(renderer, "person", family.ANA.name)[2023]
    document = renderer.document(page)
    handle = next(h for h in document["handles"] if h["identifier"] == family.ANA.phones[0])
    assert (handle["service"], handle["person"]) == ("iMessage", family.ANA.name)
    photos = next(m for m in document["messages"] if m["body"] == "Two of the best ones")
    assert len(photos["attachments"]) == 2
    assert {
        "sha256",
        "original_filename",
        "archival_filename",
        "mime_type",
        "size",
        "storage_path",
    } <= set(photos["attachments"][0])
    assert {r["type"] for r in photos["reactions"]} == {"Loved"}
    assert {r["action"] for r in photos["reactions"]} == {"added", "removed"}
    sender = photos["sender"]
    assert sender == {"service": "iMessage", "identifier": family.ANA.phones[0]}


def test_owner_messages_have_no_sender_and_a_flag(renderer: Renderer) -> None:
    document = renderer.document(_pages(renderer, "person", family.ANA.name)[2016])
    mine = next(m for m in document["messages"] if m["body"].startswith("Thank God"))
    assert mine["sender"] is None and mine["is_from_owner"] is True


def test_the_document_ids_are_the_database_ids(renderer: Renderer, full_archive) -> None:
    page = _pages(renderer, "person", family.ANA.name)[2018]
    with sqlite3.connect(full_archive.database) as db:
        stored = {r[0] for r in db.execute("SELECT id FROM messages")}
    assert set(page.message_ids) <= stored
