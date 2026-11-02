#!/usr/bin/env python3
"""
Configuration Manager for Chat2HTML

This module provides centralized configuration management for all Chat2HTML services.
It loads default settings from config.json and allows runtime overrides.

Features:
- JSON-based configuration with comments and documentation
- Service-specific settings with inheritance from global defaults
- Runtime configuration overrides
- Environment variable integration
- Validation and error handling
"""

import json
import os
from typing import Dict, Any, Optional, Union
from pathlib import Path


class ConfigurationError(Exception):
    """Raised when there's an error with configuration loading or validation"""
    pass


class ConfigManager:
    """
    Centralized configuration manager for Chat2HTML
    
    Handles loading, validation, and access to configuration settings
    across all supported messaging services.
    """
    
    def __init__(self, config_file: str = "config.json"):
        """
        Initialize configuration manager
        
        Args:
            config_file: Path to the JSON configuration file
        """
        self.config_file = config_file
        self.config = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from JSON file"""
        try:
            if not os.path.exists(self.config_file):
                raise ConfigurationError(f"Configuration file not found: {self.config_file}")
            
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
                
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in configuration file: {e}")
        except Exception as e:
            raise ConfigurationError(f"Error loading configuration: {e}")
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation
        
        Args:
            key_path: Dot-separated path to configuration key (e.g., "imessage.spam_filtering.enabled")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
            
        Examples:
            config.get("imessage.spam_filtering.enabled")
            config.get("global.max_workers", 4)
        """
        keys = key_path.split('.')
        value = self.config
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_service_config(self, service: str) -> Dict[str, Any]:
        """
        Get complete configuration for a specific service

        Args:
            service: Service name (e.g., "imessage", "whatsapp", "messenger")

        Returns:
            Dictionary containing all configuration for the service
        """
        service_config = self.get(f"services.{service}", {})
        if not service_config:
            raise ConfigurationError(f"No configuration found for service: {service}")

        # Merge with global settings where applicable
        global_config = self.get("global", {})

        # Add global settings that aren't overridden by service
        merged_config = service_config.copy()
        merged_config['_global'] = global_config  # Add global config for reference

        return merged_config
    
    def get_imessage_config(self) -> Dict[str, Any]:
        """Get iMessage-specific configuration with defaults applied"""
        return self.get_service_config("imessage")
    
    def get_whatsapp_config(self) -> Dict[str, Any]:
        """Get WhatsApp-specific configuration with defaults applied"""
        return self.get_service_config("whatsapp")
    
    def get_messenger_config(self) -> Dict[str, Any]:
        """Get Messenger-specific configuration with defaults applied"""
        return self.get_service_config("messenger")
    
    def get_html_config(self) -> Dict[str, Any]:
        """Get HTML generation configuration"""
        return self.get("html_generation", {})
    
    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration"""
        return self.get("global.database", {})

    def get_attachments_config(self) -> Dict[str, Any]:
        """Get attachments configuration"""
        return self.get("attachments", {})

    def get_global_config(self) -> Dict[str, Any]:
        """Get global configuration"""
        return self.get("global", {})

    def get_version(self) -> str:
        """Get configuration version"""
        return self.get("header.version", "unknown")
    
    def set(self, key_path: str, value: Any):
        """
        Set configuration value using dot notation
        
        Args:
            key_path: Dot-separated path to configuration key
            value: Value to set
        """
        keys = key_path.split('.')
        config_ref = self.config
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in config_ref:
                config_ref[key] = {}
            config_ref = config_ref[key]
        
        # Set the final value
        config_ref[keys[-1]] = value
    
    def save_config(self, output_file: Optional[str] = None):
        """
        Save current configuration to file
        
        Args:
            output_file: Optional different file to save to
        """
        file_path = output_file or self.config_file
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ConfigurationError(f"Error saving configuration: {e}")
    
    def expand_path(self, path: str) -> str:
        """
        Expand path with user home directory and environment variables
        
        Args:
            path: Path that may contain ~ or environment variables
            
        Returns:
            Expanded absolute path
        """
        if not path:
            return path
            
        # Expand user home directory
        expanded = os.path.expanduser(path)
        
        # Expand environment variables
        expanded = os.path.expandvars(expanded)
        
        return expanded
    
    def get_expanded_path(self, key_path: str, default: str = "") -> str:
        """
        Get a path configuration value and expand it
        
        Args:
            key_path: Dot-separated path to configuration key
            default: Default path if key not found
            
        Returns:
            Expanded absolute path
        """
        path = self.get(key_path, default)
        return self.expand_path(path)
    
    def validate_service_config(self, service: str) -> bool:
        """
        Validate configuration for a specific service
        
        Args:
            service: Service name to validate
            
        Returns:
            True if configuration is valid
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        config = self.get_service_config(service)
        
        if service == "imessage":
            # Validate iMessage-specific requirements
            db_path = self.get_expanded_path("services.imessage.database.default_db_path")
            if not db_path:
                raise ConfigurationError("iMessage database path not configured")

            spam_mode = self.get("services.imessage.spam_filtering.mode")
            valid_modes = self.get("services.imessage.spam_filtering.available_modes", [])
            if spam_mode not in valid_modes:
                raise ConfigurationError(f"Invalid spam filtering mode: {spam_mode}")

        return True


# Global configuration instance
_config_manager = None

def get_config() -> ConfigManager:
    """
    Get the global configuration manager instance
    
    Returns:
        ConfigManager instance
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager

def reload_config(config_file: str = "config.json"):
    """
    Reload configuration from file
    
    Args:
        config_file: Path to configuration file
    """
    global _config_manager
    _config_manager = ConfigManager(config_file)


# Convenience functions for common configuration access
def get_imessage_db_path() -> str:
    """Get expanded iMessage database path"""
    return get_config().get_expanded_path("services.imessage.database.default_db_path")

def get_spam_filtering_mode() -> str:
    """Get spam filtering mode for iMessage"""
    return get_config().get("services.imessage.spam_filtering.mode", "aggressive")

def get_max_workers() -> int:
    """Get maximum number of worker threads"""
    return get_config().get("global.performance.max_workers", 4)

def get_output_directory() -> str:
    """Get expanded output directory path"""
    return get_config().get_expanded_path("global.general.output_directory", "./output")

def get_vcf_file_path() -> str:
    """Get expanded VCF contacts file path"""
    return get_config().get_expanded_path("global.contacts.vcf_file_path", "")

def get_attachments_config() -> Dict[str, Any]:
    """Get attachments configuration"""
    return get_config().get("attachments", {})


if __name__ == "__main__":
    # Test configuration loading
    try:
        config = get_config()
        print("Configuration loaded successfully!")
        print(f"iMessage DB path: {get_imessage_db_path()}")
        print(f"Spam filtering: {get_spam_filtering_mode()}")
        print(f"Max workers: {get_max_workers()}")
        print(f"Output directory: {get_output_directory()}")
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
