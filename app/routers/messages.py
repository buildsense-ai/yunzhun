"""Message reading API: list, detail (lazy body), raw, attachments, flags, delete, POP3."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..mail import pop3 as pop3_client
from ..mail.imap import MailError
from ..mail.parse import parse_message
from ..mail.pop3 import Pop3Account
from ..models import Account, Folder, Message
from ..schemas import FlagsPatch, MessageDetail, MessageListItem
from ..security import decrypt, require_api_key
from ..services import mailbox

router = APIRouter(tags=["messages"], dependencies=[Depends(require_api_key)])


def _query_messages(account_id: int, folder: str | None):
    with SessionLocal() as session:
        if session.get(Account, account_id) is None:
            raise LookupError(f"account {account_id} not found")
        stmt = select(Message).where(Message.account_id == account_id)
        if folder is not None:
            f = session.scalar(
                select(Folder).where(Folder.account_id == account_id, Folder.name == folder)
            )
            if f is None:
                raise LookupError(f"folder {folder!r} not found (run sync first)")
            stmt = stmt.where(Message.folder_id == f.id)
        return stmt


@router.get("/v1/accounts/{account_id}/messages", response_model=list[MessageListItem])
def list_messages(
    account_id: int,
    folder: str | None = Query(default=None, description="raw IMAP folder name, e.g. INBOX"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    unseen_only: bool = False,
    q: str | None = Query(default=None, description="substring match on subject"),
) -> list[Message]:
    stmt = _query_messages(account_id, folder)
    if unseen_only:
        stmt = stmt.where(Message.seen == False)  # noqa: E712
    if q:
        stmt = stmt.where(Message.subject.ilike(f"%{q}%"))
    stmt = stmt.order_by(Message.internal_date.desc().nulls_last(), Message.uid.desc())
    stmt = stmt.limit(limit).offset(offset).options(selectinload(Message.attachments))
    with SessionLocal() as session:
        return session.scalars(stmt).all()


@router.get("/v1/messages/{message_id}", response_model=MessageDetail)
def get_message(message_id: int, mark_seen: bool = False) -> Message:
    if mark_seen:
        msg = mailbox.mark_seen(message_id)
    else:
        msg = mailbox.ensure_message_body(message_id)
    with SessionLocal() as session:
        return session.scalars(
            select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
        ).one()


@router.get("/v1/messages/{message_id}/raw")
def get_raw(message_id: int) -> Response:
    msg = mailbox.ensure_message_body(message_id)
    return Response(content=msg.raw or b"", media_type="message/rfc822")


@router.get("/v1/messages/{message_id}/attachments/{attachment_id}")
def get_attachment(message_id: int, attachment_id: int) -> Response:
    att, payload = mailbox.get_attachment(message_id, attachment_id)
    filename = (att.filename or "attachment").replace('"', "")
    return Response(
        content=payload,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/v1/messages/{message_id}/flags", response_model=MessageDetail)
def patch_flags(message_id: int, body: FlagsPatch) -> Message:
    return mailbox.patch_flags(message_id, add=body.add, remove=body.remove)


@router.delete("/v1/messages/{message_id}", status_code=204)
def delete_message(message_id: int) -> None:
    mailbox.delete_message(message_id)


# ---------- POP3 fallback channel ----------
@router.get("/v1/accounts/{account_id}/pop3/messages")
def pop3_fetch(account_id: int, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        acct = Pop3Account(
            address=account.address,
            auth_code=decrypt(account.auth_code_enc),
            provider=account.provider,
        )
    pop = pop3_client.connect(acct)
    try:
        messages = pop3_client.list_messages(pop)
        uids = pop3_client.uid_map(pop)
        out: list[dict] = []
        for num, size in messages[-limit:]:
            raw = pop3_client.fetch(pop, num)
            parsed = parse_message(raw)
            out.append(
                {
                    "num": num,
                    "uidl": uids.get(num),
                    "size": size,
                    "subject": parsed["subject"],
                    "from": parsed["from"],
                    "date": parsed["date"].isoformat() if parsed["date"] else None,
                    "snippet": parsed["snippet"],
                }
            )
        pop.rset()  # do not persist any state
        return out
    except MailError:
        raise
    finally:
        try:
            pop.quit()
        except Exception:  # noqa: BLE001
            pass
