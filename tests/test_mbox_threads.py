"""Mail threads (Gmail thread ids, else References chains) and attachments stored by hash."""

from __future__ import annotations

import hashlib
import mailbox
from email.message import EmailMessage
from pathlib import Path

import pytest
from fixtures import FamilyExports, family

from chattr import blobs
from chattr.parsers.mbox_parser import MboxParser


def _mail(
    message_id: str,
    subject: str = "Plan",
    day: int = 1,
    references: list[str] | None = None,
    thrid: str | None = None,
    sender: str = "ana@example.com",
    to: str = "maria@example.com",
) -> EmailMessage:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message["Date"] = f"Mon, {day:02d} Apr 2024 10:00:00 +0000"
    message["Message-ID"] = f"<{message_id}>"
    if references:
        message["In-Reply-To"] = f"<{references[-1]}>"
        message["References"] = " ".join(f"<{r}>" for r in references)
    if thrid:
        message["X-GM-THRID"] = thrid
    message.set_content("text")
    return message


def _mbox(tmp_path: Path, *messages: EmailMessage) -> Path:
    path = tmp_path / "mail.mbox"
    box = mailbox.mbox(path, create=True)
    for message in messages:
        box.add(message)
    box.flush()
    box.close()
    return path


def _threads(path: Path, **options):
    return {b.conversation.name: b for b in MboxParser(**options).parse(path)}


# --- The fixture ----------------------------------------------------------------------------


def test_the_fixture_has_a_gmail_thread_and_a_single_mail(fixture_family: FamilyExports) -> None:
    batches = MboxParser().parse(fixture_family.paths["mbox"])
    assert len(batches) == 2
    thread = next(b for b in batches if len(b.records) == 3)
    assert thread.conversation.source_id == "gmail:1790000000000000001"
    assert thread.conversation.name == "Fotos de São Martinho"  # no "Re: "
    assert thread.conversation.type == "individual"
    assert set(thread.conversation.participants) == {family.JOAO.emails[0], family.OWNER.emails[0]}
    assert [r.timestamp for r in thread.records] == sorted(r.timestamp for r in thread.records)
    single = next(b for b in batches if len(b.records) == 1)
    assert single.conversation.name == "Wedding update"


# --- Threading ------------------------------------------------------------------------------


def test_references_chains_thread_without_gmail_ids(tmp_path: Path) -> None:
    path = _mbox(
        tmp_path,
        _mail("a@x", "Plan", 1),
        _mail("b@x", "Re: Plan", 2, ["a@x"]),
        _mail("c@x", "Re: Re: Plan", 3, ["a@x", "b@x"]),
        _mail("z@x", "Something else", 4),
    )
    threads = _threads(path)
    assert [len(b.records) for b in threads.values()] == [3, 1]
    assert threads["Plan"].conversation.source_id == "msgid:a@x"


def test_a_thread_whose_first_mail_is_missing_still_groups(tmp_path: Path) -> None:
    path = _mbox(
        tmp_path,
        _mail("b@x", "Re: Plan", 2, ["a@x"]),
        _mail("c@x", "Re: Plan", 3, ["a@x", "b@x"]),
    )
    (batch,) = MboxParser().parse(path)
    assert len(batch.records) == 2 and batch.conversation.source_id == "msgid:a@x"


def test_a_reply_without_references_follows_in_reply_to(tmp_path: Path) -> None:
    reply = _mail("b@x", "Re: Plan", 2)
    reply["In-Reply-To"] = "<a@x>"
    (batch,) = MboxParser().parse(_mbox(tmp_path, _mail("a@x", "Plan", 1), reply))
    assert len(batch.records) == 2


def test_gmail_thread_ids_win_over_headers(tmp_path: Path) -> None:
    path = _mbox(
        tmp_path,
        _mail("a@x", "Plan", 1, thrid="111"),
        _mail("b@x", "Plan too", 2, thrid="111"),  # no References, but Gmail grouped them
        _mail("c@x", "Re: Plan", 3, ["a@x"], thrid="222"),  # headers say reply, Gmail split it
    )
    sizes = sorted(len(b.records) for b in MboxParser().parse(path))
    assert sizes == [1, 2]


def test_threads_are_ordered_by_their_first_mail(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _mail("late@x", "Late", 9), _mail("early@x", "Early", 2))
    assert [b.conversation.name for b in MboxParser().parse(path)] == ["Early", "Late"]


def test_prefixes_are_stripped_from_thread_names(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _mail("a@x", "Fwd: RE: Fw: Dinner?", 1))
    (batch,) = MboxParser().parse(path)
    assert batch.conversation.name == "Dinner?"


