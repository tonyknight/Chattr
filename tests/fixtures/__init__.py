"""Synthetic fixture family for Chattr's (and Caixa's) tests. Standard library only.

`build_all(out_dir)` writes every supported export format for one fictional family. See
`README.md` in this folder for the format assumptions each builder makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FamilyExports:
    """Where each synthetic export was written."""

    root: Path
    paths: dict[str, Path]


def build_all(out_dir: Path) -> FamilyExports:
    """Write every synthetic export into `out_dir` and return their locations."""
    from fixtures.builders.imessage import build_imessage
    from fixtures.builders.mbox import build_mbox
    from fixtures.builders.meta import build_meta
    from fixtures.builders.vcard import build_vcard
    from fixtures.builders.voice import build_google_voice, build_iphone_voicemail
    from fixtures.builders.whatsapp import build_whatsapp

    out_dir.mkdir(parents=True, exist_ok=True)
    meta = build_meta(out_dir / "meta")
    paths = {
        "contacts": build_vcard(out_dir / "contacts"),
        "imessage": build_imessage(out_dir / "imessage"),
        "whatsapp": out_dir / "whatsapp",
        "messenger": meta["messenger"],
        "instagram": meta["instagram"],
        "mbox": build_mbox(out_dir / "gmail"),
        "google_voice": build_google_voice(out_dir / "google-voice"),
        "iphone_voicemail": build_iphone_voicemail(out_dir / "iphone"),
    }
    build_whatsapp(paths["whatsapp"])
    return FamilyExports(root=out_dir, paths=paths)
