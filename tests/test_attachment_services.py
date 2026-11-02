"""Every file-based service archives through the one pipeline, into per-person folders."""

from __future__ import annotations

from pathlib import Path

from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings


def _archive(base: Path) -> list[str]:
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )


def test_imessage_files_go_to_the_chat_and_group_folders(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    db_path = fixture_family.paths["imessage"]
    archive = tmp_path / "Attachments"
    commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        attachments_dir=archive,
        route="service",
        spam_mode="disabled",
        workers=1,
    )
    ana = family.ANA.phones[0]
    assert _archive(archive) == sorted(
        [
            "Groups/Costa Family/Attachments/(2024-12-21) 00-05-00 Me 1.gif",
            f"{ana}/Attachments/(2023-06-04) 23-41-00 {ana} 1.png",
            f"{ana}/Attachments/(2023-06-04) 23-41-00 {ana} 2.png",
        ]
    )


def test_the_native_json_points_at_the_archived_names(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    import json

    db_path = fixture_family.paths["imessage"]
    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        attachments_dir=tmp_path / "Attachments",
        route="service",
        spam_mode="disabled",
        workers=1,
    )
    assert result.json_path is not None
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    paths = sorted(a["attachment_path"] for m in data for a in m["attachments"])
    ana = family.ANA.phones[0]
    assert paths == sorted(
        [
            "Groups/Costa Family/Attachments/(2024-12-21) 00-05-00 Me 1.gif",
            f"{ana}/Attachments/(2023-06-04) 23-41-00 {ana} 1.png",
            f"{ana}/Attachments/(2023-06-04) 23-41-00 {ana} 2.png",
        ]
    )


def test_whatsapp_and_meta_use_the_same_layout(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    settings = Settings.load(chattr_root)
    whatsapp = fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    commands.convert(settings, "whatsapp", whatsapp, attachments_dir=archive)
    commands.convert(
        settings, "messenger", fixture_family.paths["messenger"], attachments_dir=archive
    )
    joao = family.JOAO.usernames["messenger"]
    assert _archive(archive) == sorted(
        [
            f"{joao}/Attachments/(2019-07-21) 16-10-00 {joao} 1.png",
            "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 1.png",
        ]
    )


def test_converting_again_leaves_the_archive_unchanged(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    archive = tmp_path / "Attachments"
    whatsapp = fixture_family.paths["whatsapp"] / "WhatsApp Chat with Rui Pereira" / "_chat.txt"
    settings = Settings.load(chattr_root)
    commands.convert(settings, "whatsapp", whatsapp, attachments_dir=archive)
    first = {p: p.read_bytes() for p in archive.rglob("*") if p.is_file()}
    commands.convert(settings, "whatsapp", whatsapp, attachments_dir=archive)
    assert {p: p.read_bytes() for p in archive.rglob("*") if p.is_file()} == first
