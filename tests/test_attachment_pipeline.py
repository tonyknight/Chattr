"""One attachment pipeline: date names, per-person folders, no collisions, originals kept."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fixtures.builders.media import png

from chattr.attachment_manager import AttachmentInfo, AttachmentManager

EAST = timezone(timedelta(hours=-5))


@pytest.fixture(autouse=True)
def no_exiftool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """These tests don't depend on whether exiftool is installed on the machine."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty) + os.pathsep + "/usr/bin" + os.pathsep + "/bin")
    for tool in ("exiftool",):
        assert not (empty / tool).exists()


def _source(folder: Path, name: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def _info(
    path: Path,
    sender: str | None = "Ana Costa",
    person: str | None = "Ana Costa",
    group: bool = False,
) -> AttachmentInfo:
    return AttachmentInfo(
        original_path=str(path),
        original_filename=path.name,
        mime_type=None,
        file_size=path.stat().st_size,
        created_date=None,
        sender=sender,
        person=person,
        is_group=group,
    )


def _archive(base: Path, items: list[tuple[AttachmentInfo, datetime]], **options):
    manager = AttachmentManager(str(base), service_name="WhatsApp", max_workers=2, **options)
    return manager.process_attachments_parallel(items)


def _files(base: Path) -> list[str]:
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )


def _digest(folder: Path) -> dict[str, str]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()
    }


WHEN = datetime(2024, 3, 1, 14, 5, 9)


def test_names_follow_the_date_sender_number_rule_in_the_persons_folder(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "IMG_0001.png", png(rgb=(1, 2, 3)))
    (done,) = _archive(tmp_path / "out", [(_info(photo), WHEN)])
    assert _files(tmp_path / "out") == [
        "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 1.png"
    ]
    assert done.archival_path == "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 1.png"
    assert done.archival_filename == "(2024-03-01) 14-05-09 Ana Costa 1.png"
    assert done.processing_status == "completed"


