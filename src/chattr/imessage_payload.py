"""What an iMessage "balloon" holds: a link preview, an Apple Pay transfer, a message from an app.

Such a message has little or no text. What it shows is in `message.payload_data`, an
`NSKeyedArchiver` property list, and `message.balloon_bundle_id` says which kind it is.
`unarchive` turns the archive into plain Python values; `describe` reads the few fields a person
would want to see.

Derived from imessage-exporter 5.0.1 (`util/plist.rs`, `message_types/url.rs`,
`message_types/app.rs`, the balloon table in `tables/messages/message.rs`); see NOTICE.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from typing import Any, Optional

URL_BALLOON = "com.apple.messages.URLBalloonProvider"
#: Balloons Messages draws itself, by bundle id.
KINDS = {
    URL_BALLOON: ("link", "Link"),
    "com.apple.Handwriting.HandwritingProvider": ("handwriting", "Handwritten message"),
    "com.apple.DigitalTouchBalloonProvider": ("digital_touch", "Digital Touch"),
    "com.apple.PassbookUIService.PeerPaymentMessagesExtension": ("apple_pay", "Apple Pay"),
    "com.apple.ActivityMessagesApp.MessagesExtension": ("fitness", "Fitness"),
    "com.apple.mobileslideshow.PhotosMessagesApp": ("photos", "Photos"),
    "com.apple.SafetyMonitorApp.SafetyMonitorMessages": ("check_in", "Check In"),
    "com.apple.findmy.FindMyMessagesApp": ("find_my", "Find My"),
    "com.apple.icloud.apps.messages.business.extension": ("business", "Business Chat"),
    "com.apple.messages.Polls": ("poll", "Poll"),
}
#: How a message was sent "with effect" (`expressive_send_style_id`), by the end of its id.
EFFECTS = {
    "gentle": "Sent gently",
    "impact": "Sent with Slam",
    "invisibleink": "Sent with Invisible Ink",
    "loud": "Sent loudly",
    "CKConfettiEffect": "Sent with Confetti",
    "CKEchoEffect": "Sent with Echo",
    "CKFireworksEffect": "Sent with Fireworks",
    "CKHappyBirthdayEffect": "Sent with Balloons",
    "CKHeartEffect": "Sent with Love",
    "CKLasersEffect": "Sent with Lasers",
    "CKShootingStarEffect": "Sent with Shooting Star",
    "CKSparklesEffect": "Sent with Celebration",
    "CKSpotlightEffect": "Sent with Spotlight",
}
_DEPTH = 40


def unarchive(blob: Optional[bytes]) -> Any:
    """The root object of an `NSKeyedArchiver` property list as plain values; None if unreadable.

    Dictionaries, arrays, strings and URLs are resolved; the archiver's null is None. Anything
    else is given as it is stored.
    """
    if not blob:
        return None
    try:
        root = plistlib.loads(bytes(blob))
        objects = root["$objects"]
        top = root["$top"]["root"]
    except Exception:  # not a property list, or not an archive
        return None

    def resolve(value: Any, depth: int) -> Any:
        if depth > _DEPTH:
            return None
        if isinstance(value, plistlib.UID):
            if not 0 <= value.data < len(objects):
                return None
            return resolve(objects[value.data], depth + 1)
        if value == "$null":
            return None
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value]
        if isinstance(value, dict):
            if "NS.keys" in value and "NS.objects" in value:
                keys = [resolve(key, depth + 1) for key in value["NS.keys"]]
                values = [resolve(item, depth + 1) for item in value["NS.objects"]]
                return {str(k): v for k, v in zip(keys, values) if k is not None}
            if "NS.objects" in value:
                return [resolve(item, depth + 1) for item in value["NS.objects"]]
            if "NS.string" in value:
                return resolve(value["NS.string"], depth + 1)
            if "NS.relative" in value:  # an NSURL
                return {"URL": resolve(value["NS.relative"], depth + 1)}
            return {
                str(key): resolve(item, depth + 1) for key, item in value.items() if key != "$class"
            }
        return value

    return resolve(top, 0)


def _text(value: Any) -> Optional[str]:
    if isinstance(value, dict):  # a URL, or a nested {"URL": {"URL": "…"}}
        return _text(value.get("URL"))
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass
class Balloon:
    kind: str  # "link", "apple_pay", "app", "check_in", …
    app: str  # what to call it: "Link", "Apple Pay", the app's own name
    title: Optional[str] = None
    summary: Optional[str] = None
    url: Optional[str] = None
    site: Optional[str] = None

    @property
    def line(self) -> str:
        """One line for a message that has no text of its own."""
        said = " · ".join(part for part in (self.title, self.summary) if part)
        return f"{self.app}: {said}" if said else self.app


def describe(bundle_id: Optional[str], payload: Optional[bytes]) -> Optional[Balloon]:
    """What a balloon message shows; None for an ordinary message."""
    if not bundle_id:
        return None
    kind, app = KINDS.get(bundle_id, ("app", ""))
    data = unarchive(payload)
    root = data if isinstance(data, dict) else {}
    if kind == "link":
        meta = root.get("richLinkMetadata") or root.get("metadata") or {}
        meta = meta if isinstance(meta, dict) else {}
        return Balloon(
            "link",
            app,
            title=_text(meta.get("title")),
            summary=_text(meta.get("summary")),
            url=_text(meta.get("URL")) or _text(meta.get("originalURL")),
            site=_text(meta.get("siteName")),
        )
    found = root.get("userInfo")
    info: dict[str, Any] = found if isinstance(found, dict) else {}
    name = app or _text(root.get("an")) or bundle_id.rsplit(".", 1)[-1].split(":")[-1]
    title = (
        _text(info.get("image-title")) or _text(info.get("caption")) or _text(root.get("ldtext"))
    )
    summary = _text(info.get("image-subtitle")) or _text(info.get("subcaption"))
    if title and summary == title:
        summary = None
    return Balloon(kind, name, title=title, summary=summary, url=_text(root.get("URL")))


def effect(style_id: Optional[str]) -> Optional[str]:
    """`"Sent with Slam"` for an expressive send style id; None when there is none."""
    if not style_id:
        return None
    return EFFECTS.get(style_id.rsplit(".", 1)[-1])
