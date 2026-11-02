> **Superseded.** This document describes an earlier stage of Chattr (when it was named Chat2HTML or Chatter). For the current design, see the [README](../README.md) and Caixa's `docs/ai/Istanbul/Phase1-Collector/` requirements. Kept for history.

# Chat2HTML Configuration Guide

## Overview

The `config.json` file provides centralized configuration for all Chat2HTML services. It uses a hierarchical structure optimized for IDE folding and readability.

**Current Version:** 2.0.0  
**Last Updated:** 2025-01-29

## Configuration Structure

The configuration is organized into four main sections:

```
config.json
├── header          # Metadata about the configuration file
├── global          # Settings that apply across all services
├── services        # Service-specific configurations
├── attachments     # Universal attachment processing settings
└── html_generation # HTML output configuration
```

## Version Management

The configuration file includes a `header` section with version tracking:

```json
{
  "header": {
    "description": "Chat2HTML Configuration File - Unified settings for all supported messaging services",
    "version": "2.0.0",
    "last_updated": "2025-01-29",
    "schema_notes": "Hierarchical structure optimized for IDE folding and readability"
  }
}
```

**Version History:**
- **2.0.0** - Hierarchical reorganization with `global`, `services`, and `attachments` parent objects
- **1.0.0** - Initial flat structure

**Important:** Increment the version string each time you make changes to the format of the configuration objects.

## Global Configuration

The `global` section contains settings that apply across all services and can be referenced by service-specific configurations.

### Structure

```json
{
  "global": {
    "general": { ... },      // General application settings
    "contacts": { ... },     // Contact resolution settings
    "performance": { ... },  // Performance tuning
    "ai_translation": { ... }, // AI translation settings
    "database": { ... }      // Database export settings
  }
}
```

### Global > General

Basic application settings:

```json
{
  "general": {
    "output_directory": "./output",  // Where to save processed files
    "temp_directory": "./temp",      // Temporary file storage
    "log_level": "INFO"              // Logging verbosity: DEBUG, INFO, WARNING, ERROR
  }
}
```

### Global > Contacts

Contact resolution settings (used by all services):

```json
{
  "contacts": {
    "vcf_file_path": "/path/to/contacts.vcf",  // Path to VCF contacts file
    "phone_region": "US",                       // Default phone number region
    "enable_contact_resolution": true,          // Enable contact name lookup
    "normalize_phone_numbers": true             // Normalize phone number formats
  }
}
```

### Global > Performance

Performance tuning settings:

```json
{
  "performance": {
    "max_workers": 4,                    // Maximum parallel workers
    "batch_size": 1000,                  // Messages per batch
    "enable_parallel_processing": true,  // Enable multi-threading
    "memory_limit_mb": null              // Memory limit (null = unlimited)
  }
}
```

### Global > AI Translation

AI-powered translation settings (used by service-specific translation workflows):

```json
{
  "ai_translation": {
    "enabled": false,                           // Enable AI translation
    "provider": "openrouter",                   // API provider
    "api_key_env_var": "OPENROUTER_API_KEY",   // Environment variable for API key
    "model": "anthropic/claude-3-haiku",       // Model to use
    "max_requests_per_minute": 60,             // Rate limiting
    "timeout_seconds": 30,                     // Request timeout
    "fallback_on_error": true                  // Continue on translation errors
  }
}
```

### Global > Database

Database export settings:

```json
{
  "database": {
    "unified_schema": {
      "enabled": false,                    // Enable unified database schema
      "file": "output/chat_archive.db",   // Database file path
      "version": "1.0"                    // Schema version
    },
    "per_service_exports": {
      "enabled": true,                    // Export each service separately
      "format": "json",                   // Export format
      "separate_files": true              // One file per conversation
    }
  }
}
```

## Services Configuration

The `services` section contains service-specific settings. Each service is a child object under `services`.

### Structure

```json
{
  "services": {
    "imessage": { ... },   // iMessage configuration
    "whatsapp": { ... },   // WhatsApp configuration
    "messenger": { ... }   // Facebook Messenger configuration
  }
}
```

### Services > iMessage

iMessage-specific configuration:

