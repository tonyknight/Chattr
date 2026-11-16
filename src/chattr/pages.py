"""Render the planned pages as self-contained HTML.

Every person and group has **one page** with everything from them, every platform and every year:
a summary (how much, from when to when, from which sources, their groups and mail), a bar of years,
a heat-map calendar per year, a block per date, and an entry per message with a platform badge, a
stable `id="m-<id>"` anchor, the sender, and the local time with its offset (or "UTC" / "zone
unknown") at the stored precision.

A page can hold a whole life's messages with someone, so it is built to open at once however long it
is: the browser draws it as it reads it (the top is on screen before the end has arrived), it lays
out only the dates that are on screen (`content-visibility`), and pictures, video and audio load
only when they are scrolled to. A bar of years stays at the top while reading; each year begins with
its calendar. The page needs no script; one small script (`templates/lightbox.js`) adds a
full-size view of the pictures, and without it each picture is still a link to its file. Caixa's
viewer allows that one script, by its hash, and no other. Text is always
escaped; links are only made from http(s) addresses; attachment links are only made to files that
exist inside the given attachment folders. A JSON data island (`#chattr-data`) carries every stored
column of the page's messages so the database can be rebuilt from the pages.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union
from urllib.parse import quote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from chattr.filing import playable_copy
from chattr.render import GROUPS, PEOPLE, PageSpec, Renderer, compact

PathLike = Union[str, Path]
_TEMPLATES = Path(__file__).resolve().parent / "templates"
_MINUS = "−"
_ISLAND_ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    " ": "\\u2028",
    " ": "\\u2029",
}


def _nl2br(value: Optional[str]) -> Markup:
    """Escape text, then keep its line breaks."""
    return Markup("<br>\n").join(escape(line) for line in (value or "").split("\n"))


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html"])
    )
    env.filters["nl2br"] = _nl2br
    return env


def island_json(document: dict[str, Any]) -> str:
    """The data island's JSON, safe to place inside a `<script>` element."""
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return "".join(_ISLAND_ESCAPES.get(ch, ch) for ch in text)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "other"


