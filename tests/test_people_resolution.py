"""Handles in the archive resolve to people; the owner is derived; unresolved ones are reported."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import commands
from chattr.config import Settings
from chattr.database_manager import DatabaseManager
from chattr.people import Aliases, Contacts, PeopleResolver


@pytest.fixture
def archive(chattr_root: Path, fixture_family: FamilyExports) -> Path:
    """An archive holding the iMessage and WhatsApp fixtures, nothing resolved yet."""
    settings = Settings.load(chattr_root)
    commands.convert_imessage(
        settings,
        db_path=fixture_family.paths["imessage"],
        attachment_root=fixture_family.paths["imessage"].parent / "Attachments",
        spam_mode="disabled",
        workers=1,
    )
    whatsapp = fixture_family.paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    commands.convert(settings, "whatsapp", whatsapp)
    return commands.central_database(settings)


def _resolver(fixture_family: FamilyExports, **options) -> PeopleResolver:
    return PeopleResolver(contacts=Contacts.from_vcf(fixture_family.paths["contacts"]), **options)


def _people(db_path: Path) -> dict[tuple[str, str], str | None]:
    with sqlite3.connect(db_path) as db:
        return {
            (s, i): p for s, i, p in db.execute("SELECT service, identifier, person FROM handles")
        }


def test_contacts_resolve_phones_emails_and_names(
    archive: Path, fixture_family: FamilyExports
) -> None:
    with DatabaseManager(archive) as db:
        result = db.resolve_people(_resolver(fixture_family))
    people = _people(archive)
    assert people[("iMessage", family.ANA.phones[0])] == family.ANA.name
    assert people[("iMessage", family.JOAO.emails[0])] == family.JOAO.name
    assert people[("WhatsApp", family.ANA.name)] == family.ANA.name  # a name as the handle
    assert people[("iMessage", family.UNKNOWN_HANDLE)] is None
    assert result.newly_resolved == 3 and result.owner_handles == 0


def test_unresolved_handles_are_reported_with_what_is_known_about_them(
    archive: Path, fixture_family: FamilyExports
) -> None:
    with DatabaseManager(archive) as db:
        result = db.resolve_people(_resolver(fixture_family))
    keys = {(u.service, u.identifier): u for u in result.unresolved}
    assert set(keys) == {("iMessage", family.UNKNOWN_HANDLE), ("WhatsApp", family.OWNER.name)}
    stranger = keys[("iMessage", family.UNKNOWN_HANDLE)]
    assert stranger.messages == 1
    assert stranger.first_seen == stranger.last_seen and stranger.first_seen.startswith(
        "2025-01-08"
    )


def test_the_owner_is_derived_from_configuration(
    archive: Path, fixture_family: FamilyExports
) -> None:
    resolver = _resolver(fixture_family, owner_name=family.OWNER.name)
    with DatabaseManager(archive) as db:
        result = db.resolve_people(resolver)
    assert result.owner_handles == 1  # the owner's WhatsApp name
    assert {u.identifier for u in result.unresolved} == {family.UNKNOWN_HANDLE}
    with sqlite3.connect(archive) as db:
        assert db.execute(
            "SELECT person FROM handles WHERE identifier = ?", (family.OWNER.name,)
        ).fetchone() == (family.OWNER.name,)
        assert db.execute(
            "SELECT DISTINCT role FROM participants WHERE role = 'owner'"
        ).fetchall() == [("owner",)]
        owned = db.execute(
            "SELECT COUNT(*) FROM messages m JOIN handles h ON h.id = m.sender_handle_id "
            "WHERE h.identifier = ? AND m.is_from_owner = 1",
            (family.OWNER.name,),
        ).fetchone()[0]
        assert owned == 2  # both of the owner's WhatsApp messages are now flagged


def test_the_owner_can_be_known_by_a_phone_number(archive: Path) -> None:
    resolver = PeopleResolver(owner_name="Maria", owner_identifiers=[family.ANA.phones[0]])
    with DatabaseManager(archive) as db:
        assert db.resolve_people(resolver).owner_handles == 1


def test_resolving_again_changes_nothing(archive: Path, fixture_family: FamilyExports) -> None:
    resolver = _resolver(fixture_family, owner_name=family.OWNER.name)
    with DatabaseManager(archive) as db:
        first = db.resolve_people(resolver)
        before = _people(archive)
        second = db.resolve_people(resolver)
    assert _people(archive) == before
    assert (second.newly_resolved, second.owner_handles) == (0, first.owner_handles)
    assert [u.identifier for u in second.unresolved] == [u.identifier for u in first.unresolved]


def test_an_alias_overrides_a_contact_and_a_known_person_is_kept_otherwise(
    archive: Path, fixture_family: FamilyExports, tmp_path: Path
) -> None:
    with DatabaseManager(archive) as db:
        db.resolve_people(_resolver(fixture_family))
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({family.ANA.phones[0]: "Ana (daughter)"}))
    with DatabaseManager(archive) as db:
        db.resolve_people(PeopleResolver(aliases=Aliases.from_json(alias_file)))  # no contacts now
    people = _people(archive)
    assert people[("iMessage", family.ANA.phones[0])] == "Ana (daughter)"  # alias wins
    assert (
        people[("iMessage", family.JOAO.emails[0])] == family.JOAO.name
    )  # kept: nothing contradicts


def test_the_legacy_view_shows_the_person_as_the_sender(
    archive: Path, fixture_family: FamilyExports
) -> None:
    with DatabaseManager(archive) as db:
        db.resolve_people(_resolver(fixture_family))
        senders = {r["message_sender"] for r in db.get_all_messages()}
    assert family.ANA.name in senders and family.UNKNOWN_HANDLE in senders
    assert family.ANA.phones[0] not in senders


def test_one_service_can_be_resolved_alone(archive: Path, fixture_family: FamilyExports) -> None:
    with DatabaseManager(archive) as db:
        db.resolve_people(_resolver(fixture_family), service="WhatsApp")
    people = _people(archive)
    assert people[("WhatsApp", family.ANA.name)] == family.ANA.name
    assert people[("iMessage", family.ANA.phones[0])] is None


def test_without_any_inputs_everything_is_unresolved(archive: Path) -> None:
    with DatabaseManager(archive) as db:
        result = db.resolve_people(PeopleResolver())
        assert result.newly_resolved == 0 and len(result.unresolved) == len(_people(archive))


def test_the_most_active_handles_come_first(archive: Path) -> None:
    with DatabaseManager(archive) as db:
        counts = [u.messages for u in db.resolve_people(PeopleResolver()).unresolved]
    assert counts == sorted(counts, reverse=True)
