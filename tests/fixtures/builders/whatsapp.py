"""Synthetic WhatsApp "Export chat" folders in three formats.

- iOS: `[YYYY-MM-DD, HH:MM:SS] Name: text`, with U+200E marks before attachment lines, attachment
  lines `<attached: NNNNNNNN-PHOTO-YYYY-MM-DD-HH-MM-SS.ext>`, and `image omitted` when media
  wasn't included.
- Android (day/month locale, 24-hour): `DD/MM/YYYY, HH:MM - Name: text`, attachment lines
  `IMG-YYYYMMDD-WA0001.ext (file attached)`, and `<Media omitted>`.
- US locale (month/day, 12-hour): `M/D/YY, H:MM AM - Name: text`.

Each export is a folder holding `_chat.txt` and any included media.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fixtures import family
from fixtures.builders.media import png

LRM = "‎"  # left-to-right mark, used by iOS exports
NNBSP = " "  # narrow no-break space before AM/PM in newer exports


def _ios_stamp(moment: datetime) -> str:
    return moment.strftime("[%Y-%m-%d, %H:%M:%S]")


def _android_stamp(moment: datetime) -> str:
    return moment.strftime("%d/%m/%Y, %H:%M")


def _us_stamp(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{moment.month}/{moment.day}/{moment:%y}, {hour}:{moment:%M}{NNBSP}{suffix}"


def _build_ios(out: Path) -> Path:
    folder = out / "WhatsApp Chat - Ana Costa"
    folder.mkdir(parents=True, exist_ok=True)
    ana, me = family.ANA.name, family.OWNER.name
    photo_time = family.at(2024, 3, 1, 14, 5, 9)
    photo = f"00000012-PHOTO-{photo_time:%Y-%m-%d-%H-%M-%S}.png"
    (folder / photo).write_bytes(png(rgb=(30, 160, 90)))
    lines = [
        f"{_ios_stamp(family.at(2024, 3, 1, 14, 0, 0))} {ana}: {LRM}Messages and calls are end-to-end encrypted.",
        f"{_ios_stamp(family.at(2024, 3, 1, 14, 2, 30))} {ana}: Mãe, look what I found at the market",
        f"{LRM}{_ios_stamp(photo_time)} {ana}: {LRM}<attached: {photo}>",
        f"{_ios_stamp(family.at(2024, 3, 1, 14, 6, 0))} {me}: Que lindo! Buy two.",
        f"{_ios_stamp(family.at(2024, 3, 1, 14, 7, 45))} {ana}: Shopping list for Sunday:",
        "  - bacalhau",
        "  - azeite",
        "  - pastéis de nata",
        f"{LRM}{_ios_stamp(family.at(2024, 3, 1, 14, 9, 0))} {me}: {LRM}image omitted",
        f"{_ios_stamp(family.at(2024, 3, 2, 8, 30, 12))} {ana}: Bom dia ☀️",
    ]
    (folder / "_chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def _build_android(out: Path) -> Path:
    folder = out / "WhatsApp Chat with Rui Pereira"
    folder.mkdir(parents=True, exist_ok=True)
    rui, me = family.RUI.name, family.OWNER.name
    lisbon = family.TZ_LISBON
    image = "IMG-20240415-WA0001.png"
    (folder / image).write_bytes(png(rgb=(220, 200, 40)))
    lines = [
        f"{_android_stamp(family.at(2024, 4, 15, 10, 0, tz=lisbon))} - Messages and calls are end-to-end encrypted.",
        f"{_android_stamp(family.at(2024, 4, 15, 10, 1, tz=lisbon))} - {rui}: Olá Maria! Are you coming to the reunion?",
        f"{_android_stamp(family.at(2024, 4, 15, 10, 3, tz=lisbon))} - {me}: Of course. Who else is coming?",
        f"{_android_stamp(family.at(2024, 4, 15, 10, 4, tz=lisbon))} - {rui}: Everyone from the old street:",
        "  Teresa, Paulo and the twins",
        f"{_android_stamp(family.at(2024, 4, 15, 10, 6, tz=lisbon))} - {rui}: {image} (file attached)",
        f"{_android_stamp(family.at(2024, 4, 15, 10, 7, tz=lisbon))} - {me}: <Media omitted>",
        f"{_android_stamp(family.at(2024, 4, 16, 9, 15, tz=lisbon))} - {rui}: See you on the 20th",
    ]
    (folder / "_chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def _build_us(out: Path) -> Path:
    folder = out / "WhatsApp Chat with Ana Costa (US)"
    folder.mkdir(parents=True, exist_ok=True)
    ana, me = family.ANA.name, family.OWNER.name
    lines = [
        f"{_us_stamp(family.at(2025, 2, 9, 9, 5))} - {ana}: Flight lands at 3:40",
        f"{_us_stamp(family.at(2025, 2, 9, 9, 7))} - {me}: I'll be there. Arrivals, door 2.",
        f"{_us_stamp(family.at(2025, 2, 9, 15, 52))} - {ana}: Landed! Bags are slow:",
        "  carousel 7",
        f"{_us_stamp(family.at(2025, 2, 9, 16, 10))} - {me}: Parked. Take your time.",
    ]
    (folder / "_chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def build_whatsapp(out_dir: Path) -> dict[str, Path]:
    """Write the three export folders into `out_dir`; return them keyed by variant."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "ios": _build_ios(out_dir),
        "android": _build_android(out_dir),
        "us": _build_us(out_dir),
    }
