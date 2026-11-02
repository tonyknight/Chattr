"""Chattr configuration, loaded from the Chattr root folder.

The root (set up by preflight) holds:
- `config.json`: global settings (`header`, `global`);
- `Services/<Service>.json`: per-service `header`, `preferences` and `state`.

`Settings.load(root)` merges them into one view addressed with dot paths:
`global.general.output_directory`, `services.imessage.preferences.spam_filtering.mode`,
`services.whatsapp.state.last_export_file`, …

The `{CHATTR_ROOT}` placeholder in setting values is replaced at load time. Files on disk keep the
placeholder, so the root can move without rewriting them.

Root resolution order: an explicit argument (`--root`), then the `CHATTR_ROOT` environment
variable, then the `chattr_root` recorded in `~/.chattr.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

POINTER_FILE = ".chattr.json"
ROOT_ENV_VAR = "CHATTR_ROOT"
PLACEHOLDER = "{CHATTR_ROOT}"


class ConfigurationError(Exception):
    """Raised when configuration can't be found, read or validated."""


def resolve_root(cli_root: Optional[str] = None) -> Optional[Path]:
    """Find the Chattr root: argument, then environment, then the home-folder pointer."""
    if cli_root:
        return Path(os.path.expanduser(cli_root))
    env_root = os.environ.get(ROOT_ENV_VAR)
    if env_root:
        return Path(os.path.expanduser(env_root))
    pointer = Path.home() / POINTER_FILE
    if pointer.is_file():
        try:
            recorded = json.loads(pointer.read_text(encoding="utf-8")).get("chattr_root")
        except (OSError, json.JSONDecodeError):
            return None
        if recorded:
            return Path(os.path.expanduser(recorded))
    return None


def _substitute(value: Any, root: str) -> Any:
    """Replace the root placeholder in every string inside `value`."""
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, root)
    if isinstance(value, dict):
        return {key: _substitute(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, root) for item in value]
    return value


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Expected a JSON object in {path}")
    return data


class Settings:
    """The merged configuration of one Chattr root."""

    def __init__(self, root: Path, data: Dict[str, Any], service_files: Dict[str, Path]):
        self.root = root
        self.data = data
        self._service_files = service_files

    @classmethod
    def load(cls, root: Path) -> "Settings":
        root = Path(root)
        global_config = _read_json(root / "config.json")
        services: Dict[str, Any] = {}
        files: Dict[str, Path] = {}
        services_dir = root / "Services"
        if services_dir.is_dir():
            for path in sorted(services_dir.glob("*.json")):
                key = path.stem.lower()
                services[key] = _read_json(path)
                files[key] = path
        merged = {**global_config, "services": services}
        return cls(root, _substitute(merged, str(root)), files)

    def get(self, key_path: str, default: Any = None) -> Any:
        """Look up a dot-separated path; return `default` when any part is missing."""
        value: Any = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def services(self) -> list[str]:
        return sorted(self.data.get("services", {}))

    def service(self, name: str) -> Dict[str, Any]:
        found = self.get(f"services.{name.lower()}")
        if not isinstance(found, dict):
            raise ConfigurationError(f"No configuration found for service: {name}")
        return found

    def save_state(self, name: str, updates: Dict[str, Any]) -> None:
        """Merge `updates` into a service's state and write only that section back to disk."""
        key = name.lower()
        path = self._service_files.get(key)
        if path is None:
            raise ConfigurationError(f"No configuration file for service: {name}")
        on_disk = _read_json(path)
        state = {**on_disk.get("state", {}), **updates}
        on_disk["state"] = state
        path.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.data["services"][key]["state"] = _substitute(state, str(self.root))

    @staticmethod
    def expand_path(path: str) -> str:
        """Expand `~` and environment variables in a path setting."""
        if not path:
            return path
        return os.path.expandvars(os.path.expanduser(path))

    def get_expanded_path(self, key_path: str, default: str = "") -> str:
        return self.expand_path(self.get(key_path, default))


# --- Compatibility layer used by the CLI ------------------------------------------------------

ConfigManager = Settings

_settings: Optional[Settings] = None


def load_settings(cli_root: Optional[str] = None) -> Settings:
    """Load (and remember) the settings for the resolved Chattr root."""
    global _settings
    root = resolve_root(cli_root)
    if root is None:
        raise ConfigurationError(
            "No Chattr root found. Run Chattr once to set it up, pass --root, "
            f"or set {ROOT_ENV_VAR}."
        )
    _settings = Settings.load(root)
    return _settings


def get_config() -> Settings:
    """The settings loaded by `load_settings` (loaded on first use if needed)."""
    return _settings if _settings is not None else load_settings()
