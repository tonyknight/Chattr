"""
Chattr Preflight Module
Handles first-run setup, directory structure creation, and configuration initialization.
"""

import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple


# Directory structure to create in user's Chattr folder
CHATTR_STRUCTURE = {
    "Database": [],
    "Output": [
        "Chattr",
        "Messenger", 
        "GoogleVoice",
        "Voicemail",
        "WhatsApp",
        "iMessage"
    ],
    "Services": [],
    "temp": []
}

# Hidden config file in user's home directory
CHATTR_CONFIG_FILE = ".chattr.json"

# Default Chattr root location
DEFAULT_CHATTR_ROOT = "~/Documents/Chattr"

# Current version
CHATTR_VERSION = "2.0.0"


def get_home_directory() -> Path:
    """Get the user's home directory."""
    return Path.home()


def get_chattr_config_path() -> Path:
    """Get the path to the hidden .chattr.json config file."""
    return get_home_directory() / CHATTR_CONFIG_FILE


def load_chattr_config() -> Optional[Dict]:
    """
    Load the .chattr.json configuration file from user's home directory.
    
    Returns:
        Dict with config data if file exists, None otherwise
    """
    config_path = get_chattr_config_path()
    
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️  Warning: Could not read {config_path}: {e}")
        return None


def save_chattr_config(chattr_root: str) -> bool:
    """
    Save the .chattr.json configuration file to user's home directory.
    
    Args:
        chattr_root: Path to the Chattr root directory
        
    Returns:
        True if successful, False otherwise
    """
    config_path = get_chattr_config_path()
    
    config_data = {
        "chattr_root": chattr_root,
        "version": CHATTR_VERSION,
        "last_updated": datetime.now().isoformat()
    }
    
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        return True
    except IOError as e:
        print(f"❌ Error: Could not write {config_path}: {e}")
        return False


def prompt_user_for_location() -> str:
    """
    Prompt the user for the Chattr root directory location.
    
    Returns:
        Expanded path to the Chattr root directory
    """
    default_path = os.path.expanduser(DEFAULT_CHATTR_ROOT)
    
    print("\n" + "="*70)
    print("🚀 Welcome to Chattr - Your Message Archive Swiss Army Knife")
    print("="*70)
    print("\nFirst-time setup: Please specify where to store Chattr data.")
    print(f"\nDefault location: {default_path}")
    print("Press Enter to use default, or type a custom path:")
    
    user_input = input("\n📁 Chattr root directory: ").strip()
    
    if not user_input:
        return default_path
    
    return os.path.expanduser(user_input)


def create_directory_structure(chattr_root: str) -> bool:
    """
    Create the Chattr directory structure.
    
    Args:
        chattr_root: Path to the Chattr root directory
        
    Returns:
        True if successful, False otherwise
    """
    try:
        root_path = Path(chattr_root)
        
        # Create root directory
        root_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        for main_dir, subdirs in CHATTR_STRUCTURE.items():
            main_path = root_path / main_dir
            main_path.mkdir(exist_ok=True)
            
            for subdir in subdirs:
                sub_path = main_path / subdir
                sub_path.mkdir(exist_ok=True)
        
        return True
    except OSError as e:
        print(f"❌ Error: Could not create directory structure: {e}")
        return False


def copy_default_configs(chattr_root: str) -> bool:
    """
    Copy default configuration files from Defaults/ to user's Chattr directory.
    
    Args:
        chattr_root: Path to the Chattr root directory
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get the script's directory (repository root)
        defaults_dir = Path(__file__).resolve().parent / "defaults"
        
        if not defaults_dir.exists():
            print(f"❌ Error: Defaults directory not found at {defaults_dir}")
            return False
        
        chattr_path = Path(chattr_root)
        
        # Copy main config.json
        config_source = defaults_dir / "config.json"
        config_dest = chattr_path / "config.json"
        
        if config_source.exists():
            # Read the config and replace {CHATTR_ROOT} placeholder
            with open(config_source, 'r') as f:
                config_data = json.load(f)
            
            # Replace placeholders with actual path
            config_str = json.dumps(config_data, indent=2)
            config_str = config_str.replace("{CHATTR_ROOT}", chattr_root)
            config_data = json.loads(config_str)
            
            # Write to destination
            with open(config_dest, 'w') as f:
                json.dump(config_data, f, indent=2)
        
        # Copy service configs
        services_source = defaults_dir / "Services"
        services_dest = chattr_path / "Services"
        
        if services_source.exists():
            for service_file in services_source.glob("*.json"):
                dest_file = services_dest / service_file.name
                shutil.copy2(service_file, dest_file)
        
        return True
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"❌ Error: Could not copy configuration files: {e}")
        return False


def verify_chattr_structure(chattr_root: str) -> Tuple[bool, list]:
    """
    Verify that the Chattr directory structure is complete.
    
    Args:
        chattr_root: Path to the Chattr root directory
        
    Returns:
        Tuple of (is_valid, missing_items)
    """
    missing = []
    root_path = Path(chattr_root)
    
    # Check main directories
    for main_dir in CHATTR_STRUCTURE.keys():
        dir_path = root_path / main_dir
        if not dir_path.exists():
            missing.append(str(dir_path))
    
    # Check config files
    config_file = root_path / "config.json"
    if not config_file.exists():
        missing.append(str(config_file))
    
    services_dir = root_path / "Services"
    expected_services = ["iMessage.json", "WhatsApp.json", "Messenger.json", 
                        "GoogleVoice.json", "Voicemail.json"]
    for service in expected_services:
        service_file = services_dir / service
        if not service_file.exists():
            missing.append(str(service_file))
    
    return (len(missing) == 0, missing)


def run_preflight() -> Optional[str]:
    """
    Main preflight function - checks for existing config or sets up new installation.
    
    Returns:
        Path to Chattr root directory if successful, None otherwise
    """
    # Check for existing config
    existing_config = load_chattr_config()
    
    if existing_config:
        chattr_root = existing_config.get("chattr_root")
        
        if chattr_root:
            # Verify the structure is intact
            is_valid, missing = verify_chattr_structure(chattr_root)
            
            if is_valid:
                # Silent success - structure exists and is valid
                return chattr_root
            else:
                print(f"\n⚠️  Warning: Chattr structure at {chattr_root} is incomplete.")
                print(f"Missing items: {len(missing)}")
                
                response = input("\nWould you like to repair the structure? (y/n): ").strip().lower()
                
                if response == 'y':
                    if create_directory_structure(chattr_root):
                        if copy_default_configs(chattr_root):
                            print(f"\n✅ Chattr structure repaired at: {chattr_root}")
                            return chattr_root
                
                # If repair failed or declined, fall through to new setup
    
    # No existing config or repair failed - run first-time setup
    chattr_root = prompt_user_for_location()
    
    print(f"\n📦 Creating Chattr structure at: {chattr_root}")
    
    # Create directory structure
    if not create_directory_structure(chattr_root):
        return None
    
    # Copy default configs
    if not copy_default_configs(chattr_root):
        return None
    
    # Save config to home directory
    if not save_chattr_config(chattr_root):
        return None
    
    print(f"\n✅ Chattr initialized successfully!")
    print(f"📁 Data directory: {chattr_root}")
    print(f"⚙️  Configuration: {get_chattr_config_path()}")
    
    return chattr_root

