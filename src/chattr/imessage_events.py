"""What an iMessage row means when it is not an ordinary message: an edit history, or an event.

- `edit_history` reads `message.message_summary_info` (a binary property list): the earlier texts of
  each edited part with the time of each edit, and the parts that were unsent.
- `announcement` turns a row whose `item_type` is not 0 into a sentence: someone was added, the
  conversation was named, a location stopped being shared, and so on.

Derived from imessage-exporter 5.0.1 (`message_types/edited.rs`,
`tables/messages/models/group_action.rs`, `tables/messages/message.rs`); see NOTICE.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from chattr import apple_time, typedstream


@dataclass
class Edit:
    part: int
    at: datetime
    text: str


@dataclass
class EditHistory:
    edits: list[Edit] = field(default_factory=list)  # oldest first; the last is the text now
    unsent_parts: list[int] = field(default_factory=list)
    parts: int = 0

    @property
    def fully_unsent(self) -> bool:
        """Every part of the message was unsent: there is nothing left to show."""
        return bool(self.unsent_parts) and len(set(self.unsent_parts)) >= max(self.parts, 1)


def edit_history(summary: Optional[bytes]) -> EditHistory:
    """The edit history in a `message_summary_info` blob; empty when there is none to read."""
    history = EditHistory()
    if not summary:
        return history
    try:
        root = plistlib.loads(bytes(summary))
    except Exception:  # not a property list, or a damaged one
        return history
    if not isinstance(root, dict):
        return history
    parts = root.get("otr")
    history.parts = len(parts) if isinstance(parts, dict) else 0
    events = root.get("ec")
    if isinstance(events, dict):
        for key, entries in events.items():
            try:
                part = int(key)
            except (TypeError, ValueError):
                continue
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                stamp, blob = entry.get("d"), entry.get("t")
                moment = apple_time.to_utc(int(stamp)) if isinstance(stamp, (int, float)) else None
                text = typedstream.attributed_text(bytes(blob)) if blob else None
                if moment is not None and text is not None:
                    history.edits.append(Edit(part, moment, text))
        history.edits.sort(key=lambda e: (e.part, e.at))
    unsent = root.get("rp")
    if isinstance(unsent, list):
        history.unsent_parts = sorted({int(p) for p in unsent if isinstance(p, int)})
    return history


_PHOTO = {1: "changed the group photo", 2: "removed the group photo", 3: "changed the group photo"}
_BACKGROUND = {
    4: "changed the conversation's background",
    5: "changed the conversation's background",
    6: "removed the conversation's background",
    7: "changed the conversation's background",
}


def announcement(
    row: Mapping[str, Any],
    actor: Optional[str],
    name_of: Callable[[int], Optional[str]],
) -> Optional[str]:
    """The sentence for an event row, or None when the row is an ordinary message.

    `actor` is who did it (None when Messages recorded nobody); `name_of` names a handle row.
    """
    item = int(row.get("item_type") or 0)
    if item == 0:
        return None
    action = int(row.get("group_action_type") or 0)
    other = row.get("other_handle") or None
    who = actor or "Someone"
    if item == 1 and action in (0, 1) and other:
        if action == 0 and row.get("handle_id") == other:
            return f"{who} changed their phone number."
        target = name_of(int(other)) or "someone"
        if action == 0:
            return f"{who} added {target} to the conversation."
        return f"{who} removed {target} from the conversation."
    if item == 2:
        title = str(row.get("group_title") or "").strip()
        if title:
            return (
                f"{actor} named the conversation “{title}”."
                if actor
                else f"The conversation was named “{title}”."
            )
        return f"{who} removed the conversation's name."
    if item == 3:
        if action == 0:
            return f"{who} left the conversation."
        said = _PHOTO.get(action) or _BACKGROUND.get(action)
        if said:
            return f"{who} {said}."
    if item == 4 and int(row.get("share_status") or 0) in (0, 1):
        return f"{who} {'stopped' if int(row.get('share_status') or 0) else 'started'} sharing location."
    if item == 5:
        return f"{who} kept an audio message."
    if item == 7:
        return "Sharing of the Emergency SOS transcript stopped."
    if item == 8:
        return "A location was sent to emergency services."
    if item in (1, 3):
        return (
            f"{who} made a change to the group that Caixa does not know "
            f"(type {item}, action {action})."
        )
    return f"Messages recorded an event Caixa does not know (type {item})."
