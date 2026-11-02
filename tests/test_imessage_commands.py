"""iMessage commands: native JSON export with the restored options, chat list, diagnostics."""

from __future__ import annotations

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
