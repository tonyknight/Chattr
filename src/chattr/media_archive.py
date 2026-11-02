"""Archive a conversation's media through the one attachment pipeline.

A thin layer over `AttachmentManager`: it turns `MediaItem`s (a file, the time of the message that
carried it, who sent it, and whose conversation it belongs to) into archived copies under
`<archive>/<Person>/Attachments/` (groups: `Groups/<Name>/Attachments/`), named
`(YYYY-MM-DD) HH-MM-SS <sender> <n>.<ext>`. Originals stay where they are. A file that can't be
processed is reported and skipped; it never stops an import.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union

from chattr.attachment_manager import AttachmentInfo, AttachmentManager


@dataclass(frozen=True)
class MediaItem:
    path: Union[str, Path]
    when: datetime
    sender: Optional[str] = None
    person: Optional[str] = None
    is_group: bool = False


def archive_media(
    items: Iterable[MediaItem],
    destination: Union[str, Path],
    service: str,
    workers: int = 4,
) -> dict[str, AttachmentInfo]:
    """Archive the items; returns the result for each source path."""
    work = []
    for item in items:
        if not os.path.isfile(item.path):
            continue
        work.append(
            (
                AttachmentInfo(
                    original_path=str(item.path),
                    original_filename=os.path.basename(item.path),
                    mime_type=None,
                    file_size=os.path.getsize(item.path),
                    created_date=None,
                    sender=item.sender,
                    person=item.person,
                    is_group=item.is_group,
                ),
                item.when,
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
