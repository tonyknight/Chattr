> **Superseded.** This document describes an earlier stage of Chattr (when it was named Chat2HTML or Chatter). For the current design, see the [README](../README.md) and Caixa's `docs/ai/Istanbul/Phase1-Collector/` requirements. Kept for history.

# Configuration Structure v2.0.0

## Visual Hierarchy

```
config.json (v2.0.0)
│
├── 📋 header
│   ├── description
│   ├── version (2.0.0)
│   ├── last_updated
│   └── schema_notes
│
├── 🌍 global
│   ├── general
│   │   ├── output_directory
│   │   ├── temp_directory
│   │   └── log_level
│   │
│   ├── contacts
│   │   ├── vcf_file_path
│   │   ├── phone_region
│   │   ├── enable_contact_resolution
│   │   └── normalize_phone_numbers
│   │
│   ├── performance
│   │   ├── max_workers
│   │   ├── batch_size
│   │   ├── enable_parallel_processing
│   │   └── memory_limit_mb
│   │
│   ├── ai_translation
│   │   ├── enabled
│   │   ├── provider
│   │   ├── api_key_env_var
│   │   ├── model
│   │   ├── max_requests_per_minute
│   │   ├── timeout_seconds
│   │   └── fallback_on_error
│   │
│   └── database
│       ├── unified_schema
│       │   ├── enabled
│       │   ├── file
│       │   └── version
│       └── per_service_exports
│           ├── enabled
│           ├── format
│           └── separate_files
│
├── 📱 services
│   ├── imessage
│   │   ├── database
│   │   │   ├── default_db_path
│   │   │   ├── default_attachments_path
│   │   │   └── read_only
│   │   │
│   │   ├── spam_filtering
│   │   │   ├── enabled
│   │   │   ├── mode
│   │   │   ├── available_modes
│   │   │   └── description
│   │   │
│   │   ├── performance
│   │   │   ├── workers
│   │   │   ├── batch_size
│   │   │   ├── max_workers
│   │   │   └── enable_parallel_processing
│   │   │
│   │   ├── translation
│   │   │   ├── enabled
│   │   │   ├── use_global_ai_translation
│   │   │   ├── skip_duplicates
│   │   │   └── similarity_threshold
│   │   │
│   │   └── export
│   │       ├── json_format
│   │       ├── include_attachments_metadata
│   │       ├── include_tapbacks
│   │       └── include_contact_info
│   │
│   ├── whatsapp
│   │   ├── parsing
│   │   │   ├── encoding
│   │   │   ├── date_format
│   │   │   └── handle_multiline
│   │   │
│   │   ├── media
│   │   │   ├── process_attachments
│   │   │   ├── image_omitted_pattern
│   │   │   └── rename_by_timestamp
│   │   │
│   │   ├── translation
│   │   │   ├── enabled
│   │   │   ├── use_global_ai_translation
│   │   │   ├── skip_duplicates
│   │   │   └── similarity_threshold
│   │   │
│   │   └── export
│   │       └── json_format
│   │
│   └── messenger
│       ├── parsing
│       │   ├── encoding
│       │   ├── reverse_chronological
│       │   └── fix_mojibake
│       │
│       ├── translation
│       │   ├── enabled
│       │   ├── use_global_ai_translation
│       │   ├── skip_similar_messages
│       │   ├── time_window_minutes
│       │   └── similarity_tolerance
│       │
│       └── export
│           ├── json_format
│           └── include_shared_links
│
├── 📎 attachments
│   ├── general
│   │   ├── process_attachments
│   │   ├── enable_deduplication
│   │   ├── preserve_originals
│   │   └── max_attachment_workers
│   │
│   ├── conversion
│   │   ├── enabled
│   │   ├── quality
│   │   ├── supported_formats
│   │   │   ├── images
│   │   │   ├── videos
│   │   │   └── audio
│   │   └── conversion_formats
│   │       ├── .heic → .jpg
│   │       ├── .png → .jpg
│   │       ├── .webp → .jpg
│   │       └── .caf → .aac
│   │
│   └── archival_structure
│       ├── directory_pattern
│       ├── naming_format
│       ├── create_service_folders
│       └── create_date_folders
│
└── 🌐 html_generation
    ├── template
    │   ├── file
    │   ├── theme
    │   └── responsive
    │
    ├── features
    │   ├── calendar_navigation
    │   ├── image_lightbox
    │   ├── search_functionality
    │   └── message_filtering
    │
    └── performance
        ├── lazy_loading
        ├── chunk_large_conversations
        └── max_messages_per_page
```

## Key Design Principles

### 1. Hierarchical Organization

The configuration is organized into logical parent-child relationships:

- **header** - Metadata about the configuration itself
- **global** - Settings that apply across all services
- **services** - Service-specific configurations
- **attachments** - Universal attachment processing
- **html_generation** - HTML output settings

### 2. IDE Folding Support

Each major section can be folded in modern IDEs:

```json
{
  "global": { ... },          // ← Fold this
  "services": { ... },        // ← Fold this
  "attachments": { ... },     // ← Fold this
  "html_generation": { ... }  // ← Fold this
}
```

Within sections:

