"""Mail pages by thread, and the index of everything rendered."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from fixtures import family

from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord
from chattr.render import Renderer


@pytest.fixture(scope="module")
def site(full_archive, tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("mailsite")
    roots = [full_archive.media, full_archive.root / "Output" / "Mail" / "Attachments"]
    with DatabaseManager(full_archive.database) as db:
        renderer = Renderer(db)
        written = render_pages(renderer, out, roots)
    return renderer, out, written


THREAD = f"Mail/2019/(2019-11-02) {family.JOAO.name} — Fotos de São Martinho.html"
SINGLE = f"Mail/2025/(2025-01-15) {family.ANA.name} — Wedding update.html"


def _visible(path: Path) -> str:
    return path.read_text(encoding="utf-8").split('id="chattr-data"')[0]


def test_each_thread_has_a_page_named_by_date_sender_and_subject(site) -> None:
    _, out, written = site
    assert (out / THREAD).is_file() and (out / SINGLE).is_file()
    assert {p for p in written if "Mail" in p.parts} == {out / THREAD, out / SINGLE}


def test_a_thread_page_shows_every_mail_with_its_headers(site) -> None:
    renderer, out, _ = site
    text = _visible(out / THREAD)
    spec = next(p for p in renderer.pages if p.path == THREAD)
    for message_id in spec.message_ids:
        assert text.count(f'id="m-{message_id}"') == 1
    assert text.count('<article class="entry') == 3
    assert f"{family.JOAO.name} &lt;{family.JOAO.emails[0]}&gt;" in text.replace("&#34;", '"')
    assert "Fotos de São Martinho" in text and "Re: Fotos de São Martinho" in text
    assert "Inbox" in text and "Família" in text
    assert 'class="badge s-mail">Mail<' in text


def test_the_senders_own_offset_is_shown(site) -> None:
    _, out, _ = site
    text = _visible(out / THREAD)
    assert "09:15:00" in text and "−05:00" in text  # Maria wrote from UTC-5
    assert ">UTC<" in text


def test_the_plain_body_is_shown_and_html_only_mail_gets_text(site) -> None:
    _, out, _ = site
    assert "As fotos da festa de São Martinho estão prontas." in _visible(out / THREAD)
    wedding = _visible(out / SINGLE)
    assert "The date is set: 14 June." in wedding
    assert wedding.count("<h1>") == 1 and "HTML version" in wedding  # the page title only


def test_attachments_link_to_the_stored_files(site) -> None:
    _, out, _ = site
    path = out / THREAD
    links = re.findall(r'<a href="([^"]+)">familia\.png</a>', _visible(path))
    assert len(links) == 1 and (path.parent / unquote(links[0])).is_file()


def test_mail_never_appears_on_a_person_page(site) -> None:
    renderer, _, _ = site
    for page in renderer.pages:
        if page.kind == "person":
            assert all(renderer.messages[m]["service"] != "Mail" for m in page.message_ids)


def test_the_index_links_every_page(site) -> None:
    renderer, out, written = site
    index = out / "index.html"
    assert index in written and index.is_file()
    text = index.read_text(encoding="utf-8")
    hrefs = [unquote(h) for h in re.findall(r'<a href="([^"#]+\.html)"', text)]
    assert {h for h in hrefs} == {p.path for p in renderer.pages}
    for href in hrefs:
        assert (out / href).is_file()
    assert family.ANA.name in text and family.GROUP_NAME in text and "Fotos de São Martinho" in text
    assert "<h2>People</h2>" in text and "<h2>Groups</h2>" in text and "<h2>Mail</h2>" in text


def test_the_index_counts_messages_per_page(site) -> None:
    renderer, out, _ = site
    text = (out / "index.html").read_text(encoding="utf-8")
    page = next(p for p in renderer.pages if (p.title, p.year) == (family.ANA.name, 2024))
    assert len(page.message_ids) == 7  # six on the 1st, one on the 2nd
    assert f'2024</a> <span class="count">({len(page.message_ids)})</span>' in text
    assert "(1 message)" in text and "(1 messages)" not in text


# --- Hostile mail ---------------------------------------------------------------------------


@pytest.fixture
def hostile(tmp_path: Path):
    with DatabaseManager(tmp_path / "t.sqlite") as db:
        db.upsert(
            ConversationRecord("Mail", "t1", name='<script>alert("s")</script>'),
            [
                MessageRecord(
                    timestamp=family.at(2024, 6, 1, 9, 0),
                    sender="evil@example.com",
                    sender_name="<img src=x onerror=alert(1)>",
                    body="<script>alert(1)</script>\nsecond",
                    content_type="email",
                    source_id="evil-1@x",
                    mail_headers={
                        "subject": '<script>alert("s")</script>',
                        "to": [{"name": "<b>You</b>", "address": "you@example.com"}],
                        "cc": [],
                        "labels": ["<i>label</i>"],
                    },
                    service_specific={"body_html": "<script>alert(2)</script>"},
                    attachments=(
                        AttachmentRecord("c" * 64, "<x>.pdf", "application/pdf", 1, "../../x"),
                    ),
                )
            ],
        )
        db.commit()
        renderer = Renderer(db)
        written = render_pages(renderer, tmp_path / "out", [tmp_path / "files"])
    return renderer, tmp_path / "out", written


def test_hostile_mail_renders_as_text(hostile) -> None:
    _, out, written = hostile
    mail = next(p for p in written if "Mail" in p.parts)
    text = _visible(mail)
    for raw in ("<script>alert", "<img src=x", "<b>You</b>", "<i>label</i>"):
        assert raw not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    full = mail.read_text(encoding="utf-8")
    assert full.lower().count("</script>") == 1


def test_a_hostile_name_is_safe_in_the_index_and_the_file_name(hostile) -> None:
    _, out, written = hostile
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "<script" not in index.replace('<script type="application/json"', "")
    assert "<script>alert" not in index
    mail = next(p for p in written if "Mail" in p.parts)
    assert not any(ch in mail.name for ch in '<>"\\|?*')
