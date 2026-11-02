"""WhatsApp media is copied, renamed and dated by the shared AttachmentManager; originals stay."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports

from chattr import cli, commands
from chattr.config import Settings


def _export(fixture_family: FamilyExports, folder: str) -> Path:
    return fixture_family.paths["whatsapp"] / folder / "_chat.txt"


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def _digest(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in _files(folder)}


def test_ios_media_is_dated_by_the_message(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    export = _export(fixture_family, "WhatsApp Chat - Ana Costa")
    originals = _digest(export.parent)
    commands.convert(Settings.load(chattr_root), "whatsapp", export, attachments_dir=archive)
    (archived,) = _files(archive)
    assert archived.relative_to(archive).parts[:3] == ("WhatsApp", "2024", "3 - March")
    assert archived.name.startswith("(2024-03-01) 14-05-09")
    assert _digest(export.parent) == originals  # the originals are untouched


def test_android_media_is_dated_by_the_message(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    export = _export(fixture_family, "WhatsApp Chat with Rui Pereira")
    commands.convert(Settings.load(chattr_root), "whatsapp", export, attachments_dir=archive)
    (archived,) = _files(archive)
    assert archived.name.startswith("(2024-04-15) 10-06-00")


def test_the_archive_path_is_recorded_on_the_attachment(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    settings = Settings.load(chattr_root)
    commands.convert(
        settings,
        "whatsapp",
        _export(fixture_family, "WhatsApp Chat - Ana Costa"),
        attachments_dir=archive,
    )
    with sqlite3.connect(commands.central_database(settings)) as db:
        ((stored,),) = db.execute("SELECT storage_path FROM attachments").fetchall()
    assert (archive / stored).is_file()


def test_without_an_archive_folder_nothing_is_copied(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    export = _export(fixture_family, "WhatsApp Chat - Ana Costa")
    before = _files(export.parent)
    commands.convert(Settings.load(chattr_root), "whatsapp", export)
    assert _files(export.parent) == before
    assert not (chattr_root / "Attachments").exists()


def test_a_missing_media_file_does_not_stop_the_import(chattr_root: Path, tmp_path: Path) -> None:
    export = tmp_path / "WhatsApp Chat - X" / "_chat.txt"
    export.parent.mkdir()
    export.write_text("15/04/2024, 10:06 - Rui: IMG-20240415-WA0009.jpg (file attached)\n")
    result = commands.convert(
        Settings.load(chattr_root), "whatsapp", export, attachments_dir=tmp_path / "Attachments"
    )
    assert result.counts.inserted == 1


def test_the_subcommand_takes_attachments_dir(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    export = _export(fixture_family, "WhatsApp Chat - Ana Costa")
    args = ["--root", str(chattr_root), "convert", "whatsapp", str(export)]
    assert cli.main([*args, "--attachments-dir", str(archive)]) == 0
    assert len(_files(archive)) == 1


def test_messenger_does_not_take_attachments_dir_yet(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messenger = str(fixture_family.paths["messenger"])
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--root",
                str(chattr_root),
                "convert",
                "messenger",
                messenger,
                "--attachments-dir",
                str(tmp_path / "A"),
            ]
        )
    assert "--attachments-dir" in capsys.readouterr().err
