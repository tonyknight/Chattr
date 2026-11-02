"""The synthetic Google Voice Takeout and iPhone voicemail exports."""

from __future__ import annotations

import re
import sqlite3
import wave
from pathlib import Path

import pytest

from fixtures import family
from fixtures.builders.voice import build_google_voice, build_iphone_voicemail

GV_NAME = re.compile(
    r"^(?P<who>.+) - (?P<kind>Text|Received|Placed|Missed|Voicemail) - "
    r"(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}Z)\.html$"
)


@pytest.fixture(scope="module")
def calls_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_google_voice(tmp_path_factory.mktemp("gvoice"))


@pytest.fixture(scope="module")
def voicemail_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_iphone_voicemail(tmp_path_factory.mktemp("iphone"))


def test_google_voice_files_follow_takeout_naming(calls_dir: Path) -> None:
    assert calls_dir.parts[-3:] == ("Takeout", "Voice", "Calls")
    pages = sorted(calls_dir.glob("*.html"))
    kinds = set()
    for page in pages:
        match = GV_NAME.match(page.name)
        assert match, page.name
        kinds.add(match["kind"])
    assert kinds == {"Text", "Received", "Placed", "Missed", "Voicemail"}


def test_google_voice_pages_use_the_microformat_classes(calls_dir: Path) -> None:
    texts = [p for p in calls_dir.glob("* - Text - *.html")]
    assert texts
    html = texts[0].read_text(encoding="utf-8")
    for marker in ('class="hChatLog hfeed"', 'class="message"', 'class="dt"', 'class="tel"', "<q>"):
        assert marker in html, marker
    call = next(calls_dir.glob("* - Placed - *.html")).read_text(encoding="utf-8")
    assert 'class="haudio"' in call and 'class="duration"' in call


def test_google_voice_voicemail_has_audio_and_transcript(calls_dir: Path) -> None:
    page = next(calls_dir.glob("* - Voicemail - *.html"))
    html = page.read_text(encoding="utf-8")
    assert 'class="full-text"' in html
    audio = re.search(r'<audio[^>]*src="([^"]+)"', html)
    assert audio, "voicemail page links its audio"
    assert (calls_dir / audio.group(1)).is_file()


def test_google_voice_involves_ana_and_the_unknown_number(calls_dir: Path) -> None:
    joined = "".join(p.read_text(encoding="utf-8") for p in calls_dir.glob("*.html"))
    assert family.ANA.phones[0] in joined
    assert family.UNKNOWN_HANDLE in joined


def test_iphone_voicemail_db_rows_have_audio_files(voicemail_dir: Path) -> None:
    db = voicemail_dir / "voicemail.db"
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT ROWID, sender, date, duration FROM voicemail").fetchall()
    conn.close()
    assert len(rows) >= 2
    for rowid, sender, date, duration in rows:
        audio = voicemail_dir / f"{rowid}.amr"
        assert audio.is_file()
        assert date > 1_000_000_000 and duration > 0
        assert sender.startswith("+1555")


def test_audio_payloads_are_valid_wav(voicemail_dir: Path) -> None:
    audio = next(voicemail_dir.glob("*.amr"))
    with wave.open(str(audio), "rb") as clip:
        assert clip.getnframes() > 0
