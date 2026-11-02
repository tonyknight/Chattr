> **Developed in [Caixa](https://github.com/tonyknight/caixa).** This repository is a mirror of the
> `tools/chattr/` folder there: its history is published from Caixa as a fast-forward. Please send
> changes, issues and pull requests to Caixa, not here.

# 💬 Chattr

Chattr converts message exports into readable, durable archives. It reads the export formats of
messaging services, stores conversations in a SQLite database, and generates styled HTML pages
you can open in any browser.

Chattr is developed as part of [Caixa](https://github.com/tonyknight/Caixa), an open-source
digital-legacy project, and works on its own.

> **Status: in active development.** Everything below works and is tested against synthetic
> exports (a fictional family; no real data). Real exports vary, so if one of yours does not read
> correctly, that is a bug worth reporting to the Caixa project.

## What works today

Seven kinds of export go into **one SQLite archive** with stable message IDs, so running an import
again adds only what is new:

| Service | Reads |
| --- | --- |
| **WhatsApp** | an exported chat (`_chat.txt` or “WhatsApp Chat with …”), with its media |
| **Facebook Messenger** and **Instagram** | “Download your information” JSON, one conversation file at a time |
| **iMessage** | the macOS `chat.db`, opened read-only, with attachments, tapbacks and spam tagging |
| **Mail** | `.mbox` files (a Gmail Takeout), with attachments |
| **Google Voice** | a Takeout folder of calls, texts and voicemail |
| **iPhone voicemail** | a backup folder with `voicemail.db`, with optional local transcription |

From the archive Chattr can:

- write **people-first pages**: one folder per person (and per group) with a page for each year,
  mail by year, and an index with a calendar. The pages carry their own data, so
  `chattr-cli rebuild` can recreate the database from them;
- **name people** from a contacts file (`.vcf`), a names file (aliases) and the owner's name;
- optionally **translate** with a local, OpenAI-compatible model, and **transcribe** voicemail with
  a local speech model. Both are off by default and never download anything;
- run everything from a **small web page** (`chattr-cli serve`, below) or from the command line.

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

Every menu action is also a command:

```bash
chattr-cli convert whatsapp _chat.txt --owner "Your Name" --html
chattr-cli people --contacts contacts.vcf       # who is still unnamed
chattr-cli html                                 # write the pages again
chattr-cli rebuild                              # recreate the database from the pages
chattr-cli translate --endpoint URL --model NAME
chattr-cli chats ~/Library/Messages/chat.db     # iMessage tools: chats, doctor
chattr-cli serve                                # the web page
```

### The web page: `chattr-cli serve`

```bash
chattr-cli serve              # prints a link and opens it; Control-C stops it
chattr-cli serve --no-browser --port 8765
```

A small page on your own computer for people who would rather click than type: choose where the
messages come from, read how to export them, point at the file, set the options, watch it work,
then open the pages. It is built from the same functions as the command line (the form becomes the
arguments `chattr-cli convert` takes), needs only Python and Chattr, and never contacts the
internet. It listens on `127.0.0.1` only; the link it prints carries a one-time token, and other
programs and websites on your computer cannot use it.

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
