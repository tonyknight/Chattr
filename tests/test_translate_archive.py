"""`chattr-cli translate`: translating an archive in place, resumably, and showing the result."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fixtures import family
from stub_server import StubServer

from chattr import cli, commands
from chattr.config import Settings
from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.records import ConversationRecord, MessageRecord
from chattr.render import Renderer
from chattr.translation import (
    Translation,
    TranslationError,
    detect_language,
    translate_archive,
)

PT = "Cheguei! Vamos jantar amanhã com a família, não te esqueças."
EN = "I arrived! Let us have dinner tomorrow with the family, do not forget."


class Recorder:
    """A translator that records what it was asked."""

    def __init__(self, fail_on: str = "", always_fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_on, self.always_fail = fail_on, always_fail

    def translate(self, text: str) -> Translation:
        self.calls.append(text)
        if self.always_fail or (self.fail_on and self.fail_on in text):
            raise TranslationError("Could not reach the translation server: refused")
        return Translation(f"[en] {text}", 10, 5)


def _db(tmp_path: Path, *messages: MessageRecord, service: str = "WhatsApp") -> Path:
    path = tmp_path / "t.sqlite"
    with DatabaseManager(path) as db:
        db.upsert(ConversationRecord(service, "chat", name="Chat"), list(messages))
        db.commit()
    return path


def _m(minute: float, author: str, text: str, kind: str = "text") -> MessageRecord:
    return MessageRecord(
        timestamp=family.at(2024, 1, 1, 12, 0) + timedelta(minutes=minute),
        sender=author,
        sender_name=author,
        body=text,
        content_type=kind,
    )


def _translations(path: Path) -> dict[str, tuple[str | None, str | None]]:
    with sqlite3.connect(path) as db:
        return {
            b: (t, l) for b, t, l in db.execute("SELECT body, translation, language FROM messages")
        }


def test_only_messages_that_need_it_are_translated(tmp_path: Path) -> None:
    path = _db(
        tmp_path,
        _m(0, "A", PT),
        _m(60, "A", EN),
        _m(120, "B", "ok"),
        _m(180, "B", "Another one, in English, for us all."),
    )
    recorder = Recorder()
    with DatabaseManager(path) as db:
        stats = translate_archive(db, recorder)
    assert recorder.calls == [PT]
    assert (stats.translated, stats.skipped_english, stats.skipped_unknown) == (1, 2, 1)
    stored = _translations(path)
    assert stored[PT] == (f"[en] {PT}", "pt")
    assert stored[EN] == (None, "en") and stored["ok"] == (None, None)


def test_text_the_author_already_translated_is_left_alone(tmp_path: Path) -> None:
    path = _db(tmp_path, _m(0, "A", PT), _m(1, "A", EN))
    recorder = Recorder()
    with DatabaseManager(path) as db:
        stats = translate_archive(db, recorder)
    assert recorder.calls == [] and stats.skipped_by_author == 1 and stats.translated == 0


def test_only_text_messages_are_considered(tmp_path: Path) -> None:
    path = _db(
        tmp_path, _m(0, "A", PT, kind="email"), _m(1, "A", PT, kind="system"), service="Mail"
    )
    with DatabaseManager(path) as db:
        assert translate_archive(db, Recorder()).translated == 0


def test_a_second_run_does_nothing(tmp_path: Path) -> None:
    path = _db(
        tmp_path,
        _m(0, "A", PT),
        _m(600, "A", "Vamos jantar amanhã com a família, não te esqueças de mim."),
    )
    with DatabaseManager(path) as db:
        translate_archive(db, Recorder())
        again = Recorder()
        stats = translate_archive(db, again)
    assert again.calls == [] and stats.translated == 0 and stats.already_translated == 2


def test_one_failure_does_not_stop_the_rest(tmp_path: Path) -> None:
    path = _db(
        tmp_path,
        _m(0, "A", PT),
        _m(600, "A", "Mãe, você pode me buscar às seis? Estou na escola e não tenho carro."),
    )
    recorder = Recorder(fail_on="Cheguei")
    with DatabaseManager(path) as db:
        stats = translate_archive(db, recorder)
    assert (stats.translated, stats.failed) == (1, 1) and len(recorder.calls) == 2
    assert _translations(path)[PT][0] is None  # left for a later run


def test_a_server_that_is_not_there_stops_the_run_early(tmp_path: Path) -> None:
    texts = [f"{PT} {n}" for n in range(12)]
    path = _db(tmp_path, *[_m(n * 10, "A", t) for n, t in enumerate(texts)])
    recorder = Recorder(always_fail=True)
    with DatabaseManager(path) as db:
        stats = translate_archive(db, recorder)
    assert stats.aborted and len(recorder.calls) == 5 and stats.translated == 0
    assert "reach" in stats.errors[0]


def test_a_limit_caps_the_translations_in_one_run(tmp_path: Path) -> None:
    path = _db(tmp_path, *[_m(n * 10, "A", f"{PT} {n}") for n in range(6)])
    recorder = Recorder()
    with DatabaseManager(path) as db:
        stats = translate_archive(db, recorder, limit=2)
        rest = translate_archive(db, recorder)
    assert stats.translated == 2 and rest.translated == 4


def test_one_service_can_be_translated_alone(tmp_path: Path) -> None:
    path = _db(tmp_path, _m(0, "A", PT))
    with DatabaseManager(path) as db:
        assert translate_archive(db, Recorder(), service="Messenger").translated == 0
        assert translate_archive(db, Recorder(), service="WhatsApp").translated == 1


def test_tokens_are_counted(tmp_path: Path) -> None:
    path = _db(tmp_path, _m(0, "A", PT), _m(600, "A", PT + " bis"))
    with DatabaseManager(path) as db:
        stats = translate_archive(db, Recorder())
    assert (stats.tokens_in, stats.tokens_out) == (20, 10)


def test_the_fixture_archive_translates_its_portuguese(full_archive, tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite"
    shutil.copy(full_archive.database, copy)
    recorder = Recorder()
    with DatabaseManager(copy) as db:
        stats = translate_archive(db, recorder)
    assert stats.translated == len(recorder.calls) >= 3
    assert all(detect_language(call) not in (None, "en") for call in recorder.calls)
    stored = _translations(copy)
    assert stored["Cheguei! Vamos jantar amanhã?"][0] == "[en] Cheguei! Vamos jantar amanhã?"


# --- Pages and the command ------------------------------------------------------------------


def test_translations_show_under_the_original(tmp_path: Path) -> None:
    path = _db(tmp_path, _m(0, "A", PT))
    with DatabaseManager(path) as db:
        translate_archive(db, Recorder())
        written = render_pages(Renderer(db), tmp_path / "site")
    page = next(p for p in written if p.name.startswith("("))
    text = page.read_text(encoding="utf-8").split('id="chattr-data"')[0]
    assert f'<div class="translation">[en] {PT}</div>' in text and PT in text


def test_a_translation_with_markup_is_escaped(tmp_path: Path) -> None:
    class Hostile:
        def translate(self, text: str) -> Translation:
            return Translation("<script>alert(1)</script>")

    path = _db(tmp_path, _m(0, "A", PT))
    with DatabaseManager(path) as db:
        translate_archive(db, Hostile())
        written = render_pages(Renderer(db), tmp_path / "site")
    page = next(p for p in written if p.name.startswith("("))
    assert (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
        in page.read_text(encoding="utf-8").split('id="chattr-data"')[0]
    )


@pytest.fixture
def server():
    srv = StubServer()
    srv.reply = {
        "choices": [{"message": {"content": "Hello, I arrived."}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    yield srv
    srv.close()


def _archive_with_portuguese(chattr_root: Path) -> None:
    with DatabaseManager(commands.central_database(Settings.load(chattr_root))) as db:
        db.upsert(
            ConversationRecord("WhatsApp", "chat", name="Ana"),
            [_m(0, "Ana", PT), _m(60, "Ana", EN)],
        )
        db.commit()


def test_the_command_translates_through_a_local_server(
    chattr_root: Path, server: StubServer, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_with_portuguese(chattr_root)
    args = [
        "--root",
        str(chattr_root),
        "translate",
        "--endpoint",
        server.endpoint,
        "--model",
        "llama3",
    ]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "1 translated" in out and "7 tokens in" in out
    assert server.requests[0]["json"]["messages"][1]["content"] == PT
    assert (
        _translations(commands.central_database(Settings.load(chattr_root)))[PT][0]
        == "Hello, I arrived."
    )
    assert cli.main(args) == 0 and len(server.requests) == 1  # resumed: nothing left to do


def test_the_command_says_what_is_missing(
    chattr_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_with_portuguese(chattr_root)
    assert cli.main(["--root", str(chattr_root), "translate"]) == 1
    assert "--model" in capsys.readouterr().out


def test_the_command_refuses_a_remote_endpoint(
    chattr_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_with_portuguese(chattr_root)
    args = [
        "--root",
        str(chattr_root),
        "translate",
        "--endpoint",
        "https://api.example.com/v1",
        "--model",
        "m",
    ]
    assert cli.main(args) == 1
    assert "allow_remote" in capsys.readouterr().out


def test_the_command_on_an_empty_archive(
    chattr_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--root", str(chattr_root), "translate", "--model", "m"]) == 0
    assert "Nothing to translate" in capsys.readouterr().out
