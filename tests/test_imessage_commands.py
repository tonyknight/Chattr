"""iMessage commands: native JSON export with the restored options, chat list, diagnostics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings


def test_convert_imessage_writes_native_json(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    db_path = fixture_family.paths["imessage"]
    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        region="US",
        batch_size=2,
        attachment_workers=1,
        workers=1,
        spam_mode="disabled",
        route="service",
    )
    assert result.json_path == chattr_root / "Output" / "iMessage" / "imessage_export.json"
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    messages = data["messages"] if isinstance(data, dict) else data
    assert len(messages) == result.counts.total > 0


def test_list_chats_includes_the_group(fixture_family: FamilyExports) -> None:
    chats = commands.list_chats(fixture_family.paths["imessage"])
    assert family.GROUP_NAME in {name for _, name in chats}


@pytest.mark.parametrize("check", ["info", "connection", "spam"])
def test_every_doctor_check_runs(
    fixture_family: FamilyExports, check: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.doctor(fixture_family.paths["imessage"], check) is True
    assert capsys.readouterr().out.strip()


def test_doctor_reports_a_missing_database(tmp_path: Path) -> None:
    assert commands.doctor(tmp_path / "missing.db", "connection") is False


def test_attachments_are_archived_when_a_folder_is_given(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    db_path = fixture_family.paths["imessage"]
    archive = tmp_path / "Archived attachments"
    commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=db_path,
        attachment_root=db_path.parent / "Attachments",
        attachment_workers=1,
        attachments_dir=archive,
        route="service",
    )
    archived = [p for p in archive.rglob("*") if p.is_file() and not p.name.startswith(".")]
    sources = [p for p in (db_path.parent / "Attachments").rglob("*") if p.is_file()]
    distinct = {hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    # Two different photos sent in the same second must both survive (no overwriting).
    assert len(archived) == len(distinct)


def test_a_file_that_is_not_a_database_is_a_command_error(
    chattr_root: Path, tmp_path: Path
) -> None:
    not_a_db = tmp_path / "chat.db"
    not_a_db.write_text("this is not SQLite")
    with pytest.raises(commands.CommandError, match="iMessage database"):
        commands.list_chats(not_a_db)
    with pytest.raises(commands.CommandError, match="iMessage database"):
        commands.convert_imessage(Settings.load(chattr_root), db_path=not_a_db)


def test_a_permission_problem_is_a_command_error_with_the_advice(
    fixture_family: FamilyExports, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("Unable to access database. Grant Full Disk Access to your Terminal")

    monkeypatch.setattr(commands, "iMessageDatabaseParser", denied)
    with pytest.raises(commands.CommandError, match="Full Disk Access"):
        commands.list_chats(fixture_family.paths["imessage"])
