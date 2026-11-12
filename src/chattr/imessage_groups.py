"""Who is one person, and which chats are one conversation, in a Messages database.

Messages keeps one `handle` row per (address, service), so one person can have several: a phone
number registered for iMessage, SMS and RCS is three rows, and their email address is another. A
conversation can likewise be split into several `chat` rows. This module groups the handle rows that
are the same person, then merges the chats that have the same people.

Derived from imessage-exporter 5.0.1 (`tables/handle.rs`, `tables/chat_handle.rs`,
`docs/tables/duplicates.md`); see NOTICE.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from chattr.imessage_schema import Capabilities

GROUP_STYLE = 43  # `chat.style` of a group conversation (45 is one-to-one)


class _Sets:
    """Disjoint sets of integers; each set is known by its smallest member."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, item: int) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: int) -> int:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self._parent[max(a, b)] = min(a, b)

    def roots(self) -> dict[int, int]:
        return {item: self.find(item) for item in self._parent}


def handle_groups(
    db: sqlite3.Connection, caps: Capabilities, links: Iterable[tuple[int, int]] = ()
) -> dict[int, int]:
    """Handle row → the group it belongs to (the group's lowest handle row).

    Rows are one person when they have the same address, or the same non-empty
    `person_centric_id`. `links` adds pairs known from elsewhere (two addresses on one Contacts
    card).
    """
    centric = "person_centric_id" if caps.person_centric_ids else "NULL"
    sets = _Sets()
    by_address: dict[str, int] = {}
    by_centric: dict[str, int] = {}
    for rowid, address, person in db.execute(f"SELECT ROWID, id, {centric} FROM handle"):
        sets.add(rowid)
        key = (address or "").strip().lower()
        if key:
            sets.union(rowid, by_address.setdefault(key, rowid))
        if person and str(person).strip():
            sets.union(rowid, by_centric.setdefault(str(person), rowid))
    for left, right in links:
        if left in sets._parent and right in sets._parent:
            sets.union(left, right)
    return sets.roots()


def merged_chats(
    db: sqlite3.Connection, caps: Capabilities, groups: dict[int, int]
) -> dict[int, int]:
    """Chat row → the chat that stands for its conversation.

    - Chats tied together in `chat_lookup` are one conversation, whatever their names.
    - Otherwise chats are one conversation when they have the same people and are of the same
      kind (group or one-to-one): named chats only with the same name; an unnamed chat joins the
      earliest named chat with its people, or the other unnamed ones.
    - A chat with nobody in it stays by itself.

    The conversation is represented by its earliest named chat, else its earliest chat, so the
    choice does not change as messages arrive.
    """
    names: dict[int, Optional[str]] = {}
    kinds: dict[int, bool] = {}
    for rowid, name, style in db.execute("SELECT ROWID, display_name, style FROM chat"):
        names[rowid] = (name or "").strip() or None
        kinds[rowid] = style == GROUP_STYLE
    people: dict[int, set[int]] = {chat: set() for chat in names}
    for chat, handle in db.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if chat in people and handle in groups:
            people[chat].add(groups[handle])

    sets = _Sets()
    for chat in names:
        sets.add(chat)
    if caps.chat_lookup:
        seen: dict[str, int] = {}
        for identifier, chat in db.execute("SELECT identifier, chat FROM chat_lookup"):
            if chat in names and identifier:
                sets.union(chat, seen.setdefault(str(identifier), chat))

    named: dict[tuple[frozenset[int], bool, str], int] = {}
    first_named: dict[tuple[frozenset[int], bool], int] = {}
    unnamed: dict[tuple[frozenset[int], bool], int] = {}
    for chat in sorted(names):
        if not people[chat]:
            continue
        key = (frozenset(people[chat]), kinds[chat])
        name = names[chat]
        if name:
            sets.union(chat, named.setdefault((*key, name), chat))
            first_named.setdefault(key, chat)
    for chat in sorted(names):
        if not people[chat] or names[chat]:
            continue
        key = (frozenset(people[chat]), kinds[chat])
        sets.union(chat, first_named.get(key, unnamed.setdefault(key, chat)))

    members: dict[int, list[int]] = {}
    for chat, root in sets.roots().items():
        members.setdefault(root, []).append(chat)
    chosen: dict[int, int] = {}
    for rows in members.values():
        with_name = sorted(chat for chat in rows if names[chat])
        representative = with_name[0] if with_name else min(rows)
        for chat in rows:
            chosen[chat] = representative
    return chosen
