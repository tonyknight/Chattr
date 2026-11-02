"""`chattr-cli serve`: a small local web page for turning an export into pages.

Python only: the standard library's HTTP server, Jinja2 for the pages and the vendored htmx for the
few live parts (browsing folders, following a conversion). It imports nothing from Caixa and needs
no Node and no DeepSeek Harness.

The steps are the command line's: choose a service, point at the export, set options, convert with
progress, open the output. The form is turned into the arguments `chattr-cli convert` takes, so the
page and the command line cannot drift apart.

Safety, because a web page on your own computer can still be reached by other programs and other
websites:

- it listens on `127.0.0.1` only;
- the link `serve` prints carries a random token that becomes an `HttpOnly`, `SameSite=Strict`
  cookie; every other request must carry it;
- the `Host` header must be this computer's address (DNS rebinding), and a POST must come from this
  page (`Origin`);
- the folder browser shows only the home folder and the Chattr folder, and `/output/` serves only
  files inside the Chattr folder's `Output/`, never following a link out of it.
"""

from __future__ import annotations

import hmac
import mimetypes
import secrets
import threading
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

from chattr.config import Settings

PACKAGE = Path(__file__).parent
COOKIE = "chattr_session"
MAX_BODY = 1_000_000


@dataclass(frozen=True)
class ServiceInfo:
    key: str
    name: str
    blurb: str
    how_to_export: str
    export_label: str
    export_help: str
    export_required: bool = True


