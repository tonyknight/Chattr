"""Who is behind a handle: contacts (vCard), aliases (JSON) and identifier matching.

A *handle* is whatever a service shows for a sender: a phone number, an email address, a username
or a name. Resolving a handle gives a **person**, a name a human recognizes. The order is:

1. the aliases file (`{identifier or name: person}`), which always wins;
2. the contacts (a vCard file): by phone number, email address, or exact name;
3. nothing: the handle is reported as unresolved, to be linked by adding it to the aliases.

Identifiers are compared in a normalized form: phone numbers as E.164 (`+15550101`, whatever the
punctuation or whether the country code was written), email addresses lower case, anything else
case-folded text with single spaces. The archive owner is configuration (a name and identifiers).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import sqlite3
from contextlib import closing

import phonenumbers
import vobject

PathLike = Union[str, Path]
_PHONE_LIKE = re.compile(r"^\+?[\d\s().\-]{6,}$")
_MIN_PHONE_DIGITS = 7


def normalize_identifier(raw: Optional[str], region: str = "US") -> str:
    """The comparable form of a handle: E.164 for phone numbers, lower case for the rest."""
    text = (raw or "").strip()
    if not text:
        return ""
    if "@" in text:
        return text.lower()
    digits = re.sub(r"\D", "", text)
    if _PHONE_LIKE.match(text) and len(digits) >= _MIN_PHONE_DIGITS:
        try:
            parsed = phonenumbers.parse(text, region)
            if phonenumbers.is_possible_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
        code = str(phonenumbers.country_code_for_region(region))
        if text.startswith("+") or digits.startswith(code):
            return "+" + digits  # the country code is already there, just not parsable as valid
        return "+" + code + digits
    return re.sub(r"\s+", " ", text).casefold()


class _Index:
    """Normalized identifier or name → person."""

    def __init__(self, region: str) -> None:
        self.region = region
        self._people: dict[str, str] = {}

    def add(self, key: Optional[str], person: str) -> None:
        normalized = normalize_identifier(key, self.region)
        if normalized:
            self._people.setdefault(normalized, person)

    def lookup(self, key: Optional[str]) -> Optional[str]:
        return self._people.get(normalize_identifier(key, self.region))

    def __len__(self) -> int:
        return len(set(self._people.values()))


MAC_CONTACT_ENTITIES = ("ABCDContact", "ABCDSubscribedContact")
MAC_CONTACT_ENTITY_FALLBACK = (22, 23)  # their `Z_ENT` in every macOS model seen so far


def mac_contacts_folder() -> Path:
    """Where macOS keeps the Contacts databases."""
    return Path.home() / "Library" / "Application Support" / "AddressBook"


class Contacts(_Index):
    """People found by phone number, email address or exact name: from a vCard file, or from the
    Mac's own Contacts databases."""

    def __init__(self, region: str) -> None:
        super().__init__(region)
        #: The owner's own addresses (the card marked "me"), normalized.
        self.me: set[str] = set()

    def merge(self, other: "Contacts") -> "Contacts":
        """Add another set of contacts; what is already known wins."""
        for key, person in other._people.items():
            self._people.setdefault(key, person)
        self.me |= other.me
        return self

    @classmethod
    def from_macos(cls, folder: Optional[PathLike] = None, region: str = "US") -> "Contacts":
        """Read the Contacts databases of this Mac (read-only): the main one and one per account
        under `Sources/`. A database that is missing or cannot be read is skipped.

        (Reading these needs Full Disk Access for the program that runs Chattr, the same as the
        Messages database. Derived from imessage-exporter 5.0.1; see NOTICE.)
        """
        root = Path(folder) if folder else mac_contacts_folder()
        contacts = cls(region)
        databases = [root / "AddressBook-v22.abcddb"]
        sources = root / "Sources"
        if sources.is_dir():
            databases += sorted(p / "AddressBook-v22.abcddb" for p in sources.iterdir())
        for database in databases:
            if database.is_file():
                try:
                    contacts._read_macos(database)
                except (sqlite3.Error, OSError):
                    continue
        return contacts

    def _read_macos(self, database: Path) -> None:
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
            columns = {str(r[1]) for r in db.execute("PRAGMA table_info(ZABCDRECORD)")}
            try:
                marks = ", ".join("?" for _ in MAC_CONTACT_ENTITIES)
                entities = [
                    row[0]
                    for row in db.execute(
                        f"SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME IN ({marks})",
                        MAC_CONTACT_ENTITIES,
                    )
                ] or list(MAC_CONTACT_ENTITY_FALLBACK)
            except sqlite3.Error:
                entities = list(MAC_CONTACT_ENTITY_FALLBACK)
            company = "ZORGANIZATION" if "ZORGANIZATION" in columns else "NULL"
            me = "ZCONTAINERWHERECONTACTISME" if "ZCONTAINERWHERECONTACTISME" in columns else "NULL"
            marks = ", ".join("?" for _ in entities)
            names: dict[int, str] = {}
            mine: set[int] = set()
            for key, first, last, organization, is_me in db.execute(
                f"SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, {company}, {me} FROM ZABCDRECORD "
                f"WHERE Z_ENT IN ({marks})",
                entities,
            ):
                if is_me is not None:
                    mine.add(key)
                    continue
                name = " ".join(
                    str(part).strip() for part in (first, last) if part and str(part).strip()
                )
                name = name or (str(organization).strip() if organization else "")
                if name:
                    names[key] = re.sub(r"\s+", " ", name)
            mail = {str(r[1]) for r in db.execute("PRAGMA table_info(ZABCDEMAILADDRESS)")}
            address = "ZADDRESSNORMALIZED" if "ZADDRESSNORMALIZED" in mail else "ZADDRESS"
            found = [
                *db.execute(
                    "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER IS NOT NULL"
                ),
                *db.execute(
                    f"SELECT ZOWNER, {address} FROM ZABCDEMAILADDRESS WHERE ZOWNER IS NOT NULL"
                ),
            ]
        for owner, value in found:
            if not value:
                continue
            if owner in mine:
                normalized = normalize_identifier(str(value), self.region)
                if normalized:
                    self.me.add(normalized)
            elif owner in names:
                self.add(names[owner], names[owner])
                self.add(str(value), names[owner])

    @classmethod
    def from_vcf(cls, path: PathLike, region: str = "US") -> "Contacts":
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        if "BEGIN:VCARD" not in text.upper():
            raise ValueError(f"The contacts file {path} holds no vCards.")
        contacts = cls(region)
        try:
            cards = list(vobject.readComponents(text))
        except Exception as error:  # vobject raises its own ParseError for damaged cards
            raise ValueError(f"The contacts file {path} could not be read: {error}") from error
        for card in cards:
            name = cls._name(card)
            handles = [
                str(entry.value).removeprefix("tel:")
                for key in ("tel", "email")
                for entry in getattr(card, f"{key}_list", [])
            ]
            if not name or not handles:
                continue
            contacts.add(name, name)  # a name seen as a handle (WhatsApp, Messenger)
            for handle in handles:
                contacts.add(handle, name)
        return contacts

    @staticmethod
    def _name(card: Any) -> str:
        formatted = getattr(card, "fn", None)
        if formatted and str(formatted.value).strip():
            return re.sub(r"\s+", " ", str(formatted.value)).strip()
        structured = getattr(card, "n", None)
        if structured is None:
            return ""
        value = structured.value
        parts = [value.prefix, value.given, value.additional, value.family, value.suffix]
        return " ".join(str(p).strip() for p in parts if str(p).strip())


