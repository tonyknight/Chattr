"""People: vCard contacts, aliases and identifier matching resolve handles to named people."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr.people import Aliases, Contacts, PeopleResolver, normalize_identifier

# --- Normalizing identifiers ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+15550101",
        "+1 555 0101",
        "(555) 0101",
        "555-0101",
        "1-555-0101",
        "+1 (555) 0101",
        "15550101",
    ],
)
def test_formatting_variants_of_one_number_match(raw: str) -> None:
    assert normalize_identifier(raw, "US") == "+15550101"


def test_other_regions_use_their_own_country_code() -> None:
    assert normalize_identifier("912 345 678", "PT") == "+351912345678"
    assert normalize_identifier("+351 912 345 678", "US") == "+351912345678"


def test_emails_and_names_are_case_folded() -> None:
    assert normalize_identifier(" Ana.Costa@Example.COM ", "US") == "ana.costa@example.com"
    assert normalize_identifier("  Ana   Costa ", "US") == "ana costa"
    assert normalize_identifier("maria.costa.example", "US") == "maria.costa.example"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_nothing_normalizes_to_nothing(raw: str | None) -> None:
    assert normalize_identifier(raw, "US") == ""


def test_a_short_number_is_not_mistaken_for_a_phone() -> None:
    assert normalize_identifier("12345", "US") == "12345"  # a code, kept as text


# --- Contacts -------------------------------------------------------------------------------


def test_the_fixture_vcard_resolves_phones_emails_and_names(fixture_family: FamilyExports) -> None:
    contacts = Contacts.from_vcf(fixture_family.paths["contacts"])
    assert len(contacts) == 3
    assert contacts.lookup(family.ANA.phones[0]) == family.ANA.name
    assert contacts.lookup("(555) 0102") == family.JOAO.name
    assert contacts.lookup(family.RUI.emails[0].upper()) == family.RUI.name
    assert contacts.lookup(family.JOAO.name.upper()) == family.JOAO.name  # a name as a handle
    assert contacts.lookup(family.UNKNOWN_HANDLE) is None
    assert contacts.lookup("") is None


def _vcf(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "c.vcf"
    path.write_text(text, encoding="utf-8")
    return path


def test_cards_with_many_numbers_and_emails_and_folded_lines(tmp_path: Path) -> None:
    contacts = Contacts.from_vcf(
        _vcf(
            tmp_path,
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Teresa\r\n  Silva\r\n"
            "TEL;TYPE=CELL:+351 912 345 678\r\nTEL;TYPE=HOME:+351 211 000 000\r\n"
            "EMAIL:teresa@example.org\r\nEMAIL;TYPE=WORK:t.silva@example.net\r\nEND:VCARD\r\n",
        )
    )
    keys = ("+351912345678", "+351211000000", "teresa@example.org", "T.Silva@example.net")
    assert {contacts.lookup(k) for k in keys} == {
        "Teresa Silva"
    }  # a folded line continues the name


def test_a_card_without_fn_uses_the_structured_name(tmp_path: Path) -> None:
    contacts = Contacts.from_vcf(
        _vcf(tmp_path, "BEGIN:VCARD\nVERSION:3.0\nN:Pereira;Rui;;;\nTEL:+15550103\nEND:VCARD\n")
    )
    assert contacts.lookup("+15550103") == "Rui Pereira"


def test_vcard_4_and_utf8(tmp_path: Path) -> None:
    contacts = Contacts.from_vcf(
        _vcf(
            tmp_path,
            "BEGIN:VCARD\nVERSION:4.0\nFN:João Müller\nTEL;VALUE=uri:tel:+15550104\nEND:VCARD\n",
        )
    )
    assert contacts.lookup("+1 555 0104") == "João Müller"


def test_cards_without_a_name_or_any_handle_are_skipped(tmp_path: Path) -> None:
    contacts = Contacts.from_vcf(
        _vcf(
            tmp_path,
            "BEGIN:VCARD\nVERSION:3.0\nTEL:+15550105\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nFN:No Handles\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nFN:Has One\nTEL:+15550106\nEND:VCARD\n",
        )
    )
    assert len(contacts) == 1
    assert contacts.lookup("+15550105") is None and contacts.lookup("+15550106") == "Has One"


def test_a_broken_file_is_an_error_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="contacts"):
        Contacts.from_vcf(_vcf(tmp_path, "this is not a vcard"))
    with pytest.raises(FileNotFoundError):
        Contacts.from_vcf(tmp_path / "missing.vcf")


def test_the_region_decides_local_numbers(tmp_path: Path) -> None:
    contacts = Contacts.from_vcf(
        _vcf(tmp_path, "BEGIN:VCARD\nVERSION:3.0\nFN:Teresa\nTEL:912 345 678\nEND:VCARD\n"),
        region="PT",
    )
    assert contacts.lookup("+351912345678") == "Teresa"


# --- Aliases --------------------------------------------------------------------------------


def test_aliases_map_identifiers_and_names_to_people(tmp_path: Path) -> None:
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {"+1 555 0101": "Ana Costa", "ANA.C@EXAMPLE.COM": "Ana Costa", "mana": "Ana Costa"}
        )
    )
    aliases = Aliases.from_json(path)
    assert aliases.lookup("+15550101") == "Ana Costa"
    assert aliases.lookup("ana.c@example.com") == "Ana Costa"
    assert aliases.lookup("Mana") == "Ana Costa"
    assert aliases.lookup("nobody") is None


@pytest.mark.parametrize("content", ["[1, 2]", "{not json", '{"a": 1}'])
def test_bad_aliases_files_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "aliases.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="aliases"):
        Aliases.from_json(path)


# --- The resolver ---------------------------------------------------------------------------


def test_aliases_win_over_contacts(tmp_path: Path, fixture_family: FamilyExports) -> None:
    aliases_path = tmp_path / "a.json"
    aliases_path.write_text(json.dumps({family.ANA.phones[0]: "Ana (daughter)"}))
    resolver = PeopleResolver(
        contacts=Contacts.from_vcf(fixture_family.paths["contacts"]),
        aliases=Aliases.from_json(aliases_path),
    )
    assert resolver.person_for(family.ANA.phones[0], None) == "Ana (daughter)"
    assert resolver.person_for(family.JOAO.phones[0], None) == family.JOAO.name


def test_the_display_name_is_tried_when_the_identifier_is_unknown(
    fixture_family: FamilyExports,
) -> None:
    resolver = PeopleResolver(contacts=Contacts.from_vcf(fixture_family.paths["contacts"]))
    assert resolver.person_for("someone-new", family.RUI.name) == family.RUI.name
    assert resolver.person_for("someone-new", "Stranger") is None


def test_the_owner_is_known_by_name_or_identifier() -> None:
    resolver = PeopleResolver(
        owner_name="Maria Costa", owner_identifiers=["maria.costa@example.com", "+1 555 0100"]
    )
    assert resolver.is_owner("+15550100", None)
    assert resolver.is_owner("MARIA.COSTA@EXAMPLE.COM", "whoever")
    assert resolver.is_owner("some-handle", "maria costa")
    assert not resolver.is_owner("+15550101", "Ana Costa")
    assert resolver.person_for("+15550100", None) == "Maria Costa"


def test_without_an_owner_nobody_is_the_owner() -> None:
    assert not PeopleResolver().is_owner("+15550100", "Maria Costa")
