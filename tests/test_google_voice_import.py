"""`chattr-cli convert googlevoice`: a Takeout folder into the archive, voicemail audio kept."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import cli, commands
from chattr.config import Settings


def _convert(root: Path, export: Path, **options):
    return commands.convert(Settings.load(root), "googlevoice", export, **options)


def _rows(root: Path, sql: str, *params):
    with sqlite3.connect(commands.central_database(Settings.load(root))) as db:
        return db.execute(sql, params).fetchall()


def test_texts_calls_and_voicemails_land_with_their_content_types(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    result = _convert(chattr_root, fixture_family.paths["google_voice"])
    assert result.service == "Google Voice" and result.counts.inserted == 6
    assert _rows(
        chattr_root, "SELECT content_type, COUNT(*) FROM messages GROUP BY 1 ORDER BY 1"
    ) == [
        ("call", 3),
        ("text", 2),
        ("voicemail", 1),
    ]
    assert _rows(
        chattr_root,
        "SELECT name, type, COUNT(*) FROM conversations c JOIN messages m ON m.conversation_id = c.id GROUP BY c.id ORDER BY 3 DESC",
    ) == [
        (family.ANA.name, "individual", 5),
        (family.UNKNOWN_HANDLE, "individual", 1),
    ]


def test_times_keep_their_offset_and_the_transcript_is_stored(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["google_voice"])
    assert _rows(
        chattr_root,
        "SELECT timestamp_utc, tz_offset_minutes, tz_assumed FROM messages WHERE content_type = 'voicemail'",
    ) == [("2018-09-08T15:22:45+00:00", -300, 0)]
    ((transcript,),) = _rows(
        chattr_root, "SELECT transcript FROM messages WHERE content_type = 'voicemail'"
    )
    assert transcript.startswith("Hi Mãe, it's Ana.")


def test_voicemail_audio_is_kept_as_an_attachment(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["google_voice"])
    ((sha, name, mime, stored),) = _rows(
        chattr_root, "SELECT sha256, original_filename, mime_type, storage_path FROM attachments"
    )
    assert name.endswith(".mp3") and mime == "audio/mpeg"
    assert (chattr_root / "Output" / "GoogleVoice" / "Attachments" / stored).is_file()
    assert stored == f"{sha[:2]}/{sha}.mp3"


def test_call_details_are_in_service_specific(
    chattr_root: Path, fixture_family: FamilyExports
) -> None:
    _convert(chattr_root, fixture_family.paths["google_voice"])
    details = sorted(
        json.loads(r[0])["duration_seconds"]
        for r in _rows(
            chattr_root, "SELECT service_specific FROM messages WHERE content_type = 'call'"
        )
    )
    assert details == [0, 95, 312]


def test_reimporting_changes_nothing(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, fixture_family.paths["google_voice"])
    again = _convert(chattr_root, fixture_family.paths["google_voice"])
    assert (again.counts.inserted, again.counts.unchanged) == (0, 6)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM attachments") == [(1,)]


def test_owner_and_the_state_file(chattr_root: Path, fixture_family: FamilyExports) -> None:
    _convert(chattr_root, fixture_family.paths["google_voice"], owner=family.OWNER.name)
    assert _rows(chattr_root, "SELECT COUNT(*) FROM messages WHERE is_from_owner = 1") == [(2,)]
    state = json.loads((chattr_root / "Services" / "GoogleVoice.json").read_text())["state"]
    assert state["total_messages_imported"] == 6


def test_a_folder_is_required_not_a_file(chattr_root: Path, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html/>")
    with pytest.raises(commands.CommandError, match="folder"):
        _convert(chattr_root, page)
    with pytest.raises(commands.CommandError, match="not found"):
        _convert(chattr_root, tmp_path / "nothing")


def test_a_folder_with_nothing_to_import_says_so(chattr_root: Path, tmp_path: Path) -> None:
    with pytest.raises(commands.CommandError, match="No Google Voice"):
        _convert(chattr_root, tmp_path)


def test_json_route_and_html(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = _convert(chattr_root, fixture_family.paths["google_voice"], route="service", html=True)
    assert result.json_path == chattr_root / "Output" / "GoogleVoice" / "GoogleVoice.json"
    assert len(json.loads(result.json_path.read_text(encoding="utf-8"))["messages"]) == 6
    site = chattr_root / "Output" / "GoogleVoice"
    assert result.html_path == site / "index.html"
    assert (site / "Messages" / family.ANA.name / "(2018) Ana Costa.html").is_file()
    assert (site / "Messages" / family.UNKNOWN_HANDLE / "(2018) +15550199.html").is_file()


def test_the_subcommand_and_the_menu(
    chattr_root: Path,
    fixture_family: FamilyExports,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = str(fixture_family.paths["google_voice"])
    assert cli.main(["--root", str(chattr_root), "convert", "googlevoice", folder]) == 0
    assert "Google Voice: 6 new" in capsys.readouterr().out
    # Convert → Google Voice (choice 6) → the folder → central archive → no HTML → no owner → Exit
    answers = iter(["1", "6", folder, "", "", "", "4"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert cli.run_menu(Settings.load(chattr_root)) == 0
    assert "0 new" in capsys.readouterr().out
