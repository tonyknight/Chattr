# Synthetic fixture family

Every test uses this fictional family instead of real messages. **Never add real personal data.**

```python
from fixtures import build_all
exports = build_all(tmp_path)          # or use the `fixture_family` pytest fixture
exports.paths["imessage"]              # → .../imessage/chat.db
```

`build_all` is deterministic: two builds produce byte-identical files (SQLite files have identical
logical contents). The builders use the Python standard library only.

## The family

| Person | Role | Platforms |
| --- | --- | --- |
| Maria Costa | archive owner | all |
| Ana Costa | daughter | iMessage, WhatsApp (iOS and US locale), Google Voice, voicemail, email |
| João Costa | brother | iMessage group, Messenger, email, voicemail |
| Rui Pereira | friend | WhatsApp (Android), Instagram |
| "Costa Family" | group chat | iMessage (Maria, Ana, João) |
| +1 555-0199 | unknown number | iMessage, Google Voice (missed call) |

Phone numbers use the US 555-0100–0199 range reserved for fiction; email addresses use the
reserved `example.com` / `example.org` domains. Data lives in `family.py`.

## Format assumptions (verify against real exports before release)

These describe what each builder writes. They follow the platforms' export formats as publicly
documented or commonly observed. **Check them against real exports before release**, because
platforms change their formats without notice.

| Export | Builder | Assumptions |
| --- | --- | --- |
| vCard | `builders/vcard.py` | vCard 3.0, CRLF line endings, `FN`, `N`, `TEL;TYPE=CELL`, `EMAIL;TYPE=INTERNET`; the owner's own card is not included. |
| iMessage | `builders/imessage.py` | Subset of macOS `~/Library/Messages/chat.db`: `handle`, `chat`, `chat_handle_join`, `message`, `chat_message_join`, `attachment`, `message_attachment_join`. `date` counts from 2001-01-01 UTC, in seconds (old databases) or nanoseconds. Group chats have `style` 43, one-to-one 45. Tapbacks use `associated_message_type` 2000–2005 (added) / 3000–3005 (removed) with `p:<part>/<guid>` or `bp:<guid>`. `attributedBody` is a minimal NSAttributedString typedstream (integers over 127 use a `0x81` tag plus 2 bytes little-endian). Attachment `filename` values start `~/Library/Messages/Attachments/`; files are written under `Attachments/`. |
| WhatsApp | `builders/whatsapp.py` | Folder with `_chat.txt` + media. iOS: `[YYYY-MM-DD, HH:MM:SS] Name: …`, U+200E before attachment lines, `<attached: NNNNNNNN-PHOTO-…>`, `image omitted`. Android (day/month, 24 h): `DD/MM/YYYY, HH:MM - Name: …`, `… (file attached)`, `<Media omitted>`. US: `M/D/YY, H:MM AM - …` with U+202F before AM/PM. Continuation lines are kept as written. |
| Messenger / Instagram | `builders/meta.py` | "Download Your Information" JSON: `your_facebook_activity/messages/inbox/<thread>/message_1.json` and `your_instagram_activity/messages/inbox/<thread>/message_1.json`. Messages newest first; `timestamp_ms`; `photos[].uri` relative to the export root; `share.link` / `share.share_text`; `reactions[]`. Every text value is double-encoded (UTF-8 bytes read as latin-1). |
| Email (MBOX) | `builders/mbox.py` | Gmail Takeout `Takeout/Mail/All mail Including Spam and Trash.mbox`. RFC 2047 encoded non-ASCII headers; `X-GM-THRID`, `X-Gmail-Labels`; threads via `Message-ID` / `In-Reply-To` / `References`. |
| Google Voice | `builders/voice.py` | Takeout `Voice/Calls/<Name or number> - <Text\|Received\|Placed\|Missed\|Voicemail> - <YYYY-MM-DDTHH_MM_SSZ>.html`. Texts: `hChatLog hfeed` → `message` → `abbr.dt[title=ISO]`, `cite.sender.vcard` → `a.tel` → `span.fn`, `<q>`. Calls: `haudio`, `abbr.published`, `abbr.duration[title=ISO 8601]`, `contributor vcard`. Voicemails add `span.description` → `span.full-text` and `<audio src>`. |
| iPhone voicemail | `builders/voice.py` | Device backup `voicemail.db`, table `voicemail` (`date` Unix seconds, `sender`, `duration` seconds, …); audio `<ROWID>.amr`. |

**Known simplifications**
- **Audio** is short PCM WAV data whatever the file extension (`.mp3`, `.amr`): the standard
  library can't encode MP3 or AMR. Parsers must not trust extensions; speech-to-text tests use a
  stub.
- **Images** are tiny PNGs and a 1×1 GIF. There is no HEIC sample, because the standard library
  can't produce one; the attachment-pipeline ticket needs its own HEIC test asset.
