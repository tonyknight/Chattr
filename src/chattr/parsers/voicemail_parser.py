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
"""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
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
        if not database.is_file():
            raise FileNotFoundError(database)
        rows = self._rows(database)

        audio = {row["ROWID"]: self._audio_for(database.parent, row["ROWID"]) for row in rows}
        outcomes = (
            transcribe_all(self.transcriber, [a for a in audio.values() if a])
            if self.transcriber
            else {}
        )

        grouped: dict[str, list[MessageRecord]] = {}
        for row in rows:
            moment = datetime.fromtimestamp(int(row["date"] or 0), tz=timezone.utc)
            number = (row["callback_num"] or row["sender"] or "").strip() or None
            extras: dict[str, Any] = {}
            if row["duration"] is not None:
                extras["duration_seconds"] = int(row["duration"])
            if row["trashed_date"]:
                extras["trashed"] = True
            file = audio[row["ROWID"]]
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
            grouped.setdefault(number or "unknown", []).append(
                MessageRecord(
                    timestamp=moment,
                    sender=number,
                    sender_name=self.aliases.get(number or ""),
                    content_type="voicemail",
                    transcript=transcript,
                    service_specific=extras,
                    attachments=(self._attachment(file),) if file else (),
                )
            )
        batches = [
            ConversationBatch(
                ConversationRecord(
                    service=SERVICE,
                    source_id=number,
                    name=self.aliases.get(number) or number,
                    participants=(number,) if number != "unknown" else (),
                ),
                sorted(records, key=lambda r: r.timestamp),
            )
            for number, records in grouped.items()
        ]
        return sorted(batches, key=lambda b: b.records[0].timestamp)

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
