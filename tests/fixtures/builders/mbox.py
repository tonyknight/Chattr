"""A synthetic MBOX in the style of Gmail Takeout (`Takeout/Mail/All mail Including Spam and Trash.mbox`).

Built with the standard library `email` and `mailbox` modules:
- non-ASCII `Subject` and `From` headers are RFC 2047 encoded (`=?utf-8?...?=`);
- an HTML-only message, a multipart/alternative message, and a PNG attachment;
- a thread linked by `Message-ID`, `In-Reply-To` and `References`;
- Gmail's `X-GM-THRID` and `X-Gmail-Labels` headers;
- dates written with the sender's own UTC offset.
"""

from __future__ import annotations

import mailbox
from datetime import datetime, timezone
from email.header import Header
from email.message import EmailMessage, Message
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime, formataddr, parseaddr, parsedate_to_datetime
from pathlib import Path

from fixtures import family
from fixtures.builders.media import png

MAILBOX_NAME = "All mail Including Spam and Trash.mbox"


def _bare_address(header_value: str) -> str:
    """The plain email address from a (possibly encoded) From header."""
    return parseaddr(header_value)[1] or "unknown@example.com"


def _address(person: family.Person, *, encode: bool = False) -> str:
    name = str(Header(person.name, "utf-8")) if encode else person.name
    return formataddr((name, person.emails[0]), charset="utf-8") if encode else formataddr(
        (name, person.emails[0])
    )


def _headers(
    msg: Message,
    *,
    sender: family.Person,
    recipient: family.Person,
    subject: str,
    moment: datetime,
    message_id: str,
    thread_id: str,
    labels: str,
    reply_to: list[str] | None = None,
) -> None:
    msg["From"] = _address(sender, encode=not sender.name.isascii())
    msg["To"] = _address(recipient)
    msg["Subject"] = Header(subject, "utf-8").encode() if not subject.isascii() else subject
    msg["Date"] = format_datetime(moment)
    msg["Message-ID"] = message_id
    if reply_to:
        msg["In-Reply-To"] = reply_to[-1]
        msg["References"] = " ".join(reply_to)
    msg["X-GM-THRID"] = thread_id
    msg["X-Gmail-Labels"] = labels


def _plain_reply(
    sender: family.Person,
    recipient: family.Person,
    subject: str,
    moment: datetime,
    message_id: str,
    thread_id: str,
    body: str,
    reply_to: list[str],
) -> Message:
    msg = MIMEText(body, "plain", "utf-8")
    _headers(msg, sender=sender, recipient=recipient, subject=subject, moment=moment,
             message_id=message_id, thread_id=thread_id, labels="Inbox,Família", reply_to=reply_to)
    return msg


def build_mbox(out_dir: Path) -> Path:
    """Write the MBOX into `out_dir/Takeout/Mail/` and return its path."""
    folder = out_dir / "Takeout" / "Mail"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MAILBOX_NAME
    if path.exists():
        path.unlink()
    me, joao, ana = family.OWNER, family.JOAO, family.ANA
    lisbon, east = family.TZ_LISBON, family.TZ_EAST
    thread = "1790000000000000001"

    # 1. João starts a thread (multipart/alternative, encoded From and Subject).
    first_id = "<20191102093000.1@example.org>"
    first = MIMEMultipart("alternative")
    first.attach(MIMEText("Olá Maria,\n\nAs fotos da festa de São Martinho estão prontas.\n", "plain", "utf-8"))
    first.attach(MIMEText("<p>Olá Maria,</p><p>As fotos da festa de <b>São Martinho</b> estão prontas.</p>", "html", "utf-8"))
    _headers(first, sender=joao, recipient=me, subject="Fotos de São Martinho",
             moment=family.at(2019, 11, 2, 9, 30, tz=lisbon), message_id=first_id,
             thread_id=thread, labels="Inbox,Família")

    # 2. Maria replies (plain text), quoting the thread.
    second_id = "<20191102141500.2@example.com>"
    second = _plain_reply(me, joao, "Re: Fotos de São Martinho",
                          family.at(2019, 11, 2, 9, 15, tz=east), second_id, thread,
                          "Que maravilha! Send them all, please.\n", [first_id])

    # 3. João replies again with a PNG attachment.
    third_id = "<20191102160000.3@example.org>"
    third = MIMEMultipart("mixed")
    third.attach(MIMEText("Here is the best one. The rest are on the drive.\n", "plain", "utf-8"))
    attachment = MIMEApplication(png(rgb=(240, 180, 30)), "png", name="familia.png")
    attachment.replace_header("Content-Type", 'image/png; name="familia.png"')
    attachment.add_header("Content-Disposition", "attachment", filename="familia.png")
    third.attach(attachment)
    _headers(third, sender=joao, recipient=me, subject="Re: Fotos de São Martinho",
             moment=family.at(2019, 11, 2, 16, 0, tz=lisbon), message_id=third_id,
             thread_id=thread, labels="Inbox,Família", reply_to=[first_id, second_id])

    # 4. An HTML-only newsletter-style message from Ana.
    html_only = EmailMessage()
    html_only.set_content("<html><body><h1>Wedding update</h1><p>The date is set: 14 June.</p></body></html>",
                          subtype="html", charset="utf-8")
    _headers(html_only, sender=ana, recipient=me, subject="Wedding update",
             moment=family.at(2025, 1, 15, 20, 45), message_id="<20250115204500.4@example.com>",
             thread_id="1790000000000000002", labels="Inbox,Important")

    # Fixed MIME boundaries and "From " separator lines keep the file byte-identical across builds
    # (the email package otherwise picks random boundaries, and mailbox stamps the current time).
    first.set_boundary("==caixa-fixture-alt-1==")
    third.set_boundary("==caixa-fixture-mixed-3==")
    messages = (first, second, third, html_only)
    for msg in messages:
        sent = parsedate_to_datetime(str(msg["Date"])).astimezone(timezone.utc)
        msg.set_unixfrom(f"From {_bare_address(str(msg['From']))} {sent:%a %b %d %H:%M:%S %Y}")

    box = mailbox.mbox(path, create=True)
    try:
        box.lock()
        for msg in messages:
            box.add(msg)
        box.flush()
    finally:
        box.unlock()
        box.close()
    lock = path.with_name(path.name + ".lock")
    if lock.exists():
        lock.unlink()
    return path
