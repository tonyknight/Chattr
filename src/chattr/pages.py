"""Render the planned pages as self-contained HTML.

Every person and group also gets an `index.html`: all that came from them, by year and by source.

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union
from urllib.parse import quote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from chattr.filing import playable_copy
from chattr.render import GROUPS, PEOPLE, PageSpec, Renderer

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
        home="index.html",
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
                page.folder,
                {
                    "title": page.title,
                    "home": quote(f"{page.folder}/index.html", safe="/()"),
                    "links": [],
                },
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


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def _folder_indexes(site: _Site) -> dict[str, str]:
    """An `index.html` for every person and group: all that came from them, by year and source.

    A person's page also lists the groups they are in and the mail they sent; a group's lists its
    members. Returned as {path relative to the output folder: the page}.
    """
    renderer = site.renderer
    folders: dict[str, list[PageSpec]] = {}
    for spec in renderer.pages:
        if spec.kind in ("person", "group"):
            folders.setdefault(spec.folder, []).append(spec)
    homes = {
        specs[0].title: folder for folder, specs in folders.items() if specs[0].kind == "person"
    }

    def members(spec: PageSpec) -> set[str]:
        handles: set[int] = set()
        for conversation_id in spec.conversation_ids:
            handles |= set(renderer.participants.get(conversation_id, []))
        handles |= {
            renderer.messages[m]["sender_handle_id"]
            for m in spec.message_ids
            if renderer.messages[m]["sender_handle_id"]
        }
        return {renderer.label(h) for h in handles if h not in renderer.owner_handles}

    # Who is in what, worked out once (not once per person: an archive can hold thousands).
    groups_of: dict[str, dict[str, str]] = {}  # person → {group folder: group title}
    members_of: dict[str, set[str]] = {}  # group folder → its people
    mail_of: dict[str, list[PageSpec]] = {}  # person → mail threads they are in
    for spec in renderer.pages:
        if spec.kind == "group":
            names = members(spec)
            members_of.setdefault(spec.folder, set()).update(names)
            for name in names:
                groups_of.setdefault(name, {})[spec.folder] = spec.title
        elif spec.kind == "mail":
            for name in members(spec):
                mail_of.setdefault(name, []).append(spec)

    pages = {}
    for folder, specs in folders.items():
        first = specs[0]
        up = "../" * (folder.count("/") + 1)
        services: dict[str, int] = {}
        years: list[dict[str, Any]] = []
        moments = []
        for spec in sorted(specs, key=lambda s: s.year):
            by_service: dict[str, int] = {}
            for message_id in spec.message_ids:
                message = renderer.messages[message_id]
                by_service[message["service"]] = by_service.get(message["service"], 0) + 1
                moments.append(message["local"])
            for service, count in by_service.items():
                services[service] = services.get(service, 0) + count
            years.append(
                {
                    "year": spec.year,
                    "href": quote(spec.filename, safe="()"),
                    "count": len(spec.message_ids),
                    "sources": [
                        {"name": name, "slug": _slug(name), "count": count}
                        for name, count in sorted(by_service.items())
                    ],
                }
            )
        related: list[dict[str, Optional[str]]] = []
        mail = []
        if first.kind == "person":
            for other_folder, title in sorted(groups_of.get(first.title, {}).items()):
                href = quote(f"{up}{other_folder}/index.html", safe="/().")
                related.append({"title": title, "href": href})
            for spec in mail_of.get(first.title, []):
                mail.append(
                    {
                        "title": spec.filename.removesuffix(".html"),
                        "href": quote(f"{up}{spec.path}", safe="/()."),
                    }
                )
        else:
            for name in sorted(members_of.get(folder, set()), key=str.casefold):
                home = homes.get(name)
                member = quote(f"{up}{home}/index.html", safe="/().") if home else None
                related.append({"title": name, "href": member})
        total = sum(int(year["count"]) for year in years)
        pages[f"{folder}/index.html"] = site.env.get_template("person.html").render(
            title=first.title,
            kind=first.kind,
            total=_plural(total, "item"),
            first=f"{min(moments):%Y-%m-%d}",
            last=f"{max(moments):%Y-%m-%d}",
            sources=[
                {"name": name, "slug": _slug(name), "count": _plural(count, "item")}
                for name, count in sorted(services.items())
            ],
            years=years,
            related=related,
            mail=mail,
            back=f"{up}index.html",
        )
    return pages


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
) -> list[Path]:
    """Write every page and the index under `out_root`; returns the files written.

    Pages written by an earlier call and no longer wanted are deleted. The list of what was
    written is kept in `pages_list` (default: `.chattr-pages` in `out_root`).
    """
    site = _Site(renderer, Path(out_root), attachment_roots)
    written = []
    for spec in renderer.pages:
        path = site.out_root / spec.path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _mail_page(site, spec) if spec.kind == "mail" else _message_page(site, spec)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    for relative, text in _folder_indexes(site).items():
        path = site.out_root / relative
        path.write_text(text, encoding="utf-8")
        written.append(path)
    index = site.out_root / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(_index(site), encoding="utf-8")
    written.append(index)
    _forget_old_pages(
        site.out_root, Path(pages_list) if pages_list else site.out_root / PAGES_LIST, written
    )
    return written
