"""Person and group pages (one each, every year): escaped content, anchors, badges, calendars,
entries, links, data island."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from fixtures import family

from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord, ReactionRecord
from chattr.render import Renderer, compact, expand


@pytest.fixture(scope="module")
def site(full_archive, tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("site")
    outputs = full_archive.root / "Output"
    roots = [
        full_archive.media,
        outputs / "Voicemail" / "Attachments",
        outputs / "GoogleVoice" / "Attachments",
        outputs / "Mail" / "Attachments",
    ]
    with DatabaseManager(full_archive.database) as db:
        renderer = Renderer(db)
        written = render_pages(renderer, out, roots)
    return renderer, out, written


def _spec(site, kind: str, title: str):
    renderer, _, _ = site
    return next(p for p in renderer.pages if (p.kind, p.title) == (kind, title))


def _page(site, kind: str, title: str, year: int | None = None) -> tuple[Path, str]:
    """The page, or (with a year) its head, that year's calendar and that year's messages."""
    _, out, _ = site
    path = out / _spec(site, kind, title).path
    text = path.read_text(encoding="utf-8")
    if year is None:
        return path, text
    head = text[: text.index("<main>")]
    block = text[text.index(f'<section class="year-block" id="y-{year}"') :]
    block = block[: block.index("</section>")]
    return path, head + block


def test_every_person_and_group_page_is_written_at_its_planned_path(site) -> None:
    renderer, out, written = site
    planned = {out / p.path for p in renderer.pages if p.kind in ("person", "group")}
    assert planned <= set(written) and all(p.is_file() for p in planned)
    assert (out / "People" / family.ANA.name / f"{family.ANA.name}.html").is_file()
    assert not (out / "People" / family.ANA.name / "index.html").exists()  # one page, not a set


def test_pages_are_self_contained(site) -> None:
    _, text = _page(site, "person", family.ANA.name)
    assert "<link " not in text and "<script src" not in text and "@import" not in text
    assert "<style>" in text and "prefers-color-scheme: dark" in text
    # the lightbox and the data island; nothing is loaded from elsewhere
    assert text.lower().count("</script>") == 2 and "fetch(" not in text


def _scripts(text: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", text, re.S)


def test_the_lightbox_is_the_one_script_caixa_allows(site) -> None:
    """Caixa's viewer lets a page run only scripts whose hash it lists (harness page-scripts.ts)."""
    import base64
    import hashlib

    _, text = _page(site, "person", family.ANA.name)
    (lightbox,) = _scripts(text)
    assert ".attachment a > img" in lightbox and "lightbox" in lightbox
    digest = base64.b64encode(hashlib.sha256(lightbox.encode("utf-8")).digest()).decode()
    allowed = (
        Path(__file__).resolve().parents[3] / "harness" / "src" / "host" / "page-scripts.ts"
    ).read_text(encoding="utf-8")
    assert (
        f"'sha256-{digest}'" in allowed
    ), f"lightbox.js changed: put sha256-{digest} in harness/src/host/page-scripts.ts"


def test_every_picture_is_still_a_link_to_its_file(site) -> None:
    _, text = _page(site, "person", family.ANA.name)
    pictures = re.findall(r'<a href="([^"]+)"><img src="([^"]+)"', text)
    assert pictures and all(link == source for link, source in pictures)


def test_a_long_page_lays_out_only_what_is_on_screen(site) -> None:
    _, text = _page(site, "person", family.ANA.name)
    assert re.search(r"\.day \{[^}]*content-visibility:\s*auto", text)
    assert 'loading="lazy"' in text and 'preload="none"' in text


def test_every_message_has_one_stable_anchor(site) -> None:
    spec = _spec(site, "person", family.ANA.name)
    _, text = _page(site, "person", family.ANA.name)
    for message_id in spec.message_ids:
        assert text.count(f'id="m-{html.escape(message_id)}"') == 1


def test_platform_badges_name_the_service(site) -> None:
    _, text = _page(site, "person", family.ANA.name, 2023)
    assert 'class="badge s-imessage">iMessage<' in text
    _, voice = _page(site, "person", family.ANA.name, 2018)
    assert 'class="badge s-google-voice">Google Voice<' in voice