def test_the_time_is_shown_as_the_message_states_it(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.jpg", b"jpeg bytes")
    when = datetime(2023, 6, 4, 23, 41, 0, tzinfo=EAST)  # 04:41 UTC the next day
    _archive(tmp_path / "out", [(_info(photo), when)])
    assert _files(tmp_path / "out") == [
        "Ana Costa/Attachments/(2023-06-04) 23-41-00 Ana Costa 1.jpg"
    ]


def test_milliseconds_are_kept_when_there_are_any(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.jpg", b"x")
    _archive(tmp_path / "out", [(_info(photo), WHEN.replace(microsecond=250_000))])
    assert _files(tmp_path / "out") == [
        "Ana Costa/Attachments/(2024-03-01) 14-05-09 [250] Ana Costa 1.jpg"
    ]


def test_two_different_files_in_the_same_second_are_both_kept(tmp_path: Path) -> None:
    a = _source(tmp_path / "in", "IMG_0001.png", png(rgb=(10, 20, 30)))
    b = _source(tmp_path / "in", "IMG_0002.png", png(rgb=(30, 20, 10)))
    _archive(tmp_path / "out", [(_info(b), WHEN), (_info(a), WHEN)])  # given out of order
    names = _files(tmp_path / "out")
    assert names == [
        "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 1.png",
        "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 2.png",
    ]
    # Numbering follows the sorted source names, not the order they were handed over.
    out = tmp_path / "out" / "Ana Costa" / "Attachments"
    assert (out / names[0].rsplit("/", 1)[1]).read_bytes() == a.read_bytes()


def test_names_are_the_same_on_every_run_and_nothing_is_added(tmp_path: Path) -> None:
    sources = [_source(tmp_path / "in", f"p{i}.png", png(rgb=(i, i, i))) for i in range(5)]
    items = [(_info(p), WHEN) for p in sources]
    _archive(tmp_path / "out", items)
    first = _files(tmp_path / "out")
    snapshot = _digest(tmp_path / "out")
    _archive(tmp_path / "out", [(_info(p), WHEN) for p in reversed(sources)])
    assert _files(tmp_path / "out") == first and len(first) == 5
    assert _digest(tmp_path / "out") == snapshot


def test_the_same_file_sent_twice_is_two_dated_real_copies(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "x.png", png(rgb=(9, 9, 9)))
    _archive(tmp_path / "out", [(_info(photo), WHEN), (_info(photo), WHEN + timedelta(days=3))])
    out = tmp_path / "out" / "Ana Costa" / "Attachments"
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "(2024-03-01) 14-05-09 Ana Costa 1.png",
        "(2024-03-04) 14-05-09 Ana Costa 1.png",
    ]
    assert not any(p.is_symlink() for p in out.iterdir())


def test_a_new_file_never_overwrites_one_from_an_earlier_run(tmp_path: Path) -> None:
    old = _source(tmp_path / "in", "old.png", png(rgb=(1, 1, 1)))
    _archive(tmp_path / "out", [(_info(old), WHEN)])
    new = _source(tmp_path / "in", "new.png", png(rgb=(2, 2, 2)))
    _archive(tmp_path / "out", [(_info(new), WHEN)])
    out = tmp_path / "out" / "Ana Costa" / "Attachments"
    assert sorted(p.name for p in out.iterdir()) == [
        "(2024-03-01) 14-05-09 Ana Costa 1.png",
        "(2024-03-01) 14-05-09 Ana Costa 2.png",
    ]
    assert (out / "(2024-03-01) 14-05-09 Ana Costa 1.png").read_bytes() == old.read_bytes()


def test_a_file_the_archive_does_not_know_is_not_overwritten(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png(rgb=(5, 5, 5)))
    out = tmp_path / "out" / "Ana Costa" / "Attachments"
    out.mkdir(parents=True)
    stranger = out / "(2024-03-01) 14-05-09 Ana Costa 1.png"
    stranger.write_bytes(b"someone else's file")
    _archive(tmp_path / "out", [(_info(photo), WHEN)])
    assert stranger.read_bytes() == b"someone else's file"
    assert (out / "(2024-03-01) 14-05-09 Ana Costa 2.png").read_bytes() == photo.read_bytes()


def test_groups_get_their_own_folder(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png())
    _archive(
        tmp_path / "out", [(_info(photo, sender="Rui", person="Costa Family", group=True), WHEN)]
    )
    assert _files(tmp_path / "out") == [
        "Groups/Costa Family/Attachments/(2024-03-01) 14-05-09 Rui 1.png"
    ]


def test_different_people_get_different_folders(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png())
    _archive(
        tmp_path / "out",
        [
            (_info(photo, person="Ana Costa"), WHEN),
            (_info(photo, sender="Rui", person="Rui Pereira"), WHEN),
        ],
    )
    assert {n.split("/")[0] for n in _files(tmp_path / "out")} == {"Ana Costa", "Rui Pereira"}


def test_unknown_sender_and_person_have_safe_fallbacks(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png())
    _archive(tmp_path / "out", [(_info(photo, sender=None, person=None), WHEN)])
    assert _files(tmp_path / "out") == ["Unknown/Attachments/(2024-03-01) 14-05-09 Unknown 1.png"]


def test_names_are_safe_for_file_systems(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png())
    _archive(tmp_path / "out", [(_info(photo, sender="A/B: C?", person="../../escape \\ x"), WHEN)])
    (name,) = _files(tmp_path / "out")
    assert ".." not in name.split("/") and name.count("/") == 2
    assert not (tmp_path / "escape").exists()
    assert not any(ch in name.rsplit("/", 1)[1] for ch in '\\:*?"<>|')


def test_the_extension_follows_the_file_lower_cased(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "PHOTO.JPEG", b"jpeg")
    _archive(tmp_path / "out", [(_info(photo), WHEN)])
    assert _files(tmp_path / "out")[0].endswith(" 1.jpeg")


def test_a_file_that_cannot_be_read_does_not_stop_the_others(tmp_path: Path) -> None:
    good = _source(tmp_path / "in", "good.png", png())
    missing = _info(good)
    missing.original_path = str(tmp_path / "in" / "gone.png")
    results = _archive(tmp_path / "out", [(missing, WHEN), (_info(good), WHEN)])
    assert sorted(r.processing_status for r in results) == ["completed", "failed"]
    assert len(_files(tmp_path / "out")) == 1


def test_originals_are_untouched(tmp_path: Path) -> None:
    sources = [_source(tmp_path / "in", f"p{i}.png", png(rgb=(i, 0, 0))) for i in range(3)]
    before = _digest(tmp_path / "in")
    _archive(tmp_path / "out", [(_info(p), WHEN) for p in sources])
    assert _digest(tmp_path / "in") == before


# --- Metadata -------------------------------------------------------------------------------


def _fake_exiftool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, date: str = "") -> Path:
    """An exiftool that reports `date` (if any) and logs every write it is asked to do."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    log = tmp_path / "exiftool.log"
    script = bin_dir / "exiftool"
    script.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *-s3*) [ -n "{date}" ] && echo "{date}"; exit 0;;\n'
        f'  *) echo "$*" >> "{log}"; exit 0;;\n'
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return log


def test_a_missing_date_is_written_on_the_copy_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _fake_exiftool(tmp_path, monkeypatch)
    photo = _source(tmp_path / "in", "a.jpg", b"jpeg bytes")
    (done,) = _archive(tmp_path / "out", [(_info(photo), WHEN)])
    entries = log.read_text().splitlines()
    assert len(entries) == 1 and "DateTimeOriginal=2024:03:01 14:05:09" in entries[0]
    assert str(photo) not in entries[0] and "Ana Costa 1.jpg" in entries[0]
    assert done.metadata_enhanced is True


def test_an_existing_capture_date_names_the_file_and_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _fake_exiftool(tmp_path, monkeypatch, date="2019:05:04 10:11:12")
    photo = _source(tmp_path / "in", "a.jpg", b"jpeg bytes")
    (done,) = _archive(tmp_path / "out", [(_info(photo), WHEN)])
    assert _files(tmp_path / "out") == [
        "Ana Costa/Attachments/(2019-05-04) 10-11-12 Ana Costa 1.jpg"
    ]
    assert not log.exists() and done.metadata_enhanced is False


def test_without_exiftool_the_copy_is_still_made(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.jpg", b"jpeg bytes")
    (done,) = _archive(tmp_path / "out", [(_info(photo), WHEN)])
    assert done.processing_status == "completed" and done.metadata_enhanced is False


def test_only_media_files_get_a_date_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _fake_exiftool(tmp_path, monkeypatch)
    doc = _source(tmp_path / "in", "notes.txt", b"plain text")
    _archive(tmp_path / "out", [(_info(doc), WHEN)])
    assert not log.exists()


def test_the_manifest_records_each_source_hash(tmp_path: Path) -> None:
    photo = _source(tmp_path / "in", "a.png", png(rgb=(4, 4, 4)))
    _archive(tmp_path / "out", [(_info(photo), WHEN)])
    manifest = json.loads((tmp_path / "out" / ".attachments.json").read_text())
    assert manifest == {
        "Ana Costa/Attachments/(2024-03-01) 14-05-09 Ana Costa 1.png": {
            "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()
        }
    }
