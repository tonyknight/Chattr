"""
Chattr Preflight Module

Sets up a Chattr root folder: the directory tree, the default configuration files, and the
`~/.chattr.json` pointer that remembers where the root is.

Rules:
- When a root is given (`--root`, `CHATTR_ROOT`) or stdin isn't a terminal, preflight never
  prompts.
- Repair is non-destructive: missing folders and files are created, and missing default keys
  are added to existing JSON files, but existing values (user preferences and import state) are
  never overwritten.
- An explicit root doesn't replace an existing pointer, so a tool running Chattr with its own
  root (for example Caixa) doesn't change the user's standalone setup.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from chattr.config import resolve_root

# Directory structure to create in the Chattr root
CHATTR_STRUCTURE = {
    "Database": [],
    "Output": [
        "Chattr",
        "Messenger",
        "Instagram",
        "GoogleVoice",
        "Voicemail",
        "WhatsApp",
        "iMessage",
    ],
    "Services": [],
    "temp": [],
}

# Hidden pointer file in the user's home folder
CHATTR_CONFIG_FILE = ".chattr.json"

# Default Chattr root location
DEFAULT_CHATTR_ROOT = "~/Documents/Chattr"

# Current version
CHATTR_VERSION = "2.0.0"

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"


def get_home_directory() -> Path:
    """Get the user's home directory."""
    return Path.home()


def get_chattr_config_path() -> Path:
    """Get the path to the hidden .chattr.json pointer file."""
    return get_home_directory() / CHATTR_CONFIG_FILE


def load_chattr_config() -> Optional[Dict[str, Any]]:
    """Load the .chattr.json pointer, or None if it doesn't exist or can't be read."""
    config_path = get_chattr_config_path()
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Warning: Could not read {config_path}: {e}")
        return None


def save_chattr_config(chattr_root: str) -> bool:
    """Write the .chattr.json pointer to the user's home folder."""
    config_path = get_chattr_config_path()
    config_data = {
        "chattr_root": chattr_root,
        "version": CHATTR_VERSION,
        "last_updated": datetime.now().isoformat(),
    }
    try:
        config_path.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError as e:
        print(f"❌ Error: Could not write {config_path}: {e}")
        return False


def prompt_user_for_location() -> str:
    """Ask the user where to keep Chattr's data; return the expanded path."""
    default_path = os.path.expanduser(DEFAULT_CHATTR_ROOT)
    print("\n" + "=" * 70)
    print("🚀 Welcome to Chattr - Your Message Archive Swiss Army Knife")
    print("=" * 70)
    print("\nFirst-time setup: Please specify where to store Chattr data.")
    print(f"\nDefault location: {default_path}")
    print("Press Enter to use default, or type a custom path:")
    user_input = input("\n📁 Chattr root directory: ").strip()
    return os.path.expanduser(user_input) if user_input else default_path


def create_directory_structure(chattr_root: str) -> bool:
    """Create any missing folders of the Chattr tree."""
    try:
        root_path = Path(chattr_root)
        for main_dir, subdirs in CHATTR_STRUCTURE.items():
            (root_path / main_dir).mkdir(parents=True, exist_ok=True)
            for subdir in subdirs:
                (root_path / main_dir / subdir).mkdir(exist_ok=True)
        return True
    except OSError as e:
        print(f"❌ Error: Could not create directory structure: {e}")
        return False


def merge_missing(existing: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Return `existing` with any keys missing from it added from `defaults` (recursively).

    Existing values always win: preferences and state the user has are never overwritten.
    """
    merged = dict(existing)
    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = default_value
        elif isinstance(merged[key], dict) and isinstance(default_value, dict):
            merged[key] = merge_missing(merged[key], default_value)
    return merged


def _install_json(source: Path, dest: Path) -> None:
    """Copy `source` to `dest` if missing; otherwise add only the keys `dest` lacks."""
    defaults = json.loads(source.read_text(encoding="utf-8"))
    if dest.exists():
        current = json.loads(dest.read_text(encoding="utf-8"))
        merged = merge_missing(current, defaults)
        if merged == current:
            return
        data = merged
    else:
        data = defaults
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_default_configs(chattr_root: str) -> bool:
    """Install the default config.json and Services/*.json non-destructively."""
    try:
        if not DEFAULTS_DIR.exists():
            print(f"❌ Error: Defaults directory not found at {DEFAULTS_DIR}")
            return False
        root_path = Path(chattr_root)
        _install_json(DEFAULTS_DIR / "config.json", root_path / "config.json")
        for service_file in sorted((DEFAULTS_DIR / "Services").glob("*.json")):
            _install_json(service_file, root_path / "Services" / service_file.name)
        return True
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ Error: Could not install configuration files: {e}")
        return False


def verify_chattr_structure(chattr_root: str) -> Tuple[bool, list]:
    """Check that every folder and configuration file of the Chattr root exists."""
    missing = []
    root_path = Path(chattr_root)
    for main_dir, subdirs in CHATTR_STRUCTURE.items():
        for folder in [root_path / main_dir] + [root_path / main_dir / s for s in subdirs]:
            if not folder.exists():
                missing.append(str(folder))
    if not (root_path / "config.json").exists():
        missing.append(str(root_path / "config.json"))
    for service_file in sorted((DEFAULTS_DIR / "Services").glob("*.json")):
        if not (root_path / "Services" / service_file.name).exists():
            missing.append(str(root_path / "Services" / service_file.name))
    return (len(missing) == 0, missing)


def ensure_root(chattr_root: str) -> bool:
    """Create or repair a Chattr root without overwriting anything the user has."""
    return create_directory_structure(chattr_root) and copy_default_configs(chattr_root)


def run_preflight(root: Optional[str] = None, interactive: Optional[bool] = None) -> Optional[str]:
    """Make sure a usable Chattr root exists; return its path, or None if setup failed.

    Args:
        root: An explicit root folder (from `--root`). If omitted, `CHATTR_ROOT` or the
            `~/.chattr.json` pointer is used.
        interactive: Whether the user may be prompted. Defaults to "stdin is a terminal".
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    resolved = resolve_root(root)
    first_time = resolved is None
    if resolved is None:
        if not interactive:
            print(
                "❌ No Chattr root configured. Pass --root, set CHATTR_ROOT, "
                "or run Chattr in a terminal once to set it up."
            )
            return None
        resolved = Path(prompt_user_for_location())

    chattr_root = str(resolved)
    already_complete, _ = verify_chattr_structure(chattr_root)
    is_new = not (Path(chattr_root) / "config.json").exists()
    if not ensure_root(chattr_root):
        return None

    if load_chattr_config() is None and not save_chattr_config(chattr_root):
        return None

    if first_time:
        print("\n✅ Chattr initialized successfully!")
        print(f"📁 Data directory: {chattr_root}")
        print(f"⚙️  Configuration: {get_chattr_config_path()}")
    elif is_new:
        print(f"\n✅ Chattr initialized at: {chattr_root}")
    elif not already_complete:
        print(f"\n✅ Chattr structure repaired at: {chattr_root}")
    return chattr_root
