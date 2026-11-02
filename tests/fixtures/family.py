"""The synthetic fixture family: one fictional data set behind every test export.

Everything here is invented:
- US phone numbers use the 555-0100 to 555-0199 range reserved for fiction;
- email addresses use the reserved example.com and example.org domains;
- names are invented.

Never add real personal data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Person:
    """A member of the fictional family or circle."""

    key: str
    name: str
    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    usernames: dict[str, str] = field(default_factory=dict)


OWNER = Person(
    key="maria",
    name="Maria Costa",
    phones=("+15550100",),
    emails=("maria.costa@example.com",),
    usernames={"messenger": "Maria Costa", "instagram": "maria.costa.example"},
)

ANA = Person(
    key="ana",
    name="Ana Costa",
    phones=("+15550101",),
    emails=("ana.costa@example.com",),
)

JOAO = Person(
    key="joao",
    name="João Costa",
    phones=("+15550102",),
    emails=("joao.costa@example.org",),
    usernames={"messenger": "João Costa"},
)

RUI = Person(
    key="rui",
    name="Rui Pereira",
    phones=("+15550103",),
    emails=("rui.pereira@example.org",),
    usernames={"instagram": "rui.pereira.example"},
)

CONTACTS: tuple[Person, ...] = (ANA, JOAO, RUI)
PEOPLE: tuple[Person, ...] = (OWNER, *CONTACTS)

#: A handle that belongs to no known person (for the unresolved-handle test).
UNKNOWN_HANDLE = "+15550199"

GROUP_NAME = "Costa Family"

#: Local time zones used by the family (fixed offsets keep fixtures deterministic).
TZ_EAST = timezone(timedelta(hours=-5))  # owner's home, US Eastern standard time
TZ_LISBON = timezone(timedelta(hours=0))  # João lives in Lisbon (winter, UTC+0)


def at(year: int, month: int, day: int, hour: int, minute: int, second: int = 0,
       tz: timezone = TZ_EAST) -> datetime:
    """A fixed, timezone-aware moment in the family's history."""
    return datetime(year, month, day, hour, minute, second, tzinfo=tz)


#: A message long enough (> 255 characters) to need the two-byte length form in attributedBody.
LONG_MESSAGE = (
    "Mãe, I finally sorted the old photo boxes from Avó's house. There are pictures from the "
    "wedding in Porto, the trip to Sintra, and a whole envelope of letters from Tio João that "
    "nobody remembered. I scanned the first fifty and labelled them with dates where I could "
    "read them on the back. Can we look at the rest together on Sunday?"
)
assert len(LONG_MESSAGE.encode("utf-8")) > 255
