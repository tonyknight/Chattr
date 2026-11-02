# 💬 Chattr

Chattr converts message exports into readable, durable archives. It reads the export formats of
messaging services, stores conversations in a SQLite database, and generates styled HTML pages
you can open in any browser.

Chattr is developed as part of [Caixa](https://github.com/tonyknight/Caixa), an open-source
digital-legacy project, and works on its own.

> **Status: in active development.** The table below shows what works today.

## What works today

| Service | Status |
| --- | --- |
| **WhatsApp** (iOS export, `_chat.txt`) | Imports into the database; HTML generation works. Android/locale formats and media handling are in progress. |
| **Facebook Messenger** (Download Your Information JSON) | Imports into the database, one thread file at a time; attachments and shared links in progress. |
| **iMessage** (macOS `chat.db`) | Reads the database directly (read-only) and exports JSON from the menu; database import in progress. |
| **Email (MBOX)**, **Google Voice**, **iPhone Voicemail** | Planned for this development phase. |

Planned next:
- one unified database schema for every service;
- repeated imports that add only new messages;
- people-first HTML (all conversations with one person together, named by date).

See the project requirements in Caixa:
`docs/ai/Istanbul/Phase1-Collector/`.

## Install

Requires **Python 3.10+**. For attachment conversion, also install **FFmpeg** and **ExifTool** with
your system's package manager.

```bash
pip install .            # from this folder (or: pip install -e tools/chattr from Caixa)
```

For development:

```bash
pip install -e ".[dev]"  # adds pytest, black, mypy
```

## Use

```bash
chattr-cli                   # interactive menu; first run asks where to keep Chattr's data
chattr-cli --root ~/Chattr   # use this data folder, without prompting
chattr-cli --version
python -m chattr             # same as chattr-cli
```

The command is `chattr-cli`, not `chattr`: on Linux, `chattr` is a standard system command.

## Where Chattr keeps its data

On first run Chattr creates a **Chattr root** (default `~/Documents/Chattr`). It records the
location in `~/.chattr.json`. The `--root` option or the `CHATTR_ROOT` environment variable
override it.

```
Chattr/
  config.json          ← global settings
  Services/            ← one file per service: preferences + import state
    iMessage.json  WhatsApp.json  Messenger.json  GoogleVoice.json  Voicemail.json
  Database/            ← chattr.sqlite (the central archive)
  Output/              ← generated files: Output/Chattr (all services) and one folder per service
  temp/
```

- **Settings** come from `config.json` merged with each `Services/<Service>.json`. Values may use
  the `{CHATTR_ROOT}` placeholder, which is replaced when settings load.
- **Each service file** has `preferences` (how to import) and `state` (what was imported last),
  for repeated imports.
- **Running Chattr again repairs a damaged root without overwriting anything.** Missing folders
  and files are restored, and new default settings are added, but your preferences and import
  state are kept.

## Development

```bash
python -m pytest tests -q
```

Tests use a synthetic, fictional family of exports (see
[`tests/fixtures/README.md`](tests/fixtures/README.md)). Never commit real personal data.

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
- Nord theme for the HTML archive design.
