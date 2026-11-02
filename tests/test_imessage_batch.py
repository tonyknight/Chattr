"""Regression: processing a batch with a chat filter must not modify the caller's ID list."""

from __future__ import annotations

from fixtures import FamilyExports

from chattr.parsers.imessage_database_parser import iMessageDatabaseParser


def test_chat_filter_does_not_mutate_message_ids(fixture_family: FamilyExports) -> None:
    parser = iMessageDatabaseParser(str(fixture_family.paths["imessage"]))
    ids = [1, 2, 3]
    parser._process_message_batch(ids, chat_filter=1)
    assert ids == [1, 2, 3]


def test_apple_timestamp_conversion_needs_no_instance() -> None:
    converted = iMessageDatabaseParser._apple_timestamp_to_datetime(0)
    assert converted.year in (1969, 1970)