SERVICES: tuple[ServiceInfo, ...] = (
    ServiceInfo(
        "whatsapp",
        "WhatsApp",
        "A chat exported from the app",
        "In WhatsApp, open a chat, tap the contact or group name, then “Export chat”, and choose "
        "“Attach Media” to keep photos and voice notes. Unzip what it makes. Each chat is "
        "exported on its own, so repeat this for each chat you want.",
        "The chat file",
        "The _chat.txt (or “WhatsApp Chat with …”.txt) file from the unzipped export.",
    ),
    ServiceInfo(
        "messenger",
        "Messenger",
        "Facebook Messenger, from “Download your information”",
        "In Facebook’s Accounts Center choose “Your information and permissions”, then "
        "“Download your information”. Pick Messages, choose the JSON format and download. Unzip "
        "it and find a conversation’s message_1.json inside your_facebook_activity/messages/inbox.",
        "The conversation file",
        "One conversation’s message_1.json.",
    ),
    ServiceInfo(
        "instagram",
        "Instagram",
        "Instagram messages, from “Download your information”",
        "In Instagram’s Accounts Center choose “Your information and permissions”, then "
        "“Download your information”. Pick Messages, choose the JSON format and download. Unzip "
        "it and find a conversation’s message_1.json inside your_instagram_activity/messages/inbox.",
        "The conversation file",
        "One conversation’s message_1.json.",
    ),
    ServiceInfo(
        "imessage",
        "iMessage",
        "Messages on this Mac (chat.db)",
        "iMessage lives in ~/Library/Messages/chat.db on your Mac. macOS only lets a program read "
        "it after you give that program “Full Disk Access” in System Settings, Privacy & "
        "Security. Chattr opens the database read-only and never changes it.",
        "The Messages database",
        "Leave empty to use ~/Library/Messages/chat.db, or give a copy of chat.db.",
        export_required=False,
    ),
    ServiceInfo(
        "mail",
        "Mail",
        "Gmail or any .mbox mail export",
        "For Gmail, go to Google Takeout (takeout.google.com), choose only “Mail”, and download "
        "the export. Unzip it and find the .mbox file inside Takeout/Mail. Other mail programs "
        "that can export .mbox files work too.",
        "The .mbox file",
        "The mailbox file from the unzipped export.",
    ),
    ServiceInfo(
        "googlevoice",
        "Google Voice",
        "Calls, texts and voicemail from Takeout",
        "Go to Google Takeout (takeout.google.com), choose only “Voice”, and download the "
        "export. Unzip it; the calls, texts and voicemail are in Takeout/Voice/Calls.",
        "The Calls folder",
        "The Takeout/Voice/Calls folder from the unzipped export.",
    ),
    ServiceInfo(
        "voicemail",
        "iPhone voicemail",
        "A voicemail folder from an iPhone backup",
        "Copy the voicemail folder out of a Finder or iTunes backup: it holds voicemail.db and "
        "the numbered .amr audio files (in a backup they are under Library/Voicemail).",
        "The voicemail folder",
        "The folder that holds voicemail.db.",
    ),
)
BY_KEY = {service.key: service for service in SERVICES}


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(PACKAGE / "templates" / "web"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


class WebServer:
    """The local web page. `start()` serves in a thread; `stop()` ends it."""

    address = "127.0.0.1"

    def __init__(self, root: Path, roots: Optional[Sequence[Path]] = None, port: int = 0) -> None:
        self.root = Path(root).resolve()
        self.roots = [
            Path(r).resolve() for r in (roots if roots is not None else self._default_roots())
        ]
        self.token = secrets.token_urlsafe(24)
        self.env = _environment()
        self._http = ThreadingHTTPServer((self.address, port), _make_handler(self))
        self._http.daemon_threads = True
        self._thread: Optional[threading.Thread] = None

    def _default_roots(self) -> list[Path]:
        return [Path.home(), self.root]

    @property
    def port(self) -> int:
        return int(self._http.server_address[1])

    @property
    def url(self) -> str:
        """The link that signs a browser in; it is the only place the token appears."""
        return f"http://{self.address}:{self.port}/start?token={self.token}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._http.serve_forever, name="chattr-web", daemon=True
        )
        self._thread.start()

    def serve_forever(self) -> None:
        self._http.serve_forever()

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()
        if self._thread:
            self._thread.join(timeout=10)

    # --- What the pages show ----------------------------------------------------------------

    def settings(self) -> Settings:
        return Settings.load(self.root)

    def render(self, template: str, **values: Any) -> str:
        return self.env.get_template(template).render(root=str(self.root), **values)

    def outputs(self) -> list[dict[str, str]]:
        """Pages that exist: the archive's, and each service's own folder."""
        found = []
        base = self.root / "Output"
        for index in sorted(base.glob("*/index.html")) if base.is_dir() else []:
            folder = index.parent.name
            label = "Everything in your archive" if folder == "Chattr" else f"{folder} pages"
            found.append(
                {"href": f"/output/{urllib.parse.quote(folder)}/index.html", "label": label}
            )
        found.sort(key=lambda item: (item["label"] != "Everything in your archive", item["label"]))
        return found

    def inside_roots(self, path: Path) -> Optional[Path]:
        resolved = path.expanduser().resolve()
        for root in self.roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        return None

    def form_fields(self, service: ServiceInfo, values: dict[str, str]) -> list[dict[str, Any]]:
        """The option fields for a service, starting from the settings (or what was last sent)."""
        settings = self.settings()

        def text(name: str, label: str, help: str, default: str = "") -> dict[str, Any]:
            return {"name": name, "label": label, "help": help, "kind": "text",
                    "value": values.get(name, default)}  # fmt: skip

        def check(name: str, label: str, help: str, default: bool) -> dict[str, Any]:
            sent = bool(values) and name in values
            return {"name": name, "label": label, "help": help, "kind": "checkbox",
                    "value": sent if values else default}  # fmt: skip

        owner = str(settings.get("global.owner.name", "") or "")
        region = str(settings.get("global.contacts.phone_region", "US") or "US")
        contacts = settings.get_expanded_path("global.contacts.vcf_file_path") or ""
        fields: list[dict[str, Any]] = [
            text("owner", "Your name", "Whose archive this is; your own messages are shown as sent by you.", owner),
            text("contacts", "Contacts file (optional)", "A vCard (.vcf) export of your address book, to name phone numbers.", contacts),
            text("region", "Phone region", "Two-letter country code used to read local phone numbers.", region),
            text("aliases", "Names file (optional)", "A JSON file of {number, address or name: person} to say who is who.", ""),
        ]  # fmt: skip
        if service.key == "imessage":
            prefix = "services.imessage.preferences"
            mode = str(settings.get(f"{prefix}.spam_filtering.mode", "aggressive"))
            modes = settings.get(f"{prefix}.spam_filtering.available_modes", []) or [mode]
            fields += [
                {
                    "name": "spam_mode",
                    "label": "Spam filtering",
                    "help": "How much machine-sent noise to tag (nothing is thrown away).",
                    "kind": "select",
                    "choices": [{"value": m, "label": m.capitalize()} for m in modes],
                    "value": values.get("spam_mode", mode),
                },
                {
                    "name": "workers",
                    "label": "Workers",
                    "help": "How many parts of the database are read at once.",
                    "kind": "number",
                    "value": values.get(
                        "workers", str(settings.get(f"{prefix}.performance.workers", 4))
                    ),
                },
                text("limit", "Limit", "Read only this many messages; for trying it out. Empty reads everything."),
                text("attachment_root", "Attachments folder", "Where macOS keeps attachments. Empty uses the usual place."),
            ]  # fmt: skip
        if service.key == "voicemail":
            fields += [
                check("transcribe", "Transcribe on this computer", "Turn each voicemail into text with a speech model stored here. Nothing is sent anywhere.", False),
                text("model", "Speech model", "Path to a speech-to-text model on this computer (never downloaded)."),
                text("language", "Language", "A language code such as en or pt. Empty detects it."),
            ]  # fmt: skip
        if service.key in ("whatsapp", "messenger", "instagram"):
            fields.append(
                check("copy_media", "Copy photos and other files", "So the pages can show them. The originals stay where they are.", True)
            )  # fmt: skip
        fields += [
            {
                "name": "route",
                "label": "Where to put the result",
                "help": "The archive keeps everything from every service together; JSON files keep this service on its own.",
                "kind": "select",
                "choices": [
                    {"value": "central", "label": "In the archive (recommended)"},
                    {"value": "service", "label": "As JSON files for this service only"},
                ],
                "value": values.get("route", "central"),
            },
            check("html", "Make web pages", "Write pages you can open in a web browser.", True),
        ]
        return fields


