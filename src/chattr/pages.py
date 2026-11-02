"""Render the planned pages as self-contained HTML.

Each page is one HTML file: inline CSS (light and dark), a heatmap calendar of the year, a block per
date, an entry per message with a platform badge, a stable `id="m-<id>"` anchor, the sender, and
the local time with its offset (or "UTC" / "zone unknown") at the stored precision. Text is always
escaped; links are only made from http(s) addresses; attachment links are only made to files that
exist inside the given attachment folders. A JSON data island (`#chattr-data`) carries every stored
column of the page's messages so the database can be rebuilt from the pages.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union
from urllib.parse import quote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from chattr.render import PageSpec, Renderer

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
        self.roots = [Path(r).resolve() for r in attachment_roots]
        self.env = _environment()

    def href(self, storage_path: Optional[str], page_dir: Path) -> Optional[str]:
        """A relative link to an archived file, only if it exists inside one of the roots."""
        if not storage_path:
            return None
        for root in self.roots:
            candidate = (root / storage_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                relative = os.path.relpath(candidate, page_dir.resolve()).replace(os.sep, "/")
                return quote(relative, safe="/()")
        return None

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
                "kind": _attachment_kind(a["mime_type"], a["original_filename"] or ""),
            }
            for a in site.renderer.attachments.get(message["id"], [])
        ],
        "reactions": _standing_reactions(site, message["id"]),
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


def _message_page(site: _Site, spec: PageSpec) -> str:
    renderer = site.renderer
    page_dir = site.out_root / spec.folder
    messages = [renderer.messages[m] for m in spec.message_ids]
    counts: dict[str, int] = {}
    days: dict[str, dict[str, Any]] = {}
    for message in messages:
        local: datetime = message["local"]
        key = f"{local:%Y-%m-%d}"
        counts[key] = counts.get(key, 0) + 1
        day = days.setdefault(key, {"date": key, "label": _day_label(local), "entries": []})
        day["entries"].append(_entry(site, message, page_dir))
    years = [
        {
            "year": p.year,
            "href": quote(p.filename, safe="()"),
            "current": p.year == spec.year,
        }
        for p in renderer.pages
        if p.folder == spec.folder and p.kind == spec.kind
    ]
    return site.env.get_template("page.html").render(
        title=spec.title,
        year=spec.year,
        years=years,
        months=_calendar(spec.year, counts),
        days=list(days.values()),
        island=Markup(island_json(renderer.document(spec))),
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
        island=Markup(island_json(renderer.document(spec))),
    )


def _index(site: _Site) -> str:
    """The index: every person, group and mail thread, each linking to its pages."""
    renderer = site.renderer
    sections: dict[str, dict[str, Any]] = {
        "person": {"name": "People", "by_folder": {}},
        "group": {"name": "Groups", "by_folder": {}},
        "mail": {"name": "Mail", "entries": []},
    }
    for page in renderer.pages:
        count = len(page.message_ids)
        link = {"href": quote(page.path, safe="/()"), "year": page.year, "count": count}
        if page.kind == "mail":
            sections["mail"]["entries"].append(
                {"title": page.filename.removesuffix(".html"), "href": link["href"], "count": count}
            )
        else:
            entry = sections[page.kind]["by_folder"].setdefault(
                page.folder, {"title": page.title, "links": []}
            )
            entry["links"].append(link)
    ordered = []
    for kind in ("person", "group"):
        items = sorted(sections[kind]["by_folder"].values(), key=lambda i: i["title"].casefold())
        if items:
            ordered.append({"name": sections[kind]["name"], "entries": items})
    if sections["mail"]["entries"]:
        ordered.append({"name": "Mail", "entries": sections["mail"]["entries"]})
    return site.env.get_template("index.html").render(sections=ordered)


def render_pages(
    renderer: Renderer,
    out_root: PathLike,
    attachment_roots: Sequence[PathLike] = (),
) -> list[Path]:
    """Write every page and the index under `out_root`; returns the files written."""
    site = _Site(renderer, Path(out_root), attachment_roots)
    written = []
    for spec in renderer.pages:
        path = site.out_root / spec.path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _mail_page(site, spec) if spec.kind == "mail" else _message_page(site, spec)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    index = site.out_root / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(_index(site), encoding="utf-8")
    written.append(index)
    return written
