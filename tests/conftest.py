"""Shared pytest setup for Chattr's tests.

The synthetic fixture family lives in `tests/fixtures/` (standard library only). This conftest
makes that package importable as `fixtures` under pytest's importlib import mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures import FamilyExports, build_all, family  # noqa: E402  (needs the path above)


@pytest.fixture(scope="session")
def fixture_family(tmp_path_factory: pytest.TempPathFactory) -> FamilyExports:
    """Every synthetic export, built once per test session."""
    return build_all(tmp_path_factory.mktemp("family"))


@pytest.fixture
def chattr_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh Chattr root, with HOME isolated so the real ~/.chattr.json is never touched."""
    from chattr import preflight

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHATTR_ROOT", raising=False)
    root = tmp_path / "Chattr"
    assert preflight.run_preflight(str(root), interactive=False)
    return root


@dataclass
class FullArchive:
    """Every source fixture imported into one Chattr root, with contacts and the owner set."""

    root: Path
    settings: Any
    database: Path
    media: Path  # where the file-based services' attachments were archived


@pytest.fixture(scope="session")
def full_archive(
    tmp_path_factory: pytest.TempPathFactory, fixture_family: FamilyExports
) -> Iterator[FullArchive]:
    """The whole fixture family in one archive (built once; treat it as read-only)."""
    import json

    from chattr import commands, preflight
    from chattr.config import Settings
    from chattr.transcription import StubTranscriber

    base = tmp_path_factory.mktemp("full-archive")
    patch = pytest.MonkeyPatch()
    home = base / "home"
    home.mkdir()
    patch.setenv("HOME", str(home))
    patch.delenv("CHATTR_ROOT", raising=False)
    try:
        root = base / "Chattr"
        assert preflight.run_preflight(str(root), interactive=False)
        config = root / "config.json"
        data = json.loads(config.read_text())
        owner = family.OWNER
        data["global"]["owner"] = {
            "name": owner.name,
            "identifiers": [*owner.phones, *owner.emails, owner.usernames["instagram"]],
        }
        data["global"]["contacts"]["vcf_file_path"] = str(fixture_family.paths["contacts"])
        config.write_text(json.dumps(data))
        settings = Settings.load(root)
        media = root / "Output" / "Chattr" / "Messages"
        paths = fixture_family.paths
        db_path = paths["imessage"]
        commands.convert_imessage(
            settings,
            db_path=db_path,
            attachment_root=db_path.parent / "Attachments",
            spam_mode="disabled",
            workers=1,
        )
        for folder in ("WhatsApp Chat - Ana Costa", "WhatsApp Chat with Rui Pereira"):
            export = paths["whatsapp"] / folder / "_chat.txt"
            commands.convert(settings, "whatsapp", export, attachments_dir=media)
        commands.convert(
            settings,
            "whatsapp",
            paths["whatsapp"] / "WhatsApp Chat with Ana Costa (US)" / "_chat.txt",
        )
        commands.convert(settings, "messenger", paths["messenger"], attachments_dir=media)
        commands.convert(settings, "instagram", paths["instagram"], attachments_dir=media)
        commands.convert(settings, "mail", paths["mbox"])
        commands.convert(settings, "googlevoice", paths["google_voice"])
        commands.convert(
            settings,
            "voicemail",
            paths["iphone_voicemail"],
            transcriber=StubTranscriber(
                {"1.amr": "Feliz Natal, mana!", "2.amr": "Hi Mum, call me back."}
            ),
        )
        aliases = base / "aliases.json"  # linking the Instagram username is a human's job
        aliases.write_text(json.dumps({family.RUI.usernames["instagram"]: family.RUI.name}))
        commands.people(settings, aliases=aliases)
        yield FullArchive(root, settings, commands.central_database(settings), media)
    finally:
        patch.undo()
