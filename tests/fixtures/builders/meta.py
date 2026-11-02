"""Synthetic Meta "Download Your Information" exports: Messenger and Instagram direct messages.

Layout (JSON format download):
- Messenger: `your_facebook_activity/messages/inbox/<thread>_<id>/message_1.json`, with photos
  under `.../<thread>_<id>/photos/`, and `uri` values relative to the export root.
- Instagram: `your_instagram_activity/messages/inbox/<thread>_<id>/message_1.json`.

Like the real exports:
- messages are newest first;
- times are `timestamp_ms` (milliseconds since the Unix epoch);
- every text value is double-encoded (UTF-8 bytes decoded as latin-1, then written as JSON
  `\\u00XX` escapes).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fixtures import family
from fixtures.builders.media import png


def mojibake(text: str) -> str:
    """Meta's double encoding: UTF-8 bytes reinterpreted as latin-1 code points."""
    return text.encode("utf-8").decode("latin-1")


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _write(path: Path, thread: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(thread, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def _messenger(root: Path) -> Path:
    thread_dir = "your_facebook_activity/messages/inbox/joaocosta_1234567890"
    me, joao = family.OWNER.usernames["messenger"], family.JOAO.usernames["messenger"]
    lisbon = family.TZ_LISBON
    photo_rel = f"{thread_dir}/photos/1234567890_10150001_n.png"
    photo_path = root / photo_rel
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(png(rgb=(10, 90, 200)))
    sent = family.at(2019, 7, 21, 16, 10, tz=lisbon)
    messages = [
        {
            "sender_name": joao,
            "timestamp_ms": _ms(family.at(2019, 7, 20, 18, 0, tz=lisbon)),
            "content": "Olá mana! Já chegaste a Lisboa?",
        },
        {
            "sender_name": me,
            "timestamp_ms": _ms(family.at(2019, 7, 20, 18, 12, tz=lisbon)),
            "content": "Cheguei! Vamos jantar amanhã?",
            "reactions": [{"reaction": mojibake("❤️"), "actor": joao}],
        },
        {
            "sender_name": joao,
            "timestamp_ms": _ms(sent),
            "photos": [{"uri": photo_rel, "creation_timestamp": int(sent.timestamp())}],
        },
        {
            "sender_name": joao,
            "timestamp_ms": _ms(family.at(2019, 7, 21, 16, 12, tz=lisbon)),
            "content": "The restaurant: https://example.org/tasca-do-ze",
            "share": {
                "link": "https://example.org/tasca-do-ze",
                "share_text": "Tasca do Zé — cozinha tradicional",
            },
        },
        {
            "sender_name": me,
            "timestamp_ms": _ms(family.at(2019, 7, 21, 16, 20, tz=lisbon)),
            "content": "Perfeito, até às oito. Beijinhos e saudações à família!",
        },
    ]
    thread = {
        "participants": [{"name": mojibake(joao)}, {"name": mojibake(me)}],
        "messages": [_encode(m) for m in reversed(messages)],
        "title": mojibake(joao),
        "is_still_participant": True,
        "thread_path": "inbox/joaocosta_1234567890",
    }
    return _write(root / thread_dir / "message_1.json", thread)


def _instagram(root: Path) -> Path:
    thread_dir = "your_instagram_activity/messages/inbox/ruipereiraexample_9876543210"
    me, rui = family.OWNER.usernames["instagram"], family.RUI.usernames["instagram"]
    lisbon = family.TZ_LISBON
    messages = [
        {
            "sender_name": rui,
            "timestamp_ms": _ms(family.at(2022, 5, 3, 21, 30, tz=lisbon)),
            "content": "Vi a tua foto do Douro — que vista!",
        },
        {
            "sender_name": me,
            "timestamp_ms": _ms(family.at(2022, 5, 3, 21, 45, tz=lisbon)),
            "content": "Obrigada! Temos de lá voltar todos juntos.",
        },
    ]
    thread = {
        "participants": [{"name": mojibake(rui)}, {"name": mojibake(me)}],
        "messages": [_encode(m) for m in reversed(messages)],
        "title": mojibake(rui),
        "is_still_participant": True,
        "thread_path": "inbox/ruipereiraexample_9876543210",
    }
    return _write(root / thread_dir / "message_1.json", thread)


def _encode(message: dict[str, Any]) -> dict[str, Any]:
    """Apply Meta's double encoding to every human-readable string field."""
    out = dict(message)
    for key in ("sender_name", "content"):
        if key in out:
            out[key] = mojibake(out[key])
    if "share" in out:
        out["share"] = {**out["share"], "share_text": mojibake(out["share"]["share_text"])}
    if "reactions" in out:
        out["reactions"] = [{**r, "actor": mojibake(r["actor"])} for r in out["reactions"]]
    return out


def build_meta(out_dir: Path) -> dict[str, Path]:
    """Write a Messenger and an Instagram thread into `out_dir`; return their JSON paths."""
    root = out_dir / "meta-export"
    root.mkdir(parents=True, exist_ok=True)
    return {"messenger": _messenger(root), "instagram": _instagram(root)}
