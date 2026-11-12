"""iMessage: one person behind several addresses, the Mac's own Contacts, and split conversations.

Messages stores one handle per (address, service), and can split one conversation into several
chats. Before importing, handles that are the same person are grouped (the same address on two
services, a shared `person_centric_id`, one Contacts card), and chats with the same people are
merged. Ported from imessage-exporter 5.0.1; see NOTICE.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fixtures import family
from fixtures.builders.imessage import SCHEMA, apple_time

from chattr import commands
from chattr.config import Settings
from chattr.imessage_groups import handle_groups, merged_chats
from chattr.imessage_schema import Capabilities
from chattr.parsers.imessage_parser import IMessageParser
from chattr.people import Contacts

ANA_PHONE, ANA_MAIL = family.ANA.phones[0], family.ANA.emails[0]
JOAO_PHONE = family.JOAO.phones[0]


class Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.messages = 0

    def handle(
        self, rowid: int, address: str, service: str = "iMessage", pcid: str | None = None
    ) -> None:
        self.db.execute(
            "INSERT INTO handle (ROWID, id, service, person_centric_id) VALUES (?, ?, ?, ?)",
            (rowid, address, service, pcid),
        )

    def chat(
        self,
        rowid: int,
        identifier: str,
        handles: list[int],
        name: str | None = None,
        style: int = 45,
    ) -> None:
        self.db.execute(
            "INSERT INTO chat (ROWID, guid, style, chat_identifier, service_name, display_name) "
            "VALUES (?, ?, ?, ?, 'iMessage', ?)",
            (rowid, f"guid-{rowid}", style, identifier, name),
        )
        for handle in handles:
            self.db.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (rowid, handle))

    def message(
        self, chat: int, handle: int, text: str, minute: int, from_me: bool = False
    ) -> None:
        self.messages += 1
        when = apple_time(family.at(2024, 5, 1, 10, minute))
        self.db.execute(
            "INSERT INTO message (ROWID, guid, text, service, handle_id, date, is_from_me) "
            "VALUES (?, ?, ?, 'iMessage', ?, ?, ?)",
            (self.messages, f"m-{self.messages}", text, handle, when, int(from_me)),
        )
        self.db.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat, self.messages),
        )

    def done(self) -> Path:
        self.db.commit()
        self.db.close()
        return self.path


def _groups(path: Path, links: list[tuple[int, int]] = ()) -> dict[int, int]:  # type: ignore[assignment]
    with sqlite3.connect(path) as db:
        return handle_groups(db, Capabilities.determine(db), links)


def _merged(path: Path, links: list[tuple[int, int]] = ()) -> dict[int, int]:  # type: ignore[assignment]
    with sqlite3.connect(path) as db:
        caps = Capabilities.determine(db)
        return merged_chats(db, caps, handle_groups(db, caps, links))


# --- Handles that are one person ------------------------------------------------------------------


def test_one_address_on_two_services_is_one_person(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE, "iMessage")
    db.handle(2, ANA_PHONE, "SMS")
    db.handle(3, JOAO_PHONE, "iMessage")
    groups = _groups(db.done())
    assert groups[1] == groups[2] != groups[3]


def test_a_shared_person_centric_id_joins_a_phone_and_an_email(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE, pcid="person-a")
    db.handle(2, ANA_MAIL, pcid="person-a")
    db.handle(3, JOAO_PHONE, pcid="")  # an empty id joins nobody
    db.handle(4, "+15550199", pcid="")
    groups = _groups(db.done())
    assert groups[1] == groups[2] and groups[3] != groups[4]


def test_links_from_outside_such_as_a_contacts_card_are_added(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE)
    db.handle(2, ANA_MAIL)
    path = db.done()
    assert len(set(_groups(path).values())) == 2
    assert len(set(_groups(path, [(1, 2)]).values())) == 1


def test_a_database_without_person_centric_ids_still_groups_by_address(tmp_path: Path) -> None:
    path = tmp_path / "chat.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT);"
            "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);"
            f"INSERT INTO handle VALUES (1, '{ANA_PHONE}', 'iMessage'), (2, '{ANA_PHONE}', 'SMS');"
        )
    groups = _groups(path)
    assert groups[1] == groups[2]


# --- Conversations that are one conversation ------------------------------------------------------


def test_a_conversation_split_by_service_is_one(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE, "iMessage")
    db.handle(2, ANA_PHONE, "SMS")
    db.chat(10, ANA_PHONE, [1])
    db.chat(11, ANA_PHONE, [2])
    merged = _merged(db.done())
    assert merged[10] == merged[11] == 10  # the earliest chat stands for both


def test_a_group_reached_by_another_address_is_the_same_group(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE, pcid="a")
    db.handle(2, ANA_MAIL, pcid="a")
    db.handle(3, JOAO_PHONE)
    db.chat(12, "chat-1", [1, 3], name=None, style=43)
    db.chat(13, "chat-2", [2, 3], name=None, style=43)
    merged = _merged(db.done())
    assert merged[12] == merged[13]


def test_named_groups_merge_only_when_the_name_matches_too(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE)
    db.handle(3, JOAO_PHONE)
    db.chat(20, "chat-a", [1, 3], name="Costa Family", style=43)
    db.chat(21, "chat-b", [1, 3], name="Costa Family", style=43)
    db.chat(22, "chat-c", [1, 3], name="Wedding plans", style=43)
    db.chat(23, "chat-d", [1, 3], name=None, style=43)
    merged = _merged(db.done())
    assert merged[20] == merged[21] != merged[22]
    assert merged[23] == merged[20]  # an unnamed copy joins the earliest named one


def test_a_one_to_one_chat_never_merges_into_a_group(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE)
    db.handle(3, JOAO_PHONE)
    db.chat(30, ANA_PHONE, [1])
    db.chat(31, "chat-g", [1, 3], style=43)
    merged = _merged(db.done())
    assert merged[30] != merged[31]


def test_chats_tied_in_the_lookup_table_are_one_even_with_different_people(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE)
    db.handle(2, ANA_MAIL)
    db.chat(40, ANA_PHONE, [1])
    db.chat(41, ANA_MAIL, [2])
    db.db.executescript(
        "CREATE TABLE chat_lookup (identifier TEXT, domain TEXT, chat INTEGER, priority INTEGER);"
        "INSERT INTO chat_lookup VALUES ('ana', 'iMessage', 40, 0), ('ana', 'SMS', 41, 0);"
    )
    merged = _merged(db.done())
    assert merged[40] == merged[41]


def test_chats_with_nobody_in_them_are_left_alone(tmp_path: Path) -> None:
    db = Db(tmp_path / "chat.db")
    db.chat(50, "empty-1", [])
    db.chat(51, "empty-2", [])
    merged = _merged(db.done())
    assert merged[50] != merged[51]


# --- Through the parser ---------------------------------------------------------------------------


def _split(tmp_path: Path) -> Path:
    db = Db(tmp_path / "chat.db")
    db.handle(1, ANA_PHONE, "iMessage", pcid="a")
    db.handle(2, ANA_PHONE, "SMS")
    db.handle(3, ANA_MAIL, "iMessage", pcid="a")
    db.chat(10, ANA_PHONE, [1])
    db.chat(11, ANA_PHONE, [2])
    db.chat(12, ANA_MAIL, [3])
    db.message(10, 1, "from her phone", 1)
    db.message(11, 2, "by text message", 2)
    db.message(12, 3, "from her email", 3)
    db.message(10, 0, "my answer", 4, from_me=True)
    return db.done()


def test_the_split_conversation_is_imported_as_one(tmp_path: Path) -> None:
    batches = IMessageParser(spam_mode="disabled").parse(_split(tmp_path))
    assert len(batches) == 1
    (batch,) = batches
    assert [r.body for r in batch.records] == [
        "from her phone",
        "by text message",
        "from her email",
        "my answer",
    ]
    assert set(batch.conversation.participants) == {ANA_PHONE, ANA_MAIL}
    assert batch.conversation.source_id == "guid-10" and batch.conversation.type == "individual"


def test_the_parser_reports_which_addresses_are_one_person(tmp_path: Path) -> None:
    parser = IMessageParser(spam_mode="disabled")
    parser.parse(_split(tmp_path))
    assert [sorted(group) for group in parser.handle_groups] == [sorted([ANA_PHONE, ANA_MAIL])]


def test_naming_one_address_names_the_persons_others(chattr_root: Path, tmp_path: Path) -> None:
    contacts = tmp_path / "contacts.vcf"
    contacts.write_text(
        f"BEGIN:VCARD\nVERSION:3.0\nFN:{family.ANA.name}\nTEL:{ANA_PHONE}\nEND:VCARD\n", "utf-8"
    )  # the card knows her phone, not her email
    settings = Settings.load(chattr_root)
    commands.convert_imessage(
        settings, db_path=_split(tmp_path), contacts=contacts, spam_mode="disabled"
    )
    with sqlite3.connect(commands.central_database(settings)) as db:
        people = dict(db.execute("SELECT identifier, person FROM handles"))
    assert people[ANA_PHONE] == family.ANA.name and people[ANA_MAIL] == family.ANA.name


def test_two_names_in_one_group_are_left_as_they_are(chattr_root: Path, tmp_path: Path) -> None:
    contacts = tmp_path / "contacts.vcf"
    contacts.write_text(
        f"BEGIN:VCARD\nVERSION:3.0\nFN:Ana Home\nTEL:{ANA_PHONE}\nEND:VCARD\n"
        f"BEGIN:VCARD\nVERSION:3.0\nFN:Ana Work\nEMAIL:{ANA_MAIL}\nEND:VCARD\n",
        "utf-8",
    )
    settings = Settings.load(chattr_root)
    commands.convert_imessage(
        settings, db_path=_split(tmp_path), contacts=contacts, spam_mode="disabled"
    )
    with sqlite3.connect(commands.central_database(settings)) as db:
        people = dict(db.execute("SELECT identifier, person FROM handles"))
    assert people[ANA_PHONE] == "Ana Home" and people[ANA_MAIL] == "Ana Work"  # the cards win


# --- The Mac's own Contacts -----------------------------------------------------------------------

ADDRESS_BOOK = """
CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT);
INSERT INTO Z_PRIMARYKEY VALUES (22, 'ABCDContact'), (23, 'ABCDSubscribedContact'), (19, 'ABCDGroup');
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZFIRSTNAME TEXT, ZLASTNAME TEXT,
    ZORGANIZATION TEXT, ZCONTAINERWHERECONTACTISME INTEGER, ZLINKID TEXT);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT,
    ZADDRESSNORMALIZED TEXT);