def _duration(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _offset_text(message: dict[str, Any]) -> str:
    offset = message["tz_offset_minutes"]
    if message["tz_assumed"] and not offset:
        return "zone unknown"
    if offset is None:
        return "zone unknown"
    if offset == 0:
        return "UTC"
    sign = "+" if offset > 0 else _MINUS
    hours, minutes = divmod(abs(offset), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _time_text(message: dict[str, Any]) -> str:
    local: datetime = message["local"]
    return local.strftime("%H:%M" if message["time_precision"] == "minute" else "%H:%M:%S")


def _attachment_kind(mime: Optional[str], name: str) -> str:
    major = (mime or "").split("/")[0]
    if major in ("image", "video", "audio"):
        return major
    suffix = Path(name).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"):
        return "image"
    if suffix in (".mp4", ".mov", ".webm"):
        return "video"
    if suffix in (".mp3", ".m4a", ".aac", ".wav", ".amr", ".opus", ".caf"):
        return "audio"
    return "file"


class _Site:
    """Everything a page needs beyond the Renderer: where files are and how to link to them."""

    def __init__(self, renderer: Renderer, out_root: Path, attachment_roots: Iterable[PathLike]):
        self.renderer = renderer
        self.out_root = out_root
        # the output folder first: filed attachments are recorded relative to it
        self.roots = [out_root.resolve(), *(Path(r).resolve() for r in attachment_roots)]
        self.env = _environment()

    def _file(self, storage_path: Optional[str]) -> Optional[Path]:
        if not storage_path:
            return None
        for root in self.roots:
            candidate = (root / storage_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return candidate
        return None

    @staticmethod
    def _link(file: Path, page_dir: Path) -> str:
        relative = os.path.relpath(file, page_dir.resolve()).replace(os.sep, "/")
        return quote(relative, safe="/()")

    def playable(self, storage_path: Optional[str], page_dir: Path) -> Optional[str]:
        """A link to the browser-playable copy of an audio file, when one has been made."""
        file = self._file(storage_path)
        copy = playable_copy(file) if file else None
        return self._link(copy, page_dir) if copy and copy.is_file() else None

    def href(self, storage_path: Optional[str], page_dir: Path) -> Optional[str]:
        """A relative link to an archived file, only if it exists inside one of the roots."""
        file = self._file(storage_path)
        return self._link(file, page_dir) if file else None

    def label(self, handle_id: Optional[int]) -> str:
        if not handle_id:
            return "Me"
        handle = self.renderer.handles[handle_id]
        return handle["person"] or handle["display_name"] or handle["identifier"]


def _standing_reactions(site: _Site, message_id: str) -> list[dict[str, str]]:
    """Reactions still in force: a later removal cancels the same sender's earlier one."""
    standing: dict[tuple[Any, ...], dict[str, Any]] = {}
    for reaction in sorted(
        site.renderer.reactions.get(message_id, []),
        key=lambda r: (r["timestamp_utc"] or "", r["id"]),
    ):
        key = (
            reaction["sender_handle_id"],
            reaction["type"],
            reaction["emoji"],
            reaction["target_part"],
        )
        if reaction["action"] == "removed":
            standing.pop(key, None)
        else:
            standing[key] = reaction
    return [
        {
            "label": r["emoji"] or r["type"],
            "who": site.label(r["sender_handle_id"]),
        }
        for r in standing.values()
    ]


def _entry(site: _Site, message: dict[str, Any], page_dir: Path) -> dict[str, Any]:
    specific = json.loads(message["service_specific"]) if message["service_specific"] else {}
    kind = message["content_type"]
    sender = message["sender_handle_id"]
    if kind == "system" or (not sender and not message["is_from_owner"]):
        direction = "system"
    else:
        direction = "sent" if message["is_from_owner"] else "received"
    link = None
    if specific.get("shared_link") and urlparse(specific["shared_link"]).scheme in (
        "http",
        "https",
    ):
        link = {
            "href": specific["shared_link"],
            "text": specific.get("link_text") or specific["shared_link"],
            "summary": specific.get("link_summary") or "",
            "site": specific.get("link_site") or "",
        }
    call_label = {
        "received": "Received call",
        "placed": "Placed call",
        "missed": "Missed call",
    }.get(specific.get("direction", ""), "Call")
    service = message["service"]
    return {
        "id": message["id"],
        "service": service,
        "service_slug": _slug(service),
        "direction": direction,
        "kind": kind if kind in ("call", "voicemail", "system") else "text",
        "author": site.label(sender) if sender else ("Me" if message["is_from_owner"] else ""),
        "iso": message["timestamp_utc"],
        "time": _time_text(message),
        "offset": _offset_text(message),
        "body": message["body"],
        "translation": message["translation"],
        "transcript": message["transcript"],
        "call_label": call_label,
        "duration": _duration(specific.get("duration_seconds")),
        "link": link,
        "link_text": None if link else specific.get("link_text"),
        "attachments": [
            {
                "name": a["original_filename"]
                or (a["storage_path"] or "").rsplit("/", 1)[-1]
                or "file",
                "href": site.href(a["storage_path"], page_dir),
                "kind": _attachment_kind(
                    a["mime_type"], a["original_filename"] or a["storage_path"] or ""
                ),
                "playable": site.playable(a["storage_path"], page_dir),
                "format": Path(a["storage_path"] or a["original_filename"] or "").suffix.lower(),
                "needs_copy": playable_copy(Path(a["storage_path"] or "x")) is not None,
            }
            for a in site.renderer.attachments.get(message["id"], [])
        ],
        "reactions": _standing_reactions(site, message["id"]),
        "reply": _reply(site, message, page_dir),
        "edits": [
            {"text": e.get("text") or "", "at": _when(e.get("at"), message)}
            for e in specific.get("edits", [])[:-1]  # the last is the text shown
        ],
        "edited": bool(specific.get("edits") or specific.get("edited_at")),
        "marks": [
            *(["Deleted in Messages"] if specific.get("recently_deleted") else []),
            *([str(specific["effect"])] if specific.get("effect") else []),
            *([str(specific["apple_filter"]).capitalize()] if specific.get("apple_filter") else []),
        ],
    }


def _when(iso: Optional[str], message: dict[str, Any]) -> str:
    """An edit's time in the message's own zone, as the page shows times."""
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    offset = message["tz_offset_minutes"]
    if offset is not None:
        moment = moment + timedelta(minutes=offset)
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def _reply(site: _Site, message: dict[str, Any], page_dir: Path) -> Optional[dict[str, str]]:
    """What a reply answers: who said it, its first words, and a link to it (on any page)."""
    target_id = site.renderer.by_source.get(message["reply_to"] or "")
    target = site.renderer.messages.get(target_id or "")
    if target is None:
        return None
    spec = site.renderer.page_of.get(target["id"])
    here = site.renderer.page_of.get(message["id"])
    href = f"#m-{target['id']}"
    if spec is not None and here is not None and spec.path != here.path:
        other = os.path.relpath(site.out_root / spec.path, page_dir).replace(os.sep, "/")
        href = quote(other, safe="/()") + href
    words = (target["body"] or target["transcript"] or "").strip().replace("\n", " ")
    sender = target["sender_handle_id"]
    return {
        "href": href,
        "author": site.label(sender) if sender else ("Me" if target["is_from_owner"] else ""),
        "text": (words[:120] + "…") if len(words) > 120 else (words or "(no text)"),
    }


def _calendar(year: int, counts: dict[str, int]) -> list[dict[str, Any]]:
    busiest = max(counts.values(), default=1)
    months = []
    for month in range(1, 13):
        cells = []
        for week in calendar.monthcalendar(year, month):
            for day in week:
                date = f"{year}-{month:02d}-{day:02d}" if day else ""
                count = counts.get(date, 0)
                level = 0 if not count else max(1, min(4, -(-count * 4 // busiest)))
                cells.append({"day": day, "date": date, "count": count, "level": level})
        months.append({"name": calendar.month_name[month], "cells": cells})
    return months


def _day_label(date: datetime) -> str:
    return f"{date:%A}, {date:%B} {date.day}, {date.year}"


def _message_page(site: _Site, spec: PageSpec, relations: "_Relations") -> str:
    renderer = site.renderer
    page_dir = site.out_root / spec.folder
    up = "../" * (spec.folder.count("/") + 1)
    years: dict[int, dict[str, Any]] = {}
    services: dict[str, int] = {}
    for message_id in spec.message_ids:
        message = renderer.messages[message_id]
        local: datetime = message["local"]
        year = years.setdefault(
            local.year, {"year": local.year, "count": 0, "counts": {}, "days": {}}
        )
        key = f"{local:%Y-%m-%d}"
        year["count"] += 1
        year["counts"][key] = year["counts"].get(key, 0) + 1
        day = year["days"].setdefault(key, {"date": key, "label": _day_label(local), "entries": []})
        day["entries"].append(_entry(site, message, page_dir))
        services[message["service"]] = services.get(message["service"], 0) + 1
    ordered = []
    for number in sorted(years):
        year = years[number]
        ordered.append(
            {
                "year": number,
                "count": _plural(year["count"], "item"),
                "months": _calendar(number, year["counts"]),
                "days": list(year["days"].values()),
            }
        )
    first = renderer.messages[spec.message_ids[0]]["local"]
    last = renderer.messages[spec.message_ids[-1]]["local"]
    return site.env.get_template("page.html").render(
        title=spec.title,
        kind=spec.kind,
        total=_plural(len(spec.message_ids), "item"),
        first=f"{first:%Y-%m-%d}",
        last=f"{last:%Y-%m-%d}",
        sources=[
            {"name": name, "slug": _slug(name), "count": _plural(count, "item")}
            for name, count in sorted(services.items())
        ],
        related=relations.related(spec, up),
        mail=relations.mail(spec, up),
        years=ordered,
        back=f"{up}index.html",
        island=Markup(island_json(compact(renderer.document(spec)))),
    )


def _people_line(people: Iterable[dict[str, str]]) -> str:
    return ", ".join(
        f"{p['name']} <{p['address']}>" if p.get("name") else p["address"] for p in people
    )


def _mail_page(site: _Site, spec: PageSpec) -> str:
    renderer = site.renderer
    page_dir = site.out_root / spec.folder
    mails = []
    for message_id in spec.message_ids:
        message = renderer.messages[message_id]
        headers = json.loads(message["mail_headers"]) if message["mail_headers"] else {}
        specific = json.loads(message["service_specific"]) if message["service_specific"] else {}
        handle = renderer.handles.get(message["sender_handle_id"] or 0)
        address = handle["identifier"] if handle else ""
        name = site.label(message["sender_handle_id"]) if handle else "unknown sender"
        local: datetime = message["local"]
        mails.append(
            {
                "id": message["id"],
                "direction": "sent" if message["is_from_owner"] else "received",
                "author": name,
                "iso": message["timestamp_utc"],
                "date": f"{local:%Y-%m-%d}",
                "time": _time_text(message),
                "offset": _offset_text(message),
                "from_line": f"{name} <{address}>" if address and address != name else name,
                "to": _people_line(headers.get("to", [])),
                "cc": _people_line(headers.get("cc", [])),
                "subject": headers.get("subject") or "(no subject)",
                "labels": ", ".join(headers.get("labels", [])),
                "body": message["body"] or "",
                "has_html": bool(specific.get("body_html")),
                "attachments": [
                    {
                        "name": a["original_filename"] or "attachment",
                        "href": site.href(a["storage_path"], page_dir),
                    }
                    for a in renderer.attachments.get(message["id"], [])
                ],
            }
        )
    depth = spec.folder.count("/") + 1
    return site.env.get_template("mail.html").render(
        title=spec.title,
        mails=mails,
        back="../" * depth + "index.html",
        island=Markup(island_json(compact(renderer.document(spec)))),
    )


#: The categories the index sorts people into (a person in none is "unassigned"), and their names.
CATEGORIES = (
    ("family", "Family"),
    ("friends", "Friends"),
    ("business", "Business"),
    ("acquaintance", "Acquaintances"),
    ("unrecognized", "Unrecognized"),
)
_HUES = 8


def _initials(title: str) -> str:
    words = [w for w in re.split(r"[\s\-_.]+", title) if w and (w[0].isalnum())]
    if not words:
        return "#"
    if not any(c.isalpha() for c in title):  # a number: its last two digits
        digits = re.sub(r"\D", "", title)
        return digits[-2:] or "#"
    letters = [w[0] for w in words if w[0].isalpha()] or [words[0][0]]
    return (letters[0] + (letters[-1] if len(letters) > 1 else "")).upper()


def _hue(title: str) -> str:
    return f"h{sum(title.encode('utf-8')) % _HUES}"


def _size(bytes_: int) -> str:
    size = float(bytes_)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def _named(renderer: Renderer, spec: PageSpec) -> bool:
    """Whether a person's page is someone named (not a number, an address or an account that
    no longer has a name, which are 'unrecognized' until someone names them)."""
    handles: set[int] = set()
    for conversation_id in spec.conversation_ids:
        handles |= set(renderer.participants.get(conversation_id, []))
    handles |= {
        renderer.messages[m]["sender_handle_id"]
        for m in spec.message_ids
        if renderer.messages[m]["sender_handle_id"]
    }
    return any(renderer.handles[h]["person"] for h in handles if h not in renderer.owner_handles)


def _card(renderer: Renderer, page: PageSpec, href: str) -> dict[str, Any]:
    """What a tile shows about a person or group: counts, years, and each source's share."""
    by_source: dict[str, int] = {}
    for message_id in page.message_ids:
        service = renderer.messages[message_id]["service"]
        by_source[service] = by_source.get(service, 0) + 1
    first = renderer.messages[page.message_ids[0]]["local"]
    last = renderer.messages[page.message_ids[-1]]["local"]
    return {
        "title": page.title,
        "href": href,
        "count": _plural(len(page.message_ids), "item"),
        "span": str(first.year) if first.year == last.year else f"{first.year}–{last.year}",
        "first": f"{first:%Y-%m-%d}",
        "last": f"{last:%Y-%m-%d}",
        "sources": [
            {"name": name, "slug": _slug(name), "count": f"{count:,}"}
            for name, count in sorted(by_source.items(), key=lambda item: -item[1])
        ],
        "initials": _initials(page.title),
        "hue": _hue(page.title),
    }


def _stats(site: _Site) -> dict[str, Any]:
    """The dashboard: how much came from each source, and how much room its files take."""
    renderer = site.renderer
    sources: dict[str, dict[str, Any]] = {}
    seen_files: set[Path] = set()
    for message in renderer.messages.values():
        entry = sources.setdefault(
            message["service"],
            {
                "items": 0,
                "conversations": set(),
                "files": 0,
                "bytes": 0,
                "first": None,
                "last": None,
            },
        )
        entry["items"] += 1
        entry["conversations"].add(message["conversation_id"])
        local = message["local"]
        entry["first"] = (
            local if entry["first"] is None or local < entry["first"] else entry["first"]
        )
        entry["last"] = local if entry["last"] is None or local > entry["last"] else entry["last"]
        for attachment in renderer.attachments.get(message["id"], []):
            file = site._file(attachment["storage_path"])
            if file is None or file in seen_files:
                continue
            seen_files.add(file)
            entry["files"] += 1
            entry["bytes"] += file.stat().st_size
    rows = []
    for name, entry in sorted(sources.items(), key=lambda item: -item[1]["items"]):
        rows.append(
            {
                "name": name,
                "slug": _slug(name),
                "items": f"{entry['items']:,}",
                "conversations": f"{len(entry['conversations']):,}",
                "files": f"{entry['files']:,}",
                "size": _size(entry["bytes"]),
                "first": f"{entry['first']:%Y-%m-%d}",
                "last": f"{entry['last']:%Y-%m-%d}",
            }
        )
    firsts = [e["first"] for e in sources.values()]
    lasts = [e["last"] for e in sources.values()]
    first, last = (min(firsts), max(lasts)) if firsts else (None, None)
    pages = renderer.pages
    return {
        "items": f"{len(renderer.messages):,}",
        "conversations": f"{sum(len(e['conversations']) for e in sources.values()):,}",
        "people": f"{sum(1 for p in pages if p.kind == 'person' and _named(renderer, p)):,}",
        "groups": f"{sum(1 for p in pages if p.kind == 'group'):,}",
        "mail": f"{sum(1 for p in pages if p.kind == 'mail'):,}",
        "files": f"{sum(e['files'] for e in sources.values()):,}",
        "size": _size(sum(e["bytes"] for e in sources.values())),
        "first": f"{first:%Y-%m-%d}" if first else "",
        "last": f"{last:%Y-%m-%d}" if last else "",
        "years": f"{last.year - first.year + 1:,} years" if first and last else "",
        "span": (
            f"{first.year}–{last.year}"
            if first and last and first.year != last.year
            else (str(first.year) if first else "empty")
        ),
        "sources": rows,
    }


def _index(
    site: _Site, categories: Optional[Mapping[str, str]] = None, title: str = "Chattr archive"
) -> str:
    """The index: people as tiles sorted into categories, groups, mail, and the box's numbers."""
    renderer = site.renderer
    chosen = {name.casefold(): category for name, category in (categories or {}).items()}
    keys = {key for key, _ in CATEGORIES}
    people: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    mail: list[dict[str, Any]] = []
    for page in renderer.pages:
        href = quote(page.path, safe="/()")
        if page.kind == "mail":
            title_text = page.filename.removesuffix(".html")
            count = _plural(len(page.message_ids), "message")
            mail.append({"title": title_text, "href": href, "count": count})
            continue
        card = _card(renderer, page, href)
        if page.kind == "group":
            groups.append(card)
            continue
        card["named"] = _named(renderer, page)
        category = chosen.get(page.title.casefold(), "")
        card["category"] = (
            (category if category in keys else "") if card["named"] else "unrecognized"
        )
        people.append(card)
    people.sort(key=lambda p: str(p["title"]).casefold())
    groups.sort(key=lambda g: str(g["title"]).casefold())
    counts = {key: sum(1 for p in people if p["category"] == key) for key, _ in CATEGORIES}
    counts["all"] = sum(1 for p in people if p["category"] != "unrecognized")
    counts["unassigned"] = sum(1 for p in people if p["category"] == "")
    return site.env.get_template("index.html").render(
        title=title,
        categories=[{"key": key, "label": label} for key, label in CATEGORIES],
        counts={key: f"{value:,}" for key, value in counts.items()},
        people=people,
        groups=groups,
        mail=mail,
        stats=_stats(site),
    )


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


class _Relations:
    """Who is in what, worked out once for every page (an archive can hold thousands of people):
    the groups each person is in, the people in each group, the mail each person is in."""

    def __init__(self, renderer: Renderer) -> None:
        self.renderer = renderer
        self.homes: dict[str, str] = {}  # person → their page
        self.groups_of: dict[str, dict[str, str]] = {}  # person → {group page: group title}
        self.members_of: dict[str, set[str]] = {}  # group page → its people
        self.mail_of: dict[str, list[PageSpec]] = {}  # person → mail threads they are in
        for spec in renderer.pages:
            if spec.kind == "person":
                self.homes[spec.title] = spec.path
            elif spec.kind == "group":
                names = self._members(spec)
                self.members_of.setdefault(spec.path, set()).update(names)
                for name in names:
                    self.groups_of.setdefault(name, {})[spec.path] = spec.title
            elif spec.kind == "mail":
                for name in self._members(spec):
                    self.mail_of.setdefault(name, []).append(spec)

    def _members(self, spec: PageSpec) -> set[str]:
        renderer = self.renderer
        handles: set[int] = set()
        for conversation_id in spec.conversation_ids:
            handles |= set(renderer.participants.get(conversation_id, []))
        handles |= {
            renderer.messages[m]["sender_handle_id"]
            for m in spec.message_ids
            if renderer.messages[m]["sender_handle_id"]
        }
        return {renderer.label(h) for h in handles if h not in renderer.owner_handles}

    def related(self, spec: PageSpec, up: str) -> list[dict[str, Optional[str]]]:
        """A person's groups, or a group's people, each with a link to their page."""
        if spec.kind == "person":
            return [
                {"title": title, "href": quote(f"{up}{path}", safe="/().")}
                for path, title in sorted(self.groups_of.get(spec.title, {}).items())
            ]
        found: list[dict[str, Optional[str]]] = []
        for name in sorted(self.members_of.get(spec.path, set()), key=str.casefold):
            home = self.homes.get(name)
            found.append(
                {"title": name, "href": quote(f"{up}{home}", safe="/().") if home else None}
            )
        return found

    def mail(self, spec: PageSpec, up: str) -> list[dict[str, str]]:
        if spec.kind != "person":
            return []
        return [
            {"title": m.filename.removesuffix(".html"), "href": quote(f"{up}{m.path}", safe="/().")}
            for m in self.mail_of.get(spec.title, [])
        ]


PAGES_LIST = ".chattr-pages"


def _forget_old_pages(out_root: Path, record: Path, written: list[Path]) -> None:
    """Delete pages written last time that were not written now (a person was renamed or merged,
    a conversation went away), then any folder that leaves empty. Only `.html` files this
    function's own record names are ever deleted."""
    root = out_root.resolve()
    now = {path.resolve().relative_to(root).as_posix() for path in written}
    try:
        before = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = []
    for relative in before if isinstance(before, list) else []:
        if not isinstance(relative, str) or relative in now or not relative.endswith(".html"):
            continue
        page = (root / relative).resolve()
        if not page.is_relative_to(root) or not page.is_file():
            continue
        page.unlink()
        folder = page.parent
        while folder != root and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    record.parent.mkdir(parents=True, exist_ok=True)
    temporary = record.with_name(record.name + ".part")
    temporary.write_text(json.dumps(sorted(now), ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(temporary, record)


def render_pages(
    renderer: Renderer,
    out_root: PathLike,
    attachment_roots: Sequence[PathLike] = (),
    pages_list: Optional[PathLike] = None,
    categories: Optional[Mapping[str, str]] = None,
    title: str = "Chattr archive",
) -> list[Path]:
    """Write every page and the index under `out_root`; returns the files written.

    `categories` ({person's name: category}) sorts people on the index; `title` names it.

    Pages written by an earlier call and no longer wanted are deleted. The list of what was
    written is kept in `pages_list` (default: `.chattr-pages` in `out_root`).
    """
    site = _Site(renderer, Path(out_root), attachment_roots)
    relations = _Relations(renderer)
    written = []
    for spec in renderer.pages:
        path = site.out_root / spec.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if spec.kind == "mail":
            text = _mail_page(site, spec)
        else:
            text = _message_page(site, spec, relations)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    index = site.out_root / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(_index(site, categories, title), encoding="utf-8")
    written.append(index)
    _forget_old_pages(
        site.out_root, Path(pages_list) if pages_list else site.out_root / PAGES_LIST, written
    )
    return written
