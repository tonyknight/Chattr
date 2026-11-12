"""File attachments where a person would look for them: under the person, by source and date.

    People/<Person>/<Service>/(YYYY-MM-DD) HH-MM-SS <sender>.<ext>
    Groups/<Group>/<Service>/…
    Mail/<YYYY>/Attachments/(YYYY-MM-DD) HH-MM-SS <sender> — <name>.<ext>

Importers leave each file wherever suits them (by content hash, in a working folder) and record that
place in `attachments.storage_path`. Filing moves the file beside the pages that show it, names it
for its date and sender, and records the new place (relative to the output folder). It is part of
drawing the pages, so it follows a person who is renamed or merged, and it is safe to repeat.

Audio a browser cannot play (`.amr` voicemail, `.caf`) gets a copy it can, an `.m4a` (AAC) beside
the original with the same name. The original is the record and is never changed; the copy is made
again if it is missing, like a page.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from chattr.attachment_manager import safe_name
from chattr.database_manager import DatabaseManager
from chattr.render import MAIL_SERVICE, PageSpec, Renderer

PathLike = Union[str, Path]

#: Audio most browsers cannot play, and the bit rate of the copy made for each.
UNPLAYABLE = {
    ".amr": "16k",
    ".awb": "24k",
    ".caf": "32k",
    ".qta": "32k",
    ".3gp": "32k",
    ".3g2": "32k",
}
PLAYABLE = ".m4a"
MAIL_ATTACHMENTS = "Attachments"


@dataclass
class Filed:
    """What filing did. Paths are relative to the output folder."""

    created: list[str] = field(default_factory=list)  # files that arrived from outside it
    moved: list[tuple[str, str]] = field(default_factory=list)  # (from, to) inside it
    duplicates: int = 0  # staged files that were already filed, and so were dropped
    playable_made: int = 0
    unplayable: list[str] = field(default_factory=list)  # originals with no playable copy
    ffmpeg_missing: bool = False  # why: there is no ffmpeg (else the files would not convert)


def playable_copy(original: Path) -> Optional[Path]:
    """Where the browser-playable copy of `original` goes; None when it needs none."""
    return original.with_suffix(PLAYABLE) if original.suffix.lower() in UNPLAYABLE else None


def _locate(storage_path: Optional[str], roots: list[Path]) -> Optional[tuple[Path, Path]]:
    """The file a stored path means and the root it is under; None when it is not there."""
    if not storage_path:
        return None
    for root in roots:
        candidate = (root / storage_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate, root
    return None


def _stem(renderer: Renderer, spec: PageSpec, message: dict[str, Any], row: dict[str, Any],
          number: Optional[int]) -> str:  # fmt: skip
    local = message["local"]
    sender = message["sender_handle_id"]
    who = renderer.label(sender) if sender else ("Me" if message["is_from_owner"] else "Unknown")
    stem = f"({local:%Y-%m-%d}) {local:%H-%M-%S} {safe_name(who)}"
    if spec.kind == "mail":
        original = Path(row["original_filename"] or "attachment").stem
        return f"{stem} — {safe_name(original, 'attachment', 60)}"
    return f"{stem} {number}" if number else stem


def _folder(spec: PageSpec, service: str) -> str:
    if spec.kind == "mail":
        return f"{spec.folder}/{MAIL_ATTACHMENTS}"
    return f"{spec.folder}/{safe_name(service, 'Other')}"


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:  # another disk
        shutil.move(str(source), str(target))


def _tidy(folder: Path, stop: Path) -> None:
    """Remove `folder` and its parents while they are empty, never `stop` itself."""
    while folder != stop and folder.is_relative_to(stop) and folder.is_dir():
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _transcode(original: Path, copy: Path) -> bool:
    """Make the playable copy with ffmpeg: AAC at the original's own sample rate and channels."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    part = copy.with_name(copy.stem + ".part" + copy.suffix)
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(original), "-vn",
        "-c:a", "aac", "-b:a", UNPLAYABLE[original.suffix.lower()],
        "-movflags", "+faststart", str(part),
    ]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is None or done.returncode != 0 or not part.is_file() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        return False
    os.replace(part, copy)
    return True


