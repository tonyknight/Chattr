"""vCard 3.0 contacts file for the synthetic family (the owner's address book)."""

from __future__ import annotations

from pathlib import Path

from fixtures import family


def _card(person: family.Person) -> str:
    given, _, surname = person.name.partition(" ")
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{surname};{given};;;",
        f"FN:{person.name}",
    ]
    lines += [f"TEL;TYPE=CELL:{phone}" for phone in person.phones]
    lines += [f"EMAIL;TYPE=INTERNET:{email}" for email in person.emails]
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def build_vcard(out_dir: Path) -> Path:
    """Write `contacts.vcf` (the owner's contacts, not the owner) and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "contacts.vcf"
    cards = [_card(person) for person in family.CONTACTS]
    path.write_bytes(("\r\n".join(cards) + "\r\n").encode("utf-8"))
    return path