def test_more_than_two_participants_make_a_group(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _mail("a@x", to="maria@example.com, rui@example.com"))
    (batch,) = MboxParser().parse(path)
    assert batch.conversation.type == "group"
    assert len(batch.conversation.participants) == 3


def test_a_reference_cycle_does_not_hang(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _mail("a@x", "A", 1, ["b@x"]), _mail("b@x", "B", 2, ["a@x"]))
    assert sum(len(b.records) for b in MboxParser().parse(path)) == 2


# --- Attachments by hash --------------------------------------------------------------------


def test_attachments_are_stored_once_under_their_hash(
    fixture_family: FamilyExports, tmp_path: Path
) -> None:
    store = tmp_path / "Attachments"
    batches = MboxParser(attachments_dir=store).parse(fixture_family.paths["mbox"])
    (record,) = [r for b in batches for r in b.records if r.attachments]
    (attachment,) = record.attachments
    data = (store / str(attachment.storage_path)).read_bytes()
    assert attachment.sha256 == hashlib.sha256(data).hexdigest()
    assert attachment.storage_path == f"{attachment.sha256[:2]}/{attachment.sha256}.png"
    assert (attachment.original_filename, attachment.mime_type, attachment.size) == (
        "familia.png",
        "image/png",
        len(data),
    )
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    MboxParser(attachments_dir=store).parse(fixture_family.paths["mbox"])  # again: no duplicates
    assert len([p for p in store.rglob("*") if p.is_file()]) == 1


def test_without_a_store_attachments_are_hashed_but_not_kept(
    fixture_family: FamilyExports,
) -> None:
    batches = MboxParser().parse(fixture_family.paths["mbox"])
    (record,) = [r for b in batches for r in b.records if r.attachments]
    assert record.attachments[0].storage_path is None and record.attachments[0].sha256


def _with_attachment(
    name: str | None, data: bytes, mime=("application", "octet-stream")
) -> EmailMessage:
    message = _mail("att@x")
    message.add_attachment(data, maintype=mime[0], subtype=mime[1], filename=name)
    return message


def test_the_same_file_attached_twice_is_stored_once(tmp_path: Path) -> None:
    path = _mbox(
        tmp_path,
        _with_attachment("a.bin", b"same bytes"),
        _with_attachment("b.bin", b"same bytes"),
    )
    store = tmp_path / "store"
    MboxParser(attachments_dir=store).parse(path)
    assert len([p for p in store.rglob("*") if p.is_file()]) == 1


def test_an_attachment_without_a_name_still_gets_one(tmp_path: Path) -> None:
    path = _mbox(tmp_path, _with_attachment(None, b"%PDF-1.4 x", ("application", "pdf")))
    (batch,) = MboxParser(attachments_dir=tmp_path / "s").parse(path)
    (attachment,) = batch.records[0].attachments
    assert attachment.original_filename and attachment.original_filename.endswith(".pdf")


def test_a_forwarded_mail_is_kept_as_an_attachment(tmp_path: Path) -> None:
    outer = _mail("outer@x", "Fwd: inner")
    outer.add_attachment(_mail("inner@x", "inner"))
    (batch,) = MboxParser(attachments_dir=tmp_path / "s").parse(_mbox(tmp_path, outer))
    (attachment,) = batch.records[0].attachments
    assert attachment.mime_type == "message/rfc822"
    assert b"Message-ID: <inner@x>" in (tmp_path / "s" / str(attachment.storage_path)).read_bytes()


# --- The blob store -------------------------------------------------------------------------


def test_store_by_hash_is_idempotent_and_sanitises_the_suffix(tmp_path: Path) -> None:
    digest, relative = blobs.store_by_hash(tmp_path, b"abc", ".PNG")
    assert digest == hashlib.sha256(b"abc").hexdigest()
    assert relative == f"{digest[:2]}/{digest}.png"
    assert blobs.store_by_hash(tmp_path, b"abc", ".PNG") == (digest, relative)
    _, odd = blobs.store_by_hash(tmp_path, b"xyz", "../../etc/pass wd")
    assert ".." not in odd and " " not in odd
    assert (tmp_path / relative).read_bytes() == b"abc"


@pytest.mark.parametrize("suffix", ["", ".", ".verylongextensionname"])
def test_odd_suffixes_are_harmless(tmp_path: Path, suffix: str) -> None:
    _, relative = blobs.store_by_hash(tmp_path, b"1", suffix)
    assert (tmp_path / relative).is_file()
