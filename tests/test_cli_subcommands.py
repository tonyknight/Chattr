"""`chattr-cli` subcommands: every menu action can run without prompts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli


def _whatsapp(fixture_family: FamilyExports) -> str:
    return str(fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt")


def _chat_db(fixture_family: FamilyExports) -> str:
    return str(fixture_family.paths["imessage"])


def _run(root: Path, *args: str) -> int:
    return cli.main(["--root", str(root), *args])


def _archived(root: Path) -> int:
    db = root / "Database" / "chattr.sqlite"
    if not db.exists():
        return 0
    return sqlite3.connect(db).execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def test_convert_imports_into_the_central_archive_by_default(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(
        chattr_root, "convert", "whatsapp", _whatsapp(fixture_family), "--owner", family.OWNER.name
    )
    assert code == 0
    assert _archived(chattr_root) > 0
    assert "new" in capsys.readouterr().out


def test_root_can_follow_the_command(chattr_root: Path, fixture_family: FamilyExports) -> None:
    args = ["convert", "whatsapp", _whatsapp(fixture_family), "--root", str(chattr_root)]
    assert cli.main(args) == 0
    assert _archived(chattr_root) > 0


def test_convert_json_writes_the_service_folder(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    assert _run(chattr_root, "convert", "whatsapp", _whatsapp(fixture_family), "--json") == 0
    assert list((chattr_root / "Output" / "WhatsApp").glob("*.json"))
    assert _archived(chattr_root) == 0


def test_convert_html_and_aliases(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({family.JOAO.name: "João (son)"}))
    code = _run(
        chattr_root,
        "convert",
        "messenger",
        str(fixture_family.paths["messenger"]),
        "--json",
        "--html",
        "--aliases",
        str(aliases),
    )
    assert code == 0
    folder = chattr_root / "Output" / "Messenger"
    (json_path,) = folder.glob("*.json")
    assert "João (son)" in {m["sender"] for m in json.loads(json_path.read_text())["messages"]}
    assert list(folder.glob("*.html"))


def test_convert_imessage_with_the_restored_flags(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _chat_db(fixture_family)
    code = _run(
        chattr_root,
        "convert",
        "imessage",
        db,
        "--region",
        "US",
        "--attachment-root",
        str(Path(db).parent / "Attachments"),
        "--batch-size",
        "2",
        "--attachment-workers",
        "1",
        "--workers",
        "1",
        "--spam-mode",
        "disabled",
        "--verbose",
        "--attachments-dir",
        str(chattr_root / "Archived attachments"),
    )
    assert code == 0
    assert [p for p in (chattr_root / "Archived attachments").rglob("*") if p.is_file()]
    export = chattr_root / "Output" / "iMessage" / "imessage_export.json"
    assert json.loads(export.read_text(encoding="utf-8"))
    assert "11 messages" in capsys.readouterr().out


def test_convert_imessage_limit_and_chat(chattr_root: Path, fixture_family: FamilyExports) -> None:
    db = _chat_db(fixture_family)
    (group_id,) = [i for i, name in cli.commands.list_chats(db) if name == family.GROUP_NAME]
    code = _run(chattr_root, "convert", "imessage", db, "--chat", str(group_id), "--limit", "2")
    assert code == 0
    export = chattr_root / "Output" / "iMessage" / "imessage_export.json"
    assert len(json.loads(export.read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize(
    "extra",
    [
        ["imessage", "chat.db", "--db"],
        ["imessage", "chat.db", "--html"],
        ["whatsapp", "_chat.txt", "--region", "US"],
        ["whatsapp", "_chat.txt", "--contacts", "people.vcf"],
        ["whatsapp"],
    ],
)
def test_options_that_do_not_apply_are_usage_errors(
    chattr_root: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        _run(chattr_root, "convert", *extra)
    assert stop.value.code == 2
    assert "chattr-cli" in capsys.readouterr().err


def test_a_missing_export_fails_with_a_plain_message(
    chattr_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(chattr_root, "convert", "whatsapp", str(tmp_path / "missing.txt")) == 1
    assert "not found" in capsys.readouterr().out


def test_html_renders_the_archive(chattr_root: Path, fixture_family: FamilyExports) -> None:
    assert _run(chattr_root, "html") == 1  # nothing archived yet
    _run(chattr_root, "convert", "whatsapp", _whatsapp(fixture_family))
    out = chattr_root / "archive.html"
    assert _run(chattr_root, "html", "--out", str(out), "--owner", family.OWNER.name) == 0
    assert out.is_file()


def test_chats_lists_the_database(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(chattr_root, "chats", _chat_db(fixture_family)) == 0
    assert family.GROUP_NAME in capsys.readouterr().out


def test_doctor_runs_every_check_by_default(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(chattr_root, "doctor", _chat_db(fixture_family)) == 0
    out = capsys.readouterr().out
    assert "Handles:" in out and "test messages" in out and "Aggressive mode" in out


def test_doctor_runs_one_check_and_reports_failure(
    chattr_root: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    assert _run(chattr_root, "doctor", _chat_db(fixture_family), "--check", "spam") == 0
    assert _run(chattr_root, "doctor", str(tmp_path / "missing.db")) == 1


def test_no_subcommand_runs_the_menu(chattr_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []
    monkeypatch.setattr(cli, "run_menu", lambda settings: seen.append(settings.root) or 0)
    assert _run(chattr_root) == 0
    assert seen == [str(chattr_root)] or seen == [chattr_root]
