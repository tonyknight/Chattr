# 💬 Chatter - Your Message Archive Swiss Army Knife

Chatter is a unified message archive conversion framework that transforms chat logs from multiple messaging services (WhatsApp, Facebook Messenger, iMessage, Google Voice, Voicemail) into standardized formats (JSON, SQLite, HTML). It features industry-leading iMessage extraction rates (99.887%), intelligent spam filtering, professional attachment processing, and flexible output options for both one-time exports and regular incremental updates.

## ✨ Key Features

### 🎯 Multi-Service Support
- **iMessage**: Direct SQLite database access with 99.887% text extraction rate
- **WhatsApp**: Text export parsing with media handling
- **Facebook Messenger**: JSON export processing with mojibake fixing
- **Google Voice**: Call logs and voicemail integration (planned)
- **Voicemail**: Audio transcription support (planned)

### 🚀 Industry-Leading iMessage Processing
- **99.887% Text Extraction Rate**: Advanced typedstream parsing from attributedBody fields
- **Intelligent Spam Filtering**: Removes RCS, unknown senders, and system messages (512+ low-value messages)
- **VCF Contact Integration**: Phone number normalization and contact name resolution
- **Parallel Processing**: Multi-threaded message processing with configurable workers

### 📎 Professional Attachment Pipeline
- **Format Conversion**: HEIC→JPG, CAF→AAC with EXIF preservation
- **SHA-256 Deduplication**: Intelligent file hashing with symlink creation
- **Archival Organization**: Date-based folder structure with standardized naming
- **Multi-Format Support**: Images, videos, audio, and documents

### 🎨 Flexible Output Options
- **JSON Export**: Service-specific native formats
- **SQLite Database**: Unified multi-service archive (in development)
- **HTML Reports**: Interactive chat UI with Nord dark theme
- **Incremental Updates**: State tracking for regular imports without duplicates

### 🛠️ Smart Configuration
- **Automatic Setup**: First-run preflight creates directory structure at `~/Documents/Chatter`
- **Service-Specific Configs**: Individual JSON files for each messaging service
- **User Data Separation**: Clean separation between repository code and user data

## 📋 Prerequisites

