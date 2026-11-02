"""Store files by the hash of their content.

A file is written once, at `<root>/<first two hex digits>/<sha256><extension>`, however many times
it turns up. Attachments pulled out of mail (which exist only inside the mailbox) are kept this
way; the hash is what the archive records, and what lets identical files collapse.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Union


def store_by_hash(root: Union[str, Path], data: bytes, suffix: str = "") -> tuple[str, str]:
    """Write `data` under its SHA-256; returns `(sha256, path relative to root)`.

    The suffix is cleaned to letters and digits (at most 10), lower case, so a hostile file name
    can't escape the folder.
    """
    digest = hashlib.sha256(data).hexdigest()
    extension = re.sub(r"[^a-z0-9]", "", suffix.lower())[:10]
    relative = f"{digest[:2]}/{digest}" + (f".{extension}" if extension else "")
    target = Path(root) / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(data)
        os.replace(temporary, target)  # never leave a half-written file under its final name
    return digest, relative
