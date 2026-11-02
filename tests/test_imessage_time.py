"""iMessage dates: seconds and nanoseconds since 2001-01-01 UTC both give the right UTC moment."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import apple_time, commands
from chattr.config import Settings
from chattr.parsers.imessage_database_parser import iMessageDatabaseParser


def test_both_epochs_are_the_same_instant() -> None:
    moment = family.at(2016, 3, 12, 9, 15)
    seconds = int((moment - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds())
    assert apple_time.to_utc(seconds) == moment
    assert apple_time.to_utc(seconds * 1_000_000_000) == moment


def test_the_result_is_timezone_aware_utc() -> None:
    moment = apple_time.to_utc(700_000_000)
    assert moment is not None and moment.utcoffset() == timezone.utc.utcoffset(None)


@pytest.mark.parametrize("unset", [None, 0])
def test_unset_dates_are_none(unset: int | None) -> None:
    assert apple_time.to_utc(unset) is None


def test_fractional_seconds_survive_in_nanoseconds() -> None:
    moment = apple_time.to_utc(700_000_000 * 1_000_000_000 + 250_000_000)
    assert moment is not None and moment.microsecond == 250_000


def test_old_database_messages_are_not_dated_2001(fixture_family: FamilyExports) -> None:
    reader = iMessageDatabaseParser(db_path=str(fixture_family.paths["imessage"]))
    try:
        by_text = {m.text: m for m in reader.get_messages_with_fallback() if m.text}
    finally:
        reader.close()
    assert by_text["Bom dia Mãe! Landed safely."].timestamp == family.at(2016, 3, 12, 9, 15)
    assert by_text["Two of the best ones"].timestamp == family.at(2023, 6, 4, 18, 41)


def test_parallel_and_single_paths_agree_on_dates(fixture_family: FamilyExports) -> None:
    reader = iMessageDatabaseParser(db_path=str(fixture_family.paths["imessage"]))
    try:
        single = {m.guid: m.timestamp for m in reader.get_messages_with_fallback()}
        parallel = {m.guid: m.timestamp for m in reader.get_all_messages_parallel(max_workers=1)}
    finally:
        reader.close()
    assert single == parallel and all(t.year >= 2016 for t in single.values())


def test_native_json_dates_are_utc(chattr_root: Path, fixture_family: FamilyExports) -> None:
    result = commands.convert_imessage(
        Settings.load(chattr_root),
        db_path=fixture_family.paths["imessage"],
        spam_mode="disabled",
        workers=1,
        route="service",
    )
    assert result.json_path is not None
    stamps = [m["timestamp"] for m in json.loads(result.json_path.read_text(encoding="utf-8"))]
    assert all(s.endswith("+00:00") for s in stamps)
    assert "2016-03-12T14:15:00+00:00" in stamps  # 09:15 Eastern standard time
