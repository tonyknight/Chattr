"""
Chatter Preflight Module
Handles first-run setup, directory structure creation, and configuration initialization.
"""

import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple


# Directory structure to create in user's Chatter folder
CHATTER_STRUCTURE = {
    "Database": [],
    "Output": [
        "Chatter",
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
CHATTER_CONFIG_FILE = ".chatter.json"

# Default Chatter root location
DEFAULT_CHATTER_ROOT = "~/Documents/Chatter"

# Current version
CHATTER_VERSION = "2.0.0"


def get_home_directory() -> Path:
    """Get the user's home directory."""
    return Path.home()


def get_chatter_config_path() -> Path:
    """Get the path to the hidden .chatter.json config file."""
    return get_home_directory() / CHATTER_CONFIG_FILE


def load_chatter_config() -> Optional[Dict]:
    """
    Load the .chatter.json configuration file from user's home directory.
    
    Returns:
        Dict with config data if file exists, None otherwise
    """
    config_path = get_chatter_config_path()
    
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️  Warning: Could not read {config_path}: {e}")
        return None


def save_chatter_config(chatter_root: str) -> bool:
    """
    Save the .chatter.json configuration file to user's home directory.
    
    Args:
        chatter_root: Path to the Chatter root directory
        
    Returns:
        True if successful, False otherwise
    """
    config_path = get_chatter_config_path()
    
    config_data = {
        "chatter_root": chatter_root,
        "version": CHATTER_VERSION,
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
    Prompt the user for the Chatter root directory location.
    
    Returns:
        Expanded path to the Chatter root directory
    """
    default_path = os.path.expanduser(DEFAULT_CHATTER_ROOT)
    
    print("\n" + "="*70)
    print("🚀 Welcome to Chatter - Your Message Archive Swiss Army Knife")
    print("="*70)
    print("\nFirst-time setup: Please specify where to store Chatter data.")
    print(f"\nDefault location: {default_path}")
    print("Press Enter to use default, or type a custom path:")
    
    user_input = input("\n📁 Chatter root directory: ").strip()
    
    if not user_input:
        return default_path
    
    return os.path.expanduser(user_input)


def create_directory_structure(chatter_root: str) -> bool:
    """
    Create the Chatter directory structure.
    
    Args:
        chatter_root: Path to the Chatter root directory
        
    Returns:
        True if successful, False otherwise
    """
    try:
        root_path = Path(chatter_root)
        
        # Create root directory
        root_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        for main_dir, subdirs in CHATTER_STRUCTURE.items():
            main_path = root_path / main_dir
            main_path.mkdir(exist_ok=True)
            
            for subdir in subdirs:
                sub_path = main_path / subdir
                sub_path.mkdir(exist_ok=True)
        
        return True
    except OSError as e:
        print(f"❌ Error: Could not create directory structure: {e}")
        return False


def copy_default_configs(chatter_root: str) -> bool:
    """
    Copy default configuration files from Defaults/ to user's Chatter directory.
    
    Args:
        chatter_root: Path to the Chatter root directory
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get the script's directory (repository root)
        defaults_dir = Path(__file__).resolve().parent / "defaults"
        
        if not defaults_dir.exists():
            print(f"❌ Error: Defaults directory not found at {defaults_dir}")
            return False
        
        chatter_path = Path(chatter_root)
        
        # Copy main config.json
        config_source = defaults_dir / "config.json"
        config_dest = chatter_path / "config.json"
        
        if config_source.exists():
            # Read the config and replace {CHATTER_ROOT} placeholder
            with open(config_source, 'r') as f:
                config_data = json.load(f)
            
            # Replace placeholders with actual path
            config_str = json.dumps(config_data, indent=2)
            config_str = config_str.replace("{CHATTER_ROOT}", chatter_root)
            config_data = json.loads(config_str)
            
            # Write to destination
            with open(config_dest, 'w') as f:
                json.dump(config_data, f, indent=2)
        
        # Copy service configs
        services_source = defaults_dir / "Services"
        services_dest = chatter_path / "Services"
        
        if services_source.exists():
            for service_file in services_source.glob("*.json"):
                dest_file = services_dest / service_file.name
                shutil.copy2(service_file, dest_file)
        
        return True
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"❌ Error: Could not copy configuration files: {e}")
        return False


def verify_chatter_structure(chatter_root: str) -> Tuple[bool, list]:
    """
    Verify that the Chatter directory structure is complete.
    
    Args:
        chatter_root: Path to the Chatter root directory
        
    Returns:
        Tuple of (is_valid, missing_items)
    """
    missing = []
    root_path = Path(chatter_root)
    
    # Check main directories
    for main_dir in CHATTER_STRUCTURE.keys():
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
        Path to Chatter root directory if successful, None otherwise
    """
    # Check for existing config
    existing_config = load_chatter_config()
    
    if existing_config:
        chatter_root = existing_config.get("chatter_root")
        
        if chatter_root:
            # Verify the structure is intact
            is_valid, missing = verify_chatter_structure(chatter_root)
            
            if is_valid:
                # Silent success - structure exists and is valid
                return chatter_root
            else:
                print(f"\n⚠️  Warning: Chatter structure at {chatter_root} is incomplete.")
                print(f"Missing items: {len(missing)}")
                
                response = input("\nWould you like to repair the structure? (y/n): ").strip().lower()
                
                if response == 'y':
                    if create_directory_structure(chatter_root):
                        if copy_default_configs(chatter_root):
                            print(f"\n✅ Chatter structure repaired at: {chatter_root}")
                            return chatter_root
                
                # If repair failed or declined, fall through to new setup
    
    # No existing config or repair failed - run first-time setup
    chatter_root = prompt_user_for_location()
    
    print(f"\n📦 Creating Chatter structure at: {chatter_root}")
    
    # Create directory structure
    if not create_directory_structure(chatter_root):
        return None
    
    # Copy default configs
    if not copy_default_configs(chatter_root):
        return None
    
    # Save config to home directory
    if not save_chatter_config(chatter_root):
        return None
    
    print(f"\n✅ Chatter initialized successfully!")
    print(f"📁 Data directory: {chatter_root}")
    print(f"⚙️  Configuration: {get_chatter_config_path()}")
    
    return chatter_root

