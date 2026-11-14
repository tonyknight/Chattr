"""File attachments where a person would look for them: under the person, by source and date.

    People/<Person>/<Service>/(YYYY-MM-DD) HH-MM-SS <sender>.<ext>
    Groups/<Group>/<Service>/…
    Mail/<YYYY>/Attachments/(YYYY-MM-DD) HH-MM-SS <sender> — <name>.<ext>

Importers leave each file wherever suits them (by content hash, in a working folder) and record that
place in `attachments.storage_path`. Filing moves the file beside the pages that show it, names it
for its date and sender, and records the new place (relative to the output folder). It is part of
drawing the pages, so it follows a person who is renamed or merged, and it is safe to repeat.

Pictures in formats not every device opens (HEIC, PNG, WebP) are filed as JPEG at quality 85, as
Chattr has always archived them, with the date they were taken written inside
(`DateTimeOriginal`): the picture's own date when it has one, else the date of the message that
carried it. A JPEG that carries no date is given its message's date too, without touching the
picture itself. Mail attachments are never converted: there the file itself is the record.

Audio is filed as `.m4a` (AAC), which every browser plays: voicemail (`.amr`), voice notes (`.caf`,
`.opus`) and the other formats in `UNPLAYABLE` are transcoded with ffmpeg and only the `.m4a` is
kept, as with pictures. Without ffmpeg the original is filed and the gap reported; importing again
once ffmpeg is installed converts it. MP3 is converted too, so that every recording in the archive
is one format; it keeps its own bit rate (128 kbit/s at most). M4A is left alone.

A file is stored once. When the same content (the same SHA-256) is attached to several messages (a
sticker sent fifty times, a photo sent to three people), it is filed for the earliest of them and
every other message links to that one copy. Mail and chat are kept apart, because mail attachments
are never converted.
"""

from __future__ import annotations

import hashlib
from collections import Counter
import json
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

#: Audio filed as `.m4a`, and the bit rate each is given (voice formats low, music formats higher).
UNPLAYABLE = {
    ".amr": "16k",
    ".awb": "24k",
    ".caf": "32k",
    ".opus": "32k",
    ".ogg": "64k",
    ".oga": "64k",
    ".wma": "96k",
    ".wav": "128k",
    ".aif": "128k",
    ".aiff": "128k",
    ".flac": "160k",
}
PLAYABLE = ".m4a"
#: Audio that plays as it is but is filed as `.m4a` all the same, to keep one format: its bit rate
#: is the original's own, between these limits.
ALSO_M4A = frozenset({".mp3"})
_LOWEST_RATE, _HIGHEST_RATE, _USUAL_RATE = 24_000, 128_000, 64_000
#: Pictures filed as JPEG, and how hard to compress them.
TO_JPEG = frozenset({".heic", ".heif", ".png", ".webp"})
JPEG_QUALITY = 85
_EXIF_IFD, _DATE_TAKEN = 0x8769, 0x9003
MAIL_ATTACHMENTS = "Attachments"


@dataclass
class Filed:
    """What filing did. Paths are relative to the output folder."""

    created: list[str] = field(default_factory=list)  # files that arrived from outside it
    moved: list[tuple[str, str]] = field(default_factory=list)  # (from, to) inside it
    duplicates: int = 0  # staged files that were already filed, and so were dropped
    shared: int = 0  # attachments that link to a copy filed for another message
    playable_made: int = 0  # recordings filed as .m4a
    converted: int = 0  # pictures filed as JPEG
    dated: int = 0  # JPEGs given the date of their message
    unconverted: list[str] = field(default_factory=list)  # pictures that would not convert
    unplayable: list[str] = field(default_factory=list)  # recordings left in their own format
    ffmpeg_missing: bool = False  # why: there is no ffmpeg (else the files would not convert)


def playable_copy(original: Path) -> Optional[Path]:
    """Where a browser-playable copy of `original` would be; None when it needs none.

    Boxes made before recordings were filed as `.m4a` keep such a copy beside the original."""
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