def test_dates_have_separators_and_the_calendar_links_to_them(site) -> None:
    _, text = _page(site, "person", family.ANA.name, 2024)
    assert 'id="date-2024-03-01"' in text and 'id="date-2024-03-02"' in text
    assert re.search(r'<a href="#date-2024-03-01"[^>]*title="6 messages"', text)
    assert 'href="#date-2024-03-05"' not in text  # a day with no messages is not a link
    assert text.count('class="month"') == 12
    _, whole = _page(site, "person", family.ANA.name)
    assert len(re.findall(r'class="calendar[ "]', whole)) == 6  # one for each of her six years


def test_the_times_show_their_offset_precision_and_zone(site) -> None:
    _, voice = _page(site, "person", family.ANA.name, 2018)
    assert "17:40:12" in voice and "−05:00" in voice  # Google Voice states its offset
    _, imessage = _page(site, "person", family.ANA.name, 2023)
    assert ">UTC<" in imessage
    _, android = _page(site, "person", family.RUI.name, 2024)
    assert ">10:01</time>" in android and ">10:01:00</time>" not in android  # minute stays minute
    assert "zone unknown" in android


def test_calls_and_voicemail_have_their_own_entries(site) -> None:
    _, voice = _page(site, "person", family.ANA.name, 2018)
    assert "Received call" in voice and "5:12" in voice and "Placed call" in voice
    assert "Hi Mãe, it&#39;s Ana. I&#39;m running late" in voice  # the transcript, escaped
    assert "<audio controls" in voice
    _, unknown = _page(site, "person", family.UNKNOWN_HANDLE, 2018)
    assert "Missed call" in unknown
    _, vm = _page(site, "person", family.ANA.name, 2022)
    assert "Voicemail" in vm and "Hi Mum, call me back." in vm and "0:14" in vm


def test_attachment_links_point_at_files_that_exist(site) -> None:
    path, text = _page(site, "person", family.ANA.name, 2024)
    sources = re.findall(r'<img [^>]*src="([^"]+)"', text)
    assert len(sources) == 1
    assert (path.parent / unquote(sources[0])).is_file()
    path, text = _page(site, "person", family.ANA.name, 2022)
    audio = re.findall(r'<audio[^>]*>\s*<source src="([^"]+)"', text)
    assert audio and (path.parent / unquote(audio[0])).is_file()


def test_standing_reactions_show_and_removed_ones_do_not(site) -> None:
    _, text = _page(site, "person", family.ANA.name, 2016)
    assert "Liked · " in text.split('id="chattr-data"')[0]
    _, photos = _page(site, "person", family.ANA.name, 2023)
    assert "Loved" not in photos.split('id="chattr-data"')[0]  # added, then removed


def test_a_shared_link_is_a_link_and_not_part_of_the_text(site) -> None:
    _, text = _page(site, "person", family.JOAO.name, 2019)
    assert 'href="https://example.org/tasca-do-ze"' in text
    assert "Tasca do Zé — cozinha tradicional" in text and 'rel="noopener noreferrer"' in text
    assert "[Shared Link" not in text


def test_the_year_bar_goes_to_each_year_on_the_page(site) -> None:
    _, text = _page(site, "person", family.ANA.name)
    links = re.findall(r'<a class="year" href="#y-(\d{4})">(\d{4})</a>', text)
    assert [y for y, _ in links] == ["2016", "2018", "2022", "2023", "2024", "2025"]
    for year, _ in links:
        assert f'<section class="year-block" id="y-{year}"' in text
    assert 'href="#end">Latest</a>' in text and 'id="end"' in text
    # each year begins with its own calendar
    for year, _ in links:
        section = text[text.index(f'id="y-{year}"') :]
        assert section.index(f'id="cal-{year}"') < section.index('class="day"')


def test_system_notices_and_owner_messages_are_styled_apart(site) -> None:
    _, text = _page(site, "person", family.ANA.name, 2024)
    assert 'class="entry system' in text and "end-to-end encrypted" in text
    assert 'class="entry sent' in text and 'class="entry received' in text


def test_the_data_island_matches_the_planned_document(site) -> None:
    renderer, out, _ = site
    spec = _spec(site, "person", family.ANA.name)
    text = (out / spec.path).read_text(encoding="utf-8")
    island = re.search(
        r'<script type="application/json" id="chattr-data">(.*?)</script>', text, re.S
    )
    assert island is not None
    stored = json.loads(island.group(1))
    assert stored == json.loads(json.dumps(compact(renderer.document(spec))))
    assert expand(stored) == json.loads(json.dumps(renderer.document(spec)))
    assert '"translation":null' not in island.group(1)  # empty fields are left out


