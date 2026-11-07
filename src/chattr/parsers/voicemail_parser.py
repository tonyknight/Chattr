"""Parser for iPhone voicemail taken from a device backup.

The input is the folder holding `voicemail.db` (table `voicemail`: `ROWID`, `date` in Unix seconds,
`sender` and `callback_num`, `duration` in seconds, `trashed_date`) and one audio file per row,
named `<ROWID>.<extension>` (`.amr` on most phones). The extension is not trusted: the audio is
found by name and kept as it is.

Each row is a `voicemail` message from the caller, with the audio stored by content hash as its
attachment (when an `attachments_dir` is given) and, when a transcriber is supplied, a transcript
made locally. A transcript that fails is reported on the message (`transcription_error`) and never
stops the import. One conversation per caller. Times are exact instants (the phone's own time zone
isn't recorded), stored as UTC.

A folder with no `voicemail.db` is read as a folder of audio files named
`(2018-12-21) 15 02 31 - Ana Costa.amr`: the date and time the voicemail came in, then the caller (a
name or a number; the part after ` - ` may be missing). Those times are wall-clock times with no
zone, so they are kept as written and marked as assumed. Other files in the folder, and folders
inside it, are searched but only files named that way are imported.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from chattr import blobs
from chattr.chatdb import connect_readonly
from chattr.parsers.base_parser import BaseParser
from chattr.parsers.batch import ConversationBatch
from chattr.records import AttachmentRecord, ConversationRecord, MessageRecord
from chattr.transcription import Transcriber, transcribe_all

SERVICE = "Voicemail"
DATABASE = "voicemail.db"
PathLike = Union[str, Path]

AUDIO_SUFFIXES = frozenset(
    {".amr", ".m4a", ".mp3", ".wav", ".aac", ".caf", ".qta", ".3gp", ".ogg", ".opus", ".flac"}
)
#: `(2018-12-21) 15 02 31 - Ana Costa`: the date, the time (seconds optional, any of ` .:-_` between
#: its parts), then ` - ` and the caller.
_AUDIO_NAME = re.compile(
    r"^\((\d{4})-(\d{2})-(\d{2})\)\s+(\d{2})[ .:\-_](\d{2})(?:[ .:\-_](\d{2}))?(?:\s*-\s*(.*?))?\s*$"
)


def parse_audio_name(name: str) -> Optional[tuple[datetime, Optional[str]]]:
    """The time and caller a voicemail file's name gives; None when it isn't named that way."""
    found = _AUDIO_NAME.match(
        Path(name).stem if Path(name).suffix.lower() in AUDIO_SUFFIXES else name
    )
    if not found:
        return None
    year, month, day, hour, minute, second, caller = found.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError:
        return None
    return moment, (caller or "").strip() or None


def audio_files(folder: Path) -> list[tuple[Path, datetime, Optional[str]]]:
    """Every voicemail audio file in `folder` (and the folders in it), oldest first."""
    found = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file():
            continue
        named = parse_audio_name(path.name)
        if named:
            found.append((path, named[0], named[1]))
    return sorted(found, key=lambda entry: (entry[1], entry[0].name))


@dataclass(frozen=True)
class _Entry:
    """One voicemail, from a database row or from a file's name."""

    moment: datetime
    caller: Optional[str]
    audio: Optional[Path]
    extras: dict[str, Any]
    assumed: bool = False  # the time is wall-clock with no zone


class VoicemailParser(BaseParser):
    """Parse an iPhone `voicemail.db` and its audio into voicemail messages."""

    def __init__(
        self,
        owner: Optional[str] = None,
        aliases: Optional[dict[str, str]] = None,
        attachments_dir: Optional[PathLike] = None,
        transcriber: Optional[Transcriber] = None,
    ) -> None:
        self.owner = owner
        self.aliases = aliases or {}
        self.attachments_dir = Path(attachments_dir) if attachments_dir else None
        self.transcriber = transcriber

    def parse(self, file_path: PathLike) -> list[ConversationBatch]:
        path = Path(file_path)
        database = path / DATABASE if path.is_dir() else path
        if database.is_file():
            entries = self._database_entries(database)
        else:
            files = audio_files(path) if path.is_dir() else []
            if not files:
                raise FileNotFoundError(database)
            entries = [
                _Entry(moment, caller, audio, {}, assumed=True) for audio, moment, caller in files
            ]
        outcomes = (
            transcribe_all(self.transcriber, [e.audio for e in entries if e.audio])
            if self.transcriber
            else {}
        )

        grouped: dict[str, list[MessageRecord]] = {}
        for entry in entries:
            extras = dict(entry.extras)
            file = entry.audio
            transcript: Optional[str] = None
            if file and file in outcomes:
                outcome = outcomes[file]
                if outcome.transcript and outcome.transcript.text.strip():
                    transcript = outcome.transcript.text.strip()
                    extras["transcript_model"] = outcome.transcript.model
                    if outcome.transcript.language:
                        extras["transcript_language"] = outcome.transcript.language
                elif outcome.error:
                    extras["transcription_error"] = outcome.error
            grouped.setdefault(entry.caller or "unknown", []).append(
                MessageRecord(
                    timestamp=entry.moment,
                    sender=entry.caller,
                    sender_name=self.aliases.get(entry.caller or ""),
                    content_type="voicemail",
                    transcript=transcript,
                    service_specific=extras,
                    attachments=(self._attachment(file),) if file else (),
                    tz_assumed=entry.assumed,
                )
            )
        batches = [
            ConversationBatch(
                ConversationRecord(
                    service=SERVICE,
                    source_id=caller,
                    name=self.aliases.get(caller) or caller,
                    participants=(caller,) if caller != "unknown" else (),
                ),
                sorted(records, key=lambda r: r.timestamp),
            )
            for caller, records in grouped.items()
        ]
        return sorted(batches, key=lambda b: b.records[0].timestamp)

    def _database_entries(self, database: Path) -> list["_Entry"]:
        entries = []
        for row in self._rows(database):
            moment = datetime.fromtimestamp(int(row["date"] or 0), tz=timezone.utc)
            extras: dict[str, Any] = {}
            if row["duration"] is not None:
                extras["duration_seconds"] = int(row["duration"])
            if row["trashed_date"]:
                extras["trashed"] = True
            number = (row["callback_num"] or row["sender"] or "").strip() or None
            entries.append(
                _Entry(moment, number, self._audio_for(database.parent, row["ROWID"]), extras)
            )
        return entries

    @staticmethod
    def _rows(database: Path) -> list[sqlite3.Row]:
        conn = connect_readonly(database)
        try:
            return conn.execute("SELECT * FROM voicemail ORDER BY date, ROWID").fetchall()
        finally:
            conn.close()

    @staticmethod
    def _audio_for(folder: Path, rowid: int) -> Optional[Path]:
        matches = sorted(p for p in folder.glob(f"{rowid}.*") if p.suffix.lower() != ".db")
        return matches[0] if matches else None

    def _attachment(self, path: Path) -> AttachmentRecord:
        data = path.read_bytes()
        if self.attachments_dir:
            digest, relative = blobs.store_by_hash(self.attachments_dir, data, path.suffix)
        else:
            digest, relative = hashlib.sha256(data).hexdigest(), None
        return AttachmentRecord(
            digest, path.name, mimetypes.guess_type(path.name)[0], len(data), relative
        )
