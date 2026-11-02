"""Preflight sets up a Chattr root without prompting when told where, and never destroys data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chattr import preflight

SERVICES = [
    "iMessage.json",
    "WhatsApp.json",
    "Messenger.json",
    "GoogleVoice.json",
    "Voicemail.json",
]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHATTR_ROOT", raising=False)

    def no_prompt(*_args: object) -> str:
        raise AssertionError("preflight must not prompt here")

    monkeypatch.setattr("builtins.input", no_prompt)
    return home


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_first_run_with_a_root_creates_everything_without_prompting(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False) == str(root)
    for folder in ("Database", "Output/Chattr", "Output/iMessage", "Services", "temp"):
        assert (root / folder).is_dir(), folder
    assert (root / "config.json").is_file()
    for name in SERVICES:
        assert (root / "Services" / name).is_file(), name
    assert "{CHATTR_ROOT}" in (root / "config.json").read_text(), "placeholder stays on disk"
    pointer = json.loads((home / ".chattr.json").read_text())
    assert pointer["chattr_root"] == str(root)


def test_second_run_changes_nothing(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "Chattr"
    preflight.run_preflight(str(root), interactive=False)
    before = snapshot(root)
    preflight.run_preflight(str(root), interactive=False)
    assert snapshot(root) == before


def test_repair_keeps_user_preferences_and_state(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "Chattr"
    preflight.run_preflight(str(root), interactive=False)
    imessage = root / "Services" / "iMessage.json"
    data = json.loads(imessage.read_text())
    data["preferences"]["spam_filtering"]["mode"] = "conservative"
    data["state"]["last_message_rowid"] = 99
    del data["preferences"]["performance"]["batch_size"]
    imessage.write_text(json.dumps(data))
    (root / "Services" / "WhatsApp.json").unlink()
    (root / "Output" / "iMessage").rmdir()

    preflight.run_preflight(str(root), interactive=False)

    repaired = json.loads(imessage.read_text())
    assert repaired["preferences"]["spam_filtering"]["mode"] == "conservative"
    assert repaired["state"]["last_message_rowid"] == 99
    assert repaired["preferences"]["performance"]["batch_size"] == 1000, "missing default restored"
    assert (root / "Services" / "WhatsApp.json").is_file()
    assert (root / "Output" / "iMessage").is_dir()


def test_explicit_root_does_not_replace_an_existing_pointer(home: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    preflight.run_preflight(str(first), interactive=False)
    preflight.run_preflight(str(second), interactive=False)
    pointer = json.loads((home / ".chattr.json").read_text())
    assert pointer["chattr_root"] == str(first)
    assert (second / "config.json").is_file()


def test_existing_pointer_is_used_when_no_root_is_given(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "Chattr"
    preflight.run_preflight(str(root), interactive=False)
    assert preflight.run_preflight(None, interactive=False) == str(root)


def test_no_root_anywhere_and_not_interactive_fails_cleanly(home: Path) -> None:
    assert preflight.run_preflight(None, interactive=False) is None
    assert not (home / ".chattr.json").exists()


def test_a_new_root_is_reported_as_initialized(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "Fresh"
    assert preflight.run_preflight(str(root), interactive=False)
    out = capsys.readouterr().out
    assert "initialized" in out and "repaired" not in out


def test_a_damaged_root_is_reported_as_repaired(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False)
    (root / "Services" / "WhatsApp.json").unlink()
    capsys.readouterr()
    assert preflight.run_preflight(str(root), interactive=False)
    out = capsys.readouterr().out
    assert "repaired" in out and "initialized" not in out


def test_a_complete_root_is_silent(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False)
    capsys.readouterr()
    assert preflight.run_preflight(str(root), interactive=False)
    assert capsys.readouterr().out == ""
