"""Normalized records get stable, deterministic IDs."""

from __future__ import annotations

from datetime import datetime, timezone

from chattr.records import ConversationRecord, MessageRecord, assign_ids

CONV = ConversationRecord(
    service="WhatsApp", source_id="WhatsApp Chat - Ana Costa", type="individual"
)


def msg(
    minute: int, body: str, sender: str = "Ana Costa", source_id: str | None = None
) -> MessageRecord:
    return MessageRecord(
        timestamp=datetime(2024, 3, 1, 14, minute, tzinfo=timezone.utc),
        sender=sender,
        body=body,
        source_id=source_id,
    )


def test_conversation_id_is_stable_and_service_prefixed() -> None:
    again = ConversationRecord(
        service="WhatsApp", source_id="WhatsApp Chat - Ana Costa", type="individual"
    )
    assert CONV.id == again.id
    assert CONV.id.startswith("whatsapp:")
    other = ConversationRecord(service="WhatsApp", source_id="Someone else", type="individual")
    assert other.id != CONV.id


def test_message_ids_are_deterministic() -> None:
    first = assign_ids(CONV, [msg(1, "Olá"), msg(2, "Tudo bem?")])
    second = assign_ids(CONV, [msg(1, "Olá"), msg(2, "Tudo bem?")])
    assert [m.id for m in first] == [m.id for m in second]
    assert len({m.id for m in first}) == 2


def test_genuine_repeats_get_distinct_ids() -> None:
    records = assign_ids(CONV, [msg(1, "ok"), msg(1, "ok"), msg(1, "ok")])
    assert len({m.id for m in records}) == 3


def test_overlapping_export_reuses_the_ids_of_the_overlap() -> None:
    older = assign_ids(CONV, [msg(1, "a"), msg(2, "b"), msg(3, "ok"), msg(3, "ok")])
    newer = assign_ids(CONV, [msg(3, "ok"), msg(3, "ok"), msg(4, "c")])
    assert {m.id for m in older} & {m.id for m in newer} == {older[2].id, older[3].id}


def test_source_message_ids_take_precedence() -> None:
    a = assign_ids(CONV, [msg(1, "hello", source_id="GUID-1")])[0]
    b = assign_ids(CONV, [msg(9, "edited text", source_id="GUID-1")])[0]
    assert a.id == b.id
    assert a.content_hash != b.content_hash


def test_content_hash_changes_with_the_body() -> None:
    a = assign_ids(CONV, [msg(1, "one")])[0]
    b = assign_ids(CONV, [msg(1, "two")])[0]
    assert a.content_hash != b.content_hash
