"""`chattr-cli serve`, part 2: converting through the page, with progress, matches the command line."""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from fixtures import FamilyExports, family
from web_helpers import Browser

from chattr import cli
from chattr.web import WebServer


@pytest.fixture
def server(chattr_root: Path, tmp_path: Path) -> Iterator[WebServer]:
    web = WebServer(chattr_root, roots=[tmp_path], port=0)
    web.start()
    yield web
    web.stop()


@pytest.fixture
def browser(server: WebServer) -> Browser:
    return Browser(server)


def _chat(fixture_family: FamilyExports) -> str:
    return str(fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt")


def _rows(root: Path) -> set[tuple[str, int]]:
    db = sqlite3.connect(root / "Database" / "chattr.sqlite")
    return set(db.execute("SELECT id, is_from_owner FROM messages"))


def _convert(browser: Browser, service: str, **fields: str) -> Any:
    return browser.run_job(browser.post(f"/convert/{service}", fields))


# --- Converting for real --------------------------------------------------------------------


def test_a_whatsapp_chat_is_converted_and_its_pages_can_be_opened(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports
) -> None:
    done = _convert(browser, "whatsapp", export=_chat(fixture_family), html="1", copy_media="1",
                    owner=family.OWNER.name, route="central", region="US")  # fmt: skip
    assert "Done" in done.text and "Something went wrong" not in done.text
    assert len(_rows(chattr_root)) == 7
    link = re.search(r'href="(/output/Chattr/index.html)"', done.text)
    assert link, done.text
    index = browser.get(link.group(1))
    assert index.status == 200 and "Ana Costa" in index.text
    page_path = re.search(r'href="([^"]*\(2024\)[^"]*\.html)"', index.text)
    assert page_path
    page = browser.get("/output/Chattr/" + page_path.group(1))
    assert page.status == 200 and "Que lindo! Buy two." in page.text
    photo = re.search(r'src="([^"]+)"', page.text)
    assert photo, "the copied photo is shown"
    folder = "/output/Chattr/" + page_path.group(1).rsplit("/", 1)[0] + "/"
    from urllib.parse import unquote, urljoin

    assert browser.get(urljoin(folder, photo.group(1))).status == 200, unquote(photo.group(1))


def test_the_page_and_the_command_line_give_the_same_archive(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _convert(browser, "whatsapp", export=_chat(fixture_family), owner=family.OWNER.name,
             route="central", region="US")  # fmt: skip
    from chattr import preflight

    second = tmp_path / "Second"
    assert preflight.run_preflight(str(second), interactive=False)
    assert cli.main(["--root", str(second), "convert", "whatsapp", _chat(fixture_family),
                     "--owner", family.OWNER.name]) == 0  # fmt: skip
    assert _rows(chattr_root) == _rows(second) and any(owned for _, owned in _rows(second))


def test_imessage_options_reach_the_command(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports
) -> None:
    done = _convert(browser, "imessage", export=str(fixture_family.paths["imessage"]),
                    spam_mode="disabled", workers="1", route="central", html="1",
                    owner=family.OWNER.name, region="US")  # fmt: skip
    assert "Done" in done.text and len(_rows(chattr_root)) == 7


def test_mail_and_google_voice_and_voicemail_convert_too(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports
) -> None:
    paths = fixture_family.paths
    for service, source, count in (
        ("mail", paths["mbox"], 4),
        ("googlevoice", paths["google_voice"], 6),
        ("voicemail", paths["iphone_voicemail"], 2),
    ):
        done = _convert(browser, service, export=str(source), route="central", region="US")
        assert "Done" in done.text, (service, done.text)
    assert len(_rows(chattr_root)) == 4 + 6 + 2


def test_the_json_route_keeps_a_service_on_its_own(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports
) -> None:
    done = _convert(browser, "whatsapp", export=_chat(fixture_family), route="service",
                    html="1", region="US")  # fmt: skip
    assert "Done" in done.text and 'href="/output/WhatsApp/index.html"' in done.text
    assert list((chattr_root / "Output" / "WhatsApp").glob("*.json"))
    assert not (chattr_root / "Database" / "chattr.sqlite").exists()
    assert browser.get("/output/WhatsApp/index.html").status == 200


def test_pages_can_be_made_for_an_archive_converted_without_them(
    browser: Browser, chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(browser, "whatsapp", export=_chat(fixture_family), route="central", region="US")
    assert browser.get("/output/Chattr/index.html").status == 404
    done = browser.run_job(browser.post("/html"))
    assert "Done" in done.text and 'href="/output/Chattr/index.html"' in done.text
    assert browser.get("/output/Chattr/index.html").status == 200
    assert 'href="/output/Chattr/index.html"' in browser.get("/").text


def test_a_conversion_that_fails_says_why_in_plain_words(
    browser: Browser, chattr_root: Path, tmp_path: Path
) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    done = _convert(browser, "whatsapp", export=str(empty), route="central", region="US")
    assert "Something went wrong" in done.text and "No messages found" in done.text
    assert "Open your pages" not in done.text


# --- What the form checks first -------------------------------------------------------------


def test_a_mistake_in_the_form_is_shown_on_the_form_with_nothing_lost(
    browser: Browser, tmp_path: Path
) -> None:
    missing = browser.post("/convert/whatsapp", {"export": str(tmp_path / "nothing.txt"),
                                                 "owner": "Maria", "route": "central"})  # fmt: skip
    assert missing.status == 200 and "not found" in missing.text.lower()
    assert 'value="Maria"' in missing.text and str(tmp_path / "nothing.txt") in missing.text
    assert "Choose the export" in browser.post("/convert/mail", {"export": "  "}).text
    bad = browser.post(
        "/convert/imessage", {"export": "", "workers": "lots", "limit": "0", "route": "central"}
    )
    assert bad.status == 200 and bad.text.count('class="error"') >= 1
    assert "workers" in bad.text.lower() and "limit" in bad.text.lower()
    conflict = browser.post("/convert/imessage", {"export": "", "route": "service", "html": "1"})
    assert "archive" in conflict.text
    names = browser.post(
        "/convert/whatsapp", {"export": str(tmp_path), "aliases": str(tmp_path / "no.json")}
    )
    assert "aliases" in names.text.lower() or "names file" in names.text.lower()
    assert browser.get("/jobs/1").status == 404  # no job was made by any of these


# --- The arguments, exactly -----------------------------------------------------------------


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(list(argv)) or 0)
    return calls


def test_the_form_becomes_the_commands_arguments(
    browser: Browser, server: WebServer, seen: list[list[str]], tmp_path: Path
) -> None:
    export = tmp_path / "-dash.txt"  # a file name that looks like an option
    export.write_text("x")
    _convert(browser, "whatsapp", export=str(export), owner="Maria", contacts="", region="PT",
             route="central", html="1", copy_media="1")  # fmt: skip
    (argv,) = seen
    assert argv[:4] == ["--root", str(server.root), "convert", "whatsapp"]
    assert argv.index("--") == len(argv) - 2 and argv[-1] == str(export)
    assert "--html" in argv and "--json" not in argv
    assert argv[argv.index("--owner") + 1] == "Maria" and argv[argv.index("--region") + 1] == "PT"
    assert "--contacts" not in argv and "--aliases" not in argv
    assert argv[argv.index("--attachments-dir") + 1] == str(
        server.root / "Output" / "Chattr" / "Messages"
    )


def test_unticked_boxes_and_inapplicable_options_are_left_out(
    browser: Browser, server: WebServer, seen: list[list[str]], tmp_path: Path
) -> None:
    export = tmp_path / "x.mbox"
    export.write_text("x")
    _convert(
        browser, "mail", export=str(export), route="service", spam_mode="disabled", transcribe="1"
    )
    (argv,) = seen
    assert "--json" in argv and "--html" not in argv  # the unticked box is not sent
    for flag in ("--spam-mode", "--transcribe", "--attachments-dir", "--workers"):
        assert flag not in argv


def test_imessage_and_voicemail_flags(
    browser: Browser, seen: list[list[str]], tmp_path: Path
) -> None:
    chat_db = tmp_path / "chat.db"
    chat_db.write_text("x")
    _convert(browser, "imessage", export=str(chat_db), spam_mode="conservative", workers="3",
             limit="5", attachment_root=str(tmp_path), route="central")  # fmt: skip
    folder = tmp_path / "vm"
    folder.mkdir()
    _convert(browser, "voicemail", export=str(folder), transcribe="1", model=str(tmp_path),
             language="en", route="central")  # fmt: skip
    imessage, voicemail = seen
    assert imessage[imessage.index("--spam-mode") + 1] == "conservative"
    assert (
        imessage[imessage.index("--workers") + 1] == "3"
        and imessage[imessage.index("--limit") + 1] == "5"
    )
    assert "--attachments-dir" not in imessage
    assert "--transcribe" in voicemail and voicemail[voicemail.index("--language") + 1] == "en"


def test_an_empty_imessage_export_uses_the_configured_database(
    browser: Browser, seen: list[list[str]]
) -> None:
    _convert(browser, "imessage", export="", route="central", spam_mode="aggressive", workers="2")
    (argv,) = seen
    assert "--" not in argv and argv[3] == "imessage"


# --- Progress -------------------------------------------------------------------------------


def test_the_progress_panel_polls_until_the_job_ends(
    browser: Browser, seen: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = threading.Event()

    def slow(argv: list[str]) -> int:
        print("Reading the export…")
        print("\x1b[32m 50%|█████     | 5/10\x1b[0m\r 60%|██████    | 6/10\r 70%|███████   | 7/10")
        print("<script>alert(1)</script>")
        release.wait(30)
        print("All done")
        return 0

    monkeypatch.setattr(cli, "main", slow)
    export = tmp_path / "x.txt"
    export.write_text("x")
    reply = browser.post("/convert/whatsapp", {"export": str(export), "route": "central"})
    page = reply.headers["location"]
    assert 'hx-trigger="load, every 1s"' in browser.get(page).text  # the page starts polling
    deadline = __import__("time").time() + 30
    while "70%" not in (running := browser.get(page + "/status")).text:
        assert __import__("time").time() < deadline
    assert "hx-trigger" in running.text and "Working" in running.text
    assert "Reading the export" in running.text
    assert "\x1b" not in running.text and "50%" not in running.text  # only each line's last state
    assert "<script>alert(1)" not in running.text and "&lt;script&gt;" in running.text
    release.set()
    done = browser.run_job(reply)
    assert "hx-trigger" not in done.text and "Done" in done.text and "All done" in done.text


def test_jobs_run_one_at_a_time(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release, started, active, most = threading.Event(), threading.Event(), [0], [0]

    def slow(argv: list[str]) -> int:
        active[0] += 1
        most[0] = max(most[0], active[0])
        started.set()
        release.wait(30)
        active[0] -= 1
        return 0

    monkeypatch.setattr(cli, "main", slow)
    export = tmp_path / "x.txt"
    export.write_text("x")
    first = browser.post("/convert/whatsapp", {"export": str(export), "route": "central"})
    assert started.wait(30)
    second = browser.post("/convert/whatsapp", {"export": str(export), "route": "central"})
    assert "Waiting" in browser.get(second.headers["location"] + "/status").text
    release.set()
    browser.run_job(first)
    browser.run_job(second)
    assert most[0] == 1


def test_a_job_that_crashes_is_reported_and_the_next_still_runs(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def flaky(argv: list[str]) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the disk caught fire")
        return 0

    monkeypatch.setattr(cli, "main", flaky)
    export = tmp_path / "x.txt"
    export.write_text("x")
    bad = _convert(browser, "whatsapp", export=str(export), route="central")
    assert "Something went wrong" in bad.text and "the disk caught fire" in bad.text
    assert "Done" in _convert(browser, "whatsapp", export=str(export), route="central").text


def test_a_usage_error_from_the_command_is_shown_not_fatal(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuses(argv: list[str]) -> int:
        print(
            "chattr-cli convert: error: that option does not apply", file=__import__("sys").stderr
        )
        raise SystemExit(2)

    monkeypatch.setattr(cli, "main", refuses)
    export = tmp_path / "x.txt"
    export.write_text("x")
    done = _convert(browser, "whatsapp", export=str(export), route="central")
    assert "Something went wrong" in done.text and "does not apply" in done.text