- **Python 3.8+** - [Download](https://www.python.org/downloads/)
- **macOS** (for iMessage support) - Other platforms supported for WhatsApp/Messenger
- **Optional**: ExifTool for advanced EXIF metadata handling

## 🚀 Quick Start

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/tonyknight/Chatter.git
    cd Chatter
    ```

2.  **Create a Virtual Environment**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(On Windows: `venv\Scripts\activate`)*

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run Chatter**:
    ```bash
    python main.py
    ```

    On first run, Chatter will:
    - Prompt for data storage location (default: `~/Documents/Chatter`)
    - Create the directory structure automatically
    - Copy default configuration files
    - Save preferences to `~/.chatter.json`

## 📁 Directory Structure

After first run, Chatter creates the following structure in your data directory:

```
📦 ~/Documents/Chatter
 ┣ 📂 Database
 ┃ ┗ 📜 chatter.sqlite          # Unified multi-service database
 ┣ 📂 Output
 ┃ ┣ 📂 Chatter                 # HTML outputs from central database
 ┃ ┣ 📂 iMessage                # iMessage-specific exports
 ┃ ┣ 📂 WhatsApp                # WhatsApp-specific exports
 ┃ ┣ 📂 Messenger               # Messenger-specific exports
 ┃ ┣ 📂 GoogleVoice             # Google Voice exports
 ┃ ┗ 📂 Voicemail               # Voicemail exports
 ┣ 📂 Services
 ┃ ┣ 📜 iMessage.json           # iMessage preferences & state
 ┃ ┣ 📜 WhatsApp.json           # WhatsApp preferences & state
 ┃ ┣ 📜 Messenger.json          # Messenger preferences & state
 ┃ ┣ 📜 GoogleVoice.json        # Google Voice preferences & state
 ┃ ┗ 📜 Voicemail.json          # Voicemail preferences & state
 ┣ 📂 temp                      # Temporary processing files
 ┗ 📜 config.json               # Global application settings
```

## 🎯 Usage

### Processing iMessage

1. Select **"3. Process iMessage Export"** from the main menu
2. Choose from:
   - **Quick Process**: Use default settings for fast export
   - **Advanced Options**: Customize database path, spam filtering, attachments
   - **List Chats**: View all available conversations
   - **Diagnostics & Tools**: Database health checks and analysis

### Processing WhatsApp

1. Export your WhatsApp chat (Settings → Chats → Export Chat)
2. Select **"1. Import WhatsApp Chats"** from the main menu
3. Provide the path to your exported chat file

### Processing Messenger

1. Download your Facebook data (Settings → Your Facebook Information → Download Your Information)
2. Select **"2. Import Facebook Messenger Chats"** from the main menu
3. Provide the path to your Messenger JSON files

## ⚙️ Configuration

### Global Settings (`config.json`)
- Output directories
- Performance settings (workers, batch size)
- AI translation configuration
- Database settings

### Service-Specific Settings (`Services/*.json`)
Each service has its own configuration file with:
- **Preferences**: Service-specific parsing and export options
- **State**: Last import date, message counts, tracking data

### Example: iMessage Configuration

```json
{
  "preferences": {
    "spam_filtering": {
      "enabled": true,
      "mode": "aggressive"
    },
    "attachments": {
      "process_attachments": true,
      "conversion_quality": 85
    }
  },
  "state": {
    "last_import_date": "2025-11-11T10:30:00",
    "last_message_rowid": 125847
  }
}
```

## 🔧 Advanced Features

### Incremental Updates
Chatter tracks the last imported message for each service, allowing you to update your archive with only new messages without re-importing everything.

### Spam Filtering (iMessage)
- **Aggressive Mode**: 99.887% extraction rate - removes RCS, unknown senders, unread system messages
- **Conservative Mode**: 99.572% extraction rate - removes only RCS and unknown senders
- **Disabled**: 99.424% extraction rate - no filtering

### Attachment Processing
- Automatic format conversion (HEIC→JPG, CAF→AAC)
- SHA-256 deduplication with symlinks
- EXIF metadata preservation
- Date-based archival organization

## 🛠️ Development

### Repository Structure
```
📦 Chatter (Repository)
 ┣ 📂 Defaults                  # Default config templates
 ┃ ┣ 📜 config.json
 ┃ ┗ 📂 Services
 ┃   ┗ 📜 *.json
 ┣ 📂 src
 ┃ ┣ 📜 preflight.py            # First-run setup
 ┃ ┣ 📜 config.py               # Configuration manager
 ┃ ┣ 📂 parsers
 ┃ ┃ ┣ 📜 imessage_database_parser.py
 ┃ ┃ ┣ 📜 whatsapp_parser.py
 ┃ ┃ ┗ 📜 messenger_parser.py
 ┃ ┣ 📜 attachment_manager.py
 ┃ ┣ 📜 database_manager.py
 ┃ ┗ 📜 html_generator.py
 ┣ 📜 main.py                   # Main orchestrator
 ┣ 📜 requirements.txt
 ┗ 📜 README.md
```

## 📝 API Key Setup (for Translation)

If you enable AI translation, set your OpenRouter API key:

**macOS/Linux**:
```bash
export OPENROUTER_API_KEY="your_api_key_here"
```

**Windows**:
```bash
set OPENROUTER_API_KEY="your_api_key_here"
```

Or create an `API_Key.env` file in the repository root:
```
OPENROUTER_API_KEY=your_api_key_here
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

Chattr is free software, licensed under the **GNU General Public License, version 3 or later
(GPL-3.0-or-later)**. See [`LICENSE`](LICENSE) for the full text and [`NOTICE`](NOTICE) for
copyright and attribution.

## 🙏 Acknowledgments

- Parts of Chattr's iMessage support (the diagnostics tool, the message query structure and
  tapback target parsing) are derived from
  [imessage-exporter](https://github.com/ReagentX/imessage-exporter) by Christopher Sardegna
  (GPL-3.0-or-later), and its code informed Chattr's understanding of the iMessage database. See
  [`NOTICE`](NOTICE).
- Nord theme for beautiful dark mode UI
- OpenRouter for AI translation capabilities

## 📞 Support

For issues, questions, or feature requests, please open an issue on GitHub.

---

**Made with ❤️ for preserving your digital conversations**

