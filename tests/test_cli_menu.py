"""The step-by-step menu runs the same commands as the subcommands, with the same results."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands, preflight
from chattr.config import Settings

EXIT = "4"


def _menu(root: Path, monkeypatch: pytest.MonkeyPatch, answers: Iterable[str]) -> int:
    """Run the menu with scripted answers; running out of answers is end of input."""
    replies = iter(answers)

    def scripted(prompt: str = "") -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", scripted)
    return cli.run_menu(Settings.load(root))


def _second_root(tmp_path: Path) -> Path:
    root = tmp_path / "Second"
    assert preflight.run_preflight(str(root), interactive=False)
    return root


def _rows(root: Path) -> set[tuple[str, int]]:
    db = sqlite3.connect(root / "Database" / "chattr.sqlite")
    return set(db.execute("SELECT id, is_from_owner FROM messages"))


def _whatsapp(fixture_family: FamilyExports) -> str:
    return str(fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt")


def _chat_db(fixture_family: FamilyExports) -> str:
    return str(fixture_family.paths["imessage"])


def _imessage_export(root: Path) -> list[dict]:
    return json.loads((root / "Output" / "iMessage" / "imessage_export.json").read_text())


def test_convert_into_the_archive_matches_the_subcommand(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = _whatsapp(fixture_family)
    answers = ["1", "1", chat, "", "", family.OWNER.name, EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0

    second = _second_root(tmp_path)
    assert (
        cli.main(["--root", str(second), "convert", "whatsapp", chat, "--owner", family.OWNER.name])
        == 0
    )
    assert _rows(chattr_root) == _rows(second)
    assert any(owned for _, owned in _rows(chattr_root))


def test_convert_to_the_service_folder_with_html(
    chattr_root: Path, fixture_family: FamilyExports, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = ["1", "2", str(fixture_family.paths["messenger"]), "2", "y", "", EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0
    folder = chattr_root / "Output" / "Messenger"
    assert list(folder.glob("*.json")) and list(folder.glob("*.html"))
    assert not (chattr_root / "Database" / "chattr.sqlite").exists() or not _rows(chattr_root)


def test_imessage_with_default_settings_matches_the_subcommand(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _chat_db(fixture_family)
    # Convert → iMessage → database → central archive (default) → no HTML → no owner → defaults
    assert _menu(chattr_root, monkeypatch, ["1", "3", db, "", "", "", "", EXIT]) == 0
    second = _second_root(tmp_path)
    assert cli.main(["--root", str(second), "convert", "imessage", db]) == 0
    assert _rows(chattr_root) == _rows(second) and len(_rows(chattr_root)) == 7


def test_imessage_with_custom_settings_matches_the_subcommand(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _chat_db(fixture_family)
    (group,) = [str(i) for i, name in commands.list_chats(db) if name == family.GROUP_NAME]
    # JSON route; custom: spam mode 3 (disabled), 1 worker, no contacts, the group chat, 2 messages
    answers = ["1", "3", db, "2", "n", "3", "1", "", group, "2", EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0
    second = _second_root(tmp_path)
    args = ["convert", "imessage", db, "--json", "--spam-mode", "disabled", "--workers", "1"]
    assert cli.main(["--root", str(second), *args, "--chat", group, "--limit", "2"]) == 0
    menu, subcommand = _imessage_export(chattr_root), _imessage_export(second)
    assert len(menu) == len(subcommand) == 2
    assert [m["guid"] for m in menu] == [m["guid"] for m in subcommand]


def test_generate_html_from_the_archive(
    chattr_root: Path, fixture_family: FamilyExports, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = ["1", "1", _whatsapp(fixture_family), "", "", "", "2", family.OWNER.name, EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0
    assert (chattr_root / "Output" / "Chattr" / commands.ARCHIVE_PAGE).is_file()


def test_imessage_tools(
    chattr_root: Path,
    fixture_family: FamilyExports,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = _chat_db(fixture_family)
    answers = ["3", "1", db, "2", db, "3", db, "4", db, "5", EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0
    out = capsys.readouterr().out
    for expected in (family.GROUP_NAME, "Handles:", "test messages", "Aggressive mode"):
        assert expected in out


def test_an_invalid_choice_asks_again(
    chattr_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _menu(chattr_root, monkeypatch, ["9", "x", EXIT]) == 0
    assert capsys.readouterr().out.count("Please choose a number from 1 to 4") == 2


def test_command_errors_are_reported_and_the_menu_continues(
    chattr_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_export = ["1", "1", str(tmp_path / "missing.txt")]  # checked before more questions
    missing_database = ["1", "3", str(tmp_path / "missing.db")]  # checked before more questions
    assert _menu(chattr_root, monkeypatch, [*missing_export, *missing_database, EXIT]) == 0
    out = capsys.readouterr().out
    assert "Export file not found" in out and "iMessage database not found" in out
    assert "Please choose" not in out


def test_quoted_and_drag_and_dropped_paths_are_accepted(
    chattr_root: Path,
    fixture_family: FamilyExports,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = chattr_root / "Exports with spaces"
    folder.mkdir()
    chat = folder / "_chat.txt"
    chat.write_bytes(Path(_whatsapp(fixture_family)).read_bytes())
    dropped = str(chat).replace(" ", "\\ ") + " "
    answers = ["1", "1", dropped, "2", "", "", "1", "1", f"'{chat}'", "2", "", "", EXIT]
    assert _menu(chattr_root, monkeypatch, answers) == 0
    out = capsys.readouterr().out
    assert "not found" not in out and out.count("WhatsApp: ") == 2


def test_end_of_input_exits_cleanly(chattr_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _menu(chattr_root, monkeypatch, []) == 0


def test_the_old_processing_paths_are_gone() -> None:
    for name in (
        "process_and_import",
        "imessage_submenu",
        "imessage_quick_process",
        "imessage_advanced_options",
        "generate_html_submenu",
    ):
        assert not hasattr(cli, name), name


def test_a_bad_database_in_the_tools_is_reported_and_the_menu_continues(
    chattr_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    not_a_db = tmp_path / "chat.db"
    not_a_db.write_text("this is not SQLite")
    assert _menu(chattr_root, monkeypatch, ["3", "1", str(not_a_db), "5", EXIT]) == 0
    out = capsys.readouterr().out
    assert "❌" in out and "Goodbye." in out