def _to_jpeg(source: Path, taken: Any) -> Union[Path, bool, None]:
    """Write `source` as a JPEG beside it and return that file; None when it cannot be read.

    False means "leave it": a moving picture (an animated sticker) would lose its movement.

    A see-through picture gets a white background. The date it was taken is kept; a picture
    with none is given `taken` (the time of its message).
    """
    try:
        from PIL import Image

        if source.suffix.lower() in (".heic", ".heif"):
            import pillow_heif

            pillow_heif.register_heif_opener()
        target = source.with_name(source.stem + ".converted.jpg")
        with Image.open(source) as opened:
            if getattr(opened, "is_animated", False):
                return False
            image: Any = opened
            exif = opened.getexif()
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            details = exif.get_ifd(_EXIF_IFD)
            if not details.get(_DATE_TAKEN):
                details[_DATE_TAKEN] = f"{taken:%Y:%m:%d %H:%M:%S}"
            colours = opened.info.get("icc_profile")  # iPhone photos are Display P3: keep it
            image.save(
                target, "JPEG", quality=JPEG_QUALITY, optimize=True, exif=exif, icc_profile=colours
            )
        return target
    except Exception:  # an unreadable picture, or Pillow without HEIC support
        source.with_name(source.stem + ".converted.jpg").unlink(missing_ok=True)
        return None


def _date_jpeg(path: Path, taken: Any) -> bool:
    """Give a JPEG with no date its message's date, leaving the picture data as it is.

    A file with no EXIF block gets one put in; one that has a block but no date is left to
    exiftool, which can add to it safely, when exiftool is installed.
    """
    stamp = f"{taken:%Y:%m:%d %H:%M:%S}"
    try:
        from PIL import Image

        with Image.open(path) as opened:
            if opened.format != "JPEG" or opened.getexif().get_ifd(_EXIF_IFD).get(_DATE_TAKEN):
                return False
            has_exif = bool(opened.info.get("exif"))
        if has_exif:
            tool = shutil.which("exiftool")
            if not tool:
                return False
            command = [tool, "-overwrite_original", f"-DateTimeOriginal={stamp}", str(path)]
            return subprocess.run(command, capture_output=True, timeout=60).returncode == 0
        exif = Image.Exif()
        exif.get_ifd(_EXIF_IFD)[_DATE_TAKEN] = stamp
        payload = exif.tobytes()
        data = path.read_bytes()
        # after the JFIF header when there is one, else straight after the start marker
        at = 4 + int.from_bytes(data[4:6], "big") if data[2:4] == b"\xff\xe0" else 2
        block = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
        path.write_bytes(data[:at] + block + data[at:])
        return True
    except Exception:  # not a picture after all, or exiftool would not run
        return False


def _staged(sha256: str, staging: list[Path]) -> Optional[tuple[Path, Path]]:
    """A file an importer staged under this hash, if one is waiting."""
    for root in staging:
        for candidate in (root / sha256[:2]).glob(f"{sha256}*"):
            if candidate.is_file():
                return candidate, root
    return None


def _own_rate(original: Path) -> str:
    """The bit rate to give an MP3's `.m4a`: its own (ffprobe says), within the limits."""
    rate = _USUAL_RATE
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        command = [
            ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
            "stream=bit_rate:format=bit_rate", "-of", "default=nw=1:nk=1", str(original),
        ]  # fmt: skip
        try:
            said = subprocess.run(command, capture_output=True, text=True, timeout=60).stdout
            rate = next(int(line) for line in said.split() if line.isdigit())
        except (OSError, subprocess.SubprocessError, StopIteration):
            pass
    return str(max(_LOWEST_RATE, min(_HIGHEST_RATE, rate)))


def _transcode(original: Path, copy: Path) -> bool:
    """Write `original` as AAC with ffmpeg, at its own sample rate and channels."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    part = copy.with_name(copy.stem + ".part" + copy.suffix)
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(original), "-vn",
        "-c:a", "aac", "-b:a", UNPLAYABLE.get(original.suffix.lower()) or _own_rate(original),
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


def _same_file(file: Path, sha256: str, out: Path, taken: dict[str, str]) -> bool:
    """Whether the filed `file` is this attachment's content, unconverted (an earlier try)."""
    return taken.get(file.relative_to(out).as_posix()) in (None, sha256) and _holds(file, sha256)


def file_attachments(
    db: DatabaseManager,
    renderer: Renderer,
    out_root: PathLike,
    roots: Iterable[PathLike] = (),
    make_playable: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
    convert_images: bool = True,
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

    work = sorted(
        (
            (renderer.page_of[message_id], renderer.messages[message_id], rows)
            for message_id, rows in renderer.attachments.items()
            if message_id in renderer.page_of
        ),
        key=lambda item: (item[1]["local"].replace(tzinfo=None), item[1]["id"]),
    )
    # Where each filed attachment is now: a name is free unless other content holds it.
    taken: dict[str, str] = {}
    # One copy of each content: the row it is filed for. An already filed copy stays where it is;
    # otherwise the earliest message has it.
    homes: dict[tuple[str, bool], tuple[int, int]] = {}
    for _, _, rows in work:
        for row in rows:
            row["_info"] = _info(row)
    for spec, _, rows in work:
        for row in rows:
            key = (row["sha256"], spec.kind == "mail")
            there = _locate(row["storage_path"], [out])
            if there:
                taken[there[0].relative_to(out).as_posix()] = row["sha256"]
            if there and not row["_info"].get("shared"):
                rank = 0
            elif there:
                rank = 1
            elif _locate(row["storage_path"], staging) or _staged(row["sha256"], staging):
                rank = 2
            else:
                continue
            if key not in homes or rank < homes[key][0]:
                homes[key] = (rank, id(row))

    total = sum(len(rows) for _, _, rows in work)
    done = 0
    waiting = Counter(sha256 for sha256, _ in homes)
    links: list[tuple[tuple[str, bool], dict[str, Any]]] = []
    home_rows: dict[tuple[str, bool], dict[str, Any]] = {}
    for spec, message, rows in work:
        folder = _folder(spec, message["service"])
        mail = spec.kind == "mail"
        for number, row in enumerate(rows, 1):
            done += 1
            if progress and done % 25 == 0:
                progress(done, total)
            key = (row["sha256"], mail)
            if key in homes and homes[key][1] != id(row):
                links.append((key, row))  # another message has the copy: link to it afterwards
                continue
            found = _locate(row["storage_path"], everywhere)
            if found is None and (row["_info"] or key in homes):
                found = _staged(row["sha256"], staging)  # its filed copy has gone: file it again
            if found is None:
                continue
            home_rows[key] = row
            current, root = found
            inside = root == out
            waiting[row["sha256"]] -= 1
            if not inside and waiting[row["sha256"]] > 0:
                # mail and a chat both carry it, and each keeps its own: leave one for the other
                spare = current.with_name(f"{current.stem}.spare{current.suffix}")
                shutil.copyfile(current, spare)
                current = spare
            current_rel = current.relative_to(out).as_posix() if inside else None
            was_rel = current_rel
            extension = current.suffix.lower()
            was: Optional[str] = None
            info: dict[str, Any] = {}
            if convert_images and not inside and not mail and extension in TO_JPEG:
                picture = _to_jpeg(current, message["local"])
                if picture is None:
                    filed.unconverted.append(row["original_filename"] or current.name)
                elif isinstance(picture, Path):
                    current.unlink()
                    was, current, extension = extension, picture, ".jpg"
                    info = {"converted_from": was, "to": ".jpg", "quality": JPEG_QUALITY}
                    filed.converted += 1
            elif convert_images and not inside and not mail and extension in (".jpg", ".jpeg"):
                if _date_jpeg(current, message["local"]):
                    was, info = "dated", {"date_taken": "from the message"}
                    filed.dated += 1
            elif make_playable and not mail and (extension in UNPLAYABLE or extension in ALSO_M4A):
                older = current.with_suffix(PLAYABLE)  # a copy an earlier version made beside it
                made = current.with_name(current.stem + ".converted" + PLAYABLE)
                if inside and older.is_file() and older.relative_to(out).as_posix() not in taken:
                    os.replace(older, made)
                if made.is_file() or _transcode(current, made):
                    current.unlink()
                    if current_rel:
                        taken.pop(current_rel, None)
                    was, current, extension = extension, made, PLAYABLE
                    info = {"converted_from": was, "to": PLAYABLE}
                    current_rel = None
                    filed.playable_made += 1
                elif extension in UNPLAYABLE:  # (an MP3 left as it is still plays)
                    filed.unplayable.append(row["original_filename"] or current.name)
                    filed.ffmpeg_missing = filed.ffmpeg_missing or not have_ffmpeg
            if inside and row["_info"]:
                again = _staged(row["sha256"], staging)  # the same original, imported again
                if again:
                    again[0].unlink()
                    _tidy(again[0].parent, again[1])
                    filed.duplicates += 1
            stem = _stem(renderer, spec, message, row, number if len(rows) > 1 else None)
            if was and was != "dated" and not inside:
                # an earlier import filed it unconverted (no ffmpeg then, say): that copy goes
                before = out / f"{folder}/{stem}{was}"
                if before.is_file() and _same_file(before, row["sha256"], out, taken):
                    before.unlink()
            target_rel = _free_name(out, taken, folder, stem, extension, row["sha256"], current_rel)
            target = out / target_rel
            if target_rel != current_rel:
                sibling = playable_copy(current) if inside else None
                if target.is_file() and taken.get(target_rel) == row["sha256"]:
                    current.unlink()  # this content is already filed under this name
                    filed.duplicates += 1
                else:
                    _move(current, target)
                    if was_rel:
                        filed.moved.append((was_rel, target_rel))
                    else:
                        filed.created.append(target_rel)
                if sibling and sibling.is_file():  # its playable copy goes with it
                    _move(sibling, target.with_suffix(PLAYABLE))
                if current_rel:
                    taken.pop(current_rel, None)
                _tidy(current.parent, root)
                taken[target_rel] = row["sha256"]
                _record(db, row, target_rel, info or row["_info"] or {"filed": True})
            elif row["_info"].get("shared") or not row["_info"]:
                kept = {k: v for k, v in row["_info"].items() if k != "shared"}
                _record(db, row, target_rel, kept or {"filed": True})

    for key, row in links:
        home = home_rows.get(key)
        if home is None or not _locate(home["storage_path"], [out]):
            continue
        own = _locate(row["storage_path"], everywhere) or _staged(row["sha256"], staging)
        if own and own[0] != (out / home["storage_path"]).resolve():
            relative = own[0].relative_to(out).as_posix() if own[1] == out else None
            if relative is None or taken.get(relative) == row["sha256"]:
                copy = playable_copy(own[0]) if relative else None
                own[0].unlink()  # a second copy of the same thing
                if copy and copy.is_file():
                    copy.unlink()
                if relative:
                    taken.pop(relative, None)
                _tidy(own[0].parent, own[1])
                filed.duplicates += 1
        filed.shared += 1
        if row["storage_path"] != home["storage_path"] or not row["_info"].get("shared"):
            _record(db, row, home["storage_path"], {**home["_info"], "shared": True})
    db.connection.commit()
    if progress:
        progress(total, total)
    return filed


def _info(row: dict[str, Any]) -> dict[str, Any]:
    """What filing recorded about this attachment before (`attachments.processing_info`)."""
    try:
        value = json.loads(row.get("processing_info") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _record(db: DatabaseManager, row: dict[str, Any], path: str, info: dict[str, Any]) -> None:
    row["storage_path"] = path
    row["_info"] = info
    row["processing_info"] = json.dumps(info) if info else None
    db.connection.execute(
        "UPDATE attachments SET storage_path = ?, processing_info = ? "
        "WHERE sha256 = ? AND message_id = ?",
        (path, row["processing_info"], row["sha256"], row["message_id"]),
    )


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