# --- Hostile content ------------------------------------------------------------------------

HOSTILE = '<script>alert(1)</script> & "quoted" <img src=x onerror=alert(1)> </script>'


@pytest.fixture
def hostile(tmp_path: Path):
    with DatabaseManager(tmp_path / "t.sqlite") as db:
        db.upsert(
            ConversationRecord("WhatsApp", "evil", name="Evil <b>Name</b>"),
            [
                MessageRecord(
                    timestamp=family.at(2024, 5, 1, 9, 0),
                    sender="<i>Mallory</i>",
                    sender_name="<i>Mallory</i>",
                    body=HOSTILE + "\nsecond line with a separator",
                    service_specific={
                        "shared_link": "javascript:alert(1)",
                        "link_text": "<b>click</b>",
                    },
                    attachments=(
                        AttachmentRecord(
                            "a" * 64, '"><script>x</script>.png', "image/png", 3, "../../etc/x"
                        ),
                    ),
                    reactions=(ReactionRecord("Liked", "<u>Eve</u>"),),
                ),
                MessageRecord(
                    timestamp=family.at(2024, 5, 1, 9, 1),
                    sender="<i>Mallory</i>",
                    sender_name="<i>Mallory</i>",
                    body="plain",
                    service_specific={"shared_link": "https://ok.example/x?a=1&b=2"},
                ),
            ],
        )
        db.commit()
        renderer = Renderer(db)
        written = render_pages(renderer, tmp_path / "out", [tmp_path / "files"])
    return renderer, tmp_path / "out", written


def test_html_and_script_in_content_render_as_text(hostile) -> None:
    renderer, out, written = hostile
    text = written[0].read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert" not in text and "onerror=alert(1)>" not in text
    assert "&lt;i&gt;Mallory&lt;/i&gt;" in text and "<i>Mallory</i>" not in text
    # the lightbox and the island are the only scripts: nothing from the content runs
    assert text.lower().count("</script>") == 2
    assert "<br>" in text and "second line" in text
    assert "<title>&lt;i&gt;Mallory&lt;/i&gt;</title>" in text


def test_the_data_island_cannot_break_out(hostile) -> None:
    _, _, written = hostile
    text = written[0].read_text(encoding="utf-8")
    island = re.search(r'id="chattr-data">(.*?)</script>', text, re.S)
    assert island is not None
    assert "</script" not in island.group(1).lower() and " " not in island.group(1)
    data = json.loads(island.group(1))
    assert data["messages"][0]["body"].startswith(HOSTILE)  # intact after parsing


def test_only_http_links_become_links(hostile) -> None:
    _, _, written = hostile
    text = written[0].read_text(encoding="utf-8")
    assert "javascript:" not in text.split('id="chattr-data"')[0]
    assert 'href="https://ok.example/x?a=1&amp;b=2"' in text


def test_an_attachment_path_cannot_escape_the_roots(hostile) -> None:
    _, _, written = hostile
    text = written[0].read_text(encoding="utf-8")
    assert "etc/x" not in text.split('id="chattr-data"')[0]
    assert "&lt;script&gt;x&lt;/script&gt;.png" in text  # shown by name, as text


# --- Colour and the way back to the top ----------------------------------------------------------


