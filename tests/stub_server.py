"""A tiny OpenAI-compatible server on localhost for tests of the translation client."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class StubServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reply: Any = {
            "choices": [{"message": {"content": "  I arrived! Let us have dinner.  "}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 9},
        }
        self.status = 200
        self.raw: bytes | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "json": json.loads(body)}
                )
                payload = owner.raw if owner.raw is not None else json.dumps(owner.reply).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
