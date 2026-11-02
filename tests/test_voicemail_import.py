"""`chattr-cli convert voicemail`: iPhone voicemail into the archive, transcribed locally."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import stat
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings
from chattr.transcription import StubTranscriber

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the network was used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _convert(root: Path, folder: Path, **options):
    return commands.convert(Settings.load(root), "voicemail", folder, **options)


def _rows(root: Path, sql: str, *params):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql, params).fetchall()


STUB = {"1.amr": "Feliz Natal, mana! Ligo-te amanhã.", "2.amr": "Hi Mum, call me back."}


def test_voicemails_land_with_transcript_and_audio(
    chattr_root: Path, fixture_family: FamilyExports, no_network: None
) -> None:
    result = _convert(
        chattr_root, fixture_family.paths["iphone_voicemail"], transcriber=StubTranscriber(STUB)
    )
    assert result.service == "Voicemail" and result.counts.inserted == 2
    assert _rows(
        chattr_root, "SELECT content_type, transcript FROM messages ORDER BY timestamp_utc"
    ) == [
        ("voicemail", STUB["1.amr"]),
        ("voicemail", STUB["2.amr"]),
    ]
    store = chattr_root / "Output" / "Voicemail" / "Attachments"
    for original, stored in _rows(
        chattr_root, "SELECT original_filename, storage_path FROM attachments"
    ):
        assert original in STUB and (store / stored).is_file() and stored.endswith(".amr")


def test_the_transcript_is_what_the_view_and_the_html_show(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(
        chattr_root,
        fixture_family.paths["iphone_voicemail"],
        transcriber=StubTranscriber(STUB),
        html=True,
    )
    assert result.html_path is not None
    pages = "".join(
        page.read_text(encoding="utf-8")
        for page in (chattr_root / "Output" / "Chattr").rglob("(*) *.html")
    )
    assert STUB["1.amr"] in pages and STUB["2.amr"] in pages
    assert STUB["1.amr"] in {r[0] for r in _rows(chattr_root, "SELECT body FROM messages_view")}


def test_without_a_transcriber_voicemails_import_untranscribed(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["iphone_voicemail"])
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE transcript IS NULL") == [(2,)]
    assert _rows(chattr_root, "SELECT COUNT(*) FROM attachments") == [(2,)]


def test_transcribing_later_updates_the_same_messages(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    folder = fixture_family.paths["iphone_voicemail"]
    _convert(chattr_root, folder)
    again = _convert(chattr_root, folder, transcriber=StubTranscriber(STUB))
    assert (again.counts.inserted, again.counts.updated, again.counts.unchanged) == (0, 2, 0)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages") == [(2,)]
    assert _rows(chattr_root, "SELECT version FROM messages") == [(2,), (2,)]
    unchanged = _convert(chattr_root, folder, transcriber=StubTranscriber(STUB))
    assert unchanged.counts.unchanged == 2


def test_the_state_and_folders(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, fixture_family.paths["iphone_voicemail"])
    state = json.loads((chattr_root / "Services" / "Voicemail.json").read_text())["state"]
    assert state["total_messages_imported"] == 2
    assert (chattr_root / "Output" / "Voicemail").is_dir()


def test_json_route(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = _convert(
        chattr_root,
        fixture_family.paths["iphone_voicemail"],
        route="service",
        transcriber=StubTranscriber(STUB),
    )
    assert result.json_path == chattr_root / "Output" / "Voicemail" / "Voicemail.json"
    assert len(json.loads(result.json_path.read_text(encoding="utf-8"))["messages"]) == 2


def test_a_folder_without_a_voicemail_database_is_a_plain_error(
    chattr_root: Path, tmp_path: Path
) -> None:
    with pytest.raises(commands.CommandError, match="voicemail.db"):
        _convert(chattr_root, tmp_path)


# --- The command line -----------------------------------------------------------------------


def _fake_whisper(tmp_path: Path) -> Path:
    script = tmp_path / "whisper-cli"
    script.write_text("#!/bin/sh\necho 'hello from the local model'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@needs_ffmpeg
def test_the_subcommand_transcribes_with_a_local_model(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"x")
    path = chattr_root / "Services" / "Voicemail.json"
    data = json.loads(path.read_text())
    data["preferences"]["transcription"]["executable"] = str(_fake_whisper(tmp_path))
    path.write_text(json.dumps(data))
    args = [
        "--root",
        str(chattr_root),
        "convert",
        "voicemail",
        str(fixture_family.paths["iphone_voicemail"]),
    ]
    assert cli.main([*args, "--transcribe", "--model", str(model), "--language", "en"]) == 0
    assert "Voicemail: 2 new" in capsys.readouterr().out
    assert {r[0] for r in _rows(chattr_root, "SELECT transcript FROM messages")} == {
        "hello from the local model"
    }


def test_asking_to_transcribe_without_a_model_says_what_to_do(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [
        "--root",
        str(chattr_root),
        "convert",
        "voicemail",
        str(fixture_family.paths["iphone_voicemail"]),
    ]
    assert cli.main([*args, "--transcribe"]) == 1
    out = capsys.readouterr().out
    assert "model" in out and not _rows_exist(chattr_root)


def _rows_exist(root: Path) -> bool:
    db = commands.central_database(Settings.load(root))
    return db.exists() and bool(
        sqlite3.connect(db).execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    )


def test_a_missing_model_file_is_reported(
    chattr_root: Path,
    fixture_family: FamilyExports,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = [
        "--root",
        str(chattr_root),
        "convert",
        "voicemail",
        str(fixture_family.paths["iphone_voicemail"]),
    ]
    assert cli.main([*args, "--transcribe", "--model", str(tmp_path / "missing.bin")]) == 1
    assert "never downloads" in capsys.readouterr().out


def test_transcription_flags_belong_to_voicemail_only(
    chattr_root: Path, fixture_family: FamilyExports, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--root",
                str(chattr_root),
                "convert",
                "mail",
                str(fixture_family.paths["mbox"]),
                "--transcribe",
            ]
        )
    assert "voicemail" in capsys.readouterr().err


def test_voicemail_from_the_menu_without_transcription(
    chattr_root: Path,
    fixture_family: FamilyExports,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = str(fixture_family.paths["iphone_voicemail"])
    # Convert → iPhone Voicemail (choice 7) → the folder → central archive → no HTML → no owner → Exit
    answers = iter(["1", "7", folder, "", "", "", "4"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert cli.run_menu(Settings.load(chattr_root)) == 0
    assert "Voicemail: 2 new" in capsys.readouterr().out
