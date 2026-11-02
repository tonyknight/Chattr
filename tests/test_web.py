"""`chattr-cli serve`, part 1: who may talk to it, and the pages that need no conversion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from web_helpers import Browser, link_targets

from chattr.web import WebServer

SERVICES = ["whatsapp", "messenger", "instagram", "imessage", "mail", "googlevoice", "voicemail"]


@pytest.fixture
def server(chattr_root: Path, tmp_path: Path) -> Iterator[WebServer]:
    (tmp_path / "Downloads").mkdir(exist_ok=True)
    web = WebServer(chattr_root, roots=[tmp_path], port=0)
    web.start()
    yield web
    web.stop()


@pytest.fixture
def browser(server: WebServer) -> Browser:
    return Browser(server)


# --- Who may talk to it ---------------------------------------------------------------------


def test_it_listens_on_this_computer_only(server: WebServer) -> None:
    assert server.address == "127.0.0.1"
    assert server.url.startswith(f"http://127.0.0.1:{server.port}/start?token=")


def test_the_link_signs_you_in_with_a_cookie_that_scripts_cannot_read(server: WebServer) -> None:
    reply = Browser(server, signed_in=False).sign_in()
    assert reply.status == 303 and reply.headers["location"] == "/"
    cookie = reply.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie


def test_nothing_is_served_without_the_cookie(server: WebServer) -> None:
    stranger = Browser(server, signed_in=False)
    for path in ("/", "/convert/whatsapp", "/browse", "/jobs/1", "/output/Chattr/index.html"):
        reply = stranger.get(path)
        assert reply.status == 403, path
        assert "chattr-cli serve" in reply.text  # tells a person what to do


def test_a_wrong_token_or_cookie_is_refused(server: WebServer) -> None:
    stranger = Browser(server, signed_in=False)
    assert stranger.get("/start?token=nope").status == 403
    assert stranger.get("/start").status == 403
    assert stranger.get("/", cookie="chattr_session=nope").status == 403


def test_another_host_name_is_refused_against_dns_rebinding(browser: Browser) -> None:
    assert browser.get("/", headers={"Host": "evil.example"}).status == 403
    assert browser.get("/", headers={"Host": f"localhost:{browser.server.port}"}).status == 200


def test_a_form_from_another_site_is_refused(browser: Browser) -> None:
    body = {"export": "x"}
    assert (
        browser.post("/convert/whatsapp", body, headers={"Origin": "http://evil.example"}).status
        == 403
    )
    assert browser.post("/html", headers={"Origin": "null"}).status == 403
    assert browser.post("/html", cookie=None).status == 403


def test_the_script_is_served_from_the_package_and_nothing_loads_from_the_network(
    browser: Browser,
) -> None:
    script = browser.get("/static/htmx.min.js")
    assert script.status == 200 and "javascript" in script.headers["content-type"]
    assert b"htmx" in script.body
    for path in ("/", "/convert/whatsapp", "/convert/imessage", "/browse"):
        page = browser.get(path).text
        assert "https://" not in page and "http://" not in page, path
        for target in link_targets(page):
            assert target.startswith(("/", "#", "?")), (path, target)


# --- The pages ------------------------------------------------------------------------------


def test_the_home_page_offers_every_service(browser: Browser) -> None:
    page = browser.get("/")
    assert page.status == 200 and "text/html" in page.headers["content-type"]
    for service in SERVICES:
        assert f'href="/convert/{service}"' in page.text
    assert "Output/Chattr" not in page.text  # nothing to open before anything is made


def test_the_home_page_links_the_pages_once_they_exist(browser: Browser, chattr_root: Path) -> None:
    folder = chattr_root / "Output" / "Chattr"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<html>hello</html>")
    assert 'href="/output/Chattr/index.html"' in browser.get("/").text


def test_each_service_has_its_form_with_its_own_options(browser: Browser) -> None:
    forms = {s: browser.get(f"/convert/{s}") for s in SERVICES}
    assert all(f.status == 200 for f in forms.values())
    for service, form in forms.items():
        assert 'name="export"' in form.text and 'name="owner"' in form.text, service
        assert 'method="post"' in form.text.lower()
    assert (
        'name="spam_mode"' in forms["imessage"].text and 'name="workers"' in forms["imessage"].text
    )
    assert (
        'name="transcribe"' in forms["voicemail"].text and 'name="model"' in forms["voicemail"].text
    )
    for service in ("whatsapp", "mail", "googlevoice"):
        assert 'name="spam_mode"' not in forms[service].text
        assert 'name="transcribe"' not in forms[service].text
    assert 'name="copy_media"' in forms["whatsapp"].text
    assert 'name="copy_media"' not in forms["mail"].text


def test_the_forms_start_from_the_settings(browser: Browser, chattr_root: Path) -> None:
    import json

    config = chattr_root / "config.json"
    data = json.loads(config.read_text())
    data["global"]["owner"] = {"name": "Maria Costa", "identifiers": []}
    config.write_text(json.dumps(data))
    form = browser.get("/convert/whatsapp").text
    assert 'value="Maria Costa"' in form
    imessage = browser.get("/convert/imessage").text
    assert re.search(r'<option value="aggressive"\s+selected', imessage)  # Services/iMessage.json


def test_each_form_says_how_to_export(browser: Browser) -> None:
    assert "Export chat" in browser.get("/convert/whatsapp").text
    assert "Full Disk Access" in browser.get("/convert/imessage").text
    assert "Takeout" in browser.get("/convert/mail").text


def test_an_unknown_service_is_not_found(browser: Browser) -> None:
    assert browser.get("/convert/telegram").status == 404
    assert browser.get("/nothing-here").status == 404


def test_the_form_escapes_what_it_shows(browser: Browser) -> None:
    reply = browser.get("/convert/whatsapp?export=%22%3E%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert(1)" not in reply.text


# --- The folder browser ---------------------------------------------------------------------


def test_the_browser_lists_folders_first_and_stays_in_its_roots(
    browser: Browser, tmp_path: Path
) -> None:
    folder = tmp_path / "Downloads"
    (folder / "b.zip").write_bytes(b"zip")
    (folder / "a-folder").mkdir()
    (folder / ".hidden").write_text("x")
    page = browser.get(f"/browse?path={folder}").text
    assert page.index("a-folder") < page.index("b.zip") and ".hidden" not in page
    assert browser.get("/browse?path=/etc").status == 403
    assert browser.get(f"/browse?path={tmp_path}/../..").status == 403
    assert browser.get(f"/browse?path={folder}/missing").status == 404
    assert browser.get("/browse").status == 200  # starts at the first root


def test_a_folder_that_is_a_link_out_of_the_roots_is_refused(
    browser: Browser, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "outside-roots"
    outside.mkdir(exist_ok=True)
    (tmp_path / "Downloads" / "escape").symlink_to(outside)
    assert browser.get(f"/browse?path={tmp_path}/Downloads/escape").status == 403


# --- Serving the output ---------------------------------------------------------------------


def test_output_is_served_from_inside_output_only(browser: Browser, chattr_root: Path) -> None:
    page = chattr_root / "Output" / "Chattr" / "Messages" / "Ana" / "(2024) Ana.html"
    page.parent.mkdir(parents=True)
    page.write_text("<html>Ana</html>", encoding="utf-8")
    (chattr_root / "Output" / "Chattr" / "index.html").write_text("<html>index</html>")
    served = browser.get("/output/Chattr/Messages/Ana/(2024)%20Ana.html")
    assert served.status == 200 and "text/html" in served.headers["content-type"]
    assert browser.get("/output/Chattr/").text == "<html>index</html>"  # a folder shows its index
    assert browser.get("/output/Chattr/Messages/").status == 404  # no folder listings
    for attempt in (
        "/output/../config.json",
        "/output/%2e%2e/config.json",
        "/output/Chattr/../../config.json",
        "/output/..%2fconfig.json",
        "/output//etc/passwd",
    ):
        assert browser.get(attempt).status in (403, 404), attempt


def test_a_link_inside_output_that_leaves_it_is_refused(
    browser: Browser, chattr_root: Path
) -> None:
    (chattr_root / "Output" / "Chattr").mkdir(parents=True, exist_ok=True)
    (chattr_root / "Output" / "Chattr" / "secret.txt").symlink_to(chattr_root / "config.json")
    assert browser.get("/output/Chattr/secret.txt").status == 403


def test_a_refused_form_leaves_the_connection_usable(server: WebServer) -> None:
    import http.client
    import urllib.parse

    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    cookie = Browser(server).cookie or ""
    host = f"127.0.0.1:{server.port}"
    connection.request(
        "POST",
        "/convert/whatsapp",
        body=urllib.parse.urlencode({"export": "x" * 500}),
        headers={"Host": host, "Cookie": cookie, "Origin": "http://evil.example",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )  # fmt: skip
    assert connection.getresponse().read() and True
    connection.request("GET", "/", headers={"Host": host, "Cookie": cookie})
    assert connection.getresponse().status == 200  # not "bad request syntax" from leftover bytes
    connection.close()
