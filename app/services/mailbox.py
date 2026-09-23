"""Per-message operations: lazy body fetch, attachment extraction, flags, delete."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SASession, selectinload

from ..db import SessionLocal
from ..mail import imap as imap_client
from ..mail.imap import ImapAccount, MailError
from ..mail.checksums import extract_checksums
from ..mail.parse import decode_header_value, extract_leaf_part, parse_message
from ..mail.storagelinks import extract_storage_refs
from ..models import Account, Attachment, Folder, Message, ObjectRef, utcnow
from ..security import decrypt, encrypt

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
        _replace_object_refs(
            session, msg,
            extract_storage_refs(parsed["text"], parsed["html"]),
            extract_checksums(parsed["text"], parsed["html"]),
        )
        session.commit()
        session.refresh(msg)
        return msg


def _replace_object_refs(session: SASession, msg: Message, refs, checksums=None) -> None:
    if checksums is None:
        checksums = extract_checksums(msg.text_body, msg.html_body)
    msg.object_refs.clear()
    # flush deletes before inserts: re-inserting the same (message_id, url)
    # would otherwise hit the UNIQUE constraint (UoW orders inserts first).
    session.flush()
    for ref in refs:
        basename = ref.key.rsplit("/", 1)[-1]
        msg.object_refs.append(
            ObjectRef(
                account_id=msg.account_id,
                provider=ref.provider,
                bucket=ref.bucket,
                key=ref.key,
                region=ref.region,
                url=ref.url,
                presigned=ref.presigned,
                expected_md5=checksums.get(basename),
            )
        )


def scan_message_objects(message_id: int) -> list[ObjectRef]:
    """(Re-)extract object-storage references from the cached body."""
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        if not msg.body_fetched:
            raise LookupError("message body not fetched yet; GET the message first")
        refs = extract_storage_refs(msg.text_body, msg.html_body)
        _replace_object_refs(session, msg, refs)
        session.commit()
        return session.scalars(
            select(ObjectRef).where(ObjectRef.message_id == message_id).order_by(ObjectRef.id)
        ).all()


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


def ingest_raw_message(raw: bytes, account_address: str = "inbound@webhook.local") -> Message:
    """Store a webhook-delivered raw RFC822 message under a synthetic account.

    The synthetic account uses provider='webhook' so it is excluded from IMAP
    sync/IDLE; the body is already local so the message goes straight into the
    judge/pull pipeline via the normal pending query.
    """
    parsed = parse_message(raw)
    with SessionLocal() as session:
        account = session.scalar(
            select(Account).where(Account.address == account_address)
        )
        if account is None:
            account = Account(
                address=account_address, provider="webhook",
                auth_code_enc=encrypt("-"), status="inbound",
            )
            session.add(account)
            session.flush()
        folder = session.scalar(
            select(Folder).where(
                Folder.account_id == account.id, Folder.name == "INBOX"
            )
        )
        if folder is None:
            folder = Folder(account_id=account.id, name="INBOX", name_decoded="INBOX")
            session.add(folder)
            session.flush()
        next_uid = (
            session.scalar(
                select(func.max(Message.uid)).where(Message.folder_id == folder.id)
            )
            or 0
        ) + 1
        msg = Message(
            account_id=account.id,
            folder_id=folder.id,
            uid=next_uid,
            message_id=parsed["message_id"],
            subject=parsed["subject"],
            from_addr=parsed["from"],
            to_addrs=parsed["to"],
            cc_addrs=parsed["cc"],
            date=parsed["date"],
            size=len(raw),
            flags=[],
            raw=raw,
            text_body=parsed["text"],
            html_body=parsed["html"],
            snippet=parsed["snippet"],
            body_fetched=True,
        )
        session.add(msg)
        session.flush()
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
        _replace_object_refs(
            session, msg,
            extract_storage_refs(parsed["text"], parsed["html"]),
            extract_checksums(parsed["text"], parsed["html"]),
        )
        session.commit()
        session.refresh(msg)
        return msg


def mark_seen(message_id: int) -> Message:
    with SessionLocal() as session:
        msg = _get_message(session, message_id)
        already = msg.seen
    if already:
        return ensure_message_body(message_id)
    return patch_flags(message_id, add=["\\Seen"], remove=[])
