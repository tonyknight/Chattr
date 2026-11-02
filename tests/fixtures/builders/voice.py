"""Synthetic Google Voice Takeout and iPhone voicemail exports.

**Google Voice** (`Takeout/Voice/Calls/`): one HTML page per conversation or call, named
`<Name or number> - <Text|Received|Placed|Missed|Voicemail> - <UTC timestamp>.html`, using
microformat-style classes:
- text conversations: `hChatLog hfeed`, `message`, `dt` (with an ISO `title`), `sender vcard`,
  `tel`, `fn`, and the text in `<q>`;
- calls and voicemails: `haudio`, `fn`, `published`, `duration` (ISO 8601 duration in `title`),
  `contributor vcard`, `tel`;
- voicemails add a transcript in `description` / `full-text` and an `<audio>` element pointing
  to an audio file beside the page.

**iPhone voicemail** (from a device backup): `voicemail.db` with table `voicemail` (`date` in Unix
seconds, `sender`, `duration` in seconds, …) and one audio file per row, named `<ROWID>.amr`.

Audio payloads are short PCM WAV data whatever the extension, because the standard library can't
encode MP3 or AMR. Parsers must not rely on file extensions, and speech-to-text tests use a stub.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures import family
from fixtures.builders.media import wav


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H_%M_%SZ")


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _vcard(phone: str, name: str, cls: str = "sender vcard") -> str:
    return (
        f'<cite class="{cls}"><a class="tel" href="tel:{phone}">'
        f'<span class="fn">{html.escape(name)}</span></a></cite>'
    )


def _page(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" ?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{html.escape(title)}</title>'
        f"</head><body>{body}</body></html>\n"
    )


def _text_page(who: family.Person, entries: list[tuple[datetime, bool, str]]) -> str:
    me = family.OWNER
    lines = []
    for moment, from_me, text in entries:
        sender = _vcard(me.phones[0], "Me") if from_me else _vcard(who.phones[0], who.name)
        lines.append(
            f'<div class="message"><abbr class="dt" title="{_iso(moment)}">'
            f"{moment:%b %d, %Y, %I:%M:%S %p}</abbr>:\n{sender}:\n<q>{html.escape(text)}</q></div>"
        )
    body = f'<div class="hChatLog hfeed">{"".join(lines)}</div>'
    return _page(who.name, body)


def _call_page(
    kind: str, label: str, phone: str, name: str, moment: datetime, seconds: int, extra: str = ""
) -> str:
    minutes, secs = divmod(seconds, 60)
    duration = f"PT{minutes}M{secs}S" if minutes else f"PT{secs}S"
    body = (
        f'<div class="haudio"><span class="fn">{html.escape(label)}</span>\n'
        f'<abbr class="published" title="{_iso(moment)}">{moment:%b %d, %Y, %I:%M:%S %p}</abbr>\n'
        f"{_vcard(phone, name, 'contributor vcard')}\n"
        f'<abbr class="duration" title="{duration}">({minutes:02d}:{secs:02d})</abbr>{extra}</div>'
    )
    return _page(f"{kind} {name}", body)


def build_google_voice(out_dir: Path) -> Path:
    """Write a Google Voice Takeout `Calls/` folder into `out_dir`; return the folder."""
    calls = out_dir / "Takeout" / "Voice" / "Calls"
    calls.mkdir(parents=True, exist_ok=True)
    ana = family.ANA

    texts = [
        (family.at(2018, 9, 3, 17, 40, 12), False, "Mãe, can you pick me up at 6?"),
        (family.at(2018, 9, 3, 17, 41, 3), True, "Yes. The usual corner."),
    ]
    stamp = texts[0][0]
    (calls / f"{ana.name} - Text - {_utc_stamp(stamp)}.html").write_text(
        _text_page(ana, texts), encoding="utf-8"
    )

    received = family.at(2018, 9, 5, 20, 2, 0)
    (calls / f"{ana.name} - Received - {_utc_stamp(received)}.html").write_text(
        _call_page(
            "Received", f"Received call from {ana.name}", ana.phones[0], ana.name, received, 312
        ),
        encoding="utf-8",
    )

    placed = family.at(2018, 9, 6, 8, 15, 30)
    (calls / f"{ana.name} - Placed - {_utc_stamp(placed)}.html").write_text(
        _call_page("Placed", f"Placed call to {ana.name}", ana.phones[0], ana.name, placed, 95),
        encoding="utf-8",
    )

    missed = family.at(2018, 9, 7, 13, 0, 5)
    unknown = family.UNKNOWN_HANDLE
    (calls / f"{unknown} - Missed - {_utc_stamp(missed)}.html").write_text(
        _call_page("Missed", f"Missed call from {unknown}", unknown, unknown, missed, 0),
        encoding="utf-8",
    )

    voicemail = family.at(2018, 9, 8, 10, 22, 45)
    base = f"{ana.name} - Voicemail - {_utc_stamp(voicemail)}"
    (calls / f"{base}.mp3").write_bytes(wav(seconds=0.6, hz=330.0))
    transcript = "Hi Mãe, it's Ana. I'm running late, see you at dinner. Love you."
    extra = (
        f'\n<span class="description"><span class="full-text">{html.escape(transcript)}</span>'
        f'</span>\n<audio controls="controls" src="{html.escape(base)}.mp3">'
        f'<a rel="enclosure" href="{html.escape(base)}.mp3">Audio</a></audio>'
    )
    (calls / f"{base}.html").write_text(
        _call_page(
            "Voicemail", f"Voicemail from {ana.name}", ana.phones[0], ana.name, voicemail, 18, extra
        ),
        encoding="utf-8",
    )
    return calls


def build_iphone_voicemail(out_dir: Path) -> Path:
    """Write `voicemail.db` and its audio files into `out_dir/Voicemail/`; return the folder."""
    folder = out_dir / "Voicemail"
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "voicemail.db"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
            CREATE TABLE voicemail (
                ROWID INTEGER PRIMARY KEY AUTOINCREMENT, remote_uid INTEGER, date INTEGER,
                token TEXT, sender TEXT, callback_num TEXT, duration INTEGER,
                expiration INTEGER, trashed_date INTEGER, flags INTEGER
            );
            """)
        entries = [
            (
                family.JOAO.phones[0],
                family.at(2021, 12, 24, 19, 30, tz=family.TZ_LISBON),
                22,
                440.0,
            ),
            (family.ANA.phones[0], family.at(2022, 6, 14, 9, 5), 14, 520.0),
        ]
        for uid, (sender, moment, seconds, hz) in enumerate(entries, start=1):
            unix = int(moment.timestamp())
            cur = conn.execute(
                "INSERT INTO voicemail (remote_uid, date, token, sender, callback_num, duration, "
                "expiration, trashed_date, flags) VALUES (?, ?, 'Complete', ?, ?, ?, ?, 0, 2)",
                (
                    uid,
                    unix,
                    sender,
                    sender,
                    seconds,
                    unix + int(timedelta(days=30).total_seconds()),
                ),
            )
            (folder / f"{cur.lastrowid}.amr").write_bytes(wav(seconds=0.4, hz=hz))
        conn.commit()
    finally:
        conn.close()
    return folder