def _css_vars(page: str, dark: bool = False) -> dict[str, str]:
    block = re.findall(r":root \{([^}]*)\}", page)[1 if dark else 0]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _luminance(colour: str) -> float:
    channels = [c / 255 for c in _rgb(colour)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _is_orange(colour: str) -> bool:
    red, green, blue = _rgb(colour)
    return red > green > blue


def test_the_heat_map_is_four_steps_of_orange_darker_for_busier_days(site) -> None:
    _, page = _page(site, "person", family.ANA.name, 2024)
    light = _css_vars(page)
    steps = [light[f"l{n}"] for n in (1, 2, 3, 4)]
    assert all(_is_orange(step) for step in steps)
    assert [_luminance(s) for s in steps] == sorted((_luminance(s) for s in steps), reverse=True)
    assert "#FA9237".lower() in [s.lower() for s in steps]  # qromaOrange is one of the steps


def test_in_the_dark_the_busiest_days_stand_out_most(site) -> None:
    _, page = _page(site, "person", family.ANA.name, 2024)
    dark = _css_vars(page, dark=True)
    steps = [dark[f"l{n}"] for n in (1, 2, 3, 4)]
    assert all(_is_orange(step) for step in steps)
    assert [_luminance(s) for s in steps] == sorted(_luminance(s) for s in steps)


def test_nothing_on_the_page_is_green_any_more(site) -> None:
    _, page = _page(site, "person", family.ANA.name, 2024)
    for mode in (False, True):
        for name, colour in _css_vars(page, dark=mode).items():
            red, green, blue = _rgb(colour)
            assert not (green > red and green > blue), f"--{name} {colour} is green"


def test_dates_are_orange_and_readable(site) -> None:
    _, page = _page(site, "person", family.ANA.name, 2024)
    assert re.search(r"\.date-separator a \{[^}]*color:\s*var\(--date\)", page)
    for mode in (False, True):
        colours = _css_vars(page, dark=mode)
        assert _is_orange(colours["date"])
        lighter, darker = sorted(
            (_luminance(colours["date"]), _luminance(colours["bg"])), reverse=True
        )
        assert (lighter + 0.05) / (darker + 0.05) >= 4.5  # readable text, not just decoration


def test_clicking_a_date_goes_back_to_the_top(site) -> None:
    _, page = _page(site, "person", family.ANA.name, 2024)
    assert '<body id="top">' in page
    headings = re.findall(r'<h3 class="date-separator" id="date-[\d-]+">(.*?)</h3>', page)
    assert headings
    for heading in headings:
        assert re.fullmatch(r'<a href="#top" title="Back to the top">[^<]+</a>', heading)


# --- A page for each person, and pages that go away ----------------------------------------------


def test_a_persons_page_sums_up_all_that_came_from_them(site) -> None:
    renderer, out, written = site
    _, page = _page(site, "person", family.ANA.name)
    assert f"<h1>{family.ANA.name}</h1>" in page
    for service in ("iMessage", "WhatsApp", "Google Voice", "Voicemail"):
        assert f">{service}</span>" in page  # every source she is in, with its count
    total = len(_spec(site, "person", family.ANA.name).message_ids)
    assert f"{total} items, from 2016-" in page
    assert out / "People" / family.ANA.name / f"{family.ANA.name}.html" in written


def test_a_persons_page_links_their_groups_and_their_mail(site) -> None:
    _, page = _page(site, "person", family.JOAO.name)
    group = family.GROUP_NAME.replace(" ", "%20")
    assert f'href="../../Groups/{group}/{group}.html"' in page
    assert "<summary>Mail (" in page and 'href="../../Mail/' in page


def test_a_groups_page_links_its_members(site) -> None:
    _, page = _page(site, "group", family.GROUP_NAME)
    assert "People in this group" in page
    ana = family.ANA.name.replace(" ", "%20")
    assert f'href="../../People/{ana}/{ana}.html"' in page


def test_a_page_links_back_to_everyone(site) -> None:
    _, page = _page(site, "person", family.ANA.name)
    assert '<a href="../../index.html">Everyone</a>' in page


def test_the_top_index_links_each_persons_page(site) -> None:
    _, out, _ = site
    text = (out / "index.html").read_text(encoding="utf-8")
    ana = family.ANA.name.replace(" ", "%20")
    assert f'<a href="People/{ana}/{ana}.html">{family.ANA.name}</a>' in text
    assert "2016–2025" in text  # the years her page spans


def test_pages_that_are_no_longer_wanted_are_removed(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    database = tmp_path / "chattr.sqlite"
    out = tmp_path / "out"
    with DatabaseManager(database) as db:
        db.upsert(
            ConversationRecord("WhatsApp", "c1", name="+15550101", participants=("+15550101",)),
            [
                MessageRecord(
                    timestamp=datetime(2024, 3, 1, tzinfo=timezone.utc),
                    sender="+15550101",
                    body="olá",
                )
            ],
            db.start_import("WhatsApp", "test"),
        )
        db.commit()
        render_pages(Renderer(db), out)
        old = out / "People" / "+15550101"
        assert (old / "+15550101.html").is_file()
        (old / "notes.txt").write_text("mine")  # something a person put there
        db.connection.execute("UPDATE handles SET person = ?", (family.ANA.name,))
        db.commit()
        render_pages(Renderer(db), out)
    assert (out / "People" / family.ANA.name / f"{family.ANA.name}.html").is_file()
    assert sorted(p.name for p in old.iterdir()) == ["notes.txt"]  # only Chattr's pages went
