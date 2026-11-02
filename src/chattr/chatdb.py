"""Opening an iMessage `chat.db`: always read-only.

Chattr reads `chat.db`; it never changes it. Every connection to one goes through
`connect_readonly`, which opens it with SQLite's `mode=ro`: a write is refused, no journal or WAL
file is created beside it, and a missing file is an error rather than a new empty database. The
path is turned into a proper `file:` URI, so a path containing `?`, `#` or `%` still opens.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union


def connect_readonly(path: Union[str, Path]) -> sqlite3.Connection:
    """Open `path` read-only, returning rows addressable by column name."""
    uri = f"{Path(path).expanduser().resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn
