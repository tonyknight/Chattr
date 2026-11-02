"""Messenger and Instagram media is archived through the same shared AttachmentManager path."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))


def _digest(folder: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in _files(folder)}


def test_every_attachment_is_archived_dated_by_its_message(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    export = fixture_family.paths["messenger"]
    export_root = export.parents[4]  # the export folder that holds your_facebook_activity/
    originals = _digest(export_root)
    archive = tmp_path / "Attachments"
    settings = Settings.load(chattr_root)
    commands.convert(settings, "messenger", export, attachments_dir=archive)
    (archived,) = _files(archive)
    joao = family.JOAO.usernames["messenger"]
    assert archived.relative_to(archive).parts[:2] == (joao, "Attachments")
    assert archived.name == f"(2019-07-21) 16-10-00 {joao} 1.png"
    assert _digest(export_root) == originals
    with sqlite3.connect(commands.central_database(settings)) as db:
        ((stored,),) = db.execute("SELECT storage_path FROM attachments").fetchall()
    assert (archive / stored).is_file()


def test_several_attachments_on_one_message_all_arrive(chattr_root: Path, tmp_path: Path) -> None:
    from fixtures.builders.media import png

    folder = tmp_path / "inbox" / "album_1"
    (folder / "photos").mkdir(parents=True)
    photos = []
    for index, colour in enumerate([(200, 10, 10), (10, 200, 10), (10, 10, 200)]):
        (folder / "photos" / f"p{index}.png").write_bytes(png(rgb=colour))
        photos.append({"uri": f"inbox/album_1/photos/p{index}.png"})
    (folder / "message_1.json").write_text(
        json.dumps(
            {
                "participants": [{"name": "A"}, {"name": "B"}],
                "title": "Album",
                "thread_path": "inbox/album_1",
                "messages": [
                    {"sender_name": "A", "timestamp_ms": 1_700_000_000_000, "photos": photos}
                ],
            }
        )
    )
    archive = tmp_path / "Attachments"
    commands.convert(
        Settings.load(chattr_root), "messenger", folder / "message_1.json", attachments_dir=archive
    )
    assert len(_files(archive)) == 3  # same second: numbered, none overwritten


def test_instagram_takes_the_subcommand_flag(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    export = fixture_family.paths["instagram"]
    args = ["--root", str(chattr_root), "convert", "instagram", str(export)]
    assert cli.main([*args, "--attachments-dir", str(tmp_path / "A")]) == 0  # no media: no error