```json
{
  "imessage": {
    "database": {
      "default_db_path": "~/Library/Messages/chat.db",
      "default_attachments_path": "~/Library/Messages/Attachments",
      "read_only": true
    },
    
    "spam_filtering": {
      "enabled": true,
      "mode": "aggressive",
      "available_modes": ["aggressive", "conservative", "disabled"],
      "description": {
        "aggressive": "99.887% text extraction rate - removes RCS, unknown senders, unread system messages",
        "conservative": "99.572% text extraction rate - removes only RCS and unknown senders",
        "disabled": "99.424% text extraction rate - no filtering applied"
      }
    },
    
    "performance": {
      "workers": 4,
      "batch_size": 1000,
      "max_workers": 8,
      "enable_parallel_processing": true
    },
    
    "translation": {
      "enabled": false,
      "use_global_ai_translation": true,  // Use global.ai_translation settings
      "skip_duplicates": true,
      "similarity_threshold": 0.8
    },
    
    "export": {
      "json_format": "imessage_native",
      "include_attachments_metadata": true,
      "include_tapbacks": true,
      "include_contact_info": true
    }
  }
}
```

### Services > WhatsApp

WhatsApp-specific configuration:

```json
{
  "whatsapp": {
    "parsing": {
      "encoding": "utf-8",
      "date_format": "%Y-%m-%d, %H:%M:%S",
      "handle_multiline": true
    },
    
    "media": {
      "process_attachments": true,
      "image_omitted_pattern": "image omitted",
      "rename_by_timestamp": true
    },
    
    "translation": {
      "enabled": false,
      "use_global_ai_translation": true,
      "skip_duplicates": true,
      "similarity_threshold": 0.8
    },
    
    "export": {
      "json_format": "whatsapp_native"
    }
  }
}
```

### Services > Messenger

Facebook Messenger-specific configuration:

```json
{
  "messenger": {
    "parsing": {
      "encoding": "utf-8",
      "reverse_chronological": true,
      "fix_mojibake": true
    },
    
    "translation": {
      "enabled": false,
      "use_global_ai_translation": true,
      "skip_similar_messages": true,
      "time_window_minutes": 5,
      "similarity_tolerance": 0.2
    },
    
    "export": {
      "json_format": "messenger_native",
      "include_shared_links": true
    }
  }
}
```

## Attachments Configuration

The `attachments` section contains universal attachment processing settings that apply to all services.

### Structure

```json
{
  "attachments": {
    "general": { ... },           // General attachment settings
    "conversion": { ... },        // Format conversion settings
    "archival_structure": { ... } // File organization settings
  }
}
```

### Attachments > General

General attachment processing settings:

```json
{
  "general": {
    "process_attachments": true,      // Enable attachment processing
    "enable_deduplication": true,     // Deduplicate files by SHA-256 hash
    "preserve_originals": true,       // Keep original files
    "max_attachment_workers": 4       // Parallel attachment processors
  }
}
```

### Attachments > Conversion

Format conversion settings:

```json
{
  "conversion": {
    "enabled": true,
    "quality": 85,  // JPEG quality (1-100)
    
    "supported_formats": {
      "images": [".heic", ".png", ".webp", ".jpg", ".jpeg", ".gif", ".tiff", ".tif", ".bmp"],
      "videos": [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".webm"],
      "audio": [".m4a", ".caf", ".mp3", ".wav", ".aac", ".flac"]
    },
    
    "conversion_formats": {
      ".heic": ".jpg",  // Convert HEIC to JPG
      ".png": ".jpg",   // Convert PNG to JPG
      ".webp": ".jpg",  // Convert WebP to JPG
      ".caf": ".aac"    // Convert CAF to AAC
    }
  }
}
```

### Attachments > Archival Structure

File organization and naming:

```json
{
  "archival_structure": {
    "directory_pattern": "/Attachments/{service}/{year}/{month_number} - {month_name}/",
    "naming_format": "({date}) {time} [{id}].{ext}",
    "create_service_folders": true,
    "create_date_folders": true
  }
}
```

**Available placeholders:**
- `{service}` - Service name (imessage, whatsapp, messenger)
- `{year}` - Four-digit year (2025)
- `{month_number}` - Two-digit month (01-12)
- `{month_name}` - Full month name (January)
- `{date}` - Date in YYYY-MM-DD format
- `{time}` - Time in HH-MM-SS format
- `{id}` - Unique message/attachment ID
- `{ext}` - File extension

## HTML Generation Configuration

Settings for HTML output generation:

```json
{
  "html_generation": {
    "template": {
      "file": "templates/template.html",
      "theme": "nord",
      "responsive": true
    },
    
    "features": {
      "calendar_navigation": true,
      "image_lightbox": true,
      "search_functionality": false,
      "message_filtering": false
    },
    
    "performance": {
      "lazy_loading": false,
      "chunk_large_conversations": false,
      "max_messages_per_page": 10000
    }
  }
}
```

## Translation Workflow Chain

The translation system uses a workflow chain combining global and service-specific settings:

1. **Global AI Translation** (`global.ai_translation`) - Provides the AI backend
2. **Service Translation** (`services.<service>.translation`) - Service-specific translation rules

### Example Workflow

For iMessage translation:

```json
{
  "global": {
    "ai_translation": {
      "enabled": false,
      "provider": "openrouter",
      "model": "anthropic/claude-3-haiku"
    }
  },
  
  "services": {
    "imessage": {
      "translation": {
        "enabled": false,
        "use_global_ai_translation": true,  // References global.ai_translation
        "skip_duplicates": true,
        "similarity_threshold": 0.8
      }
    }
  }
}
```

To enable translation:
1. Set `global.ai_translation.enabled = true`
2. Set `services.imessage.translation.enabled = true`
3. Ensure `OPENROUTER_API_KEY` environment variable is set

## Accessing Configuration in Code

### Using ConfigManager

```python
from src.config import get_config

config = get_config()

# Access using dot notation
db_path = config.get("services.imessage.database.default_db_path")
spam_mode = config.get("services.imessage.spam_filtering.mode")

# Get service-specific config
imessage_config = config.get_service_config("imessage")

# Get attachments config
attachments_config = config.get_attachments_config()
```

### Convenience Functions

```python
from src.config import (
    get_imessage_db_path,
    get_spam_filtering_mode,
    get_max_workers,
    get_output_directory,
    get_vcf_file_path,
    get_attachments_config
)

db_path = get_imessage_db_path()
mode = get_spam_filtering_mode()
workers = get_max_workers()
```

## Best Practices

### 1. IDE Folding

The hierarchical structure is designed for IDE folding. In VS Code, you can:
- Fold entire sections (global, services, attachments)
- Fold individual services
- Fold subsections within services

### 2. Version Management

Always increment the version when making structural changes:

```json
{
  "header": {
    "version": "2.1.0",  // Increment for changes
    "last_updated": "2025-01-30"
  }
}
```

### 3. Service-Specific JSON Formats

Each service maintains its own native JSON export format until a unified schema is defined:

- `imessage_native` - iMessage-specific format with tapbacks, attributedBody, etc.
- `whatsapp_native` - WhatsApp-specific format
- `messenger_native` - Messenger-specific format

### 4. Path Expansion

All paths support:
- `~` for user home directory
- Environment variables: `$HOME`, `${VAR_NAME}`

Example:
```json
{
  "vcf_file_path": "~/Desktop/contacts.vcf"
}
```

### 5. Comments

While JSON doesn't support comments, use the `header.schema_notes` field for documentation:

```json
{
  "header": {
    "schema_notes": "Added new translation workflow chain in v2.0.0"
  }
}
```

## Migration from v1.0.0

If you have a v1.0.0 configuration, update paths as follows:

| Old Path (v1.0.0) | New Path (v2.0.0) |
|-------------------|-------------------|
| `global.output_directory` | `global.general.output_directory` |
| `global.max_workers` | `global.performance.max_workers` |
| `imessage.database` | `services.imessage.database` |
| `imessage.spam_filtering` | `services.imessage.spam_filtering` |
| `api.openrouter` | `global.ai_translation` |
| `database` | `global.database` |

## Troubleshooting

### Configuration Not Loading

```python
from src.config import get_config, ConfigurationError

try:
    config = get_config()
except ConfigurationError as e:
    print(f"Error: {e}")
```

### Invalid JSON

Use a JSON validator or:

```bash
python -m json.tool config.json
```

### Path Not Expanding

Check that paths use proper format:
- ✅ `~/Library/Messages/chat.db`
- ✅ `$HOME/Documents/file.txt`
- ❌ `~knight/Documents/file.txt` (use `~` alone)

## Future Enhancements

Planned for future versions:

1. **Unified Schema** - Single JSON format for all services
2. **Configuration Validation** - JSON Schema validation
3. **Configuration UI** - Web-based configuration editor
4. **Environment Overrides** - Override config with environment variables
5. **Profile Support** - Multiple configuration profiles (dev, prod, etc.)