"""


def _address_book(folder: Path, rows: str) -> Path:
    source = folder / "Sources" / "ABCD-1234"
    source.mkdir(parents=True)
    with sqlite3.connect(source / "AddressBook-v22.abcddb") as db:
        db.executescript(ADDRESS_BOOK + rows)
    return folder


def test_contacts_are_read_from_the_macs_address_book(tmp_path: Path) -> None:
    book = _address_book(
        tmp_path / "AddressBook",
        "INSERT INTO ZABCDRECORD VALUES (1, 22, 'Ana', 'Costa', NULL, NULL, NULL);"
        "INSERT INTO ZABCDRECORD VALUES (2, 22, NULL, NULL, 'Padaria Central', NULL, NULL);"
        "INSERT INTO ZABCDRECORD VALUES (3, 19, 'A group', NULL, NULL, NULL, NULL);"
        "INSERT INTO ZABCDRECORD VALUES (4, 22, 'Maria', 'Costa', NULL, 1, NULL);"
        "INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '(555) 010-1'), (2, 2, '+351 21 555 0100'), (3, 4, '+1 555 0100');"
        "INSERT INTO ZABCDEMAILADDRESS VALUES (1, 1, 'Ana.Costa@Example.com', 'ana.costa@example.com');",
    )
    contacts = Contacts.from_macos(book, region="US")
    assert contacts.lookup("ana.costa@example.com") == "Ana Costa"
    assert contacts.lookup("+351215550100") == "Padaria Central"  # a company card has no first name
    assert contacts.lookup("A group") is None
    assert contacts.me == {"+15550100"}  # the card marked as "me" names the owner's own addresses
    assert contacts.lookup("+15550100") is None  # and is not a contact


def test_every_source_is_read_and_a_missing_address_book_is_just_empty(tmp_path: Path) -> None:
    book = _address_book(
        tmp_path / "AddressBook",
        "INSERT INTO ZABCDRECORD VALUES (1, 22, 'Ana', 'Costa', NULL, NULL, NULL);"
        f"INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '{ANA_PHONE}');",
    )
    second = book / "Sources" / "EFGH-5678"
    second.mkdir()
    with sqlite3.connect(second / "AddressBook-v22.abcddb") as db:
        db.executescript(
            ADDRESS_BOOK
            + "INSERT INTO ZABCDRECORD VALUES (1, 22, 'João', 'Costa', NULL, NULL, NULL);"
            f"INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '{JOAO_PHONE}');"
        )
    (book / "Sources" / "broken").mkdir()
    (book / "Sources" / "broken" / "AddressBook-v22.abcddb").write_text("not a database")
    contacts = Contacts.from_macos(book)
    assert contacts.lookup(ANA_PHONE) == "Ana Costa" and contacts.lookup(JOAO_PHONE) == "João Costa"
    assert len(Contacts.from_macos(tmp_path / "nothing-here")) == 0


def test_an_import_names_people_from_the_address_book_and_knows_the_owner(
    chattr_root: Path, tmp_path: Path
) -> None:
    book = _address_book(
        tmp_path / "AddressBook",
        "INSERT INTO ZABCDRECORD VALUES (1, 22, 'Ana', 'Costa', NULL, NULL, NULL);"
        f"INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '{ANA_PHONE}');",
    )
    settings = Settings.load(chattr_root)
    result = commands.convert_imessage(
        settings, db_path=_split(tmp_path), mac_contacts=book, spam_mode="disabled"
    )
    assert result.unresolved == 0
    with sqlite3.connect(commands.central_database(settings)) as db:
        assert set(db.execute("SELECT person FROM handles")) == {(family.ANA.name,)}


def test_the_address_book_is_not_read_unless_asked(chattr_root: Path, tmp_path: Path) -> None:
    settings = Settings.load(chattr_root)
    result = commands.convert_imessage(settings, db_path=_split(tmp_path), spam_mode="disabled")
    assert result.unresolved == 2  # her phone and her email, unnamed