def file_attachments(
    db: DatabaseManager,
    renderer: Renderer,
    out_root: PathLike,
    roots: Iterable[PathLike] = (),
    make_playable: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Filed:
    """Move every attachment that is on disk to its place under `out_root`; see the module text.

    `roots` are the folders importers staged files in. A staged file whose content is already
    filed is dropped. The renderer's own rows are updated, so pages drawn next link the new places.
    """
    out = Path(out_root).resolve()
    out.mkdir(parents=True, exist_ok=True)
    have_ffmpeg = shutil.which("ffmpeg") is not None
    staging = [Path(root).resolve() for root in roots]
    everywhere = [out, *staging]
    filed = Filed()

    work = [
        (renderer.page_of[message_id], renderer.messages[message_id], rows)
        for message_id, rows in renderer.attachments.items()
        if message_id in renderer.page_of
    ]
    # Where each filed attachment is now: a name is free unless other content holds it.
    taken: dict[str, str] = {}
    for _, _, rows in work:
        for row in rows:
            found = _locate(row["storage_path"], [out])
            if found:
                taken[found[0].relative_to(out).as_posix()] = row["sha256"]

    total = sum(len(rows) for _, _, rows in work)
    done = 0
    for spec, message, rows in work:
        folder = _folder(spec, message["service"])
        for number, row in enumerate(rows, 1):
            done += 1
            if progress and done % 25 == 0:
                progress(done, total)
            found = _locate(row["storage_path"], everywhere)
            if found is None:
                continue
            current, root = found
            inside = root == out
            current_rel = current.relative_to(out).as_posix() if inside else None
            extension = current.suffix.lower()
            stem = _stem(renderer, spec, message, row, number if len(rows) > 1 else None)
            target_rel = _free_name(out, taken, folder, stem, extension, row["sha256"], current_rel)
            target = out / target_rel
            if target_rel != current_rel:
                sibling = playable_copy(current) if inside else None
                if target.is_file() and taken.get(target_rel) == row["sha256"]:
                    current.unlink()  # this content is already filed under this name
                    filed.duplicates += 1
                else:
                    _move(current, target)
                    if inside:
                        filed.moved.append((str(current_rel), target_rel))
                    else:
                        filed.created.append(target_rel)
                if sibling and sibling.is_file():  # its playable copy goes with it
                    _move(sibling, target.with_suffix(PLAYABLE))
                if current_rel:
                    taken.pop(current_rel, None)
                _tidy(current.parent, root)
                taken[target_rel] = row["sha256"]
                row["storage_path"] = target_rel
                db.connection.execute(
                    "UPDATE attachments SET storage_path = ? WHERE sha256 = ? AND message_id = ?",
                    (target_rel, row["sha256"], row["message_id"]),
                )
            copy = playable_copy(target)
            if copy is not None and copy.relative_to(out).as_posix() not in taken:
                if copy.is_file():
                    continue
                if make_playable and _transcode(target, copy):
                    filed.playable_made += 1
                else:
                    filed.unplayable.append(target_rel)
                    filed.ffmpeg_missing = filed.ffmpeg_missing or not have_ffmpeg
    db.connection.commit()
    if progress:
        progress(total, total)
    return filed


def _holds(file: Path, sha256: str) -> bool:
    """True when `file` is exactly this content (a re-import staged what is already filed)."""
    digest = hashlib.sha256()
    try:
        with open(file, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == sha256


def _free_name(
    out: Path,
    taken: dict[str, str],
    folder: str,
    stem: str,
    extension: str,
    sha256: str,
    current: Optional[str],
) -> str:
    """`folder/stem.ext`, or the first `stem (2)`, `stem (3)`… that other content does not hold."""
    number = 1
    while True:
        name = stem if number == 1 else f"{stem} ({number})"
        candidate = f"{folder}/{name}{extension}"
        number += 1
        if candidate == current or taken.get(candidate) == sha256:
            return candidate
        if candidate in taken:
            continue
        if (out / candidate).exists():
            if _holds(out / candidate, sha256):
                taken[candidate] = sha256  # filed by an earlier import of the same thing
                return candidate
            continue
        return candidate


__all__ = ["Filed", "PLAYABLE", "UNPLAYABLE", "file_attachments", "playable_copy"]
