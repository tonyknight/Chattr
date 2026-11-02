"""`chattr-cli serve` as a command, and the promise that it needs nothing but Chattr."""

from __future__ import annotations

import http.client
import os
import re
import signal
import site
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from chattr import cli, web


def _environment(home: Path) -> dict[str, str]:
    environment = {k: v for k, v in os.environ.items() if k != "CHATTR_ROOT"}
    environment["HOME"] = str(home)
    environment["PYTHONUSERBASE"] = site.getuserbase()  # keep finding packages installed per user
    return environment


def test_the_real_command_serves_a_page_and_stops_on_control_c(
    chattr_root: Path, tmp_path: Path
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "chattr", "--root", str(chattr_root), "serve", "--no-browser"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_environment(tmp_path / "home"),
    )
    try:
        assert process.stdout is not None
        text = ""
        link = None
        while link is None:
            line = process.stdout.readline()
            assert line, process.stderr.read() if process.stderr else ""
            text += line
            link = re.search(r"http://127\.0\.0\.1:(\d+)/start\?token=[\w-]+", line)
        port = int(link.group(1))
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request(
            "GET", link.group(0).split(str(port))[1], headers={"Host": f"127.0.0.1:{port}"}
        )
        reply = connection.getresponse()
        reply.read()
        assert reply.status == 303
        cookie = reply.getheader("Set-Cookie", "").split(";")[0]
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}", "Cookie": cookie})
        page = connection.getresponse()
        assert page.status == 200 and b"Chattr" in page.read()
        connection.close()
    finally:
        process.send_signal(signal.SIGINT)
        output, errors = process.communicate(timeout=60)
    assert process.returncode == 0, errors
    assert "Stopped" in output
    assert errors == ""  # nothing logged to the terminal beyond the link


def test_the_link_is_printed_once_and_opened_unless_told_not_to(
    chattr_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    def stop(self: web.WebServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(web.WebServer, "serve_forever", stop)
    assert cli.main(["--root", str(chattr_root), "serve"]) == 0
    out = capsys.readouterr().out
    (url,) = opened
    assert url in out and out.count(url.split("token=")[1]) == 1  # the token appears once
    assert url.startswith("http://127.0.0.1:") and "Control-C" in out
    opened.clear()
    assert cli.main(["--root", str(chattr_root), "serve", "--no-browser"]) == 0
    assert opened == []


def test_a_port_already_in_use_is_explained(
    chattr_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert (
            cli.main(["--root", str(chattr_root), "serve", "--port", str(port), "--no-browser"])
            == 1
        )
    assert f"port {port}" in capsys.readouterr().out.lower()


def test_serve_only_listens_on_this_computer(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main(["serve", "--host", "0.0.0.0"])
    assert stop.value.code == 2 and "--host" in capsys.readouterr().err


def test_serve_is_listed_in_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "serve" in capsys.readouterr().out


# --- Needs nothing but Chattr ---------------------------------------------------------------


def test_the_web_page_imports_nothing_from_caixa(tmp_path: Path) -> None:
    code = (
        "import sys, chattr.web, chattr.cli\n"
        "bad = [m for m in sys.modules if m == 'caixa' or m.startswith('caixa.')]\n"
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(  # from an empty folder, so nothing next to it can be picked up
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr


def test_the_package_data_covers_the_pages_and_the_script() -> None:
    """What `pip install` ships: every template and static file the web page reads."""
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; older versions skip this check
    project = Path(__file__).resolve().parents[1]
    config = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["chattr"]
    package = project / "src" / "chattr"
    shipped = {str(p.relative_to(package)) for pattern in patterns for p in package.glob(pattern)}
    for needed in (
        "static/htmx.min.js",
        "static/htmx-LICENSE.txt",
        "templates/web/base.html",
        "templates/web/home.html",
        "templates/web/convert.html",
        "templates/web/job.html",
        "templates/web/job_status.html",
        "templates/web/browse.html",
        "templates/web/message.html",
        "templates/index.html",
    ):
        assert needed in shipped, needed
    on_disk = {str(p.relative_to(package)) for p in (package / "templates" / "web").glob("*.html")}
    assert on_disk <= shipped  # no page was added without being shipped
