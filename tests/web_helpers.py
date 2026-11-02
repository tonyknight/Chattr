"""A tiny browser for testing `chattr-cli serve`: cookies, forms and polling, standard library only."""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from chattr.web import WebServer


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Browser:
    def __init__(self, server: WebServer, signed_in: bool = True) -> None:
        self.server = server
        self.cookie: Optional[str] = None
        if signed_in:
            self.sign_in()

    def request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        cookie: Optional[str] = "default",
    ) -> Reply:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        sent = {"Host": f"127.0.0.1:{self.server.port}", **(headers or {})}
        if cookie == "default":
            cookie = self.cookie
        if cookie:
            sent["Cookie"] = cookie
        data = None
        if body is not None:
            data = urllib.parse.urlencode(body).encode()
            sent["Content-Type"] = "application/x-www-form-urlencoded"
            sent.setdefault("Origin", f"http://127.0.0.1:{self.server.port}")
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        reply = Reply(
            response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        )
        connection.close()
        return reply

    def get(self, path: str, **kwargs: object) -> Reply:
        return self.request("GET", path, **kwargs)  # type: ignore[arg-type]

    def post(self, path: str, body: Optional[dict[str, str]] = None, **kwargs: object) -> Reply:
        return self.request("POST", path, body=body or {}, **kwargs)  # type: ignore[arg-type]

    def sign_in(self) -> Reply:
        reply = self.request("GET", f"/start?token={self.server.token}", cookie=None)
        cookie = reply.headers.get("set-cookie", "")
        self.cookie = cookie.split(";")[0] if cookie else None
        return reply

    def run_job(self, reply: Reply, timeout: float = 120) -> Reply:
        """Follow a redirect to a job page and poll its status until it stops polling."""
        assert reply.status == 303, reply.text
        page = reply.headers["location"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get(page + "/status")
            assert status.status == 200
            if "hx-trigger" not in status.text:
                return status
            time.sleep(0.1)
        raise AssertionError("the job never finished")


def link_targets(html: str) -> list[str]:
    return re.findall(r'(?:href|src)="([^"]+)"', html)


def json_of(reply: Reply) -> object:
    return json.loads(reply.text)
