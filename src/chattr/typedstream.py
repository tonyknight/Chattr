"""Read the text out of an iMessage `attributedBody` (an NSAttributedString "typedstream").

`message.attributedBody` holds the message text whenever `message.text` is empty, as it is for
most messages on recent macOS. The blob is Apple's typedstream archive. Only the string payload
matters here, so this reads exactly that and nothing else:

    streamtyped header … NSAttributedString … NSString <class tail> '+' <length> <UTF-8 bytes>

The length is typedstream's integer encoding: one byte when below 0x80, otherwise a tag byte
(0x81: 2 bytes, 0x82: 4 bytes, 0x83: 8 bytes), little-endian. The length is checked against the
blob, so a damaged or cut-off stream gives None, never a partial message.
"""

from __future__ import annotations

from typing import Optional

HEADER = b"streamtyped"
STRING_CLASS = b"NSString"
#: In a typedstream, `\x84\x01+` introduces the raw bytes of a C string ('+' is its type code).
STRING_BYTES_MARKER = b"\x84\x01+"
_TAGGED_LENGTHS = {0x81: 2, 0x82: 4, 0x83: 8}


def _read_length(blob: bytes, position: int) -> Optional[tuple[int, int]]:
    """The integer at `position` and where it ends, or None if it doesn't fit."""
    if position >= len(blob):
        return None
    first = blob[position]
    if first < 0x80:
        return first, position + 1
    size = _TAGGED_LENGTHS.get(first)
    if size is None or position + 1 + size > len(blob):
        return None
    return int.from_bytes(blob[position + 1 : position + 1 + size], "little"), position + 1 + size


def attributed_text(blob: Optional[bytes]) -> Optional[str]:
    """The message text held in an `attributedBody` blob, or None if it can't be read whole."""
    if not blob or HEADER not in blob[:16]:
        return None
    start = blob.find(STRING_CLASS)
    if start < 0:
        return None
    marker = blob.find(STRING_BYTES_MARKER, start + len(STRING_CLASS))
    if marker < 0:
        return None
    parsed = _read_length(blob, marker + len(STRING_BYTES_MARKER))
    if parsed is None:
        return None
    length, begin = parsed
    end = begin + length
    if end > len(blob):
        return None
    try:
        return blob[begin:end].decode("utf-8")
    except UnicodeDecodeError:
        return None