```json
{
  "services": {
    "imessage": { ... },    // ← Fold individual services
    "whatsapp": { ... },
    "messenger": { ... }
  }
}
```

### 3. Logical Grouping

Related settings are grouped together:

- **global.general** - Basic app settings
- **global.contacts** - Contact resolution (used by all services)
- **global.performance** - Performance tuning
- **global.ai_translation** - AI backend for translation
- **global.database** - Database export settings

### 4. Service Isolation

Each service has its own namespace under `services`:

```
services/
├── imessage/
├── whatsapp/
└── messenger/
```

This prevents naming conflicts and makes it clear which settings apply to which service.

### 5. Universal Attachments

Attachment processing is universal and applies to all services:

```
attachments/
├── general/          # Basic settings
├── conversion/       # Format conversion
└── archival_structure/  # File organization
```

## Translation Workflow Chain

The translation system uses a two-tier approach:

```
┌─────────────────────────────────────┐
│  global.ai_translation              │
│  (AI Backend Configuration)         │
│  - provider: openrouter             │
│  - model: claude-3-haiku            │
│  - api_key_env_var                  │
│  - rate limiting                    │
└─────────────────────────────────────┘
                 ↓
                 ↓ Referenced by
                 ↓
┌─────────────────────────────────────┐
│  services.<service>.translation     │
│  (Service-Specific Rules)           │
│  - enabled: true/false              │
│  - use_global_ai_translation: true  │
│  - skip_duplicates                  │
│  - similarity_threshold             │
└─────────────────────────────────────┘
```

### Example: iMessage Translation

1. **Global AI Backend** (`global.ai_translation`)
   - Provides the AI model and API configuration
   - Shared across all services

2. **Service Translation Rules** (`services.imessage.translation`)
   - Enables/disables translation for this service
   - References global AI backend
   - Adds service-specific rules (duplicate detection, etc.)

## Path Access Patterns

### Dot Notation

Access configuration using dot notation:

```python
# Old (v1.0.0)
config.get("imessage.database.default_db_path")

# New (v2.0.0)
config.get("services.imessage.database.default_db_path")
```

### Common Paths

| Setting | Path |
|---------|------|
| Output directory | `global.general.output_directory` |
| Max workers | `global.performance.max_workers` |
| VCF contacts | `global.contacts.vcf_file_path` |
| iMessage DB | `services.imessage.database.default_db_path` |
| Spam filtering | `services.imessage.spam_filtering.mode` |
| Attachment quality | `attachments.conversion.quality` |
| Archival pattern | `attachments.archival_structure.directory_pattern` |

## Version Migration

### v1.0.0 → v2.0.0 Changes

| v1.0.0 Path | v2.0.0 Path | Notes |
|-------------|-------------|-------|
| `_comment` | `header.description` | Moved to header |
| `_version` | `header.version` | Moved to header |
| `global.*` | `global.general.*` | Nested under general |
| `global.max_workers` | `global.performance.max_workers` | Moved to performance |
| `global.batch_size` | `global.performance.batch_size` | Moved to performance |
| `imessage.*` | `services.imessage.*` | Nested under services |
| `whatsapp.*` | `services.whatsapp.*` | Nested under services |
| `messenger.*` | `services.messenger.*` | Nested under services |
| `imessage.contacts` | `global.contacts` | Promoted to global |
| `imessage.performance` | `services.imessage.performance` | Service-specific override |
| `imessage.attachments` | `attachments.general` | Promoted to universal |
| `api.openrouter` | `global.ai_translation` | Renamed and restructured |
| `database` | `global.database` | Moved to global |

## Benefits of v2.0.0 Structure

### 1. **Improved Readability**
- Clear parent-child relationships
- Logical grouping of related settings
- Easy to navigate in IDEs with folding

### 2. **Better Scalability**
- Easy to add new services under `services/`
- Universal settings in `global/` and `attachments/`
- No naming conflicts between services

### 3. **Maintainability**
- Version tracking in `header`
- Clear separation of concerns
- Service-specific overrides don't pollute global namespace

### 4. **Flexibility**
- Services can reference global settings
- Translation workflow chain
- Universal attachment processing

### 5. **IDE Support**
- Foldable sections for large configurations
- Clear structure for autocomplete
- Easy to comment out entire sections

## Future Enhancements

### Planned for v2.1.0

1. **Environment Variable Overrides**
   ```json
   {
     "global": {
       "general": {
         "output_directory": "${OUTPUT_DIR:./output}"
       }
     }
   }
   ```

2. **Configuration Profiles**
   ```json
   {
     "profiles": {
       "development": { ... },
       "production": { ... }
     }
   }
   ```

3. **JSON Schema Validation**
   - Validate configuration on load
   - Provide helpful error messages
   - IDE autocomplete support

4. **Configuration Inheritance**
   ```json
   {
     "services": {
       "imessage": {
         "extends": "global.performance"
       }
     }
   }
   ```

## Conclusion

The v2.0.0 configuration structure provides a solid foundation for Chat2HTML's growth. The hierarchical organization, IDE folding support, and clear separation of concerns make it easy to manage complex configurations while maintaining readability and maintainability.

