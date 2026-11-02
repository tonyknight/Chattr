"""The synthetic WhatsApp exports cover iOS, Android and US-locale formats."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures import family
from fixtures.builders.whatsapp import build_whatsapp

IOS_LINE = re.compile(r"^‎?\[\d{4}-\d{2}-\d{2}, \d{2}:\d{2}:\d{2}\] [^:]+: ")
ANDROID_LINE = re.compile(r"^\d{2}/\d{2}/\d{4}, \d{2}:\d{2} - [^:]+: ")
US_LINE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} ?\s?[AP]M - [^:]+: ")


@pytest.fixture(scope="module")
def exports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_whatsapp(tmp_path_factory.mktemp("whatsapp"))


def _lines(path: Path) -> list[str]:
    return (path / "_chat.txt").read_text(encoding="utf-8").splitlines()


def test_three_variants_are_built(exports: dict[str, Path]) -> None:
    assert set(exports) == {"ios", "android", "us"}
    for folder in exports.values():
        assert (folder / "_chat.txt").is_file()


def test_ios_format_with_marks_attachments_and_omitted_media(exports: dict[str, Path]) -> None:
    lines = _lines(exports["ios"])
    assert sum(bool(IOS_LINE.match(line)) for line in lines) >= 5
    assert any(line.startswith("‎[") for line in lines)
    attached = [line for line in lines if "<attached: " in line]
    assert attached
    for line in attached:
        name = line.split("<attached: ", 1)[1].rstrip(">")
        assert (exports["ios"] / name).is_file(), name
    assert any(line.endswith("image omitted") for line in lines)


def test_android_format_with_file_attached_and_media_omitted(exports: dict[str, Path]) -> None:
    lines = _lines(exports["android"])
    assert sum(bool(ANDROID_LINE.match(line)) for line in lines) >= 5
    attached = [line for line in lines if line.endswith(" (file attached)")]
    assert attached
    for line in attached:
        name = line.rsplit(": ", 1)[1].removesuffix(" (file attached)")
        assert (exports["android"] / name).is_file(), name
    assert any(line.endswith("<Media omitted>") for line in lines)


def test_us_locale_uses_month_first_and_twelve_hour_time(exports: dict[str, Path]) -> None:
    lines = _lines(exports["us"])
    assert sum(bool(US_LINE.match(line)) for line in lines) >= 3


def test_every_variant_has_an_indented_continuation_line(exports: dict[str, Path]) -> None:
    for name, folder in exports.items():
        lines = _lines(folder)
        assert any(line.startswith("  ") for line in lines), name


def test_participants_are_the_family(exports: dict[str, Path]) -> None:
    ios = "\n".join(_lines(exports["ios"]))
    android = "\n".join(_lines(exports["android"]))
    assert family.ANA.name in ios and family.OWNER.name in ios
    assert family.RUI.name in android
