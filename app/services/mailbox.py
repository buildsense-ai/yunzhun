"""Per-message operations: lazy body fetch, attachment extraction, flags, delete."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession, selectinload

from ..db import SessionLocal
from ..mail import imap as imap_client
from ..mail.imap import ImapAccount, MailError
from ..mail.parse import decode_header_value, extract_leaf_part, parse_message
from ..models import Account, Attachment, Message, utcnow
from ..security import decrypt

LEAF_FLAG_MAP = {
    "\\Seen": "seen",
    "\\Answered": "answered",
    "\\Flagged": "flagged",
    "\\Deleted": "deleted",
}


def _get_message(session: SASession, message_id: int) -> Message:
    msg = session.get(Message, message_id)
    if msg is None:
        raise LookupError(f"message {message_id} not found")
    return msg


def _connect_for(msg: Message):
    account = msg.account
    return imap_client.connect(
        ImapAccount(
            address=account.address,
            auth_code=decrypt(account.auth_code_enc),
            provider=account.provider,
        )
    )


def ensure_message_body(message_id: int) -> Message:
    """Fetch the full RFC 822 body from IMAP on first read and cache it."""
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        if msg.body_fetched and msg.raw is not None:
            return msg

        imap = _connect_for(msg)
        try:
            raw = imap_client.fetch_message_body(imap, msg.folder.name, msg.uid)
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

        parsed = parse_message(raw)
        msg.raw = raw
        msg.text_body = parsed["text"]
        msg.html_body = parsed["html"]
        msg.snippet = parsed["snippet"] or msg.snippet
        msg.size = len(raw)
        msg.body_fetched = True
        msg.attachments.clear()
        for att in parsed["attachments"]:
            msg.attachments.append(
                Attachment(
                    filename=att["filename"],
                    content_type=att["content_type"],
                    disposition=att["disposition"],
                    size=att["size"],
                    part_index=att["part_index"],
                )
            )
        session.commit()
        session.refresh(msg)
        return msg


def get_attachment(message_id: int, attachment_id: int) -> tuple[Attachment, bytes]:
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        att = session.get(Attachment, attachment_id)
        if att is None or att.message_id != message_id:
            raise LookupError(f"attachment {attachment_id} not found")
        if msg.raw is None:
            raise LookupError("message body not fetched yet")
        part = extract_leaf_part(msg.raw, att.part_index)
        if part is None:
            raise LookupError(f"part {att.part_index} missing in stored raw message")
        payload = part.get_payload(decode=True) or b""
        if not att.filename:
            att.filename = decode_header_value(part.get_filename()) or f"part-{att.part_index}"
        session.commit()
        return att, payload


def patch_flags(message_id: int, add: list[str], remove: list[str]) -> Message:
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        imap = _connect_for(msg)
        try:
            imap_client.store_flags(imap, msg.folder.name, msg.uid, add, remove)
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

        flags = [f for f in (msg.flags or []) if f not in remove]
        flags += [f for f in add if f not in flags]
        msg.flags = flags
        for flag, column in LEAF_FLAG_MAP.items():
            if flag in remove:
                setattr(msg, column, False)
            elif flag in add:
                setattr(msg, column, True)
        session.commit()
        return session.scalars(
            select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
        ).one()


def delete_message(message_id: int) -> None:
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        imap = _connect_for(msg)
        try:
            imap_client.store_flags(
                imap, msg.folder.name, msg.uid, ["\\Deleted"], [], expunge=True
            )
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
        session.delete(msg)
        session.commit()


def mark_seen(message_id: int) -> Message:
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        already = msg.seen
    if already:
        return ensure_message_body(message_id)
    return patch_flags(message_id, add=["\\Seen"], remove=[])
