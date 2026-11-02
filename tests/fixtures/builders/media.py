"""Tiny, valid, deterministic media files built with the standard library only."""

from __future__ import annotations

import io
import math
import struct
import wave
import zlib

#: A classic 1x1 transparent GIF (43 bytes).
GIF_1X1 = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def png(width: int = 2, height: int = 2, rgb: tuple[int, int, int] = (200, 120, 60)) -> bytes:
    """A small solid-colour RGB PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    pixels = zlib.compress(row * height, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def wav(seconds: float = 0.5, rate: int = 8000, hz: float = 440.0) -> bytes:
    """A short mono 16-bit PCM WAV tone."""
    frames = int(seconds * rate)
    samples = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(frames)
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples)
    return buffer.getvalue()
