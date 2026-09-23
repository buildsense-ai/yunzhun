"""Sending (SMTP) and sync trigger endpoints."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from ..db import SessionLocal
from ..mail import smtp as smtp_client
from ..mail.smtp import SmtpAccount
from ..models import Account
from ..schemas import SendRequest, SendResult, SyncMode, SyncResult
from ..security import decrypt, require_api_key
from ..services.sync import sync_account

router = APIRouter(tags=["sending", "sync"], dependencies=[Depends(require_api_key)])


def _smtp_account(account: Account) -> SmtpAccount:
    return SmtpAccount(
        address=account.address,
        auth_code=decrypt(account.auth_code_enc),
        provider=account.provider,
        display_name=account.display_name,
    )


@router.post("/v1/accounts/{account_id}/send", response_model=SendResult)
def send(account_id: int, body: SendRequest) -> SendResult:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        smtp_acct = _smtp_account(account)
    message_id = smtp_client.send_mail(
        smtp_acct,
        to=list(body.to),
        subject=body.subject,
        text=body.text,
        html=body.html,
        cc=list(body.cc),
        attachments=[a.model_dump() for a in body.attachments],
    )
    return SendResult(message_id=message_id, to=list(body.to))


@router.post("/v1/accounts/{account_id}/sync", response_model=SyncResult)
def trigger_sync(
    account_id: int, background: BackgroundTasks, body: SyncMode | None = None
) -> SyncResult:
    mode = (body.mode if body else "incremental")
    if mode not in ("incremental", "full"):
        from fastapi import HTTPException

        raise HTTPException(400, "mode must be 'incremental' or 'full'")
    stats = sync_account(account_id, mode=mode)
    if stats.get("new_messages"):
        # event-driven: new mail kicks the pipeline right away
        from ..services.pipeline import process_pending

        background.add_task(process_pending)
    return SyncResult(**stats)