# --- HTTP ---------------------------------------------------------------------------------------


def _make_handler(web: WebServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "chattr"
        protocol_version = "HTTP/1.1"
        timeout = 30

        def log_message(self, format: str, *args: Any) -> None:  # quiet: no access log
            return

        # --- Sending --------------------------------------------------------------------

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str = "text/html; charset=utf-8",
            headers: Optional[dict[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _page(self, template: str, status: int = 200, **values: Any) -> None:
            self._send(status, web.render(template, **values).encode("utf-8"))

        def _message(self, status: int, heading: str, message: str) -> None:
            self._page("message.html", status, heading=heading, message=message)

        def _redirect(self, location: str, headers: Optional[dict[str, str]] = None) -> None:
            self._send(303, b"", headers={"Location": location, **(headers or {})})

        # --- Who is asking --------------------------------------------------------------

        def _local_host(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{web.port}", f"localhost:{web.port}")

        def _signed_in(self) -> bool:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get(COOKIE)
            return bool(morsel) and hmac.compare_digest(morsel.value, web.token)  # type: ignore[union-attr]

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in (
                f"http://127.0.0.1:{web.port}",
                f"http://localhost:{web.port}",
            ):
                return False
            return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

        def _refuse(self) -> None:
            self._message(
                403,
                "Not allowed",
                "Open the link that chattr-cli serve printed in your terminal to use this page.",
            )

        def _allowed(self, posting: bool) -> bool:
            if not self._local_host() or not self._signed_in():
                self._refuse()
                return False
            if posting and not self._same_origin():
                self._refuse()
                return False
            return True

        # --- Routes ---------------------------------------------------------------------

        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            path, query = parts.path, urllib.parse.parse_qs(parts.query)
            if path == "/start":
                return self._start(query)
            if not self._allowed(posting=False):
                return
            if path == "/":
                return self._page("home.html", services=SERVICES, outputs=web.outputs())
            if path == "/static/htmx.min.js":
                return self._send(
                    200,
                    (PACKAGE / "static" / "htmx.min.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
            if path.startswith("/convert/"):
                return self._convert_form(path.removeprefix("/convert/"), query)
            if path == "/browse":
                return self._browse(query)
            if path.startswith("/output/"):
                return self._output(path.removeprefix("/output/"))
            self._message(404, "Not found", "There is nothing at that address.")

        def _read_body(self) -> Optional[dict[str, str]]:
            """The submitted form, always read in full (so a refused request leaves no stray bytes
            to be mistaken for the next request); None when it is too large."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = MAX_BODY + 1
            if length > MAX_BODY:
                self.close_connection = True
                return None
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        def do_POST(self) -> None:
            form = self._read_body()
            if not self._allowed(posting=True):
                return
            if form is None:
                return self._message(413, "Too large", "That form is too large.")
            self._message(404, "Not found", "There is nothing at that address.")

        def _start(self, query: dict[str, list[str]]) -> None:
            if not self._local_host():
                return self._refuse()
            given = (query.get("token") or [""])[0]
            if not given or not hmac.compare_digest(given, web.token):
                return self._refuse()
            cookie = f"{COOKIE}={web.token}; HttpOnly; SameSite=Strict; Path=/"
            self._redirect("/", {"Set-Cookie": cookie})

        def _convert_form(self, key: str, query: dict[str, list[str]]) -> None:
            service = BY_KEY.get(key)
            if service is None:
                return self._message(404, "Not found", f"Chattr has no source called “{key}”.")
            values = {"export": (query.get("export") or [""])[0]}
            self._page(
                "convert.html",
                service=service,
                fields=web.form_fields(service, {}),
                values=values,
                error=None,
            )

        def _browse(self, query: dict[str, list[str]]) -> None:
            wanted = (query.get("path") or [str(web.roots[0])])[0]
            folder = web.inside_roots(Path(wanted))
            if folder is None:
                return self._message(
                    403, "Not allowed", "That folder is outside the folders Chattr may show."
                )
            if not folder.is_dir():
                return self._message(404, "Not found", "That folder does not exist.")
            entries = []
            children = sorted(
                (p for p in folder.iterdir() if not p.name.startswith(".")),
                key=lambda p: (not p.is_dir(), p.name.casefold()),
            )
            for child in children:
                if web.inside_roots(child) is None:  # a link that leaves the allowed folders
                    continue
                entries.append(
                    {"name": child.name, "path": str(child), "directory": child.is_dir()}
                )
            parent = folder.parent if folder not in web.roots else None
            self._send(
                200,
                web.env.get_template("browse.html")
                .render(here=str(folder), parent=str(parent) if parent else None, entries=entries)
                .encode("utf-8"),
            )

        def _output(self, relative: str) -> None:
            base = (web.root / "Output").resolve()
            wanted = urllib.parse.unquote(relative).lstrip("/")
            if "\x00" in wanted:
                return self._message(404, "Not found", "There is nothing at that address.")
            target = (base / wanted).resolve()
            if target != base and not target.is_relative_to(base):
                return self._message(403, "Not allowed", "That file is outside the output folder.")
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                return self._message(404, "Not found", "There is nothing at that address.")
            if not target.resolve().is_relative_to(base):
                return self._message(403, "Not allowed", "That file is outside the output folder.")
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if kind.startswith("text/"):
                kind += "; charset=utf-8"
            self._send(200, target.read_bytes(), kind)

    return Handler