class Aliases(_Index):
    """`{identifier or name: person}` from a JSON file; the human's word beats the contacts."""

    @classmethod
    def from_json(cls, path: PathLike, region: str = "US") -> "Aliases":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Can't read the aliases file {path}: {error}") from error
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise ValueError(f"The aliases file {path} must be a JSON object of names (aliases).")
        aliases = cls(region)
        for key, person in data.items():
            aliases.add(key, person)
        return aliases


class PeopleResolver:
    """Resolve handles to people, and recognize the archive owner."""

    def __init__(
        self,
        contacts: Optional[Contacts] = None,
        aliases: Optional[Aliases] = None,
        owner_name: Optional[str] = None,
        owner_identifiers: Iterable[str] = (),
        region: str = "US",
    ) -> None:
        self.contacts = contacts
        self.aliases = aliases
        self.owner_name = (owner_name or "").strip()
        self.region = region
        self._owner_keys = {
            normalize_identifier(i, region) for i in [self.owner_name, *owner_identifiers] if i
        } - {""}
        if contacts is not None and self.owner_name:
            self._owner_keys |= contacts.me  # the Contacts card marked "me"

    def is_owner(self, identifier: Optional[str], display_name: Optional[str]) -> bool:
        return any(
            normalize_identifier(value, self.region) in self._owner_keys
            for value in (identifier, display_name)
            if value
        )

    def person_for(self, identifier: Optional[str], display_name: Optional[str]) -> Optional[str]:
        """The person behind a handle: the owner, an alias, a contact, or None."""
        if self.owner_name and self.is_owner(identifier, display_name):
            return self.owner_name
        for source in (self.aliases, self.contacts):
            if source is None:
                continue
            for key in (identifier, display_name):
                person = source.lookup(key) if key else None
                if person:
                    return person
        return None
