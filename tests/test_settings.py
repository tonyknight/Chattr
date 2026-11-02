"""Settings are loaded from the Chattr root: global config.json plus Services/*.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import chattr
from chattr.config import ConfigurationError, Settings, resolve_root

DEFAULTS = Path(chattr.__file__).resolve().parent / "defaults"


def make_root(path: Path) -> Path:
    """A Chattr root holding the packaged defaults (as preflight would copy them)."""
    (path / "Services").mkdir(parents=True)
    shutil.copy(DEFAULTS / "config.json", path / "config.json")
    for service in (DEFAULTS / "Services").glob("*.json"):
        shutil.copy(service, path / "Services" / service.name)
    return path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHATTR_ROOT", raising=False)
    return home


def test_root_resolution_prefers_argument_then_environment_then_pointer(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer_root, env_root, arg_root = tmp_path / "p", tmp_path / "e", tmp_path / "a"
    assert resolve_root() is None
    (home / ".chattr.json").write_text(json.dumps({"chattr_root": str(pointer_root)}))
    assert resolve_root() == pointer_root
    monkeypatch.setenv("CHATTR_ROOT", str(env_root))
    assert resolve_root() == env_root
    assert resolve_root(str(arg_root)) == arg_root


def test_global_and_service_settings_are_merged(home: Path, tmp_path: Path) -> None:
    root = make_root(tmp_path / "root")
    settings = Settings.load(root)
    assert settings.get("global.general.output_directory") == f"{root}/Output"
    assert settings.get("global.database.unified_schema.file") == f"{root}/Database/chattr.sqlite"
    assert settings.get("services.imessage.preferences.spam_filtering.mode") == "aggressive"
    assert settings.get("services.whatsapp.state.total_messages_imported") == 0
    assert set(settings.services()) == {"imessage", "whatsapp", "messenger", "googlevoice", "voicemail"}
    assert settings.get("services.nope.preferences.x", "fallback") == "fallback"


def test_placeholder_substitution_survives_awkward_paths(home: Path, tmp_path: Path) -> None:
    root = make_root(tmp_path / 'we"ird\\root')
    settings = Settings.load(root)
    assert settings.get("global.general.temp_directory") == f"{root}/temp"


def test_state_is_saved_back_without_touching_preferences(home: Path, tmp_path: Path) -> None:
    root = make_root(tmp_path / "root")
    service_file = root / "Services" / "iMessage.json"
    before = json.loads(service_file.read_text())
    settings = Settings.load(root)
    settings.save_state("imessage", {"last_message_rowid": 42, "total_messages_imported": 7})
    after = json.loads(service_file.read_text())
    assert after["state"]["last_message_rowid"] == 42
    # Everything except the state section is written back exactly as it was on disk.
    assert {k: v for k, v in after.items() if k != "state"} == {
        k: v for k, v in before.items() if k != "state"
    }
    reloaded = Settings.load(root)
    assert reloaded.get("services.imessage.state.total_messages_imported") == 7


def test_missing_root_is_a_clear_error(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="config.json"):
        Settings.load(tmp_path / "empty")
