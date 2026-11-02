"""Copy a conversation's media into an archive folder, renamed and dated by the messages.

A thin layer over `AttachmentManager`, the shared attachment code first built for iMessage:
files are copied (never moved), named from their date, and filed under
`<archive>/<Service>/<year>/<month>/`. The date is the file's own metadata when it has any, else
the time of the message that carried it. A file that can't be processed is reported and skipped;
it never stops an import. The unified naming and per-person folders are ticket 13.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

from chattr.attachment_manager import AttachmentInfo, AttachmentManager


def archive_media(
    items: Iterable[tuple[Union[str, Path], datetime]],
    destination: Union[str, Path],
    service: str,
    workers: int = 4,
) -> dict[str, AttachmentInfo]:
    """Archive `(file, message time)` pairs; returns the result for each source path."""
    work = []
    for path, when in items:
        if not os.path.isfile(path):
            continue
        work.append(
            (
                AttachmentInfo(
                    original_path=str(path),
                    original_filename=os.path.basename(path),
                    mime_type=None,
                    file_size=os.path.getsize(path),
                    created_date=None,
                ),
                when,
            )
        )
    if not work:
        return {}
    manager = AttachmentManager(
        base_attachments_dir=str(destination),
        service_name=service,
        move_files=False,
        max_workers=workers,
    )
    return {info.original_path: info for info in manager.process_attachments_parallel(work)}
